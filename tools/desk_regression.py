#!/usr/bin/env python3
"""Drive a fresh DataRover boot through calibration to the Magic Cap desk."""

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
    / "desk-regression"
)
EXPECTED_BASE = 0x003F6A00
EXPECTED_WORKBENCH = 0x9DAB458B
EXPECTED_MIN_NONZERO = 6_000
CHECKPOINT_PATTERN = re.compile(
    rb"DESK_CHECKPOINT BASE=([0-9A-F]{8}) "
    rb"CHECKSUM=([0-9A-F]{8}) "
    rb"WORKBENCH=([0-9A-F]{8}) NONZERO=(\d+)"
)


def automation_script() -> str:
    """Return the deterministic MAME Lua input sequence."""
    return r"""local machine = manager.machine
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

    if frames == 1220 then
        press(240, 160)
    elseif frames == 1240 then
        touch_button:set_value(0)
    elseif frames == 1420 then
        press(23, 23)
    elseif frames == 1440 then
        touch_button:set_value(0)
    elseif frames == 1620 then
        press(456, 296)
    elseif frames == 1640 then
        touch_button:set_value(0)
    elseif frames == 1820 then
        press(240, 160)
    elseif frames == 1840 then
        touch_button:set_value(0)
    elseif frames == 2200 then
        local program = machine.devices[":maincpu"].spaces["program"]
        local framebuffer = program:read_u32(0x10c00030) & 0xfffffff0
        local checksum = 0
        local workbench = 0
        local nonzero = 0

        for offset = 0, 38396, 4 do
            local word = program:read_u32(framebuffer + offset)
            checksum = (checksum + word) & 0xffffffff
            if offset >= 27600 then
                workbench = (workbench + word) & 0xffffffff
            end
            if word ~= 0 then
                nonzero = nonzero + 1
            end
        end

        print(string.format(
            "DESK_CHECKPOINT BASE=%08X CHECKSUM=%08X WORKBENCH=%08X NONZERO=%d",
            framebuffer, checksum, workbench, nonzero))
        machine.screens[":screen"]:snapshot("magic-cap-desk.png")
    elseif frames == 2220 then
        machine:exit()
    end
end)
"""


def parse_checkpoint(output: bytes) -> tuple[int, int, int, int] | None:
    """Extract the framebuffer checkpoint from combined MAME output."""
    match = CHECKPOINT_PATTERN.search(output)
    if not match:
        return None
    return (
        int(match.group(1), 16),
        int(match.group(2), 16),
        int(match.group(3), 16),
        int(match.group(4)),
    )


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
        help=f"persistent artifact directory (default: {DEFAULT_WORKDIR})",
    )
    parser.add_argument(
        "--card",
        type=Path,
        help="optional 8 MiB linear card image to mount in slot 1",
    )
    parser.add_argument(
        "--system",
        default="datarover840",
        help=(
            "MAME system to boot, for example datarover840d for the "
            "development ROM (default: datarover840)"
        ),
    )
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    workdir = args.workdir.expanduser().resolve()
    card = args.card.expanduser().resolve() if args.card else None

    if not mame.is_file():
        print(f"error: MAME executable not found: {mame}", file=sys.stderr)
        return 2
    if not rompath.is_dir():
        print(f"error: ROM path not found: {rompath}", file=sys.stderr)
        return 2
    if card is not None and (
        not card.is_file() or card.stat().st_size != 8 * 1024 * 1024
    ):
        print(
            f"error: card must be an existing 8 MiB image: {card}",
            file=sys.stderr,
        )
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = workdir / f"{stamp}-{os.getpid()}"
    nvram_dir = run_dir / "nvram"
    snapshot_dir = run_dir / "snapshots"
    nvram_dir.mkdir(parents=True)
    snapshot_dir.mkdir()
    lua_path = run_dir / "desk-regression.lua"
    lua_path.write_text(automation_script(), encoding="utf-8")

    command = [
        str(mame),
        args.system,
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
        "-video",
        "none",
        "-sound",
        "none",
        "-nothrottle",
        "-skip_gameinfo",
    ]
    if card is not None:
        command.extend(["-pccard1", "linear", "-card", str(card)])
    try:
        completed = subprocess.run(
            command,
            cwd=mame.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=90,
        )
    except OSError as error:
        print(f"error: unable to run MAME: {error}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print(f"error: MAME timed out; artifacts: {run_dir}", file=sys.stderr)
        return 2

    log_path = run_dir / "mame-output.txt"
    log_path.write_bytes(completed.stdout)
    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; "
            f"see {log_path}",
            file=sys.stderr,
        )
        return 2

    actual = parse_checkpoint(completed.stdout)
    if (
        actual is None
        or actual[0] != EXPECTED_BASE
        or actual[2] != EXPECTED_WORKBENCH
        or actual[3] < EXPECTED_MIN_NONZERO
    ):
        print(
            f"FAIL: desk checkpoint {actual!r}, expected base "
            f"{EXPECTED_BASE:#010x}, workbench signature "
            f"{EXPECTED_WORKBENCH:#010x}, and at least "
            f"{EXPECTED_MIN_NONZERO} nonzero words; "
            f"see {log_path}",
            file=sys.stderr,
        )
        return 1

    snapshot_path = snapshot_dir / "magic-cap-desk.png"
    if not snapshot_path.is_file():
        print(f"FAIL: native LCD snapshot was not written: {snapshot_path}", file=sys.stderr)
        return 1

    print(
        "PASS: calibrated Magic Cap desk framebuffer "
        f"matches workbench signature {EXPECTED_WORKBENCH:#010x}"
    )
    print(f"Snapshot: {snapshot_path}")
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    return run_regression(args)


if __name__ == "__main__":
    raise SystemExit(main())
