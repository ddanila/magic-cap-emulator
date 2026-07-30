# DataRover 840 memory map

This is the initial emulator-facing map for the Apollo build of Magic Cap
3.1.2j. It comes from the archived Icras SDK headers and the matching
unstripped `MagicCAP-USA` ELF, not from guesses based on ROM strings. See
[`rom-layout.md`](rom-layout.md) for exact download, extraction, and checksum
instructions. No SDK or ROM binaries are stored in this repository.

All CPU and register values are big-endian. Addresses beginning with `0xb` are
MIPS kseg1 (uncached) aliases; for these addresses the bus/physical address is
`virtual & 0x1fffffff`.

## Top-level map

| CPU address | Bus/physical address | Size | Function | Confidence |
|---|---:|---:|---|---|
| `0x00000000` | `0x00000000` | 4 MiB | DRAM | Confirmed by `MemorySize` and the Apollo build |
| `0xb0000000` | `0x10000000` | unknown | Dino chip-select 1 window | SDK constant |
| `0xb0400000` | `0x10400000` | at least `0x22` | Glacier 1 GPIO/interrupt ASIC | Confirmed by monitor and OS code |
| `0xb0800000` | `0x10800000` | at least `0x22` | Glacier 2 GPIO/interrupt ASIC | Confirmed by monitor and OS code |
| `0xb0c00000` | `0x10c00000` | `0x200` | TX39 “Dino” integrated peripherals | Confirmed by SDK structure and code |
| `0xb0e00000` | `0x10e00000` | word access | SDRAM bank 0 mode register | SDK constant |
| `0xb0f00000` | `0x10f00000` | word access | SDRAM bank 1 mode register | SDK constant |
| `0x13c00000` | `0x13c00000` | 8 MiB window | Mask ROM / cached KUser mapping | SDK linker map |
| `0xb3c00000` | `0x13c00000` | 8 MiB window | Mask ROM / uncached kseg1 mapping | SDK and flasher header |
| `0xbfc00000` | `0x1fc00000` | reset entry | Reset-time alias of ROM offset zero | CPU reset behavior and first instruction |
| `0x24000000` | platform KUser window | 64 MiB max | PC Card slot 1 | SDK memory map |
| `0x28000000` | platform KUser window | 64 MiB max | PC Card slot 2 | SDK memory map |
| `0xff000010` | implementation-specific | `0x90` | Dino hardware breakpoints | SDK constant; not needed for bring-up |

The current driver maps RAM at zero, an 8 MiB ROM region at `0x13c00000`, its
kseg1 alias, and the reset alias. Unpopulated bytes in the ROM region read as
`0xff`; the published image occupies only its first `0x451817` bytes.

### Vector-page remapping

`SetupVectorDispatching` copies the 512-byte ROM page at `0x13e96400` to RAM
at `0x00000200`, then sets bit 25 of Dino memory configuration register 0.
After that transition, accesses to `0x13e96400`–`0x13e965ff` target the RAM
copy. Magic Cap patches the copied dispatch stub, including
`RestoreSystemGlobalPointer` at `0x13e96410`.

The driver models this region switch explicitly. Treating the entire ROM as
immutable lets boot reach `BootCap`, but it fails as soon as the operating
system attempts to patch its exception dispatch page.

### Reset alias lifetime

The CPU starts at virtual `0xbfc00000`. The word there is `0x08f00007`,
which is a MIPS `j` with target bits `0x03c0001c`. Pseudo-direct jumps retain
the high nibble of `PC + 4`, so executing that word at the reset vector lands
at `0xb3c0001c`, the normal uncached ROM alias—not at `0xbfc0001c`.

Consequently, the `0x1fc00000` physical alias is software-visible only for the
reset instruction and its delay slot. The monitor then runs from
`0xb3c00000`; its occasional explicit jumps to `0x13c0xxxx` exercise the
cached/KUser ROM mapping. The hardware may keep the reset alias enabled, but
the ROM does not require it after the first jump.

## DRAM and framebuffer

`MemorySize()` returns 4 MiB for platform type 5 (Apollo) and 2 MiB for the
other supported builds. The Apollo screen buffer is allocated as:

```text
screenBase = align_down(ram_size - 0x9600, 16)
           = 0x003f6a00                    (with 4 MiB RAM)
```

The buffer ends exactly at `0x00400000`. `DisplayServer_BootBlit` clears 9,600
32-bit words and advances one scan line by `0x78` (120) bytes. It programs a
480×320, 2-bit grayscale surface:

