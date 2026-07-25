#!/usr/bin/env python3
"""Exercise Dino's SIB telecom DMA, the built-in software modem's data path.

The telecom channel shares `sibDMA` and `sibSize` with sound: its fields sit in
the low half, and one pointer serves both directions. This harness programs a
transfer directly rather than waiting for the OS to dial, then checks what the
hardware model did:

  * transmit reads the buffer at `sibTelTxStart`;
  * receive writes the buffer at `sibTelRxStart`, and with `kSibLoopModeMask`
    set the SIB feeds transmit straight back, so the two buffers must match;
  * `kIntTelDmaHalfMask`, `kIntTelDmaEndMask` and `kIntTelDmaPtrIncMask` latch
    in `interrupt1`;
  * an explicit one-shot transfer clears its own enables and leaves the
    pointer wrapped;
  * the default mode used by Magic Cap's software modem remains enabled and
    continuously wraps the two-half buffer.

It runs with the machine in IDT monitor mode so Magic Cap is not driving the
SIB at the same time. See docs/betty-registers.md.
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
DEFAULT_WORKDIR = (
    Path.home() / "fun" / "magic-cap-assets" / "runtime" / "telecom-regression"
)

# Scratch DRAM well clear of the monitor's own workspace and the framebuffer.
TX_BUFFER = 0x0020_0000
RX_BUFFER = 0x0021_0000
WORDS = 64

RESULT_PATTERN = re.compile(
    rb"TELECOM RESULT match=(\d+)/(\d+) ptr=(\d+) enables=(\d) "
    rb"half=(\d) end=(\d) ptrinc=(\d)"
)


def monitor_config(system: str) -> str:
    """Return a config that powers on into the IDT monitor."""
    return f"""<?xml version="1.0"?>
<mameconfig version="10">
    <system name="{system}">
        <input>
            <port tag=":BOOT_MODE" type="CONFIG"
                  mask="8" defvalue="8" value="0" />
        </input>
    </system>
</mameconfig>
"""


def automation_script(
    words: int = WORDS,
    loopback: bool = True,
    continuous: bool = False,
) -> str:
    """Return Lua that programs one telecom transfer, optionally continuous."""
    loop_bit = "0x08" if loopback else "0x00"
    dma_control = "0x0003" if continuous else "0x8003"
    return f"""local machine = manager.machine
local program = machine.devices[":maincpu"].spaces["program"]
local frames = 0
local started = false

local TX, RX, WORDS = 0x{TX_BUFFER:08x}, 0x{RX_BUFFER:08x}, {words}
local SIB_SIZE, SIB_CONTROL, SIB_DMA = 0x10c00060, 0x10c00074, 0x10c00090
local TEL_RX_START, TEL_TX_START = 0x10c0006c, 0x10c00070
local INTERRUPT1 = 0x10c00100

