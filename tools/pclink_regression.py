#!/usr/bin/env python3
"""Install a Magic Cap package through the emulated PCLink serial cable."""

from __future__ import annotations

import argparse
import os
import re
import select
import shutil
import struct
import subprocess
import sys
import termios
import time
import zlib
from math import ceil
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
PTY_PATTERN = re.compile(rb":rs2321:pty PTY: (/[^\r\n]+)")
ESCAPE_BYTES = frozenset((0x0E, 0x0F, 0x10))
PC_LINK_MAGIC = b"ChMa"
CONNECT_TAG = b"Cnct"
CONNECTED_TAG = b"Cntd"
SEND_PACKAGE_TAG = b"SPkg"
GOODBYE_TAG = b"GBye"
PING_TAG = b"Ping"
PONG_TAG = b"Pong"


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
    elseif frames == 2180 then
        machine.screens[":screen"]:snapshot("navigation-workbench.png")
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
    elseif frames == 2580 then
        machine.screens[":screen"]:snapshot("navigation-storeroom.png")
    elseif frames == 2630 then press(48, 155)
    elseif frames == 2650 then touch_button:set_value(0)
    elseif frames == {snapshot_frame} then
        machine.screens[":screen"]:snapshot("package-installed.png")
    elseif frames == {snapshot_frame + 1400} then
        machine.screens[":screen"]:snapshot("pclink-disconnected.png")
    elseif frames == {exit_frame} then machine:exit()
    end
end)
"""


def lua_warm_provider_navigation(
    snapshot_frame: int,
    exit_frame: int,
    probe_package: bool = False,
    save_path: Path | None = None,
    package_ready_path: Path | None = None,
    package_snapshotted_path: Path | None = None,
    internet_center_start: bool = False,
    suppress_magicbus_warning: bool = False,
) -> str:
    """Navigate a provider-configured warm image from In box to PCLink."""
    save_clause = ""
    if save_path is not None:
        quoted_path = (
            str(save_path).replace("\\", "\\\\").replace('"', '\\"')
        )
        save_clause = (
            f'    elseif frames == {snapshot_frame + 3050} then\n'
            f'        machine:save("{quoted_path}")\n'
        )
    signal_clause = ""
    if package_ready_path is not None and package_snapshotted_path is not None:
        quoted_ready = (
            str(package_ready_path).replace("\\", "\\\\").replace('"', '\\"')
        )
        quoted_snapshotted = (
            str(package_snapshotted_path)
            .replace("\\", "\\\\")
            .replace('"', '\\"')
        )
        signal_clause = f"""    if not package_snapshotted then
        local ready = io.open("{quoted_ready}", "r")
        if ready then
            ready:close()
            machine.screens[":screen"]:snapshot("package-installed.png")
            local acknowledged = io.open("{quoted_snapshotted}", "w")
            if acknowledged then
                acknowledged:write("snapshotted\\n")
                acknowledged:close()
            end
            package_snapshotted = true
        end
    end
"""
    alert_dismissal = (
        f"""    elseif frames == {snapshot_frame + 1450} then press(413, 46)
    elseif frames == {snapshot_frame + 1470} then touch_button:set_value(0)
    elseif frames == {snapshot_frame + 1500} then press(413, 61)
    elseif frames == {snapshot_frame + 1520} then touch_button:set_value(0)
