#!/usr/bin/env python3
"""Deterministic raw-Ethernet peer for MAME's loopback UDP bridge.

The matching MAME ``udp`` network provider sends one Ethernet frame per UDP
datagram.  This peer supplies the smallest useful isolated LAN: ARP, DNS, and
an HTTP/1.0 TCP endpoint.  It never needs TAP, root, or access to a host
network interface.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import ipaddress
from pathlib import Path
import signal
import socket
import struct
import sys
import time
from typing import Callable, Sequence


GUEST_IP = ipaddress.IPv4Address("10.0.2.15").packed
DNS_IP = ipaddress.IPv4Address("10.0.2.3").packed
HTTP_IP = ipaddress.IPv4Address("10.0.2.2").packed
PEER_MAC = bytes.fromhex("020000000202")
DEFAULT_BODY = (
    b"<html><head><title>EtherLink OK</title></head>"
    b"<body><h1>EtherLink III works</h1>"
    b"<p>Magic Cap reached deterministic local HTTP.</p></body></html>\r\n"
)


def internet_checksum(data: bytes) -> int:
    """Return the RFC 1071 one's-complement checksum."""
    if len(data) & 1:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def ethernet_frame(
    destination: bytes,
    source: bytes,
    ether_type: int,
    payload: bytes,
) -> bytes:
    frame = destination + source + struct.pack("!H", ether_type) + payload
    return frame.ljust(60, b"\0")


def ipv4_packet(
    source: bytes,
    destination: bytes,
    protocol: int,
    payload: bytes,
    identification: int,
) -> bytes:
    header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(payload),
        identification & 0xFFFF,
        0,
        64,
        protocol,
        0,
        source,
        destination,
    )
    header = header[:10] + struct.pack("!H", internet_checksum(header)) + header[12:]
    return header + payload


def udp_packet(source_port: int, destination_port: int, payload: bytes) -> bytes:
    # A zero UDP checksum is valid for IPv4 and keeps the peer intentionally
    # small.  The IPv4 header itself is checksummed.
    return struct.pack(
        "!HHHH",
        source_port,
        destination_port,
        8 + len(payload),
        0,
    ) + payload


def tcp_packet(
    source_ip: bytes,
    destination_ip: bytes,
    source_port: int,
    destination_port: int,
    sequence: int,
    acknowledgement: int,
    flags: int,
    payload: bytes = b"",
    options: bytes = b"",
) -> bytes:
    if len(options) > 40:
        raise ValueError("TCP options exceed 40 bytes")
    options = options.ljust((len(options) + 3) & ~3, b"\0")
    header = struct.pack(
        "!HHIIBBHHH",
        source_port,
        destination_port,
        sequence & 0xFFFFFFFF,
        acknowledgement & 0xFFFFFFFF,
        ((20 + len(options)) // 4) << 4,
        flags,
        4096,
        0,
        0,
    ) + options
    pseudo_header = struct.pack(
        "!4s4sBBH",
        source_ip,
        destination_ip,
        0,
        6,
        len(header) + len(payload),
    )
    checksum = internet_checksum(pseudo_header + header + payload)
    return header[:16] + struct.pack("!H", checksum) + header[18:] + payload


def parse_dns_question(message: bytes) -> tuple[str, bytes] | None:
    if len(message) < 12:
        return None
    _identifier, _flags, questions, _answers, _authority, _additional = (
        struct.unpack("!HHHHHH", message[:12])
    )
    if not questions:
        return None

    labels: list[str] = []
    position = 12
    while position < len(message):
        length = message[position]
        position += 1
        if not length:
            break
        if length & 0xC0 or position + length > len(message):
            return None
        labels.append(message[position : position + length].decode("ascii", "replace"))
        position += length
    else:
        return None

    if position + 4 > len(message):
        return None
    question = message[12 : position + 4]
    return ".".join(labels), question


def dns_answer(query: bytes, address: bytes = HTTP_IP) -> tuple[str, bytes] | None:
    parsed = parse_dns_question(query)
    if parsed is None:
        return None
    name, question = parsed
    identifier = query[:2]
    response = (
        identifier
        + struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0)
        + question
        + b"\xc0\x0c"
        + struct.pack("!HHIH", 1, 1, 60, 4)
        + address
    )
    return name, response


