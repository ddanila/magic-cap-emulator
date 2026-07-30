#!/usr/bin/env python3
"""Execute and verify the TX39 instruction and CP0 extensions in MAME."""

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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "tx39-regression"
RESULT_PATTERN = re.compile(
    rb"MADD R10=([0-9A-F]{8}) HI=([0-9A-F]{8}) LO=([0-9A-F]{8}) "
    rb"PC=[0-9A-F]{8}.*"
    rb"MADDU R11=([0-9A-F]{8}) HI=([0-9A-F]{8}) LO=([0-9A-F]{8}) "
    rb"PC=[0-9A-F]{8}.*"
    rb"MULT R12=([0-9A-F]{8}) HI=([0-9A-F]{8}) LO=([0-9A-F]{8}) "
    rb"PC=[0-9A-F]{8}.*"
    rb"MULTU R13=([0-9A-F]{8}) HI=([0-9A-F]{8}) LO=([0-9A-F]{8}) "
    rb"PC=[0-9A-F]{8}",
    re.DOTALL,
)
CP0_PATTERN = re.compile(
    rb"CACHE_ENABLE CACHED=([0-9A-F]{8}) UNCACHED=([0-9A-F]{8}).*"
    rb"CONFIG FIRST=([0-9A-F]{8}) LOCKED=([0-9A-F]{8}).*"
    rb"CACHE EXCEPTION=([0-9A-F]{8}) RETURN=([0-9A-F]{8})",
    re.DOTALL,
)
CACHE_PATTERN = re.compile(
    rb"CACHE_LRU HIT=([0-9A-F]{8}) EVICTED=([0-9A-F]{8}).*"
    rb"CACHE_LOCK RETAINED=([0-9A-F]{8}) "
    rb"CACHED_STORE=([0-9A-F]{8}) MEMORY=([0-9A-F]{8}).*"
    rb"CACHE_UNLOCK RELOADED=([0-9A-F]{8}).*"
    rb"CACHE_NOALLOC RELOADED=([0-9A-F]{8})",
    re.DOTALL,
)
EXPECTED = (
    0xFFFFFFFF,
    0xFFFFFFFF,
    0xFFFFFFFF,
    0xFFFFFFFF,
    0x00000001,
    0xFFFFFFFF,
    0xFFFFFFFA,
    0xFFFFFFFF,
    0xFFFFFFFA,
    0xFFFFFFFE,
    0x00000001,
    0xFFFFFFFE,
)
EXPECTED_CP0 = (
    0x11111111,
    0x22222222,
    0x001000DF,
    0x001000DF,
    0x00000C00,
    0x00000300,
)
EXPECTED_CACHE = (
    0x11111111,
    0xBBBBBBBB,
    0x11111111,
    0x44444444,
    0xAAAAAAAA,
    0x55555555,
    0x77777777,
)


def automation_script() -> str:
    """Return isolated uncached-RAM tests for TX39 instructions and CP0."""
    return r"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0

local function run_at(address)
    cpu.state["SR"].value = 0
    cpu.state["PC"].value = 0xa0000000 | address
end

