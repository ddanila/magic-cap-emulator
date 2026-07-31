#!/usr/bin/env python3
"""Record the Sam-to-Danila Beam and parody fax demo.

This runs the real paired IrDA and fax workflows, records both native LCDs,
places the two recordings side by side, and writes an optimized GIF. Large
intermediate artifacts stay under MAGIC_CAP_ASSETS.
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    from make_demo_gif import delays_ms, frame_runs, optimize_gif, write_gif
except ModuleNotFoundError:
    from tools.make_demo_gif import delays_ms, frame_runs, optimize_gif, write_gif


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "paired-demo"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "media" / "datarover-beam-fax-demo.gif"
SIGNATURE = REPO_ROOT / "docs" / "media" / "sam-altman-signature.svg"


def command(args: list[str], cwd: Path = REPO_ROOT) -> None:
    print("+ " + " ".join(args), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def latest_child(directory: Path, previous: set[Path]) -> Path:
    created = {path for path in directory.iterdir() if path.is_dir()} - previous
    if len(created) != 1:
        raise RuntimeError(f"expected one new run under {directory}, got {created}")
    return created.pop()


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold
        else ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    names += ["/System/Library/Fonts/Helvetica.ttc"]
    for name in names:
        if Path(name).is_file():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def make_invitation(run_dir: Path) -> tuple[Path, Path]:
    signature_png = run_dir / "signature.png"
    magick = shutil.which("magick") or shutil.which("convert")
    if magick is None:
        raise RuntimeError("ImageMagick is required to rasterize the signature")
    command(
        [
            magick,
            "-background",
            "white",
            str(SIGNATURE),
            "-resize",
            "145x58",
            "-flatten",
            str(signature_png),
        ]
    )

    page = Image.new("L", (480, 320), 255)
    draw = ImageDraw.Draw(page)
    draw.rectangle((5, 5, 474, 314), outline=0, width=2)
    draw.text((18, 14), "OPENAI", fill=0, font=font(25, bold=True))
    draw.text(
        (156, 18),
        "PARODY DEMO — NOT A REAL OFFER",
        fill=0,
        font=font(12, bold=True),
    )
    draw.line((18, 50, 461, 50), fill=0, width=1)
    lines = (
        ("To: Danila Sukharev", 64, True),
        ("Dear Danila,", 96, False),
        ("We would be delighted if you joined OpenAI as a", 121, False),
        ("Senior Magic Cap Emulator Engineer.", 142, True),
        ("Please report to the Internet Center in 1998.", 167, False),
        ("Sincerely,", 197, False),
    )
    for text, y, bold in lines:
        draw.text((22, y), text, fill=0, font=font(15, bold=bold))
    with Image.open(signature_png) as signature:
        signature = signature.convert("L")
        page.paste(signature, (260, 201))
    draw.text((300, 260), "Sam Altman", fill=0, font=font(14))
    draw.text(
        (22, 291),
        "A joke recorded on an emulated 1998 DataRover.",
        fill=0,
        font=font(11),
    )
    page = page.quantize(colors=4).convert("L")
    png = run_dir / "parody-job-invitation.png"
    page.save(png)

    raw = bytearray()
    pixels = page.load()
    for y in range(320):
        for x in range(0, 480, 4):
            values = [
                round((255 - pixels[x + offset, y]) / 255 * 3)
                for offset in range(4)
            ]
            raw.append(
                (values[0] << 6) | (values[1] << 4) | (values[2] << 2) | values[3]
            )
    raw_path = run_dir / "parody-job-invitation.2bpp"
    raw_path.write_bytes(raw)
    return png, raw_path


def find_mngs(directory: Path) -> list[Path]:
    matches = sorted(directory.glob("recording*.mng"))
    if not matches:
        raise RuntimeError(f"no recording MNG found under {directory}")
    return matches


def native_lcd_mng(mngs: list[Path]) -> Path:
    """Select MAME's 480x320 LCD recording, ignoring the modem bitmap."""
    for mng in mngs:
        with mng.open("rb") as stream:
            stream.read(8)
            while header := stream.read(8):
                if len(header) != 8:
                    break
                length, chunk_type = struct.unpack(">I4s", header)
                payload = stream.read(length)
                stream.read(4)
                if chunk_type == b"IHDR":
                    width, height = struct.unpack(">II", payload[:8])
                    if (width, height) == (480, 320):
                        return mng
                    break
    raise RuntimeError(f"no 480x320 LCD recording found in {mngs}")


def extract(mngs: list[Path], destination: Path, start: int = 0) -> list[Path]:
    """Stream independent PNG frames out of MAME's MNG without a giant cache."""
    destination.mkdir(exist_ok=True)
    mng = native_lcd_mng(mngs)
    png_signature = b"\x89PNG\r\n\x1a\n"
    frame = -1
    chunks: list[bytes] = []
    with mng.open("rb") as stream:
        if stream.read(8) != b"\x8aMNG\r\n\x1a\n":
            raise RuntimeError(f"invalid MNG signature: {mng}")
        while header := stream.read(8):
            if len(header) != 8:
                raise RuntimeError(f"truncated MNG chunk header: {mng}")
            length, chunk_type = struct.unpack(">I4s", header)
            body = stream.read(length + 4)
            if len(body) != length + 4:
                raise RuntimeError(f"truncated MNG chunk: {mng}")
            chunk = header + body
            if chunk_type == b"IHDR":
                frame += 1
                chunks = [chunk] if frame >= start else []
            elif chunks:
                chunks.append(chunk)
                if chunk_type == b"IEND":
                    (destination / f"f{frame - start:05d}.png").write_bytes(
                        png_signature + b"".join(chunks)
                    )
                    chunks = []
    frames = sorted(destination.glob("*.png"))
    if not frames:
        raise RuntimeError(f"no frames decoded from {mngs}")
    return frames


