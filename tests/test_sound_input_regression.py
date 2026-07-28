import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "sound_input_regression.py"
SPEC = importlib.util.spec_from_file_location(
    "sound_input_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
sound_input = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sound_input
SPEC.loader.exec_module(sound_input)


class ConfigTests(unittest.TestCase):
    def test_selects_monitor_and_deterministic_tone(self) -> None:
        config = sound_input.monitor_config()

        self.assertIn('tag=":BOOT_MODE"', config)
        self.assertIn('tag=":MICROPHONE_SOURCE"', config)
        self.assertIn('mask="3" defvalue="0" value="1"', config)


class ScriptTests(unittest.TestCase):
    def test_drives_rx_dma_then_silence_control(self) -> None:
        script = sound_input.automation_script()

        self.assertIn("program:write_u32(dino + 0x064, base)", script)
        self.assertIn("program:write_u32(dino + 0x090, 0x80020000)", script)
        self.assertIn("source:set_value(2)", script)
        self.assertIn("SOUND_INPUT TONE_NONZERO=", script)


class ResultTests(unittest.TestCase):
    def test_accepts_complete_tone_and_silence(self) -> None:
        result = (
            127,
            -11999,
            11997,
            23,
            sound_input.INTERRUPTS,
            sound_input.DMA_FINISHED,
            0,
            sound_input.INTERRUPTS,
            sound_input.DMA_FINISHED,
        )

        passed, message = sound_input.verify_result(result)

        self.assertTrue(passed, message)

    def test_rejects_missing_interrupt(self) -> None:
        result = (
            127,
            -11999,
            11997,
            23,
            sound_input.INTERRUPTS & ~0x00200000,
            sound_input.DMA_FINISHED,
            0,
            sound_input.INTERRUPTS,
            sound_input.DMA_FINISHED,
        )

        passed, message = sound_input.verify_result(result)

        self.assertFalse(passed)
        self.assertIn("interrupt", message)

    def test_parser_handles_signed_range(self) -> None:
        output = (
            b"SOUND_INPUT TONE_NONZERO=127 MIN=-11999 MAX=11997 "
            b"CROSSINGS=23 TONE_STATUS=00640400 TONE_DMA=80000000 "
            b"SILENCE_NONZERO=0 SILENCE_STATUS=00640400 "
            b"SILENCE_DMA=80000000\n"
        )

        result = sound_input.parse_result(output)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result[1:4], (-11999, 11997, 23))


if __name__ == "__main__":
    unittest.main()
