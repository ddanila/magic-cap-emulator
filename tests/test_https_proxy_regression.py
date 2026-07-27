from __future__ import annotations

from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest


TOOLS = Path(__file__).parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import https_proxy_regression as https_proxy  # noqa: E402


class ArgumentTests(unittest.TestCase):
    def test_defaults_match_documented_browser_rule(self) -> None:
        args = https_proxy.parse_args(["--nvram-source", "/configured"])

        self.assertEqual(8765, args.proxy_port)
        self.assertEqual(9443, args.https_port)
        self.assertEqual(
            https_proxy.REPO_ROOT.parent / "cryanc" / "carl",
            args.carl,
        )
        self.assertTrue(args.upgrade_http)
        self.assertTrue(args.implicit_url_port)


class CarlSuperserverTests(unittest.TestCase):
    def test_bridges_and_maps_implicit_local_port(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            child = directory / "proxy-child"
            child.write_text(
                f"""#!{sys.executable}
import sys

while True:
    line = sys.stdin.buffer.readline()
    if line in (b"\\r\\n", b"\\n", b""):
        break
sys.stdout.buffer.write(
    b"HTTP/1.0 200 OK\\r\\nContent-Length: 2\\r\\n\\r\\nOK"
)
""",
                encoding="utf-8",
            )
            child.chmod(0o755)
            log = directory / "proxy.log"
            server = https_proxy._CarlProxyServer(
                ("127.0.0.1", 0),
                child,
                log,
                5,
                True,
                9443,
            )
            thread = threading.Thread(
                target=server.serve_forever,
                daemon=True,
            )
            thread.start()
            try:
                with socket.create_connection(
                    ("127.0.0.1", server.server_address[1]),
                    timeout=5,
                ) as client:
                    client.sendall(
                        b"GET http://localhost/ HTTP/1.0\r\n"
                        b"Host: localhost\r\n\r\n"
                    )
                    client.shutdown(socket.SHUT_WR)
                    response = bytearray()
                    while True:
                        block = client.recv(4096)
                        if not block:
                            break
                        response.extend(block)
            finally:
                server.shutdown()
                server.server_close()
                server.terminate_children()
                thread.join()

            self.assertTrue(server.connection_seen.is_set())
            self.assertIn(b"HTTP/1.0 200 OK", response)
            self.assertTrue(response.endswith(b"OK"))
            self.assertEqual(
                b"GET http://localhost/ HTTP/1.0\r\n"
                b"Host: localhost\r\n\r\n",
                (directory / "browser-proxy-requests.bin").read_bytes(),
            )
            self.assertEqual(
                b"GET http://localhost:9443/ HTTP/1.0\r\n"
                b"Host: localhost:9443\r\n\r\n",
                (directory / "carl-input.bin").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
