#!/usr/bin/env python3
"""Verify TX39 branch-likely nullification and SYNC in MAME."""

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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "tx39-branch-regression"
BRANCH_BASE = 0x1800
BRANCH_STRIDE = 0x40
RESULT_PATTERN = re.compile(
    rb"BRANCH ([A-Z0-9_]+) RESULT=([0-9A-F]{8}) LINK=([0-9A-F]{8})"
)

# name: (delay/fallthrough result, writes link register)
EXPECTED = {
    "BEQL_T": (0x01, False),
    "BEQL_N": (0x40, False),
    "BNEL_T": (0x01, False),
    "BNEL_N": (0x40, False),
    "BLEZL_T": (0x01, False),
    "BLEZL_N": (0x40, False),
    "BGTZL_T": (0x01, False),
    "BGTZL_N": (0x40, False),
    "BLTZL_T": (0x01, False),
    "BLTZL_N": (0x40, False),
    "BGEZL_T": (0x01, False),
    "BGEZL_N": (0x40, False),
    "BLTZALL_T": (0x01, True),
    "BLTZALL_N": (0x40, True),
    "BGEZALL_T": (0x01, True),
    "BGEZALL_N": (0x40, True),
    # Non-likely link branches also write r31 unconditionally, but retain
    # their ordinary delay slot when not taken.
    "BLTZAL_N": (0x41, True),
    "BGEZAL_N": (0x41, True),
    # The DataRover has no external coprocessors, so all four unbound
    # condition inputs read false.  False-likely branches are therefore
    # taken, while true-likely branches nullify their delay slots.
    "BC0FL": (0x01, False),
    "BC0TL": (0x40, False),
    "BC1FL": (0x01, False),
    "BC1TL": (0x40, False),
    "BC2FL": (0x01, False),
    "BC2TL": (0x40, False),
    "BC3FL": (0x01, False),
    "BC3TL": (0x40, False),
    "SYNC": (0x01, False),
}


def automation_script() -> str:
    """Return injected programs covering every R3900 likely branch."""
    return r"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0
local modes = {
    { name = "BEQL_T",     opcode = 0x50220002, r1 =  5, r2 = 5 },
    { name = "BEQL_N",     opcode = 0x50220002, r1 =  5, r2 = 6 },
    { name = "BNEL_T",     opcode = 0x54220002, r1 =  5, r2 = 6 },
    { name = "BNEL_N",     opcode = 0x54220002, r1 =  5, r2 = 5 },
    { name = "BLEZL_T",    opcode = 0x58200002, r1 =  0, r2 = 0 },
    { name = "BLEZL_N",    opcode = 0x58200002, r1 =  1, r2 = 0 },
    { name = "BGTZL_T",    opcode = 0x5c200002, r1 =  1, r2 = 0 },
    { name = "BGTZL_N",    opcode = 0x5c200002, r1 =  0, r2 = 0 },
    { name = "BLTZL_T",    opcode = 0x04220002, r1 = -1, r2 = 0 },
    { name = "BLTZL_N",    opcode = 0x04220002, r1 =  0, r2 = 0 },
    { name = "BGEZL_T",    opcode = 0x04230002, r1 =  0, r2 = 0 },
    { name = "BGEZL_N",    opcode = 0x04230002, r1 = -1, r2 = 0 },
    { name = "BLTZALL_T",  opcode = 0x04320002, r1 = -1, r2 = 0 },
    { name = "BLTZALL_N",  opcode = 0x04320002, r1 =  0, r2 = 0 },
    { name = "BGEZALL_T",  opcode = 0x04330002, r1 =  0, r2 = 0 },
    { name = "BGEZALL_N",  opcode = 0x04330002, r1 = -1, r2 = 0 },
    { name = "BLTZAL_N",   opcode = 0x04300002, r1 =  0, r2 = 0 },
    { name = "BGEZAL_N",   opcode = 0x04310002, r1 = -1, r2 = 0 },
    { name = "BC0FL",      opcode = 0x41020002, r1 =  0, r2 = 0 },
    { name = "BC0TL",      opcode = 0x41030002, r1 =  0, r2 = 0 },
    { name = "BC1FL",      opcode = 0x45020002, r1 =  0, r2 = 0, sr = 0x20000000 },
    { name = "BC1TL",      opcode = 0x45030002, r1 =  0, r2 = 0, sr = 0x20000000 },
    { name = "BC2FL",      opcode = 0x49020002, r1 =  0, r2 = 0, sr = 0x40000000 },
    { name = "BC2TL",      opcode = 0x49030002, r1 =  0, r2 = 0, sr = 0x40000000 },
    { name = "BC3FL",      opcode = 0x4d020002, r1 =  0, r2 = 0, sr = 0x80000000 },
    { name = "BC3TL",      opcode = 0x4d030002, r1 =  0, r2 = 0, sr = 0x80000000 },
    { name = "SYNC",       opcode = 0x0000000f, r1 =  0, r2 = 0 },
}

