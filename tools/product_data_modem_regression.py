#!/usr/bin/env python3
"""Pair Web Browser's built-in dial-up path with a direct-answer DataRover."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.data_modem_pair_regression import (  # noqa: E402
    COUNTERS as ANSWER_COUNTERS,
    HALF_DMA_BYTES,
    MIN_PCM_BYTES,
    SYMBOLS as MODEM_SYMBOLS,
    automation_script as direct_answer_script,
    call,
    load_address,
    move,
    op,
    r,
    parse_result as parse_direct_result,
)
from tools.fax_pair_regression import CallPcmExchange  # noqa: E402


ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "product-data-modem-regression"
COUNTERS = 0x0030_4000
V32_POINTER = COUNTERS + 0x100
STATUS_EVENT = COUNTERS + 0x200
INIT_ARGS = COUNTERS + 0x300
ECHO_STUB = 0x0030_6000
ECHO_WRITE_STUB = ECHO_STUB + 0x400
ECHO_BUFFER = 0x0030_6800
ECHO_RESPONSE = 0x0030_6C00
DYNAMIC_RESPONSE = ECHO_RESPONSE + 0x800
ECHO_TOTAL = 0x0030_A800
ECHO_DONE = ECHO_TOTAL + 4
ECHO_READ_TOTAL = ECHO_TOTAL + 8
ECHO_RESPONSE_KIND = ECHO_TOTAL + 12
ECHO_IP_READS = ECHO_TOTAL + 16
ECHO_WRITE_LENGTH = ECHO_TOTAL + 20
ANSWER_DELIVER_COUNTER = ANSWER_COUNTERS + next(
    index * 4
    for index, (_, name) in enumerate(MODEM_SYMBOLS)
    if name == "lapm_deliver_data"
)
ECHO_PATTERN = re.compile(rb"PRODUCT_ANSWER_ECHO bytes=(\d+)")
PEER_DATA_PATTERN = re.compile(rb"PRODUCT_ANSWER_ECHO_DATA hex=([0-9A-F]+)")
PEER_ROUND_PATTERN = re.compile(
    rb"PRODUCT_ANSWER_PEER_DATA round=\d+ kind=(\d+) "
    rb"read=\d+ wrote=\d+ hex=([0-9A-F]*)"
)
HTTP_RESPONSE_PATTERN = re.compile(
    rb"PRODUCT_ANSWER_HTTP_RESPONSE bytes=(\d+)"
)
HTTP_FIN_PATTERN = re.compile(rb"PRODUCT_ANSWER_HTTP_FIN bytes=(\d+)")
HTTP_CLOSE_ACK_PATTERN = re.compile(
    rb"PRODUCT_ANSWER_HTTP_CLOSE_ACK bytes=(\d+)"
)
DEFAULT_HTTP_TEXT = "Magic Cap built-in modem works."
MAX_HTTP_APPLICATION = 4_096

PRODUCT_SYMBOLS = (
    (0x13D4_DD08, "new_dialup_link"),
    (0x13C4_E864, "ppp_start"),
    (0x13C4_DA18, "ppp_write"),
    (0x13C4_DF70, "ppp_read"),
    (0x13C4_ECE4, "ppp_check"),
    (0x13C4_FCDC, "lcp_frame"),
    (0x13C4_FF9C, "network_control_frame"),
    (0x13C5_A628, "connect"),
    (0x13C5_A938, "connect_number"),
    (0x13C5_C55C, "monitor_connection"),
    (0x13C5_B214, "modem_write"),
    (0x13C5_B2A4, "modem_read"),
    (0x13C5_B344, "modem_write_frame"),
    (0x13C5_B3D4, "modem_read_frame"),
    (0x13C5_9A08, "open"),
    (0x13C5_BF80, "start"),
    (0x13C5_AC84, "set_status_handler"),
    *MODEM_SYMBOLS[1:],
)
CALL_READY_COUNTER = COUNTERS + next(
    index * 4
    for index, (_, name) in enumerate(PRODUCT_SYMBOLS)
    if name == "connect_number"
)
PRODUCT_PPP_READ_COUNTER = COUNTERS + next(
    index * 4
    for index, (_, name) in enumerate(PRODUCT_SYMBOLS)
    if name == "ppp_read"
)
CALL_SETTLE_FRAMES = 240
MAX_PEER_READS = 20
PRODUCT_PATTERN = re.compile(
    rb"PRODUCT_DATA_MODEM_RESULT "
    + rb" ".join(name.encode() + rb"=(\d+)" for _, name in PRODUCT_SYMBOLS)
    + rb" detector=(\d+) rates="
    + rb"([0-9A-F]{4}),([0-9A-F]{4}),([0-9A-F]{4}),([0-9A-F]{4}) "
    + rb"enables=(\d+) size=(\d+) initargs="
    + rb"([0-9A-F]{8}),([0-9A-F]{8}),([0-9A-F]{8}),([0-9A-F]{8}),"
    + rb"([0-9A-F]{8}),([0-9A-F]{8}),([0-9A-F]{8}) cfg="
    + rb"([0-9A-F]{8}),([0-9A-F]{8}),([0-9A-F]{8}),([0-9A-F]{8}) status="
    + rb"([0-9A-F]{8}),([0-9A-F]{8}),([0-9A-F]{8}),([0-9A-F]{8}) "
    + rb"status_caller=([0-9A-F]{8}) status_target=([0-9A-F]{8})"
)


def _lua_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def ppp_fcs(payload: bytes) -> int:
    """Return the complemented RFC 1662 FCS for an unescaped PPP payload."""
    fcs = 0xFFFF
    for byte in payload:
        fcs ^= byte
        for _ in range(8):
            fcs = (fcs >> 1) ^ (0x8408 if fcs & 1 else 0)
    return fcs ^ 0xFFFF


def async_ppp_frame(payload: bytes) -> bytes:
    """Frame and escape a PPP payload using the initial all-control ACCM."""
    fcs = ppp_fcs(payload)
    framed = payload + bytes((fcs & 0xFF, fcs >> 8))
    escaped = bytearray((0x7E,))
    for byte in framed:
        if byte < 0x20 or byte in (0x7D, 0x7E):
            escaped.extend((0x7D, byte ^ 0x20))
        else:
            escaped.append(byte)
    escaped.append(0x7E)
    return bytes(escaped)


def initial_lcp_response(request_id: int = 0x1F) -> bytes:
    """Acknowledge Magic Cap's deterministic request and offer empty options."""
    options = bytes.fromhex("02060000000007020802")
    acknowledge = (
        bytes.fromhex("ff03c02102")
        + bytes((request_id,))
        + (4 + len(options)).to_bytes(2, "big")
        + options
    )
    peer_request = bytes.fromhex("ff03c02101010004")
    return async_ppp_frame(acknowledge) + async_ppp_frame(peer_request)


def initial_ipcp_response(request_id: int = 0x20) -> bytes:
    """Assign the guest address and request the peer address via IPCP."""
    guest_address = bytes.fromhex("03060a00020f")
    peer_address = bytes.fromhex("03060a000202")
    negative_acknowledge = (
        bytes.fromhex("ff03802103")
        + bytes((request_id,))
        + (4 + len(guest_address)).to_bytes(2, "big")
        + guest_address
    )
    peer_request = (
        bytes.fromhex("ff0380210102")
        + (4 + len(peer_address)).to_bytes(2, "big")
        + peer_address
    )
    return async_ppp_frame(negative_acknowledge) + async_ppp_frame(peer_request)


def final_ipcp_response(request_id: int = 0x21) -> bytes:
    """Acknowledge Magic Cap's corrected address and VJ request."""
    options = bytes.fromhex("03060a00020f0206002d0301")
    acknowledge = (
        bytes.fromhex("ff03802102")
        + bytes((request_id,))
        + (4 + len(options)).to_bytes(2, "big")
        + options
    )
    return async_ppp_frame(acknowledge)


def internet_checksum(payload: bytes) -> int:
    """Return the RFC 1071 checksum for an even- or odd-length byte string."""
    if len(payload) & 1:
        payload += b"\0"
    total = sum(
        int.from_bytes(payload[offset : offset + 2], "big")
        for offset in range(0, len(payload), 2)
    )
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return total ^ 0xFFFF


