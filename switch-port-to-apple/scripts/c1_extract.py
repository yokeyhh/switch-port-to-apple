#!/usr/bin/env python3
"""Locate and extract the c1 (BRX indirect-jump) tables of translated shaders.

Why this exists
---------------
A shader that lowers a `switch` to `BRX` reads its jump table from constant bank 1.
The translator resolves the targets only if it is given that table; without it, it
reads zeros and every case falls through to the base address -- no error, just a
silently truncated CFG.

Semantics (from the Ryujinx sources vendored by the translation chain):

  Decoders/Decoder.cs  FindBrxTargetRange
      requires  IMNMX.U32 Rx,Rx,UpperBound,PT / SHL Rx,Rx,2 / LDC Rx,c[1][Rx+BaseOffset]
      count      = UpperBound + 1
      table base = the LDC's CbufOffset

  Decoders/Decoder.cs  FindBrxTargets
      base   = BRX address + sign_extend(immediate field) + 8
      target = base + c1[BaseOffset + i*4]

  Decoders/InstOp.cs / decoder loop
      addresses are bytes, advance by 8 per instruction, and are 0-based:
      0 = the first instruction, which sits at file offset 0x50.

Measured on 1,739 BRX sites: `base` is 0 for every one of them. The compiler
encodes the immediate field so that it cancels the instruction address, so each
table word is an **absolute address in the code array** -- in the exported file a
target is `0x50 + word`. (This is why every entry is non-zero, 8-aligned and
smaller than the table start.)

Layout of an exported code file (SPH first; add 0x30 inside the bank):

    [0x00, 0x50)  SPH (0x50 bytes; Ryujinx HeaderSize)
    [0x50, T)     instructions (4 x 8-byte words per group => 16-byte alignment)
    [T, R)        c1 table(s), concatenated, one per BRX site
    [R, end)      other constants (uniform defaults such as 0x3f555556, 0x4000)

Locating rule
-------------
1. T is the start of a conforming run, not any aligned position inside one. A long
   table contains many conforming positions a few entries apart; starting mid-run
   shifts every case by 4-5 entries and is silently wrong.
2. (T - 0x50) % 16 == 0: the instruction region is a whole number of 4-word groups.
   (T % 32 == 16 is a stronger, independently verified consequence.)
3. The run must be at least as long as the program actually reads, and it need not
   reach the end of the file -- constants may follow it.
4. A leading 0 entry is allowed (a `default` that lands on `base`): extend leftwards
   over adjacent zeros to reach the 16-byte congruence.

How to get the counts
---------------------
Run the translator once *without* c1. Its accessor records each read past the end of
the constant buffer as `c1 读越界 +OFF` in the .res.json notes. Then:

    count = (max(OFF) - min(OFF)) / 4 + 1
    base  = min(OFF)

Pass several result directories to union the reads across passes (newly resolved
targets can expose more BRX sites, which read deeper entries).

Usage
-----
    c1_extract.py --code CODE_DIR --out c1_tables.json RES_DIR [RES_DIR ...]
                  [--max-offset 0x8000] [--strict]

Output: JSON {program key: hex blob}, where the blob is zero-padded to `base` so it
can be assigned to the job entry's "c1" field verbatim.
"""
import argparse
import collections
import glob
import json
import os
import re
import struct
import sys

OOR_RE = re.compile(r'c1 读越界 \+([0-9A-Fa-f]+)')

# Instruction region starts here in an exported code file (SPH occupies [0, 0x50)).
TEXT_START = 0x50


def default_conforming(max_offset):
    """A plausible table entry: non-zero, 8-aligned, and a short byte offset."""
    def conf(v):
        return v != 0 and v % 8 == 0 and v < max_offset
    return conf


def find_region(vals, need, conf):
    """Rightmost conforming run long enough for `need` entries.

    Returns (start, end) in 32-bit word units, or None.
    """
    i = len(vals) - 1
    while i >= 0:
        while i >= 0 and not conf(vals[i]):
            i -= 1
        if i < 0:
            return None
        end = i + 1
        j = i
        while j >= 0 and conf(vals[j]):
            j -= 1
        start = j + 1

        # Align the run start; if it is off by a little, absorb adjacent zeros
        # (a leading 0 = "jump to base", i.e. the default case).
        cand = start if (start * 4 - TEXT_START) % 16 == 0 else None
        if cand is None:
            t = start - 1
            while t >= 0 and t > start - 5 and vals[t] == 0:
                if (t * 4 - TEXT_START) % 16 == 0:
                    cand = t
                    break
                t -= 1
        if cand is not None and end - cand >= need:
            return cand, end
        i = j
    return None


