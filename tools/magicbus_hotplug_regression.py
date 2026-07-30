#!/usr/bin/env python3
"""Exercise live Magic Bus tail attachment, removal, and reinsertion.

The release ROM starts with one AT keyboard.  This regression changes MAME's
live accessory configuration to append an SCTG endpoint, saves and reloads
while that topology transition is pending, exercises the unchanged keyboard,
removes the SCTG tail, and reinserts it.  Breakpoints in the ROM prove that
attachment and detachment take the recovered MBIC paths, that both clients
return, and that keyboard traffic still crosses the bus.

Physical removal is expected to produce one low-level ``MagicBusError`` when
IRQ-Get receives no answer from the removed address.  The ROM classifies that
completion through ``MagicBus_HandleDetachedPeripherals`` and recovers without
entering its user-visible ``MagicBus_HandleMagicBusFailure`` method.
"""

from __future__ import annotations

import argparse
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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "magicbus-hotplug"

SUPPORTED_SYSTEMS = ("datarover840", "datarover840f")
WATCHED = (
    ("failures", 0x13C2AFB8, "MagicBus_HandleMagicBusFailure"),
    ("low_errors", 0x13C28B3C, "MagicBusError"),
    ("assign", 0x13C2A8EC, "MagicBus_AssignMagicBusAddress"),
    ("peripheral_info", 0x13C29284, "GetPeripheralInfo"),
    ("mbreq_handler", 0x13C295D4, "HandlerMagicBusMBReqLine"),
    ("handle_attached", 0x13C2AA9C, "MagicBus_HandleAttachedPeripherals"),
    ("handle_detached", 0x13C2AA44, "MagicBus_HandleDetachedPeripherals"),
    ("keyboard_attached", 0x13C27594, "MagicBusATKeyboard_Attached"),
    ("keyboard_detached", 0x13C27608, "MagicBusATKeyboard_Detached"),
    ("scsi_attached", 0x13E82D10, "MagicBusSCSITargetClient_Attached"),
    ("scsi_detached", 0x13E82D54, "MagicBusSCSITargetClient_Detached"),
    ("keyboard_requests", 0x13C27B20, "MagicBusATKeyboard_PeripheralRequest"),
    ("keyboard_dispatch", 0x13C2763C, "MagicBusATKeyboard_DispatchATKeys"),
    ("keyboard_led", 0x13C27C84, "MagicBusATKeyboard_SetLedStatus"),
)
SCRATCH = 0x0030_0000
RESULT = re.compile(rb"MAGICBUS HOTPLUG ([^\n]+)")


def automation_script(max_frames: int, state_path: Path | None = None) -> str:
    """Return the Lua lifecycle state machine used by the regression."""
    if state_path is None:
        state_path = Path("/tmp/magicbus-hotplug-test.sta")
    quoted_state = (
        str(state_path).replace("\\", "\\\\").replace('"', '\\"')
    )
    slots = {
        name: SCRATCH + index * 4
        for index, (name, _address, _symbol) in enumerate(WATCHED)
    }
    setup = "\n".join(
        f'        watch(0x{slots[name]:08x}, 0x{address:08x}, "{name}")'
        for name, address, _symbol in WATCHED
    )
    report = " .. ".join(
        f'string.format("{name}=%d ", count(0x{slots[name]:08x}))'
        for name, _address, _symbol in WATCHED
    )
    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local accessory = machine.ioport.ports[":MAGICBUS_ACCESSORY"]:field(1)
local caps_lock = machine.ioport.ports[
    ":magicbus_keyboard:pc_keyboard_3"].fields["Caps"]
local frames = 0
local phase = "boot"
local action_frame = nil
local key_down_frame = nil
local dispatch_before = 0
local led_before = 0
local post_add_key = 0
local post_reinsert_key = 0
local save_load = 0
local save_deadline = nil

local function watch(slot, address, name)
    program:write_u32(slot, 0)
    cpu.debug:bpset(address, "1",
        string.format("do d@0x%08x=d@0x%08x+1; g", slot, slot))
end

local function count(slot)
    return program:read_u32(slot)
end

local function peripheral_count()
    local globals = program:read_u32(0x0000e7d0)
    return program:read_u8(globals + 0x10)
