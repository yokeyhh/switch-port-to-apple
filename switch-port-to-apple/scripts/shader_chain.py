#!/usr/bin/env python3
"""SPIR-V -> MSL -> .metallib for the NVN shader pipeline.

This is the last hop of the shader chain:

    Maxwell -> SPIR-V (dmsh_xlate) -> MSL (spirv-cross) -> .metallib (Apple metal)

It mirrors d2r-mobile's `dm/gfx/tools/dmsh_build.py` flags. The MSL step is the part
that is easy to get subtly wrong:

  * `--msl-version 20300` -- the default (1.2) rejects subgroups and other features
    the translated shaders use.
  * `--msl-argument-buffers` -- matches the binding convention dmsh_xlate emits.
  * `--msl-texture-buffer-native` -- textures stay in their native type.
  * `--msl-pad-fragment-output` -- Switch MRTs are sometimes fewer than four.
  * Output files MUST end in `.metal`. `metal` silently ignores any other suffix
    (exit 0, no `.air`), and `metallib` then fails with "Error opening '....air'".

Requirements: `spirv-cross` on PATH, Xcode with the Metal toolchain
(`xcodebuild -downloadComponent MetalToolchain` if `xcrun -f metal` fails).
Use `--msl-only` on a machine without the Metal compiler; the `.metal` sources can be
compiled later or shipped and compiled once on device.

Usage:
    shader_chain.py SPV_DIR OUT_DIR [--jobs N] [--limit N] [--stage vs]
                    [--msl-only] [--keep-err]
"""
import argparse
import collections
import concurrent.futures
import glob
import os
import shutil
import subprocess
import sys

XC = ['--msl', '--msl-version', '20300', '--msl-argument-buffers',
      '--msl-texture-buffer-native', '--msl-pad-fragment-output']


def run(cmd, errfile):
    with open(errfile, 'wb') as e:
        p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=e)
    return p.returncode == 0


def one(key, spv, outdir, msl_only=False, keep_err=False):
    msl = os.path.join(outdir, key + '.metal')
    air = os.path.join(outdir, key + '.air')
    mlb = os.path.join(outdir, key + '.metallib')
    err = os.path.join(outdir, key + '.err')

    def fail(stage):
        msg = ''
        if os.path.exists(err):
            msg = open(err, 'rb').read().decode('utf-8', 'replace')[:400]
            if not keep_err:
                os.remove(err)
        return (key, stage, msg)

    if not run(['spirv-cross', *XC, '--output', msl, spv], err):
        return fail('spirv-cross')
    if msl_only:
        if not keep_err and os.path.exists(err):
            os.remove(err)
        return (key, 'ok', '')
    if not run(['xcrun', '-sdk', 'macosx', 'metal', '-c', msl, '-o', air], err):
        return fail('metal')
    if not run(['xcrun', '-sdk', 'macosx', 'metallib', air, '-o', mlb], err):
        return fail('metallib')
    if os.path.exists(air):
        os.remove(air)
    if not keep_err and os.path.exists(err):
        os.remove(err)
    return (key, 'ok', '')


def main(argv=None):
    ap = argparse.ArgumentParser(description='Compile SPIR-V to MSL and .metallib.')
    ap.add_argument('spvdir')
    ap.add_argument('outdir')
    ap.add_argument('--jobs', type=int, default=min(16, os.cpu_count() or 4))
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--stage', action='append', default=[],
                    help='only these stages (second-to-last dot component, e.g. vs, ps)')
    ap.add_argument('--msl-only', action='store_true',
                    help='stop after spirv-cross (no Metal compiler needed)')
    ap.add_argument('--keep-err', action='store_true',
                    help='keep .err files (and .air on failure) for inspection')
    a = ap.parse_args(argv)

    if not shutil.which('spirv-cross'):
        print('spirv-cross not found on PATH', file=sys.stderr)
        return 2
    if not a.msl_only and not shutil.which('xcrun'):
        print('xcrun not found (Xcode command line tools required)', file=sys.stderr)
        return 2

    os.makedirs(a.outdir, exist_ok=True)
    keys = sorted(os.path.basename(f)[:-4] for f in glob.glob(os.path.join(a.spvdir, '*.spv')))
    if a.stage:
        want = set(a.stage)
        keys = [k for k in keys if k.rsplit('.', 2)[-2] in want]
    if a.limit:
        keys = keys[:a.limit]
    if not keys:
        print('no .spv inputs in %s' % a.spvdir, file=sys.stderr)
        return 2
    print('inputs: %d SPIR-V' % len(keys))

    results = []
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
        futs = [ex.submit(one, k, os.path.join(a.spvdir, k + '.spv'), a.outdir,
                          a.msl_only, a.keep_err) for k in keys]
        for i, f in enumerate(concurrent.futures.as_completed(futs), 1):
            results.append(f.result())
            if i % 200 == 0:
                print('  ... %d/%d' % (i, len(keys)))

    ok = sum(1 for r in results if r[1] == 'ok')
    final = 'MSL' if a.msl_only else 'metallib'
    print('  SPIR-V -> %s: %d / %d' % (final, ok, len(keys)))
    for stage in ('spirv-cross', 'metal', 'metallib'):
        bad = [r for r in results if r[1] == stage]
        if not bad:
            continue
        print('  [%s failed: %d]' % (stage, len(bad)))
        perr = collections.Counter(r[2].split('\n')[0][:100] for r in bad)
        for msg, n in perr.most_common(5):
            print('    %5d  %s' % (n, msg))
        print('    e.g. %s: %s' % (bad[0][0], bad[0][2].replace('\n', ' ')[:160]))
    if not a.msl_only:
        tot = sum(os.path.getsize(f) for f in glob.glob(os.path.join(a.outdir, '*.metallib')))
        print('  metallib total: %.1f MB' % (tot / 1e6))
    return 0 if ok == len(keys) else 1


if __name__ == '__main__':
    sys.exit(main())
