#!/usr/bin/env python3
"""Verify DataRover suspend/wake across a battery-backed RAM relaunch."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = Path.home() / "fun" / "magic-cap-assets" / "roms"
DEFAULT_WORKDIR = (
    Path.home() / "fun" / "magic-cap-assets" / "runtime" / "power-regression"
)
POFF = 0x504F4646
ON_BUTTON_POSITIVE = 0x00800000
POWER_ON_BUTTON_STATUS = 0x80000000
POWER_STOP_CPU = 0x00000010
POWER_VCC_ON = 0x00000001
DEEP_DOZE_START = 0x13C3B28C
DEEP_DOZE_END = 0x13C3B450
# Plain Doze stops the CPU here instead of running DRAM self-refresh.  Which
# routine the retained shutdown ends in depends on the battery model: with a
# healthy backup cell the OS keeps DRAM alive through DeepDoze, and with a cell
# it believes is dead there is nothing to retain, so it takes plain Doze.
DOZE_STOP = 0x13C3B270
WAIT_FOR_POWER_DOWN = 0x13C3B1C8
CHECKPOINT_PATTERN = re.compile(
    rb"POWER_CHECK ([A-Z_]+) "
    rb"PC=([0-9A-F]{8}) REASON=([0-9A-F]{8}) "
    rb"INT5=([0-9A-F]{8}) INT5EN=([0-9A-F]{8}) "
    rb"INT6=([0-9A-F]{8}) INT6EN=([0-9A-F]{8}) "
    rb"POWER=([0-9A-F]{8})"
)


def _lua_common() -> str:
    return r"""local machine = manager.machine
local program = machine.devices[":maincpu"].spaces["program"]
local cpu = machine.devices[":maincpu"]
local ports = machine.ioport.ports
local power_button = ports[":POWER_BUTTON"]:field(0x01)
local frames = 0

local function checkpoint(label)
    print(string.format(
        "POWER_CHECK %s PC=%08X REASON=%08X " ..
        "INT5=%08X INT5EN=%08X INT6=%08X INT6EN=%08X POWER=%08X",
        label,
        cpu.state["PC"].value,
        program:read_u32(0x0000e880),
        program:read_u32(0x10c00110),
        program:read_u32(0x10c00128),
        program:read_u32(0x10c00114),
        program:read_u32(0x10c0012c),
        program:read_u32(0x10c001c4)))
end
"""


def suspend_script() -> str:
    """Boot a fresh heap, enter normal power-off sleep, and persist RAM."""
    return (
        _lua_common()
        + r"""
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 1220 then
        press(240, 160)
    elseif frames == 1240 then
        touch_button:set_value(0)
    elseif frames == 1420 then
        press(23, 23)
    elseif frames == 1440 then
        touch_button:set_value(0)
    elseif frames == 1620 then
        press(456, 296)
    elseif frames == 1640 then
        touch_button:set_value(0)
    elseif frames == 1820 then
        press(240, 160)
    elseif frames == 1840 then
        touch_button:set_value(0)
    elseif frames == 2200 then
        checkpoint("DESK")
        power_button:set_value(1)
    elseif frames == 2220 then
        power_button:set_value(0)
    elseif frames == 2500 then
        checkpoint("SLEEP_A")
    elseif frames == 2600 then
        checkpoint("SLEEP_B")
        machine.screens[":screen"]:snapshot("01-suspended.png")
    elseif frames == 2620 then
        machine:exit()
    end