end

local function start_key_test(next_phase)
    dispatch_before = count(0x{slots["keyboard_dispatch"]:08x})
    led_before = count(0x{slots["keyboard_led"]:08x})
    key_down_frame = frames
    phase = next_phase
    caps_lock:set_value(caps_lock.mask)
end

local function report()
    print("MAGICBUS HOTPLUG "
        .. string.format(
            "phase=%s frames=%d peripherals=%d post_add_key=%d " ..
            "post_reinsert_key=%d save_load=%d ",
            phase, frames, peripheral_count(),
            post_add_key, post_reinsert_key, save_load)
        .. {report})
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 60 then
{setup}
    elseif phase == "boot"
            and count(0x{slots["keyboard_attached"]:08x}) >= 1
            and peripheral_count() == 1 then
        phase = "wait_add"
        action_frame = frames + 30
    elseif phase == "wait_add" and frames >= action_frame then
        accessory.user_value = 2
        phase = "adding"
        action_frame = frames + 1
    elseif phase == "adding"
            and save_load == 0
            and frames >= action_frame then
        -- The input callback has latched ADD_TAIL, but the ROM is still in
        -- its one-second attachment debounce. Rewind this exact pending MBIC
        -- state before allowing command 27 to expose the new endpoint.
        machine:save("{quoted_state}")
        phase = "wait_saved_add"
        save_deadline = frames + 30
    elseif phase == "wait_saved_add" and frames >= save_deadline then
        machine:load("{quoted_state}")
        save_load = 1
        phase = "adding"
    elseif phase == "adding"
            and count(0x{slots["scsi_attached"]:08x}) >= 1
            and peripheral_count() == 2 then
        action_frame = frames + 15
        phase = "wait_key_after_add"
    elseif phase == "wait_key_after_add" and frames >= action_frame then
        start_key_test("key_after_add")
    elseif phase == "key_after_add"
            and frames == key_down_frame + 10 then
        caps_lock:set_value(0)
    elseif phase == "key_after_add"
            and frames > key_down_frame + 10
            and count(0x{slots["keyboard_dispatch"]:08x}) > dispatch_before
            and count(0x{slots["keyboard_led"]:08x}) > led_before then
        post_add_key = 1
        phase = "wait_remove"
        action_frame = frames + 30
    elseif phase == "wait_remove" and frames >= action_frame then
        accessory.user_value = 1
        phase = "removing"
    elseif phase == "removing"
            and count(0x{slots["handle_detached"]:08x}) >= 1
            and count(0x{slots["scsi_detached"]:08x}) >= 1 then
        phase = "wait_reinsert"
        action_frame = frames + 60
    elseif phase == "wait_reinsert" and frames >= action_frame then
        accessory.user_value = 2
        phase = "reinserting"
    elseif phase == "reinserting"
            and count(0x{slots["keyboard_attached"]:08x}) >= 2
            and count(0x{slots["scsi_attached"]:08x}) >= 2
            and peripheral_count() == 2 then
        action_frame = frames + 15
        phase = "wait_key_after_reinsert"
    elseif phase == "wait_key_after_reinsert" and frames >= action_frame then
        start_key_test("key_after_reinsert")
    elseif phase == "key_after_reinsert"
            and frames == key_down_frame + 10 then
        caps_lock:set_value(0)
    elseif phase == "key_after_reinsert"
            and frames > key_down_frame + 10
            and count(0x{slots["keyboard_dispatch"]:08x}) > dispatch_before
            and count(0x{slots["keyboard_led"]:08x}) > led_before then
        post_reinsert_key = 1
        phase = "complete"
        report()
        machine:exit()
    end

    if frames >= {max_frames} and phase ~= "complete" then
        report()
        machine:exit()
    end
end)
"""


def machine_config(system: str) -> str:
    """Select the one-keyboard starting topology."""
    return f"""<?xml version="1.0"?>
<mameconfig version="10"><system name="{system}"><input>
<port tag=":MAGICBUS_ACCESSORY" type="CONFIG"
      mask="3" defvalue="1" value="1" />
