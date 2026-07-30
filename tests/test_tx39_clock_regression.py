import unittest

from tools.tx39_clock_regression import (
    EXPECTED_CONFIGS,
    automation_script,
    parse_results,
    verify_results,
)


class Tx39ClockRegressionTests(unittest.TestCase):
    def test_parses_complete_results(self) -> None:
        output = (
            b"CLOCK RF=0 CONFIG=00100030 COUNT=00030000\n"
            b"CLOCK RF=1 CONFIG=00100430 COUNT=00018000\n"
            b"CLOCK RF=2 CONFIG=00100830 COUNT=0000C000\n"
            b"CLOCK RF=3 CONFIG=00100C30 COUNT=00006000\n"
            b"CLOCK_LOCK CONFIG=001008B0 COUNT=0000C000\n"
        )
        self.assertEqual(
            parse_results(output),
            (EXPECTED_CONFIGS, (0x30000, 0x18000, 0xC000, 0x6000, 0xC000)),
        )

    def test_rejects_incomplete_results(self) -> None:
        self.assertIsNone(parse_results(b"CLOCK RF=0 CONFIG=00100030 COUNT=1"))

    def test_verifier_accepts_all_divisors_and_locked_quarter_rate(self) -> None:
        parsed = (
            EXPECTED_CONFIGS,
            (200_000, 100_000, 50_000, 25_000, 50_000),
        )
        self.assertEqual(verify_results(parsed), [])

    def test_verifier_rejects_unscaled_or_unlocked_results(self) -> None:
        parsed = (
            (*EXPECTED_CONFIGS[:4], 0x00100030),
            (200_000, 200_000, 200_000, 200_000, 200_000),
        )
        self.assertGreaterEqual(len(verify_results(parsed)), 5)

    def test_script_executes_mtc0_and_fixed_time_loops(self) -> None:
        script = automation_script()
        self.assertIn("0x40881800", script)
        self.assertIn("0x40111800", script)
        self.assertIn("0x240808b0", script)
        self.assertIn("0x1000fffe", script)


if __name__ == "__main__":
    unittest.main()
