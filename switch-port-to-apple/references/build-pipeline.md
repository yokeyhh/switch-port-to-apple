# Build pipeline & commands

Everything runs on an **Apple silicon Mac** with **Xcode** (the graphics layer uses Metal 4, so
macOS/iOS 26+ for the default build). `DM_ROOT` is the workspace root, default `~/dm-work`; the
game files, build outputs, caches and run logs all live under it and are never committed.

## Workspace layout

```
$DM_ROOT/
  data/switch/3.3.94012/Update/
      ExeFS/{main,sdk,subsdk0}          ~26 MB
      RomFS/data/build_manifest.psv     \
      RomFS/data/*.data                  ~8.4 GB
  build/cache/dm/out/<codename>-<kind>/ rewritten images + tables
  build/cache/metal_shader_cache/b-<V>/ Mac shader libs
  build/ios/dmsh_b/b-<V>/               iOS shader libs
  build/cache/dm/venv/                  Python env (pillow numpy lz4 capstone unicorn)
  out/macapp/D2R Mobile.app             Mac app
  run/ios/                              iOS derived data, logs, pulled-back device logs
```

## Getting the game files

Own the game. Dump your own copy (Switch: **nxdumptool**, pick the game's **Update**, dump ExeFS +
RomFS) or use an existing `.xci`/`.nsp`:

```sh
dm/tools/extract_game.sh <game package> <your prod.keys>     # needs hactool on PATH (or HACTOOL=<path>)
```

Then confirm placement with `./setup.sh check` (changes nothing, verifies the three program files
are the expected version).

**Gotchas seen in practice (D2R):**
- If you have **two** packages (base `.nsp` + `[UPD]` `.nsp`) rather than one combined one,
  `extract_game.sh` will not do it: it wants one package containing both and refuses when the
  `Update` dir already exists. Extract each with hactool directly and hash-verify the ExeFS.
- Titlekey-encrypted packages need the **title keys** recovered from the tickets, placed in the
  file hactool actually reads: `~/.switch/title.keys`. Base and update have *different* title keys.
- A malformed `prod.keys` (e.g. 17-byte `master_kek_source_*` entries) makes hactool refuse the
  **whole file**. Make a cleaned copy for `~/.switch/prod.keys` instead of editing the original.
- `[WARN] prod.keys does not exist.` referring to `~/.switch/prod.keys` is benign for extraction.

## Step 1–2: prepare the workspace

```sh
./setup.sh check          # health check: computer, tools, game files, signing config
./setup.sh                # do the work (≈11 min first time; skips finished steps)
```

