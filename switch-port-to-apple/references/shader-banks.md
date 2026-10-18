# Shader banks (`BNSH`) and the `c1` jump table

Everything above assumes the shaders arrive in the shape `dmsh_xlate` already eats: one blob per
program, `0x50`-byte SPH followed by Maxwell instructions. Titles built on Nintendo's own
`nn::gfx` do not hand you that — they ship **shader banks** (`BNSH` files) that embed many NVN
programs plus the constant data the programs read at run time.

This page is the container side: how to slice the programs out, and how to supply the one piece of
per-program data the translation chain needs but cannot infer — the `c1` **indirect-branch jump
table**. Measured on a real `nn::gfx` title (4,298 programs, 88 banks); the numbers are given as
sanity anchors, not as assumptions.

## `BNSH` layout

```
0x00  "BNSH"
0x04  u32   version (0 observed)
0x08  u32   0x0002010c
0x0c  u32   0x000cfeff
0x10  u32   offset of the FIRST string in "_STR"   ⇒ _STR starts at this − 0x1a
0x14  u32   0x00600000
0x18  u32   "_RLT" offset
0x1c  u32   file size
0x20..0x5f  zero
0x60  "grsc"   (graphics shader set)

      grsc+0x04 / +0x0c   u64  _STR offset − 0x60
      grsc+0x18           u32  variant slots (empty slots included)
      grsc+0x1c           u32  live variants
      grsc+0x20           u32  0xc0
      grsc+0x28           u64  variant-table offset

      variant table       0x40 bytes per entry
                          +0x08  u64  control-record offset
                          +0x10  u64  code-record offset
      control pool        records starting with magic 0x98761234
                          small banks: fixed 0x9a0 bytes = 0x120 header + 0x880 payload
                          the big "standard" bank: variable sizes, different table layout
      code pool           records starting with magic 0x12345678
      "_STR"              names of variants/stages ("default", "imgui", …)
      "_RLT"              link/relocation table
```

The control record's payload (`0x880`) is **not** the `c1` data — see below. It carries the
per-variant pipeline state (blend/depth/raster), which is what the replay layer needs, not the
translator.

### Slicing a program out

```
0x00..0x2f   NVN header (magic 0x12345678; every other byte observed zero)
0x30..0x7f   SPH (0x50 bytes)
0x80..       Maxwell instructions — 4 × 8-byte words per group, first word = scheduling control
```

- The translator wants **SPH + instructions**, so export from `+0x30` to the end of the code
  record. That is the same convention as D2R's `dmsh_dryrun.py` (`b[0x38:ci]` then `[0x30:]`) and
  matches Ryujinx's fixed `HeaderSize = 0x50`.
- **Every offset in the rest of this page is relative to that exported file** (SPH at `0x00`,
  instructions at `0x50`). In the bank itself, add `0x30`.
- **Stage** = SPH word 0, bits 10..13: `1=vs 2=tcs 3=tes 4=gs 5=ps`.
- Bound the scan by `_STR`: take `header[0x10] − 0x1a` as the end of the code pool. Do **not** use
  `buf.find(b"_STR")` — inside a host image (a `.nso`-style blob with an embedded bank) it matches
  unrelated data.
- Trim only trailing **all-zero 8-byte words**. The code record is padded to a fixed stride, and a
  *nonzero* tail is meaningful (it is the `c1` table and/or uniform constants — next section).

## `c1`: the BRX indirect-jump table

Programs compiled from a `switch` lower to `BRX` (branch-register) plus a jump table in constant
bank 1. Without the table the translator cannot resolve the targets: it reads zeros, dispatches
every case to the base address, and the CFG silently loses the switch bodies — no error, just a
shader that takes the wrong branch (a loop that never exits, in the worst case).

**The contract** (read out of the Ryujinx sources that the chain vendors; do not re-derive it):

| Source | Fact |
|---|---|
| `Decoders/Decoder.cs` `FindBrxTargetRange` | the site must be preceded by `IMNMX.U32 Rx,Rx,UpperBound,PT` → `SHL Rx,Rx,2` → `LDC Rx,c[1][Rx+BaseOffset]`; **count = UpperBound + 1**, **table base = the LDC's `CbufOffset`** |
| `Decoders/Decoder.cs` `FindBrxTargets` | `base = BRX address + sign_extend(imm12) + 8`; `target = base + c1[BaseOffset + i*4]` |
| `Decoders/InstOp.cs`, decoder loop | addresses are in **bytes** and advance by 8 per instruction |

So each `c1` word is a **byte offset added to `base`**, which is why every entry is non-zero and a
multiple of 8. The compiler picks the immediate so that `base` lands somewhere convenient: measured
on 1,095 sites, most encode `base = 0x50` (start of the instruction region); the rest use
`0x1050` / `0x2050` / … (a different fixed anchor, same table).

### Where the table lives

