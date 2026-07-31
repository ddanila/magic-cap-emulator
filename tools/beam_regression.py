#!/usr/bin/env python3
"""Beam between two emulated DataRovers over their IrDA SIR PTYs."""

from __future__ import annotations

import argparse
import errno
import os
import re
import selectors
import subprocess
import sys
import termios
import time
import tty
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageChops


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "beam-regression"
IRDA_PTY_PATTERN = re.compile(rb":irda PTY: (/[^\r\n]+)")
REPORT_PATTERN = re.compile(
    rb"BEAM_REPORT role=(\w+) (.*?) uartA=([0-9A-F]{8}) uartB=([0-9A-F]{8})"
)
PULSED_MODE_BIT = 0x0000_0100
SIR_BEGIN = 0xC0
SIR_END = 0xC1
SIR_ESCAPE = 0x7D

WATCHED = (
    ("beam_connected", 0x13C4A0C4),
    ("irda_connect", 0x13C4A71C),
    ("beam_connect", 0x13C4B27C),
    ("beam_discover", 0x13C49CE8),
    ("pulsed_mode", 0x13C540AC),
    ("irlap_connect", 0x13C5817C),
    ("irlap_open", 0x13C5846C),
)
SCRATCH = 0x0030_0100
NAME_KEY_INTERVAL = 100
KEY_POSITIONS = {
    "q": (26, 198),
    "w": (70, 198),
    "e": (112, 198),
    "r": (156, 198),
    "t": (199, 198),
    "y": (242, 198),
    "u": (285, 198),
    "i": (328, 198),
    "o": (370, 198),
    "p": (414, 198),
    "a": (33, 234),
    "s": (76, 234),
    "d": (119, 234),
    "f": (162, 234),
    "g": (205, 234),
    "h": (248, 234),
    "j": (291, 234),
    "k": (334, 234),
    "l": (377, 234),
    "z": (48, 269),
    "x": (89, 269),
    "c": (132, 269),
    "v": (176, 269),
    "b": (219, 269),
    "n": (262, 269),
    "m": (305, 269),
}


@dataclass
class Peer:
    role: str
    process: subprocess.Popen[bytes]
    log: bytearray
    pty_path: str | None = None
    ir_fd: int | None = None
    ir_tx: bytearray | None = None


def name_key_steps(text: str, start_frame: int) -> str:
    unsupported = sorted(set(text.lower()) - KEY_POSITIONS.keys())
    if not text or unsupported:
        detail = ", ".join(repr(item) for item in unsupported)
        raise ValueError(f"names must contain letters a-z only: {detail}")
    actions: list[tuple[int, tuple[int, int]]] = []
    if text[0].isupper():
        actions.append((0, (39, 302)))
        actions.append((1, KEY_POSITIONS[text[0].lower()]))
        actions.extend(
            (index + 1, KEY_POSITIONS[character.lower()])
            for index, character in enumerate(text[1:], start=1)
        )
    else:
        actions.extend(
            (index, KEY_POSITIONS[character.lower()])
            for index, character in enumerate(text)
        )
    return "".join(
        f"    elseif frames == {start_frame + offset * NAME_KEY_INTERVAL} "
        f"then press({position[0]}, {position[1]})\n"
        f"    elseif frames == "
        f"{start_frame + offset * NAME_KEY_INTERVAL + 20} then release()\n"
        for offset, position in actions
    ).rstrip()


