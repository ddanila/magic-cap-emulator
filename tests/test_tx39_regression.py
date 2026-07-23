import unittest

from tools.tx39_regression import EXPECTED, automation_script, parse_results


class Tx39RegressionTests(unittest.TestCase):
    def test_parses_instruction_results(self) -> None:
        output = (
            b"MADD R10=FFFFFFFF HI=FFFFFFFF LO=FFFFFFFF PC=A0001004\n"
            b"MADDU R11=FFFFFFFF HI=00000001 LO=FFFFFFFF PC=A0001024\n"
        )
        self.assertEqual(parse_results(output), EXPECTED)

    def test_rejects_missing_results(self) -> None:
        self.assertIsNone(parse_results(b"MADD missing"))

    def test_script_contains_tx39_opcodes(self) -> None:
        script = automation_script()
        self.assertIn("0x71095000", script)
        self.assertIn("0x71095801", script)
        self.assertIn('cpu.state["HI"]', script)
        self.assertIn('cpu.state["LO"]', script)


if __name__ == "__main__":
    unittest.main()
