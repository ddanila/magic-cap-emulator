#!/usr/bin/env python3
"""Exercise the DataRover's 38,400-baud external-modem serial path."""

from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import subprocess
import sys
import time
import tty
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "uart-b-probe"
PTY_PATTERN = re.compile(rb":rs2322:pty PTY: (/[^\r\n]+)")
UART_TX_PATTERN = re.compile(rb"UART([AB]) TX:\s+([0-9a-fA-F]{2})\b")
READY_PATTERN = re.compile(rb"UARTB READY\b")
REPORT_PATTERN = re.compile(rb"UARTB REPORT\b")

PHASE_ONE = "fill -w b0c000cc/1 5\nfill -w b0c000c8/1 1\n"
PHASE_TWO = (
    "dump -w b0c000cc/1\n"
    "dump -w b0c000c8/1\n"
    "dump -w b0c000dc/1\n"
    "fill -w b0c000dc/1 54\n"
)

TERMINAL_KEYS = {
    "a": (2, 0x0002),
    "b": (3, 0x0040),
    "c": (3, 0x0010),
    "d": (2, 0x0008),
    "e": (1, 0x0008),
    "f": (2, 0x0010),
    "i": (1, 0x0100),
    "l": (2, 0x0200),
    "m": (3, 0x0100),
    "p": (1, 0x0400),
    "u": (1, 0x0080),
    "w": (1, 0x0004),
    " ": (3, 0x8000),
    "-": (0, 0x0800),
    "/": (3, 0x0800),
    "\n": (2, 0x1000),
    "0": (0, 0x0400),
    "1": (0, 0x0002),
    "4": (0, 0x0010),
    "5": (0, 0x0020),
    "8": (0, 0x0100),
}


def key_table(command: str) -> str:
    """Return Lua table entries for an IDT monitor command string."""
    return "\n".join(
        (
            '    { machine.ioport.ports['
            f'":terminal:keyboard:GENKBD_ROW{row}"]:field(0x{mask:04x}), '
            f"0x{mask:04x} }},"
        )
        for row, mask in (TERMINAL_KEYS[character] for character in command)
    )


def automation_script(host_ready: Path, frames: int) -> str:
    """Return Lua that drives the monitor after host-side UART input arrives."""
    return f"""local machine = manager.machine
local host_ready = {json.dumps(str(host_ready))}
local phase_one = {{
{key_table(PHASE_ONE)}
}}
local phase_two = {{
{key_table(PHASE_TWO)}
}}
local command = phase_one
local command_index = 1
local key_down = false
local waiting_for_host = false
local host_seen_frame = 0
local frames = 0

local function type_command()
    if command_index > #command then
        return true
    end

    local key = command[command_index]
    if key_down then
        key[1]:set_value(0)
        command_index = command_index + 1
        key_down = false
    else
        key[1]:set_value(key[2])
        key_down = true
    end
    return false
end

emu.register_frame_done(function()
    frames = frames + 1

    if frames >= 180 and not waiting_for_host then
        if type_command() then
            waiting_for_host = true
            print("UARTB READY")
        end
    elseif waiting_for_host and host_seen_frame == 0 then
        local ready = io.open(host_ready, "r")
        if ready then
            ready:close()
            host_seen_frame = frames
        end
    elseif host_seen_frame > 0 and frames == host_seen_frame + 30 then
        command = phase_two
        command_index = 1
        key_down = false
    elseif host_seen_frame > 0 and frames > host_seen_frame + 30
            and command_index <= #command then
        type_command()
    elseif host_seen_frame > 0 and command_index > #command
            and frames >= host_seen_frame + 30 + (#phase_two * 2) + 60 then
        print("UARTB REPORT")
        machine:exit()
    end

    if frames >= {frames} then
        print("UARTB REPORT timeout")
        machine:exit()
    end
end)
"""


def machine_config(system: str) -> str:
    """Select the IDT monitor and its terminal keyboard."""
    return f"""<?xml version="1.0"?>
<mameconfig version="10"><system name="{system}"><input>
<keyboard tag=":terminal:keyboard" enabled="1" />
<port tag=":BOOT_MODE" type="CONFIG" mask="8" defvalue="8" value="0" />
</input></system></mameconfig>
"""


def extract_uart_bytes(output: bytes, channel: str) -> bytes:
    """Extract bytes logged by one Dino UART."""
    return bytes(
        int(match.group(2), 16)
        for match in UART_TX_PATTERN.finditer(output)
        if match.group(1).decode("ascii") == channel
    )


def canonicalize_terminal(data: bytes) -> str:
    """Normalize the monitor's carriage returns and erase controls."""
    return (
        data.decode("latin-1")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\b", "")
    )


