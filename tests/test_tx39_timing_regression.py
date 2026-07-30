import unittest

from tools.tx39_timing_regression import (
    EXPECTED_DIV_RESULTS,
    MODE_CYCLES,
    automation_script,
    parse_div_results,
    parse_results,
    verify_results,
)


class Tx39TimingRegressionTests(unittest.TestCase):
    def test_script_contains_all_pipeline_cases(self) -> None:
        script = automation_script()
        for mode in MODE_CYCLES:
            self.assertIn(f'name = "{mode}"', script)
        self.assertIn("0x012a4018", script)
        self.assertIn("0x712a4000", script)
        self.assertIn("0x012a001a", script)
        self.assertIn("0x00004012", script)
        self.assertIn("0x01800011", script)

    def test_parse_results(self) -> None:
        output = b"\n".join(
            f"TIMING {mode} COUNT={index:08X}".encode()
            for index, mode in enumerate(MODE_CYCLES, start=1)
        )
        self.assertEqual(
            parse_results(output),
            {
                mode: index
                for index, mode in enumerate(MODE_CYCLES, start=1)
            },
        )

    def test_parse_div_results(self) -> None:
        output = (
            b"TIMING DIV_MFLO COUNT=00000001 RESULT=0000000E\n"
            b"TIMING DIV_CANCEL RESULT=00001234\n"
        )
        self.assertEqual(parse_div_results(output), EXPECTED_DIV_RESULTS)

    def test_verify_accepts_cycle_normalized_counts(self) -> None:
        results = {
            mode: 120_000 // cycles for mode, cycles in MODE_CYCLES.items()
        }
        self.assertEqual(verify_results(results), [])

    def test_verify_rejects_blocking_divide(self) -> None:
        results = {
            mode: 120_000 // cycles for mode, cycles in MODE_CYCLES.items()
        }
        results["DIV"] = 120_000 // 38
        failures = verify_results(results)
        self.assertTrue(any("DIV normalized count" in item for item in failures))

    def test_verify_rejects_wrong_divide_result(self) -> None:
        results = {
            mode: 120_000 // cycles for mode, cycles in MODE_CYCLES.items()
        }
        failures = verify_results(results, (0, 0))
        self.assertTrue(any("divide results" in item for item in failures))

    def test_verify_requires_every_mode(self) -> None:
        self.assertEqual(
            verify_results({"BASE": 1}),
            [
                "missing timing modes: MULT, MADD, MULT_DEP, DIV, DIV_MFLO"
            ],
        )


if __name__ == "__main__":
    unittest.main()
