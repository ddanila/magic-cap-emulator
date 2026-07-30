#!/usr/bin/env python3
"""Verify TX39 Config-selected instruction and data cache refill."""

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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "tx39-refill-regression"
RESULT_PATTERN = re.compile(
    rb"DATA_SINGLE ADJACENT=([0-9A-F]{8}).*"
    rb"DATA_BURST ADJACENT=([0-9A-F]{8}).*"
    rb"DATA_LOCK ADJACENT=([0-9A-F]{8}).*"
    rb"ICACHE_PREFETCH TARGET=([0-9A-F]{8})",
    re.DOTALL,
)
EXPECTED = (
    0xBBBBBBBB,
    0x22222222,
    0x22222222,
    0x00001234,
)


def automation_script() -> str:
    """Return injected MIPS tests for one-word, burst, lock, and I-cache refill."""
    return r"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0

local function run_uncached(address)
    cpu.state["SR"].value = 0
    cpu.state["PC"].value = 0xa0000000 | address
end

local function run_cached(address)
    cpu.state["SR"].value = 0
    cpu.state["PC"].value = 0x80000000 | address
end

emu.register_frame_done(function()
    frames = frames + 1

    if frames == 10 then
        -- DCBR clear: loading A must not prefetch adjacent B.
        program:write_u32(0x00004000, 0x11111111)
        program:write_u32(0x00004004, 0x22222222)
        program:write_u32(0x00001400, 0x24080030)
        program:write_u32(0x00001404, 0x40881800)
        program:write_u32(0x00001408, 0x3c098000)
        program:write_u32(0x0000140c, 0x35294000)
        program:write_u32(0x00001410, 0xbd310000)
        program:write_u32(0x00001414, 0x25290004)
        program:write_u32(0x00001418, 0xbd310000)
        program:write_u32(0x0000141c, 0x2529fffc)
        program:write_u32(0x00001420, 0x8d280000)
        program:write_u32(0x00001424, 0x1000ffff)
        program:write_u32(0x00001428, 0x00000000)
        run_uncached(0x00001400)
    elseif frames == 11 then
        program:write_u32(0x00004004, 0xbbbbbbbb)
        program:write_u32(0x00001440, 0x3c098000)
        program:write_u32(0x00001444, 0x35294004)
        program:write_u32(0x00001448, 0x8d290000)
        program:write_u32(0x0000144c, 0x1000ffff)
        program:write_u32(0x00001450, 0x00000000)
        run_uncached(0x00001440)
    elseif frames == 12 then
        print(string.format(
            "DATA_SINGLE ADJACENT=%08X",
            cpu.state["R9"].value))

        -- DCBR plus DRSize zero: one miss refills four consecutive words.
        program:write_u32(0x00004100, 0x11111111)
        program:write_u32(0x00004104, 0x22222222)
        program:write_u32(0x00004108, 0x33333333)
        program:write_u32(0x0000410c, 0x44444444)
        program:write_u32(0x00001480, 0x24080070)
        program:write_u32(0x00001484, 0x40881800)
        program:write_u32(0x00001488, 0x3c098000)
        program:write_u32(0x0000148c, 0x35294100)
        program:write_u32(0x00001490, 0xbd310000)
        program:write_u32(0x00001494, 0x25290004)
        program:write_u32(0x00001498, 0xbd310000)
        program:write_u32(0x0000149c, 0x25290004)
        program:write_u32(0x000014a0, 0xbd310000)
        program:write_u32(0x000014a4, 0x25290004)
        program:write_u32(0x000014a8, 0xbd310000)
        program:write_u32(0x000014ac, 0x2529fff4)
        program:write_u32(0x000014b0, 0x8d2a0000)
        program:write_u32(0x000014b4, 0x1000ffff)
        program:write_u32(0x000014b8, 0x00000000)
        run_uncached(0x00001480)
    elseif frames == 13 then
        program:write_u32(0x00004104, 0xbbbbbbbb)
        program:write_u32(0x000014c0, 0x3c098000)
        program:write_u32(0x000014c4, 0x35294104)
        program:write_u32(0x000014c8, 0x8d2b0000)
        program:write_u32(0x000014cc, 0x1000ffff)
        program:write_u32(0x000014d0, 0x00000000)
        run_uncached(0x000014c0)
    elseif frames == 14 then
        print(string.format(
            "DATA_BURST ADJACENT=%08X",
            cpu.state["R11"].value))

        -- DALc applies to every line filled by the four-word data burst.
        program:write_u32(0x00004200, 0x11111111)
        program:write_u32(0x00004204, 0x22222222)
        program:write_u32(0x00004208, 0x33333333)
        program:write_u32(0x0000420c, 0x44444444)
        program:write_u32(0x00004404, 0x55555555)
        program:write_u32(0x00004604, 0x66666666)
        program:write_u32(0x00001500, 0x24080070)
        program:write_u32(0x00001504, 0x40881800)
        program:write_u32(0x00001508, 0x40803800)
        program:write_u32(0x0000150c, 0x3c098000)
        program:write_u32(0x00001510, 0x35294200)
        program:write_u32(0x00001514, 0xbd310000)
        program:write_u32(0x00001518, 0x25290004)
        program:write_u32(0x0000151c, 0xbd310000)
        program:write_u32(0x00001520, 0x25290200)
        program:write_u32(0x00001524, 0xbd310000)
        program:write_u32(0x00001528, 0x25290200)
        program:write_u32(0x0000152c, 0xbd310000)
        program:write_u32(0x00001530, 0x2529fbfc)
        program:write_u32(0x00001534, 0x24080100)
        program:write_u32(0x00001538, 0x40883800)
        program:write_u32(0x0000153c, 0x8d2c0000)
        program:write_u32(0x00001540, 0x40803800)
        program:write_u32(0x00001544, 0x1000ffff)
        program:write_u32(0x00001548, 0x00000000)
        run_uncached(0x00001500)
    elseif frames == 15 then
        program:write_u32(0x00004204, 0xaaaaaaaa)
        program:write_u32(0x00001580, 0x3c098000)
        program:write_u32(0x00001584, 0x35294404)
        program:write_u32(0x00001588, 0x8d280000)
        program:write_u32(0x0000158c, 0x25290200)
        program:write_u32(0x00001590, 0x8d280000)
        program:write_u32(0x00001594, 0x2529fc00)
        program:write_u32(0x00001598, 0x8d2c0000)
        program:write_u32(0x0000159c, 0x1000ffff)
        program:write_u32(0x000015a0, 0x00000000)
        run_uncached(0x00001580)
    elseif frames == 16 then
        print(string.format(
            "DATA_LOCK ADJACENT=%08X",
            cpu.state["R12"].value))

        -- Disable ICE, invalidate one four-word I-cache tag, then enable the
        -- minimum four-word instruction refill.
        program:write_u32(0x00005000, 0x10000003)
        program:write_u32(0x00005004, 0x00000000)
        program:write_u32(0x00005008, 0x24101234)
        program:write_u32(0x0000500c, 0x00000000)
        program:write_u32(0x00005010, 0x1000ffff)
        program:write_u32(0x00005014, 0x00000000)
        program:write_u32(0x00001600, 0x24080010)
        program:write_u32(0x00001604, 0x40881800)
        program:write_u32(0x00001608, 0x3c098000)
        program:write_u32(0x0000160c, 0x35295000)
        program:write_u32(0x00001610, 0xbd200000)
        program:write_u32(0x00001614, 0x24080030)
        program:write_u32(0x00001618, 0x40881800)
        program:write_u32(0x0000161c, 0x1000ffff)
        program:write_u32(0x00001620, 0x00000000)
        run_uncached(0x00001600)
    elseif frames == 17 then
        run_cached(0x00005000)
    elseif frames == 18 then
        program:write_u32(0x00005008, 0x24105678)
        run_cached(0x00005008)
    elseif frames == 19 then
        print(string.format(
            "ICACHE_PREFETCH TARGET=%08X",
            cpu.state["R16"].value))
        machine:exit()
    end
end)
"""


def parse_results(output: bytes) -> tuple[int, ...] | None:
    match = RESULT_PATTERN.search(output)
    if not match:
        return None
    return tuple(int(value, 16) for value in match.groups())


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
    lua_path = run_dir / "tx39-refill-regression.lua"
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
        print(f"error: unable to run TX39 refill regression: {error}", file=sys.stderr)
        return 2

    log_path.write_bytes(completed.stdout)
    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; see {log_path}",
            file=sys.stderr,
        )
        return 2

    actual = parse_results(completed.stdout)
    if actual != EXPECTED:
        print(
            f"FAIL: TX39 refill result {actual!r}, expected {EXPECTED!r}; "
            f"see {log_path}",
            file=sys.stderr,
        )
        return 1

    print(
        "PASS: TX39 one-word and burst data refill, burst auto-lock, "
        "and instruction prefetch are correct"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
