# Architecture — the five layers in detail

Everything here is grounded in `d2r-mobile`. Paths are relative to that repo.

The game program is three NSO files (the Switch executable module format):

| Module | What it is |
|---|---|
| `main` | the game itself |
| `sdk` | the Nintendo SDK |
| `subsdk0` | additional SDK/engine module |

They use the same instruction set as Apple silicon, so the vast majority executes unchanged.
What has to be handled is their dependence on **Horizon** (the Switch OS).

---

## ① Rewrite — `dm/tools/patch_nso.py` (+ `nso.py`, `dmx.py`)

Each module is rewritten **once, offline**, touching only instructions that cannot run under the
Apple kernel:

| Original instruction | Why it breaks | Rewritten to |
|---|---|---|
| Reads/writes of `x18` | the Apple kernel modifies `x18` | a per-thread *thread slot* (`K18`) |
| `mrs tpidr_el0` / `msr tpidr_el0` | Apple's kernel owns `tpidr_el0` | thread slot `KTLS` |
| `mrs tpidrro_el0` | Horizon's thread-local region has no Apple counterpart | thread slot `KTLR` |
| `svc #n` | there is no Horizon kernel | jump to a **host gate** (`KGATE`) |
| `mrs cntpct_el0` | Horizon's timer has no counterpart | host gate; the host answers as a 19.2 MHz counter |

Two tables are registered **per NSO build ID** (the 16-byte hash at offset `0x40`):

- **`CUTS`** — targeted instruction/data patches: `(offset, original_bytes, new_bytes, reason)`.
  Before rewriting, the original bytes are asserted entry by entry; **any mismatch aborts.**
  Results go to `<name>.cuts.tsv`.
- **`OBS`** — observation gates: one instruction at a function entry is replaced with
  "enter host observation callback → run the original instruction → jump back", so diagnostics
  modify zero code at runtime. Numbers match `OBS_*` in `dm/host/hle.h`. Results: `<name>.obs.tsv`.

There is also **`PARAMS`** (host-only parameter slots: resolution caps, aspect-ratio cap) — see
the `KEYS` table in `patch_nso.py`:

```python
KEYS = {'K18': 508, 'KTLS': 509, 'KTLR': 510, 'KGATE': 511,
        'KRES0': 506, 'KRES1': 507, 'KASPECT': 505, 'KASPECTUI': 504}
```

The rewritten code is **never modified again at run time**. Thread-slot numbers are read indirectly
through a read-only **key page** (only "actual key number × 8" is stored, at the end of the data
segment), and gate addresses come from thread slots. Reason: iOS will not let you execute code
written at run time, so the game code must be baked into the app as a signed segment.

**Outputs** (in the build output dir), per module:
`<name>.patched.bin` (rewritten image + key page + patch area at `PATCH_OFF`),
`<name>.fixups.tsv` (layout + key-page index header),
`<name>.gates.tsv`, `<name>.sites.tsv`, `<name>.cuts.tsv`, `<name>.obs.tsv`.

Three build outputs are produced by `dm/tools/product.sh build` (codename in `dm/PRODUCT`):

| Kind | Ruleset | Used by |
|---|---|---|
| `keypage` | `DM_RULESET=native` | `verify/regress.sh` (default) |
| `rules` | `DM_RULESET=touch-run-v4`, `DM_PRIVATE_GATES=0` | `verify/rulegate.sh` (default) |
| `touch` | `DM_RULESET=touch-run-v4`, private gates on | what `ios/run.sh` packs by default |

`CUTS` also carries *offline patches* to the game (not just ABI fixes). Example from the repo:

```python
# 显存分配器初始化配置：mov x8,#0x10400000（260 MB）→ mov x8,#0x20000000（512 MB）
(0x8524c4, '0808a2d2', '0800a4d2', '...'),
# 离线放行窗口：...   lt→gt
(0x1979bc, 'e8a79f1a', 'e8d79f1a', '...'),
```

---

## ② Load — `dm/host/loader.c`, `dm/host/thread.c`, `dm/host/main.c`

The host `dmhost` plays the role of Horizon's loader:

1. `loader.c` maps each module's rewritten image, creates the thread slots, fills in the key page,
   registers every gate from `<name>.gates.tsv`, performs cross-module relocation, and sets segment
   permissions. The code area is read-only + executable from the moment it loads.
2. Imported symbols are resolved one of three ways (see HLE below).
3. `main.c` initialises the SDK runtime **in the SDK's own startup order**, runs each module's
   `init_array` (global constructors), then enters the game entry `nnMain`.