emu.register_frame_done(function()
    frames = frames + 1

    if frames == 10 then
        -- MADD r10,r8,r9: 5 + (-2 * 3) = -1.
        program:write_u32(0x00001000, 0x71095000)
        program:write_u32(0x00001004, 0x1000ffff)
        program:write_u32(0x00001008, 0x00000000)
        cpu.state["R8"].value = 0xfffffffe
        cpu.state["R9"].value = 3
        cpu.state["HI"].value = 0
        cpu.state["LO"].value = 5
        run_at(0x00001000)
    elseif frames == 11 then
        print(string.format(
            "MADD R10=%08X HI=%08X LO=%08X PC=%08X",
            cpu.state["R10"].value,
            cpu.state["HI"].value,
            cpu.state["LO"].value,
            cpu.state["PC"].value))

        -- MADDU r11,r8,r9: 1 + (0xffffffff * 2) = 0x1ffffffff.
        program:write_u32(0x00001020, 0x71095801)
        program:write_u32(0x00001024, 0x1000ffff)
        program:write_u32(0x00001028, 0x00000000)
        cpu.state["R8"].value = 0xffffffff
        cpu.state["R9"].value = 2
        cpu.state["HI"].value = 0
        cpu.state["LO"].value = 1
        run_at(0x00001020)
    elseif frames == 12 then
        print(string.format(
            "MADDU R11=%08X HI=%08X LO=%08X PC=%08X",
            cpu.state["R11"].value,
            cpu.state["HI"].value,
            cpu.state["LO"].value,
            cpu.state["PC"].value))

        -- MULT r12,r8,r9: -2 * 3 = -6 in rd and HI/LO.
        program:write_u32(0x00001040, 0x01096018)
        program:write_u32(0x00001044, 0x1000ffff)
        program:write_u32(0x00001048, 0x00000000)
        cpu.state["R8"].value = 0xfffffffe
        cpu.state["R9"].value = 3
        cpu.state["HI"].value = 0
        cpu.state["LO"].value = 0
        run_at(0x00001040)
    elseif frames == 13 then
        print(string.format(
            "MULT R12=%08X HI=%08X LO=%08X PC=%08X",
            cpu.state["R12"].value,
            cpu.state["HI"].value,
            cpu.state["LO"].value,
            cpu.state["PC"].value))

        -- MULTU r13,r8,r9: 0xffffffff * 2 = 0x1fffffffe.
        program:write_u32(0x00001060, 0x01096819)
        program:write_u32(0x00001064, 0x1000ffff)
        program:write_u32(0x00001068, 0x00000000)
        cpu.state["R8"].value = 0xffffffff
        cpu.state["R9"].value = 2
        cpu.state["HI"].value = 0
        cpu.state["LO"].value = 0
        run_at(0x00001060)
    elseif frames == 14 then
        print(string.format(
            "MULTU R13=%08X HI=%08X LO=%08X PC=%08X",
            cpu.state["R13"].value,
            cpu.state["HI"].value,
            cpu.state["LO"].value,
            cpu.state["PC"].value))

        -- Fill a data-cache line while DCE is enabled.
        program:write_u32(0x00002000, 0x11111111)
        program:write_u32(0x00001080, 0x24080030)
        program:write_u32(0x00001084, 0x40881800)
        program:write_u32(0x00001088, 0x3c098000)
        program:write_u32(0x0000108c, 0x35292000)
        program:write_u32(0x00001090, 0xbd310000)
        program:write_u32(0x00001094, 0x8d2a0000)
        program:write_u32(0x00001098, 0x1000ffff)
        program:write_u32(0x0000109c, 0x00000000)
        run_at(0x00001080)
    elseif frames == 15 then
        -- Change backing RAM behind the cached line, disable DCE, and load
        -- through the same kseg0 address.  The second load must bypass cache.
        program:write_u32(0x00002000, 0x22222222)
        program:write_u32(0x000010a0, 0x24080020)
        program:write_u32(0x000010a4, 0x40881800)
        program:write_u32(0x000010a8, 0x3c098000)
        program:write_u32(0x000010ac, 0x35292000)
        program:write_u32(0x000010b0, 0x8d2b0000)
        program:write_u32(0x000010b4, 0x1000ffff)
        program:write_u32(0x000010b8, 0x00000000)
        run_at(0x000010a0)
    elseif frames == 16 then
        print(string.format(
            "CACHE_ENABLE CACHED=%08X UNCACHED=%08X",
            cpu.state["R10"].value,
            cpu.state["R11"].value))

        -- Config preserves its read-only cache sizes.  The first write also
        -- sets Config.Lock; the second write must therefore be ignored.
        program:write_u32(0x00001100, 0x240800df)
        program:write_u32(0x00001104, 0x40881800)
        program:write_u32(0x00001108, 0x40091800)
        program:write_u32(0x0000110c, 0x40801800)
        program:write_u32(0x00001110, 0x400a1800)
        program:write_u32(0x00001114, 0x1000ffff)
        program:write_u32(0x00001118, 0x00000000)
        run_at(0x00001100)
    elseif frames == 17 then
        print(string.format(
            "CONFIG FIRST=%08X LOCKED=%08X",
            cpu.state["R9"].value,
            cpu.state["R10"].value))

        -- A syscall pushes Cache.DALc/IALc into the previous mode pair.  The
        -- handler records that state, then RFE restores the current pair.
        program:write_u32(0x00000080, 0x40103800)
        program:write_u32(0x00000084, 0x3c1aa000)
        program:write_u32(0x00000088, 0x375a10cc)
        program:write_u32(0x0000008c, 0x03400008)
        program:write_u32(0x00000090, 0x42000010)
        program:write_u32(0x000010c0, 0x24080300)
        program:write_u32(0x000010c4, 0x40883800)
        program:write_u32(0x000010c8, 0x0000000c)
        program:write_u32(0x000010cc, 0x1000ffff)
        program:write_u32(0x000010d0, 0x00000000)
        run_at(0x000010c0)
    elseif frames == 18 then
        print(string.format(
            "CACHE EXCEPTION=%08X RETURN=%08X",
            cpu.state["R16"].value,
            cpu.state["Cache"].value))

        -- Start with three backing words that alias one two-way cache index.
        program:write_u32(0x00003000, 0x11111111)
        program:write_u32(0x00003200, 0x22222222)
        program:write_u32(0x00003400, 0x33333333)
        program:write_u32(0x00001200, 0x40803800)
        program:write_u32(0x00001204, 0x3c088000)
        program:write_u32(0x00001208, 0x35083000)
        program:write_u32(0x0000120c, 0xbd110000)
        program:write_u32(0x00001210, 0x25080200)
        program:write_u32(0x00001214, 0xbd110000)
        program:write_u32(0x00001218, 0x25080200)
        program:write_u32(0x0000121c, 0xbd110000)
        program:write_u32(0x00001220, 0x2508fc00)
        program:write_u32(0x00001224, 0x8d090000)
        program:write_u32(0x00001228, 0x25080200)
        program:write_u32(0x0000122c, 0x8d0a0000)
        program:write_u32(0x00001230, 0x1000ffff)
        program:write_u32(0x00001234, 0x00000000)
        run_at(0x00001200)
    elseif frames == 19 then
        -- Change backing RAM behind A and B.  Touching A makes B least
        -- recently used; loading C must evict B rather than A.
        program:write_u32(0x00003000, 0xaaaaaaaa)
        program:write_u32(0x00003200, 0xbbbbbbbb)
        program:write_u32(0x00001240, 0x3c088000)
        program:write_u32(0x00001244, 0x35083000)
        program:write_u32(0x00001248, 0x8d0b0000)
        program:write_u32(0x0000124c, 0x25080400)
        program:write_u32(0x00001250, 0x8d0c0000)
        program:write_u32(0x00001254, 0x2508fe00)
        program:write_u32(0x00001258, 0x8d0d0000)
        program:write_u32(0x0000125c, 0x1000ffff)
        program:write_u32(0x00001260, 0x00000000)
        run_at(0x00001240)
    elseif frames == 20 then
        print(string.format(
            "CACHE_LRU HIT=%08X EVICTED=%08X",
            cpu.state["R11"].value,
            cpu.state["R13"].value))

        -- Empty the index, enable DALc for one load of A, then leave the
        -- resulting per-index lock active while clearing the CP0 mode.
        program:write_u32(0x00003000, 0x11111111)
        program:write_u32(0x00003200, 0x22222222)
        program:write_u32(0x00003400, 0x33333333)
        program:write_u32(0x00001280, 0x40803800)
        program:write_u32(0x00001284, 0x3c088000)
        program:write_u32(0x00001288, 0x35083000)
        program:write_u32(0x0000128c, 0xbd090000)
        program:write_u32(0x00001290, 0xbd110000)
        program:write_u32(0x00001294, 0x25080200)
        program:write_u32(0x00001298, 0xbd110000)
        program:write_u32(0x0000129c, 0x25080200)
        program:write_u32(0x000012a0, 0xbd110000)
        program:write_u32(0x000012a4, 0x2508fc00)
        program:write_u32(0x000012a8, 0x24090100)
        program:write_u32(0x000012ac, 0x40893800)
        program:write_u32(0x000012b0, 0x8d0e0000)
        program:write_u32(0x000012b4, 0x40803800)
        program:write_u32(0x000012b8, 0x1000ffff)
        program:write_u32(0x000012bc, 0x00000000)
        run_at(0x00001280)
    elseif frames == 21 then
        -- Churn the unlocked way with B and C.  A must remain cached despite
        -- its changed backing word.  A store hit changes only the locked line.
        program:write_u32(0x00003000, 0xaaaaaaaa)
        program:write_u32(0x000012c0, 0x3c088000)
        program:write_u32(0x000012c4, 0x35083000)
        program:write_u32(0x000012c8, 0x25080200)
        program:write_u32(0x000012cc, 0x8d0a0000)
        program:write_u32(0x000012d0, 0x25080200)
        program:write_u32(0x000012d4, 0x8d0b0000)
        program:write_u32(0x000012d8, 0x2508fc00)
        program:write_u32(0x000012dc, 0x8d0f0000)
        program:write_u32(0x000012e0, 0x3c0a4444)
        program:write_u32(0x000012e4, 0x354a4444)
        program:write_u32(0x000012e8, 0xad0a0000)
        program:write_u32(0x000012ec, 0x8d100000)
        program:write_u32(0x000012f0, 0x3c08a000)
        program:write_u32(0x000012f4, 0x35083000)
        program:write_u32(0x000012f8, 0x8d110000)
        program:write_u32(0x000012fc, 0x1000ffff)
        program:write_u32(0x00001300, 0x00000000)
        run_at(0x000012c0)
    elseif frames == 22 then
        print(string.format(
            "CACHE_LOCK RETAINED=%08X CACHED_STORE=%08X MEMORY=%08X",
            cpu.state["R15"].value,
            cpu.state["R16"].value,
            cpu.state["R17"].value))

        -- Clear the per-index lock, write the cached value through, and churn
        -- both ways.  An uncached backing change must then be visible on A.
        program:write_u32(0x00001320, 0x3c088000)
        program:write_u32(0x00001324, 0x35083000)
        program:write_u32(0x00001328, 0xbd090000)
        program:write_u32(0x0000132c, 0xad100000)
        program:write_u32(0x00001330, 0x25080200)
        program:write_u32(0x00001334, 0x8d090000)
        program:write_u32(0x00001338, 0x25080200)
        program:write_u32(0x0000133c, 0x8d0a0000)
        program:write_u32(0x00001340, 0x3c08a000)
        program:write_u32(0x00001344, 0x35083000)
        program:write_u32(0x00001348, 0x3c095555)
        program:write_u32(0x0000134c, 0x35295555)
        program:write_u32(0x00001350, 0xad090000)
        program:write_u32(0x00001354, 0x3c088000)
        program:write_u32(0x00001358, 0x35083000)
        program:write_u32(0x0000135c, 0x8d120000)
        program:write_u32(0x00001360, 0x1000ffff)
        program:write_u32(0x00001364, 0x00000000)
        run_at(0x00001320)
    elseif frames == 23 then
        print(string.format(
            "CACHE_UNLOCK RELOADED=%08X",
            cpu.state["R18"].value))

        -- A cached store miss is write-through but must not allocate a line.
        program:write_u32(0x00003000, 0x11111111)
        program:write_u32(0x00001380, 0x40803800)
        program:write_u32(0x00001384, 0x3c088000)
        program:write_u32(0x00001388, 0x35083000)
        program:write_u32(0x0000138c, 0xbd110000)
        program:write_u32(0x00001390, 0x3c096666)
        program:write_u32(0x00001394, 0x35296666)
        program:write_u32(0x00001398, 0xad090000)
        program:write_u32(0x0000139c, 0x1000ffff)
        program:write_u32(0x000013a0, 0x00000000)
        run_at(0x00001380)
    elseif frames == 24 then
        program:write_u32(0x00003000, 0x77777777)
        program:write_u32(0x000013c0, 0x3c088000)
        program:write_u32(0x000013c4, 0x35083000)
        program:write_u32(0x000013c8, 0x8d130000)
        program:write_u32(0x000013cc, 0x1000ffff)
        program:write_u32(0x000013d0, 0x00000000)
        run_at(0x000013c0)
    elseif frames == 25 then
        print(string.format(
            "CACHE_NOALLOC RELOADED=%08X",
            cpu.state["R19"].value))
        machine:exit()
    end
end)
"""


def parse_results(output: bytes) -> tuple[int, ...] | None:
    match = RESULT_PATTERN.search(output)
    if not match:
        return None
    return tuple(int(value, 16) for value in match.groups())


def parse_cp0_results(output: bytes) -> tuple[int, ...] | None:
    match = CP0_PATTERN.search(output)
    if not match:
        return None
    return tuple(int(value, 16) for value in match.groups())


def parse_cache_results(output: bytes) -> tuple[int, ...] | None:
    match = CACHE_PATTERN.search(output)
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
    lua_path = run_dir / "tx39-regression.lua"
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
        print(f"error: unable to run TX39 regression: {error}", file=sys.stderr)
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
            f"FAIL: TX39 result {actual!r}, expected {EXPECTED!r}; see {log_path}",
            file=sys.stderr,
        )
        return 1

    actual_cp0 = parse_cp0_results(completed.stdout)
    if actual_cp0 != EXPECTED_CP0:
        print(
            f"FAIL: TX39 CP0 result {actual_cp0!r}, expected {EXPECTED_CP0!r}; "
            f"see {log_path}",
            file=sys.stderr,
        )
        return 1

    actual_cache = parse_cache_results(completed.stdout)
    if actual_cache != EXPECTED_CACHE:
        print(
            f"FAIL: TX39 cache result {actual_cache!r}, "
            f"expected {EXPECTED_CACHE!r}; see {log_path}",
            file=sys.stderr,
        )
        return 1

    print(
        "PASS: TX39 arithmetic, CP0 modes, two-way LRU, and data-cache "
        "line locking are correct"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
