import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1] / "tools" / "fax_receive_regression.py"
)
SPEC = importlib.util.spec_from_file_location(
    "fax_receive_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
fax_receive = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fax_receive
SPEC.loader.exec_module(fax_receive)


class ScriptTests(unittest.TestCase):
    def test_uses_one_ring_envelope_and_receive_fax_button(self) -> None:
        script = fax_receive.automation_script()

        self.assertEqual(script.count("ring:set_value(1)"), 1)
        self.assertEqual(script.count("ring:set_value(0)"), 1)
        self.assertIn("press(220, 156)", script)
        self.assertIn('snapshot("receiving-fax.png")', script)

    def test_traces_product_fax_and_hardware_paths(self) -> None:
        script = fax_receive.automation_script()

        for address, _ in fax_receive.SYMBOLS:
            self.assertIn(f"0x{address:08x}", script)
        self.assertIn("program:read_u32(0x10c00060)", script)
        self.assertIn("program:read_u32(0x10c00090)", script)

    def test_config_selects_external_pcm(self) -> None:
        config = fax_receive.deterministic_machine_config()

        self.assertIn('tag=":PHONE_PEER"', config)
        self.assertIn('value="2"', config)


class ResultTests(unittest.TestCase):
    def test_complete_result_parses(self) -> None:
        values = " ".join(
            f"{name}={index + 1}"
            for index, (_, name) in enumerate(fax_receive.SYMBOLS)
        )
        output = (
            f"FAX_RECEIVE_RESULT {values} "
            "telecom_words=48 telecom_enables=3\n"
        ).encode()

        result = fax_receive.parse_result(output)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["receive_now"], 1)
        self.assertEqual(result["fax_modem_transmit"], 8)
        self.assertEqual(result["telecom_words"], 48)
        self.assertEqual(result["telecom_enables"], 3)

    def test_incomplete_result_does_not_parse(self) -> None:
        self.assertIsNone(
            fax_receive.parse_result(
                b"FAX_RECEIVE_RESULT receive_now=1\n"
            )
        )


if __name__ == "__main__":
    unittest.main()
