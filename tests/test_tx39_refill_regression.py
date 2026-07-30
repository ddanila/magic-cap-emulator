import unittest

from tools.tx39_refill_regression import EXPECTED, automation_script, parse_results


class Tx39RefillRegressionTests(unittest.TestCase):
    def test_parses_complete_results(self) -> None:
        output = (
            b"DATA_SINGLE ADJACENT=BBBBBBBB\n"
            b"DATA_BURST ADJACENT=22222222\n"
            b"DATA_LOCK ADJACENT=22222222\n"
            b"ICACHE_PREFETCH TARGET=00001234\n"
        )
        self.assertEqual(parse_results(output), EXPECTED)

    def test_rejects_incomplete_results(self) -> None:
        self.assertIsNone(parse_results(b"DATA_SINGLE ADJACENT=BBBBBBBB"))

    def test_script_selects_refill_modes_and_cache_operations(self) -> None:
        script = automation_script()
        self.assertIn("0x24080030", script)
        self.assertIn("0x24080070", script)
        self.assertIn("0xbd200000", script)
        self.assertIn("0xbd310000", script)
        self.assertIn("0x40883800", script)
        self.assertIn("run_cached(0x00005008)", script)


if __name__ == "__main__":
    unittest.main()
