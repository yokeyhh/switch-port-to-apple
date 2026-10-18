#!/usr/bin/env python3
"""BNSH (nn::gfx binary shader bank) reader and NVN program extractor.

A bank embeds many NVN programs plus the constant data they read. The translator
wants one program per blob, starting at the **SPH** and followed by Maxwell
instructions, so this script exports `[code_off + 0x30 : code_end]`.

Layout (all offsets from the bank's own header):

    0x00  "BNSH"
    0x04  u32   version (0 observed)
    0x08  u32   0x0002010c
    0x0c  u32   0x000cfeff
    0x10  u32   offset of the first string in "_STR"  => _STR starts at this - 0x1a
    0x14  u32   0x00600000
    0x18  u32   "_RLT" offset
    0x1c  u32   file size
    0x20..0x5f  zero
    0x60  "grsc"
          +0x18  u32  variant slots (empty slots included)
          +0x1c  u32  live variants
          +0x28  u64  variant-table offset
    variant table    0x40 bytes per entry; +0x08 ctl offset, +0x10 code offset
    control pool     records starting with magic 0x98761234
    code pool        records starting with magic 0x12345678
    "_STR"           variant/stage names
    "_RLT"           link/relocation table

Program record:

    0x00..0x2f  NVN header (magic 0x12345678; other bytes observed zero)
    0x30..0x7f  SPH (0x50 bytes)
    0x80..      instructions, 4 x 8-byte words per group, first word = control
    ...         then the c1 jump table and/or uniform constants (see c1_extract.py)

Stage is SPH word 0, bits 10..13: 1=vs 2=tcs 3=tes 4=gs 5=ps.

Usage:
    bnsh.py summary PATH...                     one row per bank
    bnsh.py info    FILE                        header fields of one bank
    bnsh.py dump    PATH OUTDIR [--stage STAGE] export programs as <name>.<idx>.<stage>.code
    bnsh.py dump-at FILE OUTDIR OFFSET          extract a bank embedded at OFFSET in FILE

`dump` keeps the whole nonzero tail (instructions + c1 table + constants), because
that is exactly what the translator slices. Only trailing all-zero 8-byte words are
trimmed.
"""
import argparse
import os
import struct
import sys

BNSH_MAGIC = b'BNSH'
CODE_MAGIC = bytes.fromhex('78563412')   # 0x12345678, NVN graphics program
CTL_MAGIC = bytes.fromhex('34127698')    # 0x98761234, control record
STAGE = {1: 'vs', 2: 'tcs', 3: 'tes', 4: 'gs', 5: 'ps'}
NVN_HDR = 0x30


def find_all(buf, magic):
    """All 4-byte-aligned occurrences of `magic`."""
    out = []
    i = 0
    while True:
        i = buf.find(magic, i)
        if i < 0:
            return out
        if i % 4 == 0:
            out.append(i)
        i += 1


def sph_stage(buf, code_off):
    """Stage from SPH word 0, bits 10..13."""
    w = struct.unpack_from('<I', buf, code_off + 0x30)[0]
    return STAGE.get((w >> 10) & 0xf), w


def code_len(buf, off, limit):
    """Record length up to `limit`, minus trailing all-zero 8-byte words."""
    end = limit
    while end - 8 >= off + 0x80 and buf[end - 8:end] == b'\0' * 8:
        end -= 8
    return end - off


def parse(buf, base=0):
    """Parse one bank. `base` is the buffer's offset in the source file (for dump-at)."""
    if buf[:4] != BNSH_MAGIC:
        raise ValueError('not a BNSH bank (magic %r)' % buf[:4])

    def u32(off):
        return struct.unpack_from('<I', buf, off)[0]

    hdr = {
        'version': u32(0x04),
        'str_ptr': u32(0x10),
        'rlt_off': u32(0x18),
        'size': u32(0x1c),
        'variants': u32(0x7c),
        'table_off': struct.unpack_from('<Q', buf, 0x88)[0],
    }

    # End of the code pool = start of _STR. The header gives the offset of the first
    # *string*; the _STR block header is 0x1a bytes, so the block starts at str_ptr - 0x1a.
    # Do not use buf.find(b'_STR'): inside a host image it matches unrelated data.
    limit = len(buf)
    if hdr['str_ptr'] >= 0x1a:
        limit = min(limit, hdr['str_ptr'] - 0x1a)
    if hdr['size']:
        limit = min(limit, hdr['size'])

    codes = [c for c in find_all(buf, CODE_MAGIC) if c < limit]
    progs = []
    for k, off in enumerate(codes):
        nxt = codes[k + 1] if k + 1 < len(codes) else limit
        stage, w = sph_stage(buf, off)
        progs.append({'idx': k, 'off': base + off, 'size': code_len(buf, off, nxt),
                      'stage': stage, 'sph': w})
    return {'header': hdr, 'n_code': len(codes),
            'n_ctl': len([c for c in find_all(buf, CTL_MAGIC) if c < limit]),
            'programs': progs}


