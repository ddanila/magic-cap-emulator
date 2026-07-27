# 3Com EtherLink III PC Card

Cameron Kaiser's physical DataRover report identifies a **3Com EtherLink III
PC Card/PCMCIA** as the working Ethernet adapter, but not its exact revision.
The original Magic Cap driver closes that gap: Josh Carter's Rosemary Software
Archive includes `EtherLinkIII.pkg`, built 1998-09-20, and its native MIPS code
accepts 3Com manufacturer ID `0x0101` plus product ID `0x?589`.

The product comparison deliberately masks the upper revision nibble:

```text
manufacturer == 0x0101
(product & 0x0fff) == 0x0589
```

This identifies the 3C589 family without guessing whether Kaiser's physical
card was the original or B revision. The package also imports
`genmagic.com/WCPackPCCardInterface1` and
`genmagic.com/WCPackEthernetInterface1`; Ethernet support is therefore a
separate Wireless Connectivity Pack driver above the ROM's generic
`WCPack_EtherServer`, not a hidden ROM network-card implementation.

## Preserved inputs

Keep downloaded binaries and reference material outside Git:

```sh
assets="$HOME/fun/magic-cap-assets"
mkdir -p "$assets/packages" "$assets/re/3c589"

curl --fail --location \
  --output "$assets/packages/EtherLinkIII.pkg" \
  https://joshcarter.com/magic_cap/packages/EtherLinkIII.pkg
echo 'c0b23f24a91e7b03f4adf1a356dc4356f4091284a424119bbdd9d89f72279b34  EtherLinkIII.pkg' \
  | (cd "$assets/packages" && sha256sum --check)

curl --fail --location \
  --output "$assets/re/3c589/3c5x9b-technical-reference.pdf" \
  https://www.ardent-tool.com/NIC/3c5x9b_Technical_Reference.pdf
echo '7f05d1245f58aaae575a32b7df63c8f3f978a396ac74bbed1cbb8b1270758681  3c5x9b-technical-reference.pdf' \
  | (cd "$assets/re/3c589" && sha256sum --check)
```

The package is 65,624 bytes. The second file is 3Com's 1994 *EtherLink III
Parallel Tasking ISA, EISA, Micro Channel and PCMCIA Adapter Drivers Technical
Reference*, 142 pages. It publishes the 3C589/3C589B CIS byte layouts,
configuration registers, eight 16-byte register windows, EEPROM format,
command/status bits, FIFO protocol and PCMCIA interrupt behavior.

For an independent executable reference, the final Linux `3c589_cs` driver
before its removal remains in the official kernel history. The preserved
v6.15 source has SHA-256
`0ce61c6dff8ec4d105517ca9491b00cf23fe2076d886eef7d4002e5d83d127cc`
at `~/fun/magic-cap-assets/re/3c589/linux-3c589_cs.c`.

## Current implementation

The MAME fork now has a reusable `3Com EtherLink III 3C589 PC Card` device,
selectable as `-pccard1 3c589` or `-pccard2 3c589`. Its first implementation
covers:

- the published 3C589 CIS, including MANFID, LAN FUNCID, CONFIG and the
  16-byte I/O CFTABLE entry;
- PC Card COR/status and active-low IREQ signaling;
- the eight EtherLink III register windows and command/status port;
- EEPROM-backed MAC-address discovery;
- station address and receive-filter setup;
- PIO transmit and receive FIFOs;
- MAME's standard 10 Mbit network interface.

The archived driver installs cleanly through PCLink. A clean retained artifact
with the 64K `EtherLink Driver` object and zero ROM Magic Bus failures is:

```text
~/fun/magic-cap-assets/runtime/pclink-etherlink-driver/20260726T190412/
```

Passive insertion makes Magic Cap parse the CIS through the MANFID, CONFIG and
CFTABLE entries. The driver is demand-loaded by the Wireless Connectivity
Pack when an application asks for Ethernet; simply opening its Storeroom
package scene does not initialize the card.

The driver and modified Web Browser can coexist in one retained NVRAM tree.
The second transfer starts directly in the Storeroom and leaves both the 64K
`EtherLink Driver` and 454K `Web Browser` objects installed:

```sh
python3 tools/pclink_regression.py \
  --package "$HOME/fun/magic-cap-assets/packages/WebBrowser-MIPS-USA.pkg" \
  --nvram-source \
    "$HOME/fun/magic-cap-assets/runtime/pclink-etherlink-driver/20260726T190412/nvram" \
  --storeroom-source \
  --workdir \
    "$HOME/fun/magic-cap-assets/runtime/pclink-etherlink-browser"
```

