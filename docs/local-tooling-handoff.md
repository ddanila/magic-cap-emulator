# Local analysis tooling handoff

This workstation has an optional reverse-engineering and Python analysis
environment for the Magic Cap work. It is installed outside the repositories,
so it does not add generated binaries or a virtual environment to Git.

Verified on 2026-07-30:

| Command | Purpose | Installed version |
|---|---|---|
| `ghidra` | Ghidra graphical reverse-engineering environment | 12.1.2 |
| `ghidra-analyze-headless` | Scriptable Ghidra analysis | 12.1.2 |
| `magic-cap-python` | Python interpreter in the dedicated analysis environment | 3.14 |
| `magic-cap-pip` | Package manager for that environment | pip 26.1.2 |

All four commands are on the normal shell `PATH`. To locate their current
installation without relying on a machine-specific directory:

```sh
command -v ghidra ghidra-analyze-headless magic-cap-python magic-cap-pip
```

The host also has the optional native tools used by the repository's build,
debugging, media and protocol workflows:

- build and C/C++ analysis: `ccache`, `clang`, `clangd`, `clang-format`,
  `clang-tidy`, `bear`, Universal Ctags, `cppcheck`, include-what-you-use,
  `lcov`, `gcovr`, AFL++, `heaptrack` and `pahole`;
- MIPS/debugging: GNU MIPS cross-binutils, `gdb-multiarch`, `qemu-mips`,
  radare2, Valgrind, `perf`, `strace`, `rr` and elfutils;
- shell and automation: `shellcheck`, `shfmt`, Lua 5.4, `luacheck`, SDL,
  Slirp, Xvfb and the full MAME build/runtime dependency set;
- binary, filesystem and archive inspection: Binwalk, Sleuth Kit, HFS
  utilities, SRecord, `cabextract`, `unar`, `unshield`, `xdelta3`, `bsdiff`,
  MuPDF and Tesseract;
- serial, network and media inspection: `tshark`, `socat`, `picocom`, FFmpeg
  and SoX; the distribution Python also has Scapy for packet decoding.

Check the command-facing subset without depending on installation paths:

```sh
command -v \
  clang-format clang-tidy bear ctags cppcheck iwyu lcov gcovr \
  shellcheck shfmt lua5.4 luac5.4 luacheck srec_cat cabextract rr \
  mips-linux-gnu-objdump gdb-multiarch qemu-mips \
  r2 valgrind perf strace eu-readelf pahole heaptrack afl-fuzz \
  binwalk fls hformat xdelta3 bsdiff \
  mutool tesseract tshark socat picocom unar unshield ffmpeg sox
```

## Ghidra

The installed release is the official
[Ghidra 12.1.2 binary release](https://github.com/NationalSecurityAgency/ghidra/releases/tag/Ghidra_12.1.2_build),
`ghidra_12.1.2_PUBLIC_20260605.zip`. Its verified SHA-256 was:

```text
b62e81a0390618466c019c60d8c2f796ced2509c4c1aea4a37644a77272cf99d
```

The release requires a 64-bit JDK 21; OpenJDK 21 is installed. Launch the GUI
with:

```sh
ghidra
```

Use this command for scripts and repeatable imports:

```sh
ghidra-analyze-headless
```

The downloaded ZIP was removed after checksum verification and successful
extraction. The unpacked installation remains available through the commands
above.

## Python environment

The repository's current regression tools only import Pillow outside the
standard library. The dedicated environment additionally carries packages
useful for the remaining modem/DSP and MIPS investigation:

- NumPy and SciPy for PCM and signal analysis;
- Matplotlib for plots and spectrograms;
- Capstone for programmatic MIPS disassembly;
- pyelftools for the SDK ELF and its symbols;
- pyserial for serial experiments;
- Construct for prototyping binary structures.

The direct requirements are pinned in
[`requirements-analysis.txt`](../requirements-analysis.txt). Run a script
without activating the environment:

```sh
magic-cap-python tools/sound_regression.py --help
magic-cap-python -m unittest discover -s tests -v
```

Install or re-check the pinned packages with:

```sh
magic-cap-pip install -r requirements-analysis.txt
magic-cap-pip check
```

To recreate the environment elsewhere, choose any persistent location:

```sh
python3 -m venv /persistent/path/magic-cap
/persistent/path/magic-cap/bin/python -m pip install \
  -r requirements-analysis.txt
```

## Why there are two Python installations

The shell's ordinary `python3` is supplied by Linuxbrew. Ubuntu's
`python3-numpy`, `python3-scipy`, and related packages belong to
`/usr/bin/python3`; they are not visible to Linuxbrew Python. This separation
is intentional. Do not add Ubuntu's `dist-packages` directory to Linuxbrew's
`PYTHONPATH`, because binary extension modules can be tied to a different
interpreter build.

Use one of these explicit choices:

```sh
magic-cap-python analysis-script.py  # dedicated, reproducible environment
/usr/bin/python3 analysis-script.py  # Ubuntu's Python and apt packages
python3 ordinary-script.py           # Linuxbrew's base Python
```
