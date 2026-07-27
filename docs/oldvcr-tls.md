# Old VCR DataRover TLS field report

Cameron Kaiser's 2023 article
[*Bringing TLS to the Magic Cap DataRover*][article] is a useful companion to
the product guide. It documents software development in the Rosemary SDK and
then repeats the package-transfer and network workflows on a physical
DataRover 840. It is therefore field evidence and a source of acceptance
tests, not a register or circuit specification.

## Source and preserved snapshot

- Author: Cameron Kaiser
- Published: 2023-01-22
- Live article: [Old Vintage Computing Research][article]
- Frozen copy: browser-generated MHTML saved 2026-07-26, including the article
  images
- SHA-256:
  `7eff057a148015f9baec4f576a7fff0aaa877a518034d4eaf45571ce5ed71e5e`
- Canonical local path:
  `$MAGIC_CAP_ASSETS/articles/bringing-tls-to-magic-cap-datarover-2026-07-26.mhtml`

The live Blogger page includes changing navigation and sidebar content, so a
checksum of a fresh HTML request would not identify the article body reliably.
The checksum above identifies only the saved MHTML snapshot. Preserve a copy
saved by a browser with:

```sh
article_download="$HOME/Downloads/Old Vintage Computing Research_ Bringing TLS to the Magic Cap DataRover.mhtml"
article_mirror="$MAGIC_CAP_ASSETS/articles/bringing-tls-to-magic-cap-datarover-2026-07-26.mhtml"
mkdir -p "$(dirname "$article_mirror")"
cp "$article_download" "$article_mirror"
echo '7eff057a148015f9baec4f576a7fff0aaa877a518034d4eaf45571ce5ed71e5e  bringing-tls-to-magic-cap-datarover-2026-07-26.mhtml' \
  | (cd "$(dirname "$article_mirror")" && sha256sum --check)
```

The snapshot and linked software remain outside Git. This repository records
their provenance, checksums and conclusions rather than redistributing them.

## What the article establishes

The following observations are relevant to emulation:

| Observation | How this project adopts it |
|---|---|
| The physical DataRover is a 36.864 MHz Apollo/Rosemary MIPS machine with 4 KiB instruction and 1 KiB data caches. | This agrees with the ROM monitor and current TX39 model. |
| The article reports only about 768 KiB of application working memory; the ROM monitor independently reports 4 MiB of physical RAM. | Keep the hardware at 4 MiB. Browser out-of-memory behavior is an OS/application constraint, not evidence that the emulator needs more RAM. |
| Rosemary builds PowerPC packages for its Mac simulator and MIPS packages for the Apollo device; the device build uses the SDK's GCC 2.7.1 cross-compiler. | This corroborates the PowerPC-simulator/MIPS-device distinction and makes the article's MIPS browser a real Apollo package input. |
| `WinPCLink` installs a 452K package through the Storeroom computer in roughly five to six minutes on physical hardware. | Use the same 461,876-byte package as a sustained PCLink regression, while treating the reported timing as a rough field benchmark rather than a cycle-accuracy requirement. |
| A 3Com EtherLink III PC Card loads Web pages on the physical DataRover. | The archived Magic Cap driver accepts 3Com `0x0101` / `0x?589`. A reusable MAME 3C589 core plus rootless libslirp now completes ARP/TCP and renders deterministic local HTTP through the real driver. |
| The browser uses Magic Internet Kit TCP streams. A modified browser sends absolute HTTP or HTTPS URLs to a host proxy, which performs TLS. | A deterministic EtherLink regression now sends an absolute URL to a loopback-only Crypto Ancienne superserver, requires the exact decrypted local HTTPS request, and captures the rendered result. TLS belongs on the host; it does not imply new DataRover crypto hardware. |
| Under a large page, the physical browser can exhaust memory. A warm start and garbage collection preserve the page canvas well enough to continue. | Preserve physical memory limits and add an eventual memory-pressure/warm-start check; do not “fix” the workload by enlarging default RAM. |
| The original Rosemary simulator can bypass its simulated modem and tunnel TCP through Mac Open Transport after a dummy provider is configured. It forgets state at exit unless the user backs up to a virtual memory card. | Treat this as simulator behavior, not DataRover hardware behavior. It reinforces the value of our separate NVRAM and storage-card acceptance tests. |

The article also gives valuable SDK/object-model context: `ObjectID` handles,
indexicals, `.cdef`/`.odef` definitions, generated attributes, Rules, and
`NewPreferredTCPStream`. Those details help interpret package and simulator
dumps, but they do not override the ROM and SDK symbols used for hardware
reverse engineering.