The retained passing layered transfer is under
`~/fun/magic-cap-assets/runtime/pclink-etherlink-browser/20260726T192140/`.

## Guest configuration and first claim

Magic Cap does not select a newly added connection merely because it exists.
The complete UI path found from the product's own Provider Setup is:

1. create a provider and add the package-supplied **EtherLink LAN** connection;
2. enter a static card address (the retained run uses `10.0.2.15`);
3. on the provider's **locations** tab, add the **home** dialing location;
4. change `home` from the default PPP dial-up row to
   `EtherLink LAN 10.0.2.15`.

This location mapping is required even though Ethernet does not dial. Without
it the browser opens the normal “Setting up dialing” flow and never claims
the card.

With the mapping in place, a browser request gives an exact demand-load trace:

```text
COR 0x80 -> 0x00 -> 0x41
SelectWindow 0
TxReset; RxReset
SetIntrEnb; SetStatusEnb; SetRxFilter
SelectWindow 2 -> 1 -> 4 -> 1
```

Glacier multiplexes attribute and I/O cycles into the same DataRover host
window. After COR selects the I/O configuration, offsets 0–15 therefore have
to reach the 3C589 register block rather than the first CIS bytes. Routing
those post-COR cycles correctly removes the driver's “Cannot initialize the
EtherLink card” alert. The browser then remains in
**Communicating — Contacting 1**, which is the expected boundary with no host
network peer attached.

The preserved provider setup, save states, screenshots and trace are under:

```text
~/fun/magic-cap-assets/runtime/etherlink-provider-wizard/20260726T201000/
```

## Rootless frame transports

The MAME fork also has a `udp` network provider for deterministic tests that
cannot create a TAP interface. It binds only to loopback and carries exactly
one raw Ethernet frame per UDP datagram:

```text
MAME receive: 127.0.0.1:58100
peer receive: 127.0.0.1:58101
```

`MAME_UDP_NET_LOCAL_PORT` and `MAME_UDP_NET_REMOTE_PORT` override the two
ports. `-networkprovider udp -listnetwork` exposes interface 0; select that
interface for `:pccard1:3c589` in MAME's Network Devices configuration and
launch the card with `-pccard1 3c589`.

The matching `tools/etherlink_peer.py` is a privilege-free isolated LAN
endpoint. It answers ARP for `10.0.2.2` and `10.0.2.3`, maps DNS A queries to
`10.0.2.2`, and contains a small checksummed TCP/HTTP endpoint on ports 80 and
8080:

```sh
python3 tools/etherlink_peer.py \
  --trace "$HOME/fun/magic-cap-assets/runtime/etherlink/peer-trace.txt" \
  --http-requests \
    "$HOME/fun/magic-cap-assets/runtime/etherlink/http-requests.txt"
```

The fixed response delay prevents an impossible zero-latency reply from
racing the initiating guest stack. Neither this provider nor the peer opens a
host network interface or grants the guest Internet access.

For the complete host TCP path, Linux builds auto-detect `libslirp` through
`pkg-config` and include a second rootless provider:

```sh
sudo apt-get install pkg-config libslirp-dev
cd "$HOME/fun/mame"
make SUBTARGET=datarover SOURCES=src/mame/skeleton/datarover.cpp \
  NO_USE_PORTAUDIO=1 -j"$(nproc)"
./datarover -networkprovider slirp -listnetwork
```

Interface zero is `libslirp user network (10.0.2.0/24, host 10.0.2.2)`.
It gives the static guest `10.0.2.15` a conventional user-mode IPv4 network,
maps host loopback to `10.0.2.2`, and requires neither root nor TAP.
`USE_SLIRP=0` explicitly omits the optional module; `USE_SLIRP=1` requires
the development package.

Two 3C589 details were required before frames became real:

- the first transmit-preamble word is length plus control bits; bit 15 requests
  a successful-transmit interrupt, while only bits 10–0 are the frame length;
- successful requested completions enter the byte-wide TX-status stack.
  Magic Cap reads and pops that entry before acknowledging the interrupt.
  Treating every send as an unpoppable TX Complete left IREQ asserted and
  prevented a later RX Complete edge.

Interrupt Requested is also a distinct software-generated status source, not
an indication that the physical IREQ line is active. The emulation now keeps
it separate from Interrupt Latch and implements the published acknowledgement
rules for TX Available, RX Early and RX Complete.

Receive access width is the third subtle contract. Disassembly of the archived
driver's native MIPS routine at package offset `0x1904` shows this exact loop:

1. select Window 1 and read the 11-bit length from RX Status;
2. call the WCPack 8-bit Card I/O accessor once per byte;
3. store that returned byte and repeat exactly `length` times;
4. issue RX Discard.

