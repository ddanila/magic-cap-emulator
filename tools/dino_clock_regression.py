#!/usr/bin/env python3
"""Verify Dino master-clock gates for implemented peripherals."""

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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "dino-clock-regression"
RESULT_PATTERN = re.compile(
    rb"CLOCK_OFF UARTA=([0-9A-F]{8}) UARTB=([0-9A-F]{8}) "
    rb"INT1=([0-9A-F]{8}) "
    rb"INT2=([0-9A-F]{8}) INT5=([0-9A-F]{8}) "
    rb"MBUS=([0-9A-F]{8}) RTC_A=([0-9A-F]{8}) RTC_B=([0-9A-F]{8}).*"
    rb"CLOCK_ON UARTA=([0-9A-F]{8}) UARTB=([0-9A-F]{8}) "
    rb"INT1=([0-9A-F]{8}) "
    rb"INT2=([0-9A-F]{8}) INT5=([0-9A-F]{8}) "
    rb"MBUS=([0-9A-F]{8}) RTC=([0-9A-F]{8})",
    re.DOTALL,
)
UART_ENABLED = 0x80000000
UART_A_TX = 0x04000000
UART_B_TX = 0x00010000
SIB_BOUNDARIES = 0x00000180
MBUS_ENABLED = 0x80000000
MBUS_EVENTS = 0x00000A00
PERIODIC_EVENT = 0x20000000


def automation_script() -> str:
    """Return isolated clock-off/clock-on peripheral checks."""
    return r"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0
local rtc_a = 0

local MASTER_CLOCK = 0x10c001c0
local SIB_CONTROL = 0x10c00074
local UART_A_CONTROL1 = 0x10c000b0
local UART_A_HOLD = 0x10c000c4
local UART_B_CONTROL1 = 0x10c000c8
local UART_B_HOLD = 0x10c000dc
local MBUS_CONTROL1 = 0x10c000e0
local MBUS_COMMAND = 0x10c000f4
local INTERRUPT1 = 0x10c00100
local INTERRUPT2 = 0x10c00104
local INTERRUPT5 = 0x10c00110
local RTC_LOW = 0x10c00144
local TIMER_CONTROL = 0x10c00150
local PERIODIC_TIMER = 0x10c00154
local ACTIVE_CLOCKS = 0x00028803

local function park_cpu()
    program:write_u32(0x00001000, 0x1000ffff)
    program:write_u32(0x00001004, 0x00000000)
    cpu.state["SR"].value = 0
    cpu.state["PC"].value = 0xa0001000
end

local function clear_interrupts()
    program:write_u32(INTERRUPT1, 0xffffffff)
    program:write_u32(INTERRUPT2, 0xffffffff)
    program:write_u32(INTERRUPT5, 0xffffffff)
end

