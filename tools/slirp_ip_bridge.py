#!/usr/bin/env python3
"""Build and drive the raw-IPv4 libslirp bridge used by the built-in modem."""

from __future__ import annotations

import argparse
import os
import queue
import shlex
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
SOURCE = Path(__file__).with_suffix(".cpp")
DEFAULT_BUILD_DIR = ASSETS_ROOT / "runtime" / "build"
MAX_PACKET = 65_535


def _tool_output(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"unable to run {command[0]}: {error}") from error
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")
    return completed.stdout.strip()


def build_helper(build_dir: Path = DEFAULT_BUILD_DIR) -> Path:
    """Compile the helper outside Git and return its executable path."""
    compiler = shutil.which("c++") or shutil.which("clang++")
    pkg_config = shutil.which("pkg-config")
    if compiler is None:
        raise RuntimeError("a C++ compiler is required for the libslirp bridge")
    if pkg_config is None:
        raise RuntimeError("pkg-config is required for the libslirp bridge")
    if not SOURCE.is_file():
        raise RuntimeError(f"libslirp bridge source is missing: {SOURCE}")

    flags = shlex.split(_tool_output([pkg_config, "--cflags", "--libs", "slirp"]))
    build_dir = build_dir.expanduser().resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    executable = build_dir / "slirp_ip_bridge"
    if (
        executable.is_file()
        and executable.stat().st_mtime_ns >= SOURCE.stat().st_mtime_ns
    ):
        return executable

    temporary = build_dir / f".slirp_ip_bridge.{os.getpid()}.partial"
    command = [
        compiler,
        "-std=c++17",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        str(SOURCE),
        "-o",
        str(temporary),
        *flags,
    ]
    try:
        _tool_output(command)
        temporary.chmod(0o755)
        os.replace(temporary, executable)
    finally:
        temporary.unlink(missing_ok=True)
    return executable


def _read_exact(stream: object, length: int) -> bytes | None:
    data = bytearray()
    read = getattr(stream, "read")
    while len(data) < length:
        chunk = read(length - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


class SlirpBridge:
    """Exchange arbitrary IPv4 packets with one rootless libslirp instance."""

    def __init__(
        self,
        executable: Path,
        *,
        allow_host_loopback: bool = False,
    ) -> None:
        command = [str(executable)]
        if allow_host_loopback:
            command.append("--allow-host-loopback")
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        if (
            self.process.stdin is None
            or self.process.stdout is None
            or self.process.stderr is None
        ):
            self.process.kill()
            raise RuntimeError("libslirp bridge pipes were not created")
        self._packets: queue.Queue[bytes | None] = queue.Queue()
        self._write_lock = threading.Lock()
        self._stderr = bytearray()
        self._reader = threading.Thread(target=self._read_packets, daemon=True)
        self._error_reader = threading.Thread(target=self._read_errors, daemon=True)
        self._reader.start()
        self._error_reader.start()

    @property
    def errors(self) -> str:
        return self._stderr.decode("utf-8", "replace").strip()

    def _read_packets(self) -> None:
        assert self.process.stdout is not None
        while True:
            header = _read_exact(self.process.stdout, 4)
            if header is None:
                break
            length = struct.unpack(">I", header)[0]
            if not 20 <= length <= MAX_PACKET:
                self._stderr.extend(f"invalid helper output length {length}\n".encode())
                break
            packet = _read_exact(self.process.stdout, length)
            if packet is None:
                self._stderr.extend(b"truncated helper output packet\n")
                break
            self._packets.put(packet)
        self._packets.put(None)

    def _read_errors(self) -> None:
        assert self.process.stderr is not None
        while True:
            chunk = self.process.stderr.read(4096)
            if not chunk:
                return
            self._stderr.extend(chunk)

    def send(self, packet: bytes) -> None:
        header_length = (packet[0] & 0x0F) * 4 if packet else 0
        declared_length = int.from_bytes(packet[2:4], "big")
        if (
            not 20 <= len(packet) <= MAX_PACKET
            or packet[0] >> 4 != 4
            or header_length < 20
            or declared_length != len(packet)
        ):
            raise ValueError("bridge input must be one complete IPv4 packet")
        if self.process.poll() is not None:
            raise RuntimeError(
                f"libslirp bridge exited with {self.process.returncode}: {self.errors}"
            )
        assert self.process.stdin is not None
        framed = struct.pack(">I", len(packet)) + packet
        with self._write_lock:
            try:
                self.process.stdin.write(framed)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                raise RuntimeError(
                    f"unable to write libslirp packet: {error}"
                ) from error

    def receive(self, timeout: float | None = None) -> bytes | None:
        try:
            packet = self._packets.get(timeout=timeout)
        except queue.Empty:
            return None
        return packet

    def close(self) -> None:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self._reader.join(timeout=1)
        self._error_reader.join(timeout=1)
        if self.process.stdout is not None:
            self.process.stdout.close()
        if self.process.stderr is not None:
            self.process.stderr.close()

    def __enter__(self) -> SlirpBridge:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


class SlirpFileBridge:
    """Relay sequential packet files between MAME Lua and libslirp."""

    def __init__(
        self,
        root: Path,
        *,
        build_dir: Path = DEFAULT_BUILD_DIR,
        allow_host_loopback: bool = False,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.guest_dir = self.root / "guest"
        self.host_dir = self.root / "host"
        self.build_dir = build_dir
        self.allow_host_loopback = allow_host_loopback
        self.guest_packets = 0
        self.host_packets = 0
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._bridge: SlirpBridge | None = None

    def start(self) -> None:
        self.guest_dir.mkdir(parents=True, exist_ok=True)
        self.host_dir.mkdir(parents=True, exist_ok=True)
        executable = build_helper(self.build_dir)
        self._bridge = SlirpBridge(
            executable,
            allow_host_loopback=self.allow_host_loopback,
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self._bridge is not None
        guest_index = 1
        host_index = 1
        try:
            while not self._stop.is_set():
                made_progress = False
                guest_path = self.guest_dir / f"guest-{guest_index:08d}.ip"
                if guest_path.is_file():
                    self._bridge.send(guest_path.read_bytes())
                    self.guest_packets += 1
                    guest_index += 1
                    made_progress = True

                packet = self._bridge.receive(timeout=0 if made_progress else 0.01)
                if packet is not None:
                    target = self.host_dir / f"host-{host_index:08d}.ip"
                    temporary = target.with_suffix(".partial")
                    temporary.write_bytes(packet)
                    os.replace(temporary, target)
                    self.host_packets += 1
                    host_index += 1
                    made_progress = True

                if self._bridge.process.poll() is not None and not made_progress:
                    raise RuntimeError(
                        f"libslirp helper exited with "
                        f"{self._bridge.process.returncode}: {self._bridge.errors}"
                    )
        except (OSError, RuntimeError, ValueError) as error:
            self.error = str(error)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._bridge is not None:
            self._bridge.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=DEFAULT_BUILD_DIR,
        help="persistent external directory for the generated helper executable",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="compile the helper and print its path",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        executable = build_helper(args.build_dir)
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(executable)
    if not args.build_only:
        print("Use this module through the built-in modem bridge launcher.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
