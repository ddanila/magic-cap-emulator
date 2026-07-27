#!/usr/bin/env python3
"""Exercise Magic Cap HTTP over the emulated EtherLink III and libslirp."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"

DEFAULT_BODY = b"""<!doctype html>
<html>
<head><title>EtherLink OK</title></head>
<body><h1>EtherLink III works</h1>
<p>Magic Cap reached deterministic local HTTP.</p></body>
</html>
"""


def _lua_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def automation_script(
    marker: Path,
    url: str,
    max_frames: int = 9000,
    *,
    startup_close: tuple[int, int] = (413, 60),
    result_wait_frames: int = 600,
) -> str:
    """Return cold-boot browser automation with an event-driven exit."""
    return f"""local machine = manager.machine
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local marker = {_lua_quote(str(marker))}
local frames = 0
local request_frame = nil

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

local function release()
    touch_button:set_value(0)
end

local function marker_exists()
    local file = io.open(marker, "rb")
    if not file then
        return false
    end
    file:close()
    return true
end

emu.register_frame_done(function()
    frames = frames + 1
    if frames == 1800 then
        -- Close the retained browser's startup slip or alert.
        press({startup_close[0]}, {startup_close[1]})
    elseif frames == 1820 then
        release()
    elseif frames == 2100 then
        -- Open the browser's Go To slip.
        press(450, 48)
    elseif frames == 2120 then
        release()
    elseif frames == 2400 then
        emu.keypost({_lua_quote(url)})
    elseif frames == 3400 then
        machine.screens[":screen"]:snapshot("etherlink-url-entered.png")
        press(419, 143)
    elseif frames == 3420 then
        release()
    end

    if frames > 3420 and not request_frame and marker_exists() then
        request_frame = frames
    end
    if request_frame and frames == request_frame + {result_wait_frames} then
        machine.screens[":screen"]:snapshot("etherlink-http-result.png")
        machine:exit()
    elseif frames == {max_frames} then
        machine.screens[":screen"]:snapshot("etherlink-http-timeout.png")
        machine:exit()
    end
end)
"""


def config_xml(system: str) -> str:
    """Select libslirp interface zero for the emulated PC Card."""
    return f"""<?xml version="1.0"?>
<mameconfig version="10">
    <system name="{system}">
        <input>
            <keyboard tag=":magicbus_keyboard" enabled="1" />
            <keyboard tag=":terminal:keyboard" enabled="0" />
        </input>
        <network>
            <device tag=":pccard1:3c589"
                    interface="0"
                    mac="60:02:12:8c:56:34" />
        </network>
    </system>
</mameconfig>
"""


def resolve_nvram_source(source: Path, system: str) -> Path:
    """Accept either an NVRAM root or its system-specific child."""
    source = source.expanduser().resolve()
    candidate = source / system
    if (candidate / "ram").is_file():
        return source
    if source.name == system and (source / "ram").is_file():
        return source.parent
    raise ValueError(
        f"{source} does not contain {system}/ram and is not that directory"
    )


class _RequestServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        marker: Path,
        request_log: Path,
        expected_target: str | None = None,
        body: bytes = DEFAULT_BODY,
    ) -> None:
        self.marker = marker
        self.request_log = request_log
        self.expected_target = expected_target
        self.body = body
        self.request_seen = threading.Event()
        super().__init__(address, _RequestHandler)


class _RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server: _RequestServer

    def do_GET(self) -> None:
        line = f"GET {self.path} {self.request_version}\n"
        with self.server.request_log.open("a", encoding="utf-8") as output:
            output.write(line)
        if (
            self.server.expected_target is None
            or self.path == self.server.expected_target
        ):
            self.server.marker.write_text(line, encoding="utf-8")
            self.server.request_seen.set()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=us-ascii")
        self.send_header("Content-Length", str(len(self.server.body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(self.server.body)

    def log_message(self, format: str, *args: object) -> None:
        return


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
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
        default=ASSETS_ROOT / "roms",
        help="MAME ROM search root",
    )
    parser.add_argument(
        "--nvram-source",
        type=Path,
        required=True,
        help="provider-configured NVRAM root or datarover840 directory",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=ASSETS_ROOT / "runtime" / "etherlink-regression",
        help="persistent artifact root",
    )
    parser.add_argument("--system", default="datarover840")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--max-frames",
        type=int,
        default=9000,
        help="emulated-frame timeout (default: 9000)",
    )
    parser.add_argument(
        "--card-trace",
        action="store_true",
        help="route MAME device log messages into mame-output.txt",
    )
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    workdir = args.workdir.expanduser().resolve()
    try:
        nvram_source = resolve_nvram_source(args.nvram_source, args.system)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if not mame.is_file():
        print(f"error: MAME executable not found: {mame}", file=sys.stderr)
        return 2
    if not rompath.is_dir():
        print(f"error: ROM path not found: {rompath}", file=sys.stderr)
        return 2
    if not 1 <= args.port <= 65535:
        print("error: port must be between 1 and 65535", file=sys.stderr)
        return 2
    if args.max_frames <= 3420:
        print("error: max frames must exceed 3420", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = workdir / f"{stamp}-{os.getpid()}"
    cfg_dir = run_dir / "cfg"
    nvram_dir = run_dir / "nvram"
    snapshot_dir = run_dir / "snapshots"
    cfg_dir.mkdir(parents=True)
    snapshot_dir.mkdir()
    shutil.copytree(nvram_source, nvram_dir)

    marker = run_dir / "http-request-seen"
    request_log = run_dir / "http-requests.txt"
    lua_path = run_dir / "etherlink-regression.lua"
    output_path = run_dir / "mame-output.txt"
    (cfg_dir / f"{args.system}.cfg").write_text(
        config_xml(args.system),
        encoding="utf-8",
    )
    lua_path.write_text(
        automation_script(
            marker,
            f"10.0.2.2:{args.port}/",
            args.max_frames,
        ),
        encoding="utf-8",
    )

    try:
        server = _RequestServer(
            ("127.0.0.1", args.port),
            marker,
            request_log,
            f"http://10.0.2.2:{args.port}/",
        )
    except OSError as error:
        print(f"error: cannot listen on 127.0.0.1:{args.port}: {error}", file=sys.stderr)
        return 2

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
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
        "-pccard1",
        "3c589",
        "-networkprovider",
        "slirp",
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
    if args.card_trace:
        command.append("-oslog")

    print(f"artifacts: {run_dir}", flush=True)
    print(
        f"HTTP endpoint: 10.0.2.2:{args.port} -> 127.0.0.1:{args.port}",
        flush=True,
    )
    try:
        with output_path.open("wb") as output:
            completed = subprocess.run(
                command,
                cwd=mame.parent,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=args.timeout,
            )
    except subprocess.TimeoutExpired:
        print(f"FAIL: MAME exceeded {args.timeout:g} seconds", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"error: unable to run MAME: {error}", file=sys.stderr)
        return 2
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()

    if completed.returncode:
        print(f"FAIL: MAME exited with status {completed.returncode}", file=sys.stderr)
        return 1
    if not server.request_seen.is_set():
        print("FAIL: Magic Cap did not send an HTTP request", file=sys.stderr)
        return 1
    result_snapshot = snapshot_dir / "etherlink-http-result.png"
    if not result_snapshot.is_file():
        print("FAIL: rendered-result snapshot is missing", file=sys.stderr)
        return 1
    print(request_log.read_text(encoding="utf-8").rstrip())
    print("PASS: Magic Cap completed TCP and rendered deterministic local HTTP")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run_regression(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
