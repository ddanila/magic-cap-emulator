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
the ROM.

The direct regression runs without personalized NVRAM:

```sh
python3 tools/telephone_line_regression.py --mame ../mame/datarover
```

It verifies that off-hook/on-hook writes preserve a connected line, and that
asserting and releasing ring change MFIO input bit 0 and latch the correct
interrupt banks. This closes the digital DAA control boundary; it does not
yet synthesize dial tone or put remote-modem audio into telecom RX DMA.

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
telephone network. Digital DAA hook, line-connect and ring behavior is
modelled and checked separately, but dial tone, carrier acquisition and a
remote modem are still missing. The test invokes the lower ROM boundary
rather than automating the Internet Center's dial dialogs.

The product-level target is more specific. *Using Magic Cap*, pp. 135–163 and
216, documents the built-in fax modem on a telephone line, including selectable
tone/pulse dialing and fax workflows. Closing this gap therefore means driving
the real Telephone or Internet Center setup through dialing, carrier behavior
and a remote peer—not merely making the current lower-level DSP or digital-DAA
probes count more functions. See [`user-guide.md`](user-guide.md).
