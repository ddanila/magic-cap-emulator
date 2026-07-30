import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1] / "tools" / "magicbus_hotplug_regression.py"
)
SPEC = importlib.util.spec_from_file_location(
    "magicbus_hotplug_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
regression = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = regression
SPEC.loader.exec_module(regression)


class ScriptTests(unittest.TestCase):
    def test_watches_every_lifecycle_routine(self) -> None:
        script = regression.automation_script(3000)

        for _name, address, _symbol in regression.WATCHED:
            with self.subTest(address=address):
                self.assertIn(f"0x{address:08x}", script)

    def test_drives_the_ordered_tail_lifecycle(self) -> None:
        script = regression.automation_script(3000)

        add = script.index("accessory.user_value = 2")
        remove = script.index("accessory.user_value = 1", add)
        reinsert = script.index("accessory.user_value = 2", remove)
        self.assertLess(add, remove)
        self.assertLess(remove, reinsert)
        self.assertIn('phase = "complete"', script)

    def test_exercises_keyboard_after_add_and_reinsert(self) -> None:
        script = regression.automation_script(3000)

        self.assertIn('start_key_test("key_after_add")', script)
        self.assertIn('start_key_test("key_after_reinsert")', script)
        self.assertIn("caps_lock:set_value(caps_lock.mask)", script)
        self.assertIn("caps_lock:set_value(0)", script)

    def test_saves_and_loads_the_pending_tail(self) -> None:
        state = Path("/persistent/magicbus-pending.sta")
        script = regression.automation_script(3000, state)

        self.assertIn(f'machine:save("{state}")', script)
        self.assertIn(f'machine:load("{state}")', script)
        self.assertIn('phase = "wait_saved_add"', script)
        self.assertIn("save_load = 1", script)

    def test_timeout_is_configurable(self) -> None:
        self.assertIn(
            "frames >= 1234", regression.automation_script(1234)
        )

    def test_machine_config_starts_with_one_keyboard(self) -> None:
        config = regression.machine_config("datarover840")

        self.assertIn('tag=":MAGICBUS_ACCESSORY"', config)
        self.assertIn('mask="3" defvalue="1" value="1"', config)


class ResultTests(unittest.TestCase):
    def test_parses_lifecycle_result(self) -> None:
        output = (
            b"MAGICBUS HOTPLUG phase=complete frames=1100 peripherals=2 "
            b"failures=0 low_errors=1 assign=4 \n"
        )

        result = regression.parse_result(output)

        self.assertEqual(result["phase"], "complete")
        self.assertEqual(result["peripherals"], "2")
        self.assertEqual(result["low_errors"], "1")

    def test_complete_lifecycle_is_accepted(self) -> None:
        result = {
            "phase": "complete",
            "peripherals": "2",
            "failures": "0",
            "low_errors": "1",
            "assign": "4",
            "peripheral_info": "4",
            "handle_attached": "2",
            "handle_detached": "1",
            "keyboard_attached": "2",
            "scsi_attached": "2",
            "scsi_detached": "1",
            "keyboard_requests": "4",
            "keyboard_dispatch": "4",
            "keyboard_led": "2",
            "post_add_key": "1",
            "post_reinsert_key": "1",
            "save_load": "1",
        }

        self.assertEqual(regression.acceptance_errors(result), [])

    def test_timeout_and_missing_detach_are_rejected(self) -> None:
        result = {
            "phase": "removing",
            "peripherals": "2",
            "failures": "0",
            "low_errors": "0",
            "assign": "4",
            "peripheral_info": "4",
            "handle_attached": "2",
            "handle_detached": "0",
            "keyboard_attached": "2",
            "scsi_attached": "2",
            "scsi_detached": "0",
            "keyboard_requests": "2",
            "keyboard_dispatch": "2",
            "keyboard_led": "2",
            "post_add_key": "1",
            "post_reinsert_key": "0",
            "save_load": "0",
        }

        errors = regression.acceptance_errors(result)

        self.assertIn("phase=removing", errors)
        self.assertIn("low_errors=0 (need 1)", errors)
        self.assertIn("handle_detached=0 (need 1)", errors)
        self.assertIn("scsi_detached=0 (need 1)", errors)
        self.assertIn("post_reinsert_key=0 (need 1)", errors)
        self.assertIn("save_load=0 (need 1)", errors)

    def test_missing_result_is_rejected(self) -> None:
        self.assertEqual(
            regression.acceptance_errors({}), ["result line missing"]
        )


class SystemGuardTests(unittest.TestCase):
    def test_development_rom_is_rejected(self) -> None:
        self.assertNotIn("datarover840d", regression.SUPPORTED_SYSTEMS)
        self.assertIn("datarover840", regression.SUPPORTED_SYSTEMS)


if __name__ == "__main__":
    unittest.main()
