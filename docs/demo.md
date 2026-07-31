# Recording a demo of the emulated machine

The README's animation is a real recording of the emulated DataRover, not a
mock-up: MAME renders every emulated frame to an MNG and the speaker to a WAV,
and the tour is driven by a deterministic Lua input script rather than by hand.
This is the reproducible pipeline, including the parts that are easy to get
wrong.

The GIF pipeline additionally needs ImageMagick, FFmpeg and the Python Pillow
module; `gifsicle` is optional but makes the final GIF smaller. On
Debian/Ubuntu with the system Python:

```sh
sudo apt-get install imagemagick ffmpeg python3-pil gifsicle
```

If `python3` names a different Python installation, install Pillow into that
interpreter's environment or invoke the tool and test suite with
`/usr/bin/python3`.

The scenario is boot → Magic Cap desk → Stamps drawer → Hallway → the painting
→ pan around → Downtown → the Internet Center → Internet Mail rules → close.

## 1. Prepare a calibrated NVRAM set

A fresh machine opens the pen-calibration scene. Calibrating and then calling
`machine:exit()` does **not** persist it: Magic Cap only flushes its heap on a
real suspend. [`tools/demo_prep.lua`](../tools/demo_prep.lua) taps the three
calibration targets and then presses the power button the way a user does, so
the resulting NVRAM boots straight to the welcome scene.

```sh
assets="${MAGIC_CAP_ASSETS:-$PWD/../magic-cap-assets}"
DEMO="$assets/runtime/demo/tour"
mkdir -p "$DEMO"/{nvram,cfg,frames}
../mame/datarover datarover840 \
  -rompath "$assets/roms" \
  -nvram_directory "$DEMO/nvram" -cfg_directory "$DEMO/cfg" \
  -autoboot_delay 0 -autoboot_script tools/demo_prep.lua \
  -video none -videodriver dummy -audiodriver dummy \
  -nothrottle -skip_gameinfo
```

Always pass your own `-cfg_directory`: MAME persists input changes into the
shared `cfg/` and a stale entry silently changes later runs.

## 2. Record the tour

[`tools/demo_tour.lua`](../tools/demo_tour.lua) replays the scenario against
that NVRAM and takes a snapshot at each beat. `-snapview native` keeps the
snapshots at the LCD's own 480×320 rather than the window size.

```sh
../mame/datarover datarover840 \
  -rompath "$assets/roms" \
  -nvram_directory "$DEMO/nvram" -cfg_directory "$DEMO/cfg" \
  -autoboot_delay 0 -autoboot_script tools/demo_tour.lua \
  -mngwrite "$DEMO/demo.mng" -wavwrite "$DEMO/demo.wav" \
  -snapview native -snapshot_directory "$DEMO/beats" \
  -video none -videodriver dummy -nothrottle -skip_gameinfo
```

- `-mngwrite`, `-wavwrite` and `-snapview native` all work under `-video none`.
  **`-aviwrite` does not** — with no render target it silently produces
  nothing.
- The scenario is verified by its snapshots. `g-center.png` and
  `i-closed.png` are the same scene, so their hashes must match; if a tap lands
  early the beats diverge and it shows up there. Watch for modal
  confirmations — the Getting Started card's STOP button opens one, and a
  missed tap desynchronises every later beat.

## 3. Extract frames

ffmpeg mis-parses MAME's MNG (it produced a 0.98 s MP4 and a 0-byte GIF from a
two-minute capture). Decode with ImageMagick first, then feed ffmpeg PNGs:

```sh
magick "$DEMO/demo0.mng" -coalesce "$DEMO/frames/f%05d.png"
```

## 4. Video with sound, and the GIF

```sh
ffmpeg -framerate 60 -i "$DEMO/frames/f%05d.png" -i "$DEMO/demo.wav" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$DEMO/datarover-tour.mp4"

python3 tools/make_demo_gif.py "$DEMO/frames" "$DEMO/datarover-tour.gif"
```

[`tools/make_demo_gif.py`](../tools/make_demo_gif.py) does the two things that
make the GIF small and watchable:

- **Deduplicates on decoded pixels.** The MNG→PNG step re-encodes identical
  screens to different bytes, so hashing files overcounts: 346 "distinct"
  frames against a true 175 on the reference tour.
- **Keeps a per-frame delay.** GIF89a stores a delay per frame, so animations
  (the stamps drawer, window zooms) run at their recorded speed while static
  scenes are capped at 1 s. The tour goes from 133 s to 39.7 s with no motion
  lost. `--flat` gives every frame the same delay instead, which is uniformly
  worse here: 175 s long *and* it turns the stamps animation into a slideshow.

Reference numbers for the committed tour: 8001 frames captured, 175 kept,
480×320, 39.7 s, 228 KB.

## Paired Beam and fax demo

[`tools/paired_demo.py`](../tools/paired_demo.py) records a second,
two-DataRover scenario:

1. Sam Altman's DataRover discovers Danila Sukharev's device over IrDA and
   Beams Sam's name card.
2. Sam's device displays a clearly marked parody job invitation and opens the
   real Magic Cap Fax workflow.
3. The built-in software modems dial, ring, negotiate, and transfer the
   rendered screen to Danila's DataRover.
4. Danila receives the page through the normal Phone Status **receive fax**
   action and retained In-box storage.

The DataRover ROM stores and identifies the received second page, but its Fax
viewer currently exposes that transferred screen as a blank gray viewport.
After Danila opens page two, the demo redisplays the same native 2 bpp source
buffer for the final five-second reading beat. The modem negotiation, image
transfer, two-page stationery, and In-box storage before that display step are
all the real paired-emulator workflow.

The invitation says **PARODY DEMO — NOT A REAL OFFER**. Its signature comes
from Wikimedia Commons'
[Sam Altman autograph SVG](https://commons.wikimedia.org/wiki/File:Sam_altman_autograph_2024.svg),
which Commons identifies as public domain under its signature policy. The
unaltered source SVG is committed at
[`docs/media/sam-altman-signature.svg`](media/sam-altman-signature.svg).

Run the complete workflow and produce the committed side-by-side GIF with:

```sh
python3 tools/paired_demo.py
```

The tool builds the invitation at native 480×320 resolution, converts it to
the DataRover's real 2 bpp framebuffer layout, runs the existing Beam and
paired-fax acceptances with recording enabled, decodes the four MAME MNG
streams, and combines them into
[`docs/media/datarover-beam-fax-demo.gif`](media/datarover-beam-fax-demo.gif).
The recording starts after owner setup, at the two desktops, rather than
replaying first-boot calibration.

Reference numbers for the committed paired demo: 17,832 captured LCD frames,
264 kept, 960×348, 112.6 s, 242 KB.

Large MNG, PNG, NVRAM, line-audio, and protocol artifacts stay under
`$MAGIC_CAP_ASSETS/runtime/paired-demo/`. A completed run can be re-rendered
without repeating the emulation:

```sh
python3 tools/paired_demo.py \
  --skip-runs "$MAGIC_CAP_ASSETS/runtime/paired-demo/<run>"
```

## Artifacts

Recordings live outside the repo with the rest of the large assets. Only the
two compact README animations are committed:
[`datarover-tour.gif`](media/datarover-tour.gif) and
[`datarover-beam-fax-demo.gif`](media/datarover-beam-fax-demo.gif).
