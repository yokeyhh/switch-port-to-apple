# scripts

Runnable companions to `references/shader-banks.md`. They contain **no game data** —
everything is produced from files you extracted from your own copy.

| Script | Role |
|---|---|
| `bnsh.py` | Read a `BNSH` bank, list programs, export each as `<bank>.<idx>.<stage>.code` (SPH + instructions + tail) |
| `dmsh_jobs.py` | Turn a directory of `.code` files into the `dmsh_xlate` job JSON |
| `runtime_jobs.py` | Turn a host `DM_NVN_SHDUMP` directory (`<key>.ctl` + `<key>.code`) into the same job JSON, decoding the stage and slicing `c1` from the control record (`references/runtime-shaders.md`) |
| `c1_extract.py` | Locate the `c1` (`BRX` jump table) runs and emit the padded blobs |
| `c1_verify.py` | Four independent checks on those blobs |
| `shader_chain.py` | SPIR-V → MSL (`spirv-cross`) → `.metallib` (`xcrun metal`) |
| `refl_stats.py` | Aggregate `*.res.json` reflections into slot/format/texture statistics |

Requirements: Python 3.9+, no third-party modules. `shader_chain.py` additionally
needs `spirv-cross` on `PATH` and Xcode's Metal toolchain
(`xcodebuild -downloadComponent MetalToolchain` if `xcrun -f metal` fails). Without
the Metal compiler, use `--msl-only` and compile the `.metal` files later.

All scripts take `-h`.

## Order

```sh
# 1. banks -> one file per NVN program
scripts/bnsh.py summary RomFS/.../shader.bnsh
scripts/bnsh.py dump    RomFS/.../shader.bnsh work/code

# 2. jobs without c1, translate once (dmsh_xlate lives in the reference repo)
python3 scripts/dmsh_jobs.py work/code --out work/jobs_noc1.json
dotnet run --project dm/gfx/tools/dmsh_xlate -- work/jobs_noc1.json work/out_noc1

# 3. read the 'c1 读越界 +OFF' notes -> table base and count -> extract
python3 scripts/c1_extract.py work/out_noc1 --code work/code \
        --out work/c1_tables.json --junk-out work/c1_junk.json

# 4. verify (four independent checks; exit status 1 on failure)
python3 scripts/c1_verify.py work/c1_tables.json --code work/code --notes work/out_noc1

# 5. re-translate with c1; if new BRX sites appear, repeat 3-5 unioning both passes
python3 scripts/dmsh_jobs.py work/code --c1 work/c1_tables.json --out work/jobs_c1.json
dotnet run --project dm/gfx/tools/dmsh_xlate -- work/jobs_c1.json work/out_c1

# 6. SPIR-V -> MSL -> metallib
python3 scripts/shader_chain.py work/out_c1 work/mlib --jobs 10

# 7. slot/format statistics for the NVN layer
python3 scripts/refl_stats.py work/out_c1 --tsv work/shader_refl.tsv
```

## Notes

- **`c1` is only needed for programs that read it.** The extractor keys off the
  translator's out-of-range notes, so it naturally covers just those.
- **Do not turn `debug` off** in step 2/5. The translator's AST optimizer breaks
  switch-heavy shaders; see `references/pitfalls.md`.
- **Two-pass convergence is normal.** Newly resolved jump targets expose further
  `BRX` sites, which may read deeper entries. Pass both result directories to
  `c1_extract.py` (it unions the reads) and extract again.
- **Control experiment.** `c1_extract.py --junk-out` writes blobs of identical
  length with every entry set to `8`. Translating with those should *not* grow the
  output; if it does, the growth you measured with the real table is not evidence of
  resolved code.
- `bnsh.py dump` keeps the nonzero tail of each record (table + constants) on
  purpose: the translator slices the instruction region itself, and `c1` extraction
  needs those bytes.

## Runtime programs (not in any bank)

`references/runtime-shaders.md`. When the host reports a shader key missing from the
map and was run with `DM_NVN_SHDUMP=<dir>`, it has written `<key>.ctl` + `<key>.code`
for each miss. `runtime_jobs.py` turns those into the same job JSON — no `c1` hunting,
because the control record carries the table's offset and length:

```sh
python3 scripts/runtime_jobs.py shdump --out runtime/jobs.json --work runtime/code
dotnet run --project dm/gfx/tools/dmsh_xlate -- runtime/jobs.json runtime/spv
python3 scripts/shader_chain.py runtime/spv runtime/mlib --jobs 10
# install <key>_b.metallib + <key>_b.json into the package; append the rows to shader_map.tsv
```

Repeat (play further, dump, pack) until the log stops reporting misses.