```text
480 pixels × 2 bits / 8 = 120 bytes/line
120 bytes/line × 320 lines = 38,400 bytes = 0x9600
```

This exact Apollo ROM therefore uses 2 bpp/four stored gray values, even
though DataRover literature describes a 16-level display and the SDK also
contains a separate `PLATFORM_ApolloGreyScale16` configuration.

## Dino integrated peripheral block

The `DinoModule` at `0xb0c00000` consists of 32-bit registers. These offsets
are generated directly by the field order in the SDK's `Dino.h`.

| Offset(s) | Register/group |
|---:|---|
| `0x000`–`0x020` | memory configuration 0–8 |
| `0x028` | video control |
| `0x02c` | video rate and screen geometry |
| `0x030` | video high/start buffer |
| `0x034` | video low/end buffer and DF |
| `0x038`–`0x040` | red, green, blue palettes |
| `0x044`–`0x05c` | video dithering tables |
| `0x060` | SIB buffer sizes |
| `0x064`–`0x070` | SIB sound/telecom RX/TX starts |
| `0x074` | SIB control |
| `0x078`, `0x07c` | SIB sound and telecom hold |
| `0x080`, `0x084` | SIB subframe 0/1 auxiliary command |
| `0x088`, `0x08c` | SIB subframe 0/1 status |
| `0x090` | SIB DMA control |
| `0x0a0`–`0x0a8` | infrared control 1/2 and hold |
| `0x0b0`–`0x0c4` | UART A |
| `0x0c8`–`0x0dc` | UART B |
| `0x0e0`–`0x0f8` | Magic Bus control, DMA, command, data |
| `0x100`–`0x114` | interrupt status banks 1–6; writes clear documented banks 1–5 |
| `0x118`–`0x12c` | interrupt enable banks 1–6 |
| `0x140`, `0x144` | RTC high/low |
| `0x148`, `0x14c` | alarm high/low |
| `0x150`, `0x154` | timer control and periodic timer |
| `0x160`, `0x164` | SPI control and data |
| `0x180` | basic GPIO control/data |
| `0x184`–`0x190` | MFIO output, direction, input, select |
| `0x194`, `0x198` | GPIO/MFIO power-down |
| `0x1c0` | master clock |
| `0x1c4` | power control |
| `0x1c8` | SIU test |
| `0x1d8`–`0x1fc` | CHI control and DMA |

Each UART is six 32-bit registers: control 1, control 2, DMA control 1, DMA
control 2, DMA count, and hold. The monitor's `uart_hw` table contains
`0xb0c000b0` and `0xb0c000c8`, confirming both bases independently of the C
structure. Dino derives the line rate as
`230400 / (control2[9:0] + 1)`; control-1 selects seven/eight data bits,
parity and one/two stop bits. Writes now reconfigure MAME's serial engines
rather than merely shadowing those fields.

UART A is the 19,200-baud IDT/PCLink route exposed as MAME RS-232 port 1.
UART B is exposed as RS-232 port 2 and defaults to the external-modem
driver's 38,400 baud. The latter UART is also switched to the dedicated IrDA
transport when the ROM sets pulsed mode. `tools/uart_b_probe.py` uses the IDT
monitor's own `fill` and `dump` commands to select divider 5, sends `0x52`
from a host PTY, verifies enabled/empty/RX-full state and the received word,
then sends `0x54` back to the host. Because the PTY endpoint remains
configured for 38,400 baud, this exchange also detects a serial engine left
at the former hard-coded 19,200 rate.

The video-control bit-depth field is bits 7:6 (`0x00` mono, `0x40` 2-bit gray,
`0x80` 4-bit gray, `0xc0` 8-bit color). Bit 0 enables video scanning and bit 1
turns on the display. `EarlySetBuffer` writes the RAM buffer start to `+0x030`
and an encoded end address to `+0x034`.

Betty is **not** this register block. It is an external device reached through
Dino's SIB subframe registers; see [`betty-registers.md`](betty-registers.md).

### Timer module

The SDK defines `timerControl` at offset `0x150` and `perTimer` at `0x154`.
The periodic register is not a plain load-value shadow:

| Bits | SDK field | Implemented behavior |
|---:|---|---|
| 31:16 | `kTimerPerCntMask` | Live 16-bit down counter |
| 15:0 | `kTimerPerLoadMask` | Reload value |

Timer-control bit 4 enables that counter and bit 5 freezes it. The model
reloads on a new value or a disabled-to-enabled transition, exposes the live
count on reads, repeats at the programmed interval, and preserves the
partially elapsed count through both bit-5 freeze and loss of master timer
clock bit 15. Writes cannot replace the read-only count field.

