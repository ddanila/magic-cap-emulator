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
from typing import Callable

try:
    from tools import modem_bridge as modem_support
except ModuleNotFoundError:
    import modem_bridge as modem_support


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
MODEM_PTY_PATTERN = re.compile(rb":pccard1:modem PTY: (/[^\r\n]+)")
ESCAPE_BYTES = frozenset((0x0E, 0x0F, 0x10))
PC_LINK_MAGIC = b"ChMa"
CONNECT_TAG = b"Cnct"
CONNECTED_TAG = b"Cntd"
SEND_PACKAGE_TAG = b"SPkg"
GOODBYE_TAG = b"GBye"
PING_TAG = b"Ping"
PONG_TAG = b"Pong"
PACKAGE_SETTLE_FRAMES = 1800
NAME_KEY_INTERVAL = 100
PROVIDER_POST_OWNER_TO_PCLINK_FRAMES = 9700


class CombinedModemSession:
    """Own the live modem PTY, Slirp peer, and deterministic HTTP server."""

    def __init__(
        self,
        fd: int,
        pty_path: str,
        run_dir: Path,
        slirp: str,
        bubblewrap: str,
        baudrate: int,
        http_port: int,
    ) -> None:
        self.fd: int | None = fd
        self.pty_path = pty_path
        self.run_dir = run_dir
        self.slirp = slirp
        self.bubblewrap = bubblewrap
        self.baudrate = baudrate
        self.negotiator = modem_support.HayesNegotiator()
        self.dialed = False
        self.guest_wire = bytearray()
        self.host_wire = bytearray()
        self.transcript: list[str] = []
        self.slirp_process: subprocess.Popen[bytes] | None = None
        self.closed = False
        (run_dir / "slirp.rc").write_text(
            "debugppp ppp-debug.txt\n",
            encoding="utf-8",
        )
        self.http_server, self.http_thread, self.http_requests = (
            modem_support.start_acceptance_http_server(http_port)
        )
        try:
            self.slirp_log = (run_dir / "slirp-output.txt").open("wb")
        except OSError:
            self.http_server.shutdown()
            self.http_server.server_close()
            self.http_thread.join(timeout=5)
            raise

    def service(self) -> None:
        """Answer pending Hayes traffic and hand the line to Slirp on dial."""
        if self.fd is None or not select.select([self.fd], [], [], 0)[0]:
            return
        chunk = read_available(self.fd)
        self.guest_wire.extend(chunk)
        for event in self.negotiator.feed(chunk):
            self.transcript.append(f"HAYES {event.command}\n")
            if event.dial:
                self.dialed = True
                env = os.environ.copy()
                env["SLIRP_TTY"] = modem_support.classic_slirp_tty(
                    self.pty_path
                )
                self.slirp_process = subprocess.Popen(
                    [
                        self.bubblewrap,
                        "--ro-bind",
                        "/",
                        "/",
                        "--dev-bind",
                        "/dev",
                        "/dev",
                        "--bind",
                        str(self.run_dir),
                        str(self.run_dir),
                        "--unshare-uts",
                        "--hostname",
                        "10.0.2.2",
                        "--die-with-parent",
                        self.slirp,
                        "-P",
                        "-f",
                        str(self.run_dir / "slirp.rc"),
                        "-b",
                        str(self.baudrate),
                        "nozeros",
                    ],
                    cwd=self.run_dir,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=self.slirp_log,
                    stderr=subprocess.STDOUT,
                )
                time.sleep(0.25)
                response = b"\r\nCONNECT 14400\r\n"
                os.write(self.fd, response)
                self.host_wire.extend(response)
                os.close(self.fd)
                self.fd = None
                break
            if event.response:
                time.sleep(0.10)
                os.write(self.fd, event.response)
                self.host_wire.extend(event.response)

    def close(self) -> None:
        """Stop owned processes and persist all bridge evidence."""
        if self.closed:
            return
        self.closed = True
        if self.fd is not None:
            try:
                self.service()
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        if self.slirp_process is not None and self.slirp_process.poll() is None:
            self.slirp_process.terminate()
            try:
                self.slirp_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.slirp_process.kill()
                self.slirp_process.wait()
        self.http_server.shutdown()
        self.http_server.server_close()
        self.http_thread.join(timeout=5)
        self.slirp_log.close()
        (self.run_dir / "modem-transcript.txt").write_text(
            "".join(self.transcript),
            encoding="utf-8",
        )
        (self.run_dir / "modem-guest-wire.bin").write_bytes(self.guest_wire)
        (self.run_dir / "modem-host-wire.bin").write_bytes(self.host_wire)
        (self.run_dir / "http-requests.txt").write_text(
            "".join(f"{path}\n" for path in self.http_requests),
            encoding="utf-8",
        )

    def validation_error(self) -> str | None:
        """Return why the combined HTTP acceptance failed, if it did."""
        slirp_output = (self.run_dir / "slirp-output.txt").read_text(
            encoding="utf-8", errors="replace"
        )
        ppp_debug = self.run_dir / "slirp_pppdebug"
        if not self.dialed:
            return "Web Browser did not dial the emulated modem"
        if "SLiRP Ready" not in slirp_output:
            return "Slirp did not become ready"
        if (
            not ppp_debug.is_file()
            or "slirppp: PPP is up now"
            not in ppp_debug.read_text(encoding="utf-8", errors="replace")
        ):
            return "Slirp did not complete IPCP"
        if "/" not in self.http_requests:
            return "Web Browser did not request the local root page"
        return None


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


