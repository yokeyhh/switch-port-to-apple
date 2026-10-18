# Pitfalls

The traps that cost real time in this kind of port. Most are silent-until-they-bite, so read this
before writing HLE functions or changing anything on the shader chain.

## ABI: Apple AAPCS64 ≠ Switch

- **Sub-32-bit arguments.** Apple's ABI requires the *caller* to extend `bool` / `char` / `short`
  arguments; Switch code does not. ⇒ **always declare such parameters as `int` / `unsigned`** and
  truncate yourself. `NVNboolean` in the graphics layer is always received as `uint32_t` with only
  the low 8 bits examined.
- **Variadics.** Apple passes varargs on the stack, Switch in registers ⇒ you **cannot** use C's
  `...` for a variadic HLE function. (Both warnings are stated at the top of `dm/host/hle.h`.)

## iOS forbids run-time-written code (no JIT)

- The rewritten images **cannot** be malloc'd and executed. They are compiled into the app as
  signed segments (`dm/ios/tools/gen_embed.py`), and the loader only `vm_remap`s them back to their
  original relative positions.
- **Apple `ITMS-90999`: only one executable segment is allowed.** That is why each module is split
  into `__TEXT,__dm<name>x0` (text) + `__DM<name>W,__img` (RW data) + `__TEXT,__dm<name>x1` (patch
  area), and why relocations land only in the W segment.
- Consequence of "never modified at run time": thread-slot numbers are read indirectly through a
  read-only **key page**, and gate addresses are fetched from thread slots. Do not "fix" this by
  patching at run time — it will be killed by code signing.
- The diagnostic probes `anon` / `mapjit` / `file` exist specifically to demonstrate this. On Mac
  none of this applies — the loader maps normally.

## Version fingerprinting — mismatches are red, not crashes

Three independent fingerprints must line up or the host reports red and refuses to load:

1. **Translation chain version `V`** — hashes the chain sources + options + tool fingerprints
   (`dmsh_chain.lock`). Libs live in `b-<V>/`; `dmsh_chain.h` holds `V`; every reflection json
   records it. Change any chain file or tool → **rebuild libraries *and* recompile the host.**
2. **`shader_facts.tsv`** — answers the translator needs that aren't in the machine code.
3. **`c1_full.tsv`** — first line records its `b-<V>`; a mismatch makes the replay layer
   conservatively give *every* shader an exclusive 64 KiB c1 buffer.

And per-title: the rewrite `CUTS`/`OBS`/`PARAMS` tables are keyed by the main module's **NSO build
ID** (16 bytes at offset `0x40`). Each `CUTS` entry asserts the original bytes before rewriting and
**aborts on any mismatch** — a wrong table fails loudly, never silently.

The three files `setup.sh` rewrites with your local tool fingerprints
(`dmsh_chain.lock`, `dmsh_chain.h`, `c1_full.tsv`) are **expected to be dirty — do not commit them.**

## Tool-version drift

- `spirv-cross` differences change the translated MSL. A *full self-build* stays self-consistent
  regardless of version, but packing into a **prebuilt shell** must match the shell's registered
  version — `d2rm_pack.py` stops otherwise.
- A different Xcode or `spirv-cross` than the lock file is normal; `setup.sh` re-registers and
  produces libraries for *your* tools, and the host then accepts only that version.

## Shader chain: three silent-wrong traps

These do not fail the build. They produce a library that loads fine and renders wrong.

- **`debug: true` is mandatory on titles with switch-heavy shaders.** It is the translator's
  `DebugMode` flag, i.e. it *skips* the AST optimizer. With the optimizer on, ~43% of programs throw
  in `GenerateCall`: it rewrites call arguments into `AstTextureOperation`, and SPIR-V codegen casts
  them back to `AstOperand`. The remaining programs still translate, so a spot check passes.
  Consequence: don't "optimize" the translate step by turning debug off — measure first.
