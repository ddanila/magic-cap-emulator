from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "tools" / "https_proxy.py"
SPEC = importlib.util.spec_from_file_location("https_proxy", MODULE_PATH)
assert SPEC and SPEC.loader
https_proxy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = https_proxy
SPEC.loader.exec_module(https_proxy)


def public_resolver(
    host: str,
    port: int,
    *args: object,
) -> list[tuple[object, ...]]:
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("93.184.216.34", port),
        )
    ]


def private_resolver(
    host: str,
    port: int,
    *args: object,
) -> list[tuple[object, ...]]:
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("127.0.0.1", port),
        )
    ]


class RequestValidationTests(unittest.TestCase):
    def test_accepts_public_https_get(self) -> None:
        raw = (
            b"GET https://example.com/index.html HTTP/1.0\r\nHost: example.com\r\n\r\n"
        )

        request = https_proxy.validate_request(
            raw,
            max_body_bytes=1024,
            allow_private_targets=False,
            resolver=public_resolver,
        )

        self.assertEqual("GET", request.method)
        self.assertEqual("example.com", request.hostname)
        self.assertEqual(443, request.port)
        self.assertEqual(raw, request.data)

    def test_blocks_private_destination_by_default(self) -> None:
        raw = b"GET https://localhost/ HTTP/1.0\r\n\r\n"

        with self.assertRaises(https_proxy.RequestError) as raised:
            https_proxy.validate_request(
                raw,
                max_body_bytes=1024,
                allow_private_targets=False,
                resolver=private_resolver,
            )

        self.assertEqual(403, raised.exception.status)

    def test_private_destination_requires_explicit_opt_in(self) -> None:
        raw = b"GET https://localhost/ HTTP/1.0\r\n\r\n"

        request = https_proxy.validate_request(
            raw,
            max_body_bytes=1024,
            allow_private_targets=True,
            resolver=private_resolver,
        )

        self.assertEqual("localhost", request.hostname)

    def test_rejects_plain_http_and_connect(self) -> None:
        requests = (
            b"GET http://example.com/ HTTP/1.0\r\n\r\n",
            b"CONNECT example.com:443 HTTP/1.0\r\n\r\n",
        )

        for raw in requests:
            with self.subTest(raw=raw):
                with self.assertRaises(https_proxy.RequestError):
                    https_proxy.validate_request(
                        raw,
                        max_body_bytes=1024,
                        allow_private_targets=False,
                        resolver=public_resolver,
                    )

    def test_validates_post_length_and_limit(self) -> None:
        raw = b"POST https://example.com/form HTTP/1.0\r\nContent-Length: 3\r\n\r\none"
        request = https_proxy.validate_request(
            raw,
            max_body_bytes=3,
            allow_private_targets=False,
            resolver=public_resolver,
        )
        self.assertEqual("POST", request.method)

        with self.assertRaises(https_proxy.RequestError) as raised:
            https_proxy.validate_request(
                raw,
                max_body_bytes=2,
                allow_private_targets=False,
                resolver=public_resolver,
            )
        self.assertEqual(413, raised.exception.status)


class CarlProxyServerTests(unittest.TestCase):
    def test_fixed_loopback_server_streams_fake_child_response(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            child = directory / "carl"
            captured = directory / "captured.bin"
            metadata = directory / "metadata.txt"
            child.write_text(
                f"""#!{sys.executable}
import os
import pathlib
import sys

data = sys.stdin.buffer.read()
pathlib.Path({str(captured)!r}).write_bytes(data)
pathlib.Path({str(metadata)!r}).write_text(
    repr(sys.argv[1:]) + "\\n" + repr(os.environ.get("ALL_PROXY")),
    encoding="utf-8",
)
sys.stdout.buffer.write(
    b"HTTP/1.0 200 OK\\r\\nContent-Length: 2\\r\\n\\r\\nOK"
)
""",
                encoding="utf-8",
            )
            child.chmod(0o755)
            log = directory / "proxy.log"
            server = https_proxy.CarlProxyServer(
                ("127.0.0.1", 0),
                carl=child,
                log_path=log,
                child_timeout=5,
                request_timeout=2,
                max_connections=1,
                max_header_bytes=4096,
                max_body_bytes=1024,
                allow_private_targets=False,
                resolver=public_resolver,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            request = b"GET https://example.com/ HTTP/1.0\r\nHost: example.com\r\n\r\n"
            try:
                with mock.patch.dict(
                    os.environ,
                    {"ALL_PROXY": "socks://should-not-leak:1080"},
                ):
                    with socket.create_connection(
                        server.server_address,
                        timeout=5,
                    ) as client:
                        client.sendall(request)
                        client.shutdown(socket.SHUT_WR)
                        response = bytearray()
                        while True:
                            block = client.recv(4096)
                            if not block:
                                break
                            response.extend(block)
            finally:
                server.shutdown()
                server.terminate_children()
                server.server_close()
                thread.join(timeout=2)

            self.assertIn(b"HTTP/1.0 200 OK", response)
            self.assertTrue(response.endswith(b"OK"))
            self.assertEqual(request, captured.read_bytes())
            self.assertEqual(
                "['-Npst']\nNone",
                metadata.read_text(encoding="utf-8"),
            )
            self.assertEqual(0o600, os.stat(log).st_mode & 0o777)
            log_text = log.read_text(encoding="utf-8")
            self.assertIn("GET https://example.com:443", log_text)
            self.assertNotIn("/ HTTP", log_text)

    def test_server_refuses_non_loopback_bind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                https_proxy.CarlProxyServer(
                    ("0.0.0.0", 0),
                    carl=Path("/unused"),
                    log_path=Path(temporary) / "proxy.log",
                    child_timeout=5,
                    request_timeout=2,
                    max_connections=1,
                    max_header_bytes=4096,
                    max_body_bytes=1024,
                    allow_private_targets=False,
                )


class ArgumentTests(unittest.TestCase):
    def test_defaults_are_guarded(self) -> None:
        args = https_proxy.parse_args([])

        self.assertEqual(8765, args.port)
        self.assertEqual(4, args.max_connections)
        self.assertEqual(1048576, args.max_body_bytes)
        self.assertFalse(args.allow_private_targets)


if __name__ == "__main__":
    unittest.main()
