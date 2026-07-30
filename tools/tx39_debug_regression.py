#!/usr/bin/env python3
"""Verify TX39 self-debug, coincident exceptions, SDBBP, and DERET."""

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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "tx39-debug-regression"
RESULT_PATTERN = re.compile(
    rb"DEBUG_BREAK DEBUG=([0-9A-F]{8}) DEPC=([0-9A-F]{8}).*"
    rb"DEBUG_DELAY DEBUG=([0-9A-F]{8}) DEPC=([0-9A-F]{8}).*"
    rb"DEBUG_DERET SEEN=([0-9A-F]{8}) DEBUG=([0-9A-F]{8}) "
    rb"DEPC=([0-9A-F]{8}) SR=([0-9A-F]{8}).*"
    rb"DEBUG_STEP DEBUG=([0-9A-F]{8}) DEPC=([0-9A-F]{8}) "
    rb"R18=([0-9A-F]{8}).*"
    rb"DEBUG_SUPPRESS SEEN=([0-9A-F]{8}) DEBUG=([0-9A-F]{8}) "
    rb"DEPC=([0-9A-F]{8}) DELAY=([0-9A-F]{8}).*"
    rb"DEBUG_NIS DEBUG=([0-9A-F]{8}) DEPC=([0-9A-F]{8}) "
    rb"EPC=([0-9A-F]{8}) SR=([0-9A-F]{8}).*"
    rb"DEBUG_OES DEBUG=([0-9A-F]{8}) DEPC=([0-9A-F]{8}) "
    rb"EPC=([0-9A-F]{8}) CAUSE=([0-9A-F]{8}) SR=([0-9A-F]{8})",
    re.DOTALL,
)
EXPECTED = (
    0x4000_0002,
    0xA000_1800,
    0xC000_0002,
    0xA000_1840,
    0xC000_0002,
    0x8000_0002,
    0x0000_18C0,
    0x0000_0003,
    0x4000_0101,
    0xA000_1908,
    0x0000_0000,
    0x4000_0101,
    0x4000_0101,
    0x0000_1950,
    0x0000_0001,
    0x4000_4101,
    0xA000_19C0,
    0xA000_19C0,
    0x0010_0000,
    0x4000_1101,
    0xA000_1A40,
    0xA000_1A40,
    0x0000_0400,
    0x0000_0404,
)


def automation_script() -> str:
    """Return injected programs for breakpoint, return, and step behavior."""
    return r"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0
local NIS_SNAPSHOT = 0x00001b00
local OES_SNAPSHOT = 0x00001b20

cpu.debug:bpset(
    0xbfc00200, "(Debug&0x00004000)!=0",
    "do d@0x00001b00=Debug; do d@0x00001b04=DEPC; " ..
    "do d@0x00001b08=EPC; do d@0x00001b0c=SR; g")
cpu.debug:bpset(
    0xbfc00200, "(Debug&0x00001000)!=0",
    "do d@0x00001b20=Debug; do d@0x00001b24=DEPC; " ..
    "do d@0x00001b28=EPC; do d@0x00001b2c=Cause; " ..
    "do d@0x00001b30=SR; g")
cpu.debug:go()

local function clear_debug()
    cpu.state["Debug"].value = 0
    cpu.state["DEPC"].value = 0
    cpu.state["SR"].value = 0
end

local function run_break()
    program:write_u32(0x00001800, 0x0048d14e) -- sdbbp 0x12345
    clear_debug()
    cpu.state["PC"].value = 0xa0001800
end

local function run_delay_break()
    program:write_u32(0x00001840, 0x10000002) -- b 0x184c
    program:write_u32(0x00001844, 0x0000000e) -- sdbbp 0 (delay)
    program:write_u32(0x00001848, 0x00000000)
    program:write_u32(0x0000184c, 0x1000ffff)
    program:write_u32(0x00001850, 0x00000000)
    clear_debug()
    cpu.state["PC"].value = 0xa0001840
