#!/usr/bin/env python3
"""Drive Magic Cap's Power Controls and automatic idle shutoff."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "power-policy-regression"
SLEE = 0x534C4545
WAIT_FOR_POWER_DOWN = 0x13C3B1C8
POWER_VCC_ON = 0x00000001
CHECKPOINT = re.compile(
    rb"POWER_POLICY WHEN=(\w+) PC=([0-9A-F]{8}) "
    rb"REASON=([0-9A-F]{8}) POWER=([0-9A-F]{8})"
)


def config_xml() -> str:
    return """<?xml version="1.0"?>
<mameconfig version="10">
    <system name="datarover840">
        <input>
            <port tag=":RTC_RESUME" type="CONFIG"
                  mask="1" defvalue="1" value="0" />
        </input>
    </system>
</mameconfig>
"""


def automation_script() -> str:
    return r"""local machine = manager.machine
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local ac = ports[":POWER_SUPPLY"]:field(0x01)
local program = machine.devices[":maincpu"].spaces["program"]
local cpu = machine.devices[":maincpu"]
local frames = 0
local repeated = nil
local stage = "boot"
local deadline = 0

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
        -- Leave a full frame for any snapshot requested by the caller.
        next_frame = frames + 10, completion = completion
    }
end

local function service_repeated_tap()
    if repeated == nil or frames < repeated.next_frame then return end
    if not repeated.down then
        press(repeated.x, repeated.y)
        repeated.down = true
        repeated.next_frame = frames + 12
    else
        release()
        repeated.down = false
        repeated.remaining = repeated.remaining - 1
        repeated.next_frame = frames + 12
        if repeated.remaining == 0 then
            local completion = repeated.completion
            repeated = nil
            completion()
        end
    end
end

local function settle(next_stage)
    stage = next_stage
    deadline = frames + 60
end

local function checkpoint(name)
    print(string.format(
        "POWER_POLICY WHEN=%s PC=%08X REASON=%08X POWER=%08X",
        name,
        cpu.state["PC"].value,
        program:read_u32(0x0000e880),
        program:read_u32(0x10c001c4)))
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
    elseif frames == 2200 then
        machine.screens[":screen"]:snapshot("01-desk.png")
        press(455, 8)
    elseif frames == 2220 then release()
    elseif frames == 2500 then
        machine.screens[":screen"]:snapshot("02-hallway.png")
        press(424, 108)
    elseif frames == 2520 then release()
    elseif frames == 2900 then
        machine.screens[":screen"]:snapshot("03-controls.png")
        press(139, 152)
    elseif frames == 2920 then release()
    elseif frames == 3300 then
        machine.screens[":screen"]:snapshot("04-power-default.png")
        -- Ten presses prove the lower clamp rather than merely 5 - 4 = 1.
        repeat_tap(230, 122, 10, function() settle("minimum") end)
    end

    service_repeated_tap()

    if repeated == nil and frames == deadline then
        if stage == "minimum" then
            machine.screens[":screen"]:snapshot("05-power-minimum.png")
            -- Likewise, overshoot the documented upper bound.
            repeat_tap(306, 122, 70, function() settle("maximum") end)
        elseif stage == "maximum" then
            machine.screens[":screen"]:snapshot("06-power-maximum.png")
            repeat_tap(230, 122, 70, function() settle("minimum-again") end)
        elseif stage == "minimum-again" then
            machine.screens[":screen"]:snapshot("07-power-minimum-again.png")
            ac:set_value(1)
            stage = "plugged-unchecked"
            -- One minute plus ten seconds for the boundary and shutdown work.
            deadline = frames + 4200
        elseif stage == "plugged-unchecked" then
            checkpoint("plugged_unchecked")
            machine.screens[":screen"]:snapshot("08-plugged-unchecked.png")
            repeat_tap(189, 160, 1, function() settle("checked") end)
        elseif stage == "checked" then
            machine.screens[":screen"]:snapshot("09-power-checked.png")
            stage = "plugged-checked"
            deadline = frames + 4200
        elseif stage == "plugged-checked" then
            checkpoint("plugged_checked")
            machine.screens[":screen"]:snapshot("10-plugged-checked-off.png")
            machine:exit()
        end
    end
