#!/usr/bin/env python3
"""Dial a number through Magic Cap's normal Telephone application.

The test starts from an already calibrated DataRover NVRAM image, opens the
Desk and Telephone, enters 580 on the visible keypad, and presses dial.  ROM
breakpoint counters prove that the product path reaches PhoneDialer,
PhoneServer, the software-modem DTMF generator and scaler,
SpeakerPhoneAudio, the DAA hookswitch, and Dino telecom DMA.  The automatic
exchange must decode the visible number, proving that product-generated audio
crosses the emulated telephone boundary.
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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "telephone-ui-regression"

COUNTERS = 0x0031_0000
SYMBOLS = (
    (0x13C3_FE1C, "dialer"),
    (0x13C4_02E8, "start_call"),
    (0x13C4_08AC, "server_dial"),
    (0x13C4_3080, "audio_dialing"),
    (0x13C4_3A60, "start_monitor"),
    (0x13C4_38FC, "phone_half"),
    (0x13C4_39A8, "phone_full"),
    (0x13C2_4DA4, "daa_offhook"),
    (0x13C2_2AE4, "sib_offhook"),
    (0x13C2_3B3C, "telecom_start"),
    (0x13C5_A090, "softmodem_dial"),
    (0x13E6_4F80, "dialer_init"),
    (0x13E6_4E64, "call_progress"),
    (0x13E6_14F0, "block_scale"),
)

DTMF_PATTERN = re.compile(rb"Telephone exchange DTMF: ([0-9A-D*#])")
RESULT_PATTERN = re.compile(
    rb"TELEPHONE_UI_RESULT "
    rb"dialer=(\d+) start_call=(\d+) server_dial=(\d+) "
    rb"audio_dialing=(\d+) start_monitor=(\d+) "
    rb"phone_half=(\d+) phone_full=(\d+) daa_offhook=(\d+) "
    rb"sib_offhook=(\d+) telecom_start=(\d+) "
    rb"softmodem_dial=(\d+) dialer_init=(\d+) "
    rb"call_progress=(\d+) block_scale=(\d+) "
    rb"sound_size=(\d+) telecom_size=(\d+) "
    rb"sound_enables=(\d+) telecom_enables=(\d+) "
    rb"sound_tx=([0-9A-F]{8}) telecom_tx=([0-9A-F]{8}) "
    rb"telecom_rx=([0-9A-F]{8})"
)


def automation_script(result_frame: int = 3400) -> str:
    """Return Lua for the visible Telephone keypad and ROM trace."""
    addresses = ",\n    ".join(
        f"0x{address:08x}" for address, _ in SYMBOLS
    )
    counter_reads = ",\n            ".join(
        f"program:read_u32(COUNTERS + {index * 4})"
        for index in range(len(SYMBOLS))
    )
    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local frames = 0
local COUNTERS = 0x{COUNTERS:08x}
local addresses = {{
    {addresses}
}}

for index, address in ipairs(addresses) do
    local counter = COUNTERS + (index - 1) * 4
    program:write_u32(counter, 0)
    cpu.debug:bpset(
        address,
        "",
        string.format(
            "do d@0x%08x=d@0x%08x+1; g", counter, counter))
end
cpu.debug:go()

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

emu.register_frame_done(function()
    frames = frames + 1
    -- Open Desk, then the tabletop Telephone.
    if frames == 1200 then press(34, 302)
    elseif frames == 1220 then touch_button:set_value(0)
    elseif frames == 1650 then press(55, 175)
    elseif frames == 1670 then touch_button:set_value(0)

    -- Enter 580 on the visible keypad.
    elseif frames == 2150 then press(97, 120)
    elseif frames == 2170 then touch_button:set_value(0)
    elseif frames == 2300 then press(97, 181)
    elseif frames == 2320 then touch_button:set_value(0)
    elseif frames == 2450 then press(97, 244)
    elseif frames == 2470 then touch_button:set_value(0)
    elseif frames == 2700 then
        machine.screens[":screen"]:snapshot("number.png")

    -- Press Dial and retain the resulting call screen.
    elseif frames == 2800 then press(240, 92)
    elseif frames == 2820 then touch_button:set_value(0)
    elseif frames == {result_frame} then
        machine.screens[":screen"]:snapshot("calling.png")
        local size = program:read_u32(0x10c00060)
        local dma = program:read_u32(0x10c00090)
        print(string.format(
            "TELEPHONE_UI_RESULT "
            .. "dialer=%d start_call=%d server_dial=%d "
            .. "audio_dialing=%d start_monitor=%d "
            .. "phone_half=%d phone_full=%d daa_offhook=%d "
            .. "sib_offhook=%d telecom_start=%d "
            .. "softmodem_dial=%d dialer_init=%d "
            .. "call_progress=%d block_scale=%d "
            .. "sound_size=%d telecom_size=%d "
            .. "sound_enables=%d telecom_enables=%d "
            .. "sound_tx=%08X telecom_tx=%08X telecom_rx=%08X",
            {counter_reads},
            ((size & 0x3ffc0000) >> 18) + 1,
            ((size & 0x00003ffc) >> 2) + 1,
            (dma >> 16) & 3,
            dma & 3,
            program:read_u32(0x10c00068),
            program:read_u32(0x10c00070),
            program:read_u32(0x10c0006c)))
        machine:exit()
    end
end)
"""


