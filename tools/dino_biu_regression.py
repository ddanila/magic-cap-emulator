#!/usr/bin/env python3
"""Verify the Apollo ROM's Dino bus-interface configuration in MAME."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "dino-biu-regression"
EXPECTED = (
    0x0101_1091,
    0xFFFF_FFFF,
    0x2222_FF66,
    0x44FF_0100,
    0x0160_4000,
)
RESULT_PATTERN = re.compile(
    rb"DINO_BIU CFG0=([0-9A-F]{8}) CFG1=([0-9A-F]{8}) "
    rb"CFG2=([0-9A-F]{8}) CFG3=([0-9A-F]{8}) CFG4=([0-9A-F]{8})"
)


def automation_script() -> str:
    """Return a script that waits for and reports ROM BIU initialization."""
    return r"""local machine = manager.machine
local program = machine.devices[":maincpu"].spaces["program"]
local frames = 0
local DINO = 0x10c00000

emu.register_frame_done(function()
    frames = frames + 1
    local cfg1 = program:read_u32(DINO + 0x04)
    local cfg4 = program:read_u32(DINO + 0x10)
    if (cfg1 == 0xffffffff and cfg4 == 0x01604000) or frames == 120 then
        print(string.format(
            "DINO_BIU CFG0=%08X CFG1=%08X CFG2=%08X CFG3=%08X CFG4=%08X",
            program:read_u32(DINO + 0x00),
            cfg1,
            program:read_u32(DINO + 0x08),
            program:read_u32(DINO + 0x0c),
            cfg4))
        machine:exit()
    end
end)
"""


def parse_result(output: bytes) -> tuple[int, ...] | None:
    """Return the five observed configuration registers."""
    match = RESULT_PATTERN.search(output)
    if not match:
        return None
    return tuple(int(value, 16) for value in match.groups())


def verify_result(result: tuple[int, ...] | None) -> list[str]:
    """Compare the live registers with Apollo.asm.h and MM_InitializeDino."""
    if result is None:
        return ["missing Dino BIU result"]
    return [
        f"CFG{index} {actual:#010x} does not match {expected:#010x}"
        for index, (actual, expected) in enumerate(zip(result, EXPECTED))
        if actual != expected
    ]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    return parser.parse_args(argv)


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

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = workdir / f"{stamp}-{os.getpid()}"
    nvram_dir = run_dir / "nvram"
    nvram_dir.mkdir(parents=True)
    lua_path = run_dir / "dino-biu-regression.lua"
    log_path = run_dir / "mame-output.txt"
    lua_path.write_text(automation_script(), encoding="utf-8")

    command = [
        str(mame),
        "datarover840",
        "-rompath",
        str(rompath),
        "-nvram_directory",
        str(nvram_dir),
        "-autoboot_delay",
        "0",
        "-autoboot_script",
        str(lua_path),
        "-video",
        "none",
        "-sound",
        "none",
        "-videodriver",
        "dummy",
        "-audiodriver",
        "dummy",
        "-nothrottle",
        "-skip_gameinfo",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=run_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"error: unable to run Dino BIU regression: {error}", file=sys.stderr)
        return 2

    log_path.write_bytes(completed.stdout)
    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; "
            f"see {log_path}",
            file=sys.stderr,
        )
        return 2

    failures = verify_result(parse_result(completed.stdout))
    if failures:
        print(f"FAIL: {'; '.join(failures)}; see {log_path}", file=sys.stderr)
        return 1

    print(
        "PASS: Apollo initialized Dino for 32-bit page-mode DRAM, "
        "a 32-bit CS0 ROM bus, documented CS/card waits, CS0 burst, "
        "and DRAM refresh/watchdog"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
