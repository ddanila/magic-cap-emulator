#!/usr/bin/env python3
"""Install a Magic Cap package through the emulated PCLink serial cable."""

from __future__ import annotations

import argparse
import os
import re
import select
import struct
import subprocess
import sys
import termios
import time
import zlib
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = Path.home() / "fun" / "magic-cap-assets" / "roms"
DEFAULT_PACKAGE = (
    Path.home()
    / "fun"
    / "magic-cap-assets"
    / "packages"
    / "DvorakKeyboard.pkg"
)
DEFAULT_WORKDIR = (
    Path.home()
    / "fun"
    / "magic-cap-assets"
    / "runtime"
    / "pclink-regression"
)
PTY_PATTERN = re.compile(rb"PTY: (/[^\r\n]+)")
ESCAPE_BYTES = frozenset((0x0E, 0x0F, 0x10))
PC_LINK_MAGIC = b"ChMa"
CONNECT_TAG = b"Cnct"
CONNECTED_TAG = b"Cntd"
SEND_PACKAGE_TAG = b"SPkg"
GOODBYE_TAG = b"GBye"


class ProtocolError(ValueError):
    """The peer supplied a malformed PCLink CRC stream."""


def escape_payload(data: bytes) -> bytes:
    """Escape PCLink-reserved payload bytes."""
    result = bytearray()
    for value in data:
        if value in ESCAPE_BYTES:
            result.append(0x10)
        result.append(value)
    return bytes(result)


def unescape_payload(data: bytes) -> bytes:
    """Undo PCLink payload escaping, including pairs split across frames."""
    result = bytearray()
    escaped = False
    for value in data:
        if escaped:
            if value not in ESCAPE_BYTES:
                raise ProtocolError(
                    f"invalid byte 0x{value:02x} after PCLink escape"
                )
            result.append(value)
            escaped = False
        elif value == 0x10:
            escaped = True
        elif value in (0x0E, 0x0F):
            raise ProtocolError(f"unescaped PCLink byte 0x{value:02x}")
        else:
            result.append(value)
    if escaped:
        raise ProtocolError("truncated PCLink escape")
    return bytes(result)


def encode_crc_stream(data: bytes) -> bytes:
    """Encode data as the PCLink escaped, 256-byte, CRC-framed stream."""
    encoded = escape_payload(data)
    result = bytearray()
    for start in range(0, len(encoded), 256):
        frame = encoded[start : start + 256]
        crc = (~zlib.crc32(frame)) & 0xFFFFFFFF
        result.extend(struct.pack(">H", len(frame)))
        result.extend(frame)
        # Length and CRC are part of the lower framing layer and stay raw.
        result.extend(struct.pack(">I", crc))
    return bytes(result)


def decode_crc_stream(wire: bytes) -> bytes:
    """Validate and decode a complete PCLink CRC stream."""
    position = 0
    encoded = bytearray()
    while position < len(wire):
        if len(wire) - position < 2:
            raise ProtocolError("truncated PCLink frame length")
        size = int.from_bytes(wire[position : position + 2], "big")
        position += 2
        if not 1 <= size <= 256:
            raise ProtocolError(f"invalid PCLink frame length {size}")
        end = position + size
        if len(wire) - end < 4:
            raise ProtocolError("truncated PCLink frame")
        frame = wire[position:end]
        expected = int.from_bytes(wire[end : end + 4], "big")
        actual = (~zlib.crc32(frame)) & 0xFFFFFFFF
        if actual != expected:
            raise ProtocolError(
                f"PCLink CRC mismatch: expected {expected:08x}, got {actual:08x}"
            )
        encoded.extend(frame)
        position = end + 4
    return unescape_payload(bytes(encoded))


def encode_packet(tag: bytes, payload: bytes = b"") -> bytes:
    """Encode a four-character PCLink command packet."""
    if len(tag) != 4:
        raise ValueError("PCLink packet tags must contain four bytes")
    return encode_crc_stream(tag + struct.pack(">I", len(payload)) + payload)


def decode_packet(stream: bytes) -> tuple[bytes, bytes]:
    """Decode one PCLink command from an already decoded CRC stream."""
    if len(stream) < 8:
        raise ProtocolError("truncated PCLink packet header")
    size = int.from_bytes(stream[4:8], "big")
    if len(stream) != size + 8:
        raise ProtocolError(
            f"PCLink packet declares {size} payload bytes, "
            f"but contains {len(stream) - 8}"
        )
    return stream[:4], stream[8:]