def automation_script(
    role: str,
    first_name: str,
    last_name: str,
    sender: bool,
    exit_frame: int,
    debug_counters: bool = False,
    item: str = "name-card",
) -> str:
    """Drive owner setup and beam either a name card or Notebook page."""
    first_start = 4800
    first_extra = NAME_KEY_INTERVAL if first_name[0].isupper() else 0
    last_extra = NAME_KEY_INTERVAL if last_name[0].isupper() else 0
    last_start = first_start + 100 + len(first_name) * NAME_KEY_INTERVAL + first_extra
    done_frame = (
        last_start + (len(last_name) + 1) * NAME_KEY_INTERVAL + last_extra
    )
    ready_frame = done_frame + 2200
    first_steps = name_key_steps(first_name, first_start)
    last_steps = name_key_steps(last_name, last_start)
    watch_setup = (
        "\n".join(
            f"    watch(0x{SCRATCH + index * 4:08x}, 0x{address:08x})"
            for index, (_name, address) in enumerate(WATCHED)
        )
        if debug_counters
        else ""
    )
    report = (
        " .. ".join(
            f'string.format("{name}=%d ", '
            f"program:read_u32(0x{SCRATCH + index * 4:08x}))"
            for index, (name, _address) in enumerate(WATCHED)
        )
        if debug_counters
        else '""'
    )
    first_condition = (
        f"""if frames == 60 then
{watch_setup}
    elseif frames == 1220 then press(240, 160)"""
        if debug_counters
        else "if frames == 1220 then press(240, 160)"
    )
    item_x = 135 if item == "name-card" else 335
    yes_steps = "\n".join(
        f"    elseif frames == {done_frame + offset} then press(237, 100)\n"
        f"    elseif frames == {done_frame + offset + 60} then release()"
        for offset in (400, 700, 1000, 1300, 1600)
    )
    personalize_steps = f"""{yes_steps}
    elseif frames == {done_frame + 2000} then press(371, 194)
    elseif frames == {done_frame + 2060} then release()"""
    sender_steps = (
        f"""    elseif frames == 9000 then press({item_x}, 170)
    elseif frames == 9020 then release()
    elseif frames == 9200 then press(181, 301)
    elseif frames == 9220 then release()
    elseif frames == 9500 then press(265, 146)
    elseif frames == 9520 then release()
    elseif frames == 10100 then
        screen:snapshot("beam-peer-discovery.png")
    elseif frames == 10120 then press(170, 90)
    elseif frames == 10140 then release()
    elseif frames == 10220 then press(300, 217)
    elseif frames == 10240 then release()
    elseif frames == 10320 then
        screen:snapshot("beam-recipient-selected.png")
    elseif frames == 10500 then press(369, 190)
    elseif frames == 10520 then release()
    elseif frames == 10700 then press(369, 190)
    elseif frames == 10720 then release()
    elseif frames == 11600 then
        screen:snapshot("beam-transfer-result.png")
"""
        if sender
        else """    elseif frames == 10100 then
        screen:snapshot("beam-peer-listener.png")
    elseif frames == 11600 then
        screen:snapshot("beam-received-result.png")
"""
    )
    return f"""local machine = manager.machine
local ports = machine.ioport.ports
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local screen = machine.screens[":screen"]
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local irda_carrier = ports[":IRDA_CARRIER"]:field(0x01)
local frames = 0

local function press(x, y)
    touch_x:set_value(math.floor((x * 0xffff) / 479))
    touch_y:set_value(math.floor((y * 0xffff) / 319))
    touch_button:set_value(1)
end

local function release()
    touch_button:set_value(0)
end

local function watch(slot, address)
    program:write_u32(slot, 0)
    cpu.debug:bpset(address, "1",
        string.format("do d@0x%08x=d@0x%08x+1; g", slot, slot))
end

emu.register_frame_done(function()
    frames = frames + 1
    {first_condition}
    elseif frames == 1240 then release()
    elseif frames == 1420 then press(23, 23)
    elseif frames == 1440 then release()
    elseif frames == 1620 then press(456, 296)
    elseif frames == 1640 then release()
    elseif frames == 1820 then press(240, 160)
    elseif frames == 1840 then release()
    elseif frames == 2220 then press(395, 24)
    elseif frames == 2240 then release()
    elseif frames == 2420 then press(181, 301)
    elseif frames == 2440 then release()
    elseif frames == 2620 then press(265, 146)
    elseif frames == 2640 then release()
    elseif frames == 2920 then press(303, 140)
    elseif frames == 2940 then release()
    elseif frames == 3500 then press(135, 170)
    elseif frames == 3520 then release()
    elseif frames == 3800 then press(371, 236)
    elseif frames == 3820 then release()
    elseif frames == 4100 then press(451, 52)
    elseif frames == 4120 then release()
    elseif frames == 4400 then press(376, 301)
    elseif frames == 4420 then release()
{first_steps}
    elseif frames == {last_start - 100} then press(370, 102)
    elseif frames == {last_start - 80} then release()
{last_steps}
    elseif frames == {done_frame} then press(428, 144)
    elseif frames == {done_frame + 20} then release()
{personalize_steps}
    elseif frames == {ready_frame} then
        screen:snapshot("owner-setup-complete.png")
    elseif frames == {ready_frame + 50} then irda_carrier:set_value(1)
    elseif frames == {ready_frame + 70} then irda_carrier:set_value(0)
{sender_steps.rstrip()}
    elseif frames == 8000 then irda_carrier:set_value(1)
    elseif frames == 8020 then irda_carrier:set_value(0)
    elseif frames == 8150 then irda_carrier:set_value(1)
    elseif frames == 8170 then irda_carrier:set_value(0)
    elseif frames == 8300 then irda_carrier:set_value(1)
    elseif frames == 8320 then irda_carrier:set_value(0)
    elseif frames == {exit_frame - 20} then
        print(string.format(
            "BEAM_REPORT role={role} %s uartA=%08X uartB=%08X",
            {report}, program:read_u32(0x10c000b0),
            program:read_u32(0x10c000c8)))
    elseif frames == {exit_frame} then
        machine:exit()
    end
end)
"""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--frames", type=int, default=11800)
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="prepare and retain personalized owner NVRAM without a Beam transfer",
    )
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--sender-first", default="alice")
    parser.add_argument("--sender-last", default="sender")
    parser.add_argument("--receiver-first", default="bob")
    parser.add_argument("--receiver-last", default="receiver")
    parser.add_argument(
        "--record",
        action="store_true",
        help="record each native LCD stream as a MAME MNG",
    )
    parser.add_argument(
        "--item",
        choices=("name-card", "notebook"),
        default="name-card",
        help="displayed object to beam (default: name-card)",
    )
    parser.add_argument(
        "--debug-counters",
        action="store_true",
        help="enable slower ROM entry-point breakpoints on both peers",
    )
    return parser.parse_args(argv)


