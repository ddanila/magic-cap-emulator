from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from tools import pclink_regression


class PCLinkProtocolTests(unittest.TestCase):
    def test_escape_round_trip(self) -> None:
        original = bytes(range(32))
        escaped = pclink_regression.escape_payload(original)
        self.assertEqual(
            escaped,
            bytes(range(14))
            + b"\x10\x0e\x10\x0f\x10\x10"
            + bytes(range(17, 32)),
        )
        self.assertEqual(pclink_regression.unescape_payload(escaped), original)

    def test_crc_frames_cover_escaped_bytes(self) -> None:
        original = (b"A" * 254) + b"\x0eB"
        wire = pclink_regression.encode_crc_stream(original)
        first_size = int.from_bytes(wire[:2], "big")
        self.assertEqual(first_size, 256)
        first_frame = wire[2 : 2 + first_size]
        self.assertEqual(first_frame[-2:], b"\x10\x0e")
        crc = int.from_bytes(wire[2 + first_size : 6 + first_size], "big")
        self.assertEqual(crc, (~zlib.crc32(first_frame)) & 0xFFFFFFFF)
        self.assertEqual(pclink_regression.decode_crc_stream(wire), original)

    def test_escape_pair_may_cross_frame_boundary(self) -> None:
        original = (b"A" * 255) + b"\x10B"
        wire = pclink_regression.encode_crc_stream(original)
        self.assertEqual(int.from_bytes(wire[:2], "big"), 256)
        self.assertEqual(wire[257], 0x10)
        second = 2 + 256 + 4
        self.assertEqual(wire[second + 2], 0x10)
        self.assertEqual(pclink_regression.decode_crc_stream(wire), original)

    def test_crc_mismatch_is_rejected(self) -> None:
        wire = bytearray(pclink_regression.encode_crc_stream(b"payload"))
        wire[-1] ^= 1
        with self.assertRaises(pclink_regression.ProtocolError):
            pclink_regression.decode_crc_stream(bytes(wire))

    def test_packet_round_trip(self) -> None:
        wire = pclink_regression.encode_packet(b"Test", b"contents")
        packet = pclink_regression.decode_crc_stream(wire)
        self.assertEqual(
            pclink_regression.decode_packet(packet),
            (b"Test", b"contents"),
        )

    def test_package_metadata_matches_win_pclink_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "DvorakKeyboard.pkg"
            package.write_bytes(b"x" * 20332)
            metadata = pclink_regression.package_metadata(package)

        self.assertEqual(len(metadata), 0x404)
        self.assertEqual(struct.unpack_from(">II", metadata), (20332, 20332))
        self.assertEqual(struct.unpack_from(">I", metadata, 24)[0], 0x80000000)
        self.assertEqual(struct.unpack_from(">I", metadata, 28)[0], 18)
        self.assertEqual(
            metadata[32 : 32 + 36],
            "DvorakKeyboard.pkg".encode("utf-16-be"),
        )


if __name__ == "__main__":
    unittest.main()