"""
    )
    post_install = (
        f"""    elseif frames == {snapshot_frame + 1400} then
        machine.screens[":screen"]:snapshot("pclink-disconnected.png")
{alert_dismissal.rstrip()}
    elseif frames == {snapshot_frame + 1650} then press(270, 220)
    elseif frames == {snapshot_frame + 1670} then touch_button:set_value(0)
    elseif frames == {snapshot_frame + 1850} then
        machine.screens[":screen"]:snapshot("package-opened.png")
    elseif frames == {snapshot_frame + 1900} then press(440, 10)
    elseif frames == {snapshot_frame + 1920} then touch_button:set_value(0)
    elseif frames == {snapshot_frame + 2100} then
        machine.screens[":screen"]:snapshot("post-package-storeroom.png")
    elseif frames == {snapshot_frame + 2200} then press(440, 10)
    elseif frames == {snapshot_frame + 2220} then touch_button:set_value(0)
    elseif frames == {snapshot_frame + 2400} then
        machine.screens[":screen"]:snapshot("post-package-hallway.png")
    elseif frames == {snapshot_frame + 2500} then press(440, 10)
    elseif frames == {snapshot_frame + 2520} then touch_button:set_value(0)
    elseif frames == {snapshot_frame + 2700} then
        machine.screens[":screen"]:snapshot("post-package-downtown.png")
    elseif frames == {snapshot_frame + 2800} then press(260, 200)
    elseif frames == {snapshot_frame + 2820} then touch_button:set_value(0)
    elseif frames == {snapshot_frame + 3000} then
        machine.screens[":screen"]:snapshot("downtown-directory.png")
{save_clause.rstrip()}
"""
        if probe_package
        else f"""    elseif frames == {snapshot_frame + 1400} then
        machine.screens[":screen"]:snapshot("pclink-disconnected.png")
"""
    )
    debugger_clause = (
        """-- MAME exposes the raw MIPS names R2/R31, not v0/ra aliases.
local cpu = machine.devices[":maincpu"]
cpu.debug:bpset(
    0x13c29434,
    "1",
    "do R2=0; do PC=R31; g")
cpu.debug:go()
"""
        if suppress_magicbus_warning
        else ""
    )
    navigation_steps = (
        f"""    if frames == 1300 then press(413, 61)
    elseif frames == 1320 then touch_button:set_value(0)
    elseif frames == 1600 then press(421, 70)
    elseif frames == 1620 then touch_button:set_value(0)
    elseif frames == 1900 then press(343, 48)
    elseif frames == 1920 then touch_button:set_value(0)
    elseif frames == 2200 then press(440, 10)
    elseif frames == 2220 then touch_button:set_value(0)
    elseif frames == 2500 then press(440, 10)
    elseif frames == 2520 then touch_button:set_value(0)
    elseif frames == 2800 then press(452, 255)
    elseif frames == 2820 then touch_button:set_value(0)
    elseif frames == 3000 then
        machine.screens[":screen"]:snapshot("navigation-internet-center.png")
    elseif frames == 3300 then press(430, 10)
    elseif frames == 3320 then touch_button:set_value(0)
    elseif frames == 3600 then
        machine.screens[":screen"]:snapshot("navigation-downtown.png")
    elseif frames == 3700 then press(301, 110)
    elseif frames == 3720 then touch_button:set_value(0)
    elseif frames == 4000 then press(440, 10)
    elseif frames == 4020 then touch_button:set_value(0)
    elseif frames == 4300 then press(60, 130)
    elseif frames == 4320 then touch_button:set_value(0)
    elseif frames == 4500 then press(170, 132)
    elseif frames == 4520 then touch_button:set_value(0)
    elseif frames == 4800 then
        machine.screens[":screen"]:snapshot("navigation-storeroom.png")
    elseif frames == 4850 then press(413, 61)
    elseif frames == 4870 then touch_button:set_value(0)
    elseif frames == 5000 then press(48, 155)
    elseif frames == 5020 then touch_button:set_value(0)
    elseif frames == {snapshot_frame} and not package_snapshotted then
        machine.screens[":screen"]:snapshot("package-installed.png")
"""
        if internet_center_start
        else f"""    if frames == 1300 then press(413, 61)
    elseif frames == 1320 then touch_button:set_value(0)
    elseif frames == 1600 then press(421, 70)
    elseif frames == 1620 then touch_button:set_value(0)
    elseif frames == 1900 then press(343, 48)
    elseif frames == 1920 then touch_button:set_value(0)
    elseif frames == 2200 then press(440, 10)
    elseif frames == 2220 then touch_button:set_value(0)
    elseif frames == 2500 then press(440, 10)
    elseif frames == 2520 then touch_button:set_value(0)
    elseif frames == 2800 then press(452, 255)
    elseif frames == 2820 then touch_button:set_value(0)
    elseif frames == 3000 then
        machine.screens[":screen"]:snapshot("navigation-storeroom.png")
    elseif frames == 3100 then press(60, 130)
    elseif frames == 3120 then touch_button:set_value(0)
    elseif frames == 3400 then press(48, 155)
    elseif frames == 3420 then touch_button:set_value(0)
    elseif frames == {snapshot_frame} and not package_snapshotted then
        machine.screens[":screen"]:snapshot("package-installed.png")