The other timer-control names define Dino's RTC test path: bits 7 and 6 freeze
the prescaler and RTC, bit 3 clears the RTC, bit 2 is `TestC8Ms`, bit 1 is
`RtcEnTestClk`, and bit 0 is `EnRtcTest`. The IDT monitor's
`BumpTimerRough` (`0x13c04980`) enables bit 0 while the RTC is frozen and
polls `rtcHigh`; each test pulse advances that high-byte stage.
`BumpTimerFine` (`0x13c04a7c`) enables bit 1 and polls `rtcLow` masked to
32-tick units; that fine stage wraps without carrying into the separately
adjusted high byte. The model exposes one accelerated test pulse at each
poll, the only rate-independent behavior visible to the ROM. The focused
acceptance calls the monitor's real `SetTimer` (`0x13c04f04`), which returns
success and reaches the requested value within its own four-tick tolerance:

```sh
python3 tools/rtc_set_regression.py
```

The SDK and ROM still do not establish the physical test source frequency.
The monitor uses this path without enabling separately named master-clock
bit 14 `FastTimerClk`, so the model does not assign that unidentified clock
to the RTC, periodic timer, or power stop timer.

### Master clock gates

`masterClock` at offset `0x1c0` is more than a shadow register. The SDK's
`Dino.asm.h` assigns these independent peripheral clocks:

| Bit | Mask | Clocked engine |
|---:|---:|---|
| 18 | `0x00040000` | video |
| 17 | `0x00020000` | Magic Bus |
| 15 | `0x00008000` | periodic timer |
| 14 | `0x00004000` | fast timer |
| 11 | `0x00000800` | SIB |
| 2 | `0x00000004` | consumer-infrared block |
| 1 | `0x00000002` | UART A |
| 0 | `0x00000001` | UART B |

Release-ROM accesses confirm that these are active gates, not merely power
bookkeeping. Early initialization writes `0x00002abb`; timer setup adds bit
15, SIB command traffic clears and restores bit 11, `VideoOff` clears bit 18,
and Magic Bus issue/disable paths set and clear bit 17 independently.

The driver now applies those clocks to every represented consumer. Video
scanout requires both its clock and Apollo's LCD supply while retaining the
framebuffer. UART status, receive and transmit require the corresponding UART
clock, including pulsed IrDA mode. Magic Bus stops reporting enabled and does
not complete new commands while clocked off. SIB frame service and
sound/telecom DMA timers pause. Clearing the timer clock suppresses the
periodic timer; programmed registers and DMA positions are retained across
these clock transitions. The periodic timer also retains its live countdown
phase rather than restarting a full interval.

The battery-backed RTC is deliberately independent. During normal power-down,
the release ROM writes `masterClock = 0` at `0x13c3a428`, then
`PowerDownPeripherals` immediately samples `rtcLow` in its delay loop at
`0x13c3a4e8`–`0x13c3a540`. Gating RTC/alarm timekeeping with bit 15 deadlocks
that real path, so the model keeps the RTC, alarm and rollover timers live.

The power-control stop timer is independently always-on as well. The IDT
monitor initializes `masterClock` to `0x00002abb`, with bit 14 clear, and then
uses three stop-timer completions in `HardResetBetty`; gating the one-shot on
bit 14 strands that real boot path. Its tick is RTC/256, or 128 Hz: the
monitor's `Wait8msec` safety loop uses 264 RTC ticks per nominal interval,
while stop values 2 and 8 correspond to the ROM's nominal 16 ms Betty-reset
phases and 64 ms `DeepDoze` DRAM-refresh wake (exactly 15.625 ms and 62.5 ms).

Run the isolated acceptance check with:

```sh
python3 tools/dino_clock_regression.py
```

It parks the CPU before direct register writes, proves both UART clocks
(including pulsed mode), then checks SIB boundaries, Magic Bus command
completion, periodic interrupt suppression/resume, and continued RTC
advancement. The video gate is also covered by normal boot/Workbench
validation and the driver's shared clock-and-LCD blanking path. With every
master clock clear, the same regression starts stop values 2 and 8, proves
that interrupt-bank-5 bit 28 stays clear through RTC ticks 511 and 2,047, and
appears immediately after ticks 512 and 2,048. It then loads the periodic
counter with 8, observes the live `8 → 5` countdown, holds 5 across ten timer
ticks under both the dedicated freeze bit and master-clock gate, and requires
the interrupt on the fifth resumed tick. The unidentified bit-14 clock remains
separate from the observed power timer and functional RTC test path.