def package_metadata(path: Path) -> bytes:
    """Build the 0x404-byte SPkg metadata block used by WinPcLink."""
    size = path.stat().st_size
    name_text = path.name
    name = name_text.encode("utf-16-be")
    if len(name) > (0x404 - 32):
        raise ValueError(f"package filename is too long: {name_text}")

    metadata = bytearray(0x404)
    struct.pack_into(">II", metadata, 0, size, size)
    struct.pack_into(">I", metadata, 24, 0x80000000)
    # WinPcLink records the source character count, not the UTF-16 byte count.
    struct.pack_into(">I", metadata, 28, len(name_text))
    metadata[32 : 32 + len(name)] = name
    return bytes(metadata)


def lua_navigation(snapshot_frame: int, exit_frame: int) -> str:
    """Return deterministic first-boot calibration and PCLink navigation."""
    return f"""local machine = manager.machine
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

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 1220 then press(240, 160)
    elseif frames == 1240 then touch_button:set_value(0)
    elseif frames == 1420 then press(23, 23)
    elseif frames == 1440 then touch_button:set_value(0)
    elseif frames == 1620 then press(456, 296)
    elseif frames == 1640 then touch_button:set_value(0)
    elseif frames == 1820 then press(240, 160)
    elseif frames == 1840 then touch_button:set_value(0)
    elseif frames == 2200 then press(420, 70)
    elseif frames == 2220 then touch_button:set_value(0)
    elseif frames == 2260 then press(396, 24)
    elseif frames == 2280 then touch_button:set_value(0)
    elseif frames == 2320 then press(440, 10)
    elseif frames == 2340 then touch_button:set_value(0)
    elseif frames == 2400 then press(452, 255)
    elseif frames == 2420 then touch_button:set_value(0)
    elseif frames == 2500 then press(60, 130)
    elseif frames == 2520 then touch_button:set_value(0)
    elseif frames == 2630 then press(48, 155)
    elseif frames == 2650 then touch_button:set_value(0)
    elseif frames == {snapshot_frame} then
        machine.screens[":screen"]:snapshot("package-installed.png")
    elseif frames == {exit_frame} then machine:exit()
    end
end)
"""


def read_available(fd: int) -> bytes:
    result = bytearray()
    while select.select([fd], [], [], 0)[0]:
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            break
        if not chunk:
            break
        result.extend(chunk)
    return bytes(result)


def drain_process_output(
    process: subprocess.Popen[bytes], output: bytearray
) -> None:
    assert process.stdout is not None
    while select.select([process.stdout], [], [], 0)[0]:
        chunk = os.read(process.stdout.fileno(), 65536)
        if not chunk:
            break
        output.extend(chunk)


def write_all(
    fd: int,
    data: bytes,
    process: subprocess.Popen[bytes],
    output: bytearray,
) -> None:
    view = memoryview(data)
    while view:
        drain_process_output(process, output)
        select.select([], [fd], [], 1)
        try:
            count = os.write(fd, view)
        except BlockingIOError:
            continue
        view = view[count:]