local function run_mode(index)
    local address = 0x00001800 + (index - 1) * 0x40
    local mode = modes[index]

    program:write_u32(address + 0x00, 0x24100000) -- addiu s0,zero,0
    if mode.name == "SYNC" then
        program:write_u32(address + 0x04, mode.opcode)
        program:write_u32(address + 0x08, 0x26100001) -- addiu s0,s0,1
        program:write_u32(address + 0x0c, 0x1000ffff) -- b .
        program:write_u32(address + 0x10, 0x00000000) -- nop
    else
        program:write_u32(address + 0x04, mode.opcode)
        program:write_u32(address + 0x08, 0x26100001) -- delay: +1
        program:write_u32(address + 0x0c, 0x26100040) -- fallthrough: +0x40
        program:write_u32(address + 0x10, 0x1000ffff) -- target: b .
        program:write_u32(address + 0x14, 0x00000000) -- nop
    end

    cpu.state["R1"].value = mode.r1 & 0xffffffff
    cpu.state["R2"].value = mode.r2 & 0xffffffff
    cpu.state["R31"].value = 0
    cpu.state["SR"].value = mode.sr or 0
    cpu.state["PC"].value = 0xa0000000 | address
end

local function report_mode(index)
    print(string.format(
        "BRANCH %s RESULT=%08X LINK=%08X",
        modes[index].name,
        cpu.state["R16"].value,
        cpu.state["R31"].value))
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 10 then
        run_mode(1)
    elseif frames > 10 and frames <= 10 + #modes then
        local index = frames - 10
        report_mode(index)
        if index < #modes then
            run_mode(index + 1)
        else
            machine:exit()
        end
    end
end)
"""


def parse_results(output: bytes) -> dict[str, tuple[int, int]]:
    """Return result and link observations for every reported mode."""
    return {
        match.group(1).decode("ascii"): (
            int(match.group(2), 16),
            int(match.group(3), 16),
        )
        for match in RESULT_PATTERN.finditer(output)
    }


def verify_results(results: dict[str, tuple[int, int]]) -> list[str]:
    """Check delay-slot execution/nullification and unconditional links."""
    missing = [name for name in EXPECTED if name not in results]
    if missing:
        return [f"missing branch modes: {', '.join(missing)}"]

    failures: list[str] = []
    for index, (name, (expected_result, links)) in enumerate(
        EXPECTED.items(), start=1
    ):
        result, link = results[name]
        if result != expected_result:
            failures.append(
                f"{name} result {result:#x} does not match "
                f"{expected_result:#x}"
            )

        expected_link = (
            0xA000_0000
            | (BRANCH_BASE + (index - 1) * BRANCH_STRIDE + 0x0C)
            if links
            else 0
        )
        if link != expected_link:
            failures.append(
                f"{name} link {link:#010x} does not match "
                f"{expected_link:#010x}"
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
    lua_path = run_dir / "tx39-branch-regression.lua"
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
        print(f"error: unable to run TX39 branch regression: {error}", file=sys.stderr)
        return 2

    log_path.write_bytes(completed.stdout)
    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; see "
            f"{log_path}",
            file=sys.stderr,
        )
        return 2

    failures = verify_results(parse_results(completed.stdout))
    if failures:
        print(f"FAIL: {'; '.join(failures)}; see {log_path}", file=sys.stderr)
        return 1

    print(
        "PASS: all eight TX39 integer and eight coprocessor branch-likely "
        "forms execute or nullify their delay slots correctly, link branches "
        "write r31 unconditionally, and SYNC is accepted"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
