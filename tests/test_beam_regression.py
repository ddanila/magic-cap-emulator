from __future__ import annotations

import importlib.util
import errno
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


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

    def test_capitalizes_a_name_with_the_onscreen_caps_key(self) -> None:
        steps = beam.name_key_steps("Sam", 1000)
        self.assertIn("frames == 1000 then press(39, 302)", steps)
        self.assertIn("frames == 1100 then press(76, 234)", steps)
        self.assertIn("frames == 1200 then press(33, 234)", steps)
        self.assertNotIn("frames == 1200 then press(39, 302)", steps)

    def test_rejects_non_letter_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "letters a-z only"):
            beam.name_key_steps("alice-1", 1000)

    def test_script_drives_real_beam_ui_and_instruments_link(self) -> None:
        script = beam.automation_script("sender", "alice", "sender", True, 10_000, True)
        self.assertIn("frames == 4400 then press(376, 301)", script)
        self.assertIn("frames == 6100 then press(428, 144)", script)
        self.assertIn("frames == 6500 then press(237, 100)", script)
        self.assertIn("frames == 6800 then press(237, 100)", script)
        self.assertIn("frames == 7700 then press(237, 100)", script)
        self.assertNotIn("press(237, 89)", script)
        self.assertIn("frames == 8100 then press(371, 194)", script)
        self.assertIn(
            'frames == 8300 then\n        screen:snapshot("owner-setup-complete.png")',
            script,
        )
        self.assertIn("frames == 9000 then press(135, 170)", script)
        self.assertIn("frames == 9200 then press(181, 301)", script)
        self.assertIn("frames == 9500 then press(265, 146)", script)
        self.assertIn("frames == 10120 then press(170, 90)", script)
        self.assertIn("frames == 10220 then press(300, 217)", script)
        self.assertIn("frames == 10500 then press(369, 190)", script)
        self.assertIn("frames == 10700 then press(369, 190)", script)
        self.assertIn("BEAM_REPORT role=sender", script)
        for _name, address in beam.WATCHED:
            self.assertIn(f"0x{address:08x}", script)

    def test_notebook_mode_opens_the_desk_notebook_stack(self) -> None:
        script = beam.automation_script(
            "sender",
            "alice",
            "sender",
            True,
            10_000,
            item="notebook",
        )
        self.assertIn("frames == 9000 then press(335, 170)", script)
        self.assertNotIn("frames == 9000 then press(135, 170)", script)

    def test_receiver_completes_self_card_personalization(self) -> None:
        script = beam.automation_script(
            "receiver", "danila", "sukharev", False, 10_400
        )
        self.assertIn("frames == 6800 then press(237, 100)", script)
        self.assertIn("frames == 8000 then press(237, 100)", script)
        self.assertIn("frames == 8400 then press(371, 194)", script)


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
        self.assertEqual(beam.decode_sir_frames(bytes([beam.SIR_BEGIN, 0x01])), [])

    def test_item_payloads_are_type_specific(self) -> None:
        name_card = b"alice Sender" * 3
        mixed_case_name_card = b"alice Sender" * 3
        notebook = b"alice Sender" * 2 + b"Note Card"
        self.assertTrue(beam.item_payload_present("name-card", name_card))
        self.assertTrue(
            beam.item_payload_present(
                "name-card", mixed_case_name_card, "alice sender"
            )
        )
        self.assertFalse(beam.item_payload_present("notebook", name_card))
        self.assertTrue(beam.item_payload_present("notebook", notebook))
        self.assertFalse(beam.item_payload_present("name-card", notebook))

    def test_image_region_change_is_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            Image.new("RGB", (480, 320), "white").save(first)
            changed = Image.new("RGB", (480, 320), "white")
            changed.putpixel((200, 80), (0, 0, 0))
            changed.save(second)
            self.assertTrue(
                beam.image_region_changed(first, second, (176, 52, 236, 110))
            )
            self.assertFalse(beam.image_region_changed(first, second, (0, 0, 100, 40)))


class PtyTests(unittest.TestCase):
    def test_treats_linux_eio_after_slave_closure_as_eof(self) -> None:
        with patch.object(beam.os, "read", side_effect=OSError(errno.EIO, "closed")):
            self.assertIsNone(beam._read_irda(10))

    def test_does_not_hide_other_pty_read_errors(self) -> None:
        with patch.object(
            beam.os, "read", side_effect=OSError(errno.EBADF, "bad descriptor")
        ):
            with self.assertRaises(OSError):
                beam._read_irda(10)


if __name__ == "__main__":
    unittest.main()
