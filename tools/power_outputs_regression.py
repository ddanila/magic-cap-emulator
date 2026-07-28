#!/usr/bin/env python3
"""Exercise Apollo's observable MFIO power-output effects.

The release ROM uses Dino ``mfioDataOutput`` bits 17, 16 and 1 for the LCD
rail, active-high Magic Bus Vcc-off and charger enable respectively.  This
short IDT-monitor run checks the driver boundary directly:

* LCD-on renders the retained framebuffer, while LCD-off renders black;
* an attached AT keyboard requests enumeration only while Magic Bus has Vcc;
* a low main-battery ADC stays fixed without AC, rises with AC plus charger
  enable, then stops when charger enable is cleared.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "power-outputs-regression"

BATTERY_CHECKPOINT = re.compile(
    rb"POWER_OUTPUT BATTERY WHEN=(\w+) ADC=(\d+)"
)
MAGICBUS_CHECKPOINT = re.compile(
    rb"POWER_OUTPUT MAGICBUS POWERED=(\d) OFF=(\d) REDISCOVERED=(\d)"
)


def config_xml(system: str) -> str:
    return f"""<?xml version="1.0"?>
<mameconfig version="10">
    <system name="{system}">
        <input>
            <port tag=":BOOT_MODE" type="CONFIG"
                  mask="8" defvalue="8" value="0" />
            <port tag=":BATTERY" type="CONFIG"
                  mask="3" defvalue="0" value="1" />
        </input>
    </system>
</mameconfig>
"""


def automation_script() -> str:
    return r"""local machine = manager.machine
local program = machine.devices[":maincpu"].spaces["program"]
local screen = machine.screens[":screen"]
local ac = machine.ioport.ports[":POWER_SUPPLY"]:field(0x01)
local frames = 0

local MFIO = 0x10c00184
local MBUS_CONTROL = 0x10c000e0
local MBUS_COMMAND = 0x10c000f4
local SIB_SF0_AUX = 0x10c00080
local SIB_SF0_STATUS = 0x10c00088
local LCD_POWER = 0x00020000
local MBUS_VCC_OFF = 0x00010000
local CHARGER_ENABLE = 0x00000002
local MBUS_REQUEST = 0x20000000

local function main_battery_adc()
    -- Select Betty ADC channel 24, then read its conversion-result register.
    program:write_u32(SIB_SF0_AUX, 0x54000018)
    program:write_u32(SIB_SF0_AUX, 0x58000000)
    return (program:read_u32(SIB_SF0_STATUS) >> 5) & 0x03ff
end

local function battery_checkpoint(name)
    print(string.format(
        "POWER_OUTPUT BATTERY WHEN=%s ADC=%d", name, main_battery_adc()))
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 5 then
        local mfio = program:read_u32(MFIO)
        mfio = (mfio | LCD_POWER | CHARGER_ENABLE) & ~MBUS_VCC_OFF
        program:write_u32(MFIO, mfio)
        program:write_u32(MBUS_CONTROL, 1)
        program:write_u32(MBUS_COMMAND, 0xdef0)
        local powered = (program:read_u32(MBUS_CONTROL) & MBUS_REQUEST) ~= 0
        program:write_u32(MFIO, program:read_u32(MFIO) | MBUS_VCC_OFF)
        local off = (program:read_u32(MBUS_CONTROL) & MBUS_REQUEST) ~= 0
        program:write_u32(MFIO, program:read_u32(MFIO) & ~MBUS_VCC_OFF)
        program:write_u32(MBUS_COMMAND, 0xdef0)
        local rediscovered =
            (program:read_u32(MBUS_CONTROL) & MBUS_REQUEST) ~= 0
        print(string.format(
            "POWER_OUTPUT MAGICBUS POWERED=%d OFF=%d REDISCOVERED=%d",
            powered and 1 or 0, off and 1 or 0,
            rediscovered and 1 or 0))
    elseif frames == 6 then
        program:write_u32(
            MFIO, program:read_u32(MFIO) | LCD_POWER | CHARGER_ENABLE)
        screen:snapshot("lcd-on.png")
    elseif frames == 8 then
        local mfio = program:read_u32(MFIO)
        program:write_u32(MFIO, (mfio | CHARGER_ENABLE) & ~LCD_POWER)
    elseif frames == 9 then
        screen:snapshot("lcd-off.png")
    elseif frames == 11 then
        program:write_u32(
            MFIO, program:read_u32(MFIO) | LCD_POWER | CHARGER_ENABLE)
        battery_checkpoint("detached_before")
    elseif frames == 131 then
        battery_checkpoint("detached_after")
        ac:set_value(1)
    elseif frames == 251 then
        battery_checkpoint("attached_after")
        program:write_u32(
            MFIO, program:read_u32(MFIO) & ~CHARGER_ENABLE)
    elseif frames == 371 then
        battery_checkpoint("charger_off_after")
        machine:exit()
    end
