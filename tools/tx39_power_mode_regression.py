#!/usr/bin/env python3
"""Verify R3900 Config Halt/Doze stall and physical-interrupt wake."""

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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "tx39-power-mode-regression"
RESULT_PATTERN = re.compile(
    rb"POWER HALT_STALL CONFIG=([0-9A-F]{8}) MARKER=([0-9A-F]{8}).*"
    rb"POWER HALT_WAKE CONFIG=([0-9A-F]{8}) MARKER=([0-9A-F]{8}).*"
    rb"POWER DOZE_STALL CONFIG=([0-9A-F]{8}) MARKER=([0-9A-F]{8}).*"
    rb"POWER DOZE_WAKE CONFIG=([0-9A-F]{8}) MARKER=([0-9A-F]{8})",
    re.DOTALL,
)
EXPECTED = (0x100, 0, 0, 1, 0x200, 0, 0, 2)


def automation_script() -> str:
    """Return injected Halt/Doze programs and a masked Dino timer wake."""
    return r"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0
local phase = "boot"
local phase_frame = 0
local DINO = 0x10c00000

local function stop_wake_timer()
    program:write_u32(DINO + 0x150, 0x00000000) -- timerControl
    program:write_u32(DINO + 0x128, 0x00000000) -- interrupt5Enable
    program:write_u32(DINO + 0x12c, 0x00000000) -- interrupt6Enable
    program:write_u32(DINO + 0x110, 0xffffffff) -- clear interrupt5
end

local function start_wake_timer()
    stop_wake_timer()
    program:write_u32(DINO + 0x1c0, 0x00008000) -- timer master clock
    program:write_u32(DINO + 0x154, 0x00000800) -- 62.5 ms
    program:write_u32(DINO + 0x128, 0x20000000) -- periodic enable
    program:write_u32(DINO + 0x12c, 0x00040000) -- global enable
    program:write_u32(DINO + 0x150, 0x00000010) -- periodic start
end

local function run_mode(address, config, marker)
    program:write_u32(address + 0x00, 0x24010000 | config)
    program:write_u32(address + 0x04, 0x40811800) -- mtc0 r1,Config
    program:write_u32(address + 0x08, 0x24020000 | marker)
    program:write_u32(address + 0x0c, 0x1000ffff) -- b .
    program:write_u32(address + 0x10, 0x00000000) -- nop
    cpu.state["Config"].value =
        (cpu.state["Config"].value & 0x003f0000) | 0x00000030
    cpu.state["SR"].value = 0 -- the physical interrupt is masked
    cpu.state["Cause"].value = 0
    cpu.state["R2"].value = 0
    start_wake_timer()
    cpu.state["PC"].value = 0xa0000000 | address
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 10 then
        run_mode(0x00001e00, 0x00000130, 1)
        phase = "halt_stall"
        phase_frame = frames
    elseif phase == "halt_stall" and frames == phase_frame + 1 then
        print(string.format(
            "POWER HALT_STALL CONFIG=%08X MARKER=%08X",
            cpu.state["Config"].value & 0x00000300,
            cpu.state["R2"].value))
        phase = "halt_wake"
    elseif phase == "halt_wake" and cpu.state["R2"].value == 1 then
        print(string.format(
            "POWER HALT_WAKE CONFIG=%08X MARKER=%08X",
            cpu.state["Config"].value & 0x00000300,
            cpu.state["R2"].value))
        stop_wake_timer()
        run_mode(0x00001e40, 0x00000230, 2)
        phase = "doze_stall"
        phase_frame = frames
    elseif phase == "doze_stall" and frames == phase_frame + 1 then
        print(string.format(
            "POWER DOZE_STALL CONFIG=%08X MARKER=%08X",
            cpu.state["Config"].value & 0x00000300,
            cpu.state["R2"].value))
        phase = "doze_wake"
    elseif phase == "doze_wake" and cpu.state["R2"].value == 2 then
        print(string.format(
            "POWER DOZE_WAKE CONFIG=%08X MARKER=%08X",
            cpu.state["Config"].value & 0x00000300,
            cpu.state["R2"].value))
        stop_wake_timer()
        machine:exit()
    elseif frames >= 100 then
        print(string.format("POWER TIMEOUT PHASE=%s", phase))
        machine:exit()
    end
end)
"""


def parse_result(output: bytes) -> tuple[int, ...] | None:
    """Return all reported Config and marker values."""
    match = RESULT_PATTERN.search(output)
    if not match:
        return None
    return tuple(int(value, 16) for value in match.groups())


def verify_result(result: tuple[int, ...] | None) -> list[str]:
    """Compare observations with the Toshiba power-mode contract."""
    if result is None:
        return ["missing TX39 power-mode result"]
    return [
        f"field {index} {actual:#x} does not match {expected:#x}"
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
    lua_path = run_dir / "tx39-power-mode-regression.lua"
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
        print(
            f"error: unable to run TX39 power-mode regression: {error}",
            file=sys.stderr,
        )
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
        "PASS: R3900 Config Halt and Doze stall before the following "
        "instruction, and a masked physical Dino interrupt clears each mode "
        "and resumes that instruction"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