end

local function run_deret()
    program:write_u32(0x00001880, 0x40108000) -- mfc0 s0,Debug
    program:write_u32(0x00001884, 0x40918800) -- mtc0 s1,DEPC
    program:write_u32(0x00001888, 0x4200001f) -- deret
    program:write_u32(0x000018c0, 0x1000ffff) -- b .
    program:write_u32(0x000018c4, 0x00000000)
    cpu.state["R16"].value = 0
    cpu.state["R17"].value = 0x000018c0
    cpu.state["SR"].value = 0
    cpu.state["PC"].value = 0xa0001880
end

local function run_single_step()
    program:write_u32(0x00001900, 0x24110100) -- addiu s1,zero,0x100
    program:write_u32(0x00001904, 0x40918000) -- mtc0 s1,Debug
    program:write_u32(0x00001908, 0x26520001) -- addiu s2,s2,1
    program:write_u32(0x0000190c, 0x1000ffff)
    program:write_u32(0x00001910, 0x00000000)
    clear_debug()
    cpu.state["R18"].value = 0
    cpu.state["PC"].value = 0xa0001900
end

local function run_deret_to_branch()
    program:write_u32(0x00001980, 0x40108000) -- mfc0 s0,Debug
    program:write_u32(0x00001984, 0xac101a00) -- sw s0,0x1a00(zero)
    program:write_u32(0x00001988, 0x40918800) -- mtc0 s1,DEPC
    program:write_u32(0x0000198c, 0x4200001f) -- deret
    program:write_u32(0x00001940, 0x10000003) -- b 0x1950
    program:write_u32(0x00001944, 0xac141a04) -- delay: sw s4,0x1a04(zero)
    program:write_u32(0x00001948, 0xac151a04) -- fallthrough (not executed)
    program:write_u32(0x00001950, 0xac151a04) -- step must stop before this
    program:write_u32(0x00001954, 0x1000ffff)
    program:write_u32(0x00001958, 0x00000000)
    cpu.state["R17"].value = 0x00001940
    cpu.state["R20"].value = 1
    cpu.state["R21"].value = 2
    program:write_u32(0x00001a00, 0)
    program:write_u32(0x00001a04, 0)
    cpu.state["SR"].value = 0
    cpu.state["PC"].value = 0xa0001980
end

local function run_nmi_coincidence()
    clear_debug()
    for address = NIS_SNAPSHOT, NIS_SNAPSHOT + 0x0c, 4 do
        program:write_u32(address, 0)
    end
    cpu.state["Cause"].value = 0
    cpu.state["EPC"].value = 0
    cpu.state["Debug"].value = 0x00000100 -- SSt
    cpu.state["NMI"].value = 1
    cpu.state["PC"].value = 0xa00019c0
end

