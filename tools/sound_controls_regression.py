#!/usr/bin/env python3
"""Drive Magic Cap's Controls -> Sound panel and verify its settings."""

from __future__ import annotations

import argparse
import os
import re
import struct
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "sound-controls-regression"
RESULT = re.compile(
    rb"SOUND_CONTROLS max_frame=(\d+) min_frame=(\d+)"
)


def automation_script() -> str:
    """Return first-boot calibration and Sound-panel navigation."""
    return r"""local machine = manager.machine
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local frames = 0
local repeated = nil
local stage = "boot"
local deadline = 0
local max_frame = 0
local min_frame = 0

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

local function release()
    touch_button:set_value(0)
end

local function repeat_tap(x, y, count, completion)
    repeated = {
        x = x, y = y, remaining = count, down = false,
        next_frame = frames + 10, completion = completion
    }
end

local function service_repeated_tap()
    if repeated == nil or frames < repeated.next_frame then return end
    if not repeated.down then
        press(repeated.x, repeated.y)
        repeated.down = true
        repeated.next_frame = frames + 8
    else
        release()
        repeated.down = false
        repeated.remaining = repeated.remaining - 1
        repeated.next_frame = frames + 8
        if repeated.remaining == 0 then
            local completion = repeated.completion
            repeated = nil
            completion()
        end
    end
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 1220 then press(240, 160)
    elseif frames == 1240 then release()
    elseif frames == 1420 then press(23, 23)
    elseif frames == 1440 then release()
    elseif frames == 1620 then press(456, 296)
    elseif frames == 1640 then release()
    elseif frames == 1820 then press(240, 160)
    elseif frames == 1840 then release()
    elseif frames == 2200 then press(455, 8)
    elseif frames == 2220 then release()
    elseif frames == 2500 then press(424, 108)
    elseif frames == 2520 then release()
    elseif frames == 2900 then
        machine.screens[":screen"]:snapshot("01-controls.png")
        press(340, 75)
    elseif frames == 2920 then release()
    elseif frames == 3300 then
        machine.screens[":screen"]:snapshot("02-sound-controls.png")
        repeat_tap(47, 80, 40, function()
            stage = "maximum"
            deadline = frames + 60
        end)
    end

    service_repeated_tap()

    if repeated == nil and frames == deadline then
        if stage == "maximum" then
            machine.screens[":screen"]:snapshot("03-volume-maximum.png")
            max_frame = frames
            press(160, 85)
            stage = "maximum-preview-down"
            deadline = frames + 20
        elseif stage == "maximum-preview-down" then
            release()
            stage = "maximum-preview"
            deadline = frames + 120
        elseif stage == "maximum-preview" then
            repeat_tap(47, 246, 40, function()
                stage = "minimum"
                deadline = frames + 60
            end)
        elseif stage == "minimum" then
            machine.screens[":screen"]:snapshot("04-volume-minimum.png")
            min_frame = frames
            press(160, 85)
            stage = "minimum-preview-down"
            deadline = frames + 20
        elseif stage == "minimum-preview-down" then
            release()
            stage = "minimum-preview"
            deadline = frames + 120
        elseif stage == "minimum-preview" then
            print(string.format(
                "SOUND_CONTROLS max_frame=%d min_frame=%d",
                max_frame, min_frame))
            machine:exit()
        end
    end
end)
"""


def parse_result(output: bytes) -> tuple[int, int] | None:
    """Extract the emulated-frame positions of both sound previews."""
    match = RESULT.search(output)
    return tuple(int(value) for value in match.groups()) if match else None


def slider_knob_center(snapshot: Path) -> float | None:
    """Locate the wide horizontal slider thumb while ignoring its thin track."""
    with Image.open(snapshot) as image:
        grayscale = image.convert("L")
        rows = []
        for y in range(92, 237):
            dark = sum(grayscale.getpixel((x, y)) < 128 for x in range(24, 71))
            if dark >= 12:
                rows.append(y)
    return (min(rows) + max(rows)) / 2 if rows else None


def preview_peak(path: Path, frame: int, duration_frames: int = 120) -> int:
    """Return the largest absolute WAV sample in an emulated-frame window."""
    with wave.open(str(path), "rb") as capture:
        if capture.getsampwidth() != 2:
            raise ValueError("expected 16-bit WAV capture")
        rate = capture.getframerate()
        start = max(0, int((frame / 60) * rate))
        count = int((duration_frames / 60) * rate)
        capture.setpos(min(start, capture.getnframes()))
        raw = capture.readframes(count)
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    return max((abs(sample) for sample in samples), default=0)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    if not mame.is_file() or not rompath.is_dir():
        print("error: MAME executable or ROM directory is missing", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    nvram_dir = run_dir / "nvram"
    snapshot_dir = run_dir / "snapshots"
    nvram_dir.mkdir(parents=True)
    snapshot_dir.mkdir()
    lua_path = run_dir / "sound-controls.lua"
    wav_path = run_dir / "sound-controls.wav"
    lua_path.write_text(automation_script(), encoding="utf-8")
    try:
        completed = subprocess.run(
            [
                str(mame),
                "datarover840",
                "-rompath",
                str(rompath),
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
                "-sound",
                "none",
                "-videodriver",
                "dummy",
                "-audiodriver",
                "dummy",
                "-wavwrite",
                str(wav_path),
                "-nothrottle",
                "-skip_gameinfo",
            ],
            cwd=mame.parent,
            capture_output=True,
            timeout=240,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"error: unable to complete MAME run: {error}", file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 2
    output = completed.stdout + completed.stderr
    (run_dir / "mame-output.txt").write_bytes(output)
    if completed.returncode:
        print(f"error: MAME exited with status {completed.returncode}", file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 2

    failures = []
    maximum_peak = 0
    minimum_peak = 0
    result = parse_result(output)
    if result is None:
        failures.append("sound preview checkpoint is missing")
    elif not wav_path.is_file():
        failures.append("sound WAV capture is missing")
    else:
        try:
            maximum_peak = preview_peak(wav_path, result[0])
            minimum_peak = preview_peak(wav_path, result[1])
        except (OSError, EOFError, ValueError, wave.Error) as error:
            failures.append(f"unable to inspect sound previews: {error}")
        else:
            if maximum_peak < 1_000:
                failures.append(f"maximum preview peak was only {maximum_peak}")
            if minimum_peak >= maximum_peak // 10:
                failures.append(
                    f"off preview peak {minimum_peak} was not below "
                    f"one tenth of maximum {maximum_peak}"
                )
    try:
        default_y = slider_knob_center(snapshot_dir / "02-sound-controls.png")
        maximum_y = slider_knob_center(snapshot_dir / "03-volume-maximum.png")
        minimum_y = slider_knob_center(snapshot_dir / "04-volume-minimum.png")
    except OSError as error:
        failures.append(f"unable to inspect volume snapshots: {error}")
        default_y = maximum_y = minimum_y = None
    if (
        default_y is None
        or maximum_y is None
        or minimum_y is None
        or not maximum_y < default_y < minimum_y
    ):
        failures.append(
            "volume thumb did not move default -> maximum -> minimum: "
            f"{(default_y, maximum_y, minimum_y)!r}"
        )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 1

    print(
        "PASS: Sound volume moved to both clamps; the same effect was audible "
        f"at maximum (peak {maximum_peak}) and silent at off "
        f"(peak {minimum_peak})"
    )
    print(f"Artifacts: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
