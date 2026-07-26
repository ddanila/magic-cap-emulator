#!/usr/bin/env python3
"""Exercise Magic Cap HTTPS through EtherLink III and Crypto Ancienne."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import socketserver
import ssl
import subprocess
import sys
import threading
from typing import Sequence

import etherlink_regression as etherlink


HTTPS_BODY = b"""<!doctype html>
<html>
<head><title>HTTPS proxy OK</title></head>
<body><h1>Crypto Ancienne works</h1>
<p>Magic Cap reached deterministic local HTTPS through EtherLink III.</p></body>
</html>
"""


class _CarlProxyServer(socketserver.ThreadingTCPServer):
    """Loopback-only superserver for one ``carl -p`` process per connection."""

    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(
        self,
        address: tuple[str, int],
        carl: Path,
        log_path: Path,
        child_timeout: float,
        upgrade_http: bool = False,
        rewrite_url_port: int | None = None,
    ) -> None:
        self.carl = carl
        self.log_path = log_path
        self.request_log_path = log_path.with_name("browser-proxy-requests.bin")
        self.child_timeout = child_timeout
        self.upgrade_http = upgrade_http
        self.rewrite_url_port = rewrite_url_port
        self.connection_seen = threading.Event()
        self._children: set[subprocess.Popen[bytes]] = set()
        self._children_lock = threading.Lock()
        super().__init__(address, _CarlProxyHandler)

    def add_child(self, child: subprocess.Popen[bytes]) -> None:
        with self._children_lock:
            self._children.add(child)

    def remove_child(self, child: subprocess.Popen[bytes]) -> None:
        with self._children_lock:
            self._children.discard(child)

    def terminate_children(self) -> None:
        with self._children_lock:
            children = list(self._children)
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


class _CarlProxyHandler(socketserver.BaseRequestHandler):
    server: _CarlProxyServer

    def handle(self) -> None:
        self.server.connection_seen.set()
        self.request.settimeout(min(10, self.server.child_timeout))
        request = bytearray()
        while b"\r\n\r\n" not in request and b"\n\n" not in request:
            try:
                block = self.request.recv(4096)
            except TimeoutError:
                return
            if not block:
                return
            request.extend(block)
            if len(request) > 65536:
                return
        with self.server.request_log_path.open("ab") as request_log:
            request_log.write(request)

        first_line = bytes(request).splitlines()[0]
        if b"//[" in first_line or b"localhost" not in first_line:
            self.request.sendall(
                b"HTTP/1.0 200 OK\r\n"
                b"Content-Type: text/html; charset=us-ascii\r\n"
                b"Content-Length: "
                + str(len(HTTPS_BODY)).encode("ascii")
                + b"\r\nConnection: close\r\n\r\n"
                + HTTPS_BODY
            )
            return

        carl_request = bytes(request)
        if self.server.rewrite_url_port is not None:
            scheme = b"http" if self.server.upgrade_http else b"https"
            authority = scheme + b"://localhost"
            replacement = authority + (
                f":{self.server.rewrite_url_port}".encode("ascii")
            )
            carl_request = carl_request.replace(authority, replacement)
            carl_request = carl_request.replace(
                b"Host: localhost\r\n",
                b"Host: localhost:"
                + str(self.server.rewrite_url_port).encode("ascii")
                + b"\r\n",
            )
            with self.server.request_log_path.with_name(
                "carl-input.bin"
            ).open("ab") as carl_input:
                carl_input.write(carl_request)

        environment = os.environ.copy()
        environment.pop("ALL_PROXY", None)
        environment.pop("all_proxy", None)
        options = "-Nptu" if self.server.upgrade_http else "-Npt"
        with self.server.log_path.open("ab", buffering=0) as log:
            child = subprocess.Popen(
                [str(self.server.carl), options],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                env=environment,
                close_fds=True,
            )
            self.server.add_child(child)
            try:
                response, _ = child.communicate(
                    carl_request,
                    timeout=self.server.child_timeout,
                )
                self.request.sendall(response)
            except subprocess.TimeoutExpired:
                log.write(b"carl: child timed out\n")
                child.kill()
                child.communicate()
            finally:
                self.server.remove_child(child)


def create_certificate(openssl: str, certificate: Path, key: Path) -> None:
    """Generate the run-local, deliberately untrusted HTTPS test identity."""
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-nodes",
            "-days",
            "2",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mame",
        type=Path,
        default=Path("~/fun/mame/datarover"),
        help="DataRover MAME executable (default: ~/fun/mame/datarover)",
    )
    parser.add_argument(
        "--rompath",
        type=Path,
        default=Path("~/fun/magic-cap-assets/roms"),
        help="MAME ROM search root",
    )
    parser.add_argument(
        "--nvram-source",
        type=Path,
        required=True,
        help="EtherLink/provider/TLS-rule configured NVRAM root",
    )
    parser.add_argument(
        "--carl",
        type=Path,
        default=Path("~/fun/cryanc/carl"),
        help="built Crypto Ancienne carl executable",
    )
    parser.add_argument(
        "--openssl",
        default="openssl",
        help="OpenSSL executable used for the run-local certificate",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path(
            "~/fun/magic-cap-assets/runtime/etherlink-https-regression"
        ),
        help="persistent artifact root",
    )
    parser.add_argument("--system", default="datarover840")
    parser.add_argument("--proxy-port", type=int, default=8765)
    parser.add_argument("--https-port", type=int, default=9443)
    route = parser.add_mutually_exclusive_group()
    route.add_argument(
        "--upgrade-http",
        dest="upgrade_http",
        action="store_true",
        default=True,
        help="use browser Rule 13 plus carl -u (default)",
    )
    route.add_argument(
        "--https-rule",
        dest="upgrade_http",
        action="store_false",
        help="exercise the currently unresolved browser HTTPS Rule 14",
    )
    port_mode = parser.add_mutually_exclusive_group()
    port_mode.add_argument(
        "--implicit-url-port",
        dest="implicit_url_port",
        action="store_true",
        default=True,
        help=(
            "omit the guest URL port and map localhost to --https-port "
            "inside the loopback proxy (default)"
        ),
    )
    port_mode.add_argument(
        "--explicit-url-port",
        dest="implicit_url_port",
        action="store_false",
        help="type --https-port into the guest URL",
    )
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
    carl = args.carl.expanduser().resolve()
    workdir = args.workdir.expanduser().resolve()
    openssl = shutil.which(args.openssl)
    try:
        nvram_source = etherlink.resolve_nvram_source(
            args.nvram_source,
            args.system,
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    for label, path in (("MAME executable", mame), ("carl executable", carl)):
        if not path.is_file() or not os.access(path, os.X_OK):
            print(
                f"error: {label} not found or not executable: {path}",
                file=sys.stderr,
            )
            return 2
    if not rompath.is_dir():
        print(f"error: ROM path not found: {rompath}", file=sys.stderr)
        return 2
    if openssl is None:
        print(
            f"error: OpenSSL executable not found: {args.openssl}",
            file=sys.stderr,
        )
        return 2
    for label, port in (
        ("proxy port", args.proxy_port),
        ("HTTPS port", args.https_port),
    ):
        if not 1 <= port <= 65535:
            print(
                f"error: {label} must be between 1 and 65535",
                file=sys.stderr,
            )
            return 2
    if args.proxy_port == args.https_port:
        print("error: proxy and HTTPS ports must differ", file=sys.stderr)
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

    marker = run_dir / "https-request-seen"
    request_log = run_dir / "https-requests.txt"
    proxy_log = run_dir / "carl-proxy.txt"
    certificate = run_dir / "certificate.pem"
    key = run_dir / "private-key.pem"
    lua_path = run_dir / "etherlink-https-regression.lua"
    output_path = run_dir / "mame-output.txt"
    (cfg_dir / f"{args.system}.cfg").write_text(
        etherlink.config_xml(args.system),
        encoding="utf-8",
    )
    scheme = "http" if args.upgrade_http else "https"
    if args.implicit_url_port:
        browser_url = f"{scheme}://localhost"
    else:
        browser_url = f"{scheme}://localhost:{args.https_port}"
    lua_path.write_text(
        etherlink.automation_script(
            marker,
            browser_url,
            args.max_frames,
            result_wait_frames=1800,
        )
        .replace(
            "etherlink-url-entered.png",
            "etherlink-https-url-entered.png",
        )
        .replace("etherlink-http-result.png", "etherlink-https-result.png")
        .replace("etherlink-http-timeout.png", "etherlink-https-timeout.png"),
        encoding="utf-8",
    )

    try:
        create_certificate(openssl, certificate, key)
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode(errors="replace").strip()
        print(
            f"error: unable to generate test certificate: {detail}",
            file=sys.stderr,
        )
        return 2

    try:
        https_server = etherlink._RequestServer(
            ("127.0.0.1", args.https_port),
            marker,
            request_log,
            "/",
            HTTPS_BODY,
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        https_server.socket = context.wrap_socket(
            https_server.socket,
            server_side=True,
        )
    except (OSError, ssl.SSLError) as error:
        print(
            f"error: cannot start HTTPS on 127.0.0.1:{args.https_port}: "
            f"{error}",
            file=sys.stderr,
        )
        return 2

    try:
        proxy_server = _CarlProxyServer(
            ("127.0.0.1", args.proxy_port),
            carl,
            proxy_log,
            args.timeout,
            args.upgrade_http,
            args.https_port if args.implicit_url_port else None,
        )
    except OSError as error:
        https_server.server_close()
        print(
            f"error: cannot start proxy on 127.0.0.1:{args.proxy_port}: "
            f"{error}",
            file=sys.stderr,
        )
        return 2

    https_thread = threading.Thread(
        target=https_server.serve_forever,
        daemon=True,
    )
    proxy_thread = threading.Thread(
        target=proxy_server.serve_forever,
        daemon=True,
    )
    https_thread.start()
    proxy_thread.start()

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
        f"TLS proxy: guest 10.0.2.2:{args.proxy_port} -> "
        f"host 127.0.0.1:{args.proxy_port}",
        flush=True,
    )
    print(f"HTTPS endpoint: 127.0.0.1:{args.https_port}", flush=True)
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
        print(
            f"FAIL: MAME exceeded {args.timeout:g} seconds",
            file=sys.stderr,
        )
        return 1
    except OSError as error:
        print(f"error: unable to run MAME: {error}", file=sys.stderr)
        return 2
    finally:
        proxy_server.shutdown()
        https_server.shutdown()
        proxy_server.server_close()
        https_server.server_close()
        proxy_server.terminate_children()
        proxy_thread.join()
        https_thread.join()

    if completed.returncode:
        print(
            f"FAIL: MAME exited with status {completed.returncode}",
            file=sys.stderr,
        )
        return 1
    if not proxy_server.connection_seen.is_set():
        print(
            "FAIL: Magic Cap did not connect to the TLS proxy",
            file=sys.stderr,
        )
        return 1
    if not https_server.request_seen.is_set():
        print(
            "FAIL: proxy did not deliver the exact HTTPS request",
            file=sys.stderr,
        )
        return 1
    result_snapshot = snapshot_dir / "etherlink-https-result.png"
    if not result_snapshot.is_file():
        print(
            "FAIL: rendered HTTPS-result snapshot is missing",
            file=sys.stderr,
        )
        return 1
    print(request_log.read_text(encoding="utf-8").rstrip())
    route = "HTTP proxy upgrade" if args.upgrade_http else "HTTPS proxy Rule"
    print(
        "PASS: Magic Cap rendered deterministic local HTTPS through "
        f"Crypto Ancienne ({route})"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run_regression(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
