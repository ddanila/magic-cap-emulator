#!/usr/bin/env python3
"""Record, stop, and play a Magic Cap sound stamp through the real UI."""

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
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "sound-stamp-regression"
SCREEN_RATE = 60.0
RESULT_PATTERN = re.compile(
    rb"SOUND_STAMP RX_START=(\d+) RX_STOP=(\d+) "
    rb"PLAY_START=(\d+) PLAY_STOP=(\d+) NONZERO=(\d+) "
    rb"MIN=(-?\d+) MAX=(-?\d+) CROSSINGS=(\d+) "
    rb"AUDIO_VALID=(\d+) SIB_STATE=(\d+) "
    rb"QUEUE_WRITE=(\d+) QUEUE_READ=(\d+)"
)


@dataclass(frozen=True)
class Result:
    rx_start: int
    rx_stop: int
    play_start: int
    play_stop: int
    nonzero: int
    minimum: int
    maximum: int
    crossings: int
    audio_valid: int
    sib_state: int
    queue_write: int
    queue_read: int


@dataclass(frozen=True)
class Playback:
    start: float
    duration: float
    peak: int
    frequency: float


def config_xml(system: str) -> str:
    return f"""<?xml version="1.0"?>
<mameconfig version="10">
    <system name="{system}">
        <input>
            <keyboard tag=":magicbus_keyboard" enabled="1" />
            <keyboard tag=":terminal:keyboard" enabled="0" />
            <port tag=":MICROPHONE_SOURCE" type="CONFIG"
                  mask="3" defvalue="0" value="1" />
        </input>
    </system>
</mameconfig>
"""


def automation_script(max_frames: int = 5000) -> str:
    return f"""local machine = manager.machine
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local program = machine.devices[":maincpu"].spaces["program"]
local frames = 0
local dino = 0x10c00000
local rx_start = 0
local rx_stop = 0
local play_start = 0
local play_stop = 0
local receive_base = 0
local metrics_done = false
local stop_pressed = false
local stop_released = false
local play_pressed = false
local play_released = false
local audio_valid = 0
local nonzero = 0
local minimum = 0
local maximum = 0
local crossings = 0

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

local function release()
    touch_button:set_value(0)
end

local function measure_capture()
    nonzero = 0
    minimum = 32767
    maximum = -32768
    crossings = 0
    local previous = nil
    for offset = 0, 2046, 2 do
        local sample = program:read_u16(receive_base + offset)
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
    metrics_done = true
end

local function finish()
    print(string.format(
        "SOUND_STAMP RX_START=%d RX_STOP=%d PLAY_START=%d PLAY_STOP=%d " ..
        "NONZERO=%d MIN=%d MAX=%d CROSSINGS=%d AUDIO_VALID=%d " ..
        "SIB_STATE=%d QUEUE_WRITE=%d QUEUE_READ=%d",
        rx_start, rx_stop, play_start, play_stop,
        nonzero, minimum, maximum, crossings, audio_valid,
        program:read_u16(0x0000c220),
        program:read_u32(0x0000c42c),
        program:read_u32(0x0000c430)))
    machine:exit()
end

emu.register_frame_done(function()
    frames = frames + 1
    local dma = program:read_u32(dino + 0x090)
    local rx_enabled = (dma & 0x00020000) ~= 0
    local tx_enabled = (dma & 0x00010000) ~= 0

    if rx_enabled then
        if rx_start == 0 then
            rx_start = frames
            receive_base = program:read_u32(dino + 0x064) & 0x1fffffff
        end
        if (program:read_u32(dino + 0x088) & 0x00020000) ~= 0 then
            audio_valid = 1
        end
    elseif rx_start ~= 0 and rx_stop == 0 then
        rx_stop = frames
        machine.screens[":screen"]:snapshot("sound-stamp-recorded.png")
    end

    if rx_start ~= 0 and not metrics_done and frames == rx_start + 45 then
        measure_capture()
    elseif rx_start ~= 0 and not stop_pressed and frames == rx_start + 90 then
        press(350, 148)
        stop_pressed = true
    elseif stop_pressed and not stop_released
            and frames == rx_start + 110 then
        release()
        stop_released = true
    end

    if rx_stop ~= 0 and not play_pressed and frames == rx_stop + 300 then
        machine.screens[":screen"]:snapshot("sound-stamp-ready.png")
        press(420, 148)
        play_pressed = true
    elseif play_pressed and not play_released
            and frames == rx_stop + 320 then
        release()
        play_released = true
    end

    if play_pressed and tx_enabled and play_start == 0 then
        play_start = frames
    elseif play_start ~= 0 and not tx_enabled and play_stop == 0 then
        play_stop = frames
        machine.screens[":screen"]:snapshot("sound-stamp-played.png")
    end

    if play_stop ~= 0 and frames == play_stop + 30 then
        finish()
    end

    if frames == 1200 then
        press(444, 10)
    elseif frames == 1220 then
        release()
    elseif frames == 1400 then
        press(240, 165)
    elseif frames == 1420 then
        release()
    elseif frames == 1700 then
        press(333, 41)
    elseif frames == 1720 then
        release()
    elseif frames == 1900 then
        press(103, 300)
    elseif frames == 1920 then
        release()
    elseif frames == 2100 then
        press(350, 106)
    elseif frames == 2120 then
        release()
    elseif frames == 2300 then
        press(170, 100)
    elseif frames == 2320 then
        release()
    elseif frames == 2500 then
        press(174, 100)
    elseif frames == 2520 then
        release()
    elseif frames == 2800 then
        machine.screens[":screen"]:snapshot("sound-stamp-controls.png")
    elseif frames == 3000 then
        press(279, 148)
    elseif frames == 3020 then
        release()
    elseif frames == {max_frames} then
        finish()
    end
end)
"""