def _command(
    mame: Path,
    rompath: Path,
    run_dir: Path,
    lua_path: Path,
    debug_counters: bool,
    record: bool = False,
) -> list[str]:
    command = [
        str(mame),
        "datarover840",
        "-rompath",
        str(rompath),
        "-cfg_directory",
        str(run_dir / "cfg"),
        "-nvram_directory",
        str(run_dir / "nvram"),
        "-snapshot_directory",
        str(run_dir / "snapshots"),
        "-snapview",
        "native",
        "-autoboot_delay",
        "0",
        "-autoboot_script",
        str(lua_path),
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
    if debug_counters:
        command.extend(["-debug", "-debugger", "none"])
    if record:
        command.extend(["-mngwrite", str(run_dir / "recording.mng")])
    return command


def _open_irda(path: str) -> int:
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    tty.setraw(fd)
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd


def _read_irda(fd: int) -> bytes | None:
    """Read a PTY, treating Linux EIO after slave closure as EOF."""
    try:
        return os.read(fd, 65_536)
    except BlockingIOError:
        return b""
    except OSError as error:
        if error.errno == errno.EIO:
            return None
        raise


def _write_all(fd: int, data: bytes) -> bool:
    """Write a complete chunk, returning false if the PTY slave closed."""
    view = memoryview(data)
    while view:
        try:
            count = os.write(fd, view)
        except BlockingIOError:
            select = selectors.DefaultSelector()
            select.register(fd, selectors.EVENT_WRITE)
            select.select(1)
            select.close()
            continue
        except OSError as error:
            if error.errno == errno.EIO:
                return False
            raise
        view = view[count:]
    return True


def _close_irda(selector: selectors.BaseSelector, peer: Peer) -> None:
    if peer.ir_fd is None:
        return
    selector.unregister(peer.ir_fd)
    os.close(peer.ir_fd)
    peer.ir_fd = None


def _parse_report(output: bytes) -> tuple[dict[str, int], int, int] | None:
    match = REPORT_PATTERN.search(output)
    if not match:
        return None
    counts = {
        key: int(value)
        for key, value in re.findall(r"(\w+)=(\d+)", match.group(2).decode())
    }
    return counts, int(match.group(3), 16), int(match.group(4), 16)


def decode_sir_frames(data: bytes) -> list[bytes]:
    """Return unescaped IrDA SIR frames, excluding delimiters and preambles."""
    frames: list[bytes] = []
    frame: bytearray | None = None
    escaped = False
    for byte in data:
        if frame is None:
            if byte == SIR_BEGIN:
                frame = bytearray()
            continue
        if escaped:
            frame.append(byte ^ 0x20)
            escaped = False
        elif byte == SIR_ESCAPE:
            escaped = True
        elif byte == SIR_END:
            frames.append(bytes(frame))
            frame = None
        elif byte == SIR_BEGIN:
            frame = bytearray()
        else:
            frame.append(byte)
    return frames


def item_payload_present(
    item: str, data: bytes, sender_name: str = "alice Sender"
) -> bool:
    """Distinguish the two serialized item bodies in the sender stream."""
    if item == "notebook":
        return b"Note Card" in data
    return (
        data.lower().count(sender_name.lower().encode()) >= 3
        and b"Note Card" not in data
    )


def image_region_changed(first: Path, second: Path, box: tuple[int, ...]) -> bool:
    """Return whether the selected RGB screenshot region changed."""
    with Image.open(first) as first_image, Image.open(second) as second_image:
        first_crop = first_image.convert("RGB").crop(box)
        second_crop = second_image.convert("RGB").crop(box)
        return ImageChops.difference(first_crop, second_crop).getbbox() is not None


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    workdir = args.workdir.expanduser().resolve()
    if not mame.is_file() or not rompath.is_dir():
        print("error: MAME executable or ROM path is missing", file=sys.stderr)
        return 2
    minimum_frames = 8500 if args.setup_only else 9020
    if args.frames < minimum_frames:
        print(
            f"error: --frames must be at least {minimum_frames}", file=sys.stderr
        )
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    root = workdir / f"{stamp}-{os.getpid()}"
    root.mkdir(parents=True)
    peers: list[Peer] = []
    specs = (
        ("sender", args.sender_first, args.sender_last, True),
        ("receiver", args.receiver_first, args.receiver_last, False),
    )
    selector = selectors.DefaultSelector()

    try:
        for role, first_name, last_name, sender in specs:
            run_dir = root / role
            for child in ("cfg", "nvram", "snapshots"):
                (run_dir / child).mkdir(parents=True)
            lua_path = run_dir / "beam.lua"
            lua_path.write_text(
                automation_script(
                    role,
                    first_name,
                    last_name,
                    sender,
                    args.frames,
                    args.debug_counters,
                    args.item,
                ),
                encoding="utf-8",
            )
            process = subprocess.Popen(
                _command(
                    mame,
                    rompath,
                    run_dir,
                    lua_path,
                    args.debug_counters,
                    args.record,
                ),
                cwd=mame.parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            if process.stdout is None:
                raise RuntimeError(f"{role}: MAME stdout pipe was not created")
            peer = Peer(role, process, bytearray(), ir_tx=bytearray())
            peers.append(peer)
            selector.register(process.stdout, selectors.EVENT_READ, ("log", peer))

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            if all(peer.process.poll() is not None for peer in peers):
                break
            for key, _events in selector.select(0.1):
                kind, peer = key.data
                if kind == "log":
                    chunk = os.read(key.fileobj.fileno(), 65_536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    peer.log.extend(chunk)
                    if peer.pty_path is None:
                        match = IRDA_PTY_PATTERN.search(peer.log)
                        if match:
                            peer.pty_path = match.group(1).decode()
                            peer.ir_fd = _open_irda(peer.pty_path)
                            selector.register(
                                peer.ir_fd, selectors.EVENT_READ, ("irda", peer)
                            )
                else:
                    if peer.ir_fd is None:
                        continue
                    chunk = _read_irda(peer.ir_fd)
                    if chunk is None:
                        _close_irda(selector, peer)
                        continue
                    if not chunk:
                        continue
                    assert peer.ir_tx is not None
                    peer.ir_tx.extend(chunk)
                    other = peers[1] if peer is peers[0] else peers[0]
                    if other.ir_fd is not None:
                        if not _write_all(other.ir_fd, chunk):
                            _close_irda(selector, other)
        else:
            raise TimeoutError("paired MAME run timed out")

        for peer in peers:
            if peer.process.poll() is None:
                peer.process.wait(timeout=10)
            if peer.process.stdout is not None:
                peer.log.extend(peer.process.stdout.read() or b"")
            run_dir = root / peer.role
            (run_dir / "mame-output.txt").write_bytes(peer.log)
            (run_dir / "irda-transmit.bin").write_bytes(peer.ir_tx or b"")

        failures: list[str] = []
        for peer in peers:
            report = _parse_report(peer.log)
            if peer.process.returncode:
                failures.append(
                    f"{peer.role} exited with status {peer.process.returncode}"
                )
            if peer.pty_path is None:
                failures.append(f"{peer.role} did not expose an IrDA PTY")
            if report is None:
                failures.append(f"{peer.role} did not report Beam counters")
                continue
            counts, uart_a, uart_b = report
            print(
                f"{peer.role}: {len(peer.ir_tx or b'')} transmitted bytes, "
                f"uartA={uart_a:#010x}, uartB={uart_b:#010x}, "
                + ", ".join(f"{key}={value}" for key, value in counts.items())
            )
            if args.debug_counters and not counts.get("irlap_open"):
                failures.append(f"{peer.role} never opened IrLAP")
            if not ((uart_a | uart_b) & PULSED_MODE_BIT):
                failures.append(f"{peer.role} never selected a pulsed UART")

        if args.setup_only:
            expected = tuple(
                root / role / "snapshots" / "owner-setup-complete.png"
                for role in ("sender", "receiver")
            )
            if not all(path.is_file() for path in expected):
                failures.append("owner setup snapshots are incomplete")
            if failures:
                print("FAIL: " + "; ".join(failures), file=sys.stderr)
                print(f"Artifacts: {root}")
                return 1
            print("PASS: personalized owner NVRAM prepared for both devices")
            print(f"Artifacts: {root}")
            return 0

        if not all(peer.ir_tx for peer in peers):
            failures.append("IrDA traffic did not flow in both directions")
        else:
            sender_data = bytes(peers[0].ir_tx or b"")
            receiver_data = bytes(peers[1].ir_tx or b"")
            sender_frames = decode_sir_frames(sender_data)
            receiver_frames = decode_sir_frames(receiver_data)
            if len(sender_frames) < 10 or len(receiver_frames) < 2:
                failures.append("IrDA streams did not contain complete SIR frames")
            sender_name = f"{args.sender_first} {args.sender_last}"
            receiver_name = f"{args.receiver_first} {args.receiver_last}"
            if receiver_name.lower().encode() not in receiver_data.lower():
                failures.append("sender did not discover the receiver by name")
            if sender_name.lower().encode() not in sender_data.lower():
                failures.append("receiver did not identify the sender")
            if (
                f"dear {args.receiver_first.lower()},".encode()
                not in sender_data.lower()
                or b"The following item was received via beam:" not in sender_data
            ):
                failures.append("sender did not transmit the Beam envelope")
            if not item_payload_present(args.item, sender_data, sender_name):
                failures.append(f"sender did not transmit the {args.item} item body")
            expected_snapshots = (
                root / "sender" / "snapshots" / "beam-peer-discovery.png",
                root / "sender" / "snapshots" / "beam-recipient-selected.png",
                root / "sender" / "snapshots" / "beam-transfer-result.png",
                root / "receiver" / "snapshots" / "beam-received-result.png",
            )
            if not all(path.is_file() for path in expected_snapshots):
                failures.append("Beam workflow snapshots are incomplete")
            else:
                receiver_before = (
                    root / "receiver" / "snapshots" / "owner-setup-complete.png"
                )
                receiver_after = expected_snapshots[-1]
                if not receiver_before.is_file() or not image_region_changed(
                    receiver_before, receiver_after, (176, 52, 236, 110)
                ):
                    failures.append("receiver Inbox count did not advance")
        if failures:
            print("FAIL: " + "; ".join(failures), file=sys.stderr)
            print(f"Artifacts: {root}")
            return 1
        print(
            "PASS: paired IrDA discovery selected the receiver and transferred "
            + (
                "the sender's Notebook page"
                if args.item == "notebook"
                else "the sender's name card"
            )
        )
        print(f"Artifacts: {root}")
        return 0
    except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as error:
        print(f"error: {error}; artifacts: {root}", file=sys.stderr)
        return 2
    finally:
        selector.close()
        for peer in peers:
            if peer.ir_fd is not None:
                os.close(peer.ir_fd)
            if peer.process.poll() is None:
                peer.process.terminate()
                try:
                    peer.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    peer.process.kill()
                    peer.process.wait()


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
