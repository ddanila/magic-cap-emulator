# TX39 / R3900 CPU audit

The DataRover's Toshiba TMPR3902U uses the TX39 core. It is upward-compatible
with the R3000A instruction set, omits the TLB, and adds a two-stage
multiply/add unit. The exact TMPR3902U manual is not publicly available, so
the emulator uses Toshiba's family core manual plus the matching Icras SDK
ELF and ROM behavior.

## Reference manual

Keep the manual with the other persistent, uncommitted research inputs:

```sh
assets="$HOME/fun/magic-cap-assets"
mkdir -p "$assets/docs"
curl -fL \
  https://www.bitsavers.org/components/toshiba/_dataSheet/TMPR39xx-um_199507.pdf \
  -o "$assets/docs/TMPR39xx-um_199507.pdf"
printf '%s  %s\n' \
  cf9fd5fa551814bb681fefd9576114ba8d8b8e8d7bb1903e943dee546405ad38 \
  "$assets/docs/TMPR39xx-um_199507.pdf" | sha256sum --check
```

The July 1995 manual defines `MADD` and `MADDU` under primary opcode `0x1c`,
function values 0 and 1. Both add a signed or unsigned 32×32 product to the
existing `HI:LO` accumulator, write the 64-bit result back to `HI:LO`, and
also write its low word to `rd`. An omitted assembly-language `rd` encodes
register zero.

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

The boot code also reads and writes TX39 CP0 registers 3 (configuration) and
7 (cache lock) and issues cache operations 0, 5, and 17. The R3900 device
keeps the two CP0 values as read/write shadows and already models the three
observed cache operations. That is sufficient for the verified boot path;
cycle-exact cache locking and clock division are not claimed.

## Emulator behavior and regression

The MAME fork gives only the `R3900` device the `MADD`/`MADDU` execution
extension. Other MIPS-I devices still raise Reserved Instruction for primary
opcode `0x1c`. MAME's shared MIPS-I disassembler recognizes both encodings so
debug traces name them.

Run the isolated arithmetic regression with:

```sh
cd "$HOME/fun/magic-cap-emulator"
python3 tools/tx39_regression.py
```

It writes two tiny uncached-RAM programs into the running DataRover machine.
The signed case proves `5 + (-2 × 3) = -1`; the unsigned case proves
`1 + (0xffffffff × 2) = 0x1ffffffff`. Both verify `rd`, `HI`, and `LO`.
Generated Lua, NVRAM, and logs stay under
`~/fun/magic-cap-assets/runtime/tx39-regression/`.
