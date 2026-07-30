import unittest

from tools.tx39_debug_regression import (
    EXPECTED,
    automation_script,
    parse_result,
    verify_result,
)


class Tx39DebugRegressionTests(unittest.TestCase):
    def test_parses_complete_result(self) -> None:
        output = (
            b"DEBUG_BREAK DEBUG=40000002 DEPC=A0001800\n"
            b"DEBUG_DELAY DEBUG=C0000002 DEPC=A0001840\n"
            b"DEBUG_DERET SEEN=C0000002 DEBUG=80000002 "
            b"DEPC=000018C0 SR=00000003\n"
            b"DEBUG_STEP DEBUG=40000101 DEPC=A0001908 R18=00000000\n"
            b"DEBUG_SUPPRESS SEEN=40000101 DEBUG=40000101 "
            b"DEPC=00001950 DELAY=00000001\n"
            b"DEBUG_NIS DEBUG=40004101 DEPC=A00019C0 "
            b"EPC=A00019C0 SR=00100000\n"
            b"DEBUG_OES DEBUG=40001101 DEPC=A0001A40 "
            b"EPC=A0001A40 CAUSE=00000400 SR=00000404\n"
            b"DEBUG_BSF_LOAD DEBUG=40000400 R3=00000001 "
            b"CAUSE=00000000 EPC=00000000\n"
            b"DEBUG_BSF_STORE DEBUG=40000400 R3=00000002 "
            b"CAUSE=00000000 EPC=00000000\n"
            b"NMI_CACHE SR=00100000 R3=00000003\n"
            b"NMI_CLEAR SR=00000000 R3=00000004\n"
        )
        self.assertEqual(parse_result(output), EXPECTED)
        self.assertEqual(verify_result(parse_result(output)), [])

    def test_rejects_missing_result(self) -> None:
        self.assertEqual(verify_result(None), ["missing TX39 debug result"])

    def test_rejects_wrong_field(self) -> None:
        result = list(EXPECTED)
        result[-1] = 3
        failures = verify_result(tuple(result))
        self.assertEqual(len(failures), 1)
        self.assertIn("field 35", failures[0])

    def test_script_contains_debug_instructions(self) -> None:
        script = automation_script()
        for opcode in (
            "0x0048d14e",
            "0x40108000",
            "0x40918000",
            "0x40918800",
            "0x40816000",
            "0x4200001f",
        ):
            self.assertIn(opcode, script)
        self.assertIn('cpu.state["Debug"]', script)
        self.assertIn('cpu.state["DEPC"]', script)
        self.assertIn('cpu.state["NMI"]', script)
        self.assertIn('cpu.state["BERR"]', script)
        self.assertIn("cpu.debug:bpset", script)
        self.assertIn("install_read_tap", script)
        self.assertIn("install_write_tap", script)


if __name__ == "__main__":
    unittest.main()
