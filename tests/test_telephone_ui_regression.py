import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1] / "tools" / "telephone_ui_regression.py"
)
SPEC = importlib.util.spec_from_file_location(
    "telephone_ui_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
telephone_ui = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = telephone_ui
SPEC.loader.exec_module(telephone_ui)


class ScriptTests(unittest.TestCase):
    def test_uses_visible_telephone_path_and_number(self) -> None:
        script = telephone_ui.automation_script()

        for action in (
            "press(34, 302)",
            "press(55, 175)",
            "press(97, 120)",
            "press(97, 181)",
            "press(97, 244)",
            "press(240, 92)",
        ):
            self.assertIn(action, script)
        self.assertIn('snapshot("number.png")', script)
        self.assertIn('snapshot("calling.png")', script)

    def test_traces_product_actors_and_hardware_boundary(self) -> None:
        script = telephone_ui.automation_script()

        for address, _ in telephone_ui.SYMBOLS:
            self.assertIn(f"0x{address:08x}", script)
        self.assertIn("program:read_u32(0x10c00060)", script)
        self.assertIn("program:read_u32(0x10c00090)", script)

    def test_config_selects_the_automatic_exchange(self) -> None:
        config = telephone_ui.deterministic_machine_config()

        self.assertIn('tag=":PHONE_PEER"', config)
        self.assertIn('value="1"', config)

    def test_exchange_digits_parse_in_order(self) -> None:
        output = (
            b"Telephone exchange DTMF: 5\n"
            b"Telephone exchange DTMF: 8\n"
            b"Telephone exchange DTMF: 0\n"
        )

        self.assertEqual(
            telephone_ui.DTMF_PATTERN.findall(output),
            [b"5", b"8", b"0"],
        )


class ResultTests(unittest.TestCase):
    def test_complete_result_parses(self) -> None:
        output = (
            b"TELEPHONE_UI_RESULT dialer=1 start_call=1 server_dial=1 "
            b"audio_dialing=2 start_monitor=1 phone_half=2010 "
            b"phone_full=2009 daa_offhook=1 sib_offhook=1 "
            b"telecom_start=1 softmodem_dial=1 dialer_init=1 "
            b"call_progress=6 block_scale=300 sound_size=48 telecom_size=48 "
            b"sound_enables=3 telecom_enables=3 sound_tx=00357348 "
            b"telecom_tx=00357288 telecom_rx=003571C8\n"
        )

        result = telephone_ui.parse_result(output)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["dialer"], 1)
        self.assertEqual(result["phone_half"], 2010)
        self.assertEqual(result["call_progress"], 6)
        self.assertEqual(result["block_scale"], 300)
        self.assertEqual(result["sound_tx"], 0x0035_7348)
        self.assertEqual(result["telecom_rx"], 0x0035_71C8)

    def test_incomplete_result_does_not_parse(self) -> None:
        self.assertIsNone(
            telephone_ui.parse_result(b"TELEPHONE_UI_RESULT dialer=1\n")
        )


if __name__ == "__main__":
    unittest.main()
