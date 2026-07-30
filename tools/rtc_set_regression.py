#!/usr/bin/env python3
"""Call the development ROM's real RTC-setting routine inside MAME.

The IDT monitor cannot write Dino's 40-bit RTC directly.  ``SetTimer`` first
uses timer-control bit 0 to advance the high byte while the RTC is frozen,
then bit 1 to advance ``rtcLow`` in 32-tick units.  This harness calls that
ROM routine with a nearby high/low target and requires it to return success.
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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "rtc-set-regression"

SET_TIMER = 0x13C04F04
STUB = 0x00300000
STUB_UNCACHED = 0xA0300000
DONE = STUB + 0x100
TARGET_HIGH = STUB + 0x104
TARGET_LOW = STUB + 0x108
RETURN_VALUE = STUB + 0x10C
RESULT_HIGH = STUB + 0x110
RESULT_LOW = STUB + 0x114
MARKER = 0x52544353  # "RTCS"

RESULT_PATTERN = re.compile(
    rb"RTC_SET RESULT returned=([0-9A-F]{8}) "
    rb"target=([0-9A-F]{2}):([0-9A-F]{8}) "
    rb"actual=([0-9A-F]{2}):([0-9A-F]{8})"
)


def call_stub_words() -> list[int]:
    """Return a MIPS stub that calls ``SetTimer(high, low)``."""
    return [
        0x3C08B0C0,  # lui   t0, 0xb0c0
        0xAD000150,  # sw    zero, 0x150(t0): start from normal RTC mode
        0x8D040140,  # lw    a0, 0x140(t0): rtcHigh
        0x24840001,  # addiu a0, a0, 1
        0x308400FF,  # andi  a0, a0, 0xff
        0x8D050144,  # lw    a1, 0x144(t0): rtcLow
        0x24A51000,  # addiu a1, a1, 0x1000
        0x3C090030,  # lui   t1, 0x0030
        0xAD240104,  # sw    a0, TARGET_HIGH-STUB(t1)
        0xAD250108,  # sw    a1, TARGET_LOW-STUB(t1)
        0x3C1913C0,  # lui   t9, high(SET_TIMER)
        0x37394F04,  # ori   t9, t9, low(SET_TIMER)
        0x0320F809,  # jalr  t9
        0x00000000,  # nop
        0x3C08B0C0,  # lui   t0, 0xb0c0 (caller-saved)
        0x3C090030,  # lui   t1, 0x0030 (caller-saved)
        0xAD22010C,  # sw    v0, RETURN_VALUE-STUB(t1)
        0x8D0A0140,  # lw    t2, 0x140(t0)
        0x8D0B0144,  # lw    t3, 0x144(t0)
        0xAD2A0110,  # sw    t2, RESULT_HIGH-STUB(t1)
        0xAD2B0114,  # sw    t3, RESULT_LOW-STUB(t1)
        0x3C0A5254,  # lui   t2, high(MARKER)
        0x354A4353,  # ori   t2, t2, low(MARKER)
        0xAD2A0100,  # sw    t2, DONE-STUB(t1)
        0x1000FFFF,  # b     .
        0x00000000,  # nop
    ]


def automation_script() -> str:
    """Return Lua that installs the call stub and reports its result."""
    writes = "\n".join(
        f"        program:write_u32(0x{STUB + index * 4:08x}, 0x{word:08x})"
        for index, word in enumerate(call_stub_words())
    )
    return (
        r"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0
local called = false

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 10 then
__WRITES__
        program:write_u32(0x__DONE__, 0)
        cpu.state["R29"].value = 0xa03ff000
        cpu.state["PC"].value = 0x__STUB_UNCACHED__
        called = true
    elseif called and program:read_u32(0x__DONE__) == 0x__MARKER__ then
        print(string.format(
            "RTC_SET RESULT returned=%08X target=%02X:%08X actual=%02X:%08X",
            program:read_u32(0x__RETURN_VALUE__),
            program:read_u32(0x__TARGET_HIGH__) & 0xff,
            program:read_u32(0x__TARGET_LOW__),
            program:read_u32(0x__RESULT_HIGH__) & 0xff,
            program:read_u32(0x__RESULT_LOW__)))
        machine:exit()
    elseif frames == 1800 then
        print(string.format(
            "RTC_SET TIMEOUT pc=%08X control=%08X target=%02X:%08X " ..
            "actual=%02X:%08X r2=%08X r3=%08X r4=%08X r5=%08X",
            cpu.state["PC"].value, program:read_u32(0x10c00150),
            program:read_u32(0x__TARGET_HIGH__) & 0xff,
            program:read_u32(0x__TARGET_LOW__),
            program:read_u32(0x10c00140) & 0xff,
            program:read_u32(0x10c00144),
            cpu.state["R2"].value, cpu.state["R3"].value,
            cpu.state["R4"].value, cpu.state["R5"].value))
        machine:exit()
    end
end)
""".replace("__WRITES__", writes)
        .replace("__DONE__", f"{DONE:08x}")
        .replace("__MARKER__", f"{MARKER:08x}")
        .replace("__STUB_UNCACHED__", f"{STUB_UNCACHED:08x}")
        .replace("__RETURN_VALUE__", f"{RETURN_VALUE:08x}")
        .replace("__TARGET_HIGH__", f"{TARGET_HIGH:08x}")
        .replace("__TARGET_LOW__", f"{TARGET_LOW:08x}")
        .replace("__RESULT_HIGH__", f"{RESULT_HIGH:08x}")
        .replace("__RESULT_LOW__", f"{RESULT_LOW:08x}")
    )