def name_card_key_steps(
    text: str,
    start_frame: int,
    interval: int = NAME_KEY_INTERVAL,
) -> str:
    """Generate paced taps for the first-run name-card keyboard."""
    if not text:
        raise ValueError("name-card automation requires a non-empty name")
    positions = {
        "q": (26, 198),
        "w": (70, 198),
        "e": (112, 198),
        "r": (156, 198),
        "t": (199, 198),
        "y": (242, 198),
        "u": (285, 198),
        "i": (328, 198),
        "o": (370, 198),
        "p": (414, 198),
        "a": (33, 234),
        "s": (76, 234),
        "d": (119, 234),
        "f": (162, 234),
        "g": (205, 234),
        "h": (248, 234),
        "j": (291, 234),
        "k": (334, 234),
        "l": (377, 234),
        "z": (48, 269),
        "x": (89, 269),
        "c": (132, 269),
        "v": (176, 269),
        "b": (219, 269),
        "n": (262, 269),
        "m": (305, 269),
    }
    unsupported = sorted(set(text.lower()) - positions.keys())
    if unsupported:
        raise ValueError(
            "name-card automation supports letters a-z only; unsupported: "
            + ", ".join(repr(character) for character in unsupported)
        )
    return "".join(
        f"    elseif frames == {start_frame + (index * interval)} then "
        f"press({positions[character.lower()][0]}, "
        f"{positions[character.lower()][1]})\n"
        f"    elseif frames == "
        f"{start_frame + 20 + (index * interval)} then "
        "touch_button:set_value(0)\n"
        for index, character in enumerate(text)
    ).rstrip()


def provider_first_run_pclink_frame(
    owner_first_name: str,
    owner_last_name: str,
) -> int:
    """Return the frame at which first-run automation opens PCLink."""
    owner_done_frame = (
        3700
        + (len(owner_first_name) + len(owner_last_name))
        * NAME_KEY_INTERVAL
    )
    return owner_done_frame + PROVIDER_POST_OWNER_TO_PCLINK_FRAMES


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