`setup.sh` performs, in order (each idempotent; delete a step's output dir to redo it):

1. **Check** the machine (Apple silicon, macOS, free disk), tools (Xcode + Metal compiler,
   `xcodegen`, `spirv-cross`), game files, signing config.
2. **Python env** — venv with `pillow numpy lz4 capstone unicorn`.
3. **Rewrite the main program** — `dm/tools/product.sh build` → three build-output kinds.
4. **Fetch the shader package** (162 `.bin` files) out of RomFS via `dmx.py`.
5. **Build the shader translator** — `dotnet publish dm/gfx/tools/dmsh_xlate`; needs the .NET runtime
   (`export DOTNET_ROOT="$(brew --prefix dotnet)/libexec"` on Homebrew).
6. **Re-register the translation chain** if local tools differ from the lock file
   (`dmsh_chain.py --update-lock` + `--header`). *This is normal* — a different Xcode or
   `spirv-cross` version. It rewrites three repo files; **do not commit them**.
7. **Translate all shaders** — `dmsh_build.py` (Mac + iOS). Completion is judged by the
   per-shader results in `_b_build.json`, not the marker's existence.
8. **Generate the c1 index list** — `c1_guard.py`.

Finish by entering the venv the build scripts need:

```sh
source $DM_ROOT/build/cache/dm/venv/bin/activate
```

## Step 3: build the app

### iOS / iPadOS

```sh
cd dm/ios
cp local.env.example local.env        # then fill DM_TEAM_ID and DM_BUNDLE_ID
./run.sh build                        # generate Gen/ + project, compile, sign (no device needed)
./run.sh install                      # install to $DM_DEVICE
./run.sh boot 120                     # launch on device with -mode boot, pull back Documents/boot.txt
```

`build` does, in order: assets derived from your game files (`gen_assets.py`) → host table headers
→ `gen_build.py` / `gen_embed.py` → copy shader libs + manifests → generate the overlay →
`xcodegen generate` → `xcodebuild` (Release, automatic signing). Log: `$DM_ROOT/run/ios/build.log`;
app: `…/Build/Products/Release-iphoneos/DmIOS.app`.

Key env vars: `DM_OUT` (rewrite output dir), `DM_IOS_DD` (derived data), `DM_ROMFS_SRC` (original
RomFS), `DM_GEN_ONLY=1` (generate only), `DM_ASTC_PACK` (Legacy Device Pack dir), `DM_DEVICE_FAMILY=1`
(iPhone only), `DM_FREE_ACCOUNT=1` (strip un-signable entitlements).

### macOS

```sh
dm/tools/mac_app.sh                   # → $DM_ROOT/out/macapp/D2R Mobile.app  (macOS 26+)
DM_MAC_M3=1 dm/tools/mac_app.sh       # older-API build (macOS 15+), separate name/bundle ID
```

- `DM_MAC_LINK=1` (default) ships only the host + `Resources/paths.txt` — runs only on the machine
  that built it. `DM_MAC_LINK=0` makes a **self-contained** app (bundles the rewritten program,
  shader libs and overlay) — but those are derived from game content, so a public release must not
  carry them.
- Game resources are *not* in the package: on first launch the app asks for the resource folder
  (`host/app_mac.m`). The path is remembered in the app's user defaults.
- On a Mac the images are mapped normally; no `gen_embed` needed.
- If the shader libs were built for a newer target OS than the app's minimum, recompile them:
  `bash dm/gfx/tools/msl_retarget.sh <min-os> <out-dir>` and pass `DM_MSL_DIR=<out-dir>`.

## Putting game data on the device (iOS)

The 18 RomFS archive files (`00-…`..`05-…`, `17-…`, about 5.6 GB) are **not** in the install package.
Push them into the app container:

```sh
xcrun devicectl device copy to --device <dev> \
  --domain-type appDataContainer --domain-identifier <bundle-id> \
  --source $DM_ROOT/data/switch/3.3.94012/Update/RomFS \
  --destination 'Library/Application Support/romfs'
```

Alternatively drop a folder into the app's `Documents` (Files app) or use the hosted asset pack.
`romfs_locate` + `romfs_validate` check all 18 archives and their byte counts.

## Signing

Signing config lives in `dm/ios/local.env` (**not committed**), read via `tools/local_env.sh`
(search order: `$DM_LOCAL_ENV` → `dm/ios/local.env` → `~/.config/dm-ios/local.env`; **only the first
existing file is read, not merged**).

- **Paid account:** fill `DM_TEAM_ID` + `DM_BUNDLE_ID`; build against the generic iOS device.
- **Free (personal) account:** set `DM_FREE_ACCOUNT=1`. It removes entitlements the free tier cannot
  sign — **iCloud save sync, sustained execution mode, higher memory limit** — and the asset-download
  extension. It also builds against the *connected device* (`DM_IOS_UDID`) because a fresh free team
  has no devices to generate a profile from.
  The profile **expires after 7 days**: the app stops launching and must be rebuilt + reinstalled.
- Xcode's Team ID can be read from `~/Library/Preferences/com.apple.dt.xcodebuild.plist`
  (`IDEProvisioningTeamByIdentifier`), but it is only populated after a first failed build attempt.

## Verification

```sh
make -C dm/host/tests run
make -C dm/gfx/tests run
make -C dm/ios/tests test
dm/verify/regress.sh [--fresh|--struct-only] [build-output-dir] [modules…]
dm/verify/accept.sh [--fast|--full]
```

`regress.sh` needs `brew install llvm`; `accept.sh` additionally needs reference run data and
standard saves that the repository does not ship.

## Reading a run's log

The iOS app writes `Documents/boot.txt` (line-flushed; the previous run is renamed to
`Documents/boot.previous.txt`), and the host's stderr goes there too. Pull it with `devicectl device
copy from` (or `./run.sh <mode>` does it for you). Useful greps:

- `UNIMPL ` — imports the new title needs implemented.
- `【红】` — red reports (unimplemented NVN, missing shader, version mismatch, …).
- `紫闪探针 … 画面品红 0 次` — the missing-shader probe (magenta 0 = clean).
- `上屏 至帧 N` — presentation stats (frame pacing, dropped frames, footprint).
- `帧 N：` — per-frame NVN call tables.

> `devicectl device capture screenshot` returns **black for the game's Metal layer** (it captures
> UIKit fine, but not the NVN/Metal output). Use the on-device log as the source of truth; take
> screenshots from inside the app (`DM_NVN_SHOT=frame:path`) when you need a real image.

## Bringing it to a Windows/Linux box (no Xcode)

`docs/NO_XCODE.md`: download the prebuilt "shell" from Releases (contains no game content), add your
game files, make the install package with `dm/tools/d2rm_pack.py`, sign with a sideloading tool. On
this route only, the **first launch compiles shaders on the phone** (~7 min, once) because the
packaging machine has no Apple shader compiler. `spirv-cross` must match the shell's registered
version or `d2rm_pack.py` stops.

### Adapting build_ipa.sh to a project that is not in the NSP-work layout

`build_ipa.sh` expects `W` = `<NSP>/<id>-work` (with `build/patched_<PKG>`, `data/<PKG>/RomFS`,
`data/v0/control.nacp`, `icon.jpg`). A project that keeps things in its own layout does **not** need
that directory rebuilt: every path is overridable by environment variable.

```sh
cd dm/ios
export GAME=<title id> \
  DM_DISPLAY_NAME='<name>' DM_APP_VERSION='1.0.0' \
  W=$MY_WORK_FOLDER \
  PATCHED=$MY_WORK_FOLDER/rewrite-guest \
  ROMFS=$MY_WORK_FOLDER/romfs \
  MSL_IOS=$MY_IOS_SHADER_DIR \
  FONT_PY=$VENV/bin/python \
  DM_TEAM_ID=0000000000
bash tools/build_ipa.sh out/<name>-ios.ipa
```

- `DM_DISPLAY_NAME` / `DM_APP_VERSION` are **required when there is no `control.nacp`** (else the script dies).
- `FONT_PY` must be a python that has `fontTools` (`make_ext_font.py` needs it; `venv/bin/pip install fonttools`).
- `DM_TEAM_ID=0000000000` is a placeholder → an unsigned IPA; the install tool (AltStore/Sideloadly)
  swaps in your own certificate/bundle ID.
- The bulk of the IPA is the packed RomFS; an uncompressed-app (no device build) is the fastest feedback loop.

### iOS shader library: recompile the same MSL for iphoneos, same keys, reuse the Mac reflection

The host keys a shader by `<key>_b.metallib`. The Mac pack copies a pre-compiled (macOS) metallib under
that name; **iOS just recompiles the same MSL for iphoneos under the same key**. The `_b.json` reflection
is platform-neutral — its `chain` field is only the chain-version string, it records no compile command —
so copy the Mac json unchanged.

```sh
xcrun -sdk iphoneos metal -c -mios-version-min=18.0 \
  -fmetal-math-mode=relaxed -fpreserve-invariance <stem>.metal -o <key>.air
xcrun -sdk iphoneos metallib <key>.air -o <key>_b.metallib
```

`-fmetal-math-mode=relaxed` is **mandatory** (keeps fast math but forbids assuming no NaN/Inf: the
translated shaders reproduce Maxwell NaN semantics with `isnan` selects, and default `fast` lets the
GPU backend fold them to false → black band/glow). `-fpreserve-invariance` keeps `[[invariant]]`
position outputs consistent across shaders. A bridge that enumerates `(key, stem)` from the title's
packer (`collect()` + dedup by key) and compiles each for iphoneos yields a full iOS library in one pass.
