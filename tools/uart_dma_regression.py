#!/usr/bin/env python3
"""Verify both Dino UART DMA channels, interrupts, and host transport."""

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "uart-dma-regression"
PTY_PATTERN = re.compile(rb":rs232([12]):pty PTY: (/[^\r\n]+)")
READY_PATTERN = re.compile(rb"UART_DMA READY ([12])")
GO_PATTERN = re.compile(rb"UART_DMA GO ([12])")
RESULT_PATTERN = re.compile(
    rb"UART_DMA PHASE=([12]) "
    rb"A_CTL=([0-9A-F]{8}) A_COUNT=([0-9A-F]{8}) "
    rb"B_CTL=([0-9A-F]{8}) B_COUNT=([0-9A-F]{8}) "
    rb"IRQ=([0-9A-F]{8}) RX=([0-9A-F]{8})"
)


@dataclass(frozen=True)
class DmaResult:
    """One bidirectional DMA phase reported by the MAME script."""

    phase: int
    a_control: int
    a_count: int
    b_control: int
    b_count: int
    interrupt: int
    received: int


def automation_script(markers: tuple[Path, Path, Path, Path]) -> str:
    """Return Lua that swaps TX/RX roles between the two Dino UARTs."""
    ready1, sent1, ready2, sent2 = (
        json.dumps(str(path)) for path in markers
    )
    return rf"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local DINO = 0x10c00000
local UART_A = 0x0b0
local UART_B = 0x0c8
local MASTER_CLOCK = 0x1c0
local INTERRUPT2 = 0x104
local DMA_RX = 0x00008001
local DMA_TX = 0x00004001
local ready1 = {ready1}
local sent1 = {sent1}
local ready2 = {ready2}
local sent2 = {sent2}
local frames = 0
local phase = 0
local host_frame = 0

local function marker_exists(path)
    local file = io.open(path, "r")
    if file then
        file:close()
        return true
    end
    return false
end

local function write_bytes(address, text)
    for index = 1, #text do
        program:write_u8(address + index - 1, string.byte(text, index))
    end
end

local function read_word(address)
    return (program:read_u8(address) << 24)
        | (program:read_u8(address + 1) << 16)
        | (program:read_u8(address + 2) << 8)
        | program:read_u8(address + 3)
end

local function park_cpu()
    program:write_u32(0x00001000, 0x1000ffff) -- b .
    program:write_u32(0x00001004, 0x00000000) -- nop
    cpu.state["SR"].value = 0
    cpu.state["PC"].value = 0xa0001000
end

local function clear_uart()
    program:write_u32(DINO + UART_A, 0x00000001)
    program:write_u32(DINO + UART_B, 0x00000001)
    -- Match the DataRover's two external connector defaults.
    program:write_u32(DINO + UART_A + 0x04, 11) -- 19,200 baud
    program:write_u32(DINO + UART_B + 0x04, 5)  -- 38,400 baud
    program:write_u32(DINO + INTERRUPT2, 0xffffffff)
end

local function start_phase1()
    clear_uart()
    write_bytes(0x00003000, "ATX!")
    write_bytes(0x00003200, "\x00\x00\x00\x00")
    program:write_u32(DINO + UART_A + 0x08, 0x00003000)
    program:write_u32(DINO + UART_A + 0x0c, 3)
    program:write_u32(DINO + UART_B + 0x08, 0x00003200)
    program:write_u32(DINO + UART_B + 0x0c, 3)
    phase = 1
    print("UART_DMA READY 1")
end

local function start_phase2()
    clear_uart()
    write_bytes(0x00003400, "BTX?")
    write_bytes(0x00003600, "\x00\x00\x00\x00")
    program:write_u32(DINO + UART_A + 0x08, 0x00003600)
    program:write_u32(DINO + UART_A + 0x0c, 3)
    program:write_u32(DINO + UART_B + 0x08, 0x00003400)
    program:write_u32(DINO + UART_B + 0x0c, 3)
    phase = 2
    host_frame = 0
    print("UART_DMA READY 2")
end

local function report(base)
    print(string.format(
        "UART_DMA PHASE=%d A_CTL=%08X A_COUNT=%08X "
            .. "B_CTL=%08X B_COUNT=%08X IRQ=%08X RX=%08X",
        phase,
        program:read_u32(DINO + UART_A),
        program:read_u32(DINO + UART_A + 0x10),
        program:read_u32(DINO + UART_B),
        program:read_u32(DINO + UART_B + 0x10),
        program:read_u32(DINO + INTERRUPT2),
        read_word(base)))
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 10 then
        park_cpu()
        program:write_u32(DINO + MASTER_CLOCK, 0x00000003)
        start_phase1()
    elseif phase == 1 and host_frame == 0 and marker_exists(ready1) then
        program:write_u32(DINO + UART_A, DMA_TX)
        program:write_u32(DINO + UART_B, DMA_RX)
        host_frame = -1
        print("UART_DMA GO 1")
    elseif phase == 1 and host_frame == -1 and marker_exists(sent1) then
        host_frame = frames
    elseif phase == 1 and host_frame > 0 and frames >= host_frame + 30 then
        report(0x00003200)
        start_phase2()
    elseif phase == 2 and host_frame == 0 and marker_exists(ready2) then
        program:write_u32(DINO + UART_A, DMA_RX)
        program:write_u32(DINO + UART_B, DMA_TX)
        host_frame = -1
        print("UART_DMA GO 2")
    elseif phase == 2 and host_frame == -1 and marker_exists(sent2) then
        host_frame = frames
    elseif phase == 2 and host_frame > 0 and frames >= host_frame + 30 then
        report(0x00003600)
        machine:exit()
    elseif frames >= 900 then
        print("UART_DMA timeout")
        machine:exit()
    end