## Reproducible MIPS package

The article links Kaiser's modified Apollo browser from Floodgap:

```sh
magic_cap_assets="${MAGIC_CAP_ASSETS:-$PWD/../magic-cap-assets}"
mkdir -p "$magic_cap_assets/packages"
curl --fail --location \
  --output "$magic_cap_assets/packages/WebBrowser-MIPS-USA.pkg" \
  gopher://gopher.floodgap.com/9/archive/magic-cap-3/WebBrowser-MIPS-USA.pkg
echo 'a72d591b270f66a7b9f8a4df67b39aa52ed39af22932256413d47f7bdcb5ea71  WebBrowser-MIPS-USA.pkg' \
  | (cd "$magic_cap_assets/packages" && sha256sum --check)
```

The file is 461,876 bytes (451.1 KiB, the article's rounded “452K”) and its
strings contain the documented Web proxy and Crypto Ancienne-compatible TLS
proxy Rules. It is a modified Icras binary distributed by its author with a
legal and no-warranty disclaimer. For that reason it is an optional research
input, not part of `tools/fetch_assets.sh all`.

The Apollo package has a one-byte dispatch defect on the Go To path. Its
connection code compares the scheme returned by `ParseURL` with the C literal
`https:`. The parsed scheme is `https`, so the comparison fails and the code
falls through to a direct connection using the HTTP default port. An outgoing
frame trace made the failure concrete: `https://localhost` attempted
`127.0.0.1:80` instead of the Rule 14 endpoint at `10.0.2.2:8765`.

`tools/patch_tls_browser.py` changes only that fixed-size C literal from
`https:` to `https`. It validates the complete source checksum, expected size,
unique byte context, and offset before writing a separate corrected package;
neither package is committed:

```sh
python3 tools/patch_tls_browser.py \
  "$magic_cap_assets/packages/WebBrowser-MIPS-USA.pkg" \
  "$magic_cap_assets/packages/WebBrowser-MIPS-USA-HTTPS.pkg"
echo 'bdc6960304ead7948712f26298960b7320bf8b29d60b7e6137bfddd7632fce1e  WebBrowser-MIPS-USA-HTTPS.pkg' \
  | (cd "$magic_cap_assets/packages" && sha256sum --check)
```

Install it through the recovered protocol with:

```sh
python3 tools/pclink_regression.py \
  --package "$MAGIC_CAP_ASSETS/packages/WebBrowser-MIPS-USA-HTTPS.pkg" \
  --workdir \
    "$MAGIC_CAP_ASSETS/runtime/pclink-tls-browser"
```

On 2026-07-27 the emulator accepted the full corrected package stream,
returned the final `Pong`, sent `GBye`, and displayed a 454K **Web Browser**
object in the Storeroom without an alert. The regression also counted calls
to the ROM's `MagicBus_HandleMagicBusFailure` and observed zero. The modeled
`ATKB` keyboard must stay present for this: the ROM counts unanswered Magic
Bus address assignment as a peripheral failure, and a transfer this long
gives an empty bus enough time to cross the attached-device alert threshold.

## Deterministic HTTPS regression

Keep Crypto Ancienne outside either Git checkout, as a sibling of this
repository — the default the harness expects (`--carl` overrides it):

```sh
git clone https://github.com/classilla/cryanc.git ../cryanc
git -C ../cryanc checkout \
  a1572fbfda3a829e210fc3535a22ebd719417419
cd ../cryanc
gcc -O3 -Wno-error=incompatible-pointer-types -o carl carl.c
./carl -v
```

The tested checkout is pinned above. Its documented one-file GCC build needs
the narrow `-Wno-error=incompatible-pointer-types` compatibility flag with
current GCC because the historical source uses an old-style signal-handler
declaration. `build-essential` and `openssl` are the only additional Ubuntu
packages used by this test. The `carl` binary, generated certificate and
private key remain outside Git.

The installed package identifies itself as **Web Browser 3.5** (its HTTP
user-agent says `MCWB3.5.1`). In its Rules desk accessory, Rule 13 is the Web
proxy rule and Rule 14 is the TLS proxy rule. Configure Rule 14 for the Slirp
host alias `10.0.2.2` and TCP port `8765`, close the Rules window cleanly, and
save that provider/browser state as an NVRAM source.

Run the self-contained acceptance with:

```sh
python3 tools/https_proxy_regression.py \
  --nvram-source "$MAGIC_CAP_ASSETS/runtime/https-rule-config/nvram"
```

The harness owns all test services and tears them down: a loopback-only
superserver on host port 8765, one `carl -Npt` child for the intended browser
request, and a run-local TLS server on port 9443 with a newly generated
self-signed certificate. The guest enters the canonical
`https://localhost`, which the corrected browser sends through Rule 14 as an
absolute HTTPS proxy request. Because an unprivileged test cannot own port
443, the superserver records the original guest bytes and maps only this
isolated `localhost` request to port 9443 before invoking `carl`.

A pass requires all of the following:

- Magic Cap connects through its native EtherLink III driver and libslirp;
- `browser-proxy-requests.bin` contains the original absolute proxy request;
- the TLS endpoint independently decrypts the exact `GET / HTTP/1.0`;
- `snapshots/etherlink-https-result.png` exists after the browser renders
  **Crypto Ancienne works** and **Magic Cap reached deterministic local HTTPS
  through EtherLink III.**

Browser 3.5 makes a couple of later internal requests to `10.0.2.2:8080`.
They are retained verbatim in the browser-request capture and answered with
the same deterministic body so the old UI settles, but only the independently
decrypted `localhost` request creates the TLS success marker. This prevents an
internal browser probe from producing a false pass.

Two optional modes preserve related diagnostics:

- `--http-upgrade` enters `http://localhost`, uses HTTP Rule 13, and asks
  `carl -u` to upgrade the request to TLS. This is the earlier workaround;
  native Rule 14 is now the default acceptance.
- `--explicit-url-port` types port 9443 into the guest URL. Natural keyboard
  input currently produces a stray semicolon in the absolute proxy target
  (`localhost:9443;/`), which Crypto Ancienne correctly rejects. The default
  implicit-port mode avoids conflating that text-entry issue with TLS.

## Acceptance targets derived from the article

1. **Clean sustained PCLink transfer — covered.** The checksum-pinned
   461,876-byte browser installs with final `Pong` and `GBye`, appears as a
   454K Storeroom object, and finishes with zero ROM Magic Bus failures and no
   attached-device alert. The harness treats a missing or nonzero failure
   count as a regression.
2. **PC Card Ethernet and local HTTP — covered.** The original 1998 Magic Cap
   `EtherLinkIII.pkg` driver accepts
   manufacturer `0x0101` and the complete `0x?589` revision family. The MAME
   fork now provides a reusable 3C589 PC Card with the 3Com-published CIS,
   attribute/configuration registers, windowed I/O, EEPROM MAC, PIO FIFOs,
   TX-status stack, IREQ, a loopback-only UDP frame backend and an optional
   libslirp backend. WCPack performs the demand-loaded reset and
   register-window setup. With byte-lane masks preserved through PIO Data
   Read, the native driver completes ARP and TCP, sends the canonical absolute
   HTTP/1.0 request, and renders the deterministic local page. See
   [`etherlink.md`](etherlink.md) for provenance, disassembly evidence and the
   repeatable harness.
3. **Deterministic HTTPS through the native Rule 14 — covered.** The corrected
   browser, native EtherLink driver and rootless
   libslirp send the request to a loopback-only superserver. Pinned
   [Crypto Ancienne][cryanc] performs TLS against a run-local HTTPS endpoint;
   the harness requires both the exact decrypted request and rendered result.
   The captured guest request begins `GET https://localhost/ HTTP/1.0`, the
   TLS endpoint independently receives `GET / HTTP/1.0`, and the rendered
   result is captured before the test passes.
4. **Memory-pressure warm start.** Use a deterministic page large enough to
   reach the physical browser's transient-memory limit, follow the normal
   warm-start/garbage-collection path, and verify that the persistent page
   canvas remains navigable without increasing the machine's 4 MiB RAM.
5. **Simulator comparison.** When the Rosemary simulator is available, compare
   the same package's Rules, object layout and small page rendering. Keep its
   Open Transport tunnel and virtual-card persistence out of device-hardware
   conclusions.

Crypto Ancienne's own documentation says that `carl -p` reads a full proxy
request from standard input, does not bind a listening socket, and has no
access control. It also warns that `carl` does not currently validate
certificates. The regression therefore supplies a loopback-only superserver
and local test endpoint. It proves protocol interoperability, not secure
modern browsing or certificate validation.

[article]: https://oldvcr.blogspot.com/2023/01/bringing-tls-to-magic-cap-datarover.html
[cryanc]: https://github.com/classilla/cryanc