Bit 2 is not coupled to the pulsed UART transport. A complete two-peer Beam
run leaves `kClockEnIrClkMask` clear while UART B exchanges SIR frames in both
directions. That clock belongs to Dino's separate consumer-infrared register
block at `0x0a0`–`0x0a8`, which is not modeled; applying it to IrDA breaks an
observed release-ROM path.

### Consumer IR, SPI and CHI boundary

These blocks have a narrow, exact Apollo-ROM footprint. `CanDeepDoze`
(`0x13c3a164`) requires CHI control bit 0, consumer-IR control bit 0 and SPI
control bit 0 to be clear. `AnyDinoRxDmaActive` (`0x13c3a0e4`) also checks CHI
size bit 1, and `DisablePeripherals` (`0x13c3a330`) clears CHI and SPI control
before waiting for SPI on-status bit 17 to fall. Register shadows satisfy
those observed idle/disable checks.

The symbol and direct-register-access audit found no Apollo product path that
enables a CHI or SPI transfer. The only exported product method named for the
other engine,
`ConsumerIRDevice_SendIRCommand` (`0x13c26f60`), is a two-instruction
`jr ra; nop` stub. Functional CHI DMA, an attached SPI peripheral, consumer-IR
waveform output and their unobserved timing/interrupt behavior therefore
remain outside the model; none is needed for the verified product paths.

### Implemented semantics

The current behavioral model implements the subset exercised by the verified
boot, OS, and peripheral regressions:

- Buffered UART A/B transmit and receive paths, synthesized status bits,
  preserved writable control bits, interrupt behavior, and MAME RS-232
  endpoints; UART A is also wired to the generic terminal for the IDT monitor.
- A dedicated IrDA SIR PTY. Bytes use that endpoint instead of RS-232 whenever
  the owning UART selects either pulsed-mode bit. Incoming bytes return to the
  pulsed UART, and the IrDA carrier input drives Dino interrupt-bank-5 carrier
  pin and positive/negative-edge bits.
- Write-to-clear Dino interrupt status banks.
- Magic Bus command/status handling, PIO and DMA completion, peripheral
  request-line edges, checksummed discovery records, and the bidirectional
  Set-2 AT keyboard accessory.
- SIB SF0/SF1 command completion and continuous frame flags while SIB is
  enabled.
- The sound-hold FIFO's two signed 16-bit mono samples, its service interrupt,
  and sample timing derived from Dino's 9.216 MHz SIB clock/divisor, plus the
  continuously serviced sound-transmit DMA ring used by the boot chime.
- Telecom RX/TX DMA, one-shot and continuous rings, loopback/silence receive,
  and independent telecom sample timing for the built-in software modem.
- Telephone DAA digital control: Betty's connected input and hookswitch
  output, plus Dino MFIO input pin 0 and interrupt-bank-3/4 positive/negative
  ring-detector edges. The automatic test exchange feeds a deterministic
  350+440 Hz dial tone into telecom RX while connected and off-hook, and
  decodes standard DTMF pairs from telecom TX or timed pulse breaks from the
  hookswitch.
- A battery-backed 32,768 Hz RTC counter, alarm and rollover interrupts,
  freeze/clear controls, and a separate persistent clock record.
- A periodic timer with a read-only live count, writable reload, repeated
  interrupt, and phase-preserving enable/freeze/master-clock behavior.
- Read-only power-good/on-button inputs plus cold-start/VCC power state.
  `StopCpu` suspends the R3900; a pending enabled interrupt releases a
  VCC-on doze, while an on-button edge restores VCC after power-down. Dino's
  bank-6 low-priority summary follows enabled status in banks 1–5, including
  the polled `DeepDoze` wake path.
- Apollo MFIO output effects: LCD power blanks scanout while preserving its
  framebuffer, Magic Bus Vcc-off removes the peripheral and its assigned
  address, and charger enable advances the selected main-battery ADC only
  while AC is attached and the battery cover is fitted.
- A scheduled RTC/256 power stop timer used by the low-level Betty reset and
  `DeepDoze`, independent of the peripheral master clocks.
- Power-on mode input bit 3: high boots Magic Cap, low stays in the IDT
  monitor.
- Video high-buffer selection and 480×320, 2 bpp framebuffer scanout.
- Functional `masterClock` gates for video, both UARTs, Magic Bus,
  SIB, and the periodic timer; the timer's partial count is retained, while
  RTC/alarm/rollover timekeeping remains live.
- Main DRAM backed by MAME NVRAM, kept in the external runtime directory
  selected with `-nvram_directory`.

