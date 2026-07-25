import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1] / "tools" / "builtin_modem_regression.py"
)
SPEC = importlib.util.spec_from_file_location(
    "builtin_modem_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
builtin_modem = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builtin_modem
SPEC.loader.exec_module(builtin_modem)


class ScriptTests(unittest.TestCase):
    def test_calls_real_rom_modem_and_v32_entries(self) -> None:
        script = builtin_modem.modem_script()

        for word in (
            "0x37399a08",  # SoftwareModem_OpenModemPort
            "0x3739bf80",  # StartDataModem
            "0x373931dc",  # DataModemInstallModulation
            "0x37393b3c",  # SibCmdStartTelecom
            "0x373918e0",  # V32ModulatorFIR
        ):
            self.assertIn(word, script)

    def test_selects_v32_and_records_the_live_dma_state(self) -> None:
        script = builtin_modem.modem_script()

        self.assertIn("0x34040080", script)
        self.assertIn("0x8d090090", script)
        self.assertIn("0xad490148", script)

    def test_resumes_an_idle_cpu_before_redirecting_its_pc(self) -> None:
        script = builtin_modem.modem_script()

        self.assertIn('machine.debugger:command("resume :maincpu")', script)
        self.assertIn('cpu.state["PC"].value = 0xa0300000', script)

    def test_breakpoints_cover_v32_fir_and_madd(self) -> None:
        script = builtin_modem.modem_script()

        self.assertIn("0x13e518e0", script)
        self.assertIn("0x13e51974", script)


class ResultTests(unittest.TestCase):
    def test_complete_trace_matches(self) -> None:
        output = (
            b"BUILTIN_MODEM_RESULT open=1 spawn=1 server=1 dma_start=1 "
            b"half=1 full=1 init=1 receive=1 transmit=1 install=1 "
            b"v32pump=1 v32control=1 v32fir=1 madd=1 returned=1 "
            b"enables=3 size=48 tx=4000C838 rx=4000C778\n"
        )

        match = builtin_modem.RESULT_PATTERN.search(output)

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group(17), b"48")
        self.assertEqual(match.group(18), b"4000C838")
        self.assertEqual(match.group(19), b"4000C778")

    def test_incomplete_trace_does_not_match(self) -> None:
        self.assertIsNone(
            builtin_modem.RESULT_PATTERN.search(b"BUILTIN_MODEM_CALL\n")
        )


if __name__ == "__main__":
    unittest.main()
