#!/usr/bin/env python3
"""Verify PC Card modem UART and receive-queue save-state fidelity."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import select
import subprocess
import sys
import termios
import time
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "modem-save-regression"
PTY_PATTERN = re.compile(rb":pccard1:modem PTY: (/[^\r\n]+)")
RESULT_PATTERN = re.compile(
    rb"MODEM_SAVE CONFIG=([0-9A-F]{2}) IER=([0-9A-F]{2}) "
    rb"LCR=([0-9A-F]{2}) MCR=([0-9A-F]{2}) DIV=([0-9A-F]{4}) "
    rb"SCR=([0-9A-F]{2}) IIR=([0-9A-F]{2}),([0-9A-F]{2}),"
    rb"([0-9A-F]{2}) RX=([0-9A-F]{8}) "
    rb"GLACIER=([0-9A-F]{4}),([0-9A-F]{4}) CD_EDGES=([0-9A-F]{4})"
)
EXPECTED_RESULT = (
    0x41,
    0x03,
    0x03,
    0x0B,
    0x1234,
    0x5A,
    0xC4,
    0xC2,
    0xC1,
    0x42434400,
    0x0302,
    0x0306,
    0x0000,
)


def monitor_config() -> str:
    """Use the quiet IDT monitor boot path for a hardware-level probe."""
    return """<?xml version="1.0"?>
<mameconfig version="10">
    <system name="datarover840">
        <input>
            <port tag=":BOOT_MODE" type="CONFIG"
                  mask="8" defvalue="8" value="0" />
        </input>
    </system>
</mameconfig>
"""


def automation_script(state_path: Path | str = "modem-uart.sta") -> str:
    """Create, corrupt, reload, and report a nontrivial modem UART state."""
    quoted_state = str(state_path).replace("\\", "\\\\").replace('"', '\\"')
    return f"""local machine = manager.machine
local program = machine.devices[":maincpu"].spaces["program"]
local frames = 0
local stage = "init"
local deadline = 600
local uart = 0x080003f8
local config_option = 0x08000200
local glacier = 0x10400000

local function write_uart(reg, value)
    program:write_u8(uart + reg, value)
end

local function read_uart(reg)
    return program:read_u8(uart + reg)
end

local function configure_saved_state()
    program:write_u8(config_option, 0x41)
    write_uart(3, 0x80)
    write_uart(0, 0x34)
    write_uart(1, 0x12)
    write_uart(3, 0x03)
    write_uart(4, 0x0b)
    write_uart(7, 0x5a)
    write_uart(2, 0x01)
    write_uart(1, 0x03)
    write_uart(0, 0x51)
end

local function corrupt_saved_state()
    program:write_u8(config_option, 0x00)
    write_uart(2, 0x03)
    write_uart(3, 0x80)
    write_uart(0, 0x00)
    write_uart(1, 0x00)
    write_uart(3, 0x00)
    write_uart(4, 0x00)
    write_uart(7, 0x00)
    write_uart(1, 0x02)
    read_uart(2)
    write_uart(1, 0x00)
    write_uart(2, 0x00)
end

local function report_restored_state()
    local config = program:read_u8(config_option)
    local ier = read_uart(1)
    local lcr = read_uart(3)
    local mcr = read_uart(4)
    local scratch = read_uart(7)
    write_uart(3, lcr | 0x80)
    local divisor = read_uart(0) | (read_uart(1) << 8)
    write_uart(3, lcr)

    local glacier_pending = (program:read_u32(glacier + 0x0c) >> 16) & 0xffff
    local iir_rx = read_uart(2)
    local rx = (read_uart(0) << 24) | (read_uart(0) << 16)
        | (read_uart(0) << 8) | read_uart(0)
    local iir_tx = read_uart(2)
    local iir_none = read_uart(2)
    local glacier_ready = (program:read_u32(glacier + 0x0c) >> 16) & 0xffff
    local positive = (program:read_u32(glacier + 0x18) >> 16) & 0x0c00
    local negative = (program:read_u32(glacier + 0x1c) >> 16) & 0x0c00

    print(string.format(
        "MODEM_SAVE CONFIG=%02X IER=%02X LCR=%02X MCR=%02X DIV=%04X " ..
        "SCR=%02X IIR=%02X,%02X,%02X RX=%08X GLACIER=%04X,%04X " ..
        "CD_EDGES=%04X",
        config, ier, lcr, mcr, divisor, scratch, iir_rx, iir_tx, iir_none,
        rx, glacier_pending, glacier_ready, positive | negative))
    machine:exit()
end

