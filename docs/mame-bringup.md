# MAME bring-up and verification

The DataRover driver lives on the `custom` branch of the
[`ddanila/mame`](https://github.com/ddanila/mame) fork. Keep the fork as a
sibling of this repository and keep all ROMs, SDK files, captures, and logs in
the persistent `~/fun/magic-cap-assets/` tree. No copyrighted or generated
binary is committed to either repository.

## Host prerequisites

On Debian or Ubuntu, install MAME's documented build dependencies plus the
analysis and test utilities used here:

```sh
sudo apt-get update
sudo apt-get install \
  git build-essential python3 \
  libsdl2-dev libsdl2-ttf-dev libfontconfig-dev libpulse-dev \
  qt6-base-dev qt6-base-dev-tools qtchooser \
  ccache binutils-mips-linux-gnu gdb-multiarch unshield \
  curl unzip gzip
```

The cross-GCC packages are not required. In particular,
`gcc-mips-linux-gnu` and `g++-mips-linux-gnu` are absent from some current
Debian/Ubuntu repositories; `binutils-mips-linux-gnu` supplies the `readelf`,
`nm`, and `objdump` tools used for static analysis.

Follow [`rom-layout.md`](rom-layout.md) to download and verify the ROM and SDK.
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
  NO_USE_PORTAUDIO=1 \
  -j"$(nproc)"
```

This produces `~/fun/mame/datarover`. The scoped build is the normal
edit-build-run loop; a full MAME build is unnecessary.

## Run Magic Cap

The default power-on mode is Magic Cap and the default view is the handheld
LCD:

```sh
cd "$HOME/fun/mame"
./datarover datarover840 \
  -rompath "$HOME/fun/magic-cap-assets/roms" \
  -window -skip_gameinfo -view LCD
```

The current verified milestone is the centered top-hat startup artwork on a
white 480×320 LCD. It can take noticeably longer than ten wall-clock seconds
to reach ten emulated seconds because the R3900 interpreter is not yet fast
enough for real-time execution.

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
```

The serial harness writes generated configuration and logs under
`~/fun/magic-cap-assets/runtime/serial-regression/`. Override its defaults with
`--mame`, `--rompath`, `--workdir`, or `--seconds`.

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
| Display | Live Dino buffer register points at `0x003f6a00`; top-hat artwork renders in the LCD view |

The machine remains marked `MACHINE_NOT_WORKING`: touch, persistent state,
complete Betty diagnostics, sound, PC Cards, modem/networking, and additional
TX39 fidelity are later plan items.
