#!/usr/bin/env python3
"""Turn a recorded DataRover frame sequence into a compact animated GIF.

MAME's `-mngwrite` writes one image per emulated frame (~60 fps), so a
two-minute tour is 8000 frames of which most are byte-for-byte repeats of a
static Magic Cap scene. Two things make the result small and watchable:

* **Deduplicate on decoded pixels, not file bytes.** The MNG->PNG extraction
  re-encodes identical screens to different bytes, so hashing files overcounts
  distinct frames roughly two-fold (346 vs the real 175 on the reference tour).
  Every run here is keyed on the decoded grayscale buffer.
* **Keep a per-frame delay.** GIF89a stores a delay per frame, so animations
  (the stamps drawer, window zooms) can keep their recorded speed while long
  static scenes are capped - the tour drops from 133 s to 40 s without losing
  any motion. `--flat` forces one delay for every frame instead.

The output is native resolution; nothing is scaled. See docs/demo.md for the
recording steps that produce the frame directory.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image


DEFAULT_FPS = 60.0
DEFAULT_CAP_MS = 1000
DEFAULT_FLOOR_MS = 40
GIF_TICK_MS = 10  # GIF delays are stored in hundredths of a second


def frame_paths(directory: Path) -> list[Path]:
    """Return the recorded frames in capture order."""
    return sorted(directory.glob("*.png"))


def pixel_key(path: Path) -> str:
    """Hash a frame's decoded pixels, ignoring PNG encoding differences."""
    with Image.open(path) as image:
        return hashlib.sha256(image.convert("L").tobytes()).hexdigest()


def frame_runs(paths: list[Path]) -> list[tuple[Path, int]]:
    """Collapse consecutive identical frames into (frame, hold-in-frames)."""
    runs: list[tuple[Path, int]] = []
    previous_key = None
    for path in paths:
        key = pixel_key(path)
        if runs and key == previous_key:
            frame, hold = runs[-1]
            runs[-1] = (frame, hold + 1)
        else:
            runs.append((path, 1))
            previous_key = key
    return runs


def delays_ms(
    runs: list[tuple[Path, int]],
    fps: float = DEFAULT_FPS,
    cap_ms: int = DEFAULT_CAP_MS,
    floor_ms: int = DEFAULT_FLOOR_MS,
    flat: bool = False,
) -> list[int]:
    """Give each kept frame its recorded on-screen time, clamped and quantised.

    Static scenes are capped at `cap_ms` so the tour plays back quickly;
    single-frame animation steps get at least `floor_ms` so they stay visible.
    """
    if flat:
        return [_quantise(cap_ms)] * len(runs)
    return [
        _quantise(max(floor_ms, min(cap_ms, round(hold / fps * 1000))))
        for _frame, hold in runs
    ]


def _quantise(milliseconds: int) -> int:
    """Round to the 10 ms GIF tick, keeping at least one tick."""
    return max(GIF_TICK_MS, round(milliseconds / GIF_TICK_MS) * GIF_TICK_MS)


def write_gif(
    runs: list[tuple[Path, int]], delays: list[int], out: Path, colors: int = 16
) -> None:
    """Write the GIF at native resolution with per-frame delays."""
    images = []
    for frame, _hold in runs:
        with Image.open(frame) as image:
            images.append(image.convert("RGB").quantize(colors=colors))
    images[0].save(
        out,
        save_all=True,
        append_images=images[1:],
        duration=delays,
        loop=0,
        disposal=2,
        optimize=True,
    )


def optimize_gif(out: Path) -> bool:
    """Shrink with gifsicle if present; per-frame delays are preserved."""
    gifsicle = shutil.which("gifsicle")
    if not gifsicle:
        return False
    temporary = out.with_suffix(".gifsicle.tmp")
    subprocess.run(
        [gifsicle, "-O3", "--careful", str(out), "-o", str(temporary)],
        check=True,
    )
    temporary.replace(out)
    return True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "frames", type=Path, help="directory of recorded PNG frames"
    )
    parser.add_argument("out", type=Path, help="GIF to write")
    parser.add_argument(
        "--fps",
        type=float,
        default=DEFAULT_FPS,
        help="capture rate, used to turn frame holds into delays",
    )
    parser.add_argument(
        "--cap-ms",
        type=int,
        default=DEFAULT_CAP_MS,
        help="longest delay any single frame may hold",
    )
    parser.add_argument(
        "--floor-ms",
        type=int,
        default=DEFAULT_FLOOR_MS,
        help="shortest delay, so animation steps stay visible",
    )
    parser.add_argument(
        "--flat",
        action="store_true",
        help="give every frame --cap-ms instead of its recorded duration",
    )
    parser.add_argument("--colors", type=int, default=16)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    paths = frame_paths(args.frames.expanduser().resolve())
    if not paths:
        print(f"error: no PNG frames in {args.frames}", file=sys.stderr)
        return 2

    runs = frame_runs(paths)
    delays = delays_ms(
        runs,
        fps=args.fps,
        cap_ms=args.cap_ms,
        floor_ms=args.floor_ms,
        flat=args.flat,
    )
    out = args.out.expanduser().resolve()
    write_gif(runs, delays, out, colors=args.colors)
    optimized = optimize_gif(out)

    with Image.open(out) as image:
        size = f"{image.width}x{image.height}"
    print(
        f"{out}: {size}, {len(runs)} of {len(paths)} frames kept, "
        f"{sum(delays) / 1000:.1f}s, {out.stat().st_size / 1024:.0f} KB"
        f"{'' if optimized else ' (install gifsicle to shrink further)'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
