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

The same manual supplies an exact arithmetic-pipeline contract. `MULT`,
`MULTU`, `MADD`, and `MADDU` can be issued on consecutive cycles, and their
HI/LO result is available to the immediately following instruction. A
following instruction that instead reads the multiply's GPR destination
stalls for one cycle. `DIV` and `DIVU` take 35 cycles in a unit independent of
the integer pipeline: unrelated instructions continue, while `MFHI`, `MFLO`,
`MADD`, and `MADDU` interlock until the result is ready. Division continues
through exceptions, pauses with the CPU in Halt/Doze, and is cancelled by
`MTHI`, `MTLO`, or a new divide.

Ordinary loads have a matching one-cycle dependency contract. The instruction
immediately after a load proceeds without delay when independent and stalls
for one cycle when it reads the loaded GPR. The manual explicitly exempts an
`LWL` or `LWR` that uses the preceding load's destination as its own target;
this is the byte-merge bypass needed by normal unaligned-load pairs. A
destination of register zero never stalls.

The integer ISA extensions also include `BEQL`, `BNEL`, `BLEZL`, `BGTZL`,
`BLTZL`, `BGEZL`, `BLTZALL`, and `BGEZALL`. A taken branch executes its delay
slot; a not-taken likely branch nullifies it. Both link-likely instructions
write `r31 = PC + 8` unconditionally, as do the R3900's ordinary `BLTZAL` and
`BGEZAL`. The extended set also supplies false-likely and true-likely
branches for each of the four coprocessor condition inputs (`BC0FL/TL`
through `BC3FL/TL`) with the same delay-slot rule. `SYNC` waits for a
preceding load, store, or data-cache refill.

The R3900 deliberately omits two pieces inherited from the R3000A. Its exact
`TLBR`, `TLBWI`, `TLBWR`, and `TLBP` encodings execute as no-ops because the
core has no TLB. `LWCz` and `SWCz` are reserved; Coprocessor Unusable still
has the documented higher exception priority when the referenced
coprocessor is disabled.

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

An aligned opcode scan of both unstripped Apollo ELFs found no branch-likely
or `SYNC` word inside any sized `STT_FUNC` symbol. Apparent primary-opcode
matches are ASCII strings and floating-point tables in the executable monitor
section. The sole aligned `0x0000000f` is the `SYNC` entry in `asm_tab_op`,
the monitor assembler's data table, not an executed instruction. These ISA
extensions therefore improve CPU completeness without being credited for a
currently observed Magic Cap path.

The boot code also reads and writes TX39 CP0 registers 3 (Config) and 7
(Cache) and issues cache operations 0, 5, and 17. `config_cache_toshiba`
writes Config `0x74`, enabling both caches, selecting burst refill, and
choosing an eight-word instruction refill. `LockHalfDataCache` sets Cache
`DALc`, reads the first 512 bytes, then clears `DALc`.

The ROM also uses Config RF rather than treating it as an identification bit.
`SlowDownProcessor` (`0x13c00330`) sets RF to `10` for quarter-rate execution,
while `SpeedUpProcessor` (`0x13c00358`) restores `00`. `DeepDoze` repeats the
same quarter-rate selection in `QuarterDozeLoop` while cycling its DRAM
refresh and stop-CPU sequence.

These registers are no longer unrestricted shadows:

- Config reports the TMPR3902U's read-only 4 KiB instruction-cache and 1 KiB
  data-cache fields, ignores reserved bits, and resets with both caches
  enabled. ICE and DCE select cached versus uncached accesses. RF values
  `00/01/10/11` scale processor execution to 1, 1/2, 1/4, or 1/8 of the
  master rate while independently clocked peripherals retain their rates.
  Once software sets Config.Lock, further writes—including RF changes—are
  ignored until reset.
- Cache accepts only its six `IALo/DALo`, `IALp/DALp`, and `IALc/DALc` mode
  bits. An exception pushes current → previous → old and clears current; RFE
  restores previous → current and old → previous while retaining old, just
  like the TX39 manual's Status-register mode stack.
