import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "magicbus_scsi_probe.py"
SPEC = importlib.util.spec_from_file_location("magicbus_scsi_probe", MODULE_PATH)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


class ScriptTests(unittest.TestCase):
    def test_script_holds_request_through_enumeration(self) -> None:
        script = probe.automation_script(1800)

        self.assertIn('"Magic Bus SCTG get-data request"', script)
        self.assertIn('"Magic Bus SCTG send-data request"', script)
        self.assertIn("request:set_value(request.mask)", script)
        self.assertIn("send_request:set_value(send_request.mask)", script)
        self.assertIn("local send_requested = false", script)
        self.assertIn("second_command_frame = frames + 30", script)
        self.assertIn("command_index = 1", script)
        self.assertIn("second_command_frame = -1", script)
        self.assertIn(f"program:read_u32({probe.SCSI_TARGET_PERIPH})", script)

    def test_monitor_command_opens_scsi_transport(self) -> None:
        self.assertEqual(probe.MONITOR_COMMAND, "magicbus -i\n")
        script = probe.automation_script(1800)
        self.assertEqual(script.count('":terminal:keyboard:GENKBD_ROW'), 12)
        self.assertIn("key[1]:set_value(key[2])", script)
        self.assertIn("key[1]:set_value(0)", script)

    def test_script_watches_each_monitor_routine(self) -> None:
        script = probe.automation_script(1800)

        for _name, address, _symbol in probe.WATCHED:
            self.assertIn(f"0x{address:08x}", script)

    def test_config_selects_monitor_and_scsi_target(self) -> None:
        config = probe.machine_config("datarover840")

        self.assertIn('tag=":BOOT_MODE"', config)
        self.assertIn('tag=":MAGICBUS_ACCESSORY"', config)
        self.assertIn('mask="3" defvalue="1" value="3"', config)


class ResultTests(unittest.TestCase):
    def test_complete_result_is_accepted(self) -> None:
        output = (
            b"MAGICBUS SCTG address=0 init=1 init_done=1 check=4 get_data=1 "
            b"get_done=1 send_dispatch=1 send_data=1 command=2 open=2 "
            b"assign=2 transaction=17 peripherals=1\n"
            b"Magic Bus SCTG accepted 16 host bytes\n"
        )
        result = probe.parse_result(output)

        self.assertEqual(
            result,
            {
                "address": 0,
                "init": 1,
                "init_done": 1,
                "check": 4,
                "get_data": 1,
                "get_done": 1,
                "send_dispatch": 1,
                "send_data": 1,
                "command": 2,
                "open": 2,
                "assign": 2,
                "transaction": 17,
                "peripherals": 1,
                "host_bytes": 16,
            },
        )
        assert result is not None
        self.assertEqual(probe.acceptance_errors(result), [])

    def test_incomplete_result_is_rejected(self) -> None:
        result = {
            "address": 1,
            "init": 1,
            "init_done": 0,
            "check": 0,
            "get_data": 0,
            "get_done": 0,
            "send_dispatch": 0,
            "send_data": 0,
            "command": 0,
            "open": 0,
            "assign": 0,
            "transaction": 0,
            "peripherals": 0,
            "host_bytes": 0,
        }

        self.assertEqual(
            probe.acceptance_errors(result),
            [
                "address=1 (need 0)",
                "peripherals=0 (need 1)",
                "init_done=0 (need 1)",
                "check=0 (need 1)",
                "get_data=0 (need 1)",
                "get_done=0 (need 1)",
                "send_dispatch=0 (need 1)",
                "send_data=0 (need 1)",
                "command=0 (need 1)",
                "open=0 (need 1)",
                "assign=0 (need 1)",
                "transaction=0 (need 1)",
                "host_bytes=0 (need 16)",
            ],
        )


if __name__ == "__main__":
    unittest.main()
