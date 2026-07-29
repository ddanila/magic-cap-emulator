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
| Power outputs, charging & policy | MFIO LCD power blanks scanout without destroying the framebuffer; active-high Magic Bus Vcc-off removes and rediscovers its peripheral; AC plus charger enable advances the main-battery ADC; the real Power Controls clamp 1–60 minutes and automatic AC idle shutoff follows its checkbox | [`power-wake.md`](docs/power-wake.md#outputs-the-os-writes) |
| Sound I/O | ROM's startup tone (unbuffered hold register), buffered SIB sound-TX/RX DMA, host or deterministic microphone input, and Magic Cap's sound-stamp record/stop/play workflow | [`betty-registers.md`](docs/betty-registers.md) |
| Built-in software modem | Continuous 48-word SIB telecom DMA drives the ROM's V.32 and fax paths through TX39 DSP extensions; the Betty DAA hookswitch/line input and Dino ring detector are modelled; a held ring opens Phone Status, whose real **receive fax** action reaches `AnswerModem`, fax HDLC and non-silent PCM; the visible outbound Fax workflow creates a recipient, renders a screen and dials `5551212`; a clocked two-DataRover exchange runs both product fax roles through retained In-box storage, stationery and a reopened rendered page; the deterministic exchange also supplies dial tone and decodes DTMF/pulse dialing; the visible Telephone output decodes as `580`; Web Browser selects an Internet Center provider mapped to `PPP dialup`, dials through the built-in modem, completes V.32/LAPM and LCP/IPCP, and sends IPv4/TCP from `10.0.2.15` to `10.0.2.2:8080` | [`builtin-modem.md`](docs/builtin-modem.md) |
| Magic Bus | Address assignment and reinitialization, request-line edges, PIO/DMA transfers, checksummed peripheral discovery, and a bidirectional `ATKB` Set-2 keyboard accessory | [`memory-map.md`](docs/memory-map.md#magic-bus) |
| TX39 extensions | `MADD`/`MADDU` plus three-operand `MULT`/`MULTU` implemented; all four verify `rd`, `HI`, and `LO`, covering 792 multiply/add and 89 destination-writing multiply uses in the SDK ELF | [`tx39-cpu.md`](docs/tx39-cpu.md) |
| PC Cards | Both linear slots with CIS and insertion signaling; Magic Cap's original EtherLink driver configures the reusable 3Com 3C589 core, completes TCP through rootless libslirp, renders deterministic local HTTP, carries Browser 3.5's native HTTPS Rule through a host TLS proxy, and can browse public HTTPS sites through a guarded loopback launcher | [`mame-bringup.md`](docs/mame-bringup.md), [`etherlink.md`](docs/etherlink.md), [`oldvcr-tls.md`](docs/oldvcr-tls.md) |
| Storage cards | Blank setup, persistent remount, live Option-insert reformat, battery states, card-backed objects, full built-in-storage backup/restore, and source-preserving translation of a real 1.x `new items` package into 3.1 Built-in storage | [`developer-archives.md`](docs/developer-archives.md#storage-cards-an-exact-os-visible-contract), [`mame-bringup.md`](docs/mame-bringup.md) |
| PCLink | Recovered WinPCLink protocol installs archived packages into live Magic Cap, including the 452K Apollo browser from the Old VCR field report | [`pclink.md`](docs/pclink.md), [`oldvcr-tls.md`](docs/oldvcr-tls.md) |
| IrDA / Beam | Two fresh communicators discover each other by name over SIR, the sender selects the receiver, and a name card arrives in the receiver's Inbox | [`irda.md`](docs/irda.md) |
| Modem → PPP | PC Card modem completes Hayes + live Slirp LCP/IPCP; Web Browser 4.0 fetches and renders deterministic local HTTP; its 16550 registers, partially consumed receive FIFO and IREQ state survive save/load without a false card-removal edge | [`modem.md`](docs/modem.md) |
| Variants | `datarover840` / `840f` (writable flash) / `840j` / `840d` (1998-04-07 development ROM) all build and verify; `840d` also boots to the workbench | [`rom-layout.md`](docs/rom-layout.md), [`dev-rom.md`](docs/dev-rom.md) |
| OS self-tests | The development ROM's real Command-T runs through the OS scheduler: all 16 basic suites complete and return with no complaint. Fourteen individually driven unit tests — including `CheckROMPristineTable`, the OS verifying its own ROM — remain useful focused checks | [`dev-rom.md`](docs/dev-rom.md) |

## Remaining work

- **Model the built-in modem's external line side.** The SIB telecom DMA ring
  and ROM V.32 DSP execute, and the digital DAA boundary now has connected,
  off-hook and ring-level/edge behavior, and its deterministic exchange now
  supplies a direct-DMA-verified North American dial tone and decodes
  tone- and pulse-dialed numbers. The normal Telephone UI/actor path now
  enters `580`, goes off-hook, runs both 48-word DMA rings, generates DTMF
  through the ROM softmodem, and is decoded by the exchange. A verified
  full-duplex PCM bridge now supplies the line transport between two
  independent DataRovers and bounds unequal host scheduling. Exploratory
  paired runs now verify the ROM's exact command-6 options, command-2
  originating/answering roles, active V.32 receive/transmit paths, and
  non-silent PCM in both directions; Dino's 16-bit sample framing is
  confirmed. A held incoming-ring envelope now becomes the detector-edge
  cadence expected by the ROM, reaches both PhoneServer and FaxReceive, and
  opens Phone Status with **receive fax** and **answer**. Selecting **receive
  fax** supplies the live call context to `AnswerModem`, activates the 48-word
  DMA ring, runs the fax modem receive/transmit and both HDLC directions, and
  emits non-silent PCM. Word-aligned data-modem tests also exclude simple
  polarity and direct through 12 dB-attenuated line gain. A matched
  product-level fax originator now also creates a recipient, addresses the
  Desk screen, dials seven decoded digits, initializes fax mode, and starts
  DMA. The combined exchange/bridge mode supplies origin-side dial tone and
  then peer PCM. The maintained paired regression consumes equal-duration
  setup silence, triggers the physical ring after 300,000 caller PCM bytes,
  pauses the caller while the real answer UI reaches modem mode, and then
  OS-pauses whichever emulator leads the shared PCM timeline until its peer
  catches up. Both product roles initialize fax, exchange HDLC, run fax RX/TX,
  sustain more than 64 sender/receiver image callbacks with no ROM protocol
  error, and visibly show `Receiving page 1`. The extended acceptance observes
  an image-helper completion, relaunches copied receiver state, finds the new
  `a fax` In-box row, opens its one-page stationery, and renders the received
  page. Fax transfer is therefore covered end to end. A 96-byte,
  process-clocked paired run now also advances the generic originating and
  answering V.32 ROMs through R2/R3, E and B1 into data mode with matching
  negotiated rates. A call-aware product regression now maps the Internet
  Center provider's `home` location to `PPP dialup`, reopens Web Browser,
  selects that provider, dials `555-1212`, and pairs the product originator
  with the direct-answer ROM. Both sides enter V.32 data mode with matching
  stable rate payloads and live 48-word DMA, switch their framers to HDLC,
  exchange LAPM SABME/UA, and report LAPM connected. Internet Center creates
  its dial-up link, starts `PPPServer`, observes the connection monitor, and
  delivers its first PPP data unit to the answer-side ROM queue. A one-shot
  answer-side peer consumes that unit through `SoftModemRead`, returns a valid
  LCP Configure-Ack plus Configure-Request through `SoftModemWrite`, and
  restores the interrupted CPU state. Magic Cap accepts both LCP packets and
  starts IPCP. The peer NAKs the initial `0.0.0.0` address with `10.0.2.15`
  while requesting `10.0.2.2`; Magic Cap acknowledges the peer and retries
  its own request with the assigned address and VJ options. The peer
  acknowledges that corrected request, after which Magic Cap emits an
  IPv4/TCP SYN from `10.0.2.15:1024` to `10.0.2.2:8080`. The remaining
  line-side target is answering and bridging IP to a host network endpoint.
  The answer probe now dispatches replies from the protocol content of every
  PPP frame in a ROM read, including concatenated retransmissions, and captures
  Magic Cap's TCP RST when a deliberately static SYN-ACK acknowledges the
  wrong randomized client sequence. A real responder must derive that ACK and
  its checksums from each SYN. This is separate from the working PC Card PPP path
  ([`builtin-modem.md`](docs/builtin-modem.md)).

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