def resolve_nvram_source(source: Path, system: str) -> Path:
    """Accept either an NVRAM root or its system-specific child."""
    source = source.expanduser().resolve()
    if (source / system / "ram").is_file():
        return source
    if source.name == system and (source / "ram").is_file():
        return source.parent
    raise ValueError(
        f"{source} does not contain {system}/ram and is not that directory"
    )


def parse_result(output: bytes) -> Result | None:
    match = RESULT_PATTERN.search(output)
    if not match:
        return None
    return Result(*(int(value) for value in match.groups()))


def _segment(
    samples: list[int], start: int, end: int, sample_rate: int
) -> Playback:
    body = samples[start:end]
    crossings = sum(
        (left < 0) != (right < 0) for left, right in zip(body, body[1:])
    )
    duration = len(body) / sample_rate
    return Playback(
        start=start / sample_rate,
        duration=duration,
        peak=max(abs(sample) for sample in body),
        frequency=crossings / (2.0 * duration) if duration else 0.0,
    )


def audible_segments(
    path: Path, threshold: int = 150, window: float = 0.01
) -> list[Playback]:
    """Return occupied regions from the most active 16-bit PCM channel."""
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


def playback_for(result: Result, segments: list[Playback]) -> Playback | None:
    expected = result.play_start / SCREEN_RATE
    return min(
        (
            segment
            for segment in segments
            if abs(segment.start - expected) <= 0.25
        ),
        key=lambda segment: abs(segment.start - expected),
        default=None,
    )


def verify_result(
    result: Result | None, playback: Playback | None
) -> tuple[bool, str]:
    if result is None:
        return False, "sound-stamp checkpoint is missing"
    if not 0 < result.rx_start < result.rx_stop:
        return False, "Magic Cap did not start and stop sound receive DMA"
    if result.nonzero < 900:
        return False, f"capture populated only {result.nonzero}/1024 samples"
    if result.minimum > -11_000 or result.maximum < 11_000:
        return False, (
            f"capture range is only {result.minimum}..{result.maximum}"
        )
    if not 250 <= result.crossings <= 320:
        return False, (
            f"capture has {result.crossings} crossings, expected 250-320"
        )
    if not result.audio_valid:
        return False, "Dino never reported a valid SIB audio slot"
    if result.sib_state:
        return False, f"SIB command state remained {result.sib_state}"
    if result.queue_write != result.queue_read:
        return False, (
            "SIB command queue did not drain "
            f"({result.queue_write} != {result.queue_read})"
        )
    if not result.rx_stop < result.play_start < result.play_stop:
        return False, "Magic Cap did not start and stop playback after recording"
    play_frames = result.play_stop - result.play_start
    if not 100 <= play_frames <= 300:
        return False, f"playback lasted {play_frames} frames, expected 100-300"
    if playback is None:
        return False, "WAV has no audible segment at the playback DMA start"
    expected_duration = play_frames / SCREEN_RATE
    if abs(playback.duration - expected_duration) > 0.3:
        return False, (
            f"WAV segment lasts {playback.duration:.2f}s, while DMA ran "
            f"{expected_duration:.2f}s"
        )
    if playback.peak < 5_000:
        return False, f"WAV playback peak {playback.peak} is below 5000"
    return True, (
        f"recorded {result.nonzero}/1024 tone samples, drained the stop "
        f"command, and played {playback.duration:.2f}s of audio "
        f"(peak {playback.peak})"
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--system", default="datarover840")
    parser.add_argument(
        "--nvram-source",
        type=Path,
        required=True,
        help="personalized workbench NVRAM root or datarover840 directory",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    try:
        nvram_source = resolve_nvram_source(args.nvram_source, args.system)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if not mame.is_file() or not rompath.is_dir():
        print("error: MAME executable or ROM directory is missing", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    cfg_dir = run_dir / "cfg"
    nvram_dir = run_dir / "nvram"
    snapshot_dir = run_dir / "snapshots"
    cfg_dir.mkdir(parents=True)
    snapshot_dir.mkdir()
    shutil.copytree(nvram_source, nvram_dir)
    lua_path = run_dir / "sound-stamp.lua"
    wav_path = run_dir / "sound-stamp.wav"
    log_path = run_dir / "mame-output.txt"
    (cfg_dir / f"{args.system}.cfg").write_text(
        config_xml(args.system), encoding="utf-8"
    )
    lua_path.write_text(automation_script(), encoding="utf-8")

    command = [
        str(mame),
        args.system,
        "-rompath",
        str(rompath),
        "-cfg_directory",
        str(cfg_dir),
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
        "-video",
        "none",
        "-videodriver",
        "dummy",
        "-audiodriver",
        "dummy",
        "-sound",
        "sdl",
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
            cwd=mame.parent,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired as error:
        log_path.write_bytes(error.stdout or b"")
        print(f"error: MAME timed out; artifacts: {run_dir}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"error: unable to run MAME: {error}; artifacts: {run_dir}", file=sys.stderr)
        return 2
    log_path.write_bytes(completed.stdout)
    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; "
            f"see {log_path}",
            file=sys.stderr,
        )
        return 2

    result = parse_result(completed.stdout)
    try:
        segments = audible_segments(wav_path)
    except (OSError, ValueError, wave.Error) as error:
        print(f"FAIL: invalid WAV capture: {error}; artifacts: {run_dir}", file=sys.stderr)
        return 1
    passed, message = verify_result(
        result, playback_for(result, segments) if result else None
    )
    if not passed:
        print(f"FAIL: {message}; artifacts: {run_dir}", file=sys.stderr)
        return 1
    print(f"PASS: Magic Cap sound stamp {message}")
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
