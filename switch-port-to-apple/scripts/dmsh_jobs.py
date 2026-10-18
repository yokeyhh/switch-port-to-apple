#!/usr/bin/env python3
"""Build a dmsh_xlate job file from exported .code files.

`dmsh_xlate` takes a JSON array, one object per program:

    {"key": "...", "stage": "vs", "code": "/path/to/x.code",
     "tex": {}, "img": {}, "attr": {}, "fout": {}, "local": [1, 1, 1],
     "debug": true, "reduced": false, "c1": "<hex>"}

All fields except key/stage/code are optional (missing `local` means [1,1,1],
missing `debug` means DebugMode on, missing `c1` means zeros), but writing them
explicitly keeps runs reproducible.

Usage:
    dmsh_jobs.py CODE_DIR [--c1 c1_tables.json] [--out jobs.json]
                 [--stage vs --stage ps] [--reduced] [--no-debug]
                 [--local X,Y,Z]

The stage is the last dot component of the file name, as produced by bnsh.py
(`<bank>.<idx>.<stage>.code`).
"""
import argparse
import glob
import json
import os
import sys

STAGES = {'vs', 'tcs', 'tes', 'gs', 'ps', 'cs'}


def main(argv=None):
    ap = argparse.ArgumentParser(description='Build a dmsh_xlate job JSON.')
    ap.add_argument('codedir')
    ap.add_argument('--c1', default='', help='c1_tables.json from c1_extract.py')
    ap.add_argument('--out', default='-', help='output path, or - for stdout')
    ap.add_argument('--stage', action='append', default=[], choices=sorted(STAGES))
    ap.add_argument('--reduced', action='store_true',
                    help='set "reduced": true (reduced-precision mode)')
    ap.add_argument('--no-debug', action='store_true',
                    help='turn DebugMode off (NOT recommended: the AST optimizer '
                         'corrupts switch-heavy shaders)')
    ap.add_argument('--local', default='1,1,1', help='compute threadgroup size')
    a = ap.parse_args(argv)

    c1 = json.load(open(a.c1)) if a.c1 else {}
    try:
        local = [int(x, 0) for x in a.local.split(',')]
    except ValueError:
        print('--local must look like 8,8,1', file=sys.stderr)
        return 2
    if len(local) != 3:
        print('--local needs three components', file=sys.stderr)
        return 2

    jobs, skipped = [], 0
    for f in sorted(glob.glob(os.path.join(a.codedir, '*.code'))):
        name = os.path.basename(f)[:-len('.code')]
        stage = name.rsplit('.', 1)[-1]
        if stage not in STAGES:
            skipped += 1
            continue
        if a.stage and stage not in a.stage:
            continue
        job = {
            'key': name,
            'stage': stage,
            'code': f,
            'tex': {},
            'img': {},
            'attr': {},
            'fout': {},
            'local': local,
            'debug': not a.no_debug,
            'reduced': bool(a.reduced),
        }
        if name in c1:
            job['c1'] = c1[name]
        jobs.append(job)

    if not jobs:
        print('no .code files under %s' % a.codedir, file=sys.stderr)
        return 2

    text = json.dumps(jobs, indent=1)
    if a.out == '-':
        print(text)
    else:
        with open(a.out, 'w') as o:
            o.write(text)
    with_c1 = sum(1 for j in jobs if 'c1' in j)
    print('%d jobs (%d with c1, %d skipped: unknown stage)%s' % (
        len(jobs), with_c1, skipped, '' if a.out == '-' else ' -> ' + a.out),
        file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
