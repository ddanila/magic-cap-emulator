#!/usr/bin/env python3
"""Tests for the power-on password regression."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


MODULE_PATH = Path(__file__).parents[1] / "tools" / "password_wake_regression.py"
SPEC = importlib.util.spec_from_file_location("password_wake_regression", MODULE_PATH)
assert SPEC and SPEC.loader
password_wake = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = password_wake
SPEC.loader.exec_module(password_wake)


class PasswordWakeRegressionTests(unittest.TestCase):
    def test_parses_config_sleep_and_wake_results(self) -> None:
        output = (
            b"PASSWORD_CONFIG set=1 prompt=0 text=3\n"
            b"PASSWORD_SLEEP A pc=13C3B1C8 reason=504F4646 power=20002408\n"
            b"PASSWORD_SLEEP B pc=13C3B1C8 reason=504F4646 power=20002408\n"
            b"PASSWORD_WAKE should=1 open=1 bad=0 close=0\n"
        )
        self.assertEqual(password_wake.parse_config(output), (1, 0, 3))
        self.assertEqual(
            password_wake.parse_sleep(output)["B"],
            (
                password_wake.WAIT_FOR_POWER_DOWN,
                password_wake.POFF,
                0x20002408,
            ),
        )
        self.assertEqual(password_wake.parse_wake(output), (1, 1, 0, 0))

    def test_configure_script_sets_pin_twice_and_selects_every_time(self) -> None:
        script = password_wake.configure_script()
        self.assertEqual(script.count("press(295, 100)"), 2)
        self.assertEqual(script.count("press(220, 237)"), 2)
        self.assertIn('ports[":POWER_BUTTON"]', script)
        self.assertIn("PASSWORD_CONFIG", script)
        self.assertIn("PASSWORD_SLEEP", script)

    def test_wake_script_tries_wrong_then_correct_pin(self) -> None:
        script = password_wake.wake_script()
        self.assertEqual(script.count("press(432, 198)"), 4)
        self.assertIn("press(289, 96)", script)
        self.assertIn("press(358, 96)", script)
        self.assertIn("press(427, 96)", script)
        self.assertIn("press(289, 146)", script)
        self.assertEqual(script.count("power_button:set_value(1)"), 2)

    def test_image_comparison_supports_policy_crop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            Image.new("RGB", (480, 320), "white").save(first)
            changed = Image.new("RGB", (480, 320), "white")
            changed.putpixel((300, 100), (0, 0, 0))
            changed.save(second)
            self.assertFalse(password_wake.images_equal(first, second))
            self.assertTrue(
                password_wake.images_equal(first, second, (0, 200, 240, 260))
            )


if __name__ == "__main__":
    unittest.main()
