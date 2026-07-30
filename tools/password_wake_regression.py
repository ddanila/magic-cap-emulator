#!/usr/bin/env python3
"""Verify Magic Cap's power-on password across a retained-RAM wake."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageChops


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "password-wake-regression"
POFF = 0x504F4646
POWER_VCC_ON = 0x00000001
WAIT_FOR_POWER_DOWN = 0x13C3B1C8
CONFIG_RESULT = re.compile(rb"PASSWORD_CONFIG set=(\d+) prompt=(\d+) text=(\d+)")
SLEEP_RESULT = re.compile(
    rb"PASSWORD_SLEEP ([AB]) pc=([0-9A-F]{8}) "
    rb"reason=([0-9A-F]{8}) power=([0-9A-F]{8})"
)
WAKE_RESULT = re.compile(
    rb"PASSWORD_WAKE should=(\d+) open=(\d+) bad=(\d+) close=(\d+)"
)


def _lua_common() -> str:
    return r"""local machine = manager.machine
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local power_button = ports[":POWER_BUTTON"]:field(0x01)
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0

local function watch(slot, address)
    program:write_u32(slot, 0)
    cpu.debug:bpset(address, "1",
        string.format("do d@0x%08x=d@0x%08x+1; g", slot, slot))
end

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

local function release()
    touch_button:set_value(0)
end
"""


def configure_script() -> str:
    """Calibrate, set PIN 1234 twice, select every-time, and suspend."""
    return (
        _lua_common()
        + r"""
local scratch = 0x00300300

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
    elseif frames == 2900 then press(240, 152)
    elseif frames == 2920 then release()
    elseif frames == 3100 then
        machine.screens[":screen"]:snapshot("01-privacy.png")
        watch(scratch, 0x13db4bd0)
        watch(scratch + 4, 0x13db4a70)
        watch(scratch + 8, 0x13db4ab4)
        press(345, 100)
    elseif frames == 3120 then release()
    elseif frames == 3370 then
        machine.screens[":screen"]:snapshot("02-set-password.png")
        press(295, 100)
    elseif frames == 3390 then release()
    elseif frames == 3420 then press(364, 100)
    elseif frames == 3440 then release()
    elseif frames == 3470 then press(432, 100)
    elseif frames == 3490 then release()
    elseif frames == 3520 then press(295, 149)
    elseif frames == 3540 then release()
    elseif frames == 3570 then press(432, 249)
    elseif frames == 3590 then release()
    elseif frames == 3850 then
        machine.screens[":screen"]:snapshot("03-confirm-password.png")
        press(295, 100)
    elseif frames == 3870 then release()
    elseif frames == 3900 then press(364, 100)
    elseif frames == 3920 then release()
    elseif frames == 3950 then press(432, 100)
    elseif frames == 3970 then release()
    elseif frames == 4000 then press(295, 149)
    elseif frames == 4020 then release()
    elseif frames == 4050 then press(432, 249)
    elseif frames == 4070 then release()
    elseif frames == 4350 then press(220, 237)
    elseif frames == 4370 then release()
    elseif frames == 4600 then press(220, 237)
    elseif frames == 4620 then release()
    elseif frames == 4800 then
        machine.screens[":screen"]:snapshot("04-every-time.png")
        print(string.format(
            "PASSWORD_CONFIG set=%d prompt=%d text=%d",
            program:read_u32(scratch),
            program:read_u32(scratch + 4),
            program:read_u32(scratch + 8)))
    elseif frames == 5000 then power_button:set_value(1)
    elseif frames == 5020 then power_button:set_value(0)
    elseif frames == 5300 then
        print(string.format(
            "PASSWORD_SLEEP A pc=%08X reason=%08X power=%08X",
            cpu.state["PC"].value,
            program:read_u32(0x0000e880),
            program:read_u32(0x10c001c4)))
    elseif frames == 5400 then
        print(string.format(
            "PASSWORD_SLEEP B pc=%08X reason=%08X power=%08X",
            cpu.state["PC"].value,
            program:read_u32(0x0000e880),
            program:read_u32(0x10c001c4)))
        machine.screens[":screen"]:snapshot("05-suspended.png")
        machine:exit()
    end
end)
"""
    )


def wake_script() -> str:
    """Wake retained RAM, reject a wrong PIN, then accept PIN 1234."""
    return (
        _lua_common()
        + r"""
local scratch = 0x00300340
watch(scratch, 0x13c1d7e4)
watch(scratch + 4, 0x13e13c38)
watch(scratch + 8, 0x13e13ba0)
watch(scratch + 12, 0x13e13d38)

