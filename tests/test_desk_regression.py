import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "desk_regression.py"
SPEC = importlib.util.spec_from_file_location("desk_regression", MODULE_PATH)
assert SPEC and SPEC.loader
desk_regression = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = desk_regression
SPEC.loader.exec_module(desk_regression)


class DeskRegressionTests(unittest.TestCase):
    def test_parses_framebuffer_checkpoint(self) -> None:
        output = (
            b"noise\nDESK_CHECKPOINT BASE=003F6A00 "
            b"CHECKSUM=62D64BA4 NONZERO=157\n"
        )

        self.assertEqual(
            desk_regression.parse_checkpoint(output),
            (0x003F6A00, 0x62D64BA4, 157),
        )

    def test_rejects_missing_checkpoint(self) -> None:
        self.assertIsNone(desk_regression.parse_checkpoint(b"booting\n"))

    def test_script_drives_welcome_and_three_calibration_points(self) -> None:
        script = desk_regression.automation_script()

        self.assertIn("press(240, 160)", script)
        self.assertIn("press(23, 23)", script)
        self.assertIn("press(456, 296)", script)
        self.assertIn("DESK_CHECKPOINT", script)
        self.assertIn('snapshot("magic-cap-desk.png")', script)


if __name__ == "__main__":
    unittest.main()
