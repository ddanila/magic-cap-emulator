# Project background

Context for the DataRover 840 emulator: the hardware, the ROM, why it is a
MAME driver, what existed before this project, and how correctness is judged
without owning the physical machine.

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

Sources: [PDA Museum](https://pdamuseum.eu/pda/datarover840/),
[Old VCR teardown & history](http://oldvcr.blogspot.com/2022/12/magic-cap-from-magic-link-to-datarover.html)
(board photos, chip identification),
[Josh Carter's Magic Cap archive](https://joshcarter.com/magic_cap/).

## The ROM

The USA ROM image (build 3.1.2j) comes from the
[Rosemary Software Archive](https://joshcarter.com/magic_cap/packages/)
(`MagicCap-USA.zip` → `MagicCap-USA.image`, 4,528,151 bytes, dated 2000-05-04),
sha256 `94785cb334f14eac00ed200af014c35972b4f25694103bc6a49b3afa280a6f1b`.
It is **not committed to this repo** — it's copyrighted General Magic software
(abandonware, but still). `tools/fetch_assets.sh` mirrors and
checksum-verifies every research input; exact per-file provenance is in
[`rom-layout.md`](rom-layout.md).

Key facts established by analysis:

- **Big-endian MIPS-I.** The image begins with valid BE code and boots the
  **IDT boot monitor** (© 1992 Integrated Device Technology, build dated
  Dec 5, 1997) before Magic Cap proper. The monitor knows two platforms
  (`BigBoard2`, `Sputnik2`) and probes for a "Sony Core" vs "Toshiba Core"
  CPU — matching the Magic Link → DataRover hardware lineage.
- **Image format.** The `.image` is raw, linear ROM content based at physical
  `0x13C0_0000`. See [`rom-layout.md`](rom-layout.md).
- **Memory map.** The `0xB0C0_xxxx` block (kseg1 → physical `0x10C0_0000`) is
  the TX39 **Dino** peripheral module. The external **Betty** ASIC exposes
  16-bit registers over Dino's SIB; it is not directly memory mapped. See
  [`memory-map.md`](memory-map.md) and [`betty-registers.md`](betty-registers.md).
- **Symbols.** The [archive.org DataRover840](https://archive.org/details/DataRover840)
  bundle contains the complete Icras SDK 3.2, including an **unstripped
  Apollo MIPS ELF** whose symbols (`BootCap`, display, Dino/Glacier,
  SIB/Betty, touch, serial, sound, modem) are the backbone of ROM annotation.

## Approach

Build it as a **MAME driver** (in a fork, upstreamable later) rather than a
standalone emulator. MAME supplies the mature
[`mips1` CPU core](https://github.com/mamedev/mame/tree/master/src/devices/cpu/mips)
(the R3900 is R3000A-compatible; the fork adds the TX39 `MADD`/`MADDU`
extension), plus LCD rendering, touch/pointer input, PCMCIA slots,
serial/modem and RTC devices, save states, and a debugger with MIPS
disassembly — most of an emulator's boring 80% — and it is fully OSS
(BSD-3/GPL-2). A minimal standalone C/Rust harness was the fallback plan but
was never needed.

Bring-up followed scoped `SUBTARGET` builds and MAME's unmapped-access
logging; the reproducible build, launch, and regression commands are in
[`mame-bringup.md`](mame-bringup.md).

## Prior art

Before this project (survey, July 2026) no emulator for any Magic Cap device
existed — nothing in MAME, on GitHub, or in QEMU. The only way people ran
Magic Cap was the Mac-hosted *Magic Cap Simulator*, a native recompile of the
OS rather than a hardware emulator ([details below](#the-magic-cap-simulators)).

The only documented prior preservation effort is
[Cooper Hewitt / Small Data Industries (2019)](https://www.cooperhewitt.org/2019/05/13/a-predecessor-of-todays-smartphones/),
who concluded there was no non-destructive way to dump the Motorola Envoy's
TSOP-56 ROMs and fell back to running the Mac simulator under Basilisk II —
application-level simulation, not device emulation. No Magic Link or Envoy
(68K-generation) ROM dump is known to exist, so those machines can't be
device-emulated today; the DataRover, with its freely downloadable 3.1.2j
image, is the one Magic Cap machine that can.

## Resources

- **Toshiba TX39 core documentation.** The primary CPU reference is the *TX39 Family Core Architecture User's Manual* (Jul 1995, 246 pp) — it documents the R3900 core in emulation-grade depth: five-stage pipeline, complete instruction set with per-instruction detail, MMU direct segment mapping, CP0/exception processing, I/D caches with lock functions, and debug registers. The [Bitsavers PDF](https://www.bitsavers.org/components/toshiba/_dataSheet/TMPR39xx-um_199507.pdf) is pinned by checksum in [`tx39-cpu.md`](tx39-cpu.md); the same document is [scanned on archive.org](https://archive.org/details/manualzilla-id-7260633). Supplement with the [Bitsavers TMPR39xx family overview](http://www.bitsavers.org/components/toshiba/_dataSheet/TMPR39xx-family.pdf) and the TMPR3904/3912/3922 sibling manuals. None of these mention the TMPR3902 by name — the SoC-specific peripherals (Dino, Betty) remain undocumented anywhere public, so those come from ROM reverse engineering.
- **The DataRover 840 product specification.** Icras's 234-page
  [*Using Magic Cap*](https://bitsavers.trailing-edge.com/pdf/generalMagic/Using_Magic_Cap.pdf)
  guide is specifically for Magic Cap 3.1 on this machine. Its checksum,
  reproducible mirror and workflow-by-workflow emulator coverage are recorded
  in [`user-guide.md`](user-guide.md). The same Bitsavers collection also
  holds the [*Telescript Language Reference*](https://bitsavers.org/pdf/generalMagic/Telescript_Language_Reference_Oct95.pdf)
  and Sony Magic Link material; those are broader historical/context sources,
  not substitutes for the DataRover guide.
- **Community field evidence.** Cameron Kaiser's
  [DataRover teardown and history](https://oldvcr.blogspot.com/2022/12/magic-cap-from-magic-link-to-datarover.html)
  supplies board photographs and chip identification. His
  [TLS/browser field report](https://oldvcr.blogspot.com/2023/01/bringing-tls-to-magic-cap-datarover.html)
  documents Rosemary development plus real-device PCLink, EtherLink III and
  memory-pressure behavior; its preserved snapshot, optional package
  checksums and emulator acceptance map are in
  [`oldvcr-tls.md`](oldvcr-tls.md). Also useful are
  [Josh Carter's FAQs](https://joshcarter.com/magic_cap/),
  [comp.os.magic-cap archives](https://groups.google.com/g/comp.os.magic-cap),
  and [archive.org DataRover 840 software](https://archive.org/details/DataRover840).
- **Public developer archives.** Josh Carter's Magic Cap archive and the
  resurrected datarover.com publish the complete Magic Internet Kit source,
  SDK manuals, FAQs, samples, packages and earlier-platform equates. Their
  public URLs, checksums, adopted findings and strict Apollo-versus-Astro
  boundaries are in [`developer-archives.md`](developer-archives.md).
- **Ghidra** — free RE suite with solid big-endian MIPS-I support for static analysis of the ROM.

## The Magic Cap Simulators

Two distinct simulators exist, and the difference matters:

- **Magic Cap Simulator 1.0** ([Macintosh Repository](https://www.macintoshrepository.org/1316-magic-cap-simulator-1-0)) — Magic Cap **1.x**, the Sony Magic Link / Motorola Envoy era. 68K, runs under Basilisk II ([Adafruit guide](https://learn.adafruit.com/magic-cap-the-smartphone-os-from-the-90s/hardware-and-legacy)). Its UI and internals diverge noticeably from 3.1, but its simulated GMCD cards are now the authentic input for the DataRover's `Translation.pkg` compatibility path; see [`developer-archives.md`](developer-archives.md#storage-cards-an-exact-os-visible-contract).
- **The Rosemary SDK simulator** — Magic Cap **3.x**, part of the actual DataRover development environment (PowerPC, Mac OS 7.5.5+, CodeWarrior Pro era; needs SheepShaver/QEMU rather than Basilisk II). The SDK is on Macintosh Garden per [Old VCR's TLS post](http://oldvcr.blogspot.com/2023/01/bringing-tls-to-magic-cap-datarover.html); the standalone `RosemarySimulatorMac.sit` is also on Cameron Kaiser's [Floodgap Gopher staging archive](gopher://gopher.floodgap.com/9/archive/magic-cap-3/), and the SDK tools documentation is on the resurrected [datarover.com](http://www.datarover.com/Develop/MagicCap/Docs/Tools/CWMagic/Simulator.html) (self-signed cert). **This is the reference that matches our target OS version.**
- **Windows-hosted builds** existed too but are less useful: a Magic Cap 1.x *Pre-release 1.0, build 327* survives on [archive.org](https://archive.org/details/magic-cap) (runs under DOSBox + Win 3.1), and a "Build 2001" simulator (~Magic Cap 3.1) circulated on BetaArchive in 2014 but its download is dead — a lost-media recovery target if the 3.x Windows sim ever matters.

A simulator is a native Mac recompile of the same portable Magic Cap source tree our MIPS ROM was built from — not a hardware emulator. It says nothing about the TX39, Betty, or timing, but it is a **debug build with introspection tools** the device ROM lacks, which makes it useful well beyond "what should the screen look like":

- **Runtime object-model ground truth.** The simulator's Inspector and `Dump Package` / `Dump Inspector Target Deep` commands write full text descriptions (ObjectMaker syntax) of any live object — object IDs, fields, flags, class names. The same object structures live in our ROM's persistent store; dumps from the simulator are a labeled map for interpreting them. SDK headers (`Indexicals.h`, class definition files) give the complete class hierarchy and indexical numbering. This complements the unstripped Apollo ELF: the ELF names the code, the simulator dumps describe the *data*.
- **A specification of the hardware abstraction boundary.** The simulator's Hardware menu is effectively the list of what the portable OS expects from the platform layer: power on/off, warm reset, two card slots, phone-line connect/incoming-call events, hardware keyboard attach, memory sizing. Anything *not* simulated there is device-specific — a useful razor for deciding whether a Betty behavior is OS-visible or board plumbing.
- **Acceptance-test material.** The debug runtime carries a hidden Testing Scene, an "Execute Standard System" self-test, and action journaling/replay. The release device ROM retains none of these, but the 1998-04-07 development ROM does — see [`dev-rom.md`](dev-rom.md).
- **An end-to-end package loop.** The SDK builds packages; the simulator runs
  them natively; PCLink installs them onto the emulated DataRover. Building a
  trivial package and comparing its behavior side by side closes the loop from
  source to emulated device. The Floodgap archive also hosts Kaiser's
  MIPS-native proxy-capable `WebBrowser-MIPS-USA.pkg`, built with the Rosemary
  GCC 2.7.1 toolchain. Its checksum-guarded HTTPS dispatch correction is
  documented in [`oldvcr-tls.md`](oldvcr-tls.md).
- **Debug-build details**: `Assert` / `Whisper` / `Log` / `DebugMessage` macros are compiled in only in the simulator ("ignored on communicators"), and "Simulate Device Contrast" confirms the 16-gray LCD rendering expectations.

## Verification (no real hardware)

We don't own a DataRover 840, so correctness is judged by external signals only:

- **Screen appearance** vs. photos/screenshots of Magic Cap 3.x in the wild ([PDA Museum](https://pdamuseum.eu/pda/datarover840/), [Old VCR](http://oldvcr.blogspot.com/2022/12/magic-cap-from-magic-link-to-datarover.html), [Pen Computing review](http://www.pencomputing.com/magic_cap/data_rover_840.html)) and the Rosemary Simulator's UI as a behavioral reference.
- **The ROM's own voice**: the IDT boot monitor and Magic Cap debug builds talk over the serial port — an emulated UART console is our primary instrument for everything that happens before (and behind) the screen.
- **Internal consistency**: the ROM's own diagnostics (Betty register readback tests, memory sizing) passing is itself evidence the hardware model is right.
- **The OS's own tests**: the 1998-04-07 development ROM retains General Magic's test framework. Its real Command-T entry runs through the native scheduler and completes all 16 basic suites without entering the ROM's failure oracle. These are the strongest signals available without hardware — tests written by the OS authors, judging the emulated machine. See [`dev-rom.md`](dev-rom.md).
- **Simulator cross-checks**: the Rosemary simulator's object dumps describe the same structures our ROM stores — see [The Magic Cap Simulators](#the-magic-cap-simulators).
