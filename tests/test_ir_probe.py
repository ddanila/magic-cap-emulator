import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "ir_probe.py"
SPEC = importlib.util.spec_from_file_location("ir_probe", MODULE_PATH)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


def counts(**overrides: int) -> dict[str, int]:
    base = {
        "irda_init": 1,
        "irlap_init": 1,
        "irlap_open": 1,
        "daemon_main": 1,
        "beam_init": 2,
        "pulsed_mode": 1,
        "beam_discover": 1,
        "daemon_active": 1,
        "uart_a": 0x05014000,
        "uart_b": 0x05014100,
    }
    base.update(overrides)
    return base


class ScriptTests(unittest.TestCase):
    def test_watches_the_whole_stack(self) -> None:
        script = probe.automation_script(9000)

        for _name, address, _symbol in probe.WATCHED:
            with self.subTest(address=address):
                self.assertIn(f"0x{address:08x}", script)

    def test_reads_both_uart_control_registers(self) -> None:
        script = probe.automation_script(9000)

        self.assertIn(f"0x{probe.UART_A_CONTROL1:08x}", script)
        self.assertIn(f"0x{probe.UART_B_CONTROL1:08x}", script)

    def test_breakpoint_action_is_a_single_command(self) -> None:
        script = probe.automation_script(9000)
        action = script[script.index('"do d@'):]

        self.assertEqual(action[: action.index('"', 1)].count("do "), 1)


class ParseTests(unittest.TestCase):
    def test_parses_counts_and_uart_state(self) -> None:
        output = (
            b"IR COUNTS irda_init=1 irlap_init=1 irlap_open=0 "
            b"uartA=05014000 uartB=05014100\n"
        )

        parsed = probe.parse_counts(output)

        self.assertEqual(parsed["irda_init"], 1)
        self.assertEqual(parsed["irlap_open"], 0)
        self.assertEqual(parsed["uart_a"], 0x05014000)
        self.assertEqual(parsed["uart_b"], 0x05014100)

    def test_missing_line_yields_nothing(self) -> None:
        self.assertEqual(probe.parse_counts(b"Average speed: 300%\n"), {})


class VerdictTests(unittest.TestCase):
    def test_boot_bring_up_is_accepted(self) -> None:
        self.assertEqual(probe.boot_errors(counts()), [])

    def test_missing_bring_up_is_reported(self) -> None:
        self.assertEqual(
            probe.boot_errors(counts(irlap_init=0, daemon_main=0)),
            ["irlap_init", "daemon_main"],
        )

    def test_link_needs_open_and_a_pulsed_uart(self) -> None:
        self.assertEqual(probe.link_errors(counts()), [])

    def test_link_without_pulsed_mode_is_rejected(self) -> None:
        # Both ports wired: IrDA SIR never engaged.
        errors = probe.link_errors(counts(uart_b=0x05014000))

        self.assertIn("no UART in pulsed mode", errors)

    def test_link_without_irlap_open_is_rejected(self) -> None:
        self.assertIn("irlap_open", probe.link_errors(counts(irlap_open=0)))


class SystemGuardTests(unittest.TestCase):
    def test_development_rom_is_rejected(self) -> None:
        self.assertNotIn("datarover840d", probe.SUPPORTED_SYSTEMS)


if __name__ == "__main__":
    unittest.main()
