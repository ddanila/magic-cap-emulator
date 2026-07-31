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
INVITATION_TEXT = """PARODY DEMO - NOT A REAL OFFER
To: Danila Sukharev
Dear Danila,
Please join OpenAI as a
Senior Magic Cap Emulator Engineer.
This is a joke for a 1998 DataRover.
Sincerely,
Sam Altman"""


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


def signature_strokes(run_dir: Path) -> list[tuple[int, int, int]]:
    """Rasterize the reference signature into horizontal Notebook pen runs."""
    signature = run_dir / "notebook-signature.png"
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
            "120x45",
            "-flatten",
            "-threshold",
            "55%",
            str(signature),
        ]
    )
    strokes: list[tuple[int, int, int]] = []
    with Image.open(signature) as image:
        pixels = image.convert("L")
        for y in range(pixels.height):
            start: int | None = None
            for x in range(pixels.width + 1):
                black = x < pixels.width and pixels.getpixel((x, y)) < 128
                if black and start is None:
                    start = x
                elif not black and start is not None:
                    strokes.append((270 + start, 215 + y, 270 + x - 1))
                    start = None
    return strokes


def notebook_invitation_script(strokes: list[tuple[int, int, int]]) -> str:
    """Create, commit, and reopen a real Magic Cap Notebook invitation."""
    text_start = 3700
    lines = INVITATION_TEXT.splitlines()
    text_steps = "\n".join(
        f'  elseif frames == {text_start + index * 240} then '
        f'emu.keypost("{line.replace(chr(34), chr(92) + chr(34))}\\n")'
        for index, line in enumerate(lines)
    )
    keyboard_done = text_start + len(lines) * 240
    stroke_start = keyboard_done + 500
    stroke_interval = 30
    stroke_steps = "\n".join(
        f"  elseif frames == {stroke_start + index * stroke_interval} "
        f"then press({x1}, {y})\n"
        f"  elseif frames == {stroke_start + index * stroke_interval + 10} "
        f"then move({x2}, {y})\n"
        f"  elseif frames == {stroke_start + index * stroke_interval + 20} "
        "then release()"
        for index, (x1, y, x2) in enumerate(strokes)
    )
    ink_done = stroke_start + len(strokes) * stroke_interval
    return f"""local machine = manager.machine
local ports = machine.ioport.ports
local screen = machine.screens[":screen"]
local touch_x = ports[":TOUCH_X"]:field(0xffff)
local touch_y = ports[":TOUCH_Y"]:field(0xffff)
local touch_button = ports[":TOUCH_BUTTON"]:field(0x01)
local frames = 0

local function move(x, y)
  touch_x:set_value(math.floor(x * 0xffff / 479))
  touch_y:set_value(math.floor(y * 0xffff / 319))
end
local function press(x, y)
  move(x, y)
  touch_button:set_value(1)
end
local function release()
  touch_button:set_value(0)
end

emu.register_frame_done(function()
  frames = frames + 1
  if frames == 1000 then press(440, 10)
  elseif frames == 1020 then release()
  elseif frames == 1300 then press(335, 170)
  elseif frames == 1320 then release()
  elseif frames == 1800 then press(451, 100)
  elseif frames == 1820 then release()
  elseif frames == 2200 then press(145, 92)
  elseif frames == 2220 then release()
  elseif frames == 3000 then press(31, 80)
  elseif frames == 3020 then release()
  elseif frames == 3200 then press(376, 301)
  elseif frames == 3220 then release()
{text_steps}
  elseif frames == {keyboard_done + 200} then press(455, 302)
  elseif frames == {keyboard_done + 220} then release()
{stroke_steps}
  elseif frames == {ink_done + 200} then
    screen:snapshot("notebook-invitation-created.png")
  elseif frames == {ink_done + 400} then press(440, 10)
  elseif frames == {ink_done + 420} then release()
  elseif frames == {ink_done + 700} then press(335, 170)
  elseif frames == {ink_done + 720} then release()
  elseif frames == {ink_done + 1000} then
    screen:snapshot("notebook-invitation-reopened.png")
  elseif frames == {ink_done + 1200} then machine:exit()
  end
end)
"""


def prepare_notebook_invitation(
    mame: Path, rompath: Path, source_nvram: Path, run_dir: Path
) -> Path:
    """Prepare a retained Sam-owned NVRAM with the real invitation page open."""
    cfg = run_dir / "cfg"
    nvram = run_dir / "nvram"
    snapshots = run_dir / "snapshots"
    cfg.mkdir(parents=True)
    snapshots.mkdir()
    shutil.copytree(source_nvram, nvram)
    script = run_dir / "notebook-invitation.lua"
    script.write_text(
        notebook_invitation_script(signature_strokes(run_dir)), encoding="utf-8"
    )
    command(
        [
            str(mame),
            "datarover840",
            "-rompath",
            str(rompath),
            "-cfg_directory",
            str(cfg),
            "-nvram_directory",
            str(nvram),
            "-snapshot_directory",
            str(snapshots),
            "-snapview",
            "native",
            "-autoboot_script",
            str(script),
            "-autoboot_delay",
            "0",
            "-video",
            "none",
            "-sound",
            "none",
            "-videodriver",
            "dummy",
            "-audiodriver",
            "dummy",
            "-nothrottle",
            "-skip_gameinfo",
        ],
        cwd=mame.parent,
    )
    return nvram


def find_mngs(directory: Path) -> list[Path]:
    matches = sorted(directory.glob("recording*.mng"))
    if not matches:
        raise RuntimeError(f"no recording MNG found under {directory}")
    return matches


def native_lcd_mngs(mngs: list[Path]) -> list[Path]:
    """Select every 480x320 LCD rollover, ignoring modem recordings."""
    matches: list[Path] = []
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
                        matches.append(mng)
                    break
    if not matches:
        raise RuntimeError(f"no 480x320 LCD recording found in {mngs}")
    return matches


def extract(mngs: list[Path], destination: Path, start: int = 0) -> list[Path]:
    """Stream independent PNG frames out of MAME's MNG without a giant cache."""
    destination.mkdir(exist_ok=True)
    png_signature = b"\x89PNG\r\n\x1a\n"
    frame = -1
    for mng in native_lcd_mngs(mngs):
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
        beam_dir = max((run_dir / "beam").iterdir(), key=lambda path: path.name)
        fax_dir = max((run_dir / "fax").iterdir(), key=lambda path: path.name)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
        beam_work = run_dir / "beam"
        owner_work = run_dir / "fax-answer-owner"
        document_work = run_dir / "fax-origin-document"
        fax_work = run_dir / "fax"
        beam_work.mkdir(parents=True)
        owner_work.mkdir()
        document_work.mkdir()
        fax_work.mkdir()

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
        document_nvram = prepare_notebook_invitation(
            args.mame,
            args.rompath,
            beam_dir / "sender" / "nvram",
            document_work,
        )

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
                str(document_nvram),
                "--recipient-first",
                "Danila",
                "--recipient-last",
                "Sukharev",
                "--origin-document",
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
