#!/usr/bin/env python3
"""Run whole test suites from the development ROM's basic system test list.

`tools/devrom_tests.py` calls individual no-argument test bodies. That works
only for tests that need no setup, and it cannot reach the 28 `*TestSuite_*`
classes at all. This drives them the way the OS does:

    suite = ReadReferenceField(System_iBasicSystemTestList, offset)
    RunTests(System_iTestMachine, suite, 0)

`RunTests` is the primitive underneath `TestMachine_RunOneTest`, and index 0
means "every test in this suite". Going through the suite matters: the suite
installs each test's fixture, which is why the formatter tests report failures
when their bodies are called naked and pass when run this way.

`TestMachine_RunOneTest` itself is deliberately avoided - it wraps `RunTests`
with `TestsComplete`, which never returns to an injected frame.

Suite objects live at offsets 0x04 upward in the list. Addresses are from the
Apollo USA development build; see docs/dev-rom.md.
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
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = Path.home() / "fun" / "magic-cap-assets" / "roms"
DEFAULT_WORKDIR = (
    Path.home() / "fun" / "magic-cap-assets" / "runtime" / "devrom-suites"
)

TEST_MACHINE_SLOT = 0x0002D4B4        # System_iTestMachine
BASIC_SYSTEM_TEST_LIST_SLOT = 0x00029714  # System_iBasicSystemTestList
READ_REFERENCE_FIELD = 0x13C9423C
RUN_TESTS = 0x13E97C90                # RunTests(machine, suite, index)
FAILURE_ORACLE = 0x13E97834           # AnnounceNonDebugFailure
RUN_ALL_TESTS_IN_SUITE = 0

STUB = 0x0030_0000
SUITE_SLOT = STUB + 0x80
DONE = STUB + 0x84
HITS = STUB + 0x88
MARKER_VALUE = 0x12345678

FIRST_SUITE_OFFSET = 0x04
LAST_SUITE_OFFSET = 0x40

RESULT = re.compile(
    rb"DEVROM_SUITE offset=0x([0-9a-f]+) suite=([0-9A-F]{8}) "
    rb"returned=(\d) complaints=(\d+)"
)


def suite_stub_words(offset: int, index: int = RUN_ALL_TESTS_IN_SUITE) -> list[int]:
    """Resolve one list entry and run its tests, then park.

    `lw` sign-extends its offset, so the slot address is split the way the
    assembler would: high half adjusted by 0x8000 when the low half is
    negative.
    """
    def load_slot(register: int, slot: int) -> list[int]:
        return [
            0x3C000000 | (register << 16) | ((slot + 0x8000) >> 16),
            0x8C000000 | (register << 21) | (register << 16) | (slot & 0xFFFF),
        ]

    words: list[int] = []
    # suite = ReadReferenceField(*list_slot, offset)
    words += load_slot(4, BASIC_SYSTEM_TEST_LIST_SLOT)
    words += [
        0x24050000 | offset,                        # li   $5, offset
        0x3C190000 | (READ_REFERENCE_FIELD >> 16),
        0x37390000 | (READ_REFERENCE_FIELD & 0xFFFF),
        0x0320F809,                                 # jalr $25
        0x00000000,
        0x3C09A030, 0x35290080, 0xAD220000,         # sw   $2, SUITE_SLOT
    ]
    # RunTests(*test_machine_slot, suite, index)
    words += load_slot(4, TEST_MACHINE_SLOT)
    words += [
        0x3C09A030, 0x35290080, 0x8D250000,         # lw   $5, SUITE_SLOT
        0x24060000 | index,                         # li   $6, index
        0x3C190000 | (RUN_TESTS >> 16),
        0x37390000 | (RUN_TESTS & 0xFFFF),
        0x0320F809,                                 # jalr $25
        0x00000000,
        0x3C080000 | (MARKER_VALUE >> 16),
        0x35080000 | (MARKER_VALUE & 0xFFFF),
        0x3C09A030, 0x35290084, 0xAD280000,         # sw   $8, DONE
        0x1000FFFF, 0x00000000,                     # park
    ]
    return words


def automation_script(offset: int, budget: int, call_frame: int) -> str:
    writes = "\n".join(
        f"    program:write_u32(0x{STUB + index * 4:08x}, 0x{word:08x})"
        for index, word in enumerate(suite_stub_words(offset))
    )
    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames, called = 0, false

emu.register_frame_done(function()
    frames = frames + 1
    if frames == {call_frame} then
{writes}
        program:write_u32(0x{DONE:08x}, 0)
        program:write_u32(0x{HITS:08x}, 0)
        cpu.debug:bpset(0x{FAILURE_ORACLE:08x}, "1",
            "do d@0x{HITS:08x}=d@0x{HITS:08x}+1; g")
        cpu.state["PC"].value = 0x{STUB | 0xa000_0000:08x}
        called = true
    elseif called and frames % 60 == 0 then
        if program:read_u32(0x{DONE:08x}) == 0x{MARKER_VALUE:08x} then
            print(string.format(
                "DEVROM_SUITE offset=0x%02x suite=%08X returned=1 complaints=%d",
                {offset}, program:read_u32(0x{SUITE_SLOT:08x}),
                program:read_u32(0x{HITS:08x})))
            machine:exit()
        end
    end
    if frames == {budget} then
        print(string.format(
            "DEVROM_SUITE offset=0x%02x suite=%08X returned=0 complaints=%d",
            {offset}, program:read_u32(0x{SUITE_SLOT:08x}),
            program:read_u32(0x{HITS:08x})))
        machine:exit()
    end
end)
"""


