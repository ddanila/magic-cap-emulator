#!/usr/bin/env python3
"""Run the development ROM's Command-T through Magic Cap's scheduler.

The direct-call test harnesses deliberately replace the emulated PC and wait
for a ROM routine to return.  That cannot run tests which yield to an actor,
timer, semaphore, announcement, or scene.  This harness instead makes one
short injected call to the real

    Semaphore_RunSoon(false, bootstrap, System_iTestMachine)

API.  It then restores the interrupted CPU context in full.  The system run
queue invokes ``bootstrap`` at an actual dispatcher boundary; bootstrap uses
``Semaphore_RunSoon(true, ...)`` to attach the final completion to that
dispatcher actor.  The user completion calls the ROM's
``TestMachine_CommandTea`` and returns normally to the scheduler.

Addresses are specific to the Apollo USA development ROM dated 1998-04-07.
No ROM image or generated runtime artifact is stored in this repository.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "devrom-command-t"

TEST_MACHINE_SLOT = 0x0002_D4B4
FLUSH_INSTRUCTION_CACHE = 0x13C1_D250
SEMAPHORE_RUN_SOON = 0x13CB_F9A0
TEST_MACHINE_COMMAND_TEA = 0x13E9_837C
TEST_MACHINE_RUN_TEST_SUITES = 0x13E9_8288
RUN_TESTS = 0x13E9_7C90
TESTS_COMPLETE = 0x13E9_8128
FAILURE_ORACLE = 0x13E9_7834
REBOOT_COMMAND_T_FINISHED = 0x13E9_3EE0

STUB = 0x0030_0000
STUB_UNCACHED = STUB | 0xA000_0000
BOOTSTRAP = STUB + 0x100
CALLBACK = STUB + 0x180
BOOTSTRAP_DESCRIPTOR = STUB + 0x1D0
CALLBACK_DESCRIPTOR = STUB + 0x1D8
ROM_GP = 0x0000_E020
QUEUED = STUB + 0x200
BOOTSTRAP_ENTERED = STUB + 0x204
USER_QUEUED = STUB + 0x208
ENTERED = STUB + 0x20C
RETURNED = STUB + 0x210
RUN_SUITES_HITS = STUB + 0x214
RUN_TESTS_HITS = STUB + 0x218
COMPLETE_HITS = STUB + 0x21C
COMPLAINT_HITS = STUB + 0x220
REBOOT_HITS = STUB + 0x224
SCHEDULER_DESCRIPTOR = STUB + 0x228
SCHEDULER_TARGET = STUB + 0x22C
LAST_TEST_BODY = STUB + 0x230
LAST_TEST_INDEX = STUB + 0x234
MOVING_SOUND_LOOPS = STUB + 0x238
MOVING_SOUND_ANNOUNCEMENTS = STUB + 0x23C
MOVING_SOUND_WAIT = STUB + 0x240
MOVING_SOUND_DONE = STUB + 0x244
SOUND_INTERRUPT_HITS = STUB + 0x248
SOUND_HALF_HITS = STUB + 0x24C
SOUND_FULL_HITS = STUB + 0x250
INSTRUMENT_DONE_HITS = STUB + 0x254
INSTRUMENT_COMPLETION_HITS = STUB + 0x258
MARKER = 0x434D_4454  # "CMDT"
EXPECTED_BASIC_SUITES = 16
QUEUE_RETURN_HIGH_INDEX = 17
QUEUE_RETURN_LOW_INDEX = 18

TEST_BODY_ADDRESSES = (
    0x13E8_8994,
    0x13E8_8DC0,
    0x13E8_97B4,
    0x13E8_AFA4,
    0x13E8_B334,
    0x13E8_B9B0,
    0x13E8_BA10,
    0x13E8_C43C,
    0x13E8_D07C,
    0x13E8_E618,
    0x13E8_ED6C,
    0x13E9_0460,
    0x13E9_06E0,
    0x13E9_2AD8,
    0x13E9_2D30,
    0x13E9_3010,
    0x13E9_3CF8,
    0x13E9_3F30,
    0x13E9_3FD0,
    0x13E9_5DF0,
    0x13E9_5F60,
    0x13E9_BDC0,
    0x13E9_E8A8,
    0x13E9_EBA4,
    0x13E9_F620,
    0x13E9_FA3C,
    0x13E9_FCE0,
)

RESULT = re.compile(
    rb"DEVROM_COMMAND_T "
    rb"queued=(\d) restored=(\d) bootstrap=(\d) user_queued=(\d) "
    rb"entered=(\d) returned=(\d) "
    rb"run_suites=(\d+) run_tests=(\d+) complete=(\d+) "
    rb"complaints=(\d+) reboot=(\d+)"
)


def queue_stub_words() -> tuple[int, ...]:
    """Return a leaf stub which queues Command-T and resumes the idle path.

    Lua restores every interrupted CPU register after QUEUED is written, so
    the stub cannot leak its temporary register or stack state into Magic Cap.
    The two return-address immediates are patched after Lua samples the PC.
    """
    return (
        0x3C19_13C1,  # lui  t9, high(FLUSH_INSTRUCTION_CACHE)
        0x3739_D250,  # ori  t9, t9, low(FLUSH_INSTRUCTION_CACHE)
        0x0320_F809,  # jalr t9: make low-DRAM completion code executable
        0x0000_0000,
        0x0000_2021,  # move a0, zero: enter through the system run queue
        0x3C05_0030,  # lui  a1, high(BOOTSTRAP_DESCRIPTOR)
        0x34A5_01D0,  # ori  a1, a1, low(BOOTSTRAP_DESCRIPTOR)
        0x3C06_0003,  # lui  a2, high(TEST_MACHINE_SLOT adjusted)
        0x8CC6_D4B4,  # lw   a2, low(TEST_MACHINE_SLOT)
        0x3C19_13CB,  # lui  t9, high(SEMAPHORE_RUN_SOON)
        0x3739_F9A0,  # ori  t9, t9, low(SEMAPHORE_RUN_SOON)
        0x0320_F809,  # jalr t9
        0x0000_0000,
        0x3C08_0030,  # lui  t0, high(QUEUED)
        0x3C09_434D,  # lui  t1, high(MARKER)
        0x3529_4454,  # ori  t1, t1, low(MARKER)
        0xAD09_0200,  # sw   t1, low(QUEUED)(t0)
        0x3C1A_0000,  # lui  k0, high(saved PC): patched by Lua
        0x375A_0000,  # ori  k0, k0, low(saved PC): patched by Lua
        0x0340_0008,  # jr   k0
        0x0000_0000,
    )


def bootstrap_words() -> tuple[int, ...]:
    """Move from the system run queue onto the current actor's user queue."""
    return (
        0x27BD_FFE0,  # addiu sp, sp, -32
        0xAFBF_001C,  # sw    ra, 28(sp)
        0xAFB0_0018,  # sw    s0, 24(sp)
        0x0080_8021,  # move  s0, a0: System_iTestMachine
        0x3C08_0030,  # lui   t0, high(BOOTSTRAP_ENTERED)
        0x3C09_434D,  # lui   t1, high(MARKER)
        0x3529_4454,  # ori   t1, t1, low(MARKER)
        0xAD09_0204,  # sw    t1, low(BOOTSTRAP_ENTERED)(t0)
        0x2404_0001,  # li    a0, 1: queue on the current/user actor
        0x3C05_0030,  # lui   a1, high(CALLBACK_DESCRIPTOR)
        0x34A5_01D8,  # ori   a1, a1, low(CALLBACK_DESCRIPTOR)
        0x0200_3021,  # move  a2, s0
        0x3C19_13CB,  # lui   t9, high(SEMAPHORE_RUN_SOON)
        0x3739_F9A0,  # ori   t9, t9, low(SEMAPHORE_RUN_SOON)
        0x0320_F809,  # jalr  t9
        0x0000_0000,
        0x3C08_0030,  # lui   t0, high(USER_QUEUED)
        0x3C09_434D,  # lui   t1, high(MARKER)
        0x3529_4454,  # ori   t1, t1, low(MARKER)
        0xAD09_0208,  # sw    t1, low(USER_QUEUED)(t0)
        0x0000_1021,  # move  v0, zero
        0x8FBF_001C,  # lw    ra, 28(sp)
        0x8FB0_0018,  # lw    s0, 24(sp)
        0x03E0_0008,  # jr    ra
        0x27BD_0020,  # addiu sp, sp, 32
    )


