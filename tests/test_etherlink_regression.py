from __future__ import annotations

import importlib.util
from http.client import HTTPConnection
from pathlib import Path
import tempfile
import unittest
from urllib.request import urlopen


MODULE_PATH = Path(__file__).parents[1] / "tools" / "etherlink_regression.py"
SPEC = importlib.util.spec_from_file_location("etherlink_regression", MODULE_PATH)
assert SPEC and SPEC.loader
etherlink = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(etherlink)


class AutomationTests(unittest.TestCase):
    def test_script_enters_url_and_waits_for_request_marker(self) -> None:
        script = etherlink.automation_script(
            Path("/persistent/run/request-seen"),
            "10.0.2.2:8080/",
            4321,
        )

        self.assertIn('emu.keypost("10.0.2.2:8080/")', script)
        self.assertIn('local marker = "/persistent/run/request-seen"', script)
        self.assertIn("frames == request_frame + 600", script)
        self.assertIn("frames == 4321", script)

    def test_config_selects_card_interface_zero(self) -> None:
        config = etherlink.config_xml("datarover840")

        self.assertIn('system name="datarover840"', config)
        self.assertIn('device tag=":pccard1:3c589"', config)
        self.assertIn('interface="0"', config)


class NvramTests(unittest.TestCase):
    def test_accepts_root_or_system_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "nvram"
            system = root / "datarover840"
            system.mkdir(parents=True)
            (system / "ram").write_bytes(b"ram")

            self.assertEqual(
                root.resolve(),
                etherlink.resolve_nvram_source(root, "datarover840"),
            )
            self.assertEqual(
                root.resolve(),
                etherlink.resolve_nvram_source(system, "datarover840"),
            )

    def test_rejects_unconfigured_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                etherlink.resolve_nvram_source(
                    Path(temporary),
                    "datarover840",
                )


class HttpServerTests(unittest.TestCase):
    def test_records_request_and_signals_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            marker = directory / "seen"
            request_log = directory / "requests.txt"
            server = etherlink._RequestServer(
                ("127.0.0.1", 0),
                marker,
                request_log,
            )
            thread = etherlink.threading.Thread(
                target=server.serve_forever,
                daemon=True,
            )
            thread.start()
            try:
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/test",
                    timeout=5,
                ) as response:
                    self.assertIn(b"EtherLink III works", response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

            self.assertTrue(server.request_seen.is_set())
            self.assertEqual(
                "GET /test HTTP/1.1\n",
                request_log.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                request_log.read_text(encoding="utf-8"),
                marker.read_text(encoding="utf-8"),
            )

    def test_marker_waits_for_exact_absolute_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            marker = directory / "seen"
            request_log = directory / "requests.txt"
            target = "http://10.0.2.2:8080/"
            server = etherlink._RequestServer(
                ("127.0.0.1", 0),
                marker,
                request_log,
                target,
            )
            thread = etherlink.threading.Thread(
                target=server.serve_forever,
                daemon=True,
            )
            thread.start()
            connection = HTTPConnection(
                "127.0.0.1",
                server.server_port,
                timeout=5,
            )
            try:
                connection.request("GET", target + "/[")
                connection.getresponse().read()
                self.assertFalse(server.request_seen.is_set())
                connection.request("GET", target)
                connection.getresponse().read()
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join()

            self.assertTrue(server.request_seen.is_set())
            self.assertEqual(
                f"GET {target} HTTP/1.1\n",
                marker.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
