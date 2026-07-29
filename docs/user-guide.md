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

The 8 MiB linear-card model now distinguishes product storage from unrelated
raw images. An erased or previously formatted storage image exposes the
documented `GMMC` tuple; it starts as `BLNK`, while a common-memory `MCAP`
header changes the derived tuple to `RAMC` and supplies its metacluster offset.
Other raw images retain the generic SRAM CIS, so the 840F flasher and the
low-level card-window regression are not misidentified as erasable storage.

`tools/storage_card_regression.py` runs three isolated boots against one
disposable card file. The first captures Magic Cap's setup and naming windows
and formats the erased image. The second proves the on-disk header and
`RAMC`/`0xb0` tuple survive a fresh process. The third inserts the formatted
card while holding Option, captures the erase/setup and naming windows, and
requires the regenerated header stamp to differ. It also cycles slot 1 through
Good, Low and Dead and requires BVD2/BVD1 pin codes `11`, `01` and `00`
respectively. Both slots expose the same selector under **Machine
Configuration**. Two more processes select **new items go here**, create and
draw on a Notebook page, leave Notebook to commit it, then reinsert the same
card and require the reopened page to be pixel-identical. Package translation
and backup/restore are automated by their separate focused regressions below.

The same FAQ distinguishes self-hosted custom cards from ordinary PCMCIA
cards. An ordinary card is claimed by a separately installed `CardServer`
subclass in `iCardServers` after its `CanHandleCard()` accepts the insertion.
This matches the already working package-supplied EtherLink path and provides
the contract for future archived WaveLAN, NE2000 and wireless-card work.

## Requirements and coverage

| Area | User-guide contract | Current evidence | Coverage |
|---|---|---|---|
| Navigation and touch | Desk, Hallway and Downtown form the main geography; Controls can rerun touch-screen alignment (pp. 3–12, 51–53) | Fresh-boot automation reaches the Desk and calibrates three points; Tab-menu touch round trips pass | Core path covered; user-requested realignment is not automated |
| Speaker and controls | Volume and system sounds are configurable; a sound stamp can record, stop and play microphone audio (pp. 23, 51–53, 67–68) | Startup beep and continuously buffered sound-TX DMA pass; direct sound-RX DMA captures tone/silence; the real Stamper UI records, stops, drains its SIB command, and plays the captured audio into a WAV | Principal sound-stamp workflow covered; volume/control UI choices are not separate tests |
| Infrared Beam | Any displayed card or page can be beamed; discovery fills the recipient, multiple peers require selection, and Magic Cap 3.1 does not interoperate with earlier versions (pp. 78–79, 133) | Two fresh 3.1 peers discover by owner name, select Bob and transfer Alice's name card | Principal workflow covered; other object types and old-version rejection are not separate tests |
| Web access | Internet Center provider settings drive dial-up Web access and downloaded-page handling (pp. 105–120, 165–174) | PC Card modem completes Hayes/PPP and Web Browser 4.0 fetches a deterministic local page; the same browser selects a provider whose `home` location uses `PPP dialup`, completes V.32/LAPM, LCP/IPCP and dynamic TCP, sends `GET / HTTP/1.0`, receives `HTTP/1.0 200 OK`, renders the deterministic body, and completes an orderly TCP close | PC Card PPP fetch and built-in deterministic HTTP render covered; a general built-in-modem host bridge remains |
| Telephone and built-in modem | The built-in fax modem uses a telephone line, presents receive-fax/answer choices, and can fax visible pages through Magic lamp → Fax (pp. 76–78, 94–95, 135–163, 216) | ROM V.32/fax code and continuous DMA execute; one held ring opens Phone Status; **receive fax** starts live-call `AnswerModem`, both fax HDLC directions, the Receiving fax window and non-silent PCM; a clocked two-DataRover run creates/selects a recipient, dials `5551212`, sustains sender/receiver image data, then relaunches the receiver and opens the new In-box fax, its one-page stationery and rendered page; paired generic V.32 roles complete LAPM SABME/UA; Web Browser's real `PPP dialup` path dials `555-1212`, completes LCP/IPCP, exchanges IPv4/TCP through the answer ROM, and renders an HTTP response | Internal ROM/DSP, digital DAA, V.32/LAPM, both fax UIs, dialing, normal Telephone dialing, remote PCM transport, complete received-fax delivery and Internet Center's deterministic HTTP handoff covered; a general host bridge remains |
| Storeroom and packages | Packages can be moved, unpacked, sent, backed up and restored through Storeroom (pp. 179–206) | PCLink installs a real package; Storeroom creates a card-resident built-in backup and restores it in a fresh process | Package installation and full built-in backup/restore covered |
| Storage cards | A card inserted while off is offered for setup after power-on; Option-insert while running can erase/setup it; Magic Cap displays card battery state and can translate older packages (pp. 183–198, 210–215) | Erased `BLNK` setup/naming, persistent `RAMC` remount, live Option-insert reformat, Good/Low/Dead BVD states, a card-backed Notebook object, and built-in backup/restore pass across process boundaries; `Translation.pkg` copies an authentic 1.x `new items` package into Built-in storage and exposes its Notebook page without source writes | Covered |
| Power | Main and backup batteries are displayed; AC operation recharges the main cell; automatic shutoff defaults to five minutes and is adjustable from 1–60 minutes, optionally while plugged in (pp. 209–211) | Battery ADC levels, AC/cover inputs, retained-RAM suspend/wake, LCD/Magic Bus rail effects and charger-driven main-ADC rise pass; the real Power Controls show 5, clamp at 1/60, and govern plugged-in `SLEE`/VCC-off shutdown | Covered |
| Magic Bus | The connector supports PCs, external keyboards, external modems and other accessories, commonly daisy-chained (p. 217) | One checksummed bidirectional `ATKB` keyboard is discovered, rediscovered after bus reinitialization, and exchanges Set-2/LED traffic | Single keyboard covered; multiple devices, chaining and other accessory classes are not |
| Persistence and privacy | Storage-card backup/restore and power-on password confirmation protect retained information (pp. 196–198, 207–208) | Battery-backed DRAM and RTC survive a two-process power cycle; a card backup restores retained RAM and reaches the success dialog | Hardware retention and backup/restore covered; password UI is not automated |

## Acceptance backlog derived from the guide

These are product-level tests, ordered by how directly they close known
hardware gaps:

1. **Magic Bus topology.** Replace the single optional endpoint with an
   addressable collection, prove two descriptors on one bus, and cover another
   documented class such as an external modem or PC interface.
2. **Smaller UI contracts.** Add focused checks for Controls-initiated touch
   realignment, volume changes, password-on-wake and beaming a notebook page.

This backlog complements the hardware-oriented list in
[`PLAN.md`](../PLAN.md#remaining-work). It should not expand the driver
with guessed behavior: each item still starts by tracing the relevant ROM path
and SDK symbols.