- Cache operations 0 (instruction index invalidate), 5 (data index LRU-bit
  clear), 9 (data index lock-bit clear), and 17 (data hit invalidate) are
  recognized. These are the complete set of operation/cache combinations
  defined by the TX39 manual. Instruction index invalidation covers all four
  words sharing the selected physical tag. Like an explicit CP0 instruction,
  `CACHE` raises Coprocessor Unusable in user mode unless Status.CU0 is set;
  kernel mode may use it regardless of CU0.

The data cache now matches the documented 1 KiB geometry: 128 indices with
two one-word ways. Every index retains an LRU replacement selector and at
most one locked way. Loads fill an invalid way before replacing the LRU way;
hits update LRU; a DALc access locks its selected way and confines later
replacement to the other. Cached store misses write through without
allocating. A store hit on a locked line updates only the cache, matching the
manual's required read → clear lock → store sequence for committing that
value to memory. Both cache arrays, LRU selectors, and lock state survive
MAME save/load, and reset invalidates every line and clears every lock.

Config's refill fields now have functional effects. Instruction misses align
to and fill the selected 4/8/16/32-word block. With DCBR clear, a data miss
fills the data cache's native one-word line; with DCBR set, DRSize selects a
4/8/16/32-word aligned burst. DALc locks every data-cache word brought in by
that burst. The instruction-cache backing still represents each word
separately, so this models the refill's contents but not a shared physical tag
object.

Dino's receive channels are external bus masters. Their Magic Bus, sound and
telecom writes now invalidate matching, unlocked R3900 data-cache words before
raising completion. This matters once burst refill is enabled: the CPU may
have prefetched a destination word that software never explicitly loaded. The
release monitor supplies a direct behavioral oracle—it receives the 88-byte
Magic Bus peripheral-information record into an ordinary cached stack buffer
and immediately validates its length and checksum without issuing a cache
operation. Leaving the prefetched words resident makes
`AssignMagicBusAddresses` reject a byte-for-byte correct record; invalidating
the DMA range lets the monitor discover the SCTG endpoint while retaining the
documented four-word data refill. DALc-locked lines remain private on-chip
storage and are deliberately not invalidated. Multiply, divide and ordinary
load-use timing are modelled as described below.

