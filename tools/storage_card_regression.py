#!/usr/bin/env python3
"""Exercise Magic Cap storage-card setup, persistence, and Option-insert."""

from __future__ import annotations

import argparse
import hashlib
import json
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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "storage-card-regression"
CARD_SIZE = 8 * 1024 * 1024
BLANK_SHA256 = hashlib.sha256(b"\xff" * CARD_SIZE).hexdigest()
CHECKPOINT_PATTERN = re.compile(
    rb"STORAGE_(BLANK|FORMAT|BATTERY|REINSERT|OPTION|FINAL|OBJECT_CREATE|OBJECT_RELAUNCH) ([^\r\n]+)"
)


def automation_script(card_path: Path | str) -> str:
    """Return the deterministic blank-card lifecycle input sequence."""
    encoded_card_path = json.dumps(str(card_path))
    return f"""local machine = manager.machine
local program = machine.devices[":maincpu"].spaces["program"]
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local card_battery = ports[":PCCARD1_BATTERY"]:field(0x03)
local frames = 0
local card_image = nil
local battery_good = 0
local battery_low = 0
local battery_dead = 0

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

local function load_card()
    if card_image == nil then
        for _, image in pairs(machine.images) do
            if image.brief_instance_name == "card" then
                card_image = image
                break
            end
        end
    end
    if card_image == nil then
        print("STORAGE_ERROR no card image device")
        machine:exit()
        return
    end
    local error = card_image:load({encoded_card_path})
    if error ~= nil then
        print("STORAGE_ERROR load " .. tostring(error))
        machine:exit()
    end
end

local function tuple_word(first)
    local value = 0
    for index = first, first + 3 do
        value = (value << 8) | program:read_u8(
            0x08000000 + index * 2)
    end
    return value
end

local function battery_pins()
    local bvd1 = (program:read_u32(0x10c00180) >> 1) & 1
    local bvd2 = program:read_u16(0x1040000c) & 2
    return bvd1 | bvd2
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 1220 then
        press(240, 160)
    elseif frames == 1240 then
        touch_button:set_value(0)
    elseif frames == 1420 then
        press(23, 23)
    elseif frames == 1440 then
        touch_button:set_value(0)
    elseif frames == 1620 then
        press(456, 296)
    elseif frames == 1640 then
        touch_button:set_value(0)
    elseif frames == 1820 then
        press(240, 160)
    elseif frames == 1840 then
        touch_button:set_value(0)
    elseif frames == 2050 then
        press(395, 26)
    elseif frames == 2070 then
        touch_button:set_value(0)
    elseif frames == 2220 then
        load_card()
    elseif frames == 2400 then
        print(string.format(
            "STORAGE_BLANK CODE=%02X LINK=%02X MAGIC=%08X TYPE=%08X COMMON=%08X DINO=%08X GLACIER=%04X",
            program:read_u8(0x08000000), program:read_u8(0x08000002),
            tuple_word(2), tuple_word(10), program:read_u32(0x24000000),
            program:read_u32(0x10c00180),
            program:read_u16(0x1040000c)))
        machine.screens[":screen"]:snapshot("storage-setup.png")
    elseif frames == 2450 then
        press(237, 141)
    elseif frames == 2470 then
        touch_button:set_value(0)
    elseif frames == 2700 then
        print(string.format(
            "STORAGE_FORMAT CIS=%08X HEADER=%08X VERSION=%08X CLUSTER=%08X NAME=%08X STAMP=%08X",
            program:read_u32(0x24000000),
            program:read_u32(0x24000058),
            program:read_u32(0x2400005c),
            program:read_u32(0x24000060),
            program:read_u32(0x24000088),
            program:read_u32(0x24000080)))
        machine.screens[":screen"]:snapshot("storage-name.png")
    elseif frames == 2750 then
        press(239, 145)
    elseif frames == 2770 then
        touch_button:set_value(0)
    elseif frames == 2800 then
        battery_good = battery_pins()
        -- Configuration fields are toggle inputs in MAME's Lua API.  Pulse
        -- the field once for each setting instead of passing the setting's
        -- numeric value (all non-zero values mean "pressed").
        card_battery:set_value(1)
    elseif frames == 2801 then
        card_battery:set_value(0)
    elseif frames == 2840 then
        battery_low = battery_pins()
        card_battery:set_value(1)
    elseif frames == 2841 then
        card_battery:set_value(0)
    elseif frames == 2880 then
        battery_dead = battery_pins()
        card_battery:set_value(1)
    elseif frames == 2881 then
        card_battery:set_value(0)
    elseif frames == 2920 then
        print(string.format(
            "STORAGE_BATTERY GOOD=%X LOW=%X DEAD=%X",
            battery_good, battery_low, battery_dead))
    elseif frames == 3000 then
        machine:exit()
    end
end)
"""