def inputs(paths):
    for p in paths:
        if os.path.isdir(p):
            for n in sorted(os.listdir(p)):
                q = os.path.join(p, n)
                if os.path.isfile(q):
                    yield q
        else:
            yield p


def load_banks(paths):
    """Yield (path, bytes, parsed) for every input that is a BNSH bank."""
    for f in inputs(paths):
        b = open(f, 'rb').read()
        if b[:4] != BNSH_MAGIC:
            continue
        yield f, b, parse(b)


def cmd_summary(args):
    total = {}
    print('%-56s %7s %7s %10s  %s' % ('file', 'code', 'ctl', 'bytes', 'stages'))
    for f, b, r in load_banks(args.paths):
        hist = {}
        nbytes = 0
        for p in r['programs']:
            hist[p['stage']] = hist.get(p['stage'], 0) + 1
            total[p['stage']] = total.get(p['stage'], 0) + 1
            nbytes += p['size']
        print('%-56s %7d %7d %10d  %s' % (
            os.path.basename(f)[:56], r['n_code'], r['n_ctl'], nbytes,
            ' '.join('%s:%d' % kv for kv in sorted(hist.items()))))
    print('\ntotal: %s' % ' '.join('%s:%d' % kv for kv in sorted(total.items())))
    return 0


def cmd_info(args):
    b = open(args.file, 'rb').read()
    r = parse(b)
    for k, v in r['header'].items():
        print('%-10s %s' % (k, hex(v) if isinstance(v, int) else v))
    print('%-10s %d' % ('code_recs', r['n_code']))
    print('%-10s %d' % ('ctl_recs', r['n_ctl']))
    hist = {}
    for p in r['programs']:
        hist[p['stage']] = hist.get(p['stage'], 0) + 1
    print('%-10s %s' % ('stages', ' '.join('%s:%d' % kv for kv in sorted(hist.items()))))
    return 0


def export(b, r, outdir, name, off_base=0):
    """Write one .code file per program: SPH + instructions + tail."""
    os.makedirs(outdir, exist_ok=True)
    n = 0
    for p in r['programs']:
        o = p['off']
        dst = os.path.join(outdir, '%s.%03d.%s.code' % (name, p['idx'], p['stage'] or 'x'))
        with open(dst, 'wb') as f:
            f.write(b[o + NVN_HDR:o + p['size']])
        n += 1
    return n


def cmd_dump(args):
    n = 0
    for f, b, r in load_banks([args.path]):
        name = os.path.splitext(os.path.basename(f))[0]
        progs = r['programs']
        if args.stage:
            keep = [p for p in progs if p['stage'] == args.stage]
            for p in keep:
                p['idx'] = p['idx']          # keep original index in the name
            progs = keep
        r = dict(r, programs=progs)
        n += export(b, r, args.outdir, name)
    print('exported %d programs to %s' % (n, args.outdir))
    return 0


def cmd_dump_at(args):
    b = open(args.file, 'rb').read()
    off = int(args.offset, 0)
    r = parse(b[off:], base=off)
    name = os.path.splitext(os.path.basename(args.file))[0]
    n = export(b, r, args.outdir, name)
    print('exported %d programs at %#x' % (n, off))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Read BNSH shader banks and export NVN programs.')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('summary', help='one row per bank')
    p.add_argument('paths', nargs='+')
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser('info', help='header fields of one bank')
    p.add_argument('file')
    p.set_defaults(func=cmd_info)

    p = sub.add_parser('dump', help='export programs (SPH + code + tail)')
    p.add_argument('path')
    p.add_argument('outdir')
    p.add_argument('--stage', choices=sorted(STAGE.values()),
                   help='only this stage')
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser('dump-at', help='export a bank embedded at an offset')
    p.add_argument('file')
    p.add_argument('outdir')
    p.add_argument('offset', help='byte offset, decimal or 0x...')
    p.set_defaults(func=cmd_dump_at)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == '__main__':
    sys.exit(main())