</input></system></mameconfig>
"""


def parse_result(output: bytes) -> dict[str, str]:
    """Parse the single lifecycle result line."""
    match = RESULT.search(output)
    if not match:
        return {}
    return dict(
        re.findall(r"(\w+)=([A-Za-z0-9_]+)", match.group(1).decode())
    )


def acceptance_errors(result: dict[str, str]) -> list[str]:
    """Explain every missing lifecycle observation."""
    if not result:
        return ["result line missing"]

    errors = []

    def value(name: str) -> int:
        try:
            return int(result.get(name, "0"))
        except ValueError:
            return 0

    if result.get("phase") != "complete":
        errors.append(f"phase={result.get('phase', 'missing')}")
    if value("peripherals") != 2:
        errors.append(f"peripherals={value('peripherals')} (need 2)")
    if value("failures") != 0:
        errors.append(f"failures={value('failures')}")
    if value("low_errors") != 1:
        errors.append(f"low_errors={value('low_errors')} (need 1)")
    for name, minimum in (
        ("assign", 4),
        ("peripheral_info", 4),
        ("handle_attached", 2),
        ("handle_detached", 1),
        ("keyboard_attached", 2),
        ("scsi_attached", 2),
        ("scsi_detached", 1),
        ("keyboard_requests", 2),
        ("keyboard_dispatch", 2),
        ("keyboard_led", 2),
        ("post_add_key", 1),
        ("post_reinsert_key", 1),
        ("save_load", 1),
    ):
        if value(name) < minimum:
            errors.append(f"{name}={value(name)} (need {minimum})")
    return errors


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--system", default="datarover840")
    parser.add_argument(
        "--frames",
        type=int,
        default=3000,
        help="maximum emulated frames before reporting an incomplete phase",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    if not mame.is_file():
        print(f"error: MAME executable not found: {mame}", file=sys.stderr)
        return 2
    if args.system not in SUPPORTED_SYSTEMS:
        print(
            f"error: {args.system} does not use the release build's addresses; "
            f"choose one of {', '.join(SUPPORTED_SYSTEMS)}",
            file=sys.stderr,
        )
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    (run_dir / "nvram").mkdir(parents=True)
    (run_dir / "cfg").mkdir()
    lua_path = run_dir / "magicbus-hotplug.lua"
    state_path = run_dir / "magicbus-pending.sta"
    lua_path.write_text(
        automation_script(args.frames, state_path), encoding="utf-8"
    )
    (run_dir / "cfg" / f"{args.system}.cfg").write_text(
        machine_config(args.system), encoding="utf-8"
    )

    completed = subprocess.run(
        [
            str(mame),
            args.system,
            "-rompath",
            str(rompath),
            "-cfg_directory",
            str(run_dir / "cfg"),
            "-nvram_directory",
            str(run_dir / "nvram"),
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
        ],
        cwd=mame.parent,
        capture_output=True,
        timeout=1200,
    )
    output = completed.stdout + completed.stderr
    (run_dir / "mame-output.txt").write_bytes(output)

    result = parse_result(output)
    if not result:
        print(f"error: no lifecycle result; see {run_dir}", file=sys.stderr)
        return 2

    width = max(len(name) for name, _address, _symbol in WATCHED)
    for name, _address, symbol in WATCHED:
        print(f"  {name:<{width}}  {int(result.get(name, '0')):4d}  {symbol}")
    print(
        f"  {'phase':<{width}}  {result.get('phase', 'missing')}\n"
        f"  {'peripherals':<{width}}  {result.get('peripherals', '0')}\n"
        f"  {'post_add_key':<{width}}  {result.get('post_add_key', '0')}\n"
        f"  {'post_reinsert_key':<{width}}  "
        f"{result.get('post_reinsert_key', '0')}\n"
        f"  {'save_load':<{width}}  {result.get('save_load', '0')}"
    )
    print(f"Artifacts: {run_dir}")

    errors = acceptance_errors(result)
    if not state_path.is_file():
        errors.append("save state missing")
    if errors:
        print(
            "FAIL: incomplete Magic Bus hot-plug lifecycle: "
            + ", ".join(errors),
            file=sys.stderr,
        )
        return 1
    print(
        "PASS: SCTG tail attached, detached, and reattached; "
        "pending topology survived save/load and keyboard traffic survived "
        "both live transitions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
