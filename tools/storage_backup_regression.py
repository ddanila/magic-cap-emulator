#!/usr/bin/env python3
"""Exercise Magic Cap built-in-storage backup and restore on a PC Card."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import storage_card_regression


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "storage-backup-regression"
CHECKPOINT_PATTERN = re.compile(
    rb"STORAGE_(BACKUP|RESTORE) ([^\r\n]+)"
)
MAGICBUS_FAILURE_ADDRESS = 0x13C2_AFB8
MAGICBUS_FAILURE_COUNTER = 0x0030_1000
RESTORE_DIALOG_CHECKSUM = 0x886C_0C0D


def backup_script(card_path: Path | str) -> str:
    """Return the warm-state built-in-storage backup input sequence."""
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

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 60 then
        program:write_u32({MAGICBUS_FAILURE_COUNTER}, 0)
        cpu.debug:bpset({MAGICBUS_FAILURE_ADDRESS}, "1",
            "do d@0x{MAGICBUS_FAILURE_COUNTER:08x}="
            .. "d@0x{MAGICBUS_FAILURE_COUNTER:08x}+1; g")
    elseif program:read_u32({MAGICBUS_FAILURE_COUNTER}) ~= 0 then
        print(string.format(
            "STORAGE_ERROR first Magic Bus failure at frame %d",
            frames))
        machine:exit()
    elseif frames == 1300 then load_card()
    elseif frames == 1800 then press(173, 300)
    elseif frames == 1820 then touch_button:set_value(0)
    elseif frames == 2100 then press(147, 232)
    elseif frames == 2120 then touch_button:set_value(0)
    elseif frames == 2500 then
        machine.screens[":screen"]:snapshot("storage-backup-ready.png")
    elseif frames == 2600 then press(262, 212)
    elseif frames == 2620 then touch_button:set_value(0)
    elseif frames == 3000 then
        print("STORAGE_BACKUP_PROGRESS FRAME=3000")
        machine.screens[":screen"]:snapshot("storage-backup-progress.png")
    elseif frames == 17000 then
        print(string.format(
            "STORAGE_BACKUP HEADER=%08X MAGICBUS_FAILURES=%08X",
            program:read_u32(0x24000058),
            program:read_u32({MAGICBUS_FAILURE_COUNTER})))
        machine.screens[":screen"]:snapshot("storage-backup-complete.png")
        machine:exit()
    elseif frames >= 6000 and frames % 3000 == 0 then
        print(string.format("STORAGE_BACKUP_PROGRESS FRAME=%d", frames))
    end
end)
"""


def restore_script(card_path: Path | str) -> str:
    """Return the fresh-process backup-package restore sequence."""
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

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 60 then
        program:write_u32({MAGICBUS_FAILURE_COUNTER}, 0)
        cpu.debug:bpset({MAGICBUS_FAILURE_ADDRESS}, "1",
            "do d@0x{MAGICBUS_FAILURE_COUNTER:08x}="
            .. "d@0x{MAGICBUS_FAILURE_COUNTER:08x}+1; g")
    elseif frames == 1300 then load_card()
    elseif frames == 1600 then press(240, 145)
    elseif frames == 1620 then touch_button:set_value(0)
    elseif frames == 1900 then press(365, 145)
    elseif frames == 1920 then touch_button:set_value(0)
    elseif frames == 2250 then press(450, 50)
    elseif frames == 2270 then touch_button:set_value(0)
    elseif frames == 2600 then press(237, 136)
    elseif frames == 2620 then touch_button:set_value(0)
    elseif frames == 3000 then
        print("STORAGE_RESTORE_PROGRESS FRAME=3000")
        machine.screens[":screen"]:snapshot("storage-restore-progress.png")
    elseif frames == 10000 then
        local framebuffer = program:read_u32(0x10c00030) & 0xfffffff0
        local dialog_checksum = 0
        -- Sum the stable message-text rectangle.  The alert's exclamation
        -- icon animates, so intentionally exclude it and the close box.
        for y = 55, 91 do
            for xbyte = 16, 88, 4 do
                dialog_checksum = (
                    dialog_checksum
                    + program:read_u32(framebuffer + (y * 120) + xbyte)
                ) & 0xffffffff
            end
        end
        print(string.format(
            "STORAGE_RESTORE HEADER=%08X MAGICBUS_FAILURES=%08X DIALOG=%08X",
            program:read_u32(0x24000058),
            program:read_u32({MAGICBUS_FAILURE_COUNTER}),
            dialog_checksum))
        machine.screens[":screen"]:snapshot("storage-restore-complete.png")
        machine:exit()
    elseif frames >= 6000 and frames % 3000 == 0 then
        print(string.format("STORAGE_RESTORE_PROGRESS FRAME=%d", frames))
    end