emu.register_frame_done(function()
    frames = frames + 1
    if stage == "init" and frames >= 30 then
        configure_saved_state()
        stage = "wait-rx"
    elseif stage == "wait-rx" then
        if (read_uart(5) & 0x01) ~= 0 then
            local first = read_uart(0)
            if first ~= 0x41 then
                print(string.format("MODEM_SAVE_ERROR FIRST=%02X", first))
                machine:exit()
                return
            end
            -- Ignore insertion history. A restore must not add CD edges.
            program:write_u32(glacier + 0x18, 0xffffffff)
            program:write_u32(glacier + 0x1c, 0xffffffff)
            machine:save("{quoted_state}")
            stage = "saved"
            deadline = frames + 90
        elseif frames >= deadline then
            print("MODEM_SAVE_ERROR RX_TIMEOUT")
            machine:exit()
        end
    elseif stage == "saved" and frames >= deadline then
        corrupt_saved_state()
        machine:load("{quoted_state}")
        stage = "loaded"
        deadline = frames + 90
    elseif stage == "loaded" and frames >= deadline then
        report_restored_state()
    end
end)
"""


def parse_result(output: bytes) -> tuple[int, ...] | None:
    """Return all restored register/queue/Glacier values."""
    match = RESULT_PATTERN.search(output)
    if not match:
        return None
    return tuple(int(value, 16) for value in match.groups())


def configure_raw_pty(fd: int) -> None:
    """Put the modem PTY slave into unprocessed eight-bit mode."""
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] = 0
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def set_nonblocking(fd: int) -> None:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    if not mame.is_file() or not rompath.is_dir():
        print("error: MAME executable or ROM directory is missing", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    config_dir = run_dir / "cfg"
    config_dir.mkdir(parents=True)
    (config_dir / "datarover840.cfg").write_text(
        monitor_config(), encoding="utf-8"
    )
    state_path = run_dir / "modem-uart.sta"
    script_path = run_dir / "modem-save.lua"
    script_path.write_text(automation_script(state_path), encoding="utf-8")
    log_path = run_dir / "mame-output.txt"
    command = [
        str(mame),
        "datarover840",
        "-rompath",
        str(rompath),
        "-pccard1",
        "modem",
        "-cfg_directory",
        str(config_dir),
        "-nvram_directory",
        str(run_dir / "nvram"),
        "-autoboot_delay",
        "0",
        "-autoboot_script",
        str(script_path),
        "-video",
        "none",
        "-sound",
        "none",
        "-videodriver",
        "dummy",
        "-audiodriver",
        "dummy",
        "-skip_gameinfo",
    ]

    process: subprocess.Popen[bytes] | None = None
    pty_fd: int | None = None
    output = bytearray()
    try:
        process = subprocess.Popen(
            command,
            cwd=mame.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert process.stdout is not None
        set_nonblocking(process.stdout.fileno())
        deadline = time.monotonic() + 30
        supplied = False
        while process.poll() is None and time.monotonic() < deadline:
            readable, _, _ = select.select([process.stdout], [], [], 0.05)
            if readable:
                try:
                    chunk = os.read(process.stdout.fileno(), 65_536)
                except BlockingIOError:
                    chunk = b""
                output.extend(chunk)
                if not supplied:
                    match = PTY_PATTERN.search(output)
                    if match:
                        pty_fd = os.open(
                            match.group(1).decode("ascii"),
                            os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK,
                        )
                        configure_raw_pty(pty_fd)
                        os.write(pty_fd, b"ABCD")
                        supplied = True
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
            print(f"error: MAME timed out; artifacts: {run_dir}", file=sys.stderr)
            return 2
        while True:
            try:
                chunk = os.read(process.stdout.fileno(), 65_536)
            except BlockingIOError:
                break
            if not chunk:
                break
            output.extend(chunk)
    finally:
        if pty_fd is not None:
            os.close(pty_fd)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        log_path.write_bytes(output)

    if process is None or process.returncode:
        print(
            f"error: MAME exited with status "
            f"{None if process is None else process.returncode}; "
            f"artifacts: {run_dir}",
            file=sys.stderr,
        )
        return 2
    result = parse_result(bytes(output))
    if result != EXPECTED_RESULT:
        print(
            f"FAIL: modem restore result {result!r}, expected "
            f"{EXPECTED_RESULT!r}; artifacts: {run_dir}",
            file=sys.stderr,
        )
        return 1
    if not state_path.is_file():
        print(f"FAIL: save state is missing; artifacts: {run_dir}", file=sys.stderr)
        return 1

    print(
        "PASS: PC Card modem registers, partially consumed RX queue, "
        "interrupts, and continuous card presence survive save/load"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
