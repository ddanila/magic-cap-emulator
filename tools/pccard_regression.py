#!/usr/bin/env python3
"""Verify PC Card hardware and a live Magic Cap insertion."""

from __future__ import annotations

import argparse
import hashlib
import json
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
DEFAULT_CARD = (
    Path.home()
    / "fun"
    / "magic-cap-assets"
    / "roms"
    / "DataRover840FRomFlasher"
)
DEFAULT_WORKDIR = (
    Path.home()
    / "fun"
    / "magic-cap-assets"
    / "runtime"
    / "pccard-regression"
)
EXPECTED_CARD_SHA256 = (
    "16fe122872e295ee03be4be1322013a6e504997d9996997c8c7b0997ec65c5f7"
)
EXPECTED_CARD_SIZE = 8 * 1024 * 1024
WRITE_TEST = 0x13579BDF
CHECKPOINT_PATTERN = re.compile(
    rb"PCCARD_CHECKPOINT COMMON=([0-9A-F]{8}) "
    rb"ATTR=([0-9A-F]{2}),([0-9A-F]{2}),([0-9A-F]{2}) "
    rb"GLACIER=([0-9A-F]{4}) WRITE=([0-9A-F]{8})"
)
OS_CHECKPOINT_PATTERN = re.compile(
    rb"PCCARD_OS_CHECKPOINT STATE=([0-9A-F]{4}) "
    rb"WORKBENCH=([0-9A-F]{8}) NONZERO=(\d+)"
)
EXPECTED_WORKBENCH = 0x9DAB458B
EXPECTED_MIN_NONZERO = 6_000


def automation_script(card_path: Path | str = "flasher.card") -> str:
    """Return the Lua boot, insertion, and hardware probe sequence."""
    encoded_card_path = json.dumps(str(card_path))
    return f"""local machine = manager.machine
local program = machine.devices[":maincpu"].spaces["program"]
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

local function workbench_checkpoint()
    local framebuffer = program:read_u32(0x10c00030) & 0xfffffff0
    local workbench = 0
    local nonzero = 0
    for offset = 0, 38396, 4 do
        local word = program:read_u32(framebuffer + offset)
        if offset >= 27600 then
            workbench = (workbench + word) & 0xffffffff
        end
        if word ~= 0 then
            nonzero = nonzero + 1
        end
    end
    return workbench, nonzero
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
    elseif frames == 2220 then
        local loaded = false
        for tag, image in pairs(machine.images) do
            if image.brief_instance_name == "card" then
                local error = image:load({encoded_card_path})
                if error ~= nil then
                    print("PCCARD_LOAD_ERROR " .. tostring(error))
                else
                    loaded = true
                end
            end
        end
        if not loaded then
            print("PCCARD_LOAD_ERROR no card image device")
            machine:exit()
            return
        end

        program:write_u32(0x247ffffc, 0x{WRITE_TEST:08x})
        print(string.format(
            "PCCARD_CHECKPOINT COMMON=%08X ATTR=%02X,%02X,%02X GLACIER=%04X WRITE=%08X",
            program:read_u32(0x24000000),
            program:read_u8(0x08000000),
            program:read_u8(0x08000002),
            program:read_u8(0x08000004),
            program:read_u16(0x1040000c),
            program:read_u32(0x247ffffc)))
    elseif frames == 2400 then
        local workbench, nonzero = workbench_checkpoint()
        -- cardSlotState is the low halfword of the aligned word at e7e0.
        local state = program:read_u32(0x0000e7e0) & 0xffff
        print(string.format(
            "PCCARD_OS_CHECKPOINT STATE=%04X WORKBENCH=%08X NONZERO=%d",
            state, workbench, nonzero))
        machine.screens[":screen"]:snapshot("magic-cap-card-inserted.png")
    elseif frames == 2420 then
        machine:exit()
    end
end)
"""


def parse_checkpoint(
    output: bytes,
) -> tuple[int, tuple[int, int, int], int, int] | None:
    """Extract common memory, CIS bytes, Glacier inputs, and write readback."""
    match = CHECKPOINT_PATTERN.search(output)
    if not match:
        return None
    return (
        int(match.group(1), 16),
        tuple(int(match.group(index), 16) for index in range(2, 5)),
        int(match.group(5), 16),
        int(match.group(6), 16),
    )


def parse_os_checkpoint(output: bytes) -> tuple[int, int, int] | None:
    """Extract Magic Cap's slot state and post-insertion framebuffer."""
    match = OS_CHECKPOINT_PATTERN.search(output)
    if not match:
        return None
    return (
        int(match.group(1), 16),
        int(match.group(2), 16),
        int(match.group(3)),
    )


