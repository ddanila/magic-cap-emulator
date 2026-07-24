#!/usr/bin/env python3
"""Compare two Magic Cap ROM images (for example a development build against
the 3.1.2j release) and report layout, byte, and debug-string differences."""

from __future__ import annotations

import argparse
import hashlib
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path


ROM_BASE = 0x13C00000
RESET_INSTRUCTION = 0x08F00007
MONITOR_MARKER = b"IDT MONITOR "

MIN_STRING_LENGTH = 6
STRING_PATTERN = re.compile(rb"[\x20-\x7e]{%d,}" % MIN_STRING_LENGTH)

# Markers of a debug build: the SDK compiles Assert/Whisper/Log/DebugMessage
# in for the simulator only ("ignored on communicators"), and the Rosemary
# source tree paths leak through when assertions are retained.
DEBUG_MARKERS = (
    b"Assert",
    b"Whisper",
    b"DebugMessage",
    b"/export/src/gm/",
    b"Breakpoint",
    b"StayHere",
    b"TODO",
)
# Dates worth surfacing on sight: build stamps look like "Dec  5 1997".
DATE_PATTERN = re.compile(
    rb"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    rb"[ ]{1,2}\d{1,2}[ ]\d{4}"
)
VERSION_PATTERN = re.compile(rb"\d\.\d\.\d[a-z]?")


class FormatError(ValueError):
    """Raised when an input does not look like a DataRover ROM image."""


@dataclass(frozen=True)
class RomFacts:
    path: Path
    size: int
    sha256: str
    first_instruction: int
    has_monitor_marker: bool
    strings: tuple[bytes, ...]
    dates: tuple[bytes, ...]
    versions: tuple[bytes, ...]
    debug_marker_counts: dict[bytes, int]


def extract_strings(data: bytes) -> tuple[bytes, ...]:
    """Return printable ASCII runs, in order of first appearance."""
    return tuple(STRING_PATTERN.findall(data))


def read_facts(path: Path) -> RomFacts:
    data = path.read_bytes()
    if len(data) < 0x18:
        raise FormatError(f"{path}: too short to be a ROM image")

    strings = extract_strings(data)
    joined = b"\n".join(strings)
    return RomFacts(
        path=path,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        first_instruction=struct.unpack_from(">I", data)[0],
        has_monitor_marker=data[0x0C : 0x0C + len(MONITOR_MARKER)] == MONITOR_MARKER,
        strings=strings,
        dates=tuple(dict.fromkeys(m.group(0) for m in DATE_PATTERN.finditer(joined))),
        versions=tuple(
            dict.fromkeys(m.group(0) for m in VERSION_PATTERN.finditer(joined))
        ),
        debug_marker_counts={
            marker: joined.count(marker) for marker in DEBUG_MARKERS
        },
    )


def common_prefix_length(a: bytes, b: bytes) -> int:
    limit = min(len(a), len(b))
    for index in range(limit):
        if a[index] != b[index]:
            return index
    return limit


def common_suffix_length(a: bytes, b: bytes, ceiling: int) -> int:
    limit = min(len(a), len(b), ceiling)
    for index in range(1, limit + 1):
        if a[-index] != b[-index]:
            return index - 1
    return limit


def differing_spans(
    a: bytes, b: bytes, gap_tolerance: int = 64
) -> list[tuple[int, int]]:
    """Coalesce differing byte offsets into (start, length) spans.

    Runs separated by fewer than `gap_tolerance` identical bytes are merged so
    that one edited function reads as a single span rather than dozens.
    """
    if len(a) != len(b):
        raise FormatError("byte spans require equal-length images")

    spans: list[tuple[int, int]] = []
    start: int | None = None
    last_diff = 0
    for index, (left, right) in enumerate(zip(a, b)):
        if left != right:
            if start is None:
                start = index
            elif index - last_diff > gap_tolerance:
                spans.append((start, last_diff - start + 1))
                start = index
            last_diff = index
    if start is not None:
        spans.append((start, last_diff - start + 1))
    return spans


def report_side(facts: RomFacts, label: str) -> None:
    print(f"{label}: {facts.path}")
    print(f"  size:            {facts.size} bytes (0x{facts.size:x})")
    print(f"  sha256:          {facts.sha256}")
    print(f"  mapped span:     0x{ROM_BASE:08x}..0x{ROM_BASE + facts.size - 1:08x}")
    print(
        "  reset word:      "
        f"0x{facts.first_instruction:08x}"
        + (
            "  (expected)"
            if facts.first_instruction == RESET_INSTRUCTION
            else "  (unexpected)"
        )
    )
    print(
        "  IDT marker:      "
        + ("present" if facts.has_monitor_marker else "missing")
    )
    print(f"  printable runs:  {len(facts.strings)}")
    if facts.versions:
        shown = b" ".join(facts.versions[:8]).decode("ascii", "replace")
        print(f"  version-like:    {shown}")
    if facts.dates:
        shown = b" | ".join(facts.dates[:8]).decode("ascii", "replace")
        print(f"  date stamps:     {shown}")
    marks = ", ".join(
        f"{marker.decode()}={count}"
        for marker, count in facts.debug_marker_counts.items()
        if count
    )
    print(f"  debug markers:   {marks or 'none'}")


