#!/usr/bin/env python3
"""Pair two DataRover external-telephone PCM streams over TCP."""

from __future__ import annotations

import argparse
import select
import socket
import sys
import threading
import time
from collections.abc import Callable


class PcmRelay:
    """Pair two TCP clients and forward their byte streams unchanged."""

    STARTUP_GRACE = 4_096
    MAX_SKEW = 2_048

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        startup_grace: int | None = None,
        max_skew: int | None = None,
        capture_limit: int = 0,
        process_clock_stale_after: float | None = None,
        read_size: int = 65_536,
    ) -> None:
        if read_size <= 0:
            raise ValueError(f"invalid read size: {read_size}")
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port))
        self.listener.listen(2)
        self.host = host
        self.port = self.listener.getsockname()[1]
        self.startup_grace = (
            self.STARTUP_GRACE if startup_grace is None else startup_grace
        )
        self.max_skew = self.MAX_SKEW if max_skew is None else max_skew
        self.capture_limit = capture_limit
        self.process_clock_stale_after = process_clock_stale_after
        self.read_size = read_size
        self.captured = [bytearray(), bytearray()]
        self.forwarded = [0, 0]
        self.started_at_peer_bytes: list[int | None] = [None, None]
        self.peer_count = 0
        self.error: Exception | None = None
        self._peer_condition = threading.Condition()
        self._paused = [False, False]
        self._active = [False, False]
        self._process_controller: Callable[[int, bool], None] | None = None
        self._process_control_started_at: float | None = None
        self._last_forward_at: list[float | None] = [None, None]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def wait(self, timeout: float | None = None) -> None:
        self._thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def wait_for_peer_count(self, count: int, timeout: float | None = None) -> bool:
        if count not in (1, 2):
            raise ValueError(f"invalid peer count: {count}")
        with self._peer_condition:
            return self._peer_condition.wait_for(
                lambda: self.peer_count >= count, timeout=timeout
            )

    def set_process_controller(
        self, controller: Callable[[int, bool], None]
    ) -> None:
        """Keep connected producers on the same byte clock.

        ``controller`` receives the accepted peer index and whether its
        producer should be paused. Callers can map that to SIGSTOP/SIGCONT for
        emulator processes after using ``wait_for_peer_count`` to make the
        connection order deterministic.
        """
        self._process_controller = controller
        self._process_control_started_at = time.monotonic()
        self._rebalance_processes()

    def disable_process_control(self) -> None:
        controller = self._process_controller
        self._process_controller = None
        self._process_control_started_at = None
        if controller is not None:
            for index, paused in enumerate(self._paused):
                if paused:
                    controller(index, False)
        self._paused = [False, False]

    def mark_peer_inactive(self, index: int) -> None:
        """Release clock holds after the corresponding producer has exited."""
        if index not in (0, 1):
            raise ValueError(f"invalid peer index: {index}")
        self._active[index] = False
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
        if self._process_controller is None or self.peer_count < 2:
            return
        if not all(self._active):
            self._set_paused(0, False)
            self._set_paused(1, False)
            return
        difference = self.forwarded[0] - self.forwarded[1]
        if difference and self.process_clock_stale_after is not None:
            lagging = 1 if difference > 0 else 0
            last_lag_progress = self._last_forward_at[lagging]
            freshness_origin = (
                last_lag_progress
                if last_lag_progress is not None
                else self._process_control_started_at
            )
            if (
                freshness_origin is not None
                and time.monotonic() - freshness_origin
                >= self.process_clock_stale_after
            ):
                self._set_paused(0, False)
                self._set_paused(1, False)
                return
        if difference > 0:
            self._set_paused(0, True)
            self._set_paused(1, False)
        elif difference < 0:
            self._set_paused(0, False)
            self._set_paused(1, True)
        else:
            self._set_paused(0, False)
            self._set_paused(1, False)

    def _run(self) -> None:
        peers: list[socket.socket] = []
        try:
            while len(peers) < 2:
                peer, _ = self.listener.accept()
                peer.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4_096)
                peer.setblocking(False)
                peers.append(peer)
                with self._peer_condition:
                    self.peer_count = len(peers)
                    self._peer_condition.notify_all()
            self._active = [True, True]
            while not self._stop.is_set():
                self._rebalance_processes()
                readable, _, exceptional = select.select(
                    [
                        peer
                        for index, peer in enumerate(peers)
                        if self._active[index]
                        and not self._paused[index]
                        and (
                            not self._active[1 - index]
                            or min(self.forwarded) < self.startup_grace
                            or self.forwarded[index]
                            <= self.forwarded[1 - index] + self.max_skew
                        )
                    ],
                    [],
                    peers,
                    0.1,
                )
                if exceptional:
                    break
                for source in readable:
                    index = peers.index(source)
                    try:
                        data = source.recv(self.read_size)
                    except BlockingIOError:
                        continue
                    except ConnectionResetError:
                        self.mark_peer_inactive(index)
                        if not any(self._active):
                            return
                        continue
                    if not data:
                        self.mark_peer_inactive(index)
                        if not any(self._active):
                            return
                        continue
                    if self.started_at_peer_bytes[index] is None:
                        self.started_at_peer_bytes[index] = self.forwarded[
                            1 - index
                        ]
                    if len(self.captured[index]) < self.capture_limit:
                        remaining = self.capture_limit - len(self.captured[index])
                        self.captured[index].extend(data[:remaining])
                    target = peers[1 - index]
                    if self._active[1 - index]:
                        try:
                            target.setblocking(True)
                            target.sendall(data)
                            target.setblocking(False)
                        except (BrokenPipeError, ConnectionResetError):
                            self.mark_peer_inactive(1 - index)
                    self.forwarded[index] += len(data)
                    self._last_forward_at[index] = time.monotonic()
                    self._rebalance_processes()
        except OSError as error:
            if not self._stop.is_set():
                self.error = error
        finally:
            self.disable_process_control()
            for peer in peers:
                peer.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7200)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        relay = PcmRelay(args.host, args.port)
    except OSError as error:
        print(f"error: unable to listen: {error}", file=sys.stderr)
        return 2

    print(
        f"PHONE_PCM_RELAY listening={relay.host}:{relay.port}",
        flush=True,
    )
    relay.start()
    try:
        while relay.is_alive():
            relay.wait(timeout=1)
    except KeyboardInterrupt:
        relay.stop()
    if relay.error:
        print(f"error: relay failed: {relay.error}", file=sys.stderr)
        return 1
    print(
        f"PHONE_PCM_RELAY_RESULT peer1={relay.forwarded[0]} "
        f"peer2={relay.forwarded[1]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