def configure_raw_pty(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] = 0
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


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
        "--package",
        type=Path,
        default=DEFAULT_PACKAGE,
        help=f"external .pkg file to install (default: {DEFAULT_PACKAGE})",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=DEFAULT_WORKDIR,
        help=f"persistent artifact root (default: {DEFAULT_WORKDIR})",
    )
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    package = args.package.expanduser().resolve()
    artifact_root = args.workdir.expanduser().resolve()

    for label, path, kind in (
        ("MAME executable", mame, "file"),
        ("ROM path", rompath, "directory"),
        ("package", package, "file"),
    ):
        valid = path.is_file() if kind == "file" else path.is_dir()
        if not valid:
            print(f"error: {label} not found: {path}", file=sys.stderr)
            return 2

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    workdir = artifact_root / stamp
    workdir.mkdir(parents=True)

    # Allow the known 48 KiB test package and some installation time after a
    # 19,200-baud transfer. Larger packages extend the scripted run.
    wire_seconds = (package.stat().st_size * 1.08 * 10) / 19_200
    exit_frame = max(4700, 2750 + int((wire_seconds + 10) * 60))
    snapshot_frame = exit_frame - 200
    lua_path = workdir / "pclink.lua"
    lua_path.write_text(
        lua_navigation(snapshot_frame, exit_frame), encoding="utf-8"
    )

    command = [
        str(mame),
        "datarover840",
        "-rompath",
        str(rompath),
        "-nvram_directory",
        str(workdir / "nvram"),
        "-snapshot_directory",
        str(workdir / "snapshots"),
        "-snapview",
        "native",
        "-autoboot_script",
        str(lua_path),
        "-rs2321",
        "pty",
        "-video",
        "none",
        "-sound",
        "none",
        "-nothrottle",
        "-skip_gameinfo",
        "-oslog",
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=mame.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as error:
        print(f"error: unable to run MAME: {error}", file=sys.stderr)
        return 2

    output = bytearray()
    device_wire = bytearray()
    host_wire = bytearray()
    connect_wire_length: int | None = None
    fd: int | None = None
    error: str | None = None
    try:
        assert process.stdout is not None
        deadline = time.monotonic() + 30
        pty_path = None
        while time.monotonic() < deadline:
            if select.select([process.stdout], [], [], 0.25)[0]:
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                output.extend(chunk)
                match = PTY_PATTERN.search(output)
                if match:
                    pty_path = match.group(1).decode()
                    break
        if pty_path is None:
            raise RuntimeError("MAME did not announce its PCLink PTY")

        fd = os.open(pty_path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        configure_raw_pty(fd)

        connect_stream = None
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            drain_process_output(process, output)
            if select.select([fd], [], [], 0.05)[0]:
                device_wire.extend(read_available(fd))
            if device_wire.startswith(PC_LINK_MAGIC):
                try:
                    decoded = decode_crc_stream(device_wire[4:])
                    tag, payload = decode_packet(decoded)
                except ProtocolError:
                    pass
                else:
                    if tag == CONNECT_TAG:
                        connect_stream = payload
                        connect_wire_length = len(device_wire)
                        break
            if process.poll() is not None:
                break
        if connect_stream is None:
            raise RuntimeError("communicator did not issue a valid Cnct request")

        # WinPcLink sends this acknowledgement twice; Magic Cap requires both.
        connected = encode_packet(CONNECTED_TAG)
        host_wire.extend(connected)
        host_wire.extend(connected)
        write_all(fd, bytes(host_wire), process, output)

        time.sleep(0.5)
        metadata = encode_packet(SEND_PACKAGE_TAG, package_metadata(package))
        package_stream = encode_crc_stream(package.read_bytes() + b"\0\0\0\0")
        host_wire.extend(metadata)
        host_wire.extend(package_stream)
        write_all(fd, metadata, process, output)
        write_all(fd, package_stream, process, output)

        deadline = time.monotonic() + max(60, exit_frame // 30)
        while process.poll() is None and time.monotonic() < deadline:
            drain_process_output(process, output)
            if select.select([fd], [], [], 0.05)[0]:
                device_wire.extend(read_available(fd))
        if process.poll() is None:
            raise RuntimeError("MAME did not reach the post-install checkpoint")
    except (OSError, RuntimeError, ProtocolError, ValueError) as caught:
        error = str(caught)
    finally:
        if fd is not None:
            try:
                device_wire.extend(read_available(fd))
                os.close(fd)
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
        try:
            tail = process.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            process.kill()
            tail = process.communicate()[0]
        output.extend(tail)

    (workdir / "device-wire.bin").write_bytes(device_wire)
    (workdir / "host-wire.bin").write_bytes(host_wire)
    log_path = workdir / "mame-output.txt"
    log_path.write_bytes(output)

    if error:
        print(f"error: {error}; see {workdir}", file=sys.stderr)
        return 2
    if process.returncode:
        print(
            f"error: MAME exited with status {process.returncode}; see {log_path}",
            file=sys.stderr,
        )
        return 2
    if b"RX overrun" in output:
        print(f"error: Dino UART receive overrun; see {log_path}", file=sys.stderr)
        return 1

    assert connect_wire_length is not None
    trailing = bytes(device_wire[connect_wire_length:])
    if trailing:
        try:
            tag, _ = decode_packet(decode_crc_stream(trailing))
        except ProtocolError as caught:
            print(f"error: malformed device response: {caught}", file=sys.stderr)
            return 1
        if tag == GOODBYE_TAG:
            print(
                "error: communicator rejected the transfer with GBye",
                file=sys.stderr,
            )
            return 1

    screenshot = workdir / "snapshots" / "package-installed.png"
    if not screenshot.is_file():
        print(f"error: install screenshot not produced: {screenshot}", file=sys.stderr)
        return 1

    print(f"PASS: installed {package.name} through PCLink")
    print(f"Install screenshot: {screenshot}")
    print(f"Persistent artifacts: {workdir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
