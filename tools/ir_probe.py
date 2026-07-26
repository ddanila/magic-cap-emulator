#!/usr/bin/env python3
"""Measure how far Magic Cap's IrDA stack gets on the emulated machine.

Beaming does not use Dino's "IR module" at `0x0a0`-`0x0a8`. That block is
consumer-IR carrier timing plus the GPIO that holds Betty in reset
(`Gen2MFS.h`'s `AssertBettyResetSignal`). IrDA data instead rides a Dino UART
in **pulsed mode** - `kUartPulseLow6CLockMask`, bit 8 of `uartA/B.control1` -
which is IrDA SIR's 3/16 pulse encoding. `SerialServerDino_PulsedMode` reads
that bit from whichever port the serial server owns, and the OS layers its own
IrLAP implementation above it.

This counts entries into the stack so the boundary is measurable:

    irdaInit -> irlapInit -> IRDaemonActor_Main / InitializeBeam
    irlapOpen / BeamDiscover        (only once the user beams something)

On a plain boot the stack initialises but never opens the link, because
beaming is user-initiated. Pass `--require-link` only when another harness or
debugger action is driving the Beam UI; `tools/beam_regression.py` is the
end-to-end acceptance check for the implemented peer transport.

The addresses are from the release build; the development ROM shifts them, so
this refuses to run against anything else. See docs/irda.md.
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
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = Path.home() / "fun" / "magic-cap-assets" / "roms"
DEFAULT_WORKDIR = Path.home() / "fun" / "magic-cap-assets" / "runtime" / "ir-probe"

SUPPORTED_SYSTEMS = ("datarover840", "datarover840f")
WATCHED = (
    ("irda_init", 0x13C597C4, "irdaInit"),
    ("irlap_init", 0x13C58658, "irlapInit"),
    ("irlap_open", 0x13C5846C, "irlapOpen"),
    ("daemon_main", 0x13C483FC, "IRDaemonActor_Main"),
    ("beam_init", 0x13C4824C, "IRDaemonActor_InitializeBeam"),
    ("pulsed_mode", 0x13C540AC, "SerialServerDino_PulsedMode"),
    ("beam_discover", 0x13C49CE8, "BeamDiscover"),
    ("daemon_active", 0x13C481B4, "IRDaemonActor_Active"),
)
# The stack has to reach these on a plain boot; the rest wait for the user.
BOOT_EXPECTED = ("irda_init", "irlap_init", "daemon_main", "beam_init")
LINK_EXPECTED = ("irlap_open",)

SCRATCH = 0x0030_0000
UART_A_CONTROL1 = 0x10C000B0
UART_B_CONTROL1 = 0x10C000C8
PULSED_MODE_BIT = 0x0000_0100  # kUartPulseLow6CLockMask

COUNTS = re.compile(rb"IR COUNTS ([^\n]+?) uartA=([0-9A-F]{8}) uartB=([0-9A-F]{8})")


def automation_script(frames: int) -> str:
    setup = "\n".join(
        f'    watch({SCRATCH + index * 4}, 0x{address:08x})'
        for index, (_name, address, _symbol) in enumerate(WATCHED)
    )
    report = " .. ".join(
        f'string.format("{name}=%d ", program:read_u32({SCRATCH + index * 4}))'
        for index, (name, _address, _symbol) in enumerate(WATCHED)
    )
    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames = 0

local function watch(slot, address)
    program:write_u32(slot, 0)
    -- One command only: two chained `do`s halt the machine instead of
    -- continuing, which looks exactly like a hang.
    cpu.debug:bpset(address, "1",
        string.format("do d@0x%08x=d@0x%08x+1; g", slot, slot))
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 60 then
{setup}
    elseif frames == {frames} then
        print(string.format("IR COUNTS %s uartA=%08X uartB=%08X",
            {report}, program:read_u32(0x{UART_A_CONTROL1:08x}),
            program:read_u32(0x{UART_B_CONTROL1:08x})))
        machine:exit()
    end
end)
"""


def parse_counts(output: bytes) -> dict[str, int]:
    match = COUNTS.search(output)
    if not match:
        return {}
    counts = {
        key: int(value)
        for key, value in re.findall(r"(\w+)=(\d+)", match.group(1).decode())
    }
    counts["uart_a"] = int(match.group(2), 16)
    counts["uart_b"] = int(match.group(3), 16)
    return counts


def boot_errors(counts: dict[str, int]) -> list[str]:
    """Report which parts of the boot-time bring-up are missing."""
    return [name for name in BOOT_EXPECTED if not counts.get(name)]


def link_errors(counts: dict[str, int]) -> list[str]:
    """Report what is missing for an actually opened IrDA link."""
    missing = [name for name in LINK_EXPECTED if not counts.get(name)]
    if not any(
        counts.get(port, 0) & PULSED_MODE_BIT for port in ("uart_a", "uart_b")
    ):
        missing.append("no UART in pulsed mode")
    return missing


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--system", default="datarover840")
    parser.add_argument("--frames", type=int, default=9000)
    parser.add_argument(
        "--require-link",
        action="store_true",
        help=(
            "demand an opened IrLAP link and a UART in pulsed mode; this is "
            "the acceptance check for routing IR traffic to a peer"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    mame = args.mame.expanduser().resolve()
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
    lua_path = run_dir / "ir.lua"
    lua_path.write_text(automation_script(args.frames), encoding="utf-8")

    completed = subprocess.run(
        [
            str(mame), args.system,
            "-rompath", str(args.rompath.expanduser().resolve()),
            "-cfg_directory", str(run_dir / "cfg"),
            "-nvram_directory", str(run_dir / "nvram"),
            "-autoboot_delay", "0",
            "-autoboot_script", str(lua_path),
            "-debug", "-debugger", "none",
            "-video", "none", "-sound", "none",
            "-videodriver", "dummy", "-audiodriver", "dummy",
            "-nothrottle", "-skip_gameinfo",
        ],
        cwd=mame.parent,
        capture_output=True,
        timeout=1200,
    )
    output = completed.stdout + completed.stderr
    (run_dir / "mame-output.txt").write_bytes(output)

    counts = parse_counts(output)
    if not counts:
        print(f"error: no counts reported; see {run_dir}", file=sys.stderr)
        return 2

    width = max(len(name) for name, _address, _symbol in WATCHED)
    for name, _address, symbol in WATCHED:
        print(f"  {name:<{width}}  {counts.get(name, 0):4d}  {symbol}")
    for port in ("uart_a", "uart_b"):
        pulsed = "pulsed" if counts[port] & PULSED_MODE_BIT else "wired"
        print(f"  {port:<{width}}  {counts[port]:#010x}  {pulsed}")
    print(f"Artifacts: {run_dir}")

    missing = boot_errors(counts)
    if missing:
        print(
            "FAIL: the IrDA stack did not come up: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    if args.require_link:
        unmet = link_errors(counts)
        if unmet:
            print(
                "FAIL: no IrDA link was opened: " + ", ".join(unmet),
                file=sys.stderr,
            )
            return 1
        print("PASS: IrDA link opened over a pulsed-mode UART")
        return 0

    print("PASS: the IrDA stack initialises; the link waits for the user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
