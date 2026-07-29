#!/usr/bin/env python3
"""Exercise the DataRover telephone DAA's digital control boundary.

The Apollo ROM controls the DAA hookswitch through Betty IOData bit 9 and
receives the ring detector on Dino MFIO pin 0.  This short monitor-mode test
checks the hook output, sampled line/ring inputs, and both ring edges without
requiring a configured Magic Cap heap.
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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "telephone-line-regression"

RESULT_PATTERN = re.compile(
    rb"TELEPHONE_LINE "
    rb"CONNECTED=(\d) OFFHOOK=(\d) ONHOOK=(\d) "
    rb"RING_HIGH=(\d) RING_POS=(\d) RING_LOW=(\d) RING_NEG=(\d)"
)
EXPECTED_RESULT = (1, 1, 1, 1, 1, 1, 1)


def config_xml(system: str) -> str:
    return f"""<?xml version="1.0"?>
<mameconfig version="10">
    <system name="{system}">
        <input>
            <port tag=":BOOT_MODE" type="CONFIG"
                  mask="8" defvalue="8" value="0" />
        </input>
    </system>
</mameconfig>
"""


def automation_script() -> str:
    return r"""local machine = manager.machine
local program = machine.devices[":maincpu"].spaces["program"]
local ring = machine.ioport.ports[":PHONE_RING"]:field(0x01)
local frames = 0

local SIB_SF0_AUX = 0x10c00080
local SIB_SF0_STATUS = 0x10c00088
local INT3 = 0x10c00108
local INT4 = 0x10c0010c
local MFIO_INPUT = 0x10c0018c
local PHONE_CONNECTED = 0x0100
local TELECOM_OFFHOOK = 0x0200

local connected = false
local offhook = false
local onhook = false
local ring_high = false
local ring_pos = false
local ring_low = false
local ring_neg = false

local function betty_io()
    -- Betty command: register zero, read.
    program:write_u32(SIB_SF0_AUX, 0x00000000)
    return program:read_u32(SIB_SF0_STATUS) & 0xffff
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 5 then
        connected = (betty_io() & PHONE_CONNECTED) ~= 0

        -- Betty command: register zero, write.  The external connected input
        -- remains sampled while bit 9 drives the DAA hookswitch.
        program:write_u32(SIB_SF0_AUX, 0x04000200)
        local value = program:read_u32(SIB_SF0_STATUS) & 0xffff
        offhook =
            (value & TELECOM_OFFHOOK) ~= 0
            and (value & PHONE_CONNECTED) ~= 0

        program:write_u32(SIB_SF0_AUX, 0x04000000)
        value = program:read_u32(SIB_SF0_STATUS) & 0xffff
        onhook =
            (value & TELECOM_OFFHOOK) == 0
            and (value & PHONE_CONNECTED) ~= 0

        program:write_u32(INT3, 0xffffffff)
        program:write_u32(INT4, 0xffffffff)
        ring:set_value(1)
    elseif frames == 6 then
        ring_high = (program:read_u32(MFIO_INPUT) & 1) ~= 0
        ring_pos = (program:read_u32(INT3) & 1) ~= 0
        program:write_u32(INT3, 1)
        ring:set_value(0)
    elseif frames == 7 then
        ring_low = (program:read_u32(MFIO_INPUT) & 1) == 0
        ring_neg = (program:read_u32(INT4) & 1) ~= 0
        print(string.format(
            "TELEPHONE_LINE CONNECTED=%d OFFHOOK=%d ONHOOK=%d RING_HIGH=%d RING_POS=%d RING_LOW=%d RING_NEG=%d",
            connected and 1 or 0,
            offhook and 1 or 0,
            onhook and 1 or 0,
            ring_high and 1 or 0,
            ring_pos and 1 or 0,
            ring_low and 1 or 0,
            ring_neg and 1 or 0))
        machine:exit()
    end
end)
"""


def parse_result(output: bytes) -> tuple[int, ...] | None:
    match = RESULT_PATTERN.search(output)
    return tuple(int(group) for group in match.groups()) if match else None


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
    nvram_dir = run_dir / "nvram"
    config_dir.mkdir(parents=True)
    nvram_dir.mkdir()
    (config_dir / f"{args.system}.cfg").write_text(
        config_xml(args.system), encoding="utf-8"
    )
    script_path = run_dir / "telephone-line.lua"
    script_path.write_text(automation_script(), encoding="utf-8")

    command = [
        str(args.mame),
        args.system,
        "-rompath",
        str(args.rompath),
        "-cfg_directory",
        str(config_dir),
        "-nvram_directory",
        str(nvram_dir),
        "-autoboot_delay",
        "0",
        "-autoboot_script",
        str(script_path),
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
        command, cwd=args.mame.parent, capture_output=True, timeout=60
    )
    output = completed.stdout + completed.stderr
    log_path = run_dir / "mame-output.txt"
    log_path.write_bytes(output)

    result = parse_result(output)
    if completed.returncode:
        print(
            f"FAIL: MAME exited with status {completed.returncode}; "
            f"see {log_path}",
            file=sys.stderr,
        )
        return 1
    if result != EXPECTED_RESULT:
        print(
            f"FAIL: telephone line result {result!r}, expected "
            f"{EXPECTED_RESULT!r}; see {log_path}",
            file=sys.stderr,
        )
        return 1

    print(
        "PASS: Betty preserves the connected-line input while toggling the "
        "DAA hookswitch, and Dino samples and interrupts on both ring edges"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main() -> int:
    return run_regression(parse_args(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
