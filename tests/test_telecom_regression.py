import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "telecom_regression.py"
SPEC = importlib.util.spec_from_file_location("telecom_regression", MODULE_PATH)
assert SPEC and SPEC.loader
telecom = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = telecom
SPEC.loader.exec_module(telecom)


def result(**overrides: int) -> dict[str, int]:
    base = {
        "match": telecom.WORDS,
        "words": telecom.WORDS,
        "ptr": 0,
        "enables": 0,
        "half": 1,
        "end": 1,
        "ptrinc": 1,
    }
    base.update(overrides)
    return base


class ScriptTests(unittest.TestCase):
    def test_loopback_sets_the_loop_mode_bit(self) -> None:
        script = telecom.automation_script(loopback=True)

        self.assertIn("(0x19 << 16) | 0x08 | 0x20 | 0x01", script)

    def test_control_run_clears_the_loop_mode_bit(self) -> None:
        script = telecom.automation_script(loopback=False)

        self.assertIn("(0x19 << 16) | 0x00 | 0x20 | 0x01", script)

    def test_one_shot_sets_once_and_continuous_omits_it(self) -> None:
        self.assertIn(
            "program:write_u32(SIB_DMA, 0x8003)",
            telecom.automation_script(),
        )
        self.assertIn(
            "program:write_u32(SIB_DMA, 0x0003)",
            telecom.automation_script(continuous=True),
        )

    def test_script_uses_lua_compatible_numbers(self) -> None:
        # Lua rejects digit separators, which cost a run to discover.
        self.assertNotIn("_0000", telecom.automation_script())
        self.assertNotIn("0x0019_", telecom.automation_script())

    def test_monitor_config_selects_the_idt_monitor(self) -> None:
        config = telecom.monitor_config("datarover840")

        self.assertIn('tag=":BOOT_MODE"', config)
        self.assertIn('value="0"', config)

    def test_dial_tone_takes_betty_off_hook_at_7200_hz(self) -> None:
        script = telecom.automation_script(
            words=1024, loopback=False, dial_tone=True
        )

        self.assertIn("program:write_u32(SIB_SF0_AUX, 0x04000200)", script)
        self.assertIn("(0x27 << 16) | 0x00 | 0x20 | 0x01", script)
        self.assertIn("amplitude(350)", script)
        self.assertIn("amplitude(440)", script)


class ParseTests(unittest.TestCase):
    def test_parses_a_result_line(self) -> None:
        output = (
            b"TELECOM START\n"
            b"TELECOM RESULT match=64/64 ptr=0 enables=0 half=1 end=1 ptrinc=1\n"
        )

        parsed = telecom.parse_result(output)

        assert parsed is not None
        self.assertEqual(parsed["match"], 64)
        self.assertEqual(parsed["words"], 64)
        self.assertEqual(parsed["enables"], 0)

    def test_missing_result_is_none(self) -> None:
        self.assertIsNone(telecom.parse_result(b"TELECOM START\n"))

    def test_parses_dial_tone_spectrum(self) -> None:
        output = (
            b"TELECOM TONE samples=2048 min=-7878 max=7878 "
            b"hz350=3987 hz440=3985 hz1000=3\n"
        )

        self.assertEqual(
            telecom.parse_tone_result(output),
            {
                "samples": 2048,
                "min": -7878,
                "max": 7878,
                "hz350": 3987,
                "hz440": 3985,
                "hz1000": 3,
            },
        )


class VerifyTests(unittest.TestCase):
    def test_accepts_a_complete_loopback(self) -> None:
        passed, message = telecom.verify(result())

        self.assertTrue(passed, message)
        self.assertIn("looped back", message)

    def test_rejects_a_partial_loopback(self) -> None:
        passed, message = telecom.verify(result(match=17))

        self.assertFalse(passed)
        self.assertIn("17 of 64", message)

    def test_rejects_missing_interrupts(self) -> None:
        for field, name in (
            ("half", "kIntTelDmaHalfMask"),
            ("end", "kIntTelDmaEndMask"),
            ("ptrinc", "kIntTelDmaPtrIncMask"),
        ):
            with self.subTest(field=field):
                passed, message = telecom.verify(result(**{field: 0}))
                self.assertFalse(passed)
                self.assertIn(name, message)

    def test_rejects_a_one_shot_that_stayed_enabled(self) -> None:
        passed, message = telecom.verify(result(enables=1))

        self.assertFalse(passed)
        self.assertIn("left enables set", message)

    def test_rejects_a_pointer_that_did_not_wrap(self) -> None:
        passed, message = telecom.verify(result(ptr=63))

        self.assertFalse(passed)
        self.assertIn("instead of wrapping", message)

    def test_control_requires_silence(self) -> None:
        passed, message = telecom.verify_no_loopback(result(match=0))

        self.assertTrue(passed, message)

    def test_control_fails_when_data_arrives_anyway(self) -> None:
        passed, message = telecom.verify_no_loopback(result(match=64))

        self.assertFalse(passed)
        self.assertIn("kSibLoopModeMask", message)

    def test_continuous_requires_both_enables_to_remain_set(self) -> None:
        passed, message = telecom.verify_continuous(result(enables=3))

        self.assertTrue(passed, message)

        passed, message = telecom.verify_continuous(result(enables=0))
        self.assertFalse(passed)
        self.assertIn("expected sibDMA RX/TX enables 3", message)

    def test_accepts_a_clean_dial_tone(self) -> None:
        passed, message = telecom.verify_dial_tone(
            result(match=0),
            {
                "samples": 2048,
                "min": -7878,
                "max": 7878,
                "hz350": 3987,
                "hz440": 3985,
                "hz1000": 3,
            },
        )

        self.assertTrue(passed, message)
        self.assertIn("350+440 Hz", message)

    def test_rejects_missing_dial_tone_component(self) -> None:
        passed, message = telecom.verify_dial_tone(
            result(match=0),
            {
                "samples": 2048,
                "min": -4000,
                "max": 4000,
                "hz350": 3987,
                "hz440": 0,
                "hz1000": 3,
            },
        )

        self.assertFalse(passed)
        self.assertIn("insufficient range", message)


if __name__ == "__main__":
    unittest.main()
