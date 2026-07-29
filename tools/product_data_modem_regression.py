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
ECHO_BUFFER = 0x0030_6800
ECHO_RESPONSE = 0x0030_6C00
ECHO_TOTAL = 0x0030_7800
ECHO_DONE = ECHO_TOTAL + 4
ECHO_READ_TOTAL = ECHO_TOTAL + 8
ECHO_RESPONSE_KIND = ECHO_TOTAL + 12
ECHO_IP_READS = ECHO_TOTAL + 16
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
CALL_SETTLE_FRAMES = 240
MAX_PEER_READS = 12
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


def tcp_syn_ack_response() -> bytes:
    """Answer the deterministic browser SYN at the negotiated peer address."""
    source_ip = bytes.fromhex("0a000202")
    destination_ip = bytes.fromhex("0a00020f")
    tcp = bytearray.fromhex(
        "1f900400010203043f999b01601210000000000002040218"
    )
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
    response_addresses = (
        ECHO_RESPONSE,
        ECHO_RESPONSE + 0x200,
        ECHO_RESPONSE + 0x400,
        ECHO_RESPONSE + 0x600,
    )
    response_lengths = tuple(
        len(response)
        for response in (
            initial_lcp_response(),
            initial_ipcp_response(),
            final_ipcp_response(),
            tcp_syn_ack_response(),
        )
    )
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
    words += [op(0x0D, rt=10, immediate=0x20)]
    branch(0x05, 9, 10, "advance_scan")
    words += [
        op(0x24, rs=11, rt=8, immediate=2),
        op(0x0D, rt=10, immediate=0x21),
    ]
    branch(0x04, 8, 10, "ip")
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
    words += [
        op(0x0D, rt=4, immediate=response_lengths[0]),
        *load_address(5, 0xA000_0000 + response_addresses[0]),
        op(0x0D, rt=9, immediate=1),
    ]
    jump("write")

    labels["ipcp"] = len(words)
    words += [
        op(0x24, rs=13, rt=8, immediate=4),
        op(0x0D, rt=10, immediate=0x20),
    ]
    branch(0x04, 8, 10, "initial_ipcp")
    words += [
        op(0x0D, rt=4, immediate=response_lengths[2]),
        *load_address(5, 0xA000_0000 + response_addresses[2]),
        op(0x0D, rt=9, immediate=3),
    ]
    jump("write")

    labels["initial_ipcp"] = len(words)
    words += [
        op(0x0D, rt=4, immediate=response_lengths[1]),
        *load_address(5, 0xA000_0000 + response_addresses[1]),
        op(0x0D, rt=9, immediate=2),
    ]
    jump("write")

    labels["ip"] = len(words)
    words += [
        op(0x0D, rt=9, immediate=4),
        *load_address(10, 0xA000_0000 + ECHO_IP_READS),
        op(0x23, rs=10, rt=8, immediate=0),
        op(0x09, rs=8, rt=8, immediate=1),
        op(0x2B, rs=10, rt=8, immediate=0),
        op(0x0D, rt=10, immediate=1),
    ]
    branch(0x05, 8, 10, "write")
    words += [
        op(0x0D, rt=4, immediate=response_lengths[3]),
        *load_address(5, 0xA000_0000 + response_addresses[3]),
    ]

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


