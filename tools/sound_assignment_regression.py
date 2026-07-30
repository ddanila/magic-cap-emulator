#!/usr/bin/env python3
"""Explore and verify Magic Cap system-sound reassignment."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
MAME = REPO_ROOT.parent / "mame" / "datarover"
ROMPATH = ASSETS_ROOT / "roms"
WORKDIR = ASSETS_ROOT / "runtime" / "sound-assignment-regression"
ASSIGNMENT_PATTERN = re.compile(
    rb"SOUND_ASSIGNMENT can_sound=(\d+) can_coupon=(\d+) set_sound=(\d+) "
    rb"coupon_accepted=(\d+) coupon_apply=(\d+) action=(\d+) button=([0-9A-F]+)"
)
RETAINED_PATTERN = re.compile(rb"SOUND_RETAINED action=(\d+) button=([0-9A-F]+)")
SCREEN_RATE = 60
LIVE_PREVIEW_FRAME = 5000
RETAINED_PREVIEW_FRAME = 1600


@dataclass(frozen=True)
class AssignmentResult:
    can_sound: int
    can_coupon: int
    set_sound: int
    coupon_accepted: int
    coupon_apply: int
    action: int
    button: int


@dataclass(frozen=True)
class RetainedResult:
    action: int
    button: int


@dataclass(frozen=True)
class Playback:
    start: float
    duration: float
    peak: int
    frequency: float


def action_addresses(system: str) -> tuple[int, int]:
    if system == "datarover840d":
        return 0x13DD85FC, 0x13DD864C
    return 0x13DD766C, 0x13DD76BC


def automation_script(system: str = "datarover840") -> str:
    if system == "datarover840d":
        method_addresses = (
            0x13DD8500,
            0x13DD86B0,
            0x13DD87CC,
            0x13E41D70,
            0x13E41DC4,
        )
    else:
        method_addresses = (
            0x13DD7570,
            0x13DD7720,
            0x13DD783C,
            0x13E40F50,
            0x13E40FA4,
        )
    action_entry, _action_sound = action_addresses(system)
    trace_setup = rf"""
    local function watch(address, slot)
        program:write_u32(slot, 0)
        cpu.debug:bpset(address, "1", string.format(
            "do d@0x%08x=d@0x%08x+1; g", slot, slot))
    end
    watch(0x{method_addresses[0]:08x}, 0x00320000)
    watch(0x{method_addresses[1]:08x}, 0x00320004)
    watch(0x{method_addresses[2]:08x}, 0x00320008)
    watch(0x{method_addresses[3]:08x}, 0x0032000c)
    watch(0x{method_addresses[4]:08x}, 0x00320010)
    program:write_u32(0x00320014, 0)
    program:write_u32(0x00320018, 0)
    cpu.debug:bpset(0x{action_entry:08x}, "1",
        "do d@0x00320014=d@0x00320014+1; do d@0x00320018=R4; g")
"""
    trace_report = r"""
        print(string.format(
            "SOUND_ASSIGNMENT can_sound=%d can_coupon=%d set_sound=%d " ..
            "coupon_accepted=%d coupon_apply=%d action=%d button=%08X",
            program:read_u32(0x00320000),
            program:read_u32(0x00320004),
            program:read_u32(0x00320008),
            program:read_u32(0x0032000c),
            program:read_u32(0x00320010),
            program:read_u32(0x00320014),
            program:read_u32(0x00320018)))