def paired_frames(
    left: list[Path],
    right: list[Path],
    start: int,
    destination: Path,
    sequence: int,
    title: str,
) -> int:
    destination.mkdir(exist_ok=True)
    count = max(len(left), len(right))
    start = min(start, count - 1)
    label_font = font(15, bold=True)
    title_font = font(13, bold=True)
    for index in range(start, count):
        with Image.open(left[min(index, len(left) - 1)]) as left_image:
            with Image.open(right[min(index, len(right) - 1)]) as right_image:
                canvas = Image.new("L", (960, 348), 255)
                canvas.paste(left_image.convert("L"), (0, 28))
                canvas.paste(right_image.convert("L"), (480, 28))
        draw = ImageDraw.Draw(canvas)
        draw.text((8, 5), "Sam Altman", fill=0, font=label_font)
        right_label = "Danila Sukharev"
        right_width = draw.textbbox((0, 0), right_label, font=label_font)[2]
        draw.text((952 - right_width, 5), right_label, fill=0, font=label_font)
        width = draw.textbbox((0, 0), title, font=title_font)[2]
        draw.text(((960 - width) // 2, 7), title, fill=0, font=title_font)
        canvas.save(destination / f"f{sequence:05d}.png")
        sequence += 1
    return sequence


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--beam-start", type=int, default=8600)
    parser.add_argument("--fax-start", type=int, default=8100)
    parser.add_argument("--skip-runs", type=Path, help="reuse a prior paired-demo run")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    args.mame = args.mame.expanduser().resolve()
    args.rompath = args.rompath.expanduser().resolve()
    if not args.mame.is_file() or not args.rompath.is_dir():
        print("error: MAME executable or ROM path is missing", file=sys.stderr)
        return 2

    if args.skip_runs:
        run_dir = args.skip_runs.expanduser().resolve()
        beam_dir = next((run_dir / "beam").iterdir())
        fax_dir = next((run_dir / "fax").iterdir())
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
        beam_work = run_dir / "beam"
        owner_work = run_dir / "fax-answer-owner"
        fax_work = run_dir / "fax"
        beam_work.mkdir(parents=True)
        owner_work.mkdir()
        fax_work.mkdir()
        _invitation_png, invitation_raw = make_invitation(run_dir)

        before = set(beam_work.iterdir())
        command(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "beam_regression.py"),
                "--mame",
                str(args.mame),
                "--rompath",
                str(args.rompath),
                "--workdir",
                str(beam_work),
                "--sender-first",
                "Sam",
                "--sender-last",
                "Altman",
                "--receiver-first",
                "Danila",
                "--receiver-last",
                "Sukharev",
                "--record",
            ]
        )
        beam_dir = latest_child(beam_work, before)

        # The Beam receiver now owns Sam's card. Prepare the same Danila-owned
        # device independently for Fax so the incoming sender identity does
        # not trigger Magic Cap's duplicate-card conversation over page two.
        before = set(owner_work.iterdir())
        command(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "beam_regression.py"),
                "--mame",
                str(args.mame),
                "--rompath",
                str(args.rompath),
                "--workdir",
                str(owner_work),
                "--sender-first",
                "Danila",
                "--sender-last",
                "Sukharev",
                "--receiver-first",
                "Demo",
                "--receiver-last",
                "Peer",
                "--frames",
                "9300",
                "--setup-only",
            ]
        )
        owner_dir = latest_child(owner_work, before)

        before = set(fax_work.iterdir())
        command(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "fax_pair_regression.py"),
                "--mame",
                str(args.mame),
                "--rompath",
                str(args.rompath),
                "--workdir",
                str(fax_work),
                "--nvram-source",
                str(owner_dir / "sender" / "nvram"),
                "--origin-nvram-source",
                str(beam_dir / "sender" / "nvram"),
                "--recipient-first",
                "Danila",
                "--recipient-last",
                "Sukharev",
                "--origin-screen-raw",
                str(invitation_raw),
                "--open-received-fax",
                "--record",
            ]
        )
        fax_dir = latest_child(fax_work, before)

    decoded = run_dir / "decoded"
    decoded.mkdir(exist_ok=True)
    beam_left = extract(
        find_mngs(beam_dir / "sender"), decoded / "beam-sam", args.beam_start
    )
    beam_right = extract(
        find_mngs(beam_dir / "receiver"), decoded / "beam-danila", args.beam_start
    )
    fax_left = extract(
        find_mngs(fax_dir / "origin"), decoded / "fax-sam", args.fax_start
    )
    fax_right = extract(
        find_mngs(fax_dir / "answer"), decoded / "fax-danila", args.fax_start
    )

    combined = run_dir / "combined"
    sequence = paired_frames(
        beam_left,
        beam_right,
        0,
        combined,
        0,
        "IrDA Beam",
    )
    paired_frames(
        fax_left,
        fax_right,
        0,
        combined,
        sequence,
        "Built-in modem fax",
    )
    paths = sorted(combined.glob("*.png"))
    runs = frame_runs(paths)
    delays = delays_ms(runs)
    if delays:
        delays[-1] = 5000
    args.output = args.output.expanduser().resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_gif(runs, delays, args.output)
    optimize_gif(args.output)
    print(
        f"{args.output}: {len(runs)} of {len(paths)} frames, "
        f"{sum(delays) / 1000:.1f}s, {args.output.stat().st_size / 1024:.0f} KB"
    )
    print(f"Artifacts: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
