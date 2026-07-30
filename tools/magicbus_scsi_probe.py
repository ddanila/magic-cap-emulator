#!/usr/bin/env python3
"""Execute FastChecksum through both directions of the monitor SCTG transport."""

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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "magicbus-scsi-probe"
SUPPORTED_SYSTEMS = ("datarover840", "datarover840f", "datarover840j")
SCSI_TARGET_PERIPH = 0x0000_C1E8
NUMBER_MAGICBUS_PERIPHS = 0x0000_C168
SCTG_COMMAND = 0x80
SCTG_FAST_CHECKSUM = 0x9235_0908
SCRATCH = 0x0030_0100
WATCHED = (
    ("init", 0x13C0_5620, "InitMagicBus"),
    ("init_done", 0x13C0_568C, "InitMagicBus return"),
    ("check", 0x13C0_5A64, "CheckMagicBus"),
    ("get_data", 0x13C0_5EF4, "GetDataFunction"),
    ("get_done", 0x13C0_6108, "GetDataFunction return"),
    ("send_dispatch", 0x13C0_5C3C, "SendDataFunction dispatch"),
    ("send_data", 0x13C0_6148, "SendDataFunction"),
    ("command", 0x13C0_5300, "magicbus_cmd"),
    ("open", 0x13C0_541C, "scsiopen"),
    ("assign", 0x13C0_5694, "AssignMagicBusAddresses"),
    ("transaction", 0x13C0_65E0, "MagicBusCommand"),
)
GET_DONE_SLOT = SCRATCH + 16
SEND_DATA_SLOT = SCRATCH + 24
RESULT = re.compile(
    rb"MAGICBUS SCTG address=(\d+) init=(\d+) init_done=(\d+) check=(\d+) "
    rb"get_data=(\d+) get_done=(\d+) send_dispatch=(\d+) send_data=(\d+) "
    rb"command=(\d+) open=(\d+) assign=(\d+) transaction=(\d+) "
    rb"peripherals=(\d+)"
)
HOST_DATA = re.compile(
    rb"Magic Bus SCTG accepted (\d+) host bytes, "
    rb"command=([0-9a-f]+) status=(\d+) result=([0-9a-f]+)"
)
MONITOR_COMMAND = "magicbus -i\n"
TERMINAL_KEYS = {
    "a": (2, 0x0002),
    "b": (3, 0x0040),
    "c": (3, 0x0010),
    "g": (2, 0x0020),
    "i": (1, 0x0100),
    "m": (3, 0x0100),
    "s": (2, 0x0004),
    "u": (1, 0x0080),
    " ": (3, 0x8000),
    "-": (0, 0x0800),
    "\n": (2, 0x1000),
}


def automation_script(frames: int) -> str:
    """Return Lua that opens and exercises the monitor SCSI transport."""
    setup = "\n".join(
        f'    watch({SCRATCH + index * 4}, 0x{address:08x}, "{name}")'
        for index, (name, address, _symbol) in enumerate(WATCHED)
    )
    counters = ", ".join(
        f"program:read_u32({SCRATCH + index * 4})"
        for index, _entry in enumerate(WATCHED)
    )
    command = "\n".join(
        (
            '    { machine.ioport.ports['
            f'":terminal:keyboard:GENKBD_ROW{row}"]:field(0x{mask:04x}), '
            f"0x{mask:04x} }},"
        )
        for row, mask in (TERMINAL_KEYS[character] for character in MONITOR_COMMAND)
    )
    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local request = machine.ioport.ports[
    ":MAGICBUS_SCSI_REQUEST"].fields["Magic Bus SCTG get-data request"]
local send_request = machine.ioport.ports[
    ":MAGICBUS_SCSI_REQUEST"].fields["Magic Bus SCTG send-data request"]
local frames = 0
local command = {{
{command}
}}
local command_index = 1
local key_down = false
local send_requested = false
local second_command_frame = 0
local send_seen_frame = 0

local function watch(slot, address, name)
    program:write_u32(slot, 0)
    cpu.debug:bpset(address, "1",
        string.format("do d@0x%08x=d@0x%08x+1; g", slot, slot))
end

{setup}

local function report()
    print(string.format(
        "MAGICBUS SCTG address=%d init=%d init_done=%d check=%d get_data=%d "
        .. "get_done=%d send_dispatch=%d send_data=%d command=%d open=%d "
        .. "assign=%d transaction=%d peripherals=%d",
        program:read_u32({SCSI_TARGET_PERIPH}),
        {counters}, program:read_u32({NUMBER_MAGICBUS_PERIPHS})))
end

