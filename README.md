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

## New in v2

- **Shader banks beyond D2R.** A new reference covers titles that ship shaders as `BNSH` banks
  (`nn::gfx`) rather than a pre-extracted per-program package: how to slice the NVN programs out,
  where the `c1` `BRX` jump table actually lives (tail of the code segment), how to locate it without
  guessing, how to verify it with four independent checks, and why the translator must be run with
  `debug: true` (the AST optimizer breaks ~43% of such programs). Chinese text included.

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

- This skill is **documentation only**. It contains **no game code, art, text, data tables or
  shaders**, and it does not download any. Everything it describes is generated on your own machine
  from game files you legally own.
- The approach assumes the source software and Apple silicon share the **arm64** instruction set and
  that graphics are **NVN**. It does **not** apply to non-arm64 sources (those need emulation).
- It is a knowledge capture of the d2r-mobile methodology. The authoritative source for the code is
  the [d2r-mobile repository](https://github.com/jhaizhou-ops/d2r-mobile).

## License

MIT — see [LICENSE](LICENSE).
