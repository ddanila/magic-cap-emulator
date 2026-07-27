#!/usr/bin/env python3
"""Correct HTTPS Rule dispatch in Cameron Kaiser's MIPS Web Browser package."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from typing import Sequence


SOURCE_SHA256 = (
    "a72d591b270f66a7b9f8a4df67b39aa52ed39af22932256413d47f7bdcb5ea71"
)
PATCHED_SHA256 = (
    "bdc6960304ead7948712f26298960b7320bf8b29d60b7e6137bfddd7632fce1e"
)
SOURCE_SIZE = 461_876
SOURCE_LITERAL_OFFSET = 0x63E86
BROKEN_LITERAL = b"https:\x00\x00proxy"
FIXED_LITERAL = b"https\x00\x00\x00proxy"


class PatchError(ValueError):
    """The input is not the supported browser package."""


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def patch_payload(
    payload: bytes,
    *,
    expected_sha256: str = SOURCE_SHA256,
    expected_size: int = SOURCE_SIZE,
    expected_offset: int | None = SOURCE_LITERAL_OFFSET,
) -> bytes:
    """Return the package with the erroneous scheme literal corrected."""
    if len(payload) != expected_size:
        raise PatchError(
            f"input size is {len(payload):,} bytes; expected "
            f"{expected_size:,} bytes"
        )

    digest = sha256(payload)
    if digest != expected_sha256:
        raise PatchError(
            f"input SHA-256 is {digest}; expected {expected_sha256}"
        )

    occurrences = payload.count(BROKEN_LITERAL)
    if occurrences != 1:
        raise PatchError(
            "expected exactly one HTTPS dispatch literal; "
            f"found {occurrences}"
        )
    offset = payload.index(BROKEN_LITERAL)
    if expected_offset is not None and offset != expected_offset:
        raise PatchError(
            f"HTTPS dispatch literal is at {offset:#x}; "
            f"expected {expected_offset:#x}"
        )

    corrected = (
        payload[:offset]
        + FIXED_LITERAL
        + payload[offset + len(BROKEN_LITERAL) :]
    )
    if expected_sha256 == SOURCE_SHA256 and sha256(corrected) != PATCHED_SHA256:
        raise PatchError("corrected package does not match its expected SHA-256")
    return corrected


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="downloaded original package")
    parser.add_argument("output", type=Path, help="path for the corrected package")
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing output file",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    source = args.source.expanduser()
    output = args.output.expanduser()

    if source.resolve() == output.resolve():
        print("error: source and output paths must differ", file=sys.stderr)
        return 2
    if output.exists() and not args.force:
        print(
            f"error: output already exists: {output} (use --force to replace)",
            file=sys.stderr,
        )
        return 2

    try:
        payload = source.read_bytes()
        corrected = patch_payload(payload)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(corrected)
    except (OSError, PatchError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(f"source SHA-256:  {sha256(payload)}")
    print(f"patched SHA-256: {sha256(corrected)}")
    print(f"corrected package: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