end)
"""


def parse_checkpoints(output: bytes) -> dict[str, dict[str, int]]:
    """Parse final backup/restore hexadecimal checkpoint fields."""
    result: dict[str, dict[str, int]] = {}
    for match in CHECKPOINT_PATTERN.finditer(output):
        fields: dict[str, int] = {}
        for item in match.group(2).split():
            key, value = item.split(b"=", 1)
            fields[key.decode("ascii")] = int(value, 16)
        result[match.group(1).decode("ascii")] = fields
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a Magic Cap storage card, back up built-in storage, "
            "and restore it."
        )
    )
    parser.add_argument(
        "--mame",
        type=Path,
        default=DEFAULT_MAME,
        help=f"MAME executable (default: {DEFAULT_MAME})",
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


def deterministic_machine_config() -> str:
    """Keep the working Magic Bus keyboard present throughout long I/O."""
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
    card_path.write_bytes(b"\xff" * storage_card_regression.CARD_SIZE)
    state_dir = run_dir / "state" / "nvram"
    state_dir.mkdir(parents=True)

    phases = (
        (
            "setup",
            storage_card_regression.automation_script(
                card_path,
                exercise_battery=False,
            ),
            150,
            False,
        ),
        ("backup", backup_script(card_path), 600, True),
        ("restore", restore_script(card_path), 420, True),
    )
    outputs: list[bytes] = []
    snapshots: dict[str, Path] = {}
    card_hashes: dict[str, str] = {}
    ram_hash_before_restore: str | None = None
    for name, script, timeout, needs_debugger in phases:
        print(f"Running {name} phase...", flush=True)
        phase_dir = run_dir / name
        cfg_dir = phase_dir / "cfg"
        snapshot_dir = phase_dir / "snapshots"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "datarover840.cfg").write_text(
            deterministic_machine_config(),
            encoding="utf-8",
        )
        snapshot_dir.mkdir()
        snapshots[name] = snapshot_dir
        lua_path = phase_dir / f"storage-{name}.lua"
        lua_path.write_text(script, encoding="utf-8")
        command = [
            str(mame),
            "datarover840",
            "-rompath",
            str(rompath),
            "-nvram_directory",
            str(state_dir),
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
            process = subprocess.Popen(
                command,
                cwd=mame.parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            started = time.monotonic()
            next_progress = started + 30
            while process.poll() is None:
                now = time.monotonic()
                if now - started >= timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise subprocess.TimeoutExpired(command, timeout)
                if now >= next_progress:
                    print(
                        f"Still running {name} phase "
                        f"({int(now - started)}s)...",
                        flush=True,
                    )
                    next_progress = now + 30
                time.sleep(1)
            output, _ = process.communicate()
            returncode = process.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            print(
                f"error: MAME {name} phase failed: {error}; "
                f"artifacts: {run_dir}",
                file=sys.stderr,
            )
            return 2
        output_path = phase_dir / "mame-output.txt"
        output_path.write_bytes(output)
        outputs.append(output)
        if (
            returncode
            or b"STORAGE_ERROR" in output
            or b"[LUA ERROR]" in output
        ):
            print(
                f"FAIL: MAME {name} phase failed; see {output_path}",
                file=sys.stderr,
            )
            return 1
        card_hashes[name] = hashlib.sha256(card_path.read_bytes()).hexdigest()
        ram_path = state_dir / "datarover840" / "ram"
        if name == "backup" and ram_path.is_file():
            ram_hash_before_restore = hashlib.sha256(
                ram_path.read_bytes()
            ).hexdigest()

    combined = b"\n".join(outputs)
    log_path = run_dir / "mame-output.txt"
    log_path.write_bytes(combined)
    checkpoints = parse_checkpoints(combined)
    expected = {
        "BACKUP": {
            "HEADER": 0x4D434150,
            "MAGICBUS_FAILURES": 0,
        },
        "RESTORE": {
            "HEADER": 0x4D434150,
            "MAGICBUS_FAILURES": 0,
            "DIALOG": RESTORE_DIALOG_CHECKSUM,
        },
    }
    for name in ("BACKUP", "RESTORE"):
        actual = checkpoints.get(name)
        if actual is None or any(
            actual.get(key) != value
            for key, value in expected[name].items()
        ):
            print(
                f"FAIL: {name} checkpoint {actual!r}, "
                f"expected {expected[name]!r}; "
                f"see {log_path}",
                file=sys.stderr,
            )
            return 1

    required_snapshots = {
        "setup": {"storage-setup.png", "storage-name.png"},
        "backup": {
            "storage-backup-ready.png",
            "storage-backup-progress.png",
            "storage-backup-complete.png",
        },
        "restore": {
            "storage-restore-progress.png",
            "storage-restore-complete.png",
        },
    }
    if any(
        not (snapshots[phase] / filename).is_file()
        for phase, filenames in required_snapshots.items()
        for filename in filenames
    ):
        print(
            f"FAIL: backup/restore snapshots are incomplete; see {run_dir}",
            file=sys.stderr,
        )
        return 1

    card_data = card_path.read_bytes()
    if (
        card_hashes["setup"] == card_hashes["backup"]
        or b"\x00b\x00a\x00c\x00k\x00u\x00p" not in card_data
        or b"FBk" not in card_data
    ):
        print(
            f"FAIL: card does not contain the completed backup package; "
            f"see {run_dir}",
            file=sys.stderr,
        )
        return 1

    ram_path = state_dir / "datarover840" / "ram"
    if (
        ram_hash_before_restore is None
        or not ram_path.is_file()
        or hashlib.sha256(ram_path.read_bytes()).hexdigest()
        == ram_hash_before_restore
    ):
        print(
            f"FAIL: retained RAM did not change during restore; see {run_dir}",
            file=sys.stderr,
        )
        return 1

    progress = (
        snapshots["restore"] / "storage-restore-progress.png"
    ).read_bytes()
    complete = (
        snapshots["restore"] / "storage-restore-complete.png"
    ).read_bytes()
    if progress == complete:
        print(
            f"FAIL: restore never left its progress screen; see {run_dir}",
            file=sys.stderr,
        )
        return 1

    print(
        "PASS: built-in storage backup is card-resident and full restore "
        "completes"
    )
    print(f"Persistent card: {card_path}")
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
