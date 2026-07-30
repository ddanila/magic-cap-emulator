#!/usr/bin/env python3
"""Exercise Magic Bus discovery and an AT-keyboard request end to end.

The probe counts entries into the release ROM's own Magic Bus routines. After
the ROM discovers the modeled ``ATKB`` peripheral, Lua presses and releases
Caps Lock on MAME's keyboard matrix. A complete run reaches peripheral-info
validation, the keyboard client's request and Set-2 dispatch routines, and the
return LED-control write without entering either Magic Bus error path.

This is an instrument by default and prints every count. Pass
``--require-clean`` to make the complete discovery-and-keypress sequence an
acceptance gate. See docs/memory-map.md.

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
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "magicbus-probe"

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
    ("low_errors", 0x13C28B3C, "MagicBusError"),
    ("keyboard_attached", 0x13C27594, "MagicBusATKeyboard_Attached"),
    ("scsi_attached", 0x13E82D10, "MagicBusSCSITargetClient_Attached"),
    ("keyboard_requests", 0x13C27B20, "MagicBusATKeyboard_PeripheralRequest"),
    ("keyboard_dispatch", 0x13C2763C, "MagicBusATKeyboard_DispatchATKeys"),
    ("keyboard_led", 0x13C27C84, "MagicBusATKeyboard_SetLedStatus"),
)
SCRATCH = 0x0030_0000
COUNTS = re.compile(rb"MAGICBUS COUNTS ([^\n]+)")
KEY_FRAME = 600


def automation_script(frames: int, accessories: int = 1) -> str:
    """Return Lua that counts entries into each watched routine."""
    if accessories not in (1, 2):
        raise ValueError("Magic Bus probe supports one or two accessories")
    keyboard_attached_index = next(
        index
        for index, (name, _address, _symbol) in enumerate(WATCHED)
        if name == "keyboard_attached"
    )
    keyboard_attached_slot = SCRATCH + keyboard_attached_index * 4
    scsi_attached_index = next(
        index
        for index, (name, _address, _symbol) in enumerate(WATCHED)
        if name == "scsi_attached"
    )
    scsi_attached_slot = SCRATCH + scsi_attached_index * 4
    setup = "\n".join(
        f'    watch({SCRATCH + index * 4}, 0x{address:08x}, "{name}")'
        for index, (name, address, _symbol) in enumerate(WATCHED)
    )
    report = " .. ".join(
        f'string.format("{name}=%d ", program:read_u32({SCRATCH + index * 4}))'
        for index, (name, _address, _symbol) in enumerate(WATCHED)
    )
    second_ready = (
        f"\n            and program:read_u32({scsi_attached_slot}) >= 1"
        if accessories == 2
        else ""
    )
    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local caps_lock = machine.ioport.ports[
    ":magicbus_keyboard:pc_keyboard_3"].fields["Caps"]
local frames = 0
local key_down_frame = nil

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
    end

    -- The power-supply class deliberately cycles Magic Bus Vcc during boot.
    -- Inject only after the real client has attached: a physical keyboard
    -- cannot retain a key pressed before that power cycle either.
    if frames >= {KEY_FRAME}
            and key_down_frame == nil
            and program:read_u32({keyboard_attached_slot}) >= 1{second_ready} then
        key_down_frame = frames
        print(string.format(
            "MAGICBUS KEY_DOWN FRAME=%d MFIO=%08X CONTROL=%08X",
            frames, program:read_u32(0x10c00184),
            program:read_u32(0x10c000e0)))
        caps_lock:set_value(caps_lock.mask)
    elseif key_down_frame ~= nil and frames == key_down_frame + 10 then
        caps_lock:set_value(0)
    end

    if frames == {frames} then
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


def acceptance_errors(
    counts: dict[str, int], required_accessories: int = 1
) -> list[str]:
    """Explain which observable parts of a complete transaction are absent."""
    errors = []
    for name in ("failures", "low_errors"):
        if counts.get(name):
            errors.append(f"{name}={counts[name]}")
    for name in ("assign", "peripheral_info"):
        if counts.get(name, 0) < required_accessories:
            errors.append(f"{name}={counts.get(name, 0)} (need {required_accessories})")
    for name in (
        "keyboard_attached",
        "keyboard_requests",
        "keyboard_dispatch",
        "keyboard_led",
    ):
        if counts.get(name, 0) < 1:
            errors.append(f"{name}={counts.get(name, 0)} (need 1)")
    if required_accessories == 2 and counts.get("scsi_attached", 0) < 1:
        errors.append(f"scsi_attached={counts.get('scsi_attached', 0)} (need 1)")
    return errors


def machine_config(system: str, accessories: int) -> str:
    """Select one or two attached endpoints through the machine config."""
    return f"""<?xml version="1.0"?>
<mameconfig version="10"><system name="{system}"><input>
<port tag=":MAGICBUS_ACCESSORY" type="CONFIG"
      mask="3" defvalue="1" value="{accessories}" />
</input></system></mameconfig>
"""


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
        help=(
            "emulated frames to watch; key injection waits for the real "
            "keyboard client to attach"
        ),
    )
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="require discovery, keyboard delivery, and zero bus errors",
    )
    parser.add_argument(
        "--two-accessories",
        action="store_true",
        help="enumerate an AT keyboard and independently addressed SCSI target",
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
    accessories = 2 if args.two_accessories else 1
    lua_path.write_text(automation_script(args.frames, accessories), encoding="utf-8")
    (run_dir / "cfg" / f"{args.system}.cfg").write_text(
        machine_config(args.system, accessories), encoding="utf-8"
    )

    completed = subprocess.run(
        [
            str(mame),
            args.system,
            "-rompath",
            str(rompath),
            "-cfg_directory",
            str(run_dir / "cfg"),
            "-nvram_directory",
            str(run_dir / "nvram"),
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

    errors = acceptance_errors(counts, accessories)
    if args.require_clean and errors:
        print(
            "FAIL: incomplete Magic Bus transaction: " + ", ".join(errors),
            file=sys.stderr,
        )
        return 1
    if args.require_clean:
        print(
            f"PASS: {accessories} independently addressed accessor"
            f"{'ies' if accessories != 1 else 'y'} discovered; "
            "AT keyboard data dispatched with no errors"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