An emulation that discards the caller's byte-lane mask at the window-register
layer and reads the FIFO as a full 16 bits pops two bytes per driver
iteration: the interrupt and final discard look correct, but the protocol
stack sees every other byte followed by zeros. Passing the real mask into PIO
Data Read makes byte reads pop one byte and word reads pop two, as the 3Com
reference specifies. RX Complete is raised after MAME's simulated wire
transfer, and RX Bytes decreases as data is read.

With those corrections, a cold NVRAM run completes:

```text
ARP: 10.0.2.15 asks for 10.0.2.2
ARP: 10.0.2.2 replies
TCP: 10.0.2.15:1024 sends SYN to 10.0.2.2:8080
TCP: libslirp returns a checksummed SYN-ACK
TCP: Magic Cap acknowledges it and sends an HTTP/1.0 GET
HTTP: the browser renders "EtherLink III works"
```

The deterministic regression owns the host server, copied NVRAM, exact-request
marker, browser automation, screenshot and teardown in one process:

```sh
python3 tools/etherlink_regression.py \
  --nvram-source \
    "$HOME/fun/magic-cap-assets/runtime/etherlink-provider-wizard/20260726T201000/nvram"
```

It requires a provider-configured tree containing the installed browser and
EtherLink driver. A pass requires the canonical absolute request
`GET http://10.0.2.2:8080/ HTTP/1.0` and
`snapshots/etherlink-http-result.png`; generated state and captures remain
under `~/fun/magic-cap-assets/runtime/etherlink-regression/`. The clean
2026-07-26 pass is retained under
`20260726T193221.829181Z-2890929/`; it rendered the heading
**EtherLink III works** and the text **Magic Cap reached deterministic local
HTTP.**

Do not use a save-state restore as the live-network test setup. MAME currently
rejects loading a state after a network backend creates its anonymous polling
timer. Boot a copied, provider-configured NVRAM tree and keep the same MAME
process alive through the request instead.

## Proxy-assisted local HTTPS

The same native EtherLink path now carries a deterministic host-proxied TLS
request. The modified Web Browser 3.5 connects its HTTP proxy Rule 13 to
Slirp's `10.0.2.2:8765`; a loopback-only superserver invokes pinned Crypto
Ancienne `carl -Nptu`, which upgrades the request and negotiates TLS with a
run-local HTTPS endpoint. Run it with:

```sh
python3 tools/https_proxy_regression.py \
  --nvram-source \
    "$HOME/fun/magic-cap-assets/runtime/https-rule-config/20260727T050000-http-upgrade/nvram"
```

The 2026-07-26 pass is retained under
`~/fun/magic-cap-assets/runtime/etherlink-https-regression/20260726T213340.799847Z-2926968/`.
It requires the independently decrypted `GET / HTTP/1.0` and a final screen
showing **Crypto Ancienne works**. This proves the frame, ARP, TCP, proxy and
TLS path without adding crypto hardware to the DataRover.

Browser 3.5's native HTTPS Rule 14 remains a narrower known gap: it can be
enabled and configured, but an `https://` URL still connects directly rather
than reaching the proxy. `tools/https_proxy_regression.py --https-rule`
reproduces that behavior. Build instructions, security boundaries and the
browser's explicit-port input quirk are in
[`oldvcr-tls.md`](oldvcr-tls.md#deterministic-https-regression).

## Acceptance sequence

The gates, in order, are:

1. **Covered:** install the modified Web Browser on top of the driver-equipped
   NVRAM. The
   PCLink harness supports this reproducibly with `--nvram-source
   .../nvram --storeroom-source`.
2. **Covered:** request a page so WCPack claims the card. The retained trace
   contains the reset/COR sequence, Window 0 setup, station-address
   programming and transitions through operating Windows 1, 2 and 4.
3. **Covered:** the loopback-only UDP provider and deterministic peer carry
   isolated raw frames; libslirp supplies a mature host TCP stack without
   privileges.
4. **Covered:** Magic Cap completes ARP and TCP, emits the canonical absolute
   HTTP request, consumes the response through the byte-accurate 3C589 FIFO,
   and renders the deterministic page.
5. **Covered:** the loopback-only Crypto Ancienne superserver negotiates TLS
   with a deterministic local endpoint, which observes the exact request, and
   Magic Cap renders the response.

Both plain HTTP and proxy-assisted local TLS are therefore covered over the
original EtherLink driver. The remaining browser network target is native
HTTPS Rule 14 dispatch, not basic Ethernet, TCP or TLS interoperability.
