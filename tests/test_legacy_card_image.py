from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import legacy_card_image  # noqa: E402


def wrapper(common: bytes = b"common") -> bytes:
    prefix = bytearray(b"\xff" * 0x70)
    tuple_offset = 0x1D
    prefix[tuple_offset : tuple_offset + 6] = b"\xa0\x20GMMC"
    prefix[tuple_offset + 6 : tuple_offset + 10] = (0x0001_0001).to_bytes(
        4,
        "big",
    )
    prefix[tuple_offset + 10 : tuple_offset + 14] = b"RAMC"
    prefix[tuple_offset + 14 : tuple_offset + 18] = (0x70).to_bytes(4, "big")
    return bytes(prefix) + common


class LegacyCardImageTests(unittest.TestCase):
    def test_reads_metacluster_offset_from_gm_tuple(self) -> None:
        self.assertEqual(
            legacy_card_image.validate_simulator_wrapper(wrapper()),
            0x70,
        )

    def test_merges_changes_and_pads_with_erased_bytes(self) -> None:
        result = legacy_card_image.build_mame_image(
            wrapper(b"before"),
            b"latest",
        )
        self.assertEqual(len(result), legacy_card_image.CARD_SIZE)
        offset = legacy_card_image.SIMULATOR_HEADER_SIZE
        self.assertEqual(result[offset : offset + 6], b"latest")
        self.assertEqual(
            result[offset + 6 :],
            b"\xff" * (len(result) - offset - 6),
        )

    def test_wrapper_is_preserved_without_changes(self) -> None:
        source = wrapper(b"preserved")
        result = legacy_card_image.build_mame_image(source)
        self.assertEqual(result[: len(source)], source)

    def test_rejects_wrong_changes_size(self) -> None:
        with self.assertRaisesRegex(
            legacy_card_image.LegacyCardError,
            "changes image",
        ):
            legacy_card_image.build_mame_image(wrapper(b"before"), b"short")

    def test_rejects_nonlegacy_card(self) -> None:
        with self.assertRaisesRegex(
            legacy_card_image.LegacyCardError,
            "CISTPL_GM",
        ):
            legacy_card_image.build_mame_image(b"\xff" * 1024)

    def test_rejects_other_magic_cap_version(self) -> None:
        source = bytearray(wrapper())
        source[0x23:0x27] = (0x0002_0001).to_bytes(4, "big")
        with self.assertRaisesRegex(
            legacy_card_image.LegacyCardError,
            "unsupported",
        ):
            legacy_card_image.build_mame_image(bytes(source))


if __name__ == "__main__":
    unittest.main()
