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
input and **Incoming telephone ring** drives the MFIO level and both edge
interrupts. Ring is intentionally a level input rather than a one-shot event,
so an automation harness or UI operator can supply the cadence expected by
the ROM. **Telephone exchange** selects either a silent line or the automatic
test exchange.

The direct regression runs without personalized NVRAM:

```sh
python3 tools/telephone_line_regression.py --mame ../mame/datarover
```

It verifies that off-hook/on-hook writes preserve a connected line, and that
asserting and releasing ring change MFIO input bit 0 and latch the correct
interrupt banks.

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
capture without changing the forwarded bytes.

The automated transport check uses two isolated monitor boots, distinct
constant sample words and continuous 64-word RX/TX rings:

```sh
python3 tools/telephone_bridge_regression.py
```

It requires every receive word on each DataRover to equal the other
DataRover's transmit word and verifies nonzero byte counts in both relay
directions. This closes the external bidirectional PCM transport; it does not
yet claim carrier. The next test must run the real originating and answering
software-modem state machines over this bridge and require both to report
carrier before attempting data or fax.

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
2 with role `1`. A representative 20-second training window forwards roughly
135–159 KiB per direction. Both streams are non-silent, with peaks around
10,400 and 15,800 and RMS levels around 6,900 and 7,800. Reversing signed
16-bit PCM polarity in both directions does not change the ROM's no-carrier
result.

This narrows the remaining failure: raw full-duplex transport, scheduler
skew, silent DSP output, answer-role selection, and simple line polarity have
all been excluded. Product call sequencing/state or a subtler Betty
codec/analog-line behavior remains to be found. Carrier, data transfer, and
fax are still unclaimed.

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

This deliberately proves the ROM/Dino/DSP boundary, not an imaginary
telephone network. Digital DAA hook, line-connect and ring behavior plus the
exchange's dial-tone waveform, outbound DTMF decoder and hookswitch pulse
decoder are modelled and checked separately. The normal Telephone actor,
generated analog digits, off-hook and DMA path are also covered, but carrier
acquisition and a remote modem are still missing. This test invokes the lower
ROM boundary rather than automating the Internet Center's dial dialogs.

The product-level target is more specific. *Using Magic Cap*, pp. 135–163 and
216, documents the built-in fax modem on a telephone line, including selectable
tone/pulse dialing and fax workflows. The real Telephone now reaches its call
screen, generates its tone-dialed number and crosses the line hardware.
Closing the remaining gap means driving the Internet Center through carrier
behavior and a remote peer, then covering fax—not merely making the current
lower-level DSP or digital-DAA probes count more functions. See
[`user-guide.md`](user-guide.md).
