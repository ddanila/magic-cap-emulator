#!/usr/bin/env python3
"""Run the development ROM's own unit-test functions inside the emulator.

The 1998-04-07 development image (`datarover840d`) retains the OS test
framework the shipping ROM omits. This harness boots it, forces a call to a
no-argument unit-test entry point, and uses the oracle the ROM itself names:

    "A TestSite assertion or complaint was triggered in this non-debug build.
     To track this down, put a breakpoint on AnnounceNonDebugFailure and
     re-run the test."

A suite passes when its function returns and `AnnounceNonDebugFailure` was
never entered. This is the same idea as the `BettyTest` serial checkpoint: let
the ROM judge the hardware model.

Addresses are specific to the Apollo USA development build; see
docs/dev-rom.md. Nothing is written into this Git checkout.
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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "devrom-tests"

# AnnounceNonDebugFailure__Fv in the Apollo USA development build.
FAILURE_ORACLE = 0x13E97834

# Formatter test machinery in the Apollo USA development build.  The two
# formatter bodies cannot be called naked: FormatterTestSuite_RunTest swaps in
# the suite's formatter before calling them and restores the system formatter
# afterward.  The formatter suite is item nine in System_iBasicSystemTestList.
READ_REFERENCE_FIELD = 0x13C9423C
FORMATTER_RUN_TEST = 0x13E8D07C
BASIC_SYSTEM_TEST_LIST_SLOT = 0x00029714
FORMATTER_SUITE_LIST_OFFSET = 0x24

# Helpers used by the optional operand trace.  Breakpoints here preserve values
# that AnnounceNonDebugFailure itself does not take as arguments.
CHECK_EXPECTED_TEXT = 0x13E8C4F0
CHECK_EXPECTED_DOUBLE = 0x13E8CD60

# No-argument test entry points (84 exist in the development build). `status`
# records what a forced call from a freshly calibrated machine does:
#
#   "passes"    returns, and the ROM never enters AnnounceNonDebugFailure
#   "noreturn"  keeps the OS running with the PC moving through OS code but
#               never comes back, so it waits on task or scene context; a
#               forced call cannot drive these (see docs/dev-rom.md)
SUITES: dict[str, dict[str, object]] = {
    "datetime": {
        "address": 0x13E9DBFC,
        "symbol": "DateTimeUnitTests__Fv",
        "status": "passes",
    },
    "cache": {
        "address": 0x13E9C824,
        "symbol": "CacheUnitTests__Fv",
        "status": "passes",
    },
    "font": {"address": 0x13E9D488, "symbol": "FontUnitTests__Fv", "status": "passes"},
    "rompristine": {
        "address": 0x13E948F4,
        "symbol": "CheckROMPristineTable__Fv",
        "status": "passes",
    },
    "endianswap": {
        "address": 0x13E8E524,
        "symbol": "TestEndianSwapping__Fv",
        "status": "passes",
    },
    "objectmap": {
        "address": 0x13E8E494,
        "symbol": "TestObjectMap__Fv",
        "status": "passes",
    },
    "cliquetable": {
        "address": 0x13E89DFC,
        "symbol": "TestCliqueTable__Fv",
        "status": "passes",
    },
    "fastenedstack": {
        "address": 0x13E899CC,
        "symbol": "TestFastenedStack__Fv",
        "status": "passes",
    },
    "interchangetable": {
        "address": 0x13E8AE24,
        "symbol": "TestDynamicInterchangeTable__Fv",
        "status": "passes",
    },
    "paths": {
        "address": 0x13E9C81C,
        "symbol": "PathsUnitTests__Fv",
        "status": "passes",
    },
    "textmapping": {
        "address": 0x13E9C684,
        "symbol": "TextMappingUnitTests__Fv",
        "status": "passes",
    },
    "objectname": {
        "address": 0x13E9DEDC,
        "symbol": "ObjectNameTests__Fv",
        "status": "passes",
    },
    # These bodies need FormatterTestSuite_RunTest to install the suite's
    # number formatter. Calling them directly produces 29 and 37 false
    # complaints respectively; through the ROM's wrapper both pass.
    "fmtinteger": {
        "address": 0x13E8C564,
        "symbol": "TestFormattingInteger__Fv",
        "status": "passes",
        "formatter_test": 1,
    },
    "scanfloat": {
        "address": 0x13E8CDE8,
        "symbol": "TestScanningFloatingPoint__Fv",
        "status": "passes",
        "formatter_test": 6,
    },
    # Need context a forced call does not provide.
    "announcement": {
        "address": 0x13E9D5B8,
        "symbol": "AnnouncementUnitTests__Fv",
        "status": "noreturn",
    },
    "contact": {
        "address": 0x13E9CCA0,
        "symbol": "ContactUnitTests__Fv",
        "status": "noreturn",
    },
    "datebook": {
        "address": 0x13E9C1EC,
        "symbol": "DatebookTaskUnitTests__Fv",
        "status": "noreturn",
    },
    "fmtfixed": {
        "address": 0x13E8C7F4,
        "symbol": "TestFormattingFixed__Fv",
        "status": "noreturn",
    },
    "fmtfloat": {
        "address": 0x13E8CD14,
        "symbol": "TestFormattingFloatingPoint__Fv",
        "status": "noreturn",
    },
    "numeraldouble": {
        "address": 0x13E8C968,
        "symbol": "TestNumeralFromDouble__Fv",
        "status": "noreturn",
    },
    "padprecision": {
        "address": 0x13E8CCD0,
        "symbol": "TestPadToMaxPrecision__Fv",
        "status": "noreturn",
    },
    "lossofaccuracy": {
        "address": 0x13E8CC78,
        "symbol": "TestLossOfAccuracy__Fv",
        "status": "noreturn",
    },
    "scaninteger": {
        "address": 0x13E8C748,
        "symbol": "TestScanningInteger__Fv",
        "status": "noreturn",
    },
    "scanfixed": {
        "address": 0x13E8C8BC,
        "symbol": "TestScanningFixed__Fv",
        "status": "noreturn",
    },
    "scantime": {
        "address": 0x13E8CEA4,
        "symbol": "TestScanningTime__Fv",
        "status": "noreturn",
    },
    "buggbm15189": {
        "address": 0x13E8CBE0,
        "symbol": "TestBuggbm15189__Fv",
        "status": "noreturn",
    },
    "bugrwt12821": {
        "address": 0x13E8CC3C,
        "symbol": "TestBugRWT12821__Fv",
        "status": "noreturn",
    },
    "textstyle": {
        "address": 0x13E9D140,
        "symbol": "TextStyleUnitTests__Fv",
        "status": "noreturn",
    },
}
DEFAULT_SUITES = tuple(
    name for name, suite in SUITES.items() if suite["status"] == "passes"
)

# Scratch DRAM for the call stub and its result words. The harness hijacks the
# CPU and never resumes normal operation, so clobbering heap bytes here is
# deliberate; this is a diagnostic run, not a live session.
STUB = 0x00300000
STUB_UNCACHED = 0xA0300000
DONE = STUB + 0x100
HITS = STUB + 0x104
MARKER_VALUE = 0x12345678

RESULT_PATTERN = re.compile(
    rb"DEVROM_TEST RESULT suite=(\w+) returned=(\d) failures=(\d+)"
)


def call_stub_words(target: int, formatter_test_index: int | None = None) -> list[int]:
    """Return the MIPS stub that calls `target` and then parks in a spin loop.

    `jal` cannot reach 0x13e9xxxx from low DRAM (its 28-bit range keeps the top
    four address bits), so the call goes through `jalr`.

    Formatter tests first resolve item nine in the live basic-system test list
    and call FormatterTestSuite_RunTest(object, test_index).  This reproduces
    the ROM's formatter swap instead of calling the test body out of context.
    """
    marker_hi, marker_lo = MARKER_VALUE >> 16, MARKER_VALUE & 0xFFFF
    done_hi, done_lo = DONE >> 16, DONE & 0xFFFF
    words: list[int] = []
    if formatter_test_index is not None:
        list_slot_hi = (BASIC_SYSTEM_TEST_LIST_SLOT + 0x8000) >> 16
        list_slot_lo = BASIC_SYSTEM_TEST_LIST_SLOT & 0xFFFF
        words.extend(
            (
                0x3C040000 | list_slot_hi,
                0x8C840000 | list_slot_lo,  # lw $4, basic test list slot
                0x24050000 | FORMATTER_SUITE_LIST_OFFSET,  # li $5, 0x24
                0x3C190000 | (READ_REFERENCE_FIELD >> 16),
                0x37390000 | (READ_REFERENCE_FIELD & 0xFFFF),
                0x0320F809,  # jalr $25: resolve list item nine
                0x00000000,
                0x00402021,  # move $4, $2: formatter suite object
                0x24050000 | formatter_test_index,  # li $5, test index
            )
        )
        target = FORMATTER_RUN_TEST

    words.extend(
        [
            0x3C190000 | (target >> 16),  # lui  $25, target_hi
            0x37390000 | (target & 0xFFFF),  # ori $25, $25, target_lo
            0x0320F809,  # jalr $25
            0x00000000,  # nop  (delay slot)
            0x3C080000 | marker_hi,  # lui  $8, marker_hi
            0x35080000 | marker_lo,  # ori  $8, $8, marker_lo
            0x3C090000 | done_hi,  # lui  $9, done_hi
            0x35290000 | done_lo,  # ori  $9, $9, done_lo
            0xAD280000,  # sw   $8, 0($9)
            0x1000FFFF,  # b    .   (park)
            0x00000000,  # nop
        ]
    )
    return words


def config_xml(system: str) -> str:
    """Return a MAME config that pins the RTC to its saved value.

    The driver normally advances the RTC by the host wall-clock time that
    passed while the machine was off, so a suite would see a different time of
    day on every run. Freezing it keeps these checks reproducible.
    """
    return f"""<?xml version="1.0"?>
