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
  D2R-era rule is a property of D2R's container, not of NVN). In the exported code file (SPH first),
  search `[0x50:]`, take a conforming run's **start**, require `(T − 0x50) % 16 == 0`, and keep only
  as many entries as the translator actually reads (a run can swallow the trailing `0x4000`
  constant). Entries are absolute code addresses (`0x50 + word`), not offsets from a large base.
  Full algorithm, the four independent checks and a control experiment: `references/shader-banks.md`.
- **A long `c1` run has conforming positions inside it.** Tables for several `BRX` sites are
  concatenated, so scanning backwards for "an aligned position whose next N words look like a table"
  selects a **mid-run** start and shifts every case by 4–5 entries. Take the run start. Likewise, do
  not assume the table ends at the file end — uniform constants (`0x3f555556`, `0x4000`, …) follow it
  and `0x4000` passes a naive "8-aligned, small, non-zero" test.
- **The key includes the `0x30`/`0x100` stage header.** The host hashes
  `sha256(control[0:nctl] ‖ code[0:ncode])`, where `code` starts at the stage header — not at the SPH.
  `nctl` is `min(0x880, u32@control+0x20)`. A key computed another way (e.g. without the header, or
  a hard-coded `0x880`) will never match the package and every shader "mysteriously" misses.
- **A bank is not the whole shader set.** Later areas, characters and menus often build NVN programs
  at run time. Expect to dump them from the host and pack them with the same chain —
  `references/runtime-shaders.md`.

## A fragment output's *type class* must match an integer attachment

Translated MSL happily contains `out.m_31.x = as_type<float>(_94);` — a bit-pattern move whose
value is an `int` 0…255. If the attachment is an integer format (`NVN 0x3 → MTLPixelFormatR8Uint`,
RGB10A2_Uint, …) the pipeline **fails to build**, once per subpass, and every draw in that pass is
skipped. The host log line is unambiguous:

```
output of type float4 is not compatible with a MTLPixelFormatR8Uint color attachment
```

