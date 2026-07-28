import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "power_outputs_regression.py"
SPEC = importlib.util.spec_from_file_location(
    "power_outputs_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
power_outputs = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = power_outputs
SPEC.loader.exec_module(power_outputs)


class ConfigTests(unittest.TestCase):
    def test_config_selects_monitor_and_low_main_battery(self) -> None:
        config = power_outputs.config_xml("datarover840")

        self.assertIn('tag=":BOOT_MODE"', config)
        self.assertIn('mask="8" defvalue="8" value="0"', config)
        self.assertIn('tag=":BATTERY"', config)
        self.assertIn('mask="3" defvalue="0" value="1"', config)


class ScriptTests(unittest.TestCase):
    def test_script_exercises_all_three_outputs(self) -> None:
        script = power_outputs.automation_script()

        self.assertIn("LCD_POWER = 0x00020000", script)
        self.assertIn("MBUS_VCC_OFF = 0x00010000", script)
        self.assertIn("CHARGER_ENABLE = 0x00000002", script)
        self.assertIn('screen:snapshot("lcd-on.png")', script)
        self.assertIn('screen:snapshot("lcd-off.png")', script)
        self.assertIn('ac:set_value(1)', script)


class CheckpointTests(unittest.TestCase):
    def test_parses_battery_values(self) -> None:
        output = (
            b"POWER_OUTPUT BATTERY WHEN=detached_before ADC=200\n"
            b"POWER_OUTPUT BATTERY WHEN=attached_after ADC=208\n"
        )

        self.assertEqual(
            power_outputs.parse_battery_checkpoints(output),
            {"detached_before": 200, "attached_after": 208},
        )

    def test_parses_magicbus_values(self) -> None:
        output = (
            b"POWER_OUTPUT MAGICBUS POWERED=1 OFF=0 REDISCOVERED=1\n"
        )

        self.assertEqual(
            power_outputs.parse_magicbus_checkpoint(output), (1, 0, 1)
        )

    def test_missing_magicbus_checkpoint_is_none(self) -> None:
        self.assertIsNone(
            power_outputs.parse_magicbus_checkpoint(b"Average speed: 400%\n")
        )


if __name__ == "__main__":
    unittest.main()
