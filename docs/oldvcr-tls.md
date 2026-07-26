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
  `~/fun/magic-cap-assets/articles/bringing-tls-to-magic-cap-datarover-2026-07-26.mhtml`

The live Blogger page includes changing navigation and sidebar content, so a
checksum of a fresh HTML request would not identify the article body reliably.
The checksum above identifies only the saved MHTML snapshot. Preserve a copy
saved by a browser with:

```sh
article_download="$HOME/Downloads/Old Vintage Computing Research_ Bringing TLS to the Magic Cap DataRover.mhtml"
article_mirror="$HOME/fun/magic-cap-assets/articles/bringing-tls-to-magic-cap-datarover-2026-07-26.mhtml"
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
| A 3Com EtherLink III PC Card loads Web pages on the physical DataRover. | Add PC Card Ethernet as a hardware target. The current driver offers linear SRAM and a serial modem, and the current MAME tree has no 3C589 PC Card device. |
| The browser uses Magic Internet Kit TCP streams. A modified browser sends absolute HTTP or HTTPS URLs to a host proxy, which performs TLS. | Extend the existing PPP/HTTP acceptance with an optional proxy test. TLS belongs on the host; it does not imply new DataRover crypto hardware. |
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
magic_cap_assets="$HOME/fun/magic-cap-assets"
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

Install it through the recovered protocol with:

```sh
python3 tools/pclink_regression.py \
  --package "$HOME/fun/magic-cap-assets/packages/WebBrowser-MIPS-USA.pkg" \
  --workdir \
    "$HOME/fun/magic-cap-assets/runtime/pclink-tls-browser"
```

On 2026-07-26 the emulator accepted the full package stream, returned the
final `Pong`, disconnected, and displayed a 454K **Web Browser** object in the
Storeroom without an alert. The regression also counted calls to the ROM's
`MagicBus_HandleMagicBusFailure` and observed zero. The earlier alert was
traced to the harness selecting **None** for the Magic Bus accessory: this ROM
counts unanswered address assignment as a peripheral failure, and the long
transfer merely gave it enough time to cross the alert threshold. Keeping the
modeled `ATKB` keyboard present makes both the small and sustained PCLink
acceptances clean without patching ROM control flow or dismissing alerts.
The retained clean evidence is under:

```text
~/fun/magic-cap-assets/runtime/pclink-tls-browser-clean/20260726T183612/
```

## Acceptance targets derived from the article

1. **Clean sustained PCLink transfer — covered.** The checksum-pinned
   461,876-byte browser installs with final `Pong` and `GBye`, appears as a
   454K Storeroom object, and finishes with zero ROM Magic Bus failures and no
   attached-device alert. The harness treats a missing or nonzero failure
   count as a regression.
2. **PC Card Ethernet.** Identify the exact EtherLink III revision/CIS from
   stronger evidence before choosing a device. Model the likely 3C589-family
   attribute and I/O spaces, configuration option and IREQ; then require the
   ROM to create its `WCPack_EtherServer`, perform ARP and fetch a local HTTP
   page. The Apollo ELF already contains `Hardware/WCPack/Ethernet.cpp`,
   `WCPack_EtherServer` and its small ARP implementation, which independently
   supports pursuing this path.
3. **Deterministic HTTPS through a host proxy.** Install the modified browser
   into a copied provider-configured NVRAM tree, keep the same MAME process
   alive, serve a small local HTTPS page, and expose a pinned
   [Crypto Ancienne][cryanc] `carl -p` through a loopback-only superserver.
   Configure the browser's TLS proxy Rule and require the exact local request
   plus rendered result. Do not depend on a public Web site.
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
certificates. Any future harness must bind only an isolated loopback/test
network and describe the result as protocol interoperability, not secure
modern browsing.

[article]: https://oldvcr.blogspot.com/2023/01/bringing-tls-to-magic-cap-datarover.html
[cryanc]: https://github.com/classilla/cryanc
