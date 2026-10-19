#!/usr/bin/env python3
"""Build a dmsh_xlate job file from a host's DM_NVN_SHDUMP output.

A companion to `references/runtime-shaders.md`. When the host finds a shader key that is not in its
map and `DM_NVN_SHDUMP=<dir>` is set, it writes two files per missing stage:

    <key>.ctl    the NVN control record (first 0x880 bytes)
    <key>.code   the code region (its length is u32@ctl+0x704)

This script reads a directory of those pairs and emits the same JSON array `dmsh_jobs.py` emits, so
the rest of the chain (dmsh_xlate -> shader_chain.py) is unchanged. It strips the stage header from
each `.code` (the translator wants the code region without it) and slices the `c1` (BRX) table out of
the code region using the offsets the control record carries:

    hs  = u32@ctl+0x708   stage header size: 0x30 (graphics) or 0x100 (compute)
    c1o = u32@ctl+0x700   c1 table offset within the code region
    c1t = u32@ctl+0x704   code region size  (== c1 table end)
    c1  = code[c1o:c1t]

The stage is decoded from the shader-program header word at offset `hs` (bits 10-13):
1 vs, 2 tcs, 3 tes, 4 gs, 5 ps; a 0x100 header means compute.

Usage:
    runtime_jobs.py DUMP_DIR [--out jobs.json] [--work DIR]
                   [--no-debug] [--no-reduced] [--local X,Y,Z] [--limit N]

`--work` (default: `./runtime_code`) receives the header-stripped `.code` files the job references.
DebugMode and reduced precision default on, matching the reference build's `XLATE_DEBUG=1`
`XLATE_REDUCED=1`.
"""
import argparse
import glob
import json
import os
import struct
import sys

STAGES = {'vs', 'tcs', 'tes', 'gs', 'ps', 'cs'}
STAGE_BITS = {1: 'vs', 2: 'tcs', 3: 'tes', 4: 'gs', 5: 'ps'}
GFX_HEADER = 0x30
CS_HEADER = 0x100


def decode(ctl, code):
    """-> (stage, stripped_code_bytes, c1_hex or ''). Raises ValueError on an unreadable pair."""
    if len(ctl) < 0x70c or len(code) < 4:
        raise ValueError('control or code too short')
    hs = struct.unpack_from('<I', ctl, 0x708)[0]
    c1o, c1t = struct.unpack_from('<2I', ctl, 0x700)

    if hs == CS_HEADER:
        stage = 'cs'
    elif hs == GFX_HEADER:
        w = struct.unpack_from('<I', code, hs)[0]
        stage = STAGE_BITS.get((w >> 10) & 0xf)
        if stage is None:
            raise ValueError('unknown stage bits %#x' % ((w >> 10) & 0xf))
    else:
        raise ValueError('unknown stage header size %#x' % hs)

    if not (hs <= c1o <= c1t <= len(code)) or (c1t and c1t != len(code)):
        raise ValueError('bad c1 range hs=%#x c1=%#x..%#x code=%#x' % (hs, c1o, c1t, len(code)))

    return stage, code[hs:], (code[c1o:c1t].hex() if c1t > c1o else '')


def main(argv=None):
    ap = argparse.ArgumentParser(description='dmsh_xlate jobs from DM_NVN_SHDUMP output.')
    ap.add_argument('dumpdir')
    ap.add_argument('--out', default='-', help='output path, or - for stdout')
    ap.add_argument('--work', default='runtime_code',
                    help='directory for the header-stripped .code files')
    ap.add_argument('--no-debug', action='store_true',
                    help='turn DebugMode off (NOT recommended: the AST optimizer corrupts '
                         'switch-heavy shaders)')
    ap.add_argument('--no-reduced', action='store_true',
                    help='turn relaxed precision off (it is on by default in the reference build)')
    ap.add_argument('--local', default='1,1,1', help='compute threadgroup size')
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args(argv)

    try:
        local = [int(x, 0) for x in a.local.split(',')]
    except ValueError:
        print('--local must look like 8,8,1', file=sys.stderr)
        return 2
    if len(local) != 3:
        print('--local needs three components', file=sys.stderr)
        return 2

    os.makedirs(a.work, exist_ok=True)
    jobs, bad, no_c1 = [], [], 0
    for cf in sorted(glob.glob(os.path.join(a.dumpdir, '*.code'))):
        key = os.path.basename(cf)[:-len('.code')]
        ctlp = os.path.join(a.dumpdir, key + '.ctl')
        if not os.path.exists(ctlp):
            bad.append((key, 'no .ctl'))
            continue
        try:
            stage, code, c1 = decode(open(ctlp, 'rb').read(), open(cf, 'rb').read())
        except ValueError as e:
            bad.append((key, str(e)))
            continue
        cp = os.path.join(a.work, key + '.code')
        with open(cp, 'wb') as o:
            o.write(code)
        job = {'key': key, 'stage': stage, 'code': os.path.abspath(cp),
               'tex': {}, 'img': {}, 'attr': {}, 'fout': {},
               'local': local, 'debug': not a.no_debug, 'reduced': not a.no_reduced}
        if c1:
            job['c1'] = c1
        else:
            no_c1 += 1
        jobs.append(job)
        if a.limit and len(jobs) >= a.limit:
            break

    for key, why in bad:
        print('  skip %s: %s' % (key, why), file=sys.stderr)

    if not jobs:
        print('no usable .ctl/.code pairs under %s' % a.dumpdir, file=sys.stderr)
        return 2

    text = json.dumps(jobs, indent=1)
    if a.out == '-':
        print(text)
    else:
        with open(a.out, 'w') as o:
            o.write(text)
    print('%d jobs (%d without c1, %d skipped)%s' % (
        len(jobs), no_c1, len(bad), '' if a.out == '-' else ' -> ' + a.out), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
