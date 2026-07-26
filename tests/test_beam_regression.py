from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "beam_regression.py"
SPEC = importlib.util.spec_from_file_location("beam_regression", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
beam = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = beam
SPEC.loader.exec_module(beam)


class NameAutomationTests(unittest.TestCase):
    def test_generates_paced_keyboard_taps(self) -> None:
        steps = beam.name_key_steps("az", 1000)
        self.assertIn("frames == 1000 then press(33, 234)", steps)
        self.assertIn("frames == 1100 then press(48, 269)", steps)
        self.assertIn("frames == 1120 then release()", steps)

    def test_rejects_non_letter_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "letters a-z only"):
            beam.name_key_steps("alice-1", 1000)

    def test_script_drives_real_beam_ui_and_instruments_link(self) -> None:
        script = beam.automation_script(
            "sender", "alice", "sender", True, 10_000, True
        )
        self.assertIn("frames == 4400 then press(376, 301)", script)
        self.assertIn("frames == 7300 then press(135, 170)", script)
        self.assertIn("frames == 7500 then press(181, 301)", script)
        self.assertIn("frames == 7800 then press(265, 146)", script)
        self.assertIn("frames == 8420 then press(170, 90)", script)
        self.assertIn("frames == 8520 then press(300, 217)", script)
        self.assertIn("frames == 8700 then press(369, 190)", script)
        self.assertIn("BEAM_REPORT role=sender", script)
        for _name, address in beam.WATCHED:
            self.assertIn(f"0x{address:08x}", script)


class ReportTests(unittest.TestCase):
    def test_parses_report(self) -> None:
        parsed = beam._parse_report(
            b"BEAM_REPORT role=sender irlap_open=2 beam_discover=1 "
            b"uartA=C0000001 uartB=C00001C1\n"
        )
        self.assertEqual(
            parsed,
            ({"irlap_open": 2, "beam_discover": 1}, 0xC0000001, 0xC00001C1),
        )

    def test_needs_complete_report(self) -> None:
        self.assertIsNone(beam._parse_report(b"unrelated output"))


class SirFrameTests(unittest.TestCase):
    def test_decodes_preambles_delimiters_and_escapes(self) -> None:
        stream = bytes(
            [
                0xFF,
                0xFF,
                beam.SIR_BEGIN,
                0x01,
                beam.SIR_ESCAPE,
                0xE0,
                beam.SIR_ESCAPE,
                0xE1,
                beam.SIR_ESCAPE,
                0x5D,
                0x02,
                beam.SIR_END,
                0xFF,
            ]
        )
        self.assertEqual(
            beam.decode_sir_frames(stream),
            [bytes([0x01, 0xC0, 0xC1, 0x7D, 0x02])],
        )

    def test_ignores_incomplete_frame(self) -> None:
        self.assertEqual(
            beam.decode_sir_frames(bytes([beam.SIR_BEGIN, 0x01])), []
        )


if __name__ == "__main__":
    unittest.main()
