# Code reuse map — what is engine, what is title-specific

Paths are relative to the repo root. Classification is derived from the repo's own documentation
(`dm/README.md`, `dm/gfx/README.md`, `dm/ios/README.md`, and README's "Taking it to other games").

Legend: **ENGINE** = reusable for another Switch title as-is or with light parameterisation ·
**ADAPT** = reusable *machinery*, but its tables/constants must be regenerated or re-authored per
title · **TITLE** = written for D2R specifically; remove or replace for a new game.

---

## Top level

| Path | Class | Notes |
|---|---|---|
| `setup.sh` | ENGINE | One-command workspace prep + health check (`./setup.sh check`). Pipeline in `references/build-pipeline.md`. |
| `dm/tools/extract_game.sh` | ADAPT | Unpacks `.xci`/`.nsp` via hactool; assumes one package with base+update and refuses if the `Update` dir exists. |
| `dm/PRODUCT` | ADAPT | One-line build codename. |
| `docs/` | ENGINE | Per-part docs; `DERIVED.md` explains what derived material is/isn't shipped. |

---

## `dm/tools/` — rewrite & build

| Path | Class | Notes |
|---|---|---|
| `patch_nso.py` | ADAPT | The generic rewriter **mechanics** are engine. `CUTS` / `OBS` / `PARAMS` are keyed by NSO build ID and are **title+version specific** — author your own. |
| `nso.py` (14 lines) | ENGINE | NSO container parsing. |
| `product.sh` | ENGINE | Produces the three build-output kinds. |
| `dmx.py` | ENGINE | Reads the game's resource storage (RomFS-style archive). |
| `casc_overlay.py` | ENGINE | Rewrites the resource storage at build time (pack/unpack). |
| `nso.py`, `x18.py`, `l3reg.py`, `l3seg.sh`, `l3run.sh`, `sideeff.py`, `sweep.py`, `fn.py`, `xref_sdk.py`, `hle_inventory.py`, `sdis.py`, `lockalign.py`, `mini_sync.sh`, `sampagg.py`, `cat18.py`, `frameprobe.sh`, `winlf/`, `tp.c`, `blackcols.py` | ENGINE | Static-analysis / logging / sweep helpers for reading the guest program. |
| `guest_dylib.py` | ADAPT | Guest dynamic-library helper. |
| `astc_pack.py` | ADAPT | Builds the ASTC supplement pack from *your* game files (A16-and-earlier GPUs). |
| `mac_app.sh` | ENGINE | Wraps the Mac host into a double-clickable `.app` (supports `DM_MAC_M3=1` older-API build). |
| `gen_bright_levels.py`, `d2rm_pack.py` | ADAPT | `d2rm_pack.py` also drives the no-Xcode packaging route (`docs/NO_XCODE.md`). |
| `save_txn_regress.sh`, `host_savecmp.sh`, `host_war13.sh`, `conc_watch.sh`, `frameprobe.sh` | TITLE/ADAPT | Test harnesses tied to the game's save format / content. |

---

## `dm/host/` — loader, HLE, Mac front end

**Loading / execution — ENGINE**

| File | Role |
|---|---|
| `main.c` | Bootstrap: load modules, init SDK runtime in SDK order, run `init_array`s, enter `nnMain`. *Has one TITLE bit: the save-flush throttle patches a fixed address, and `argv[0]` is hard-coded to `"D2R"`.* |
| `loader.c` | Multi-module loading, thread slots, gate registration, cross-module relocation, takeover by name, segment permissions. |
| `entry.S` | `dm_gate` common entry + the named-stub table for unimplemented imports. |
| `hle.c` | `resolve_import` and `gate_dispatch`. |
| `thread.c` | Thread-slot init per game thread. |
| `hle.h`, `dm.h` | `HLE` / `HLE_FORCE` macros, module table, logging, host-call registry, `OBS_*` numbers. |
| `hle_svc.c` | Kernel syscalls: address space/heap, queries, debug output, timing. |
| `hle_thread.c` | Thread & sync syscalls. |
| `hle_guestthread.c` | Host-spawned game threads carrying a full SDK thread context. |
| `hle_table.c` | Pass-through table for libc functions. |
| `hle_os.c` (**ADAPT**) | Odds and ends: guest→host object side table, clocks. |
| `hle_sdkinit.c` | Takeover of system-service init at SDK startup. |
| `romfs_validate.c` | Startup check of the game resource dir (archive set + byte counts). |
| `crash.c`, `diag.c`, `memstat.m`, `hle_count.c`, `hle_fdstat.c` | Crash printing, watchdog, probes, call counts, fd stats. |
| `front.h`, `front_mac.m` (**ADAPT**) | Front-end contract (ENGINE) + Mac implementation. `front_game_res` hard-codes 1600×900 / 1280×720 docked/handheld. |
| `dm_input.h` | State the front end writes for the input layer. |

**System services — mostly ENGINE, some TITLE**

| File | Class | Notes |
|---|---|---|
| `hle_fs.c` | ADAPT | Mount names → host dirs. TITLE defaults on iOS: `DM_STASH_SEED` and `DM_TOUCH_DEFAULTS` are on by default. |
| `hle_tact.c`, `hle_granny.c` | ADAPT | zlib block decompression / Granny section decoding — general, but cut addresses are title-specific. |
| `hle_audio.c`, `hle_codec.c` | ENGINE | Audio output + Opus decode. |
| `hle_hid.c`, `hle_touch.c` | ENGINE | Gamepad + touch input. |
| `hle_swkbd.c`, `hle_charname.c` | ENGINE | On-screen keyboard; Chinese allowed in names. |
| `hle_oe.c` | **ADAPT (danger)** | Operation mode (handheld/docked). iOS default 3D render scale 0.75 and `scale3d_tick` rewrite **offsets inside D2R's renderer** — a wild pointer for another game. Turn off / move out first. |
| `hle_time.c` | ADAPT | Clocks; the fixed-time test switch recognises a fixed address in the game program. |
| `hle_movie.c` | ENGINE | Cutscenes: not decoded, played empty for their duration. |
| `hle_net.c`, `hle_friends.c`, `hle_misc.c` | ENGINE | Online/friends → unavailable; the game takes its offline branch. |
| `hle_account.c`, `hle_lifecycle.c` | ADAPT | Local user; exit flow. |

**In-game features — TITLE (all `dm_*.c`)**

Every `dm_*.c` is a feature/touch adaptation layered on top of the original game, and its header
comment states which addresses in the 3.3.94012 image it relies on. All addresses are offsets into
the original image → **all of these are title-specific**:

`dm_actions.c`, `dm_autobuff.c`, `dm_beltauto.c`, `dm_bossrush.c`, `dm_bright.c`, `dm_cloudmerge.c`,
`dm_cloudsync.c` (chars recognised by `.d2s`), `dm_cursor.c` (whole file game-specific),
`dm_cursor_item.c`, `dm_custom.c` (feature switches), `dm_doorspot.h`, `dm_dropfilter.c`,
`dm_drophaptic.c`, `dm_elysium.c`, `dm_freetp.c`, `dm_hud.c`, `dm_i18n.c`, `dm_kills.c`,
`dm_lootfilter.c`, `dm_namegen.c`, `dm_padmode.c`, `dm_pages.c`, `dm_potpick.c`, `dm_pouch.c`,
`dm_prof.c`, `dm_rift.c`, `dm_rift_ui.c`, `dm_runeword.c`, `dm_rwlv_core.c`, `dm_shoprefresh.c`,
`dm_textbox*.c`, `dm_textinput.c`, `dm_townwp.c`, `dm_vault*.c`, `dm_world.c` (whole file
game-specific), `dm_private_aim.c`, and the rest.

> For a new title: delete the `dm_*.c` gameplay features and the host-call registry entries in
> `hle.h` (`SVC_HOST_*`), then keep the reuse of `hle_*` machinery only.

---

## `dm/gfx/` — NVN → Metal

| Path | Class | Notes |
|---|---|---|
| `nvn/hle_nvn.c` | ENGINE | Interface layer: takeover, object impls, command recording, pacing. |
| `nvn/nvn_mtl.m`, `nvn_mtl.h` | ENGINE | Replay layer: device, pools, textures, shaders, pipelines, draw, present. |
| `nvn/nvn_obj.h` | ENGINE | Host object layouts + command record format (shared by both layers). |
| `nvn/nvn_entry.S`, `nvn_thunks.S` | ADAPT | Unimplemented stub entry, timing wrapper, per-function thunks — **regenerate** per title. |
| `nvn/nvn_tex.c/.h` | ENGINE | Block-linear storage size/alignment/level offsets (ported from deko3d). |
| `nvn/nvn_dirty.c/.h` | ENGINE | Write-protected dirty-page tracking → texture re-upload. |
| `nvn/nvn_refl.m/.h` | ENGINE | Reflection json → fixed C arrays. |
| `nvn/nvn_hmap.h`, `nvn_cover.h`, `nvn_pace.h`, `nvn_pso_skey.h` | ENGINE | Hash table, full-coverage proof, pacer, pipeline manifest key. |
| `nvn/nvn_builtin.h` | **ADAPT** | Hand-written MSL for the 4 compute shaders embedded in D2R's `main`. |
| `nvn/include/nvn.h` | ENGINE | NVN interface declarations (third party). |
| `nvn/*.tsv`, `names.txt`, `dmsh_chain.h` | **ADAPT** | Generated tables + chain version; regenerate per title. |
| `tex/` | ENGINE | Texture formats, deswizzling, BC decompression. |
| `metal/` | ENGINE | Standalone minimal offscreen Metal example (not in the host). |
| `tools/dmsh_xlate/` | ENGINE | The C# shader translator (references `third_party/ryujinx`). |
| `tools/dmsh_build.py` | ENGINE | Drives the whole chain; `IOSOUT=` also emits the iOS variant. |
| `tools/dmsh_chain.py`, `dmsh_chain.lock` | ENGINE | Chain version + tool fingerprints. |
| `tools/dmsh_facts.py` | ADAPT | Builds/checks the translation fact table (per title). |
| `tools/dmsh_fetch.py` | ENGINE | Readable-tile fragment shader variants. |
| `tools/c1_guard.py` | ENGINE | Generates `c1_full.tsv`. |
| `tools/dmsh_dryrun.py` | ENGINE | Dry-run "can it be converted" over the library. |
| `tools/shader_map.py`, `maxwell_refs.py` | ADAPT | Shader map / machine-code resource references (conventions in the header). |
| `tools/nvn_slots.py`, `gen_protos.py` | ADAPT | Generate `nvn_slots.tsv`/`names.txt` and `nvn_protos.h`/thunks — **per title**. |
| `tools/tbdr_*.py`, `passgpu_top.py` | ENGINE | Pass load/store analysis, GPU time by pass. |
| `tools/nometal_run.py` | ENGINE | Run the chain without a Metal compiler (ship MSL as source). |
| `tools/msl_retarget.sh` | ENGINE | Recompile shader libs for an older min-OS. |
| `hle_vi.c`, `hle_nv.c` | ENGINE | Display-service / graphics-driver init glue. |

---

## `dm/ios/` — iOS shell

| Path | Class | Notes |
|---|---|---|
| `run.sh` | ENGINE | `build` / `install` / `<mode> [secs]`. Pipeline in `references/build-pipeline.md`. |
| `project.yml` | ADAPT | XcodeGen definition (3 targets + signing capabilities). `tools/project_free.py` strips free-account-un-signable entitlements; `tools/project_m3.py` makes the iOS-18 older-API variant. |
| `local.env.example` | ENGINE | Template for `local.env` (`DM_TEAM_ID`, `DM_BUNDLE_ID`, `DM_DEVICE`, `DM_IOS_UDID`…). |
| `Sources/main.m` | ADAPT | App entry, modes, logging, asset location, lifecycle. |
| `Sources/front_ios.m` | ENGINE | iOS `front.h` impl: Metal view, touch collection/publication. |
| `Sources/dm_touch_controls.[ch]` | ENGINE | Pure-C touch classification state machine (unit-testable without a device). |
| `Sources/DmTouchOverlay.[hm]` | ADAPT | Stick + skill buttons (draw-only). |
| `Sources/dm_touch_game_*.c` | **TITLE** | Reads skill bindings read-only from the running D2R game. |
| `Sources/asset_dl.[hm]`, `cloud_ios.m`, `dm_update.m`, `dm_sharelog.m` | ENGINE | Asset download, iCloud save sync, version notice, share log. |
| `Sources/TouchArt/`, `NativeHUD/` | ADAPT | Self-drawn control art (repo holds only the stick image + manifest; the skill atlas is generated from game files). |
| `RomfsDownloader/` | ENGINE | Background-Assets download extension for the `romfs` pack. |
| `TouchSmokeTests/`, `tests/`, `tools/` | ENGINE | XCTest UI tests, unit tests, generation scripts. |
| `tools/gen_embed.py`, `gen_build.py`, `gen_assets.py`, `gen_romfs_overlay.py`, `layout_patch.py`, `touch_layout.py`, `touch_strings.py`, `native_art/` | ADAPT | Build-time generation. **`gen_assets.py` derives repo-content-free assets from *your* game files.** |
| `tools/devlock.sh`, `devbattery.sh`, `local_env.sh`, `logport.py` | ENGINE | Shared helpers: device lock, battery check, local config, log port. |
| `tools/touch_e2e*.sh`, `charsel_retap.sh`, `lifecycle.sh`, `screen_check.py`, `fullscreen_check.py`, `bootprof.sh`, `heat_ab.sh`, `gpu_read.sh`, `xtrace.sh` | ADAPT | Real-device measurement/regression harnesses. |
| `tools/uireg/` | ENGINE | Scripted touch-UI regression. |
| `docs/` | ADAPT | JSON specs (touch layout, skill bindings) referenced by checksum. |
| `Gen/`, `DmIOS.xcodeproj/` | — | Generated at build time; never committed. |

---

## `dm/verify/`

| Path | Class | Notes |
|---|---|---|
| `regress.sh` | ENGINE | Rewrite equivalence: `structck.py` + `equiv.py` (Unicorn, every site) + `gatefn`. |
| `accept.sh` | ENGINE | Full acceptance run (needs reference run data + standard saves not shipped). |
| `gatefn/`, `gfxq/`, `logicself/`, `oracle/`, `touchcases/`, `shotgate_pages/` | ENGINE | Verification sub-tooling. |

---

## `dm/third_party/`

`libdeflate` (zlib), `bcdec` (BC decode), `flip` (image), `ryujinx` (shader translation library
referenced by `dmsh_xlate`). All third-party; see `docs/DEPENDENCIES.md`.

---

## Quick "reuse this, rewrite that" summary

**Keep the engine, change the data:**
- `patch_nso.py` mechanics + `hle*.c` machinery + all of `gfx/nvn/` + `gfx/tools/` chain +
  `ios/tools/gen_*.py` + `run.sh` / `mac_app.sh` / `setup.sh`.

**Regenerate per title (never hand-edit):**
- `CUTS` / `OBS` / `PARAMS` in `patch_nso.py`; `nvn_slots.tsv`, `names.txt`, `nvn_protos.h`, thunks;
  `shader_map.tsv`, `shader_facts.tsv`, `c1_full.tsv`, `pso_manifest.tsv`, `b-<V>/*`;
  `hle.h`'s `SVC_HOST_*` registry; `Gen/`, `DmIOS.xcodeproj`.

**Remove or gate behind per-game config (the "another game runs into these first" list):**
- `hle_oe.c` 3D-scale + `scale3d_tick` renderer offsets
- `hle_fs.c` `DM_STASH_SEED` / `DM_TOUCH_DEFAULTS`
- `main.c` save-flush throttle address + `argv[0]="D2R"`
- `hle_time.c` fixed-address test switch
- `front_game_res` hard-coded resolutions in `front_mac.m`
- `dm_cloudsync.c` `.d2s`, and all of `dm_cursor.c` / `dm_world.c`
- all other `dm_*.c` gameplay features