emu.register_frame_done(function()
    frames = frames + 1

    if frames == 1 then
        -- Hold the get-data request through enumeration.  Discovery consumes
        -- the first request record; a repeated information pass presents it
        -- to the monitor's normal transport dispatcher.
        request:set_value(request.mask)
    end

    if second_command_frame == -1
            and command_index == #command
            and not key_down then
        -- Assert the second request with the newline that invokes the second
        -- command, so the idle monitor cannot consume it beforehand.
        send_request:set_value(send_request.mask)
        second_command_frame = 0
    end

    if command_index <= #command then
        local key = command[command_index]
        if key_down then
            key[1]:set_value(0)
            command_index = command_index + 1
            key_down = false
        else
            key[1]:set_value(key[2])
            key_down = true
        end
    end

    if not send_requested
            and program:read_u32({GET_DONE_SLOT}) > 0 then
        request:set_value(0)
        second_command_frame = frames + 30
        send_requested = true
    elseif second_command_frame > 0 and frames >= second_command_frame then
        -- The monitor command services one transport request.  Run it again
        -- so the second invocation discovers and services the opposite one.
        command_index = 1
        second_command_frame = -1
    end

    if send_seen_frame == 0 and program:read_u32({SEND_DATA_SLOT}) > 0 then
        send_seen_frame = frames
    end

    if send_seen_frame > 0 and frames >= send_seen_frame + 2 then
        request:set_value(0)
        send_request:set_value(0)
        report()
        machine:exit()
    elseif frames == {frames} then
        request:set_value(0)
        send_request:set_value(0)
        report()
        machine:exit()
    end
end)
"""


def machine_config(system: str) -> str:
    """Select monitor boot, its terminal keyboard, and an SCTG endpoint."""
    return f"""<?xml version="1.0"?>
<mameconfig version="10"><system name="{system}"><input>
<keyboard tag=":terminal:keyboard" enabled="1" />
<port tag=":BOOT_MODE" type="CONFIG" mask="8" defvalue="8" value="0" />
<port tag=":MAGICBUS_ACCESSORY" type="CONFIG"
      mask="3" defvalue="1" value="3" />
</input></system></mameconfig>
"""


def parse_result(output: bytes) -> dict[str, int] | None:
    """Parse the monitor transport checkpoint."""
    match = RESULT.search(output)
    if not match:
        return None
    result = dict(
        zip(
            (
                "address",
                "init",
                "init_done",
                "check",
                "get_data",
                "get_done",
                "send_dispatch",
                "send_data",
                "command",
                "open",
                "assign",
                "transaction",
                "peripherals",
            ),
            (int(value) for value in match.groups()),
            strict=True,
        )
    )
    host_data = HOST_DATA.search(output)
    result["host_bytes"] = int(host_data.group(1)) if host_data else 0
    result["host_command"] = int(host_data.group(2), 16) if host_data else -1
    result["host_status"] = int(host_data.group(3)) if host_data else -1
    result["host_result"] = int(host_data.group(4), 16) if host_data else -1
    return result


def acceptance_errors(result: dict[str, int]) -> list[str]:
    """Explain which part of the monitor SCSI path did not execute."""
    errors = []
    if result["address"] != 0:
        errors.append(f"address={result['address']} (need 0)")
    if result["peripherals"] != 1:
        errors.append(f"peripherals={result['peripherals']} (need 1)")
    for name in (
        "init",
        "init_done",
        "check",
        "get_data",
        "get_done",
        "send_dispatch",
        "send_data",
        "command",
        "open",
        "assign",
        "transaction",
    ):
        if result[name] < 1:
            errors.append(f"{name}={result[name]} (need 1)")
    if result["host_bytes"] != 24:
        errors.append(f"host_bytes={result['host_bytes']} (need 24)")
    if result["host_command"] != SCTG_COMMAND:
        errors.append(
            f"host_command={result['host_command']:#04x} "
            f"(need {SCTG_COMMAND:#04x})"
        )
    if result["host_status"] != 0:
        errors.append(f"host_status={result['host_status']} (need 0)")
    if result["host_result"] != SCTG_FAST_CHECKSUM:
        errors.append(
            f"host_result={result['host_result']:#010x} "
            f"(need {SCTG_FAST_CHECKSUM:#010x})"
        )
    return errors


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--system", default="datarover840")
    parser.add_argument("--frames", type=int, default=1800)
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
            f"error: {args.system} does not use the release monitor addresses",
            file=sys.stderr,
        )
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    (run_dir / "cfg").mkdir(parents=True)
    (run_dir / "nvram").mkdir()
    lua_path = run_dir / "magicbus-scsi.lua"
    lua_path.write_text(automation_script(args.frames), encoding="utf-8")
    (run_dir / "cfg" / f"{args.system}.cfg").write_text(
        machine_config(args.system), encoding="utf-8"
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
            "2",
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
            "-oslog",
        ],
        cwd=mame.parent,
        capture_output=True,
        timeout=300,
    )
    output = completed.stdout + completed.stderr
    (run_dir / "mame-output.txt").write_bytes(output)
    result = parse_result(output)
    if result is None:
        print(f"error: no SCSI checkpoint; see {run_dir}", file=sys.stderr)
        return 2

    errors = acceptance_errors(result)
    if errors:
        print("FAIL: " + ", ".join(errors), file=sys.stderr)
        print(f"Artifacts: {run_dir}")
        return 1
    print(
        "PASS: IDT monitor discovered SCTG, executed FastChecksum through "
        "command 3, and returned 0x92350908 through command 7"
    )
    print(f"Artifacts: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