The remaining cache timing boundary is now narrower than an unknown register
set. Apollo's SDK headers and `MM_InitializeDino` establish the exact Dino
BIU configuration, including CS0 ROM waits, CS0 burst, card waits, 32-bit
page-mode DRAM, refresh and watchdog values. They do not state Dino's external
`ACK*` latency for DRAM. The Toshiba bus protocol latches a word one clock
after observing `ACK*`, so refill duration depends on that missing board-side
parameter. See
[`memory-map.md`](memory-map.md#bus-interface-configuration) for the complete
decode and regression. External-bus and cache-miss cycle timing are therefore
not guessed.

## Emulator behavior and regression

The MAME fork gives only the `R3900` device the three-operand
`MULT`/`MULTU` behavior and the `MADD`/`MADDU` execution extension. Other
MIPS-I devices retain their original two-operand multiply behavior and still
raise Reserved Instruction for primary opcode `0x1c`. The R3900
disassembler shows a nonzero destination explicitly, while other MIPS-I
devices retain the conventional spelling.

The R3900 arithmetic pipeline is also device-specific. Multiply and
multiply/add accept one instruction per emulated processor cycle. A nonzero
GPR destination records a one-cycle dependency, charged only if the next
executed instruction actually reads it; exceptions flush that dependency.
Successful byte, halfword, word, `LWL`, and `LWR` loads record the same
dependency. Source decoding distinguishes address and value operands, so an
independent instruction proceeds and the manual's `LWL`/`LWR`
target-register bypass does not spuriously stall.

Division retains a pending result and 35-cycle countdown while independent
integer and exception-handler instructions execute. HI/LO consumers spend the
remaining cycles at their interlock, while `MTHI`, `MTLO`, or another divide
discard the pending result. Pending results, countdowns and dependencies are
save-state data. Other MIPS-I devices retain MAME's previous blocking divide
and multiply timing.

Config Halt and Doze stop instruction issue after the `MTC0` that sets the
mode, leaving the next instruction pending and preserving independent divider
progress. Assertion of any physical interrupt input clears both mode bits
even when Status masks that interrupt; an unmasked interrupt then takes its
normal exception, while a masked one resumes the pending instruction. NMI and
reset clear the modes as well. External DMA invalidation continues while Doze
permits cache snooping and is suppressed in Halt. Apollo normally uses Dino's
separate `StopCpu` power-control path, so a focused injected test drives these
architectural Config modes directly with a masked periodic Dino interrupt.

The R3900 decoder now distinguishes the Toshiba integer branch-likely
extensions from the baseline MIPS-I REGIMM aliases and implements
`BC0FL/TL` through `BC3FL/TL` against MAME's four coprocessor condition
callbacks. A not-taken likely branch advances past its delay slot without
fetching or executing it; a taken branch uses the ordinary delayed-branch
state. Link writes are unconditional. Coprocessors 1–3 still raise the
architectural Coprocessor Unusable exception unless their Status enable bit
is set. Baseline MIPS-I devices retain their original REGIMM alias behavior,
reject the four likely primary opcodes, and reject likely coprocessor
sub-opcodes. `SYNC` is accepted only by the R3900 and is currently an
effective no-op because memory accesses and cache fills complete synchronously
inside the interpreter. The disassembler applies the same device distinction.

The R3900 self-debug unit now exposes CP0 register 16 `Debug` and register 17
`DEPC`. `SDBBP` enters debug mode at fixed vector `0xbfc00200` without
modifying ordinary Status, Cause or EPC state; a breakpoint in a branch delay
slot records the branch address and DBD. `DERET` jumps through writable DEPC,
clears DM, sets current KU/IE, and disables the next single-step event. If its
return instruction branches, suppression extends through the delay slot.
Debug mode also forces cache auto-lock off. The disassembler recognizes both
instructions and names all four R3900-specific CP0 registers.

The NMI input is edge-latched and sampled at instruction boundaries. It
records EPC/BD, sets Status `NmI`, clears Config Halt/Doze, and enters the
fixed uncached `0xbfc00000` vector without shifting the ordinary mode stacks.
`NmI` is write-one-to-clear. An NMI arriving in debug mode remains pending
until `DERET`. Status bit 20 is cache parity-error state on baseline MIPS-I
cores but the NMI latch on R3900; cache lookup therefore leaves it intact.
The regression executes from cached kseg0 with `NmI` set, then separately
proves that `MTC0 Status` with bit 20 set clears it.

The focused debug model covers DBP/DSS, DBD, DM, SSt, BsF storage, DEPC,
the documented return suppression, and the asynchronous coincidences the
interpreter can represent. A single step coincident with NMI first records
the NMI state and then enters debug with `NIS`; a coincident enabled ordinary
interrupt similarly records Cause, EPC and shifted Status before setting
`OES`. Exact-entry debugger snapshots verify that those ordinary registers
survive beneath debug mode. A data-read or data-write bus error while DM is
set is consumed as `BsF`; it does not alter Cause/EPC or enter the ordinary
bus-error vector. Read/write taps inject both cases and prove execution
continues at the following instruction. The R3900 has no TLB, so TLF has no
applicable source. The same exact-entry harness executes `CACHE` from user
mode twice: with CU0 clear it observes Cause.CpU, CE=0, the faulting EPC and
the shifted KU mode stack; with CU0 set the legal cache operation completes
and execution reaches the following instruction. It also executes all four
TLB encodings through the following instruction, then verifies that an
unsupported `LWC0` reports CpU in CU0-disabled user mode and RI in kernel
mode.

The DataRover has no external coprocessor, so its four condition callbacks
default false. The injected branch regression nevertheless enables each
applicable Status bit and verifies both possible false-line outcomes:
`BCzFL` takes the branch and executes its delay slot, while `BCzTL` falls
through and nullifies it. Neither likely branches nor the self-debug
instructions occur in a sized function in the available SDK ELFs.

Run the isolated CPU regressions with:

```sh
python3 tools/tx39_regression.py
python3 tools/tx39_refill_regression.py
python3 tools/tx39_clock_regression.py
python3 tools/tx39_timing_regression.py
python3 tools/tx39_power_mode_regression.py
python3 tools/tx39_branch_regression.py
python3 tools/tx39_debug_regression.py
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

The final programs use addresses `0x3000`, `0x3200`, and `0x3400`, which share
one of the data cache's 128 indices. They prove both ways remain resident,
touch A to make B least-recently-used, and observe B's changed backing value
only after loading C evicts it. A DALc load then keeps A resident while B and
C churn the unlocked way. A locked store reads back only through the cache
while an uncached alias still sees backing RAM; after operation 9 clears the
index lock, both-way churn exposes a later backing value. A final store-miss
case changes backing RAM after the write and observes the change on the next
cached load, proving the store did not allocate.
Generated Lua, NVRAM, and logs stay under
`$MAGIC_CAP_ASSETS/runtime/tx39-regression/`.

The refill companion first selects DCBR-clear mode and proves that loading one
data word does not prefetch its neighbor. It then selects a four-word data
burst, changes the neighbor in backing RAM, and observes the prefetched old
value through the cached alias. Its DALc case churns both same-index
alternatives after a burst and proves the adjacent burst word remains locked.
Finally it branches over an instruction that arrived in a four-word refill,
changes its backing word, and jumps directly to the cached address; the old
instruction executes, proving instruction prefetch. The expected observations
are `BBBBBBBB`, `22222222`, `22222222`, and `00001234`, respectively.
Generated inputs and logs stay under
`$MAGIC_CAP_ASSETS/runtime/tx39-refill-regression/`.

`tools/magicbus_scsi_probe.py` is the cross-subsystem DMA-coherency regression.
In addition to the SCTG command-3/command-7 transport checkpoints, it requires
the monitor's address-assignment and transaction routines to execute and
`numberMagicBusPeriphs` to reach one. The latter remained zero when a stale
burst-refilled word caused the monitor's information checksum validation to
fail.

The clock companion runs the same three-instruction counter loop for one
video-frame interval at every RF value. A reference run counted `204779`,
`102380`, `51180`, and `25579` iterations: each normalized count is within
0.08% of the full-rate result. It then sets RF=`10` and Config.Lock together,
attempts to restore RF=`00`, and counts `51177` iterations while reading back
the still-locked quarter-rate Config value. This verifies the functional
processor divider without claiming external-bus wait-state accuracy.
Artifacts stay under `$MAGIC_CAP_ASSETS/runtime/tx39-clock-regression/`.

The timing companion compares fixed-duration uncached loops. A reference run
counted `204744` three-cycle baseline iterations; after normalization, the
one-cycle-issue `MULT`, `MADD`, and unconsumed `DIV` loops differed by less
than 0.01%. An immediate GPR consumer reduced `MULT` throughput by exactly one
cycle per loop. Independent `LW` matched the four-cycle multiply loop, its
immediate GPR consumer added exactly one cycle, and `LW` → `LWL` retained the
five-instruction issue rate without a false target-register dependency. A
load into register zero followed by an instruction reading zero retained the
same five-instruction rate.
`DIV` followed by `MFLO` completed `15749` 39-cycle loops,
returned `100 / 7 = 14`, and a separate `MTHI` case retained `0x1234` after
cancelling an active divide. This distinguishes real divider overlap from the
old implementation, which charged all 35 cycles at `DIV` and prevented
unrelated execution. Artifacts stay under
`$MAGIC_CAP_ASSETS/runtime/tx39-timing-regression/`.

The branch companion executes taken and not-taken cases for all eight integer
likely forms. It requires each taken delay slot to add `1`, each nullified
slot to leave only the fallthrough's `0x40`, and both link-likely forms to
write the exact uncached `PC + 8` address in either direction. Two not-taken
ordinary link cases require the non-nullified `0x41` result and the same
unconditional link. A final injected `SYNC` reaches the following
instruction without a Reserved Instruction exception. Artifacts stay under
`$MAGIC_CAP_ASSETS/runtime/tx39-branch-regression/`.

The self-debug companion executes `SDBBP` both normally and in a branch delay
slot, reads Debug through real `MFC0`, rewrites DEPC with `MTC0`, and returns
with `DERET`. It then enables SSt and proves the pending instruction has not
executed when DSS arrives. A final DERET returns to a branch and requires the
branch plus delay slot to execute before DSS stops at the target. Artifacts
stay under `$MAGIC_CAP_ASSETS/runtime/tx39-debug-regression/`.
