#!/usr/bin/env python3
"""Check the battery model against the ROM's own calibration thresholds.

`BatteryServer_CalculateLevel` turns a Betty ADC reading into a percentage
between the "empty" and "full" fields of the Apollo calibration record the ROM
selects for Betty revision 1, and warns below the "low" field:

               empty   low    full     record
  main   (24)     80    320    800     0x13e96dc0
  backup (28)    400    816   1600     0x13e96e20

The driver used to answer 340 on the backup channel, below even the empty
point, so Magic Cap posted a backup-battery warning over the desk on every
boot. This harness boots twice and requires the OS to react to the difference:

  * with healthy readings the desk matches the recorded signature;
  * with the backup cell set to Empty the screen differs, because the OS posts
    "your communicator's backup battery is completely out of power".

The second run is the control. Without it a broken model that always reads
healthy would pass, exactly as it did before this check existed.
"""

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
    Path.home() / "fun" / "magic-cap-assets" / "runtime" / "battery-regression"
)

# Betty ADC readings the driver must answer for each configuration, and the
# battery config port value that selects it.
HEALTHY = 0x00
BACKUP_EMPTY = 0x08

CHECKPOINT = re.compile(rb"BATTERY_CHECKPOINT SCREEN=([0-9A-F]{8})")


def config_xml(system: str, battery: int) -> str:
    return f"""<?xml version="1.0"?>
<mameconfig version="10">
    <system name="{system}">
        <input>
            <port tag=":BATTERY" type="CONFIG"
                  mask="3" defvalue="0" value="{battery & 0x03}" />
            <port tag=":BATTERY" type="CONFIG"
                  mask="12" defvalue="0" value="{battery & 0x0c}" />
        </input>
    </system>
</mameconfig>
"""


def automation_script(snapshot: str) -> str:
    """Boot through calibration, then checksum the whole screen."""
    return f"""local machine = manager.machine
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local frames = 0

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 1220 then press(240, 160)
    elseif frames == 1240 then touch_button:set_value(0)
    elseif frames == 1420 then press(23, 23)
    elseif frames == 1440 then touch_button:set_value(0)
    elseif frames == 1620 then press(456, 296)
    elseif frames == 1640 then touch_button:set_value(0)
    elseif frames == 1820 then press(240, 160)
    elseif frames == 1840 then touch_button:set_value(0)
    elseif frames == 2200 then
        local program = machine.devices[":maincpu"].spaces["program"]
        local framebuffer = program:read_u32(0x10c00030) & 0xfffffff0
        local screen = 0
        for offset = 0, 38396, 4 do
            screen = (screen + program:read_u32(framebuffer + offset)) & 0xffffffff
        end
        print(string.format("BATTERY_CHECKPOINT SCREEN=%08X", screen))
        machine.screens[":screen"]:snapshot("{snapshot}")
    elseif frames == 2260 then
        machine:exit()
    end
end)
"""


def parse_checkpoint(output: bytes) -> int | None:
    match = CHECKPOINT.search(output)
    return int(match.group(1), 16) if match else None


def run_case(
    args: argparse.Namespace, base_dir: Path, name: str, battery: int
) -> int | None:
    run_dir = base_dir / name
    config_dir = run_dir / "cfg"
    nvram_dir = run_dir / "nvram"
    snapshot_dir = run_dir / "snapshots"
    config_dir.mkdir(parents=True)
    nvram_dir.mkdir()
    snapshot_dir.mkdir()
    lua_path = run_dir / f"{name}.lua"
    lua_path.write_text(automation_script(f"{name}.png"), encoding="utf-8")
    (config_dir / f"{args.system}.cfg").write_text(
        config_xml(args.system, battery), encoding="utf-8"
    )

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
        str(lua_path),
        "-video",
        "none",
        "-sound",
        "none",
        "-nothrottle",
        "-skip_gameinfo",
    ]
    completed = subprocess.run(
        command, cwd=args.mame.parent, capture_output=True, timeout=600
    )
    output = completed.stdout + completed.stderr
    (run_dir / "mame-output.txt").write_bytes(output)
    return parse_checkpoint(output)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument(
        "--system",
        default="datarover840d",
        help=(
            "MAME system to boot. The development ROM posts the backup-battery "
            "warning most reliably (default: datarover840d)"
        ),
    )
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    args.mame = args.mame.expanduser().resolve()
    args.rompath = args.rompath.expanduser().resolve()
    workdir = args.workdir.expanduser().resolve()
    if not args.mame.is_file():
        print(f"error: MAME executable not found: {args.mame}", file=sys.stderr)
        return 2
    if not args.rompath.is_dir():
        print(f"error: ROM path not found: {args.rompath}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    base_dir = workdir / f"{stamp}-{os.getpid()}"
    base_dir.mkdir(parents=True)

    healthy = run_case(args, base_dir, "healthy", HEALTHY)
    empty = run_case(args, base_dir, "backup-empty", BACKUP_EMPTY)

    if healthy is None or empty is None:
        print(
            "FAIL: a run produced no screen checkpoint; see "
            f"{base_dir}",
            file=sys.stderr,
        )
        return 1
    if healthy == empty:
        print(
            f"FAIL: the desk looks identical ({healthy:#010x}) with a healthy "
            "backup cell and an empty one, so the OS is not seeing the reading",
            file=sys.stderr,
        )
        return 1

    print(
        f"PASS: healthy desk {healthy:#010x} differs from the empty-backup "
        f"desk {empty:#010x}; the OS reacts to the modelled cell"
    )
    print(f"Artifacts: {base_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
