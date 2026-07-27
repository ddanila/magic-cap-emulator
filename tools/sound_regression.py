#!/usr/bin/env python3
"""Capture and verify the DataRover ROM's sound output.

Two checkpoints:

  beep  the hardware-generated startup tone, produced through Betty's
        unbuffered sound-hold register
  dma   buffered SIB sound DMA - the OS programs sibSize, sibSoundTxStart and
        sibDMA, and Dino streams the buffer to the speaker on its own. See
        docs/betty-registers.md.
"""

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
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "sound-regression"


# The buffered chime the OS plays during boot. The buffer is 1024 words of two
# 16-bit samples at roughly 11 kHz - about 190 ms - but it is a continuously
# serviced two-half ring, not a one-shot: the ROM refills each half from its
# half and full interrupt handlers, so the chime runs far longer than one pass.
# Measured at 2140 ms, repeatably. The bounds are wide enough to tolerate a
# different chime without accepting a single unrefilled pass.
DMA_MIN_DURATION = 1.0
DMA_MAX_DURATION = 4.0
DMA_MIN_PEAK = 1_000
DMA_SEGMENT_THRESHOLD = 150


@dataclass(frozen=True)
class Segment:
    start: float
    duration: float
    peak: int
    frequency: float


@dataclass(frozen=True)
class Tone:
    channel: int
    frequency: float
    duration: float
    peak: int
    nonzero: int


def automation_script(frames: int = 240) -> str:
    """Return a boot script that exits after the requested frame."""
    return f"""local machine = manager.machine
local frames = 0

emu.register_frame_done(function()
    frames = frames + 1
    if frames == {frames} then
        machine:exit()
    end
end)
"""


def find_segments(
    samples: list[int],
    sample_rate: int,
    threshold: int = DMA_SEGMENT_THRESHOLD,
    window: float = 0.01,
) -> list[Segment]:
    """Split one channel into audible segments separated by silence."""
    span = max(1, int(sample_rate * window))
    segments: list[Segment] = []
    start: int | None = None
    end = 0
    for index in range(0, max(0, len(samples) - span), span):
        chunk = samples[index : index + span]
        if max(abs(sample) for sample in chunk) > threshold:
            if start is None:
                start = index
            end = index + span
        elif start is not None:
            segments.append(_segment(samples, start, end, sample_rate))
            start = None
    if start is not None:
        segments.append(_segment(samples, start, end, sample_rate))
    return segments


def _segment(
    samples: list[int], start: int, end: int, sample_rate: int
) -> Segment:
    body = samples[start:end]
    crossings = sum(
        (left < 0) != (right < 0) for left, right in zip(body, body[1:])
    )
    duration = len(body) / sample_rate
    return Segment(
        start=start / sample_rate,
        duration=duration,
        peak=max(abs(sample) for sample in body),
        frequency=crossings / (2.0 * duration) if duration else 0.0,
    )


def loudest_channel(path: Path) -> tuple[list[int], int]:
    """Return the samples of the most occupied channel, plus its rate."""
    with wave.open(str(path), "rb") as capture:
        channels = capture.getnchannels()
        sample_rate = capture.getframerate()
        raw = capture.readframes(capture.getnframes())
    interleaved = struct.unpack(f"<{len(raw) // 2}h", raw)
    best = max(
        (list(interleaved[channel::channels]) for channel in range(channels)),
        key=lambda samples: sum(1 for sample in samples if sample),
        default=[],
    )
    return best, sample_rate


def verify_dma(segments: list[Segment]) -> tuple[bool, str]:
    """The buffered chime must follow the startup beep as its own segment."""
    if len(segments) < 2:
        return False, (
            f"expected the startup beep and a buffered DMA segment, "
            f"found {len(segments)} audible segment(s)"
        )
    chime = segments[-1]
    if not DMA_MIN_DURATION <= chime.duration <= DMA_MAX_DURATION:
        return False, (
            f"buffered segment lasts {chime.duration * 1000:.0f} ms, expected "
            f"{DMA_MIN_DURATION * 1000:.0f}-{DMA_MAX_DURATION * 1000:.0f} ms"
        )
    if chime.peak < DMA_MIN_PEAK:
        return False, (
            f"buffered segment peaks at {chime.peak}, below {DMA_MIN_PEAK}"
        )
    return True, (
        f"buffered SIB sound DMA played {chime.duration * 1000:.0f} ms at "
        f"t={chime.start:.2f}s, {chime.frequency:.0f} Hz, peak {chime.peak}"
    )


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
    parser.add_argument(
        "--checkpoint",
        choices=("beep", "dma"),
        default="beep",
        help=(
            "'beep' verifies the unbuffered startup tone (default); 'dma' runs "
            "long enough for the OS to play its buffered chime through SIB DMA"
        ),
    )
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
    # The buffered chime starts around frame 863 of a cold boot, so the DMA
    # checkpoint has to keep running well past the startup beep.
    exit_frame = 1200 if args.checkpoint == "dma" else 240
    lua_path.write_text(automation_script(exit_frame), encoding="utf-8")

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
            timeout=60 if args.checkpoint == "beep" else 300,
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

    if args.checkpoint == "dma":
        try:
            samples, sample_rate = loudest_channel(wav_path)
        except (OSError, EOFError, wave.Error, ValueError) as error:
            print(
                f"FAIL: invalid WAV capture: {error}; see {wav_path}",
                file=sys.stderr,
            )
            return 1
        segments = find_segments(samples, sample_rate)
        passed, message = verify_dma(segments)
        if not passed:
            print(f"FAIL: {message}; see {wav_path}", file=sys.stderr)
            for segment in segments:
                print(
                    f"  segment t={segment.start:.2f}s "
                    f"{segment.duration * 1000:.0f} ms peak={segment.peak}",
                    file=sys.stderr,
                )
            return 1
        print(f"PASS: {message}")
        print(f"Capture: {wav_path}")
        print(f"Artifacts: {run_dir}")
        return 0

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
