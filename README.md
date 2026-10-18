# Switch → Apple Native Port (OpenCode skill)

An [OpenCode](https://opencode.ai) skill that captures **how to run a Nintendo Switch
(Horizon OS, aarch64) game natively on Apple silicon** — no CPU emulation — by

1. offline-rewriting only the instructions Apple's kernel breaks,
2. loading the rewritten images with a small **host** that replaces Horizon,
3. **HLE**-ing the kernel syscalls and SDK system services,
4. translating **NVN → Metal** (and the game's shaders offline), and
5. wrapping it all in a platform shell (macOS `.app` / iOS `.app`).

It is grounded in the [**d2r-mobile**](https://github.com/jhaizhou-ops/d2r-mobile) project
(Diablo II: Resurrected, Switch release, MIT), and is written to be reused for **other titles**:
it includes a file-by-file *engine vs title-specific* reuse map and the list of game-specific
defaults you must turn off first.

中文：[README.zh-CN.md](README.zh-CN.md)

## Contents

| File | Covers |
|---|---|
| `SKILL.md` | The playbook: when the approach applies, the five-layer model, the workflow for a new title |
| `references/architecture.md` | The five layers in detail — rewrite tables, loader, HLE macros, NVN→Metal, shader chain, iOS shell |
| `references/code-reuse-map.md` | Every directory/file classified **ENGINE / ADAPT / TITLE** |
| `references/build-pipeline.md` | Workspace setup, Mac/iOS builds, artifacts, signing, pushing game data to the device |
| `references/pitfalls.md` | AAPCS64 vs Switch ABI, no-JIT signing rules, BC textures, version drift, and more |
| `references/shader-banks.md` | `BNSH` shader banks: slicing NVN programs out, and the `c1` `BRX` jump table (layout, extraction, verification) |

The last one is in Chinese too: `references/shader-banks.zh-CN.md`.

## What's new

- **v3 — runnable tools.** `scripts/` now ships the pipeline instead of only documenting it:
  bank slicing, job JSON, `c1` extraction/verification, SPIR-V → metallib, reflection statistics.
  No game data; everything runs on files from your own copy.
- **v2 — shader banks beyond D2R.** A reference for titles that ship shaders as `BNSH` banks
  (`nn::gfx`) rather than a pre-extracted per-program package: how to slice the NVN programs out,
  where the `c1` `BRX` jump table actually lives (tail of the code segment), how to locate it without
  guessing, how to verify it with four independent checks, and why the translator must be run with
  `debug: true` (the AST optimizer breaks ~43% of such programs). Chinese text included.

## Tools

`switch-port-to-apple/scripts/` contains runnable companions to that reference. Python 3.9+, no
third-party modules; `shader_chain.py` also needs `spirv-cross` and Xcode's Metal toolchain.

| Script | Does |
|---|---|
| `bnsh.py` | read a `BNSH` bank, list programs, export each as `<bank>.<idx>.<stage>.code` |
| `dmsh_jobs.py` | build the `dmsh_xlate` job JSON (with or without `c1`) |
| `c1_extract.py` | locate the `BRX` jump-table runs and emit the padded `c1` blobs |
| `c1_verify.py` | four independent checks on those blobs (non-zero exit on failure) |
| `shader_chain.py` | SPIR-V → MSL → `.metallib` (or `--msl-only`) |
| `refl_stats.py` | aggregate `*.res.json` reflections into slot/format/texture statistics |

```sh
scripts/bnsh.py dump  shader.bnsh work/code
scripts/dmsh_jobs.py  work/code --out work/jobs_noc1.json
#   ... translate once, then read the 'c1 读越界' notes:
scripts/c1_extract.py work/out_noc1 --code work/code --out work/c1.json --junk-out work/junk.json
scripts/c1_verify.py  work/c1.json --code work/code --notes work/out_noc1
scripts/dmsh_jobs.py  work/code --c1 work/c1.json --out work/jobs_c1.json
#   ... translate again, then:
scripts/shader_chain.py work/out_c1 work/mlib --jobs 10
```

Full order, including the second extraction pass: `switch-port-to-apple/scripts/README.md`.

## Install

### 1. Copy into your OpenCode skills (global)

```sh
mkdir -p ~/.config/opencode/skills
cp -R switch-port-to-apple ~/.config/opencode/skills/
```

Then mention it as `@switch-port-to-apple`, or let the agent auto-invoke it when the task matches.

### 2. Add it as an HTTP catalog

Add this repo as a skills source in any `opencode.jsonc`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "skills": [
    "https://raw.githubusercontent.com/yokeyhh/switch-port-to-apple/main/"
  ]
}
```

OpenCode reads `index.json` at that base URL and downloads each skill's files from
`<base-url>/<skill-name>/<file>`.

### 3. Clone

```sh
git clone https://github.com/yokeyhh/switch-port-to-apple.git
```

## Scope and honesty

- This skill is **documentation plus small generic tools**. It contains **no game code, art, text,
  data tables or shaders**, and it does not download any. Everything it describes is generated on
  your own machine from game files you legally own.
- The approach assumes the source software and Apple silicon share the **arm64** instruction set and
  that graphics are **NVN**. It does **not** apply to non-arm64 sources (those need emulation).
- It is a knowledge capture of the d2r-mobile methodology. The authoritative source for the code is
  the [d2r-mobile repository](https://github.com/jhaizhou-ops/d2r-mobile).

## License

MIT — see [LICENSE](LICENSE).
