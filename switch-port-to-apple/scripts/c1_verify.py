#!/usr/bin/env python3
"""Independently verify extracted c1 (BRX jump table) blobs.

Four checks, each of which fails for a different reason. None of them reuses the
extractor's locating rule, so they are not circular:

  1. coverage   every read offset recorded by the no-c1 run is inside the blob
  2. structure  the table starts at T with T % 32 == 16
                (32-byte instruction groups from 0x50; the extractor only required
                16-byte alignment, so this is an independent consequence)
  3. landing    every entry is an absolute address that lands inside the instruction
                region: 0x50 + entry in [0x50, T). (All 1,739 BRX sites in the
                reference corpus resolve to base 0, so this applies universally.)
  4. content    every entry is non-zero and a multiple of 8

Usage:
    c1_verify.py TABLES.json --code CODE_DIR --notes RES_DIR [RES_DIR ...]

TABLES.json is the output of c1_extract.py; RES_DIR holds the *first* (no c1)
run's .res.json, which carries the `c1 读越界 +OFF` notes.
"""
import argparse
import glob
import json
import os
import re
import struct
import sys

OOR_RE = re.compile(r'c1 读越界 \+([0-9A-Fa-f]+)')
TEXT_START = 0x50


def read_notes(dirs):
    need = {}
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, '**', '*.res.json'), recursive=True)):
            key = os.path.basename(f)[:-len('.res.json')]
            try:
                r = json.load(open(f))
            except (OSError, ValueError):
                continue
            offs = [int(m.group(1), 16) for n in r.get('notes', [])
                    for m in [OOR_RE.search(n)] if m]
            if offs:
                need[key] = ((max(offs) - min(offs)) // 4 + 1, min(offs))
    return need


def split_padding(raw):
    """Separate the leading zero padding (base) from the table bytes."""
    pad = 0
    while pad + 4 <= len(raw) and raw[pad:pad + 4] == b'\0\0\0\0':
        pad += 4
    return pad, raw[pad:]


def main(argv=None):
    ap = argparse.ArgumentParser(description='Verify c1 blobs with four independent checks.')
    ap.add_argument('tables', help='JSON written by c1_extract.py --out')
    ap.add_argument('--code', required=True, help='exported .code files (SPH first)')
    ap.add_argument('--notes', nargs='+', required=True,
                    help='directories with the no-c1 run .res.json')
    ap.add_argument('--verbose', action='store_true')
    a = ap.parse_args(argv)

    tables = json.load(open(a.tables))
    need = read_notes(a.notes)

    ok = {'coverage': 0, 'structure': 0, 'landing': 0, 'content': 0}
    fails = {k: [] for k in ok}

    for key, hexblob in sorted(tables.items()):
        path = os.path.join(a.code, key + '.code')
        if not os.path.exists(path):
            for k in ok:
                fails[k].append((key, 'missing .code'))
            continue
        b = open(path, 'rb').read()
        raw = bytes.fromhex(hexblob)
        pad, tbl = split_padding(raw)
        vals = list(struct.unpack('<%dI' % (len(tbl) // 4), tbl))
        t = b.find(tbl)

        # 1) coverage: the blob is long enough for everything the first run read
        count, base = need.get(key, (0, 0))
        if base + count * 4 <= len(raw):
            ok['coverage'] += 1
        else:
            fails['coverage'].append((key, 'need=%d base=%#x blob=%d' % (count, base, len(raw))))

        # 2) structure: table start on a 32-byte instruction-group boundary
        if t >= 0 and t % 32 == 16:
            ok['structure'] += 1
        else:
            fails['structure'].append((key, 'T=%s T%%32=%s' % (
                hex(t) if t >= 0 else 'not-found', t % 32 if t >= 0 else '-')))

        # 3) landing: each word is an absolute code address (base 0 for every BRX site)
        if t >= 0:
            bad = [v for v in vals if not (TEXT_START <= TEXT_START + v < t)]
            if not bad:
                ok['landing'] += 1
            else:
                fails['landing'].append((key, 'outside text: %s (T=%#x)' % (
                    [hex(TEXT_START + v) for v in bad[:3]], t)))
        else:
            fails['landing'].append((key, 'table not found in .code'))

        # 4) content: non-zero, 8-byte aligned offsets
        if all(v and v % 8 == 0 for v in vals):
            ok['content'] += 1
        else:
            fails['content'].append((key, [hex(v) for v in vals if not v or v % 8][:3]))

    total = len(tables)
    print('verified %d c1 blobs:' % total)
    labels = {
        'coverage': 'coverage   (all reads inside blob)',
        'structure': 'structure  (T % 32 == 16)',
        'landing': 'landing    (base + entry in text)',
        'content': 'content    (non-zero, %8 == 0)',
    }
    for k in ('coverage', 'structure', 'landing', 'content'):
        n = ok[k]
        extra = ''
        if n + len(fails[k]) < total:
            extra = '  (%d not applicable)' % (total - n - len(fails[k]))
        print('  %-38s %d / %d = %.1f%%%s' % (labels[k], n, total,
                                              100.0 * n / total if total else 0.0, extra))
    for k, lst in fails.items():
        if lst:
            print('  %s failed (%d):' % (k, len(lst)))
            for key, why in lst[:(20 if a.verbose else 6)]:
                print('    x %-40s %s' % (key, why))
    return 0 if not any(fails.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
