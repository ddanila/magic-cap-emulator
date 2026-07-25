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
   completion marker and parks in a spin loop. The two number-format tests
   first resolve `FormatterTestSuite` from the live basic-system test list and
   enter through the ROM's own `FormatterTestSuite_RunTest` wrapper.

A suite passes when the marker appears — the function returned — and the
complaint counter is still zero.

```sh
cd "$HOME/fun/magic-cap-emulator"
python3 tools/devrom_tests.py                       # every suite known to pass
python3 tools/devrom_tests.py --suite font          # one suite
python3 tools/devrom_tests.py --self-check          # validate the oracle
python3 tools/devrom_tests.py --suite fmtinteger --suite scanfloat
python3 tools/devrom_tests.py --suite fmtinteger --trace-complaints
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

### Reproducibility: pin the clock

The driver resumes the RTC by adding the host wall-clock time that passed while
the machine was off, which is what a real communicator does. For a headless
check that means a different emulated time of day on every run, and that
changes results: a full pass over the suites on the host clock failed six of
the original twelve with "did not return", while three consecutive passes
with the clock pinned each took all twelve.

`datarover840d` therefore exposes a **RTC on resume** machine configuration
setting, and the harness selects `Freeze at saved value` by default
(`--rtc host` restores the realistic behavior). Measured directly, two runs on
the host clock read RTC `0x00307cba` and `0x0031fcba` — three seconds of drift
— while two frozen runs both read `0x0015fcba`.

### Results

Fourteen suites pass, judged by the ROM's own oracle:

| Suite | Symbol |
|---|---|
| `datetime` | `DateTimeUnitTests__Fv` |
| `cache` | `CacheUnitTests__Fv` |
| `font` | `FontUnitTests__Fv` |
| `rompristine` | `CheckROMPristineTable__Fv` |
| `endianswap` | `TestEndianSwapping__Fv` |
| `objectmap` | `TestObjectMap__Fv` |
| `cliquetable` | `TestCliqueTable__Fv` |
| `fastenedstack` | `TestFastenedStack__Fv` |
| `interchangetable` | `TestDynamicInterchangeTable__Fv` |
| `paths` | `PathsUnitTests__Fv` |
| `textmapping` | `TextMappingUnitTests__Fv` |
| `objectname` | `ObjectNameTests__Fv` |
| `fmtinteger` | `TestFormattingInteger__Fv` |
| `scanfloat` | `TestScanningFloatingPoint__Fv` |

These are OS-level self-tests written by the people who wrote this OS,
executing against the emulated hardware and finding nothing to complain about
— a much stronger signal than a framebuffer checksum. `CheckROMPristineTable`
is particularly direct: it is the OS verifying the ROM image it is running
from.

### Why the apparent 66 complaints were not emulator failures

Calling the two formatter bodies directly produced stable totals of 29 and 37
complaints. `--trace-complaints` identified every caller:

| Directly called body | Complaint site | Count | Check |
|---|---:|---:|---|
| `TestFormattingInteger` | `0x13e8c540` | 29 | `CheckExpectedText` text mismatch |
| `TestScanningFloatingPoint` | `0x13e8cd94` | 13 | scanner result/reference mismatch |
| `TestScanningFloatingPoint` | `0x13e8cdb8` | 24 | parsed/expected double mismatch |

The integer expectations make the missing context visible: they include
locale-specific forms such as `(1)`, `1.000`, `eins`, and values suffixed with
`DM`. The direct call was using the machine's ordinary number formatter
instead of the test fixture's formatter, so simple values happened to match
while locale-sensitive cases did not.

The ROM's real entry point proves the required setup. At `0x13e8d07c`,
`FormatterTestSuite_RunTest`:

1. saves the current system number formatter;
2. reads the `FormatterTestSuite.formatter` reference field at offset 8;
3. installs that formatter;
4. dispatches test 1 (`TestFormattingInteger`) or test 6
   (`TestScanningFloatingPoint`);
5. restores the saved formatter.

The `.dx` database identifies `FormatterTestSuite` as class `0x05f9`.
At runtime it is item nine of `System_iBasicSystemTestList`; the harness
resolves that item dynamically rather than pinning a heap address. Through
this wrapper, the same 34 integer text comparisons and 28 floating-point scan
cases return with **zero complaints**. The 66 reports therefore came entirely
from bypassing the ROM's fixture setup; they are neither an FPU gap nor a
hardware-emulation defect.

One debugger trap is worth retaining: a breakpoint action chaining two `do`
commands silently stops the machine instead of continuing, which looks exactly
like a hung test. The operand trace uses `logerror` plus one counter update and
routes it through MAME's `-oslog` output.

Fourteen more do not return from a forced call (`announcement`, `contact`,
`datebook`, `fmtfixed`, `fmtfloat`, `numeraldouble`, `padprecision`,
`lossofaccuracy`, `scaninteger`, `scanfixed`, `scantime`, `buggbm15189`,
`bugrwt12821`, `textstyle`). The PC keeps moving through OS code, so they wait
on task or scene context rather than crashing — the same limitation that stops
Command-T being driven this way.

For the ones that do not return, the PC keeps moving through OS code rather
than sitting in a tight loop, so they are waiting on something — task or scene
context — rather than crashing. `announcement`'s dependence on session state
points the same way. The suites themselves are compiled into the ROM — the Mac
SDK ships no test packages — so no extra input is needed, only the right
context. That context is the test machine.

## Running whole suites: `RunTests`

Calling a no-argument test body directly only works for tests that need no
setup, and it cannot reach the 28 `*TestSuite_*` classes at all. There is a
better entry point, and it is the one the test machine itself uses:

```c
suite = ReadReferenceField(System_iBasicSystemTestList, offset);
RunTests(System_iTestMachine, suite, index);   /* 0x13e97c90 */
```

Three things make this the right lever:

- **Index 0 runs the entire suite.** Driving the formatter suite with index 0
  entered `FormatterTestSuite_RunTest` seven times and ran every test body
  behind it; index 1 ran only `TestFormattingInteger`, index 2 only
  `TestFormattingFixed`.
- **It returns.** `TestMachine_RunOneTest` (`0x13e98188`) wraps the same
  `RunTests` call with `TestsComplete`, and that never comes back to an
  injected frame — a forced call parks inside it. `RunTests` on its own
  completes in about sixty frames.
- **Going through the suite installs the fixture.** The formatter tests report
  failures when their bodies are called naked and none when run this way, which
  is the same effect that made those 66 complaints disappear.

Suite objects live at offsets `0x04` upward in the list object; `0x24` is the
formatter suite. One caveat found the hard way: on a freshly calibrated boot
the test machine and its list are not resolvable at frame 900 — the read
returns nothing — so the call has to happen later. 2400 works and is the
default.

```sh
python3 tools/devrom_suites.py                 # every suite slot
python3 tools/devrom_suites.py --offset 0x24   # one suite
```

Each suite runs in its own boot from a copy of one calibrated NVRAM, with the
RTC pinned, and is judged by the same oracle as the individual tests: the suite
must return without entering `AnnounceNonDebugFailure`.

Sweeping every slot gives **13 of 16 suites running clean with no complaint**,
and each suite is several tests — the formatter suite alone runs seven:

| Result | Slots |
|---|---|
| Ran clean | `0x08`, `0x0c`, `0x14`, `0x18`, `0x1c`, `0x20`, `0x24`, `0x28`, `0x30`, `0x34`, `0x38`, `0x3c`, `0x40` |
| Did not return | `0x04`, `0x10`, `0x2c` |

No suite reported a single complaint, which is the meaningful part: thirteen
suites of the OS authors' own tests exercise the emulated machine and find
nothing wrong with it. The three that do not return behave like the individual
tests that wait on task or scene context. The scheduler-driven Command-T run
below resolves that limitation and completes all 16.

## The test machine and Command-T

`TestSite` is not reachable as a scene by the obvious UI routes (taps on the
Downtown directory sign and the street pan arrow do nothing), but the
interesting entry point is not the scene — it is the **test machine**, and
General Magic drove it with a single command they called **Command-T**. The
dev-only strings name it: *"Command-T finished successfully."*, *"reboot at the
end of command-T"*, and even the address results went to,
`commandt@kelp.genmagic.com`.

The relevant entry points, all in the Apollo USA development build:

| Address | Symbol |
|---|---|
| `0x13e983d4` | `TestMachine_RunAllTests` |
| `0x13e98288` | `TestMachine_RunTestSuites` |
| `0x13e981f8` | `TestMachine_RunTestSuite` |
| `0x13e98188` | `TestMachine_RunOneTest` |
| `0x13e9837c` | `TestMachine_CommandTea` |
| `0x13e98508` | `TestMachine_FillErUp` |
| `0x13e978fc` | `TestMachine_InstallInto` |
| `0x13e93ee0` | `RebootAnnouncingCommandTFinished__Fv` |

These are dispatched methods (no direct `jal` callers), so they take the object
in `$a0` the way the dispatcher passes it. The objects are live: after a warm
boot, the indexical reference slots hold real references, so nothing needs
installing first.

| Indexical | RAM slot | Value observed |
|---|---|---|
| `System_iTestMachine` | `0x0002d4b4` | `0x00032a94` |
| `System_iBasicSystemTestList` | `0x00029714` | `0x00032aa4` |
| `TestSite_iTestCardList` | `0x000340fc` | `0x00032c3c` |
| `TestSite_iLimitTestSuite` | `0x000340a4` | `0x00032bdc` |
| `TestSite_iUnitTestTextSuite` | `0x000340ec` | `0x000331cc` |

The tempting forced-call route was misleading. `TestMachine_RunAllTests`
returned almost immediately because its mutable tests-to-run list was empty,
while `TestMachine_RunTestSuite` parked as soon as a test yielded. The
canonical Command-T method supplies the missing intent directly:
`TestMachine_CommandTea` dispatches
`TestMachine_RunTestSuites(testMachine, System_iBasicSystemTestList)`.

### Native scheduler harness

`tools/devrom_command_t.py` runs that method in its real task context:

```sh
cd "$HOME/fun/magic-cap-emulator"
python3 tools/devrom_command_t.py
```

By default the harness calibrates a fresh `datarover840d`, copies its NVRAM
into an isolated run directory under
`~/fun/magic-cap-assets/runtime/devrom-command-t/`, then starts Command-T.
`--nvram-source DIR` can reuse an already calibrated NVRAM without modifying
the source. ROMs, NVRAM, Lua scripts, screenshots, and logs remain outside the
Git checkout.

This is deliberately not another forced call:

1. The harness waits until the CPU is in the ordinary `Doze`/`DeepDoze` entry
   path rather than sampling a transient interrupt or DRAM-refresh context.
2. A short injected stub calls
   `Semaphore_RunSoon(false, bootstrap, System_iTestMachine)`, jumps back to
   the sampled idle PC so no MIPS branch-delay target remains pending, then
   Lua restores registers 1–31, HI, LO, SR, and PC exactly.
3. The real system run queue invokes `bootstrap` at a dispatcher boundary.
   It calls `Semaphore_RunSoon(true, callback, testMachine)`, attaching the
   final completion to the current user actor.
4. The user queue invokes `callback`, which calls
   `TestMachine_CommandTea` and returns normally through both scheduler
   frames.

`CompletionFunction` is a MIPS transition-vector pointer, not a raw code
address: word zero is the entry PC and word one is `$gp`. Both descriptors and
their code live at user-accessible low-DRAM addresses; the initial injection
alone uses the uncached kseg1 alias. `FlushInstructionCache` makes those
instructions visible before the system queue receives the descriptor.

The acceptance oracles are ROM entry points, not timing guesses. The run must
enter `TestMachine_RunTestSuites` once, enter `RunTests` 16 times, reach
`TestsComplete` once, return from `TestMachine_CommandTea`, and never enter
`AnnounceNonDebugFailure`. A verified result is:

```text
queued=1 restored=1 bootstrap=1 user_queued=1 entered=1 returned=1
run_suites=1 run_tests=16 complete=1 complaints=0 reboot=0
```

This run also exposed a hardware-model bug that the forced suites could not:
BasicTestSuite test 17, `play moving sound`, completed its first five sounds
and then waited forever for `Speaker_Busy` to clear. Dino sound DMA had
stopped after its first buffer even though the ROM had not requested
`kSibSoundDmaOnceMask`. The normal mode is a continuously serviced two-half
ring; only explicit one-shot mode stops at the end. With that corrected,
Command-T serviced 107 half and 106 full sound interrupts, left no active
sounds, completed all 16 suites, and reported no complaint.

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

The candidate list for `devrom_tests.py` comes from the no-argument entry
points — 84 of them. That search found the first twelve passing bodies; the two
formatter bodies only became valid checks after their suite wrapper was
reproduced:

```sh
llvm-readelf --symbols "$dbg/Apollo/MagicCap-USA" \
  | awk '$4=="FUNC" && $2!="00000000" {print $2, $8}' \
  | grep -E "__Fv$" | grep -iE "test|verify|check|validate" \
  | grep -viE "TestName|CanRun" | sort -k2
```

Not everything it lists is a test: the same pattern catches setup and teardown
helpers (`BeginUsingTestProvider`, `CreateTestPackage`, `DestroyTestPackages`,
`RetractTestAnnouncements`) and things better left alone in an automated run
(`FaxTest`, `CheckPowerButton`, `ResetCardCheckState`). Read the name before
adding it.

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
