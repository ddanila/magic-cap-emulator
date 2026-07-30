#!/usr/bin/env python3
"""Verify TX39 Config RF processor-clock divisors in MAME."""

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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "tx39-clock-regression"
RESULT_PATTERN = re.compile(
    rb"CLOCK RF=0 CONFIG=([0-9A-F]{8}) COUNT=([0-9A-F]{8}).*"
    rb"CLOCK RF=1 CONFIG=([0-9A-F]{8}) COUNT=([0-9A-F]{8}).*"
    rb"CLOCK RF=2 CONFIG=([0-9A-F]{8}) COUNT=([0-9A-F]{8}).*"
    rb"CLOCK RF=3 CONFIG=([0-9A-F]{8}) COUNT=([0-9A-F]{8}).*"
    rb"CLOCK_LOCK CONFIG=([0-9A-F]{8}) COUNT=([0-9A-F]{8})",
    re.DOTALL,
)
EXPECTED_CONFIGS = (
    0x00100030,
    0x00100430,
    0x00100830,
    0x00100C30,
    0x001008B0,
)


def automation_script() -> str:
    """Return fixed-time loops for the four RF values and Config locking."""
    return r"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0

local function run_mode(address, config)
    program:write_u32(address + 0x00, 0x24080000 | config)
    program:write_u32(address + 0x04, 0x40881800)
    program:write_u32(address + 0x08, 0x00000000)
    program:write_u32(address + 0x0c, 0x00000000)
    program:write_u32(address + 0x10, 0x40111800)
    program:write_u32(address + 0x14, 0x00000000)
    program:write_u32(address + 0x18, 0x24100000)
    program:write_u32(address + 0x1c, 0x26100001)
    program:write_u32(address + 0x20, 0x1000fffe)
    program:write_u32(address + 0x24, 0x00000000)
    cpu.state["SR"].value = 0
    cpu.state["PC"].value = 0xa0000000 | address
end

local function report_mode(rf)
    print(string.format(
        "CLOCK RF=%d CONFIG=%08X COUNT=%08X",
        rf,
        cpu.state["R17"].value,
        cpu.state["R16"].value))
end

emu.register_frame_done(function()
    frames = frames + 1

    if frames == 10 then
        run_mode(0x00001800, 0x0030)
    elseif frames == 11 then
        report_mode(0)
        run_mode(0x00001840, 0x0430)
    elseif frames == 12 then
        report_mode(1)
        run_mode(0x00001880, 0x0830)
    elseif frames == 13 then
        report_mode(2)
        run_mode(0x000018c0, 0x0c30)
    elseif frames == 14 then
        report_mode(3)

        -- Set RF=2 and Lock together, then attempt to return to RF=0.
        local address = 0x00001900
        program:write_u32(address + 0x00, 0x240808b0)
        program:write_u32(address + 0x04, 0x40881800)
        program:write_u32(address + 0x08, 0x00000000)
        program:write_u32(address + 0x0c, 0x00000000)
        program:write_u32(address + 0x10, 0x24080030)
        program:write_u32(address + 0x14, 0x40881800)
        program:write_u32(address + 0x18, 0x00000000)
        program:write_u32(address + 0x1c, 0x00000000)
        program:write_u32(address + 0x20, 0x40111800)
        program:write_u32(address + 0x24, 0x00000000)
        program:write_u32(address + 0x28, 0x24100000)
        program:write_u32(address + 0x2c, 0x26100001)
        program:write_u32(address + 0x30, 0x1000fffe)
        program:write_u32(address + 0x34, 0x00000000)
        cpu.state["SR"].value = 0
        cpu.state["PC"].value = 0xa0000000 | address
    elseif frames == 15 then
        print(string.format(
            "CLOCK_LOCK CONFIG=%08X COUNT=%08X",
            cpu.state["R17"].value,
            cpu.state["R16"].value))
        machine:exit()
    end
end)
"""


def parse_results(output: bytes) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    match = RESULT_PATTERN.search(output)
    if not match:
        return None
    values = tuple(int(value, 16) for value in match.groups())
    return values[0::2], values[1::2]


def verify_results(
    parsed: tuple[tuple[int, ...], tuple[int, ...]] | None,
) -> list[str]:
    if parsed is None:
        return ["complete clock report is missing"]

    configs, counts = parsed
    failures: list[str] = []
    if configs != EXPECTED_CONFIGS:
        failures.append(
            f"Config values {configs!r} do not match {EXPECTED_CONFIGS!r}"
        )
    if any(count == 0 for count in counts):
        failures.append(f"one or more fixed-time loop counts are zero: {counts!r}")
        return failures

    full_count = counts[0]
    for rf, count in enumerate(counts[:4]):
        normalized = count * (1 << rf)
        if not 0.97 * full_count <= normalized <= 1.03 * full_count:
            failures.append(
                f"RF={rf} normalized count {normalized} is not within 3% "
                f"of RF=0 count {full_count}"
            )

    locked_normalized = counts[4] * 4
    if not 0.97 * full_count <= locked_normalized <= 1.03 * full_count:
        failures.append(
            f"locked RF=2 normalized count {locked_normalized} is not within "
            f"3% of RF=0 count {full_count}"
        )
    return failures


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
    lua_path = run_dir / "tx39-clock-regression.lua"
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
        print(f"error: unable to run TX39 clock regression: {error}", file=sys.stderr)
        return 2

    log_path.write_bytes(completed.stdout)
    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; see {log_path}",
            file=sys.stderr,
        )
        return 2

    failures = verify_results(parse_results(completed.stdout))
    if failures:
        print(f"FAIL: {'; '.join(failures)}; see {log_path}", file=sys.stderr)
        return 1

    print(
        "PASS: TX39 RF divisors scale processor execution by 1, 2, 4, and 8, "
        "and Config Lock retains the selected rate"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
