import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "devrom_suites.py"
SPEC = importlib.util.spec_from_file_location("devrom_suites", MODULE_PATH)
assert SPEC and SPEC.loader
suites = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = suites
SPEC.loader.exec_module(suites)


class StubTests(unittest.TestCase):
    def test_resolves_the_list_entry_then_runs_it(self) -> None:
        words = suites.suite_stub_words(0x24)

        # lui $4, hi(list slot) / lw $4, lo(list slot)
        self.assertEqual(words[0], 0x3C040000 | ((suites.BASIC_SYSTEM_TEST_LIST_SLOT + 0x8000) >> 16))
        self.assertEqual(words[1], 0x8C840000 | (suites.BASIC_SYSTEM_TEST_LIST_SLOT & 0xFFFF))
        self.assertEqual(words[2], 0x24050024)  # li $5, offset
        self.assertIn(0x0320F809, words)        # jalr $25

    def test_slot_load_compensates_for_sign_extension(self) -> None:
        # 0x29714's low half is negative as a signed 16-bit offset, so the
        # high half has to be one greater.
        words = suites.suite_stub_words(0x04)

        self.assertEqual(words[0] & 0xFFFF, 0x0003)
        self.assertEqual(words[1] & 0xFFFF, 0x9714)

    def test_index_zero_means_the_whole_suite(self) -> None:
        words = suites.suite_stub_words(0x08)

        self.assertEqual(suites.RUN_ALL_TESTS_IN_SUITE, 0)
        self.assertIn(0x24060000, words)  # li $6, 0

    def test_a_specific_index_is_encoded(self) -> None:
        words = suites.suite_stub_words(0x08, index=3)

        self.assertIn(0x24060003, words)

    def test_every_word_fits_in_32_bits(self) -> None:
        for offset in range(suites.FIRST_SUITE_OFFSET, suites.LAST_SUITE_OFFSET + 1, 4):
            for word in suites.suite_stub_words(offset):
                self.assertEqual(word, word & 0xFFFFFFFF)


class ScriptTests(unittest.TestCase):
    def test_script_reports_both_outcomes(self) -> None:
        script = suites.automation_script(0x24, 9000, 2400)

        self.assertIn("returned=1", script)
        self.assertIn("returned=0", script)
        self.assertIn("frames == 2400", script)
        self.assertIn("frames == 9000", script)

    def test_breakpoint_action_is_a_single_command(self) -> None:
        script = suites.automation_script(0x24, 9000, 2400)
        action = script[script.index('"do d@'):]

        self.assertEqual(action[: action.index('"', 1)].count("do "), 1)


class ResultTests(unittest.TestCase):
    def test_parses_a_clean_run(self) -> None:
        output = b"DEVROM_SUITE offset=0x24 suite=000336DC returned=1 complaints=0\n"

        result = suites.parse_result(output)

        assert result is not None
        self.assertEqual(result["offset"], 0x24)
        self.assertEqual(result["suite"], 0x000336DC)
        self.assertTrue(result["returned"])
        self.assertEqual(result["complaints"], 0)

    def test_parses_a_stalled_run(self) -> None:
        output = b"DEVROM_SUITE offset=0x10 suite=00000000 returned=0 complaints=2\n"

        result = suites.parse_result(output)

        assert result is not None
        self.assertFalse(result["returned"])
        self.assertEqual(result["complaints"], 2)

    def test_missing_result_is_none(self) -> None:
        self.assertIsNone(suites.parse_result(b"Average speed: 300%\n"))


if __name__ == "__main__":
    unittest.main()