"""
    )
    return f"""local machine = manager.machine
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local frames = 0
local package_snapshotted = false
{debugger_clause.rstrip()}

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

emu.register_frame_done(function()
    frames = frames + 1
{signal_clause.rstrip()}
{navigation_steps.rstrip()}
{post_install.rstrip()}
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
    parser.add_argument(
        "--nvram-source",
        type=Path,
        help=(
            "copy a provider-configured NVRAM directory into the run and "
            "use warm-boot navigation"
        ),
    )
    parser.add_argument(
        "--probe-package",
        action="store_true",
        help="open the received package and capture its Package scene",
    )
    parser.add_argument(
        "--internet-center-source",
        action="store_true",
        help="treat --nvram-source as starting on Internet Center's provider screen",
    )
    parser.add_argument(
        "--connect-only",
        action="store_true",
        help="diagnose a PCLink connect/disconnect without sending a package",
    )
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    package = args.package.expanduser().resolve()
    artifact_root = args.workdir.expanduser().resolve()
    nvram_source = (
        args.nvram_source.expanduser().resolve()
        if args.nvram_source
        else None
    )

    inputs = [
        ("MAME executable", mame, "file"),
        ("ROM path", rompath, "directory"),
    ]
    if not args.connect_only:
        inputs.append(("package", package, "file"))
    if nvram_source is not None:
        inputs.append(("NVRAM source", nvram_source, "directory"))
    for label, path, kind in inputs:
        valid = path.is_file() if kind == "file" else path.is_dir()
        if not valid:
            print(f"error: {label} not found: {path}", file=sys.stderr)
            return 2
    if args.probe_package and nvram_source is None:
        print(
            "error: --probe-package requires --nvram-source",
            file=sys.stderr,
        )
        return 2
    if args.internet_center_source and nvram_source is None:
        print(
            "error: --internet-center-source requires --nvram-source",
            file=sys.stderr,
        )
        return 2
    if args.probe_package and args.connect_only:
        print(
            "error: --probe-package cannot be combined with --connect-only",
            file=sys.stderr,
        )
        return 2
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    workdir = artifact_root / stamp
    workdir.mkdir(parents=True)
    (workdir / "cfg").mkdir()
    if nvram_source is not None:
        shutil.copytree(nvram_source, workdir / "nvram")

    # Schedule the screenshots from the exact escaped wire size. A broad
    # package-size estimate is not precise enough once escaping and PCLink's
    # per-frame length/CRC overhead are included.
    metadata_wire = (
        b""
        if args.connect_only
        else encode_packet(SEND_PACKAGE_TAG, package_metadata(package))
    )
    package_wire = (
        b""
        if args.connect_only
        else encode_crc_stream(package.read_bytes() + b"\0\0\0\0")
    )
    wire_seconds = (len(metadata_wire) + len(package_wire)) * 10 / 19_200
    navigation_frame = (
        5100
        if args.internet_center_source
        else 3500 if nvram_source is not None else 2750
    )
    snapshot_frame = navigation_frame + ceil(wire_seconds * 60) + 120
    exit_frame = snapshot_frame + (
        3100 if args.probe_package else 1600
    )
    lua_path = workdir / "pclink.lua"
    post_install_state = workdir / "post-install.sta"
    package_ready_path = workdir / "package-ready"
    package_snapshotted_path = workdir / "package-snapshotted"
    lua_path.write_text(
        (
            lua_warm_provider_navigation(
                snapshot_frame,
                exit_frame,
                args.probe_package,
                post_install_state if args.probe_package else None,
                package_ready_path,
                package_snapshotted_path,
                args.internet_center_source,
                args.probe_package,
            )
            if nvram_source is not None
            else lua_navigation(snapshot_frame, exit_frame)
        ),
        encoding="utf-8",
    )

    command = [
        str(mame),
        "datarover840",
        "-rompath",
        str(rompath),
        "-nvram_directory",
        str(workdir / "nvram"),
        "-cfg_directory",
        str(workdir / "cfg"),
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
        # Keep host-side PTY scheduling gaps short in emulated time.  With
        # unlimited speed, a full PTY buffer can drain while this process is
        # descheduled and PCLink's serial watchdog expires before the next
        # write, even though every frame subsequently arrives intact.
        "-throttle",
        "-speed",
        "10",
        "-skip_gameinfo",
        "-oslog",
    ]
    if args.probe_package:
        command.extend(["-debug", "-debugger", "none"])
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
    disconnect_completed = False
    pong_seen = False
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
        deadline = time.monotonic() + 180
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
        if not args.connect_only:
            host_wire.extend(metadata_wire)
            host_wire.extend(package_wire)
            write_all(fd, metadata_wire, process, output)
            write_all(fd, package_wire, process, output)
        ping_wire = encode_packet(PING_TAG)
        pong_wire = encode_packet(PONG_TAG)
        goodbye_wire = encode_packet(GOODBYE_TAG)
        host_wire.extend(ping_wire)
        write_all(fd, ping_wire, process, output)
        deadline = time.monotonic() + max(60, exit_frame // 30)
        while process.poll() is None and time.monotonic() < deadline:
            drain_process_output(process, output)
            if (
                fd is not None
                and not disconnect_completed
                and pong_seen
                and (
                    nvram_source is None
                    or package_snapshotted_path.is_file()
                )
            ):
                # WinPcLink's documented Close Connection operation is
                # initiated on the PC. Send the normal GBye request and leave
                # the serial endpoint open long enough for Magic Cap to
                # consume it; the surrounding MAME run owns the PTY lifetime.
                host_wire.extend(goodbye_wire)
                write_all(fd, goodbye_wire, process, output)
                disconnect_completed = True
            if fd is not None and select.select([fd], [], [], 0.05)[0]:
                device_wire.extend(read_available(fd))
                trailing = bytes(device_wire[connect_wire_length:])
                if pong_wire in trailing:
                    pong_seen = True
                    if nvram_source is not None:
                        package_ready_path.write_text(
                            "package-ready\n",
                            encoding="ascii",
                        )
                if goodbye_wire in trailing:
                    # A device-side close is also valid (and is used by the
                    # connect-only diagnostic if it races the PC request).
                    disconnect_completed = True
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
    if not disconnect_completed:
        print(
            "error: PCLink disconnect did not complete",
            file=sys.stderr,
        )
        return 1
    if not pong_seen:
        print(
            "error: communicator did not answer the final PCLink Ping",
            file=sys.stderr,
        )
        return 1
    screenshot = workdir / "snapshots" / "package-installed.png"
    if not screenshot.is_file():
        print(f"error: install screenshot not produced: {screenshot}", file=sys.stderr)
        return 1
    disconnect_screenshot = (
        workdir / "snapshots" / "pclink-disconnected.png"
    )
    if not disconnect_screenshot.is_file():
        print(
            f"error: disconnect screenshot not produced: "
            f"{disconnect_screenshot}",
            file=sys.stderr,
        )
        return 1
    if args.probe_package and not post_install_state.is_file():
        print(
            f"error: post-install state not produced: {post_install_state}",
            file=sys.stderr,
        )
        return 1
    if args.probe_package:
        opened_screenshot = workdir / "snapshots" / "package-opened.png"
        if not opened_screenshot.is_file():
            print(
                f"error: package-opened screenshot not produced: "
                f"{opened_screenshot}",
                file=sys.stderr,
            )
            return 1
        if opened_screenshot.read_bytes() == disconnect_screenshot.read_bytes():
            print(
                "error: received package did not open after disconnect",
                file=sys.stderr,
            )
            return 1

    if args.connect_only:
        print("PASS: completed a PCLink connect/disconnect cycle")
    else:
        print(f"PASS: installed {package.name} through PCLink")
    print(f"Install screenshot: {screenshot}")
    print(f"Disconnect screenshot: {disconnect_screenshot}")
    print(f"Persistent artifacts: {workdir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