emu.register_frame_done(function()
    frames = frames + 1

    if frames == 10 then
        park_cpu()
        program:write_u32(MASTER_CLOCK, 0)
        program:write_u32(SIB_CONTROL, 0x00000001)
        program:write_u32(UART_A_CONTROL1, 0x00000001)
        program:write_u32(UART_B_CONTROL1, 0x00000001)
        program:write_u32(MBUS_CONTROL1, 0x00000001)
        program:write_u32(PERIODIC_TIMER, 327)
        program:write_u32(TIMER_CONTROL, 0x00000010)
        clear_interrupts()
        program:write_u32(UART_A_HOLD, 0x00000041)
        program:write_u32(UART_B_HOLD, 0x00000042)
        program:write_u32(MBUS_COMMAND, 0x00000000)
        rtc_a = program:read_u32(RTC_LOW)
    elseif frames == 20 then
        local rtc_b = program:read_u32(RTC_LOW)
        print(string.format(
            "CLOCK_OFF UARTA=%08X UARTB=%08X INT1=%08X INT2=%08X " ..
            "INT5=%08X MBUS=%08X RTC_A=%08X RTC_B=%08X",
            program:read_u32(UART_A_CONTROL1),
            program:read_u32(UART_B_CONTROL1),
            program:read_u32(INTERRUPT1),
            program:read_u32(INTERRUPT2),
            program:read_u32(INTERRUPT5),
            program:read_u32(MBUS_CONTROL1),
            rtc_a,
            rtc_b))

        program:write_u32(MASTER_CLOCK, ACTIVE_CLOCKS)
        clear_interrupts()
        program:write_u32(UART_A_CONTROL1, 0x00000101)
        program:write_u32(UART_A_HOLD, 0x00000043)
        program:write_u32(UART_B_HOLD, 0x00000044)
        program:write_u32(MBUS_COMMAND, 0x00000000)
    elseif frames == 30 then
        print(string.format(
            "CLOCK_ON UARTA=%08X UARTB=%08X INT1=%08X INT2=%08X " ..
            "INT5=%08X MBUS=%08X RTC=%08X",
            program:read_u32(UART_A_CONTROL1),
            program:read_u32(UART_B_CONTROL1),
            program:read_u32(INTERRUPT1),
            program:read_u32(INTERRUPT2),
            program:read_u32(INTERRUPT5),
            program:read_u32(MBUS_CONTROL1),
            program:read_u32(RTC_LOW)))
        machine:exit()
    end
end)
"""


def parse_results(output: bytes) -> tuple[int, ...] | None:
    match = RESULT_PATTERN.search(output)
    if not match:
        return None
    return tuple(int(value, 16) for value in match.groups())


def verify_results(values: tuple[int, ...] | None) -> list[str]:
    if values is None:
        return ["complete Dino clock report is missing"]

    (
        off_uart_a,
        off_uart_b,
        off_int1,
        off_int2,
        off_int5,
        off_mbus,
        rtc_a,
        rtc_b,
        on_uart_a,
        on_uart_b,
        on_int1,
        on_int2,
        on_int5,
        on_mbus,
        rtc_running,
    ) = values
    failures: list[str] = []
    if off_uart_a & UART_ENABLED or off_uart_b & UART_ENABLED:
        failures.append("a UART reported enabled while its master clock was off")
    if off_int1 & SIB_BOUNDARIES:
        failures.append("SIB produced frame boundaries while its master clock was off")
    if off_int2 & (UART_A_TX | UART_B_TX | MBUS_EVENTS):
        failures.append("UART or Magic Bus completed work with master clocks off")
    if off_int5 & PERIODIC_EVENT:
        failures.append("periodic timer fired while its master clock was off")
    if off_mbus & MBUS_ENABLED:
        failures.append("Magic Bus reported enabled while its master clock was off")
    if (rtc_b - rtc_a) & 0xFFFFFFFF < 100:
        failures.append(
            f"RTC did not remain live with the timer clock off: "
            f"{rtc_a:08X}->{rtc_b:08X}"
        )

    if not (on_uart_a & UART_ENABLED) or not (on_uart_b & UART_ENABLED):
        failures.append("a UART did not report enabled after its clock resumed")
    if on_int1 & SIB_BOUNDARIES != SIB_BOUNDARIES:
        failures.append("SIB frame boundaries did not resume")
    if (on_int2 & (UART_A_TX | UART_B_TX)) != (UART_A_TX | UART_B_TX):
        failures.append("UART A/B transmit did not resume")
    if on_int2 & MBUS_EVENTS != MBUS_EVENTS:
        failures.append("Magic Bus command completion did not resume")
    if on_int5 & PERIODIC_EVENT != PERIODIC_EVENT:
        failures.append("periodic timer did not resume")
    if not on_mbus & MBUS_ENABLED:
        failures.append("Magic Bus did not report enabled after its clock resumed")
    if (rtc_running - rtc_b) & 0xFFFFFFFF < 100:
        failures.append(
            f"RTC stopped during the clock-restore interval: "
            f"{rtc_b:08X}->{rtc_running:08X}"
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
    config_dir = run_dir / "cfg"
    nvram_dir = run_dir / "nvram"
    config_dir.mkdir(parents=True)
    nvram_dir.mkdir()
    lua_path = run_dir / "dino-clock-regression.lua"
    log_path = run_dir / "mame-output.txt"
    lua_path.write_text(automation_script(), encoding="utf-8")

    command = [
        str(mame),
        "datarover840",
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
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"error: unable to run Dino clock regression: {error}", file=sys.stderr)
        return 2

    log_path.write_bytes(completed.stdout)
    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; see {log_path}",
            file=sys.stderr,
        )
        return 2

    failures = verify_results(parse_results(completed.stdout))
    if failures:
        print(f"FAIL: {'; '.join(failures)}; see {log_path}", file=sys.stderr)
        return 1

    print(
        "PASS: Dino master clocks gate and resume both UARTs, SIB, "
        "Magic Bus, and the periodic timer while the RTC remains independent"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
