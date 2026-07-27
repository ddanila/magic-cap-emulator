# DataRover user-guide acceptance map

The primary product-level specification for this project is Icras's
*Using Magic Cap: The User's Guide for the DataRover 840*. It documents Magic
Cap 3.1 on the exact target machine, unlike the older Magic Cap 1.x books and
simulators. Register behavior still comes from the ROM, SDK headers and TX39
documentation; this guide defines what a user should be able to accomplish
once those registers are modelled.

## Source and checksum

- Title: *Using Magic Cap: The User's Guide for the DataRover 840*
- Publisher: Icras, Inc.
- Software: Magic Cap 3.1
- PDF: 234 pages, generated 2000-04-04
- Source:
  [`Using_Magic_Cap.pdf`](https://bitsavers.trailing-edge.com/pdf/generalMagic/Using_Magic_Cap.pdf)
- SHA-256:
  `20010cefe051b94fde9f8fa16273a6c33547e85cc061fb5e80625296fa21f22a`
- Download destination:
  `$MAGIC_CAP_ASSETS/docs/Using_Magic_Cap.pdf`

Fetch or verify it with:

```sh
tools/fetch_assets.sh manual
tools/fetch_assets.sh --verify manual
```

The PDF is copyrighted and remains outside Git. Page numbers below are the
printed page numbers, not PDF page indices.

## How to use this source

The guide is authoritative about visible workflow, default settings and
supported accessory classes. It is not a circuit diagram and does not identify
Dino or Betty registers. A documented workflow therefore becomes an
acceptance target, but implementation still follows ROM-observed behavior
rather than inventing hardware behind the UI.

Likewise, an untested workflow is not automatically a driver failure. Much of
Magic Cap is portable OS code and already runs if the required hardware
boundary exists. The table distinguishes proven emulator behavior from
product behavior that still needs an end-to-end check.

Kaiser's later real-device and SDK
[*Bringing TLS to the Magic Cap DataRover*](oldvcr-tls.md) report complements
this source. In particular, it turns PC Card Ethernet, a sustained PCLink
transfer and proxy-assisted HTTPS into concrete field-derived checks. Those
claims remain separate from Icras's product contract.

## Developer storage-card specification

The product guide describes what the user sees; General Magic's preserved
[`PC Cards` FAQ](http://www.datarover.com/Develop/MagicCap/Docs/FAQ/Q+A_PCCards.html)
supplies the missing software format. Attribute-memory CIS tuple `0xA0`
contains eight big-endian words: magic `GMMC`, version `0x00010001`, a
four-character card type, common-memory offset of the metacluster, unique ID,
modification date, modification time and CRC. The documented construction
path sets the final three words to zero. Relevant types are `BLNK` for an
unformatted RAM card and `RAMC` for a formatted one; the complete type table
and field layout are in
[`developer-archives.md`](developer-archives.md#storage-cards-an-exact-os-visible-contract).

This is not yet implemented. The current 8 MiB linear-card model returns a
generic SRAM CIS and an all-`0xff` common-memory image. It proves Glacier,
PCMCIA and persistence plumbing, but does not supply the documented Magic Cap
card metadata. Adding a `BLNK` tuple is therefore the first controlled
lifecycle experiment; the real ROM flow must confirm whether that omission is
the reason setup does not start before the resulting metacluster is treated
as understood.

The same FAQ distinguishes self-hosted custom cards from ordinary PCMCIA
cards. An ordinary card is claimed by a separately installed `CardServer`
subclass in `iCardServers` after its `CanHandleCard()` accepts the insertion.
This matches the already working package-supplied EtherLink path and provides
the contract for future archived WaveLAN, NE2000 and wireless-card work.

## Requirements and coverage

| Area | User-guide contract | Current evidence | Coverage |
|---|---|---|---|
| Navigation and touch | Desk, Hallway and Downtown form the main geography; Controls can rerun touch-screen alignment (pp. 3–12, 51–53) | Fresh-boot automation reaches the Desk and calibrates three points; Tab-menu touch round trips pass | Core path covered; user-requested realignment is not automated |
| Speaker and controls | Volume and system sounds are configurable; a sound stamp can record, stop and play microphone audio (pp. 23, 51–53, 67–68) | Startup beep and continuously buffered sound-TX DMA playback pass | Output covered; microphone and sound-RX DMA are missing |
| Infrared Beam | Any displayed card or page can be beamed; discovery fills the recipient, multiple peers require selection, and Magic Cap 3.1 does not interoperate with earlier versions (pp. 78–79, 133) | Two fresh 3.1 peers discover by owner name, select Bob and transfer Alice's name card | Principal workflow covered; other object types and old-version rejection are not separate tests |
| Web access | Internet Center provider settings drive dial-up Web access and downloaded-page handling (pp. 105–120, 165–174) | PC Card modem completes Hayes/PPP and Web Browser 4.0 fetches a deterministic local page | Covered for the PC Card PPP route |
| Telephone and built-in modem | The built-in fax modem uses a telephone line and supports tone/pulse configuration (pp. 135–163, 216) | ROM V.32 code and continuous telecom DMA execute | Internal ROM/DSP boundary covered; DAA, dial tone, carrier, remote peer, pulse dialing and fax are missing |
| Storeroom and packages | Packages can be moved, unpacked, sent, backed up and restored through Storeroom (pp. 179–206) | PCLink installs a real package into built-in storage | Package installation covered; storage-card backup/restore is not |
| Storage cards | A card inserted while off is offered for setup after power-on; Option-insert while running can erase/setup it; Magic Cap displays card battery state and can translate older packages (pp. 183–198, 210–215) | Both Glacier slots expose common memory, generic CIS, writes and insertion edges; the developer FAQ now defines the missing Magic tuple | Electrical/raw path covered and next format step specified; setup, formatting, battery and backup lifecycle is not |
| Power | Main and backup batteries are displayed; AC operation recharges the main cell; automatic shutoff defaults to five minutes and is adjustable from 1–60 minutes, optionally while plugged in (pp. 209–211) | Battery ADC levels, AC/cover inputs and retained-RAM suspend/wake pass | Inputs and explicit wake covered; charging, time-varying capacity and the user-configured idle policy are not |
| Magic Bus | The connector supports PCs, external keyboards, external modems and other accessories, commonly daisy-chained (p. 217) | One checksummed bidirectional `ATKB` keyboard is discovered and exchanges Set-2/LED traffic | Single keyboard covered; multiple devices, chaining and other accessory classes are not |
| Persistence and privacy | Storage-card backup/restore and power-on password confirmation protect retained information (pp. 196–198, 207–208) | Battery-backed DRAM and RTC survive a two-process power cycle | Hardware retention covered; backup/restore and password UI are not automated |

## Acceptance backlog derived from the guide

These are product-level tests, ordered by how directly they close known
hardware gaps:

1. **Sound-stamp record/play.** Open an email, add the general-drawer sound
   stamp, record from a deterministic host source, stop, play it back and
   verify the captured samples. This is the clearest acceptance test for
   Betty/Dino sound-RX DMA and microphone input.
2. **Storage-card lifecycle.** Add CIS tuple `0xA0` with `GMMC`, version
   `0x00010001`, type `BLNK` and a stable unique ID to a disposable card.
   Power-cycle with it inserted, complete Magic Cap's setup/name flow, capture
   any resulting tuple and metacluster changes, write an object, relaunch,
   then back up and restore built-in information. Add selectable BVD battery
   levels and test live Option-insert erase plus `Translation.pkg` separately.
3. **Power Controls policy and charging.** Verify the five-minute default,
   1–60 minute adjustment and “even when plugged in” choice. With AC attached
   and charger enable asserted, advance a modelled main-battery level and
   confirm the Power window changes.
4. **Built-in line side.** Drive the real Telephone/Internet Center setup,
   cover tone and pulse dialing, provide DAA/ring/carrier behavior, connect a
   peer and only then extend to fax.
5. **Magic Bus topology.** Replace the single optional endpoint with an
   addressable collection, prove two descriptors on one bus, and cover another
   documented class such as an external modem or PC interface.
6. **Smaller UI contracts.** Add focused checks for Controls-initiated touch
   realignment, volume changes, password-on-wake and beaming a notebook page.

This backlog complements the hardware-oriented list in
[`PLAN.md`](../PLAN.md#remaining-work). It should not expand the driver
with guessed behavior: each item still starts by tracing the relevant ROM path
and SDK symbols.
