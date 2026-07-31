#!/usr/bin/env python3
"""Send a fax between two complete Magic Cap DataRover instances."""

from __future__ import annotations

import argparse
import os
import re
import select
import signal
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "fax-pair-regression"
COUNTERS = 0x0031_3000
RING_TRIGGER_BYTES = 300_000
CHUNK_SIZE = 4_096
MIN_IMAGE_CALLS = 64

# Shipping DataRover 840 ROM entry points, recovered against the SDK ELF.
SYMBOLS = (
    (0x13C5_A938, "connect_number"),
    (0x13C5_ADD4, "answer_modem"),
    (0x13C5_B7E4, "init_fax"),
    (0x13E4_26C4, "command_handler"),
    (0x13E4_2588, "line_handler"),
    (0x13E5_2230, "fax_modem_init"),
    (0x13E5_258C, "fax_modem_receive"),
    (0x13E5_25B4, "fax_modem_transmit"),
    (0x13C5_BA10, "start_receive_image"),
    (0x13C5_BBB8, "receive_image"),
    (0x13C5_BD44, "receive_hdlc"),
    (0x13C5_BE38, "send_hdlc"),
    (0x13C5_B808, "send_image"),
    (0x13C2_3B3C, "telecom_start"),
    (0x13E8_F3EC, "receive_now"),
)
DTMF_PATTERN = re.compile(rb"Telephone exchange DTMF: ([0-9A-D*#])")
RESULT_PATTERN = re.compile(
    rb"FAX_PAIR_RESULT role=(origin|answer) "
    + rb" ".join(name.encode() + rb"=(\d+)" for _, name in SYMBOLS)
    + rb" telecom_words=(\d+) telecom_enables=(\d+)"
    + rb" protocol_errors=(\d+) last_error=(\d+) last_detail=(\d+)"
    + rb" image_zero_reads=(\d+) image_failures=(\d+) image_pages=(\d+)"
    + rb" image_completions=(\d+)"
)
DIAGNOSTIC_NAMES = (
    "protocol_errors",
    "last_error",
    "last_detail",
    "image_zero_reads",
    "image_failures",
    "image_pages",
    "image_completions",
)
REQUIRED = {
    "origin": (
        "connect_number",
        "init_fax",
        "command_handler",
        "line_handler",
        "fax_modem_init",
        "fax_modem_receive",
        "fax_modem_transmit",
        "receive_hdlc",
        "send_hdlc",
        "send_image",
        "telecom_start",
    ),
    "answer": (
        "answer_modem",
        "command_handler",
        "line_handler",
        "fax_modem_init",
        "fax_modem_receive",
        "fax_modem_transmit",
        "start_receive_image",
        "receive_image",
        "receive_hdlc",
        "send_hdlc",
        "telecom_start",
        "receive_now",
    ),
}


