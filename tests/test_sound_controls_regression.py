import importlib.util
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image, ImageDraw


MODULE_PATH = Path(__file__).parents[1] / "tools" / "sound_controls_regression.py"
SPEC = importlib.util.spec_from_file_location(
    "sound_controls_regression", MODULE_PATH
)
assert SPEC and SPEC.loader
controls = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = controls
SPEC.loader.exec_module(controls)


class ScriptTests(unittest.TestCase):
    def test_script_opens_sound_and_overshoots_both_volume_clamps(self) -> None:
        script = controls.automation_script()

        self.assertIn("press(455, 8)", script)
        self.assertIn("press(424, 108)", script)
        self.assertIn("press(340, 75)", script)
        self.assertIn("repeat_tap(47, 80, 40", script)
        self.assertIn("repeat_tap(47, 246, 40", script)
        self.assertEqual(script.count("press(160, 85)"), 2)


class ResultTests(unittest.TestCase):
    def test_parse_result(self) -> None:
        self.assertEqual(
            controls.parse_result(
                b"SOUND_CONTROLS max_frame=4000 min_frame=5000\n"
            ),
            (4000, 5000),
        )
        self.assertIsNone(controls.parse_result(b"SOUND_CONTROLS incomplete"))

    def test_slider_knob_center_finds_wide_thumb(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "slider.png"
            image = Image.new("L", (480, 320), 255)
            draw = ImageDraw.Draw(image)
            draw.line((47, 92, 47, 236), fill=0, width=2)
            draw.rectangle((25, 110, 69, 122), fill=0)
            image.save(path)

            self.assertEqual(controls.slider_knob_center(path), 116)

    def test_preview_peak_reads_requested_emulated_window(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "preview.wav"
            rate = 600
            samples = [0] * rate + [1234] * rate
            with wave.open(str(path), "wb") as capture:
                capture.setnchannels(1)
                capture.setsampwidth(2)
                capture.setframerate(rate)
                capture.writeframes(struct.pack(f"<{len(samples)}h", *samples))

            self.assertEqual(controls.preview_peak(path, 60, 60), 1234)


if __name__ == "__main__":
    unittest.main()
