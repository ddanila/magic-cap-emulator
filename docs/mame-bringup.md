# MAME bring-up and verification

The DataRover driver lives on the `custom` branch of the
[`ddanila/mame`](https://github.com/ddanila/mame) fork. Keep the fork as a
sibling of this repository and keep all ROMs, SDK files, captures, and logs in
the persistent `~/fun/magic-cap-assets/` tree. No copyrighted or generated
binary is committed to either repository.

## Host prerequisites

The build and every regression except the live Slirp PPP bridge run on both
Linux and macOS. The bridge's dependencies (classic `slirp` and `bubblewrap`,
see [`modem.md`](modem.md)) are Linux-only; `tools/modem_bridge.py --probe`
still works everywhere.

### macOS

Install the Xcode Command Line Tools, then:

```sh
brew install sdl2 sdl2_ttf coreutils
```

`coreutils` supplies the `sha256sum` and `nproc` used by these docs
(equivalently, use `shasum -a 256` and `sysctl -n hw.ncpu`). Current macOS
also ships its own `/sbin/sha256sum`, which has no `--check` option and can
shadow the Homebrew one; where a doc pipes into `sha256sum --check`, use
`shasum -a 256 -c` instead, or just run `tools/fetch_assets.sh`, which picks
a working implementation itself. Add `unshield` to the `brew install` line
when extracting the SDK cabinets. The
`mips-linux-gnu-*` static-analysis commands in
[`memory-map.md`](memory-map.md) assume the Debian cross-binutils package;
on macOS, Ghidra covers the same disassembly needs.

### Debian / Ubuntu

Install MAME's documented build dependencies plus the
analysis and test utilities used here:

```sh
sudo apt-get update
sudo apt-get install \
  git build-essential python3 \
  libsdl2-dev libsdl2-ttf-dev libfontconfig-dev libpulse-dev \
  qt6-base-dev qt6-base-dev-tools qtchooser \
  ccache binutils-mips-linux-gnu gdb-multiarch unshield \
  curl unzip gzip slirp bubblewrap
```

The cross-GCC packages are not required. In particular,
`gcc-mips-linux-gnu` and `g++-mips-linux-gnu` are absent from some current
Debian/Ubuntu repositories; `binutils-mips-linux-gnu` supplies the `readelf`,
`nm`, and `objdump` tools used for static analysis.

Mirror every research input (ROMs, packages, Windows reference tools, CPU
manual) with one checksum-verified command; add `all` to include the 176 MiB
SDK bundle, and see [`rom-layout.md`](rom-layout.md) for what each file is:

```sh
cd "$HOME/fun/magic-cap-emulator"
tools/fetch_assets.sh all
```

The resulting MAME ROM must be:

```text
~/fun/magic-cap-assets/roms/datarover840/magiccap-usa.image
```

## Clone and build

From `~/fun`, clone both repositories if they are not already present:

```sh
cd "$HOME/fun"
git clone https://github.com/ddanila/magic-cap-emulator.git
git clone --branch custom https://github.com/ddanila/mame.git
```

Build only the DataRover driver:

```sh
cd "$HOME/fun/mame"
PATH="/usr/lib/ccache:$PATH" \
  make SUBTARGET=datarover \
  SOURCES=src/mame/skeleton/datarover.cpp \
  REGENIE=1 \
  NO_USE_PORTAUDIO=1 \
  -j"$(nproc)"
```

(On macOS without coreutils, replace `$(nproc)` with
`$(sysctl -n hw.ncpu)`.)

This produces `~/fun/mame/datarover`. The scoped build is the normal
edit-build-run loop; a full MAME build is unnecessary.

## Run Magic Cap

For interactive play, `tools/start_manual.sh` wraps everything below (views,
persistent state in the assets tree, pointer alignment) — see its header for
modes. The rest of this section documents the underlying invocation.

The default power-on mode is Magic Cap and the default view is the handheld
LCD:

```sh
cd "$HOME/fun/mame"
./datarover datarover840 \
  -rompath "$HOME/fun/magic-cap-assets/roms" \
  -window -skip_gameinfo -view LCD \
  -lightgun_device mouse
```

