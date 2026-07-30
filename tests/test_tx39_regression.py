import unittest

from tools.tx39_regression import (
    EXPECTED,
    EXPECTED_CACHE,
    EXPECTED_CP0,
    automation_script,
    parse_cache_results,
    parse_cp0_results,
    parse_results,
)


class Tx39RegressionTests(unittest.TestCase):
    def test_parses_instruction_results(self) -> None:
        output = (
            b"MADD R10=FFFFFFFF HI=FFFFFFFF LO=FFFFFFFF PC=A0001004\n"
            b"MADDU R11=FFFFFFFF HI=00000001 LO=FFFFFFFF PC=A0001024\n"
            b"MULT R12=FFFFFFFA HI=FFFFFFFF LO=FFFFFFFA PC=A0001044\n"
            b"MULTU R13=FFFFFFFE HI=00000001 LO=FFFFFFFE PC=A0001064\n"
        )
        self.assertEqual(parse_results(output), EXPECTED)

    def test_rejects_missing_results(self) -> None:
        self.assertIsNone(parse_results(b"MADD missing"))

    def test_parses_cp0_results(self) -> None:
        output = (
            b"CACHE_ENABLE CACHED=11111111 UNCACHED=22222222\n"
            b"CONFIG FIRST=001000DF LOCKED=001000DF\n"
            b"CACHE EXCEPTION=00000C00 RETURN=00000300\n"
        )
        self.assertEqual(parse_cp0_results(output), EXPECTED_CP0)

    def test_rejects_missing_cp0_results(self) -> None:
        self.assertIsNone(parse_cp0_results(b"CONFIG missing"))

    def test_parses_cache_results(self) -> None:
        output = (
            b"CACHE_LRU HIT=11111111 EVICTED=BBBBBBBB\n"
            b"CACHE_LOCK RETAINED=11111111 "
            b"CACHED_STORE=44444444 MEMORY=AAAAAAAA\n"
            b"CACHE_UNLOCK RELOADED=55555555\n"
            b"CACHE_NOALLOC RELOADED=77777777\n"
        )
        self.assertEqual(parse_cache_results(output), EXPECTED_CACHE)

    def test_rejects_missing_cache_results(self) -> None:
        self.assertIsNone(parse_cache_results(b"CACHE_LRU missing"))

    def test_script_contains_tx39_opcodes(self) -> None:
        script = automation_script()
        self.assertIn("0x71095000", script)
        self.assertIn("0x71095801", script)
        self.assertIn("0x01096018", script)
        self.assertIn("0x01096819", script)
        self.assertIn("0x40881800", script)
        self.assertIn("0x40883800", script)
        self.assertIn("0x42000010", script)
        self.assertIn("0xbd090000", script)
        self.assertIn("0xbd110000", script)
        self.assertIn('cpu.state["HI"]', script)
        self.assertIn('cpu.state["LO"]', script)


if __name__ == "__main__":
    unittest.main()
