import tempfile
import unittest
from pathlib import Path

from tools import paired_demo


class InvitationTests(unittest.TestCase):
    def test_builds_stylus_strokes_from_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            strokes = paired_demo.signature_strokes(Path(temporary))

            self.assertGreater(len(strokes), 100)
            self.assertTrue(all(270 <= x1 <= x2 < 390 for x1, _y, x2 in strokes))
            self.assertTrue(all(215 <= y < 260 for _x1, y, _x2 in strokes))

    def test_document_script_uses_only_visible_notebook_input(self) -> None:
        script = paired_demo.notebook_invitation_script([(270, 205, 280)])

        self.assertIn('emu.keypost("PARODY DEMO', script)
        self.assertIn('emu.keypost("Senior Magic Cap Emulator Engineer.', script)
        self.assertIn("press(451, 100)", script)
        self.assertIn("press(270, 205)", script)
        self.assertIn("move(280, 205)", script)
        self.assertNotIn("program:write", script)


class RecordingTests(unittest.TestCase):
    def test_finds_all_mame_rollover_recordings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in ("recording1.mng", "recording0.mng"):
                (directory / name).touch()

            self.assertEqual(
                [path.name for path in paired_demo.find_mngs(directory)],
                ["recording0.mng", "recording1.mng"],
            )


if __name__ == "__main__":
    unittest.main()
