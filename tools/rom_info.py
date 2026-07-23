#!/usr/bin/env python3
"""Inspect a DataRover 840 ROM image and its optional flasher-card wrapper."""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from dataclasses import dataclass
from pathlib import Path


ROM_BASE = 0x13C00000
KNOWN_USA_SHA256 = (
    "94785cb334f14eac00ed200af014c35972b4f25694103bc6a49b3afa280a6f1b"
)
RESET_INSTRUCTION = 0x08F00007
MONITOR_MARKER = b"IDT MONITOR "

FLASH_CARD_SIZE = 8 * 1024 * 1024
FLASH_HEADER_SIZE = 0x400
FLASH_MAGIC = b"BowserLives\0"
FLASH_BASE = 0xB3C00000


class FormatError(ValueError):
    """Raised when an input does not match the observed DataRover format."""


@dataclass(frozen=True)
class RomInfo:
    size: int
    sha256: str
    first_instruction: int
    has_monitor_marker: bool


@dataclass(frozen=True)
class FlasherInfo:
    version: int
    reserved: int
    base_address: int
    payload_size: int
    payload: bytes
    header_is_zero_filled: bool
    padding_is_erased: bool


def inspect_rom(data: bytes) -> RomInfo:
    """Return the ROM properties needed before mapping it in an emulator."""
    if len(data) < 4:
        raise FormatError("ROM is too short to contain a MIPS instruction")

    return RomInfo(
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        first_instruction=struct.unpack_from(">I", data)[0],
        has_monitor_marker=data[0x0C : 0x0C + len(MONITOR_MARKER)]
        == MONITOR_MARKER,
    )


def inspect_flasher(data: bytes) -> FlasherInfo:
    """Parse the header and payload layout observed in the 840F card image."""
    if len(data) != FLASH_CARD_SIZE:
        raise FormatError(
            f"flasher image is {len(data)} bytes; expected {FLASH_CARD_SIZE}"
        )
    if data[: len(FLASH_MAGIC)] != FLASH_MAGIC:
        raise FormatError("flasher image does not begin with BowserLives\\0")

    version, reserved, base_address, payload_size = struct.unpack_from(
        ">IIII", data, 0x0C
    )
    payload_end = FLASH_HEADER_SIZE + payload_size
    if payload_end > len(data):
        raise FormatError("flasher payload extends past the end of the card")

    return FlasherInfo(
        version=version,
        reserved=reserved,
        base_address=base_address,
        payload_size=payload_size,
        payload=data[FLASH_HEADER_SIZE:payload_end],
        header_is_zero_filled=not any(data[0x1C:FLASH_HEADER_SIZE]),
        padding_is_erased=all(byte == 0xFF for byte in data[payload_end:]),
    )


def hex_span(base: int, size: int) -> str:
    """Format an inclusive address span."""
    return f"0x{base:08x}..0x{base + size - 1:08x}"


def report_rom(path: Path, info: RomInfo) -> None:
    print(f"ROM: {path}")
    print(f"  size:              {info.size} bytes (0x{info.size:x})")
    print(f"  sha256:            {info.sha256}")
    print(f"  mapped span:       {hex_span(ROM_BASE, info.size)}")
    print(f"  first BE word:     0x{info.first_instruction:08x}")
    print(
        "  reset instruction: "
        + ("recognized" if info.first_instruction == RESET_INSTRUCTION else "unknown")
    )
    print(
        "  IDT marker @ 0x0c: " + ("present" if info.has_monitor_marker else "missing")
    )
    print(
        "  known USA 3.1.2j:  "
        + ("yes" if info.sha256 == KNOWN_USA_SHA256 else "no")
    )


def report_flasher(path: Path, info: FlasherInfo, rom_data: bytes) -> bool:
    payload_matches = info.payload == rom_data
    print(f"Flasher card: {path}")
    print(f"  size:              {FLASH_CARD_SIZE} bytes (8 MiB)")
    print(f"  version:           {info.version}")
    print(f"  reserved word:     0x{info.reserved:08x}")
    print(f"  base address:      0x{info.base_address:08x}")
    print(f"  payload size:      {info.payload_size} bytes (0x{info.payload_size:x})")
    print(
        "  reserved header:   "
        + ("zero-filled" if info.header_is_zero_filled else "contains data")
    )
    print(
        "  trailing space:    "
        + ("0xff-filled" if info.padding_is_erased else "contains non-0xff data")
    )
    print("  payload matches:   " + ("yes" if payload_matches else "no"))
    return (
        info.version == 1
        and info.reserved == 0
        and info.base_address == FLASH_BASE
        and payload_matches
        and info.payload_size == len(rom_data)
        and info.header_is_zero_filled
        and info.padding_is_erased
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path, help="raw MagicCAP-USA.image path")
    parser.add_argument(
        "--flasher",
        type=Path,
        help="optional uncompressed DataRover840FRomFlasher card image",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        rom_data = args.rom.read_bytes()
        rom_info = inspect_rom(rom_data)
        report_rom(args.rom, rom_info)

        valid = (
            rom_info.first_instruction == RESET_INSTRUCTION
            and rom_info.has_monitor_marker
        )
        if args.flasher:
            print()
            flasher_info = inspect_flasher(args.flasher.read_bytes())
            valid = report_flasher(args.flasher, flasher_info, rom_data) and valid
    except (FormatError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