"""
    return rf"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local frames = 0
{trace_setup}

local function move(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
end

local function press(x, y)
    move(x, y)
    touch_button:set_value(1)
end

local function release()
    touch_button:set_value(0)
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 700 then
        press(395, 23)
    elseif frames == 720 then
        release()
    elseif frames == 1000 then
        machine.screens[":screen"]:snapshot("01-desk.png")
        press(455, 8)
    elseif frames == 1020 then release()
    elseif frames == 1300 then press(424, 108)
    elseif frames == 1320 then release()
    elseif frames == 1600 then press(140, 65)
    elseif frames == 1620 then release()
    elseif frames == 1900 then
        machine.screens[":screen"]:snapshot("02-general-controls.png")
        press(232, 182)
    elseif frames == 1920 then release()
    elseif frames == 2200 then
        machine.screens[":screen"]:snapshot("03-construction-mode.png")
        press(103, 300)
    elseif frames == 2220 then release()
    elseif frames == 2800 then
        machine.screens[":screen"]:snapshot("04-magic-hat.png")
        press(340, 117)
    elseif frames == 2820 then release()
    elseif frames == 3100 then
        machine.screens[":screen"]:snapshot("05-sound-coupons.png")
        press(48, 262)
    elseif frames > 3100 and frames < 3240 then
        local fraction = (frames - 3100) / 140
        move(48 + fraction * 192, 262 + fraction * 38)
    elseif frames == 3240 then release()
    elseif frames == 3500 then
        machine.screens[":screen"]:snapshot("06-sound-coupon-in-tote.png")
        press(430, 8)
    elseif frames == 3520 then release()
    elseif frames == 3800 then press(340, 75)
    elseif frames == 3820 then release()
    elseif frames == 4100 then
        machine.screens[":screen"]:snapshot("07-sound-effects.png")
        press(240, 300)
    elseif frames == 4120 then release()
    elseif frames == 4500 then
        machine.screens[":screen"]:snapshot("08-sound-coupon-over-effects.png")
        press(168, 140)
    elseif frames > 4500 and frames < 4640 then
        local fraction = (frames - 4500) / 140
        move(168 + fraction * 262, 140 - fraction * 55)
    elseif frames == 4640 then move(430, 85)
    elseif frames == 4700 then
        machine.screens[":screen"]:snapshot("09-coupon-hovering.png")
    elseif frames == 4720 then release()
    elseif frames == 5000 then
        machine.screens[":screen"]:snapshot("10-alarm-assigned.png")
        press(430, 85)
    elseif frames == 5020 then release()
    elseif frames == 5400 then
        machine.screens[":screen"]:snapshot("11-assigned-effect-played.png")
        press(382, 84)
    elseif frames == 5420 then release()
    elseif frames == 5700 then press(430, 8)
    elseif frames == 5720 then release()
    elseif frames == 6000 then
        machine.screens[":screen"]:snapshot("12-assignment-left.png")
        press(430, 8)
    elseif frames == 6020 then release()
    elseif frames == 6400 then
        machine.screens[":screen"]:snapshot("13-hallway-committed.png")
{trace_report}
        machine:exit()
    end
end)
"""


def retained_automation_script(system: str = "datarover840") -> str:
    action_entry, _action_sound = action_addresses(system)
    return rf"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local frames = 0

program:write_u32(0x00320014, 0)
program:write_u32(0x00320018, 0)
cpu.debug:bpset(0x{action_entry:08x}, "1",
    "do d@0x00320014=d@0x00320014+1; do d@0x00320018=R4; g")

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

local function release()
    touch_button:set_value(0)
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 1000 then
        machine.screens[":screen"]:snapshot("01-retained-hallway.png")
        press(424, 108)
    elseif frames == 1020 then release()
    elseif frames == 1300 then
        press(340, 75)
    elseif frames == 1320 then release()
    elseif frames == 1600 then
        machine.screens[":screen"]:snapshot("02-retained-sound-controls.png")
        press(430, 85)
    elseif frames == 1620 then release()
    elseif frames == 2100 then
        machine.screens[":screen"]:snapshot("03-retained-effect-played.png")
        print(string.format(
            "SOUND_RETAINED action=%d button=%08X",
            program:read_u32(0x00320014),
            program:read_u32(0x00320018)))
        machine:exit()
    end
