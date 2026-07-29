import importlib.util
import socket
import sys
import time
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "fax_pair_regression.py"
SPEC = importlib.util.spec_from_file_location("fax_pair_regression", MODULE_PATH)
assert SPEC and SPEC.loader
fax_pair = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fax_pair
SPEC.loader.exec_module(fax_pair)


class ScriptTests(unittest.TestCase):
    def test_origin_uses_complete_visible_fax_workflow(self) -> None:
        script = fax_pair.automation_script("origin", Path("/tmp/ring.trigger"))

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
        self.assertIn('snapshot("fax-origin-active.png")', script)

    def test_answer_uses_byte_gate_ring_and_receive_fax(self) -> None:
        script = fax_pair.automation_script("answer", Path("/tmp/ring.trigger"))

        self.assertIn('io.open(ring_trigger_path, "r")', script)
        self.assertEqual(script.count("ring:set_value(1)"), 1)
        self.assertEqual(script.count("ring:set_value(0)"), 1)
        self.assertIn("press(220, 156)", script)
        self.assertIn('snapshot("fax-answer-active.png")', script)

    def test_both_roles_trace_image_and_fax_paths(self) -> None:
        for role in ("origin", "answer"):
            script = fax_pair.automation_script(role, Path("/tmp/ring.trigger"))
            for address, _ in fax_pair.SYMBOLS:
                self.assertIn(f"0x{address:08x}", script)
            self.assertIn("program:read_u32(0x10c00060)", script)
            self.assertIn("program:read_u32(0x10c00090)", script)
            self.assertIn("0x13e8b010", script)
            self.assertIn("0x13c5bd30", script)
            self.assertIn("0x13e8bc8c", script)
            self.assertIn(f"/tmp/{role}.result-ready", script)
            self.assertIn("if result_written then", script)

    def test_configs_select_role_specific_line_peers(self) -> None:
        self.assertIn('value="3"', fax_pair.deterministic_machine_config("origin"))
        self.assertIn('value="2"', fax_pair.deterministic_machine_config("answer"))


class ResultTests(unittest.TestCase):
    def test_complete_role_result_parses(self) -> None:
        values = " ".join(
            f"{name}={index + 1}" for index, (_, name) in enumerate(fax_pair.SYMBOLS)
        )
        output = (
            f"FAX_PAIR_RESULT role=origin {values} "
            "telecom_words=48 telecom_enables=0 "
            "protocol_errors=2 last_error=10 last_detail=1 "
            "image_zero_reads=3 image_failures=4 image_pages=5\n"
        ).encode()

        result = fax_pair.parse_result(output, "origin")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["connect_number"], 1)
        self.assertEqual(result["send_image"], 13)
        self.assertEqual(result["telecom_words"], 48)
        self.assertEqual(result["last_error"], 10)
        self.assertEqual(result["image_pages"], 5)
        self.assertIsNone(fax_pair.parse_result(output, "answer"))


class ExchangeTests(unittest.TestCase):
    def test_setup_silence_then_exchanges_bounded_call_pcm(self) -> None:
        exchange = fax_pair.CallPcmExchange()
        exchange.start()
        caller = socket.create_connection(("127.0.0.1", exchange.port))
        answer = socket.create_connection(("127.0.0.1", exchange.port))
        caller.settimeout(2)
        answer.settimeout(2)
        try:
            caller.sendall(b"CALL")
            self.assertEqual(caller.recv(4), b"\0\0\0\0")
            deadline = time.monotonic() + 2
            while max(exchange.forwarded) < 4 and time.monotonic() < deadline:
                time.sleep(0.01)
            caller_index = exchange.forwarded.index(max(exchange.forwarded))
            exchange.arm(caller_index)

            answer.sendall(b"CED!")
            self.assertEqual(caller.recv(4), b"CED!")
            self.assertTrue(exchange.answer_ready.wait(timeout=2))
            exchange.release_call()

            caller.sendall(b"CNG!")
            self.assertEqual(answer.recv(4), b"CNG!")
            self.assertGreater(sum(exchange.call_forwarded), 0)
        finally:
            caller.close()
            answer.close()
            exchange.stop()

    def test_process_controller_alternates_pcm_leader(self) -> None:
        exchange = fax_pair.CallPcmExchange()
        controls: list[tuple[int, bool]] = []
        exchange.set_process_controller(
            lambda index, paused: controls.append((index, paused))
        )
        exchange.start()
        caller = socket.create_connection(("127.0.0.1", exchange.port))
        answer = socket.create_connection(("127.0.0.1", exchange.port))
        caller.settimeout(2)
        answer.settimeout(2)
        try:
            caller.sendall(b"CALL")
            self.assertEqual(caller.recv(4), b"\0\0\0\0")
            deadline = time.monotonic() + 2
            while max(exchange.forwarded) < 4 and time.monotonic() < deadline:
                time.sleep(0.01)
            caller_index = exchange.forwarded.index(max(exchange.forwarded))
            answer_index = 1 - caller_index
            exchange.arm(caller_index)
            self.assertIn((caller_index, True), controls)

            answer.sendall(b"CED!")
            self.assertEqual(caller.recv(4), b"CED!")
            self.assertTrue(exchange.answer_ready.wait(timeout=2))
            self.assertIn((answer_index, True), controls)

            exchange.release_call()
            caller.sendall(b"CNG!")
            self.assertEqual(answer.recv(4), b"CNG!")
            exchange.disable_process_control()
            self.assertIn((caller_index, False), controls)
            self.assertIn((answer_index, False), controls)
        finally:
            caller.close()
            answer.close()
            exchange.stop()


if __name__ == "__main__":
    unittest.main()
