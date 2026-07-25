import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "power_regression.py"
SPEC = importlib.util.spec_from_file_location("power_regression", MODULE_PATH)
assert SPEC and SPEC.loader
power_regression = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = power_regression
SPEC.loader.exec_module(power_regression)


class PowerRegressionTests(unittest.TestCase):
    def test_parses_dino_and_cpu_checkpoints(self) -> None:
        output = (
            b"POWER_CHECK BUTTON_ASSERTED PC=13C3B344 "
            b"REASON=504F4646 INT5=50800000 INT5EN=D8808208 "
            b"INT6=40000000 INT6EN=00040000 POWER=A0008C0B\n"
        )

        self.assertEqual(
            power_regression.parse_checkpoints(output)["BUTTON_ASSERTED"],
            (
                0x13C3B344,
                0x504F4646,
                0x50800000,
                0xD8808208,
                0x40000000,
                0x00040000,
                0xA0008C0B,
            ),
        )

    def test_suspend_script_calibrates_then_powers_off(self) -> None:
        script = power_regression.suspend_script()

        self.assertIn("press(240, 160)", script)
        self.assertIn("press(23, 23)", script)
        self.assertIn("press(456, 296)", script)
        self.assertIn('checkpoint("SLEEP_A")', script)
        self.assertIn("power_button:set_value(1)", script)

    def test_wake_script_checks_edge_while_button_is_held(self) -> None:
        script = power_regression.wake_script()

        asserted = script.index('checkpoint("BUTTON_ASSERTED")')
        pressed = script.rindex("power_button:set_value(1)")
        released = script.rindex("power_button:set_value(0)")
        self.assertLess(pressed, asserted)
        self.assertLess(asserted, released)
        self.assertIn('checkpoint("WARM_DOZE_A")', script)
        self.assertIn('checkpoint("CLEANUP_BUTTON")', script)
        self.assertIn('checkpoint("FINAL_SLEEP_B")', script)
        self.assertIn('checkpoint("WOKE_B")', script)
        self.assertIn("press(421, 70)", script)


if __name__ == "__main__":
    unittest.main()