local function run_interrupt_coincidence()
    clear_debug()
    for address = OES_SNAPSHOT, OES_SNAPSHOT + 0x10, 4 do
        program:write_u32(address, 0)
    end
    cpu.state["NMI"].value = 0
    cpu.state["Cause"].value = 0x00000400 -- Int0 pending
    cpu.state["EPC"].value = 0
    cpu.state["SR"].value = 0x00000401 -- Int0 enabled, IEc
    cpu.state["Debug"].value = 0x00000100 -- SSt
    cpu.state["PC"].value = 0xa0001a40
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 10 then
        run_break()
    elseif frames == 11 then
        print(string.format(
            "DEBUG_BREAK DEBUG=%08X DEPC=%08X",
            cpu.state["Debug"].value, cpu.state["DEPC"].value))
        run_delay_break()
    elseif frames == 12 then
        print(string.format(
            "DEBUG_DELAY DEBUG=%08X DEPC=%08X",
            cpu.state["Debug"].value, cpu.state["DEPC"].value))
        run_deret()
    elseif frames == 13 then
        print(string.format(
            "DEBUG_DERET SEEN=%08X DEBUG=%08X DEPC=%08X SR=%08X",
            cpu.state["R16"].value, cpu.state["Debug"].value,
            cpu.state["DEPC"].value, cpu.state["SR"].value))
        run_single_step()
    elseif frames == 14 then
        print(string.format(
            "DEBUG_STEP DEBUG=%08X DEPC=%08X R18=%08X",
            cpu.state["Debug"].value, cpu.state["DEPC"].value,
            cpu.state["R18"].value))
        run_deret_to_branch()
    elseif frames == 15 then
        print(string.format(
            "DEBUG_SUPPRESS SEEN=%08X DEBUG=%08X DEPC=%08X DELAY=%08X",
            program:read_u32(0x00001a00), cpu.state["Debug"].value,
            cpu.state["DEPC"].value,
            program:read_u32(0x00001a04)))
        run_nmi_coincidence()
    elseif frames == 16 then
        print(string.format(
            "DEBUG_NIS DEBUG=%08X DEPC=%08X EPC=%08X SR=%08X",
            program:read_u32(NIS_SNAPSHOT + 0x00),
            program:read_u32(NIS_SNAPSHOT + 0x04),
            program:read_u32(NIS_SNAPSHOT + 0x08),
            program:read_u32(NIS_SNAPSHOT + 0x0c)))
        run_interrupt_coincidence()
    elseif frames == 17 then
        print(string.format(
            "DEBUG_OES DEBUG=%08X DEPC=%08X EPC=%08X CAUSE=%08X SR=%08X",
            program:read_u32(OES_SNAPSHOT + 0x00),
            program:read_u32(OES_SNAPSHOT + 0x04),
            program:read_u32(OES_SNAPSHOT + 0x08),
            program:read_u32(OES_SNAPSHOT + 0x0c),
            program:read_u32(OES_SNAPSHOT + 0x10)))
        machine:exit()
    end
end)
"""


def parse_result(output: bytes) -> tuple[int, ...] | None:
    """Return all reported debug-state observations."""
    match = RESULT_PATTERN.search(output)
    if not match:
        return None
    return tuple(int(value, 16) for value in match.groups())


def verify_result(result: tuple[int, ...] | None) -> list[str]:
    """Compare debug state with the Toshiba architectural contract."""
    if result is None:
        return ["missing TX39 debug result"]
    return [
        f"field {index} {actual:#010x} does not match {expected:#010x}"
        for index, (actual, expected) in enumerate(zip(result, EXPECTED))
        if actual != expected
    ]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    workdir = args.workdir.expanduser().resolve()
    if not mame.is_file():
        print(f"error: MAME executable not found: {mame}", file=sys.stderr)
        return 2
    if not rompath.is_dir():
        print(f"error: ROM path not found: {rompath}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = workdir / f"{stamp}-{os.getpid()}"
    nvram_dir = run_dir / "nvram"
    nvram_dir.mkdir(parents=True)
    lua_path = run_dir / "tx39-debug-regression.lua"
    log_path = run_dir / "mame-output.txt"
    lua_path.write_text(automation_script(), encoding="utf-8")

    command = [
        str(mame),
        "datarover840",
        "-rompath",
        str(rompath),
        "-nvram_directory",
        str(nvram_dir),
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
        "-debug",
        "-debugger",
        "none",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=run_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"error: unable to run TX39 debug regression: {error}", file=sys.stderr)
        return 2

    log_path.write_bytes(completed.stdout)
    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; "
            f"see {log_path}",
            file=sys.stderr,
        )
        return 2

    failures = verify_result(parse_result(completed.stdout))
    if failures:
        print(f"FAIL: {'; '.join(failures)}; see {log_path}", file=sys.stderr)
        return 1

    print(
        "PASS: TX39 SDBBP records breakpoint and delay-slot state, DERET "
        "returns through DEPC, single-step honors its return/branch-delay "
        "suppression contract, and coincident NMI/interrupt state reaches "
        "NIS/OES with the ordinary exception registers intact"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