def deterministic_machine_config() -> str:
    """Pin the automatic exchange and the known-good keyboard accessory."""
    return """<?xml version="1.0"?>
<mameconfig version="10">
    <system name="datarover840">
        <input>
            <port tag=":PHONE_PEER" type="CONFIG"
                  mask="1" defvalue="1" value="1" />
            <port tag=":MAGICBUS_ACCESSORY" type="CONFIG"
                  mask="1" defvalue="1" value="1" />
        </input>
    </system>
</mameconfig>
"""


def parse_result(output: bytes) -> dict[str, int] | None:
    """Parse the final decimal counters and hexadecimal DMA addresses."""
    match = RESULT_PATTERN.search(output)
    if match is None:
        return None
    names = (
        *(name for _, name in SYMBOLS),
        "sound_size",
        "telecom_size",
        "sound_enables",
        "telecom_enables",
        "sound_tx",
        "telecom_tx",
        "telecom_rx",
    )
    values = {
        name: int(value, 16 if name.endswith(("tx", "rx")) else 10)
        for name, value in zip(names, match.groups(), strict=True)
    }
    return values


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
            "calibrated datarover840 NVRAM directory to copy and test; "
            "the source is never modified"
        ),
    )
    parser.add_argument("--system", default="datarover840")
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    source = args.nvram_source.expanduser().resolve()
    if not mame.is_file():
        print(f"error: MAME executable not found: {mame}", file=sys.stderr)
        return 2
    if not rompath.is_dir():
        print(f"error: ROM path not found: {rompath}", file=sys.stderr)
        return 2
    if args.system != "datarover840":
        print(
            "error: ROM symbol addresses are specific to datarover840",
            file=sys.stderr,
        )
        return 2
    if not (source / args.system / "ram").is_file():
        print(
            f"error: calibrated NVRAM not found under {source}",
            file=sys.stderr,
        )
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = (
        args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    )
    run_dir.mkdir(parents=True)
    cfg_dir = run_dir / "cfg"
    nvram_dir = run_dir / "nvram"
    snapshot_dir = run_dir / "snapshots"
    cfg_dir.mkdir()
    snapshot_dir.mkdir()
    shutil.copytree(source, nvram_dir)
    (cfg_dir / f"{args.system}.cfg").write_text(
        deterministic_machine_config(),
        encoding="utf-8",
    )
    lua_path = run_dir / "telephone-ui.lua"
    lua_path.write_text(automation_script(), encoding="utf-8")
    output_path = run_dir / "mame-output.txt"

    command = [
        str(mame),
        args.system,
        "-rompath",
        str(rompath),
        "-cfg_directory",
        str(cfg_dir),
        "-nvram_directory",
        str(nvram_dir),
        "-snapshot_directory",
        str(snapshot_dir),
        "-snapview",
        "native",
        "-autoboot_delay",
        "0",
        "-autoboot_script",
        str(lua_path),
        "-debug",
        "-debugger",
        "none",
        "-oslog",
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
            timeout=240,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"error: MAME failed: {error}; artifacts: {run_dir}", file=sys.stderr)
        return 2
    output_path.write_bytes(completed.stdout)
    result = parse_result(completed.stdout)
    digits = DTMF_PATTERN.findall(completed.stdout)
    missing = (
        []
        if result is None
        else [name for _, name in SYMBOLS if result[name] == 0]
    )
    number = snapshot_dir / "number.png"
    calling = snapshot_dir / "calling.png"
    hardware_ok = (
        result is not None
        and result["sound_size"] == 48
        and result["telecom_size"] == 48
        and result["sound_enables"] == 3
        and result["telecom_enables"] == 3
        and result["sound_tx"] != 0
        and result["telecom_tx"] != 0
        and result["telecom_rx"] != 0
    )
    ui_ok = (
        number.is_file()
        and calling.is_file()
        and number.read_bytes() != calling.read_bytes()
    )
    exchange_ok = digits == [b"5", b"8", b"0"]
    if (
        completed.returncode
        or missing
        or not hardware_ok
        or not ui_ok
        or not exchange_ok
    ):
        print(
            "FAIL: Telephone UI/actor/audio/DMA checkpoint incomplete "
            f"(result={result!r}, missing={missing}, ui={ui_ok}, "
            f"exchange_digits={digits!r}); "
            f"see {output_path}",
            file=sys.stderr,
        )
        return 1

    print(
        "PASS: Magic Cap entered 580 in the Telephone, showed the active "
        "call screen, traversed PhoneDialer, PhoneServer, and the software "
        "DTMF generator, went off-hook, kept both 48-word sound and telecom "
        "DMA rings running, and the exchange decoded 580"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
