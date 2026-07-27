#!/usr/bin/env python3
"""Launch Magic Cap with a Hayes modem front end and a Slirp PPP backend."""

from __future__ import annotations

import argparse
import fcntl
import http.server
import os
import re
import select
import shutil
import subprocess
import sys
import termios
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_STATE = (
    ASSETS_ROOT
    / "runtime"
    / "state-card-load"
    / "pc-card-only.sta"
)
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "modem-bridge"
PTY_PATTERN = re.compile(rb":pccard1:modem PTY: (/[^\r\n]+)")
PPP_FLAG = 0x7E
PPP_ESCAPE = 0x7D
PPP_ESCAPE_XOR = 0x20
PPP_LCP = 0xC021


@dataclass(frozen=True)
class HayesEvent:
    """A complete command received from the emulated modem UART."""

    command: str
    response: bytes
    dial: bool


class HayesNegotiator:
    """Small command-mode modem sufficient to reach Magic Cap's PPP stack."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.echo = True

    def feed(self, data: bytes) -> list[HayesEvent]:
        """Consume serial bytes and return events for complete CR commands."""
        self._buffer.extend(data)
        events: list[HayesEvent] = []
        while b"\r" in self._buffer:
            raw, _, remainder = self._buffer.partition(b"\r")
            self._buffer[:] = remainder
            command = raw.strip(b"\n").decode("ascii", "replace")
            if not command:
                continue

            upper = command.upper()
            dial = upper.startswith("ATD")
            response = b""
            if self.echo:
                response += command.encode("ascii", "replace") + b"\r"
            if not dial:
                response += b"\r\nOK\r\n"

            # ATE0 is part of Magic Cap's first compound initialization
            # command.  Echo that command, then disable echo for later ones.
            if upper.startswith("AT") and "E0" in upper:
                self.echo = False
            elif upper.startswith("AT") and "E1" in upper:
                self.echo = True
            events.append(HayesEvent(command, response, dial))
        return events


def ppp_frames(data: bytes) -> list[bytes]:
    """Extract and unescape complete async-HDLC PPP frames."""
    frames: list[bytes] = []
    frame = bytearray()
    escaped = False
    inside = False
    for value in data:
        if value == PPP_FLAG:
            if inside and frame:
                frames.append(bytes(frame))
            frame.clear()
            escaped = False
            inside = True
        elif not inside:
            continue
        elif escaped:
            frame.append(value ^ PPP_ESCAPE_XOR)
            escaped = False
        elif value == PPP_ESCAPE:
            escaped = True
        else:
            frame.append(value)
    return frames


def ppp_protocol(frame: bytes) -> int | None:
    """Return a PPP protocol from an unescaped frame, ignoring its FCS."""
    if frame.startswith(b"\xff\x03") and len(frame) >= 4:
        return int.from_bytes(frame[2:4], "big")
    if frame and (frame[0] & 1):
        return frame[0]
    if len(frame) >= 2:
        return int.from_bytes(frame[:2], "big")
    return None


def configure_raw_pty(fd: int) -> None:
    """Put a PTY slave into unprocessed eight-bit mode."""
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


def classic_slirp_tty(pty_path: str) -> str:
    """Format SLIRP_TTY for Debian's classic Slirp 1.0.17.

    That release unconditionally removes the last byte as though the value
    came from a newline-terminated input line.  Supplying the terminator keeps
    it from truncating the final digit of /dev/pts/N.
    """
    return pty_path + "\n"


def autodial_script(exit_frame: int | None) -> str:
    """Click Mail in the saved Phone-line panel and optionally stop MAME."""
    exit_clause = ""
    if exit_frame is not None:
        exit_clause = (
            f'    elseif frames == {exit_frame - 120} then\n'
            '        machine.screens[":screen"]:snapshot("ppp-connected.png")\n'
            f'    elseif frames == {exit_frame} then machine:exit()\n'
        )
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
    if frames == 500 then press(320, 164)
    elseif frames == 520 then touch_button:set_value(0)
{exit_clause}    end
end)
"""