<mameconfig version="10">
    <system name="{system}">
        <input>
            <port tag=":RTC_RESUME" type="CONFIG" mask="1" defvalue="1" value="0" />
        </input>
    </system>
</mameconfig>
"""


def calibration_script() -> str:
    """Return Lua that boots a fresh machine through calibration and exits.

    Forcing a call during a first boot does not work: the unit tests never
    return while first-run initialization is still settling. Suites are
    therefore run against the NVRAM this phase leaves behind.
    """
    return r"""local machine = manager.machine
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
    elseif frames == 2400 then
        print("DEVROM_TEST CALIBRATED")
        machine:exit()
    end
end)
"""


def automation_script(
    suite: str,
    target: int,
    budget_frames: int,
    call_frame: int,
    oracle: int = FAILURE_ORACLE,
    trace_complaints: bool = False,
    formatter_test_index: int | None = None,
) -> str:
    """Return the MAME Lua driving boot, calibration, and the forced call."""
    stub_writes = "\n".join(
        f"    program:write_u32(0x{STUB + 4 * index:08x}, 0x{word:08x})"
        for index, word in enumerate(call_stub_words(target, formatter_test_index))
    )
    complaint_commands = []
    if trace_complaints:
        complaint_commands.append(
            f'logerror "DEVROM_TEST COMPLAINT suite={suite} '
            "ra=%08X r2=%08X r4=%08X r5=%08X r6=%08X r7=%08X "
            'r16=%08X r17=%08X r18=%08X r19=%08X r29=%08X\\n",'
            "R31,R2,R4,R5,R6,R7,R16,R17,R18,R19,R29"
        )
    complaint_commands.extend(
        (
            f"do d@0x{HITS:08x}=d@0x{HITS:08x}+1",
            "g",
        )
    )
    complaint_action = "; ".join(complaint_commands)

    trace_breakpoints = ""
    if trace_complaints and suite == "fmtinteger":
        trace_breakpoints = f"""
        cpu.debug:bpset(0x{CHECK_EXPECTED_TEXT:08x}, "1",
            [==[logerror "DEVROM_TEST EXPECTED_TEXT suite={suite} actual=%08X expected=%08X text=%.80s\\n",R4,R5,R5; g]==])