def sha256(path: Path) -> str:
    """Return the hexadecimal SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
        "--card",
        type=Path,
        default=DEFAULT_CARD,
        help=f"verified 840F flasher image (default: {DEFAULT_CARD})",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=DEFAULT_WORKDIR,
        help=f"persistent artifact directory (default: {DEFAULT_WORKDIR})",
    )
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    source_card = args.card.expanduser().resolve()
    workdir = args.workdir.expanduser().resolve()

    if not mame.is_file():
        print(f"error: MAME executable not found: {mame}", file=sys.stderr)
        return 2
    if not rompath.is_dir():
        print(f"error: ROM path not found: {rompath}", file=sys.stderr)
        return 2
    if not source_card.is_file():
        print(f"error: flasher-card image not found: {source_card}", file=sys.stderr)
        return 2
    if source_card.stat().st_size != EXPECTED_CARD_SIZE:
        print(
            f"error: card is {source_card.stat().st_size} bytes; "
            f"expected {EXPECTED_CARD_SIZE}",
            file=sys.stderr,
        )
        return 2
    actual_digest = sha256(source_card)
    if actual_digest != EXPECTED_CARD_SHA256:
        print(
            f"error: card SHA-256 is {actual_digest}; "
            f"expected {EXPECTED_CARD_SHA256}",
            file=sys.stderr,
        )
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = workdir / f"{stamp}-{os.getpid()}"
    nvram_dir = run_dir / "nvram"
    snapshot_dir = run_dir / "snapshots"
    nvram_dir.mkdir(parents=True)
    snapshot_dir.mkdir()
    card_copy = run_dir / "flasher.card"
    shutil.copyfile(source_card, card_copy)
    lua_path = run_dir / "pccard-regression.lua"
    lua_path.write_text(automation_script(card_copy), encoding="utf-8")

    command = [
        str(mame),
        "datarover840",
        "-rompath",
        str(rompath),
        "-nvram_directory",
        str(nvram_dir),
        "-snapshot_directory",
        str(snapshot_dir),
        "-snapview",
        "native",
        "-pccard1",
        "linear",
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
    except OSError as error:
        print(f"error: unable to run MAME: {error}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print(f"error: MAME timed out; artifacts: {run_dir}", file=sys.stderr)
        return 2

    log_path = run_dir / "mame-output.txt"
    log_path.write_bytes(completed.stdout)
    if completed.returncode:
        print(
            f"error: MAME exited with status {completed.returncode}; "
            f"see {log_path}",
            file=sys.stderr,
        )
        return 2

    actual = parse_checkpoint(completed.stdout)
    # The copied image is writable, so Glacier's active-high write-protect
    # input (bit 3) remains clear. Detect, voltage-sense, ready, and BVD2
    # account for the remaining 0x0306 status.
    expected = (0x426F7773, (0x01, 0x03, 0x61), 0x0306, WRITE_TEST)
    if actual != expected:
        print(
            f"FAIL: PC Card checkpoint {actual!r}, expected {expected!r}; "
            f"see {log_path}",
            file=sys.stderr,
        )
        return 1

    actual_os = parse_os_checkpoint(completed.stdout)
    if (
        actual_os is None
        or actual_os[0] != 0x0001
        or actual_os[1] != EXPECTED_WORKBENCH
        or actual_os[2] < EXPECTED_MIN_NONZERO
    ):
        print(
            f"FAIL: Magic Cap insertion checkpoint {actual_os!r}, expected "
            f"slot state 0x0001, workbench signature "
            f"{EXPECTED_WORKBENCH:#010x}, and at least "
            f"{EXPECTED_MIN_NONZERO} nonzero words; see {log_path}",
            file=sys.stderr,
        )
        return 1

    if card_copy.read_bytes()[-4:] != WRITE_TEST.to_bytes(4, "big"):
        print(
            f"FAIL: write did not persist to disposable image: {card_copy}",
            file=sys.stderr,
        )
        return 1
    if sha256(source_card) != EXPECTED_CARD_SHA256:
        print(
            f"FAIL: source flasher image was modified: {source_card}",
            file=sys.stderr,
        )
        return 1

    snapshot_path = snapshot_dir / "magic-cap-card-inserted.png"
    if not snapshot_path.is_file():
        print(f"FAIL: post-insertion snapshot was not written: {snapshot_path}", file=sys.stderr)
        return 1

    print(
        "PASS: card spaces, CIS, pins, writable media, and live Magic Cap "
        "insertion match"
    )
    print(f"Disposable card: {card_copy}")
    print(f"Snapshot: {snapshot_path}")
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    return run_regression(args)


if __name__ == "__main__":
    raise SystemExit(main())