def reinsertion_script(card_path: Path | str) -> str:
    """Return a fresh-boot persisted-card reinsertion check."""
    encoded_card_path = json.dumps(str(card_path))
    return f"""local machine = manager.machine
local program = machine.devices[":maincpu"].spaces["program"]
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
    if frames == 1220 then
        press(240, 160)
    elseif frames == 1240 then
        touch_button:set_value(0)
    elseif frames == 1420 then
        press(23, 23)
    elseif frames == 1440 then
        touch_button:set_value(0)
    elseif frames == 1620 then
        press(456, 296)
    elseif frames == 1640 then
        touch_button:set_value(0)
    elseif frames == 1820 then
        press(240, 160)
    elseif frames == 1840 then
        touch_button:set_value(0)
    elseif frames == 2050 then
        press(395, 26)
    elseif frames == 2070 then
        touch_button:set_value(0)
    elseif frames == 2220 then
        load_card()
    elseif frames == 3000 then
        print(string.format(
            "STORAGE_REINSERT MAGIC=%08X TYPE=%08X CLUSTER=%08X HEADER=%08X",
            tuple_word(2), tuple_word(10), tuple_word(14),
            program:read_u32(0x24000058)))
        machine.screens[":screen"]:snapshot("storage-reinserted.png")
        machine:exit()
    end
end)
"""


def option_insert_script(card_path: Path | str) -> str:
    """Return a fresh-boot live Option-insert forced-reformat check."""
    encoded_card_path = json.dumps(str(card_path))
    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local option_button = ports[":OPTION_BUTTON"]:field(0x01)
local frames = 0
local option_seen = nil
local OPTION_OBSERVED = 0x00300000

program:write_u32(OPTION_OBSERVED, 0xffffffff)
cpu.debug:bpset(
    0x13c32a9c, "1", "do d@0x00300000=R2; g")
cpu.debug:go()

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
    if frames == 1220 then
        press(240, 160)
    elseif frames == 1240 then
        touch_button:set_value(0)
    elseif frames == 1420 then
        press(23, 23)
    elseif frames == 1440 then
        touch_button:set_value(0)
    elseif frames == 1620 then
        press(456, 296)
    elseif frames == 1640 then
        touch_button:set_value(0)
    elseif frames == 1820 then
        press(240, 160)
    elseif frames == 1840 then
        touch_button:set_value(0)
    elseif frames == 2050 then
        press(395, 26)
    elseif frames == 2070 then
        touch_button:set_value(0)
    elseif frames == 2200 then
        program:write_u32(OPTION_OBSERVED, 0xffffffff)
        option_button:set_value(1)
    elseif frames == 2240 then
        load_card()
    elseif frames == 2320 then
        option_button:set_value(0)
    end

    if option_seen == nil and program:read_u32(OPTION_OBSERVED) == 1 then
        option_seen = frames
    elseif option_seen ~= nil and frames == option_seen + 200 then
        print(string.format(
            "STORAGE_OPTION HEADER=%08X TYPE=%08X FLAG=%08X",
            program:read_u32(0x24000058), tuple_word(10),
            program:read_u32(OPTION_OBSERVED)))
        machine.screens[":screen"]:snapshot("storage-option-setup.png")
    elseif option_seen ~= nil and frames == option_seen + 250 then
        press(237, 141)
    elseif option_seen ~= nil and frames == option_seen + 270 then
        touch_button:set_value(0)
    elseif option_seen ~= nil and frames == option_seen + 500 then
        machine.screens[":screen"]:snapshot("storage-option-name.png")
    elseif option_seen ~= nil and frames == option_seen + 550 then
        press(239, 145)
    elseif option_seen ~= nil and frames == option_seen + 570 then
        touch_button:set_value(0)
    elseif option_seen ~= nil and frames == option_seen + 800 then
        print(string.format(
            "STORAGE_FINAL CIS=%08X HEADER=%08X TYPE=%08X CLUSTER=%08X STAMP=%08X",
            program:read_u32(0x24000000),
            program:read_u32(0x24000058), tuple_word(10), tuple_word(14),
            program:read_u32(0x24000080)))
        machine.screens[":screen"]:snapshot("storage-final.png")
        machine:exit()
    elseif frames == 5000 then
        print("STORAGE_ERROR Option-insert state machine timed out")
        machine:exit()
    end