end)
"""


def parse_results(output: bytes) -> dict[int, DmaResult]:
    """Parse the two hardware reports from MAME output."""
    results = {}
    for match in RESULT_PATTERN.finditer(output):
        values = [int(value, 16) for value in match.groups()]
        phase = int(match.group(1))
        results[phase] = DmaResult(phase, *values[1:])
    return results


def verify_results(
    results: dict[int, DmaResult],
    host_received: dict[int, bytes],
) -> list[str]:
    """Return precise failures for register, memory, or serial behavior."""
    failures = []
    expected_rx = {1: 0x42525823, 2: 0x41525824}  # "BRX#", "ARX$"
    expected_tx = {1: b"ATX!", 2: b"BTX?"}
    for phase in (1, 2):
        result = results.get(phase)
        if result is None:
            failures.append(f"phase {phase} report is missing")
            continue
        if result.a_control != 0xC0000001:
            failures.append(
                f"phase {phase} UART A control={result.a_control:#010x}"
            )
        if result.b_control != 0xC0000001:
            failures.append(
                f"phase {phase} UART B control={result.b_control:#010x}"
            )
        if result.a_count != 3 or result.b_count != 3:
            failures.append(
                f"phase {phase} counts={result.a_count}/{result.b_count}"
            )
        if (result.interrupt & 0x00C03000) != 0x00C03000:
            failures.append(
                f"phase {phase} DMA interrupt bits={result.interrupt:#010x}"
            )
        if result.received != expected_rx[phase]:
            failures.append(
                f"phase {phase} RX word={result.received:#010x}"
            )
        if expected_tx[phase] not in host_received.get(phase, b""):
            failures.append(
                f"phase {phase} host bytes={host_received.get(phase, b'')!r}"
            )
    return failures


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def _drain(fd: int) -> None:
    while True:
        try:
            if not os.read(fd, 4096):
                return
        except BlockingIOError:
            return


def run_regression(args: argparse.Namespace) -> int:
    """Run both DMA directions through two host PTYs and validate them."""
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
    nvram_dir = run_dir / "nvram"
    nvram_dir.mkdir(parents=True)
    markers = (
        run_dir / "phase1-host-ready",
        run_dir / "phase1-host-sent",
        run_dir / "phase2-host-ready",
        run_dir / "phase2-host-sent",
    )
    script = run_dir / "uart-dma.lua"
    script.write_text(automation_script(markers), encoding="utf-8")

    command = [
        str(mame),
        "datarover840",
        "-rompath",
        str(rompath),
        "-nvram_directory",
        str(nvram_dir),
        "-rs2321",
        "pty",
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
    pty_fds: dict[int, int] = {}
    armed: set[int] = set()
    sent: set[int] = set()
    host_received = {1: bytearray(), 2: bytearray()}
    deadline = time.monotonic() + args.timeout

    try:
        while time.monotonic() < deadline:
            for key, _mask in selector.select(0.02):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if chunk:
                    output.extend(chunk)

            for match in PTY_PATTERN.finditer(output):
                channel = int(match.group(1))
                if channel not in pty_fds:
                    fd = os.open(
                        os.fsdecode(match.group(2)),
                        os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK,
                    )
                    tty.setraw(fd)
                    pty_fds[channel] = fd

            ready = {
                int(match.group(1))
                for match in READY_PATTERN.finditer(output)
            }
            go = {
                int(match.group(1))
                for match in GO_PATTERN.finditer(output)
            }
            if 1 in ready and 1 not in armed and len(pty_fds) == 2:
                _drain(pty_fds[1])
                _drain(pty_fds[2])
                markers[0].touch()
                armed.add(1)
            if 1 in go and 1 not in sent:
                os.write(pty_fds[2], b"BRX#")
                markers[1].touch()
                sent.add(1)
            if 2 in ready and 2 not in armed and len(pty_fds) == 2:
                _drain(pty_fds[1])
                _drain(pty_fds[2])
                markers[2].touch()
                armed.add(2)
            if 2 in go and 2 not in sent:
                os.write(pty_fds[1], b"ARX$")
                markers[3].touch()
                sent.add(2)

            active = 2 if 2 in armed else 1 if 1 in armed else 0
            if active:
                tx_channel = 2 if active == 2 else 1
                try:
                    host_received[active].extend(
                        os.read(pty_fds[tx_channel], 4096)
                    )
                except BlockingIOError:
                    pass

            if process.poll() is not None:
                output.extend(process.stdout.read())
                break
        else:
            process.terminate()
            process.wait(timeout=5)
            print(f"error: UART DMA regression timed out; see {run_dir}", file=sys.stderr)
            return 2
    finally:
        selector.close()
        for fd in pty_fds.values():
            os.close(fd)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

    (run_dir / "mame-output.txt").write_bytes(output)
    for phase, data in host_received.items():
        (run_dir / f"phase{phase}-host-received.bin").write_bytes(data)
    if process.returncode:
        print(
            f"error: MAME exited with status {process.returncode}; see {run_dir}",
            file=sys.stderr,
        )
        return 2

    failures = verify_results(
        parse_results(bytes(output)),
        {phase: bytes(data) for phase, data in host_received.items()},
    )
    if failures:
        print(f"FAIL: {'; '.join(failures)}", file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 1

    print(
        "PASS: both Dino UARTs transferred four-byte TX/RX DMA buffers, "
        "reported current index 3, and raised half/end interrupts"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
