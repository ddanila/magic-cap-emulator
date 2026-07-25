import math
import unittest

from tools.sound_regression import (
    DMA_MIN_PEAK,
    analyze_samples,
    automation_script,
    find_segments,
    verify_dma,
)


def burst(rate: int, frequency: float, duration: float, amplitude: int) -> list[int]:
    return [
        round(amplitude * math.sin(2 * math.pi * frequency * index / rate))
        for index in range(int(rate * duration))
    ]


class SoundRegressionTests(unittest.TestCase):
    def test_tone_measurement(self) -> None:
        rate = 48_000
        frequency = 750
        samples = [0] * 100
        samples.extend(
            round(12_000 * math.sin(2 * math.pi * frequency * index / rate))
            for index in range(rate // 16)
        )
        samples.extend([0] * 100)

        tone = analyze_samples(samples, rate, 0)

        self.assertIsNotNone(tone)
        assert tone is not None
        self.assertAlmostEqual(tone.frequency, frequency, delta=10)
        self.assertAlmostEqual(tone.duration, 1 / 16, delta=0.001)
        self.assertGreaterEqual(tone.peak, 11_900)

    def test_silent_channel(self) -> None:
        self.assertIsNone(analyze_samples([0] * 100, 48_000, 0))

    def test_automation_exits(self) -> None:
        self.assertIn("frames == 240", automation_script())
        self.assertIn("machine:exit()", automation_script())

    def test_automation_frame_is_configurable(self) -> None:
        self.assertIn("frames == 1200", automation_script(1200))


class SegmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rate = 48_000

    def build(self) -> list[int]:
        # 70 ms beep, one second of silence, then a 190 ms buffered chime.
        samples = burst(self.rate, 768, 0.07, 16_000)
        samples.extend([0] * self.rate)
        samples.extend(burst(self.rate, 820, 0.19, 5_500))
        return samples

    def test_finds_both_bursts(self) -> None:
        segments = find_segments(self.build(), self.rate)

        self.assertEqual(len(segments), 2)
        self.assertAlmostEqual(segments[0].duration, 0.07, delta=0.02)
        self.assertAlmostEqual(segments[1].start, 1.07, delta=0.02)
        self.assertAlmostEqual(segments[1].frequency, 820, delta=25)

    def test_accepts_a_real_shaped_capture(self) -> None:
        passed, message = verify_dma(find_segments(self.build(), self.rate))

        self.assertTrue(passed, message)
        self.assertIn("buffered SIB sound DMA", message)

    def test_rejects_a_capture_with_only_the_beep(self) -> None:
        samples = burst(self.rate, 768, 0.07, 16_000)

        passed, message = verify_dma(find_segments(samples, self.rate))

        self.assertFalse(passed)
        self.assertIn("found 1 audible segment", message)

    def test_rejects_a_chime_that_is_too_short(self) -> None:
        samples = burst(self.rate, 768, 0.07, 16_000)
        samples.extend([0] * self.rate)
        samples.extend(burst(self.rate, 820, 0.03, 5_500))

        passed, message = verify_dma(find_segments(samples, self.rate))

        self.assertFalse(passed)
        self.assertIn("expected", message)

    def test_rejects_a_chime_that_is_too_quiet(self) -> None:
        samples = burst(self.rate, 768, 0.07, 16_000)
        samples.extend([0] * self.rate)
        samples.extend(burst(self.rate, 820, 0.19, DMA_MIN_PEAK // 2))

        passed, message = verify_dma(find_segments(samples, self.rate))

        self.assertFalse(passed)
        self.assertIn("below", message)

    def test_silence_yields_no_segments(self) -> None:
        self.assertEqual(find_segments([0] * self.rate, self.rate), [])


if __name__ == "__main__":
    unittest.main()