Each game thread is a host pthread. `guest_thread_init` in `thread.c` fills that thread's slots:
the gate entry, the `x18` stand-in, the thread-local-storage block, and Horizon's thread-local region.

The **front-end contract** is `dm/host/front.h` — the only interface the host needs from a platform:

```c
void     front_run(void (*guest)(void));   // run the guest (own thread if there's a window)
void    *front_metal_layer(void);          // the CAMetalLayer to present into (NULL = headless)
uint64_t front_game_res(int docked);       // (u64)height<<32 | width  — 1600x900 / 1280x720 stock
int      front_text_input(...);            // software keyboard (swkbd)
void     dm_host_preload_start(int argc, char **argv);  // start loading early, in parallel with UI
void     dm_touch_set(const dm_touch *t, int n);        // touch points
void     front_save_failed(int err);                    // disk-full notice (weak by default)
```

Mac implements it in `front_mac.m`; iOS in `dm/ios/Sources/front_ios.m`.

---

## ③ HLE — `dm/host/hle*.c` + the macros in `dm/host/hle.h`

Three layers of Horizon dependence, each connected differently:

- **Kernel syscalls.** A rewritten `svc` goes through `dm_gate` (`host/entry.S`), which saves
  registers and enters `gate_dispatch` (`host/hle.c`) — which looks up by the gate's *return
  address* which instruction it used to be. Syscalls go to `svc_dispatch`.
  `hle_svc.c` implements memory calls (it reserves one block of guest address space and carves the
  heap/stack out of it); `hle_thread.c` implements thread & synchronisation calls. So `nn::os`
  runs as-is; only the kernel beneath it is replaced.
- **C library functions.** `hle_table.c` points libc functions whose calling convention matches and
  whose layout is identical straight at Apple's libc.
- **System services.** On real hardware the SDK talks to system service processes (files, accounts,
  audio, input) over IPC. The host has none, so it takes over at the SDK interface layer *by
  function name* and supplies host implementations.

Registration macros (`hle.h`) — note the ABI warnings in the header comment:

```c
// HLE: fill in imports no module defines
#define HLE(mangled, ret, cname, ...)                                    \
    static ret cname(__VA_ARGS__);                                       \
    __attribute__((used, section("__DATA,swhle")))                       \
    static const hle_ent hle_ent_##cname = {mangled, (void *)cname};     \
    static ret cname(__VA_ARGS__)

// HLE_FORCE: forced takeover even if the SDK defines the symbol.
// Only for service-layer nn:: (fs/oe/aoc/pctl...); NOT libc++ / nn::os internals.
// Every entry MUST state a reason; an entry nobody references aborts at load (fail loud).
#define HLE_FORCE(mangled, why, ret, cname, ...)                         \
    static ret cname(__VA_ARGS__);                                       \
    __attribute__((used, section("__DATA,swforce")))                     \
    static const hle_force_ent hle_force_##cname = {mangled, (void *)cname, why}; \
    static ret cname(__VA_ARGS__)
```

An import with no implementation becomes a named stub: it prints one line `UNIMPL <name>` and
returns 0. **That log is your to-do list for a new title.**

`hle_force_ent` also carries a `why` string — the table is deliberately self-documenting.

**Host call numbers** — beyond syscalls, internal game functions can be hooked: a cut replaces a
function entry with `svc #number; ret` and the rewriter turns it into a host gate. Horizon uses
`0x00–0x7F`, so `0x80+` is reserved for host calls. They are registered in `hle.h` with a
file + main-offset comment and dispatched in `hle_svc.c → svc_host_call`:

```c
enum { SVC_HOST_TACT_ZLIB = 0x80, SVC_HOST_C2S_DELIVER = 0x81, /* ... */ };
```

Service files: `hle_fs.c` (filesystem, mount names → host dirs), `hle_tact.c` / `hle_granny.c`
(zlib block decompression, Granny data decoding), `hle_audio.c` / `hle_codec.c` (audio out, Opus),
`hle_hid.c` / `hle_touch.c` (input), `hle_swkbd.c` / `hle_charname.c` (software keyboard),
`hle_oe.c` / `hle_time.c` / `hle_account.c`, `hle_movie.c` (cutscenes played as empty for their
duration), `hle_net.c` / `hle_friends.c` / `hle_misc.c` (online → unavailable), `hle_lifecycle.c`.

---

## ④ Graphics — NVN → Metal, `dm/gfx/`

The game draws with NVN. `main` imports only **one** NVN symbol, `nvnBootstrapLoader`; everything
else is fetched through it by name. The host force-takes that import over (`gfx/nvn/hle_nvn.c`),
so not a single instruction of the Switch's NVN driver runs.