"""
    elif trace_complaints and suite == "scanfloat":
        trace_breakpoints = f"""
        cpu.debug:bpset(0x{CHECK_EXPECTED_DOUBLE:08x}, "1",
            [==[logerror "DEVROM_TEST EXPECTED_DOUBLE suite={suite} ref_actual=%08X ref_expected=%08X value=%08X%08X expected=%08X%08X\\n",R4,R5,R6,R7,d@(R29+0x10),d@(R29+0x14); g]==])
"""

    template = r"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0
local called = false

local DONE = 0x__DONE__
local HITS = 0x__HITS__

local function write_stub()
__STUB_WRITES__
    program:write_u32(DONE, 0)
    program:write_u32(HITS, 0)
end

-- Boots from the calibrated NVRAM produced by the preparation phase, so no
-- welcome or calibration taps are needed here.
emu.register_frame_done(function()
    frames = frames + 1

    if frames == __CALL_FRAME__ then
        machine.screens[":screen"]:snapshot("before-__SUITE__.png")
        -- Count entries into the ROM's complaint reporter without halting.
        cpu.debug:bpset(0x__ORACLE__, "1",
            [==[__COMPLAINT_ACTION__]==])
__TRACE_BREAKPOINTS__
        write_stub()
        cpu.state["PC"].value = 0x__STUB_UNCACHED__
        called = true
        print(string.format(
            "DEVROM_TEST CALL suite=__SUITE__ target=%08X", 0x__TARGET__))
    elseif called and frames % 120 == 0 then
        if program:read_u32(DONE) == 0x__MARKER__ then
            print(string.format(
                "DEVROM_TEST RESULT suite=__SUITE__ returned=1 failures=%d",
                program:read_u32(HITS)))
            machine.screens[":screen"]:snapshot("after-__SUITE__.png")
            machine:exit()
        end
    end

    if frames == __BUDGET__ then
        print(string.format(
            "DEVROM_TEST RESULT suite=__SUITE__ returned=0 failures=%d",
            program:read_u32(HITS)))
        print(string.format("DEVROM_TEST NORETURN pc=%08X",
            cpu.state["PC"].value))
        machine.screens[":screen"]:snapshot("noreturn-__SUITE__.png")
        machine:exit()
    end
end)
"""
    replacements = {
        "__STUB_WRITES__": stub_writes,
        "__SUITE__": suite,
        "__TARGET__": f"{target:08x}",
        "__ORACLE__": f"{oracle:08x}",
        "__COMPLAINT_ACTION__": complaint_action,
        "__TRACE_BREAKPOINTS__": trace_breakpoints.rstrip(),
        "__STUB_UNCACHED__": f"{STUB_UNCACHED:08x}",
        "__DONE__": f"{DONE:08x}",
        "__HITS__": f"{HITS:08x}",
        "__MARKER__": f"{MARKER_VALUE:08x}",
        "__BUDGET__": str(budget_frames),
        "__CALL_FRAME__": str(call_frame),
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    return template


def parse_results(output: bytes) -> dict[str, tuple[bool, int]]:
    """Map suite name to (returned, failure_count) from MAME output."""
    results: dict[str, tuple[bool, int]] = {}
    for match in RESULT_PATTERN.finditer(output):
        suite = match.group(1).decode("ascii")
        results[suite] = (match.group(2) == b"1", int(match.group(3)))
    return results


def run_mame(
    args: argparse.Namespace,
    lua_path: Path,
    nvram_dir: Path,
    snapshot_dir: Path,
    seconds: int,
    log_path: Path,
) -> bytes:
    """Run one headless MAME session and return its combined output."""
    config_dir = nvram_dir.parent / "cfg"
    config_dir.mkdir(exist_ok=True)
    if args.rtc == "frozen":
        (config_dir / f"{args.system}.cfg").write_text(
            config_xml(args.system), encoding="utf-8"
        )

    command = [
        str(args.mame),
        args.system,
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
        "-seconds_to_run",
        str(seconds),
    ]
    if args.trace_complaints:
        # Breakpoint `printf` output is swallowed by the headless debugger
        # backend.  `logerror` plus `-oslog` routes it to captured stderr
        # without creating a shared error.log beside the MAME executable.
        command.append("-oslog")
    completed = subprocess.run(command, cwd=args.mame.parent, capture_output=True)
    output = completed.stdout + completed.stderr
    log_path.write_bytes(output)
    return output


def prepare_nvram(args: argparse.Namespace, base_dir: Path) -> Path | None:
    """Boot once through calibration and return the resulting NVRAM directory."""
    prep_dir = base_dir / "calibrate"
    nvram_dir = prep_dir / "nvram"
    snapshot_dir = prep_dir / "snapshots"
    nvram_dir.mkdir(parents=True)
    snapshot_dir.mkdir()
    lua_path = prep_dir / "calibrate.lua"
    lua_path.write_text(calibration_script(), encoding="utf-8")

    output = run_mame(
        args,
        lua_path,
        nvram_dir,
        snapshot_dir,
        args.seconds,
        prep_dir / "mame-output.txt",
    )
    if b"DEVROM_TEST CALIBRATED" not in output:
        return None
    return nvram_dir


def run_suite(
    args: argparse.Namespace, suite: str, run_dir: Path, nvram_source: Path
) -> tuple[bool, int] | None:
    """Boot the development ROM warm, force one suite, and return its verdict."""
    target = int(SUITES[suite]["address"])  # type: ignore[arg-type]
    formatter_test_index = SUITES[suite].get("formatter_test")
    nvram_dir = run_dir / "nvram"
    snapshot_dir = run_dir / "snapshots"
    # Every suite starts from its own copy, so one suite cannot disturb another.
    shutil.copytree(nvram_source, nvram_dir)
    snapshot_dir.mkdir(exist_ok=True)
    lua_path = run_dir / f"{suite}.lua"
    # --self-check points the counter at the suite function itself, which the
    # stub calls exactly once. A PASS there proves the breakpoint counter can
    # fire, so a zero count in a normal run is a real negative rather than a
    # detector that silently did nothing.
    oracle = target if args.self_check else FAILURE_ORACLE
    lua_path.write_text(
        automation_script(
            suite,
            target,
            args.budget,
            args.call_frame,
            oracle,
            args.trace_complaints,
            (int(formatter_test_index) if formatter_test_index is not None else None),
        ),
        encoding="utf-8",
    )

    output = run_mame(
        args,
        lua_path,
        nvram_dir,
        snapshot_dir,
        args.seconds,
        run_dir / f"{suite}-mame-output.txt",
    )
    return parse_results(output).get(suite)


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
    parser.add_argument(
        "--system",
        default="datarover840d",
        help="MAME system to boot (default: datarover840d)",
    )
    parser.add_argument(
        "--suite",
        action="append",
        choices=sorted(SUITES),
        help=(
            "suite to run; repeatable. Default: every suite known to pass "
            f"({len(DEFAULT_SUITES)} of {len(SUITES)})"
        ),
    )
    parser.add_argument(
        "--rtc",
        choices=("frozen", "host"),
        default="frozen",
        help=(
            "'frozen' (default) pins the resumed RTC to its saved value for "
            "reproducible runs; 'host' keeps the driver's realistic behavior of "
            "advancing it by elapsed host time"
        ),
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help=(
            "validate the oracle instead of testing: count entries into the "
            "suite function itself, which must be exactly one"
        ),
    )
    parser.add_argument(
        "--trace-complaints",
        action="store_true",
        help=(
            "log comparison operands and any complaint caller for the "
            "fmtinteger and scanfloat diagnostics"
        ),
    )
    parser.add_argument(
        "--nvram-source",
        type=Path,
        help=(
            "reuse an existing calibrated NVRAM directory instead of booting a "
            "fresh machine first (it is copied, never modified)"
        ),
    )
    parser.add_argument(
        "--call-frame",
        type=int,
        default=900,
        help="frame after the warm boot at which to force the call (default: 900)",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=6000,
        help="emulated frames before giving up on a call (default: 6000)",
    )
    parser.add_argument(
        "--seconds",
        type=int,
        default=120,
        help="emulated seconds per suite (default: 120)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    args.mame = args.mame.expanduser().resolve()
    args.rompath = args.rompath.expanduser().resolve()
    workdir = args.workdir.expanduser().resolve()

    if not args.mame.is_file():
        print(f"error: MAME executable not found: {args.mame}", file=sys.stderr)
        return 2
    if not args.rompath.is_dir():
        print(f"error: ROM path not found: {args.rompath}", file=sys.stderr)
        return 2

    suites = tuple(args.suite) if args.suite else DEFAULT_SUITES
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    base_dir = workdir / f"{stamp}-{os.getpid()}"
    base_dir.mkdir(parents=True)

    if args.nvram_source is not None:
        nvram_source = args.nvram_source.expanduser().resolve()
        if not nvram_source.is_dir():
            print(f"error: NVRAM source not found: {nvram_source}", file=sys.stderr)
            return 2
    else:
        print("Calibrating a fresh machine to reuse for every suite ...")
        nvram_source = prepare_nvram(args, base_dir)
        if nvram_source is None:
            print("error: calibration boot did not complete", file=sys.stderr)
            print(f"Artifacts: {base_dir}", file=sys.stderr)
            return 2

    failures = 0
    for suite in suites:
        run_dir = base_dir / suite
        run_dir.mkdir(parents=True)
        verdict = run_suite(args, suite, run_dir, nvram_source)
        symbol = SUITES[suite]["symbol"]
        if verdict is None:
            print(f"FAIL: {suite} ({symbol}) produced no verdict")
            failures += 1
            continue
        returned, complaints = verdict
        if args.self_check:
            if returned and complaints == 1:
                print(f"PASS: {suite} oracle counted exactly one entry")
            else:
                print(
                    f"FAIL: {suite} oracle self-check returned={returned} "
                    f"count={complaints}, expected returned=True count=1"
                )
                failures += 1
            continue
        if returned and complaints == 0:
            print(f"PASS: {suite} ({symbol}) returned, no ROM complaint")
        elif returned:
            print(f"FAIL: {suite} ({symbol}) reported {complaints} complaint(s)")
            failures += 1
        else:
            print(
                f"FAIL: {suite} ({symbol}) did not return within {args.budget} frames"
            )
            failures += 1

    print(f"Artifacts: {base_dir}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