These are deliberately ROM-observed behaviors, not a claim that all Dino
timing and interrupt semantics are complete. Sound-receive DMA accepts host
microphone, deterministic-tone and silence inputs; both its direct buffer
boundary and Magic Cap's sound-stamp record/stop/play workflow pass. Power
outputs without a represented consumer, such as card Vpp, remain outside this
implemented subset.

## Magic Bus

Magic Bus is Dino's peripheral bus for external accessories, at `0x0e0`-`0x0fc`:
`mbusControl1`, `mbusControl2`, the DMA start/length/count registers,
`mbusCommand` and `mbusData`. Bits 31:29 of `mbusControl1` are status —
`kMbusEnabledStatusMask`, `kMbusEmptyStatusMask` and `kMbusIntStatusMask` — and
the driver synthesizes them rather than returning whatever the OS last wrote.
`TestMBReqLine` (`0x13c28364`) samples bit 29 as the peripheral request line.
Its positive and negative edges latch interrupt-bank-2 bits `0x08` and `0x04`.

The physical connector is broader than the packet controller. *Using Magic
Cap*, p. 217, describes PCs, external modems, external keyboards and other
accessories, with multiple devices commonly daisy-chained. The Magic Internet
Kit resolves an important naming trap: its `MagicBusModem` class does not
enumerate a packet-bus peripheral. It targets `iSerialBServer` at 38,400 baud,
so its hardware route is the UART-B transport described above. The Rosemary
SDK marks the old `MagicBusSerialPort` class obsolete and removes unused
Magic Bus clients; there is no evidence for inventing a modem descriptor here.

The machine configuration can present one `ATKB` keyboard or a two-device
topology containing that keyboard plus an `SCTG` SCSI target, or present the
SCSI target alone for the IDT monitor. The combined topology proves independent
address assignment and ROM client attachment for two different descriptor
classes. The monitor path additionally exercises both directions of the SCTG
transport and one non-destructive command/response buffer. The product
client's request handler is empty, so physical daisy-chain/electrical timing
rather than an undiscovered product payload remains unmodeled.

The controller completes transfers synchronously but preserves the ROM's four
transaction classes:

- type 1 returns peripheral data through `mbusData` for four bytes or through
  receive DMA for larger blocks. A PIO reply raises receive-data,
  command-detect and DMA-end (the monitor derives its four-byte length from
  those bits); DMA replies raise command-detect and DMA-end;
- type 2 accepts host data from `mbusData` or transmit DMA and raises the
  matching empty/DMA-end completion;
- type 3 completes when the transmit shifter becomes empty; and
- type 4 is a command-only transaction.

Enabling the controller reports transmit-buffer-available, which the IDT
monitor waits for before writing `mbusCommand`. The command write then reports
both transmit-buffer-available and empty. This distinction matters because the
ROM reprograms `mbusControl1` several times while staging DMA.

### Address assignment and peripheral information

The SDK ELF retains both the command table and the debug types needed to
recover the protocol. A wire command is the command-table halfword XORed with
the addressed peripheral's code. The complete recovered address-code table is
`0c00, 0a00, 0804, 0600, 0404, 0204, 0000, 0e04` for addresses zero through
seven. `MagicBus_AssignMagicBusAddresses` assigns only zero through five;
seven is broadcast. The two modeled endpoints take addresses zero and one:

| Address 0 | Address 1 | High-level command | Use |
|---:|---:|---:|---|
| `def0` | `def0` | 31, broadcast | ask an unaddressed peripheral to identify itself |
| `dca8` | `daa8` | 24 | accept the proposed address |
| `dce0` | `dae0` | 21 | finish assignment |
| `dcb0` | `dab0` | 25 | acknowledge another endpoint on shared MBReq |
| `dcf8` | `daf8` | 32 | begin peripheral discovery |
| `cc5c` / `cc60` | `ca5c` / `ca60` | 12 / 13 | select ID or full information |
| `cc24` | `ca24` | 2 | read selected data |
| `cc18` | `ca18` | 1 | read a pending request record |
| `cc3c` | `ca3c` | 5 | write client data |
| `cc48` | `ca48` | 7 | write SCTG transport data |
| `dcc8` | `dac8` | 28 | addressed request polling |

Broadcast commands continue to use address seven, including `def0` for command
31 and `decc` for command 28.