Metal has no implicit conversion in either direction, so the *declaration* has to change:
`float4 m_31 [[color(1)]]` → `uint4 …`, and `as_type<float>(i)` → `uint(i)`. The translator usually
exposes this as a per-target control (this chain: `"fout": {"<target>": "float|sint|uint"}`, target
index taken from the reflection's `raster.colors`). One shader, one recompile:

```bash
xcrun -sdk macosx metal -c fixed.metal -o fixed.air
xcrun -sdk macosx metallib fixed.air -o <key>_b.metallib   # drop into pack + runtime msl dir
```

Do **not** dodge this by binding a same-width float-format view of the attachment: `as_type<float>(255)`
is a denormal, so a UNORM write stores 0 and the pass silently writes nothing (which is exactly the
"input is empty, so the effect is a no-op" symptom you were trying to fix).

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
- **Implement every `Get` that pairs a `Set`.** Titles read state back and reuse it (channel mask,
  depth-stencil, multisample, polygon, color/alpha-ref, vertex-attrib/stream). A getter left as a
  zero-returning stub does not just log — it makes the game build a *different* pipeline than it
  intended, and the log floods with one red line per draw. Mirror the SDK's object layouts
  (`nvn_obj.h` in nxnative) and the log goes quiet.
- **Vertex topology is per-shader, not global.** A vertex shader that writes
  `render_target_array_index` needs a Metal `inputPrimitiveTopology` other than `Unspecified`; but a
  shader that writes `[[point_size]]` fails if you force `Triangle`. Set the topology from the MSL
  itself (does it declare `[[point_size]]` / `[[render_target_array_index]]`?) rather than one global
  choice, or half the pipelines fail either way.
- **`[[render_target_array_index]]` needs `renderTargetArrayLength` — or every layer write lands in slice 0.**
  When the reflection says a stage writes the layer index (NVN's `gl_Layer`; the `rt_array` field), the
  render pass must span the layer range. Metal's `renderTargetArrayLength` defaults to **1**, so leaving
  it unset does not error: all instance/layer writes are clamped into slice 0 and slices 1..N keep
  whatever the texture was created with (usually zero). The failure looks nothing like a layer bug —
  one real case was a 32³ RGB10A2 colour-grading LUT that got only slice 0 filled, so the tone-mapping
  pass sampled empty slices, produced an exactly-zero sRGB buffer, and the whole **indoor** scene was
  black while outdoor and UI rendering were perfect. NVN has no per-RTV layer count: take the range from
  the texture itself (3D → `depth`, 2D array → `arrayLength`). Set it unconditionally for 3D colour
  attachments; for 2D arrays only when a stage actually writes the layer index, otherwise a plain clear
  clears array layers the game never asked for. Make the flag **sticky for the whole pass** — the
  clear-only and reopen paths must attach the same range the first `rp_begin` did.
- **An NVN texture view in `STENCIL` depth-stencil mode samples the *stencil* plane - and Metal cannot
  sample a stencil plane at all.** `nvnTextureViewSetDepthStencilMode(view, NVN_TEXTURE_DEPTH_STENCIL_MODE_STENCIL)`
  is the easiest of the view setters to leave unimplemented: `set_format`, `set_target` and `set_swizzle`
  are obviously visible in the rendered result, this one is not. Recording it and then reading the
  *depth* plane instead produces a very specific symptom: the shaders that use such a view read the
  sampled value's **raw bits** (`as_type<uint>` then `extract_bits`, a packed per-pixel byte - bit 6 =
  "an effect pass owns this pixel", other bit fields = material classes), so the depth's low bits drive
  `discard_fragment()` and the scene gets **high-frequency stripes along depth contours** (one real
  case: 49.9% of the pixels had depth bit 6 set, and 38.5% of the scene HDR held exactly the clear
  value). Confirm it in one dump: `MTLBlitOptionStencilFromDepthStencil` out of the combined
  depth-stencil texture shows a clean packed mask (`0x00` 23.9% / `0x01` 0.4% / `0x20` 75.7%, bit 6
  clear everywhere) where the depth plane looks like a smooth depth buffer.
  The fix is a **mirror**: blit the stencil plane into a buffer, then run your own small compute kernel
  that writes a sampleable texture. Two details decide whether it works:
  - **Store `as_type<float>(0x3F800000 | byte)`, not the byte value and not the normalised byte.** Every
    bit the shader inspects is then the stencil's, the value stays an ordinary ~1.0 float (the byte sits
    in the mantissa's low bits, so nothing is denormal and can be flushed), and - the reason to prefer
    this over an `R8Uint` mirror - the **declared type stays `texture2d<float>`**, so there is no
    undefined pixel-format/type mismatch (bind an integer format to a float-typed argument and the fetch
    behaviour is not specified).
  - **Refill it after the pass that stores the stencil**, and force that attachment's store action to
    `Store` (the redundant-attachment analysis will otherwise pick `DontCare` and the blit reads
    discarded content). Until the first refill, return a 1x1 "all-zero mask" texture rather than the
    uninitialised mirror. A pass that writes the stencil *and* samples it as a mask in the same draw
    still reads the previous content; no game has been seen to do that.
- **Print the *registered* NVN format and the view flags in your binding dump, not just the resolved
  Metal format.** One storage can be registered twice, as two texture objects with two different views
  (the depth and its stencil plane above). If the dump only shows the resolved format (`fmt=260`), both
  bindings look identical and the bug is invisible; adding `nfmt` / `vfmt` / the `set_*` flags (and
  especially the depth-stencil mode) makes it one line: `ser=12923 nfmt=0x35 vfmt=0x35 ds=1/1` next to
  `ser=12923 nfmt=0x35 vfmt=0 ds=0/-1`.
- **Integer attachments reject float fragment outputs.** A pipeline pairing a `float4` fragment
  shader with an `R8Uint` colour attachment fails to create ("output of type float4 is not compatible
  with a MTLPixelFormatR8Uint color attachment"). The shader's output *index* tells you which
  attachment: a mismatch here is usually an attachment-binding/format bug in the replay layer, not a
  game bug, and it shows up as a skipped pass → black scene.
- **NVN `transform feedback` has no direct Metal equivalent** and is often stubbed to a no-op
  (`nvnCommandBufferBegin/EndTransformFeedback`, `BindTransformFeedbackBuffer`). Some games only use
  it for GPU particles, so the no-op is survivable; check before assuming your black scene is
  unrelated.
- **Do not paper over a missing host table with a guest NOP.** The nv-device texture-view finalizer
  fault is a *host* gap (the nv-device view table). Patching the guest's `main` with `NOP`s hides it
  and makes the clean rewrite diverge from the patched one. Fix the host side and keep the rewritten
  image pristine.
- **`nvnCommandBufferSetAlphaRef` can be a no-op** to get first light; the alpha-test state a game
  actually relies on may still bite later.

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

## Advancing a game headlessly (and what input really is)

A port spends its first weeks before there is a window, a controller, or a person watching. Two host
features pay for themselves immediately:

- **Scripted input by frame number.** An env var that injects button/axis states at given *guest
  frames* (not wall-clock seconds) makes the whole boot sequence reproducible and lets you skip the
  parts you already trust. e.g. `DM_KEYS='f360:A,f600:LLeft~0.8,f960:A*3/200'` — press A at frame
  360, hold the left stick left for 0.8 s from frame 600, then A three times 200 frames apart.
- **Populating a software keyboard from an env var.** A guest-side on-screen keyboard blocks
  headless runs (a windowless run cancels the dialog and loops). Feeding the text from the host
  (`DM_KBD='...'`) unblocks name entry without any UI automation.

Two traps:

- **"The button does nothing" ≠ "the button was not delivered".** Before blaming the input HLE, log
  what the host actually published (a one-line debug print inside the pad-state fill function). If
  the game reads your state and still does not advance, the screen wants something else.
- **The on-screen glyph is not the button.** A prompt that looks like the `L` shoulder button can be
  the **left analog stick** (tilt, not click). One title's "Get Up" prompt accepted `L` in the raw
  input state but never advanced; the left stick up did. Read the glyph as "some input", and if the
  obvious button is a no-op, try the stick directions.

## Shared fonts: the title may never call `Decode`

From SDK 13 the shared font is handed over as **an 8-byte BFTTF header + a word-XOR'd TTF**, so the
game can call `nn::fontll::ScalableFontEngineHelper::Decode` itself. Some titles never do — they pass
`GetSharedFontAddress` straight to `LoadFont`.

Then the header is read as font data, `LoadFont` fails, the font object's character map stays empty,
**every** glyph lookup misses (including the `'?'` fallback), and a text object that only checks the
first lookup dereferences the second, unchecked one:

```
bl glyph_lookup          ; cbz x0, fallback
...
; state 2 → "this cell was already resolved": look the cached id up again
bl glyph_lookup          ; no NULL check
ldrh w0, [x0, #0x4c]     ; SIGSEGV, fault addr 0x4c
```

Fingerprints that make this recognisable in minutes:

- it crashes **only** in CJK languages — Latin text never touches the shared font;
- the crashing lookup's character argument is `0x3f` (`'?'`); no font "lacks" `'?'`;
- the language **list** already renders CJK (that path uses the title's own RomFS font), so this is
  not "missing glyphs" — missing glyphs draw tofu boxes, they do not fault;
- read logging proves the `.bfotf` files in RomFS were read **completely, long before** the crash.

Fix: one line in the title profile — the address points at the TTF itself and the size excludes the
header (same fix as SMBW, which hit the identical wall on SDK 10):

```
shared_font	ttf
```

**How to prove "the file was read" cheaply.** A per-`ReadFile` log (`DM_FSLOG=<tsv>`: time, guest
path, offset, length) plus the archive inventory (`name, offset, size` for every entry inside the
pack) lets you intersect read ranges with files:

```python
# fshits.py <inventory.tsv> <fslog.tsv> [name substring ...]
# 100.0%  x1  @0.41s  arc/applifontbinFOT-UDKakugoC80Pro-B.bfotf.trpak
```

If a file shows 100% coverage *before* the crash frame, the reader/decompressor is not your bug.
Do this **before** instrumenting the decoder — it is one run and one script.

## On-screen controls on a desktop front end

A desktop front end starts as a keyboard map (WASD = left stick, arrows = D-pad, four letters =
ABXY) and that is enough for months. When it becomes a *playability* harness, a mouse-driven
virtual stick pays for itself — and the cheapest implementation touches no HLE at all:

- one transparent `NSView` overlay over the game view draws the stick, a D-pad, ABXY and +/−;
- the step function **ORs its buttons and axes into the same state the keyboard produces** and calls
  the existing input publish (`dm_input_set`) — the game cannot tell the two apart;
- the mouse monitor must "consume on hit, forward on miss", otherwise a click on a button also
  becomes a touch-screen finger.

Three things that cost time:

- **Verify the overlay's geometry with markers, not by eye.** A layered subview over a manually
  assigned `CAMetalLayer` is worth one calibration run: draw a coloured square at the view's bottom
  left **and another at the opposite corner**, `screencapture`, and check both pixel positions
  against the window frame the host logs. The eye cannot tell a 1:1 overlay from a 2× one on a
  translucent circle — and one marker at `(0,0)` proves nothing, because `(0,0)` lands in the same
  place at any scale. (Cost: an hour of chasing a "clipped stick" that was never clipped.)
- **The stick origin is the press point, not the base centre.** Centring it makes every touch jerk
  the character; anchoring at the press point feels right and is three lines.
- **Ship a D-pad too.** Menus often ignore the analog stick (this title loses steps and even moves
  the cursor the opposite way) while the D-pad is exact, so the world can use the stick and the
  menus the D-pad.

Limitation to write down rather than fight: one mouse pointer means "hold the stick and press A"
is impossible; real play is mouse on the stick plus keyboard buttons (or the reverse).

## Reading the log: what "good" looks like

- `紫闪探针 … 画面品红 0 次, 调色表异常 0 次` → no missing shaders (the magenta probe).
- `上屏 至帧 N：… p50 16.67 ms` → steady 60 fps (16.67 ms = 60 Hz).
- Thermal throttling lowers the 3D render scale (log reports 温控 level); the UI is untouched.
- Sparse frame logs are normal — `帧 N：` lines are periodic, so a "stalled" log usually just means
  nothing worth logging happened, or the app is backgrounded.

## Diagnosing a black (or wrong) scene

"Valid input, black output" is the most common graphics symptom and the least guessable. Reading
intermediate textures back to the host is far shorter than reasoning about the shaders. Three
diagnostics carry almost all the weight — build them before you need them:

- **Per-pass attachment read-back** (`DM_NVN_RTSTAT=<frame|from-to>`). At the end of every render pass,
  copy each colour attachment into a host buffer and print a value-range line (min/max, count of
  non-empty blocks, count of non-finite pixels). Walking the buffer chain from the geometry pass
  forward to the swapchain localises the break to **one pass**. Non-finite counts matter: a NaN usually
  means a `0 × ∞` in the shader, an exact `0` usually means a zeroed input.
- **Texture dump by serial** (`DM_NVN_TEXDUMP=<frame>:<path>%d.png` + `DM_NVN_TEXRAW=<serial>,…`).
  Blit-copies the whole texture out — **including 3D and array textures**, which is what lets you see
  *which slices* an LUT or shadow map actually got. If a multi-slice target has content in slice 0 and
  zeros elsewhere, stop looking at the shaders.
- **Output-substitution probe** (`DM_NVN_MAGSCAN=<fragment key>:<every>:<phase length>`). Rotates
  phases over the run: normal → drop that shader's draws → replace its output with constant grey →
  replace with constant magenta. If replacing the output turns the black screen **grey**, the draw is
  rasterising fine and the problem is purely arithmetic; if it stays black, it is culling, a degenerate
  triangle, or the draw never being encoded. That single step removes half the hypothesis space.

Also cheap and worth having: force the per-binding pass dump on for the first *N* draws of one shader
key (`DM_NVN_KEYDUMP=<key>:<count>[:<interval>]`) to read the uniform values the game actually wrote at
gameplay time rather than at boot.

**Read a raw dump with the header's `rowBytes`, not with `width × bpp` — and then don't trust the
lane-0 stride either.** Blit-based dumpers typically copy into a fixed 4-byte-per-texel staging
format regardless of the source format, so a `R8Unorm` or `R16F` texture lands as
`rowBytes = width*4`. Decoding that buffer as tightly packed 1- or 2-byte texels produces
completely fake statistics — a "75% of texels are zero" histogram (it is the padding) and periodic
banding (it is the stride). Two of this project's runs went down that hole.

Then comes the second-order version of the same trap: **a 4-byte stride with "take lane 0" is not a
decoder for a single-channel texture either.** In this project that reading showed a depth map
"only in the left third of the screen", which sent a whole debugging cycle after a view-clipping bug
that did not exist — while the **PNG the dumper wrote from the same texture was full-frame**. Two
rules follow:

- **Judge content from the PNG the dumper writes; judge raw bytes from `.raw`.** When they disagree,
  the PNG is right. `.raw` is for formats the PNG path cannot represent (integer formats sample as
  float and come out all zero) and for exact byte values (an sRGB target's PNG is *linearized*).
- When you build a hypothesis on top of a dump, **first reproduce the dump's own picture** from the
  bytes. If you cannot, you have learned nothing about the texture, only about your decoder.

**Prove the display chain before blaming the tonemapper.** A "washed out / over-exposed" frame is
usually *not* the tonemap: engine tone curves are frequently near-identity below ~0.8 and only
compress the shoulder, which reads as "the curve does nothing" in a dump. Two cheap proofs:

- *Closure*: dump the swapchain's raw bytes and the tonemap output texture's *linearized* dump
  (`TEXDUMP` on an sRGB target linearizes). If the two are **byte-identical (same md5)**, the whole
  present chain — including any double sRGB encode/decode — is closed and correct.
- *Where the brightness is*: dump the HDR target **before** the tonemap and look at mean/median/clipped
  fraction. If it already sits at mean ≈ 0.88 / median ≈ 0.93 / 7% clipped, the lighting stage is the
  suspect, not the curve.

And when an effect pass is a no-op, check whether it has any *input* at all before touching its maths.
In this project an AO pass produced nothing but its clear value, and the first diagnosis ("it discards
every fragment because the sampled depth is empty") turned out to be an artifact of the raw decoder
above, not of the texture. Two things did survive scrutiny and are worth carrying forward:

- **Sampling a texture that is also an attachment of the same pass is undefined in Metal.** Colour
  feedback is usually handled (the runtime ends the pass and snapshots the attachment before the
  draw); the **depth** case usually is not — and a deferred renderer's SSAO pass is exactly
  "depth attachment = the texture the fragment stage samples". Fix by snapshotting the depth plane, or,
  when none of the pass's draws test/write depth (`dt=0, dw=0, st=0`), by not attaching it at all.
  A 1x1 exposure pass that `load`s, blends and samples its own attachment is the same class.
- If a pass reads a **depth** attachment through a sampling view, also confirm the preceding pass
  actually *stored* it (store actions get set to `DontCare` aggressively when a target looks dead).

Then check the *reflection*, not the pixels: `rt_array`, `point_size`, integer output formats, and the
attachment formats are where whole classes of silent-wrong live (§Graphics).

## When the frame is washed out (over-exposed)

"Washed out" is a measurement, not an impression. Three numbers decide whether it is the tone curve
or the lighting:

- **the HDR target that feeds the tonemap**: median, p99, fraction of pixels above 1.0;
- **the final frame**: pure-white fraction, and its *displayed dynamic range* (`p99/p50` of the
  linearised values, which collapses to ~1.0 when the highlights are clipped);
- **the same two for a scene you already know renders correctly** (menus, character creator). Same
  chain, different content — this is what separates "the curve is wrong" from "the scene is too hot".

In this project the in-game room measured HDR median 1.00 / p99 4.88 / 42% above 1.0, final frame
**67% pure white** and a displayed range of **1.09** (vs 4.88 in the HDR). The menus in the same
build: median 0.15, pure white 1–2%. So the curve was fine and the scene arrived ~5× above it.

Cheap confirmation before touching any code: rescale the dumped HDR (here ×0.2) and run a normal
ACES + sRGB in numpy. If the frame suddenly reads like the real game, the defect is a **global gain**,
not one light's colour, not "missing contrast". That one step removes most shader hypotheses.

Where the gain hides:

- **Exposure is frequently baked into a shader's static `c1` segment**, not applied per frame. The
  tone-mapping shader here carried `c1[0].z = 1/0.18` (middle-grey normalisation) and
  `c1[0].w = 1/13.6` (log2 axis width), and the 32³ grading LUT was generated from the *same*
  constants — so the curve "looks like a curve" in a dump while the input is 5× too high.
- **A 1×1 auto-exposure buffer can exist and be dead.** Here the adaptation pass wrote it and only
  the adaptation pass read it; the frame had **zero texture→buffer copies**, so there was no CPU
  readback either. "The game computes an exposure" does not mean "the exposure is applied".
- **A knob that costs nothing**: scale that single `c1` float for the tone-mapping shader only — gate
  on the constant pair (`1/0.18` next to the sRGB knee `0.0031308`) so the LUT generator is not
  touched, and expose it as e.g. `DM_NVN_EXPOSURE=<k>`. It shifts the whole image along the tone
  curve, touches no render target, and defaults off. Use it to unblock play-testing, and keep
  looking for the real gain.
- Sanity-check the curve's **white point in HDR units**. A middle-grey-normalised curve that
  saturates at an HDR value of ~1.5 is being fed values ~8× mid-grey; a filmic curve for that range
  should not clip until ~4–16.

### Clear-only passes are usually not your bug

A pass that opens targets, clears them and issues **no draws** looks alarming in a pass dump, but
triage it in two steps before instrumenting the HLE:

1. **Is there a `K` line (a program was fetched)?** No `K` line at all means the guest itself never
   issued a draw in that pass — scratch targets being cleared for a later frame, or an effect that
   simply does not run in this scene (screen-space reflections/GI indoors). Nothing was dropped.
   Only "`K` present, draw missing" or a `Q` line with a skip reason points at the host.
2. **Does anything read that target in the same frame?** No reader = dead chain; ignore it.

In this project 26 such passes (a 21-pass R8 luminance pyramid plus five half-res scratch targets)
turned out to be exactly this, and the only real reader was the deferred lighting pass reading three
legitimately zero buffers.

## Reconstructing a missing stage instead of hardcoding a constant

A fixed knob unblocks play-testing but is not a port. Two cheap diagnostics decide whether the
missing thing can be rebuilt from data the game already produces.

**1. Null one input and measure what it was worth.** `MAGSCAN`-style output substitution answers
"did this draw happen"; it cannot answer "how much of the frame is this term". Add the mirror image:
`DM_NVN_TEXZERO=<serial,…>`, which binds an all-zero 1×1 dummy **of the type class the shader
declares** (not of the original texture) — so `texture2d`, `texturecube` and arrays all stay valid
and only the content changes. Here, zeroing the irradiance probe and the prefiltered specular probe
dropped the frame median from 0.92 to 0.73 (sRGB), i.e. IBL was ~41% of the median radiance, and the
frame was *still* 2.7× middle grey with the whole IBL gone. That single result retired the
"ambient light is too bright" theory and pointed at a global gain. Caveat: you are nulling sampled
*content*, so do not do this to depth/stencil inputs that drive discard or classification.

**2. Prove a value is unused on both sides before claiming it is missing.** "No pass binds it this
frame" is only half the proof — the CPU may consume it. The other half is the copy counter: log
`CopyTextureToBuffer` (`copy_t2b`) behind a flag and run a whole session. Here it was **0 copies in
1500 frames**, which is what makes "the auto-exposure output is dead" a conclusion rather than a
guess.

**3. Then rebuild the stage from the curve's own units.** A tone curve that carries `1/0.18` states
its contract: middle grey arrives as 0.18. If the engine also computes an average luminance (even an
unconsumed one), the missing exposure is `k = 0.18 / avg`. Here the engine's 1×1 held 0.7106 and
`0.18/0.7106 = 0.253`, matching the best hand-tuned constant to 1% — three independent numbers
agreeing is what makes the reconstruction credible.

Implementation notes that matter:

- read the 1×1 back with a **1-texel blit queued behind the frame, value read in the completion
  handler** — never a GPU wait on the present thread;
- identify it structurally (`1×1` + `RGBA32Float` attachment), not by serial;
- **register the c1 slot** (buffer, byte offset, original value) when the shader is built, then write
  `original * k` each time k changes — otherwise repeated scaling accumulates;
- damp (`k = old + (new-old)*0.25`), clamp (0.02…20), and expect a wild k during loading (here it
  went 15 → 0.25 over ~100 frames);
- keep it **opt-in and labelled unverified**: reconstructing a stage is not the same as proving the
  original behaviour. It is still far better than a constant, because it follows the scene instead
  of crushing the ones that were already correct.

Make the constant gate **value-based, not index-based**: the same shader family in this title had 16
variants, and the `5.555556` sat in slot 2 in some and slot 3 in others. An index-based match (plus a
neighbouring sentinel) silently applied to half of them — the symptom is "the fix works in some
scenes and not others".

## "The model is exploded" is usually a buffer nobody wrote

Two states decide whether a pile of garbage vertices ever reaches the screen. Check both before you
suspect vertex formats, strides or skinning matrices.

### 1. `SetRasterizerDiscard` means "these draws must not be rasterised"

Modern NVN titles use **transform feedback + rasterizer discard** for GPU skinning / mesh
pre-processing: the draw exists only to stream vertex-shader outputs into a buffer. The geometry is
intermediate data — rasterising it paints giant flat polygons across the screen, which reads exactly
like "the model is broken" or "the vertex format is wrong".

Metal has no discard state, but skipping the draw is **exactly equivalent** (NVN guarantees the
attachment is untouched). Record the flag as a real op, skip in the draw path, and count the skips —
they are a good health metric. In this title one 1080p pass had **237** such draws per frame.

### 2. A vertex buffer that overlaps a transform-feedback range

Transform feedback itself is often still unimplemented (Metal has no stream-out; it needs
SPIRV-Cross's `msl-transform-feedback` to emit `[[buffer(n)]]` writes plus binding plumbing). Then
the buffer the game streams into is **never written**, and the *consumer* draw — the game binding it
back as a vertex buffer, not necessarily via `DrawTransformFeedback` — reads uninitialised memory.

Cheap, permanent instrumentation that proves it:

```c
// at BindTransformFeedbackBuffer: remember (address, size) per index  — addresses are guest addresses,
// so the overlap check below needs no translation
// at BindVertexBuffers: for each range, if it intersects a recorded TF range → log once + set bit i
// in a per-stream "poison" mask on the bind op
// in the draw path: if any bound stream has its poison bit set → skip the draw (DM_NVN_TFSKIP=0 to compare)
```

What this buys you: the difference between *"random garbage polygons"* and *"a coherent mesh with a
wrong camera"*. The first means the vertex data is fake; the second means the data is real and the
transform is wrong. Those are completely different investigations, and picking the right one is worth
more than any amount of staring at the screenshot. Skipping is also the honest fallback: drawing
unwritten memory can only ever produce garbage.

Caveat: `nvnCommandBufferDrawTransformFeedback(cb, primitive, address)` gives you no vertex count, so
it cannot be honoured either. Log it and drop it rather than guessing a count.

**Also guard the index buffer and the indirect-argument buffer.** They come from the same unwritten
memory. At *record* time, test the index range (`count × 2` for 16-bit indices) and the 16/20-byte
argument record against the TF ranges, store a 2-bit poison flag in the draw op, and skip in the draw
path alongside the vertex-stream poison. Keep the bits separate so the log says which one fired.

**Third consumer, and the one that fools everybody: a render target nobody wrote.** If the skipped
draws were the only ones writing a preview/offscreen target, the target still holds whatever was in
that GPU allocation, and a later UI quad samples it. The screen then shows a smooth, plausible-looking
fill — a gradient, "vertical bands", a grey card, a blurred portrait — and it **changes between runs**.
Do not chase UVs, decoders or vertex formats there. See the next section.

## Undefined content: run the same frame twice before believing any decode theory

The most expensive misdiagnosis in this project's log: a character-creation confirm screen whose panel
looked like "a texture sampled with the wrong UV sub-range — one row stretched across the panel". It
was written into a report. It was wrong, and the check that settles it takes one extra run.

Run the **same** configuration twice (same scripted input, same frame number, same env) and diff the
two screenshots:

| run | panel mean RGB | high-frequency energy in the panel |
|---|---|---|
| A | 0.588 / 0.302 / 0.256 | 2.1‰ |
| B | 0.436 / 0.291 / 0.248 | 4.1‰ |
| C | 0.603 / 0.613 / 0.668 | 2.9‰ |

Pairwise correlation 0.63–0.86; 13–35 % of pixels differ by more than 0.1. **Identical input, three
different pictures** ⇒ the content is undefined. No decoder, UV, stride or format hypothesis can
produce three different results from the same bytes — and every hour spent on one is wasted.

Two cheap numbers that identify this class immediately:

- **High-frequency energy** (`mean |∇|`) of only 2–4‰ over the whole panel ⇒ the content is *blurry*,
  i.e. leftover memory that was later blurred by a post effect, not geometry or a textured quad drawn
  wrong (those have hard edges and normal gradient energy).
- **Between-run mean colour differs more than within-region variance** ⇒ the whole base colour changed,
  so it is not a local misplacement.

The honest fix is to implement the missing stage (here: transform feedback). Clearing targets on first
use is only a cosmetic band-aid, and it touches every title's hot path. Prefer writing "this target is
never written because X is unimplemented" over a heal that hides it.


## For small NVN structs, check the calling convention before trusting a negative result

NVN passes many small structs **by value** (`NVNbufferRange { address, size }`,
`NVNtextureView`, …). On aarch64 a two-qword struct arrives exactly like two integer arguments
(x2/x3). If you declare the HLE function as taking a *pointer*, the guest address lands in the first
integer slot and you read `0 + 0` — with no crash and no warning.

The trap is not the wrong read, it is the conclusion you draw from it: a freshly added diagnostic
that depends on those values will produce "no hits", and you will conclude your hypothesis was wrong
when in fact the instrument was broken. This cost a full round of investigation here (a
vertex-buffer-vs-transform-feedback overlap check that could never fire).

**Hard rule:** if a brand-new diagnostic prints all zeros, or the same value everywhere, suspect the
ABI first (struct by value vs pointer, `bool`/`char` widening, variadics) and only then the
hypothesis.

## Screenshot claims: measure before you write them down

A downscaled frame is not evidence. This cost two full rounds here — once in each direction — on the
same class of artifact:

1. repeated decor in a bedroom was recorded as a "4×2 tiling bug" (it was two shelves, a poster and a
   jagged rug pattern);
2. after correcting that, a clean CJK title on a menu was recorded as "the whole UI layer tiled at ⅓
   scale" — and that one reached a written report before a 2× crop of a 1000×150 strip showed the text
   is drawn exactly once.

Dense CJK glyphs are the worst case: at ½ or ¼ scale the strokes alias into what reads as repeated
blocks. Whole-frame thumbnails also manufacture patterns out of diagonal bands and gradients.

Cheap, decisive checks, cheapest first:

- **Pixel-diff two shots of the same camera.** A real artifact leaves structure; identical frames are
  identical (here: mean |Δ| = 0.0000, and a 0.998 correlation against two other shots of the same
  frame from different runs).
- **High-pass a single text/detail row and autocorrelate it 1-D.** A genuine tile shows a *secondary
  peak* near 1.0 at some shift. Here the maximum over all shifts was 0.19 — and the "broken" frame's
  autocorrelation curve was point-for-point identical to the clean frame's.
  Note the trap: whole-image autocorrelation is **useless** on flat or gradient-heavy frames (it just
  decays), which is exactly how the phantom survived the first check.
- **Crop small, then enlarge.** 1000×150 at 2× shows strokes; the full 1920×1080 at thumbnail size
  shows shapes that are not there.

Corollary: when a "bug" appears in exactly the frames you were not looking at, and never survives a
numeric test, it is usually the viewing scale. Write the number into the report, not the impression.

### Measuring a UI rectangle takes two numbers

Third instance of the same failure: a selected menu row was recorded as "the highlight box is too
narrow, it only covers the first two characters". Measure it:

```
yellow mask   : r > 140 & b < 110 & r-b > 60 & g-b > 40
box           : bounding rect of the mask            -> 642 x 80 px
text row      : bright pixels of an unselected row   -> 115 px and 164 px wide
selected row  : 4 CJK glyphs at the same size        -> at most ~250 px
```

The box is 2.5–5× the text width. Claim dead in five minutes of arithmetic.

**First make sure the frame is not a transition.** Capture several consecutive frames of the same
screen before measuring: here frames 1150–1350 had the box at the same place (±10 px from an animated
arrow) and an identical whole-frame mean (51.0), so the reading was trustworthy. A single frame taken
during a cross-fade contains *two* layouts at once, and every width/height measured from it is a blend
of both.

### On-screen controls: give them a self-test that needs no mouse

When a desktop front end grows touch-style controls, "plug in a mouse and see if it moves" does not
prove the hit-test → grab → axis chain, and it cannot catch the classic bug where the right stick
writes the left stick's axes. Add an env-triggered self-test that calls the same entry points the
event monitor calls, then logs the resulting axes and button mask:

```
DM_TOUCH_TEST=0.8,0.6
  left  stick (0.80,0.60)xr -> axes (26213,19660) / (0,0)      buttons 0
  right stick (0.80,0.60)xr -> axes (0,0) / (26213,19660)      buttons 0
  shoulders tapped once     -> 0x3c0  (expected L|R|ZL|ZR = 0x3c0)
```

It also documents the dead zone and the clamping behaviour for free (the numbers are reproducible).


### A post-process pass that outputs a constant: shader-computed vs clear-value, and the empty-atlas check

Two separate failure modes look identical in a screenshot ("this pass output is 1.0 everywhere"):

**(a) Is it the shader computing it, or is it clear-value residue?** A pass that discards / never draws
leaves the attachment at its clear value, which is indistinguishable from a shader that really computes
that constant. Split them by writing the intermediate booleans/floats into the (single-channel) target
in screen-x bands:

```
band 0: did we take the early-out branch?        band 1: depth-threshold test
band 2..: the bits that matter (strength, sample, final value)
```

Write into the R component and read the **R** channel band averages. Do **not** convert to grayscale /
`convert('L')` — L weights R at 0.299 and drags a raw 1.0 down to 0.299, which looks like "the band is half".
Here this proved the SSAO shader really computes 1.0 (the sample loop ran but found zero occlusion);
it was not a discard/clear-state artifact.

**(b) If it computes the constant, check the input atlas is not empty.** A loop that reads a 1024² "kernel /
thickness" atlas and finds nothing will return its neutral value. Read those textures directly in the
fragment (`read()`/`sample()`) and write them into bands: here two atlas textures (fp_t_tcb_28, fp_t_tcb_42)
came back all-zero, which is why the loop found no occlusion.

Then ask "who fills it, and does that pass run this frame?" The texture was **cleared** by one render pass and
only **read** by others; the pass that should write 0/1/255 content into it (an R8Uint pass fixed earlier) did
**not run in this scene**. So the atlas is empty by the game's own scene-gating — not a host draw-path bug.
Conclusion: "constant output + empty input atlas + the producer pass is scene-gated" = the effect is
legitimately off in this scene, not a bug to chase.

### PASSDUMP only shows the first 4 floats of a constant buffer; use DM_NVN_CBHEX for deep slots

To answer "is this coefficient zero or just not bound", the first 4 floats are not enough — a SSAO pass
needs `c3[212..219]` (8 per-material AO strengths) and `c3[228]` (a mode/float + a depth threshold).
PASSDUMP prints only the first float4 of each cbuffer. `DM_NVN_CBHEX=<reg>[,<reg>...]` (optionally
`DM_NVN_CBHEX_KEY=<fragment key prefix>`) prints the whole constant buffer in hex from `draw()`; it lives
inside the `passdump()` block, so set `DM_NVN_PASSDUMP` too.

## A signal the engine computes but nobody reads is usually a *missing stage*

When a scene looks wrong (5x too bright, washed out, flat), check whether the engine computes a signal
that nothing consumes. Here a 1x1 RGBA32F average-luminance target was written every frame, and **no pass
bound it and no copy read it back** (`DM_NVN_COPYLOG` showed `copy_t2b` = 0 for the whole run). That is not
"the exposure is 1.0"; it is "the exposure stage is missing from our side". The fix is to reconstruct the
stage from the value the game itself computes, using the calibration the game itself declares:

- The tone curve's static `c1` carried `1/0.18` (middle grey) and the LUT generator carried `0.18`; the
  engine's own 1x1 read `0.7106`; `0.18/0.7106 = 0.2533`, which is exactly the hand-tuned value that looked
  right. Three independent numbers agreeing is the confidence you need.
- Implement it the reusable way: recognise the 1x1 RGBA32F attachment at bind time, blit 1x1 -> a 16-byte
  shared buffer behind the frame (same trick as any present-time readback), compute `k = midgrey/avg`
  (clamped, first-order smoothed), and rewrite the tone-mapping `c1` slot to `base*k` each time it moves.
  Register `(buffer, offset, base)` so repeated rewrites do not compound.
- **Make it the default** when the un-exposed path is visibly broken (median 235/255, 5.5% pure white ->
  median 138, 0.5% with it on). Keep a kill switch (`=0`) and let an explicit manual value win.

Lesson: "brightness is wrong" is rarely one bad light. Measure the HDR median vs the curve's white point
first; if the median sits ~5x middle grey, the missing thing is a whole stage (exposure/tone), not a lamp.

## EFAULT from a host `pread`/`read` into guest memory is usually a write-protect race

If guest memory is write-protected for dirty-page tracking (textures: protect after upload, fault on
game write), a **kernel** write into that page does not take the page-fault hook — `pread` just returns
`EFAULT`. Calling an "unprotect before host write" hook once before a large read is not enough: the
graphics thread can re-protect the page in the window between your mprotect and the kernel's copy, and
a long read can cross a page that gets re-protected mid-copy.

Symptom: intermittent guest abort with `fs pread fd 失败 errno=14`, then a `Break`/assert, and the
process exits with the game's abort code. Re-running often passes, which is the tell.

Fix:

- Call the unprotect hook **before every** host syscall that writes guest memory (`read`, `pread`,
  `recv`, `recvfrom`), not once per operation.
- On `EFAULT`, re-unprotect and retry a bounded number of times (8, with `sched_yield`). The read is at
  the same offset so retrying is idempotent.
- Only after the retries fail, log the fd/path/offset/length/address plus `vm_region` protection — that
  is what distinguishes "race" from "the guest really passed a bad pointer".

Verified: the same new-game script ran 4/4 times to the same frame with 0 failures after the change;
before it, 1 of 3 runs aborted mid-opening.

## Host code is shared by macOS and iOS — guard Mac-only Mach APIs

`<mach/mach_vm.h>` (`vm_region_64`, `mach_vm_region`, `mach_vm_read_overwrite`) **does not exist in the
iOS SDK**; the build fails with `mach_vm.h unsupported`. `<mach/mach.h>` itself is fine. The same goes
for `proc_*`, Mac-only `sysctl` mibs, `NSWindow`/AppKit, and anything in `IOKit` you did not link for iOS.

Guard **both the include and the calls**:

```c
#include <TargetConditionals.h>
#if !TARGET_OS_IPHONE
#include <mach/mach_vm.h>
#endif
...
#if !TARGET_OS_IPHONE
    if (vm_region_64(...) == KERN_SUCCESS) { ... }
#endif
```

Keep the shared logic (here: the EFAULT retry) unconditional; only the extra diagnostic goes away.

Rule: **rebuild the IPA after every host change.** The Mac build cannot see these; `xcodebuild` is the
only thing that catches a new Mac-only API before it reaches a device.