def callback_words() -> tuple[int, ...]:
    """Return the scheduler completion adapter for TestMachine_CommandTea."""
    return (
        0x27BD_FFE0,  # addiu sp, sp, -32
        0xAFBF_001C,  # sw    ra, 28(sp)
        0x3C08_0030,  # lui   t0, high(ENTERED)
        0x3C09_434D,  # lui   t1, high(MARKER)
        0x3529_4454,  # ori   t1, t1, low(MARKER)
        0xAD09_020C,  # sw    t1, low(ENTERED)(t0)
        0x3C19_13E9,  # lui   t9, high(TEST_MACHINE_COMMAND_TEA)
        0x3739_837C,  # ori   t9, t9, low(TEST_MACHINE_COMMAND_TEA)
        0x0320_F809,  # jalr  t9
        0x0000_0000,
        0x3C08_0030,  # lui   t0, high(RETURNED)
        0x3C09_434D,  # lui   t1, high(MARKER)
        0x3529_4454,  # ori   t1, t1, low(MARKER)
        0xAD09_0210,  # sw    t1, low(RETURNED)(t0)
        0x8FBF_001C,  # lw    ra, 28(sp)
        0x03E0_0008,  # jr    ra
        0x27BD_0020,  # addiu sp, sp, 32
    )