end)
"""


def parse_checkpoints(output: bytes) -> dict[str, tuple[int, int, int]]:
    return {
        match.group(1).decode("ascii"): tuple(
            int(value, 16) for value in match.groups()[1:]
        )
        for match in CHECKPOINT.finditer(output)
    }


def read_idle_minutes(snapshot: Path, scratch: Path) -> int | None:
    """OCR only the numeric field, after deterministic nearest-neighbor scale."""
    with Image.open(snapshot) as image:
        digit = image.convert("L").crop((246, 105, 294, 139))
        digit = digit.resize(
            (digit.width * 10, digit.height * 10), Image.Resampling.NEAREST
        )
        digit = digit.point(lambda value: 255 if value >= 179 else 0)
        digit.save(scratch)
    completed = subprocess.run(
        [
            "tesseract",
            str(scratch),
            "stdout",
            "--psm",
            "10",
            "-c",
            "tessedit_char_whitelist=0123456789",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"\d+", completed.stdout)
    return int(match.group()) if match else None


def checkbox_mark_pixels(snapshot: Path) -> int:
    """Count dark pixels inside the checkbox, excluding its border."""
    with Image.open(snapshot) as image:
        interior = image.convert("L").crop((181, 152, 198, 169))
        pixels = (
            interior.get_flattened_data()
            if hasattr(interior, "get_flattened_data")
            else interior.getdata()
        )
        return sum(value < 128 for value in pixels)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
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
    config_dir = run_dir / "cfg"
    snapshot_dir = run_dir / "snapshots"
    nvram_dir = run_dir / "nvram"
    config_dir.mkdir(parents=True)
    snapshot_dir.mkdir()
    nvram_dir.mkdir()
    (config_dir / "datarover840.cfg").write_text(
        config_xml(), encoding="utf-8"
    )
    script = run_dir / "power-policy.lua"
    script.write_text(automation_script(), encoding="utf-8")
    completed = subprocess.run(
        [
            str(args.mame),
            "datarover840",
            "-rompath",
            str(args.rompath),
            "-cfg_directory",
            str(config_dir),
            "-nvram_directory",
            str(nvram_dir),
            "-snapshot_directory",
            str(snapshot_dir),
            "-snapview",
            "native",
            "-autoboot_delay",
            "0",
            "-autoboot_script",
            str(script),
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
        cwd=args.mame.parent,
        capture_output=True,
        timeout=600,
    )
    output = completed.stdout + completed.stderr
    (run_dir / "mame-output.txt").write_bytes(output)

    failures: list[str] = []
    if completed.returncode:
        failures.append(f"MAME exited with status {completed.returncode}")

    expected_minutes = {
        "04-power-default.png": 5,
        "05-power-minimum.png": 1,
        "06-power-maximum.png": 60,
        "07-power-minimum-again.png": 1,
    }
    ocr_scratch = run_dir / "idle-minutes.png"
    for name, expected in expected_minutes.items():
        snapshot = snapshot_dir / name
        if not snapshot.is_file():
            failures.append(f"snapshot was not produced: {name}")
            continue
        actual = read_idle_minutes(snapshot, ocr_scratch)
        if actual != expected:
            failures.append(
                f"{name} displayed {actual!r} minutes, expected {expected}"
            )

    default_snapshot = snapshot_dir / "04-power-default.png"
    checked_snapshot = snapshot_dir / "09-power-checked.png"
    if default_snapshot.is_file() and checkbox_mark_pixels(default_snapshot):
        failures.append('"even when plugged in" was checked by default')
    if checked_snapshot.is_file():
        if checkbox_mark_pixels(checked_snapshot) < 5:
            failures.append('"even when plugged in" did not become checked')
    else:
        failures.append("checked Power snapshot was not produced")

    checkpoints = parse_checkpoints(output)
    if set(checkpoints) != {"plugged_unchecked", "plugged_checked"}:
        failures.append("automatic-shutoff checkpoints are incomplete")
    else:
        unchecked = checkpoints["plugged_unchecked"]
        checked = checkpoints["plugged_checked"]
        if not unchecked[2] & POWER_VCC_ON:
            failures.append("unchecked plugged-in policy shut VCC off")
        if (
            checked[0] != WAIT_FOR_POWER_DOWN
            or checked[1] != SLEE
            or checked[2] & POWER_VCC_ON
        ):
            failures.append(
                "checked plugged-in policy did not reach SLEE/VCC-off sleep"
            )

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 1

    print(
        "PASS: Power Controls clamp 5 -> 1 -> 60; AC stays awake while "
        "unchecked and reaches SLEE/VCC-off sleep when checked"
    )
    print(f"Artifacts: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
