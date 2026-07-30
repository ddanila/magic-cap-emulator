# TX39 / R3900 CPU audit

The DataRover's Toshiba TMPR3902U uses the TX39 core. It is upward-compatible
with the R3000A instruction set, omits the TLB, and adds a two-stage
multiply/add unit. The exact TMPR3902U manual is not publicly available, so
the emulator uses Toshiba's family core manual plus the matching Icras SDK
ELF and ROM behavior.

## Reference manual

Keep the manual with the other persistent, uncommitted research inputs:

```sh
assets="${MAGIC_CAP_ASSETS:-$PWD/../magic-cap-assets}"
mkdir -p "$assets/docs"
curl -fL \
  https://www.bitsavers.org/components/toshiba/_dataSheet/TMPR39xx-um_199507.pdf \
  -o "$assets/docs/TMPR39xx-um_199507.pdf"
printf '%s  %s\n' \
  cf9fd5fa551814bb681fefd9576114ba8d8b8e8d7bb1903e943dee546405ad38 \
  "$assets/docs/TMPR39xx-um_199507.pdf" | sha256sum --check
```

The same 246-page document is also scanned on archive.org as
[manualzilla-id-7260633](https://archive.org/details/manualzilla-id-7260633),
a fallback if the Bitsavers copy moves.

The July 1995 manual defines three-operand `MULT rd,rs,rt` and
`MULTU rd,rs,rt` as TX39 extensions to the normal SPECIAL encodings. They
write the product's low word to both `rd` and `LO`, and its high word to
`HI`; omitting `rd` encodes register zero and retains the familiar MIPS-I
spelling. It also defines `MADD` and `MADDU` under primary opcode `0x1c`,
function values 0 and 1. Those add a signed or unsigned 32×32 product to the
existing `HI:LO` accumulator, write the 64-bit result back to `HI:LO`, and
also write its low word to `rd`.

## ROM audit

The hosted SDK ELF's executable `.text` section contains 792 aligned
`MADD` instructions and no `MADDU` instructions. They are concentrated in 20
V.32 modem DSP functions:

| Largest users | `MADD` count |
|---|---:|
| `BlockRealInCplxCoefFIR` | 192 |
| `BlockRealInCplxCoefFIRUpdate` | 128 |
| `BlockFIR` / `BlockFIRUpdate` | 64 each |
| `BasebandEqualizer` / `BasebandEqualizerUpdate` | 63 each |
| `BlockCplxFIR` / `BlockCplxFIRUpdate` | 60 each |
| `V32ModulatorFIR` | 36 |
| Remaining eleven modem helpers | 62 |

This explains why the unextended MIPS-I core can reach the desk but cannot
correctly execute the archived software modem. GNU binutils labels these
words as raw data when disassembling an ELF marked for the baseline R3000;
the encoding and surrounding multiply/shift sequences match the Toshiba
manual.

The same audit finds 89 signed `MULT` words with a nonzero `rd`, spread
through 19 modem helpers. `V32ModulatorFIR` has 12; `BlockShortScale`,
`V32NonlinearDecoder`, `V32NonlinearEncoder`, and `V32TrellisSearch` have
eight each. There are another 536 conventional `MULT` and 303 `MULTU`
encodings with `rd=0`, but no three-operand `MULTU` in this ELF.

The nonzero destination is architecturally significant. The Telephone's
software DTMF path calls `BlockShortScale`, whose eight unrecognized words
multiply Q12 oscillator samples before shifting them right by 12. Treating
the words as ordinary two-operand MIPS-I `MULT` updates only `HI:LO`, leaves
the input registers unscaled, and reduces the transmitted waveform to
roughly ±6. Implementing the TX39 destination write restores the intended
16-bit DTMF amplitude.

The boot code also reads and writes TX39 CP0 registers 3 (Config) and 7
(Cache) and issues cache operations 0, 5, and 17. `config_cache_toshiba`
writes Config `0x74`, enabling both caches, selecting burst refill, and
choosing an eight-word instruction refill. `LockHalfDataCache` sets Cache
`DALc`, reads the first 512 bytes, then clears `DALc`.

These registers are no longer unrestricted shadows:

- Config reports the TMPR3902U's read-only 4 KiB instruction-cache and 1 KiB
  data-cache fields, ignores reserved bits, and resets with both caches
  enabled. ICE and DCE select cached versus uncached accesses. Once software
  sets Config.Lock, further writes are ignored until reset.
- Cache accepts only its six `IALo/DALo`, `IALp/DALp`, and `IALc/DALc` mode
  bits. An exception pushes current → previous → old and clears current; RFE
  restores previous → current and old → previous while retaining old, just
  like the TX39 manual's Status-register mode stack.
- Cache operations 0 (instruction index invalidate), 5 (data index lock/LRU
  clear), and 17 (data hit invalidate) remain recognized.

MAME's underlying MIPS-I cache is still direct-mapped and word-line based.
The TMPR3902U's per-line two-way replacement/locking, burst refill timing,
reduced-frequency clock timing, and cycle costs are therefore not claimed.

## Emulator behavior and regression

The MAME fork gives only the `R3900` device the three-operand
`MULT`/`MULTU` behavior and the `MADD`/`MADDU` execution extension. Other
MIPS-I devices retain their original two-operand multiply behavior and still
raise Reserved Instruction for primary opcode `0x1c`. The R3900
disassembler shows a nonzero destination explicitly, while other MIPS-I
devices retain the conventional spelling.

Run the isolated arithmetic regression with:

```sh
python3 tools/tx39_regression.py
```

It writes a suite of tiny uncached-RAM programs into the running DataRover
machine.
The multiply/add cases prove `5 + (-2 × 3) = -1` and
`1 + (0xffffffff × 2) = 0x1ffffffff`; the multiply cases prove
`-2 × 3 = -6` and `0xffffffff × 2 = 0x1fffffffe`. All four verify `rd`,
`HI`, and `LO`.

The CP0 programs first fill a cache line with `0x11111111`, change backing RAM
to `0x22222222`, clear DCE, and prove that the next kseg0 load bypasses the
stale line. They then write Config with ICE clear and Lock set, verify the
read-only size fields and writable mask, attempt a forbidden second write,
and observe the unchanged value `0x001000df`. Finally they set both current
Cache modes, execute a real `syscall`, record `0x00000c00` in the exception
handler, and execute RFE in the return jump's delay slot. The restored Cache
value is `0x00000300`.
Generated Lua, NVRAM, and logs stay under
`$MAGIC_CAP_ASSETS/runtime/tx39-regression/`.
