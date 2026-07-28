#!/usr/bin/env python3
"""Exercise Magic Cap's real 1.x storage-card translation entry path."""

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
DEFAULT_CHANGES = (
    ASSETS_ROOT
    / "research"
    / "magic-cap-1-simulator"
    / "legacy-current.raw"
)
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "storage-translation-regression"
CHECKPOINT_PATTERN = re.compile(rb"STORAGE_TRANSLATION ([^\r\n]+)")
CARD_SERVER_ACCEPT_ADDRESS = 0x13C2_E4EC
CARD_FAILURE_ADDRESS = 0x13C2_E424
COUNTERS = 0x0030_1000


def automation_script(card_path: Path | str) -> str:
    """Return the deterministic legacy-card prompt/selection sequence."""
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

local function load_card()
    for _, image in pairs(machine.images) do
        if image.brief_instance_name == "card" then
            local error = image:load({encoded_card_path})
            if error ~= nil then
                print("STORAGE_ERROR load " .. tostring(error))
                machine:exit()
            end
            return
        end
    end
    print("STORAGE_ERROR no card image device")
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
        cpu.debug:bpset({CARD_SERVER_ACCEPT_ADDRESS}, "R17!=0",
            "do d@0x{COUNTERS:08x}=d@0x{COUNTERS:08x}+1; g")
        cpu.debug:bpset({CARD_FAILURE_ADDRESS}, "1",
            "do d@0x{COUNTERS + 4:08x}=d@0x{COUNTERS + 4:08x}+1; g")
        cpu.debug:go()
    elseif frames == 1300 then
        load_card()
    elseif frames == 2250 then
        machine.screens[":screen"]:snapshot("translation-prompt.png")
    elseif frames == 2350 then
        press(237, 201)
    elseif frames == 2370 then
        touch_button:set_value(0)
    elseif frames == 3000 then
        machine.screens[":screen"]:snapshot("translation-selection.png")
        print(string.format(
            "STORAGE_TRANSLATION ACCEPTED=%08X FAILURES=%08X "
            .. "CIS=%02X VERSION=%08X TYPE=%08X COMMON=%08X",
            program:read_u32({COUNTERS}),
            program:read_u32({COUNTERS + 4}),
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
    parser.add_argument("--changes", type=Path, default=DEFAULT_CHANGES)
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
        "Simulator changes file": args.changes.expanduser().resolve(),
        "Translation NVRAM": args.nvram.expanduser().resolve(),
    }
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
    changes_hash = sha256(inputs["Simulator changes file"])
    card_path = run_dir / "legacy-1x.card"
    card_path.write_bytes(
        legacy_card_image.build_mame_image(
            inputs["Simulator wrapper"].read_bytes(),
            inputs["Simulator changes file"].read_bytes(),
        )
    )
    card_hash = sha256(card_path)
    lua_path = run_dir / "storage-translation.lua"
    lua_path.write_text(automation_script(card_path), encoding="utf-8")
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
        "CIS": 0xA0,
        "VERSION": 0x0001_0001,
        "TYPE": 0x5241_4D43,
        "COMMON": 0x4000_0000,
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
    if (
        not prompt.is_file()
        or not selection.is_file()
        or prompt.read_bytes() == selection.read_bytes()
    ):
        print(
            f"FAIL: translation UI did not advance; see {run_dir}",
            file=sys.stderr,
        )
        return 1
    if (
        sha256(inputs["Simulator wrapper"]) != wrapper_hash
        or sha256(inputs["Simulator changes file"]) != changes_hash
        or sha256(card_path) != card_hash
    ):
        print(
            f"FAIL: translation entry modified a source card; see {run_dir}",
            file=sys.stderr,
        )
        return 1

    print(
        "PASS: Magic Cap accepts the 1.x card, opens Translation.pkg's "
        "selection UI, and leaves the source unchanged"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