def parse_result(output: bytes) -> tuple[int, int, int, int, int] | None:
    """Return ROM result, target high/low, and actual high/low."""
    match = RESULT_PATTERN.search(output)
    if not match:
        return None
    return tuple(int(value, 16) for value in match.groups())  # type: ignore[return-value]


def verify_result(result: tuple[int, int, int, int, int] | None) -> list[str]:
    """Return failures for a parsed run."""
    if result is None:
        return ["ROM SetTimer result is missing"]
    returned, target_high, target_low, actual_high, actual_low = result
    failures: list[str] = []
    if returned != 1:
        failures.append(f"SetTimer returned {returned}, not success")
    if actual_high != target_high:
        failures.append(
            f"RTC high byte is {actual_high:02X}, expected {target_high:02X}"
        )
    delta = (actual_low - target_low) & 0xFFFFFFFF
    signed_delta = delta if delta < 0x80000000 else delta - 0x100000000
    if abs(signed_delta) > 4:
        failures.append(
            f"RTC low word missed target by {signed_delta} ticks "
            f"({actual_low:08X} vs {target_low:08X})"
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
    if not mame.is_file():
        print(f"error: MAME executable not found: {mame}", file=sys.stderr)
        return 2
    if not rompath.is_dir():
        print(f"error: ROM path not found: {rompath}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    config_dir = run_dir / "cfg"
    nvram_dir = run_dir / "nvram"
    config_dir.mkdir(parents=True)
    nvram_dir.mkdir()
    lua_path = run_dir / "rtc-set-regression.lua"
    log_path = run_dir / "mame-output.txt"
    lua_path.write_text(automation_script(), encoding="utf-8")

    command = [
        str(mame),
        "datarover840d",
        "-rompath",
        str(rompath),
        "-cfg_directory",
        str(config_dir),
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
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"error: unable to run RTC-set regression: {error}", file=sys.stderr)
        return 2

    log_path.write_bytes(completed.stdout)
    failures = verify_result(parse_result(completed.stdout))
    if completed.returncode or failures:
        detail = (
            "; ".join(failures) if failures else f"MAME status {completed.returncode}"
        )
        print(f"FAIL: {detail}; see {log_path}", file=sys.stderr)
        return 1

    result = parse_result(completed.stdout)
    assert result is not None
    _, target_high, target_low, actual_high, actual_low = result
    print(
        "PASS: ROM SetTimer used Dino's rough and fine RTC test modes, "
        f"returned success, and set {target_high:02X}:{target_low:08X} "
        f"to {actual_high:02X}:{actual_low:08X}"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
