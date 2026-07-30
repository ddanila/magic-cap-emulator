import unittest

from tools.dino_biu_regression import (
    EXPECTED,
    automation_script,
    parse_result,
    verify_result,
)


class DinoBiuRegressionTests(unittest.TestCase):
    def test_parses_complete_result(self) -> None:
        output = (
            b"DINO_BIU CFG0=01011091 CFG1=FFFFFFFF CFG2=2222FF66 "
            b"CFG3=44FF0100 CFG4=01604000\n"
        )
        self.assertEqual(parse_result(output), EXPECTED)
        self.assertEqual(verify_result(parse_result(output)), [])

    def test_rejects_missing_result(self) -> None:
        self.assertEqual(verify_result(None), ["missing Dino BIU result"])

    def test_rejects_wrong_register(self) -> None:
        result = list(EXPECTED)
        result[2] = 0
        failures = verify_result(tuple(result))
        self.assertEqual(len(failures), 1)
        self.assertIn("CFG2", failures[0])

    def test_script_reads_all_five_registers(self) -> None:
        script = automation_script()
        for offset in ("0x00", "0x04", "0x08", "0x0c", "0x10"):
            self.assertIn(f"DINO + {offset}", script)
        self.assertIn("cfg1 == 0xffffffff", script)
        self.assertIn("cfg4 == 0x01604000", script)


if __name__ == "__main__":
    unittest.main()