def parse_result(output: bytes) -> dict[str, int] | None:
    match = RESULT.search(output)
    if not match:
        return None
    return {
        "offset": int(match.group(1), 16),
        "suite": int(match.group(2), 16),
        "returned": int(match.group(3)),
        "complaints": int(match.group(4)),
    }


def calibration_script() -> str:
    """Boot a fresh machine through calibration and exit, leaving NVRAM."""
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
        print("DEVROM_SUITE CALIBRATED")
        machine:exit()
    end
end)
"""


def config_xml(system: str) -> str:
    """Pin the resumed RTC so runs are reproducible across days."""
    return f"""<?xml version="1.0"?>
<mameconfig version="10">
    <system name="{system}">
        <input>
            <port tag=":RTC_RESUME" type="CONFIG" mask="1" defvalue="1" value="0" />
        </input>
    </system>
</mameconfig>
"""


def run_mame(args, lua: Path, nvram: Path, log: Path, seconds: int) -> bytes:
    config_dir = nvram.parent / "cfg"
    config_dir.mkdir(exist_ok=True)
    (config_dir / f"{args.system}.cfg").write_text(
        config_xml(args.system), encoding="utf-8"
    )
    completed = subprocess.run(
        [
            str(args.mame), args.system,
            "-rompath", str(args.rompath),
            "-cfg_directory", str(config_dir),
            "-nvram_directory", str(nvram),
            "-autoboot_delay", "0",
            "-autoboot_script", str(lua),
            "-debug", "-debugger", "none",
            "-video", "none", "-sound", "none",
            "-videodriver", "dummy", "-audiodriver", "dummy",
            "-nothrottle", "-skip_gameinfo",
            "-seconds_to_run", str(seconds),
        ],
        cwd=args.mame.parent,
        capture_output=True,
        timeout=seconds * 8 + 120,
    )
    output = completed.stdout + completed.stderr
    log.write_bytes(output)
    return output


def prepare_nvram(args, base: Path) -> Path | None:
    prep = base / "calibrate"
    nvram = prep / "nvram"
    nvram.mkdir(parents=True)
    lua = prep / "calibrate.lua"
    lua.write_text(calibration_script(), encoding="utf-8")
    output = run_mame(args, lua, nvram, prep / "mame-output.txt", args.seconds)
    return nvram if b"DEVROM_SUITE CALIBRATED" in output else None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--system", default="datarover840d")
    parser.add_argument(
        "--offset",
        type=lambda value: int(value, 0),
        action="append",
        help="list offset to run; repeatable. Default: every suite slot",
    )
    parser.add_argument("--budget", type=int, default=9000,
                        help="frames to allow a suite before giving up")
    parser.add_argument(
        "--call-frame",
        type=int,
        default=2400,
        help=(
            "frame at which to force the call. The test machine and its suite "
            "list are not resolvable earlier on a freshly calibrated boot: at "
            "900 the list read returns nothing at all"
        ),
    )
    parser.add_argument("--seconds", type=int, default=260)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    args.mame = args.mame.expanduser().resolve()
    args.rompath = args.rompath.expanduser().resolve()
    if not args.mame.is_file():
        print(f"error: MAME executable not found: {args.mame}", file=sys.stderr)
        return 2

    offsets = args.offset or list(
        range(FIRST_SUITE_OFFSET, LAST_SUITE_OFFSET + 1, 4)
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    base = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    base.mkdir(parents=True)

    print("Calibrating a fresh machine to reuse for every suite ...")
    source = prepare_nvram(args, base)
    if source is None:
        print(f"error: calibration boot did not complete; see {base}",
              file=sys.stderr)
        return 2

    ran = complained = stalled = 0
    for offset in offsets:
        run_dir = base / f"offset-{offset:02x}"
        nvram = run_dir / "nvram"
        run_dir.mkdir()
        shutil.copytree(source, nvram)
        lua = run_dir / "suite.lua"
        lua.write_text(
            automation_script(offset, args.budget, args.call_frame),
            encoding="utf-8",
        )
        output = run_mame(args, lua, nvram, run_dir / "mame-output.txt",
                          args.seconds)
        result = parse_result(output)
        if result is None:
            print(f"  offset 0x{offset:02x}: no verdict")
            stalled += 1
            continue
        if not result["returned"]:
            print(f"  offset 0x{offset:02x}: suite {result['suite']:#010x} "
                  "did not return")
            stalled += 1
        elif result["complaints"]:
            print(f"  offset 0x{offset:02x}: suite {result['suite']:#010x} "
                  f"reported {result['complaints']} complaint(s)")
            complained += 1
        else:
            print(f"  offset 0x{offset:02x}: suite {result['suite']:#010x} "
                  "ran clean")
            ran += 1

    print(f"\n{ran} suite(s) ran clean, {complained} complained, "
          f"{stalled} did not return")
    print(f"Artifacts: {base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
