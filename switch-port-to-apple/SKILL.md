---
name: Switch to Apple Native Port
description: Use when porting a Nintendo Switch (Horizon OS, aarch64) game or application to Apple silicon (macOS, iOS, iPadOS) by running the original program natively — no CPU emulation — via an offline binary rewrite, a host compatibility layer (HLE) that replaces Horizon, and an NVN-to-Metal graphics backend. Grounded in the d2r-mobile project; covers the rewrite/load/HLE/graphics/shell pipeline, which code is reusable versus title-specific, and how to adapt the whole thing to a new title.
---

# Switch → Apple Native Port

Run a Nintendo Switch aarch64 binary **natively** on Apple silicon. iPhone and Switch use
the same class of CPU, so the game's own program executes byte for byte as shipped: only the
handful of instructions Apple's kernel breaks are rewritten offline, and the operating system
it expects (Horizon) is replaced by a small host. There is no CPU emulator and no JIT.

**Reference implementation:** `d2r-mobile` — Diablo II: Resurrected, Switch release 1.0.34.0.
- Local clone: `/Users/huangyuqi/dm-work/d2r-mobile`
- Upstream: `github.com/jhaizhou-ops/d2r-mobile` (MIT)
- Every path below is relative to that clone unless stated otherwise. Repo docs are bilingual
  (`README.md` / `README.zh-CN.md`); code comments carry both Chinese and English.

Read this page first; it is the map. Load the reference files it points at when you need depth:

| File | Covers |
|---|---|
| `references/architecture.md` | The five layers in detail, with the real APIs, tables and file formats |
| `references/code-reuse-map.md` | Every directory/file classified as reusable engine vs title-specific |
| `references/build-pipeline.md` | Workspace setup, Mac/iOS builds, artifacts, signing, pushing game data |
| `references/pitfalls.md` | The traps that cost real time (AAPCS64, no-JIT, BC textures, version drift) |
| `references/shader-banks.md` | When shaders arrive in `BNSH` banks instead of a per-program package: slicing NVN programs out, and the `c1` BRX jump table (`shader-banks.zh-CN.md` for the Chinese text) |

## When this approach applies

Use it only when **all** of these hold:

- **Source is aarch64.** Switch software is aarch64 (Cortex-A57 class); Apple silicon is arm64.
  Same instruction set → the guest program can run as-is. This is the entire premise. A non-arm64
  target (x86 console, ARM32) needs emulation and this playbook does not apply.
- **Graphics are NVN.** Nintendo's native graphics API. The graphics layer here is NVN-specific;
  a title using a non-NVN engine would need a different backend (the host/HLE side still applies).
- **You legally own the game** and have its program modules (`NSO`) + RomFS from your own copy.
  The repo ships **no** game content — it is generated from your files at build time.
- **Offline is acceptable.** Online (multiplayer, friends, shop) is out of scope; those services
  answer "unavailable" and the game takes its offline branch. Some games need targeted offline
  patches (see `CUTS` in `references/architecture.md`).

## The five layers (mental model)

```
your game: ExeFS main/sdk/subsdk0 (NSO) + RomFS
    │
 ①  REWRITE      offline: patch only the instructions Apple's kernel breaks        tools/patch_nso.py
    │            (x18, tpidr_el0, svc #n, cntpct_el0) → *.patched.bin + tables
 ②  LOAD         map the rewritten images, fill thread slots, register gates,
    │            relocate across modules                                     host/loader.c, thread.c
 ③  HLE          replace Horizon: kernel syscalls + SDK system services with
    │            host implementations                                          host/hle*.c
 ④  GRAPHICS     NVN calls recorded as commands, replayed as Metal; the game's
    │            shaders translated offline (Tegra Maxwell → SPIR-V → MSL →
    │            metallib)                                              gfx/nvn/*, gfx/tools/*
 ⑤  SHELL        platform app: window, input, storage, lifecycle
                 Mac: host/front_mac.m + host/app_mac.m   iOS: dm/ios/
```

The game's program is never modified at run time — iOS forbids executing memory written at
run time, so the rewritten images are compiled **into** the app as signed segments
(`dm/ios/tools/gen_embed.py`), and all gate addresses / thread-slot numbers are read indirectly
through a read-only key page.

