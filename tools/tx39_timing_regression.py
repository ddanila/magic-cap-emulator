#!/usr/bin/env python3
"""Verify TX39 GPR interlocks and nonblocking divide timing in MAME."""

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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "tx39-timing-regression"
MODE_CYCLES = {
    "BASE": 3,
    "MULT": 4,
    "MADD": 4,
    "MULT_DEP": 6,
    "LOAD": 4,
    "LOAD_DEP": 6,
    "LOAD_LWL": 5,
    "LOAD_ZERO": 5,
    "DIV": 4,
    "DIV_MFLO": 39,
}
RESULT_PATTERN = re.compile(
    rb"TIMING "
    rb"(BASE|MULT|MADD|MULT_DEP|LOAD|LOAD_DEP|LOAD_LWL|LOAD_ZERO|DIV|DIV_MFLO) "
    rb"COUNT=([0-9A-F]{8})"
)
DIV_RESULT_PATTERN = re.compile(
    rb"TIMING DIV_MFLO COUNT=[0-9A-F]{8} RESULT=([0-9A-F]{8}).*"
    rb"TIMING DIV_CANCEL RESULT=([0-9A-F]{8})",
    re.DOTALL,
)
EXPECTED_DIV_RESULTS = (14, 0x1234)


def automation_script() -> str:
    """Return fixed-time loops for the documented R3900 pipeline cases."""
    return r"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0
local modes = {
    {
        name = "BASE",
        body = {
            0x26100001, -- addiu s0,s0,1
            0x1000fffe, -- b loop
            0x00000000, -- nop
        },
    },
    {
        name = "MULT",
        body = {
            0x26100001, -- addiu s0,s0,1
            0x012a4018, -- mult t0,t1,t2
            0x1000fffd, -- b loop
            0x00000000, -- nop
        },
    },
    {
        name = "MADD",
        body = {
            0x26100001, -- addiu s0,s0,1
            0x712a4000, -- madd t0,t1,t2
            0x1000fffd, -- b loop
            0x00000000, -- nop
        },
    },
    {
        name = "MULT_DEP",
        body = {
            0x26100001, -- addiu s0,s0,1
            0x012a4018, -- mult t0,t1,t2
            0x01005821, -- addu t3,t0,zero
            0x1000fffc, -- b loop
            0x00000000, -- nop
        },
    },
    {
        name = "LOAD",
        body = {
            0x26100001, -- addiu s0,s0,1
            0x8d880000, -- lw t0,0(t4)
            0x1000fffd, -- b loop
            0x00000000, -- nop
        },
    },
    {
        name = "LOAD_DEP",
        body = {
            0x26100001, -- addiu s0,s0,1
            0x8d880000, -- lw t0,0(t4)
            0x01005821, -- addu t3,t0,zero
            0x1000fffc, -- b loop
            0x00000000, -- nop
        },
    },
    {
        name = "LOAD_LWL",
        body = {
            0x8d880000, -- lw t0,0(t4)
            0x89880001, -- lwl t0,1(t4): target-register bypass
            0x26100001, -- addiu s0,s0,1
            0x1000fffc, -- b loop
            0x00000000, -- nop
        },
    },
    {
        name = "LOAD_ZERO",
        body = {
            0x8d800000, -- lw zero,0(t4)
            0x00005821, -- addu t3,zero,zero
            0x26100001, -- addiu s0,s0,1
            0x1000fffc, -- b loop
            0x00000000, -- nop
        },
    },
    {
        name = "DIV",
        body = {
            0x26100001, -- addiu s0,s0,1
            0x012a001a, -- div t1,t2
            0x1000fffd, -- b loop
            0x00000000, -- nop
        },
    },
    {
        name = "DIV_MFLO",
        body = {
            0x012a001a, -- div t1,t2
            0x00004012, -- mflo t0
            0x26100001, -- addiu s0,s0,1
            0x1000fffc, -- b loop
            0x00000000, -- nop
        },
    },
}

local function run_mode(index)
    local address = 0x00001800 + (index - 1) * 0x80

    -- Cancel a divide left by the preceding mode, initialize operands and
    -- clear the loop counter.  The timed loop begins at offset 0x10.
    program:write_u32(address + 0x00, 0x00000011) -- mthi zero
    program:write_u32(address + 0x04, 0x24090064) -- addiu t1,zero,100
    program:write_u32(address + 0x08, 0x240a0007) -- addiu t2,zero,7
    program:write_u32(address + 0x0c, 0x24100000) -- addiu s0,zero,0
    for word, opcode in ipairs(modes[index].body) do
        program:write_u32(address + 0x0c + word * 4, opcode)
    end

    cpu.state["SR"].value = 0
    cpu.state["R12"].value = 0xa0002000
    cpu.state["PC"].value = 0xa0000000 | address
