import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "serial_regression.py"
SPEC = importlib.util.spec_from_file_location("serial_regression", MODULE_PATH)
assert SPEC and SPEC.loader
serial_regression = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = serial_regression
SPEC.loader.exec_module(serial_regression)


class SerialRegressionTests(unittest.TestCase):
    def test_extracts_uart_a_bytes_only(self) -> None:
        log = (
            b"[:] UARTA TX: 49 I\n"
            b"[:] UARTB TX: 78 x\n"
            b"[:] UARTA TX: 44 D\n"
        )

        self.assertEqual(serial_regression.extract_uart_bytes(log), b"ID")

    def test_canonicalizes_terminal_controls(self) -> None:
        data = b"\r\nTitle\r\n\r\n\b    \rReady\r\n<IDT>"

        self.assertEqual(
            serial_regression.canonicalize_terminal(data),
            "Title\nReady\n<IDT>\n",
        )

    def test_monitor_config_selects_idt_monitor(self) -> None:
        config = serial_regression.monitor_config()

        self.assertIn('tag=":terminal:keyboard" enabled="1"', config)
        self.assertIn('tag=":BOOT_MODE"', config)
        self.assertIn('defvalue="8" value="0"', config)

    def test_betty_checkpoint_calls_rom_diagnostic(self) -> None:
        checkpoint = serial_regression.CHECKPOINTS["betty"]

        self.assertEqual(checkpoint["command"], "call 13c076b0\n")
        self.assertEqual(checkpoint["seconds"], 8)

    def test_monitor_command_uses_key_matrix(self) -> None:
        script = serial_regression.monitor_command_script("call 13c076b0\n")

        self.assertIn('":terminal:keyboard:GENKBD_ROW', script)
        self.assertIn("command_index", script)
        self.assertIn("set_value", script)


if __name__ == "__main__":
    unittest.main()