def automation_script(call_frame: int, budget: int) -> str:
    test_body_breakpoints = "\n".join(
        f"""    cpu.debug:bpset(0x{address:08x}, "1",
        "do d@0x{LAST_TEST_BODY:08x}=0x{address:08x}; g")"""
        for address in TEST_BODY_ADDRESSES[1:]
    )
    writes = "\n".join(
        f"    program:write_u32(0x{STUB + index * 4:08x}, 0x{word:08x})"
        for index, word in enumerate(queue_stub_words())
    )
    writes += "\n"
    writes += "\n".join(
        f"    program:write_u32(0x{BOOTSTRAP + index * 4:08x}, 0x{word:08x})"
        for index, word in enumerate(bootstrap_words())
    )
    writes += "\n"
    writes += "\n".join(
        f"    program:write_u32(0x{CALLBACK + index * 4:08x}, 0x{word:08x})"
        for index, word in enumerate(callback_words())
    )
    writes += f"""
    -- CompletionFunction is a MIPS transition-vector descriptor, not a raw
    -- address: the scheduler loads entry PC from +0 and $gp from +4.
    -- The callback itself runs after RestoreMode, so its entry must be the
    -- user-accessible low-DRAM address rather than the injected kseg1 alias.
    program:write_u32(0x{BOOTSTRAP_DESCRIPTOR:08x}, 0x{BOOTSTRAP:08x})
    program:write_u32(0x{BOOTSTRAP_DESCRIPTOR + 4:08x}, 0x{ROM_GP:08x})
    program:write_u32(0x{CALLBACK_DESCRIPTOR:08x}, 0x{CALLBACK:08x})
    program:write_u32(0x{CALLBACK_DESCRIPTOR + 4:08x}, 0x{ROM_GP:08x})"""
    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0
local saved_state = nil
local restored = false
local reported = false
local register_names = {{ "HI", "LO", "SR" }}
for index = 1, 31 do
    table.insert(register_names, "R" .. index)
end

local function counter_breakpoint(address, counter)
    cpu.debug:bpset(address, "1", string.format(
        "do d@0x%08x=d@0x%08x+1; g", counter, counter))
end

