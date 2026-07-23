# DataRover 840 / Magic Cap Emulator

An attempt to build an open-source emulator for the [General Magic DataRover 840](https://pdamuseum.eu/pda/datarover840/) — the last and best Magic Cap communicator (1998), running Magic Cap 3.1 on a MIPS CPU. No emulator for this machine exists anywhere today; the only way people run Magic Cap in 2026 is the 68K-era *Magic Cap Simulator* inside a classic Mac emulator, which is a different OS build for different hardware.

## The machine

| Component | Detail |
|---|---|
| CPU/SoC | Toshiba **TMPR3902U** (TX39 family, R3900 core, MIPS-I / R3000A-compatible ISA), 36.864 MHz (9.216 MHz osc ×4) |
| Cache | 4 KB I-cache, 1 KB D-cache |
| RAM | 4 MB (2× Hitachi 51W16160TT-6) |
| ROM | 8 MB mask ROM (2× OKI, labeled PIC31H / PIC31L); the 840F variant uses flash instead |
| Display | 480×320 grayscale LCD, backlit, resistive touchscreen (this ROM configures a 2bpp framebuffer) |
| Peripheral hardware | TX39 **"Dino"** integrated peripherals plus General Magic **"Betty"** SIB ASIC (GPIO, touch, ADC, sound, telecom) |
| I/O | 14.4k modem, 2× PC Card (PCMCIA) slots, IrDA, Magic Bus, mic + speaker |
| OS | Magic Cap 3.1 (build 3.1.2j in the archived ROMs) |

Sources: [PDA Museum](https://pdamuseum.eu/pda/datarover840/), [Old VCR teardown & history](http://oldvcr.blogspot.com/2022/12/magic-cap-from-magic-link-to-datarover.html) (board photos, chip identification), [Josh Carter's Magic Cap archive](https://joshcarter.com/magic_cap/).

## The ROM

The USA ROM image (build 3.1.2j) comes from the [Rosemary Software Archive](https://joshcarter.com/magic_cap/packages/) (`MagicCap-USA.zip` → `MagicCap-USA.image`, 4,528,151 bytes, dated 2000-05-04).

- sha256: `94785cb334f14eac00ed200af014c35972b4f25694103bc6a49b3afa280a6f1b`
- **Not committed to this repo** — it's copyrighted General Magic software (abandonware, but still). Keep it in `roms/` locally; that directory is git-ignored.
- Exact ROM, flasher, WinDownload, and SDK acquisition/extraction commands are
  kept in [`docs/rom-layout.md`](docs/rom-layout.md#download-the-rom-and-flasher-image).

### What initial inspection shows

- **Big-endian MIPS.** The image begins with valid BE MIPS-I code: `0x08F00007` = `j 0x...` over the embedded `"IDT MONITOR "` signature.
- Boots into the **IDT boot monitor** (© 1992 Integrated Device Technology, build dated Dec 5, 1997) before Magic Cap proper.
- The monitor knows two platforms (`BigBoard2`, `Sputnik2`) and probes for a **"Sony Core"** vs **"Toshiba Core"** CPU — matching the Magic Link → DataRover hardware lineage.
- The `0xB0C0_xxxx` block (kseg1 → physical `0x10C0_0000`) is the
  TX39 **Dino** peripheral module: video, UARTs, timers, interrupts, GPIO, and
  SIB. The external **Betty** ASIC exposes 16-bit registers over Dino's SIB;
  it is not directly memory mapped. See
  [`docs/memory-map.md`](docs/memory-map.md) and
  [`docs/betty-registers.md`](docs/betty-registers.md).
- **The image-format question is resolved.** The `.image` is raw, linear ROM
  content based at physical `0x13C0_0000`; the 8 MB flasher-card image contains
  a 1 KB header, the exact `.image` bytes, then erased (`0xFF`) space. The
  archived Icras SDK also contains an unstripped MIPS ELF with symbols and
  source paths. See [`docs/rom-layout.md`](docs/rom-layout.md).

## What already exists (survey, July 2026)

**No prior emulator.** Nothing in MAME (no DataRover/Magic Link/Envoy driver, no TX39/TMPR39xx support), nothing on GitHub, no QEMU machine. We'd be first.

Reusable open-source parts:

- **MAME `mips1` CPU core** ([`src/devices/cpu/mips/mips1.cpp`](https://github.com/mamedev/mame/tree/master/src/devices/cpu/mips)) — mature MIPS-I interpreter supporting R2000/R3000/R3041/etc., both endiannesses. The R3900 is R3000A-compatible for user/kernel code; TX39-specific bits (MAC instructions, config registers, simplified MMU) would need small additions.
- **MAME framework** — screen/LCD rendering, touch/pointer input, PCMCIA slot devices, serial/modem devices, RTC devices, save states, debugger with MIPS disassembly. Most of an emulator's boring 80% for free.
- **Toshiba TX39-family datasheets** on Bitsavers — [TMPR39xx family overview](http://www.bitsavers.org/components/toshiba/_dataSheet/TMPR39xx-family.pdf), TMPR3904/3912/3922 manuals (the 3902's documented siblings; the 3902 itself appears undocumented publicly, so sibling datasheets + ROM reverse engineering fill the gap).
- **Ghidra** — free RE suite with solid big-endian MIPS-I support for static analysis of the ROM.
- **Reference behavior**: the [Magic Cap Simulator](https://www.macintoshrepository.org/1316-magic-cap-simulator-1-0) under Basilisk II ([Adafruit guide](https://learn.adafruit.com/magic-cap-the-smartphone-os-from-the-90s/hardware-and-legacy)) shows what a booted Magic Cap should look/behave like.
- **Community knowledge**: [Old VCR blog](http://oldvcr.blogspot.com/2022/12/magic-cap-from-magic-link-to-datarover.html), [Josh Carter's FAQs](https://joshcarter.com/magic_cap/) (incl. developer docs and the 840F flasher, useful for understanding ROM layout), [comp.os.magic-cap archives](https://groups.google.com/g/comp.os.magic-cap), [archive.org DataRover 840 software](https://archive.org/details/DataRover840).

## Approach

Build it as a **MAME driver** (working in a MAME fork, upstreamable later), rather than writing a standalone emulator: the CPU core, LCD/input/PCMCIA/serial infrastructure, debugger, and preservation conventions all exist there, and MAME is fully OSS (BSD-3/GPL-2). This repo holds the reverse-engineering notes, analysis tooling, and driver code as it develops.

Fallback if MAME iteration feels heavy: a minimal standalone C/Rust harness reusing an existing R3000 interpreter for exploration, feeding findings back into the MAME driver.

## Plan

### Phase 0 — Repo & research base ✅
Repo, README, survey of existing parts (this document).

### Phase 1 — ROM understanding
- ~~**Hunt for the DataRover SDK.**~~ ✅ The
  [archive.org DataRover840](https://archive.org/details/DataRover840)
  bundle contains the complete Icras SDK 3.2, including the hosted Win32
  build, an unstripped Apollo MIPS ELF, debugger data, and platform headers.
- ~~Resolve the image format question (4.3 MB image vs 8 MB ROM).~~ ✅ See
  [`docs/rom-layout.md`](docs/rom-layout.md) and `tools/rom_info.py`.
- ~~Analyze the SDK's unstripped BE MIPS ELF, cross-check its ROM map, and
  determine the reset-vector alias lifetime.~~ ✅ The first reset jump moves
  execution from `0xBFC0_0000` to the normal `0xB3C0_001C` ROM alias.
- ~~Annotate the early monitor and extract the initial hardware register
  map.~~ ✅ Dino, both Glaciers, UARTs, interrupts, the 2bpp framebuffer, and
  Betty's SIB protocol are documented.
- ~~Locate the Magic Cap entry and its initial hardware drivers.~~ ✅ The ELF
  exposes `BootCap`, display, Dino/Glacier, SIB/Betty, touch, serial, sound,
  and modem symbols for continued behavioral work.
- ~~Deliver `docs/memory-map.md` and `docs/betty-registers.md`.~~ ✅

### Phase 2 — Minimal machine bring-up
- Toolchain smoke test first: build stock MAME on this machine with `SOURCES=` scoped to a single small driver, confirming the edit-build-run loop is fast enough before writing any driver code.
- MAME skeleton driver: `mips1` (R3000A BE for now) + 4 MB RAM + ROM mapping.
- Run until the first unimplemented hardware access; use MAME's unmapped-access logging + debugger to iterate.
- Stub the IDT monitor's UART first — a serial console is likely the earliest sign of life and a debugging channel thereafter.

### Phase 3 — Display & Betty
- Implement enough of Betty (interrupts, GPIO, timers) for the boot to proceed.
- **Use the ROM's own diagnostics as the test suite**: locate the IDT monitor's self-test/readback routines (the `... readback: 0x%x (0x%x)` family found in Phase 1) and drive them via the serial console; each passing readback is an acceptance test for the corresponding Betty register.
- Regression harness in `tools/`: run the emulator headless, capture serial output up to a boot checkpoint, and diff against a known-good log — so hardware-model regressions surface as text diffs, not "the screen looks off".
- Find and render the framebuffer: 480×320, 2bpp grayscale, 120-byte stride,
  at the top 38,400 bytes of RAM.
- First milestone: **Magic Cap boot screen renders**.

### Phase 4 — Interactive desk
- Touchscreen (ADC via Betty) → MAME pointer input.
- RTC, NVRAM/persistent storage so the OS keeps state.
- Sound (Betty SoundCfg) as stretch.
- Milestone: **navigate the Magic Cap desk with the mouse**.

### Phase 5 — Beyond
- TX39 core fidelity: add R3900 extensions to MAME's `mips1` if the ROM actually uses them.
- PC Card slots (linear flash card images — the flasher-card image from the archive is a ready-made test), package installation (`.pkg` files from the archive).
- Serial/modem → PPP bridge for the true endgame: **Magic Cap on the internet**, running the archived Web Browser 4.0.
- 840F flash variant, Japan ROM, MAME upstream submission.

## Verification (no real hardware)

We don't own a DataRover 840, so correctness is judged by external signals only:

- **Screen appearance** vs. photos/screenshots of Magic Cap 3.x in the wild ([PDA Museum](https://pdamuseum.eu/pda/datarover840/), [Old VCR](http://oldvcr.blogspot.com/2022/12/magic-cap-from-magic-link-to-datarover.html), [Pen Computing review](http://www.pencomputing.com/magic_cap/data_rover_840.html)) and the 68K Magic Cap Simulator's UI as a behavioral reference.
- **The ROM's own voice**: the IDT boot monitor and Magic Cap debug builds talk over the serial port — an emulated UART console is our primary instrument for everything that happens before (and behind) the screen.
- **Internal consistency**: diagnostics in the ROM (Betty register readback tests, memory sizing) passing is itself evidence the hardware model is right.

## Repo layout

```
roms/       ROM images — git-ignored, bring your own (see links above)
docs/       RE notes: memory map, Betty registers, boot flow
tools/      analysis scripts (ROM splitting, checksums, string maps)
mame/       driver code (initially patches/fork notes against upstream MAME)
```

Driver development happens in the MAME fork at [ddanila/mame](https://github.com/ddanila/mame) (cloned as a sibling of this repo, `../mame`, work happens on the `custom` branch, never on `master`); this repo tracks notes and patches.

## License

Code and notes here: MIT. MAME driver code follows MAME's licensing. ROM images remain © General Magic and are not distributed here.