def product_automation_script(
    call_trigger_path: Path = Path("call.trigger"),
    result_frame: int = 7600,
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
    peer_result_path = _lua_path(call_trigger_path.parent / "answer.result-ready")
    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local power_button = ports[":POWER_BUTTON"]:field(0x01)
local screen = machine.screens[":screen"]
local frames = 0
local result_written, call_triggered, call_ready_frame = false, false, nil
local COUNTERS = 0x{COUNTERS:08x}
local CALL_READY_COUNTER = 0x{CALL_READY_COUNTER:08x}
local CALL_SETTLE_FRAMES = {CALL_SETTLE_FRAMES}
local V32_POINTER = 0x{V32_POINTER:08x}
local STATUS_EVENT = 0x{STATUS_EVENT:08x}
local INIT_ARGS = 0x{INIT_ARGS:08x}
local addresses = {{ {addresses} }}
local call_trigger_path = "{trigger}"
local result_path = "{result_path}"
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

  elseif frames == {result_frame} then
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


def answer_automation_script(
    call_trigger_path: Path = Path("call.trigger"),
    result_offset: int = 2400,
) -> str:
    """Keep the retained answer peer awake while running the direct-answer ROM."""
    script = direct_answer_script(
        "answer", start_frame=999999, result_offset=result_offset
    )
    responses = (
        initial_lcp_response(),
        initial_ipcp_response(),
        final_ipcp_response(),
        tcp_syn_ack_response(),
    )
    echo_words = echo_responder_words()
    echo_loop = 0xA000_0000 + ECHO_STUB + (len(echo_words) - 2) * 4
    echo_writes = "\n".join(
        f"  program:write_u32(ECHO_STUB + {index * 4}, 0x{word:08x})"
        for index, word in enumerate(echo_words)
    )
    response_writes = "\n".join(
        f"  program:write_u8(ECHO_RESPONSE + 0x{response_index * 0x200:03x}"
        f" + {index}, 0x{byte:02x})"
        for response_index, response in enumerate(responses)
        for index, byte in enumerate(response)
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
        f"local protocol_written = false\n"
        f"local echo_saved_state = nil\n"
        f"local ECHO_STUB = 0x{ECHO_STUB:08x}\n"
        f"local ECHO_BUFFER = 0x{ECHO_BUFFER:08x}\n"
        f"local ECHO_RESPONSE = 0x{ECHO_RESPONSE:08x}\n"
        f"local ECHO_TOTAL = 0x{ECHO_TOTAL:08x}\n"
        f"local ECHO_DONE = 0x{ECHO_DONE:08x}\n"
        f"local ECHO_READ_TOTAL = 0x{ECHO_READ_TOTAL:08x}\n"
        f"local ECHO_RESPONSE_KIND = 0x{ECHO_RESPONSE_KIND:08x}\n"
        f"local ECHO_IP_READS = 0x{ECHO_IP_READS:08x}\n"
        f"local ECHO_LOOP = 0x{echo_loop:08x}\n"
        f"local ANSWER_DELIVER_COUNTER = 0x{ANSWER_DELIVER_COUNTER:08x}\n\n"
        f"local function start_echo()\n"
        f"  echo_round = echo_round + 1\n"
        f"{echo_writes}\n"
        f"{response_writes}\n"
        f"  if echo_round == 1 then\n"
        f"    program:write_u32(ECHO_TOTAL, 0)\n"
        f"    program:write_u32(ECHO_IP_READS, 0)\n"
        f"  end\n"
        f"  program:write_u32(ECHO_DONE, 0)\n"
        f"  program:write_u32(ECHO_READ_TOTAL, 0)\n"
        f"  program:write_u32(ECHO_RESPONSE_KIND, 0)\n"
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
        "        or echo_pc == ECHO_LOOP or echo_pc == ECHO_LOOP + 4 then\n"
        "      for _,name in ipairs(register_names) do\n"
        "        cpu.state[name].value = echo_saved_state[name]\n"
        "      end\n"
        '      cpu.state["PC"].value = echo_saved_state.PC\n'
        "      echo_active = false\n"
        "      echo_deliver_seen = program:read_u32(ANSWER_DELIVER_COUNTER)\n"
        "      local echo_read_total = program:read_u32(ECHO_READ_TOTAL)\n"
        "      local echo_hex = {}\n"
        "      for offset = 0, echo_read_total - 1 do\n"
        "        table.insert(echo_hex,\n"
        '          string.format("%02X", program:read_u8(ECHO_BUFFER + offset)))\n'
        "      end\n"
        '      print(string.format("PRODUCT_ANSWER_PEER_DATA "\n'
        '        .. "round=%d kind=%d read=%d wrote=%d hex=%s",\n'
        "        echo_round, program:read_u32(ECHO_RESPONSE_KIND),\n"
        "        echo_read_total, program:read_u32(ECHO_TOTAL),\n"
        "        table.concat(echo_hex)))\n"
        "      if program:read_u32(ECHO_IP_READS) >= 2\n"
        "          and not protocol_written then\n"
        "        local protocol_result = assert(io.open(\n"
        '          protocol_result_path, "w"))\n'
        '        protocol_result:write("ready\\n")\n'
        "        protocol_result:close()\n"
        "        protocol_written = true\n"
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


def validate_results(
    product: dict[str, int | tuple[int, ...]] | None,
    answer: dict[str, int | str] | None,
    forwarded: list[int],
    echoed: int | None,
    peer_data: bytes | None,
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
        if tuple(rate & 0x0FFF for rate in product_rates[1:]) != tuple(
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

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    run_dir.mkdir(parents=True)
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
                    answer_automation_script(answer_trigger)
                    if role == "answer"
                    else product_automation_script(call_trigger)
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
                run_dir / "protocol.result-ready",
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
    failures = validate_results(
        product,
        answer,
        completion_forwarded or relay.call_forwarded,
        echoed,
        peer_data,
    )
    failures.extend(trigger_errors)
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
        "V.32/LAPM and LCP/IPCP, then emitted IPv4/TCP "
        "10.0.2.15:1024 -> 10.0.2.2:8080 "
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
