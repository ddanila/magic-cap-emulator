import unittest

from tools.tx39_branch_regression import (
    BRANCH_BASE,
    BRANCH_STRIDE,
    EXPECTED,
    automation_script,
    parse_results,
    verify_results,
)


def passing_results() -> dict[str, tuple[int, int]]:
    results: dict[str, tuple[int, int]] = {}
    for index, (name, (result, links)) in enumerate(EXPECTED.items(), start=1):
        link = (
            0xA000_0000
            | (BRANCH_BASE + (index - 1) * BRANCH_STRIDE + 0x0C)
            if links
            else 0
        )
        results[name] = (result, link)
    return results


class Tx39BranchRegressionTests(unittest.TestCase):
    def test_script_contains_all_modes_and_opcodes(self) -> None:
        script = automation_script()
        for name in EXPECTED:
            self.assertIn(f'name = "{name}"', script)
        for opcode in (
            "0x50220002",
            "0x54220002",
            "0x58200002",
            "0x5c200002",
            "0x04220002",
            "0x04230002",
            "0x04320002",
            "0x04330002",
            "0x0000000f",
        ):
            self.assertIn(opcode, script)

    def test_parse_results(self) -> None:
        output = (
            b"BRANCH BEQL_T RESULT=00000001 LINK=00000000\n"
            b"BRANCH BLTZALL_N RESULT=00000040 LINK=A0001B4C\n"
        )
        self.assertEqual(
            parse_results(output),
            {
                "BEQL_T": (1, 0),
                "BLTZALL_N": (0x40, 0xA000_1B4C),
            },
        )

    def test_verify_accepts_expected_results(self) -> None:
        self.assertEqual(verify_results(passing_results()), [])

    def test_verify_rejects_executed_nullified_slot(self) -> None:
        results = passing_results()
        results["BEQL_N"] = (0x41, 0)
        failures = verify_results(results)
        self.assertTrue(any("BEQL_N result" in item for item in failures))

    def test_verify_rejects_missing_unconditional_link(self) -> None:
        results = passing_results()
        results["BLTZALL_N"] = (0x40, 0)
        failures = verify_results(results)
        self.assertTrue(any("BLTZALL_N link" in item for item in failures))

    def test_verify_requires_every_mode(self) -> None:
        self.assertEqual(
            verify_results({}),
            ["missing branch modes: " + ", ".join(EXPECTED)],
        )


if __name__ == "__main__":
    unittest.main()
