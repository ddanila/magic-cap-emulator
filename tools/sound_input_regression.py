#!/usr/bin/env python3
"""Verify DataRover SIB sound-receive DMA with tone and silence."""

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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "sound-input-regression"
RESULT_PATTERN = re.compile(
    rb"SOUND_INPUT TONE_NONZERO=(\d+) MIN=(-?\d+) MAX=(-?\d+) "
    rb"CROSSINGS=(\d+) TONE_STATUS=([0-9A-F]{8}) "
    rb"TONE_DMA=([0-9A-F]{8}) SILENCE_NONZERO=(\d+) "
    rb"SILENCE_STATUS=([0-9A-F]{8}) SILENCE_DMA=([0-9A-F]{8})"
)
INTERRUPTS = 0x00640400
DMA_FINISHED = 0x80000000


def monitor_config() -> str:
    return """<?xml version="1.0"?>
<mameconfig version="10">
    <system name="datarover840">
        <input>
            <port tag=":BOOT_MODE" type="CONFIG"
                  mask="8" defvalue="8" value="0" />
            <port tag=":MICROPHONE_SOURCE" type="CONFIG"
                  mask="3" defvalue="0" value="1" />
        </input>
    </system>
</mameconfig>
"""


def automation_script() -> str:
    return r"""local machine = manager.machine
local program = machine.devices[":maincpu"].spaces["program"]
local source = machine.ioport.ports[":MICROPHONE_SOURCE"]:field(0x03)
local frames = 0
local stage = "tone"
local dino = 0x10c00000
local tone_buffer = 0x00020000
local silence_buffer = 0x00020400
local words = 64
local interrupt_mask = 0x00640400

local function clear_buffer(base)
    for offset = 0, (words * 4) - 4, 4 do
        program:write_u32(base + offset, 0)
    end
end

local function start_capture(base)
    program:write_u32(dino + 0x100, interrupt_mask)
    program:write_u32(dino + 0x060, (words - 1) << 18)
    program:write_u32(dino + 0x064, base)
    -- Enable SIB and sound, select signed 16-bit samples, and use divisor 25
    -- for the release ROM's 11.025 kHz rate.
    program:write_u32(dino + 0x074, 0x00009911)
    program:write_u32(dino + 0x090, 0x80020000)
end

local function metrics(base)
    local nonzero = 0
    local minimum = 32767
    local maximum = -32768
    local crossings = 0
    local previous = nil
    for offset = 0, (words * 4) - 2, 2 do
        local sample = program:read_u16(base + offset)
        if sample >= 0x8000 then sample = sample - 0x10000 end
        if sample ~= 0 then nonzero = nonzero + 1 end
        if sample < minimum then minimum = sample end
        if sample > maximum then maximum = sample end
        if previous ~= nil
                and ((previous < 0 and sample >= 0)
                    or (previous >= 0 and sample < 0)) then
            crossings = crossings + 1
        end
        previous = sample
    end
    return nonzero, minimum, maximum, crossings
end

local tone_nonzero = 0
local tone_min = 0
local tone_max = 0
local tone_crossings = 0
local tone_status = 0
local tone_dma = 0

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 30 then
        clear_buffer(tone_buffer)
        start_capture(tone_buffer)
    elseif frames == 60 and stage == "tone" then
        tone_nonzero, tone_min, tone_max, tone_crossings =
                metrics(tone_buffer)
        tone_status = program:read_u32(dino + 0x100) & interrupt_mask
        tone_dma = program:read_u32(dino + 0x090)
        source:set_value(2)
        stage = "silence-pending"
    elseif frames == 70 and stage == "silence-pending" then
        -- Configuration fields become live on the next input-frame update.
        clear_buffer(silence_buffer)
        start_capture(silence_buffer)
        stage = "silence"
    elseif frames == 100 and stage == "silence" then
        local silence_nonzero = metrics(silence_buffer)
        local silence_status =
                program:read_u32(dino + 0x100) & interrupt_mask
        local silence_dma = program:read_u32(dino + 0x090)
        print(string.format(
            "SOUND_INPUT TONE_NONZERO=%d MIN=%d MAX=%d CROSSINGS=%d " ..
            "TONE_STATUS=%08X TONE_DMA=%08X SILENCE_NONZERO=%d " ..
            "SILENCE_STATUS=%08X SILENCE_DMA=%08X",
            tone_nonzero, tone_min, tone_max, tone_crossings,
            tone_status, tone_dma, silence_nonzero,
            silence_status, silence_dma))
        machine:exit()
    end
end)
"""


def parse_result(output: bytes) -> tuple[int, ...] | None:
    match = RESULT_PATTERN.search(output)
    if not match:
        return None
    return tuple(
        int(value, 16) if index >= 4 and index != 6 else int(value)
        for index, value in enumerate(match.groups())
    )


def verify_result(result: tuple[int, ...] | None) -> tuple[bool, str]:
    if result is None:
        return False, "sound-input checkpoint is missing"
    (
        tone_nonzero,
        minimum,
        maximum,
        crossings,
        tone_status,
        tone_dma,
        silence_nonzero,
        silence_status,
        silence_dma,
    ) = result
    if tone_nonzero < 120:
        return False, f"test tone populated only {tone_nonzero}/128 samples"
    if minimum > -11_000 or maximum < 11_000:
        return False, f"test tone range is only {minimum}..{maximum}"
    if not 20 <= crossings <= 25:
        return False, f"test tone has {crossings} crossings, expected 20-25"
    if tone_status != INTERRUPTS or silence_status != INTERRUPTS:
        return False, (
            f"interrupt status is {tone_status:#010x}/{silence_status:#010x}, "
            f"expected {INTERRUPTS:#010x}"
        )
    if tone_dma != DMA_FINISHED or silence_dma != DMA_FINISHED:
        return False, (
            f"finished DMA is {tone_dma:#010x}/{silence_dma:#010x}, "
            f"expected {DMA_FINISHED:#010x}"
        )
    if silence_nonzero:
        return False, f"silence source wrote {silence_nonzero} nonzero samples"
    return True, (
        f"captured {tone_nonzero}/128 tone samples at {minimum}..{maximum} "
        f"with {crossings} crossings, then 128 silent samples"
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    if not mame.is_file() or not rompath.is_dir():
        print("error: MAME executable or ROM directory is missing", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    config_dir = run_dir / "cfg"
    config_dir.mkdir(parents=True)
    (config_dir / "datarover840.cfg").write_text(
        monitor_config(), encoding="utf-8"
    )
    script_path = run_dir / "sound-input.lua"
    script_path.write_text(automation_script(), encoding="utf-8")
    completed = subprocess.run(
        [
            str(mame),
            "datarover840",
            "-rompath",
            str(rompath),
            "-cfg_directory",
            str(config_dir),
            "-nvram_directory",
            str(run_dir / "nvram"),
            "-autoboot_delay",
            "0",
            "-autoboot_script",
            str(script_path),
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
        ],
        cwd=mame.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=60,
    )
    log_path = run_dir / "mame-output.txt"
    log_path.write_bytes(completed.stdout)
    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; "
            f"artifacts: {run_dir}",
            file=sys.stderr,
        )
        return 2
    passed, message = verify_result(parse_result(completed.stdout))
    if not passed:
        print(f"FAIL: {message}; artifacts: {run_dir}", file=sys.stderr)
        return 1
    print(f"PASS: SIB sound-receive DMA {message}")
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