def report_strings(a: RomFacts, b: RomFacts, sample: int) -> None:
    set_a, set_b = set(a.strings), set(b.strings)
    only_a = sorted(set_a - set_b)
    only_b = sorted(set_b - set_a)
    print("String comparison")
    print(f"  shared:          {len(set_a & set_b)}")
    print(f"  only in A:       {len(only_a)}")
    print(f"  only in B:       {len(only_b)}")

    for label, unique in (("A", only_a), ("B", only_b)):
        interesting = [
            s for s in unique if any(marker in s for marker in DEBUG_MARKERS)
        ]
        if interesting:
            print(f"  debug-ish strings only in {label} ({len(interesting)}):")
            for value in interesting[:sample]:
                print(f"    {value.decode('ascii', 'replace')}")
            if len(interesting) > sample:
                print(f"    ... {len(interesting) - sample} more")


def report_bytes(a: Path, b: Path, gap_tolerance: int, sample: int) -> None:
    data_a, data_b = a.read_bytes(), b.read_bytes()
    print("Byte comparison")
    prefix = common_prefix_length(data_a, data_b)
    print(f"  common prefix:   {prefix} bytes (0x{prefix:x})")

    if len(data_a) != len(data_b):
        delta = len(data_b) - len(data_a)
        print(f"  size delta:      {delta:+d} bytes")
        print(
            "  span diff:       skipped, images differ in length; a byte-for-"
            "byte span list would be shift noise, so use the string "
            "comparison below"
        )
        suffix = common_suffix_length(data_a, data_b, min(len(data_a), len(data_b)))
        print(f"  common suffix:   {suffix} bytes (0x{suffix:x})")
        return

    identical = sum(1 for left, right in zip(data_a, data_b) if left == right)
    ratio = 100.0 * identical / len(data_a)
    spans = differing_spans(data_a, data_b, gap_tolerance)
    print(f"  identical bytes: {identical} of {len(data_a)} ({ratio:.2f}%)")
    print(f"  differing spans: {len(spans)} (gap tolerance {gap_tolerance})")
    for start, length in spans[:sample]:
        print(
            f"    0x{ROM_BASE + start:08x} +0x{length:<6x}"
            f" (file 0x{start:x})"
        )
    if len(spans) > sample:
        print(f"    ... {len(spans) - sample} more")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom_a", type=Path, help="first ROM image (A, e.g. release)")
    parser.add_argument(
        "rom_b", type=Path, help="second ROM image (B, e.g. development build)"
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=20,
        help="how many spans/strings to list per section (default: 20)",
    )
    parser.add_argument(
        "--gap-tolerance",
        type=int,
        default=64,
        help="identical bytes allowed inside one differing span (default: 64)",
    )
    parser.add_argument(
        "--dump-strings",
        type=Path,
        metavar="DIR",
        help=(
            "write the full string sets to DIR as only-a.txt, only-b.txt, and "
            "shared.txt. Keep DIR outside the Git checkout: ROM strings are "
            "copyrighted General Magic content, so a bulk dump is a research "
            "artifact, not repository material"
        ),
    )
    return parser.parse_args(argv)


def dump_strings(a: RomFacts, b: RomFacts, directory: Path) -> None:
    """Write full string sets for offline reading. Output stays uncommitted."""
    set_a, set_b = set(a.strings), set(b.strings)
    directory.mkdir(parents=True, exist_ok=True)
    for name, values in (
        ("only-a.txt", sorted(set_a - set_b)),
        ("only-b.txt", sorted(set_b - set_a)),
        ("shared.txt", sorted(set_a & set_b)),
    ):
        target = directory / name
        with target.open("wb") as handle:
            for value in values:
                handle.write(value + b"\n")
        print(f"  wrote {target} ({len(values)} strings)")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        facts_a = read_facts(args.rom_a)
        facts_b = read_facts(args.rom_b)
        report_side(facts_a, "A")
        print()
        report_side(facts_b, "B")
        print()
        report_bytes(args.rom_a, args.rom_b, args.gap_tolerance, args.sample)
        print()
        report_strings(facts_a, facts_b, args.sample)
        if args.dump_strings:
            print()
            print("String dump")
            dump_strings(facts_a, facts_b, args.dump_strings)
        if facts_a.sha256 == facts_b.sha256:
            print()
            print("The two images are byte-identical.")
    except (FormatError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
