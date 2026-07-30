import unittest

from tools.rtc_set_regression import (
    MARKER,
    SET_TIMER,
    automation_script,
    call_stub_words,
    parse_result,
    verify_result,
)


class RtcSetRegressionTests(unittest.TestCase):
    def test_stub_calls_real_set_timer(self) -> None:
        words = call_stub_words()
        self.assertIn(0x3C190000 | (SET_TIMER >> 16), words)
        self.assertIn(0x37390000 | (SET_TIMER & 0xFFFF), words)
        self.assertIn(0x0320F809, words)

    def test_script_installs_stub_and_timeout(self) -> None:
        script = automation_script()
        self.assertIn('cpu.state["PC"].value = 0xa0300000', script)
        self.assertIn(f"== 0x{MARKER:08x}", script)
        self.assertIn("RTC_SET TIMEOUT", script)

    def test_parses_and_accepts_success_with_small_rtc_advance(self) -> None:
        output = (
            b"RTC_SET RESULT returned=00000001 target=12:34567890 actual=12:34567893\n"
        )
        result = parse_result(output)
        self.assertEqual(result, (1, 0x12, 0x34567890, 0x12, 0x34567893))
        self.assertEqual(verify_result(result), [])

    def test_rejects_failure_wrong_high_and_large_low_delta(self) -> None:
        failures = verify_result((0, 0x12, 0x34567890, 0x13, 0x34567900))
        self.assertEqual(len(failures), 3)

    def test_rejects_missing_result(self) -> None:
        self.assertEqual(
            verify_result(parse_result(b"RTC_SET TIMEOUT")),
            ["ROM SetTimer result is missing"],
        )


if __name__ == "__main__":
    unittest.main()
