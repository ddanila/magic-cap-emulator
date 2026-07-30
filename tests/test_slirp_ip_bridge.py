from __future__ import annotations

import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tools.slirp_ip_bridge import SlirpBridge, build_helper


def internet_checksum(payload: bytes) -> int:
    if len(payload) & 1:
        payload += b"\0"
    total = sum(
        int.from_bytes(payload[offset : offset + 2], "big")
        for offset in range(0, len(payload), 2)
    )
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return total ^ 0xFFFF


def udp_packet(
    payload: bytes,
    destination_port: int,
    *,
    source_port: int = 20_000,
) -> bytes:
    source = socket.inet_aton("10.0.2.15")
    destination = socket.inet_aton("10.0.2.2")
    udp = struct.pack(">HHHH", source_port, destination_port, 8 + len(payload), 0)
    ip = bytearray(
        struct.pack(
            ">BBHHHBBH4s4s",
            0x45,
            0,
            20 + len(udp) + len(payload),
            0x1234,
            0,
            64,
            socket.IPPROTO_UDP,
            0,
            source,
            destination,
        )
    )
    ip[10:12] = internet_checksum(ip).to_bytes(2, "big")
    return bytes(ip) + udp + payload


def udp_payload(packet: bytes, destination_port: int) -> bytes | None:
    if len(packet) < 28 or packet[0] >> 4 != 4 or packet[9] != socket.IPPROTO_UDP:
        return None
    ip_header = (packet[0] & 0x0F) * 4
    if len(packet) < ip_header + 8:
        return None
    _source, target, length, _checksum = struct.unpack(
        ">HHHH", packet[ip_header : ip_header + 8]
    )
    if target != destination_port or length < 8:
        return None
    return packet[ip_header + 8 : ip_header + length]


def tcp_syn_packet(
    destination_port: int,
    *,
    source_port: int = 20_000,
) -> bytes:
    source = socket.inet_aton("10.0.2.15")
    destination = socket.inet_aton("10.0.2.2")
    tcp = bytearray(
        struct.pack(
            ">HHIIHHHH",
            source_port,
            destination_port,
            0x1234_5678,
            0,
            (6 << 12) | 0x02,
            4_096,
            0,
            0,
        )
        + bytes.fromhex("02040218")
    )
    pseudo = (
        source
        + destination
        + bytes((0, socket.IPPROTO_TCP))
        + len(tcp).to_bytes(2, "big")
    )
    tcp[16:18] = internet_checksum(pseudo + tcp).to_bytes(2, "big")
    ip = bytearray(
        struct.pack(
            ">BBHHHBBH4s4s",
            0x45,
            0,
            20 + len(tcp),
            0x1234,
            0,
            64,
            socket.IPPROTO_TCP,
            0,
            source,
            destination,
        )
    )
    ip[10:12] = internet_checksum(ip).to_bytes(2, "big")
    return bytes(ip) + tcp


def tcp_syn_ack_window(packet: bytes, destination_port: int) -> int | None:
    if len(packet) < 40 or packet[9] != socket.IPPROTO_TCP:
        return None
    ip_header = (packet[0] & 0x0F) * 4
    tcp = packet[ip_header:]
    source, target = struct.unpack(">HH", tcp[:4])
    flags = tcp[13]
    if source == destination_port or target != destination_port or flags & 0x12 != 0x12:
        return None
    pseudo = (
        packet[12:20] + bytes((0, socket.IPPROTO_TCP)) + len(tcp).to_bytes(2, "big")
    )
    if internet_checksum(pseudo + tcp) != 0:
        return None
    return int.from_bytes(tcp[14:16], "big")


class SlirpBridgeTests(unittest.TestCase):
    def test_builds_outside_the_repository_and_reuses_the_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = build_helper(Path(directory))
            modified = executable.stat().st_mtime_ns
            self.assertTrue(executable.is_file())
            self.assertEqual(build_helper(Path(directory)), executable)
            self.assertEqual(executable.stat().st_mtime_ns, modified)

    def test_routes_ipv4_udp_to_the_host_and_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = build_helper(Path(directory))
            server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            server.bind(("127.0.0.1", 0))
            server.settimeout(5)
            server_port = server.getsockname()[1]
            server_error: list[Exception] = []
            server_requests: list[bytes] = []

            def echo() -> None:
                try:
                    request, address = server.recvfrom(4096)
                    server_requests.append(request)
                    server.sendto(b"reply:" + request, address)
                except Exception as error:  # pragma: no cover - assertion below
                    server_error.append(error)

            thread = threading.Thread(target=echo)
            thread.start()
            response = None
            received_packets: list[bytes] = []
            bridge_errors = ""
            try:
                with SlirpBridge(executable, allow_host_loopback=True) as bridge:
                    request = udp_packet(b"magic-cap", server_port)
                    with self.assertRaises(ValueError):
                        bridge.send(request + b"\0")
                    bridge.send(request)
                    deadline = time.monotonic() + 5
                    while response is None and time.monotonic() < deadline:
                        packet = bridge.receive(timeout=0.1)
                        if packet is not None:
                            received_packets.append(packet)
                            response = udp_payload(packet, 20_000)
                    bridge_errors = bridge.errors
            finally:
                server.close()
                thread.join(timeout=5)
            self.assertEqual(server_error, [], bridge_errors)
            self.assertEqual(server_requests, [b"magic-cap"], bridge_errors)
            self.assertEqual(response, b"reply:magic-cap", bridge_errors)
            self.assertTrue(received_packets, bridge_errors)
            self.assertTrue(
                all(
                    len(packet) == int.from_bytes(packet[2:4], "big")
                    for packet in received_packets
                ),
                bridge_errors,
            )

    def test_clamps_tcp_window_for_the_guest_stack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = build_helper(Path(directory))
            server = socket.socket()
            server.bind(("127.0.0.1", 0))
            server.listen()
            server_port = server.getsockname()[1]
            window = None
            bridge_errors = ""
            try:
                with SlirpBridge(executable, allow_host_loopback=True) as bridge:
                    bridge.send(tcp_syn_packet(server_port))
                    deadline = time.monotonic() + 5
                    while window is None and time.monotonic() < deadline:
                        packet = bridge.receive(timeout=0.1)
                        if packet is not None:
                            window = tcp_syn_ack_window(packet, 20_000)
                    bridge_errors = bridge.errors
            finally:
                server.close()
            self.assertEqual(window, 4_096, bridge_errors)


if __name__ == "__main__":
    unittest.main()
