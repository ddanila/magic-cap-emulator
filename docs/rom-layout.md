# ROM and SDK layout

This note records facts reproduced from the archived USA 3.1.2j release rather
than assumptions made from ROM strings. The copyrighted inputs are not stored
in this repository.

## Reproducible inputs

The [Rosemary Software Archive][packages] publishes:

- `MagicCap-USA.zip` containing `MagicCap-USA.image` (4,528,151 bytes,
  SHA-256 `94785cb334f14eac00ed200af014c35972b4f25694103bc6a49b3afa280a6f1b`)
- `DataRover840FRomFlasher.gz`, an 8 MiB linear flash-card image
- `WinDownload.zip`, the Windows serial download program

The [Archive.org DataRover840 item][archive] contains `Datarover840.zip`
(SHA-256 `4455c41a681006b6cac791c639014b87739c2513212078708cd9efaaf554d839`).
Contrary to the previous assumption that an SDK still needed to be found, its
`Developer/IcrasSoftwareDevelopmentKit3.2` directory is the complete Windows
SDK installer.

Its InstallShield cabinets contain these particularly useful Apollo artifacts:

| SDK path | Purpose |
|---|---|
| `Program Files/debug/apollo/MagicCAP-USA` | Unstripped big-endian MIPS-I ELF |
| `Program Files/debug/apollo/MagicCap-USA.debug.x` | Magic Cap debugger database |
| `Program Files/debug/apollo/MagicCAP-USA.image` | Exact release ROM image |
| `Program Files/include/MemoryMapDino.asm.h` | Link/download memory-map constants |

The SDK's ROM image is byte-identical to the separately published image. Keep
all extracted SDK and ROM files under `roms/`, which is git-ignored.

## The `.image` file is raw ROM data

`MagicCAP-USA.image` starts directly with executable big-endian MIPS code:

```text
00000000  08 f0 00 07 00 00 00 00 00 00 00 00 49 44 54 20
00000010  4d 4f 4e 49 54 4f 52 20 ...
```

The first word is a MIPS `j` instruction and the `IDT MONITOR ` marker starts
at offset `0x0c`. There is no download-container header.

The uncompressed 840F card proves how the non-power-of-two file is used:

| Card offset | Size | Contents |
|---|---:|---|
| `0x000` | 12 | ASCII `BowserLives\0` |
| `0x00c` | 4 | Big-endian format version `1` |
| `0x010` | 4 | Reserved, zero |
| `0x014` | 4 | Big-endian base address `0xb3c00000` |
| `0x018` | 4 | Big-endian payload length `0x00451817` |
| `0x01c` | `0x3e4` | Zero-filled reserved header |
| `0x400` | `0x451817` | `.image` file, byte-for-byte |
| `0x451c17` | `0x3ae3e9` | Erased flash (`0xff`) to 8 MiB |

Therefore the initial emulator ROM region should be 8 MiB, pre-filled with
`0xff`, with the `.image` loaded at region offset zero. Splitting even/odd bytes
for the two physical mask-ROM chips is not necessary unless physical chip dumps
surface later.

Run the local verifier against separately obtained files:

```sh
python3 tools/rom_info.py roms/MagicCap-USA.image \
  --flasher roms/DataRover840FRomFlasher
```

## CPU address

The historical [840F update instructions][update] invoke:

```text
WinDownload -sysrom -p -base 0xb3c00000 ... MagicCAP-USA.image
```

The SDK header independently defines:

```c
#define kROMStart      0x13C00000
#define kFlashROMStart 0xB3C00000
#define kTotalROMSize  0x00800000
```

The SDK ELF confirms this map:

- architecture: MIPS R3000, MIPS-I, big-endian
- ROM load segment: `0x13c00000`, size `0x44dde8`
- `.monitortext`: `0x13c00000`
- `.text`: `0x13c1d120`
- entry point / `BootCap`: `0x13c1d120`
- `Reset`: `0x13c00000`
- `BootMonitor`: `0x13c0001c`

The ELF is unstripped and includes source paths and symbols such as
`MemorySize`, `HardResetBetty`, `DisplayServer_BootBlit`, and
`SerialInterfaceMemServer_ReinitializeClass`. It should be the primary static
analysis input; the raw `.image` remains the byte-level source used by MAME.

MIPS maps virtual `0xb3c00000` through kseg1 to physical `0x13c00000`. The CPU
still begins at the architectural reset vector `0xbfc00000`, so the skeleton
driver also needs a reset-time alias of the start of ROM at physical
`0x1fc00000`. Whether that alias persists after the board's address remapper is
configured remains an open hardware question and should be settled from early
monitor accesses.

[archive]: https://archive.org/details/DataRover840
[packages]: https://joshcarter.com/magic_cap/packages/
[update]: https://joshcarter.com/magic_cap/faqs/updating_flash_rom/
