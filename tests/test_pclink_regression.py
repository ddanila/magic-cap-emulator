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
        modem_match = pclink_regression.MODEM_PTY_PATTERN.search(output)
        self.assertIsNotNone(modem_match)
        assert modem_match is not None
        self.assertEqual(modem_match.group(1), b"/dev/pts/6")

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

    def test_isolates_magicbus_from_pclink_navigation(self) -> None:
        config = pclink_regression.isolated_machine_config()

        self.assertIn('tag=":MAGICBUS_ACCESSORY"', config)
        self.assertIn('defvalue="1" value="0"', config)

    def test_warm_provider_navigation_opens_pclink(
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

    def test_package_probe_opens_received_package(
        self,
    ) -> None:
        script = pclink_regression.lua_warm_provider_navigation(
            5200,
            7200,
            True,
            Path("/tmp/post-install.sta"),
        )

        self.assertNotIn("press(413, 46)", script)
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
        self.assertIn("local package_ready_frame = nil", script)
        self.assertIn(
            f"package_ready_frame + "
            f"{pclink_regression.PACKAGE_SETTLE_FRAMES}",
            script,
        )
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

    def test_provider_first_run_completes_owner_card_then_reaches_pclink(
        self,
    ) -> None:
        pclink_frame = pclink_regression.provider_first_run_pclink_frame(
            "Ada",
            "Lovelace",
        )
        script = pclink_regression.lua_warm_provider_navigation(
            pclink_frame + 120,
            pclink_frame + 2000,
            owner_first_name="Ada",
            owner_last_name="Lovelace",
        )

        self.assertIn("press(120, 40)", script)
        self.assertIn("provider-name-card-step-6.png", script)
        self.assertIn("provider-name-card-complete.png", script)
        self.assertIn("press(293, 258)", script)
        self.assertIn("provider-locations-tab.png", script)
        self.assertIn("press(450, 58)", script)
        self.assertIn("press(145, 103)", script)
        self.assertIn("provider-phone-locations.png", script)
        self.assertIn("press(102, 300)", script)
        self.assertIn("press(50, 104)", script)
        self.assertIn("provider-home-location-created.png", script)
        self.assertIn("provider-home-location-returned.png", script)
        self.assertIn("provider-choose-connection.png", script)
        self.assertIn("press(250, 77)", script)
        self.assertIn("provider-pccard-selected.png", script)
        self.assertIn("press(425, 202)", script)
        self.assertIn("provider-pccard-assigned.png", script)
        self.assertIn("press(440, 10)", script)
        self.assertIn("navigation-internet-center.png", script)
        self.assertIn("navigation-downtown.png", script)
        self.assertIn("navigation-storeroom.png", script)
        self.assertIn("press(48, 155)", script)
        self.assertIn(f"frames == {pclink_frame}", script)
        self.assertNotIn("0x13c29434", script)

    def test_name_card_automation_rejects_non_keyboard_characters(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "letters a-z only"):
            pclink_regression.name_card_key_steps("Ada-1", 100)
        with self.assertRaisesRegex(ValueError, "non-empty name"):
            pclink_regression.name_card_key_steps("", 100)

    def test_package_probe_has_no_magicbus_warning_workaround(
        self,
    ) -> None:
        script = pclink_regression.lua_warm_provider_navigation(
            5200,
            7200,
            probe_package=True,
            save_path=Path("/tmp/post-install.sta"),
        )

        self.assertNotIn("0x13c29434", script)
        self.assertNotIn("do R2=0; do PC=R31; g", script)
        self.assertNotIn("frames == 6650", script)
        self.assertNotIn("frames == 6700", script)

    def test_combined_acceptance_stays_live_through_browser_http(
        self,
    ) -> None:
        script = pclink_regression.lua_warm_provider_navigation(
            5200,
            18120,
            probe_package=True,
            package_ready_path=Path("/tmp/package-ready"),
            package_snapshotted_path=Path("/tmp/package-snapshotted"),
            browser_acceptance=True,
            http_port=8080,
        )

        self.assertIn("package-opened.png", script)
        self.assertIn("press(451, 148)", script)
        self.assertIn("browser-scene-opened.png", script)
        self.assertIn("press(391, 270)", script)
        self.assertIn("press(262, 270)", script)
        self.assertIn("press(434, 270)", script)
        self.assertIn("browser-url-entered.png", script)
        self.assertIn("press(419, 143)", script)
        self.assertIn("browser-result.png", script)
        self.assertNotIn("machine:save", script)

if __name__ == "__main__":
    unittest.main()
