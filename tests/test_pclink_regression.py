from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from tools import pclink_regression


class PCLinkProtocolTests(unittest.TestCase):
    def test_pclink_pty_pattern_ignores_pc_card_modem(self) -> None:
        output = (
            b":pccard1:modem PTY: /dev/pts/6\n"
            b":rs2321:pty PTY: /dev/pts/8\n"
        )

        match = pclink_regression.PTY_PATTERN.search(output)

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group(1), b"/dev/pts/8")

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

    def test_navigation_leaves_failure_checkpoints(self) -> None:
        script = pclink_regression.lua_navigation(4700, 4900)

        self.assertIn("navigation-workbench.png", script)
        self.assertIn("navigation-storeroom.png", script)
        self.assertIn("frames == 4700", script)
        self.assertIn("pclink-disconnected.png", script)
        self.assertNotIn("power_button", script)
        self.assertIn("frames == 4900", script)

    def test_warm_provider_navigation_dismisses_alerts_and_opens_pclink(
        self,
    ) -> None:
        script = pclink_regression.lua_warm_provider_navigation(5200, 5400)

        self.assertIn("press(413, 61)", script)
        self.assertIn("press(421, 70)", script)
        self.assertIn("press(343, 48)", script)
        self.assertIn("press(440, 10)", script)
        self.assertIn("press(452, 255)", script)
        self.assertIn("press(48, 155)", script)
        self.assertIn("navigation-storeroom.png", script)
        self.assertIn("frames == 5200", script)
        self.assertNotIn("power_button", script)
        self.assertIn("frames == 5400", script)

    def test_package_probe_recovers_alert_and_opens_received_package(
        self,
    ) -> None:
        script = pclink_regression.lua_warm_provider_navigation(
            5200,
            7200,
            True,
            Path("/tmp/post-install.sta"),
        )

        self.assertIn("press(413, 46)", script)
        self.assertIn("press(413, 61)", script)
        self.assertIn("press(270, 220)", script)
        self.assertIn("pclink-disconnected.png", script)
        self.assertIn("package-opened.png", script)
        self.assertIn("post-package-downtown.png", script)
        self.assertIn("downtown-directory.png", script)
        self.assertIn('machine:save("/tmp/post-install.sta")', script)
        self.assertNotIn("power_button", script)

    def test_warm_navigation_snapshots_before_host_disconnect(self) -> None:
        script = pclink_regression.lua_warm_provider_navigation(
            5200,
            7200,
            package_ready_path=Path("/tmp/package-ready"),
            package_snapshotted_path=Path("/tmp/package-snapshotted"),
        )

        self.assertIn('io.open("/tmp/package-ready", "r")', script)
        self.assertIn('snapshot("package-installed.png")', script)
        self.assertIn('io.open("/tmp/package-snapshotted", "w")', script)

    def test_internet_center_navigation_reaches_storeroom(self) -> None:
        script = pclink_regression.lua_warm_provider_navigation(
            5200,
            7200,
            internet_center_start=True,
        )

        self.assertIn("press(430, 10)", script)
        self.assertIn("navigation-downtown.png", script)
        self.assertIn("press(60, 130)", script)
        self.assertIn("press(170, 132)", script)
        self.assertIn("press(48, 155)", script)

    def test_package_probe_dismisses_both_magicbus_alert_variants(
        self,
    ) -> None:
        script = pclink_regression.lua_warm_provider_navigation(
            5200,
            7200,
            probe_package=True,
            save_path=Path("/tmp/post-install.sta"),
            suppress_magicbus_warning=True,
        )

        self.assertIn("press(413, 46)", script)
        self.assertIn("press(413, 61)", script)
        self.assertIn("0x13c29434", script)
        self.assertIn("do R2=0; do PC=R31; g", script)

if __name__ == "__main__":
    unittest.main()