def tcp_syn_ack_response(client_sequence: int = 0x3F99_9B00) -> bytes:
    """Answer the deterministic browser SYN at the negotiated peer address."""
    source_ip = bytes.fromhex("0a000202")
    destination_ip = bytes.fromhex("0a00020f")
    tcp = bytearray.fromhex(
        "1f9004000102030400000000601210000000000002040218"
    )
    acknowledgement = (client_sequence + 1) & 0xFFFF_FFFF
    tcp[8:12] = acknowledgement.to_bytes(4, "big")
    pseudo_header = (
        source_ip
        + destination_ip
        + bytes((0, 6))
        + len(tcp).to_bytes(2, "big")
    )
    tcp[16:18] = internet_checksum(pseudo_header + tcp).to_bytes(2, "big")
    ip = bytearray.fromhex(
        "4500002c12340000400600000a0002020a00020f"
    )
    ip[10:12] = internet_checksum(ip).to_bytes(2, "big")
    return async_ppp_frame(bytes.fromhex("ff030021") + ip + tcp)


def echo_responder_words() -> list[int]:
    """Read one answer data unit and dispatch its protocol-specific reply."""
    words = [
        *load_address(8, 0xA030_0800),
        op(0x23, rs=8, rt=23, immediate=0),
        op(0x23, rs=8, rt=28, immediate=4),
    ]
    words += call(0x13E4_2DDC)
    empty = len(words)
    words += [0, 0]
    words += [
        move(16, 2),
        move(4, 16),
        *load_address(5, 0xA000_0000 + ECHO_BUFFER),
        *call(0x13E4_2CC8),
    ]
    read_failed = len(words)
    words += [0, 0]
    words += [
        *load_address(8, 0xA000_0000 + ECHO_READ_TOTAL),
        op(0x2B, rs=8, rt=2, immediate=0),
        op(0x0D, rt=4, immediate=0),
        op(0x0D, rt=9, immediate=0),
        *load_address(11, 0xA000_0000 + ECHO_BUFFER),
        move(12, 2),
        op(0x09, rs=12, rt=12, immediate=-2),
        op(0x0D, rt=13, immediate=0),
        op(0x0D, rt=14, immediate=0),
        op(0x0D, rt=15, immediate=0),
    ]
    branches: list[tuple[int, str]] = []
    labels: dict[str, int] = {}

    def branch(major: int, rs: int, rt: int, label: str) -> None:
        branches.append((len(words), label))
        words.extend((op(major, rs=rs, rt=rt), 0))

    def jump(label: str) -> None:
        branch(0x04, 0, 0, label)

    labels["scan"] = len(words)
    branch(0x06, 12, 0, "scan_done")
    words += [
        op(0x24, rs=11, rt=8, immediate=0),
        op(0x24, rs=11, rt=9, immediate=1),
        op(0x0D, rt=10, immediate=0x7D),
    ]
    branch(0x04, 8, 10, "check_ip")
    words += [op(0x0D, rt=10, immediate=0x80)]
    branch(0x04, 8, 10, "record_ipcp")
    words += [op(0x0D, rt=10, immediate=0xC0)]
    branch(0x04, 8, 10, "record_lcp")

    labels["advance_scan"] = len(words)
    words += [
        op(0x09, rs=11, rt=11, immediate=1),
        op(0x09, rs=12, rt=12, immediate=-1),
    ]
    jump("scan")

    labels["check_ip"] = len(words)
    words += [op(0x0D, rt=10, immediate=0x2A)]
    branch(0x04, 9, 10, "check_guest_address")
    words += [op(0x0D, rt=10, immediate=0x20)]
    branch(0x05, 9, 10, "advance_scan")
    words += [
        op(0x24, rs=11, rt=8, immediate=2),
        op(0x0D, rt=10, immediate=0x21),
    ]
    branch(0x04, 8, 10, "ip")
    jump("advance_scan")

    labels["check_guest_address"] = len(words)
    for offset, expected in (
        (2, 0x7D),
        (3, 0x20),
        (4, 0x7D),
        (5, 0x22),
        (6, 0x7D),
        (7, 0x2F),
    ):
        words += [
            op(0x24, rs=11, rt=8, immediate=offset),
            op(0x0D, rt=10, immediate=expected),
        ]
        branch(0x05, 8, 10, "advance_scan")
    words += [op(0x0D, rt=15, immediate=1)]
    jump("advance_scan")

    labels["record_ipcp"] = len(words)
    words += [op(0x0D, rt=10, immediate=0x21)]
    branch(0x05, 9, 10, "advance_scan")
    words += [move(13, 11)]
    jump("advance_scan")

    labels["record_lcp"] = len(words)
    words += [op(0x0D, rt=10, immediate=0x21)]
    branch(0x05, 9, 10, "advance_scan")
    words += [move(14, 11)]
    jump("advance_scan")

    labels["scan_done"] = len(words)
    branch(0x05, 13, 0, "ipcp")
    branch(0x05, 14, 0, "lcp")
    jump("write")

    labels["lcp"] = len(words)
    words += [op(0x0D, rt=9, immediate=1)]
    jump("write")

    labels["ipcp"] = len(words)
    branch(0x04, 15, 0, "initial_ipcp")
    words += [op(0x0D, rt=9, immediate=3)]
    jump("write")

    labels["initial_ipcp"] = len(words)
    words += [op(0x0D, rt=9, immediate=2)]
    jump("write")

    labels["ip"] = len(words)
    words += [
        op(0x0D, rt=9, immediate=4),
        *load_address(10, 0xA000_0000 + ECHO_IP_READS),
        op(0x23, rs=10, rt=8, immediate=0),
        op(0x09, rs=8, rt=8, immediate=1),
        op(0x2B, rs=10, rt=8, immediate=0),
    ]
    jump("write")

    labels["write"] = len(words)
    words += [
        *load_address(10, 0xA000_0000 + ECHO_RESPONSE_KIND),
        op(0x2B, rs=10, rt=9, immediate=0),
    ]
    no_response = len(words)
    words += [0, 0]
    words += [
        *call(0x13E4_2C80),
        *load_address(8, 0xA000_0000 + ECHO_TOTAL),
        op(0x23, rs=8, rt=9, immediate=0),
        r(rs=9, rt=2, rd=9, function=0x21),
        op(0x2B, rs=8, rt=9, immediate=0),
    ]
    done = len(words)
    words += [
        *load_address(8, 0xA000_0000 + ECHO_DONE),
        op(0x0D, rt=9, immediate=1),
        op(0x2B, rs=8, rt=9, immediate=0),
        0x1000_FFFF,
        0,
    ]
    words[empty] = op(0x06, rs=2, immediate=done - (empty + 1))
    words[read_failed] = op(
        0x06, rs=2, immediate=done - (read_failed + 1)
    )
    words[no_response] = op(
        0x06, rs=4, immediate=done - (no_response + 1)
    )
    for index, label in branches:
        words[index] = (words[index] & 0xFFFF_0000) | (
            (labels[label] - (index + 1)) & 0xFFFF
        )
    return words


def write_responder_words() -> list[int]:
    """Write one Lua-generated response through the answer ROM queue."""
    words = [
        *load_address(8, 0xA000_0000 + ECHO_WRITE_LENGTH),
        op(0x23, rs=8, rt=4, immediate=0),
    ]
    empty = len(words)
    words += [0, 0]
    words += [
        *load_address(5, 0xA000_0000 + DYNAMIC_RESPONSE),
        *call(0x13E4_2C80),
        *load_address(8, 0xA000_0000 + ECHO_TOTAL),
        op(0x23, rs=8, rt=9, immediate=0),
        r(rs=9, rt=2, rd=9, function=0x21),
        op(0x2B, rs=8, rt=9, immediate=0),
    ]
    done = len(words)
    words += [
        *load_address(8, 0xA000_0000 + ECHO_DONE),
        op(0x0D, rt=9, immediate=1),
        op(0x2B, rs=8, rt=9, immediate=0),
        0x1000_FFFF,
        0,
    ]
    words[empty] = op(0x06, rs=4, immediate=done - (empty + 1))
    return words


