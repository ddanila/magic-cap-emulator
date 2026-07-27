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
structure.

The video-control bit-depth field is bits 7:6 (`0x00` mono, `0x40` 2-bit gray,
`0x80` 4-bit gray, `0xc0` 8-bit color). Bit 0 enables video scanning and bit 1
turns on the display. `EarlySetBuffer` writes the RAM buffer start to `+0x030`
and an encoded end address to `+0x034`.

Betty is **not** this register block. It is an external device reached through
Dino's SIB subframe registers; see [`betty-registers.md`](betty-registers.md).

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
- A battery-backed 32,768 Hz RTC counter, alarm and rollover interrupts,
  freeze/clear controls, and a separate persistent clock record.
- Read-only power-good/on-button inputs plus cold-start/VCC power state.
  `StopCpu` suspends the R3900; a pending enabled interrupt releases a
  VCC-on doze, while an on-button edge restores VCC after power-down. Dino's
  bank-6 low-priority summary follows enabled status in banks 1–5, including
  the polled `DeepDoze` wake path.
- Synchronous stop-timer completion used by the low-level Betty reset.
- Power-on mode input bit 3: high boots Magic Cap, low stays in the IDT
  monitor.
- Video high-buffer selection and 480×320, 2 bpp framebuffer scanout.
- Main DRAM backed by MAME NVRAM, kept in the external runtime directory
  selected with `-nvram_directory`.

These are deliberately ROM-observed behaviors, not a claim that all Dino
timing and interrupt semantics are complete. Sound-receive DMA/microphone
input and board-level effects of the power-rail outputs remain outside this
implemented subset.

## Magic Bus

Magic Bus is Dino's peripheral bus for external accessories, at `0x0e0`-`0x0fc`:
`mbusControl1`, `mbusControl2`, the DMA start/length/count registers,
`mbusCommand` and `mbusData`. Bits 31:29 of `mbusControl1` are status —
`kMbusEnabledStatusMask`, `kMbusEmptyStatusMask` and `kMbusIntStatusMask` — and
the driver synthesizes them rather than returning whatever the OS last wrote.
`TestMBReqLine` (`0x13c28364`) samples bit 29 as the peripheral request line.
Its positive and negative edges latch interrupt-bank-2 bits `0x08` and `0x04`.

The physical connector is broader than the current endpoint. *Using Magic
Cap*, p. 217, describes PCs, external modems, external keyboards and other
accessories, with multiple devices commonly daisy-chained. The present machine
configuration intentionally models only one optional `ATKB` keyboard. A future
topology should enumerate multiple independently addressed descriptors rather
than treating “more Magic Bus” as extra keyboard scan codes; the acceptance
backlog is in [`user-guide.md`](user-guide.md).

The controller completes transfers synchronously but preserves the ROM's four
transaction classes:

- type 1 returns peripheral data through `mbusData` for four bytes or through
  receive DMA for larger blocks, then raises command-detect and, for DMA,
  DMA-end;
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
the addressed peripheral's code. The modeled endpoint uses address zero and
implements every command the ROM sends to its built-in keyboard client:

| Wire word | High-level command | Use |
|---:|---:|---|
| `def0` | 31, broadcast | ask an unaddressed peripheral to identify itself |
| `dca8` | 24, address 0 | accept address zero |
| `dce0` | 21, address 0 | finish assignment |
| `dcf8` | 32, address 0 | begin peripheral discovery |
| `cc5c` / `cc60` | 12 / 13 | select the ID or full information record |
| `cc24` | 2 | read the selected record or keyboard data |
| `cc18` | 1 | read a pending request record |
| `cc3c` | 5 | write keyboard reset, LED or typematic control |
| `dcc8` / `decc` | 28 | addressed/broadcast request polling |

`MagicBus_AssignMagicBusAddress` broadcasts command 31. The keyboard raises
MBReq, accepts command 24, and drops MBReq; the ROM then finishes assignment
and reads ID `ATKB`. It next accepts an 88-byte
`MagicbusPeripheralInfo` record. The record layout was recovered from STABS
types in the unstripped SDK image:

- length at byte 2 and peripheral ID at byte 4;
- hardware, software and protocol revisions at bytes 8–10;
- three frequency/delay pairs at bytes 12–35;
- latency and request timing at bytes 36–51;
- power figures at bytes 52–63;
- maximum request length at byte 64;
- variable strings beginning at byte 80; and
- a big-endian 16-bit checksum after `pInfoLength + 2` bytes.

The ROM initializes its built-in `MagicBusATKeyboard` client only after that
length and checksum validate. “Magic Bus accessory” in MAME's machine
configuration defaults to **AT keyboard** and can be changed to **None**. A
reset applies a changed selection. **None** deliberately leaves address
assignment unanswered; this ROM counts that silence as a peripheral failure
and can eventually show the attached-device alert.

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

Traffic also works in the other direction. The ROM's eight-byte `K` packets
reset the keyboard, update its LEDs, and set its repeat rate. The driver
forwards those operations to MAME's AT keyboard and consumes controller
self-test/acknowledgement bytes rather than exposing them as key transitions.
Magic Bus state, the scan FIFO, and all in-flight transaction state participate
in save states.

The acceptance probe exercises discovery plus both directions of keyboard
traffic: it injects Caps Lock, observes Set-2 dispatch, and requires the ROM
to send the corresponding LED update back to the device.

```sh
python3 tools/magicbus_probe.py
python3 tools/magicbus_probe.py --require-clean
```

The gate requires address assignment, validated peripheral info, keyboard
attachment, request handling, scan dispatch and LED control, with zero entries
into `MagicBusError` or `MagicBus_HandleMagicBusFailure`. The probe refuses the
development ROM because those routine addresses shift and would silently
measure nothing.

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
magic_cap_assets="$HOME/fun/magic-cap-assets"
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
