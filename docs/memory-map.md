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

The initial driver can map RAM at zero, an 8 MiB ROM region at
`0x13c00000`, its kseg1 alias, and the reset alias. Unpopulated bytes in the ROM
region must read as `0xff`; the published image occupies only its first
`0x451817` bytes.

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
elf=roms/sdk/Program_Files/debug/apollo/MagicCAP-USA

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
