import unittest

from tools.data_modem_pair_regression import (
    HALF_DMA_BYTES,
    MIN_PCM_BYTES,
    SYMBOLS,
    automation_script,
    external_bridge_config,
    op,
    parse_result,
    role_words,
    validate_results,
)


class DataModemPairScriptTests(unittest.TestCase):
    def test_origin_selects_v32_and_answer_uses_command_handler(self) -> None:
        origin = role_words("origin")
        answer = role_words("answer")

        self.assertIn(op(0x0D, rt=4, immediate=0x80), origin)
        self.assertIn(0x3739_31DC, origin)
        self.assertIn(0x3739_26C4, answer)
        self.assertGreater(len(answer), len(origin))

    def test_scripts_probe_carrier_and_data_mode(self) -> None:
        for role in ("answer", "origin"):
            script = automation_script(role)
            self.assertIn(f"role={role}", script)
            self.assertIn("0x13e4b770", script)
            self.assertIn("detector=%d", script)
            self.assertIn("rates=%04X,%04X,%04X,%04X", script)

    def test_config_selects_external_pcm(self) -> None:
        config = external_bridge_config("datarover840")

        self.assertIn('tag=":PHONE_PEER"', config)
        self.assertIn('value="2"', config)


class DataModemPairResultTests(unittest.TestCase):
    @staticmethod
    def output(role: str) -> bytes:
        counters = " ".join(f"{name}=1" for _, name in SYMBOLS)
        return (
            f"DATA_MODEM_PAIR_RESULT role={role} {counters} "
            "returned=1 detector=1 rates=FFF0,FFF0,FFF0,FFF0 "
            "enables=3 size=48\n"
        ).encode()

    def test_result_parses(self) -> None:
        result = parse_result(self.output("answer"))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["role"], "answer")
        self.assertEqual(result["data_mode"], 1)
        self.assertEqual(result["rates"], (0xFFF0,) * 4)

    def test_complete_pair_is_accepted(self) -> None:
        answer = parse_result(self.output("answer"))
        origin = parse_result(self.output("origin"))
        assert answer is not None and origin is not None

        failures = validate_results(
            {"answer": answer, "origin": origin},
            [MIN_PCM_BYTES, MIN_PCM_BYTES + HALF_DMA_BYTES],
        )

        self.assertEqual(failures, [])

    def test_missing_data_mode_and_clock_divergence_are_rejected(self) -> None:
        answer = parse_result(self.output("answer"))
        origin = parse_result(self.output("origin"))
        assert answer is not None and origin is not None
        origin["data_mode"] = 0

        failures = validate_results(
            {"answer": answer, "origin": origin},
            [MIN_PCM_BYTES, MIN_PCM_BYTES + HALF_DMA_BYTES + 1],
        )

        self.assertIn("origin missed data_mode", failures)
        self.assertTrue(
            any(failure.startswith("PCM clocks diverged") for failure in failures)
        )


if __name__ == "__main__":
    unittest.main()
