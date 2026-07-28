import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


MODULE_PATH = Path(__file__).parents[1] / "tools" / "power_policy_regression.py"
SPEC = importlib.util.spec_from_file_location(
    "power_policy_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
policy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = policy
SPEC.loader.exec_module(policy)


class ConfigTests(unittest.TestCase):
    def test_config_freezes_rtc_for_repeatable_screens(self) -> None:
        config = policy.config_xml()

        self.assertIn('tag=":RTC_RESUME"', config)
        self.assertIn('mask="1" defvalue="1" value="0"', config)


class ScriptTests(unittest.TestCase):
    def test_script_drives_real_power_controls_and_idle_intervals(self) -> None:
        script = policy.automation_script()

        self.assertIn("press(424, 108)", script)
        self.assertIn("press(139, 152)", script)
        self.assertIn("repeat_tap(230, 122, 10", script)
        self.assertIn("repeat_tap(306, 122, 70", script)
        self.assertIn("repeat_tap(189, 160, 1", script)
        self.assertEqual(script.count("deadline = frames + 4200"), 2)


class CheckpointTests(unittest.TestCase):
    def test_parses_both_policy_states(self) -> None:
        output = (
            b"POWER_POLICY WHEN=plugged_unchecked PC=13C3B270 "
            b"REASON=00000000 POWER=6000241B\n"
            b"POWER_POLICY WHEN=plugged_checked PC=13C3B1C8 "
            b"REASON=534C4545 POWER=60002408\n"
        )

        self.assertEqual(
            policy.parse_checkpoints(output),
            {
                "plugged_unchecked": (0x13C3B270, 0, 0x6000241B),
                "plugged_checked": (
                    policy.WAIT_FOR_POWER_DOWN,
                    policy.SLEE,
                    0x60002408,
                ),
            },
        )


class ImageTests(unittest.TestCase):
    def test_checkbox_count_ignores_border_and_finds_mark(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "checkbox.png"
            image = Image.new("L", (480, 320), 255)
            image.save(path)
            self.assertEqual(policy.checkbox_mark_pixels(path), 0)

            image.putpixel((189, 160), 0)
            image.save(path)
            self.assertEqual(policy.checkbox_mark_pixels(path), 1)

    @mock.patch.object(policy.subprocess, "run")
    def test_numeric_ocr_is_limited_to_digit_output(self, run: mock.Mock) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="60\n", stderr=""
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "screen.png"
            scratch = root / "digit.png"
            Image.new("RGB", (480, 320), "white").save(source)

            self.assertEqual(policy.read_idle_minutes(source, scratch), 60)
            self.assertTrue(scratch.is_file())


if __name__ == "__main__":
    unittest.main()
