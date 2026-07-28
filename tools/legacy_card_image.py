#!/usr/bin/env python3
"""Convert a Magic Cap 1.x Simulator card into a MAME linear-card image.

The classic Macintosh simulator writes a GMCD/MCAP card file containing a
12-byte Macintosh header, a compact PC Card CIS, and the card's common-memory
contents.  When its File > Don't Save Changes option is disabled, an ejected
card may also produce a second file named after the card.  That file contains
the newer common-memory contents without the 0x70-byte file header.

This tool preserves both inputs, merges the optional changes file after the
Simulator header, and pads the result to MAME's 8 MiB linear-card size.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


CARD_SIZE = 8 * 1024 * 1024
SIMULATOR_HEADER_SIZE = 0x70
SIMULATOR_CIS_OFFSET = 0x0C
MAGIC_TUPLE = b"\xa0\x20GMMC"
SUPPORTED_VERSION = 0x0001_0001
SUPPORTED_TYPE = b"RAMC"


class LegacyCardError(ValueError):
    """The input is not a supported Magic Cap 1.x Simulator card."""


def validate_simulator_wrapper(wrapper: bytes) -> int:
    """Validate a 1.x wrapper and return its metacluster offset."""
    if len(wrapper) <= SIMULATOR_HEADER_SIZE:
        raise LegacyCardError("Simulator card wrapper is truncated")
    tuple_offset = wrapper.find(
        MAGIC_TUPLE,
        SIMULATOR_CIS_OFFSET,
        SIMULATOR_HEADER_SIZE,
    )
    if tuple_offset < 0:
        raise LegacyCardError("CISTPL_GM tuple for a Magic Cap card is absent")

    payload = tuple_offset + 2
    if payload + 16 > len(wrapper):
        raise LegacyCardError("CISTPL_GM tuple is truncated")
    version = int.from_bytes(wrapper[payload + 4 : payload + 8], "big")
    card_type = wrapper[payload + 8 : payload + 12]
    metacluster_offset = int.from_bytes(
        wrapper[payload + 12 : payload + 16],
        "big",
    )
    if version != SUPPORTED_VERSION:
        raise LegacyCardError(
            f"unsupported Magic Cap card version 0x{version:08x}"
        )
    if card_type != SUPPORTED_TYPE:
        raise LegacyCardError(
            f"expected RAMC card type, found {card_type!r}"
        )
    if not (0 < metacluster_offset < CARD_SIZE):
        raise LegacyCardError(
            f"invalid metacluster offset 0x{metacluster_offset:x}"
        )
    return metacluster_offset


def build_mame_image(wrapper: bytes, changes: bytes | None = None) -> bytes:
    """Merge a Simulator card and pad it to an 8 MiB MAME image."""
    validate_simulator_wrapper(wrapper)
    merged = wrapper
    if changes is not None:
        expected = len(wrapper) - SIMULATOR_HEADER_SIZE
        if len(changes) != expected:
            raise LegacyCardError(
                "changes image has "
                f"{len(changes)} bytes, expected {expected}"
            )
        merged = wrapper[:SIMULATOR_HEADER_SIZE] + changes

    if len(merged) > CARD_SIZE:
        raise LegacyCardError(
            f"merged card is {len(merged)} bytes, larger than {CARD_SIZE}"
        )
    return merged + (b"\xff" * (CARD_SIZE - len(merged)))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wrapper",
        required=True,
        type=Path,
        help="GMCD/MCAP Simulator card containing its CIS prefix",
    )
    parser.add_argument(
        "--changes",
        type=Path,
        help="optional newer common-memory file written on eject",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="new 8 MiB MAME linear-card image",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        wrapper = args.wrapper.expanduser().read_bytes()
        changes = (
            args.changes.expanduser().read_bytes()
            if args.changes is not None
            else None
        )
        image = build_mame_image(wrapper, changes)
    except (OSError, LegacyCardError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    output = args.output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(image)
    print(f"Wrote {output} ({len(image)} bytes)")
    print(f"SHA-256 {hashlib.sha256(image).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
