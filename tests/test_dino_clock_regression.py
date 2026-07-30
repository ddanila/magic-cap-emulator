import unittest

from tools.dino_clock_regression import (
    MBUS_ENABLED,
    MBUS_EVENTS,
    PERIODIC_EVENT,
    SIB_BOUNDARIES,
    STOP_TIMER_EVENT,
    UART_A_TX,
    UART_B_TX,
    UART_ENABLED,
    automation_script,
    parse_results,
    verify_results,
)


class DinoClockRegressionTests(unittest.TestCase):
    def test_parses_complete_results(self) -> None:
        output = (
            b"CLOCK_OFF UARTA=40000001 UARTB=40000001 "
            b"INT1=00000000 INT2=00000000 "
            b"INT5=00000000 MBUS=40000001 RTC_A=00000100 RTC_B=00001000\n"
            b"CLOCK_ON UARTA=C0000101 UARTB=C0000001 "
            b"INT1=00000180 INT2=04010A00 "
            b"INT5=20000000 MBUS=C0000001 RTC=00002000\n"
            b"STOP_TIMER V2_PRE=00000000 V2_POST=10000000 "
            b"V8_PRE=00000000 V8_POST=10000000\n"
        )
        self.assertIsNotNone(parse_results(output))
        self.assertEqual(verify_results(parse_results(output)), [])

    def test_rejects_incomplete_results(self) -> None:
        self.assertIsNone(parse_results(b"CLOCK_OFF UART=40000001"))

    def test_verifier_rejects_ungated_and_unresumed_engines(self) -> None:
        bad = (
            UART_ENABLED,
            UART_ENABLED,
            SIB_BOUNDARIES,
            UART_A_TX | UART_B_TX | MBUS_EVENTS,
            PERIODIC_EVENT,
            MBUS_ENABLED,
            1,
            2,
            0,
            0,
            0,
            0,
            0,
            0,
            2,
            STOP_TIMER_EVENT,
            0,
            STOP_TIMER_EVENT,
            0,
        )
        self.assertGreaterEqual(len(verify_results(bad)), 17)

    def test_script_controls_major_clock_domains(self) -> None:
        script = automation_script()
        self.assertIn("local ACTIVE_CLOCKS = 0x00028803", script)
        self.assertIn("program:write_u32(SIB_CONTROL", script)
        self.assertIn("program:write_u32(UART_A_HOLD", script)
        self.assertIn("program:write_u32(UART_B_HOLD", script)
        self.assertIn("program:write_u32(MBUS_COMMAND", script)
        self.assertIn("program:write_u32(PERIODIC_TIMER", script)
        self.assertIn("stop_timer_start(2)", script)
        self.assertIn("stop_timer_start(8)", script)
        self.assertIn("program:write_u32(MASTER_CLOCK, 0)", script)
        self.assertIn("emu.attotime.from_ticks", script)


if __name__ == "__main__":
    unittest.main()
