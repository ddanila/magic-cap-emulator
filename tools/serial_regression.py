#!/usr/bin/env python3
"""Run the DataRover IDT monitor and compare its serial boot checkpoint."""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = Path.home() / "fun" / "magic-cap-assets" / "roms"
DEFAULT_WORKDIR = (
    Path.home()
    / "fun"
    / "magic-cap-assets"
    / "runtime"
    / "serial-regression"
)
EXPECTED_SERIAL = REPO_ROOT / "tests" / "data" / "idt-monitor.txt"
UART_TX_PATTERN = re.compile(rb"UARTA TX:\s+([0-9a-fA-F]{2})\b")


def extract_uart_bytes(mame_log: bytes) -> bytes:
    """Extract UART A transmit bytes from MAME's -oslog output."""
    return bytes(
        int(match.group(1), 16)
        for match in UART_TX_PATTERN.finditer(mame_log)
    )


def canonicalize_terminal(data: bytes) -> str:
    """Normalize terminal controls while retaining exact visible text."""
    text = data.decode("latin-1").replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.splitlines():
        visible = line.replace("\b", "").rstrip()
        if visible.strip():
            lines.append(visible.strip())
    return "\n".join(lines) + ("\n" if lines else "")


def monitor_config() -> str:
    """Return a MAME system configuration with the option key asserted."""
    return """<?xml version="1.0"?>
<mameconfig version="10">
    <system name="datarover840">
        <input>
            <port tag=":BOOT_MODE" type="CONFIG"
                  mask="8" defvalue="8" value="0" />
        </input>
    </system>
</mameconfig>
"""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mame",
        type=Path,
        default=DEFAULT_MAME,
        help=f"DataRover MAME executable (default: {DEFAULT_MAME})",
    )
    parser.add_argument(
        "--rompath",
        type=Path,
        default=DEFAULT_ROMPATH,
        help=f"MAME ROM search path (default: {DEFAULT_ROMPATH})",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=DEFAULT_WORKDIR,
        help=f"persistent logs/config directory (default: {DEFAULT_WORKDIR})",
    )
    parser.add_argument(
        "--seconds",
        type=int,
        default=3,
        help="emulated seconds to run (default: 3)",
    )
    args = parser.parse_args(argv)
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    return args


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    workdir = args.workdir.expanduser().resolve()

    if not mame.is_file():
        print(f"error: MAME executable not found: {mame}", file=sys.stderr)
        return 2
    if not rompath.is_dir():
        print(f"error: ROM path not found: {rompath}", file=sys.stderr)
        return 2

    config_dir = workdir / "cfg"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "datarover840.cfg").write_text(
        monitor_config(), encoding="utf-8"
    )

    command = [
        str(mame),
        "datarover840",
        "-rompath",
        str(rompath),
        "-cfg_directory",
        str(config_dir),
        "-video",
        "none",
        "-sound",
        "none",
        "-nothrottle",
        "-seconds_to_run",
        str(args.seconds),
        "-oslog",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=mame.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=max(30, args.seconds * 10),
        )
    except OSError as error:
        print(f"error: unable to run MAME: {error}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("error: MAME did not reach the checkpoint before timeout", file=sys.stderr)
        return 2

    workdir.mkdir(parents=True, exist_ok=True)
    raw_log = workdir / "mame-oslog.txt"
    serial_log = workdir / "idt-monitor.txt"
    raw_log.write_bytes(completed.stdout)
    actual = canonicalize_terminal(extract_uart_bytes(completed.stdout))
    serial_log.write_text(actual, encoding="utf-8")

    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; see {raw_log}",
            file=sys.stderr,
        )
        return 2

    expected = EXPECTED_SERIAL.read_text(encoding="utf-8")
    if actual != expected:
        diff = difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=str(EXPECTED_SERIAL),
            tofile=str(serial_log),
        )
        sys.stdout.writelines(diff)
        return 1

    print(f"PASS: serial checkpoint matches {EXPECTED_SERIAL}")
    print(f"Captured serial: {serial_log}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    return run_regression(args)


if __name__ == "__main__":
    raise SystemExit(main())
