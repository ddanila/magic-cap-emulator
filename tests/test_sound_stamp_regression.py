import importlib.util
import math
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "sound_stamp_regression.py"
SPEC = importlib.util.spec_from_file_location("sound_stamp_regression", MODULE_PATH)
assert SPEC and SPEC.loader
sound_stamp = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sound_stamp
SPEC.loader.exec_module(sound_stamp)


class ScriptTests(unittest.TestCase):
    def test_drives_the_documented_sound_stamp_controls(self) -> None:
        script = sound_stamp.automation_script()

        self.assertIn("press(103, 300)", script)
        self.assertIn("press(170, 100)", script)
        self.assertIn("press(279, 148)", script)
        self.assertIn("press(350, 148)", script)
        self.assertIn("press(420, 148)", script)
        self.assertIn("press(463, 16)", script)
        self.assertIn("press(34, 300)", script)
        self.assertIn('snapshot("sound-stamp-committed.png")', script)
        self.assertIn('snapshot("sound-stamp-card-left.png")', script)
        self.assertIn("0x00020000", script)
        self.assertIn("SOUND_STAMP RX_START=", script)
        self.assertNotIn("touch_button:value()", script)

    def test_config_selects_the_deterministic_microphone(self) -> None:
        self.assertIn(
            'mask="3" defvalue="0" value="1"',
            sound_stamp.config_xml("datarover840"),
        )


class ResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = sound_stamp.Result(
            rx_start=3056,
            rx_stop=3178,
            play_start=3478,
            play_stop=3638,
            nonzero=967,
            minimum=-12000,
            maximum=12000,
            crossings=286,
            audio_valid=1,
            sib_state=0,
            queue_write=24,
            queue_read=24,
        )
        self.playback = sound_stamp.Playback(
            start=self.result.play_start / 60,
            duration=(self.result.play_stop - self.result.play_start) / 60,
            peak=13_000,
            frequency=470,
        )

    def test_accepts_complete_product_flow(self) -> None:
        passed, message = sound_stamp.verify_result(self.result, self.playback)

        self.assertTrue(passed, message)
        self.assertIn("drained the stop command", message)

    def test_rejects_stuck_sib_command(self) -> None:
        result = sound_stamp.Result(**{**self.result.__dict__, "sib_state": 5})

        passed, message = sound_stamp.verify_result(result, self.playback)

        self.assertFalse(passed)
        self.assertIn("state remained 5", message)

    def test_rejects_rx_dma_that_never_stops(self) -> None:
        result = sound_stamp.Result(**{**self.result.__dict__, "rx_stop": 0})

        passed, message = sound_stamp.verify_result(result, self.playback)

        self.assertFalse(passed)
        self.assertIn("receive DMA", message)

    def test_parses_checkpoint(self) -> None:
        output = (
            b"SOUND_STAMP RX_START=3056 RX_STOP=3178 "
            b"PLAY_START=3478 PLAY_STOP=3638 NONZERO=967 "
            b"MIN=-12000 MAX=12000 CROSSINGS=286 AUDIO_VALID=1 "
            b"SIB_STATE=0 QUEUE_WRITE=24 QUEUE_READ=24\n"
        )

        self.assertEqual(sound_stamp.parse_result(output), self.result)


class WaveTests(unittest.TestCase):
    def test_finds_playback_at_dma_start(self) -> None:
        rate = 48_000
        samples = [0] * int(rate * (self_start := 2.0))
        samples.extend(
            round(12_000 * math.sin(2 * math.pi * 500 * index / rate))
            for index in range(rate * 2)
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(rate)
                interleaved = [sample for value in samples for sample in (0, value)]
                output.writeframes(struct.pack(f"<{len(interleaved)}h", *interleaved))

            segments = sound_stamp.audible_segments(path)
            result = sound_stamp.Result(
                rx_start=10,
                rx_stop=20,
                play_start=round(self_start * 60),
                play_stop=round((self_start + 2) * 60),
                nonzero=1000,
                minimum=-12000,
                maximum=12000,
                crossings=280,
                audio_valid=1,
                sib_state=0,
                queue_write=2,
                queue_read=2,
            )

        playback = sound_stamp.playback_for(result, segments)

        self.assertIsNotNone(playback)
        assert playback is not None
        self.assertAlmostEqual(playback.duration, 2.0, delta=0.02)
        self.assertGreaterEqual(playback.peak, 11_900)


if __name__ == "__main__":
    unittest.main()
