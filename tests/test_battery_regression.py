import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "battery_regression.py"
SPEC = importlib.util.spec_from_file_location("battery_regression", MODULE_PATH)
assert SPEC and SPEC.loader
battery = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = battery
SPEC.loader.exec_module(battery)


class ConfigTests(unittest.TestCase):
    def test_healthy_selects_the_default_settings(self) -> None:
        config = battery.config_xml("datarover840d", battery.HEALTHY)

        self.assertIn('mask="3" defvalue="0" value="0"', config)
        self.assertIn('mask="12" defvalue="0" value="0"', config)

    def test_backup_empty_sets_only_the_backup_field(self) -> None:
        config = battery.config_xml("datarover840d", battery.BACKUP_EMPTY)

        self.assertIn('mask="3" defvalue="0" value="0"', config)
        self.assertIn('mask="12" defvalue="0" value="8"', config)

    def test_config_names_the_requested_system(self) -> None:
        self.assertIn(
            'name="datarover840"', battery.config_xml("datarover840", 0)
        )


class ScriptTests(unittest.TestCase):
    def test_script_calibrates_before_measuring(self) -> None:
        script = battery.automation_script("healthy.png")

        for target in ("press(240, 160)", "press(23, 23)", "press(456, 296)"):
            self.assertIn(target, script)
        self.assertIn("BATTERY_CHECKPOINT", script)
        self.assertIn("healthy.png", script)


class CheckpointTests(unittest.TestCase):
    def test_parses_a_screen_checksum(self) -> None:
        output = b"BATTERY_CHECKPOINT SCREEN=54C0E76A\n"

        self.assertEqual(battery.parse_checkpoint(output), 0x54C0E76A)

    def test_missing_checkpoint_is_none(self) -> None:
        self.assertIsNone(battery.parse_checkpoint(b"Average speed: 300%\n"))


if __name__ == "__main__":
    unittest.main()
