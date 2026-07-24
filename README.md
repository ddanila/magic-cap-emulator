# DataRover 840 / Magic Cap Emulator

The first emulator for the [General Magic DataRover 840](https://pdamuseum.eu/pda/datarover840/) — the last and best Magic Cap communicator (1998), running Magic Cap 3.1 on a MIPS CPU. It is built as a MAME driver (fork: [ddanila/mame](https://github.com/ddanila/mame), `custom` branch); this repository holds the reverse-engineering notes, analysis tooling, and regression harnesses.

**Current state:** the emulated machine boots ROM build 3.1.2j to the interactive Magic Cap workbench — touchscreen, persistent storage, sound, both PC Card slots, package installation over serial PCLink, and a PC Card modem completing live PPP all work. See [Status](#status).

Before this project (survey, July 2026) no emulator for any Magic Cap device existed — nothing in MAME, on GitHub, or in QEMU. The only way people ran Magic Cap was the Mac-hosted *Magic Cap Simulator*, a native recompile of the OS rather than a hardware emulator ([details below](#the-magic-cap-simulators)).

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
- These inputs are hobbyist-hosted and could disappear, so the local mirror is
  the dependency rather than the download: `tools/fetch_assets.sh all` fetches
  and checksum-verifies every research input (USA/Japan ROMs, 840F flasher,
  packages, the original Windows tools, the TX39 manual, and the SDK's Apollo
  ELF), and `--verify` re-checks the mirror without any network access. Exact
  per-file provenance is in
  [`docs/rom-layout.md`](docs/rom-layout.md#mirror-everything-at-once).

Key facts established by analysis (details in [`docs/`](docs/)):

- **Big-endian MIPS-I.** The image begins with valid BE code: `0x08F00007` =
  `j 0x...` over the embedded `"IDT MONITOR "` signature. It boots the **IDT
  boot monitor** (© 1992 Integrated Device Technology, build dated Dec 5,
  1997) before Magic Cap proper. The monitor knows two platforms (`BigBoard2`,
  `Sputnik2`) and probes for a "Sony Core" vs "Toshiba Core" CPU — matching
  the Magic Link → DataRover hardware lineage.
- **Image format.** The `.image` is raw, linear ROM content based at physical
  `0x13C0_0000`; the 8 MB flasher-card image contains a 1 KB header, the exact
  `.image` bytes, then erased (`0xFF`) space. See
  [`docs/rom-layout.md`](docs/rom-layout.md).
- **Memory map.** The `0xB0C0_xxxx` block (kseg1 → physical `0x10C0_0000`) is
  the TX39 **Dino** peripheral module: video, UARTs, timers, interrupts, GPIO,
  and SIB. The external **Betty** ASIC exposes 16-bit registers over Dino's
  SIB; it is not directly memory mapped. See
  [`docs/memory-map.md`](docs/memory-map.md) and
  [`docs/betty-registers.md`](docs/betty-registers.md).
- **Symbols.** The [archive.org DataRover840](https://archive.org/details/DataRover840)
  bundle contains the complete Icras SDK 3.2: a hosted Win32 build, debugger
  data, platform headers, and an **unstripped Apollo MIPS ELF** whose symbols
  (`BootCap`, display, Dino/Glacier, SIB/Betty, touch, serial, sound, modem)
  are the backbone of ROM annotation.

## Approach

Build it as a **MAME driver** (in a fork, upstreamable later) rather than a standalone emulator. MAME supplies the mature [`mips1` CPU core](https://github.com/mamedev/mame/tree/master/src/devices/cpu/mips) (the R3900 is R3000A-compatible; the fork adds the TX39 `MADD`/`MADDU` extension), plus LCD rendering, touch/pointer input, PCMCIA slots, serial/modem and RTC devices, save states, and a debugger with MIPS disassembly — most of an emulator's boring 80% — and it is fully OSS (BSD-3/GPL-2). A minimal standalone C/Rust harness was the fallback plan but was never needed.

Bring-up followed scoped `SUBTARGET` builds and MAME's unmapped-access logging; the reproducible build, launch, and regression commands are in [`docs/mame-bringup.md`](docs/mame-bringup.md).

## Status

### What works

| Subsystem | Verified behavior | Details |
|---|---|---|
| Boot & serial | IDT monitor reaches an interactive `<IDT>` prompt; both Dino UARTs on MAME RS-232 | [`memory-map.md`](docs/memory-map.md) |
| Betty (SIB ASIC) | Boot reaches `BootCap`; the ROM's own `BettyTest` diagnostic passes | [`betty-registers.md`](docs/betty-registers.md) |
| Display | 480×320 2bpp framebuffer renders splash → welcome → workbench | [`memory-map.md`](docs/memory-map.md) |
| Touch | Pointer input drives the touch macro incl. three-point calibration; desk is mouse-navigable | [`betty-registers.md`](docs/betty-registers.md) |
| Persistence & power | Battery-backed DRAM + RTC as NVRAM; power-button suspend/wake | [`memory-map.md`](docs/memory-map.md) |
| Sound | ROM's startup tone rendered at the programmed rate (unbuffered path) | [`betty-registers.md`](docs/betty-registers.md) |
| TX39 extensions | `MADD`/`MADDU` implemented for the modem DSP's 792 uses | [`tx39-cpu.md`](docs/tx39-cpu.md) |
| PC Cards | Both linear slots with CIS and insertion signaling | [`mame-bringup.md`](docs/mame-bringup.md) |
| PCLink | Recovered WinPCLink protocol installs archived packages into live Magic Cap | [`pclink.md`](docs/pclink.md) |
| Modem → PPP | PC Card modem completes Hayes + live Slirp LCP/IPCP; Web Browser 4.0 installs | [`modem.md`](docs/modem.md) |
| Variants | `datarover840` / `840f` (writable flash) / `840j` / `840d` (1998-04-07 development ROM) all build and verify; `840d` also boots to the workbench | [`rom-layout.md`](docs/rom-layout.md), [`dev-rom.md`](docs/dev-rom.md) |
| OS self-tests | Three of the development ROM's own unit-test suites (date/time, cache, font) run against the emulated hardware and report no complaint, judged by the oracle the ROM names itself | [`dev-rom.md`](docs/dev-rom.md) |

Each subsystem has a headless regression under [`tools/`](tools/); the full list and expected checkpoints are in [`docs/mame-bringup.md`](docs/mame-bringup.md).

### Remaining work

- **Final combined acceptance**: load a plain-HTTP page in the archived Web
  Browser 4.0 over the live PPP link — *Magic Cap on the internet*.
- **Buffered SIB sound DMA** (the startup tone uses the unbuffered path).
- **Complete wake-path interaction.** In-session suspend/wake works, but a
  warm boot of a heap that was saved *while suspended* re-enters suspend at
  the end of boot, and a subsequent power-button wake is rejected. The SDK
  ELF's power/wake code has now been read, and it reframes the bug: the
  bank-1 masking is a deliberate software branch in
  `EnableInterruptsForShutdown` taken when the `shutdownReason` global reads
  `EMER`, which leaves the on-button bits out of the wake sources on purpose.
  The handshake boot actually checks is `interrupt5` bits 23/22 (latched
  on-button edges) then `powerControl` bit 31 (button held). Registers, bits,
  RAM globals, driver requirements, and debugger breakpoints are in
  [`power-wake.md`](docs/power-wake.md); the driver change and its
  verification are still open, so the machine stays `MACHINE_NOT_WORKING`.

### Open questions

- **Reach the development ROM's remaining test suites.** Three of its
  OS-level unit tests already run as acceptance checks (see below); the other
  no-argument suites do not return from a forced call, and the 28
  `*TestSuite_RunTest` entry points take arguments. Both probably need the
  build's `TestSite` scene, whose UI route is not mapped yet
  ([`dev-rom.md`](docs/dev-rom.md)).

## Resources

- **Toshiba TX39 core documentation.** The primary CPU reference is the *TX39 Family Core Architecture User's Manual* (Jul 1995, 246 pp) — it documents the R3900 core in emulation-grade depth: five-stage pipeline, complete instruction set with per-instruction detail, MMU direct segment mapping, CP0/exception processing, I/D caches with lock functions, and debug registers. The [Bitsavers PDF](https://www.bitsavers.org/components/toshiba/_dataSheet/TMPR39xx-um_199507.pdf) is pinned by checksum in [`docs/tx39-cpu.md`](docs/tx39-cpu.md); the same document is [scanned on archive.org](https://archive.org/details/manualzilla-id-7260633). Supplement with the [Bitsavers TMPR39xx family overview](http://www.bitsavers.org/components/toshiba/_dataSheet/TMPR39xx-family.pdf) and the TMPR3904/3912/3922 sibling manuals. None of these mention the TMPR3902 by name — the SoC-specific peripherals (Dino, Betty) remain undocumented anywhere public, so those come from ROM reverse engineering.
- **General Magic primary docs** on Bitsavers' [`/pdf/generalMagic/`](https://bitsavers.org/pdf/generalMagic/) — `Using_Magic_Cap.pdf`, the [*Telescript Language Reference*](https://bitsavers.org/pdf/generalMagic/Telescript_Language_Reference_Oct95.pdf) (Oct 1995, 263 pp; TDE 1.0 Alpha), and the Sony Magic Link Press Kit. Telescript is the agent language behind Magic Cap's messaging/comms stack — useful context for the parts of the ROM that aren't UI.
- **Community knowledge**: [Old VCR blog](http://oldvcr.blogspot.com/2022/12/magic-cap-from-magic-link-to-datarover.html), [Josh Carter's FAQs](https://joshcarter.com/magic_cap/) (incl. developer docs and the 840F flasher, useful for understanding ROM layout), [comp.os.magic-cap archives](https://groups.google.com/g/comp.os.magic-cap), [archive.org DataRover 840 software](https://archive.org/details/DataRover840).
- **Ghidra** — free RE suite with solid big-endian MIPS-I support for static analysis of the ROM.
- **Prior art & preservation.** The only documented prior preservation effort is [Cooper Hewitt / Small Data Industries (2019)](https://www.cooperhewitt.org/2019/05/13/a-predecessor-of-todays-smartphones/), who concluded there was no non-destructive way to dump the Motorola Envoy's TSOP-56 ROMs and fell back to running the Mac simulator under Basilisk II — application-level simulation, not device emulation. No Magic Link or Envoy (68K-generation) ROM dump is known to exist, so those machines can't be device-emulated today; the DataRover, with its freely downloadable 3.1.2j image, is the one Magic Cap machine that can.

### The Magic Cap Simulators

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

## Verification (no real hardware)

We don't own a DataRover 840, so correctness is judged by external signals only:

- **Screen appearance** vs. photos/screenshots of Magic Cap 3.x in the wild ([PDA Museum](https://pdamuseum.eu/pda/datarover840/), [Old VCR](http://oldvcr.blogspot.com/2022/12/magic-cap-from-magic-link-to-datarover.html), [Pen Computing review](http://www.pencomputing.com/magic_cap/data_rover_840.html)) and the Rosemary Simulator's UI as a behavioral reference.
- **The ROM's own voice**: the IDT boot monitor and Magic Cap debug builds talk over the serial port — an emulated UART console is our primary instrument for everything that happens before (and behind) the screen.
- **Internal consistency**: the ROM's own diagnostics (Betty register readback tests, memory sizing) passing is itself evidence the hardware model is right.
- **The OS's own unit tests**: the 1998-04-07 development ROM retains General Magic's test framework, and three of its suites (date/time, cache, font) now run inside the emulator and report no complaint, using the failure oracle the ROM documents for itself. These are the strongest signals available without hardware — tests written by the OS authors, judging the emulated machine. See [`dev-rom.md`](docs/dev-rom.md).
- **Simulator cross-checks**: the Rosemary simulator's object dumps describe the same structures our ROM stores, and its self-tests (if retained in the device ROM) are more ROM-provided diagnostics to drive — see [The Magic Cap Simulators](#the-magic-cap-simulators).

## Repo layout

```
docs/       RE notes: memory map, Betty registers, ROM layout, bring-up, PCLink, modem, TX39 CPU, power/wake, development ROMs
tools/      headless regression harnesses and analysis scripts (ROM info, ROM diff, serial, desk, sound, TX39, PC Card, PCLink, modem, development-ROM OS self-tests), fetch_assets.sh to mirror research inputs, start_manual.sh for interactive play
tests/      unit tests for the tools, with captured serial fixtures
roms/       optional git-ignored compatibility path; persistent assets live outside the repo in ~/fun/magic-cap-assets/
```

Driver development happens in the MAME fork at [ddanila/mame](https://github.com/ddanila/mame) (cloned as a sibling of this repo, `../mame`, work happens on the `custom` branch, never on `master`); this repo tracks notes, tools, and tests.

## License

Code and notes here: MIT. MAME driver code follows MAME's licensing. ROM images remain © General Magic and are not distributed here.