- The game fetches 523 names (`gfx/nvn/names.txt`). Each gets a thunk (`nvn_thunks.S`) that bumps a
  call counter then jumps to the implementation. Of these, the 216 with call sites in `main`
  (`nvn_slots.tsv`) are implemented: 203 per NVN semantics, 13 empty (`g_nops`). The other 307 go to
  a **red-reporting stub** — `【红】NVN 未实现被调用：<name>`. Use that red output to prioritise
  work on a new title.
- Every implementation is signature-checked at compile time against `nvn_protos.h`.
  **ABI note:** Apple arm64 and Switch differ for integer params narrower than 32 bits, so
  `NVNboolean` params are always received as `uint32_t` and only the low 8 bits examined.

**Two layers:**

| Layer | File | Role |
|---|---|---|
| Interface (record) | `gfx/nvn/hle_nvn.c` | host objects stored in place inside the game's NVN object memory (`nvn_obj.h` layouts, each with a static assert vs NVN's reserved size); commands recorded into the game-provided command memory |
| Replay | `gfx/nvn/nvn_mtl.m` | replays the record stream as Metal at submit time |

Replay highlights: memory pools become zero-copy `MTLBuffer`s (GPU address == CPU address → unified
memory only); "GPU completion" effects (sync objects, events, counters) fire only after the Metal
command buffer actually completes; pipeline & depth-stencil states cached by state hash and
pre-warmed from the manifest at startup; Tegra block-linear textures deswizzled before upload
(`gfx/tex/`); presentation to a `CAMetalLayer`, timed by the pacer in `nvn/nvn_pace.h`.

### Shader translation chain (`gfx/tools/`)

Offline, never on the phone:

```
Switch shader package
  → tools/dmsh_xlate        SPIR-V via Ryujinx's shader translation library (C#)
  → spirv-cross             SPIR-V → Metal Shading Language (MSL)
  → xcrun metal / metallib  → Metal shader library            (or, without a Metal compiler:
                                                               ship MSL as source, compile once on device)
```

Two inputs are easy to get wrong before the chain even runs:

- **Input shape.** `dmsh_xlate` wants one program per blob, starting at the **SPH** (0x50 bytes) and
  followed by Maxwell instructions. Titles on Nintendo's own `nn::gfx` ship `BNSH` **shader banks**
  instead: you must slice each program out (`0x30`-byte NVN header + SPH + code). See
  `references/shader-banks.md`.
- **`c1`.** A shader that lowers a `switch` to `BRX` reads its jump table from constant bank 1. The
  table is not in the control section — it sits at the **tail of the code segment**. Without it the
  switch bodies are silently dropped (no error, just a wrong CFG). `references/shader-banks.md`
  covers the layout, the two-pass extraction and the verification.

- **Key**: first 16 hex digits of `sha256(shader control section, 0x880 bytes)`. At run time the game
  hands the control section to `nvnProgramSetShaders`; the replay layer computes the same key.
- **Build outputs**: `<key>_b.metallib`, `<key>_b.json` (reflection: binding kind, slot, argument-buffer
  index), `<key>_b.metal` (source), optionally `<key>_bf.metallib` (readable-tile variant for pass
  merging). `_b_build.json` records per-key success/failure.
- **`nvn/shader_map.tsv`**: one row per variant; at run time only "key → stage" is used.
- **`nvn/shader_facts.tsv`**: translation facts not readable from machine code (texture dims, image
  formats, vertex-attribute / fragment-output component types, compute threadgroup size + shared
  bytes). Maintained by `tools/dmsh_facts.py`.
- **Versioning**: chain version `V` (12 hex digits) hashes the chain sources + options + tool
  fingerprints (`tools/dmsh_chain.lock`). Libraries live in `b-<V>/`; each reflection json records
  `V`; the host takes `V` from `nvn/dmsh_chain.h`, loads **only** from `b-<V>/`, and verifies each
  json — mismatch = red, no load. So: change any chain file/tool → rebuild libraries **and**
  recompile the host.
  `dmsh_chain.py` prints `V`; `--check-tools`; `--update-lock`; `--header`.
- **`nvn/c1_full.tsv`**: shader keys whose c1 (built-in constant) *run-time* index could not be
  proven bounded (`tools/c1_guard.py`). Those get a dedicated 64 KiB c1 buffer; if the list is
  missing or version-mismatched the replay layer reports red and conservatively treats *all* shaders
  that way. Do not confuse this with the **translation-time** `c1` input, which is the `BRX` jump
  table stored in the code segment (`references/shader-banks.md`).
