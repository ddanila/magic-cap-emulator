#!/usr/bin/env python3
"""Verify fresh and persistent DataRover 840F system-flash boot."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from tools import serial_regression
else:
    import serial_regression


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "flash-variant-regression"
FLASH_SIZE = 8 * 1024 * 1024
LANE_SIZE = FLASH_SIZE // 4


def reassemble_flash(lanes: list[bytes]) -> bytes:
    """Interleave four byte-wide flash chips into the 32-bit ROM bus."""
    if len(lanes) != 4:
        raise ValueError(f"expected four flash lanes, got {len(lanes)}")
    if any(len(lane) != LANE_SIZE for lane in lanes):
        raise ValueError("each flash lane must be exactly 2 MiB")

    image = bytearray(FLASH_SIZE)
    for lane_index, lane in enumerate(lanes):
        image[lane_index::4] = lane
    return bytes(image)


def verify_flash_seed(rom: bytes, lanes: list[bytes]) -> list[str]:
    """Return failures if lane interleave differs from ROM plus erased tail."""
    if len(rom) > FLASH_SIZE:
        return [f"source ROM is larger than 8 MiB: {len(rom)} bytes"]
    try:
        image = reassemble_flash(lanes)
    except ValueError as error:
        return [str(error)]

    failures = []
    if image[: len(rom)] != rom:
        mismatch = next(
            (
                offset
                for offset, (actual, expected) in enumerate(
                    zip(image, rom, strict=False)
                )
                if actual != expected
            ),
            None,
        )
        failures.append(f"interleaved flash differs from ROM at {mismatch:#x}")
    if any(value != 0xFF for value in image[len(rom) :]):
        failures.append("unused flash tail is not erased")
    return failures


def lane_digests(lanes: list[bytes]) -> list[str]:
    """Return stable hashes used to prove second-process persistence."""
    return [hashlib.sha256(lane).hexdigest() for lane in lanes]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    """Boot fresh flash, verify its lanes, then boot the saved flash again."""
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    source_path = rompath / "datarover840" / "magiccap-usa.image"
    if not source_path.is_file():
        print(f"error: source ROM not found: {source_path}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    serial_args = argparse.Namespace(
        mame=mame,
        rompath=rompath,
        workdir=run_dir,
        seconds=None,
        checkpoint="monitor",
        system="datarover840f",
    )
    lane_dir = run_dir / "nvram" / "monitor" / "datarover840f"
    lane_paths = [lane_dir / f"flash{index}" for index in range(4)]

    if serial_regression.run_regression(serial_args):
        print(f"FAIL: fresh 840F flash did not boot; artifacts: {run_dir}", file=sys.stderr)
        return 1
    if not all(path.is_file() for path in lane_paths):
        print(f"FAIL: flash lane NVRAM is incomplete; artifacts: {run_dir}", file=sys.stderr)
        return 1

    first_lanes = [path.read_bytes() for path in lane_paths]
    failures = verify_flash_seed(source_path.read_bytes(), first_lanes)
    if failures:
        print(f"FAIL: {'; '.join(failures)}; artifacts: {run_dir}", file=sys.stderr)
        return 1
    first_digests = lane_digests(first_lanes)

    if serial_regression.run_regression(serial_args):
        print(f"FAIL: persistent 840F flash did not boot; artifacts: {run_dir}", file=sys.stderr)
        return 1
    second_digests = lane_digests([path.read_bytes() for path in lane_paths])
    if second_digests != first_digests:
        print(f"FAIL: flash changed across monitor relaunch; artifacts: {run_dir}", file=sys.stderr)
        return 1

    print(
        "PASS: fresh 840F flash reconstructs the 8 MiB ROM image exactly "
        "and its four lane files boot unchanged in a second process"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
