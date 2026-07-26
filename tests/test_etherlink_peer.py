from __future__ import annotations

import ipaddress
import struct
import unittest

from tools import etherlink_peer


GUEST_MAC = bytes.fromhex("6002128c5634")


def arp_request(target: bytes) -> bytes:
    payload = struct.pack(
        "!HHBBH6s4s6s4s",
        1,
        0x0800,
        6,
        4,
        1,
        GUEST_MAC,
        etherlink_peer.GUEST_IP,
        b"\xff" * 6,
        target,
    )
    return etherlink_peer.ethernet_frame(
        b"\xff" * 6,
        GUEST_MAC,
        0x0806,
        payload,
    )


class ChecksumTests(unittest.TestCase):
    def test_ipv4_header_checksum_round_trips(self) -> None:
        packet = etherlink_peer.ipv4_packet(
            etherlink_peer.DNS_IP,
            etherlink_peer.GUEST_IP,
            17,
            b"payload",
            7,
        )
        self.assertEqual(0, etherlink_peer.internet_checksum(packet[:20]))

    def test_tcp_checksum_includes_pseudo_header(self) -> None:
        segment = etherlink_peer.tcp_packet(
            etherlink_peer.HTTP_IP,
            etherlink_peer.GUEST_IP,
            80,
            1025,
            1,
            2,
            0x12,
        )
        pseudo = struct.pack(
            "!4s4sBBH",
            etherlink_peer.HTTP_IP,
            etherlink_peer.GUEST_IP,
            0,
            6,
            len(segment),
        )
        self.assertEqual(
            0,
            etherlink_peer.internet_checksum(pseudo + segment),
        )

    def test_tcp_options_update_data_offset_and_checksum(self) -> None:
        segment = etherlink_peer.tcp_packet(
            etherlink_peer.HTTP_IP,
            etherlink_peer.GUEST_IP,
            80,
            1025,
            1,
            2,
            0x12,
            options=b"\x02\x04\x05\xb4",
        )
        pseudo = struct.pack(
            "!4s4sBBH",
            etherlink_peer.HTTP_IP,
            etherlink_peer.GUEST_IP,
            0,
            6,
            len(segment),
        )
        self.assertEqual(6, segment[12] >> 4)
        self.assertEqual(0, etherlink_peer.internet_checksum(pseudo + segment))


class FrameTests(unittest.TestCase):
    def test_ignores_duplicate_address_probe(self) -> None:
        peer = etherlink_peer.EtherLinkPeer()
        self.assertEqual(
            [],
            peer.handle_frame(arp_request(etherlink_peer.GUEST_IP)),
        )

    def test_answers_arp_for_dns_and_http_addresses(self) -> None:
        peer = etherlink_peer.EtherLinkPeer()
        for address in (etherlink_peer.DNS_IP, etherlink_peer.HTTP_IP):
            with self.subTest(address=ipaddress.IPv4Address(address)):
                responses = peer.handle_frame(arp_request(address))
                self.assertEqual(1, len(responses))
                response = responses[0]
                self.assertEqual(GUEST_MAC, response[:6])
                self.assertEqual(etherlink_peer.PEER_MAC, response[6:12])
                self.assertEqual(b"\x00\x02", response[20:22])
                self.assertEqual(address, response[28:32])

    def test_dns_answer_preserves_question_and_maps_to_http_peer(self) -> None:
        query = (
            b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
            b"\x01\x31\x00\x00\x01\x00\x01"
        )
        result = etherlink_peer.dns_answer(query)
        self.assertIsNotNone(result)
        assert result is not None
        name, response = result
        self.assertEqual("1", name)
        self.assertEqual(b"\x12\x34", response[:2])
        self.assertEqual(query[12:], response[12 : 12 + len(query[12:])])
        self.assertEqual(etherlink_peer.HTTP_IP, response[-4:])

    def test_tcp_syn_gets_syn_ack(self) -> None:
        peer = etherlink_peer.EtherLinkPeer()
        segment = etherlink_peer.tcp_packet(
            etherlink_peer.GUEST_IP,
            etherlink_peer.HTTP_IP,
            1025,
            80,
            100,
            0,
            0x02,
        )
        frame = etherlink_peer.ethernet_frame(
            etherlink_peer.PEER_MAC,
            GUEST_MAC,
            0x0800,
            etherlink_peer.ipv4_packet(
                etherlink_peer.GUEST_IP,
                etherlink_peer.HTTP_IP,
                6,
                segment,
                1,
            ),
        )
        responses = peer.handle_frame(frame)
        self.assertEqual(1, len(responses))
        tcp = responses[0][14 + 20 :]
        self.assertEqual(0x12, tcp[13])
        self.assertEqual(101, struct.unpack("!I", tcp[8:12])[0])


if __name__ == "__main__":
    unittest.main()