**At the tail of the code record, immediately after the instruction region.** Not in the control
record, not "the 0x100 bytes before the control section" (that D2R-era rule is a coincidence of
D2R's own container and does not survive the move to `BNSH`).

```
[0x00,0x50) SPH          [0x50,T) instructions
[T,R)       c1 table(s), concatenated, one per BRX site
[R,end)     other constants (uniform defaults — 0x3f555556, 0x4000, …)
```

Three properties make it findable without guessing:

1. **`T` is a run start.** The word before `T` is not a legal table entry (it is zero, a float, or
   not 8-aligned).
2. **`(T − 0x50) % 16 == 0`.** The instruction region is a whole number of 4-word groups.
3. **`T % 32 == 16`** (verified after the fact on 843/843 tables). Groups are 32 bytes starting at
   `0x50`, so the end of the instruction region always lands on this congruence. If your `T` gives
   8 or 0 here, you are off by 8 bytes.

Two traps that cost real time:

- **Do not search for "any 16-aligned position whose next N words look like a table."** A long
  table (multiple sites concatenated) contains many such positions, 4 entries apart; searching
  backwards from the end picks a **mid-run** start and silently shifts every case by 4..5 entries.
  Take the **run start**, not a position inside the run.
- **Do not assume the table is flush with the end of the record.** Constants follow it, and they
  can look table-ish: `0x4000` is 8-aligned and small, a lone conforming word after a float. Use
  the run's end, not the file's end.

A minimal extractor, given the count (see "two-pass" below):

```python
def conf(v):                      # a plausible table entry
    return v != 0 and v % 8 == 0 and v < 0x8000

def find_table(buf, need):
    """buf = the exported code file (SPH first); need = entries the shader reads."""
    words = [int.from_bytes(buf[i:i+4], 'little') for i in range(0, len(buf), 4)]
    i = len(words) - 1
    while i >= 0:
        while i >= 0 and not conf(words[i]): i -= 1
        if i < 0: return None
        end = i + 1
        j = i
        while j >= 0 and conf(words[j]): j -= 1
        start = j + 1
        if (start * 4 - 0x50) % 16 == 0 and end - start >= need:
            return start * 4, end * 4      # T, R
        i = j
    return None
```

(If a table may begin with a 0 entry — a `default` that lands on `base` — allow extending
leftwards over zeros so that the start still satisfies the 16-byte congruence. On this title no
table did.)

### Getting `base` and `count` for free

You do not need a Maxwell decoder to learn what each site reads. Run the translator once **without**
`c1`: its accessor reports every out-of-range read as `c1 读越界 +OFF`, and the notes give you both
numbers per program:

- `count = (max(OFF) − min(OFF)) / 4 + 1`
- `base   = min(OFF)`

That `base` is the `CbufOffset` the decoder recovered, so the table must sit at that offset of the
blob you hand back:

```python
job["c1"] = (b"\0" * base + table_bytes).hex()
```

### Two-pass convergence

1. Translate with **no** `c1` → collect `base`/`count` from the notes.
2. Extract the table, feed it, translate again.
3. Newly resolved targets expose **more** `BRX` sites, which may read deeper entries: union the
   read offsets from both runs, extract once more. Convergence on this title: pass 2 had 14
   remaining out-of-range reads, pass 3 had 0.

### Verification that does not trust the extractor

Run all four; each is independent of the locating rules:

| Check | Why it can fail |
|---|---|
| all read offsets < `len(c1)` | a short table / wrong run end |
| `T % 32 == 16` | an 8-byte shift inside the run |
| `0x50 + entry ∈ [0x50, T)` for every entry of the first table | values taken from constants instead of the table |
| every entry non-zero and `% 8 == 0` | a float or a padding word slipped in |

And one behavioural control, because "the shader got bigger" alone proves nothing: translate the
same programs with a **same-length junk `c1`** (every entry set to `8`) and compare. On this title:

| | SPIR-V size, 843 c1 programs | programs that changed |
|---|---|---|
| no `c1` | 58.5 MB | — |
| real `c1` | **62.7 MB (+7.2%)** | **843 / 843**, up to +44% |
| junk `c1` | 58.5 MB (+0.0%) | 19 / 843 |

The real tables hold 4–45 entries per program (median 8, across however many `BRX` sites it has);
the junk one resolves a single target.

## Feeding the chain

`dmsh_xlate` already accepts `"c1"` as a hex string per job entry (`Program.cs` `Accessor`), so
nothing in the chain changes: `{key, stage, code, tex, img, attr, fout, local, debug, reduced, c1}`.
Two things to keep in mind:

- **`debug: true` is mandatory for titles like this.** With the AST optimizer enabled, 43% of the
  programs die in `GenerateCall` (the optimizer rewrites call arguments into `AstTextureOperation`,
  the SPIR-V codegen casts to `AstOperand`). See `pitfalls.md`.
- The `c1` blob is *only* consumed by the `BRX` resolution. Plain `c[1]` loads in the shader are
  emitted as normal constant-buffer accesses, so a partial blob is not "wrong data" for them — it
  simply is not used.