end

local function report_mode(index)
    if modes[index].name == "DIV_MFLO" then
        print(string.format(
            "TIMING %s COUNT=%08X RESULT=%08X",
            modes[index].name,
            cpu.state["R16"].value,
            cpu.state["R8"].value))
    else
        print(string.format(
            "TIMING %s COUNT=%08X",
            modes[index].name,
            cpu.state["R16"].value))
    end
end

local function run_cancel_case()
    local address = 0x00001b00
    local code = {
        0x00000011, -- mthi zero
        0x24090064, -- addiu t1,zero,100
        0x240a0007, -- addiu t2,zero,7
        0x012a001a, -- div t1,t2
        0x240c1234, -- addiu t4,zero,0x1234
        0x01800011, -- mthi t4: cancel the divide
        0x00006810, -- mfhi t5
        0x1000ffff, -- b .
        0x00000000, -- nop
    }
    for word, opcode in ipairs(code) do
        program:write_u32(address - 4 + word * 4, opcode)
    end
    cpu.state["SR"].value = 0
    cpu.state["PC"].value = 0xa0000000 | address
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 10 then
        program:write_u32(0x00002000, 0x12345678)
        run_mode(1)
    elseif frames > 10 and frames <= 10 + #modes then
        local index = frames - 10
        report_mode(index)
        if index < #modes then
            run_mode(index + 1)
        else
            run_cancel_case()
        end
    elseif frames == 11 + #modes then
        print(string.format(
            "TIMING DIV_CANCEL RESULT=%08X",
            cpu.state["R13"].value))
        machine:exit()
    end
end)
"""


def parse_results(output: bytes) -> dict[str, int]:
    """Return the last counter reported for each timing mode."""
    return {
        match.group(1).decode("ascii"): int(match.group(2), 16)
        for match in RESULT_PATTERN.finditer(output)
    }


def parse_div_results(output: bytes) -> tuple[int, int] | None:
    """Return the completed divide and cancelled-divide observations."""
    match = DIV_RESULT_PATTERN.search(output)
    if not match:
        return None
    return tuple(int(value, 16) for value in match.groups())


def verify_results(
    results: dict[str, int],
    div_results: tuple[int, int] | None = EXPECTED_DIV_RESULTS,
) -> list[str]:
    """Compare each loop's cycle-normalized work with the baseline."""
    missing = [mode for mode in MODE_CYCLES if mode not in results]
    if missing:
        return [f"missing timing modes: {', '.join(missing)}"]

    if any(results[mode] == 0 for mode in MODE_CYCLES):
        return [f"one or more loop counts are zero: {results!r}"]

    baseline = results["BASE"] * MODE_CYCLES["BASE"]
    failures: list[str] = []
    for mode, cycles in MODE_CYCLES.items():
        normalized = results[mode] * cycles
        if not 0.97 * baseline <= normalized <= 1.03 * baseline:
            failures.append(
                f"{mode} normalized count {normalized} is not within 3% "
                f"of baseline {baseline}"
            )
    if div_results != EXPECTED_DIV_RESULTS:
        failures.append(
            f"divide results {div_results!r} do not match "
            f"{EXPECTED_DIV_RESULTS!r}"
        )
    return failures


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
    lua_path = run_dir / "tx39-timing-regression.lua"
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
        print(f"error: unable to run TX39 timing regression: {error}", file=sys.stderr)
        return 2

    log_path.write_bytes(completed.stdout)
    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; see {log_path}",
            file=sys.stderr,
        )
        return 2

    failures = verify_results(
        parse_results(completed.stdout), parse_div_results(completed.stdout)
    )
    if failures:
        print(f"FAIL: {'; '.join(failures)}; see {log_path}", file=sys.stderr)
        return 1

    print(
        "PASS: TX39 MULT/MADD and loads accept independent instructions each "
        "cycle, dependent GPR reads stall once, LWL bypasses its target, and "
        "DIV overlaps execution while MFLO retains its 35-cycle interlock"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