end)
"""
    )


def wake_script() -> str:
    """Relaunch retained RAM, prove DeepDoze stops, then wake by on-button."""
    return (
        _lua_common()
        + r"""
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 1000 then
        checkpoint("WARM_DOZE_A")
        machine.screens[":screen"]:snapshot("02-warm-doze.png")
    elseif frames == 1100 then
        checkpoint("WARM_DOZE_B")
    elseif frames == 1400 then
        checkpoint("CLEANUP_DOZE_A")
    elseif frames == 1500 then
        checkpoint("CLEANUP_DOZE_B")
    elseif frames == 1520 then
        power_button:set_value(1)
    elseif frames == 1530 then
        checkpoint("CLEANUP_BUTTON")
    elseif frames == 1650 then
        power_button:set_value(0)
    elseif frames == 1900 then
        checkpoint("FINAL_SLEEP_A")
    elseif frames == 2000 then
        checkpoint("FINAL_SLEEP_B")
    elseif frames == 2020 then
        power_button:set_value(1)
    elseif frames == 2030 then
        checkpoint("BUTTON_ASSERTED")
    elseif frames == 2150 then
        power_button:set_value(0)
    elseif frames == 2200 then
        press(421, 70)
    elseif frames == 2220 then
        touch_button:set_value(0)
    elseif frames == 2400 then
        checkpoint("WOKE_A")
        machine.screens[":screen"]:snapshot("03-woke.png")
    elseif frames == 2600 then
        checkpoint("WOKE_B")
    elseif frames == 2620 then
        machine:exit()
    end