emu.register_frame_done(function()
    frames = frames + 1

    if frames == 120 then
        -- A recognisable pattern, and a receive buffer that must be overwritten.
        for index = 0, WORDS - 1 do
            program:write_u32(TX + index * 4, 0x5a000000 + index)
            program:write_u32(RX + index * 4, 0xffffffff)
        end

        program:write_u32(TEL_TX_START, TX)
        program:write_u32(TEL_RX_START, RX)
        -- Telecom size field is bits 13:2 and holds the last valid index.
        program:write_u32(SIB_SIZE, ((WORDS - 1) * 4) & 0x3ffc)
        -- kSibEnableSib | kSibEnableTel [| kSibLoopModeMask], divisor 25.
        program:write_u32(SIB_CONTROL, 0x00190000 | {loop_bit} | 0x20 | 0x01)
        -- Both directions. kSibTelDmaOnceMask makes this an explicit
        -- one-shot; without it the software modem uses a continuous ring.
        program:write_u32(SIB_DMA, {dma_control})
        started = true
        print("TELECOM START")

    elseif started and frames > 120 then
        local dma = program:read_u32(SIB_DMA)
        local enables = dma & 0x0003
        if enables == 0 or frames == 240 then
            local status = program:read_u32(INTERRUPT1)
            local match = 0
            for index = 0, WORDS - 1 do
                if program:read_u32(RX + index * 4) == 0x5a000000 + index then
                    match = match + 1
                end
            end
            print(string.format(
                "TELECOM RESULT match=%d/%d ptr=%d enables=%d half=%d end=%d ptrinc=%d",
                match, WORDS, (dma & 0x3ffc) >> 2, enables,
                (status & 0x00100000) ~= 0 and 1 or 0,
                (status & 0x00080000) ~= 0 and 1 or 0,
                (status & 0x00020000) ~= 0 and 1 or 0))
            machine:exit()
        end
    end
end)
"""


def parse_result(output: bytes) -> dict[str, int] | None:
    match = RESULT_PATTERN.search(output)
    if not match:
        return None
    return {
        "match": int(match.group(1)),
        "words": int(match.group(2)),
        "ptr": int(match.group(3)),
        "enables": int(match.group(4)),
        "half": int(match.group(5)),
        "end": int(match.group(6)),
        "ptrinc": int(match.group(7)),
    }


def verify_no_loopback(result: dict[str, int]) -> tuple[bool, str]:
    """With kSibLoopModeMask clear, receive must not reproduce transmit.

    This is the control for the loopback check: it proves the loop-mode bit
    actually gates the data path rather than the test passing regardless.
    """
    if result["match"]:
        return False, (
            f"{result['match']} words arrived with loop mode disabled; "
            "receive DMA is not honouring kSibLoopModeMask"
        )
    if not result["end"]:
        return False, "kIntTelDmaEndMask never latched"
    return True, (
        "loop mode disabled: receive DMA wrote silence over the buffer and "
        "still completed the transfer"
    )


def verify(result: dict[str, int]) -> tuple[bool, str]:
    """Check the transfer against what Dino's documented behavior requires."""
    if result["match"] != result["words"]:
        return False, (
            f"loopback delivered {result['match']} of {result['words']} words; "
            "receive DMA did not reproduce the transmitted buffer"
        )
    if not result["half"]:
        return False, "kIntTelDmaHalfMask never latched"
    if not result["end"]:
        return False, "kIntTelDmaEndMask never latched"
    if not result["ptrinc"]:
        return False, "kIntTelDmaPtrIncMask never latched"
    if result["enables"]:
        return False, (
            f"one-shot transfer left enables set (sibDMA bits 1:0 = "
            f"{result['enables']})"
        )
    if result["ptr"]:
        return False, f"pointer stopped at {result['ptr']} instead of wrapping"
    return True, (
        f"telecom DMA looped back {result['words']} words, latched half/end/"
        "pointer interrupts, and cleared its one-shot enables"
    )


def verify_continuous(result: dict[str, int]) -> tuple[bool, str]:
    """Check the buffer mode used by Magic Cap's built-in software modem."""
    if result["match"] != result["words"]:
        return False, (
            f"loopback delivered {result['match']} of {result['words']} words; "
            "receive DMA did not reproduce the transmitted buffer"
        )
    if not result["half"] or not result["end"] or not result["ptrinc"]:
        return False, "continuous DMA did not latch all half/end/pointer events"
    if result["enables"] != 3:
        return False, (
            "continuous transfer stopped; expected sibDMA RX/TX enables 3, "
            f"got {result['enables']}"
        )
    return True, (
        f"continuous telecom DMA kept RX/TX enabled after wrapping its "
        f"{result['words']}-word buffer"
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--system", default="datarover840")
    parser.add_argument(
        "--no-loopback",
        action="store_true",
        help=(
            "control run: clear kSibLoopModeMask and require that receive DMA "
            "writes silence instead of the transmitted pattern"
        ),
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help=(
            "omit kSibTelDmaOnceMask and require RX/TX to remain enabled "
            "after the buffer wraps, as used by the built-in software modem"
        ),
    )
    args = parser.parse_args(argv)
    if args.no_loopback and args.continuous:
        parser.error("--no-loopback and --continuous are separate control runs")
    return args


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
    config_dir = run_dir / "cfg"
    nvram_dir = run_dir / "nvram"
    config_dir.mkdir(parents=True)
    nvram_dir.mkdir()
    lua_path = run_dir / "telecom-regression.lua"
    log_path = run_dir / "mame-output.txt"
    lua_path.write_text(
        automation_script(
            loopback=not args.no_loopback,
            continuous=args.continuous,
        ),
        encoding="utf-8",
    )
    (config_dir / f"{args.system}.cfg").write_text(
        monitor_config(args.system), encoding="utf-8"
    )

    command = [
        str(mame),
        args.system,
        "-rompath",
        str(rompath),
        "-cfg_directory",
        str(config_dir),
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
        "-nothrottle",
        "-skip_gameinfo",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=mame.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"error: unable to run telecom transfer: {error}", file=sys.stderr)
        return 2

    log_path.write_bytes(completed.stdout)
    result = parse_result(completed.stdout)
    if result is None:
        print(
            f"FAIL: no telecom result reported; see {log_path}", file=sys.stderr
        )
        return 1

    if args.no_loopback:
        passed, message = verify_no_loopback(result)
    elif args.continuous:
        passed, message = verify_continuous(result)
    else:
        passed, message = verify(result)
    if not passed:
        print(f"FAIL: {message}; see {log_path}", file=sys.stderr)
        return 1
    print(f"PASS: {message}")
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