def lua_provider_first_run_navigation(
    snapshot_frame: int,
    owner_first_name: str,
    owner_last_name: str,
) -> str:
    """Complete owner/location setup, then navigate to PCLink."""
    first_steps = name_card_key_steps(
        owner_first_name,
        3500,
    )
    last_start = 3600 + (len(owner_first_name) * NAME_KEY_INTERVAL)
    last_steps = name_card_key_steps(
        owner_last_name,
        last_start,
    )
    done_frame = (
        last_start
        + (len(owner_last_name) * NAME_KEY_INTERVAL)
        + NAME_KEY_INTERVAL
    )
    location_start = done_frame + 1200
    navigation_start = location_start + 5800
    return f"""    if frames == 1000 then press(120, 40)
    elseif frames == 1020 then touch_button:set_value(0)
    elseif frames == 1200 then
        machine.screens[":screen"]:snapshot("provider-card-selected.png")
    elseif frames == 1400 then press(320, 164)
    elseif frames == 1420 then touch_button:set_value(0)
    elseif frames == 1600 then
        machine.screens[":screen"]:snapshot("provider-first-location.png")
    elseif frames == 1800 then press(421, 70)
    elseif frames == 1820 then touch_button:set_value(0)
    elseif frames == 2000 then
        machine.screens[":screen"]:snapshot("provider-battery-dismissed.png")
    elseif frames == 2200 then press(305, 135)
    elseif frames == 2220 then touch_button:set_value(0)
    elseif frames == 2400 then
        machine.screens[":screen"]:snapshot("provider-name-card.png")
    elseif frames == 2600 then press(135, 170)
    elseif frames == 2620 then touch_button:set_value(0)
    elseif frames == 2800 then
        machine.screens[":screen"]:snapshot("provider-name-card-step-2.png")
    elseif frames == 2900 then press(371, 236)
    elseif frames == 2920 then touch_button:set_value(0)
    elseif frames == 3100 then
        machine.screens[":screen"]:snapshot("provider-name-card-step-3.png")
    elseif frames == 3200 then press(451, 52)
    elseif frames == 3220 then touch_button:set_value(0)
    elseif frames == 3400 then
        machine.screens[":screen"]:snapshot("provider-name-card-step-4.png")
{first_steps}
    elseif frames == {last_start - 100} then press(370, 102)
    elseif frames == {last_start - 80} then touch_button:set_value(0)
{last_steps}
    elseif frames == {done_frame - 100} then
        machine.screens[":screen"]:snapshot("provider-owner-name-entered.png")
    elseif frames == {done_frame} then press(428, 144)
    elseif frames == {done_frame + 20} then touch_button:set_value(0)
    elseif frames == {done_frame + 300} then
        machine.screens[":screen"]:snapshot("provider-name-card-step-5.png")
    elseif frames == {done_frame + 400} then press(237, 110)
    elseif frames == {done_frame + 420} then touch_button:set_value(0)
    elseif frames == {done_frame + 600} then
        machine.screens[":screen"]:snapshot("provider-name-card-step-6.png")
    elseif frames == {done_frame + 700} then press(371, 194)
    elseif frames == {done_frame + 720} then touch_button:set_value(0)
    elseif frames == {done_frame + 900} then
        machine.screens[":screen"]:snapshot("provider-name-card-complete.png")
    elseif frames == {location_start} then press(293, 258)
    elseif frames == {location_start + 20} then touch_button:set_value(0)
    elseif frames == {location_start + 400} then
        machine.screens[":screen"]:snapshot("provider-locations-tab.png")
    elseif frames == {location_start + 600} then press(450, 58)
    elseif frames == {location_start + 620} then touch_button:set_value(0)
    elseif frames == {location_start + 1000} then
        machine.screens[":screen"]:snapshot("provider-add-location.png")
    elseif frames == {location_start + 1200} then press(145, 103)
    elseif frames == {location_start + 1220} then touch_button:set_value(0)
    elseif frames == {location_start + 1600} then
        machine.screens[":screen"]:snapshot("provider-phone-locations.png")
    elseif frames == {location_start + 1800} then press(102, 300)
    elseif frames == {location_start + 1820} then touch_button:set_value(0)
    elseif frames == {location_start + 2200} then
        machine.screens[":screen"]:snapshot("provider-location-stamps.png")
    elseif frames == {location_start + 2400} then press(50, 104)
    elseif frames == {location_start + 2420} then touch_button:set_value(0)
    elseif frames == {location_start + 2800} then
        machine.screens[":screen"]:snapshot("provider-home-location-setup.png")
    elseif frames == {location_start + 3000} then press(451, 52)
    elseif frames == {location_start + 3020} then touch_button:set_value(0)
    elseif frames == {location_start + 3400} then
        machine.screens[":screen"]:snapshot("provider-home-location-created.png")
    elseif frames == {location_start + 3600} then press(440, 10)
    elseif frames == {location_start + 3620} then touch_button:set_value(0)
    elseif frames == {location_start + 4000} then
        machine.screens[":screen"]:snapshot("provider-home-location-returned.png")
    elseif frames == {location_start + 4200} then press(70, 65)
    elseif frames == {location_start + 4220} then touch_button:set_value(0)
    elseif frames == {location_start + 4600} then
        machine.screens[":screen"]:snapshot("provider-choose-connection.png")
    elseif frames == {location_start + 4800} then press(250, 77)
    elseif frames == {location_start + 4820} then touch_button:set_value(0)
    elseif frames == {location_start + 5000} then
        machine.screens[":screen"]:snapshot("provider-pccard-selected.png")
    elseif frames == {location_start + 5200} then press(425, 202)
    elseif frames == {location_start + 5220} then touch_button:set_value(0)
    elseif frames == {location_start + 5600} then
        machine.screens[":screen"]:snapshot("provider-pccard-assigned.png")
    elseif frames == {navigation_start} then press(440, 10)
    elseif frames == {navigation_start + 20} then touch_button:set_value(0)
    elseif frames == {navigation_start + 300} then press(440, 10)
    elseif frames == {navigation_start + 320} then touch_button:set_value(0)
    elseif frames == {navigation_start + 600} then press(452, 255)
    elseif frames == {navigation_start + 620} then touch_button:set_value(0)
    elseif frames == {navigation_start + 800} then
        machine.screens[":screen"]:snapshot("navigation-internet-center.png")
    elseif frames == {navigation_start + 1100} then press(430, 10)
    elseif frames == {navigation_start + 1120} then touch_button:set_value(0)
    elseif frames == {navigation_start + 1400} then
        machine.screens[":screen"]:snapshot("navigation-downtown.png")
    elseif frames == {navigation_start + 1500} then press(301, 110)
    elseif frames == {navigation_start + 1520} then touch_button:set_value(0)
    elseif frames == {navigation_start + 1800} then press(440, 10)
    elseif frames == {navigation_start + 1820} then touch_button:set_value(0)
    elseif frames == {navigation_start + 2100} then press(60, 130)
    elseif frames == {navigation_start + 2120} then touch_button:set_value(0)
    elseif frames == {navigation_start + 2300} then press(170, 132)
    elseif frames == {navigation_start + 2320} then touch_button:set_value(0)
    elseif frames == {navigation_start + 2600} then
        machine.screens[":screen"]:snapshot("navigation-storeroom.png")
    elseif frames == {navigation_start + 2700} then press(48, 155)
    elseif frames == {navigation_start + 2720} then touch_button:set_value(0)
    elseif frames == {snapshot_frame} and not package_snapshotted then
        machine.screens[":screen"]:snapshot("package-installed.png")
"""


