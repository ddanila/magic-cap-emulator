import unittest

from tools.tx39_power_mode_regression import (
    EXPECTED,
    automation_script,
    parse_result,
    verify_result,
)


class Tx39PowerModeRegressionTests(unittest.TestCase):
    def test_parse_and_verify_complete_result(self) -> None:
        output = (
            b"POWER HALT_STALL CONFIG=00000100 MARKER=00000000\n"
            b"POWER HALT_WAKE CONFIG=00000000 MARKER=00000001\n"
            b"POWER DOZE_STALL CONFIG=00000200 MARKER=00000000\n"
            b"POWER DOZE_WAKE CONFIG=00000000 MARKER=00000002\n"
        )
        self.assertEqual(parse_result(output), EXPECTED)
        self.assertEqual(verify_result(parse_result(output)), [])

    def test_rejects_missing_result(self) -> None:
        self.assertEqual(
            verify_result(None),
            ["missing TX39 power-mode result"],
        )

    def test_rejects_wrong_field(self) -> None:
        result = list(EXPECTED)
        result[-1] = 3
        failures = verify_result(tuple(result))
        self.assertEqual(len(failures), 1)
        self.assertIn("field 7", failures[0])

    def test_script_contains_modes_and_masked_timer_wake(self) -> None:
        script = automation_script()
        for value in (
            "0x00000130",
            "0x00000230",
            "0x40811800",
            "0x20000000",
            "0x00040000",
            'cpu.state["SR"].value = 0',
        ):
            self.assertIn(value, script)


if __name__ == "__main__":
    unittest.main()
