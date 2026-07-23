#!/usr/bin/env python3
"""Capture and verify the DataRover ROM's hardware-generated startup beep."""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = Path.home() / "fun" / "magic-cap-assets" / "roms"
DEFAULT_WORKDIR = (
    Path.home()
    / "fun"
    / "magic-cap-assets"
    / "runtime"
    / "sound-regression"
)


@dataclass(frozen=True)
class Tone:
    channel: int
    frequency: float
    duration: float
    peak: int
    nonzero: int


def automation_script() -> str:
    """Return a short boot script that exits after the ROM emits its beep."""
    return """local machine = manager.machine
local frames = 0

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 240 then
        machine:exit()
    end
end)
"""


def analyze_samples(samples: list[int], sample_rate: int, channel: int) -> Tone | None:
    """Measure the occupied portion of one signed-PCM channel."""
    occupied = [index for index, sample in enumerate(samples) if sample]
    if not occupied:
        return None

    first, last = occupied[0], occupied[-1]
    tone = samples[first : last + 1]
    crossings = sum(
        (left < 0) != (right < 0)
        for left, right in zip(tone, tone[1:])
    )
    duration = len(tone) / sample_rate
    frequency = crossings / (2.0 * duration)
    return Tone(
        channel=channel,
        frequency=frequency,
        duration=duration,
        peak=max(abs(sample) for sample in tone),
        nonzero=len(occupied),
    )


def analyze_wave(path: Path) -> Tone | None:
    """Return the most populated 16-bit PCM channel in a WAV capture."""
    with wave.open(str(path), "rb") as capture:
        if capture.getsampwidth() != 2:
            raise ValueError(
                f"expected 16-bit PCM, got {capture.getsampwidth() * 8}-bit"
            )
        channels = capture.getnchannels()
        sample_rate = capture.getframerate()
        raw = capture.readframes(capture.getnframes())

    interleaved = struct.unpack(f"<{len(raw) // 2}h", raw)
    tones = [
        tone
        for channel in range(channels)
        if (
            tone := analyze_samples(
                list(interleaved[channel::channels]), sample_rate, channel
            )
        )
    ]
    return max(tones, key=lambda tone: tone.nonzero, default=None)


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
    lua_path = run_dir / "sound-regression.lua"
    wav_path = run_dir / "boot.wav"
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
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"error: unable to capture sound: {error}", file=sys.stderr)
        return 2

    log_path.write_bytes(completed.stdout)
    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; see {log_path}",
            file=sys.stderr,
        )
        return 2
    if not wav_path.is_file():
        print(f"FAIL: WAV capture was not written: {wav_path}", file=sys.stderr)
        return 1

    try:
        tone = analyze_wave(wav_path)
    except (OSError, EOFError, wave.Error, ValueError) as error:
        print(f"FAIL: invalid WAV capture: {error}; see {wav_path}", file=sys.stderr)
        return 1
    if (
        tone is None
        or not 700.0 <= tone.frequency <= 850.0
        or not 0.04 <= tone.duration <= 0.09
        or tone.peak < 1_000
    ):
        print(f"FAIL: unexpected startup tone {tone!r}; see {wav_path}", file=sys.stderr)
        return 1

    print(
        "PASS: ROM startup beep "
        f"{tone.frequency:.1f} Hz, {tone.duration * 1_000:.1f} ms, "
        f"peak {tone.peak}"
    )
    print(f"Capture: {wav_path}")
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