end)
"""
    )


def parse_checkpoints(output: bytes) -> dict[str, tuple[int, ...]]:
    """Extract labeled Dino/CPU checkpoints from MAME output."""
    return {
        match.group(1).decode("ascii"): tuple(
            int(value, 16) for value in match.groups()[1:]
        )
        for match in CHECKPOINT_PATTERN.finditer(output)
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mame",
        type=Path,
        default=DEFAULT_MAME,
        help=f"DataRover MAME executable (default: {DEFAULT_MAME})",
    )
    parser.add_argument(
        "--rompath",
        type=Path,
        default=DEFAULT_ROMPATH,
        help=f"MAME ROM search path (default: {DEFAULT_ROMPATH})",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=DEFAULT_WORKDIR,
        help=f"persistent artifact directory (default: {DEFAULT_WORKDIR})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="timeout for each MAME phase in seconds (default: 300)",
    )
    return parser.parse_args(argv)


def _run_phase(
    mame: Path,
    rompath: Path,
    run_dir: Path,
    phase: str,
    script: str,
    timeout: int,
) -> tuple[int, bytes] | None:
    lua_path = run_dir / f"{phase}.lua"
    lua_path.write_text(script, encoding="utf-8")
    command = [
        str(mame),
        "datarover840",
        "-rompath",
        str(rompath),
        "-nvram_directory",
        str(run_dir / "nvram"),
        "-snapshot_directory",
        str(run_dir / "snapshots"),
        "-snapview",
        "native",
        "-autoboot_delay",
        "0",
        "-autoboot_script",
        str(lua_path),
        "-video",
        "none",
        "-sound",
        "none",
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
            timeout=timeout,
        )
    except OSError as error:
        print(f"error: unable to run MAME: {error}", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b""
        (run_dir / f"{phase}-output.txt").write_bytes(output)
        print(
            f"error: {phase} phase timed out; artifacts: {run_dir}",
            file=sys.stderr,
        )
        return None

    (run_dir / f"{phase}-output.txt").write_bytes(completed.stdout)
    return completed.returncode, completed.stdout


def _failure(reason: str, run_dir: Path) -> int:
    print(f"FAIL: {reason}; artifacts: {run_dir}", file=sys.stderr)
    return 1


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
    (run_dir / "nvram").mkdir(parents=True)
    (run_dir / "snapshots").mkdir()

    suspend = _run_phase(
        mame,
        rompath,
        run_dir,
        "suspend",
        suspend_script(),
        args.timeout,
    )
    if suspend is None:
        return 2
    if suspend[0]:
        return _failure(f"suspend phase exited with status {suspend[0]}", run_dir)
    suspend_checks = parse_checkpoints(suspend[1])
    required_suspend = {"DESK", "SLEEP_A", "SLEEP_B"}
    if not required_suspend.issubset(suspend_checks):
        return _failure("suspend checkpoints are incomplete", run_dir)

    sleep_a = suspend_checks["SLEEP_A"]
    sleep_b = suspend_checks["SLEEP_B"]
    if sleep_a[0] != sleep_b[0]:
        return _failure("CPU did not remain stopped in normal sleep", run_dir)
    if sleep_b[1] != POFF or sleep_b[6] & POWER_VCC_ON:
        return _failure("normal sleep did not retain POFF with VCC off", run_dir)

    wake = _run_phase(
        mame,
        rompath,
        run_dir,
        "wake",
        wake_script(),
        args.timeout,
    )
    if wake is None:
        return 2
    if wake[0]:
        return _failure(f"wake phase exited with status {wake[0]}", run_dir)
    wake_checks = parse_checkpoints(wake[1])
    required_wake = {
        "WARM_DOZE_A",
        "WARM_DOZE_B",
        "CLEANUP_DOZE_A",
        "CLEANUP_DOZE_B",
        "CLEANUP_BUTTON",
        "FINAL_SLEEP_A",
        "FINAL_SLEEP_B",
        "BUTTON_ASSERTED",
        "WOKE_A",
        "WOKE_B",
    }
    if not required_wake.issubset(wake_checks):
        return _failure("wake checkpoints are incomplete", run_dir)

    doze_a = wake_checks["WARM_DOZE_A"]
    doze_b = wake_checks["WARM_DOZE_B"]
    cleanup_doze_a = wake_checks["CLEANUP_DOZE_A"]
    cleanup_doze_b = wake_checks["CLEANUP_DOZE_B"]
    final_sleep_a = wake_checks["FINAL_SLEEP_A"]
    final_sleep_b = wake_checks["FINAL_SLEEP_B"]
    button = wake_checks["BUTTON_ASSERTED"]
    woke_a = wake_checks["WOKE_A"]
    woke_b = wake_checks["WOKE_B"]
    if not (
        DEEP_DOZE_START <= doze_a[0] < DEEP_DOZE_END
        and DEEP_DOZE_START <= doze_b[0] < DEEP_DOZE_END
    ):
        return _failure("warm boot did not enter DeepDoze", run_dir)
    if doze_b[1] != POFF or not (doze_b[3] & ON_BUTTON_POSITIVE):
        return _failure("warm DeepDoze did not enable the on-button", run_dir)
    if not (
        DEEP_DOZE_START <= cleanup_doze_a[0] < DEEP_DOZE_END
        and (
            cleanup_doze_b[0] == DOZE_STOP
            or DEEP_DOZE_START <= cleanup_doze_b[0] < DEEP_DOZE_END
        )
    ):
        return _failure(
            "retained shutdown did not stop the CPU in a doze routine", run_dir
        )
    if (
        final_sleep_a[0] != WAIT_FOR_POWER_DOWN
        or final_sleep_b[0] != WAIT_FOR_POWER_DOWN
        or final_sleep_a[6] & POWER_VCC_ON
        or final_sleep_b[6] & POWER_VCC_ON
    ):
        return _failure("warm boot did not finish in VCC-off sleep", run_dir)
    if not (button[6] & POWER_ON_BUTTON_STATUS):
        return _failure("powerControl did not report the held button", run_dir)
    if not (button[2] & ON_BUTTON_POSITIVE):
        return _failure("Dino did not retain the on-button rising edge", run_dir)
    if button[6] & POWER_STOP_CPU:
        return _failure("on-button did not release StopCpu", run_dir)
    for label, checkpoint in (("WOKE_A", woke_a), ("WOKE_B", woke_b)):
        if (
            DEEP_DOZE_START <= checkpoint[0] < DEEP_DOZE_END
            or checkpoint[0] == WAIT_FOR_POWER_DOWN
        ):
            return _failure(f"{label} remained inside the power-down path", run_dir)
        if not checkpoint[6] & POWER_VCC_ON:
            return _failure(f"{label} left VCC off", run_dir)
        if checkpoint[6] & POWER_STOP_CPU:
            return _failure(f"{label} reasserted StopCpu", run_dir)

    expected_snapshots = (
        "01-suspended.png",
        "02-warm-doze.png",
        "03-woke.png",
    )
    for name in expected_snapshots:
        if not (run_dir / "snapshots" / name).is_file():
            return _failure(f"snapshot was not written: {name}", run_dir)

    print(
        "PASS: retained-RAM shutdown reached VCC-off sleep and the Dino "
        "on-button edge restored the OS"
    )
    print(f"Snapshot: {run_dir / 'snapshots' / '03-woke.png'}")
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    return run_regression(args)


if __name__ == "__main__":
    raise SystemExit(main())