end)
"""


def parse_assignment(output: bytes) -> AssignmentResult | None:
    match = ASSIGNMENT_PATTERN.search(output)
    if not match:
        return None
    values = [int(value) for value in match.groups()[:-1]]
    return AssignmentResult(*values, int(match.group(7), 16))


def parse_retained(output: bytes) -> RetainedResult | None:
    match = RETAINED_PATTERN.search(output)
    if not match:
        return None
    return RetainedResult(int(match.group(1)), int(match.group(2), 16))


def _segment(samples: list[int], start: int, end: int, sample_rate: int) -> Playback:
    body = samples[start:end]
    crossings = sum((left < 0) != (right < 0) for left, right in zip(body, body[1:]))
    duration = len(body) / sample_rate
    return Playback(
        start=start / sample_rate,
        duration=duration,
        peak=max((abs(sample) for sample in body), default=0),
        frequency=crossings / (2.0 * duration) if duration else 0.0,
    )


def audible_segments(
    path: Path, threshold: int = 150, window: float = 0.01
) -> list[Playback]:
    with wave.open(str(path), "rb") as capture:
        if capture.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM")
        channels = capture.getnchannels()
        sample_rate = capture.getframerate()
        raw = capture.readframes(capture.getnframes())
    interleaved = struct.unpack(f"<{len(raw) // 2}h", raw)
    samples = max(
        (list(interleaved[channel::channels]) for channel in range(channels)),
        key=lambda channel: sum(bool(sample) for sample in channel),
        default=[],
    )
    span = max(1, round(sample_rate * window))
    found: list[Playback] = []
    start: int | None = None
    end = 0
    for index in range(0, max(0, len(samples) - span), span):
        occupied = max(abs(sample) for sample in samples[index : index + span])
        if occupied > threshold:
            if start is None:
                start = index
            end = index + span
        elif start is not None:
            found.append(_segment(samples, start, end, sample_rate))
            start = None
    if start is not None:
        found.append(_segment(samples, start, end, sample_rate))
    return found


def playback_after(path: Path, frame: int) -> Playback | None:
    expected = frame / SCREEN_RATE
    return min(
        (
            segment
            for segment in audible_segments(path)
            if expected <= segment.start <= expected + 0.5
        ),
        key=lambda segment: segment.start,
        default=None,
    )


def verify_results(
    assignment: AssignmentResult | None,
    retained: RetainedResult | None,
    live: Playback | None,
    replayed: Playback | None,
) -> list[str]:
    failures: list[str] = []
    if assignment is None:
        failures.append("assignment method checkpoint is missing")
    else:
        if assignment.can_sound < 1 or assignment.can_coupon < 1:
            failures.append("the error button never evaluated the sound coupon")
        if assignment.set_sound != 1:
            failures.append(f"SetSound ran {assignment.set_sound} times, expected 1")
        if assignment.coupon_apply != 1 or assignment.coupon_accepted != 1:
            failures.append(
                "the alarm coupon was not applied and accepted exactly once"
            )
        if assignment.action != 1 or assignment.button == 0:
            failures.append("the reassigned error button was not tapped")
    if retained is None:
        failures.append("retained-state method checkpoint is missing")
    elif retained.action != 1 or retained.button == 0:
        failures.append("the retained error button was not tapped")
    if live is None:
        failures.append("live alarm playback is missing")
    if replayed is None:
        failures.append("retained alarm playback is missing")
    if live is not None and replayed is not None:
        if live.duration < 1.0 or not 450 <= live.frequency <= 750:
            failures.append(
                f"live reassigned playback did not have the alarm signature: {live!r}"
            )
        if abs(live.duration - replayed.duration) > 0.03:
            failures.append(
                "retained playback duration changed: "
                f"{live.duration:.2f}s -> {replayed.duration:.2f}s"
            )
        if abs(live.frequency - replayed.frequency) > 5:
            failures.append(
                "retained playback frequency changed: "
                f"{live.frequency:.1f}Hz -> {replayed.frequency:.1f}Hz"
            )
        if abs(live.peak - replayed.peak) > 300:
            failures.append(
                f"retained playback peak changed: {live.peak} -> {replayed.peak}"
            )
    return failures


def resolve_nvram_source(source: Path, system: str) -> Path:
    source = source.expanduser().resolve()
    if (source / system / "ram").is_file():
        return source
    if source.name == system and (source / "ram").is_file():
        return source.parent
    raise ValueError(
        f"{source} does not contain {system}/ram and is not that directory"
    )


def run_phase(
    args: argparse.Namespace,
    directory: Path,
    source: Path,
    script: str,
) -> tuple[int, bytes, Path]:
    nvram_dir = directory / "nvram"
    snapshot_dir = directory / "snapshots"
    shutil.copytree(resolve_nvram_source(source, args.system), nvram_dir)
    snapshot_dir.mkdir()
    lua_path = directory / "sound-assignment.lua"
    wav_path = directory / "sound-assignment.wav"
    log_path = directory / "mame-output.txt"
    lua_path.write_text(script, encoding="utf-8")
    command = [
        str(args.mame),
        args.system,
        "-rompath",
        str(args.rompath),
        "-nvram_directory",
        str(nvram_dir),
        "-snapshot_directory",
        str(snapshot_dir),
        "-snapview",
        "native",
        "-autoboot_delay",
        "0",
        "-autoboot_script",
        str(lua_path),
        "-debug",
        "-debugger",
        "none",
        "-video",
        "none",
        "-sound",
        "sdl",
        "-videodriver",
        "dummy",
        "-audiodriver",
        "dummy",
        "-wavwrite",
        str(wav_path),
        "-nothrottle",
        "-skip_gameinfo",
    ]
    environment = os.environ.copy()
    environment["SDL_AUDIODRIVER"] = "dummy"
    try:
        completed = subprocess.run(
            command,
            cwd=args.mame.parent,
            env=environment,
            capture_output=True,
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or b"") + (error.stderr or b"")
        log_path.write_bytes(output)
        return 124, output, wav_path
    except OSError as error:
        output = str(error).encode()
        log_path.write_bytes(output)
        return 127, output, wav_path
    output = completed.stdout + completed.stderr
    log_path.write_bytes(output)
    return completed.returncode, output, wav_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nvram-source", type=Path, required=True)
    parser.add_argument("--mame", type=Path, default=MAME)
    parser.add_argument("--rompath", type=Path, default=ROMPATH)
    parser.add_argument("--workdir", type=Path, default=WORKDIR)
    parser.add_argument("--system", default="datarover840")
    parser.add_argument("--retained-only", action="store_true")
    parser.add_argument("--timeout", type=float, default=300)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    args.mame = args.mame.expanduser().resolve()
    args.rompath = args.rompath.expanduser().resolve()
    if not args.mame.is_file() or not args.rompath.is_dir():
        print("error: MAME executable or ROM directory is missing", file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    run_dir.mkdir(parents=True)

    if args.retained_only:
        phase_dir = run_dir / "retained"
        phase_dir.mkdir()
        status, output, wav_path = run_phase(
            args,
            phase_dir,
            args.nvram_source,
            retained_automation_script(args.system),
        )
        if status:
            print(f"error: MAME exited with status {status}", file=sys.stderr)
            print(f"Artifacts: {run_dir}", file=sys.stderr)
            return 2
        result = parse_retained(output)
        try:
            playback = playback_after(wav_path, RETAINED_PREVIEW_FRAME)
        except (OSError, EOFError, ValueError, wave.Error) as error:
            print(f"FAIL: unable to inspect retained WAV: {error}", file=sys.stderr)
            print(f"Artifacts: {run_dir}", file=sys.stderr)
            return 1
        if result is None or result.action != 1 or playback is None:
            print("FAIL: retained sound checkpoint is incomplete", file=sys.stderr)
            print(f"Artifacts: {run_dir}", file=sys.stderr)
            return 1
        print(
            "PASS: retained error button played "
            f"{playback.duration:.2f}s at {playback.frequency:.1f} Hz"
        )
        print(f"Artifacts: {run_dir}")
        return 0

    assignment_dir = run_dir / "assignment"
    assignment_dir.mkdir()
    status, assignment_output, assignment_wav = run_phase(
        args,
        assignment_dir,
        args.nvram_source,
        automation_script(args.system),
    )
    if status:
        print(f"error: assignment MAME exited with status {status}", file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 2

    retained_dir = run_dir / "retained"
    retained_dir.mkdir()
    status, retained_output, retained_wav = run_phase(
        args,
        retained_dir,
        assignment_dir / "nvram",
        retained_automation_script(args.system),
    )
    if status:
        print(f"error: retained MAME exited with status {status}", file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 2

    try:
        live = playback_after(assignment_wav, LIVE_PREVIEW_FRAME)
        replayed = playback_after(retained_wav, RETAINED_PREVIEW_FRAME)
    except (OSError, EOFError, ValueError, wave.Error) as error:
        print(f"FAIL: unable to inspect sound WAVs: {error}", file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 1
    failures = verify_results(
        parse_assignment(assignment_output),
        parse_retained(retained_output),
        live,
        replayed,
    )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 1

    assert live is not None and replayed is not None
    print(
        "PASS: Magic Hat alarm coupon reassigned the error sound and retained "
        f"the {live.duration:.2f}s/{live.frequency:.1f}Hz playback across "
        f"relaunch (peak {live.peak} -> {replayed.peak})"
    )
    print(f"Artifacts: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