end)
"""


def object_creation_script(card_path: Path | str) -> str:
    """Route new items to the card, create a notebook page, and commit it."""
    encoded_card_path = json.dumps(str(card_path))
    return f"""local machine = manager.machine
local program = machine.devices[":maincpu"].spaces["program"]
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local frames = 0

local function move(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
end

local function press(x, y)
    move(x, y)
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

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 1300 then load_card()
    elseif frames == 1600 then press(382, 80)
    elseif frames == 1620 then touch_button:set_value(0)
    elseif frames == 1900 then press(29, 145)
    elseif frames == 1920 then touch_button:set_value(0)
    elseif frames == 2100 then press(240, 145)
    elseif frames == 2120 then touch_button:set_value(0)
    elseif frames == 2400 then
        machine.screens[":screen"]:snapshot("storage-new-items-package.png")
    elseif frames == 2500 then press(35, 300)
    elseif frames == 2520 then touch_button:set_value(0)
    elseif frames == 2800 then press(335, 170)
    elseif frames == 2820 then touch_button:set_value(0)
    elseif frames == 3100 then press(450, 100)
    elseif frames == 3120 then touch_button:set_value(0)
    elseif frames == 3400 then press(145, 92)
    elseif frames == 3420 then touch_button:set_value(0)
    elseif frames == 3700 then press(237, 110)
    elseif frames == 3720 then touch_button:set_value(0)
    elseif frames == 3900 then
        machine.screens[":screen"]:snapshot("storage-object-blank.png")
    elseif frames == 4000 then press(100, 100)
    elseif frames == 4010 then move(140, 130)
    elseif frames == 4020 then move(180, 160)
    elseif frames == 4030 then move(220, 130)
    elseif frames == 4040 then move(260, 100)
    elseif frames == 4050 then touch_button:set_value(0)
    elseif frames == 4100 then press(260, 100)
    elseif frames == 4110 then move(220, 130)
    elseif frames == 4120 then move(180, 160)
    elseif frames == 4130 then move(140, 130)
    elseif frames == 4140 then move(100, 100)
    elseif frames == 4150 then touch_button:set_value(0)
    elseif frames == 4350 then
        machine.screens[":screen"]:snapshot("storage-object-drawn.png")
    elseif frames == 4450 then press(440, 10)
    elseif frames == 4470 then touch_button:set_value(0)
    elseif frames == 4750 then
        machine.screens[":screen"]:snapshot("storage-object-committed.png")
        print(string.format(
            "STORAGE_OBJECT_CREATE HEADER=%08X",
            program:read_u32(0x24000058)))
    elseif frames == 4850 then machine:exit()
    end
end)
"""


def object_relaunch_script(card_path: Path | str) -> str:
    """Reinsert the card in a new process and reopen its notebook object."""
    encoded_card_path = json.dumps(str(card_path))
    return f"""local machine = manager.machine
