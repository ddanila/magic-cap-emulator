# IrDA and beaming

Magic Cap can beam objects between communicators. The emulated DataRover now
provides the complete path used by the ROM: Dino pulsed UART bytes, carrier
detect and wake interrupts, a host PTY, peer discovery, recipient selection,
and object transfer through the real Beam UI.

## It does not use Dino's IR module

Dino has an IR block at `0x0a0`-`0x0a8`, and it is tempting to assume beaming
runs through it. It does not. `Dino.asm.h` describes `irControl1` as carrier
detect, a baud divisor, a test bit and consumer-IR enables, and `irControl2` as
`kIRPeriodMask` / `kIROnTimeMask` / `kIRDelayMask` / `kIRWaitMask` — carrier
timing for **consumer IR**, the remote-control kind, not a data link.

The ROM confirms it: the functions that touch `irControl1` are `BettyTest`,
`HardResetBetty`, `SibServerBootBeep`, `DisablePeripherals` and `CanDeepDoze`,
and they are using it as a GPIO, because `Gen2MFS.h` puts the Betty reset line
there:

```c
#define AssertBettyResetSignal() \
    ((DinoModule*)DINOMODULE)->irControl1 &= ~kIOResetBettyOffMask;
```

No function in the `irlap`, `irlmp` or `irda` families touches that IR block.

## It rides a UART in pulsed mode

IrDA SIR encodes each bit as a short pulse, and Dino's UARTs implement that
directly: `kUartPulseLow3ClockMask` (bit 9) and
`kUartPulseLow6CLockMask` (bit 8) in `uartA.control1` /
`uartB.control1`. `SerialServerDino_PulsedMode` (`0x13c540ac`) asks the
serial server which port it owns and tests that port's control register, so
the routing cannot be hard-coded from the register address alone.

The release ROM selects **UART B** for its IrDA serial server. During a Beam
run its control register reads `0xc0000141`: enabled and empty status, the
writable UART configuration, and pulsed mode. UART A remains the wired IDT /
PCLink port.

Above that sits the OS's own IrDA implementation — `irdaInit` → `irlapInit`,
with `IRDaemonActor` running IrLAP/IrLMP and `Beam*` / `BeamWindow_*` on top.
The MAME driver transports SIR-framed bytes; it does not replace or emulate
that protocol stack.

## Driver transport

The driver exposes a dedicated `:irda` PTY and prints its path at startup:

```text
:irda PTY: /dev/pts/7
```

UART writes go to this PTY only while the owning UART has either pulsed-mode
bit set. Otherwise they continue to the normal RS-232/terminal path. Bytes
read from the PTY return to whichever UART is currently pulsed, including its
receive-holding status and interrupt behavior.

The physical transceiver's carrier-detect line is represented by the
**IrDA carrier** input. Its live level appears as Dino interrupt-bank-5 bit 16
(`kIntCarDetPinMask`); changes latch positive- and negative-edge bits 15 and
14. `SerialServerDinoIrDA_SetWakeUpOnIREvents` enables the positive edge, so a
peer or bridge must assert carrier just before delivering its first frame.
The automated harness does this through the MAME input field.

A PTY is intentionally a byte-stream boundary, not a virtual room: two MAME
processes are not connected merely because both expose one. A bridge must
copy each PTY's output to the other and drive carrier. This makes raw wire
capture and a future physical/host IrDA bridge possible without embedding
peer policy in the device.

## Idle-stack probe

`tools/ir_probe.py` measures the release ROM's idle initialization:

```sh
python3 tools/ir_probe.py
```

On a plain boot `irdaInit`, `irlapInit`, `IRDaemonActor_Main`, and
`IRDaemonActor_InitializeBeam` run, while `irlapOpen` and `BeamDiscover` do
not. Neither UART selects pulsed mode. That is expected: discovery is a user
action, not a boot action. `--require-link` is useful only if the Beam UI is
being driven separately; use the paired harness below for acceptance.

## End-to-end Beam regression

Run:

```sh
python3 tools/beam_regression.py
```

The headless harness:

1. starts two release-ROM machines with independent fresh NVRAM;
2. calibrates both and creates `alice Sender` and `bob Receiver` owner cards;
3. opens both IrDA PTYs and bridges their bytes in both directions;
4. drives the sender through Name cards → Magic Lamp → Beam;
5. pulses carrier, requires discovery of `bob Receiver`, selects and accepts
   that peer, then touches **send**; and
6. decodes the captured SIR frames and requires the peer names and serialized
   name-card payload.

A representative passing transfer sent 22 complete frames / 1,491 bytes from
Alice and 15 frames / 323 bytes from Bob. The sender stream contains
`alice Sender`, `Dear bob,` and the ROM-generated
`The following item was received via beam:` notification; the reverse stream
contains `bob Receiver`. The final receiver capture shows its Desk Inbox
counter advance from 1 to 2.

Every run keeps raw `irda-transmit.bin` streams, generated Lua, MAME output,
isolated NVRAM, and snapshots of discovery, selected recipient, sender result,
and receiver result under:

```text
~/fun/magic-cap-assets/runtime/beam-regression/<timestamp>-<pid>/
```

Those artifacts stay outside Git; the ROM and generated NVRAM are not
redistributable project binaries.