- **`nvn/pso_manifest.tsv`**: warm-up seed manifest; row order = pre-build order, do not sort.
- **`nvn/nvn_builtin.h`**: MSL hand-written from disassembly for the 4 compute shaders embedded in
  `main` (not in the shader package).

Binding conventions (`tools/maxwell_refs.py` header): uniform buffer `r` = constant section `c[3+r]`;
separate texture `r` handle at `c[2][0x168+8r]`, sampler `r` at `c[2][0x568+8r]`; storage buffer `r`
address at `c[0][base+16r]` (base: vertex `0x110`, compute `0x310`, fragment `0x510`). Window origin
top-left and depth range 0–1 match Metal, so nothing is flipped.

### Texture formats — `dm/gfx/tex/`

Tegra block-linear deswizzling, format mapping, BC decompression. A16-and-earlier Apple GPUs lack
BC, so those devices need an ASTC supplement pack built from your own files
(`dm/tools/astc_pack.py`, `casc_overlay.py`; see `docs/LEGACY_DEVICES.md`).

---

## ⑤ Shell

**Mac** — `dm/host/front_mac.m` (window, keyboard, mouse) + `dm/host/app_mac.m` (the `.app`, resource
folder picker). Packaged by `dm/tools/mac_app.sh`. On a Mac the loader can map the images normally;
no embed is needed.

**iOS** — `dm/ios/` packs host + gfx + rewritten program into one app. iOS forbids run-time code,
so:

- `tools/gen_embed.py` turns the rewritten images into `Gen/embed.S`, splitting each module into
  three blocks (Apple ITMS-90999 allows only one executable segment):
  `__TEXT,__dm<name>x0` (original text), `__DM<name>W,__img` (RW: rest of ro + data + bss),
  `__TEXT,__dm<name>x1` (patch area). They are non-adjacent in the file; at run time
  `loader.c` (iOS branch) reserves one contiguous range and `vm_remap`s them back to their original
  relative positions — so layout is byte-identical and no PC-relative addressing needs fixing.
- `tools/gen_build.py` derives the host's source list, header paths and frameworks from what
  `dm/host/Makefile` actually expands to, and emits `Gen/dm.xcconfig` + `Gen/src/`. The host's
  `main.c` is compiled with `-Dmain=dm_main`.
- `Sources/main.m` is the entry: `-mode <name>` (default `boot` = start the game). On boot it raises
  the fd soft limit, opens `Documents/boot.txt`, checks BC support, locates the game assets, sets the
  host env vars (`DM_ROMFS`, `DM_ROMFS_OVERLAY`, `DM_SAVE`, `DM_TEMP`, `DM_MSL_DIR`), `chdir`s to
  `Documents`, calls `dm_host_preload_start`, installs the Metal view, then `boot_thread` (64 MB stack)
  does iCloud sync and calls `dm_main`. Diagnostic modes: `anon`, `mapjit`, `file` (prove the
  no-run-time-code restriction), `tsd`, `msl` (load each metallib), `blank` (heat baseline), `thermal`.
- **Asset lookup order** (`romfs_locate`, validated by `dm/host/romfs_validate.c` — all 18 archives
  present with matching byte counts): ① `Library/Application Support/romfs` in the container
  (dev path) ② a folder in `Documents` ③ an Apple-hosted Background Assets pack named `romfs`
  (`RomfsDownloader/`) ④ none → the asset screen with progress/retry.
- **Touch** is a pure-C state machine (`Sources/dm_touch_controls.c`, no UIKit) classified into
  gamepad buttons/sticks + raw touch + a UI cursor, published via `dm_touch_set` / `dm_input_set` /
  `dm_cursor_set_screen`. The game's own UI widgets are reused wherever possible; only the stick and
  skill buttons are drawn on top (`Sources/DmTouchOverlay.m`, draw-only).
- **Overlay RomFS**: a small RomFS shipped with the app; the host checks it *before* the real game
  files, so UI layout/textures can change without touching the player's files. Generated by
  `tools/gen_romfs_overlay.py` from your game files; the repo stores only the diffs (layout patches,
  string replacements keyed by name with original-text checksums, and new textures written to
  borrowed old paths in `dm/data/casc_overlay/borrowed_paths.tsv`).

---

## Verification — `dm/verify/`

`regress.sh` (rewrite equivalence) does three things: `structck.py` (structural), `equiv.py`
(before/after equivalence of **every** rewritten site, run under Unicorn), and `gatefn` (host gate
functionality). `accept.sh [--fast|--full]` runs the full acceptance set. The test tooling is part of
the project, not an afterthought — treat the regression gates as mandatory before/after any change.
