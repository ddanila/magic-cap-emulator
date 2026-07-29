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
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "telecom-regression"

# Scratch DRAM well clear of the monitor's own workspace and the framebuffer.
TX_BUFFER = 0x0020_0000
RX_BUFFER = 0x0021_0000
WORDS = 64

RESULT_PATTERN = re.compile(
    rb"TELECOM RESULT match=(\d+)/(\d+) ptr=(\d+) enables=(\d) "
    rb"half=(\d) end=(\d) ptrinc=(\d)"
)
TONE_PATTERN = re.compile(
    rb"TELECOM TONE samples=(\d+) min=(-?\d+) max=(-?\d+) "
    rb"hz350=(\d+) hz440=(\d+) hz1000=(\d+)"
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
    dial_tone: bool = False,
) -> str:
    """Return Lua that programs one telecom transfer, optionally continuous."""
    loop_bit = "0x08" if loopback else "0x00"
    dma_control = "0x0003" if continuous else "0x8003"
    divisor = "0x27" if dial_tone else "0x19"
    offhook = (
        "        program:write_u32(SIB_SF0_AUX, 0x04000200)\n"
        if dial_tone
        else ""
    )
    tone_report = (
        r"""
            local samples = {}
            local minimum, maximum = 32767, -32768
            for index = 0, WORDS - 1 do
                local word = program:read_u32(RX + index * 4)
                local first, second = (word >> 16) & 0xffff, word & 0xffff
                if first >= 0x8000 then first = first - 0x10000 end
                if second >= 0x8000 then second = second - 0x10000 end
                table.insert(samples, first)
                table.insert(samples, second)
                minimum = math.min(minimum, first, second)
                maximum = math.max(maximum, first, second)
            end
            local function amplitude(frequency)
                local real, imag = 0.0, 0.0
                for index, sample in ipairs(samples) do
                    local angle =
                        2.0 * math.pi * frequency * (index - 1) / 7200.0
                    real = real + sample * math.cos(angle)
                    imag = imag - sample * math.sin(angle)
                end
                return math.floor(
                    2.0 * math.sqrt(real * real + imag * imag)
                    / #samples + 0.5)
            end
            print(string.format(
                "TELECOM TONE samples=%d min=%d max=%d hz350=%d hz440=%d hz1000=%d",
                #samples, minimum, maximum, amplitude(350),
                amplitude(440), amplitude(1000)))
"""
        if dial_tone
        else ""
    )
    return f"""local machine = manager.machine
local program = machine.devices[":maincpu"].spaces["program"]
local frames = 0
local started = false

local TX, RX, WORDS = 0x{TX_BUFFER:08x}, 0x{RX_BUFFER:08x}, {words}
local SIB_SIZE, SIB_CONTROL, SIB_DMA = 0x10c00060, 0x10c00074, 0x10c00090
local TEL_RX_START, TEL_TX_START = 0x10c0006c, 0x10c00070
local INTERRUPT1 = 0x10c00100
local SIB_SF0_AUX = 0x10c00080

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
{offhook}        -- Telecom size field is bits 13:2 and holds the last valid index.
        program:write_u32(SIB_SIZE, ((WORDS - 1) * 4) & 0x3ffc)
        -- kSibEnableSib | kSibEnableTel [| kSibLoopModeMask].
        program:write_u32(
            SIB_CONTROL, ({divisor} << 16) | {loop_bit} | 0x20 | 0x01)
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
{tone_report}            machine:exit()
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


def parse_tone_result(output: bytes) -> dict[str, int] | None:
    match = TONE_PATTERN.search(output)
    if not match:
        return None
    names = ("samples", "min", "max", "hz350", "hz440", "hz1000")
    return {
        name: int(value)
        for name, value in zip(names, match.groups(), strict=True)
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


def verify_dial_tone(
    result: dict[str, int], tone: dict[str, int] | None
) -> tuple[bool, str]:
    passed, message = verify_no_loopback(result)
    if not passed:
        return passed, message
    if tone is None:
        return False, "dial-tone sample report is missing"
    if tone["samples"] < 2000:
        return False, f"only {tone['samples']} dial-tone samples were captured"
    if tone["min"] > -6000 or tone["max"] < 6000:
        return False, (
            f"dial tone has insufficient range {tone['min']}..{tone['max']}"
        )
    for frequency in ("hz350", "hz440"):
        if not 3500 <= tone[frequency] <= 4500:
            return False, (
                f"{frequency[2:]} Hz component has amplitude "
                f"{tone[frequency]}, expected approximately 4000"
            )
    if tone["hz1000"] >= 200:
        return False, (
            f"off-band 1000 Hz amplitude {tone['hz1000']} is unexpectedly high"
        )
    return True, (
        "off-hook receive DMA captured the deterministic 350+440 Hz "
        "telephone dial tone"
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
    parser.add_argument(
        "--dial-tone",
        action="store_true",
        help=(
            "take Betty off-hook with loop mode clear and require the "
            "automatic telephone exchange's 350+440 Hz dial tone"
        ),
    )
    args = parser.parse_args(argv)
    if sum((args.no_loopback, args.continuous, args.dial_tone)) > 1:
        parser.error(
            "--no-loopback, --continuous and --dial-tone are separate runs"
        )
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
            words=1024 if args.dial_tone else WORDS,
            loopback=not (args.no_loopback or args.dial_tone),
            continuous=args.continuous,
            dial_tone=args.dial_tone,
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
        "-videodriver",
        "dummy",
        "-audiodriver",
        "dummy",
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

    if args.dial_tone:
        passed, message = verify_dial_tone(
            result, parse_tone_result(completed.stdout)
        )
    elif args.no_loopback:
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
