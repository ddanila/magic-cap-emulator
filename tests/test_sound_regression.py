import math
import unittest

from tools.sound_regression import analyze_samples, automation_script


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


if __name__ == "__main__":
    unittest.main()
