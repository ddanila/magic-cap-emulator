from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "modem_bridge.py"
SPEC = importlib.util.spec_from_file_location("modem_bridge", MODULE_PATH)
assert SPEC and SPEC.loader
modem_bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = modem_bridge
SPEC.loader.exec_module(modem_bridge)


class HayesNegotiatorTests(unittest.TestCase):
    def test_first_compound_command_is_echoed_then_disables_echo(self) -> None:
        modem = modem_bridge.HayesNegotiator()

        first = modem.feed(b"ATE0V1&d2W2\r")
        second = modem.feed(b"atl0\r")

        self.assertEqual(len(first), 1)
        self.assertEqual(
            first[0].response, b"ATE0V1&d2W2\r\r\nOK\r\n"
        )
        self.assertFalse(first[0].dial)
        self.assertEqual(second[0].response, b"\r\nOK\r\n")

    def test_fragmented_commands_and_dial(self) -> None:
        modem = modem_bridge.HayesNegotiator()

        self.assertEqual(modem.feed(b"AT&N"), [])
        events = modem.feed(b"0\rATDT1 (651) 555-1212\r")

        self.assertEqual([event.command for event in events], [
            "AT&N0",
            "ATDT1 (651) 555-1212",
        ])
        self.assertFalse(events[0].dial)
        self.assertTrue(events[1].dial)
        self.assertEqual(events[1].response, b"ATDT1 (651) 555-1212\r")


class PPPFrameTests(unittest.TestCase):
    def test_extracts_magic_cap_lcp_frame(self) -> None:
        wire = bytes.fromhex(
            "7e ff 7d23 c021 7d21 7d21 7d20 7d2e "
            "7d22 7d26 7d20 7d20 7d20 7d20 7d25 "
            "7d26 7d20 7d20 7d20 7d20 9a 96 7e"
        )

        frames = modem_bridge.ppp_frames(wire)

        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][:4], b"\xff\x03\xc0\x21")
        self.assertEqual(
            modem_bridge.ppp_protocol(frames[0]), modem_bridge.PPP_LCP
        )

    def test_ignores_incomplete_frame(self) -> None:
        self.assertEqual(modem_bridge.ppp_frames(b"\x7e\xff\x7d"), [])


class AutomationTests(unittest.TestCase):
    def test_classic_slirp_tty_preserves_final_path_byte(self) -> None:
        self.assertEqual(
            modem_bridge.classic_slirp_tty("/dev/pts/8"),
            "/dev/pts/8\n",
        )

    def test_autodial_click_and_optional_exit(self) -> None:
        interactive = modem_bridge.autodial_script(None)
        probe = modem_bridge.autodial_script(3600)

        self.assertIn("frames == 500", interactive)
        self.assertIn("press(320, 164)", interactive)
        self.assertNotIn("machine:exit()", interactive)
        self.assertIn('snapshot("ppp-connected.png")', probe)
        self.assertIn("frames == 3480", probe)
        self.assertIn("frames == 3600", probe)
        self.assertIn("machine:exit()", probe)


if __name__ == "__main__":
    unittest.main()