local program = machine.devices[":maincpu"].spaces["program"]
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

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 1300 then load_card()
    elseif frames == 1550 then press(237, 110)
    elseif frames == 1570 then touch_button:set_value(0)
    elseif frames == 1900 then press(335, 170)
    elseif frames == 1920 then touch_button:set_value(0)
    elseif frames == 2150 then press(274, 12)
    elseif frames == 2170 then touch_button:set_value(0)
    elseif frames == 2450 then
        machine.screens[":screen"]:snapshot("storage-object-relaunched.png")
        print(string.format(
            "STORAGE_OBJECT_RELAUNCH HEADER=%08X",
            program:read_u32(0x24000058)))
    elseif frames == 2550 then machine:exit()
    end
end)
"""


def parse_checkpoints(output: bytes) -> dict[str, dict[str, int]]:
    """Parse named hexadecimal checkpoint fields from MAME output."""
    result: dict[str, dict[str, int]] = {}
    for match in CHECKPOINT_PATTERN.finditer(output):
        fields: dict[str, int] = {}
        for item in match.group(2).split():
            key, value = item.split(b"=", 1)
            fields[key.decode("ascii")] = int(value, 16)
        result[match.group(1).decode("ascii")] = fields
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mame",
        type=Path,
        default=DEFAULT_MAME,
        help=f"DataRover MAME executable (default: {DEFAULT_MAME})",
    )
    parser.add_argument(
        "--rompath",
        type=Path,
        default=DEFAULT_ROMPATH,
        help=f"MAME ROM search path (default: {DEFAULT_ROMPATH})",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=DEFAULT_WORKDIR,
        help=f"persistent artifact directory (default: {DEFAULT_WORKDIR})",
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
    run_dir.mkdir(parents=True)
    card_path = run_dir / "storage.card"
    card_path.write_bytes(b"\xff" * CARD_SIZE)
    if hashlib.sha256(card_path.read_bytes()).hexdigest() != BLANK_SHA256:
        print("error: unable to create deterministic blank card", file=sys.stderr)
        return 2

    phases = (
        ("setup", automation_script(card_path), False, "setup"),
        ("reinsert", reinsertion_script(card_path), False, "reinsert"),
        ("option", option_insert_script(card_path), True, "object"),
        ("object-create", object_creation_script(card_path), False, "object"),
        ("object-relaunch", object_relaunch_script(card_path), False, "object"),
    )
    outputs: list[bytes] = []
    snapshot_dirs: dict[str, Path] = {}
    card_hashes: dict[str, str] = {}
    for name, script, needs_debugger, state_name in phases:
        print(f"Running {name} phase...", flush=True)
        phase_dir = run_dir / name
        nvram_dir = run_dir / "state" / state_name / "nvram"
        cfg_dir = phase_dir / "cfg"
        snapshot_dir = phase_dir / "snapshots"
        nvram_dir.mkdir(parents=True, exist_ok=True)
        cfg_dir.mkdir(parents=True)
        snapshot_dir.mkdir()
        snapshot_dirs[name] = snapshot_dir
        lua_path = phase_dir / f"storage-card-{name}.lua"
        lua_path.write_text(script, encoding="utf-8")
        command = [
            str(mame),
            "datarover840",
            "-rompath",
            str(rompath),
            "-nvram_directory",
            str(nvram_dir),
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
        if needs_debugger:
            command.extend(["-debug", "-debugger", "none"])
        try:
            completed = subprocess.run(
                command,
                cwd=mame.parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=150,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            print(
                f"error: MAME {name} phase failed: {error}; "
                f"artifacts: {run_dir}",
                file=sys.stderr,
            )
            return 2
        phase_log = phase_dir / "mame-output.txt"
        phase_log.write_bytes(completed.stdout)
        outputs.append(completed.stdout)
        card_hashes[name] = hashlib.sha256(card_path.read_bytes()).hexdigest()
        if completed.returncode or b"STORAGE_ERROR" in completed.stdout:
            print(
                f"FAIL: MAME {name} phase failed; see {phase_log}",
                file=sys.stderr,
            )
            return 1

    combined_output = b"\n".join(outputs)
    log_path = run_dir / "mame-output.txt"
    log_path.write_bytes(combined_output)
    checkpoints = parse_checkpoints(combined_output)
    expected = {
        "BLANK": {
            "CODE": 0xA0,
            "LINK": 0x20,
            "MAGIC": 0x474D4D43,
            "TYPE": 0x424C4E4B,
            "COMMON": 0xFFFFFFFF,
        },
        "FORMAT": {
            "CIS": 0x13034349,
            "HEADER": 0x4D434150,
            "VERSION": 0x00020001,
            "CLUSTER": 0x000000B0,
            "NAME": 0x0055006E,
        },
        "BATTERY": {
            "GOOD": 0x3,
            "LOW": 0x1,
            "DEAD": 0x0,
        },
        "REINSERT": {
            "MAGIC": 0x474D4D43,
            "TYPE": 0x52414D43,
            "CLUSTER": 0x000000B0,
            "HEADER": 0x4D434150,
        },
        "OPTION": {
            "HEADER": 0x4D434150,
            "TYPE": 0x52414D43,
            "FLAG": 0x00000001,
        },
        "FINAL": {
            "CIS": 0x13034349,
            "HEADER": 0x4D434150,
            "TYPE": 0x52414D43,
            "CLUSTER": 0x000000B0,
        },
        "OBJECT_CREATE": {"HEADER": 0x4D434150},
        "OBJECT_RELAUNCH": {"HEADER": 0x4D434150},
    }
    for name, fields in expected.items():
        actual = checkpoints.get(name)
        if actual is None or any(actual.get(key) != value for key, value in fields.items()):
            print(
                f"FAIL: {name} checkpoint {actual!r}, expected at least "
                f"{fields!r}; see {log_path}",
                file=sys.stderr,
            )
            return 1
    if checkpoints["FORMAT"]["STAMP"] == checkpoints["FINAL"]["STAMP"]:
        print(
            f"FAIL: Option-insert did not regenerate the storage header; "
            f"see {log_path}",
            file=sys.stderr,
        )
        return 1

    snapshots = {
        "setup": {"storage-setup.png", "storage-name.png"},
        "reinsert": {"storage-reinserted.png"},
        "option": {
            "storage-option-setup.png",
            "storage-option-name.png",
            "storage-final.png",
        },
        "object-create": {
            "storage-new-items-package.png",
            "storage-object-blank.png",
            "storage-object-drawn.png",
            "storage-object-committed.png",
        },
        "object-relaunch": {"storage-object-relaunched.png"},
    }
    if any(
        not (snapshot_dirs[phase] / filename).is_file()
        for phase, filenames in snapshots.items()
        for filename in filenames
    ):
        print(f"FAIL: lifecycle snapshots are incomplete; see {run_dir}", file=sys.stderr)
        return 1

    card_data = card_path.read_bytes()
    if (
        len(card_data) != CARD_SIZE
        or card_data[0:5] != b"\x13\x03CIS"
        or card_data[0x58:0x5C] != b"MCAP"
        or hashlib.sha256(card_data).hexdigest() == BLANK_SHA256
    ):
        print(f"FAIL: formatted image did not persist: {card_path}", file=sys.stderr)
        return 1

    blank_object = (
        snapshot_dirs["object-create"] / "storage-object-blank.png"
    ).read_bytes()
    drawn_object = (
        snapshot_dirs["object-create"] / "storage-object-drawn.png"
    ).read_bytes()
    relaunched_object = (
        snapshot_dirs["object-relaunch"] / "storage-object-relaunched.png"
    ).read_bytes()
    if blank_object == drawn_object:
        print(
            f"FAIL: notebook pen input did not change the object; see {run_dir}",
            file=sys.stderr,
        )
        return 1
    if drawn_object != relaunched_object:
        print(
            f"FAIL: card-backed notebook object changed across relaunch; "
            f"see {run_dir}",
            file=sys.stderr,
        )
        return 1
    if card_hashes["option"] == card_hashes["object-create"]:
        print(
            f"FAIL: creating the notebook object did not change the card image; "
            f"see {run_dir}",
            file=sys.stderr,
        )
        return 1

    print(
        "PASS: blank setup, format, naming, persistence, reinsertion, "
        "Option-insert reformat, and a card-backed notebook object match "
        "Magic Cap"
    )
    print(f"Persistent card: {card_path}")
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
