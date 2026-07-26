#!/usr/bin/env bash
#
# Mirror every hobbyist-hosted research input into the persistent asset tree.
#
# The ROM images, SDK bundle, packages, and reference manuals live on personal
# and community hosts that can disappear. This script makes the local mirror
# reproducible and checkable, so bring-up never depends on a live download.
# It is idempotent: an asset whose checksum already matches is left alone.
#
# Nothing it writes belongs in Git; everything lands under $ASSETS.
#
# Usage:
#   tools/fetch_assets.sh                 # fetch/verify everything except the SDK
#   tools/fetch_assets.sh all             # ... including the 176 MiB SDK bundle
#   tools/fetch_assets.sh sdk             # only the SDK bundle and its Apollo files
#   tools/fetch_assets.sh rom japan       # only the named groups
#   tools/fetch_assets.sh --verify        # check the existing mirror, download nothing
#
# Groups: rom japan packages wintools manual sdk macsdk
#
# Environment overrides:
#   ASSETS      persistent asset tree (default: ~/fun/magic-cap-assets)
#
# Extracted-artifact checksums are the documented ones (docs/rom-layout.md,
# docs/pclink.md, docs/tx39-cpu.md) and are hard assertions. Container archive
# checksums are only what these hosts served locally: a re-zipped container
# with an identical payload is a warning, not a failure.

set -euo pipefail

ASSETS="${ASSETS:-$HOME/fun/magic-cap-assets}"
PACKAGES_URL="https://joshcarter.com/magic_cap/packages"

verify_only=0
groups=()
for arg in "$@"; do
  case "$arg" in
    --verify|--verify-only) verify_only=1 ;;
    all)                    groups+=(rom japan packages wintools manual sdk macsdk) ;;
    rom|japan|packages|wintools|manual|sdk|macsdk) groups+=("$arg") ;;
    -h|--help)              sed -n '3,28p' "$0"; exit 0 ;;
    *) printf 'error: unknown argument %s (see --help)\n' "$arg" >&2; exit 2 ;;
  esac
