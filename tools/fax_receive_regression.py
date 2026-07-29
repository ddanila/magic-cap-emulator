#!/usr/bin/env python3
"""Verify Magic Cap's product-level built-in fax answering path."""

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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "fax-receive-regression"
COUNTERS = 0x0031_2000

# Shipping DataRover 840 ROM entry points, recovered against the SDK ELF.
SYMBOLS = (
    (0x13E8_F3EC, "receive_now"),
    (0x13C5_ADD4, "answer_modem"),
    (0x13E4_26C4, "command_handler"),
    (0x13C2_3B3C, "telecom_start"),
    (0x13E4_2588, "line_handler"),
    (0x13E5_2230, "fax_modem_init"),
    (0x13E5_258C, "fax_modem_receive"),
    (0x13E5_25B4, "fax_modem_transmit"),
    (0x13C5_BD44, "receive_hdlc"),
    (0x13C5_BE38, "send_hdlc"),
)
RESULT_PATTERN = re.compile(
    rb"FAX_RECEIVE_RESULT "
    + rb" ".join(name.encode() + rb"=(\d+)" for _, name in SYMBOLS)
    + rb" telecom_words=(\d+) telecom_enables=(\d+)"
)


def automation_script(result_frame: int = 2600) -> str:
    """Ring, press receive fax, and trace the ROM and fax DSP path."""
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
local ring = ports[":PHONE_RING"]:field(0x01)
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
    if frames == 1100 then
        machine.screens[":screen"]:snapshot("before-ring.png")
    elseif frames == 1200 then
        ring:set_value(1)
    elseif frames == 1320 then
        ring:set_value(0)
    elseif frames == 1450 then
        machine.screens[":screen"]:snapshot("incoming-call.png")
    elseif frames == 1500 then
        press(220, 156)
    elseif frames == 1520 then
        touch_button:set_value(0)
    elseif frames == 1700 then
        machine.screens[":screen"]:snapshot("receiving-fax.png")
    elseif frames == {result_frame} then
        local size = program:read_u32(0x10c00060)
        local dma = program:read_u32(0x10c00090)
        print(string.format(
            "FAX_RECEIVE_RESULT {fields} "
            .. "telecom_words=%d telecom_enables=%d",
            {reads},
            ((size & 0x00003ffc) >> 2) + 1,
            dma & 3))
        machine:exit()
    end
end)
"""


def deterministic_machine_config() -> str:
    """Select the external PCM boundary while leaving its input silent."""
    return """<?xml version="1.0"?>
<mameconfig version="10">
    <system name="datarover840">
        <input>
            <port tag=":PHONE_LINE" type="CONFIG"
                  mask="1" defvalue="1" value="1" />
            <port tag=":PHONE_PEER" type="CONFIG"
                  mask="3" defvalue="1" value="2" />
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
    lua_path = run_dir / "fax-receive.lua"
    lua_path.write_text(automation_script(), encoding="utf-8")
    output_path = run_dir / "mame-output.txt"
    pcm_path = run_dir / "fax-line.pcm"

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
        "-bitb",
        str(pcm_path),
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
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(
            f"error: MAME failed: {error}; artifacts: {run_dir}",
            file=sys.stderr,
        )
        return 2
    output_path.write_bytes(completed.stdout)

    result = parse_result(completed.stdout)
    incoming = snapshot_dir / "incoming-call.png"
    receiving = snapshot_dir / "receiving-fax.png"
    ui_changed = (
        incoming.is_file()
        and receiving.is_file()
        and incoming.read_bytes() != receiving.read_bytes()
    )
    pcm = pcm_path.read_bytes() if pcm_path.is_file() else b""
    pcm_nonzero = any(pcm)
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
        or not dma_ok
        or not ui_changed
        or not pcm_nonzero
    ):
        print(
            "FAIL: fax-answer path incomplete "
            f"(result={result!r}, missing={missing}, dma_ok={dma_ok}, "
            f"ui_changed={ui_changed}, pcm_bytes={len(pcm)}, "
            f"pcm_nonzero={pcm_nonzero}); see {output_path}",
            file=sys.stderr,
        )
        return 1

    print(
        "PASS: receive fax used the live incoming call, entered AnswerModem, "
        "ran the fax modem receive/transmit path, and emitted line PCM"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
