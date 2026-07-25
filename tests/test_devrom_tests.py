import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "devrom_tests.py"
SPEC = importlib.util.spec_from_file_location("devrom_tests", MODULE_PATH)
assert SPEC and SPEC.loader
devrom_tests = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = devrom_tests
SPEC.loader.exec_module(devrom_tests)


class StubTests(unittest.TestCase):
    def test_encodes_jalr_call_to_a_far_target(self) -> None:
        target = 0x13E9DBFC
        words = devrom_tests.call_stub_words(target)

        # lui $25, 0x13e9 / ori $25, $25, 0xdbfc / jalr $25 / nop
        self.assertEqual(words[0], 0x3C190000 | (target >> 16))
        self.assertEqual(words[1], 0x37390000 | (target & 0xFFFF))
        self.assertEqual(words[2], 0x0320F809)
        self.assertEqual(words[3], 0x00000000)

    def test_parks_after_writing_the_completion_marker(self) -> None:
        words = devrom_tests.call_stub_words(devrom_tests.SUITES["cache"]["address"])

        done = devrom_tests.STUB + 0x40
        self.assertEqual(words[4], 0x3C080000 | (devrom_tests.MARKER_VALUE >> 16))
        self.assertEqual(words[5], 0x35080000 | (devrom_tests.MARKER_VALUE & 0xFFFF))
        self.assertEqual(words[6], 0x3C090000 | (done >> 16))
        self.assertEqual(words[7], 0x35290000 | (done & 0xFFFF))
        self.assertEqual(words[8], 0xAD280000)  # sw $8, 0($9)
        self.assertEqual(words[9], 0x1000FFFF)  # b .

    def test_every_stub_word_fits_in_32_bits(self) -> None:
        for suite in devrom_tests.SUITES.values():
            for word in devrom_tests.call_stub_words(suite["address"]):
                self.assertEqual(word, word & 0xFFFFFFFF)


class ScriptTests(unittest.TestCase):
    def test_call_script_uses_the_documented_oracle(self) -> None:
        script = devrom_tests.automation_script("cache", 0x13E9C824, 6000, 900)

        self.assertIn(f"bpset(0x{devrom_tests.FAILURE_ORACLE:08x}", script)
        self.assertIn(f"cpu.state[\"PC\"].value = 0x{devrom_tests.STUB_UNCACHED:08x}", script)
        self.assertIn("suite=cache", script)
        self.assertNotIn("__", script.replace("__index", ""))

    def test_self_check_oracle_can_be_overridden(self) -> None:
        script = devrom_tests.automation_script(
            "cache", 0x13E9C824, 6000, 900, oracle=0x13E9C824
        )

        self.assertIn("bpset(0x13e9c824", script)

    def test_calibration_script_taps_welcome_and_three_targets(self) -> None:
        script = devrom_tests.calibration_script()

        self.assertIn("press(240, 160)", script)
        self.assertIn("press(23, 23)", script)
        self.assertIn("press(456, 296)", script)
        self.assertIn("DEVROM_TEST CALIBRATED", script)

    def test_budget_and_call_frame_are_substituted(self) -> None:
        script = devrom_tests.automation_script("font", 0x13E9D488, 1234, 567)

        self.assertIn("frames == 567 then", script)
        self.assertIn("frames == 1234 then", script)


class ResultParsingTests(unittest.TestCase):
    def test_parses_pass_and_noreturn_verdicts(self) -> None:
        output = (
            b"DEVROM_TEST CALL suite=cache target=13E9C824\n"
            b"DEVROM_TEST RESULT suite=cache returned=1 failures=0\n"
            b"DEVROM_TEST RESULT suite=contact returned=0 failures=0\n"
            b"DEVROM_TEST NORETURN pc=13CBFAF4\n"
        )

        results = devrom_tests.parse_results(output)

        self.assertEqual(results["cache"], (True, 0))
        self.assertEqual(results["contact"], (False, 0))

    def test_parses_a_complaint_count(self) -> None:
        output = b"DEVROM_TEST RESULT suite=font returned=1 failures=3\n"

        self.assertEqual(devrom_tests.parse_results(output)["font"], (True, 3))

    def test_ignores_unrelated_output(self) -> None:
        self.assertEqual(devrom_tests.parse_results(b"Average speed: 350%\n"), {})


class SuiteTableTests(unittest.TestCase):
    def test_default_suites_are_the_passing_ones(self) -> None:
        expected = tuple(
            name
            for name, suite in devrom_tests.SUITES.items()
            if suite["status"] == "passes"
        )

        self.assertEqual(devrom_tests.DEFAULT_SUITES, expected)
        self.assertIn("datetime", devrom_tests.DEFAULT_SUITES)
        self.assertIn("rompristine", devrom_tests.DEFAULT_SUITES)
        # A suite the ROM complains about must not be a default check, and
        # neither must one that cannot be driven by a forced call.
        self.assertNotIn("fmtinteger", devrom_tests.DEFAULT_SUITES)
        self.assertNotIn("contact", devrom_tests.DEFAULT_SUITES)

    def test_every_suite_has_a_known_status(self) -> None:
        for name, suite in devrom_tests.SUITES.items():
            with self.subTest(suite=name):
                self.assertIn(
                    suite["status"], {"passes", "complains", "noreturn"}
                )

    def test_addresses_are_inside_the_development_rom(self) -> None:
        for name, suite in devrom_tests.SUITES.items():
            with self.subTest(suite=name):
                # 8 MiB ROM region based at 0x13c00000.
                self.assertGreaterEqual(suite["address"], 0x13C00000)
                self.assertLess(suite["address"], 0x14400000)


if __name__ == "__main__":
    unittest.main()