`MagicBus_AssignMagicBusAddress` broadcasts command 31. Every unaddressed
endpoint raises the shared MBReq line. Command 24 assigns the first responder;
the remaining endpoint keeps MBReq asserted, and the ROM repeats discovery at
the next address. It reads IDs `ATKB` and `SCTG`, then accepts an 88-byte
`MagicbusPeripheralInfo` record for each. The record layout was recovered from
STABS types in the unstripped SDK image:

- length at byte 2 and peripheral ID at byte 4;
- hardware, software and protocol revisions at bytes 8–10;
- three frequency/delay pairs at bytes 12–35;
- latency and request timing at bytes 36–51;
- power figures at bytes 52–63;
- maximum request length at byte 64;
- variable strings beginning at byte 80; and
- a big-endian 16-bit checksum after `pInfoLength + 2` bytes.

Only after length and checksum validation does the ROM initialize its built-in
`MagicBusATKeyboard` or `MagicBusSCSITargetClient`. “Magic Bus accessories” in
MAME's machine configuration defaults to **One AT keyboard** and can select
**None**, **AT keyboard and SCSI target**, or **One SCSI target**. A reset
applies a changed selection. **None** deliberately leaves address assignment
unanswered; this ROM counts that silence as a peripheral failure and can
eventually show the attached-device alert.

### Keyboard request and control traffic

MAME's AT-keyboard encoder supplies Set-2 make/break bytes. When bytes are
queued, the peripheral asserts MBReq. The ROM polls it, reads a 16-byte request
record whose type byte is 14, and dispatches that record to
`MagicBusATKeyboard_PeripheralRequest`. The client then issues command 2; the
reply is a count byte followed by at most 15 scan bytes. The request line stays
low while the request record sits in the ROM's software queue and is raised
again only if another batch remains. Reasserting it immediately was found to
trap `GetPollingCommand` in repeated request reads and is therefore covered by
the regression.

With multiple endpoints, command 28 opens an address-specific poll window:
non-addressed peripherals temporarily release the physically shared line, so
MBReq reflects only the selected endpoint. `GetPollingCommand` samples that
level after each command-28 completion and advances to the next address when
it is low. Modeling MBReq as an unconditional wired OR incorrectly attributed
keyboard traffic to the SCSI target; the two-accessory acceptance covers this
distinction, including the separate Set-2 make and break batches.

Traffic also works in the other direction. The ROM's eight-byte `K` packets
reset the keyboard, update its LEDs, and set its repeat rate. The driver
forwards those operations to MAME's AT keyboard and consumes controller
self-test/acknowledgement bytes rather than exposing them as key transitions.
Magic Bus state, the scan FIFO, and all in-flight transaction state participate
in save states.

### SCTG monitor/PCLink transport

The IDT monitor's `magicbus -i` command discovers an SCTG-only configuration,
validates the same 88-byte information record, and reports `MagicBus SCSI
controller connected`. Holding the modeled target-request input during
enumeration presents the request after the information record is accepted.
For the target-to-host direction, the monitor:

1. enters `CheckMagicBus`;
2. reads a 16-byte command-1 request record whose length is six 32-bit words
   and whose function byte is 18;
3. routes that function to `GetDataFunction`; and
4. issues command 3 to receive the aligned 24-byte target payload.

The payload executes the monitor's safe `FastChecksum` command (`0x80`) against
the first 16 bytes of `mBusBuffer` itself. Its recovered command layout is:

| Offset | Size | Probe value | Meaning |
|---:|---:|---:|---|
| `0` | 1 | `0x80` | command selector |
| `2` | 1 | returned `0` | status; `GetDataFunction` clears it before dispatch and writes `1` for an unsupported selector |
| `4` | 4 | `0x0000b280` | source address (`mBusBuffer`) |
| `8` | 4 | `0x00000010` | byte length |
| `16` | 4 | `0x12345678` | initial 32-bit sum |
| `20` | 4 | returned `0x92350908` | result |

`FastChecksum` adds four big-endian words to the supplied initial value, so
the independently predictable result is
`0x12345678 + 0x80000000 + 0x0000b280 + 0x10 = 0x92350908`.
The source excludes the result field, keeping the operation deterministic and
confined to the monitor's own scratch buffer.

The opposite request record uses function byte 19. `CheckMagicBus` routes it
to `SendDataFunction`, which issues command 7 and transmits the modified
24-byte monitor/PCLink buffer to the target through Dino DMA. The modeled target
checks command, status, length and exact result before raising DMA-end
completion. The probe invokes `magicbus -i` once for each direction because
one monitor command services one transport request.

