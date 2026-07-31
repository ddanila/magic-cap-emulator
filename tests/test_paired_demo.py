import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools import paired_demo


class InvitationTests(unittest.TestCase):
    def test_builds_native_page_and_exact_framebuffer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            png, raw = paired_demo.make_invitation(Path(temporary))

            with Image.open(png) as image:
                self.assertEqual(image.size, (480, 320))
            self.assertEqual(raw.stat().st_size, 480 * 320 // 4)

    def test_framebuffer_uses_data_rover_white_level(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _png, raw = paired_demo.make_invitation(Path(temporary))
            data = raw.read_bytes()

            self.assertEqual(data[3 * 120 + 3], 0x00)
            self.assertIn(0xFF, data)


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
