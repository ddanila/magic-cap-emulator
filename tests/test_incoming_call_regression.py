import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1] / "tools" / "incoming_call_regression.py"
)
SPEC = importlib.util.spec_from_file_location(
    "incoming_call_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
incoming_call = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = incoming_call
SPEC.loader.exec_module(incoming_call)


class ScriptTests(unittest.TestCase):
    def test_uses_one_ring_envelope_without_synthesizing_edges(self) -> None:
        script = incoming_call.automation_script()

        self.assertEqual(script.count("ring:set_value(1)"), 1)
        self.assertEqual(script.count("ring:set_value(0)"), 1)
        self.assertNotIn("// 2", script)

    def test_traces_ring_qualification_and_both_clients(self) -> None:
        script = incoming_call.automation_script()

        for address, _ in incoming_call.SYMBOLS:
            self.assertIn(f"0x{address:08x}", script)
        self.assertIn('snapshot("incoming-call.png")', script)


class ResultTests(unittest.TestCase):
    def test_complete_result_parses(self) -> None:
        output = (
            b"INCOMING_CALL_RESULT ring_isr=100 ring_completion=1 "
            b"continue_ring=1 trigger_clients=1 phone_server=1 fax_receive=1\n"
        )

        self.assertEqual(
            incoming_call.parse_result(output),
            {
                "ring_isr": 100,
                "ring_completion": 1,
                "continue_ring": 1,
                "trigger_clients": 1,
                "phone_server": 1,
                "fax_receive": 1,
            },
        )

    def test_incomplete_result_does_not_parse(self) -> None:
        self.assertIsNone(
            incoming_call.parse_result(
                b"INCOMING_CALL_RESULT ring_isr=100\n"
            )
        )


if __name__ == "__main__":
    unittest.main()
