import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1] / "tools" / "telephone_line_regression.py"
)
SPEC = importlib.util.spec_from_file_location(
    "telephone_line_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
telephone_line = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = telephone_line
SPEC.loader.exec_module(telephone_line)


class ConfigTests(unittest.TestCase):
    def test_config_selects_monitor_mode(self) -> None:
        config = telephone_line.config_xml("datarover840")

        self.assertIn('tag=":BOOT_MODE"', config)
        self.assertIn('mask="8" defvalue="8" value="0"', config)


class ScriptTests(unittest.TestCase):
    def test_script_toggles_hookswitch_and_both_ring_edges(self) -> None:
        script = telephone_line.automation_script()

        self.assertIn("program:write_u32(SIB_SF0_AUX, 0x04000200)", script)
        self.assertIn("program:write_u32(SIB_SF0_AUX, 0x04000000)", script)
        self.assertIn("ring:set_value(1)", script)
        self.assertIn("ring:set_value(0)", script)
        self.assertIn("MFIO_INPUT = 0x10c0018c", script)

    def test_pulse_script_dials_five_eight_and_ten_pulses(self) -> None:
        script = telephone_line.pulse_automation_script()

        self.assertIn("local digits = { 5, 8, 10 }", script)
        self.assertIn("set_offhook(false)", script)
        self.assertIn("set_offhook(true)", script)
        self.assertIn("next_transition = frames + 30", script)


class ResultTests(unittest.TestCase):
    def test_parses_complete_result(self) -> None:
        output = (
            b"TELEPHONE_LINE CONNECTED=1 OFFHOOK=1 ONHOOK=1 "
            b"RING_HIGH=1 RING_POS=1 RING_LOW=1 RING_NEG=1\n"
        )

        self.assertEqual(
            telephone_line.parse_result(output),
            telephone_line.EXPECTED_RESULT,
        )

    def test_rejects_partial_result(self) -> None:
        self.assertIsNone(
            telephone_line.parse_result(b"TELEPHONE_LINE CONNECTED=1\n")
        )

    def test_parses_pulse_digits(self) -> None:
        output = (
            b"Telephone exchange pulse digit: 5\n"
            b"Telephone exchange pulse digit: 8\n"
            b"Telephone exchange pulse digit: 0\n"
        )

        self.assertEqual(telephone_line.parse_pulse_result(output), "580")


if __name__ == "__main__":
    unittest.main()
