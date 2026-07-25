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

        self.assertIn("0x00190000 | 0x08 | 0x20 | 0x01", script)

    def test_control_run_clears_the_loop_mode_bit(self) -> None:
        script = telecom.automation_script(loopback=False)

        self.assertIn("0x00190000 | 0x00 | 0x20 | 0x01", script)

    def test_script_uses_lua_compatible_numbers(self) -> None:
        # Lua rejects digit separators, which cost a run to discover.
        self.assertNotIn("_0000", telecom.automation_script())
        self.assertNotIn("0x0019_", telecom.automation_script())

    def test_monitor_config_selects_the_idt_monitor(self) -> None:
        config = telecom.monitor_config("datarover840")

        self.assertIn('tag=":BOOT_MODE"', config)
        self.assertIn('value="0"', config)


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


if __name__ == "__main__":
    unittest.main()