local function report()
    if reported then return end
    reported = true
    local active_sounds = 0
    for index = 0, 15 do
        if program:read_u32(0x0000e970 + index * 12) ~= 0 then
            active_sounds = active_sounds + 1
        end
    end
    print(string.format(
        "DEVROM_COMMAND_T STATE pc=%08X system_queue=%d user_queue=%d",
        cpu.state["PC"].value, program:read_u16(0x00010a28),
        program:read_u16(0x00010964)))
    print(string.format(
        "DEVROM_COMMAND_T DISPATCH descriptor=%08X target=%08X test_body=%08X test_index=%d",
        program:read_u32(0x{SCHEDULER_DESCRIPTOR:08x}),
        program:read_u32(0x{SCHEDULER_TARGET:08x}),
        program:read_u32(0x{LAST_TEST_BODY:08x}),
        program:read_u32(0x{LAST_TEST_INDEX:08x})))
    print(string.format(
        "DEVROM_COMMAND_T MOVING_SOUND loops=%d announcements=%d wait=%d done=%d",
        program:read_u32(0x{MOVING_SOUND_LOOPS:08x}),
        program:read_u32(0x{MOVING_SOUND_ANNOUNCEMENTS:08x}),
        program:read_u32(0x{MOVING_SOUND_WAIT:08x}),
        program:read_u32(0x{MOVING_SOUND_DONE:08x})))
    print(string.format(
        "DEVROM_COMMAND_T SOUND active=%d music=%d out=%d in=%d buffer=%d half=%d full=%d instrument_done=%d instrument_completion=%d",
        active_sounds, program:read_u16(0x0000e920),
        program:read_u8(0x0000e938), program:read_u8(0x0000e939),
        program:read_u8(0x0000e930),
        program:read_u32(0x{SOUND_HALF_HITS:08x}),
        program:read_u32(0x{SOUND_FULL_HITS:08x}),
        program:read_u32(0x{INSTRUMENT_DONE_HITS:08x}),
        program:read_u32(0x{INSTRUMENT_COMPLETION_HITS:08x})))
    print(string.format(
        "DEVROM_COMMAND_T DINO size=%08X start=%08X control=%08X dma=%08X int1=%08X enable1=%08X common=%d",
        program:read_u32(0x10c00060), program:read_u32(0x10c00068),
        program:read_u32(0x10c00074), program:read_u32(0x10c00090),
        program:read_u32(0x10c00100), program:read_u32(0x10c00118),
        program:read_u32(0x{SOUND_INTERRUPT_HITS:08x})))
    print(string.format(
        "DEVROM_COMMAND_T queued=%d restored=%d bootstrap=%d user_queued=%d entered=%d returned=%d run_suites=%d run_tests=%d complete=%d complaints=%d reboot=%d",
        program:read_u32(0x{QUEUED:08x}) == 0x{MARKER:08x} and 1 or 0,
        restored and 1 or 0,
        program:read_u32(0x{BOOTSTRAP_ENTERED:08x}) == 0x{MARKER:08x} and 1 or 0,
        program:read_u32(0x{USER_QUEUED:08x}) == 0x{MARKER:08x} and 1 or 0,
        program:read_u32(0x{ENTERED:08x}) == 0x{MARKER:08x} and 1 or 0,
        program:read_u32(0x{RETURNED:08x}) == 0x{MARKER:08x} and 1 or 0,
        program:read_u32(0x{RUN_SUITES_HITS:08x}),
        program:read_u32(0x{RUN_TESTS_HITS:08x}),
        program:read_u32(0x{COMPLETE_HITS:08x}),
        program:read_u32(0x{COMPLAINT_HITS:08x}),
        program:read_u32(0x{REBOOT_HITS:08x})))
    machine:exit()
end

local function install()
{writes}
    for address = 0x{QUEUED:08x}, 0x{INSTRUMENT_COMPLETION_HITS:08x}, 4 do
        program:write_u32(address, 0)
    end
    cpu.debug:bpset(0x13cbfbd8, "1",
        "do d@0x{SCHEDULER_DESCRIPTOR:08x}=R19; g")
    cpu.debug:bpset(0x13cbfbe4, "1",
        "do d@0x{SCHEDULER_TARGET:08x}=R1; g")
{test_body_breakpoints}
    cpu.debug:bpset(0x13e88994, "1",
        "do d@0x{LAST_TEST_INDEX:08x}=R5; g")
    counter_breakpoint(0x13e88798, 0x{MOVING_SOUND_LOOPS:08x})
    counter_breakpoint(0x13e887d8, 0x{MOVING_SOUND_ANNOUNCEMENTS:08x})
    counter_breakpoint(0x13e887e0, 0x{MOVING_SOUND_WAIT:08x})
    counter_breakpoint(0x13e88820, 0x{MOVING_SOUND_DONE:08x})
    counter_breakpoint(0x13c3db9c, 0x{SOUND_INTERRUPT_HITS:08x})
    counter_breakpoint(0x13c3dddc, 0x{SOUND_HALF_HITS:08x})
    counter_breakpoint(0x13c3de08, 0x{SOUND_FULL_HITS:08x})
    counter_breakpoint(0x13c3e5d0, 0x{INSTRUMENT_DONE_HITS:08x})
    counter_breakpoint(0x13c3e5ec, 0x{INSTRUMENT_COMPLETION_HITS:08x})
    counter_breakpoint(0x{TEST_MACHINE_RUN_TEST_SUITES:08x},
        0x{RUN_SUITES_HITS:08x})
    counter_breakpoint(0x{RUN_TESTS:08x}, 0x{RUN_TESTS_HITS:08x})
    counter_breakpoint(0x{TESTS_COMPLETE:08x}, 0x{COMPLETE_HITS:08x})
    counter_breakpoint(0x{FAILURE_ORACLE:08x}, 0x{COMPLAINT_HITS:08x})
    counter_breakpoint(0x{REBOOT_COMMAND_T_FINISHED:08x}, 0x{REBOOT_HITS:08x})
    cpu.debug:go()
