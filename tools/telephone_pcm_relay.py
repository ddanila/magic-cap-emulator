#!/usr/bin/env python3
"""Pair two DataRover external-telephone PCM streams over TCP."""

from __future__ import annotations

import argparse
import select
import socket
import sys
import threading


class PcmRelay:
    """Pair two TCP clients and forward their byte streams unchanged."""

    MAX_SKEW = 2_048

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port))
        self.listener.listen(2)
        self.host = host
        self.port = self.listener.getsockname()[1]
        self.forwarded = [0, 0]
        self.error: Exception | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def wait(self, timeout: float | None = None) -> None:
        self._thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def stop(self) -> None:
        self._stop.set()
        self.listener.close()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        peers: list[socket.socket] = []
        try:
            while len(peers) < 2:
                peer, _ = self.listener.accept()
                peer.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4_096)
                peer.setblocking(False)
                peers.append(peer)
            active = [True, True]
            while not self._stop.is_set():
                readable, _, exceptional = select.select(
                    [
                        peer
                        for index, peer in enumerate(peers)
                        if active[index]
                        and (
                            not active[1 - index]
                            or min(self.forwarded) < 4_096
                            or self.forwarded[index]
                            <= self.forwarded[1 - index] + self.MAX_SKEW
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
                        data = source.recv(65_536)
                    except BlockingIOError:
                        continue
                    except ConnectionResetError:
                        active[index] = False
                        if not any(active):
                            return
                        continue
                    if not data:
                        active[index] = False
                        if not any(active):
                            return
                        continue
                    target = peers[1 - index]
                    if active[1 - index]:
                        try:
                            target.setblocking(True)
                            target.sendall(data)
                            target.setblocking(False)
                        except (BrokenPipeError, ConnectionResetError):
                            active[1 - index] = False
                    self.forwarded[index] += len(data)
        except OSError as error:
            if not self._stop.is_set():
                self.error = error
        finally:
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
