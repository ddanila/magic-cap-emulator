import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


MODULE_PATH = Path(__file__).parents[1] / "tools" / "make_demo_gif.py"
SPEC = importlib.util.spec_from_file_location("make_demo_gif", MODULE_PATH)
assert SPEC and SPEC.loader
make_demo_gif = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = make_demo_gif
SPEC.loader.exec_module(make_demo_gif)


def write_frame(directory: Path, index: int, level: int) -> Path:
    """Write a frame filled with `level`, re-encoded so bytes vary."""
    path = directory / f"f{index:05d}.png"
    image = Image.new("L", (8, 4), color=level)
    # A pixel-identical frame saved with different PNG settings has different
    # bytes; the tool must not treat that as a new frame.
    image.save(path, optimize=bool(index % 2))
    return path


class FrameRunTests(unittest.TestCase):
    def test_collapses_identical_pixels_despite_differing_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            for index in range(5):
                write_frame(directory, index, 40)
            write_frame(directory, 5, 200)

            runs = make_demo_gif.frame_runs(
                make_demo_gif.frame_paths(directory)
            )

            self.assertEqual([hold for _frame, hold in runs], [5, 1])

    def test_repeated_scene_after_a_change_is_a_new_run(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            for index, level in enumerate((10, 10, 90, 10)):
                write_frame(directory, index, level)

            runs = make_demo_gif.frame_runs(
                make_demo_gif.frame_paths(directory)
            )

            self.assertEqual([hold for _frame, hold in runs], [2, 1, 1])

    def test_frames_are_ordered_by_capture_index(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            for index in (11, 2, 100):
                write_frame(directory, index, index)

            names = [
                path.name for path in make_demo_gif.frame_paths(directory)
            ]

            self.assertEqual(names, ["f00002.png", "f00011.png", "f00100.png"])


class DelayTests(unittest.TestCase):
    def runs(self, holds: list[int]) -> list[tuple[Path, int]]:
        return [(Path(f"f{index}.png"), hold) for index, hold in enumerate(holds)]

    def test_static_scene_is_capped_and_animation_keeps_its_speed(self) -> None:
        # 600 frames at 60 fps is 10 s on screen; 6 frames is 100 ms.
        delays = make_demo_gif.delays_ms(self.runs([600, 6]), fps=60.0)

        self.assertEqual(delays, [1000, 100])

    def test_single_frame_steps_get_the_floor_not_one_tick(self) -> None:
        delays = make_demo_gif.delays_ms(
            self.runs([1]), fps=60.0, floor_ms=40
        )

        self.assertEqual(delays, [40])

    def test_delays_are_whole_gif_ticks(self) -> None:
        delays = make_demo_gif.delays_ms(self.runs([7, 13, 41]), fps=60.0)

        for delay in delays:
            self.assertEqual(delay % make_demo_gif.GIF_TICK_MS, 0)

    def test_flat_mode_ignores_recorded_duration(self) -> None:
        delays = make_demo_gif.delays_ms(
            self.runs([600, 1, 30]), fps=60.0, cap_ms=1000, flat=True
        )

        self.assertEqual(delays, [1000, 1000, 1000])


class GifWritingTests(unittest.TestCase):
    def test_writes_native_size_frames_with_their_delays(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            frames = [
                (write_frame(directory, 0, 20), 600),
                (write_frame(directory, 1, 220), 6),
            ]
            out = directory / "tour.gif"

            delays = make_demo_gif.delays_ms(frames, fps=60.0)
            make_demo_gif.write_gif(frames, delays, out)

            with Image.open(out) as gif:
                self.assertEqual(gif.size, (8, 4))
                self.assertEqual(gif.n_frames, 2)
                self.assertEqual(gif.info["duration"], 1000)
                gif.seek(1)
                self.assertEqual(gif.info["duration"], 100)


if __name__ == "__main__":
    unittest.main()
