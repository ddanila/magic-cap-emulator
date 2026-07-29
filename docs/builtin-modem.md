# Built-in software modem and V.32 DSP

The DataRover's built-in modem is mostly software. Dino and Betty provide a
clocked bidirectional telecom DMA ring; Magic Cap processes its samples in the
ROM with modem code built for the TX39 CPU. This is distinct from the emulated
PC Card modem described in [`modem.md`](modem.md).

The Apollo ELF from the Icras SDK names the complete path in the shipping USA
3.1.2j ROM:

| Address | ELF symbol / role |
|---|---|
| `0x13c59a08` | `SoftwareModem_OpenModemPort` |
| `0x13c24ea4` | `SoftModemSpawn` |
| `0x13c228e4` | `SibServerStartTelecom` |
| `0x13c23b3c` | private `SibCmdStartTelecom` |
| `0x13c25198` / `0x13c25230` | telecom half/full handlers |
| `0x13e42f20` | `DataModemInit` |
| `0x13e43160` / `0x13e431b4` | `DataModemRcv` / `DataModemXmt` |
| `0x13e431dc` | `DataModemInstallModulation` |
| `0x13e50b10` | V.32 sample pump |
| `0x13e49b40` | V.32 control |
| `0x13e518e0` | `V32ModulatorFIR` |
| `0x13e51974` | first TX39 `MADD` in that FIR |
| `0x13e614f0` | `BlockShortScale`, using TX39 three-operand `MULT` |