def read_notes(dirs):
    """Union the out-of-range reads from every *.res.json under `dirs`."""
    need = {}
    for d in dirs:
        files = sorted(glob.glob(os.path.join(d, '**', '*.res.json'), recursive=True))
        for f in files:
            key = os.path.basename(f)[:-len('.res.json')]
            try:
                r = json.load(open(f))
            except (OSError, ValueError):
                continue
            offs = [int(m.group(1), 16) for n in r.get('notes', [])
                    for m in [OOR_RE.search(n)] if m]
            if not offs:
                continue
            lo, hi = need.get(key, (min(offs), max(offs)))
            need[key] = (min(lo, min(offs)), max(hi, max(offs)))
    return need


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Extract c1 (BRX jump table) blobs for dmsh_xlate jobs.')
    ap.add_argument('resdirs', nargs='+', metavar='RES_DIR',
                    help='directories with the no-c1 run\'s *.res.json (repeat for more passes)')
    ap.add_argument('--code', required=True,
                    help='directory of exported .code files (SPH first)')
    ap.add_argument('--out', default='',
                    help='write {key: hex blob} as JSON here')
    ap.add_argument('--max-offset', default='0x8000',
                    help='largest plausible table entry (default 0x8000)')
    ap.add_argument('--strict', action='store_true',
                    help='exit 1 if any program could not be resolved')
    ap.add_argument('--junk-out', default='',
                    help='also write same-length blobs with every entry = 8 (control run)')
    a = ap.parse_args(argv)

    conf = default_conforming(int(a.max_offset, 0))
    need = read_notes(a.resdirs)

    tables, info, miss = {}, {}, []
    for key, (lo, hi) in sorted(need.items()):
        path = os.path.join(a.code, key + '.code')
        if not os.path.exists(path):
            miss.append((key, 'no %s' % path))
            continue
        b = open(path, 'rb').read()
        count = (hi - lo) // 4 + 1
        vals = list(struct.unpack('<%dI' % (len(b) // 4), b))
        got = find_region(vals, count, conf)
        if got is None:
            miss.append((key, 'need=%d base=%#x len=%#x' % (count, lo, len(b))))
            continue
        start, end = got
        # Keep exactly what the translator can read. A conforming run can extend into
        # the uniform constants that follow the table (0x4000 is a favourite), so the
        # run end is an upper bound, not the table length.
        if (end - start) * 4 < count * 4:
            miss.append((key, 'run too short: %d < %d' % ((end - start) * 4, count * 4)))
            continue
        tables[key] = (b'\0' * lo + b[start * 4:start * 4 + count * 4]).hex()
        info[key] = (count, lo, start * 4, end * 4, len(b))

    n = len(tables)
    print('programs that read c1: %d' % len(need))
    print('  tables extracted:      %d' % n)
    print('  run start (T-0x50)%%16==0: %d / %d' % (
        sum(1 for _, _, s, _, _ in info.values() if (s - TEXT_START) % 16 == 0), n))
    print('  run reaches EOF (upper bound only): %d / %d' % (
        sum(1 for *_, e, L in info.values() if e == L), n))
    print('  non-zero CbufOffset:      %d' % sum(1 for _, lo, _, _, _ in info.values() if lo))
    dist = collections.Counter(c for c, *_ in info.values())
    print('  entries per program (top 10): %s' % dict(dist.most_common(10)))
    print('  total c1 bytes:            %d' % sum(len(bytes.fromhex(t)) for t in tables.values()))
    print('  unresolved:                %d' % len(miss))
    for key, why in miss[:10]:
        print('    x %-40s %s' % (key, why))

    if a.out:
        with open(a.out, 'w') as f:
            json.dump(tables, f)
        print('  wrote %s' % a.out)

    if a.junk_out:
        # Control experiment: identical shape, meaningless targets. If the real table
        # resolves switch cases, translated code must grow versus this one.
        junk = {}
        for k, v in tables.items():
            raw = bytes.fromhex(v)
            pad = 0
            while pad + 4 <= len(raw) and raw[pad:pad + 4] == b'\0\0\0\0':
                pad += 4
            junk[k] = (b'\0' * pad + b'\x08\0\0\0' * ((len(raw) - pad) // 4)).hex()
        with open(a.junk_out, 'w') as f:
            json.dump(junk, f)
        print('  wrote control blobs %s' % a.junk_out)

    if miss and a.strict:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