The same dispatcher also exposes selectors `0x20` (`SystemStatus`), `0x40`
(`ProgramTheFlash`), `0xa0` (`SetCardPower`) and `0xc0` (`CCITTChecksum`).
Those routines are now mapped from the SDK ELF, but the peer deliberately does
not invoke the destructive flash/card-power operations. `CCITTChecksum` adds
no new transport behavior beyond the verified checksum round trip.

Dino receive DMA writes physical DRAM behind the CPU's cached low-address
mapping. After each modeled receive write, the driver invalidates matching
unlocked R3900 data-cache words before signaling DMA completion. This includes
Magic Bus plus SIB sound and telecom receive channels. The monitor's
peripheral-information path is the regression oracle: it validates an
88-byte DMA record in a cached stack buffer with no explicit cache operation.
The SCTG probe therefore also requires `AssignMagicBusAddresses`,
`MagicBusCommand`, and the final one-peripheral count; those checks catch stale
prefetched destination words even when the emitted record and checksum are
correct.

The production ROM exposes five methods on `MagicBusSCSITargetClient`.
`Attached` sets its attached byte after inherited setup, `Detached` delegates
to the base class, `CanHandlePeripheral` recognizes the literal `SCTG`, and
`ReinitializeClass` registers the class metadata. The fifth,
`PeripheralRequest` at `0x13e82e10`, is exactly `jr ra; nop`. The symbol and
call audit found no disk, block-read/write, or other payload client methods.
`SCTG` is therefore a SCSI-target-shaped endpoint used by the IDT monitor,
not evidence for a Magic Cap disk or an alternate Storeroom transport.
Inventing a deeper request parser would contradict the shipped handler.

The acceptance probe exercises discovery plus both directions of keyboard
traffic: it injects Caps Lock, observes Set-2 dispatch, and requires the ROM
to send the corresponding LED update back to the device.

```sh
python3 tools/magicbus_probe.py
python3 tools/magicbus_probe.py --require-clean
python3 tools/magicbus_probe.py --two-accessories --require-clean
python3 tools/magicbus_scsi_probe.py
```

The ordinary gate requires address assignment, validated peripheral info,
keyboard attachment, request handling, scan dispatch and LED control. The
two-accessory gate additionally requires two assignments and information
records plus entry into `MagicBusSCSITargetClient_Attached`. Both reject any
entry into `MagicBusError` or `MagicBus_HandleMagicBusFailure`. The probe
refuses the development ROM because those routine addresses shift and would
silently measure nothing.

The SCTG probe boots the release monitor, types `magicbus -i` through the
terminal's real key matrix, and holds the target-to-host request through
discovery. After function 18 and command 3 complete, it invokes the command a
second time with the host-to-target request and requires `SendDataFunction`
plus a 24-byte command-7 DMA receipt containing command `0x80`, status zero
and result `0x92350908`. It also requires the monitor's open,
assignment, command and low-level transaction checkpoints and a final
peripheral count of one, covering Dino-to-R3900 cache coherency during the
checksummed discovery DMA. It uses the SCSI-only configuration
because the monitor assigns a keyboard-plus-target chain with an address gap
that its simple standalone scanner does not traverse; the ordinary OS gate
covers that combined topology.

Magic Cap later repeats broadcast address assignment when it reinitializes
the bus. Modeled endpoints discard their earlier addresses and answer that
broadcast again, allowing the ROM to recreate its clients.
`tools/storage_backup_regression.py` exercises the default keyboard path
during both backup and restore and rejects any entry into
`MagicBus_HandleMagicBusFailure`.

## Glacier blocks

The monitor initializes two custom 16-bit GPIO/interrupt blocks at
`0xb0400000` and `0xb0800000`. The SDK debug type information gives this
layout for each:

| Offset | 16-bit register |
|---:|---|
| `0x00` | IO data output |
| `0x02` | MFIO data output |
| `0x04` | IO direction |
| `0x06` | MFIO direction |
| `0x08` | reserved |
| `0x0a` | MFIO select |
| `0x0c` | IO data input |
| `0x0e` | MFIO data input |
| `0x10`, `0x12` | IO/MFIO positive-edge interrupt enable |
| `0x14`, `0x16` | IO/MFIO negative-edge interrupt enable |
| `0x18`, `0x1a` | IO/MFIO positive-edge status/clear |
| `0x1c`, `0x1e` | IO/MFIO negative-edge status/clear |
| `0x20` | control |

Glacier 1 and 2 have different platform wiring. Modeling their register
semantics can wait until boot code reaches the corresponding interrupt or
GPIO paths, but both address windows should be logged from the start.

