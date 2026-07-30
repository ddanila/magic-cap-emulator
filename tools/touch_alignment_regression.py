#!/usr/bin/env python3
"""Drive Controls -> Screen and repeat Magic Cap touch alignment."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageChops


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "touch-alignment-regression"
RESULT = re.compile(rb"TOUCH_ALIGNMENT calibrate=(\d+) touch=(\d+) commit=(\d+)")


def automation_script() -> str:
    """Return the first-boot and Controls navigation script."""
    return r"""local machine = manager.machine
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0
local scratch = 0x00300100

local function watch(slot, address)
    program:write_u32(slot, 0)
    cpu.debug:bpset(address, "1",
        string.format("do d@0x%08x=d@0x%08x+1; g", slot, slot))
end

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

local function release()
    touch_button:set_value(0)
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 1220 then press(240, 160)
    elseif frames == 1240 then release()
    elseif frames == 1420 then press(23, 23)
    elseif frames == 1440 then release()
    elseif frames == 1620 then press(456, 296)
    elseif frames == 1640 then release()
    elseif frames == 1820 then press(240, 160)
    elseif frames == 1840 then release()
    elseif frames == 2200 then press(455, 8)
    elseif frames == 2220 then release()
    elseif frames == 2500 then press(424, 108)
    elseif frames == 2520 then release()
    elseif frames == 2900 then
        machine.screens[":screen"]:snapshot("01-controls.png")
        press(240, 75)
    elseif frames == 2920 then release()
    elseif frames == 3300 then
        machine.screens[":screen"]:snapshot("02-screen-controls.png")
        program:write_u32(scratch, 0)
        program:write_u32(scratch + 4, 0)
        program:write_u32(scratch + 8, 0)
        watch(scratch, 0x13e132f0)
        watch(scratch + 4, 0x13e12d04)
        watch(scratch + 8, 0x13e128c8)
        press(275, 133)
    elseif frames == 3320 then release()
    elseif frames == 3500 then
        machine.screens[":screen"]:snapshot("03-realignment-start.png")
        press(23, 23)
    elseif frames == 3520 then release()
    elseif frames == 3700 then
        machine.screens[":screen"]:snapshot("04-realignment-second.png")
        press(456, 296)
    elseif frames == 3720 then release()
    elseif frames == 3900 then
        machine.screens[":screen"]:snapshot("05-realignment-third.png")
        press(240, 160)
    elseif frames == 3920 then release()
    elseif frames == 4300 then
        machine.screens[":screen"]:snapshot("06-screen-controls-returned.png")
        print(string.format(
            "TOUCH_ALIGNMENT calibrate=%d touch=%d commit=%d",
            program:read_u32(scratch),
            program:read_u32(scratch + 4),
            program:read_u32(scratch + 8)))
        machine:exit()
    end
end)
"""


def parse_result(output: bytes) -> tuple[int, int, int] | None:
    """Extract the post-adjust CalibrationPad routine counts."""
    match = RESULT.search(output)
    return tuple(int(value) for value in match.groups()) if match else None


def screen_panel_matches(before: Path, after: Path) -> bool:
    """Ignore the live status bar while requiring the Screen panel to return."""
    with Image.open(before) as first, Image.open(after) as second:
        first_panel = first.convert("RGB").crop((0, 25, 480, 280))
        second_panel = second.convert("RGB").crop((0, 25, 480, 280))
        return ImageChops.difference(first_panel, second_panel).getbbox() is None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    if not mame.is_file() or not rompath.is_dir():
        print("error: MAME executable or ROM directory is missing", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    nvram_dir = run_dir / "nvram"
    snapshot_dir = run_dir / "snapshots"
    nvram_dir.mkdir(parents=True)
    snapshot_dir.mkdir()
    lua_path = run_dir / "touch-alignment.lua"
    lua_path.write_text(automation_script(), encoding="utf-8")

    command = [
        str(mame),
        "datarover840",
        "-rompath",
        str(rompath),
        "-nvram_directory",
        str(nvram_dir),
        "-snapshot_directory",
        str(snapshot_dir),
        "-snapview",
        "native",
        "-autoboot_delay",
        "0",
        "-autoboot_script",
        str(lua_path),
        "-debug",
        "-debugger",
        "none",
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
            cwd=mame.parent,
            capture_output=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"error: unable to complete MAME run: {error}", file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 2
    output = completed.stdout + completed.stderr
    (run_dir / "mame-output.txt").write_bytes(output)
    if completed.returncode:
        print(f"error: MAME exited with status {completed.returncode}", file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 2

    failures = []
    result = parse_result(output)
    if result != (1, 3, 1):
        failures.append(
            f"CalibrationPad counts were {result!r}, expected (1, 3, 1)"
        )
    before = snapshot_dir / "02-screen-controls.png"
    after = snapshot_dir / "06-screen-controls-returned.png"
    if not before.is_file() or not after.is_file():
        failures.append("Screen control snapshots are incomplete")
    elif not screen_panel_matches(before, after):
        failures.append("Screen controls did not return after alignment")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 1

    print(
        "PASS: Controls invoked CalibrationPad, accepted all three targets, "
        "committed calibration, and returned to Screen"
    )
    print(f"Artifacts: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