def lua_warm_provider_navigation(
    snapshot_frame: int,
    exit_frame: int,
    probe_package: bool = False,
    save_path: Path | None = None,
    package_ready_path: Path | None = None,
    package_snapshotted_path: Path | None = None,
    internet_center_start: bool = False,
    browser_acceptance: bool = False,
    http_port: int = 8080,
    owner_first_name: str | None = None,
    owner_last_name: str | None = None,
) -> str:
    """Generate provider-seeded navigation and post-install acceptance."""
    settle_offset = (
        PACKAGE_SETTLE_FRAMES
        if package_ready_path is not None
        and package_snapshotted_path is not None
        else 0
    )
    post_base = snapshot_frame + settle_offset
    save_clause = ""
    if save_path is not None:
        quoted_path = (
            str(save_path).replace("\\", "\\\\").replace('"', '\\"')
        )
        save_clause = (
            f'    elseif frames == {post_base + 3050} then\n'
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
            if not package_ready_frame then
                package_ready_frame = frames
            end
        end
        if package_ready_frame
                and frames >= package_ready_frame + {PACKAGE_SETTLE_FRAMES} then
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
    if browser_acceptance:
        key_positions = {
            **{
                digit: (26 + (index * 43), 198)
                for index, digit in enumerate("1234567890")
            },
            ".": (391, 270),
            ":": (262, 270),
            "/": (434, 270),
        }
        address = f"10.0.2.2:{http_port}/"
        key_steps = "".join(
            f"    elseif frames == "
            f"{post_base + 3600 + (index * 60)} then "
            f"press({key_positions[character][0]}, "
            f"{key_positions[character][1]})\n"
            f"    elseif frames == "
            f"{post_base + 3620 + (index * 60)} then "
            f"touch_button:set_value(0)\n"
            for index, character in enumerate(address)
        ).rstrip()
        post_install = f"""    elseif frames == {post_base + 1400} then
        machine.screens[":screen"]:snapshot("pclink-disconnected.png")
    elseif frames == {post_base + 1650} then press(270, 220)
    elseif frames == {post_base + 1670} then touch_button:set_value(0)
    elseif frames == {post_base + 1850} then
        machine.screens[":screen"]:snapshot("package-opened.png")
    elseif frames == {post_base + 1950} then press(451, 148)
    elseif frames == {post_base + 1970} then touch_button:set_value(0)
    elseif frames == {post_base + 2200} then
        machine.screens[":screen"]:snapshot("browser-scene-opened.png")
    elseif frames == {post_base + 2300} then press(126, 80)
    elseif frames == {post_base + 2320} then touch_button:set_value(0)
    elseif frames == {post_base + 2700} then press(450, 45)
    elseif frames == {post_base + 2720} then touch_button:set_value(0)
    elseif frames == {post_base + 3000} then press(120, 302)
    elseif frames == {post_base + 3020} then touch_button:set_value(0)
    elseif frames == {post_base + 3300} then press(118, 237)
    elseif frames == {post_base + 3320} then touch_button:set_value(0)
{key_steps}
    elseif frames == {post_base + 4700} then
        machine.screens[":screen"]:snapshot("browser-url-entered.png")
    elseif frames == {post_base + 4800} then press(419, 143)
    elseif frames == {post_base + 4820} then touch_button:set_value(0)
    elseif frames == {post_base + 5100} then
        machine.screens[":screen"]:snapshot("browser-go-pressed.png")
    elseif frames == {post_base + 7000} then
        machine.screens[":screen"]:snapshot("browser-loading.png")
    elseif frames == {exit_frame - 120} then
        machine.screens[":screen"]:snapshot("browser-result.png")
"""
    elif probe_package:
        post_install = f"""    elseif frames == {post_base + 1400} then
        machine.screens[":screen"]:snapshot("pclink-disconnected.png")
    elseif frames == {post_base + 1650} then press(270, 220)
    elseif frames == {post_base + 1670} then touch_button:set_value(0)
    elseif frames == {post_base + 1850} then
        machine.screens[":screen"]:snapshot("package-opened.png")
    elseif frames == {post_base + 1900} then press(440, 10)
    elseif frames == {post_base + 1920} then touch_button:set_value(0)
    elseif frames == {post_base + 2100} then
        machine.screens[":screen"]:snapshot("post-package-storeroom.png")
    elseif frames == {post_base + 2200} then press(440, 10)
    elseif frames == {post_base + 2220} then touch_button:set_value(0)
    elseif frames == {post_base + 2400} then
        machine.screens[":screen"]:snapshot("post-package-hallway.png")
    elseif frames == {post_base + 2500} then press(440, 10)
    elseif frames == {post_base + 2520} then touch_button:set_value(0)
    elseif frames == {post_base + 2700} then
        machine.screens[":screen"]:snapshot("post-package-downtown.png")
    elseif frames == {post_base + 2800} then press(260, 200)
    elseif frames == {post_base + 2820} then touch_button:set_value(0)
    elseif frames == {post_base + 3000} then
        machine.screens[":screen"]:snapshot("downtown-directory.png")
{save_clause.rstrip()}
"""
    else:
        post_install = f"""    elseif frames == {post_base + 1400} then
        machine.screens[":screen"]:snapshot("pclink-disconnected.png")
"""
    if owner_first_name is not None:
        assert owner_last_name is not None
        navigation_steps = lua_provider_first_run_navigation(
            snapshot_frame,
            owner_first_name,
            owner_last_name,
        )
    elif internet_center_start:
        navigation_steps = f"""    if frames == 1300 then press(413, 61)
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
    else:
        navigation_steps = f"""    if frames == 1300 then press(413, 61)
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
    return f"""local machine = manager.machine
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local frames = 0
local package_snapshotted = false
local package_ready_frame = nil

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
    service: Callable[[], None] | None = None,
) -> None:
    view = memoryview(data)
    while view:
        drain_process_output(process, output)
        if service is not None:
            service()
        if not select.select([], [fd], [], 0.05)[1]:
            continue
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
            "copy an external NVRAM directory into the isolated run and "
            "use provider navigation"
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
        "--owner-first-name",
        help="letters-only first name for provider first-run setup",
    )
    parser.add_argument(
        "--owner-last-name",
        help="letters-only last name for provider first-run setup",
    )
    parser.add_argument(
        "--connect-only",
        action="store_true",
        help="diagnose a PCLink connect/disconnect without sending a package",
    )
    parser.add_argument(
        "--combined-browser-acceptance",
        action="store_true",
        help=(
            "continue in the same MAME process through Web Browser, PPP, "
            "and deterministic local HTTP"
        ),
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=8080,
        help="local combined-acceptance HTTP port (default: 8080)",
    )
    parser.add_argument(
        "--slirp",
        default="slirp",
        help="classic Slirp executable (default: slirp)",
    )
    parser.add_argument(
        "--bubblewrap",
        default="bwrap",
        help="Bubblewrap executable used to isolate Slirp's hostname",
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=57_600,
        help="Slirp link pacing rate (default: 57600)",
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
    provider_source = nvram_source is not None
    combined_browser = args.combined_browser_acceptance
    probe_package = args.probe_package or combined_browser

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
    if probe_package and not provider_source:
        print(
            "error: package probing requires a provider source",
            file=sys.stderr,
        )
        return 2
    if args.internet_center_source and not provider_source:
        print(
            "error: --internet-center-source requires a provider source",
            file=sys.stderr,
        )
        return 2
    if (args.owner_first_name is None) != (args.owner_last_name is None):
        print(
            "error: --owner-first-name and --owner-last-name are required "
            "together",
            file=sys.stderr,
        )
        return 2
    if (
        args.owner_first_name is not None
        and not provider_source
    ):
        print(
            "error: owner-name setup requires --nvram-source",
            file=sys.stderr,
        )
        return 2
    if (
        args.owner_first_name is not None
        and args.internet_center_source
    ):
        print(
            "error: owner-name setup and --internet-center-source are "
            "mutually exclusive",
            file=sys.stderr,
        )
        return 2
    if args.owner_first_name is not None:
        try:
            name_card_key_steps(args.owner_first_name, 0)
            name_card_key_steps(args.owner_last_name or "", 0)
        except ValueError as caught:
            print(f"error: {caught}", file=sys.stderr)
            return 2
    if probe_package and args.connect_only:
        print(
            "error: package probing cannot be combined with --connect-only",
            file=sys.stderr,
        )
        return 2
    if combined_browser:
        if shutil.which(args.slirp) is None:
            print("error: classic Slirp is required", file=sys.stderr)
            return 2
        if shutil.which(args.bubblewrap) is None:
            print("error: Bubblewrap is required", file=sys.stderr)
            return 2
        if not 1 <= args.http_port <= 65_535:
            print(
                "error: --http-port must be between 1 and 65535",
                file=sys.stderr,
            )
            return 2
        if args.baudrate <= 0:
            print("error: --baudrate must be positive", file=sys.stderr)
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
        provider_first_run_pclink_frame(
            args.owner_first_name or "",
            args.owner_last_name or "",
        )
        if args.owner_first_name is not None
        else 5100 if args.internet_center_source
        else 3500 if provider_source else 2750
    )
    snapshot_frame = navigation_frame + ceil(wire_seconds * 60) + 120
    post_frames = (
        11_120
        if combined_browser
        else 3100 if probe_package else 1600
    )
    exit_frame = (
        snapshot_frame
        + post_frames
        + (PACKAGE_SETTLE_FRAMES if provider_source else 0)
    )
    lua_path = workdir / "pclink.lua"
    post_install_state = workdir / "post-install.sta"
    package_ready_path = workdir / "package-ready"
    package_snapshotted_path = workdir / "package-snapshotted"
    lua_path.write_text(
        (
            lua_warm_provider_navigation(
                snapshot_frame=snapshot_frame,
                exit_frame=exit_frame,
                probe_package=probe_package,
                save_path=post_install_state
                if probe_package and not combined_browser
                else None,
                package_ready_path=package_ready_path,
                package_snapshotted_path=package_snapshotted_path,
                internet_center_start=args.internet_center_source,
                browser_acceptance=combined_browser,
                http_port=args.http_port,
                owner_first_name=args.owner_first_name,
                owner_last_name=args.owner_last_name,
            )
            if provider_source
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
        "-videodriver",
        "dummy",
        "-audiodriver",
        "dummy",
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
    if combined_browser or args.owner_first_name is not None:
        command.extend(["-pccard1", "modem"])
    if probe_package or args.owner_first_name is not None:
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
    modem_fd: int | None = None
    modem_session: CombinedModemSession | None = None
    error: str | None = None
    try:
        assert process.stdout is not None
        deadline = time.monotonic() + 30
        pty_path = None
        modem_pty_path = None
        while time.monotonic() < deadline:
            if select.select([process.stdout], [], [], 0.25)[0]:
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                output.extend(chunk)
                match = PTY_PATTERN.search(output)
                if match:
                    pty_path = match.group(1).decode()
                modem_match = MODEM_PTY_PATTERN.search(output)
                if modem_match:
                    modem_pty_path = modem_match.group(1).decode()
                if pty_path is not None and (
                    not combined_browser or modem_pty_path is not None
                ):
                    break
        if pty_path is None:
            raise RuntimeError("MAME did not announce its PCLink PTY")
        if combined_browser and modem_pty_path is None:
            raise RuntimeError("MAME did not announce its modem PTY")

        fd = os.open(pty_path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        configure_raw_pty(fd)
        if modem_pty_path is not None:
            modem_fd = os.open(
                modem_pty_path,
                os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK,
            )
            configure_raw_pty(modem_fd)
            modem_session = CombinedModemSession(
                modem_fd,
                modem_pty_path,
                workdir,
                args.slirp,
                args.bubblewrap,
                args.baudrate,
                args.http_port,
            )
            modem_fd = None

        connect_stream = None
        connect_timeout = (
            max(180, (navigation_frame // 20) + 60)
            if args.owner_first_name is not None
            else 180
        )
        deadline = time.monotonic() + connect_timeout
        while time.monotonic() < deadline:
            drain_process_output(process, output)
            if modem_session is not None:
                modem_session.service()
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
        modem_service = (
            modem_session.service if modem_session is not None else None
        )
        write_all(fd, bytes(host_wire), process, output, modem_service)

        pause_deadline = time.monotonic() + 0.5
        while time.monotonic() < pause_deadline:
            if modem_service is not None:
                modem_service()
            time.sleep(0.05)
        if not args.connect_only:
            host_wire.extend(metadata_wire)
            host_wire.extend(package_wire)
            write_all(fd, metadata_wire, process, output, modem_service)
            write_all(fd, package_wire, process, output, modem_service)
        ping_wire = encode_packet(PING_TAG)
        pong_wire = encode_packet(PONG_TAG)
        goodbye_wire = encode_packet(GOODBYE_TAG)
        host_wire.extend(ping_wire)
        write_all(fd, ping_wire, process, output, modem_service)
        deadline = time.monotonic() + max(60, exit_frame // 30)
        while process.poll() is None and time.monotonic() < deadline:
            drain_process_output(process, output)
            if modem_service is not None:
                modem_service()
            if (
                fd is not None
                and not disconnect_completed
                and pong_seen
                and (
                    not provider_source
                    or package_snapshotted_path.is_file()
                )
            ):
                # WinPcLink's documented Close Connection operation is
                # initiated on the PC. Send the normal GBye request and leave
                # the serial endpoint open long enough for Magic Cap to
                # consume it; the surrounding MAME run owns the PTY lifetime.
                host_wire.extend(goodbye_wire)
                write_all(
                    fd,
                    goodbye_wire,
                    process,
                    output,
                    modem_service,
                )
                disconnect_completed = True
            if fd is not None and select.select([fd], [], [], 0.05)[0]:
                device_wire.extend(read_available(fd))
                trailing = bytes(device_wire[connect_wire_length:])
                if pong_wire in trailing:
                    pong_seen = True
                    if provider_source:
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
        if modem_fd is not None:
            try:
                os.close(modem_fd)
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
        if modem_session is not None:
            try:
                modem_session.close()
            except OSError as caught:
                if error is None:
                    error = f"unable to finalize modem evidence: {caught}"

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
    if (
        probe_package
        and not combined_browser
        and not post_install_state.is_file()
    ):
        print(
            f"error: post-install state not produced: {post_install_state}",
            file=sys.stderr,
        )
        return 1
    if probe_package:
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
    if combined_browser:
        browser_screenshot = workdir / "snapshots" / "browser-result.png"
        if not browser_screenshot.is_file():
            print(
                f"error: browser result screenshot not produced: "
                f"{browser_screenshot}",
                file=sys.stderr,
            )
            return 1
        assert modem_session is not None
        modem_error = modem_session.validation_error()
        if modem_error is not None:
            print(f"error: {modem_error}; see {workdir}", file=sys.stderr)
            return 1

    if args.connect_only:
        print("PASS: completed a PCLink connect/disconnect cycle")
    elif combined_browser:
        print(
            "PASS: installed Web Browser through PCLink and fetched "
            "the local HTTP page over PPP"
        )
        print(f"Browser screenshot: {browser_screenshot}")
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