## Workflow: adapting it to a new title

The host was written for one game, so some of its behaviour is game-specific and is **on by
default**. Expect to turn those off first. The order that works:

1. **Get the game files** from your own copy into the workspace layout
   (`$DM_ROOT/data/switch/<ver>/Update/{ExeFS,RomFS}`). See `references/build-pipeline.md`.
2. **Run the host as-is and read the red lines.** `dmhost` prints `UNIMPL <name>` for
   unimplemented imports and `【红】…` for red reports. These enumerate what the new title needs.
3. **Strip the title-specific defaults.** `references/code-reuse-map.md` lists them precisely —
   e.g. `hle_oe.c` rewrites a 3D scale at hard-coded offsets inside *this* game's renderer (a wild
   pointer for any other title); `main.c`, `hle_fs.c`, `hle_time.c`, `front_mac.m`, `dm_cloudsync.c`,
   `dm_cursor.c`, `dm_world.c` all assume D2R. Move them behind per-game config.
4. **Re-key the rewrite rule tables.** `tools/patch_nso.py` keys `CUTS`/`OBS`/`PARAMS` by the main
   module's NSO build ID (a 16-byte hash at offset `0x40`). A different title/version → different ID
   → you author your own entries (each asserts the original bytes before rewriting, and aborts on
   mismatch — a wrong table fails loudly, never silently).
5. **Regenerate the graphics tables** from the new title: `nvn_slots.py` / `gen_protos.py`
   (`nvn_slots.tsv`, `names.txt`, `nvn_protos.h`, thunks), then implement the NVN functions the new
   game actually calls (start from what the red stubs report).
6. **Regenerate the shader set**: the shader package is per-title; run the translation chain
   (`setup.sh` does it) to produce `<key>_b.metallib` + `_b.json` for every shader the game uses.
   If the title ships `BNSH` banks rather than an extracted package, you also have to slice the NVN
   programs out and supply the `c1` BRX jump table — `references/shader-banks.md`, and pass
   `debug: true` to the translator or ~43% of the programs fail.
7. **Repoint the shell** at the new ExeFS/RomFS and verify with the repo's verification tools.

Full detail for each step is in the reference files.

## Code reuse in one breath

- **Reusable engine (title-agnostic):** the rewriter core (`patch_nso.py` mechanics, `nso.py`),
  the loader/thready/HLE *machinery* (`loader.c`, `thread.c`, `entry.S`, `hle.c`, the `HLE` /
  `HLE_FORCE` macros in `hle.h`), the kernel+libc HLE (`hle_svc.c`, `hle_thread.c`, `hle_table.c`),
  the `front.h` contract and `front_mac.m`, the whole NVN→Metal layer (`gfx/nvn/*`), the shader
  translation chain (`gfx/tools/*`), the iOS build machinery (`ios/tools/gen_*.py`, `run.sh`).
- **Title-specific (must be adapted/removed):** every `CUTS`/`OBS`/`PARAMS` rule, the `dm_*.c`
  gameplay features, the address constants they use, `dm_cursor.c` / `dm_world.c`, and the
  game-specific defaults listed in the map.

The exact file-by-file breakdown is in `references/code-reuse-map.md`.

## Non-negotiables

- **Only one executable segment** in the iOS binary (Apple `ITMS-90999`); hence the
  `x0` / `W` / `x1` three-block split in `gen_embed.py`.
- **Version-check everything.** Rule tables, shader facts and the translation-chain version
  (`b-<V>/`, `dmsh_chain.h`) are all fingerprinted; a mismatch reports red and refuses to load
  rather than producing a subtly broken build.
- **AAPCS64 ≠ Switch ABI** for sub-32-bit arguments and variadics — see `references/pitfalls.md`
  before writing any HLE function.
- **Never commit the three tool-fingerprint files** that `setup.sh` rewrites
  (`dm/gfx/tools/dmsh_chain.lock`, `dm/gfx/nvn/dmsh_chain.h`, `dm/gfx/nvn/c1_full.tsv`).
