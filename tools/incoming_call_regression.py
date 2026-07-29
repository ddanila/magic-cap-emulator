#!/usr/bin/env python3
"""Verify Magic Cap's product-level incoming telephone-call path."""

from __future__ import annotations

import argparse
import os
import re
import shutil
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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "incoming-call-regression"
COUNTERS = 0x0031_1000

# Shipping DataRover 840 ROM entry points, recovered against the SDK ELF.
SYMBOLS = (
    (0x13C4_11A0, "ring_isr"),
    (0x13C4_1160, "ring_completion"),
    (0x13C4_187C, "continue_ring"),
    (0x13C3_F478, "trigger_clients"),
    (0x13C4_1D28, "phone_server"),
    (0x13E8_F2B8, "fax_receive"),
)
RESULT_PATTERN = re.compile(
    rb"INCOMING_CALL_RESULT "
    + rb" ".join(name.encode() + rb"=(\d+)" for _, name in SYMBOLS)
)


def automation_script(result_frame: int = 1600) -> str:
    """Hold the ring-envelope input and trace the ROM's detector path."""
    addresses = ",\n    ".join(
        f"0x{address:08x}" for address, _ in SYMBOLS
    )
    reads = ",\n            ".join(
        f"program:read_u32(COUNTERS + {index * 4})"
        for index in range(len(SYMBOLS))
    )
    fields = " ".join(f"{name}=%d" for _, name in SYMBOLS)
    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local ring = machine.ioport.ports[":PHONE_RING"]:field(0x01)
local frames = 0
local COUNTERS = 0x{COUNTERS:08x}
local addresses = {{
    {addresses}
}}

for index, address in ipairs(addresses) do
    local counter = COUNTERS + (index - 1) * 4
    program:write_u32(counter, 0)
    cpu.debug:bpset(
        address,
        "",
        string.format(
            "do d@0x%08x=d@0x%08x+1; g", counter, counter))
end
cpu.debug:go()

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 1100 then
        machine.screens[":screen"]:snapshot("before-ring.png")
    elseif frames == 1200 then
        -- This is the ringing envelope, not a manually synthesized carrier.
        ring:set_value(1)
    elseif frames == 1320 then
        ring:set_value(0)
    elseif frames == 1500 then
        machine.screens[":screen"]:snapshot("incoming-call.png")
    elseif frames == {result_frame} then
        print(string.format(
            "INCOMING_CALL_RESULT {fields}",
            {reads}))
        machine:exit()
    end
end)
"""


def deterministic_machine_config() -> str:
    return """<?xml version="1.0"?>
<mameconfig version="10">
    <system name="datarover840">
        <input>
            <port tag=":PHONE_LINE" type="CONFIG"
                  mask="1" defvalue="1" value="1" />
            <port tag=":PHONE_PEER" type="CONFIG"
                  mask="3" defvalue="1" value="0" />
        </input>
    </system>
</mameconfig>
"""


def parse_result(output: bytes) -> dict[str, int] | None:
    match = RESULT_PATTERN.search(output)
    if match is None:
        return None
    return {
        name: int(value)
        for (_, name), value in zip(SYMBOLS, match.groups(), strict=True)
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument(
        "--nvram-source",
        type=Path,
        required=True,
        help=(
            "calibrated datarover840 NVRAM directory to copy and test; "
            "the source is never modified"
        ),
    )
    parser.add_argument("--system", default="datarover840")
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    source = args.nvram_source.expanduser().resolve()
    if not mame.is_file():
        print(f"error: MAME executable not found: {mame}", file=sys.stderr)
        return 2
    if not rompath.is_dir():
        print(f"error: ROM path not found: {rompath}", file=sys.stderr)
        return 2
    if args.system != "datarover840":
        print(
            "error: ROM symbol addresses are specific to datarover840",
            file=sys.stderr,
        )
        return 2
    if not (source / args.system / "ram").is_file():
        print(
            f"error: calibrated NVRAM not found under {source}",
            file=sys.stderr,
        )
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = (
        args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    )
    run_dir.mkdir(parents=True)
    cfg_dir = run_dir / "cfg"
    nvram_dir = run_dir / "nvram"
    snapshot_dir = run_dir / "snapshots"
    cfg_dir.mkdir()
    snapshot_dir.mkdir()
    shutil.copytree(source, nvram_dir)
    (cfg_dir / f"{args.system}.cfg").write_text(
        deterministic_machine_config(), encoding="utf-8"
    )
    lua_path = run_dir / "incoming-call.lua"
    lua_path.write_text(automation_script(), encoding="utf-8")
    output_path = run_dir / "mame-output.txt"

    command = [
        str(mame),
        args.system,
        "-rompath",
        str(rompath),
        "-cfg_directory",
        str(cfg_dir),
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
        "-oslog",
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
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(
            f"error: MAME failed: {error}; artifacts: {run_dir}",
            file=sys.stderr,
        )
        return 2
    output_path.write_bytes(completed.stdout)

    result = parse_result(completed.stdout)
    before = snapshot_dir / "before-ring.png"
    incoming = snapshot_dir / "incoming-call.png"
    ui_changed = (
        before.is_file()
        and incoming.is_file()
        and before.read_bytes() != incoming.read_bytes()
    )
    missing = (
        list(name for _, name in SYMBOLS)
        if result is None
        else [name for _, name in SYMBOLS if result[name] == 0]
    )
    if completed.returncode or missing or not ui_changed:
        print(
            "FAIL: incoming-call path incomplete "
            f"(result={result!r}, missing={missing}, ui_changed={ui_changed}); "
            f"see {output_path}",
            file=sys.stderr,
        )
        return 1

    print(
        "PASS: the ring envelope generated detector edges, Magic Cap "
        "completed ring qualification, notified PhoneServer and FaxReceive, "
        "and opened the incoming Phone Status window"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
