import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "devrom_command_t.py"
SPEC = importlib.util.spec_from_file_location("devrom_command_t", MODULE_PATH)
assert SPEC and SPEC.loader
command_t = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = command_t
SPEC.loader.exec_module(command_t)


class StubTests(unittest.TestCase):
    def test_queue_stub_enters_through_the_real_system_run_queue(self) -> None:
        words = command_t.queue_stub_words()

        self.assertIn(0x00002021, words)  # forUser = false
        self.assertIn(
            0x3C050000 | (command_t.BOOTSTRAP_DESCRIPTOR >> 16),
            words,
        )
        self.assertIn(
            0x34A50000 | (command_t.BOOTSTRAP_DESCRIPTOR & 0xFFFF),
            words,
        )
        self.assertIn(0x0320F809, words)  # jalr t9
        self.assertIn(0x3C1913C1, words)
        self.assertIn(0x3739D250, words)
        self.assertEqual(command_t.SEMAPHORE_RUN_SOON, 0x13CBF9A0)

    def test_queue_stub_loads_the_live_test_machine(self) -> None:
        words = command_t.queue_stub_words()

        self.assertIn(0x3C060003, words)
        self.assertIn(0x8CC6D4B4, words)

    def test_queue_stub_returns_through_a_patched_nonbranching_park(self) -> None:
        words = command_t.queue_stub_words()

        self.assertEqual(words[command_t.QUEUE_RETURN_HIGH_INDEX], 0x3C1A0000)
        self.assertEqual(words[command_t.QUEUE_RETURN_LOW_INDEX], 0x375A0000)
        self.assertEqual(
            words[command_t.QUEUE_RETURN_LOW_INDEX + 1],
            0x03400008,
        )

    def test_callback_calls_canonical_command_tea(self) -> None:
        words = command_t.callback_words()

        self.assertIn(0x3C1913E9, words)
        self.assertIn(0x3739837C, words)
        self.assertEqual(command_t.TEST_MACHINE_COMMAND_TEA, 0x13E9837C)

    def test_bootstrap_moves_onto_the_user_actor(self) -> None:
        words = command_t.bootstrap_words()

        self.assertIn(0x24040001, words)  # forUser = true
        self.assertIn(
            0x3C050000 | (command_t.CALLBACK_DESCRIPTOR >> 16),
            words,
        )
        self.assertIn(
            0x34A50000 | (command_t.CALLBACK_DESCRIPTOR & 0xFFFF),
            words,
        )
        self.assertIn(0x0320F809, words)

    def test_every_word_fits_in_32_bits(self) -> None:
        words = (
            command_t.queue_stub_words()
            + command_t.bootstrap_words()
            + command_t.callback_words()
        )
        for word in words:
            self.assertEqual(word, word & 0xFFFFFFFF)


class ScriptTests(unittest.TestCase):
    def test_script_restores_the_interrupted_architectural_state(self) -> None:
        script = command_t.automation_script(2400, 60_000)

        self.assertIn('"HI", "LO", "SR"', script)
        self.assertIn('table.insert(register_names, "R" .. index)', script)
        self.assertIn('cpu.state["PC"].value = saved_state.PC', script)
        self.assertIn("pc >= 0x13c3b4a0 and pc < 0x13c3b540", script)
        self.assertIn("CONTEXT_RESTORED", script)

    def test_script_has_completion_and_failure_oracles(self) -> None:
        script = command_t.automation_script(2400, 60_000)

        self.assertIn(f"0x{command_t.ROM_GP:08x}", script)
        self.assertIn(f"0x{command_t.BOOTSTRAP:08x}", script)
        self.assertIn(f"0x{command_t.CALLBACK:08x}", script)
        self.assertIn(f"0x{command_t.RUN_TESTS:08x}", script)
        self.assertIn(f"0x{command_t.TESTS_COMPLETE:08x}", script)
        self.assertIn(f"0x{command_t.FAILURE_ORACLE:08x}", script)
        self.assertIn("frames == 60000", script)


class ResultTests(unittest.TestCase):
    def test_parse_complete_run(self) -> None:
        output = (
            b"DEVROM_COMMAND_T queued=1 restored=1 bootstrap=1 user_queued=1 "
            b"entered=1 returned=1 "
            b"run_suites=1 run_tests=16 complete=1 complaints=0 reboot=0\n"
        )

        result = command_t.parse_result(output)

        assert result is not None
        self.assertEqual(result["run_tests"], 16)
        self.assertEqual(result["complaints"], 0)

    def test_missing_result_is_none(self) -> None:
        self.assertIsNone(command_t.parse_result(b"Average speed: 400%\n"))


if __name__ == "__main__":
    unittest.main()
