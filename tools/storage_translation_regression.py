#!/usr/bin/env python3
"""Translate a real Magic Cap 1.x card package into 3.1 built-in storage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import legacy_card_image


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WRAPPER = (
    ASSETS_ROOT
    / "research"
    / "magic-cap-1-simulator"
    / "legacy-card-current.raw"
)
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "storage-translation-regression"
CHECKPOINT_PATTERN = re.compile(rb"STORAGE_TRANSLATION ([^\r\n]+)")
CARD_SERVER_ACCEPT_ADDRESS = 0x13C2_E4EC
CARD_FAILURE_ADDRESS = 0x13C2_E424
COUNTERS = 0x0030_1000


def automation_script(card_path: Path | str) -> str:
    """Return the deterministic legacy-card translation sequence."""
    encoded_card_path = json.dumps(str(card_path))
    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local frames = 0

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

local function load_card(instance, path)
    for name, image in pairs(machine.images) do
        print("STORAGE_IMAGE " .. tostring(name) .. " "
            .. tostring(image.brief_instance_name))
        if image.brief_instance_name == instance then
            local error = image:load(path)
            if error ~= nil then
                print("STORAGE_ERROR load " .. instance .. " "
                    .. tostring(error))
                machine:exit()
            end
            return
        end
    end
    print("STORAGE_ERROR no " .. instance .. " image device")
    machine:exit()
end

local function tuple_word(first)
    local value = 0
    for index = first, first + 3 do
        value = (value << 8) | program:read_u8(
            0x08000000 + index * 2)
    end
    return value
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 60 then
        program:write_u32({COUNTERS}, 0)
        program:write_u32({COUNTERS + 4}, 0)
        program:write_u32({COUNTERS + 8}, 0)
        cpu.debug:bpset({CARD_SERVER_ACCEPT_ADDRESS}, "R17!=0",
            "do d@0x{COUNTERS:08x}=d@0x{COUNTERS:08x}+1; g")
        cpu.debug:bpset({CARD_FAILURE_ADDRESS}, "1",
            "do d@0x{COUNTERS + 4:08x}=d@0x{COUNTERS + 4:08x}+1; g")
        cpu.debug:bpset(0x13c39f14, "1",
            "do d@0x{COUNTERS + 8:08x}=d@0x{COUNTERS + 8:08x}+1; g")
        cpu.debug:go()
    elseif frames == 1100 then
        machine.screens[":screen"]:snapshot("destination-name.png")
    elseif frames == 1300 then
        machine.screens[":screen"]:snapshot("destination-ready.png")
    elseif frames == 1400 then
        load_card("card1", {encoded_card_path})
    elseif frames == 2350 then
        machine.screens[":screen"]:snapshot("translation-prompt.png")
    elseif frames == 2420 then
        press(237, 201)
    elseif frames == 2440 then
        touch_button:set_value(0)
    elseif frames == 2500 then
        machine.screens[":screen"]:snapshot("translation-selection.png")
    elseif frames == 2530 then
        press(82, 84)
    elseif frames == 2550 then
        touch_button:set_value(0)
    elseif frames == 2600 then
        machine.screens[":screen"]:snapshot("translation-selected.png")
    elseif frames == 2630 then
        press(351, 192)
    elseif frames == 2650 then
        touch_button:set_value(0)
    elseif frames == 2850 then
        machine.screens[":screen"]:snapshot("translation-progress.png")
    elseif frames == 3000 then
        machine.screens[":screen"]:snapshot("translation-complete.png")
    elseif frames == 3100 then
        press(35, 296)
    elseif frames == 3120 then
        touch_button:set_value(0)
    elseif frames == 3350 then
        machine.screens[":screen"]:snapshot("translation-desk.png")
    elseif frames == 3400 then
        press(338, 170)
    elseif frames == 3420 then
        touch_button:set_value(0)
    elseif frames == 3650 then
        machine.screens[":screen"]:snapshot("translation-notebook-page1.png")
    elseif frames == 3700 then
        press(272, 10)
    elseif frames == 3720 then
        touch_button:set_value(0)
    elseif frames == 3950 then
        machine.screens[":screen"]:snapshot("translation-notebook-page2.png")
        print(string.format(
            "STORAGE_TRANSLATION ACCEPTED=%08X FAILURES=%08X "
            .. "RESETS=%08X CIS=%02X VERSION=%08X TYPE=%08X "
            .. "COMMON=%08X",
            program:read_u32({COUNTERS}),
            program:read_u32({COUNTERS + 4}),
            program:read_u32({COUNTERS + 8}),
            program:read_u8(0x08000000 + 0x11 * 2),
            tuple_word(0x17),
            tuple_word(0x1b),
            program:read_u32(0x24000070)))
        machine:exit()
    end
end)
"""


def parse_checkpoint(output: bytes) -> dict[str, int]:
    """Parse the final hexadecimal translation checkpoint."""
    match = CHECKPOINT_PATTERN.search(output)
    if match is None:
        return {}
    result: dict[str, int] = {}
    for item in match.group(1).split():
        key, value = item.split(b"=", 1)
        result[key.decode("ascii")] = int(value, 16)
    return result


