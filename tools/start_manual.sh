#!/usr/bin/env bash
#
# Manual/interactive launcher for the DataRover 840 MAME driver.
# For headless verification use the regression harnesses in this
# directory instead; see docs/mame-bringup.md.
#
# Usage:
#   tools/start_manual.sh              # boot Magic Cap, handheld LCD view (default)
#   tools/start_manual.sh monitor      # serial view for the IDT monitor prompt
#   tools/start_manual.sh both         # LCD and serial terminal side by side
#   tools/start_manual.sh snap         # boot with F12 native LCD snapshots enabled
#   tools/start_manual.sh fresh        # back up machine state, start from clean NVRAM
#   tools/start_manual.sh -- <args>    # pass extra args straight through to ./datarover
#
# Environment overrides:
#   MAME_DIR    MAME fork checkout (default: sibling ../mame of this repo)
#   ASSETS      persistent asset tree (default: ~/fun/magic-cap-assets)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAME_DIR="${MAME_DIR:-$REPO_ROOT/../mame}"
ASSETS="${ASSETS:-$HOME/fun/magic-cap-assets}"
ROMPATH="$ASSETS/roms"
BIN="$MAME_DIR/datarover"
ROM="$ROMPATH/datarover840/magiccap-usa.image"

fail() { printf 'error: %s\n' "$1" >&2; exit 1; }

[[ -x "$BIN" ]] || fail "datarover binary not found at $BIN
       build it:  cd $MAME_DIR && make SUBTARGET=datarover \\
                    SOURCES=src/mame/skeleton/datarover.cpp REGENIE=1 \\
                    NO_USE_PORTAUDIO=1 -j\"\$(nproc)\"
       (see docs/mame-bringup.md)"
[[ -f "$ROM" ]] || fail "ROM not found at $ROM (see docs/rom-layout.md)"

# View / mode selection.
view="LCD"
extra=()
mode="${1:-run}"
case "$mode" in
  run|"")     view="LCD" ;;
  monitor)    view="Serial terminal" ;;
  both)       view="LCD and serial terminal" ;;
  snap)
    mkdir -p "$ASSETS/captures"
    extra+=(-snapview native -snapshot_directory "$ASSETS/captures") ;;
  fresh)
    # A stale battery-backed RAM state can wedge Magic Cap (e.g. after
    # playing at the IDT monitor prompt, which shares the OS heap RAM).
    # Move it aside so the next boot is a clean cold start + calibration.
    if [[ -d "$ASSETS/runtime/manual/nvram/datarover840" ]]; then
      backup="$ASSETS/runtime/manual-backup-$(date +%Y%m%dT%H%M%S)"
      mkdir -p "$backup"
      mv "$ASSETS/runtime/manual/nvram/datarover840" "$backup/"
      echo "previous state moved to $backup"
    fi ;;
  --)         ;;  # only pass-through args follow
  *)          fail "unknown mode '$mode' (use: run | monitor | both | snap | fresh | -- <args>)" ;;
esac

# Collect pass-through args after a `--`.
if [[ "${1:-}" == "--" ]]; then
  shift
  extra+=("$@")
elif [[ "${2:-}" == "--" ]]; then
  shift 2
  extra+=("$@")
fi

# Keep MAME runtime state (config, battery-backed NVRAM) in the assets
# tree so nothing lands in the Git checkout.
state="$ASSETS/runtime/manual"
mkdir -p "$state/cfg" "$state/nvram"

# -ui_active: keep Tab and the other UI keys live despite the emulated
#   terminal keyboard.
# -nokeepaspect: SDL maps the pointer over the whole window, so the pen
#   mapping is only exact when the screen fills the window; keep the
#   window near 3:2 to avoid distortion (docs/mame-bringup.md).
set -x
exec "$BIN" datarover840 \
  -rompath "$ROMPATH" \
  -cfg_directory "$state/cfg" \
  -nvram_directory "$state/nvram" \
  -window -skip_gameinfo \
  -ui_active \
  -nokeepaspect \
  -view "$view" \
  -lightgun_device mouse \
  ${extra[@]+"${extra[@]}"}