The ROM allocates 192-byte receive and transmit buffers, or 48 32-bit words
each, at runtime addresses `0x0000c778` and `0x0000c838`. Its start command
sets the RX and TX enable bits but neither `kSibTelDmaOnceMask` nor
`kSibTelDmaLoopMask`. The absence of both flags therefore means a continuous
two-half ring. Only an explicit `kSibTelDmaOnceMask` transfer stops at the end;
the register-level behavior is covered separately in
[`betty-registers.md`](betty-registers.md#sib-telecom-dma).

## Telephone DAA control

The SDK's unstripped Apollo ELF exposes the digital line-side contract that
sits around the sample stream:

- `DAAOffHook` calls `SibServerTelecomOffHook(..., true)`, which updates
  Betty IOData bit `0x0200`; `DAAOnHook` clears it.
- Betty IOData bit `0x0100` is the independently sampled connected-line
  input. Writes preserve this input while changing the hookswitch output.
- Apollo maps the telephone ring detector to Dino MFIO input pin 0. Its
  positive and negative edges latch bit 0 in interrupt banks 3 and 4.
- `HardwareSetRingEvents` clears those two status bits and gates both
  interrupt enables. The ROM installs `RingInterrupt` for Dino interrupt
  numbers 95 and 127 and uses the edge cadence to qualify a real ring.

The driver now implements this boundary. **Phone line** selects the connected
input. **Incoming telephone ring** is the ringing envelope a user or
automation holds active; while held, MAME produces the external DAA
detector's 20 ms half-period waveform on MFIO0 and latches both edge
interrupts. This distinction matters: one envelope edge cannot pass the ROM's
ring qualifier. `RingInterrupt` groups four detector edges, accepts a
12–100 ms total, requires four valid groups, and then schedules the
one-second completion action. **Telephone exchange** selects either a silent
line or the automatic test exchange.

The direct regression runs without personalized NVRAM:

```sh
python3 tools/telephone_line_regression.py --mame ../mame/datarover
```

It verifies that off-hook/on-hook writes preserve a connected line, and that
asserting and releasing ring change MFIO input bit 0 and latch the correct
interrupt banks.

The product-level incoming regression requires a calibrated retained state:

```sh
python3 tools/incoming_call_regression.py \
  --nvram-source "$MAGIC_CAP_ASSETS/runtime/manual/nvram"
```

It holds the ring envelope once; the harness does not synthesize detector
edges. Breakpoint counters require `RingInterrupt`, its completion callback,
`LandLineCircuit_ContinueRingAction`, client dispatch,
`LandLinePhoneServer_RingAction`, and `FaxReceive_RingAction`. The final
screenshot must differ from the pre-ring scene. The accepted result is the
documented Phone Status window—“You have a telephone call.”—with
**receive fax** and **answer**. This adopts the incoming-call and fax workflow
from *Using Magic Cap*, pp. 94–95 and 156.

The next product action is independently covered:

```sh
python3 tools/fax_receive_regression.py \
  --nvram-source "$MAGIC_CAP_ASSETS/runtime/manual/nvram"
```

This presses **receive fax** in Phone Status. It proves the qualified call
supplies the context that `SoftwareModem_AnswerModem` previously lacked,
checks the softmodem command and line handlers, `FaxModemInit`,
`FaxModemRcv`, `FaxModemXmt`, and both fax HDLC directions. It also requires
the 48-word RX/TX DMA ring, a non-silent external PCM capture, and the visible
Receiving fax progress window. Its line input is intentionally silent, so
this claims the complete local answer startup—not remote negotiation or a
received page.

The matching product-origin baseline starts from the same calibrated state:

```sh
python3 tools/fax_origin_regression.py \
  --nvram-source "$MAGIC_CAP_ASSETS/runtime/manual/nvram"
```

It goes to the Desk, opens Magic lamp → Fax, creates a `Fax Peer` contact with
the default `(650)` prefix and `555-1212`, selects that contact, and sends the
current screen. The test exchange must decode the ROM's sampled output as
`5551212`; breakpoints require `SoftwareModem_ConnectToNumber`,
`SoftwareModem_InitFax`, the softmodem command and line handlers, and telecom
DMA startup. The visible Sending fax window and the 48-word bidirectional DMA
ring are also required. A silent test exchange cannot answer, so this claims
the genuine originating and dialing startup, not carrier or page transfer.

The complete paired product path uses the same calibrated source for two
isolated copies:

```sh
python3 tools/fax_pair_regression.py \
  --nvram-source "$MAGIC_CAP_ASSETS/runtime/manual/nvram"
```

The origin repeats the visible recipient-creation workflow while the answerer
waits at the ordinary Desk. A central-office relay returns equal-duration
silence during dialing instead of queuing stale caller audio at the answerer.
After 300,000 caller PCM bytes it holds the caller, rings the answerer, waits
for the real **receive fax** workflow to emit its first modem samples, then
OS-pauses whichever process leads the call's PCM byte count until its peer
catches up. This prevents a virtual modem from advancing its timers through
bytes still buffered in the host socket. Result markers release clock control
and keep each emulator alive until both have recorded their counters.

Add `--verify-stored-page` for the longer persistence acceptance. It waits for
the image helper to complete, launches a third MAME process from a copy of the
answerer's resulting NVRAM, and follows the user-guide sequence: Desk → In box
→ newest fax → page thumbnail. Tesseract must recognize the `a fax` row,
one-page received-fax stationery, `Fax page 1`, and `555-1212` in the rendered
page:

```sh
python3 tools/fax_pair_regression.py \
  --nvram-source "$MAGIC_CAP_ASSETS/runtime/manual/nvram" \
  --verify-stored-page
```

The automatic exchange supplies the North American continuous dial tone,
350 Hz plus 440 Hz, only while the line is connected and Betty is off-hook.
It uses phase accumulators at Dino's programmed telecom rate, so its samples
and save-state continuation are deterministic. The direct analog regression
programs the real 7,200-sample/s setting and analyzes 2,048 received samples:

```sh
python3 tools/telecom_regression.py --dial-tone
```

A passing capture spans about −7,878..7,878, measures amplitudes near 4,000
at both 350 and 440 Hz, and has negligible 1 kHz off-band energy. This closes
the first analog DAA input.

The other direction feeds every transmitted telecom sample into a 40 ms DTMF
detector. It evaluates the standard four low and four high frequencies,
requires one dominant component from each group, debounces held tones, and
stops dial tone on the first digit. The multi-digit regression transmits
tone/silence blocks for `580` and requires the exchange to decode that exact
number:

```sh
python3 tools/telecom_regression.py --dtmf
```

The detector and its partial block, debounce state and dial-tone state survive
MAME save/load.

For pulse dialing, the same exchange observes Betty's physical hookswitch
rather than telecom samples. Loop breaks from 20–150 ms count as pulses; a
400 ms make interval completes a digit, with ten pulses representing zero.
The regression produces the same `580` number as 5, 8 and 10 approximately
67 ms breaks separated by approximately 50 ms makes:

```sh
python3 tools/telephone_line_regression.py --pulse
```

A sustained on-hook state remains a hangup and does not produce a digit. A
product-level pass through the Telephone is covered separately below. The
ROM's asynchronous `SoftwareModem_CheckDialTone`, carrier and a remote modem
remain later milestones.

## Product Telephone path

The normal Telephone application now has its own headless acceptance test.
It starts from a calibrated copy of retained state, opens Desk → Telephone,
presses `5`, `8`, `0` on the visible keypad and then presses Dial:

```sh
python3 tools/telephone_ui_regression.py \
  --mame ../mame/datarover \
  --nvram-source "$MAGIC_CAP_ASSETS/runtime/manual/nvram"
```

The source NVRAM is never modified. The run keeps its generated Lua, copied
NVRAM, MAME log and `number.png` / `calling.png` screenshots in a timestamped
directory under `$MAGIC_CAP_ASSETS/runtime/telephone-ui-regression/`.

This is not just a screenshot check. Debugger counters require the shipping
ROM to traverse:

| Address | Product path |
|---|---|
| `0x13c3fe1c` | `PhoneDialer_DialNumber` |
| `0x13c402e8` / `0x13c408ac` | `PhoneServer_StartCall` / `PhoneServer_DialNumber` |
| `0x13c43080` / `0x13c43a60` | `SpeakerPhoneAudio_DialingInProgress` / `SpeakerPhoneAudioDino_StartMonitor` |
| `0x13c438fc` / `0x13c439a8` | phone half/full DMA service |
| `0x13c24da4` / `0x13c22ae4` | `DAAOffHook` / `SibServerTelecomOffHook` |
| `0x13c23b3c` | private `SibCmdStartTelecom` |
| `0x13c5a090` | `SoftwareModem_DialNumberWithResultCode` |
| `0x13e64f80` | `DialerInit` |
| `0x13e64e64` | `CallProgressGenerator` |
| `0x13e614f0` | `BlockShortScale` |

The final checkpoint also requires both sound and telecom directions enabled,
48-word rings, nonzero sound-TX, telecom-TX and telecom-RX addresses, and the
automatic exchange to decode exactly `580`. A passing UI shows `580` before
Dial and `Calling 580` afterward.

The recovered product call chain is
`PhoneDialer_DialNumber` → `ModemDialer_DialNumberCommon` →
`SoftwareModem_DialNumberWithResultCode`. Its soft-modem command carries
`T580`; `DialerInit` parses all three digits and
`CallProgressGenerator` produces each tone and gap.

This path exposed a CPU-core bug rather than a missing DAA route.
`BlockShortScale` contains eight TX39 `MULT rd,rs,rt` instructions. The old
baseline MIPS-I interpretation updated only `HI:LO` and left `rd` unchanged,
so the following Q12 right shift reduced a healthy oscillator to about ±6.
The R3900 core now also writes the low product to `rd`, as the Toshiba manual
specifies. The resulting waveform crosses the existing telecom-TX and DAA
path and is decoded from samples; the emulator never infers digits from UI
taps or ROM objects.

A representative passing checkpoint is:

```text
Telephone exchange DTMF: 5
Telephone exchange DTMF: 8
Telephone exchange DTMF: 0
TELEPHONE_UI_RESULT dialer=1 start_call=1 server_dial=1 audio_dialing=2 start_monitor=1 phone_half=677 phone_full=676 daa_offhook=1 sib_offhook=1 telecom_start=1 softmodem_dial=1 dialer_init=1 call_progress=90 block_scale=1598 sound_size=48 telecom_size=48 sound_enables=3 telecom_enables=3 sound_tx=40357348 telecom_tx=40357288 telecom_rx=40357108
PASS: Magic Cap entered 580 in the Telephone, showed the active call screen, traversed PhoneDialer, PhoneServer, and the software DTMF generator, went off-hook, kept both 48-word sound and telecom DMA rings running, and the exchange decoded 580
```

## External PCM bridge

The **External PCM bridge** telephone-exchange setting connects Dino's
telecom stream to MAME's `phone_bridge` bitbanger image. The wire format is
exactly one big-endian 32-bit DMA word—two signed 16-bit samples—at the ROM's
programmed sample rate. It is full duplex and does not decode, synthesize or
otherwise special-case modem signals.

**External PCM bridge with test exchange** combines the two existing
boundaries. It presents deterministic central-office dial tone while the
originator is off hook, decodes its first outbound digit, and then exposes
peer PCM. Use this combined setting only on the originating machine; the
answerer remains on the pure bridge so its fax response is never masked by a
locally generated dial tone.

Two independent MAME processes can therefore act as the originating and
answering ends of one line. Start the byte-for-byte relay:

```sh
python3 tools/telephone_pcm_relay.py --port 7200
```

Select **External PCM bridge** in both machines and attach both to it:

```sh
../mame/datarover datarover840 -bitb socket.127.0.0.1:7200
```

After receiving its first complete telecom word, MAME waits up to 50 ms for
each subsequent word. Four consecutive misses return it to nonblocking
startup mode, so a disconnected peer cannot hang the machine. This bounded
wait is needed because the two emulators have nominally identical
7,200-sample/s clocks but different host workloads; without it, the faster
virtual modem can consume silence while its peer is still computing. The
relay also bounds stream skew and permits a configurable, bounded diagnostic
capture without changing the forwarded bytes. Its
`started_at_peer_bytes` counters record how many bytes the opposite peer had
already sent when each direction first became active, making pre-stream
virtual-time skew measurable.

The automated transport check uses two isolated monitor boots, distinct
constant sample words and continuous 64-word RX/TX rings:

```sh
python3 tools/telephone_bridge_regression.py
```

It requires every receive word on each DataRover to equal the other
DataRover's transmit word and verifies nonzero byte counts in both relay
directions. This closes the raw external bidirectional PCM transport. The
paired-fax regression below supplies the product-level, clocked call exchange;
data-modem carrier remains a separate open acceptance target.

## Paired carrier baseline

Exploratory paired-ROM runs now reach both real data-modem roles over the
synchronized bridge. The originating side opens `System_iSoftwareModem`,
runs `StartDataModem`, installs modulation `0x80` (V.32), and starts telecom
DMA. The answering side replays the shipping `AnswerModem` command sequence
through `SoftModemCommandHandler`, rather than calling guessed DSP helpers:

- command 6 uses object operations 4983 and 4985. Their observed values are
  `4` and `0x0669880e`; the ROM sets bit zero of the second value, producing
  `0x0669880f`;
- command 2 uses role `1`, flags `0x00a1`, and option words
  `0xffffffff`, `0x00001fff`, `0x0000f071`, `0xffffffff`, and
  `0x00000100`.

The trace confirms both data receive/transmit callbacks, the V.32
pump/control/FIR path, clean return from debugger-injected setup, and command
2 with role `1`. The bridge now exposes deterministic peer connection order,
96-byte half-DMA reads and optional process-clock control. Holding whichever
emulator leads the PCM byte clock removes host socket buffering from the
training timeline. A paired-delivery run forwarded 215,912 and 215,916 bytes,
set both ROM detector flags, negotiated the same `0xfff0` rate word, called
`V32DataPumpReportStatus`, and advanced both sides through E and B1 into
`V32DataModeRxState`. This closes ROM-to-ROM V.32 carrier acquisition at the
DSP boundary.

Both streams are non-silent. Earlier word-aligned experiments preserve all
four bytes of each DMA word: reversing signed 16-bit polarity leaves their
levels unchanged, while 12 dB attenuation plus 14-bit quantization lowers
them as expected. A paired two-wire-hybrid experiment that adds a -20 dB
local echo also follows the same training frontier; synthetic echo is not
required for carrier. The decisive condition is allowing enough synchronized
training time: at ten seconds both roles are still in the complementary
R2/R3 waits, while by fifteen seconds both have entered data mode.

Reproduce that acceptance with a provider-configured retained state:

```sh
python3 tools/data_modem_pair_regression.py \
  --nvram-source \
    "$MAGIC_CAP_ASSETS/runtime/combined-browser/<passing-run>/nvram"
```

The harness copies the source twice, starts the answering peer first so TCP
connection order identifies the roles, then holds both processes until the
external PCM endpoints exist. It alternates execution at the 96-byte
half-DMA boundary and requires matching stable rate payloads, detector lock,
`V32DataPumpReportStatus`, `V32DataModeRxState`, HDLC-framer initialization,
LAPM SABME/UA and connect reporting, both directions of the 48-word DMA ring,
at least 100 KiB per stream and no more than one half-ring of final skew.
Generated Lua, copied NVRAM and logs remain under
`$MAGIC_CAP_ASSETS/runtime/data-modem-pair-regression/`.

## Product Internet Center carrier

The lower-level pair is now connected to a real Web Browser session. Prepare
a retained state in the Internet Center by opening the provider on the
Internet Providers sign, selecting its **locations** tab, choosing the `home`
location, assigning **PPP dialup**, accepting the chooser, and leaving the
provider editor so the change is committed. This is the location-based
connection selection described by *Using Magic Cap*, pp. 165–174.

Then run:

```sh
python3 tools/product_data_modem_regression.py \
  --nvram-source \
    "$MAGIC_CAP_ASSETS/runtime/combined-browser/<provider-run>/nvram"
```

The source is copied and never modified. One DataRover wakes from the
Internet Center, returns through Downtown and Hallway to the Desk, opens Web
Browser, selects the retained provider and reloads. The visible product
workflow displays `Dialing 555-1212` and reaches
`SoftwareModem_ConnectToNumber`, `SoftwareModem_OpenModemPort`,
`SoftwareModem_StartDataModem`, the ROM V.32 pump and continuous telecom DMA.

The other DataRover uses the exact direct-answer command sequence verified by
the generic pair. A call-aware exchange returns silence before the call
instead of queuing dial tone and DTMF into the answerer's receive socket.
After the product writes its dial-complete marker, the exchange identifies
the caller from observed pre-call PCM, pauses it, starts the answerer, sends
the first answer carrier block, and releases both processes on a shared
96-byte clock. Result markers keep both peers alive until their counters have
been sampled.

A passing run requires the product's real dial-up-link, PPP start/write/read,
LCP/IPCP negotiation, first IPv4 packet, connect/open/start and
connection-monitor paths; both ROMs'
receive/transmit/pump/status paths; V.32 data-mode entry; matching low 12-bit
payloads in the three stable rate samples; HDLC-framer initialization; LAPM
SABME/UA and connect reporting; answer-side delivery of the product data
units; valid answer-side LCP/IPCP responses and product-side delivery of the
replies; live
48-word RX/TX DMA; at least 100 KiB of call PCM per side; and no more than one
half-ring of final skew. The first sampled rate word carries transient state
and the product detector byte may clear after the sticky data-mode transition,
so neither is used to reject an otherwise stable connection.

This closes product selection, dialing, carrier acquisition and the local
application-status handoff. The trace reaches `DialupPPPMeans_NewDataLink`,
`PPPServer_StartDataLink`, `SoftwareModem_ConnectToNumber`,
`MonitorDataConnection`, `LapmReportConnect`, and `PPPServer_WritePDU`.
The product sends bytes through `SoftwareModem_Write` and LAPM, and the answer
ROM calls `LapmDeliverData`. An answer-side peer reads each data unit with the
ROM's `SoftModemRead`, writes framed RFC 1662 responses through
`SoftModemWrite`, and restores the interrupted CPU state so Magic Cap's LAPM
task transmits them. It acknowledges Magic Cap's LCP Configure-Request and
sends its own request. Magic Cap accepts both, starts IPCP with address
`0.0.0.0`, accepts a NAK assigning `10.0.2.15`, acknowledges peer address
`10.0.2.2`, and retries with the assigned address plus VJ compression. The
peer acknowledges the corrected request, completing IPCP. Magic Cap
immediately sends PPP protocol `0x0021`: an IPv4/TCP SYN from
`10.0.2.15:1024` to `10.0.2.2:8080`. The peer derives its acknowledgement
from the randomized sequence and computes fresh IP/TCP checksums and PPP FCS.
Magic Cap accepts the SYN-ACK and sends `GET / HTTP/1.0` with
`Host: 10.0.2.2:8080`. The peer reassembles the request across multiple ROM
reads, derives the acknowledgement from the complete TCP payload, and returns
a checksum-valid `HTTP/1.0 200 OK` response with a deterministic HTML body.
The harness keeps both modem processes on the same PCM clock until the
product's own PPP-read counter confirms receipt. Tesseract then verifies
`Magic Cap built-in modem works.` in the final Web Browser snapshot. The
current combined data/FIN segment also causes the browser's
`The connection was unexpectedly dropped.` notice after the body renders;
graceful close sequencing remains part of the general bridge work. The
product calls
`SoftwareModem_Read`, `PPPServer_ReadPDU` and `LCP_ProcessFrame` throughout.
This proves LCP/IPCP, bidirectional TCP and rendered HTTP across the complete
ROM stack. A general host bridge remains separate from the already working
PC Card PPP path.

ROM reads are not packet-aligned: one observed 58-byte read contained an LCP
Configure-Ack followed by an IPCP Configure-Request, while another contained
several retransmitted LCP requests. The answer probe therefore scans the
whole read and prioritizes IP, IPCP, then LCP rather than advancing a scripted
round number. Per-read logs record the selected protocol kind, exact escaped
bytes and cumulative reply count. All responses are generated from the
decoded live frame: retransmitted control IDs remain matched, IPCP is NAKed
until the guest address really changes, and the TCP acknowledgement follows
the randomized SYN. The captured HTTP request is split across ROM reads; the
peer retains partial async-PPP frames, reconstructs the complete request and
generates fresh IPv4, TCP and PPP checksums for its response. Its completion
barrier distinguishes answer-side write return from product-side PPP
consumption, avoiding a host-scheduling race that previously captured the
screen before delivery.

The live Dino SIB control value is `0x00a79923`: telecom 16-bit mode is set
and divisor `0x27` selects 7,200 samples/s. Telecom size `0x00bc` describes
the expected 48-word ring. This confirms the bridge's two signed 16-bit
samples per big-endian word; 8-bit packing is not an unresolved codec guess.
Calling the high-level `SoftwareModem_AnswerModem` method without a live
phone-call object reaches the method but deliberately stops before command 6
or telecom DMA. Isolated envelope edges also fail because the ROM expects a
physical detector cadence. That cadence is now modelled, and the qualified
Phone Status **receive fax** action supplies the missing call object. It
reaches `AnswerModem`, issues 24 softmodem commands during the observed
interval, installs `SoftModemLineHandler`, runs both `FaxModemRcv` and
`FaxModemXmt`, exercises HDLC receive/send, and produces non-silent PCM.

This narrows the remaining application-level gap: raw full-duplex transport,
scheduler skew, silent DSP output, answer-role selection, simple polarity, a
broad direct-to-attenuated gain range, V.32 carrier, incoming call-object
creation, and the real fax-answer and fax-origin startups have all been
addressed or excluded. The direct carrier harness does not create an Internet
Center connection object. The product carrier regression now creates that
object, completes V.32 and LAPM, starts the PPP actor and delivers its first
data unit to the answer ROM. A remote PPP consumer and network bridge remain
distinct from proving the paired ROM data pumps. A
captured answer stream begins with the expected strong 2,100 Hz CED and then
V.21-like FSK; replaying it through a single socket peer makes the origin run
fax RX/TX, exchange HDLC, and call `SendFaxImageData` 71 times. This proves
answer generation, bridge framing, and origin detection independently.

The maintained paired result closes that timing layer. Independent video-frame
schedules were unsuitable before both telecom streams existed: one machine
could accumulate stale audio or advance its modem timeout while the other UI
was still answering. A read-side skew bound alone was also insufficient
because the leading emulator could continue into the host socket buffer. The
byte-gated exchange instead creates one line timeline with setup silence,
caller hold, and active process scheduling at each PCM lead change.

A representative passing run decoded `5551212`; ran fax receive/transmit and
HDLC in both ROMs; reached 109 origin `SendFaxImageData` and 163 answer
`ReceiveFaxImageData` calls; retained 1,752,829 bytes of non-silent
bidirectional PCM; and recorded no protocol error or image-helper failure.
The answer UI remained in `Receiving page 1` instead of showing the earlier
“technical difficulties” dialog. The regression now requires at least 64
image callbacks in each direction plus those zero-error conditions.

This proves sustained real product-to-product transfer through receiver image
mode. The extended run additionally sees one successful image-helper return.
That session can contain a retried line error before successful cleanup, so
the stored-page mode does not label every internal attempt error-free; instead
it requires the successful completion and the stronger persisted result. On
relaunch Magic Cap finishes `Cleaning up...`, the Desk shows a new In-box
item, its top row is from DANILA SUKHAREV with subject `a fax`, the stationery
says one page was received, and its thumbnail opens as a rendered `Fax page
1`. This closes received-fax delivery from visible send through retained
object and reopened page.

## Published design cross-check

General Magic's preserved
[`SoftModem specifications`](http://www.datarover.com/Softmodem/)
independently describe the original target as a 36 MHz R3000 with 4 KiB
instruction and 1 KiB data caches and a single-cycle 16×16
multiply-accumulate extension. Its low-cost configuration explicitly names
Dino and the Betty 14-bit codec. V.32bis uses 7,200 samples/s in 48-sample
DMA frames and requires half/full interrupts or equivalent double buffering.

Those published requirements match the recovered TX39 cache sizes and
`MADD`, the implemented continuous 48-word RX/TX ring, and the ROM's
half/full service routines. They are independent confirmation of the model,
not a register specification: the page does not identify Dino bit fields,
Betty registers or the external DAA. The complete preserved-source assessment
is in [`developer-archives.md`](developer-archives.md#published-softmodem-and-sib-requirements).

## Reproduce the ROM/Dino boundary

First fetch the ROM and the SDK's unstripped Apollo ELF. They are research
inputs, not repository contents:

```sh
tools/fetch_assets.sh all
```

They remain under `$MAGIC_CAP_ASSETS/`; no ROM, ELF, NVRAM, generated Lua,
or MAME binary is committed.

The regression needs a live provider-configured Magic Cap heap. A successful
combined browser acceptance described in [`modem.md`](modem.md#install-and-test-web-browser-40)
leaves one in its printed run directory. Point `--nvram-source` at that run's
`nvram` directory:

```sh
python3 tools/builtin_modem_regression.py \
  --mame ../mame/datarover \
  --nvram-source \
    "$MAGIC_CAP_ASSETS/runtime/combined-browser/<passing-run>/nvram"
```

The harness validates `datarover840/ram`, copies the entire NVRAM tree into a
new timestamped directory, and never modifies the source. Results remain under
`$MAGIC_CAP_ASSETS/runtime/builtin-modem-regression/`.

The injected test frame calls the real ROM entry points. It opens the
`System_iSoftwareModem` object, starts the data modem, selects modulation
constant 128 (V.32), starts Dino telecom DMA, and calls the ROM's V.32 FIR with
a bounded scratch group. MAME debugger counters require the actor spawn,
server start, modem initialization, modulation install, V.32 pump/control/FIR,
and the FIR's first `MADD` to execute. The hardware checks require both DMA
directions to remain enabled with a 48-word buffer and nonzero receive and
transmit addresses.

A passing trace is:

```text
BUILTIN_MODEM_CALL object=00025C54
BUILTIN_MODEM_RETURN
BUILTIN_MODEM_RESULT open=1 spawn=1 server=1 dma_start=1 half=1 full=1 init=1 receive=1 transmit=1 install=1 v32pump=1 v32control=1 v32fir=1 madd=1 returned=1 enables=3 size=48 tx=4000C838 rx=4000C778
PASS: Magic Cap opened the built-in modem, kept its 48-word telecom ring running, selected V.32, and executed the ROM's V32ModulatorFIR through a TX39 MADD instruction
```

This single-machine test deliberately proves the ROM/Dino/DSP boundary, not
an imaginary telephone network; the paired regression above supplies the
remote V.32 carrier proof. Digital DAA hook, line-connect and ring behavior plus the
exchange's dial-tone waveform, outbound DTMF decoder and hookswitch pulse
decoder are modelled and checked separately. The normal Telephone actor,
generated analog digits, off-hook and DMA path are also covered. This test
invokes the lower ROM boundary rather than automating the Internet Center's
dial dialogs.

The product-level target is more specific. *Using Magic Cap*, pp. 135–163 and
216, documents the built-in fax modem on a telephone line, including selectable
tone/pulse dialing and fax workflows. The real Telephone now reaches its call
screen, generates its tone-dialed number and crosses the line hardware.
The product regression now drives the Internet Center through carrier, LAPM,
LCP/IPCP, TCP and a rendered deterministic HTTP response, and the fax pair
covers the complete send/receive workflow. Closing the remaining gap means
connecting the protocol-aware PPP peer to a general host-network endpoint and
performing graceful TCP teardown—not merely making the lower-level DSP or
digital-DAA probes count more functions.
See
[`user-guide.md`](user-guide.md).
