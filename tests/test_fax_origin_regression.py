import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1] / "tools" / "fax_origin_regression.py"
)
SPEC = importlib.util.spec_from_file_location(
    "fax_origin_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
fax_origin = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fax_origin
SPEC.loader.exec_module(fax_origin)


class ScriptTests(unittest.TestCase):
    def test_uses_visible_fax_and_recipient_creation_workflow(self) -> None:
        script = fax_origin.automation_script()

        for action in (
            "press(181, 301)",
            "press(205, 146)",
            "press(157, 157)",
            "press(345, 177)",
            'emu.keypost("5551212")',
            'emu.keypost("Fax")',
            'emu.keypost("Peer")',
            "press(326, 210)",
        ):
            self.assertIn(action, script)
        self.assertIn('snapshot("fax-addressed.png")', script)
        self.assertIn('snapshot("sending-fax.png")', script)

    def test_traces_fax_origin_and_hardware_paths(self) -> None:
        script = fax_origin.automation_script()

        for address, _ in fax_origin.SYMBOLS:
            self.assertIn(f"0x{address:08x}", script)
        self.assertIn("program:read_u32(0x10c00060)", script)
        self.assertIn("program:read_u32(0x10c00090)", script)


class ResultTests(unittest.TestCase):
    def test_complete_result_parses(self) -> None:
        values = " ".join(
            f"{name}={index + 1}"
            for index, (_, name) in enumerate(fax_origin.SYMBOLS)
        )
        output = (
            f"FAX_ORIGIN_RESULT {values} "
            "telecom_words=48 telecom_enables=3\n"
        ).encode()

        result = fax_origin.parse_result(output)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["connect_number"], 1)
        self.assertEqual(result["line_handler"], 4)
        self.assertEqual(result["telecom_words"], 48)

    def test_incomplete_result_does_not_parse(self) -> None:
        self.assertIsNone(
            fax_origin.parse_result(
                b"FAX_ORIGIN_RESULT connect_number=1\n"
            )
        )

    def test_dtmf_digits_parse_in_order(self) -> None:
        output = b"".join(
            f"Telephone exchange DTMF: {digit}\n".encode()
            for digit in "5551212"
        )

        self.assertEqual(
            b"".join(fax_origin.DTMF_PATTERN.findall(output)),
            b"5551212",
        )


if __name__ == "__main__":
    unittest.main()
