#!/usr/bin/env python3
"""Aggregate dmsh_xlate reflection output (*.res.json) into the facts you need
to build the NVN -> Metal slot tables.

Each successful translation writes one `<key>.res.json`:

    key              program name
    stage            vs / ps / ...
    notes            things the translator had to assume (e.g. "no PC type for
                     texture handle X, using 2D")
    cbufs            [{binding, slot}]                 c[slot] -> Metal buffer(binding)
    sbufs            [{binding, slot, cb_slot, cb_offset, flags}]
    textures         [{binding, type, format, cbuf_slot, handle, array, separate, flags}]
    images           [{...}]
    uses_frag_coord  whether gl_FragCoord is read
    frag_out_map     MRT output map (one value per combination -> how many PSOs)

The distributions printed here are what `nvn_slots.tsv`, the prototypes and the
thunks have to line up with.

Usage:
    refl_stats.py RES_DIR [--tsv OUT.tsv] [--top N]
"""
import argparse
import collections
import glob
import json
import os
import sys

# Ryujinx SamplerType: low bits = dimension, high bits = flags.
SAMPLER_TYPE = {0: 'None', 1: '1D', 2: 'Buffer', 3: '2D', 4: '3D', 5: 'Cube'}


def dim(t):
    base = SAMPLER_TYPE.get(t & 0xff, '?%d' % (t & 0xff))
    if t & 0x100:
        base += '+Array'
    if t & 0x200:
        base += '+MS'
    if t & 0x400:
        base += '+Shadow'
    return base


def note_class(s):
    """Collapse a note to its first clause so counts are meaningful."""
    for sep in ('（', '('):
        s = s.split(sep)[0]
    return s[:40]


def main(argv=None):
    ap = argparse.ArgumentParser(description='Summarize dmsh_xlate reflection json.')
    ap.add_argument('resdir')
    ap.add_argument('--tsv', default='', help='write the distributions as TSV')
    ap.add_argument('--top', type=int, default=12)
    a = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(a.resdir, '**', '*.res.json'), recursive=True))
    if not files:
        print('no *.res.json under %s' % a.resdir, file=sys.stderr)
        return 2

    stages = collections.Counter()
    tex_dim = collections.Counter()
    cbuf_slots = collections.Counter()
    sbuf_slots = collections.Counter()
    tex_per = collections.Counter()
    fmt = collections.Counter()
    handles = collections.Counter()
    fragmap = collections.Counter()
    fragcoord = 0
    notes = collections.Counter()
    cbslotsets = collections.Counter()
    sbflag = collections.Counter()

    for f in files:
        try:
            r = json.load(open(f))
        except (OSError, ValueError):
            continue
        stages[r.get('stage', '?')] += 1
        if r.get('uses_frag_coord'):
            fragcoord += 1
        fragmap[r.get('frag_out_map')] += 1
        cbufs, sbufs = r.get('cbufs', []), r.get('sbufs', [])
        cbslotsets[tuple(sorted(b['slot'] for b in cbufs))] += 1
        for b in cbufs:
            cbuf_slots[b['slot']] += 1
        for b in sbufs:
            sbuf_slots[b['slot']] += 1
            sbflag[b.get('flags')] += 1
        texs = r.get('textures', []) + r.get('images', [])
        tex_per[len(texs)] += 1
        for t in texs:
            tex_dim[dim(t['type'])] += 1
            fmt[t.get('format')] += 1
            handles[t.get('handle')] += 1
        for s in r.get('notes', []):
            notes[note_class(s)] += 1

    def top(c, k=None, width=24):
        for kk, vv in c.most_common(k or a.top):
            print('  %-*s %6d' % (width, str(kk), vv))

    print('programs: %d (%s)' % (
        sum(stages.values()), ' / '.join('%s %d' % kv for kv in sorted(stages.items()))))
    print('\ntexture/image dimensions (translator defaults to 2D when unknown)')
    top(tex_dim)
    print('\ntexture formats')
    top(fmt, min(a.top, 8))
    print('\ntextures + images per program')
    top(tex_per, min(a.top, 10))
    print('\nconstant-buffer slots c[slot]: uses')
    top(cbuf_slots, min(a.top, 16))
    print('\nconstant-buffer slot sets per program (top %d)' % a.top)
    top(cbslotsets, a.top, 40)
    print('\nscratch/storage buffer slots')
    top(sbuf_slots, min(a.top, 10))
    print('  flags: %s' % dict(sbflag))
    print('\ntexture handle indices (from c[0])')
    top(handles, min(a.top, 12))
    print('\nMRT output maps: %d distinct' % len(fragmap))
    top(fragmap, min(a.top, 8))
    print('\nprograms reading gl_FragCoord: %d' % fragcoord)
    print('\ntranslator notes (top %d)' % min(a.top, 8))
    top(notes, min(a.top, 8), 40)

    if a.tsv:
        with open(a.tsv, 'w') as o:
            o.write('# kind\tvalue\tcount\n')
            for name, c in (('texture_dim', tex_dim), ('format', fmt), ('textures_per',
                            tex_per), ('cbuf_slot', cbuf_slots), ('sbuf_slot', sbuf_slots)):
                for k, v in c.most_common():
                    o.write('%s\t%s\t%d\n' % (name, k, v))
        print('\nwrote %s' % a.tsv)
    return 0


if __name__ == '__main__':
    sys.exit(main())