def deterministic_machine_config() -> str:
    """Keep the known-good Magic Bus keyboard attached."""
    return """<?xml version="1.0"?>
<mameconfig version="10">
    <system name="datarover840">
        <input>
            <port tag=":MAGICBUS_ACCESSORY" type="CONFIG"
                  mask="1" defvalue="1" value="1" />
        </input>
    </system>
</mameconfig>
"""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--wrapper", type=Path, default=DEFAULT_WRAPPER)
    parser.add_argument(
        "--changes",
        type=Path,
        help="optional newer common-memory image written by the Simulator",
    )
    parser.add_argument(
        "--nvram",
        type=Path,
        required=True,
        help="NVRAM directory with Translation.pkg already installed",
    )
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    return parser.parse_args(argv)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_regression(args: argparse.Namespace) -> int:
    inputs = {
        "MAME executable": args.mame.expanduser().resolve(),
        "ROM path": args.rompath.expanduser().resolve(),
        "Simulator wrapper": args.wrapper.expanduser().resolve(),
        "Translation NVRAM": args.nvram.expanduser().resolve(),
    }
    if args.changes is not None:
        inputs["Simulator changes file"] = args.changes.expanduser().resolve()
    for label, path in inputs.items():
        wants_directory = label in {"ROM path", "Translation NVRAM"}
        valid = path.is_dir() if wants_directory else path.is_file()
        if not valid:
            print(f"error: {label} not found: {path}", file=sys.stderr)
            return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    run_dir.mkdir(parents=True)
    cfg_dir = run_dir / "cfg"
    snapshot_dir = run_dir / "snapshots"
    cfg_dir.mkdir()
    snapshot_dir.mkdir()
    (cfg_dir / "datarover840.cfg").write_text(
        deterministic_machine_config(),
        encoding="utf-8",
    )
    shutil.copytree(inputs["Translation NVRAM"], run_dir / "nvram")

    wrapper_hash = sha256(inputs["Simulator wrapper"])
    changes = inputs.get("Simulator changes file")
    changes_hash = sha256(changes) if changes is not None else None
    card_path = run_dir / "legacy-1x.card"
    card_path.write_bytes(
        legacy_card_image.build_mame_image(
            inputs["Simulator wrapper"].read_bytes(),
            changes.read_bytes() if changes is not None else None,
        )
    )
    card_hash = sha256(card_path)
    lua_path = run_dir / "storage-translation.lua"
    lua_path.write_text(
        automation_script(card_path),
        encoding="utf-8",
    )
    command = [
        str(inputs["MAME executable"]),
        "datarover840",
        "-rompath",
        str(inputs["ROM path"]),
        "-nvram_directory",
        str(run_dir / "nvram"),
        "-cfg_directory",
        str(cfg_dir),
        "-snapshot_directory",
        str(snapshot_dir),
        "-snapview",
        "native",
        "-pccard1",
        "linear",
        "-pccard2",
        "linear",
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
        result = subprocess.run(
            command,
            cwd=inputs["MAME executable"].parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"error: MAME failed: {error}; artifacts: {run_dir}", file=sys.stderr)
        return 2
    output_path = run_dir / "mame-output.txt"
    output_path.write_bytes(result.stdout)
    checkpoint = parse_checkpoint(result.stdout)
    expected = {
        "FAILURES": 0,
        "RESETS": 0,
        "CIS": 0xA0,
        "VERSION": 0x0001_0001,
        "TYPE": 0x5241_4D43,
    }
    if (
        result.returncode
        or b"STORAGE_ERROR" in result.stdout
        or b"[LUA ERROR]" in result.stdout
        or checkpoint.get("ACCEPTED", 0) < 1
        or any(checkpoint.get(key) != value for key, value in expected.items())
    ):
        print(
            f"FAIL: translation checkpoint {checkpoint!r}; "
            f"see {output_path}",
            file=sys.stderr,
        )
        return 1

    prompt = snapshot_dir / "translation-prompt.png"
    selection = snapshot_dir / "translation-selection.png"
    selected = snapshot_dir / "translation-selected.png"
    progress = snapshot_dir / "translation-progress.png"
    complete = snapshot_dir / "translation-complete.png"
    page1 = snapshot_dir / "translation-notebook-page1.png"
    page2 = snapshot_dir / "translation-notebook-page2.png"
    if (
        not prompt.is_file()
        or not selection.is_file()
        or not selected.is_file()
        or not progress.is_file()
        or not complete.is_file()
        or not page1.is_file()
        or not page2.is_file()
        or prompt.read_bytes() == selection.read_bytes()
        or selection.read_bytes() == selected.read_bytes()
        or selected.read_bytes() == complete.read_bytes()
        or page1.read_bytes() == page2.read_bytes()
    ):
        print(
            f"FAIL: translation UI did not advance; see {run_dir}",
            file=sys.stderr,
        )
        return 1
    if (
        sha256(inputs["Simulator wrapper"]) != wrapper_hash
        or (
            changes is not None
            and sha256(changes) != changes_hash
        )
        or sha256(card_path) != card_hash
    ):
        print(
            f"FAIL: translation modified a source card; see {run_dir}",
            file=sys.stderr,
        )
        return 1
    print(
        "PASS: Magic Cap translated the 1.x card's new-items package into "
        "Built-in storage, exposed its second Notebook page, and left the "
        "source unchanged"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