The current driver goes further than this original bring-up note: both blocks
route PC Card detect, READY/IREQ, BVD and write-protect inputs, latch insertion
edges, and expose slot common/attribute/I/O cycles. The linear-card option
persists an 8 MiB common-memory image. Unrelated raw images return a generic
SRAM CIS; erased and formatted Magic Cap storage images instead expose the
vendor tuple below.

The recovered General Magic FAQ now defines the OS-visible piece that generic
CIS lacks. Magic Cap expects vendor tuple `0xA0` with magic `GMMC`, version
`0x00010001`, card type and the common-memory offset of its metacluster. A
blank image uses type `BLNK`; a formatted card uses `RAMC`. This tuple belongs
in attribute memory, not at the beginning of the raw common-memory image.
Field layout, all card types, the standard-card `CardServer` path and a
concrete lifecycle acceptance sequence are in
[`developer-archives.md`](developer-archives.md#storage-cards-an-exact-os-visible-contract).
The implemented sequence is automated by `tools/storage_card_regression.py`;
it proves `BLNK` setup, the ROM-written `MCAP` header, a derived `RAMC` tuple,
fresh-process persistence and live Option-insert reformat. Each slot also has
a **PC Card slot battery** machine setting. Good drives BVD2/BVD1=`11`, Low
drives `01`, and Dead drives `00`; slot 1 BVD1 appears at Dino IO bit 1
(`0x10c00180`) and BVD2 at Glacier 1 IO-input bit 1 (`0x1040000c`). The ROM
maps those three codes to `kCardBatteryGood`, `kCardBatteryLow` and
`kCardBatteryDead`. The same regression selects the mounted card as Magic
Cap's new-item destination, draws on a Notebook page, leaves the scene to
commit the object, and proves the page's rendered pixels after reinsertion in
a fresh emulator process.

## Boot landmarks

| Address | ELF symbol | Emulator relevance |
|---:|---|---|
| `0x13c00000` | `Reset` | ROM offset zero/reset word |
| `0x13c0001c` | `BootMonitor` | IDT monitor entry |
| `0x13c00388` | `MM_InitializeDino` | earliest memory/peripheral setup |
| `0x13c02568` | `video_on` | monitor video enable |
| `0x13c02718` | `ResetSib` | monitor SIB setup |
| `0x13c027c8` | `HardResetBetty` | external Betty reset |
| `0x13c02a80` | `touch_init` | Betty setup and named register probes |
| `0x13c076b0` | `BettyTest` | monitor register readback test |
| `0x13c1d120` | `BootCap` | ELF entry / Magic Cap boot |
| `0x13c1ec80` | `MemorySize` | Apollo RAM-size selection |
| `0x13c1f358` | `AllocateScreenBuffer` | framebuffer placement |
| `0x13c1f860` | `DisplayServer_BootBlit` | geometry, palette, initial blit |
| `0x13c1fd18` | `EarlySetBuffer` | video DMA programming |
| `0x13c23644` | `SibCmdWriteBettyRegField` | production Betty writes |
| `0x13c236f0` | `SibCmdReadBettyReg` | production Betty reads |
| `0x13c25a00` | `InitializeDino` | OS-side Dino setup |
| `0x13c25bd4` | `InitializeGlacier` | OS-side Glacier setup |
| `0x13c25c34` | `SyncSibAndBetty` | OS-side SIB/Betty synchronization |

## Reproducing the static analysis

After following the SDK extraction instructions in
[`rom-layout.md`](rom-layout.md), install a big-endian MIPS binutils package
(`binutils-mips-linux-gnu` on Debian/Ubuntu) and run:

```sh
magic_cap_assets="${MAGIC_CAP_ASSETS:-$PWD/../magic-cap-assets}"
elf="$magic_cap_assets/sdk/extracted/Program_Files/debug/apollo/MagicCAP-USA"

mips-linux-gnu-readelf -h -l -S "$elf"
mips-linux-gnu-nm -n "$elf" |
  grep -E 'Reset$|BootMonitor$|BootCap$|MemorySize|Screen|Betty|Dino|Glacier'

mips-linux-gnu-objdump -d \
  --start-address=0x13c00000 --stop-address=0x13c00600 "$elf"
mips-linux-gnu-objdump -d \
  --start-address=0x13c1f358 --stop-address=0x13c1ff10 "$elf"
mips-linux-gnu-objdump -d \
  --start-address=0x13c23644 --stop-address=0x13c237a0 "$elf"
```

Ghidra can import the same ELF directly as big-endian MIPS. Preserve its ELF
section addresses; do not rebase the ROM to the CPU reset vector.
