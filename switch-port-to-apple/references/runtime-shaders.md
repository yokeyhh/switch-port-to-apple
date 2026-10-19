# Runtime-constructed NVN programs

A bank scan (`references/shader-banks.md`) or a pre-extracted shader package only covers what the
title *ships*. Many titles also build NVN programs **at run time**: the control record and the code
region are written into guest memory by the game, so no `.bnsh`/package scan ever sees them.

Symptom: early scenes look fine, then a later area / character / UI / menu renders wrong or black,
and the replay layer logs a miss. In nxnative that is

```
[dm] 【红】着色器 9f3c1b0a77d2e401 不在映射表里（DM_NVN_SHDUMP=目录 可把控制段与代码区存盘）
```

A handful of misses is normal. The game stays up (the draw is skipped); the picture does not.
The miss list is your work queue.

## The key is the same as the bank pass

Nothing changes about how a program is *identified*. For each stage the host hashes

```
key = sha256(control[0 : nctl] ‖ code[0 : ncode])[:16]        # 16 hex chars = first 8 bytes
```

- `control` — the NVN control record, i.e. `NVNshaderData.control` as passed to
  `nvnProgramSetShaders`. **Its `0x30`/`0x100` stage header is included** in the hash (via `code`).
- `nctl` — `min(0x880, own)` where `own = u32@control+0x20`. Sections `≥ 0x880` use the first
  `0x880`; shorter ones (`≥ 0x760`) use their own length. D2R-era code that hashes a fixed `0x880`
  over-runs on SDK-10 titles.
- `code` — the code region `NVNshaderData.data`.
- `ncode` — `u32@control+0x704`.

Because it is the same formula, runtime keys go into the **same** `shader_map.tsv` and the same
package directory as the bank pass — at lookup time a runtime shader is indistinguishable from a
banked one. (A key computed any other way — e.g. hashing SPH+instructions without the stage header —
will never match. Do not "simplify" it.)

## The host-side contract

Two cheap requirements:

1. **Compute the key at `SetShaders`, not at draw time.** The control record is only guaranteed valid
   during `nvnProgramSetShaders`; by the time the program is drawn the game may have freed or reused
   it. (The code region lives in a memory pool and stays valid while the program is in use.) Cache
   `{key}` per program/stage there.
2. **On a map miss with `DM_NVN_SHDUMP=<dir>`, write two files named by the key:**
   - `<key>.ctl` — the first `0x880` bytes of the control record;
   - `<key>.code` — the `ncode` bytes of the code region.

   Keep a host copy of the control record for this; the game's copy may be gone by draw time. Whether
   the miss is logged in the *replay* layer (nxnative: `nvn_mtl.m`) or the *NVN* layer
   (`hle_nvn.c`) does not matter — only the `(key, control, code)` triple does.

The control record also carries, at fixed offsets, everything the offline step needs to reconstruct
the job (see below): `+0x700` `c1` offset, `+0x704` code size (= `c1` end), `+0x708` stage-header
size (`0x30` graphics / `0x100` compute).

## Offline: dump → translate → install

`scripts/runtime_jobs.py` turns a dump directory into a `dmsh_xlate` job file:

| job field | derived from |
|---|---|
| `key` | the file name (the host already named it by key) |
| `stage` | `0x100` header ⇒ `cs`; `0x30` graphics header ⇒ stage in bits 10–13 of the word at the header size, `1 vs 2 tcs 3 tes 4 gs 5 ps` |
| `code` | the code region **minus its `hs`-byte stage header** (the translator slices the instruction region itself; the header is only needed for the hash and the stage decode) |
| `c1` | `code[ u32@ctl+0x700 : u32@ctl+0x704 ]`, when non-empty |

The `0x700`/`0x704` pair is why the runtime path needs **no** `c1_extract.py` hunting: a runtime
program's own control record states where its `c1` (`BRX`) table is and how long it is, because the
table is the tail of the code region. (`shader-banks.md` still hunts `c1` for **banked** programs —
some containers drop or reshape those fields.)

Then the chain is exactly the bank chain:

```sh
# host ran with DM_NVN_SHDUMP=shdump  ->  shdump/<key>.ctl + shdump/<key>.code
python3 scripts/runtime_jobs.py shdump --out runtime/jobs.json
dotnet run --project dm/gfx/tools/dmsh_xlate -- runtime/jobs.json runtime/spv
python3 scripts/shader_chain.py runtime/spv runtime/mlib --jobs 10
# install <key>_b.metallib + <key>_b.json into the package; append the rows to shader_map.tsv
```

Repeat: run the game again with `DM_NVN_SHDUMP`, pack the new misses, until the log stops reporting
them. It converges in a few rounds — the first batch unblocks the next scene, which constructs a few
more. In one port the bank set grew by **+67** runtime programs (graphics and compute) over four
rounds before the opening sequence stopped reporting misses.

## Traps

- **`debug: true` still applies.** Runtime programs are switch-heavy too; the AST optimizer throws on
  a large fraction of them (`pitfalls.md`). `runtime_jobs.py` keeps it on by default.
- **Do not hand-edit or re-cook the bytes.** The key covers them; any change makes lookup miss.
- **Do not silence the miss and move on.** Skipping the draw keeps the game alive but the scene
  stays wrong (usually black). Pack the misses instead.
- **Reduce, don't remove, the host's logging.** The miss line is per-program both loud and cheap; a
  run that reaches a new area should print it exactly the number of times you need.
- **Half of these need `c1`, half don't.** The extractor keys off the dump, so it naturally covers
  only the ones that read the jump table.
