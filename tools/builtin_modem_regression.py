#!/usr/bin/env python3
"""Drive Magic Cap's built-in software modem over Dino telecom DMA.

The test boots the Apollo USA 3.1 ROM from provider-configured NVRAM, calls the
ROM's real SoftwareModem_OpenModemPort and StartDataModem entry points, selects
V.32, and then returns to the interrupted Magic Cap task. Debugger counters
prove that the ROM:

  * spawns its software-modem actors and starts Dino telecom DMA;
  * keeps the ROM's 48-word bidirectional telecom ring enabled;
  * reaches the V.32 pump, control, and FIR routines;
  * executes the TX39 MADD instruction in V32ModulatorFIR.

This exercises the ROM/Dino boundary without pretending that a telephone DAA,
remote carrier, or Internet provider is present. Addresses are specific to the
shipping Apollo USA 3.1 ROM documented in docs/builtin-modem.md.
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
    Path.home()
    / "fun"
    / "magic-cap-assets"
    / "runtime"
    / "builtin-modem-regression"
)

STUB = 0x0030_0000
DONE = STUB + 0xF0
COUNTERS = STUB + 0x100
SAVED_S7 = STUB + 0x140
SAVED_GP = STUB + 0x144
OBSERVED_DMA = STUB + 0x148

SYMBOLS = (
    (0x13C5_9A08, "open"),
    (0x13C2_4EA4, "spawn"),
    (0x13C2_28E4, "server"),
    (0x13C2_3B3C, "dma_start"),
    (0x13C2_5198, "half"),
    (0x13C2_5230, "full"),
    (0x13E4_2F20, "init"),
    (0x13E4_3160, "receive"),
    (0x13E4_31B4, "transmit"),
    (0x13E4_31DC, "install"),
    (0x13E5_0B10, "v32pump"),
    (0x13E4_9B40, "v32control"),
    (0x13E5_18E0, "v32fir"),
    (0x13E5_1974, "madd"),
)

RESULT_PATTERN = re.compile(
    rb"BUILTIN_MODEM_RESULT "
    rb"open=(\d) spawn=(\d) server=(\d) dma_start=(\d) "
    rb"half=(\d) full=(\d) init=(\d) receive=(\d) transmit=(\d) "
    rb"install=(\d) v32pump=(\d) v32control=(\d) v32fir=(\d) madd=(\d) "
    rb"returned=(\d) enables=(\d) size=(\d+) "
    rb"tx=([0-9A-F]{8}) rx=([0-9A-F]{8})"
)


def modem_script(
    call_frame: int = 1200,
    result_frame: int = 2000,
) -> str:
    """Return Lua that calls the ROM modem and traces its DMA callbacks."""
    addresses = ",\n    ".join(f"0x{address:08x}" for address, _ in SYMBOLS)
    counter_reads = ",\n            ".join(
        f'program:read_u32(COUNTERS + {index * 4})'
        for index in range(len(SYMBOLS))
    )
    open_stub_words = (
        0x3C04_0003,  # lui  a0, 3
        0x8C84_CA84,  # lw   a0, -0x357c(a0): System_iSoftwareModem
        0x3C19_13C5,  # lui  t9, 0x13c5
        0x3739_9A08,  # ori  t9, t9, 0x9a08
        0x0320_F809,  # jalr t9: SoftwareModem_OpenModemPort
        0x0000_0000,
        0x0000_2021,  # move a0, zero
        0x3C19_13C5,  # lui  t9, 0x13c5
        0x3739_BF80,  # ori  t9, t9, 0xbf80
        0x0320_F809,  # jalr t9: StartDataModem
        0x0000_0000,
        0x3C08_0030,  # lui  t0, 0x0030
        0x8D17_0140,  # lw   s7, 0x140(t0): modem DSP global base
        0x8D1C_0144,  # lw   gp, 0x144(t0): modem DSP small-data base
        0x3404_0080,  # ori  a0, zero, 128: V.32 modulation
        0x3C19_13E4,  # lui  t9, 0x13e4
        0x3739_31DC,  # ori  t9, t9, 0x31dc
        0x0320_F809,  # jalr t9: DataModemInstallModulation
        0x0000_0000,
        0x3C04_A030,  # lui  a0, 0xa030
        0x3484_00C0,  # ori  a0, a0, 0xc0: local SibCommand
        0x3C19_13C2,  # lui  t9, 0x13c2
        0x3739_3B3C,  # ori  t9, t9, 0x3b3c
        0x0320_F809,  # jalr t9: SibCmdStartTelecom
        0x0000_0000,
        0x3C08_B0C0,  # lui  t0, 0xb0c0: Dino
        0x8D09_0090,  # lw   t1, 0x90(t0): sibDMA
        0x3C0A_0030,  # lui  t2, 0x0030
        0xAD49_0148,  # sw   t1, 0x148(t2): observed DMA state
        0x3404_0004,  # ori  a0, zero, 4: one FIR group
        0x3C05_A030,  # lui  a1, 0xa030
        0x34A5_0200,  # ori  a1, a1, 0x200: FIR input/history
        0x3C06_A030,  # lui  a2, 0xa030
        0x34C6_0220,  # ori  a2, a2, 0x220: FIR output
        0x3C19_13E5,  # lui  t9, 0x13e5
        0x3739_18E0,  # ori  t9, t9, 0x18e0
        0x0320_F809,  # jalr t9: V32ModulatorFIR
        0x0000_0000,
        0x3C08_0030,  # lui  t0, 0x0030
        0x3409_0001,  # ori  t1, zero, 1
        0xAD09_00F0,  # sw   t1, 0xf0(t0): completed
        0x1000_FFFF,  # b .
        0x0000_0000,
    )
    open_stub_writes = "\n".join(
        f"    program:write_u32(STUB + {index * 4}, 0x{word:08x})"
        for index, word in enumerate(open_stub_words)
    )

    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0
local restored = false
local saved_state = nil
local injected_frame = 0
local retries = 0

local STUB, DONE, COUNTERS = 0x{STUB:08x}, 0x{DONE:08x}, 0x{COUNTERS:08x}
local SAVED_S7 = 0x{SAVED_S7:08x}
local SAVED_GP = 0x{SAVED_GP:08x}
local OBSERVED_DMA = 0x{OBSERVED_DMA:08x}
local addresses = {{
    {addresses}
}}
local register_names = {{ "HI", "LO", "SR" }}
for index = 1, 31 do
    table.insert(register_names, "R" .. index)
end

for index, address in ipairs(addresses) do
    local counter = COUNTERS + ((index - 1) * 4)
    local action = string.format(
        "do d@0x%08x=d@0x%08x+1; g", counter, counter)
    if address == 0x13e42f20 then
        action = string.format(
            "do d@0x%08x=d@0x%08x+1; d@0x%08x=R23; d@0x%08x=R28; g",
            counter, counter, SAVED_S7, SAVED_GP)
    end
    cpu.debug:bpset(
        address,
        string.format("d@0x%08x==0", counter),
        action)
end
cpu.debug:go()

local function write_stub()
{open_stub_writes}
    for index = 0, #addresses - 1 do
        program:write_u32(COUNTERS + index * 4, 0)
    end
    program:write_u32(SAVED_S7, 0)
    program:write_u32(SAVED_GP, 0)
    program:write_u32(OBSERVED_DMA, 0)
    -- SibCmdStartTelecom only consumes the sample-rate divisor at +4.
    program:write_u32(STUB + 0xc4, 39)
    for address = STUB + 0x200, STUB + 0x23c, 4 do
        program:write_u32(address, 0)
    end
    program:write_u32(DONE, 0)
end

local function inject()
    saved_state = {{ PC = cpu.state["PC"].value }}
    for _, name in ipairs(register_names) do
        saved_state[name] = cpu.state[name].value
    end
    restored = false
    injected_frame = frames
    cpu.state["PC"].value = 0xa0300000
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames >= {call_frame} and saved_state == nil then
        write_stub()
        -- Magic Cap normally dozes while idle.  The debugger resume command
        -- clears the CPU's HALT suspension before redirecting its PC.
        machine.debugger:command("resume :maincpu")
        print(string.format(
            "BUILTIN_MODEM_CALL object=%08X",
            program:read_u32(0x0002ca84)))
        inject()
    elseif saved_state ~= nil and not restored
            and program:read_u32(DONE) == 1 then
        for _, name in ipairs(register_names) do
            cpu.state[name].value = saved_state[name]
        end
        cpu.state["PC"].value = saved_state.PC
        restored = true
        print("BUILTIN_MODEM_RETURN")
    elseif saved_state ~= nil and not restored
            and program:read_u32(COUNTERS) == 0
            and frames >= injected_frame + 30 and retries < 3 then
        for _, name in ipairs(register_names) do
            cpu.state[name].value = saved_state[name]
        end
        retries = retries + 1
        injected_frame = frames
        machine.debugger:command("resume :maincpu")
        cpu.state["PC"].value = 0xa0300000
        print(string.format("BUILTIN_MODEM_RETRY attempt=%d", retries + 1))
    elseif frames == {result_frame} then
        local dma = program:read_u32(OBSERVED_DMA)
        print(string.format(
            "BUILTIN_MODEM_RESULT open=%d spawn=%d server=%d dma_start=%d half=%d full=%d init=%d receive=%d transmit=%d install=%d v32pump=%d v32control=%d v32fir=%d madd=%d returned=%d enables=%d size=%d tx=%08X rx=%08X",
            {counter_reads},
            restored and 1 or 0,
            dma & 0x0003,
            ((program:read_u32(0x10c00060) & 0x3ffc) >> 2) + 1,
            program:read_u32(0x10c00070),
            program:read_u32(0x10c0006c)))
        machine:exit()
    end
end)
"""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument(
        "--nvram-source",
        type=Path,
        required=True,
        help=(
            "provider-configured NVRAM directory to copy and test; the source "
            "is never modified"
        ),
    )
    parser.add_argument("--system", default="datarover840")
    return parser.parse_args(argv)


