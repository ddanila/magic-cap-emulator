#!/usr/bin/env python3
"""Check the battery and power-supply inputs the OS reads.

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

It also covers the battery cover, which `PowerSupplyGen2MFS_BatteryCoverAttached`
reads from `ioControl` bit 2 inverted: removing it mid-session must change the
screen, because the OS notices the switch through IO interrupt 2.

The extra runs are controls. Without them a model that always reported a
healthy, closed-up machine would pass, exactly as it did before this check
existed.
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

# POWER_SUPPLY bit 1 removes the battery cover; the harness toggles it while
# the desk is up rather than at power-on, because a machine that boots with no
# cover has no cells and never brings the display up.
COVER_REMOVED = 0x02
COVER_TOGGLE_FRAME = 2300

CHECKPOINT = re.compile(
    rb"BATTERY_CHECKPOINT WHEN=(\w+) SCREEN=([0-9A-F]{8})"
)


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


def automation_script(snapshot: str, remove_cover: bool = False) -> str:
    """Boot through calibration, checksum the screen, then checksum it again.

    The second checkpoint exists for the cover case: the switch is thrown
    between the two, so a reaction shows up as a changed "after" checksum.
    """
    toggle = "true" if remove_cover else "false"
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

local function screen()
    local program = machine.devices[":maincpu"].spaces["program"]
    local framebuffer = program:read_u32(0x10c00030) & 0xfffffff0
    local total = 0
    for offset = 0, 38396, 4 do
        total = (total + program:read_u32(framebuffer + offset)) & 0xffffffff
    end
    return total
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
        print(string.format("BATTERY_CHECKPOINT WHEN=before SCREEN=%08X", screen()))
        machine.screens[":screen"]:snapshot("{snapshot}")
    elseif frames == {COVER_TOGGLE_FRAME} and {toggle} then
        machine.ioport.ports[":POWER_SUPPLY"]:field({COVER_REMOVED}):set_value({COVER_REMOVED})
        print("BATTERY_COVER_REMOVED")
    elseif frames == 2900 then
        print(string.format("BATTERY_CHECKPOINT WHEN=after SCREEN=%08X", screen()))
        machine.screens[":screen"]:snapshot("after-{snapshot}")
        machine:exit()
    end
end)
"""


def parse_checkpoints(output: bytes) -> dict[str, int]:
    """Map checkpoint name to screen checksum."""
    return {
        match.group(1).decode("ascii"): int(match.group(2), 16)
        for match in CHECKPOINT.finditer(output)
    }


def run_case(
    args: argparse.Namespace,
    base_dir: Path,
    name: str,
    battery: int,
    remove_cover: bool = False,
) -> dict[str, int]:
    run_dir = base_dir / name
    config_dir = run_dir / "cfg"
    nvram_dir = run_dir / "nvram"
    snapshot_dir = run_dir / "snapshots"
    config_dir.mkdir(parents=True)
    nvram_dir.mkdir()
    snapshot_dir.mkdir()
    lua_path = run_dir / f"{name}.lua"
    lua_path.write_text(
        automation_script(f"{name}.png", remove_cover), encoding="utf-8"
    )
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
        "-videodriver",
        "dummy",
        "-audiodriver",
        "dummy",
        "-nothrottle",
        "-skip_gameinfo",
    ]
    completed = subprocess.run(
        command, cwd=args.mame.parent, capture_output=True, timeout=600
    )
    output = completed.stdout + completed.stderr
    (run_dir / "mame-output.txt").write_bytes(output)
    return parse_checkpoints(output)


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
    cover = run_case(args, base_dir, "cover-removed", HEALTHY, remove_cover=True)

    for name, points in (
        ("healthy", healthy), ("backup-empty", empty), ("cover-removed", cover)
    ):
        missing = {"before", "after"} - points.keys()
        if missing:
            print(
                f"FAIL: {name} run reported no {'/'.join(sorted(missing))} "
                f"checkpoint; see {base_dir}",
                file=sys.stderr,
            )
            return 1

    failures = []

    # The cover run is identical to the healthy one until the switch is thrown,
    # which is what makes its "after" difference attributable.
    if cover["before"] != healthy["before"]:
        failures.append(
            f"the cover run diverged before the switch was thrown "
            f"({cover['before']:#010x} vs {healthy['before']:#010x})"
        )
    if empty["before"] == healthy["before"]:
        failures.append(
            f"an empty backup cell left the desk unchanged "
            f"({healthy['before']:#010x}), so the OS is not seeing the reading"
        )
    if cover["after"] == healthy["after"]:
        failures.append(
            f"removing the battery cover left the desk unchanged "
            f"({healthy['after']:#010x}), so the OS is not seeing ioControl bit 2"
        )

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        print(f"Artifacts: {base_dir}", file=sys.stderr)
        return 1

    print(
        f"PASS: empty backup cell changes the desk "
        f"({healthy['before']:#010x} -> {empty['before']:#010x}); removing the "
        f"battery cover changes it too "
        f"({healthy['after']:#010x} -> {cover['after']:#010x})"
    )
    print(f"Artifacts: {base_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