end

emu.register_frame_done(function()
    frames = frames + 1
    -- Inject only from the ordinary Doze/DeepDoze idle path.  A fixed frame
    -- can land in a DRAM refresh loop or an interrupt handler, whose transient
    -- context must not be used as a scheduler call boundary.
    local pc = cpu.state["PC"].value
    if frames >= {call_frame} and saved_state == nil
            and pc >= 0x13c3b4a0 and pc < 0x13c3b540 then
        install()
        saved_state = {{ PC = cpu.state["PC"].value }}
        for _, name in ipairs(register_names) do
            saved_state[name] = cpu.state[name].value
        end
        program:write_u32(
            0x{STUB + QUEUE_RETURN_HIGH_INDEX * 4:08x},
            0x3c1a0000 | (saved_state.PC >> 16))
        program:write_u32(
            0x{STUB + QUEUE_RETURN_LOW_INDEX * 4:08x},
            0x375a0000 | (saved_state.PC & 0xffff))
        -- Magic Cap normally dozes while idle.  Resume it before redirecting
        -- the PC for the short queueing call.
        machine.debugger:command("resume :maincpu")
        cpu.state["PC"].value = 0x{STUB_UNCACHED:08x}
        print(string.format(
            "DEVROM_COMMAND_T QUEUE machine=%08X interrupted_pc=%08X",
            program:read_u32(0x{TEST_MACHINE_SLOT:08x}), saved_state.PC))
    elseif saved_state ~= nil and not restored
            and program:read_u32(0x{QUEUED:08x}) == 0x{MARKER:08x} then
        -- The stub has already jumped back to the sampled idle path, leaving
        -- no pending MIPS branch target that could override the PC write.
        for _, name in ipairs(register_names) do
            cpu.state[name].value = saved_state[name]
        end
        cpu.state["PC"].value = saved_state.PC
        restored = true
        print(string.format(
            "DEVROM_COMMAND_T CONTEXT_RESTORED system_queue=%d user_queue=%d",
            program:read_u16(0x00010a28), program:read_u16(0x00010964)))
    elseif restored
            and program:read_u32(0x{RETURNED:08x}) == 0x{MARKER:08x} then
        report()
    elseif frames == {budget} then
        machine.screens[":screen"]:snapshot("command-t-stalled.png")
        report()
    end
end)
"""


def calibration_script() -> str:
    """Boot a fresh development-ROM machine through pen calibration."""
    return """local machine = manager.machine
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

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 1220 then press(240, 160)
    elseif frames == 1240 then touch_button:set_value(0)
    elseif frames == 1420 then press(23, 23)
    elseif frames == 1440 then touch_button:set_value(0)
    elseif frames == 1620 then press(456, 296)
    elseif frames == 1640 then touch_button:set_value(0)
    elseif frames == 1820 then press(240, 160)
    elseif frames == 1840 then touch_button:set_value(0)
    elseif frames == 2400 then
        print("DEVROM_COMMAND_T CALIBRATED")
        machine:exit()
    end
end)
"""


def config_xml(system: str) -> str:
    return f"""<?xml version="1.0"?>
<mameconfig version="10">
    <system name="{system}">
        <input>
            <port tag=":RTC_RESUME" type="CONFIG" mask="1" defvalue="1" value="0" />
        </input>
    </system>
