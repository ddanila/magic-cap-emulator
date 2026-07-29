# MAME bring-up and verification

The DataRover driver lives on the `custom` branch of the
[`ddanila/mame`](https://github.com/ddanila/mame) fork. Keep the fork as a
sibling of this repository, and keep all ROMs, SDK files, captures, and logs
in the persistent asset tree — the sibling `../magic-cap-assets` by default,
or wherever the `MAGIC_CAP_ASSETS` environment variable points. No
copyrighted or generated binary is committed to either repository.

Command examples here and in the other docs assume the repository root as the
working directory and write the asset tree as `$MAGIC_CAP_ASSETS`. To follow
them literally, export it once per shell:

```sh
export MAGIC_CAP_ASSETS="${MAGIC_CAP_ASSETS:-$PWD/../magic-cap-assets}"
```

## Host prerequisites

The build and every headless regression except the live Slirp PPP bridge run
on both Linux and macOS. The host-UI touch regression is Linux/X11-only
because it drives a real MAME Tab menu under Xvfb. The bridge's dependencies
(classic `slirp` and `bubblewrap`, see [`modem.md`](modem.md)) are Linux-only;
`tools/modem_bridge.py --probe` still works everywhere.

### macOS

Install the Xcode Command Line Tools, then:

```sh
brew install sdl2 sdl2_ttf coreutils unar tesseract
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
  git build-essential python3 python3-pil \
  libsdl2-dev libsdl2-ttf-dev libfontconfig-dev libpulse-dev \
  pkg-config libslirp-dev \
  qt6-base-dev qt6-base-dev-tools qtchooser \
  ccache binutils-mips-linux-gnu gdb-multiarch unshield unar xvfb xdotool \
  curl unzip gzip openssl slirp bubblewrap \
  imagemagick ffmpeg gifsicle tesseract-ocr
```

The cross-GCC packages are not required. In particular,
`gcc-mips-linux-gnu` and `g++-mips-linux-gnu` are absent from some current
Debian/Ubuntu repositories; `binutils-mips-linux-gnu` supplies the `readelf`,
`nm`, and `objdump` tools used for static analysis. `unar` is only needed to
inspect the public StuffIt/BinHex developer archives documented in
[`developer-archives.md`](developer-archives.md); it is not a MAME build
dependency.

Mirror every research input (ROMs, packages, Windows reference tools, CPU
manual) with one checksum-verified command; add `all` to include the 176 MiB
SDK bundle, and see [`rom-layout.md`](rom-layout.md) for what each file is:

```sh
tools/fetch_assets.sh all
```

The resulting MAME ROM must be:

```text
$MAGIC_CAP_ASSETS/roms/datarover840/magiccap-usa.image
```

## Clone and build

Clone both repositories side by side, in any parent directory:

```sh
git clone https://github.com/ddanila/magic-cap-emulator.git
git clone --branch custom https://github.com/ddanila/mame.git
```

Build only the DataRover driver:

```sh
cd ../mame
PATH="/usr/lib/ccache:$PATH" \
  make SUBTARGET=datarover \
  SOURCES=src/mame/skeleton/datarover.cpp \
  REGENIE=1 \
  NO_USE_PORTAUDIO=1 \
  -j"$(nproc)"
```

(On macOS without coreutils, replace `$(nproc)` with
`$(sysctl -n hw.ncpu)`.)

This produces `../mame/datarover`. The scoped build is the normal
edit-build-run loop; a full MAME build is unnecessary.

## Run Magic Cap

For interactive play, `tools/start_manual.sh` wraps everything below (views,
persistent state in the assets tree, pointer alignment) — see its header for
modes. Set `MAGIC_CAP_NVRAM` to an NVRAM root containing `datarover840/` when
an interactive session should use a prepared state instead of the default
manual state. The rest of this section documents the underlying invocation.

The default power-on mode is Magic Cap and the default view is the handheld
LCD:

```sh
cd ../mame
./datarover datarover840 \
  -rompath "$MAGIC_CAP_ASSETS/roms" \
  -window -skip_gameinfo -nokeepaspect -view LCD \
  -lightgun -lightgun_device lightgun
```

Boot first shows a small bare top hat. That is only the early splash. The
interactive welcome scene is the larger hat inside a dark circle with
`Magic Cap™` and `Touch the screen to begin`. Click it, then click the three
calibration targets in order: upper-left, lower-right, center. The verified
result is the Magic Cap 3.1.2j workbench. **Machine Configuration** carries
**Main battery**, **Backup battery**, **AC adapter** and **Battery cover**
settings for exercising the OS's power paths; the defaults are healthy readings
taken from the ROM's own calibration records, on battery power with the cover
fitted (see [`power-wake.md`](power-wake.md#battery-levels)). Removing the cover
before power-on leaves the machine unable to bring the display up, which is why
the regression toggles it mid-session.

Keep the fork checkout in step with this repository. Harnesses that reference
driver devices by tag — `magicbus_probe.py` names the Magic Bus keyboard's
ioport, for instance — fail against a stale build with an unhelpful "no counts
reported", because the Lua lookup throws before anything is measured. Rebuild
after pulling the fork.

Machine configuration changes made interactively — or from a Lua script —
persist into the cfg directory, so a stray setting can silently affect later
runs. Every headless harness here passes its own `-cfg_directory` for that
reason.

### Save-state coverage

The machine is `MACHINE_SUPPORTS_SAVE`, and the modeled driver state is
registered with `save_item`, including the PC Card modem's configuration
option, 16550 registers, 64 KiB circular receive buffer and indices, pending
transmit interrupt, and Glacier READY/IREQ level. A state saved with the modem
present restores continuous card presence; it no longer manufactures a
remove/insert edge. For compatibility, selecting the optional modem while
loading a state whose saved CD pins describe an empty slot remains a genuine
insertion and is signaled as such.

`tools/modem_save_regression.py` writes distinctive UART state, supplies four
host bytes, consumes one, saves, corrupts every group, reloads, and proves the
three remaining bytes, divisor/register values, RX-then-TX interrupt priority,
READY/IREQ transition, and absence of new card-detect edges. A host PTY peer
or Slirp process is external to MAME and is not serialized.

`-lightgun -lightgun_device lightgun` maps the host pointer to one absolute
SDL lightgun device. The driver binds X, Y, and pen-down to that same device;
do not add `-mouse` or use `-lightgun_device mouse`, because that mixes
absolute and relative input state and can leave the pen position stale after
closing MAME's Tab menu. `-nokeepaspect` makes the screen fill the window so
the crosshair and host cursor agree at any window size (keep the window near
3:2 to avoid distortion). Press **End** for the DataRover power button; a
normal press enters suspend-to-RAM rather than destroying the battery-backed
heap, and another press wakes the CPU.

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
cd ../mame
./datarover -validate datarover840
./datarover -rompath "$MAGIC_CAP_ASSETS/roms" \
  -verifyroms datarover840
```

Run the analysis-tool unit tests and exact serial checkpoint comparison:

```sh
python3 -m unittest discover -s tests -v
python3 tools/serial_regression.py
python3 tools/serial_regression.py --checkpoint betty
python3 tools/desk_regression.py
python3 tools/menu_touch_regression.py
python3 tools/power_regression.py
python3 tools/sound_regression.py
python3 tools/sound_regression.py --checkpoint dma
python3 tools/sound_input_regression.py
# Requires a personalized NVRAM that resumes at the ordinary Desk:
python3 tools/sound_stamp_regression.py \
  --nvram-source "$MAGIC_CAP_ASSETS/runtime/manual/nvram"
python3 tools/telecom_regression.py
python3 tools/telecom_regression.py --continuous
python3 tools/telecom_regression.py --no-loopback
python3 tools/telecom_regression.py --dial-tone
python3 tools/telecom_regression.py --dtmf
python3 tools/telephone_line_regression.py
python3 tools/telephone_line_regression.py --pulse
python3 tools/telephone_bridge_regression.py
python3 tools/builtin_modem_regression.py \
  --nvram-source /path/to/a/passing/combined-browser/run/nvram
python3 tools/data_modem_pair_regression.py \
  --nvram-source /path/to/a/passing/combined-browser/run/nvram
# Requires the provider's home location to be mapped to PPP dialup:
python3 tools/product_data_modem_regression.py \
  --nvram-source /path/to/a/provider-configured/run/nvram
# Bounded host-supplied response through the same built-in-modem path:
python3 tools/product_data_modem_regression.py \
  --nvram-source /path/to/a/provider-configured/run/nvram \
  --http-upstream-url https://example.com/magic-cap-small.html \
  --http-expected-text "Expected page text"
# Requires calibrated NVRAM that resumes at the ordinary Desk:
python3 tools/telephone_ui_regression.py \
  --nvram-source "$MAGIC_CAP_ASSETS/runtime/manual/nvram"
python3 tools/incoming_call_regression.py \
  --nvram-source "$MAGIC_CAP_ASSETS/runtime/manual/nvram"
python3 tools/fax_receive_regression.py \
  --nvram-source "$MAGIC_CAP_ASSETS/runtime/manual/nvram"
python3 tools/fax_origin_regression.py \
  --nvram-source "$MAGIC_CAP_ASSETS/runtime/manual/nvram"
python3 tools/fax_pair_regression.py \
  --nvram-source "$MAGIC_CAP_ASSETS/runtime/manual/nvram"
# Longer relaunch/OCR check of the retained In-box fax and rendered page:
python3 tools/fax_pair_regression.py \
  --nvram-source "$MAGIC_CAP_ASSETS/runtime/manual/nvram" \
  --verify-stored-page
python3 tools/battery_regression.py
python3 tools/power_outputs_regression.py
python3 tools/power_policy_regression.py
python3 tools/magicbus_probe.py
python3 tools/ir_probe.py
python3 tools/beam_regression.py
python3 tools/tx39_regression.py
python3 tools/pccard_regression.py
python3 tools/modem_save_regression.py
python3 tools/storage_card_regression.py
python3 tools/storage_backup_regression.py
# Requires a state made by installing Translation.pkg with pclink_regression:
python3 tools/storage_translation_regression.py \
  --nvram "$TRANSLATION_NVRAM"
python3 tools/pclink_regression.py
python3 tools/etherlink_regression.py \
  --nvram-source /path/to/provider-and-browser/nvram
python3 tools/https_proxy_regression.py \
  --nvram-source /path/to/proxy-rule-configured/nvram
# Long-running loopback proxy for interactive public HTTPS browsing:
python3 tools/https_proxy.py
python3 tools/modem_bridge.py --probe
python3 tools/modem_bridge.py --acceptance
python3 tools/devrom_tests.py
python3 tools/devrom_suites.py
python3 tools/devrom_command_t.py
```

All checks above except `menu_touch_regression.py` select MAME's SDL `dummy`
video and audio drivers as well as disabling emulated video/audio output.
They therefore run without creating a host GUI window. The touch check
intentionally creates a real MAME window inside Xvfb to exercise the Tab menu;
`tools/start_manual.sh` remains the normal interactive launcher.

The serial harness writes generated configuration and logs under
`$MAGIC_CAP_ASSETS/runtime/serial-regression/`. Override its defaults with
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
`$MAGIC_CAP_ASSETS/runtime/desk-regression/`; no binary artifact is
written to this Git checkout.

The Linux-only menu-touch harness opens a real 720×480 MAME window under
Xvfb, presses and releases the pen, opens and closes the Tab menu with
separate key-down/key-up events, moves to a second point, and presses again.
It requires the effective X/Y/button bindings to all name lightgun 1 and
requires both coordinates plus pen-down to change after `menu_active` returns
to zero. Generated Lua, isolated NVRAM, and host logs remain under
`$MAGIC_CAP_ASSETS/runtime/menu-touch-regression/`.

The power harness is deliberately two processes, not a same-process shortcut.
It calibrates a fresh heap, enters normal VCC-off power-down, exits so only
battery-backed RAM/RTC survive, then relaunches that NVRAM. It observes the
ROM pass through `DeepDoze` and the retained shutdown's final
`WaitForPowerDown`, holds the on-button, and requires Dino's rising-edge latch
and live button status before proving execution has left the whole power-down
path with VCC restored. Generated Lua, both process logs, NVRAM, and three LCD
snapshots stay under
`$MAGIC_CAP_ASSETS/runtime/power-regression/`.

The Beam harness starts two fresh communicators with isolated configuration
and NVRAM, calibrates and creates owner name cards for Alice and Bob, then
bridges their dedicated IrDA PTYs. It drives the real Magic Lamp → Beam UI,
pulses Dino's IrDA carrier input, requires the sender to discover and select
`bob Receiver`, and transfers `alice Sender`'s name card. The check decodes
complete SIR frames, requires traffic and peer names in both directions, and
requires the serialized name-card payload. Raw wire captures, both MAME logs,
generated Lua, NVRAM, and UI snapshots remain under
`$MAGIC_CAP_ASSETS/runtime/beam-regression/`.

The sound harness boots with SDL's dummy audio backend and asks MAME to write
the mixed output to a persistent WAV capture. Its default `beep` checkpoint
verifies that the ROM's hardware-generated startup tone is present near 750 Hz
for roughly 60 ms. `--checkpoint dma` runs far enough into the boot for the OS
to play its chime through a continuously serviced SIB sound DMA ring and
requires a second audible segment lasting one to four seconds. The measured
segment is about 2.14 seconds; a single unrefilled 190 ms buffer pass now fails
the check. See [`betty-registers.md`](betty-registers.md). The emulated DAC
lands on the capture's second channel, so the analysis always picks the most
occupied channel.
The WAV, generated Lua, NVRAM, and log remain under
`$MAGIC_CAP_ASSETS/runtime/sound-regression/`.

The telephone-bridge harness starts two isolated IDT-monitor machines and a
local stream relay. Each continuous 64-word telecom DMA ring must receive the
other machine's distinct sample word in every slot. MAME starts after one
complete telecom word, waits at most 50 ms for later words, and falls back to
nonblocking startup after four misses; this keeps unequal host workloads
sample-aligned without hanging after a disconnect. The retained Lua, logs and
NVRAM remain under
`$MAGIC_CAP_ASSETS/runtime/telephone-bridge-regression/`. See
[`builtin-modem.md`](builtin-modem.md#external-pcm-bridge) for the reusable
relay and manual connection command.

The data-modem pair harness copies a provider-configured state into isolated
originating and answering peers, replays the shipping command roles and
process-clocks their external PCM at each 96-byte half-DMA boundary. It
requires both V.32 ROMs to lock their detectors, negotiate identical stable
rate payloads, enter data mode, switch to HDLC framing, complete LAPM
SABME/UA, report connected and retain their 48-word bidirectional DMA rings.
The product companion additionally requires Internet Center's dial-up and PPP
actors, the connection monitor, PPP output, answer-side ROM-queue delivery,
product-side PPP input, complete LCP/IPCP negotiation and an IPv4/TCP SYN from
`10.0.2.15:1024` to `10.0.2.2:8080`. The answer derives a valid SYN-ACK from
the randomized sequence and requires the browser's `GET / HTTP/1.0` with its
`Host` header, reassembles the request across ROM reads, and returns a
checksum-valid `HTTP/1.0 200 OK` plus deterministic HTML. The product-side
PPP counter gates completion, and Tesseract requires the response text in the
final Web Browser snapshot. Its trace classifies every PPP frame in each ROM
read, so concatenated control packets and retransmissions do not advance a
scripted response sequence. Artifacts remain under
`$MAGIC_CAP_ASSETS/runtime/product-data-modem-regression/`.

`--http-upstream-url` replaces the deterministic body with a host-fetched
HTTP(S) response. The adapter normalizes status, content type, length and
connection headers to HTTP/1.0, caps the complete application response at 700
bytes so it fits the current single ROM write, preserves the normalized bytes
as `host-http-response.bin`, and requires caller-supplied OCR text. This is a
bounded response adapter, not yet a transparent proxy: the fetch happens
before dialing, and the remaining work is forwarding the live guest request
and segmenting larger responses.

The paired-fax harness instead starts two ordinary retained-state machines,
creates and selects `Fax Peer` through the visible origin UI, and rings the
answerer from caller PCM position rather than an independent video frame. Its
central-office relay returns setup silence, holds the caller while the
answering UI enters modem mode, then OS-pauses whichever emulator leads the
call PCM count until its peer catches up. It requires bidirectional fax RX/TX
and HDLC, at least 64 origin `SendFaxImageData` and answer
`ReceiveFaxImageData` calls, zero ROM protocol/image-helper failures, changed
progress-window captures, and non-silent PCM in both directions. Artifacts
remain under
`$MAGIC_CAP_ASSETS/runtime/fax-pair-regression/`.
With `--verify-stored-page`, a copied receiver NVRAM is relaunched and
Tesseract must recognize the new In-box row, one-page fax stationery, and
rendered page. The original calibrated NVRAM remains untouched.

The TX39 harness executes signed and unsigned multiply and multiply/add
instructions from uncached RAM and verifies `rd`, `HI`, and `LO`. Its
generated inputs and log remain under
`$MAGIC_CAP_ASSETS/runtime/tx39-regression/`; the CPU audit and
reference-manual download command are in
[`tx39-cpu.md`](tx39-cpu.md).

The PC Card harness copies the verified 840F flasher into its persistent run
directory, inserts that disposable copy after the workbench appears, and
checks common memory, CIS bytes, write/readback, Glacier card-detect signals,
and Magic Cap's live slot state. The source image and exact acquisition
instructions are in [`rom-layout.md`](rom-layout.md).

The product-level companion creates its own erased 8 MiB card; no binary is
stored in this repository:

```sh
python3 tools/storage_card_regression.py
```

Its three isolated boots prove blank-card setup and naming, persisted
`BLNK`→`RAMC`/metacluster conversion, normal remount, and live Option-insert
erase/setup with header regeneration. The first boot additionally cycles the
slot through Good, Low and Dead battery settings and requires BVD2/BVD1
codes `11`, `01` and `00`. Two following processes share the Option phase's
retained state, select the card for new items, draw and commit a Notebook
page, then reopen page 2 and require a byte-identical screenshot. All other
phases use isolated NVRAM, every phase isolates MAME configuration, and only
the intended state group plus card image are shared. Generated cards, logs,
Lua and screenshots remain under
`$MAGIC_CAP_ASSETS/runtime/storage-card-regression/`.

The longer companion drives the documented full-device backup and restore:

```sh
python3 tools/storage_backup_regression.py
```

It creates its own erased card and isolated retained state, skips the
lifecycle test's battery cycling, backs up built-in storage through Storeroom,
then restores from the card in a fresh emulator process. The gate requires
the card bytes to contain the named backup package and `FBk` marker, retained
RAM to change during restore, the real successful-restore dialog, and zero
entries into the ROM's Magic Bus recovery routine. The latter also covers
rediscovery of the attached keyboard when Magic Cap reinitializes the bus.
Artifacts remain under
`$MAGIC_CAP_ASSETS/runtime/storage-backup-regression/`.

For a preserved Simulator 1.x card, build a disposable MAME image without
modifying either classic-Macintosh input:

```sh
python3 tools/legacy_card_image.py \
  --wrapper "$MAGIC_CAP_ASSETS/research/magic-cap-1-simulator/card.raw" \
  --changes "$MAGIC_CAP_ASSETS/research/magic-cap-1-simulator/card-changes.raw" \
  --output "$MAGIC_CAP_ASSETS/runtime/legacy-1x.card"
```

The corresponding automated translation check requires an isolated NVRAM tree
in which the public
[`Translation.pkg`](https://joshcarter.com/magic_cap/packages/Translation.pkg)
has already been installed through PCLink:

```sh
python3 tools/storage_translation_regression.py \
  --wrapper "$MAGIC_CAP_ASSETS/research/magic-cap-1-simulator/card.raw" \
  --changes "$MAGIC_CAP_ASSETS/research/magic-cap-1-simulator/card-changes.raw" \
  --nvram "$TRANSLATION_NVRAM"
```

It requires the real CompatibilityCardServer to accept the card with no
generic-card failure or software reset, checks the 1.x `GMMC`/`RAMC` tuple and
metacluster, selects the card's genuine `new items` package, translates it to
Built-in storage, and opens the resulting second Notebook page. It re-hashes
all source representations afterward. Create the fixture with **Don't Save
Changes** clear, then power off and eject the simulated card; a live-memory
capture can contain unfinished object references and is not a valid input.
The copyrighted Simulator/card inputs remain outside Git; the public
Simulator behavior and menu contract are documented at
[*Magic Cap Simulator*](https://www.datarover.com/Develop/MagicCap/Docs/Tools/CWMagic/Simulator.html).

The PCLink harness uses the real UART-A PTY and recovered WinPCLink framing to
install an archived package through the Storeroom computer. It fails on a
Dino receive overrun, uses a final `Ping`/`Pong` as the completed-stream
barrier, immediately closes the connection from the host with `GBye`, and
verifies that the installed object appears in post-transfer native LCD
captures. Its deterministic machine configuration keeps the modeled Magic Bus
keyboard present: an empty bus makes this ROM count unanswered address
assignment as a peripheral failure. The harness directly counts entries into
that ROM failure handler and requires zero, including during the sustained
461,876-byte browser-package transfer. Package and reference-tool download
commands, checksums, protocol notes, and alternate inputs are in
[`pclink.md`](pclink.md).

The modem probe inserts the I/O card, accepts the ROM's Hayes initialization
and dial string, sends `CONNECT`, and requires Magic Cap to emit a valid PPP
LCP frame. The live Slirp handoff, guest network settings, Web Browser 4.0
download/install command, and automated plain-HTTP browser acceptance are in
[`modem.md`](modem.md).

The build also contains `datarover840f`, `datarover840j`, and `datarover840d`
(the 1998-04-07 development ROM — see [`dev-rom.md`](dev-rom.md)). Verify all
four external ROM sets with:

```sh
cd ../mame
for set in datarover840 datarover840d datarover840f datarover840j; do
  ./datarover -rompath "$MAGIC_CAP_ASSETS/roms" -verifyroms "$set"
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

`tools/devrom_command_t.py` is the full scheduler-level acceptance run. It
queues the canonical `TestMachine_CommandTea` through the real system and user
run queues, restores the interrupted CPU context, and requires all 16 basic
suites plus `TestsComplete` to return without a ROM complaint. It also covers
the continuous buffered-sound ring through the moving-sound test. A fresh
calibrated NVRAM is the default; use `--nvram-source` to copy and reuse one.

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
mkdir -p "$MAGIC_CAP_ASSETS/captures"
cd ../mame
./datarover datarover840 \
  -rompath "$MAGIC_CAP_ASSETS/roms" \
  -window -skip_gameinfo -view LCD \
  -snapview native \
  -snapshot_directory "$MAGIC_CAP_ASSETS/captures"
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
| Workbench | Live Dino buffer `0x003f6a00` reaches stable lower-workbench signature `0x9dab458b`; the clock-dependent full-screen checksum is informational |
| Persistence | 4 MiB DRAM and Dino RTC use external NVRAM files; a two-process regression proves retained-RAM power-down and on-button wake |
| Power | LCD power blanks scanout without losing its framebuffer; Magic Bus Vcc-off drops and later rediscovers the accessory; AC plus charger enable raises the main-battery ADC; the real controls clamp 1–60 minutes and their AC-idle checkbox governs automatic `SLEE`/VCC-off shutdown |
| Sound output | ROM programs Betty and Dino for 11.025 kHz output; the captured startup tone measures about 750 Hz |
| Sound input | One-shot SIB receive DMA captures deterministic tone/silence with all expected status; the real Stamper UI records a 1 kHz microphone source, stops and drains its SIB command, then plays an audible WAV segment |
| Built-in modem | ROM opens `System_iSoftwareModem`, keeps its 48-word telecom RX/TX ring enabled, and executes V.32 and fax code through TX39 DSP extensions; paired generic roles negotiate matching rates and complete V.32 plus LAPM; Web Browser selects an Internet Center provider mapped to `PPP dialup`, dials `555-1212`, starts its real PPP actor, completes LCP/IPCP with guest address `10.0.2.15`, exchanges dynamic TCP, sends `GET / HTTP/1.0`, receives `HTTP/1.0 200 OK`, and renders its deterministic body; direct DAA verification covers connected/off-hook and both ring edges; a held ring opens Phone Status; **receive fax** reaches live-call `AnswerModem`, both fax HDLC directions and non-silent PCM; a clocked two-DataRover product run creates a recipient, dials `5551212`, sustains image transfer, then relaunches the receiver and opens the retained In-box fax, one-page stationery and rendered page; the exchange also supplies deterministic dial tone and decodes DTMF/pulse dialing |
| Magic Bus | ROM assigns and later reassigns address zero, validates the checksummed `ATKB` descriptor, dispatches Set-2 Caps Lock input, and writes the LED state back with no bus failures |
| PC Cards | Both Glacier-backed slots pass common-memory, CIS, write/readback and live-OS checks; blank storage setup, persistent `RAMC` remount, Option-insert reformat, Good/Low/Dead battery pins, a card-backed Notebook object and full built-in backup/restore also pass; `Translation.pkg` copies an authentic 1.x `new items` package into Built-in storage without source writes |
| PC Card Ethernet | The archived EtherLink driver initializes the 3C589, completes ARP/TCP through rootless libslirp, renders deterministic local HTTP, and carries Browser 3.5's native HTTPS Rule through a loopback Crypto Ancienne proxy; the absolute request, decrypted request, and rendered result are checked |
| PCLink | The Storeroom computer installs archived `DvorakKeyboard.pkg`; the optional 452K TLS-browser package also transfers, disconnects cleanly, and records zero ROM Magic Bus failures |
| IrDA / Beam | Two fresh peers exchange SIR discovery frames, select `bob Receiver`, and transfer `alice Sender`'s name card into the receiver's Inbox |
| PC Card modem | Magic Cap detects the card, completes its Hayes sequence, emits an async-HDLC PPP LCP frame, and preserves its 16550/RX/IREQ state without a false card edge across save/load |
| Variants | Audited USA mask-ROM, USA 840F flash, and Japan ROM sets all build, verify, and enter execution |

The machine remains marked `MACHINE_NOT_WORKING` while modeled hardware is
still incomplete. The current gaps include a general host bridge for the
now-connected built-in dial-up path, multi-device
Magic Bus topology, and hardware fidelity beyond the register behavior
exercised by the ROM; see
[`PLAN.md`](../PLAN.md#remaining-work). Magic Bus discovery and its
AT-keyboard traffic are functional and covered by the headless probe.