end)
"""


def parse_battery_checkpoints(output: bytes) -> dict[str, int]:
    return {
        match.group(1).decode("ascii"): int(match.group(2))
        for match in BATTERY_CHECKPOINT.finditer(output)
    }


def parse_magicbus_checkpoint(output: bytes) -> tuple[int, int, int] | None:
    match = MAGICBUS_CHECKPOINT.search(output)
    return tuple(int(group) for group in match.groups()) if match else None


def image_is_effectively_black(path: Path) -> bool:
    with Image.open(path) as image:
        # MAME composites the 28x26 light-gun crosshair over native
        # snapshots.  Permit that tiny overlay while requiring the LCD
        # raster itself to be black.
        rgb = image.convert("RGB")
        pixels = (
            rgb.get_flattened_data()
            if hasattr(rgb, "get_flattened_data")
            else rgb.getdata()
        )
        non_black = sum(any(channel for channel in pixel) for pixel in pixels)
        return non_black <= image.width * image.height // 100


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--system", default="datarover840")
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    args.mame = args.mame.expanduser().resolve()
    args.rompath = args.rompath.expanduser().resolve()
    if not args.mame.is_file():
        print(f"error: MAME executable not found: {args.mame}", file=sys.stderr)
        return 2
    if not args.rompath.is_dir():
        print(f"error: ROM path not found: {args.rompath}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    config_dir = run_dir / "cfg"
    snapshot_dir = run_dir / "snapshots"
    nvram_dir = run_dir / "nvram"
    config_dir.mkdir(parents=True)
    snapshot_dir.mkdir()
    nvram_dir.mkdir()
    (config_dir / f"{args.system}.cfg").write_text(
        config_xml(args.system), encoding="utf-8"
    )
    script = run_dir / "power-outputs.lua"
    script.write_text(automation_script(), encoding="utf-8")

    command = [
        str(args.mame),
        args.system,
        "-rompath",
        str(args.rompath),
        "-cfg_directory",
        str(config_dir),
        "-nvram_directory",
        str(nvram_dir),
        "-snapshot_directory",
        str(snapshot_dir),
        "-snapview",
        "native",
        "-autoboot_delay",
        "0",
        "-autoboot_script",
        str(script),
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
    completed = subprocess.run(
        command, cwd=args.mame.parent, capture_output=True, timeout=120
    )
    output = completed.stdout + completed.stderr
    (run_dir / "mame-output.txt").write_bytes(output)

    failures: list[str] = []
    if completed.returncode:
        failures.append(f"MAME exited with status {completed.returncode}")

    battery = parse_battery_checkpoints(output)
    expected = {
        "detached_before",
        "detached_after",
        "attached_after",
        "charger_off_after",
    }
    missing = expected - battery.keys()
    if missing:
        failures.append(
            f"missing battery checkpoint(s): {', '.join(sorted(missing))}"
        )
    else:
        if battery["detached_after"] != battery["detached_before"]:
            failures.append("the battery changed while AC was detached")
        if battery["attached_after"] <= battery["detached_after"]:
            failures.append("AC plus charger enable did not raise the battery ADC")
        if battery["charger_off_after"] != battery["attached_after"]:
            failures.append("the battery kept charging after charger disable")

    magicbus = parse_magicbus_checkpoint(output)
    if magicbus != (1, 0, 1):
        failures.append(
            f"Magic Bus request state was {magicbus!r}, expected (1, 0, 1)"
        )

    lcd_on = snapshot_dir / "lcd-on.png"
    lcd_off = snapshot_dir / "lcd-off.png"
    if not lcd_on.is_file() or not lcd_off.is_file():
        failures.append("LCD snapshots were not produced")
    else:
        if image_is_effectively_black(lcd_on):
            failures.append("the LCD-on snapshot is black")
        if not image_is_effectively_black(lcd_off):
            failures.append("the LCD-off snapshot is not black")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 1

    print(
        "PASS: LCD rail blanks output, Magic Bus Vcc drops and rediscovers "
        "its request, and "
        f"AC charging raises main ADC {battery['detached_after']} -> "
        f"{battery['attached_after']} before charger disable"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
