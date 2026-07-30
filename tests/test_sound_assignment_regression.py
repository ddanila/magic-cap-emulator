import importlib.util
import math
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "sound_assignment_regression.py"
SPEC = importlib.util.spec_from_file_location(
    "sound_assignment_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
sound_assignment = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sound_assignment
SPEC.loader.exec_module(sound_assignment)


class ScriptTests(unittest.TestCase):
    def test_assignment_uses_construction_mode_and_a_real_sound_coupon(self) -> None:
        script = sound_assignment.automation_script()

        self.assertIn("press(232, 182)", script)
        self.assertIn("press(103, 300)", script)
        self.assertIn("press(340, 117)", script)
        self.assertIn("press(48, 262)", script)
        self.assertIn("move(48 + fraction * 192", script)
        self.assertIn("press(168, 140)", script)
        self.assertIn("move(168 + fraction * 262", script)
        self.assertIn("watch(0x13dd783c", script)
        self.assertIn("coupon_apply=%d", script)
        self.assertIn('snapshot("13-hallway-committed.png")', script)

    def test_development_rom_uses_elf_method_addresses(self) -> None:
        script = sound_assignment.automation_script("datarover840d")

        self.assertIn("watch(0x13dd87cc", script)
        self.assertIn("watch(0x13e41dc4", script)

    def test_retained_phase_reopens_sound_controls(self) -> None:
        script = sound_assignment.retained_automation_script()

        self.assertIn("press(424, 108)", script)
        self.assertIn("press(340, 75)", script)
        self.assertIn("press(430, 85)", script)
        self.assertIn("SOUND_RETAINED action=", script)


class ResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assignment = sound_assignment.AssignmentResult(
            can_sound=6,
            can_coupon=5,
            set_sound=1,
            coupon_accepted=1,
            coupon_apply=1,
            action=1,
            button=0x2381C,
        )
        self.retained = sound_assignment.RetainedResult(action=1, button=0x2381C)
        self.live = sound_assignment.Playback(
            start=83.51,
            duration=1.21,
            peak=5742,
            frequency=599.17,
        )
        self.replayed = sound_assignment.Playback(
            start=26.84,
            duration=1.21,
            peak=5743,
            frequency=599.17,
        )

    def test_parses_both_method_checkpoints(self) -> None:
        assignment = (
            b"SOUND_ASSIGNMENT can_sound=6 can_coupon=5 set_sound=1 "
            b"coupon_accepted=1 coupon_apply=1 action=1 button=0002381C\n"
        )
        retained = b"SOUND_RETAINED action=1 button=0002381C\n"

        self.assertEqual(sound_assignment.parse_assignment(assignment), self.assignment)
        self.assertEqual(sound_assignment.parse_retained(retained), self.retained)

    def test_accepts_assignment_and_matching_retained_playback(self) -> None:
        self.assertEqual(
            sound_assignment.verify_results(
                self.assignment,
                self.retained,
                self.live,
                self.replayed,
            ),
            [],
        )

    def test_rejects_a_coupon_that_never_reached_set_sound(self) -> None:
        rejected = sound_assignment.AssignmentResult(
            **{**self.assignment.__dict__, "set_sound": 0, "coupon_apply": 0}
        )

        failures = sound_assignment.verify_results(
            rejected,
            self.retained,
            self.live,
            self.replayed,
        )

        self.assertTrue(any("SetSound" in failure for failure in failures))
        self.assertTrue(any("not applied" in failure for failure in failures))

    def test_rejects_changed_retained_waveform(self) -> None:
        original_error = sound_assignment.Playback(
            start=26.84,
            duration=0.84,
            peak=6495,
            frequency=206,
        )

        failures = sound_assignment.verify_results(
            self.assignment,
            self.retained,
            self.live,
            original_error,
        )

        self.assertTrue(any("duration changed" in failure for failure in failures))
        self.assertTrue(any("frequency changed" in failure for failure in failures))


class WaveTests(unittest.TestCase):
    def test_finds_playback_after_ui_frame(self) -> None:
        sample_rate = 8_000
        expected = 1.0
        samples = [0] * round((expected + 0.2) * sample_rate)
        samples.extend(
            round(5_000 * math.sin(2 * math.pi * 600 * index / sample_rate))
            for index in range(round(1.2 * sample_rate))
        )
        samples.extend([0] * sample_rate)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                interleaved = [sample for value in samples for sample in (0, value)]
                output.writeframes(struct.pack(f"<{len(interleaved)}h", *interleaved))

            playback = sound_assignment.playback_after(path, frame=60)

        self.assertIsNotNone(playback)
        assert playback is not None
        self.assertAlmostEqual(playback.duration, 1.2, delta=0.02)
        self.assertAlmostEqual(playback.frequency, 600, delta=5)


if __name__ == "__main__":
    unittest.main()
