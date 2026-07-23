#!/usr/bin/env python3
"""Execute and verify the TX39 MADD/MADDU instruction extensions in MAME."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = Path.home() / "fun" / "magic-cap-assets" / "roms"
DEFAULT_WORKDIR = (
    Path.home()
    / "fun"
    / "magic-cap-assets"
    / "runtime"
    / "tx39-regression"
)
RESULT_PATTERN = re.compile(
    rb"MADD R10=([0-9A-F]{8}) HI=([0-9A-F]{8}) LO=([0-9A-F]{8}) "
    rb"PC=([0-9A-F]{8}).*"
    rb"MADDU R11=([0-9A-F]{8}) HI=([0-9A-F]{8}) LO=([0-9A-F]{8}) "
    rb"PC=([0-9A-F]{8})",
    re.DOTALL,
)
EXPECTED = (
    0xFFFFFFFF,
    0xFFFFFFFF,
    0xFFFFFFFF,
    0xA0001004,
    0xFFFFFFFF,
    0x00000001,
    0xFFFFFFFF,
    0xA0001024,
)


def automation_script() -> str:
    """Return an isolated uncached-RAM test for both TX39 operations."""
    return r"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0

local function run_at(address)
    cpu.state["SR"].value = 0
    cpu.state["PC"].value = 0xa0000000 | address
end

emu.register_frame_done(function()
    frames = frames + 1

    if frames == 10 then
        -- MADD r10,r8,r9: 5 + (-2 * 3) = -1.
        program:write_u32(0x00001000, 0x71095000)
        program:write_u32(0x00001004, 0x1000ffff)
        program:write_u32(0x00001008, 0x00000000)
        cpu.state["R8"].value = 0xfffffffe
        cpu.state["R9"].value = 3
        cpu.state["HI"].value = 0
        cpu.state["LO"].value = 5
        run_at(0x00001000)
    elseif frames == 11 then
        print(string.format(
            "MADD R10=%08X HI=%08X LO=%08X PC=%08X",
            cpu.state["R10"].value,
            cpu.state["HI"].value,
            cpu.state["LO"].value,
            cpu.state["PC"].value))

        -- MADDU r11,r8,r9: 1 + (0xffffffff * 2) = 0x1ffffffff.
        program:write_u32(0x00001020, 0x71095801)
        program:write_u32(0x00001024, 0x1000ffff)
        program:write_u32(0x00001028, 0x00000000)
        cpu.state["R8"].value = 0xffffffff
        cpu.state["R9"].value = 2
        cpu.state["HI"].value = 0
        cpu.state["LO"].value = 1
        run_at(0x00001020)
    elseif frames == 12 then
        print(string.format(
            "MADDU R11=%08X HI=%08X LO=%08X PC=%08X",
            cpu.state["R11"].value,
            cpu.state["HI"].value,
            cpu.state["LO"].value,
            cpu.state["PC"].value))
        machine:exit()
    end
end)
"""


def parse_results(output: bytes) -> tuple[int, ...] | None:
    match = RESULT_PATTERN.search(output)
    if not match:
        return None
    return tuple(int(value, 16) for value in match.groups())


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
    lua_path = run_dir / "tx39-regression.lua"
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
        print(f"error: unable to run TX39 regression: {error}", file=sys.stderr)
        return 2

    log_path.write_bytes(completed.stdout)
    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; see {log_path}",
            file=sys.stderr,
        )
        return 2

    actual = parse_results(completed.stdout)
    if actual != EXPECTED:
        print(
            f"FAIL: TX39 result {actual!r}, expected {EXPECTED!r}; see {log_path}",
            file=sys.stderr,
        )
        return 1

    print("PASS: TX39 MADD and MADDU update rd, HI, and LO correctly")
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
