#!/usr/bin/env python3
"""Send a fax through Magic Cap's visible product workflow."""

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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "fax-origin-regression"
COUNTERS = 0x0031_3000
SYMBOLS = (
    (0x13C5_A938, "connect_number"),
    (0x13C5_B7E4, "init_fax"),
    (0x13E4_26C4, "command_handler"),
    (0x13E4_2588, "line_handler"),
    (0x13C2_3B3C, "telecom_start"),
)
DTMF_PATTERN = re.compile(rb"Telephone exchange DTMF: ([0-9A-D*#])")
RESULT_PATTERN = re.compile(
    rb"FAX_ORIGIN_RESULT "
    + rb" ".join(name.encode() + rb"=(\d+)" for _, name in SYMBOLS)
    + rb" telecom_words=(\d+) telecom_enables=(\d+)"
)


def automation_script(result_frame: int = 5800) -> str:
    """Create a fax recipient, address the Desk screen, and send it."""
    addresses = ",\n    ".join(
        f"0x{address:08x}" for address, _ in SYMBOLS
    )
    reads = ",\n            ".join(
        f"program:read_u32(COUNTERS + {index * 4})"
        for index in range(len(SYMBOLS))
    )
    fields = " ".join(f"{name}=%d" for _, name in SYMBOLS)
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

local function release()
    touch_button:set_value(0)
end

emu.register_frame_done(function()
    frames = frames + 1
    -- Desk, Magic lamp, Fax, and the recipient chooser.
    if frames == 1200 then press(34, 302)
    elseif frames == 1220 then release()
    elseif frames == 1500 then press(181, 301)
    elseif frames == 1520 then release()
    elseif frames == 1800 then press(205, 146)
    elseif frames == 1820 then release()
    elseif frames == 2200 then press(157, 157)
    elseif frames == 2220 then release()
    elseif frames == 2500 then press(345, 177)
    elseif frames == 2520 then release()

    -- Create Fax Peer with the default (650) prefix and 555-1212.
    elseif frames == 2850 then emu.keypost("5551212")
    elseif frames == 3150 then press(421, 143)
    elseif frames == 3170 then release()
    elseif frames == 3450 then emu.keypost("Fax")
    elseif frames == 3700 then press(370, 102)
    elseif frames == 3720 then release()
    elseif frames == 3800 then emu.keypost("Peer")
    elseif frames == 4150 then press(428, 143)
    elseif frames == 4170 then release()
    elseif frames == 4500 then press(115, 130)
    elseif frames == 4520 then release()
    elseif frames == 4800 then press(347, 242)
    elseif frames == 4820 then release()
    elseif frames == 5100 then
        machine.screens[":screen"]:snapshot("fax-addressed.png")

    -- Send the currently selected Desk screen.
    elseif frames == 5150 then press(326, 210)
    elseif frames == 5170 then release()
    elseif frames == 5500 then
        machine.screens[":screen"]:snapshot("sending-fax.png")
    elseif frames == {result_frame} then
        local size = program:read_u32(0x10c00060)
        local dma = program:read_u32(0x10c00090)
        print(string.format(
            "FAX_ORIGIN_RESULT {fields} "
            .. "telecom_words=%d telecom_enables=%d",
            {reads},
            ((size & 0x00003ffc) >> 2) + 1,
            dma & 3))
        machine:exit()
    end
end)
"""


def deterministic_machine_config() -> str:
    return """<?xml version="1.0"?>
<mameconfig version="10">
    <system name="datarover840">
        <input>
            <port tag=":PHONE_PEER" type="CONFIG"
                  mask="3" defvalue="1" value="1" />
            <port tag=":MAGICBUS_ACCESSORY" type="CONFIG"
                  mask="1" defvalue="1" value="1" />
        </input>
    </system>
</mameconfig>
"""


def parse_result(output: bytes) -> dict[str, int] | None:
    match = RESULT_PATTERN.search(output)
    if match is None:
        return None
    names = (
        *(name for _, name in SYMBOLS),
        "telecom_words",
        "telecom_enables",
    )
    return {
        name: int(value)
        for name, value in zip(names, match.groups(), strict=True)
    }


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
        deterministic_machine_config(), encoding="utf-8"
    )
    lua_path = run_dir / "fax-origin.lua"
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
        print(
            f"error: MAME failed: {error}; artifacts: {run_dir}",
            file=sys.stderr,
        )
        return 2
    output_path.write_bytes(completed.stdout)

    result = parse_result(completed.stdout)
    digits = b"".join(DTMF_PATTERN.findall(completed.stdout)).decode("ascii")
    addressed = snapshot_dir / "fax-addressed.png"
    sending = snapshot_dir / "sending-fax.png"
    ui_changed = (
        addressed.is_file()
        and sending.is_file()
        and addressed.read_bytes() != sending.read_bytes()
    )
    missing = (
        list(name for _, name in SYMBOLS)
        if result is None
        else [name for _, name in SYMBOLS if result[name] == 0]
    )
    dma_ok = (
        result is not None
        and result["telecom_words"] == 48
        and result["telecom_enables"] == 3
    )
    if (
        completed.returncode
        or missing
        or digits != "5551212"
        or not dma_ok
        or not ui_changed
    ):
        print(
            "FAIL: fax-origin path incomplete "
            f"(result={result!r}, missing={missing}, digits={digits!r}, "
            f"dma_ok={dma_ok}, ui_changed={ui_changed}); "
            f"see {output_path}",
            file=sys.stderr,
        )
        return 1

    print(
        "PASS: the visible Fax workflow created and selected a recipient, "
        "dialed 555-1212, initialized fax mode, and started telecom DMA"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
