#!/usr/bin/env python3
"""Measure the Magic Bus failure cycle behind the "attached device" warning.

Long sessions post *"A problem happened while using an attached device"*. This
counts entries into the ROM's own Magic Bus routines to show why, once every
half minute or so:

    GetPollingCommand -> MagicBus_AssignMagicBusAddress
                      -> IssueMagicBusCommand
                      -> MagicBus_HandleMagicBusFailure

`MagicBusActor_Main` then checks `TotalFailuresExceedLimit`, and the alert
appears once the count passes the ROM's limit of five. The peripheral
discovery paths are never entered, so the OS is not reacting to anything the
driver signals: it broadcasts an address assignment and counts the silence,
because the driver has no Magic Bus peripheral to acknowledge it.

This is an instrument rather than a pass/fail gate: it prints the counts and
exits 0. Pass `--require-clean` to demand zero failures, which is what a
working peripheral model should produce - use it as the acceptance check when
implementing one. See docs/memory-map.md.

The addresses are from the release build. The development ROM shifts them, so
this refuses to run against anything else rather than silently measuring
nothing.
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
    Path.home() / "fun" / "magic-cap-assets" / "runtime" / "magicbus-probe"
)

# Release-build addresses; see the module docstring.
SUPPORTED_SYSTEMS = ("datarover840", "datarover840f")
WATCHED = (
    ("failures", 0x13C2AFB8, "MagicBus_HandleMagicBusFailure"),
    ("assign", 0x13C2A8EC, "MagicBus_AssignMagicBusAddress"),
    ("issue", 0x13C2848C, "IssueMagicBusCommand"),
    ("poll", 0x13C298C4, "GetPollingCommand"),
    ("limit_checks", 0x13C29434, "TotalFailuresExceedLimit"),
    ("req_line", 0x13C28364, "TestMBReqLine"),
    ("mbreq_handler", 0x13C295D4, "HandlerMagicBusMBReqLine"),
    ("peripheral_info", 0x13C29284, "GetPeripheralInfo"),
)
SCRATCH = 0x0030_0000
COUNTS = re.compile(rb"MAGICBUS COUNTS ([^\n]+)")


def automation_script(frames: int) -> str:
    """Return Lua that counts entries into each watched routine."""
    setup = "\n".join(
        f'    watch({SCRATCH + index * 4}, 0x{address:08x}, "{name}")'
        for index, (name, address, _symbol) in enumerate(WATCHED)
    )
    report = " .. ".join(
        f'string.format("{name}=%d ", program:read_u32({SCRATCH + index * 4}))'
        for index, (name, _address, _symbol) in enumerate(WATCHED)
    )
    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0

local function watch(slot, address, name)
    program:write_u32(slot, 0)
    -- One command only: chaining two `do`s stops the machine instead of
    -- continuing, which looks exactly like the code under test hanging.
    cpu.debug:bpset(address, "1",
        string.format("do d@0x%08x=d@0x%08x+1; g", slot, slot))
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 60 then
{setup}
    elseif frames == {frames} then
        print("MAGICBUS COUNTS " .. {report})
        machine:exit()
    end
end)
"""


def parse_counts(output: bytes) -> dict[str, int]:
    match = COUNTS.search(output)
    if not match:
        return {}
    return {
        key: int(value)
        for key, value in re.findall(r"(\w+)=(\d+)", match.group(1).decode())
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--system", default="datarover840")
    parser.add_argument(
        "--frames",
        type=int,
        default=9000,
        help="emulated frames to watch; failures accrue roughly every 1800",
    )
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="fail unless no Magic Bus failure was recorded",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    if not mame.is_file():
        print(f"error: MAME executable not found: {mame}", file=sys.stderr)
        return 2
    if args.system not in SUPPORTED_SYSTEMS:
        print(
            f"error: {args.system} does not use the release build's addresses; "
            f"choose one of {', '.join(SUPPORTED_SYSTEMS)}",
            file=sys.stderr,
        )
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    (run_dir / "nvram").mkdir(parents=True)
    (run_dir / "cfg").mkdir()
    lua_path = run_dir / "magicbus.lua"
    lua_path.write_text(automation_script(args.frames), encoding="utf-8")

    completed = subprocess.run(
        [
            str(mame), args.system,
            "-rompath", str(rompath),
            "-cfg_directory", str(run_dir / "cfg"),
            "-nvram_directory", str(run_dir / "nvram"),
            "-autoboot_delay", "0",
            "-autoboot_script", str(lua_path),
            "-debug", "-debugger", "none",
            "-video", "none", "-sound", "none",
            "-videodriver", "dummy", "-audiodriver", "dummy",
            "-nothrottle", "-skip_gameinfo",
        ],
        cwd=mame.parent,
        capture_output=True,
        timeout=1200,
    )
    output = completed.stdout + completed.stderr
    (run_dir / "mame-output.txt").write_bytes(output)

    counts = parse_counts(output)
    if not counts:
        print(f"error: no counts reported; see {run_dir}", file=sys.stderr)
        return 2

    width = max(len(name) for name, _address, _symbol in WATCHED)
    for name, _address, symbol in WATCHED:
        print(f"  {name:<{width}}  {counts.get(name, 0):4d}  {symbol}")
    print(f"Artifacts: {run_dir}")

    if args.require_clean and counts.get("failures"):
        print(
            f"FAIL: {counts['failures']} Magic Bus failure(s) recorded in "
            f"{args.frames} frames; no peripheral acknowledged the address "
            "assignment",
            file=sys.stderr,
        )
        return 1
    if args.require_clean:
        print("PASS: no Magic Bus failure recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
