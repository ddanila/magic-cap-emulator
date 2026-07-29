import socket
import time
import unittest

from tools.telephone_bridge_regression import (
    RX_BUFFER,
    TX_BUFFER,
    WORDS,
    automation_script,
    monitor_bridge_config,
    parse_result,
)
from tools.telephone_pcm_relay import PcmRelay


class TelephoneBridgeRegressionTests(unittest.TestCase):
    def test_relay_rejects_invalid_read_size(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid read size"):
            PcmRelay(read_size=0)

    def test_config_selects_monitor_and_external_bridge(self) -> None:
        config = monitor_bridge_config("datarover840")

        self.assertIn('tag=":BOOT_MODE"', config)
        self.assertIn('tag=":PHONE_PEER"', config)
        self.assertIn('mask="3"', config)
        self.assertIn('value="2"', config)

    def test_script_streams_distinct_words_without_internal_loopback(self) -> None:
        script = automation_script(0x1111_2222, 0x3333_4444)

        self.assertIn("0x11112222", script)
        self.assertIn("0x33334444", script)
        self.assertIn("program:write_u32(SIB_DMA, 0x0003)", script)
        self.assertIn("zero=%d self=%d first=%08X", script)
        self.assertNotIn("| 0x08 |", script)

    def test_result_parses(self) -> None:
        output = (
            b"PHONE_BRIDGE_RESULT received=64/64 expected=33334444 "
            b"enables=3 tx=00200000 rx=00210000\n"
        )

        self.assertEqual(
            parse_result(output),
            {
                "received": WORDS,
                "words": WORDS,
                "expected": 0x3333_4444,
                "enables": 3,
                "tx": TX_BUFFER,
                "rx": RX_BUFFER,
            },
        )

    def test_missing_result_rejected(self) -> None:
        self.assertIsNone(parse_result(b"PHONE_BRIDGE_RESULT missing"))

    def test_relay_allows_both_peers_to_prime_before_skew_limit(self) -> None:
        relay = PcmRelay()
        relay.start()
        first = socket.create_connection((relay.host, relay.port))
        second = socket.create_connection((relay.host, relay.port))
        try:
            first.sendall(b"\x00" * 4_096)
            deadline = time.monotonic() + 2
            while relay.forwarded[0] < 4_096 and time.monotonic() < deadline:
                time.sleep(0.01)

            self.assertEqual(relay.forwarded[0], 4_096)
        finally:
            first.close()
            second.close()
            relay.stop()

    def test_relay_supports_small_skew_and_bounded_capture(self) -> None:
        relay = PcmRelay(
            startup_grace=4,
            max_skew=4,
            capture_limit=3,
        )
        relay.start()
        first = socket.create_connection((relay.host, relay.port))
        second = socket.create_connection((relay.host, relay.port))
        second.settimeout(2)
        try:
            first.sendall(b"abcdef")

            self.assertEqual(second.recv(6), b"abcdef")
            self.assertEqual(relay.captured[0], b"abc")
            self.assertEqual(relay.captured[1], b"")
            self.assertEqual(relay.started_at_peer_bytes, [0, None])
        finally:
            first.close()
            second.close()
            relay.stop()

    def test_relay_reports_deterministic_connection_order(self) -> None:
        relay = PcmRelay()
        relay.start()
        first = socket.create_connection((relay.host, relay.port))
        self.assertTrue(relay.wait_for_peer_count(1, timeout=2))
        second = socket.create_connection((relay.host, relay.port))
        try:
            self.assertTrue(relay.wait_for_peer_count(2, timeout=2))
            self.assertEqual(relay.peer_count, 2)
        finally:
            first.close()
            second.close()
            relay.stop()

    def test_process_controller_pauses_the_leading_pcm_producer(self) -> None:
        changes: list[tuple[int, bool]] = []
        relay = PcmRelay(startup_grace=4, max_skew=4)
        relay.start()
        first = socket.create_connection((relay.host, relay.port))
        self.assertTrue(relay.wait_for_peer_count(1, timeout=2))
        second = socket.create_connection((relay.host, relay.port))
        self.assertTrue(relay.wait_for_peer_count(2, timeout=2))
        relay.set_process_controller(
            lambda index, paused: changes.append((index, paused))
        )
        try:
            first.sendall(b"abcd")
            deadline = time.monotonic() + 2
            while (0, True) not in changes and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIn((0, True), changes)

            second.sendall(b"wxyz")
            deadline = time.monotonic() + 2
            while (0, False) not in changes and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIn((0, False), changes)
            self.assertEqual(relay.forwarded, [4, 4])
        finally:
            first.close()
            second.close()
            relay.stop()

    def test_process_controller_releases_when_lagging_pcm_stalls(self) -> None:
        changes: list[tuple[int, bool]] = []
        relay = PcmRelay(
            startup_grace=4,
            max_skew=4,
            process_clock_stale_after=0.05,
        )
        relay.start()
        first = socket.create_connection((relay.host, relay.port))
        self.assertTrue(relay.wait_for_peer_count(1, timeout=2))
        second = socket.create_connection((relay.host, relay.port))
        self.assertTrue(relay.wait_for_peer_count(2, timeout=2))
        relay.set_process_controller(
            lambda index, paused: changes.append((index, paused))
        )
        try:
            first.sendall(b"abcd")
            deadline = time.monotonic() + 2
            while (0, False) not in changes and time.monotonic() < deadline:
                time.sleep(0.01)

            self.assertIn((0, True), changes)
            self.assertIn((0, False), changes)
        finally:
            first.close()
            second.close()
            relay.stop()

    def test_process_controller_releases_when_peer_process_exits(self) -> None:
        changes: list[tuple[int, bool]] = []
        relay = PcmRelay(startup_grace=4, max_skew=4)
        relay.start()
        first = socket.create_connection((relay.host, relay.port))
        self.assertTrue(relay.wait_for_peer_count(1, timeout=2))
        second = socket.create_connection((relay.host, relay.port))
        self.assertTrue(relay.wait_for_peer_count(2, timeout=2))
        relay.set_process_controller(
            lambda index, paused: changes.append((index, paused))
        )
        try:
            first.sendall(b"abcd")
            deadline = time.monotonic() + 2
            while (0, True) not in changes and time.monotonic() < deadline:
                time.sleep(0.01)
            relay.mark_peer_inactive(1)

            self.assertIn((0, True), changes)
            self.assertEqual(changes[-1], (0, False))
        finally:
            first.close()
            second.close()
            relay.stop()


if __name__ == "__main__":
    unittest.main()