def browser_prepare_script(port: int, save_path: Path) -> str:
    """Open Web Browser 4.0, enter the local URL, and save that state."""
    key_positions = {
        **{
            digit: (26 + (index * 43), 198)
            for index, digit in enumerate("1234567890")
        },
        ".": (391, 270),
        ":": (262, 270),
        "/": (434, 270),
    }
    address = f"10.0.2.2:{port}/"
    address_keys = ", ".join(
        f"{{{key_positions[character][0]}, "
        f"{key_positions[character][1]}}}"
        for character in address
    )
    quoted_save_path = (
        str(save_path).replace("\\", "\\\\").replace('"', '\\"')
    )
    navigation = """    if frames == 300 then
        machine.screens[":screen"]:snapshot("browser-state-loaded.png")
    elseif frames == 500 then press(301, 110)
    elseif frames == 520 then touch_button:set_value(0)
    elseif frames == 800 then press(440, 10)
    elseif frames == 820 then touch_button:set_value(0)
    elseif frames == 1100 then press(60, 130)
    elseif frames == 1120 then touch_button:set_value(0)
    elseif frames == 1400 then press(270, 220)
    elseif frames == 1420 then touch_button:set_value(0)
    elseif frames == 1600 then
        machine.screens[":screen"]:snapshot("browser-package-opened.png")
    elseif frames == 1700 then press(451, 148)
    elseif frames == 1720 then touch_button:set_value(0)
    elseif frames == 1900 then
        machine.screens[":screen"]:snapshot("browser-scene-opened.png")
    elseif frames == 2000 then press(126, 80)
    elseif frames == 2020 then touch_button:set_value(0)
    elseif frames == 2400 then press(450, 45)
    elseif frames == 2420 then touch_button:set_value(0)
    elseif frames == 2700 then press(120, 302)
    elseif frames == 2720 then touch_button:set_value(0)
    elseif frames == 3000 then press(118, 237)
    elseif frames == 3020 then touch_button:set_value(0)"""
    address_start = 3300
    snapshot_frame = 4450
    save_frame = 4500
    exit_frame = 4600
    return f"""local machine = manager.machine
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local frames = 0
local address_keys = {{ {address_keys} }}

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

emu.register_frame_done(function()
    frames = frames + 1
{navigation}
    elseif frames == {snapshot_frame} then
        machine.screens[":screen"]:snapshot("browser-url-entered.png")
    elseif frames == {save_frame} then
        machine:save("{quoted_save_path}")
    elseif frames == {exit_frame} then
        machine:exit()
    end

    for index, position in ipairs(address_keys) do
        local start = {address_start} + ((index - 1) * 60)
        if frames == start then
            press(position[1], position[2])
        elseif frames == start + 20 then
            touch_button:set_value(0)
        end
    end
end)
"""


def browser_acceptance_script(exit_frame: int) -> str:
    """Dial from a prepared Web Browser URL state and capture its result."""
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
    if frames == 300 then
        machine.screens[":screen"]:snapshot("browser-ready.png")
    elseif frames == 2000 then press(419, 143)
    elseif frames == 2020 then touch_button:set_value(0)
    elseif frames == 2300 then
        machine.screens[":screen"]:snapshot("browser-go-pressed.png")
    elseif frames == 4000 then
        machine.screens[":screen"]:snapshot("browser-dialing.png")
    elseif frames == 6000 then
        machine.screens[":screen"]:snapshot("browser-loading.png")
    elseif frames == {exit_frame - 120} then
        machine.screens[":screen"]:snapshot("browser-result.png")
    elseif frames == {exit_frame} then
        machine:exit()
    end