done
# Default: everything cheap. The SDK bundle is 176 MiB, so it is opt-in.
[[ ${#groups[@]} -gt 0 ]] || groups=(rom japan packages wintools manual)

failures=0
warnings=0

want() {  # want <group> -> is this group selected?
  local g
  for g in "${groups[@]}"; do [[ "$g" == "$1" ]] && return 0; done
  return 1
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1 && sha256sum --version >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1        # coreutils
  else
    shasum -a 256 "$1" | cut -d' ' -f1    # macOS / perl
  fi
}

matches() {  # matches <path> <sha256>
  [[ -f "$1" ]] || return 1
  [[ "$(sha256_of "$1")" == "$2" ]]
}

# assert <path> <sha256> — a mismatch is a failure.
assert() {
  if matches "$1" "$2"; then
    printf '  ok        %s\n' "${1#"$ASSETS"/}"
  elif [[ -f "$1" ]]; then
    printf '  MISMATCH  %s\n            have %s\n            want %s\n' \
      "${1#"$ASSETS"/}" "$(sha256_of "$1")" "$2" >&2
    failures=$((failures + 1))
  else
    printf '  MISSING   %s\n' "${1#"$ASSETS"/}" >&2
    failures=$((failures + 1))
  fi
}

# note <path> <sha256> — a mismatch is only a warning (see header).
note() {
  if matches "$1" "$2"; then
    printf '  ok        %s\n' "${1#"$ASSETS"/}"
  elif [[ -f "$1" ]]; then
    printf '  differs   %s (container re-packed upstream; payload is what matters)\n' \
      "${1#"$ASSETS"/}"
    warnings=$((warnings + 1))
  else
    printf '  MISSING   %s\n' "${1#"$ASSETS"/}" >&2
    failures=$((failures + 1))
  fi
}

# fetch <url> <dest> [<sha256>] — download unless the checksum already matches.
fetch() {
  local url="$1" dest="$2" sha="${3:-}"
  if [[ -n "$sha" ]] && matches "$dest" "$sha"; then
    printf '  have      %s\n' "${dest#"$ASSETS"/}"
    return 0
  fi
  # In verify mode the following assert/note reports and counts the problem.
  (( verify_only )) && return 0
  mkdir -p "$(dirname "$dest")"
  printf '  fetch     %s\n' "${dest#"$ASSETS"/}"
  curl --fail --location --silent --show-error --output "$dest.part" "$url"
  mv -f "$dest.part" "$dest"
}

if (( verify_only )); then
  printf 'Verifying mirror under %s (no downloads)\n' "$ASSETS"
else
  printf 'Mirroring research inputs into %s\n' "$ASSETS"
fi

# --- USA mask ROM and 840F flasher card ------------------------------------
if want rom; then
  echo 'USA ROM and 840F flasher card'
  fetch "$PACKAGES_URL/MagicCap-USA.zip" "$ASSETS/roms/MagicCap-USA.zip" \
    2d41de3989227151f8a3c89a068b19fc95c48c124d47869bd53c3bcd98129f23
  rom="$ASSETS/roms/datarover840/magiccap-usa.image"
  rom_sha=94785cb334f14eac00ed200af014c35972b4f25694103bc6a49b3afa280a6f1b
  if ! matches "$rom" "$rom_sha" && (( ! verify_only )); then
    mkdir -p "$ASSETS/roms/datarover840"
    unzip -j -o "$ASSETS/roms/MagicCap-USA.zip" MagicCap-USA.image \
      -d "$ASSETS/roms/datarover840" >/dev/null
    mv -f "$ASSETS/roms/datarover840/MagicCap-USA.image" "$rom"
  fi
  note "$ASSETS/roms/MagicCap-USA.zip" \
    2d41de3989227151f8a3c89a068b19fc95c48c124d47869bd53c3bcd98129f23
  assert "$rom" "$rom_sha"

  fetch "$PACKAGES_URL/DataRover840FRomFlasher.gz" \
    "$ASSETS/roms/DataRover840FRomFlasher.gz" \
    b2d5a3c1b99151f4fa952c65bb4d8b21e872de3dec2a0ee91730a256c070a9e2
  flasher="$ASSETS/roms/DataRover840FRomFlasher"
  flasher_sha=16fe122872e295ee03be4be1322013a6e504997d9996997c8c7b0997ec65c5f7
  if ! matches "$flasher" "$flasher_sha" && (( ! verify_only )); then
    gzip --decompress --keep --force "$ASSETS/roms/DataRover840FRomFlasher.gz"
  fi
  assert "$flasher" "$flasher_sha"
fi

# --- Japan ROM (datarover840j) ---------------------------------------------
if want japan; then
  echo 'Japan ROM'
  fetch "$PACKAGES_URL/MagicCap-Japan.zip" "$ASSETS/roms/MagicCap-Japan.zip" \
    d103d18f2f2b016a015745119df5007b87b931fcde48ff2cd11dca5a2bd7e617
  jrom="$ASSETS/roms/datarover840j/magiccap-japan.image"
  jrom_sha=897d7320703c6ca432fb0982a7570d8c7c3ce60695b8103a947b37ec8f30e0e4
  if ! matches "$jrom" "$jrom_sha" && (( ! verify_only )); then
    mkdir -p "$ASSETS/roms/datarover840j"
    unzip -j -o "$ASSETS/roms/MagicCap-Japan.zip" MagicCap-Japan.image \
      -d "$ASSETS/roms/datarover840j" >/dev/null
    mv -f "$ASSETS/roms/datarover840j/MagicCap-Japan.image" "$jrom"
  fi
  assert "$ASSETS/roms/MagicCap-Japan.zip" \
    d103d18f2f2b016a015745119df5007b87b931fcde48ff2cd11dca5a2bd7e617
  assert "$jrom" "$jrom_sha"
fi

# --- Archived packages (PCLink and browser inputs) -------------------------
if want packages; then
  echo 'Archived packages'
  while read -r sha name; do
    fetch "$PACKAGES_URL/$name" "$ASSETS/packages/$name" "$sha"
    assert "$ASSETS/packages/$name" "$sha"
  done <<'PKGS'
069456eceb30a6fff044462d6a9f55e0140c4cc099968287f843478fc8fcc339 DvorakKeyboard.pkg
39b6b3de9da3e52467f2c5c1ec81adad6d47af5a20085929ce7f74fe78a44502 Betteris.pkg
b401b0f82beff0d945a4eb0361c8cf02aa16ec3fd79a3267edd46248c92bc706 WebBrowser40.mc2
beb0de0cdb51207534c280c88402ec11972dd7dfce11cd08514adc92c2f6f406 MagicJavaScript.pkg
PKGS
fi

# --- Original Windows host tools (behavioral references) -------------------
if want wintools; then
  echo 'Windows reference tools'
  fetch "$PACKAGES_URL/WinPCLink.zip" "$ASSETS/tools/WinPCLink.zip" \
    13204d0f2cba4f904c0b7638c5396c3bf7cf13477e395040528ec4d782ff6200
  pclink_exe="$ASSETS/tools/winpclink/WinPCLink/WinPcLink.exe"
  pclink_sha=866199ca80ca1c1c51fea49dfb02db2b016ad4fc9f8ad8a7300246115b5bfed4
  if ! matches "$pclink_exe" "$pclink_sha" && (( ! verify_only )); then
    mkdir -p "$ASSETS/tools/winpclink"
    unzip -o "$ASSETS/tools/WinPCLink.zip" -d "$ASSETS/tools/winpclink" >/dev/null
  fi
  assert "$ASSETS/tools/WinPCLink.zip" \
    13204d0f2cba4f904c0b7638c5396c3bf7cf13477e395040528ec4d782ff6200
  assert "$pclink_exe" "$pclink_sha"

  fetch "$PACKAGES_URL/WinDownload.zip" "$ASSETS/tools/WinDownload.zip" \
    874abb2f969756671dffbd97660720e92dc14d0a59d4797d3d420aac82240c58
  dl_exe="$ASSETS/tools/windownload/WinDownload.exe"
  dl_sha=da69f8d0ddc5309e47a63316dbe1c7cd52c1dab3722fa4d8b2072c7c3d369eeb
  if ! matches "$dl_exe" "$dl_sha" && (( ! verify_only )); then
    mkdir -p "$ASSETS/tools/windownload"
    unzip -j -o "$ASSETS/tools/WinDownload.zip" -d "$ASSETS/tools/windownload" >/dev/null
  fi
  note "$ASSETS/tools/WinDownload.zip" \
    874abb2f969756671dffbd97660720e92dc14d0a59d4797d3d420aac82240c58
  assert "$dl_exe" "$dl_sha"
fi

# --- Product and CPU reference manuals ------------------------------------
if want manual; then
  echo 'Reference manuals'
  user_guide_sha=20010cefe051b94fde9f8fa16273a6c33547e85cc061fb5e80625296fa21f22a
  fetch \
    https://bitsavers.trailing-edge.com/pdf/generalMagic/Using_Magic_Cap.pdf \
    "$ASSETS/docs/Using_Magic_Cap.pdf" "$user_guide_sha"
  assert "$ASSETS/docs/Using_Magic_Cap.pdf" "$user_guide_sha"

  tx39_manual_sha=cf9fd5fa551814bb681fefd9576114ba8d8b8e8d7bb1903e943dee546405ad38
  fetch \
    https://www.bitsavers.org/components/toshiba/_dataSheet/TMPR39xx-um_199507.pdf \
    "$ASSETS/docs/TMPR39xx-um_199507.pdf" "$tx39_manual_sha"
  assert "$ASSETS/docs/TMPR39xx-um_199507.pdf" "$tx39_manual_sha"
fi

# --- Icras SDK 3.2: the unstripped Apollo ELF and platform headers ---------
if want sdk; then
  echo 'Icras SDK 3.2 (Apollo ELF, debugger database, platform headers)'
  bundle="$ASSETS/sdk/Datarover840.zip"
  bundle_sha=4455c41a681006b6cac791c639014b87739c2513212078708cd9efaaf554d839
  fetch https://archive.org/download/DataRover840/Datarover840.zip \
    "$bundle" "$bundle_sha"
  assert "$bundle" "$bundle_sha"

  apollo="$ASSETS/sdk/extracted/Program_Files/debug/apollo"
  elf_sha=4a97da908226f3f6a803b82aa2a69a32a601f6c985c682f28b28502a91802962
  if ! matches "$apollo/MagicCAP-USA" "$elf_sha" && (( ! verify_only )); then
    if ! command -v unshield >/dev/null 2>&1; then
      printf 'error: unshield not found (brew install unshield / apt install unshield)\n' >&2
      exit 1
    fi
    mkdir -p "$ASSETS/sdk/installer"
    # All three InstallShield parts must sit together for extraction.
    unzip -j -o "$bundle" \
      'DataRover 840/Developer/IcrasSoftwareDevelopmentKit3.2/data1.cab' \
      'DataRover 840/Developer/IcrasSoftwareDevelopmentKit3.2/data1.hdr' \
      'DataRover 840/Developer/IcrasSoftwareDevelopmentKit3.2/data2.cab' \
      -d "$ASSETS/sdk/installer" >/dev/null
    unshield -d "$ASSETS/sdk/extracted" x "$ASSETS/sdk/installer/data1.cab" \
      MagicCAP-USA MagicCap-USA.debug.x MagicCAP-USA.image \
      Dino.asm.h Dino.h Gen2MFS.asm.h Gen2MFS.h Hardware.asm.h Hardware.h \
      MemoryMapDino.asm.h MipsCPU.h Platform.h PlatformDefines.h >/dev/null
  fi
  assert "$apollo/MagicCAP-USA" "$elf_sha"
  assert "$apollo/MagicCap-USA.debug.x" \
    09aabfbdfd8d977260bb71c89b64357575478799d97813cbc7c2582118d63de8
  # The SDK's own ROM image is byte-identical to the separately published one.
  assert "$apollo/MagicCAP-USA.image" \
    94785cb334f14eac00ed200af014c35972b4f25694103bc6a49b3afa280a6f1b
fi

# --- Mac Rosemary SDK: the 1998-04-07 development ROMs ---------------------
if want macsdk; then
  echo 'Mac Rosemary SDK (development ROMs, debugger databases)'
  sit="$ASSETS/sdk-mac/magicdeveloper.sit"
  sit_sha=1ea81ba35c2ade992bac2b0348cbc4f7443f3f006a1480d66c04848c8de89e76
  # Macintosh Garden publishes MD5 0e3385d40ba3c9c069b1af99a430fe7a for this
  # file; the SHA-256 above is of that same 74,208,013-byte download.
  fetch https://old.mac.gdn/apps/magicdeveloper.sit "$sit" "$sit_sha"
  assert "$sit" "$sit_sha"

  dbg="$ASSETS/sdk-mac/extracted/MagicDeveloper/MagicDeveloper/Debugger"
  dev_usa_sha=fee43c259942baa0fe893be583e41800935a2e06a5dbdeb7ab83739a88aa00f8
  if ! matches "$dbg/Apollo/MagicCap-USA.image" "$dev_usa_sha" \
     && (( ! verify_only )); then
    if ! command -v unar >/dev/null 2>&1; then
      printf 'error: unar not found (brew install unar / apt install unar)\n' >&2
      exit 1
    fi
    # StuffIt 5; unar is the only reliable extractor. ~200 MiB unpacked.
    unar -quiet -force-overwrite -output-directory "$ASSETS/sdk-mac/extracted" \
      "$sit" >/dev/null
  fi
  assert "$dbg/Apollo/MagicCap-USA.image" "$dev_usa_sha"
  assert "$dbg/Apollo/MagicCap-Japan.image" \
    ed5e5f0307d44f3023328f7e9b83b44682643b8ef5768d78c4e7627ed625d8bc
  assert "$dbg/Sputnik/MagicCap-USA.image" \
    1a7f5eb74e6a83e4721e797fc21f200567ebffe6576930e10894d8debb2eaa37
  assert "$dbg/Sputnik/MagicCap-Japan.image" \
    e940fcae73b657ac6437a6c8cdaf2f5d6e89e95d408e751260c0a801f8bc1ae1
  assert "$dbg/Apollo/MagicCap-USA" \
    c3b3fe4a38c1a3a7c666f579639398601e48e62bc26437b27e3fc1b020e3b034

  # Arrange the Apollo development image under the MAME set name so
  # `datarover840d` verifies and boots from the normal rompath.
  devrom="$ASSETS/roms/datarover840d/magiccap-usa-dev.image"
  if ! matches "$devrom" "$dev_usa_sha" && (( ! verify_only )); then
    mkdir -p "$ASSETS/roms/datarover840d"
    cp -f "$dbg/Apollo/MagicCap-USA.image" "$devrom"
  fi
  assert "$devrom" "$dev_usa_sha"
fi

echo
if (( failures )); then
  printf '%d asset(s) missing or wrong.\n' "$failures" >&2
  exit 1
fi
if (( warnings )); then
  printf 'Mirror complete, %d container archive(s) differ from the recorded copy.\n' \
    "$warnings"
else
  echo 'Mirror complete and fully verified.'
fi
