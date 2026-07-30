from __future__ import annotations

import unittest

from tools.flash_variant_regression import (
    FLASH_SIZE,
    LANE_SIZE,
    lane_digests,
    reassemble_flash,
    verify_flash_seed,
)


class FlashVariantRegressionTests(unittest.TestCase):
    def test_reassembles_four_big_endian_byte_lanes(self) -> None:
        lanes = [bytes([index]) * LANE_SIZE for index in range(4)]
        image = reassemble_flash(lanes)
        self.assertEqual(image[:8], b"\x00\x01\x02\x03\x00\x01\x02\x03")
        self.assertEqual(len(image), FLASH_SIZE)

    def test_accepts_rom_prefix_and_erased_tail(self) -> None:
        rom = b"\x08\xf0\x00\x07"
        lanes = [
            bytes([rom[index]]) + (b"\xff" * (LANE_SIZE - 1))
            for index in range(4)
        ]
        self.assertEqual(verify_flash_seed(rom, lanes), [])

    def test_rejects_reversed_lanes_and_programmed_tail(self) -> None:
        rom = b"\x08\xf0\x00\x07"
        lanes = [
            bytes([rom[3 - index]]) + (b"\xff" * (LANE_SIZE - 1))
            for index in range(4)
        ]
        lanes[0] = lanes[0][:-1] + b"\x00"
        failures = verify_flash_seed(rom, lanes)
        self.assertTrue(any("differs from ROM" in failure for failure in failures))
        self.assertTrue(any("tail is not erased" in failure for failure in failures))

    def test_lane_hashes_detect_persistence_changes(self) -> None:
        lanes = [bytes([index]) * LANE_SIZE for index in range(4)]
        changed = lanes.copy()
        changed[2] = changed[2][:-1] + b"\xff"
        self.assertNotEqual(lane_digests(lanes), lane_digests(changed))

    def test_rejects_wrong_lane_geometry(self) -> None:
        with self.assertRaisesRegex(ValueError, "four flash lanes"):
            reassemble_flash([])
        with self.assertRaisesRegex(ValueError, "exactly 2 MiB"):
            reassemble_flash([b"", b"", b"", b""])


if __name__ == "__main__":
    unittest.main()
