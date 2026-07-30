import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


MODULE_PATH = Path(__file__).parents[1] / "tools" / "touch_alignment_regression.py"
SPEC = importlib.util.spec_from_file_location(
    "touch_alignment_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
alignment = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = alignment
SPEC.loader.exec_module(alignment)


class ScriptTests(unittest.TestCase):
    def test_script_uses_controls_adjust_and_all_three_targets(self) -> None:
        script = alignment.automation_script()

        self.assertIn("press(455, 8)", script)
        self.assertIn("press(424, 108)", script)
        self.assertIn("press(240, 75)", script)
        self.assertIn("press(275, 133)", script)
        self.assertLess(
            script.rindex("press(23, 23)"), script.rindex("press(456, 296)")
        )
        self.assertLess(
            script.rindex("press(456, 296)"), script.rindex("press(240, 160)")
        )

    def test_script_watches_real_calibration_methods(self) -> None:
        script = alignment.automation_script()

        self.assertIn("0x13e132f0", script)
        self.assertIn("0x13e12d04", script)
        self.assertIn("0x13e128c8", script)
        self.assertIn("TOUCH_ALIGNMENT calibrate=%d touch=%d commit=%d", script)


class ResultTests(unittest.TestCase):
    def test_parse_result(self) -> None:
        self.assertEqual(
            alignment.parse_result(
                b"TOUCH_ALIGNMENT calibrate=1 touch=3 commit=1\n"
            ),
            (1, 3, 1),
        )
        self.assertIsNone(alignment.parse_result(b"TOUCH_ALIGNMENT incomplete"))

    def test_panel_comparison_ignores_status_bar_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = root / "before.png"
            after = root / "after.png"
            first = Image.new("RGB", (480, 320), "white")
            second = first.copy()
            second.putpixel((10, 10), (0, 0, 0))
            first.save(before)
            second.save(after)
            self.assertTrue(alignment.screen_panel_matches(before, after))

            second.putpixel((240, 100), (0, 0, 0))
            second.save(after)
            self.assertFalse(alignment.screen_panel_matches(before, after))


if __name__ == "__main__":
    unittest.main()