def monitor_dump(text: str, address: int) -> int | None:
    """Return a word printed by the monitor's dump command."""
    for line in text.upper().splitlines():
        match = re.match(
            r"\s*([0-9A-F]{8}):((?:\s+[0-9A-F]{8})+)",
            line,
        )
        if not match:
            continue
        base = int(match.group(1), 16)
        words = re.findall(r"[0-9A-F]{8}", match.group(2))
        offset = address - base
        if offset >= 0 and not (offset % 4) and (offset // 4) < len(words):
            return int(words[offset // 4], 16)
    return None


def acceptance_errors(
    output: bytes,
    host_received: bytes,
) -> list[str]:
    """Explain which part of the external-modem serial path did not pass."""
    errors = []
    terminal = canonicalize_terminal(extract_uart_bytes(output, "A"))
    divider = monitor_dump(terminal, 0xB0C000CC)
    control = monitor_dump(terminal, 0xB0C000C8)
    received = monitor_dump(terminal, 0xB0C000DC)

    if not READY_PATTERN.search(output):
        errors.append("monitor did not configure UART B")
    if divider != 5:
        errors.append(f"divider={divider!r} (need 5 for 38,400 baud)")
    if control != 0xD0000001:
        errors.append(
            f"control={control!r} (need enabled/empty/RX-full 0xd0000001)"
        )
    if received != 0x52:
        errors.append(f"received={received!r} (need host byte 0x52)")
    if b"T" not in host_received:
        errors.append(f"host received {host_received!r} (need guest byte b'T')")
    if not REPORT_PATTERN.search(output):
        errors.append("probe did not reach its final report")
    return errors


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--system", default="datarover840")
    parser.add_argument("--frames", type=int, default=1200)
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args(argv)
    if args.frames <= 0:
        parser.error("--frames must be positive")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def run_probe(args: argparse.Namespace) -> int:
    """Run MAME, exchange one byte in each direction, and check the monitor."""
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    if not mame.is_file():
        print(f"error: MAME executable not found: {mame}", file=sys.stderr)
        return 2
    if not rompath.is_dir():
        print(f"error: ROM path not found: {rompath}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    config_dir = run_dir / "cfg"
    nvram_dir = run_dir / "nvram"
    config_dir.mkdir(parents=True)
    nvram_dir.mkdir()
    host_ready = run_dir / "host-ready"
    script = run_dir / "uart-b.lua"
    (config_dir / f"{args.system}.cfg").write_text(
        machine_config(args.system), encoding="utf-8"
    )
    script.write_text(
        automation_script(host_ready, args.frames), encoding="utf-8"
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
        "-rs2322",
        "pty",
        "-video",
        "none",
        "-sound",
        "none",
        "-videodriver",
        "dummy",
        "-audiodriver",
        "dummy",
        "-nothrottle",
        "-oslog",
        "-autoboot_delay",
        "0",
        "-autoboot_script",
        str(script),
    ]

    process = subprocess.Popen(
        command,
        cwd=mame.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output = bytearray()
    host_received = bytearray()
    pty_fd: int | None = None
    sent = False
    deadline = time.monotonic() + args.timeout

    try:
        while time.monotonic() < deadline:
            for key, _mask in selector.select(0.05):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)

            if pty_fd is None:
                match = PTY_PATTERN.search(output)
                if match:
                    pty_fd = os.open(
                        os.fsdecode(match.group(1)),
                        os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK,
                    )
                    tty.setraw(pty_fd)

            if (
                not sent
                and pty_fd is not None
                and READY_PATTERN.search(output)
            ):
                os.write(pty_fd, b"R")
                host_ready.touch()
                sent = True

            if pty_fd is not None:
                try:
                    host_received.extend(os.read(pty_fd, 4096))
                except BlockingIOError:
                    pass

            if process.poll() is not None:
                while True:
                    chunk = process.stdout.read(65536)
                    if not chunk:
                        break
                    output.extend(chunk)
                break
        else:
            process.terminate()
            process.wait(timeout=5)
            print("error: UART B probe timed out", file=sys.stderr)
            return 2
    finally:
        selector.close()
        if pty_fd is not None:
            os.close(pty_fd)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

    (run_dir / "mame-output.txt").write_bytes(output)
    (run_dir / "host-received.bin").write_bytes(host_received)
    terminal = canonicalize_terminal(extract_uart_bytes(output, "A"))
    (run_dir / "monitor.txt").write_text(terminal, encoding="utf-8")

    if process.returncode:
        print(
            f"error: MAME exited with status {process.returncode}; see {run_dir}",
            file=sys.stderr,
        )
        return 2

    errors = acceptance_errors(bytes(output), bytes(host_received))
    if errors:
        print("FAIL: " + "; ".join(errors), file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 1

    print(
        "PASS: UART B exchanged host 0x52 and guest 0x54 at 38,400 baud"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_probe(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