def run_mame(
    args: argparse.Namespace,
    lua_path: Path,
    nvram_dir: Path,
    log_path: Path,
    *,
    debug: bool,
    seconds: int,
) -> bytes:
    command = [
        str(args.mame),
        args.system,
        "-rompath",
        str(args.rompath),
        "-nvram_directory",
        str(nvram_dir),
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
        "-seconds_to_run",
        str(seconds),
    ]
    if debug:
        command.extend(["-debug", "-debugger", "none"])
    try:
        completed = subprocess.run(
            command,
            cwd=args.mame.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=180,
        )
        output = completed.stdout
    except (OSError, subprocess.TimeoutExpired) as error:
        output = f"unable to run MAME: {error}\n".encode()
    log_path.write_bytes(output)
    return output


def run_regression(args: argparse.Namespace) -> int:
    args.mame = args.mame.expanduser().resolve()
    args.rompath = args.rompath.expanduser().resolve()
    workdir = args.workdir.expanduser().resolve()
    if not args.mame.is_file():
        print(f"error: MAME executable not found: {args.mame}", file=sys.stderr)
        return 2
    if not args.rompath.is_dir():
        print(f"error: ROM path not found: {args.rompath}", file=sys.stderr)
        return 2
    if args.system != "datarover840":
        print(
            "error: ROM symbol addresses are specific to datarover840",
            file=sys.stderr,
        )
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = workdir / f"{stamp}-{os.getpid()}"
    run_dir.mkdir(parents=True)

    source = args.nvram_source.expanduser().resolve()
    if not (source / args.system / "ram").is_file():
        print(
            f"error: provider-configured NVRAM not found under {source}",
            file=sys.stderr,
        )
        return 2

    modem_dir = run_dir / "modem"
    nvram_dir = modem_dir / "nvram"
    modem_dir.mkdir()
    shutil.copytree(source, nvram_dir)
    lua_path = modem_dir / "builtin-modem.lua"
    lua_path.write_text(modem_script(), encoding="utf-8")
    log_path = modem_dir / "mame-output.txt"
    output = run_mame(
        args,
        lua_path,
        nvram_dir,
        log_path,
        debug=True,
        seconds=45,
    )

    match = RESULT_PATTERN.search(output)
    if match is None:
        print(f"FAIL: no modem result reported; see {log_path}", file=sys.stderr)
        return 1
    counter_count = len(SYMBOLS)
    values = tuple(
        int(value, 16 if index >= counter_count + 3 else 10)
        for index, value in enumerate(match.groups())
    )
    counters = values[:counter_count]
    returned, enables, size = values[counter_count : counter_count + 3]
    tx, rx = values[counter_count + 3 : counter_count + 5]
    missing = [
        name
        for (_, name), hit in zip(SYMBOLS, counters, strict=True)
        if not hit
    ]
    if missing:
        print(
            f"FAIL: ROM modem path missed {', '.join(missing)}; see {log_path}",
            file=sys.stderr,
        )
        return 1
    if not returned or enables != 3 or size != 48 or not tx or not rx:
        print(
            "FAIL: modem did not retain its 48-word continuous RX/TX ring "
            f"(returned={returned}, enables={enables}, size={size}, "
            f"tx={tx:#010x}, rx={rx:#010x}); see {log_path}",
            file=sys.stderr,
        )
        return 1

    print(
        "PASS: Magic Cap opened the built-in modem, kept its 48-word "
        "telecom ring running, selected V.32, and executed the ROM's "
        "V32ModulatorFIR through a TX39 MADD instruction"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