local function report()
    print(string.format(
        "PASSWORD_WAKE should=%d open=%d bad=%d close=%d",
        program:read_u32(scratch),
        program:read_u32(scratch + 4),
        program:read_u32(scratch + 8),
        program:read_u32(scratch + 12)))
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 1000 then
        machine.screens[":screen"]:snapshot("06-warm-doze.png")
    elseif frames == 1520 then power_button:set_value(1)
    elseif frames == 1650 then power_button:set_value(0)
    elseif frames == 2020 then power_button:set_value(1)
    elseif frames == 2150 then power_button:set_value(0)
    elseif frames == 2400 then
        machine.screens[":screen"]:snapshot("07-password-prompt.png")
        press(432, 198)
    elseif frames == 2420 then release()
    elseif frames == 2450 then press(432, 198)
    elseif frames == 2470 then release()
    elseif frames == 2500 then press(432, 198)
    elseif frames == 2520 then release()
    elseif frames == 2550 then press(432, 198)
    elseif frames == 2570 then release()
    elseif frames == 2600 then press(432, 249)
    elseif frames == 2620 then release()
    elseif frames == 2900 then
        machine.screens[":screen"]:snapshot("08-bad-password.png")
        report()
        press(289, 96)
    elseif frames == 2920 then release()
    elseif frames == 2950 then press(358, 96)
    elseif frames == 2970 then release()
    elseif frames == 3000 then press(427, 96)
    elseif frames == 3020 then release()
    elseif frames == 3050 then press(289, 146)
    elseif frames == 3070 then release()
    elseif frames == 3100 then press(427, 246)
    elseif frames == 3120 then release()
    elseif frames == 3500 then
        machine.screens[":screen"]:snapshot("09-unlocked.png")
        report()
        machine:exit()
    end
end)
"""
    )


def parse_config(output: bytes) -> tuple[int, int, int] | None:
    """Return password setter helper counts."""
    match = CONFIG_RESULT.search(output)
    return tuple(int(value) for value in match.groups()) if match else None


def parse_sleep(output: bytes) -> dict[str, tuple[int, int, int]]:
    """Return retained-shutdown checkpoints."""
    return {
        match.group(1).decode("ascii"): tuple(
            int(value, 16) for value in match.groups()[1:]
        )
        for match in SLEEP_RESULT.finditer(output)
    }


def parse_wake(output: bytes) -> tuple[int, int, int, int] | None:
    """Return password scene helper counts."""
    match = WAKE_RESULT.search(output)
    return tuple(int(value) for value in match.groups()) if match else None


def images_equal(first: Path, second: Path, box: tuple[int, ...] | None = None) -> bool:
    """Return whether two screenshots (or matching crops) are identical."""
    with Image.open(first) as first_image, Image.open(second) as second_image:
        first_rgb = first_image.convert("RGB")
        second_rgb = second_image.convert("RGB")
        if box is not None:
            first_rgb = first_rgb.crop(box)
            second_rgb = second_rgb.crop(box)
        return ImageChops.difference(first_rgb, second_rgb).getbbox() is None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--timeout", type=int, default=300)
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
        "-debug",
        "-debugger",
        "none",
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
            cwd=mame.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"error: {phase} phase failed: {error}", file=sys.stderr)
        return None
    (run_dir / f"{phase}-output.txt").write_bytes(completed.stdout)
    return completed.returncode, completed.stdout


def _fail(reason: str, run_dir: Path) -> int:
    print(f"FAIL: {reason}", file=sys.stderr)
    print(f"Artifacts: {run_dir}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    if not mame.is_file() or not rompath.is_dir():
        print("error: MAME executable or ROM directory is missing", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    (run_dir / "nvram").mkdir(parents=True)
    (run_dir / "snapshots").mkdir()

    configured = _run_phase(
        mame, rompath, run_dir, "configure", configure_script(), args.timeout
    )
    if configured is None:
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 2
    if configured[0]:
        return _fail(f"configure phase exited with status {configured[0]}", run_dir)
    config_counts = parse_config(configured[1])
    if config_counts is None or config_counts[0] != 1:
        return _fail(f"password setter was not observed: {config_counts!r}", run_dir)
    sleep = parse_sleep(configured[1])
    if set(sleep) != {"A", "B"}:
        return _fail("sleep checkpoints are incomplete", run_dir)
    if (
        sleep["A"][0] != WAIT_FOR_POWER_DOWN
        or sleep["B"][0] != WAIT_FOR_POWER_DOWN
        or sleep["B"][1] != POFF
        or sleep["B"][2] & POWER_VCC_ON
    ):
        return _fail("configured machine did not remain asleep", run_dir)

    woke = _run_phase(mame, rompath, run_dir, "wake", wake_script(), args.timeout)
    if woke is None:
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 2
    if woke[0]:
        return _fail(f"wake phase exited with status {woke[0]}", run_dir)
    wake_counts = parse_wake(woke[1])
    if wake_counts is None:
        return _fail("password wake counters are missing", run_dir)
    if wake_counts[:2] != (1, 1):
        return _fail(f"password decision/open counts were {wake_counts[:2]!r}", run_dir)

    snapshots = run_dir / "snapshots"
    policy = snapshots / "04-every-time.png"
    prompt = snapshots / "07-password-prompt.png"
    wrong = snapshots / "08-bad-password.png"
    unlocked = snapshots / "09-unlocked.png"
    if not all(path.is_file() for path in (policy, prompt, wrong, unlocked)):
        return _fail("password screenshots are incomplete", run_dir)
    if not images_equal(prompt, wrong):
        return _fail("wrong PIN did not remain at the password prompt", run_dir)
    if images_equal(prompt, unlocked):
        return _fail("correct PIN did not leave the password prompt", run_dir)
    if not images_equal(policy, unlocked, (0, 200, 240, 260)):
        return _fail("unlocked Privacy panel did not retain every-time policy", run_dir)

    print(
        "PASS: every-time password survived retained-RAM sleep, wrong PIN "
        "stayed locked, and PIN 1234 returned to Privacy"
    )
    print(f"Snapshot: {unlocked}")
    print(f"Artifacts: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
