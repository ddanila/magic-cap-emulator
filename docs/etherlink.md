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
Reference*, 11 scanned pages. It publishes the 3C589/3C589B CIS byte layouts,
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

## Acceptance sequence

The gates, in order, are:

1. **Covered:** install the modified Web Browser on top of the driver-equipped
   NVRAM. The
   PCLink harness supports this reproducibly with `--nvram-source
   .../nvram --storeroom-source`.
2. **Covered:** request a page so WCPack claims the card. The retained trace
   contains the reset/COR sequence, Window 0 setup, station-address
   programming and transitions through operating Windows 1, 2 and 4.
3. **Next:** attach a deterministic, privilege-free Ethernet peer and require
   the ROM's real ARP request and reply, followed by a small local HTTP
   response.
4. Connect that proved frame path to a loopback-only HTTP/TLS proxy acceptance
   described in [`oldvcr-tls.md`](oldvcr-tls.md).

Until the ARP and HTTP gates pass, the device is a guest-initialized hardware
core, not yet a claimed working DataRover Ethernet path.
