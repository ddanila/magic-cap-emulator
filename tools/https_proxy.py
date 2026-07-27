#!/usr/bin/env python3
"""Run a guarded loopback TLS proxy for Magic Cap Web Browser 3.5."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import sys
import threading
from typing import Callable, Sequence
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_CARL = REPO_ROOT.parent / "cryanc" / "carl"
DEFAULT_LOG = ASSETS_ROOT / "runtime" / "https-proxy" / "proxy.log"
LISTEN_HOST = "127.0.0.1"
ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST"})


class RequestError(ValueError):
    """A client error that can be returned as a small HTTP response."""

    def __init__(self, status: int, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ProxyRequest:
    """A complete, validated request and its privacy-safe log fields."""

    data: bytes
    method: str
    hostname: str
    port: int


Resolver = Callable[..., list[tuple[object, ...]]]


def _header_end(data: bytes) -> int | None:
    endings = []
    for separator in (b"\r\n\r\n", b"\n\n"):
        position = data.find(separator)
        if position >= 0:
            endings.append(position + len(separator))
    return min(endings) if endings else None


def _error_response(status: int, reason: str, detail: str) -> bytes:
    body = (
        "<!doctype html><title>Magic Cap proxy error</title>"
        f"<h1>{status} {reason}</h1><p>{detail}</p>"
    ).encode("ascii", "replace")
    return (
        f"HTTP/1.0 {status} {reason}\r\n".encode("ascii")
        + b"Content-Type: text/html; charset=us-ascii\r\n"
        + f"Content-Length: {len(body)}\r\n".encode("ascii")
        + b"Connection: close\r\n\r\n"
        + body
    )


def _target_is_public(
    hostname: str,
    port: int,
    resolver: Resolver = socket.getaddrinfo,
) -> bool:
    try:
        answers = resolver(
            hostname,
            port,
            socket.AF_INET,
            socket.SOCK_STREAM,
        )
    except socket.gaierror as error:
        raise RequestError(
            502,
            "Bad Gateway",
            "The destination name could not be resolved.",
        ) from error
    addresses = {
        ipaddress.ip_address(answer[4][0])
        for answer in answers
        if len(answer) >= 5 and answer[4]
    }
    if not addresses:
        raise RequestError(
            502,
            "Bad Gateway",
            "The destination has no IPv4 address.",
        )
    return all(address.is_global for address in addresses)


def validate_request(
    data: bytes,
    *,
    max_body_bytes: int,
    allow_private_targets: bool,
    resolver: Resolver = socket.getaddrinfo,
) -> ProxyRequest:
    """Validate one complete proxy request before giving it to ``carl``."""
    header_end = _header_end(data)
    if header_end is None:
        raise RequestError(400, "Bad Request", "Incomplete request headers.")
    header = data[:header_end]
    body = data[header_end:]
    if b"\0" in header:
        raise RequestError(400, "Bad Request", "Invalid request header.")
    try:
        lines = header.replace(b"\r\n", b"\n").split(b"\n")
        method_raw, target_raw, version_raw = lines[0].split(b" ")
        method = method_raw.decode("ascii")
        target = target_raw.decode("ascii")
        version = version_raw.decode("ascii")
    except (UnicodeDecodeError, ValueError) as error:
        raise RequestError(
            400,
            "Bad Request",
            "Expected an absolute HTTP/1.x proxy request.",
        ) from error
    if method not in ALLOWED_METHODS:
        raise RequestError(
            405,
            "Method Not Allowed",
            "Only GET, HEAD and POST are accepted.",
        )
    if version not in {"HTTP/1.0", "HTTP/1.1"}:
        raise RequestError(
            505,
            "HTTP Version Not Supported",
            "Only HTTP/1.0 and HTTP/1.1 are accepted.",
        )
    try:
        parsed = urlsplit(target)
        hostname = parsed.hostname
        port = parsed.port or 443
    except ValueError as error:
        raise RequestError(
            400,
            "Bad Request",
            "The destination URL is invalid.",
        ) from error
    if parsed.scheme.lower() != "https" or hostname is None:
        raise RequestError(
            403,
            "Forbidden",
            "This listener accepts absolute https:// URLs only.",
        )
    if parsed.username is not None or parsed.password is not None:
        raise RequestError(
            403,
            "Forbidden",
            "Credentials in destination URLs are not accepted.",
        )
    if parsed.fragment:
        raise RequestError(
            400,
            "Bad Request",
            "URL fragments must not be sent to a proxy.",
        )
    if not 1 <= port <= 65535:
        raise RequestError(400, "Bad Request", "Invalid destination port.")

    content_lengths: list[int] = []
    transfer_encoding = False
    for raw_line in lines[1:]:
        if not raw_line:
            continue
        if b":" not in raw_line:
            raise RequestError(
                400,
                "Bad Request",
                "Malformed request header.",
            )
        name, value = raw_line.split(b":", 1)
        name = name.strip().lower()
        value = value.strip()
        if name == b"transfer-encoding":
            transfer_encoding = True
        elif name == b"content-length":
            try:
                content_lengths.append(int(value))
            except ValueError as error:
                raise RequestError(
                    400,
                    "Bad Request",
                    "Invalid Content-Length.",
                ) from error
    if transfer_encoding:
        raise RequestError(
            400,
            "Bad Request",
            "Transfer-Encoding request bodies are not supported.",
        )
    if any(length < 0 for length in content_lengths):
        raise RequestError(400, "Bad Request", "Invalid Content-Length.")
    if len(set(content_lengths)) > 1:
        raise RequestError(
            400,
            "Bad Request",
            "Conflicting Content-Length headers.",
        )
    expected_body = content_lengths[0] if content_lengths else 0
    if expected_body > max_body_bytes:
        raise RequestError(
            413,
            "Content Too Large",
            "The request body exceeds the configured limit.",
        )
    if len(body) != expected_body:
        raise RequestError(
            400,
            "Bad Request",
            "The request body does not match Content-Length.",
        )
    if not allow_private_targets and not _target_is_public(
        hostname,
        port,
        resolver,
    ):
        raise RequestError(
            403,
            "Forbidden",
            "Private, loopback, link-local and reserved targets are blocked.",
        )
    return ProxyRequest(data, method, hostname, port)


class CarlProxyServer(socketserver.ThreadingTCPServer):
    """A fixed-loopback, bounded superserver for one ``carl`` per request."""

    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False
    request_queue_size = 8

    def __init__(
        self,
        address: tuple[str, int],
        *,
        carl: Path,
        log_path: Path,
        child_timeout: float,
        request_timeout: float,
        max_connections: int,
        max_header_bytes: int,
        max_body_bytes: int,
        allow_private_targets: bool,
        resolver: Resolver = socket.getaddrinfo,
    ) -> None:
        if address[0] != LISTEN_HOST:
            raise ValueError(f"proxy must bind {LISTEN_HOST}")
        self.carl = carl
        self.log_path = log_path
        self.child_timeout = child_timeout
        self.request_timeout = request_timeout
        self.max_header_bytes = max_header_bytes
        self.max_body_bytes = max_body_bytes
        self.allow_private_targets = allow_private_targets
        self.resolver = resolver
        self._slots = threading.BoundedSemaphore(max_connections)
        self._children: set[subprocess.Popen[bytes]] = set()
        self._children_lock = threading.Lock()
        self._log_lock = threading.Lock()
        super().__init__(address, CarlProxyHandler)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            log_path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        os.chmod(log_path, 0o600)
        self._log = os.fdopen(descriptor, "a", encoding="utf-8", buffering=1)

    def process_request(
        self,
        request: socket.socket,
        client_address: tuple[str, int],
    ) -> None:
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(
                    _error_response(
                        503,
                        "Service Unavailable",
                        "The proxy connection limit is in use.",
                    )
                )
            finally:
                self.shutdown_request(request)
            self.log_event("rejected connection: capacity reached")
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(
        self,
        request: socket.socket,
        client_address: tuple[str, int],
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

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

    def log_event(self, message: str) -> None:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._log_lock:
            self._log.write(f"{stamp} {message}\n")

    def server_close(self) -> None:
        super().server_close()
        log = getattr(self, "_log", None)
        if log is not None and not log.closed:
            log.close()


class CarlProxyHandler(socketserver.BaseRequestHandler):
    server: CarlProxyServer

    def _read_request(self) -> bytes:
        self.request.settimeout(self.server.request_timeout)
        request = bytearray()
        header_end = None
        while header_end is None:
            try:
                block = self.request.recv(4096)
            except TimeoutError as error:
                raise RequestError(
                    408,
                    "Request Timeout",
                    "Timed out while reading request headers.",
                ) from error
            if not block:
                raise RequestError(
                    400,
                    "Bad Request",
                    "Connection closed before the request was complete.",
                )
            request.extend(block)
            header_end = _header_end(request)
            if header_end is None and len(request) > self.server.max_header_bytes:
                raise RequestError(
                    431,
                    "Request Header Fields Too Large",
                    "The request headers exceed the configured limit.",
                )
        if header_end > self.server.max_header_bytes:
            raise RequestError(
                431,
                "Request Header Fields Too Large",
                "The request headers exceed the configured limit.",
            )

        header = bytes(request[:header_end])
        content_length = 0
        for raw_line in header.replace(b"\r\n", b"\n").split(b"\n")[1:]:
            if b":" not in raw_line:
                continue
            name, value = raw_line.split(b":", 1)
            if name.strip().lower() == b"content-length":
                try:
                    content_length = int(value.strip())
                except ValueError:
                    break
        if content_length > self.server.max_body_bytes:
            raise RequestError(
                413,
                "Content Too Large",
                "The request body exceeds the configured limit.",
            )
        expected_size = header_end + max(content_length, 0)
        if len(request) > expected_size:
            raise RequestError(
                400,
                "Bad Request",
                "Pipelined requests are not supported.",
            )
        while len(request) < expected_size:
            try:
                block = self.request.recv(min(65536, expected_size - len(request)))
            except TimeoutError as error:
                raise RequestError(
                    408,
                    "Request Timeout",
                    "Timed out while reading the request body.",
                ) from error
            if not block:
                break
            request.extend(block)
        return bytes(request)

    def handle(self) -> None:
        try:
            raw_request = self._read_request()
            request = validate_request(
                raw_request,
                max_body_bytes=self.server.max_body_bytes,
                allow_private_targets=self.server.allow_private_targets,
                resolver=self.server.resolver,
            )
        except RequestError as error:
            self.server.log_event(f"rejected request: {error.detail}")
            try:
                self.request.sendall(
                    _error_response(error.status, error.reason, error.detail)
                )
            except OSError:
                pass
            return

        self.server.log_event(
            f"{request.method} https://{request.hostname}:{request.port}"
        )
        # The read deadline puts the socket into nonblocking mode. Restore a
        # blocking descriptor before giving its duplicate to carl so a reply
        # larger than the socket buffer is streamed instead of truncated.
        self.request.settimeout(None)
        environment = os.environ.copy()
        environment.pop("ALL_PROXY", None)
        environment.pop("all_proxy", None)
        with self.server.log_path.open("ab", buffering=0) as log:
            try:
                child = subprocess.Popen(
                    [str(self.server.carl), "-Npst"],
                    stdin=subprocess.PIPE,
                    stdout=self.request,
                    stderr=log,
                    env=environment,
                    close_fds=True,
                )
            except OSError as error:
                self.server.log_event(f"unable to launch carl: {error}")
                self.request.sendall(
                    _error_response(
                        502,
                        "Bad Gateway",
                        "The TLS helper could not be started.",
                    )
                )
                return
            self.server.add_child(child)
            try:
                child.communicate(
                    request.data,
                    timeout=self.server.child_timeout,
                )
                self.server.log_event(
                    f"carl exited {child.returncode} for "
                    f"{request.hostname}:{request.port}"
                )
            except subprocess.TimeoutExpired:
                self.server.log_event(
                    f"carl timed out for {request.hostname}:{request.port}"
                )
                child.kill()
                child.communicate()
            finally:
                self.server.remove_child(child)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--carl",
        type=Path,
        default=DEFAULT_CARL,
        help=f"built Crypto Ancienne carl executable (default: {DEFAULT_CARL})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="host loopback port and Browser Rule 14 port (default: 8765)",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_LOG,
        help=f"append-only diagnostic log (default: {DEFAULT_LOG})",
    )
    parser.add_argument(
        "--child-timeout",
        type=float,
        default=90.0,
        help="maximum seconds for one remote transaction (default: 90)",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=10.0,
        help="maximum seconds to receive one guest request (default: 10)",
    )
    parser.add_argument(
        "--max-connections",
        type=int,
        default=4,
        help="maximum concurrent TLS helpers (default: 4)",
    )
    parser.add_argument(
        "--max-header-bytes",
        type=int,
        default=65536,
        help="maximum guest request-header size (default: 65536)",
    )
    parser.add_argument(
        "--max-body-bytes",
        type=int,
        default=1048576,
        help="maximum POST body size (default: 1048576)",
    )
    parser.add_argument(
        "--allow-private-targets",
        action="store_true",
        help=(
            "allow loopback/private/link-local destinations; unsafe unless "
            "you intend to expose host or LAN services"
        ),
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    carl = args.carl.expanduser().resolve()
    log_path = args.log.expanduser().resolve()
    if not carl.is_file() or not os.access(carl, os.X_OK):
        print(
            f"error: carl executable not found or not executable: {carl}",
            file=sys.stderr,
        )
        return 2
    for label, value in (
        ("port", args.port),
        ("max connections", args.max_connections),
        ("maximum header bytes", args.max_header_bytes),
        ("maximum body bytes", args.max_body_bytes),
    ):
        if value < 1:
            print(f"error: {label} must be positive", file=sys.stderr)
            return 2
    if args.port > 65535:
        print("error: port must not exceed 65535", file=sys.stderr)
        return 2
    for label, value in (
        ("child timeout", args.child_timeout),
        ("request timeout", args.request_timeout),
    ):
        if value <= 0:
            print(f"error: {label} must be positive", file=sys.stderr)
            return 2

    try:
        server = CarlProxyServer(
            (LISTEN_HOST, args.port),
            carl=carl,
            log_path=log_path,
            child_timeout=args.child_timeout,
            request_timeout=args.request_timeout,
            max_connections=args.max_connections,
            max_header_bytes=args.max_header_bytes,
            max_body_bytes=args.max_body_bytes,
            allow_private_targets=args.allow_private_targets,
        )
    except (OSError, ValueError) as error:
        print(
            f"error: cannot start proxy on {LISTEN_HOST}:{args.port}: {error}",
            file=sys.stderr,
        )
        return 2

    target_policy = (
        "private targets ALLOWED"
        if args.allow_private_targets
        else "public IPv4 targets only"
    )
    print(f"Magic Cap HTTPS proxy listening on {LISTEN_HOST}:{args.port}")
    print(f"Browser Rule 14: host 10.0.2.2, port {args.port}")
    print(f"Target policy: {target_policy}")
    print(f"Log: {log_path}")
    print(
        "WARNING: Crypto Ancienne does not authenticate server "
        "certificates. Do not send passwords or sensitive data."
    )
    print("Press Ctrl-C to stop.", flush=True)
    server.log_event(f"proxy started on {LISTEN_HOST}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping proxy...", flush=True)
    finally:
        server.shutdown()
        server.terminate_children()
        server.server_close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