def build_http_application(
    body: bytes,
    status: int = 200,
    reason: str = "OK",
    content_type: str = "text/html",
) -> bytes:
    """Normalize a bounded host response for the Magic Cap HTTP/1.0 peer."""
    safe_reason = reason.encode("ascii", "replace").decode("ascii")
    safe_content_type = content_type.encode("ascii", "replace").decode("ascii")
    headers = (
        f"HTTP/1.0 {status} {safe_reason}\r\n"
        f"Content-Type: {safe_content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    application = headers + body
    if len(application) > MAX_HTTP_APPLICATION:
        raise ValueError(
            f"normalized HTTP response exceeds {MAX_HTTP_APPLICATION} bytes"
        )
    return application


def fetch_http_application(url: str) -> bytes:
    """Fetch and normalize one explicitly configured host HTTP endpoint."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "magic-cap-emulator-host-bridge/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(MAX_HTTP_APPLICATION + 1)
            status = response.status
            reason = response.reason or ""
            content_type = response.headers.get_content_type()
    except (OSError, urllib.error.URLError) as error:
        raise ValueError(f"unable to fetch HTTP upstream: {error}") from error
    if len(body) > MAX_HTTP_APPLICATION:
        raise ValueError("HTTP upstream body exceeds the bridge limit")
    return build_http_application(body, status, reason, content_type)


def tcp_peer_lua(http_application: bytes | None = None) -> str:
    """Return Lua helpers that build a SYN-ACK from the live browser SYN."""
    if http_application is None:
        body = (
            f"<html><body>{DEFAULT_HTTP_TEXT}</body></html>\r\n".encode()
        )
        http_application = build_http_application(body)
    if len(http_application) > MAX_HTTP_APPLICATION:
        raise ValueError("HTTP application exceeds the bridge limit")
    application_lua = "{ " + ", ".join(
        f"0x{byte:02x}" for byte in http_application
    ) + " }"
    return r"""
local function append_bytes(target, source)
  for _, byte in ipairs(source) do table.insert(target, byte) end
end

local function internet_checksum(bytes)
  local total = 0
  for index = 1, #bytes, 2 do
    total = total + bytes[index] * 256 + (bytes[index + 1] or 0)
    total = (total & 0xffff) + (total >> 16)
  end
  while total > 0xffff do
    total = (total & 0xffff) + (total >> 16)
  end
  return (~total) & 0xffff
end

local function async_ppp_frame(payload)
  local fcs = 0xffff
  for _, byte in ipairs(payload) do
    fcs = fcs ~ byte
    for _ = 1, 8 do
      if (fcs & 1) ~= 0 then
        fcs = (fcs >> 1) ~ 0x8408
      else
        fcs = fcs >> 1
      end
    end
  end
  fcs = fcs ~ 0xffff
  local framed = {}
  append_bytes(framed, payload)
  table.insert(framed, fcs & 0xff)
  table.insert(framed, (fcs >> 8) & 0xff)
  local escaped = { 0x7e }
  for _, byte in ipairs(framed) do
    if byte < 0x20 or byte == 0x7d or byte == 0x7e then
      table.insert(escaped, 0x7d)
      table.insert(escaped, byte ~ 0x20)
    else
      table.insert(escaped, byte)
    end
  end
  table.insert(escaped, 0x7e)
  return escaped
end

local function read_ppp_frames()
  local frames, frame, escaped = {}, {}, false
  for offset = 0, program:read_u32(ECHO_READ_TOTAL) - 1 do
    local byte = program:read_u8(ECHO_BUFFER + offset)
    if byte == 0x7e then
      if #frame > 0 then table.insert(frames, frame) end
      frame, escaped = {}, false
    elseif escaped then
      table.insert(frame, byte ~ 0x20)
      escaped = false
    elseif byte == 0x7d then
      escaped = true
    else
      table.insert(frame, byte)
    end
  end
  return frames
end

local function dynamic_syn_ack()
  for _, frame in ipairs(read_ppp_frames()) do
    if #frame >= 44 and frame[1] == 0xff and frame[2] == 0x03
        and frame[3] == 0x00 and frame[4] == 0x21
        and frame[5] == 0x45 and (frame[38] & 0x02) ~= 0 then
      local sequence =
        ((frame[29] * 256 + frame[30]) * 256 + frame[31]) * 256 + frame[32]
      local acknowledgement = (sequence + 1) & 0xffffffff
      local ip = {
        0x45, 0x00, 0x00, 0x2c, 0x12, 0x34, 0x00, 0x00,
        0x40, 0x06, 0x00, 0x00, 0x0a, 0x00, 0x02, 0x02,
        0x0a, 0x00, 0x02, 0x0f
      }
      local ip_checksum = internet_checksum(ip)
      ip[11], ip[12] = (ip_checksum >> 8) & 0xff, ip_checksum & 0xff
      local tcp = {
        0x1f, 0x90, 0x04, 0x00, 0x01, 0x02, 0x03, 0x04,
        (acknowledgement >> 24) & 0xff,
        (acknowledgement >> 16) & 0xff,
        (acknowledgement >> 8) & 0xff,
        acknowledgement & 0xff,
        0x60, 0x12, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x02, 0x04, 0x02, 0x18
      }
      local pseudo = {
        0x0a, 0x00, 0x02, 0x02, 0x0a, 0x00, 0x02, 0x0f,
        0x00, 0x06, 0x00, #tcp
      }
      append_bytes(pseudo, tcp)
      local tcp_checksum = internet_checksum(pseudo)
      tcp[17], tcp[18] = (tcp_checksum >> 8) & 0xff, tcp_checksum & 0xff
      local payload = { 0xff, 0x03, 0x00, 0x21 }
      append_bytes(payload, ip)
      append_bytes(payload, tcp)
      return async_ppp_frame(payload)
    end
  end
  return nil
end

local http_pending = {}

local function collect_http_frame()
  for offset = 0, program:read_u32(ECHO_READ_TOTAL) - 1 do
    local byte = program:read_u8(ECHO_BUFFER + offset)
    if #http_pending == 0 then
      if byte == 0x7e then table.insert(http_pending, byte) end
    else
      table.insert(http_pending, byte)
      if byte == 0x7e and #http_pending > 2 then
        local frame, escaped = {}, false
        for index = 2, #http_pending - 1 do
          local encoded = http_pending[index]
          if escaped then
            table.insert(frame, encoded ~ 0x20)
            escaped = false
          elseif encoded == 0x7d then
            escaped = true
          else
            table.insert(frame, encoded)
          end
        end
        http_pending = {}
        if #frame >= 44 and frame[1] == 0xff and frame[2] == 0x03
            and frame[3] == 0x00 and frame[4] == 0x21
            and frame[5] == 0x45 then
          return frame
        end
      end
    end
  end
  return nil
end

local http_server_next_sequence = nil
local http_fin_sent = false
local http_close_ack_sent = false
local http_application = __HTTP_APPLICATION__

local function build_http_packet(
    sequence, acknowledgement, flags, application, identification)
  local tcp_length = 20 + #application
  local ip_length_out = 20 + tcp_length
  local ip = {
    0x45, 0x00, (ip_length_out >> 8) & 0xff, ip_length_out & 0xff,
    (identification >> 8) & 0xff, identification & 0xff,
    0x00, 0x00, 0x40, 0x06, 0x00, 0x00,
    0x0a, 0x00, 0x02, 0x02, 0x0a, 0x00, 0x02, 0x0f
  }
  local ip_checksum = internet_checksum(ip)
  ip[11], ip[12] = (ip_checksum >> 8) & 0xff, ip_checksum & 0xff
  local tcp = {
    0x1f, 0x90, 0x04, 0x00,
    (sequence >> 24) & 0xff,
    (sequence >> 16) & 0xff,
    (sequence >> 8) & 0xff,
    sequence & 0xff,
    (acknowledgement >> 24) & 0xff,
    (acknowledgement >> 16) & 0xff,
    (acknowledgement >> 8) & 0xff,
    acknowledgement & 0xff,
    0x50, flags, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00
  }
  append_bytes(tcp, application)
  local pseudo = {
    0x0a, 0x00, 0x02, 0x02, 0x0a, 0x00, 0x02, 0x0f,
    0x00, 0x06, (tcp_length >> 8) & 0xff, tcp_length & 0xff
  }
  append_bytes(pseudo, tcp)
  local tcp_checksum = internet_checksum(pseudo)
  tcp[17], tcp[18] = (tcp_checksum >> 8) & 0xff, tcp_checksum & 0xff
  local payload = { 0xff, 0x03, 0x00, 0x21 }
  append_bytes(payload, ip)
  append_bytes(payload, tcp)
  return async_ppp_frame(payload)
end

local function dynamic_http_response()
  local frame = collect_http_frame()
  if frame == nil then return nil, nil end
  local ip_header = (frame[5] & 0x0f) * 4
  local tcp_start = 5 + ip_header
  local tcp_header = (frame[tcp_start + 12] >> 4) * 4
  local data_start = tcp_start + tcp_header
  local client_sequence =
    ((frame[tcp_start + 4] * 256 + frame[tcp_start + 5]) * 256
      + frame[tcp_start + 6]) * 256 + frame[tcp_start + 7]
  local client_acknowledgement =
    ((frame[tcp_start + 8] * 256 + frame[tcp_start + 9]) * 256
      + frame[tcp_start + 10]) * 256 + frame[tcp_start + 11]
  local ip_length = frame[7] * 256 + frame[8]
  local client_data_length = ip_length - ip_header - tcp_header
  local flags = frame[tcp_start + 13]
  local client_next_sequence =
    (client_sequence + client_data_length
      + (((flags & 0x03) ~= 0) and 1 or 0)) & 0xffffffff
  if http_fin_sent and not http_close_ack_sent
      and (flags & 0x01) ~= 0 then
    http_close_ack_sent = true
    return build_http_packet(
      (http_server_next_sequence + 1) & 0xffffffff,
      client_next_sequence, 0x10, {}, 0x1237
    ), "close_ack"
  end
  if http_server_next_sequence ~= nil and not http_fin_sent
      and (flags & 0x10) ~= 0
      and client_acknowledgement == http_server_next_sequence then
    http_fin_sent = true
    return build_http_packet(
      http_server_next_sequence, client_next_sequence, 0x11, {}, 0x1236
    ), "fin"
  end
  if frame[data_start] ~= string.byte("G")
      or frame[data_start + 1] ~= string.byte("E")
      or frame[data_start + 2] ~= string.byte("T") then
    return nil, nil
  end
  http_server_next_sequence =
    (0x01020305 + #http_application) & 0xffffffff
  return build_http_packet(
    0x01020305, client_next_sequence, 0x18, http_application, 0x1235
  ), "response"
end

local function join_frames(first, second)
  local joined = {}
  append_bytes(joined, first)
  append_bytes(joined, second)
  return joined
end

local function control_ack(frame)
  local length = frame[7] * 256 + frame[8]
  local acknowledge = {}
  for index = 1, 4 + length do acknowledge[index] = frame[index] end
  acknowledge[5] = 0x02
  return async_ppp_frame(acknowledge)
end

local function dynamic_control_response(kind)
  for _, frame in ipairs(read_ppp_frames()) do
    if #frame >= 10 and frame[5] == 0x01 then
      if kind == 1 and frame[3] == 0xc0 and frame[4] == 0x21 then
        local peer_request =
          async_ppp_frame({ 0xff, 0x03, 0xc0, 0x21, 0x01, 0x01, 0x00, 0x04 })
        return join_frames(control_ack(frame), peer_request)
      elseif kind == 2 and frame[3] == 0x80 and frame[4] == 0x21 then
        local negative_acknowledge = async_ppp_frame({
          0xff, 0x03, 0x80, 0x21, 0x03, frame[6], 0x00, 0x0a,
          0x03, 0x06, 0x0a, 0x00, 0x02, 0x0f
        })
        local peer_request = async_ppp_frame({
          0xff, 0x03, 0x80, 0x21, 0x01, 0x02, 0x00, 0x0a,
          0x03, 0x06, 0x0a, 0x00, 0x02, 0x02
        })
        return join_frames(negative_acknowledge, peer_request)
      elseif kind == 3 and frame[3] == 0x80 and frame[4] == 0x21 then
        return control_ack(frame)
      end
    end
  end
  return nil
end

local function dynamic_peer_response(kind)
  if kind == 4 then
    return dynamic_syn_ack()
  end
  return dynamic_control_response(kind)
end
""".replace("__HTTP_APPLICATION__", application_lua)


def product_automation_script(
    call_trigger_path: Path = Path("call.trigger"),
    result_frame: int = 7600,
    reload_only: bool = False,
) -> str:
    """Drive the retained Internet Center provider through Web Browser reload."""
    addresses = ", ".join(f"0x{address:08x}" for address, _ in PRODUCT_SYMBOLS)
    fields = " ".join(f"{name}=%d" for _, name in PRODUCT_SYMBOLS)
    reads = ", ".join(
        f"program:read_u32(COUNTERS + {index * 4})"
        for index in range(len(PRODUCT_SYMBOLS))
    )
    trigger = _lua_path(call_trigger_path)
    result_path = _lua_path(call_trigger_path.parent / "product.result-ready")
    http_result_path = _lua_path(
        call_trigger_path.parent / "product-http.result-ready"
    )
    peer_result_path = _lua_path(call_trigger_path.parent / "answer.result-ready")
    script = f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local power_button = ports[":POWER_BUTTON"]:field(0x01)
local screen = machine.screens[":screen"]
local frames = 0
local result_written, http_result_written = false, false
local call_triggered, call_ready_frame = false, nil
local COUNTERS = 0x{COUNTERS:08x}
local CALL_READY_COUNTER = 0x{CALL_READY_COUNTER:08x}
local PRODUCT_PPP_READ_COUNTER = 0x{PRODUCT_PPP_READ_COUNTER:08x}
local CALL_SETTLE_FRAMES = {CALL_SETTLE_FRAMES}
local V32_POINTER = 0x{V32_POINTER:08x}
local STATUS_EVENT = 0x{STATUS_EVENT:08x}
local INIT_ARGS = 0x{INIT_ARGS:08x}
local addresses = {{ {addresses} }}
local call_trigger_path = "{trigger}"
local result_path = "{result_path}"
local http_result_path = "{http_result_path}"
local peer_result_path = "{peer_result_path}"

local function press(x, y)
  touch_x:set_value(math.floor(x * 65535 / 479))
  touch_y:set_value(math.floor(y * 65535 / 319))
  touch_button:set_value(1)
end

local function release()
  touch_button:set_value(0)
end

for index,address in ipairs(addresses) do
  local counter = COUNTERS + (index - 1) * 4
  program:write_u32(counter, 0)
  local action = string.format(
    "do d@0x%08x=d@0x%08x+1; g", counter, counter)
  if address == 0x13e42f20 then
    action = string.format(
      "do d@0x%08x=d@0x%08x+1; do d@0x%08x=R23; "
      .. "do d@0x%08x=R4; do d@0x%08x=R5; "
      .. "do d@0x%08x=R6; do d@0x%08x=R7; "
      .. "do d@0x%08x=d@(R29+16); do d@0x%08x=d@(R29+20); "
      .. "do d@0x%08x=d@(R29+24); "
      .. "do d@0x%08x=d@R6; do d@0x%08x=d@(R6+4); "
      .. "do d@0x%08x=d@(R6+8); do d@0x%08x=d@(R6+12); g",
      counter, counter, V32_POINTER,
      INIT_ARGS, INIT_ARGS + 4, INIT_ARGS + 8, INIT_ARGS + 12,
      INIT_ARGS + 16, INIT_ARGS + 20, INIT_ARGS + 24,
      INIT_ARGS + 28, INIT_ARGS + 32, INIT_ARGS + 36, INIT_ARGS + 40)
  elseif address == 0x13e50e80 then
    action = string.format(
      "do d@0x%08x=d@0x%08x+1; "
      .. "do d@0x%08x=d@0x%08x*256+(R4&255); "
      .. "do d@0x%08x=R4; do d@0x%08x=R31; g",
      counter, counter,
      STATUS_EVENT, STATUS_EVENT,
      STATUS_EVENT + 4, STATUS_EVENT + 8)
  end
  cpu.debug:bpset(address, "", action)
end
cpu.debug:bpset(
  0x13e50e94, "",
  string.format("do d@0x%08x=R25; g", STATUS_EVENT + 12))
program:write_u32(V32_POINTER, 0)
for offset = 0, 20, 4 do
  program:write_u32(STATUS_EVENT + offset, 0)
end
cpu.debug:go()

emu.register_frame_done(function()
  frames = frames + 1
  -- The calibrated source resumes in the Internet Center after cleanup.
  if frames == 550 then power_button:set_value(1)
  elseif frames == 700 then power_button:set_value(0)

  -- Internet Center -> Downtown -> Hallway -> Desk.
  elseif frames == 1250 then press(440, 10)
  elseif frames == 1270 then release()
  elseif frames == 1550 then press(440, 10)
  elseif frames == 1570 then release()
  elseif frames == 1850 then press(440, 10)
  elseif frames == 1870 then release()

  -- Magic lamp -> Web Browser.
  elseif frames == 2500 then press(34, 302)
  elseif frames == 2520 then release()
  elseif frames == 2800 then press(126, 75)
  elseif frames == 2820 then release()

  -- Commands -> provider, choose the retained provider, then reload.
  elseif frames == 3150 then press(171, 302)
  elseif frames == 3170 then release()
  elseif frames == 3450 then press(225, 225)
  elseif frames == 3470 then release()
  elseif frames == 3800 then press(180, 186)
  elseif frames == 3820 then release()
  elseif frames == 4100 then press(170, 153)
  elseif frames == 4120 then release()
  elseif frames == 4400 then press(300, 95)
  elseif frames == 4420 then release()
  elseif frames == 4700 then press(450, 250)
  elseif frames == 4720 then release()

  elseif frames >= {result_frame}
      and (http_result_written or frames >= {result_frame + 1200}) then
    screen:snapshot("product-result.png")
    local v32 = program:read_u32(V32_POINTER)
    local detector, rate0, rate1, rate2, rate3 = 0, 0, 0, 0, 0
    if v32 ~= 0 then
      detector = program:read_u8(v32 - 0x2006)
      rate0 = program:read_u16(v32 - 0x1fcf)
      rate1 = program:read_u16(v32 - 0x1fcd)
      rate2 = program:read_u16(v32 - 0x1fcb)
      rate3 = program:read_u16(v32 - 0x1fc9)
    end
    local enables = program:read_u32(0x10c00090) & 3
    local size =
      ((program:read_u32(0x10c00060) & 0x3ffc) >> 2) + 1
    print(string.format(
      "PRODUCT_DATA_MODEM_RESULT {fields} "
      .. "detector=%d rates=%04X,%04X,%04X,%04X enables=%d size=%d "
      .. "initargs=%08X,%08X,%08X,%08X,%08X,%08X,%08X "
      .. "cfg=%08X,%08X,%08X,%08X "
      .. "status=%08X,%08X,%08X,%08X "
      .. "status_caller=%08X status_target=%08X",
      {reads}, detector, rate0, rate1, rate2, rate3, enables, size,
      program:read_u32(INIT_ARGS),
      program:read_u32(INIT_ARGS + 4),
      program:read_u32(INIT_ARGS + 8),
      program:read_u32(INIT_ARGS + 12),
      program:read_u32(INIT_ARGS + 16),
      program:read_u32(INIT_ARGS + 20),
      program:read_u32(INIT_ARGS + 24),
      program:read_u32(INIT_ARGS + 28),
      program:read_u32(INIT_ARGS + 32),
      program:read_u32(INIT_ARGS + 36),
      program:read_u32(INIT_ARGS + 40),
      program:read_u32(STATUS_EVENT),
      program:read_u32(STATUS_EVENT + 4),
      0,
      0,
      program:read_u32(STATUS_EVENT + 8),
      program:read_u32(STATUS_EVENT + 12)))
    local result_file = assert(io.open(result_path, "w"))
    result_file:write("ready\\n")
    result_file:close()
    result_written = true
  end
  if not http_result_written
      and program:read_u32(PRODUCT_PPP_READ_COUNTER) >= 8 then
    local http_result = assert(io.open(http_result_path, "w"))
    http_result:write("received\\n")
    http_result:close()
    http_result_written = true
  end
  if call_ready_frame == nil
      and program:read_u32(CALL_READY_COUNTER) > 0 then
    call_ready_frame = frames
  end
  if not call_triggered and call_ready_frame ~= nil
      and frames >= call_ready_frame + CALL_SETTLE_FRAMES then
    screen:snapshot("product-dialing.png")
    local trigger_file = assert(io.open(call_trigger_path, "w"))
    trigger_file:write("dialed\\n")
    trigger_file:close()
    call_triggered = true
  end
  if result_written then
    local peer_result = io.open(peer_result_path, "r")
    if peer_result ~= nil then
      peer_result:close()
      machine:exit()
    end
  end
end)
"""
    if reload_only:
        script = re.sub(
            r"  -- Internet Center -> Downtown -> Hallway -> Desk\.\n"
            r".*?"
            r"  elseif frames == 4720 then release\(\)\n\n",
            "  -- A copied post-run state is already in Web Browser.\n"
            "  elseif frames == 1250 then press(450, 250)\n"
            "  elseif frames == 1270 then release()\n\n",
            script,
            count=1,
            flags=re.DOTALL,
        )
    return script


def answer_automation_script(
    call_trigger_path: Path = Path("call.trigger"),
    result_offset: int = 2400,
    http_application: bytes | None = None,
) -> str:
    """Keep the retained answer peer awake while running the direct-answer ROM."""
    script = direct_answer_script(
        "answer", start_frame=999999, result_offset=result_offset
    )
    echo_words = echo_responder_words()
    write_words = write_responder_words()
    echo_loop = 0xA000_0000 + ECHO_STUB + (len(echo_words) - 2) * 4
    write_loop = (
        0xA000_0000 + ECHO_WRITE_STUB + (len(write_words) - 2) * 4
    )
    echo_writes = "\n".join(
        f"  program:write_u32(ECHO_STUB + {index * 4}, 0x{word:08x})"
        for index, word in enumerate(echo_words)
    )
    write_writes = "\n".join(
        f"  program:write_u32(ECHO_WRITE_STUB + {index * 4}, 0x{word:08x})"
        for index, word in enumerate(write_words)
    )
    trigger = _lua_path(call_trigger_path)
    result_path = _lua_path(call_trigger_path.parent / "answer.result-ready")
    peer_result_path = _lua_path(call_trigger_path.parent / "product.result-ready")
    protocol_result_path = _lua_path(
        call_trigger_path.parent / "protocol.result-ready"
    )
    script = script.replace(
        "local saved_state = nil\n",
        'local saved_state = nil\n'
        f'local call_trigger_path = "{trigger}"\n'
        f'local result_path = "{result_path}"\n'
        f'local peer_result_path = "{peer_result_path}"\n'
        f'local protocol_result_path = "{protocol_result_path}"\n'
        'local ports = machine.ioport.ports\n'
        'local power_button = ports[":POWER_BUTTON"]:field(0x01)\n'
        'local touch_x = ports[":TOUCH_X"]:field(0xffff)\n'
        'local touch_y = ports[":TOUCH_Y"]:field(0xffff)\n'
        'local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)\n',
        1,
    )
    script = script.replace(
        "local function inject()\n",
        f"local echo_active, echo_round, echo_deliver_seen = false, 0, 0\n"
        f'local echo_phase, protocol_written = "read", false\n'
        f"local http_response_pending = false\n"
        f"local echo_saved_state = nil\n"
        f"local ECHO_STUB = 0x{ECHO_STUB:08x}\n"
        f"local ECHO_WRITE_STUB = 0x{ECHO_WRITE_STUB:08x}\n"
        f"local ECHO_BUFFER = 0x{ECHO_BUFFER:08x}\n"
        f"local ECHO_RESPONSE = 0x{ECHO_RESPONSE:08x}\n"
        f"local DYNAMIC_RESPONSE = 0x{DYNAMIC_RESPONSE:08x}\n"
        f"local ECHO_TOTAL = 0x{ECHO_TOTAL:08x}\n"
        f"local ECHO_DONE = 0x{ECHO_DONE:08x}\n"
        f"local ECHO_READ_TOTAL = 0x{ECHO_READ_TOTAL:08x}\n"
        f"local ECHO_RESPONSE_KIND = 0x{ECHO_RESPONSE_KIND:08x}\n"
        f"local ECHO_IP_READS = 0x{ECHO_IP_READS:08x}\n"
        f"local ECHO_WRITE_LENGTH = 0x{ECHO_WRITE_LENGTH:08x}\n"
        f"local ECHO_LOOP = 0x{echo_loop:08x}\n"
        f"local ECHO_WRITE_LOOP = 0x{write_loop:08x}\n"
        f"local ANSWER_DELIVER_COUNTER = 0x{ANSWER_DELIVER_COUNTER:08x}\n\n"
        f"{tcp_peer_lua(http_application)}\n"
        f"local function start_echo()\n"
        f"  echo_round = echo_round + 1\n"
        f"{echo_writes}\n"
        f"{write_writes}\n"
        f"  if echo_round == 1 then\n"
        f"    program:write_u32(ECHO_TOTAL, 0)\n"
        f"    program:write_u32(ECHO_IP_READS, 0)\n"
        f"  end\n"
        f"  program:write_u32(ECHO_DONE, 0)\n"
        f"  program:write_u32(ECHO_READ_TOTAL, 0)\n"
        f"  program:write_u32(ECHO_RESPONSE_KIND, 0)\n"
        f'  echo_phase = "read"\n'
        f'  echo_saved_state = {{ PC = cpu.state["PC"].value }}\n'
        f"  for _,name in ipairs(register_names) do\n"
        f"    echo_saved_state[name] = cpu.state[name].value\n"
        f"  end\n"
        f'  machine.debugger:command("resume :maincpu")\n'
        f'  cpu.state["SR"].value = cpu.state["SR"].value & 0xfffffffc\n'
        f'  cpu.state["PC"].value = 0x{0xA000_0000 + ECHO_STUB:08x}\n'
        f"  echo_active = true\n"
        f'  print(string.format("PRODUCT_ANSWER_ECHO_START round=%d", echo_round))\n'
        f"end\n\n"
        f"local function start_dynamic_write(response)\n"
        f"  for index, byte in ipairs(response) do\n"
        f"    program:write_u8(DYNAMIC_RESPONSE + index - 1, byte)\n"
        f"  end\n"
        f"  program:write_u32(ECHO_WRITE_LENGTH, #response)\n"
        f"  program:write_u32(ECHO_DONE, 0)\n"
        f'  echo_saved_state = {{ PC = cpu.state["PC"].value }}\n'
        f"  for _,name in ipairs(register_names) do\n"
        f"    echo_saved_state[name] = cpu.state[name].value\n"
        f"  end\n"
        f'  machine.debugger:command("resume :maincpu")\n'
        f'  cpu.state["SR"].value = cpu.state["SR"].value & 0xfffffffc\n'
        f'  cpu.state["PC"].value = 0x{0xA000_0000 + ECHO_WRITE_STUB:08x}\n'
        f'  echo_phase = "write"\n'
        f"end\n\n"
        "local function inject()\n",
        1,
    )
    script = script.replace(
        "local function inject()\n",
        "local function press(x, y)\n"
        "  touch_x:set_value(math.floor(x * 65535 / 479))\n"
        "  touch_y:set_value(math.floor(y * 65535 / 319))\n"
        "  touch_button:set_value(1)\n"
        "end\n"
        "local function release()\n"
        "  touch_button:set_value(0)\n"
        "end\n\n"
        "local function inject()\n",
        1,
    )
    script = script.replace(
        "  frames = frames + 1\n  local pc = cpu.state",
        "  frames = frames + 1\n"
        "  if frames == 550 then power_button:set_value(1)\n"
        "  elseif frames == 700 then power_button:set_value(0) end\n"
        "  if frames == 1250 then press(440, 10)\n"
        "  elseif frames == 1270 then release()\n"
        "  elseif frames == 1550 then press(440, 10)\n"
        "  elseif frames == 1570 then release()\n"
        "  elseif frames == 1850 then press(440, 10)\n"
        "  elseif frames == 1870 then release() end\n"
        "  if saved_state == nil then\n"
        '    local trigger_file = io.open(call_trigger_path, "r")\n'
        "    if trigger_file ~= nil then\n"
        "      trigger_file:close()\n"
        "      inject()\n"
        "    end\n"
        "  end\n"
        f"  if restored and not echo_active and echo_round < {MAX_PEER_READS}\n"
        "      and program:read_u32(ANSWER_DELIVER_COUNTER)\n"
        "        > echo_deliver_seen then\n"
        "    start_echo()\n"
        "  end\n"
        "  if echo_active then\n"
        '    local echo_pc = cpu.state["PC"].value\n'
        "    if program:read_u32(ECHO_DONE) == 1\n"
        "        or echo_pc == ECHO_LOOP or echo_pc == ECHO_LOOP + 4\n"
        "        or echo_pc == ECHO_WRITE_LOOP\n"
        "        or echo_pc == ECHO_WRITE_LOOP + 4 then\n"
        "      for _,name in ipairs(register_names) do\n"
        "        cpu.state[name].value = echo_saved_state[name]\n"
        "      end\n"
        '      cpu.state["PC"].value = echo_saved_state.PC\n'
        '      if echo_phase == "write" then\n'
        "        echo_active = false\n"
        '        print("PRODUCT_ANSWER_DYNAMIC_WRITE_RETURN")\n'
        "        if http_response_pending and not protocol_written then\n"
        "          local protocol_result = assert(io.open(\n"
        '            protocol_result_path, "w"))\n'
        '          protocol_result:write("ready\\n")\n'
        "          protocol_result:close()\n"
        "          protocol_written = true\n"
        "          http_response_pending = false\n"
        "        end\n"
        "      else\n"
        "        echo_deliver_seen = program:read_u32(ANSWER_DELIVER_COUNTER)\n"
        "        local echo_read_total = program:read_u32(ECHO_READ_TOTAL)\n"
        "        local echo_hex = {}\n"
        "        for offset = 0, echo_read_total - 1 do\n"
        "          table.insert(echo_hex,\n"
        '            string.format("%02X", program:read_u8(ECHO_BUFFER + offset)))\n'
        "        end\n"
        '        print(string.format("PRODUCT_ANSWER_PEER_DATA "\n'
        '          .. "round=%d kind=%d read=%d wrote=%d hex=%s",\n'
        "          echo_round, program:read_u32(ECHO_RESPONSE_KIND),\n"
        "          echo_read_total, program:read_u32(ECHO_TOTAL),\n"
        "          table.concat(echo_hex)))\n"
        "        local http_response, http_kind = dynamic_http_response()\n"
        "        local response = http_response\n"
        "        local response_kind = program:read_u32(ECHO_RESPONSE_KIND)\n"
        "        if response == nil and (response_kind ~= 4\n"
        "            or program:read_u32(ECHO_IP_READS) == 1) then\n"
        "          response = dynamic_peer_response(response_kind)\n"
        "        end\n"
        "        if response ~= nil then\n"
        '          if http_kind == "response" then\n'
        '            print(string.format("PRODUCT_ANSWER_HTTP_RESPONSE bytes=%d",\n'
        "              #http_response))\n"
        "            http_response_pending = true\n"
        '          elseif http_kind == "fin" then\n'
        '            print(string.format("PRODUCT_ANSWER_HTTP_FIN bytes=%d",\n'
        "              #http_response))\n"
        '          elseif http_kind == "close_ack" then\n'
        "            print(string.format(\n"
        '              "PRODUCT_ANSWER_HTTP_CLOSE_ACK bytes=%d",\n'
        "              #http_response))\n"
        "          end\n"
        "          start_dynamic_write(response)\n"
        "        else\n"
        "          echo_active = false\n"
        "        end\n"
        "      end\n"
        "    end\n"
        "  end\n"
        "  local pc = cpu.state",
        1,
    )
    script = script.replace(
        "    machine:exit()\n",
        '    local result_file = assert(io.open(result_path, "w"))\n'
        '    result_file:write("ready\\\\n")\n'
        "    result_file:close()\n",
        1,
    )
    script = script.replace(
        '    local result_file = assert(io.open(result_path, "w"))\n',
        '    print(string.format("PRODUCT_ANSWER_ECHO bytes=%d",\n'
        "      program:read_u32(ECHO_TOTAL)))\n"
        '    print(string.format("PRODUCT_ANSWER_LCP_REPLY read=%d wrote=%d",\n'
        "      program:read_u32(ECHO_READ_TOTAL),\n"
        "      program:read_u32(ECHO_TOTAL)))\n"
        "    local echo_hex = {}\n"
        "    for offset = 0, program:read_u32(ECHO_READ_TOTAL) - 1 do\n"
        "      table.insert(echo_hex,\n"
        '        string.format("%02X", program:read_u8(ECHO_BUFFER + offset)))\n'
        "    end\n"
        '    print("PRODUCT_ANSWER_ECHO_DATA hex=" .. table.concat(echo_hex))\n'
        '    local result_file = assert(io.open(result_path, "w"))\n',
        1,
    )
    ending = "end)\n"
    assert script.endswith(ending)
    script = (
        script[: -len(ending)]
        + "  if result_printed then\n"
        + '    local peer_result = io.open(peer_result_path, "r")\n'
        + "    if peer_result ~= nil then\n"
        + "      peer_result:close()\n"
        + "      machine:exit()\n"
        + "    end\n"
        + "  end\n"
        + ending
    )
    return script


def machine_config(role: str, system: str = "datarover840") -> str:
    """Use exchange-plus-bridge for the product and pure bridge for answer."""
    if role not in ("product", "answer"):
        raise ValueError(f"invalid role: {role}")
    peer = 3 if role == "product" else 2
    return f"""<?xml version="1.0"?>
<mameconfig version="10"><system name="{system}"><input>
<port tag=":PHONE_LINE" type="CONFIG" mask="1" defvalue="1" value="1" />
<port tag=":PHONE_PEER" type="CONFIG" mask="3" defvalue="1" value="{peer}" />
</input></system></mameconfig>
"""


def parse_product_result(output: bytes) -> dict[str, int | tuple[int, ...]] | None:
    match = PRODUCT_PATTERN.search(output)
    if match is None:
        return None
    values = match.groups()
    result: dict[str, int | tuple[int, ...]] = {}
    offset = 0
    for _, name in PRODUCT_SYMBOLS:
        result[name] = int(values[offset])
        offset += 1
    result["detector"] = int(values[offset])
    result["rates"] = tuple(
        int(value, 16) for value in values[offset + 1 : offset + 5]
    )
    result["enables"] = int(values[offset + 5])
    result["size"] = int(values[offset + 6])
    result["initargs"] = tuple(
        int(value, 16) for value in values[offset + 7 : offset + 14]
    )
    result["config"] = tuple(
        int(value, 16) for value in values[offset + 14 : offset + 18]
    )
    result["status"] = tuple(
        int(value, 16) for value in values[offset + 18 : offset + 22]
    )
    result["status_caller"] = int(values[offset + 22], 16)
    result["status_target"] = int(values[offset + 23], 16)
    return result


def parse_echo_result(output: bytes) -> int | None:
    """Return the number of answer-side bytes round-tripped through the ROM."""
    match = ECHO_PATTERN.search(output)
    return int(match.group(1)) if match is not None else None


def parse_peer_data(output: bytes) -> bytes | None:
    """Return the first unescaped payload from the answer's final ROM read."""
    matches = [
        framed
        for kind, framed in PEER_ROUND_PATTERN.findall(output)
        if kind == b"4"
    ]
    if not matches:
        match = PEER_DATA_PATTERN.search(output)
        if match is None:
            return None
        matches = [match.group(1)]
    for encoded in matches:
        framed = bytes.fromhex(encoded.decode())
        payload = bytearray()
        escaped = False
        for byte in framed[1:] if framed[:1] == b"\x7e" else framed:
            if byte == 0x7E:
                break
            if escaped:
                payload.append(byte ^ 0x20)
                escaped = False
            elif byte == 0x7D:
                escaped = True
            else:
                payload.append(byte)
        if payload.startswith(bytes.fromhex("ff03002145")):
            return bytes(payload)
    return None


def parse_http_request(output: bytes) -> bytes | None:
    """Return the HTTP bytes carried by the first post-handshake TCP packet."""
    for kind, encoded in PEER_ROUND_PATTERN.findall(output):
        if kind != b"4":
            continue
        framed = bytes.fromhex(encoded.decode())
        payload = bytearray()
        escaped = False
        for byte in framed[1:] if framed[:1] == b"\x7e" else framed:
            if byte == 0x7E:
                break
            if escaped:
                payload.append(byte ^ 0x20)
                escaped = False
            elif byte == 0x7D:
                escaped = True
            else:
                payload.append(byte)
        if not payload.startswith(bytes.fromhex("ff03002145")):
            continue
        ip_header = (payload[4] & 0x0F) * 4
        tcp_start = 4 + ip_header
        if len(payload) < tcp_start + 13:
            continue
        tcp_header = (payload[tcp_start + 12] >> 4) * 4
        application = bytes(payload[tcp_start + tcp_header :])
        if application.startswith(b"GET "):
            return application
    return None


def parse_http_response_result(output: bytes) -> int | None:
    """Return the escaped byte count of the generated HTTP response frame."""
    match = HTTP_RESPONSE_PATTERN.search(output)
    return int(match.group(1)) if match is not None else None


def parse_http_fin_result(output: bytes) -> int | None:
    """Return the escaped byte count of the generated TCP FIN frame."""
    match = HTTP_FIN_PATTERN.search(output)
    return int(match.group(1)) if match is not None else None


def parse_http_close_ack_result(output: bytes) -> int | None:
    """Return the escaped byte count of the final TCP close ACK."""
    match = HTTP_CLOSE_ACK_PATTERN.search(output)
    return int(match.group(1)) if match is not None else None


def verify_rendered_http(
    run_dir: Path, expected_text: str = DEFAULT_HTTP_TEXT
) -> tuple[bool, str]:
    """OCR the final Web Browser screen for the deterministic response body."""
    image = run_dir / "product" / "snapshots" / "product-result.png"
    if not image.is_file():
        return False, "product did not capture its final Web Browser screen"
    tesseract = shutil.which("tesseract")
    if tesseract is None:
        return False, "tesseract is required for Web Browser text acceptance"
    try:
        completed = subprocess.run(
            [tesseract, str(image), "stdout"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"Web Browser OCR failed: {error}"
    (run_dir / "product" / "product-result-ocr.txt").write_text(
        completed.stdout, encoding="utf-8"
    )
    text = " ".join(completed.stdout.lower().split())
    if completed.returncode or expected_text.lower() not in text:
        return False, f"Web Browser did not render the HTTP body: {text!r}"
    if "connection was unexpectedly dropped" in text:
        return False, "Web Browser reported an unexpected TCP disconnect"
    return True, "deterministic HTTP body rendered"


def validate_results(
    product: dict[str, int | tuple[int, ...]] | None,
    answer: dict[str, int | str] | None,
    forwarded: list[int],
    echoed: int | None,
    peer_data: bytes | None,
    http_response_bytes: int = 0,
) -> list[str]:
    failures: list[str] = []
    opened_ip = (
        peer_data is not None
        and peer_data.startswith(bytes.fromhex("ff03002145"))
        and peer_data[16:24] == bytes.fromhex("0a00020f0a000202")
    )
    if product is None:
        failures.append("product did not report a result")
    else:
        for name in (
            "new_dialup_link",
            "ppp_start",
            "ppp_write",
            "ppp_read",
            "lcp_frame",
            "connect_number",
            "monitor_connection",
            "open",
            "start",
            "modem_read",
            "init",
            "receive",
            "transmit",
            "install",
            "pump",
            "report_status",
            "framer_hdlc_init",
            "lapm_init",
            "lapm_start",
            "lapm_main",
            "lapm_report_connect",
            "lapm_send_sabme",
            "lapm_process_ua",
            "lapm_deliver_data",
            "data_mode",
        ):
            if not product[name]:
                failures.append(f"product missed {name}")
        if product["ppp_read"] < 4 or product["lcp_frame"] < 5:
            failures.append("product did not complete IPCP")
        if (
            (product["enables"] != 3 or product["size"] != 48)
            and not opened_ip
        ):
            failures.append("product lost its 48-word RX/TX DMA ring")
    if answer is None:
        failures.append("answer did not report a result")
    else:
        for name in (
            "open",
            "init",
            "receive",
            "transmit",
            "install",
            "pump",
            "report_status",
            "framer_hdlc_init",
            "lapm_init",
            "lapm_start",
            "lapm_main",
            "lapm_report_connect",
            "lapm_process_sabme",
            "lapm_deliver_data",
            "data_mode",
            "returned",
        ):
            if not answer[name]:
                failures.append(f"answer missed {name}")
        if answer["lapm_deliver_data"] < 4:
            failures.append("answer did not receive the first IP packet")
        if answer["detector"] != 1 and not opened_ip:
            failures.append("answer detector did not lock")
    if echoed is None:
        failures.append("answer did not report its PPP peer replies")
    elif echoed < (
        len(initial_lcp_response())
        + len(initial_ipcp_response())
        + len(final_ipcp_response())
    ):
        failures.append("answer did not complete its LCP and IPCP replies")
    if not opened_ip:
        failures.append("product did not send IP after IPCP opened")
    if product is not None and answer is not None:
        product_rates = product["rates"]
        answer_rates = answer["rates"]
        assert isinstance(product_rates, tuple)
        assert isinstance(answer_rates, tuple)
        if not http_response_bytes and tuple(
            rate & 0x0FFF for rate in product_rates[1:]
        ) != tuple(
            rate & 0x0FFF for rate in answer_rates[1:]
        ):
            failures.append("the peers negotiated different rate words")
    if min(forwarded, default=0) < MIN_PCM_BYTES:
        failures.append(f"insufficient paired PCM: {tuple(forwarded)}")
    if len(forwarded) == 2 and abs(forwarded[0] - forwarded[1]) > HALF_DMA_BYTES:
        failures.append(f"PCM clocks diverged: {tuple(forwarded)}")
    return failures


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--nvram-source", type=Path, required=True)
    parser.add_argument("--system", default="datarover840")
    parser.add_argument(
        "--reload-only",
        action="store_true",
        help="start from a copied post-run Web Browser state",
    )
    parser.add_argument(
        "--http-upstream-url",
        help=(
            "fetch one bounded host HTTP(S) response and replay it through "
            "the built-in modem"
        ),
    )
    parser.add_argument(
        "--http-expected-text",
        help="OCR text required when --http-upstream-url is used",
    )
    return parser.parse_args(argv)


def _command(
    args: argparse.Namespace,
    role_dir: Path,
    script: Path,
    port: int,
) -> list[str]:
    return [
        str(args.mame),
        args.system,
        "-rompath",
        str(args.rompath),
        "-cfg_directory",
        str(role_dir / "cfg"),
        "-nvram_directory",
        str(role_dir / "nvram"),
        "-snapshot_directory",
        str(role_dir / "snapshots"),
        "-snapview",
        "native",
        "-autoboot_script",
        str(script),
        "-autoboot_delay",
        "0",
        "-bitb",
        f"socket.127.0.0.1:{port}",
        "-debug",
        "-debugger",
        "none",
        "-video",
        "none",
        "-sound",
        "none",
        "-videodriver",
        "dummy",
        "-audiodriver",
        "dummy",
        "-nothrottle",
        "-skip_gameinfo",
    ]


def run_regression(args: argparse.Namespace) -> int:
    args.mame = args.mame.expanduser().resolve()
    args.rompath = args.rompath.expanduser().resolve()
    source = args.nvram_source.expanduser().resolve()
    if not args.mame.is_file():
        print(f"error: MAME executable not found: {args.mame}", file=sys.stderr)
        return 2
    if not args.rompath.is_dir():
        print(f"error: ROM path not found: {args.rompath}", file=sys.stderr)
        return 2
    if args.system != "datarover840":
        print("error: ROM addresses require datarover840", file=sys.stderr)
        return 2
    if not (source / args.system / "ram").is_file():
        print(f"error: NVRAM not found under {source}", file=sys.stderr)
        return 2
    if args.http_upstream_url and not args.http_expected_text:
        print(
            "error: --http-upstream-url requires --http-expected-text",
            file=sys.stderr,
        )
        return 2
    try:
        http_application = (
            fetch_http_application(args.http_upstream_url)
            if args.http_upstream_url
            else build_http_application(
                (
                    f"<html><body>{DEFAULT_HTTP_TEXT}</body></html>\r\n"
                ).encode()
            )
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    expected_http_text = args.http_expected_text or DEFAULT_HTTP_TEXT

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    run_dir.mkdir(parents=True)
    if args.http_upstream_url:
        (run_dir / "host-http-response.bin").write_bytes(http_application)
    call_trigger = run_dir / "call.trigger"
    answer_trigger = run_dir / "answer.trigger"
    relay = CallPcmExchange(
        capture_limit=500_000,
        chunk_size=HALF_DMA_BYTES,
    )
    relay.start()
    processes: list[tuple[str, subprocess.Popen[bytes]]] = []
    outputs: dict[str, bytes] = {}
    try:
        for role in ("answer", "product"):
            role_dir = run_dir / role
            (role_dir / "cfg").mkdir(parents=True)
            (role_dir / "snapshots").mkdir()
            shutil.copytree(source, role_dir / "nvram")
            (role_dir / "cfg" / f"{args.system}.cfg").write_text(
                machine_config(role, args.system), encoding="utf-8"
            )
            script = role_dir / f"{role}.lua"
            script.write_text(
                (
                    answer_automation_script(
                        answer_trigger,
                        http_application=http_application,
                    )
                    if role == "answer"
                    else product_automation_script(
                        call_trigger,
                        result_frame=6200 if args.reload_only else 7600,
                        reload_only=args.reload_only,
                    )
                ),
                encoding="utf-8",
            )
            process = subprocess.Popen(
                _command(args, role_dir, script, relay.port),
                cwd=args.mame.parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            processes.append((role, process))

        trigger_errors: list[str] = []

        def arm_call() -> None:
            while not call_trigger.is_file():
                if (run_dir / "product.result-ready").is_file():
                    trigger_errors.append(
                        "product did not arm the call after ConnectToNumber"
                    )
                    for _, process in processes:
                        if process.poll() is None:
                            process.kill()
                    return
                if any(process.poll() is not None for _, process in processes):
                    trigger_errors.append("a peer exited before dialing completed")
                    return
                threading.Event().wait(0.05)
            while max(relay.forwarded) == 0:
                threading.Event().wait(0.01)
            caller_index = relay.forwarded.index(max(relay.forwarded))
            processes_by_role = dict(processes)
            process_by_index = {
                caller_index: processes_by_role["product"],
                1 - caller_index: processes_by_role["answer"],
            }

            def control(index: int, paused: bool) -> None:
                try:
                    os.kill(
                        process_by_index[index].pid,
                        signal.SIGSTOP if paused else signal.SIGCONT,
                    )
                except ProcessLookupError:
                    pass

            relay.set_process_controller(control)
            relay.arm(caller_index)
            answer_trigger.write_text("answer\n", encoding="ascii")
            if not relay.answer_ready.wait(timeout=90):
                trigger_errors.append("answer carrier did not start within 90 seconds")
                return
            relay.release_call()

        completion_forwarded: list[int] = []

        def release_completion_hold() -> None:
            markers = (
                run_dir / "product-http.result-ready",
                run_dir / "product.result-ready",
            )
            answer_result_seen: float | None = None
            while not any(marker.is_file() for marker in markers):
                if any(process.poll() is not None for _, process in processes):
                    return
                if (run_dir / "answer.result-ready").is_file():
                    if answer_result_seen is None:
                        answer_result_seen = time.monotonic()
                    elif time.monotonic() - answer_result_seen >= 20:
                        break
                threading.Event().wait(0.05)
            completion_forwarded[:] = relay.call_forwarded
            relay.disable_process_control()

        trigger_thread = threading.Thread(target=arm_call, daemon=True)
        completion_thread = threading.Thread(
            target=release_completion_hold, daemon=True
        )
        trigger_thread.start()
        completion_thread.start()

        for role, process in processes:
            output, _ = process.communicate(timeout=600)
            outputs[role] = output
            (run_dir / role / "mame-output.txt").write_bytes(output)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: product modem run failed: {error}", file=sys.stderr)
        return 1
    finally:
        relay.disable_process_control()
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
        relay.stop()

    product = parse_product_result(outputs.get("product", b""))
    answer = parse_direct_result(outputs.get("answer", b""))
    echoed = parse_echo_result(outputs.get("answer", b""))
    peer_data = parse_peer_data(outputs.get("answer", b""))
    http_request = parse_http_request(outputs.get("answer", b""))
    http_response_bytes = parse_http_response_result(
        outputs.get("answer", b"")
    )
    http_fin_bytes = parse_http_fin_result(outputs.get("answer", b""))
    http_close_ack_bytes = parse_http_close_ack_result(
        outputs.get("answer", b"")
    )
    failures = validate_results(
        product,
        answer,
        completion_forwarded or relay.call_forwarded,
        echoed,
        peer_data,
        http_response_bytes or 0,
    )
    failures.extend(trigger_errors)
    if http_request is None or not http_request.startswith(
        b"GET / HTTP/1.0\r\nHost: 10.0.2.2:8080\r\n"
    ):
        failures.append("browser HTTP request did not reach the answer peer")
    if http_response_bytes is None or http_response_bytes <= 0:
        failures.append("answer peer did not write its HTTP response")
    if http_fin_bytes is None or http_fin_bytes <= 0:
        failures.append("answer peer did not complete orderly TCP close")
    if http_close_ack_bytes is None or http_close_ack_bytes <= 0:
        failures.append("answer peer did not acknowledge the product TCP FIN")
    if product is not None and http_close_ack_bytes and product["ppp_read"] < 8:
        failures.append("product did not receive the HTTP response")
    rendered, render_detail = verify_rendered_http(
        run_dir, expected_http_text
    )
    if not rendered:
        failures.append(render_detail)
    if relay.error is not None:
        failures.append(f"PCM relay failed: {relay.error}")
    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 1

    assert product is not None
    rates = product["rates"]
    assert isinstance(rates, tuple)
    print(
        "PASS: Web Browser selected its Internet Center PPP dial-up provider, "
        "dialed through the built-in software modem, completed paired "
        "V.32/LAPM, LCP/IPCP and TCP, sent GET / HTTP/1.0, received an "
        "HTTP/1.0 200 OK response and rendered its body "
        f"(peer-bytes={echoed}, "
        f"rates={','.join(f'{rate:04x}' for rate in rates)}, "
        f"PCM={tuple(relay.call_forwarded)})"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