- **`c1` is the `BRX` jump table, and it lives at the tail of the code segment.** It is *not* the
  `0x880` control-record payload, and it is *not* "the 0x100 bytes before the control section" (that
  D2R-era rule is a property of D2R's container, not of NVN). Search the code segment
  `[0x80:]`, take a conforming run's **start**, require `(T − 0x80) % 16 == 0`, and let the
  translator's out-of-range notes tell you the table base and count. Full algorithm and the four
  independent checks: `references/shader-banks.md`.
- **A long `c1` run has conforming positions inside it.** Tables for several `BRX` sites are
  concatenated, so scanning backwards for "an aligned position whose next N words look like a table"
  selects a **mid-run** start and shifts every case by 4–5 entries. Take the run start. Likewise, do
  not assume the table ends at the file end — uniform constants (`0x3f555556`, `0x4000`, …) follow it
  and `0x4000` passes a naive "8-aligned, small, non-zero" test.

## Graphics: device and OS splits

- **BC-compressed textures.** Apple GPUs from **A16 and earlier** do not support BC. Those devices
  need a **Legacy Device Pack** (ASTC) built from your own game files
  (`dm/tools/astc_pack.py` → `casc_overlay.py` → `finalize`) plus an app built with `DM_ASTC_PACK`
  and the pack copied to the device. The app detects a non-BC GPU and refuses to start without it.
- **Metal 4** is required by the default graphics backend ⇒ **macOS 26 / iOS 26+**. For older systems
  build the older-API variant (`DM_MAC_M3=1` for Mac, `DM_IOS_M3=1` for iOS 18–25). The two switches
  are independent and **combine**: "free account + iOS 18" needs both.
- **Shader lib target OS.** The cached libs record the *build machine's* system version as target;
  they won't load on an older minimum. Recompile with
  `bash dm/gfx/tools/msl_retarget.sh <min-os> <out>` and set `DM_MSL_DIR`.
- Unified memory only: NVN memory pools become zero-copy `MTLBuffer`s, so GPU address == CPU address.
- Some NVN paths are unimplemented by design (mid-pass clears, indexed quads, 8-bit indices, some
  texture copies/clears) and report red; `g_nops` functions (e.g. `MultiDraw*IndirectCount`,
  `DispatchComputeIndirect`, `SetDepthBounds`) are silent no-ops.

## Asset location & overlay

- Only the **18 RomFS archives** are validated. Config keys and archive lists are not read.
- The overlay RomFS is consulted **before** the real game files. That is how UI layout/textures are
  changed without touching the player's files — but it also means a stale overlay can shadow a fix.
- New textures are written into **borrowed old paths** the game no longer reads
  (`dm/data/casc_overlay/borrowed_paths.tsv`).

## Device / build workflow

- **Free Apple ID signature lasts 7 days.** After that the app won't launch; rebuild + reinstall.
  Free-account builds also lose iCloud save sync and sustained execution.
- `devicectl device info processes | grep <App>/<exe>` can **false-negative** and make a launcher
  script think the app exited when it is alive. Confirm before concluding a crash.
- `devicectl device capture screenshot` returns **black** for the game's Metal layer (UIKit captures
  fine). Don't diagnose "black screen" from it — read the log, or use `DM_NVN_SHOT` from inside.
- An app launched while the device is **locked** goes to the background; the host pauses and the log
  stops mid-boot. That is expected, not a hang.
- A worktree with **uncommitted changes** gets build number 1 ("cannot be used for sign-off") —
  expected after `setup.sh` rewrites the three fingerprint files.

## Title-specific defaults that bite a *new* game

The host has D2R-specific behaviour **on by default**. Another title hits these first:

- `hle_oe.c` — 3D render scale 0.75 on iOS, and `scale3d_tick` rewrites offsets **inside D2R's
  renderer** → a wild pointer for another game.
- `hle_fs.c` — `DM_STASH_SEED` and `DM_TOUCH_DEFAULTS` on by default.
- `main.c` — save-flush throttle patches a fixed address; `argv[0]` hard-coded to `"D2R"`.
- `hle_time.c` — fixed-time test switch recognises a fixed address.
- `front_mac.m` `front_game_res` — docked/handheld resolutions hard-coded to 1600×900 / 1280×720.
- `dm_cloudsync.c` — characters recognised by `.d2s`.
- `dm_cursor.c`, `dm_world.c` — the whole files are game-specific.

Turn them off or move them behind per-game config **before** you start debugging the new title.

## Reading the log: what "good" looks like

- `紫闪探针 … 画面品红 0 次, 调色表异常 0 次` → no missing shaders (the magenta probe).
- `上屏 至帧 N：… p50 16.67 ms` → steady 60 fps (16.67 ms = 60 Hz).
- Thermal throttling lowers the 3D render scale (log reports 温控 level); the UI is untouched.
- Sparse frame logs are normal — `帧 N：` lines are periodic, so a "stalled" log usually just means
  nothing worth logging happened, or the app is backgrounded.
