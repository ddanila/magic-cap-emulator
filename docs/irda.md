# IrDA and beaming

Magic Cap can beam objects between communicators. This note records how that
is wired on the DataRover, because the obvious answer is the wrong one, and how
far the stack gets on the emulated machine today.

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

No function in the `irlap`, `irlmp` or `irda` families touches Dino registers
at all.

## It rides a UART in pulsed mode

IrDA SIR encodes each bit as a short pulse, and Dino's UARTs implement that
directly: `kUartPulseLow3ClockMask` (bit 9) and `kUartPulseLow6CLockMask`
(bit 8) in `uartA.control1` / `uartB.control1`.
`SerialServerDino_PulsedMode` (`0x13c540ac`) asks the serial server which port
it owns and returns bit 8 of that port's control register, so either UART can
be the infrared one and the byte stream above it is ordinary serial.

Above that sits the OS's own IrDA implementation — `irdaInit` → `irlapInit`,
with `IRDaemonActor` running the link and `Beam*` / `BeamWindow_*` on top.

The practical consequence for the emulator is that **no new Dino module is
needed**. The driver already stores and returns the UART control registers, so
the pulsed-mode bit round-trips and `SerialServerDino_PulsedMode` reads it
correctly. What is missing is somewhere for the light to go.

## How far it gets today

`tools/ir_probe.py` counts entries into the stack over a boot:

```sh
python3 tools/ir_probe.py                # report how far the stack gets
python3 tools/ir_probe.py --require-link # demand an opened link
```

Measured on the release build:

| Routine | Entries |
|---|---:|
| `irdaInit` | 1 |
| `irlapInit` | 1 |
| `IRDaemonActor_Main` | 1 |
| `IRDaemonActor_InitializeBeam` | 2 |
| `irlapOpen` | 0 |
| `BeamDiscover` | 0 |
| `SerialServerDino_PulsedMode` | 0 |

Both UARTs read `0x05014000` — bit 8 clear, so neither is in pulsed mode.

So the IrDA stack **initialises itself on every boot** and then waits: nothing
opens the link, no discovery runs, and no port is switched to infrared until
the user beams something from the Beam window. That is correct behavior for an
idle communicator, and it is why beaming cannot be exercised by booting alone.

## What a working implementation needs

Two pieces, in this order:

1. **Somewhere for the data to go.** When a UART has bit 8 set, its traffic is
   infrared rather than wire, so the driver should route it to an IR endpoint
   instead of the RS-232 port. The cheapest useful endpoint is a loopback for
   bring-up; the useful one is a peer, either a second emulated DataRover or a
   host-side IrLAP responder on a PTY, in the shape `tools/modem_bridge.py`
   already uses for PPP.
2. **A way to trigger a beam.** `BeamWindow_Beam` is reached through the UI, so
   an acceptance run has to drive the Beam window the way the PCLink harness
   drives the Storeroom, or force the call once the transport exists.

`--require-link` is the acceptance check for step one: it demands `irlapOpen`
and a UART actually in pulsed mode, and fails today for exactly the reasons
above.