</mameconfig>
"""


def run_mame(
    args: argparse.Namespace,
    lua: Path,
    nvram: Path,
    log: Path,
    seconds: int,
) -> bytes:
    config_dir = nvram.parent / "cfg"
    config_dir.mkdir(exist_ok=True)
    (config_dir / f"{args.system}.cfg").write_text(
        config_xml(args.system), encoding="utf-8"
    )
    try:
        completed = subprocess.run(
            [
                str(args.mame),
                args.system,
                "-rompath",
                str(args.rompath),
                "-cfg_directory",
                str(config_dir),
                "-nvram_directory",
                str(nvram),
                "-autoboot_delay",
                "0",
                "-autoboot_script",
                str(lua),
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
                "-snapshot_directory",
                str(lua.parent),
                "-seconds_to_run",
                str(seconds),
            ],
            cwd=args.mame.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=max(180, seconds * 2),
        )
        output = completed.stdout
    except (OSError, subprocess.TimeoutExpired) as error:
        output = f"unable to run MAME: {error}\n".encode()
    log.write_bytes(output)
    return output


def prepare_nvram(args: argparse.Namespace, base: Path) -> Path | None:
    prep = base / "calibrate"
    nvram = prep / "nvram"
    nvram.mkdir(parents=True)
    lua = prep / "calibrate.lua"
    lua.write_text(calibration_script(), encoding="utf-8")
    output = run_mame(args, lua, nvram, prep / "mame-output.txt", args.seconds)
    if b"DEVROM_COMMAND_T CALIBRATED" not in output:
        return None
    return nvram


def parse_result(output: bytes) -> dict[str, int] | None:
    match = RESULT.search(output)
    if match is None:
        return None
    names = (
        "queued",
        "restored",
        "bootstrap",
        "user_queued",
        "entered",
        "returned",
        "run_suites",
        "run_tests",
        "complete",
        "complaints",
        "reboot",
    )
    return dict(zip(names, (int(value) for value in match.groups()), strict=True))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--system", default="datarover840d")
    parser.add_argument(
        "--nvram-source",
        type=Path,
        help=(
            "already-calibrated NVRAM directory to copy; by default a fresh "
            "machine is calibrated first"
        ),
    )
    parser.add_argument("--call-frame", type=int, default=1200)
    parser.add_argument("--budget", type=int, default=60_000)
    parser.add_argument("--seconds", type=int, default=1200)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    args.mame = args.mame.expanduser().resolve()
    args.rompath = args.rompath.expanduser().resolve()
    if not args.mame.is_file():
        print(f"error: MAME executable not found: {args.mame}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    base = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    base.mkdir(parents=True)
    if args.nvram_source is not None:
        source = args.nvram_source.expanduser().resolve()
        if not (source / args.system / "ram").is_file():
            print(
                f"error: calibrated NVRAM not found under {source}",
                file=sys.stderr,
            )
            return 2
    else:
        print("Calibrating a fresh development-ROM machine ...")
        source = prepare_nvram(args, base)
        if source is None:
            print(
                f"error: calibration did not complete; see {base}",
                file=sys.stderr,
            )
            return 2

    run_dir = base / "command-t"
    nvram = run_dir / "nvram"
    run_dir.mkdir()
    shutil.copytree(source, nvram)
    lua = run_dir / "command-t.lua"
    lua.write_text(automation_script(args.call_frame, args.budget), encoding="utf-8")
    log = run_dir / "mame-output.txt"
    output = run_mame(args, lua, nvram, log, args.seconds)
    result = parse_result(output)
    if result is None:
        print(f"FAIL: Command-T produced no verdict; see {log}", file=sys.stderr)
        return 1

    required = (
        "queued",
        "restored",
        "bootstrap",
        "user_queued",
        "entered",
        "returned",
    )
    missing = [name for name in required if not result[name]]
    if missing:
        print(
            f"FAIL: Command-T missed {', '.join(missing)}; see {log}",
            file=sys.stderr,
        )
        return 1
    if result["run_suites"] != 1:
        print(
            f"FAIL: Command-T did not enter RunTestSuites exactly once; see {log}",
            file=sys.stderr,
        )
        return 1
    if result["run_tests"] != EXPECTED_BASIC_SUITES:
        print(
            f"FAIL: Command-T ran {result['run_tests']} of "
            f"{EXPECTED_BASIC_SUITES} basic suites; see {log}",
            file=sys.stderr,
        )
        return 1
    if result["complete"] != 1 or result["complaints"]:
        print(
            f"FAIL: Command-T complete={result['complete']} "
            f"complaints={result['complaints']}; see {log}",
            file=sys.stderr,
        )
        return 1

    print(
        f"PASS: Command-T returned through the OS scheduler after all "
        f"{result['run_tests']} basic suites with no complaints"
    )
    print(f"Artifacts: {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
