import importlib.util
import io
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "tools" / "rom_diff.py"
SPEC = importlib.util.spec_from_file_location("rom_diff", MODULE_PATH)
assert SPEC and SPEC.loader
rom_diff = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rom_diff
SPEC.loader.exec_module(rom_diff)


def sample_rom(tail: bytes = b"", *, strings: bytes = b"") -> bytes:
    head = (
        struct.pack(">I", rom_diff.RESET_INSTRUCTION)
        + bytes(8)
        + rom_diff.MONITOR_MARKER
    )
    return head + b"Magic Cap 3.1.2j built Apr  7 1998" + strings + tail


class RomFactsTests(unittest.TestCase):
    def test_reads_reset_word_marker_and_stamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.image"
            path.write_bytes(sample_rom())

            facts = rom_diff.read_facts(path)

        self.assertEqual(facts.first_instruction, rom_diff.RESET_INSTRUCTION)
        self.assertTrue(facts.has_monitor_marker)
        self.assertIn(b"Apr  7 1998", facts.dates)
        self.assertIn(b"3.1.2j", facts.versions)

    def test_counts_debug_markers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "debug.image"
            path.write_bytes(
                sample_rom(strings=b"Assert failed /export/src/gm/cap/Foo.cp Assert")
            )

            facts = rom_diff.read_facts(path)

        self.assertEqual(facts.debug_marker_counts[b"Assert"], 2)
        self.assertEqual(facts.debug_marker_counts[b"/export/src/gm/"], 1)

    def test_rejects_short_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "short.image"
            path.write_bytes(b"\x08\xf0\x00\x07")

            with self.assertRaisesRegex(rom_diff.FormatError, "too short"):
                rom_diff.read_facts(path)


class SpanTests(unittest.TestCase):
    def test_merges_runs_within_gap_tolerance(self) -> None:
        a = bytes(200)
        b = bytearray(200)
        b[10] = 1
        b[20] = 1  # 9 identical bytes away: merged at tolerance 64
        b[150] = 1  # far away: its own span

        spans = rom_diff.differing_spans(a, bytes(b), gap_tolerance=64)

        self.assertEqual(spans, [(10, 11), (150, 1)])

    def test_splits_runs_beyond_gap_tolerance(self) -> None:
        a = bytes(200)
        b = bytearray(200)
        b[10] = 1
        b[20] = 1

        spans = rom_diff.differing_spans(a, bytes(b), gap_tolerance=4)

        self.assertEqual(spans, [(10, 1), (20, 1)])

    def test_requires_equal_lengths(self) -> None:
        with self.assertRaisesRegex(rom_diff.FormatError, "equal-length"):
            rom_diff.differing_spans(bytes(4), bytes(5))

    def test_common_prefix_and_suffix(self) -> None:
        self.assertEqual(rom_diff.common_prefix_length(b"abcdef", b"abcXef"), 3)
        self.assertEqual(rom_diff.common_suffix_length(b"abcdef", b"Xbcdef", 6), 5)


class CliTests(unittest.TestCase):
    def test_reports_equal_length_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            a_path = Path(directory) / "a.image"
            b_path = Path(directory) / "b.image"
            a_path.write_bytes(sample_rom(tail=bytes(64)))
            b_path.write_bytes(sample_rom(tail=bytes(63) + b"\x01"))

            output = io.StringIO()
            with redirect_stdout(output):
                result = rom_diff.main([str(a_path), str(b_path)])

        text = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("differing spans: 1", text)
        self.assertNotIn("byte-identical", text)

    def test_reports_identical_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            a_path = Path(directory) / "a.image"
            b_path = Path(directory) / "b.image"
            a_path.write_bytes(sample_rom())
            b_path.write_bytes(sample_rom())

            output = io.StringIO()
            with redirect_stdout(output):
                result = rom_diff.main([str(a_path), str(b_path)])

        self.assertEqual(result, 0)
        self.assertIn("byte-identical", output.getvalue())

    def test_skips_span_diff_for_unequal_lengths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            a_path = Path(directory) / "a.image"
            b_path = Path(directory) / "b.image"
            a_path.write_bytes(sample_rom())
            b_path.write_bytes(sample_rom(tail=b"extra payload"))

            output = io.StringIO()
            with redirect_stdout(output):
                result = rom_diff.main([str(a_path), str(b_path)])

        text = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("size delta:      +13", text)
        self.assertIn("span diff:       skipped", text)

    def test_missing_file_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            a_path = Path(directory) / "a.image"
            a_path.write_bytes(sample_rom())

            with redirect_stdout(io.StringIO()):
                result = rom_diff.main(
                    [str(a_path), str(Path(directory) / "missing.image")]
                )

        self.assertEqual(result, 2)


if __name__ == "__main__":
    unittest.main()
