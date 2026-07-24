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
CHECKPOINTS = {
    "monitor": {
        "expected": EXPECTED_SERIAL,
        "output": "idt-monitor.txt",
        "seconds": 3,
    },
    "betty": {
        "expected": REPO_ROOT / "tests" / "data" / "betty-test.txt",
        "output": "betty-test.txt",
        "seconds": 8,
        "command": r"call 13c076b0\n",
        "delay": 4,
    },
}


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


def monitor_config(system: str = "datarover840") -> str:
    """Return a monitor-mode MAME configuration with its keyboard enabled."""
    return f"""<?xml version="1.0"?>
<mameconfig version="10">
    <system name="{system}">
        <input>
            <keyboard tag=":terminal:keyboard" enabled="1" />
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
        help="emulated seconds to run (default depends on checkpoint)",
    )
    parser.add_argument(
        "--checkpoint",
        choices=CHECKPOINTS,
        default="monitor",
        help="serial checkpoint to verify (default: monitor)",
    )
    parser.add_argument(
        "--system",
        default="datarover840",
        help=(
            "MAME system to boot, for example datarover840d for the "
            "development ROM (default: datarover840)"
        ),
    )
    args = parser.parse_args(argv)
    if args.seconds is not None and args.seconds <= 0:
        parser.error("--seconds must be positive")
    return args


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    workdir = args.workdir.expanduser().resolve()
    checkpoint = CHECKPOINTS[args.checkpoint]
    seconds = args.seconds or checkpoint["seconds"]

    if not mame.is_file():
        print(f"error: MAME executable not found: {mame}", file=sys.stderr)
        return 2
    if not rompath.is_dir():
        print(f"error: ROM path not found: {rompath}", file=sys.stderr)
        return 2

    config_dir = workdir / "cfg"
    nvram_dir = workdir / "nvram" / args.checkpoint
    config_dir.mkdir(parents=True, exist_ok=True)
    nvram_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / f"{args.system}.cfg").write_text(
        monitor_config(args.system), encoding="utf-8"
    )

    command = [
        str(mame),
        args.system,
        "-rompath",
        str(rompath),
        "-cfg_directory",
        str(config_dir),
        "-nvram_directory",
        str(nvram_dir),
        "-video",
        "none",
        "-sound",
        "none",
        "-nothrottle",
        "-seconds_to_run",
        str(seconds),
        "-oslog",
    ]
    if "command" in checkpoint:
        command.extend(
            [
                "-natural",
                "-autoboot_delay",
                str(checkpoint["delay"]),
                "-autoboot_command",
                str(checkpoint["command"]),
            ]
        )
    try:
        completed = subprocess.run(
            command,
            cwd=mame.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=max(30, seconds * 10),
        )
    except OSError as error:
        print(f"error: unable to run MAME: {error}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("error: MAME did not reach the checkpoint before timeout", file=sys.stderr)
        return 2

    workdir.mkdir(parents=True, exist_ok=True)
    raw_log = workdir / "mame-oslog.txt"
    serial_log = workdir / str(checkpoint["output"])
    raw_log.write_bytes(completed.stdout)
    actual = canonicalize_terminal(extract_uart_bytes(completed.stdout))
    serial_log.write_text(actual, encoding="utf-8")

    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; see {raw_log}",
            file=sys.stderr,
        )
        return 2

    expected_path = Path(checkpoint["expected"])
    expected = expected_path.read_text(encoding="utf-8")
    if actual != expected:
        diff = difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=str(expected_path),
            tofile=str(serial_log),
        )
        sys.stdout.writelines(diff)
        return 1

    print(f"PASS: serial checkpoint matches {expected_path}")
    print(f"Captured serial: {serial_log}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    return run_regression(args)


if __name__ == "__main__":
    raise SystemExit(main())