end)
"""


def start_acceptance_http_server(
    port: int,
) -> tuple[http.server.ThreadingHTTPServer, threading.Thread, list[str]]:
    """Start the deterministic browser-acceptance HTTP endpoint."""
    requests: list[str] = []
    body = (
        b"<html><head><title>Magic Cap PPP</title></head>"
        b"<body><h1>Magic Cap is online</h1>"
        b"<p>Web Browser 4.0 reached the host through Slirp PPP.</p>"
        b"</body></html>\n"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self) -> None:
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            return

    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, requests


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
        "--state",
        type=Path,
        default=DEFAULT_STATE,
        help=f"external configured guest state (default: {DEFAULT_STATE})",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=DEFAULT_WORKDIR,
        help=f"persistent artifact root (default: {DEFAULT_WORKDIR})",
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
    parser.add_argument(
        "--probe",
        action="store_true",
        help="capture the guest's first PPP LCP frame without starting Slirp",
    )
    parser.add_argument(
        "--acceptance",
        action="store_true",
        help="run a finite headless live-Slirp check and capture the guest UI",
    )
    parser.add_argument(
        "--browser-acceptance",
        action="store_true",
        help="load a post-install browser state and verify local HTTP over PPP",
    )
    parser.add_argument(
        "--browser-ready",
        action="store_true",
        help=(
            "with --browser-acceptance, treat --state as an already entered "
            "URL checkpoint and skip the preparation relaunch"
        ),
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=8080,
        help="local browser-acceptance HTTP port (default: 8080)",
    )
    parser.add_argument(
        "--no-autodial",
        action="store_true",
        help="do not click Mail automatically after loading the saved state",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="disable video and sound (implied by --probe)",
    )
    return parser.parse_args(argv)


def _validate(
    args: argparse.Namespace,
) -> tuple[Path, Path, Path, Path] | None:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    state = args.state.expanduser().resolve()
    artifact_root = args.workdir.expanduser().resolve()
    inputs = [
        ("MAME executable", mame, "file"),
        ("ROM path", rompath, "directory"),
        ("guest state", state, "file"),
    ]
    for label, path, kind in inputs:
        valid = path.is_file() if kind == "file" else path.is_dir()
        if not valid:
            print(f"error: {label} not found: {path}", file=sys.stderr)
            return None
    if not args.probe and shutil.which(args.slirp) is None:
        print(
            "error: classic Slirp is required; install it with "
            "`sudo apt install slirp`",
            file=sys.stderr,
        )
        return None
    if not args.probe and shutil.which(args.bubblewrap) is None:
        print(
            "error: Bubblewrap is required; install it with "
            "`sudo apt install bubblewrap`",
            file=sys.stderr,
        )
        return None
    if args.baudrate <= 0:
        print("error: --baudrate must be positive", file=sys.stderr)
        return None
    if sum((args.probe, args.acceptance, args.browser_acceptance)) > 1:
        print(
            "error: --probe, --acceptance, and --browser-acceptance are "
            "mutually exclusive",
            file=sys.stderr,
        )
        return None
    if (args.acceptance or args.browser_acceptance) and args.no_autodial:
        print(
            "error: acceptance modes require automatic dialing",
            file=sys.stderr,
        )
        return None
    if args.browser_ready and not args.browser_acceptance:
        print(
            "error: --browser-ready requires --browser-acceptance",
            file=sys.stderr,
        )
        return None
    if not 1 <= args.http_port <= 65_535:
        print("error: --http-port must be between 1 and 65535", file=sys.stderr)
        return None
    return mame, rompath, state, artifact_root


def _drain_stream(stream, sink) -> bytes:
    try:
        data = os.read(stream.fileno(), 65_536)
    except BlockingIOError:
        return b""
    if data:
        sink.write(data)
        sink.flush()
    return data


def prepare_browser_state(
    mame: Path,
    rompath: Path,
    source_state: Path,
    run_dir: Path,
    port: int,
) -> Path:
    """Enter the browser URL while answering the inserted modem's Hayes I/O."""
    prepared_state = run_dir / "browser-ready.sta"
    lua_path = run_dir / "browser-prepare.lua"
    lua_path.write_text(
        browser_prepare_script(port, prepared_state),
        encoding="utf-8",
    )
    command = browser_prepare_command(
        mame, rompath, source_state, run_dir, lua_path
    )
    log_path = run_dir / "browser-prepare-output.txt"
    transcript_path = run_dir / "browser-prepare-modem.txt"
    process: subprocess.Popen[bytes] | None = None
    pty_fd: int | None = None
    try:
        with (
            log_path.open("wb") as log,
            transcript_path.open("w", encoding="utf-8") as transcript,
        ):
            process = subprocess.Popen(
                command,
                cwd=mame.parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            assert process.stdout is not None
            set_nonblocking(process.stdout.fileno())
            output = bytearray()
            deadline = time.monotonic() + 30
            pty_path: str | None = None
            while time.monotonic() < deadline and process.poll() is None:
                if select.select([process.stdout], [], [], 0.1)[0]:
                    chunk = _drain_stream(process.stdout, log)
                    output.extend(chunk)
                    match = PTY_PATTERN.search(output)
                    if match:
                        pty_path = match.group(1).decode()
                        break
            if pty_path is None:
                raise RuntimeError(
                    "browser preparation did not announce its modem PTY"
                )

            pty_fd = os.open(
                pty_path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK
            )
            configure_raw_pty(pty_fd)
            negotiator = HayesNegotiator()
            deadline = time.monotonic() + 180
            while process.poll() is None and time.monotonic() < deadline:
                ready, _, _ = select.select(
                    [process.stdout, pty_fd], [], [], 0.05
                )
                if process.stdout in ready:
                    _drain_stream(process.stdout, log)
                if pty_fd in ready:
                    chunk = os.read(pty_fd, 65_536)
                    for event in negotiator.feed(chunk):
                        transcript.write(f"HAYES {event.command}\n")
                        transcript.flush()
                        if event.dial:
                            raise RuntimeError(
                                "browser preparation dialed before acceptance"
                            )
                        if event.response:
                            time.sleep(0.10)
                            os.write(pty_fd, event.response)
            if process.poll() is None:
                raise RuntimeError("browser state preparation timed out")
            process.wait()
    finally:
        if pty_fd is not None:
            os.close(pty_fd)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    if process is None or process.returncode:
        raise RuntimeError(
            f"browser state preparation exited with status "
            f"{None if process is None else process.returncode}"
        )
    if not prepared_state.is_file():
        raise RuntimeError("browser state preparation produced no save state")
    return prepared_state


def browser_prepare_command(
    mame: Path,
    rompath: Path,
    source_state: Path,
    run_dir: Path,
    lua_path: Path,
) -> list[str]:
    """Build the bridged-modem URL-preparation launch command."""
    command = [
        str(mame),
        "datarover840",
        "-rompath",
        str(rompath),
        "-pccard1",
        "modem",
        "-cfg_directory",
        str(run_dir / "cfg"),
        "-snapshot_directory",
        str(run_dir / "snapshots"),
        "-snapview",
        "native",
        "-skip_gameinfo",
        "-oslog",
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
    ]
    command.extend(["-state", str(source_state), "-rs2321", "pty"])
    return command


def run_bridge(args: argparse.Namespace) -> int:
    paths = _validate(args)
    if paths is None:
        return 2
    mame, rompath, state, artifact_root = paths

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = artifact_root / f"{stamp}-{os.getpid()}"
    (run_dir / "cfg").mkdir(parents=True)
    (run_dir / "snapshots").mkdir()
    lua_path: Path | None = None
    if args.browser_acceptance:
        if not args.browser_ready:
            try:
                state = prepare_browser_state(
                    mame,
                    rompath,
                    state,
                    run_dir,
                    args.http_port,
                )
            except (OSError, RuntimeError) as caught:
                print(
                    f"error: {caught}; see {run_dir}",
                    file=sys.stderr,
                )
                return 1
        lua_path = run_dir / "browser-acceptance.lua"
        lua_path.write_text(
            browser_acceptance_script(10000),
            encoding="utf-8",
        )
    elif not args.no_autodial:
        lua_path = run_dir / "autodial.lua"
        exit_frame = 3600 if args.probe else 6000 if args.acceptance else None
        lua_path.write_text(
            autodial_script(exit_frame), encoding="utf-8"
        )
    slirp_config_path = run_dir / "slirp.rc"
    # Despite `help debugppp` claiming that it accepts a filename, classic
    # Slirp 1.0.17 always writes this fixed basename in its working directory.
    ppp_debug_path = run_dir / "slirp_pppdebug"
    if not args.probe:
        slirp_config_path.write_text(
            "debugppp ppp-debug.txt\n",
            encoding="utf-8",
        )

    command = [
        str(mame),
        "datarover840",
        "-rompath",
        str(rompath),
        "-state",
        str(state),
        "-pccard1",
        "modem",
        "-cfg_directory",
        str(run_dir / "cfg"),
        "-snapshot_directory",
        str(run_dir / "snapshots"),
        "-snapview",
        "native",
        "-skip_gameinfo",
        "-oslog",
    ]
    if args.browser_acceptance:
        # The post-install save state was captured with the live PCLink
        # serial device.  The modem is intentionally inserted only for this
        # bridged launch; preparing the URL with an unanswered modem PTY
        # leaves Magic Cap's modem actor in a failed state.
        command.extend(["-rs2321", "pty"])
    if lua_path is not None:
        command.extend(
            ["-autoboot_delay", "0", "-autoboot_script", str(lua_path)]
        )
    if (
        args.probe
        or args.acceptance
        or args.browser_acceptance
        or args.headless
    ):
        command.extend([
            "-video", "none", "-sound", "none",
            "-videodriver", "dummy", "-audiodriver", "dummy",
            "-nothrottle",
        ])

    mame_log_path = run_dir / "mame-output.txt"
    modem_log_path = run_dir / "modem-transcript.txt"
    slirp_log_path = run_dir / "slirp-output.txt"
    guest_wire_path = run_dir / "guest-wire.bin"
    host_wire_path = run_dir / "host-wire.bin"
    guest_wire = bytearray()
    host_wire = bytearray()
    slirp_process: subprocess.Popen[bytes] | None = None
    pty_fd: int | None = None
    error: str | None = None
    lcp_seen = False
    http_server: http.server.ThreadingHTTPServer | None = None
    http_thread: threading.Thread | None = None
    http_requests: list[str] = []

    if args.browser_acceptance:
        try:
            http_server, http_thread, http_requests = (
                start_acceptance_http_server(args.http_port)
            )
        except OSError as caught:
            print(
                f"error: unable to start HTTP server on port "
                f"{args.http_port}: {caught}",
                file=sys.stderr,
            )
            return 2

    try:
        process = subprocess.Popen(
            command,
            cwd=mame.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as caught:
        if http_server is not None:
            http_server.shutdown()
            http_server.server_close()
        if http_thread is not None:
            http_thread.join(timeout=5)
        print(f"error: unable to run MAME: {caught}", file=sys.stderr)
        return 2

    try:
        assert process.stdout is not None
        set_nonblocking(process.stdout.fileno())
        with (
            mame_log_path.open("wb") as mame_log,
            modem_log_path.open("w", encoding="utf-8") as modem_log,
            slirp_log_path.open("wb") as slirp_log,
        ):
            output = bytearray()
            deadline = time.monotonic() + 30
            pty_path: str | None = None
            while time.monotonic() < deadline and process.poll() is None:
                if select.select([process.stdout], [], [], 0.1)[0]:
                    chunk = _drain_stream(process.stdout, mame_log)
                    output.extend(chunk)
                    match = PTY_PATTERN.search(output)
                    if match:
                        pty_path = match.group(1).decode()
                        break
            if pty_path is None:
                raise RuntimeError("MAME did not announce its modem PTY")

            pty_fd = os.open(
                pty_path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK
            )
            configure_raw_pty(pty_fd)
            negotiator = HayesNegotiator()
            dialed = False
            deadline = (
                time.monotonic()
                + (300 if args.browser_acceptance else 180)
                if args.probe or args.acceptance or args.browser_acceptance
                else float("inf")
            )

            while time.monotonic() < deadline and process.poll() is None:
                readable = [process.stdout]
                if pty_fd is not None:
                    readable.append(pty_fd)
                ready, _, _ = select.select(readable, [], [], 0.05)
                if process.stdout in ready:
                    _drain_stream(process.stdout, mame_log)
                if pty_fd is None or pty_fd not in ready:
                    if slirp_process is not None and slirp_process.poll() is not None:
                        # At the scripted acceptance exit, MAME closes the PTY
                        # and Slirp can finish just before MAME's process status
                        # becomes visible here.
                        snapshot_name = (
                            "browser-result.png"
                            if args.browser_acceptance
                            else "ppp-connected.png"
                        )
                        snapshot = run_dir / "snapshots" / snapshot_name
                        if (
                            args.acceptance or args.browser_acceptance
                        ) and snapshot.is_file():
                            continue
                        raise RuntimeError(
                            f"Slirp exited with status {slirp_process.returncode}"
                        )
                    continue

                chunk = os.read(pty_fd, 65_536)
                if not chunk:
                    continue
                guest_wire.extend(chunk)
                if dialed:
                    frames = ppp_frames(bytes(guest_wire))
                    if any(ppp_protocol(frame) == PPP_LCP for frame in frames):
                        lcp_seen = True
                        if args.probe:
                            break
                    continue

                for event in negotiator.feed(chunk):
                    modem_log.write(f"HAYES {event.command}\n")
                    modem_log.flush()
                    if event.dial:
                        dialed = True
                        if args.probe:
                            time.sleep(0.25)
                            response = b"\r\nCONNECT\r\n"
                            os.write(pty_fd, response)
                            host_wire.extend(response)
                        else:
                            env = os.environ.copy()
                            env["SLIRP_TTY"] = classic_slirp_tty(pty_path)
                            slirp_process = subprocess.Popen(
                                [
                                    args.bubblewrap,
                                    "--ro-bind",
                                    "/",
                                    "/",
                                    "--dev-bind",
                                    "/dev",
                                    "/dev",
                                    "--bind",
                                    str(run_dir),
                                    str(run_dir),
                                    "--unshare-uts",
                                    "--hostname",
                                    "10.0.2.2",
                                    "--die-with-parent",
                                    args.slirp,
                                    "-P",
                                    "-f",
                                    str(slirp_config_path),
                                    "-b",
                                    str(args.baudrate),
                                    "nozeros",
                                ],
                                cwd=run_dir,
                                env=env,
                                stdin=subprocess.DEVNULL,
                                stdout=slirp_log,
                                stderr=subprocess.STDOUT,
                            )
                            # Slirp opens the same slave.  Keep this descriptor
                            # long enough to send CONNECT, then leave all guest
                            # reads to Slirp so no PPP bytes are stolen.
                            time.sleep(0.25)
                            response = b"\r\nCONNECT 14400\r\n"
                            os.write(pty_fd, response)
                            host_wire.extend(response)
                            os.close(pty_fd)
                            pty_fd = None
                        break

                    if event.response:
                        time.sleep(0.10)
                        os.write(pty_fd, event.response)
                        host_wire.extend(event.response)

            if args.probe and not lcp_seen:
                raise RuntimeError("Magic Cap did not emit a PPP LCP frame")
            if (
                args.acceptance or args.browser_acceptance
            ) and process.poll() is None:
                raise RuntimeError("live PPP acceptance timed out")
    except (OSError, RuntimeError) as caught:
        error = str(caught)
    except KeyboardInterrupt:
        if args.probe:
            error = "probe interrupted before a PPP LCP frame was verified"
    finally:
        if pty_fd is not None:
            try:
                os.close(pty_fd)
            except OSError:
                pass
        if slirp_process is not None and slirp_process.poll() is None:
            slirp_process.terminate()
            try:
                slirp_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                slirp_process.kill()
                slirp_process.wait()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if http_server is not None:
            http_server.shutdown()
            http_server.server_close()
        if http_thread is not None:
            http_thread.join(timeout=5)

    guest_wire_path.write_bytes(guest_wire)
    host_wire_path.write_bytes(host_wire)
    (run_dir / "http-requests.txt").write_text(
        "".join(f"{path}\n" for path in http_requests),
        encoding="utf-8",
    )
    if error:
        print(f"error: {error}; see {run_dir}", file=sys.stderr)
        return 1
    if args.probe:
        print("PASS: Magic Cap completed Hayes dialing and emitted PPP LCP")
    elif args.acceptance or args.browser_acceptance:
        slirp_output = slirp_log_path.read_text(
            encoding="utf-8", errors="replace"
        )
        snapshot_path = run_dir / "snapshots" / (
            "browser-result.png"
            if args.browser_acceptance
            else "ppp-connected.png"
        )
        if "SLiRP Ready" not in slirp_output:
            print(
                f"error: Slirp did not become ready; see {run_dir}",
                file=sys.stderr,
            )
            return 1
        if (
            not ppp_debug_path.is_file()
            or "slirppp: PPP is up now"
            not in ppp_debug_path.read_text(
                encoding="utf-8", errors="replace"
            )
        ):
            print(
                f"error: Slirp did not complete IPCP; see {run_dir}",
                file=sys.stderr,
            )
            return 1
        if not snapshot_path.is_file():
            print(
                f"error: guest acceptance snapshot missing; see {run_dir}",
                file=sys.stderr,
            )
            return 1
        if args.browser_acceptance and "/" not in http_requests:
            print(
                f"error: Web Browser did not request the local root page; "
                f"see {run_dir}",
                file=sys.stderr,
            )
            return 1
        if args.browser_acceptance:
            print(
                "PASS: Web Browser 4.0 requested local HTTP through Slirp PPP"
            )
            print(f"HTTP requests: {run_dir / 'http-requests.txt'}")
        else:
            print(
                "PASS: Magic Cap completed a live PPP negotiation with Slirp"
            )
        print(f"Snapshot: {snapshot_path}")
    else:
        print("Modem bridge stopped")
    print(f"Persistent artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_bridge(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
