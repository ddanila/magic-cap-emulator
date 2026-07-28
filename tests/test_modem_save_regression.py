import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "modem_save_regression.py"
SPEC = importlib.util.spec_from_file_location(
    "modem_save_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
modem_save = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = modem_save
SPEC.loader.exec_module(modem_save)


class ConfigTests(unittest.TestCase):
    def test_uses_quiet_monitor_boot(self) -> None:
        config = modem_save.monitor_config()

        self.assertIn('tag=":BOOT_MODE"', config)
        self.assertIn('mask="8" defvalue="8" value="0"', config)


class ScriptTests(unittest.TestCase):
    def test_preserves_then_corrupts_every_uart_state_group(self) -> None:
        script = modem_save.automation_script("/tmp/modem.sta")

        self.assertIn('machine:save("/tmp/modem.sta")', script)
        self.assertIn('machine:load("/tmp/modem.sta")', script)
        self.assertIn("program:write_u8(config_option, 0x41)", script)
        self.assertIn("write_uart(2, 0x03)", script)
        self.assertIn("local glacier_pending", script)
        self.assertIn("CD_EDGES=%04X", script)


class ResultTests(unittest.TestCase):
    def test_parses_complete_restore(self) -> None:
        output = (
            b"MODEM_SAVE CONFIG=41 IER=03 LCR=03 MCR=0B DIV=1234 "
            b"SCR=5A IIR=C4,C2,C1 RX=42434400 "
            b"GLACIER=0302,0306 CD_EDGES=0000\n"
        )

        self.assertEqual(modem_save.parse_result(output), modem_save.EXPECTED_RESULT)

    def test_rejects_partial_restore(self) -> None:
        self.assertIsNone(modem_save.parse_result(b"MODEM_SAVE CONFIG=41\n"))


if __name__ == "__main__":
    unittest.main()
