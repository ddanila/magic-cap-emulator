# Status and plan

The emulated machine boots ROM build 3.1.2j to the interactive Magic Cap
workbench. Each implemented subsystem has an automated regression under
[`tools/`](tools/); all but the Linux/X11 Tab-menu touch check are headless.
The full regression list and expected checkpoints are in
[`docs/mame-bringup.md`](docs/mame-bringup.md).

## What works

| Subsystem | Verified behavior | Details |
|---|---|---|
| Boot & serial | IDT monitor reaches an interactive `<IDT>` prompt; both Dino UARTs on MAME RS-232 | [`memory-map.md`](docs/memory-map.md) |
| Betty (SIB ASIC) | Boot reaches `BootCap`; the ROM's own `BettyTest` diagnostic passes | [`betty-registers.md`](docs/betty-registers.md) |
| Display | 480×320 2bpp framebuffer renders splash → welcome → workbench | [`memory-map.md`](docs/memory-map.md) |
| Touch | One absolute pointer device drives X/Y/pen-down through calibration and remains live across MAME Tab-menu round trips | [`mame-bringup.md`](docs/mame-bringup.md) |
| Persistence & power | Battery-backed DRAM + RTC as NVRAM; power-button suspend/wake across a retained-RAM relaunch | [`power-wake.md`](docs/power-wake.md) |
| Battery & supply inputs | Both ADC channels answer within the ROM's own calibration thresholds, so the spurious backup-battery warning is gone; battery levels, AC adapter and battery cover are selectable, and removing the cover raises the IO interrupt the OS services | [`power-wake.md`](docs/power-wake.md#battery-levels) |
| Sound output | ROM's startup tone (unbuffered hold register) plus the buffered SIB sound DMA ring: half/end handlers continuously refill DRAM buffers and complete the OS speaker lifecycle | [`betty-registers.md`](docs/betty-registers.md) |
| Built-in software modem | Continuous 48-word SIB telecom DMA ring drives the ROM's V.32 pump/control/FIR through a TX39 `MADD` | [`builtin-modem.md`](docs/builtin-modem.md) |
| Magic Bus | Address assignment, request-line edges, PIO/DMA transfers, checksummed peripheral discovery, and a bidirectional `ATKB` Set-2 keyboard accessory | [`memory-map.md`](docs/memory-map.md#magic-bus) |
| TX39 extensions | `MADD`/`MADDU` implemented for the modem DSP's 792 uses | [`tx39-cpu.md`](docs/tx39-cpu.md) |
| PC Cards | Both linear slots with CIS and insertion signaling; Magic Cap's original EtherLink driver configures the reusable 3Com 3C589 core, completes TCP through rootless libslirp, renders deterministic local HTTP, and carries a host-proxied local TLS request | [`mame-bringup.md`](docs/mame-bringup.md), [`etherlink.md`](docs/etherlink.md), [`oldvcr-tls.md`](docs/oldvcr-tls.md) |
| PCLink | Recovered WinPCLink protocol installs archived packages into live Magic Cap, including the 452K Apollo browser from the Old VCR field report | [`pclink.md`](docs/pclink.md), [`oldvcr-tls.md`](docs/oldvcr-tls.md) |
| IrDA / Beam | Two fresh communicators discover each other by name over SIR, the sender selects the receiver, and a name card arrives in the receiver's Inbox | [`irda.md`](docs/irda.md) |
| Modem → PPP | PC Card modem completes Hayes + live Slirp LCP/IPCP; Web Browser 4.0 fetches and renders deterministic local HTTP | [`modem.md`](docs/modem.md) |
| Variants | `datarover840` / `840f` (writable flash) / `840j` / `840d` (1998-04-07 development ROM) all build and verify; `840d` also boots to the workbench | [`rom-layout.md`](docs/rom-layout.md), [`dev-rom.md`](docs/dev-rom.md) |
| OS self-tests | The development ROM's real Command-T runs through the OS scheduler: all 16 basic suites complete and return with no complaint. Fourteen individually driven unit tests — including `CheckROMPristineTable`, the OS verifying its own ROM — remain useful focused checks | [`dev-rom.md`](docs/dev-rom.md) |

## Remaining work

- **Act on the power-supply outputs.** The charger and the LCD, IR, sound,
  MagicBus and modem Vcc rails round-trip through `mfioDataOutput`, so the OS
  reads back what it wrote, but nothing acts on them: LCD power off does not
  blank the display and the charger does not recharge the modelled cells. The
  DataRover guide explicitly promises live AC charging and a configurable
  1–60 minute idle shutoff, making both concrete acceptance targets
  ([`power-wake.md`](docs/power-wake.md#outputs-the-os-writes)).

- **Complete the storage-card lifecycle.** Both Glacier slots currently pass
  raw common-memory, CIS, insertion and write/readback checks. They do not yet
  cover Magic Cap's blank-card setup/format flow, storage-card battery levels,
  live Option-insert erase/setup, package translation, or backup/restore
  ([`user-guide.md`](docs/user-guide.md#acceptance-backlog-derived-from-the-guide)).

- **Complete PC Card modem save states.** The main driver state is registered,
  but the optional modem card's 16550 registers and receive queue are not.
  A restore therefore deliberately pulses card detect and makes Magic Cap
  re-enumerate the card instead of resuming an in-flight modem session
  ([`mame-bringup.md`](docs/mame-bringup.md#known-gap-save-state-coverage)).

- **Resolve the modified browser's native HTTPS Rule.** Deterministic
  proxy-assisted TLS is covered through Browser 3.5's HTTP proxy Rule 13 and
  Crypto Ancienne's documented `-u` HTTP-to-TLS upgrade: the exact decrypted
  local request and rendered page are both required. Rule 14 can be configured
  visibly, but an `https://` URL still opens the destination directly instead
  of the configured proxy. That browser-level dispatch gap is now isolated
  from EtherLink, TCP, proxy and TLS interoperability
  ([`oldvcr-tls.md`](docs/oldvcr-tls.md#deterministic-https-regression)).

- **Model the built-in modem's external line side.** The SIB telecom DMA ring
  and ROM V.32 DSP execute, but the external DAA, carrier acquisition, and a
  remote modem are not represented. Tone/pulse dialing and fax are documented
  product behaviors beyond that boundary. This is separate from the working
  PC Card PPP path ([`builtin-modem.md`](docs/builtin-modem.md)).

- **Implement microphone/audio input.** Speaker playback covers the
  unbuffered hold register and continuously serviced sound-TX DMA ring, but
  `sibSoundRxStart`, the sound-RX DMA enable/interrupt path, and a host
  microphone source are not implemented. The guide's email sound-stamp
  record/stop/play workflow is the intended end-to-end acceptance test
  ([`betty-registers.md`](docs/betty-registers.md#buffered-sib-sound-dma)).

- **Expand Magic Bus beyond one keyboard.** The product connector supports
  PCs, external modems, keyboards and other accessories, commonly in a daisy
  chain. The driver currently presents either one `ATKB` device or an empty
  bus; multiple addressable devices and other accessory classes remain
  uncovered ([`memory-map.md`](docs/memory-map.md#magic-bus)).

- **Improve hardware fidelity beyond observed ROM needs.** TX39 configuration
  and cache-lock registers and many Dino registers are behavioral shadows.
  The verified boot, power, sound, telecom, and peripheral paths act on the
  bits the ROM uses, but cycle-exact cache locking, clock division, and a
  complete functional Dino are not claimed
  ([`tx39-cpu.md`](docs/tx39-cpu.md), [`memory-map.md`](docs/memory-map.md)).

The machine stays `MACHINE_NOT_WORKING` while these hardware gaps remain.
The product-level coverage matrix and smaller UI acceptance backlog derived
from Icras's guide are in [`user-guide.md`](docs/user-guide.md). The
independent real-device, SDK, PCLink, Ethernet and proxy-browser evidence from
Kaiser's 2023 field report is mapped separately in
[`oldvcr-tls.md`](docs/oldvcr-tls.md).
