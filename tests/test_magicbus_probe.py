import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "magicbus_probe.py"
SPEC = importlib.util.spec_from_file_location("magicbus_probe", MODULE_PATH)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


class ScriptTests(unittest.TestCase):
    def test_watches_every_routine_in_the_chain(self) -> None:
        script = probe.automation_script(9000)

        for _name, address, _symbol in probe.WATCHED:
            with self.subTest(address=address):
                self.assertIn(f"0x{address:08x}", script)

    def test_each_counter_gets_its_own_slot(self) -> None:
        slots = {
            probe.SCRATCH + index * 4 for index, _ in enumerate(probe.WATCHED)
        }

        self.assertEqual(len(slots), len(probe.WATCHED))

    def test_breakpoint_action_is_a_single_command(self) -> None:
        # Two chained `do` commands halt the machine instead of continuing,
        # which reads as the code under test hanging.
        script = probe.automation_script(9000)
        action = script[script.index('"do d@'):]
        self.assertEqual(action[: action.index('"', 1)].count("do "), 1)

    def test_report_frame_is_configurable(self) -> None:
        self.assertIn("frames == 1234", probe.automation_script(1234))

    def test_injects_a_magicbus_keyboard_key(self) -> None:
        script = probe.automation_script(9000)

        self.assertIn('":magicbus_keyboard:pc_keyboard_3"', script)
        self.assertIn("caps_lock:set_value(caps_lock.mask)", script)
        self.assertIn("caps_lock:set_value(0)", script)


class CountTests(unittest.TestCase):
    def test_parses_a_count_line(self) -> None:
        output = b"MAGICBUS COUNTS failures=3 assign=3 issue=3 poll=6 \n"

        counts = probe.parse_counts(output)

        self.assertEqual(counts["failures"], 3)
        self.assertEqual(counts["poll"], 6)

    def test_missing_line_yields_nothing(self) -> None:
        self.assertEqual(probe.parse_counts(b"Average speed: 300%\n"), {})

    def test_complete_transaction_is_accepted(self) -> None:
        counts = {
            "failures": 0,
            "low_errors": 0,
            "assign": 1,
            "peripheral_info": 1,
            "keyboard_attached": 1,
            "keyboard_requests": 1,
            "keyboard_dispatch": 1,
            "keyboard_led": 1,
        }

        self.assertEqual(probe.acceptance_errors(counts), [])

    def test_missing_dispatch_and_bus_errors_are_reported(self) -> None:
        counts = {
            "failures": 2,
            "low_errors": 1,
            "assign": 1,
            "peripheral_info": 1,
            "keyboard_attached": 1,
            "keyboard_requests": 1,
            "keyboard_led": 1,
        }

        self.assertEqual(
            probe.acceptance_errors(counts),
            ["failures=2", "low_errors=1", "keyboard_dispatch=0"],
        )


class SystemGuardTests(unittest.TestCase):
    def test_development_rom_is_rejected(self) -> None:
        # Its addresses shift, so probing it would silently measure nothing.
        self.assertNotIn("datarover840d", probe.SUPPORTED_SYSTEMS)
        self.assertIn("datarover840", probe.SUPPORTED_SYSTEMS)


if __name__ == "__main__":
    unittest.main()
