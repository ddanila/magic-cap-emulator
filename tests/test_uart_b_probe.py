import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "uart_b_probe.py"
SPEC = importlib.util.spec_from_file_location("uart_b_probe", MODULE_PATH)
assert SPEC and SPEC.loader
uart_b_probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = uart_b_probe
SPEC.loader.exec_module(uart_b_probe)


class UartBProbeTests(unittest.TestCase):
    def test_extracts_each_uart_independently(self) -> None:
        output = (
            b"UARTA TX: 49 I\n"
            b"UARTB TX: 54 T\n"
            b"UARTA TX: 44 D\n"
        )

        self.assertEqual(uart_b_probe.extract_uart_bytes(output, "A"), b"ID")
        self.assertEqual(uart_b_probe.extract_uart_bytes(output, "B"), b"T")

    def test_parses_monitor_dump(self) -> None:
        text = (
            "<IDT>dump -w b0c000dc/1\n"
            "b0c000d0: 00000000 00000000 00000000 00000052\n"
        )

        self.assertEqual(
            uart_b_probe.monitor_dump(text, 0xB0C000DC),
            0x52,
        )

    def test_accepts_complete_exchange(self) -> None:
        terminal = (
            b"UARTA TX: 42 B\nUARTA TX: 30 0\nUARTA TX: 43 C\n"
            b"UARTA TX: 30 0\nUARTA TX: 30 0\nUARTA TX: 30 0\n"
            b"UARTA TX: 43 C\nUARTA TX: 43 C\nUARTA TX: 3a :\n"
            b"UARTA TX: 20  \nUARTA TX: 30 0\nUARTA TX: 30 0\n"
            b"UARTA TX: 30 0\nUARTA TX: 30 0\nUARTA TX: 30 0\n"
            b"UARTA TX: 30 0\nUARTA TX: 30 0\nUARTA TX: 35 5\n"
            b"UARTA TX: 0d .\n"
            b"UARTA TX: 42 B\nUARTA TX: 30 0\nUARTA TX: 43 C\n"
            b"UARTA TX: 30 0\nUARTA TX: 30 0\nUARTA TX: 30 0\n"
            b"UARTA TX: 43 C\nUARTA TX: 38 8\nUARTA TX: 3a :\n"
            b"UARTA TX: 20  \nUARTA TX: 44 D\nUARTA TX: 30 0\n"
            b"UARTA TX: 30 0\nUARTA TX: 30 0\nUARTA TX: 30 0\n"
            b"UARTA TX: 30 0\nUARTA TX: 30 0\nUARTA TX: 31 1\n"
            b"UARTA TX: 0d .\n"
            b"UARTA TX: 42 B\nUARTA TX: 30 0\nUARTA TX: 43 C\n"
            b"UARTA TX: 30 0\nUARTA TX: 30 0\nUARTA TX: 30 0\n"
            b"UARTA TX: 44 D\nUARTA TX: 43 C\nUARTA TX: 3a :\n"
            b"UARTA TX: 20  \nUARTA TX: 30 0\nUARTA TX: 30 0\n"
            b"UARTA TX: 30 0\nUARTA TX: 30 0\nUARTA TX: 30 0\n"
            b"UARTA TX: 30 0\nUARTA TX: 35 5\nUARTA TX: 32 2\n"
            b"UARTA TX: 0d .\n"
        )
        output = b"UARTB READY\n" + terminal + b"UARTB REPORT\n"

        self.assertEqual(
            uart_b_probe.acceptance_errors(output, b"T"),
            [],
        )

    def test_reports_incomplete_exchange(self) -> None:
        errors = uart_b_probe.acceptance_errors(b"", b"")

        self.assertGreaterEqual(len(errors), 6)

    def test_automation_waits_for_host_handshake(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            script = uart_b_probe.automation_script(
                Path(directory) / "host-ready",
                1000,
            )

        self.assertIn("UARTB READY", script)
        self.assertIn("io.open(host_ready", script)
        self.assertIn("fill -w", uart_b_probe.PHASE_ONE)
        self.assertIn("dump -w", uart_b_probe.PHASE_TWO)

    def test_config_selects_monitor_mode(self) -> None:
        config = uart_b_probe.machine_config("datarover840")

        self.assertIn('tag=":terminal:keyboard" enabled="1"', config)
        self.assertIn('tag=":BOOT_MODE"', config)
        self.assertIn('defvalue="8" value="0"', config)


if __name__ == "__main__":
    unittest.main()