@dataclass
class TcpConnection:
    peer_initial: int
    peer_next: int
    guest_next: int
    response: bytes = b""
    request: bytearray = field(default_factory=bytearray)


class EtherLinkPeer:
    """Pure frame handler plus an optional UDP serving loop."""

    def __init__(
        self,
        *,
        body: bytes = DEFAULT_BODY,
        event: Callable[[str], None] | None = None,
    ) -> None:
        self.body = body
        self.event = event or (lambda _message: None)
        self.guest_mac: bytes | None = None
        self.identification = 1
        self.connections: dict[tuple[bytes, int, int], TcpConnection] = {}
        self.http_requests: list[str] = []

    def _next_identification(self) -> int:
        value = self.identification
        self.identification = (self.identification + 1) & 0xFFFF
        return value

    def _ip_frame(
        self,
        destination_mac: bytes,
        source_ip: bytes,
        destination_ip: bytes,
        protocol: int,
        payload: bytes,
    ) -> bytes:
        return ethernet_frame(
            destination_mac,
            PEER_MAC,
            0x0800,
            ipv4_packet(
                source_ip,
                destination_ip,
                protocol,
                payload,
                self._next_identification(),
            ),
        )

    def _handle_arp(self, source_mac: bytes, payload: bytes) -> list[bytes]:
        if len(payload) < 28:
            return []
        hardware, protocol, hardware_len, protocol_len, operation = struct.unpack(
            "!HHBBH", payload[:8]
        )
        if (hardware, protocol, hardware_len, protocol_len, operation) != (
            1,
            0x0800,
            6,
            4,
            1,
        ):
            return []

        sender_mac = payload[8:14]
        sender_ip = payload[14:18]
        target_ip = payload[24:28]
        self.guest_mac = sender_mac
        self.event(
            f"RX ARP who-has {ipaddress.IPv4Address(target_ip)} "
            f"tell {ipaddress.IPv4Address(sender_ip)}"
        )

        # Ignore Magic Cap's duplicate-address probe for its own address.
        if target_ip == sender_ip:
            return []
        if target_ip not in (DNS_IP, HTTP_IP):
            return []

        reply = struct.pack(
            "!HHBBH6s4s6s4s",
            1,
            0x0800,
            6,
            4,
            2,
            PEER_MAC,
            target_ip,
            sender_mac,
            sender_ip,
        )
        self.event(
            f"TX ARP {ipaddress.IPv4Address(target_ip)} is-at "
            f"{PEER_MAC.hex(':')}"
        )
        return [ethernet_frame(source_mac, PEER_MAC, 0x0806, reply)]

    def _handle_dns(
        self,
        source_mac: bytes,
        source_ip: bytes,
        destination_ip: bytes,
        payload: bytes,
    ) -> list[bytes]:
        if len(payload) < 8:
            return []
        source_port, destination_port, length, _checksum = struct.unpack(
            "!HHHH", payload[:8]
        )
        if destination_port != 53 or length < 8 or length > len(payload):
            return []

        answer = dns_answer(payload[8:length])
        if answer is None:
            return []
        name, response = answer
        self.event(f"RX DNS A {name or '.'}")
        self.event(f"TX DNS {name or '.'} -> {ipaddress.IPv4Address(HTTP_IP)}")
        datagram = udp_packet(53, source_port, response)
        return [
            self._ip_frame(
                source_mac,
                destination_ip,
                source_ip,
                17,
                datagram,
            )
        ]

    def _http_response(self) -> bytes:
        return (
            b"HTTP/1.0 200 OK\r\n"
            b"Content-Type: text/html\r\n"
            + f"Content-Length: {len(self.body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + self.body
        )

    def _handle_tcp(
        self,
        source_mac: bytes,
        source_ip: bytes,
        destination_ip: bytes,
        segment: bytes,
    ) -> list[bytes]:
        if len(segment) < 20:
            return []
        (
            source_port,
            destination_port,
            sequence,
            acknowledgement,
            data_offset,
            flags,
            _window,
            _checksum,
            _urgent,
        ) = struct.unpack("!HHIIBBHHH", segment[:20])
        header_length = (data_offset >> 4) * 4
        if (
            destination_port not in (80, 8080)
            or header_length < 20
            or header_length > len(segment)
        ):
            return []
        data = segment[header_length:]
        key = (source_ip, source_port, destination_port)
        self.event(
            f"RX TCP flags=0x{flags:02x} seq={sequence} ack={acknowledgement} "
            f"header={header_length} data={len(data)} "
            f"options={segment[20:header_length].hex() or '-'}"
        )

        if flags & 0x02:
            peer_initial = (0x53430000 + source_port) & 0xFFFFFFFF
            connection = self.connections.get(key)
            if connection is None:
                connection = TcpConnection(
                    peer_initial=peer_initial,
                    peer_next=(peer_initial + 1) & 0xFFFFFFFF,
                    guest_next=(sequence + 1) & 0xFFFFFFFF,
                )
                self.connections[key] = connection
            self.event(
                f"RX TCP SYN {ipaddress.IPv4Address(source_ip)}:{source_port} "
                f"-> {ipaddress.IPv4Address(destination_ip)}:{destination_port}"
            )
            reply = tcp_packet(
                destination_ip,
                source_ip,
                destination_port,
                source_port,
                connection.peer_initial,
                connection.guest_next,
                0x12,
                options=segment[20:header_length],
            )
            self.event(
                f"TX TCP SYN-ACK seq={connection.peer_initial} "
                f"ack={connection.guest_next}"
            )
            return [
                self._ip_frame(
                    source_mac,
                    destination_ip,
                    source_ip,
                    6,
                    reply,
                )
            ]

        connection = self.connections.get(key)
        if connection is None:
            return []

        if data:
            connection.guest_next = (sequence + len(data)) & 0xFFFFFFFF
            connection.request.extend(data)
            request_text = connection.request.decode("iso-8859-1", "replace")
            if "\r\n\r\n" not in request_text and "\n\n" not in request_text:
                return []

            request_line = request_text.splitlines()[0] if request_text else ""
            if not connection.response:
                self.http_requests.append(request_line)
                connection.response = self._http_response()
                self.event(f"RX HTTP {request_line}")
            reply = tcp_packet(
                destination_ip,
                source_ip,
                destination_port,
                source_port,
                connection.peer_next,
                connection.guest_next,
                0x19,  # FIN | PSH | ACK
                connection.response,
            )
            connection.peer_next = (
                connection.peer_next + len(connection.response) + 1
            ) & 0xFFFFFFFF
            self.event(
                f"TX HTTP 200 ({len(connection.response)} bytes) and FIN"
            )
            return [
                self._ip_frame(
                    source_mac,
                    destination_ip,
                    source_ip,
                    6,
                    reply,
                )
            ]

        if flags & 0x01:
            connection.guest_next = (sequence + 1) & 0xFFFFFFFF
            reply = tcp_packet(
                destination_ip,
                source_ip,
                destination_port,
                source_port,
                connection.peer_next,
                connection.guest_next,
                0x10,
            )
            return [
                self._ip_frame(
                    source_mac,
                    destination_ip,
                    source_ip,
                    6,
                    reply,
                )
            ]
        return []

    def _handle_ipv4(self, source_mac: bytes, packet: bytes) -> list[bytes]:
        if len(packet) < 20 or packet[0] >> 4 != 4:
            return []
        header_length = (packet[0] & 0x0F) * 4
        total_length = struct.unpack("!H", packet[2:4])[0]
        if (
            header_length < 20
            or total_length < header_length
            or total_length > len(packet)
        ):
            return []
        protocol = packet[9]
        source_ip = packet[12:16]
        destination_ip = packet[16:20]
        payload = packet[header_length:total_length]
        self.guest_mac = source_mac

        if protocol == 17 and destination_ip == DNS_IP:
            return self._handle_dns(
                source_mac,
                source_ip,
                destination_ip,
                payload,
            )
        if protocol == 6 and destination_ip == HTTP_IP:
            return self._handle_tcp(
                source_mac,
                source_ip,
                destination_ip,
                payload,
            )
        return []

    def handle_frame(self, frame: bytes) -> list[bytes]:
        if len(frame) < 14:
            return []
        _destination, source, ether_type = struct.unpack("!6s6sH", frame[:14])
        if ether_type == 0x0806:
            return self._handle_arp(source, frame[14:])
        if ether_type == 0x0800:
            return self._handle_ipv4(source, frame[14:])
        return []


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--local-port",
        type=int,
        default=58101,
        help="peer UDP receive port (default: 58101)",
    )
    parser.add_argument(
        "--mame-port",
        type=int,
        default=58100,
        help="MAME UDP receive port (default: 58100)",
    )
    parser.add_argument(
        "--trace",
        type=Path,
        help="write the decoded frame exchange to this file",
    )
    parser.add_argument(
        "--http-requests",
        type=Path,
        help="write received HTTP request lines to this file",
    )
    parser.add_argument(
        "--ready-file",
        type=Path,
        help="touch this file after binding the UDP socket",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0,
        help="exit after this many seconds; zero waits indefinitely",
    )
    parser.add_argument(
        "--response-delay-ms",
        type=float,
        default=10,
        help="delay replies to avoid zero-latency guest races (default: 10)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    for name in ("local_port", "mame_port"):
        value = getattr(args, name)
        if not 1 <= value <= 65535:
            print(f"error: --{name.replace('_', '-')} must be 1..65535", file=sys.stderr)
            return 2
    if not 0 <= args.response_delay_ms <= 10_000:
        print(
            "error: --response-delay-ms must be between 0 and 10000",
            file=sys.stderr,
        )
        return 2

    trace_lines: list[str] = []

    def record(message: str) -> None:
        stamped = f"{time.monotonic():.6f} {message}"
        trace_lines.append(stamped)
        print(stamped, flush=True)
        if args.trace:
            args.trace.parent.mkdir(parents=True, exist_ok=True)
            args.trace.write_text("\n".join(trace_lines) + "\n")

    peer = EtherLinkPeer(event=record)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", args.local_port))
    udp.settimeout(0.25)
    target = ("127.0.0.1", args.mame_port)

    if args.ready_file:
        args.ready_file.parent.mkdir(parents=True, exist_ok=True)
        args.ready_file.touch()
    record(
        f"READY 127.0.0.1:{args.local_port} -> "
        f"127.0.0.1:{args.mame_port}"
    )

    stop = False

    def request_stop(_signal: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    deadline = time.monotonic() + args.timeout if args.timeout else None
    try:
        while not stop and (deadline is None or time.monotonic() < deadline):
            try:
                frame, _address = udp.recvfrom(4096)
            except TimeoutError:
                continue
            for response in peer.handle_frame(frame):
                if args.response_delay_ms:
                    time.sleep(args.response_delay_ms / 1000)
                udp.sendto(response, target)
    except KeyboardInterrupt:
        pass
    finally:
        udp.close()
        if args.http_requests:
            args.http_requests.parent.mkdir(parents=True, exist_ok=True)
            args.http_requests.write_text(
                "".join(f"{line}\n" for line in peer.http_requests)
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