Boot first shows a small bare top hat. That is only the early splash. The
interactive welcome scene is the larger hat inside a dark circle with
`Magic Cap™` and `Touch the screen to begin`. Click it, then click the three
calibration targets in order: upper-left, lower-right, center. The verified
result is the Magic Cap 3.1.2j workbench. **Machine Configuration** carries
**Main battery** and **Backup battery** settings for exercising the OS's
low-power paths; the defaults are healthy readings taken from the ROM's own
calibration records (see [`power-wake.md`](power-wake.md#battery-levels)).

`-lightgun_device mouse` maps the host pointer to the resistive pen axes.
Add `-nokeepaspect` for interactive use: SDL normalizes the pointer over the
whole window while `-keepaspect` letterboxes the 3:2 screen, so with
letterboxing the crosshair and the host cursor only agree at the window
center. With `-nokeepaspect` the screen fills the window and the pen mapping
is exact at any window size (keep the window near 3:2 to avoid distortion).
Press **End** for the DataRover power button; a normal press enters
suspend-to-RAM rather than destroying the battery-backed heap, and another
press wakes the CPU.

The driver exposes three video views under **Tab → Video Options**:

- `LCD` — the handheld display and default view.
- `Serial terminal` — the UART terminal only.
- `LCD and serial terminal` — both surfaces side by side.

A black terminal with a blinking green cursor is normal during automatic
Magic Cap boot: the production path does not print the IDT banner. It does not
mean the LCD framebuffer is black. Do not use `-video none` for an interactive
run; that option is only for headless tests.

## Run the IDT monitor

Start MAME, open **Tab → Machine Configuration**, change **Power-on mode** to
`IDT monitor`, reset the machine, and choose the `Serial terminal` view. The
current serial checkpoint is:

```text
IDT System Integration Manager Ver. 4.0 Feb, 1992
Copyright 1992 Integrated Device Technology, Inc.
Modifications - Copyright 1997 General Magic, Inc.
*** Hosted Version 4.23 Dec 5, 1997 ***
Profiler initialized
Memory: 4194304 (0x400000), Icache: 4096 (0x1000), Dcache: 1024 (0x400)
Toshiba Core - id: 0x2200
Platform: Apollo
For help enter '?'
<IDT>
```

The terminal keyboard is connected to UART A, so monitor commands can be
entered at the prompt.

## Automated checks

Validate the driver and ROM definition:

```sh
cd "$HOME/fun/mame"
./datarover -validate datarover840
./datarover -rompath "$HOME/fun/magic-cap-assets/roms" \
  -verifyroms datarover840
```

Run the analysis-tool unit tests and exact serial checkpoint comparison:

```sh
cd "$HOME/fun/magic-cap-emulator"
python3 -m unittest discover -s tests -v
python3 tools/serial_regression.py
python3 tools/serial_regression.py --checkpoint betty
python3 tools/desk_regression.py
python3 tools/power_regression.py
python3 tools/sound_regression.py
python3 tools/sound_regression.py --checkpoint dma
python3 tools/telecom_regression.py
python3 tools/telecom_regression.py --no-loopback
python3 tools/battery_regression.py
python3 tools/tx39_regression.py
python3 tools/pccard_regression.py
python3 tools/pclink_regression.py
python3 tools/modem_bridge.py --probe
python3 tools/modem_bridge.py --acceptance
python3 tools/devrom_tests.py
```

The serial harness writes generated configuration and logs under
`~/fun/magic-cap-assets/runtime/serial-regression/`. Override its defaults with
`--mame`, `--rompath`, `--workdir`, or `--seconds`. The `betty` checkpoint
uses the monitor's `call` command to execute the ROM's own `BettyTest` at
`0x13c076b0`. A failed register readback enters the ROM's `StayHere` loop;
returning to the `<IDT>` prompt is therefore the acceptance condition.

The desk harness starts with a fresh isolated NVRAM directory, taps the
welcome scene and all three calibration targets, and verifies the native
framebuffer at `0x003f6a00`. Its exact `0x9dab458b` signature covers the
stable lower workbench area; the full-screen checksum is reported but not
used as an assertion because the clock is time-dependent. It also writes a
native LCD PNG. Every run keeps its Lua script, MAME output, NVRAM, and
snapshot under a timestamped directory in
`~/fun/magic-cap-assets/runtime/desk-regression/`; no binary artifact is
written to this Git checkout.

The power harness is deliberately two processes, not a same-process shortcut.
It calibrates a fresh heap, enters normal VCC-off power-down, exits so only
battery-backed RAM/RTC survive, then relaunches that NVRAM. It observes the
ROM pass through `DeepDoze` and the retained shutdown's final
`WaitForPowerDown`, holds the on-button, and requires Dino's rising-edge latch
and live button status before proving execution has left the whole power-down
path with VCC restored. Generated Lua, both process logs, NVRAM, and three LCD
snapshots stay under
`~/fun/magic-cap-assets/runtime/power-regression/`.

The sound harness boots with SDL's dummy audio backend and asks MAME to write
the mixed output to a persistent WAV capture. Its default `beep` checkpoint
verifies that the ROM's hardware-generated startup tone is present near 750 Hz
for roughly 60 ms. `--checkpoint dma` runs far enough into the boot for the OS
to play its chime through buffered SIB sound DMA and requires a second audible
segment of 120-300 ms; see [`betty-registers.md`](betty-registers.md). The
emulated DAC lands on the capture's second channel, so the analysis always
picks the most occupied channel.
The WAV, generated Lua, NVRAM, and log remain under
`~/fun/magic-cap-assets/runtime/sound-regression/`.

The TX39 harness executes signed and unsigned multiply/add instructions from
uncached RAM and verifies `rd`, `HI`, and `LO`. Its generated inputs and log
remain under `~/fun/magic-cap-assets/runtime/tx39-regression/`; the CPU audit
and reference-manual download command are in
[`tx39-cpu.md`](tx39-cpu.md).

The PC Card harness copies the verified 840F flasher into its persistent run
directory, inserts that disposable copy after the workbench appears, and
checks common memory, CIS bytes, write/readback, Glacier card-detect signals,
and Magic Cap's live slot state. The source image and exact acquisition
instructions are in [`rom-layout.md`](rom-layout.md).

The PCLink harness uses the real UART-A PTY and recovered WinPCLink framing to
install an archived package through the Storeroom computer. It fails on a
Dino receive overrun, uses a final `Ping`/`Pong` as the completed-stream
barrier, immediately closes the connection from the host with `GBye`, and
verifies that the installed object appears in post-transfer native LCD
captures. Package and reference-tool download commands, checksums, protocol
notes, and alternate inputs are in [`pclink.md`](pclink.md).

The modem probe inserts the I/O card, accepts the ROM's Hayes initialization
and dial string, sends `CONNECT`, and requires Magic Cap to emit a valid PPP
LCP frame. The live Slirp handoff, guest network settings, Web Browser 4.0
download/install command, and automated plain-HTTP browser acceptance are in
[`modem.md`](modem.md).

The build also contains `datarover840f`, `datarover840j`, and `datarover840d`
(the 1998-04-07 development ROM — see [`dev-rom.md`](dev-rom.md)). Verify all
four external ROM sets with:

```sh
cd "$HOME/fun/mame"
for set in datarover840 datarover840d datarover840f datarover840j; do
  ./datarover -rompath "$HOME/fun/magic-cap-assets/roms" -verifyroms "$set"
done
```

The serial and desk harnesses accept `--system`, so both checkpoints can be
run against any of these sets; `datarover840d` passes both.

`tools/devrom_tests.py` goes further with that set: it forces calls to the
development ROM's own OS unit tests and judges them with the oracle the ROM
names itself (`AnnounceNonDebugFailure`). Fourteen suites return with no
complaint. `--self-check` validates the detector; the harness also enters the
two number-format tests through the ROM's own formatter-suite wrapper so their
required locale setup is present. [`dev-rom.md`](dev-rom.md) covers the
two-phase design and the suites that need more context than a forced call
provides.

**Machine configuration → RTC on resume.** Resuming battery-backed state
normally advances the RTC by the host wall-clock time that passed while the
machine was off. That is realistic, but it gives every headless run a
different time of day, which changes results: the original twelve-suite pass
failed six times on the host clock and passed completely with the clock
pinned. Set
**RTC on resume** to `Freeze at saved value` for reproducible runs;
`devrom_tests.py` does so by default and takes `--rtc host` to opt out. Other
harnesses start from fresh NVRAM, where no resume happens.

The 840F uses four persistent 2 MiB flash devices rather than the mask-ROM
map. Japan/840F acquisition and layout details are in
[`rom-layout.md`](rom-layout.md).

## Writing Lua automation

Points that cost time when writing a new harness:

- **Read Dino registers at their physical addresses.** MAME's Lua program space
  is not translated, so `program:read_u32(0x10c00140)` reads the RTC while the
  kseg1 alias `0xb0c00140` returns `0xffffffff`. The existing harnesses use
  `0x10c0xxxx` for the same reason.
- **Keep a breakpoint action to one command.** `cpu.debug:bpset(addr, "1", "do
  d@0x300104=d@0x300104+1; g")` works; chaining two `do` commands before the
  `g` stops the machine at the first hit instead of continuing, which looks
  exactly like the code under test hanging.
- **`jal` cannot reach the whole ROM.** Its target keeps the top four address
  bits, so a stub in low DRAM must call `0x13e9xxxx` through `jalr` with the
  address built by `lui`/`ori`.
- **Forcing the PC only drives code that does not yield.** Setting
  `cpu.state["PC"]` runs a leaf-ish ROM function fine, but anything that waits
  on a task, timer, or scene never returns to the injected frame: the scheduler
  resumes other work and abandons it. See [`dev-rom.md`](dev-rom.md).

## Native LCD snapshot

MAME's native snapshot mode writes one image per emulated screen. With the LCD
view active, press F12 after the startup artwork appears, or start MAME with:

```sh
mkdir -p "$HOME/fun/magic-cap-assets/captures"
cd "$HOME/fun/mame"
./datarover datarover840 \
  -rompath "$HOME/fun/magic-cap-assets/roms" \
  -window -skip_gameinfo -view LCD \
  -snapview native \
  -snapshot_directory "$HOME/fun/magic-cap-assets/captures"
```

Captures stay outside Git. `-snapview native` is also why an LCD capture can
contain valid pixels even if an interactive window was accidentally showing
the separate serial-terminal view.

## Verified boot landmarks

| Checkpoint | Evidence |
|---|---|
| Reset vector | Executes at `0xbfc00000` and jumps to the ROM's normal uncached alias |
| CPU identity | IDT monitor reports Toshiba core ID `0x2200`, 4 KiB I-cache, 1 KiB D-cache |
| RAM | Monitor reports 4,194,304 bytes |
| Serial | Exact banner and interactive `<IDT>` prompt pass the headless regression |
| Magic Cap entry | MAME debugger reaches ELF symbol `BootCap` at `0x13c1d120` |
| Early splash | The bare top hat renders before the interactive UI |
| Welcome | Circled hat, `Magic Cap™`, and `Touch the screen to begin` render and accept a pen tap |
| Calibration | Upper-left, lower-right, and center targets accept synthesized Betty ADC samples |
| Workbench | Live Dino buffer `0x003f6a00` reaches deterministic checksum `0x62d64ba4` |
| Persistence | 4 MiB DRAM and Dino RTC use external NVRAM files; a two-process regression proves retained-RAM power-down and on-button wake |
| Sound | ROM programs Betty and Dino for 11.025 kHz output; the captured startup tone measures about 750 Hz |
| PC Cards | Both Glacier-backed slots pass common-memory, CIS, write/readback, insertion, and live-OS checks |
| PCLink | The Storeroom computer accepts the handshake and installs archived `DvorakKeyboard.pkg` into built-in storage |
| PC Card modem | Magic Cap detects the card, completes its Hayes sequence, and emits an async-HDLC PPP LCP frame |
| Variants | Audited USA mask-ROM, USA 840F flash, and Japan ROM sets all build, verify, and enter execution |

The machine remains marked `MACHINE_NOT_WORKING` while the modeled hardware
is still incomplete. Buffered sound DMA is one known later item; incomplete
MagicBus peripheral replies also make the ROM's background MagicBus actor
eventually display an attached-device warning in longer sessions.