class CallPcmExchange:
    """Keep two full-duplex PCM streams on one central-office timeline.

    Before a call is armed, every transmitted byte consumes an equal-duration
    silence byte locally. This lets the origin dial without queuing stale
    audio at the answerer. Once armed, the answerer's first output is delivered
    to the paused origin and subsequent chunks are exchanged with bounded skew.
    """

    def __init__(
        self,
        capture_limit: int = 2_000_000,
        chunk_size: int = CHUNK_SIZE,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError(f"invalid chunk size: {chunk_size}")
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(2)
        self.port = self.listener.getsockname()[1]
        self.capture_limit = capture_limit
        self.chunk_size = chunk_size
        self.forwarded = [0, 0]
        self.call_forwarded = [0, 0]
        self.started_at_peer_bytes: list[int | None] = [None, None]
        self.captured = [bytearray(), bytearray()]
        self.answer_ready = threading.Event()
        self.error: Exception | None = None
        self._caller_index: int | None = None
        self._connected = False
        self._call_released = False
        self._clock_control_enabled = False
        self._paused = [False, False]
        self._process_controller: Callable[[int, bool], None] | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def arm(self, caller_index: int) -> None:
        if caller_index not in (0, 1):
            raise ValueError(f"invalid caller index: {caller_index}")
        self._caller_index = caller_index
        if self._clock_control_enabled:
            self._set_paused(caller_index, True)

    def set_process_controller(self, controller: Callable[[int, bool], None]) -> None:
        self._process_controller = controller
        self._clock_control_enabled = True

    def disable_process_control(self) -> None:
        self._clock_control_enabled = False
        self._set_paused(0, False)
        self._set_paused(1, False)
        self._process_controller = None

    def release_call(self) -> None:
        self._call_released = True
        self._rebalance_processes()

    def stop(self) -> None:
        self._stop.set()
        self.listener.close()
        self._thread.join(timeout=5)
        self.disable_process_control()

    def _set_paused(self, index: int, paused: bool) -> None:
        if self._paused[index] == paused:
            return
        self._paused[index] = paused
        if self._process_controller is not None:
            self._process_controller(index, paused)

    def _rebalance_processes(self) -> None:
        if (
            not self._clock_control_enabled
            or not self._connected
            or not self._call_released
        ):
            return
        difference = self.call_forwarded[0] - self.call_forwarded[1]
        if difference > 0:
            self._set_paused(0, True)
            self._set_paused(1, False)
        elif difference < 0:
            self._set_paused(0, False)
            self._set_paused(1, True)
        else:
            self._set_paused(0, False)
            self._set_paused(1, False)

    @staticmethod
    def _send(peer: socket.socket, data: bytes) -> None:
        peer.setblocking(True)
        peer.sendall(data)
        peer.setblocking(False)

    def _capture(self, index: int, data: bytes) -> None:
        remaining = self.capture_limit - len(self.captured[index])
        if remaining > 0:
            self.captured[index].extend(data[:remaining])

    def _run(self) -> None:
        peers: list[socket.socket] = []
        try:
            while len(peers) < 2 and not self._stop.is_set():
                peer, _ = self.listener.accept()
                peer.setsockopt(
                    socket.SOL_SOCKET, socket.SO_RCVBUF, self.chunk_size
                )
                peer.setblocking(False)
                peers.append(peer)
            active = [True, True]
            while not self._stop.is_set() and any(active):
                candidates = []
                for index, peer in enumerate(peers):
                    if not active[index]:
                        continue
                    if self._paused[index]:
                        continue
                    if self._connected and (
                        self.call_forwarded[index]
                        > self.call_forwarded[1 - index] + self.chunk_size
                    ):
                        continue
                    candidates.append(peer)
                readable, _, exceptional = select.select(candidates, [], peers, 0.1)
                if exceptional:
                    break
                for source in readable:
                    index = peers.index(source)
                    try:
                        data = source.recv(self.chunk_size)
                    except BlockingIOError:
                        continue
                    except ConnectionResetError:
                        active[index] = False
                        continue
                    if not data:
                        active[index] = False
                        continue

                    self._capture(index, data)
                    if self.started_at_peer_bytes[index] is None:
                        self.started_at_peer_bytes[index] = self.forwarded[1 - index]
                    self.forwarded[index] += len(data)
                    caller_index = self._caller_index

                    if caller_index is None:
                        self._send(source, bytes(len(data)))
                        continue
                    if not self._connected and index == caller_index:
                        self._send(source, bytes(len(data)))
                        continue
                    if not self._connected:
                        self._connected = True
                        self.call_forwarded[index] += len(data)
                        self._send(peers[caller_index], data)
                        if self._clock_control_enabled:
                            self._set_paused(index, True)
                        self.answer_ready.set()
                        continue

                    self.call_forwarded[index] += len(data)
                    self._send(peers[1 - index], data)
                    self._rebalance_processes()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (OSError, ValueError) as error:
            if not self._stop.is_set():
                self.error = error
        finally:
            self.disable_process_control()
            for peer in peers:
                peer.close()


def automation_script(
    role: str,
    ring_trigger_path: Path,
    origin_result_frame: int = 16500,
    answer_result_offset: int = 3600,
    origin_ready: bool = False,
    recipient_first: str = "Fax",
    recipient_last: str = "Peer",
    origin_screen_raw: Path | None = None,
) -> str:
    """Drive the visible origin or answer workflow and trace its fax path."""
    if role not in ("origin", "answer"):
        raise ValueError(f"invalid fax role: {role}")
    addresses = ",\n    ".join(f"0x{address:08x}" for address, _ in SYMBOLS)
    reads = ",\n            ".join(
        f"program:read_u32(COUNTERS + {index * 4})" for index in range(len(SYMBOLS))
    )
    fields = " ".join(f"{name}=%d" for _, name in SYMBOLS)
    trigger = str(ring_trigger_path).replace("\\", "\\\\").replace('"', '\\"')
    result_path = (
        str(ring_trigger_path.parent / f"{role}.result-ready")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )
    peer_role = "answer" if role == "origin" else "origin"
    peer_result_path = (
        str(ring_trigger_path.parent / f"{peer_role}.result-ready")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )
    screen_path = (
        str(origin_screen_raw).replace("\\", "\\\\").replace('"', '\\"')
        if origin_screen_raw is not None
        else ""
    )
    load_screen_step = (
        """    elseif frames == 1400 then
      load_screen()
      snapshot("fax-source-page.png")
"""
        if origin_screen_raw is not None
        else ""
    )
    reload_screen_step = (
        """    elseif frames == 8200 then
      load_screen()
"""
        if origin_screen_raw is not None
        else ""
    )
    origin_steps = """
    -- Desk, Magic lamp, Fax, and the recipient chooser.
    if frames == 1200 then press(34, 302)
    elseif frames == 1220 then release()
__LOAD_SCREEN_STEP__
    elseif frames == 1500 then press(181, 301)
    elseif frames == 1520 then release()
    elseif frames == 1800 then press(205, 146)
    elseif frames == 1820 then release()
    elseif frames == 2200 then press(157, 157)
    elseif frames == 2220 then release()
    elseif frames == 2500 then press(345, 177)
    elseif frames == 2520 then release()

    -- Create Fax Peer with the complete 650-555-1212 number.
    elseif frames == 2850 then emu.keypost("6505551212")
    elseif frames == 3150 then press(421, 143)
    elseif frames == 3210 then release()
    elseif frames == 3300 then snapshot("fax-recipient-first-name.png")
    elseif frames == 3450 then emu.keypost("Fax")
    elseif frames == 3700 then press(370, 102)
    elseif frames == 3760 then release()
    elseif frames == 3780 then snapshot("fax-recipient-last-name.png")
    elseif frames == 3800 then emu.keypost("Peer")
    elseif frames == 4150 then press(428, 143)
    elseif frames == 4210 then release()
    elseif frames == 4300 then snapshot("fax-recipient-created.png")
    elseif frames == 4500 then press(347, 111)
    elseif frames == 4560 then release()
    elseif frames == 4800 then press(347, 242)
    elseif frames == 4860 then release()
    elseif frames == 5100 then snapshot("fax-addressed.png")
    elseif frames == 5150 then press(326, 210)
    elseif frames == 5210 then release()
    elseif frames == 5400 then press(307, 135)
    elseif frames == 5460 then release()
    elseif frames == 5700 then press(451, 264)
    elseif frames == 5760 then release()
    elseif frames == 6000 then snapshot("fax-dialing-setup.png")
    elseif frames == 6200 then press(102, 300)
    elseif frames == 6260 then release()
    elseif frames == 6500 then snapshot("fax-location-stamps.png")
    elseif frames == 6800 then press(50, 104)
    elseif frames == 6860 then release()
    elseif frames == 7200 then snapshot("fax-home-location-setup.png")
    elseif frames == 7400 then press(137, 183)
    elseif frames == 7460 then release()
    elseif frames == 7700 then press(235, 110)
    elseif frames == 7760 then release()
    elseif frames == 8100 then snapshot("fax-home-location-created.png")
__RELOAD_SCREEN_STEP__

    -- With Home now available, recreate the Fax and dial without the tutorial.
    elseif frames == 8500 then press(181, 301)
    elseif frames == 8560 then release()
    elseif frames == 8800 then press(205, 146)
    elseif frames == 8860 then release()
    elseif frames == 9200 then press(157, 157)
    elseif frames == 9260 then release()
    elseif frames == 9500 then press(345, 177)
    elseif frames == 9560 then release()
    elseif frames == 9850 then emu.keypost("5551212")
    elseif frames == 10150 then press(421, 143)
    elseif frames == 10210 then release()
    elseif frames == 10450 then emu.keypost("Fax")
    elseif frames == 10700 then press(370, 102)
    elseif frames == 10760 then release()
    elseif frames == 10800 then emu.keypost("Peer")
    elseif frames == 11150 then press(428, 143)
    elseif frames == 11210 then release()
    elseif frames == 11500 then press(347, 111)
    elseif frames == 11560 then release()
    elseif frames == 11800 then press(347, 242)
    elseif frames == 11860 then release()
    elseif frames == 12100 then snapshot("fax-addressed-retry.png")
    elseif frames == 12200 then press(326, 210)
    elseif frames == 12260 then release()
    elseif frames == 13000 then snapshot("fax-origin-active.png")
    end
"""
    origin_steps = (
        origin_steps.replace("__LOAD_SCREEN_STEP__", load_screen_step.rstrip())
        .replace("__RELOAD_SCREEN_STEP__", reload_screen_step.rstrip())
        .replace('emu.keypost("Fax")', f'emu.keypost("{recipient_first}")')
        .replace('emu.keypost("Peer")', f'emu.keypost("{recipient_last}")')
    )
    origin_ready_steps = """
    -- Diagnostic shortcut for retained state already at the addressed Fax.
    if frames == 1200 then snapshot("fax-addressed.png")
    elseif frames == 1600 then press(326, 210)
    elseif frames == 1620 then release()
    elseif frames == 2000 then snapshot("fax-origin-active.png")
    end
"""
    answer_steps = """
    if ring_start == 0 then
      local trigger_file = io.open(ring_trigger_path, "r")
      if trigger_file ~= nil then
        trigger_file:close()
        ring_start = frames
        print(string.format("FAX_PAIR_RING frame=%d", frames))
      end
    end
    if ring_start > 0 and frames == ring_start then
      ring:set_value(1)
    elseif ring_start > 0 and frames == ring_start + 120 then
      ring:set_value(0)
    elseif ring_start > 0 and frames == ring_start + 250 then
      snapshot("incoming-call.png")
    elseif ring_start > 0 and frames == ring_start + 300 then
      press(220, 156)
    elseif ring_start > 0 and frames == ring_start + 320 then
      release()
    elseif ring_start > 0 and frames == ring_start + 1500 then
      snapshot("fax-answer-active.png")
    end
"""
    if role == "origin":
        steps = origin_ready_steps if origin_ready else origin_steps
    else:
        steps = answer_steps
    finish = (
        f"frames == {origin_result_frame}"
        if role == "origin"
        else f"ring_start > 0 and frames == ring_start + {answer_result_offset}"
    )
    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local ports = machine.ioport.ports
local ring = ports[":PHONE_RING"]:field(0x01)
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local screen = machine.screens[":screen"]
local frames = 0
local ring_start = 0
local result_written = false
local ring_trigger_path = "{trigger}"
local origin_screen_path = "{screen_path}"
local result_path = "{result_path}"
local peer_result_path = "{peer_result_path}"
local COUNTERS = 0x{COUNTERS:08x}
local DIAGNOSTICS = COUNTERS + 0x100
local addresses = {{
    {addresses}
}}

local function press(x, y)
  touch_x:set_value(math.floor((x * 0xffff) / 479))
  touch_y:set_value(math.floor((y * 0xffff) / 319))
  touch_button:set_value(1)
end

local function release()
  touch_button:set_value(0)
end

local function snapshot(name)
  screen:snapshot(name)
end

local function load_screen()
  local source = assert(io.open(origin_screen_path, "rb"))
  local pixels = source:read("*a")
  source:close()
  assert(#pixels == 38400, "fax source must be a 480x320 2bpp buffer")
  local framebuffer = program:read_u32(0x10c00030) & 0xfffffff0
  for index = 1, #pixels do
    program:write_u8(framebuffer + index - 1, string.byte(pixels, index))
  end
end

for index, address in ipairs(addresses) do
  local counter = COUNTERS + (index - 1) * 4
  program:write_u32(counter, 0)
  cpu.debug:bpset(
    address, "",
    string.format(
      "do d@0x%08x=d@0x%08x+1; g", counter, counter))
end
for index = 0, 6 do
  program:write_u32(DIAGNOSTICS + index * 4, 0)
end
cpu.debug:bpset(
  0x13e8b010, "",
  string.format(
    "do d@0x%08x=d@0x%08x+1; "
    .. "do d@0x%08x=R5; do d@0x%08x=R6; g",
    DIAGNOSTICS, DIAGNOSTICS,
    DIAGNOSTICS + 4, DIAGNOSTICS + 8))
cpu.debug:bpset(
  0x13c5bd30, "R2==0",
  string.format(
    "do d@0x%08x=d@0x%08x+1; g",
    DIAGNOSTICS + 12, DIAGNOSTICS + 12))
cpu.debug:bpset(
  0x13e8bc8c, "R2==1",
  string.format(
    "do d@0x%08x=d@0x%08x+1; g",
    DIAGNOSTICS + 16, DIAGNOSTICS + 16))
cpu.debug:bpset(
  0x13e8bc8c, "R2==2",
  string.format(
    "do d@0x%08x=d@0x%08x+1; g",
    DIAGNOSTICS + 20, DIAGNOSTICS + 20))
cpu.debug:bpset(
  0x13e8bc8c, "R2==0",
  string.format(
    "do d@0x%08x=d@0x%08x+1; g",
    DIAGNOSTICS + 24, DIAGNOSTICS + 24))
cpu.debug:go()

emu.register_frame_done(function()
  frames = frames + 1
{steps.rstrip()}
  if not result_written and {finish} then
    local size = program:read_u32(0x10c00060)
    local dma = program:read_u32(0x10c00090)
    print(string.format(
      "FAX_PAIR_RESULT role={role} {fields} "
      .. "telecom_words=%d telecom_enables=%d "
      .. "protocol_errors=%d last_error=%d last_detail=%d "
      .. "image_zero_reads=%d image_failures=%d image_pages=%d "
      .. "image_completions=%d",
      {reads},
      ((size & 0x00003ffc) >> 2) + 1, dma & 3,
      program:read_u32(DIAGNOSTICS),
      program:read_u32(DIAGNOSTICS + 4),
      program:read_u32(DIAGNOSTICS + 8),
      program:read_u32(DIAGNOSTICS + 12),
      program:read_u32(DIAGNOSTICS + 16),
      program:read_u32(DIAGNOSTICS + 20),
      program:read_u32(DIAGNOSTICS + 24)))
    local result_file = assert(io.open(result_path, "w"))
    result_file:write("ready\\n")
    result_file:close()
    result_written = true
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


def deterministic_machine_config(role: str) -> str:
    """Select pure bridge for answer and exchange plus bridge for origin."""
    peer = 2 if role == "answer" else 3
    return f"""<?xml version="1.0"?>
<mameconfig version="10">
    <system name="datarover840">
        <input>
            <port tag=":PHONE_LINE" type="CONFIG"
                  mask="1" defvalue="1" value="1" />
            <port tag=":PHONE_PEER" type="CONFIG"
                  mask="3" defvalue="1" value="{peer}" />
            <port tag=":MAGICBUS_ACCESSORY" type="CONFIG"
                  mask="1" defvalue="1" value="1" />
        </input>
    </system>
</mameconfig>
"""


def parse_result(output: bytes, role: str) -> dict[str, int] | None:
    for match in RESULT_PATTERN.finditer(output):
        if match.group(1).decode() != role:
            continue
        names = (
            *(name for _, name in SYMBOLS),
            "telecom_words",
            "telecom_enables",
            *DIAGNOSTIC_NAMES,
        )
        return {
            name: int(value)
            for name, value in zip(names, match.groups()[1:], strict=True)
        }
    return None


def stored_fax_script(result_frame: int = 5700) -> str:
    """Reopen the newest In-box fax and its first rendered page."""
    return f"""local machine = manager.machine
local ports = machine.ioport.ports
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local screen = machine.screens[":screen"]
local frames = 0

local function press(x, y)
  touch_x:set_value(math.floor((x * 0xffff) / 479))
  touch_y:set_value(math.floor((y * 0xffff) / 319))
  touch_button:set_value(1)
end

local function release()
  touch_button:set_value(0)
end

emu.register_frame_done(function()
  frames = frames + 1
  if frames == 600 then screen:snapshot("01-resumed.png")
  elseif frames == 800 then press(414, 61)
  elseif frames == 820 then release()
  elseif frames == 1100 then press(34, 302)
  elseif frames == 1120 then release()
  elseif frames == 1500 then screen:snapshot("02-desk.png")
  elseif frames == 1700 then press(395, 23)
  elseif frames == 1720 then release()
  elseif frames == 1900 then press(54, 99)
  elseif frames == 1920 then release()
  elseif frames == 2300 then screen:snapshot("03-desk.png")
  elseif frames == 2500 then press(205, 91)
  elseif frames == 2520 then release()
  elseif frames == 3000 then screen:snapshot("04-inbox.png")
  elseif frames == 3200 then press(155, 57)
  elseif frames == 3220 then release()
  elseif frames == 3700 then screen:snapshot("05-fax-stationery.png")
  elseif frames == 3900 then press(92, 230)
  elseif frames == 3920 then release()
  elseif frames == 4400 then screen:snapshot("06-fax-cover-page.png")
  elseif frames == 4600 then press(248, 11)
  elseif frames == 4620 then release()
  elseif frames == 5000 then screen:snapshot("07-duplicate-name-card.png")
  elseif frames == 5200 then press(238, 263)
  elseif frames == 5220 then release()
  elseif frames == 5500 then screen:snapshot("08-fax-page.png")
  elseif frames == {result_frame} then
    print("FAX_STORED_RESULT completed=1")
    machine:exit()
  end
end)
"""


def verify_stored_fax(args: argparse.Namespace, run_dir: Path) -> tuple[bool, str]:
    """Relaunch the answer state and OCR its In-box fax stationery/page."""
    tesseract = shutil.which("tesseract")
    if tesseract is None:
        return False, "tesseract executable is required for stored-page OCR"

    verify_dir = run_dir / "stored-page-verification"
    cfg_dir = verify_dir / "cfg"
    nvram_dir = verify_dir / "nvram"
    snapshot_dir = verify_dir / "snapshots"
    cfg_dir.mkdir(parents=True)
    snapshot_dir.mkdir()
    shutil.copytree(run_dir / "answer" / "nvram", nvram_dir)
    (cfg_dir / f"{args.system}.cfg").write_text(
        deterministic_machine_config("answer"), encoding="utf-8"
    )
    script = verify_dir / "stored-fax.lua"
    script.write_text(stored_fax_script(), encoding="utf-8")
    command = [
        str(args.mame),
        args.system,
        "-rompath",
        str(args.rompath),
        "-cfg_directory",
        str(cfg_dir),
        "-nvram_directory",
        str(nvram_dir),
        "-snapshot_directory",
        str(snapshot_dir),
        "-snapview",
        "native",
        "-autoboot_script",
        str(script),
        "-autoboot_delay",
        "0",
        "-video",
        "none",
        "-sound",
        "none",
        "-videodriver",
        "dummy",
        "-audiodriver",
        "dummy",
        "-nothrottle",
        "-sleep",
        "-skip_gameinfo",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=args.mame.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"stored-page relaunch failed: {error}"
    (verify_dir / "mame-output.txt").write_bytes(completed.stdout)

    images = {
        "inbox": snapshot_dir / "04-inbox.png",
        "stationery": snapshot_dir / "05-fax-stationery.png",
        "page": snapshot_dir / "08-fax-page.png",
    }
    if completed.returncode or any(not path.is_file() for path in images.values()):
        return (
            False,
            "stored-page relaunch did not produce all three UI checkpoints",
        )

    texts: dict[str, str] = {}
    for name, image in images.items():
        try:
            ocr = subprocess.run(
                [tesseract, str(image), "stdout"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, f"Tesseract failed for {name}: {error}"
        text = " ".join(ocr.stdout.lower().split())
        texts[name] = text
        (verify_dir / f"{name}-ocr.txt").write_text(ocr.stdout, encoding="utf-8")

    rendered_invitation = all(
        token in texts["page"] for token in ("openai", "danila", "parody")
    )
    duplicate_sender_prompt = all(
        token in texts["page"]
        for token in ("fax page 2", "name card", "already have")
    )
    checks = (
        "a fax" in texts["inbox"],
        "two page fax was received" in texts["stationery"],
        "danila sukharev" in texts["stationery"],
        rendered_invitation or duplicate_sender_prompt,
    )
    if not all(checks):
        return False, f"stored-page OCR mismatch: {texts!r}"
    if rendered_invitation:
        page_result = "rendered invitation"
    else:
        page_result = "page 2 with the expected duplicate-sender prompt"
    return True, f"In-box row, two-page stationery, and {page_result} verified"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument(
        "--nvram-source",
        type=Path,
        required=True,
        help=(
            "calibrated datarover840 NVRAM directory copied for both peers; "
            "the source is never modified"
        ),
    )
    parser.add_argument(
        "--origin-nvram-source",
        type=Path,
        help=(
            "optional distinct origin NVRAM; useful with --origin-ready for "
            "retained state already at the addressed Fax window"
        ),
    )
    parser.add_argument(
        "--origin-ready",
        action="store_true",
        help="origin NVRAM already resumes at the addressed Fax window",
    )
    parser.add_argument(
        "--verify-stored-page",
        action="store_true",
        help=(
            "wait for image completion, relaunch the answer NVRAM, and use "
            "Tesseract to verify its In-box stationery and rendered page"
        ),
    )
    parser.add_argument("--recipient-first", default="Fax")
    parser.add_argument("--recipient-last", default="Peer")
    parser.add_argument(
        "--origin-screen-raw",
        type=Path,
        help="480x320 2bpp framebuffer injected before opening Fax",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="record each native LCD stream as a MAME MNG",
    )
    parser.add_argument("--system", default="datarover840")
    parser.add_argument(
        "--ring-trigger-bytes",
        type=int,
        default=RING_TRIGGER_BYTES,
        help="caller PCM bytes after which the answering machine is rung",
    )
    return parser.parse_args(argv)


def _machine_command(
    args: argparse.Namespace,
    role_dir: Path,
    script: Path,
    port: int,
) -> list[str]:
    command = [
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
        "-sleep",
        "-skip_gameinfo",
    ]
    if args.record:
        command.extend(["-mngwrite", str(role_dir / "recording.mng")])
    return command


def run_regression(args: argparse.Namespace) -> int:
    args.mame = args.mame.expanduser().resolve()
    args.rompath = args.rompath.expanduser().resolve()
    if args.origin_screen_raw is not None:
        args.origin_screen_raw = args.origin_screen_raw.expanduser().resolve()
    source = args.nvram_source.expanduser().resolve()
    origin_source = (
        args.origin_nvram_source.expanduser().resolve()
        if args.origin_nvram_source is not None
        else source
    )
    if not args.mame.is_file():
        print(f"error: MAME executable not found: {args.mame}", file=sys.stderr)
        return 2
    if not args.rompath.is_dir():
        print(f"error: ROM path not found: {args.rompath}", file=sys.stderr)
        return 2
    if args.system != "datarover840":
        print(
            "error: ROM symbol addresses are specific to datarover840",
            file=sys.stderr,
        )
        return 2
    if not (source / args.system / "ram").is_file():
        print(
            f"error: calibrated NVRAM not found under {source}",
            file=sys.stderr,
        )
        return 2
    if not (origin_source / args.system / "ram").is_file():
        print(
            f"error: origin NVRAM not found under {origin_source}",
            file=sys.stderr,
        )
        return 2
    if args.ring_trigger_bytes <= 0:
        print("error: --ring-trigger-bytes must be positive", file=sys.stderr)
        return 2
    if args.origin_screen_raw is not None and (
        not args.origin_screen_raw.is_file()
        or args.origin_screen_raw.stat().st_size != 38_400
    ):
        print(
            "error: --origin-screen-raw must be exactly 38,400 bytes",
            file=sys.stderr,
        )
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    run_dir.mkdir(parents=True)
    ring_trigger = run_dir / "ring.trigger"
    exchange = CallPcmExchange()
    exchange.start()
    processes: dict[str, subprocess.Popen[bytes]] = {}
    outputs: dict[str, bytes] = {}
    trigger_state: list[int] = []
    ready_state: list[int] = []
    trigger_error: list[str] = []
    heartbeat_stop = threading.Event()

    try:
        for role in ("answer", "origin"):
            role_dir = run_dir / role
            (role_dir / "cfg").mkdir(parents=True)
            (role_dir / "snapshots").mkdir()
            role_source = origin_source if role == "origin" else source
            shutil.copytree(role_source, role_dir / "nvram")
            (role_dir / "cfg" / f"{args.system}.cfg").write_text(
                deterministic_machine_config(role), encoding="utf-8"
            )
            script = role_dir / f"{role}.lua"
            script.write_text(
                automation_script(
                    role,
                    ring_trigger,
                    origin_result_frame=5000 if args.origin_ready else 16500,
                    answer_result_offset=(4800 if args.verify_stored_page else 3600),
                    origin_ready=args.origin_ready,
                    recipient_first=args.recipient_first,
                    recipient_last=args.recipient_last,
                    origin_screen_raw=args.origin_screen_raw,
                ),
                encoding="utf-8",
            )
            processes[role] = subprocess.Popen(
                _machine_command(args, role_dir, script, exchange.port),
                cwd=args.mame.parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

        def heartbeat() -> None:
            while not heartbeat_stop.wait(20):
                print(
                    f"fax pair still running (line bytes={tuple(exchange.forwarded)})",
                    flush=True,
                )

        def trigger_answer() -> None:
            origin = processes["origin"]
            while not heartbeat_stop.is_set():
                origin_bytes = max(exchange.forwarded)
                if origin_bytes >= args.ring_trigger_bytes:
                    origin_index = exchange.forwarded.index(origin_bytes)
                    answer_index = 1 - origin_index
                    process_by_index = {
                        origin_index: processes["origin"],
                        answer_index: processes["answer"],
                    }

                    def control_process(index: int, paused: bool) -> None:
                        process = process_by_index[index]
                        if process.poll() is None:
                            process.send_signal(
                                signal.SIGSTOP if paused else signal.SIGCONT
                            )

                    exchange.set_process_controller(control_process)
                    exchange.arm(origin_index)
                    trigger_state.append(origin_bytes)
                    try:
                        ring_trigger.write_text("ring\n", encoding="ascii")
                        if not exchange.answer_ready.wait(timeout=90):
                            trigger_error.append(
                                "answer PCM did not start within 90 seconds"
                            )
                        ready_state.append(exchange.forwarded[answer_index])
                    finally:
                        exchange.release_call()
                    return
                if origin.poll() is not None:
                    trigger_error.append("origin exited before the PCM ring threshold")
                    return
                time.sleep(0.01)

        def release_clock_control() -> None:
            result_markers = (
                run_dir / "origin.result-ready",
                run_dir / "answer.result-ready",
            )
            while not heartbeat_stop.is_set():
                if any(marker.is_file() for marker in result_markers):
                    exchange.disable_process_control()
                    return
                time.sleep(0.05)

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        trigger_thread = threading.Thread(target=trigger_answer, daemon=True)
        completion_thread = threading.Thread(target=release_clock_control, daemon=True)
        heartbeat_thread.start()
        trigger_thread.start()
        completion_thread.start()

        for role, process in processes.items():
            try:
                output, _ = process.communicate(timeout=600)
            except subprocess.TimeoutExpired:
                trigger_error.append(f"{role} timed out after 600 seconds")
                process.kill()
                output, _ = process.communicate()
            outputs[role] = output
            (run_dir / role / "mame-output.txt").write_bytes(output)

        heartbeat_stop.set()
        trigger_thread.join(timeout=2)
        completion_thread.join(timeout=2)
        heartbeat_thread.join(timeout=2)
    except OSError as error:
        print(
            f"error: paired MAME launch failed: {error}; artifacts: {run_dir}",
            file=sys.stderr,
        )
        return 2
    finally:
        heartbeat_stop.set()
        for process in processes.values():
            if process.poll() is None:
                process.kill()
                process.wait()
        exchange.stop()

    for index, pcm in enumerate(exchange.captured):
        (run_dir / f"line-peer-{index}.pcm").write_bytes(pcm)

    results = {role: parse_result(output, role) for role, output in outputs.items()}
    stored_page_ok = not args.verify_stored_page
    stored_page_detail = "not requested"
    if args.verify_stored_page:
        answer_result = results.get("answer")
        if answer_result is None or answer_result["image_completions"] == 0:
            stored_page_ok = False
            stored_page_detail = "receiver image helper never completed"
        else:
            stored_page_ok, stored_page_detail = verify_stored_fax(args, run_dir)
    missing = {
        role: (
            list(REQUIRED[role])
            if results.get(role) is None
            else [
                name
                for name in REQUIRED[role]
                if results[role] is not None and results[role][name] == 0
            ]
        )
        for role in ("origin", "answer")
    }
    digits = b"".join(DTMF_PATTERN.findall(outputs.get("origin", b""))).decode("ascii")
    dma_ok = all(
        result is not None and result["telecom_words"] == 48
        for result in results.values()
    )
    clean_protocol = all(
        result is not None
        and result["protocol_errors"] == 0
        and result["image_failures"] == 0
        for result in results.values()
    )
    protocol_ok = clean_protocol or (
        args.verify_stored_page
        and results.get("origin") is not None
        and results.get("answer") is not None
        and results["origin"]["protocol_errors"] == 0
        and results["origin"]["image_failures"] == 0
        and results["answer"]["image_completions"] > 0
        and stored_page_ok
    )
    sustained_image_ok = (
        results.get("origin") is not None
        and results.get("answer") is not None
        and results["origin"]["send_image"] >= MIN_IMAGE_CALLS
        and results["answer"]["receive_image"] >= MIN_IMAGE_CALLS
    )
    pcm_ok = all(pcm and any(pcm) for pcm in exchange.captured)
    origin_snapshots = run_dir / "origin" / "snapshots"
    answer_snapshots = run_dir / "answer" / "snapshots"
    ui_ok = all(
        first.is_file()
        and second.is_file()
        and first.read_bytes() != second.read_bytes()
        for first, second in (
            (
                origin_snapshots / "fax-addressed.png",
                origin_snapshots / "fax-origin-active.png",
            ),
            (
                answer_snapshots / "incoming-call.png",
                answer_snapshots / "fax-answer-active.png",
            ),
        )
    )
    returncodes_ok = all(process.returncode == 0 for process in processes.values())
    if (
        not returncodes_ok
        or any(missing.values())
        or digits[:7] != "5551212"
        or not dma_ok
        or not protocol_ok
        or not stored_page_ok
        or not sustained_image_ok
        or not pcm_ok
        or not ui_ok
        or not trigger_state
        or not ready_state
        or trigger_error
        or exchange.error is not None
    ):
        print(
            "FAIL: paired fax path incomplete "
            f"(results={results!r}, missing={missing!r}, "
            f"digits={digits!r}, dma_ok={dma_ok}, pcm_ok={pcm_ok}, "
            f"protocol_ok={protocol_ok}, "
            f"stored_page_ok={stored_page_ok}, "
            f"stored_page_detail={stored_page_detail!r}, "
            f"sustained_image_ok={sustained_image_ok}, ui_ok={ui_ok}, "
            f"trigger={trigger_state!r}, "
            f"ready={ready_state!r}, trigger_error={trigger_error!r}, "
            f"relay_error={exchange.error!r}); artifacts: {run_dir}",
            file=sys.stderr,
        )
        return 1

    if args.verify_stored_page:
        print(
            "PASS: paired Fax stored one received page in the In box and "
            "reopened its stationery and rendered page"
        )
    else:
        print(
            "PASS: two visible Magic Cap Fax workflows dialed 555-1212, "
            "negotiated bidirectional fax/HDLC, and sustained error-free "
            "sender and receiver image transfer"
        )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
