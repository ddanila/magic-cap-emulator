# The 1998-04-07 development ROMs

The Mac Rosemary SDK ships **four** DataRover-family ROM images dated
1998-04-07, each with its own unstripped MIPS ELF and debugger database. They
are development builds of the same OS as the shipping 3.1.2j image, and the USA
Apollo build carries a complete on-device test framework that the release ROM
does not. This note records what they are and what they are good for; the
comparison is reproducible with `tools/rom_diff.py`.

No image, ELF, or string dump is committed. Everything lives under
`~/fun/magic-cap-assets/sdk-mac/`, fetched by `tools/fetch_assets.sh macsdk`.

## What the SDK contains

`MagicDeveloper/MagicDeveloper/Debugger/` holds two platform directories, and
each holds a USA and a Japan build:

| Path | Size | sha256 (prefix) |
|---|---:|---|
| `Apollo/MagicCap-USA.image` | 4,885,207 | `fee43c25` |
| `Apollo/MagicCap-Japan.image` | 6,448,856 | `ed5e5f03` |
| `Sputnik/MagicCap-USA.image` | 4,864,567 | `1a7f5eb7` |
| `Sputnik/MagicCap-Japan.image` | 6,428,120 | `e940fcae` |

Alongside each image: an unstripped ELF (`MagicCap-USA`, 10,486,288 bytes for
Apollo USA) and a `.dx` debugger database — the Mac SDK's equivalent of the
Windows SDK's `.debug.x`.

**Apollo is the DataRover platform**; the emulated machine's IDT monitor
reports `Platform: Apollo`. `Sputnik` is the sibling platform the same monitor
knows about (it probes `BigBoard2` / `Sputnik2` and a Sony vs Toshiba core).
The two dev images are genuinely different builds — 7,671 shared strings but
~575 unique to each side, and Sputnik is 20,640 bytes smaller — so the Sputnik
pair is a reference for what is board-specific, not a duplicate.

## Development versus release, USA Apollo

```text
release  4,528,151 bytes  94785cb3…   8,024 printable runs
dev      4,885,207 bytes  fee43c25…  10,083 printable runs   (+357,056 bytes)
```

Both start with the same reset word `0x08f00007` and the `IDT MONITOR ` marker,
and both ELFs use entry point `0x13c1d120` (`BootCap`) with the ROM segment
based at `0x13c00000`. The layouts agree; the dev build is the same ROM plus
extra content:

| Segment | Release | Development |
|---|---|---|
| RAM (globals) | `0x00000000` + `0x0000dfe0` | `0x00000000` + `0x0000e020` |
| ROM | `0x13c00000`, `0x44dde8` | `0x13c00000`, `0x4a4e50` |
| Addressed `FUNC` symbols | 14,587 | 15,281 |

**RAM globals shift.** The extra code pushes the globals segment up by `0x40`,
so addresses recovered from the release build do not transfer. For example
`shutdownReason` is `0x0000e880` in the release build and `0x0000e8c0` in the
dev build; `wakeInterrupt1mirror` moves `0x0000e8b0` → `0x0000e8f0` (see
[`power-wake.md`](power-wake.md)). Always re-resolve globals against the ELF
matching the image you are running.

## What the development build adds

719 addressed functions exist only in the dev build (36 only in the release).
By theme:

| Theme | Dev-only symbols |
|---|---:|
| Test / suite | 241 / 71 |
| Debug-name and formatting helpers | 39 |
| Heap inspection and stress | 12 |
| Power | 8 |
| Log, journal, dump | 9 |

Concretely, the dev build contains **28 `*TestSuite_RunTest` entry points** and
**11 `*UnitTests__Fv` functions**, covering framework, cluster, contacts,
datebook, date/time, dialing, DNR, flattener, font, formatter, frozen objects,
fonts, packages, and more. It also carries:

- `DebugNamesLibrarian_*` — class-name and literal-formatting services, the
  machinery behind the simulator's object dumps, present here on-device.
- `InputQueueMonitor_BeginRecordJournal` / `EndRecordJournal` /
  `ToggleRecordJournal` — the action journaling the simulator documentation
  describes, in a device ROM.
- `MemoryWindow_*` (`ScrambleHeap`, `WringHeap`, `SetCurrentHeapNumber`) and
  `HeapStatisticsCache_*` — a live heap inspector and deliberate heap-stress
  tools.
- `CapacityTester_Create{Appointments,NameCards,NoteCards,Telecards,Stuff}` —
  bulk object creation for capacity testing.

The accompanying UI text confirms these are user-drivable: the dev-only strings
include *"This package displays tests and allows you to run them. Each item in
the list below is a test suite…"*, *"About Test Suites"*, *"About TestSite"*,
*"No Test Suites"*, *"Testing cancelled at your request."*, *"Aborting
communication test since no phone line plugged in."*, and
*"Timing Tests"*.

