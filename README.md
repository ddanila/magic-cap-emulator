# DataRover 840 / Magic Cap Emulator

An attempt to build an open-source emulator for the [General Magic DataRover 840](https://pdamuseum.eu/pda/datarover840/) — the last and best Magic Cap communicator (1998), running Magic Cap 3.1 on a MIPS CPU. No emulator for this machine exists anywhere today; the only way people run Magic Cap in 2026 is a *Magic Cap Simulator* (a native Mac recompile of the OS, not a hardware emulator) inside a classic Mac emulator — see [The Magic Cap Simulators](#the-magic-cap-simulators) below.

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
- **Not committed to this repo** — it's copyrighted General Magic software
  (abandonware, but still). Keep persistent local downloads outside the Git
  checkout under `~/fun/magic-cap-assets/`; the MAME-ready ROM path is
  `~/fun/magic-cap-assets/roms/datarover840/magiccap-usa.image`.
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

**No prior emulator.** Nothing in MAME (no DataRover/Magic Link/Envoy driver, no TX39/TMPR39xx support), nothing on GitHub, no QEMU machine. We'd be first. The only documented prior preservation effort is [Cooper Hewitt / Small Data Industries (2019)](https://www.cooperhewitt.org/2019/05/13/a-predecessor-of-todays-smartphones/), who concluded there was no non-destructive way to dump the Motorola Envoy's TSOP-56 ROMs and fell back to running the Mac simulator under Basilisk II — i.e. application-level simulation, not device emulation. No Magic Link or Envoy (68K-generation) ROM dump is known to exist, so those machines can't be device-emulated today; the DataRover, with its freely downloadable 3.1.2j image, is the one Magic Cap machine that can.

Reusable open-source parts:

- **MAME `mips1` CPU core** ([`src/devices/cpu/mips/mips1.cpp`](https://github.com/mamedev/mame/tree/master/src/devices/cpu/mips)) — mature MIPS-I interpreter supporting R2000/R3000/R3041/etc., both endiannesses. The R3900 is R3000A-compatible for user/kernel code; TX39-specific bits (MAC instructions, config registers, simplified MMU) would need small additions.
- **MAME framework** — screen/LCD rendering, touch/pointer input, PCMCIA slot devices, serial/modem devices, RTC devices, save states, debugger with MIPS disassembly. Most of an emulator's boring 80% for free.
- **Toshiba TX39 core documentation.** The best CPU reference is the full [*TX39 Family Core Architecture User's Manual*](https://archive.org/details/manualzilla-id-7260633) (Jul 1995, 246 pp, on archive.org) — it documents the R3900 core in emulation-grade depth: five-stage pipeline, complete instruction set with per-instruction detail, MMU direct segment mapping, CP0/exception processing, I/D caches with lock functions, and debug registers. Supplement with the [Bitsavers TMPR39xx datasheets](http://www.bitsavers.org/components/toshiba/_dataSheet/TMPR39xx-family.pdf) (TMPR3904/3912/3922, the 3902's documented siblings). Neither mentions the TMPR3902 by name — the SoC-specific peripherals (Dino, Betty) remain undocumented anywhere public, so those still come from ROM reverse engineering.
- **Ghidra** — free RE suite with solid big-endian MIPS-I support for static analysis of the ROM.
- **Reference behavior**: the Magic Cap Simulators — see the dedicated section below.
- **General Magic primary docs** on Bitsavers' [`/pdf/generalMagic/`](https://bitsavers.org/pdf/generalMagic/) — `Using_Magic_Cap.pdf`, the [*Telescript Language Reference*](https://bitsavers.org/pdf/generalMagic/Telescript_Language_Reference_Oct95.pdf) (Oct 1995, 263 pp; TDE 1.0 Alpha), and the Sony Magic Link Press Kit. Telescript is the agent language behind Magic Cap's messaging/comms stack — useful context for the parts of the ROM that aren't UI.
- **Community knowledge**: [Old VCR blog](http://oldvcr.blogspot.com/2022/12/magic-cap-from-magic-link-to-datarover.html), [Josh Carter's FAQs](https://joshcarter.com/magic_cap/) (incl. developer docs and the 840F flasher, useful for understanding ROM layout), [comp.os.magic-cap archives](https://groups.google.com/g/comp.os.magic-cap), [archive.org DataRover 840 software](https://archive.org/details/DataRover840).

## The Magic Cap Simulators

Two distinct simulators exist, and the difference matters:

- **Magic Cap Simulator 1.0** ([Macintosh Repository](https://www.macintoshrepository.org/1316-magic-cap-simulator-1-0)) — Magic Cap **1.x**, the Sony Magic Link / Motorola Envoy era. 68K, runs under Basilisk II ([Adafruit guide](https://learn.adafruit.com/magic-cap-the-smartphone-os-from-the-90s/hardware-and-legacy)). UI and internals diverge noticeably from 3.1; treat it as a curiosity.
- **The Rosemary SDK simulator** — Magic Cap **3.x**, part of the actual DataRover development environment (PowerPC, Mac OS 7.5.5+, CodeWarrior Pro era; needs SheepShaver/QEMU rather than Basilisk II). The SDK is on Macintosh Garden per [Old VCR's TLS post](http://oldvcr.blogspot.com/2023/01/bringing-tls-to-magic-cap-datarover.html); the standalone `RosemarySimulatorMac.sit` is also on Cameron Kaiser's [Floodgap Gopher staging archive](gopher://gopher.floodgap.com/9/archive/magic-cap-3/), and the SDK tools documentation is on the resurrected [datarover.com](http://www.datarover.com/Develop/MagicCap/Docs/Tools/CWMagic/Simulator.html) (self-signed cert). **This is the reference that matches our target OS version.**
- **Windows-hosted builds** existed too but are less useful: a Magic Cap 1.x *Pre-release 1.0, build 327* survives on [archive.org](https://archive.org/details/magic-cap) (runs under DOSBox + Win 3.1), and a "Build 2001" simulator (~Magic Cap 3.1) circulated on BetaArchive in 2014 but its download is dead — a lost-media recovery target if the 3.x Windows sim ever matters.

A simulator is a native Mac recompile of the same portable Magic Cap source tree our MIPS ROM was built from — not a hardware emulator. It says nothing about the TX39, Betty, or timing, but it is a **debug build with introspection tools** the device ROM lacks, which makes it useful well beyond "what should the screen look like":

- **Runtime object-model ground truth.** The simulator's Inspector and `Dump Package` / `Dump Inspector Target Deep` commands write full text descriptions (ObjectMaker syntax) of any live object — object IDs, fields, flags, class names. The same object structures live in our ROM's persistent store; dumps from the simulator are a labeled map for interpreting them. SDK headers (`Indexicals.h`, class definition files) give the complete class hierarchy and indexical numbering. This complements the unstripped Apollo ELF: the ELF names the code, the simulator dumps describe the *data*.
- **A specification of the hardware abstraction boundary.** The simulator's Hardware menu is effectively the list of what the portable OS expects from the platform layer: power on/off, warm reset, two card slots, phone-line connect/incoming-call events, hardware keyboard attach, memory sizing. Anything *not* simulated there is device-specific — a useful razor for deciding whether a Betty behavior is OS-visible or board plumbing.
- **Acceptance-test material.** The debug runtime carries a hidden Testing Scene, an "Execute Standard System" self-test, and action journaling/replay. If the device ROM retains any of these, triggering them inside the emulated DataRover is a strong internal-consistency signal (same philosophy as the `BettyTest` checkpoint).
- **An end-to-end package loop.** The SDK builds packages; the simulator runs them natively; PCLink (already working in this driver) installs them onto the emulated DataRover. Building a trivial package and comparing its behavior side by side closes the loop from source to emulated device. The Floodgap archive also hosts a ready-made MIPS-native device package — Kaiser's 2023 TLS-capable `WebBrowser-MIPS-USA.pkg` (built with the same Rosemary gcc 2.7.1 toolchain) — a real payload to install and run without building anything first.
- **Debug-build details**: `Assert` / `Whisper` / `Log` / `DebugMessage` macros are compiled in only in the simulator ("ignored on communicators"), and "Simulate Device Contrast" confirms the 16-gray LCD rendering expectations.

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

### Phase 2 — Minimal machine bring-up ✅
- ~~Toolchain smoke test first: build stock MAME on this machine with
  `SOURCES=` scoped to a single small driver, confirming the edit-build-run
  loop is fast enough before writing any driver code.~~ ✅
- ~~MAME skeleton driver: big-endian R3900 + 4 MB RAM + ROM mapping.~~ ✅
- ~~Run until the first unimplemented hardware access; use MAME's
  unmapped-access logging + debugger to iterate.~~ ✅
- ~~Stub the IDT monitor's UART first.~~ ✅ The monitor reaches an interactive
  `<IDT>` prompt, and its output is captured by the regression harness.

### Phase 3 — Display & Betty
- ~~Implement enough of Betty (interrupts, GPIO, timers) for the boot to
  proceed.~~ ✅ A 16-register Betty shadow, SIB completion flags, RTC,
  power-good, stop-timer completion, and absent-card GPIO state reach
  `BootCap`.
- ~~**Use the ROM's own diagnostics as the test suite**: locate and drive the
  IDT monitor's Betty self-test/readback routine through the serial
  console.~~ ✅ `python3 tools/serial_regression.py --checkpoint betty` calls
  the ROM's `BettyTest`; every failed comparison branches to `StayHere`, while
  the passing checkpoint returns to the `<IDT>` prompt.
- ~~Regression harness in `tools/`: run the emulator headless, capture serial
  output up to a boot checkpoint, and diff against a known-good log.~~ ✅ See
  `python3 tools/serial_regression.py`.
- ~~Find and render the framebuffer: 480×320, 2bpp grayscale, 120-byte stride,
  at the top 38,400 bytes of RAM.~~ ✅
- ~~First milestone: **Magic Cap boot screen renders**.~~ ✅ The ROM's centered
  top-hat startup artwork is visible in the `LCD` view.

### Phase 4 — Interactive desk
- ~~Touchscreen (ADC via Betty) → MAME pointer input.~~ ✅ Pointer presses
  drive Betty's six-sample touch macro, including the ROM's three-point
  calibration flow.
- ~~RTC, NVRAM/persistent storage so the OS keeps state.~~ ✅ Main DRAM and
  Dino's 32,768 Hz RTC are battery-backed. The power button enters
  suspend-to-RAM and a second press wakes the CPU.
- ~~Sound output (Betty `SoundCfgB` and Dino's sound-hold FIFO) as stretch.~~
  ✅ The ROM's own 750 Hz startup tone is rendered as signed 16-bit mono at
  the 11.025 kHz rate programmed by Dino. Buffered SIB DMA remains future
  work.
- ~~Milestone: **navigate the Magic Cap desk with the mouse**.~~ ✅ The bare
  hat is the early splash; the later circled-hat `Magic Cap™ / Touch the
  screen to begin` scene is interactive. After upper-left, lower-right, and
  center calibration taps, build 3.1.2j reaches the workbench.

### Phase 5 — Beyond
- ~~TX39 core fidelity: add R3900 extensions to MAME's `mips1` if the ROM
  actually uses them.~~ ✅ The modem DSP contains 792 TX39 `MADD`
  instructions. The R3900 device now implements `MADD`/`MADDU`, with an
  isolated arithmetic regression; see [`docs/tx39-cpu.md`](docs/tx39-cpu.md).
- ~~PC Card slots (linear flash card images — the flasher-card image from the
  archive is a ready-made test), package installation (`.pkg` files from the
  archive).~~ ✅ Both 8 MiB linear slots expose common/attribute memory and
  insertion signals. PCLink installs an archived package into live Magic Cap;
  see [`docs/pclink.md`](docs/pclink.md).
- ~~External serial cable and PCLink host.~~ ✅ Both Dino UARTs are wired to
  MAME RS-232 ports. The automated host reproduces WinPCLink framing and
  verifies an OS-visible package install.
- Modem → PPP bridge for the true endgame: **Magic Cap on the internet**,
  running the archived Web Browser 4.0. ✅ The emulated PC Card modem is
  detected by Magic Cap, accepts the ROM's Hayes sequence, completes live
  Slirp LCP/IPCP, receives `10.0.2.15`, and sends IPv4 packets. Web Browser
  4.0 also installs through PCLink. Loading local HTTP in that browser on the
  same configured guest remains the final combined acceptance; see
  [`docs/modem.md`](docs/modem.md).
- ~~840F flash variant and Japan ROM.~~ ✅ The 840F has four persistent
  writable 2 MiB flash lanes; the separately archived Japan image is an
  audited clone set. See [`docs/rom-layout.md`](docs/rom-layout.md).

## Verification (no real hardware)

We don't own a DataRover 840, so correctness is judged by external signals only:

- **Screen appearance** vs. photos/screenshots of Magic Cap 3.x in the wild ([PDA Museum](https://pdamuseum.eu/pda/datarover840/), [Old VCR](http://oldvcr.blogspot.com/2022/12/magic-cap-from-magic-link-to-datarover.html), [Pen Computing review](http://www.pencomputing.com/magic_cap/data_rover_840.html)) and the Rosemary (Magic Cap 3.x) Simulator's UI as a behavioral reference — see [The Magic Cap Simulators](#the-magic-cap-simulators).
- **Simulator object dumps**: the Rosemary simulator's Inspector/`Dump Package` output describes the same object structures our ROM stores — a cross-check for persistent-store interpretation, and its Testing Scene / "Execute Standard System" self-test (if retained in the device ROM) is more ROM-provided diagnostics to drive.
- **The ROM's own voice**: the IDT boot monitor and Magic Cap debug builds talk over the serial port — an emulated UART console is our primary instrument for everything that happens before (and behind) the screen.
- **Internal consistency**: diagnostics in the ROM (Betty register readback tests, memory sizing) passing is itself evidence the hardware model is right.

## Repo layout

```
roms/       Optional git-ignored compatibility path; persistent assets live outside the repo
docs/       RE notes: memory map, Betty registers, boot flow
tools/      analysis scripts (ROM splitting, checksums, string maps)
mame/       driver code (initially patches/fork notes against upstream MAME)
```

Driver development happens in the MAME fork at [ddanila/mame](https://github.com/ddanila/mame) (cloned as a sibling of this repo, `../mame`, work happens on the `custom` branch, never on `master`); this repo tracks notes and patches.

The reproducible build, launch, monitor-selection, serial-regression, and
snapshot commands are in [`docs/mame-bringup.md`](docs/mame-bringup.md).

## License

Code and notes here: MIT. MAME driver code follows MAME's licensing. ROM images remain © General Magic and are not distributed here.