This settles a question the [README](../README.md#the-magic-cap-simulators)
raised from the simulator documentation — whether a device ROM retains the
hidden Testing Scene and self-tests. The **release** ROM does not; this
development ROM does.

One string names the boundary precisely: *"A TestSite assertion or complaint
was triggered in this non-debug build. To track this down, put a breakpoint
[…]"*. So even this image is a *non-debug* build in the SDK's sense —
`Assert` / `Whisper` / `DebugMessage` bodies are still compiled out, exactly as
the SDK documents for communicators. What the dev ROM adds is the test and
inspection *framework*, not assertion text.

## Why this matters for the emulator

The project judges correctness by the ROM's own voice — the `BettyTest`
diagnostic is already an acceptance checkpoint. The dev ROM extends that
approach substantially:

- 28 suites' worth of ROM-provided, self-checking tests, callable the same way
  `BettyTest` is (`call` from the IDT monitor, or a debugger breakpoint plus a
  forced call), each validating OS-level behavior against the hardware model.
- A live heap inspector to cross-check the persistence model, and heap-stress
  entry points that exercise the scavenger against battery-backed DRAM.
- Journal record/replay, which is the natural fit for deterministic
  touch-input regressions.

## Running it: the `datarover840d` set

The driver exposes the Apollo development image as `datarover840d`, a clone of
`datarover840` using the same machine configuration — the image needed no
hardware change, as its layout predicted:

```sh
cd "$HOME/fun/magic-cap-emulator"
tools/fetch_assets.sh macsdk        # also places the image in the rompath

cd "$HOME/fun/mame"
./datarover -rompath "$HOME/fun/magic-cap-assets/roms" -verifyroms datarover840d
./datarover datarover840d -rompath "$HOME/fun/magic-cap-assets/roms" \
  -window -skip_gameinfo -view LCD -lightgun -mouse -lightgun_device lightgun
```

Both headless harnesses take `--system`, so the existing checkpoints run
against it unchanged:

```sh
python3 tools/serial_regression.py --system datarover840d \
  --workdir "$HOME/fun/magic-cap-assets/runtime/serial-regression-dev"
python3 tools/desk_regression.py --system datarover840d \
  --workdir "$HOME/fun/magic-cap-assets/runtime/desk-regression-dev"
```

Verified results on this image:

| Checkpoint | Result |
|---|---|
| IDT monitor banner | Byte-identical to the release capture — the monitor portion is the same build |
| Boot to workbench | Reaches the calibrated desk; the stable lower-workbench signature is the same `0x9dab458b` as the release |

The development build also opens a version card on boot that the release does
not: it reads `Rosemary (release), 04/…`, which is a convenient on-screen
build identifier when checking which image a session is running.

The scene inventory differs too. The dev-only strings include
`$Reset Hallway, TestSite And Downtown` and *"To pan the Hallway, TestSite and
Downtown to the left"*, so **`TestSite` is a scene in this build**, alongside
the Hallway and Downtown that the release ships. `About TestSite`,
`No Test Suites`, and `No Tests` are also present, so the scene has an empty
state.

The ROM states its own failure oracle, which is the natural acceptance hook:

> *"A TestSite assertion or complaint was triggered in this non-debug build. To
> track this down, put a breakpoint on `AnnounceNonDebugFailure` and re-run the
> test."*

`AnnounceNonDebugFailure__Fv` is at `0x13e97834` in this build. Driving a suite
and asserting that this address is never reached is the same shape as the
existing `BettyTest` checkpoint. Candidate no-argument entry points:

| Address | Symbol |
|---|---|
| `0x13e9c1ec` | `DatebookTaskUnitTests__Fv` |
| `0x13e9c824` | `CacheUnitTests__Fv` |
| `0x13e9cca0` | `ContactUnitTests__Fv` |
| `0x13e9d488` | `FontUnitTests__Fv` |
| `0x13e9d5b8` | `AnnouncementUnitTests__Fv` |
| `0x13e9dbfc` | `DateTimeUnitTests__Fv` |

## Driving the suites: `tools/devrom_tests.py`

`tools/devrom_tests.py` runs these entry points and applies the ROM's own
verdict. It works in two phases, which matters:

1. **Calibrate.** Boot a fresh machine, tap the welcome scene and the three
   calibration targets, exit. This leaves a calibrated NVRAM directory.
2. **Call.** For each suite, boot warm from a *copy* of that NVRAM, set a
   counting breakpoint on `AnnounceNonDebugFailure`, write a small stub into
   scratch DRAM, and point the PC at it. The stub calls the suite through
   `jalr` (a `jal` cannot reach `0x13e9xxxx` from low DRAM), then writes a
   completion marker and parks in a spin loop.

A suite passes when the marker appears — the function returned — and the
complaint counter is still zero.

```sh
cd "$HOME/fun/magic-cap-emulator"
python3 tools/devrom_tests.py                       # the suites that return
python3 tools/devrom_tests.py --suite font          # one suite
python3 tools/devrom_tests.py --self-check          # validate the oracle
```

Forcing the call during a *first* boot does not work: the tests never return
while first-run initialization is still settling, which is why the calibration
phase exists. `--nvram-source` reuses an existing calibrated directory instead.

### Trusting a pass

A zero complaint count only means something if the counter can fire at all, so
`--self-check` points it at the suite function itself, which the stub calls
exactly once, and requires a count of exactly one. That control passes, so a
zero count in a normal run is a real negative rather than a detector that
silently did nothing.

### Results

| Suite | Symbol | Result |
|---|---|---|
| `datetime` | `DateTimeUnitTests__Fv` | **passes** — returns, no complaint |
| `cache` | `CacheUnitTests__Fv` | **passes** |
| `font` | `FontUnitTests__Fv` | **passes** |
| `announcement` | `AnnouncementUnitTests__Fv` | does not return from a freshly calibrated machine; does return from a longer-lived session's NVRAM |
| `contact` | `ContactUnitTests__Fv` | does not return (checked to 9,000 frames) |
| `datebook` | `DatebookTaskUnitTests__Fv` | does not return |

The three that pass are three OS-level self-tests, written by the people who
wrote this OS, executing against the emulated hardware and finding nothing to
complain about. That is a much stronger signal than a framebuffer checksum.

For the ones that do not return, the PC keeps moving through OS code rather
than sitting in a tight loop, so they are waiting on something — task or scene
context — rather than crashing. `announcement`'s dependence on session state
points the same way. Reaching those probably needs the real `TestSite` scene
rather than a forced call: the UI route there is still unmapped, since taps on
the Downtown directory sign and street arrow did not respond in a first probe.
The suites themselves are compiled into the ROM — the Mac SDK ships no test
packages — so no extra input is needed, only the right context.

## Reproducing the comparison

```sh
cd "$HOME/fun/magic-cap-emulator"
tools/fetch_assets.sh macsdk

dbg="$HOME/fun/magic-cap-assets/sdk-mac/extracted/MagicDeveloper/MagicDeveloper/Debugger"
python3 tools/rom_diff.py \
  "$HOME/fun/magic-cap-assets/roms/datarover840/magiccap-usa.image" \
  "$dbg/Apollo/MagicCap-USA.image"

# Apollo against Sputnik, same date and OS version:
python3 tools/rom_diff.py "$dbg/Apollo/MagicCap-USA.image" \
  "$dbg/Sputnik/MagicCap-USA.image"
```

`rom_diff.py` reports size, checksum, reset word, IDT marker, version and date
stamps, byte-level spans when the images are the same length, and the string
set comparison. `--dump-strings DIR` writes the full string sets for offline
reading; point it **outside** the checkout (the ROM's strings are copyrighted
General Magic content — the repo keeps derived findings, not bulk dumps):

```sh
python3 tools/rom_diff.py "$HOME/fun/magic-cap-assets/roms/datarover840/magiccap-usa.image" \
  "$dbg/Apollo/MagicCap-USA.image" \
  --dump-strings "$HOME/fun/magic-cap-assets/analysis/rom-diff-usa"
```

Symbol-set comparison uses the two ELFs (Homebrew LLVM, or
`binutils-mips-linux-gnu` on Debian):

```sh
llvm-readelf --symbols "$dbg/Apollo/MagicCap-USA" \
  | awk '$4=="FUNC" && $2!="00000000" {print $8}' | sort -u > /tmp/dev.syms
llvm-readelf --symbols \
  "$HOME/fun/magic-cap-assets/sdk/extracted/Program_Files/debug/apollo/MagicCAP-USA" \
  | awk '$4=="FUNC" && $2!="00000000" {print $8}' | sort -u > /tmp/rel.syms
comm -23 /tmp/dev.syms /tmp/rel.syms | grep -iE "test|suite" | head
```

## Acquisition

The SDK is a single 74,208,013-byte StuffIt 5 archive, `magicdeveloper.sit`,
from the [Macintosh Garden Magic Developer
page](https://macintoshgarden.org/apps/magic-developer) (MD5
`0e3385d40ba3c9c069b1af99a430fe7a`, as published there; sha256
`1ea81ba35c2ade992bac2b0348cbc4f7443f3f006a1480d66c04848c8de89e76`). Extraction
needs `unar` (`brew install unar`); `7z` and `bsdtar` do not handle StuffIt 5.
`tools/fetch_assets.sh macsdk` does the download, extraction, and checksum
verification.

Note that this is the same SDK whose simulator the
[README](../README.md#the-magic-cap-simulators) describes as the
version-matched behavioral reference, so the archive is also the route to the
Rosemary Simulator, its documentation, and the Apollo/Sputnik interface
headers.
