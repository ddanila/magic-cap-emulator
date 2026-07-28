# Betty register interface

Betty is General Magic's external mixed-signal/peripheral ASIC for GPIO,
touch, ADC, sound, and telecom functions. It is **not memory mapped at
`0xb0c00000`**. That address belongs to the TX39 “Dino” integrated peripheral
module. Dino talks to Betty through its Serial Interface Bus (SIB).

This initial map is derived from the matching unstripped SDK ELF's
`touch_init`, `dump_betty_regs`, `write_betty_regs`, `BettyTest`, and
production `SibCmd*` routines. Exact binary acquisition instructions are in
[`rom-layout.md`](rom-layout.md); the copyrighted inputs are not committed.

## Command encoding

The monitor sends a 32-bit command through Dino's SIB subframe-0 auxiliary
register (`0xb0c00080`) and receives the reply in the low 16 bits of the
subframe-0 status register (`0xb0c00088`):

```text
31 30                 27 26 25              16 15               0
+--+--------------------+--+------------------+------------------+
|0 | register (0..15)   |W |     reserved     | 16-bit payload   |
+--+--------------------+--+------------------+------------------+

command = (register << 27) | (write ? 0x04000000 : 0) | value
```

Examples observed in both the monitor diagnostics and production driver:

| Operation | Command |
|---|---:|
| Read register 0 | `0x00000000` |
| Write register 1 = `0x0202` | `0x0c000202` |
| Read register 1 | `0x08000000` |
| Write register 5 = `0x0028` | `0x2c000028` |
| Read register 5 | `0x28000000` |
| Read register 12 (Betty ID) | `0x60000000` |

The monitor waits on Dino interrupt-bank-1 SIB flags between command and
response. A first-pass Betty model can therefore start as a 16-word shadow
array behind this command protocol, then add read-only/status and side-effect
behavior as diagnostics demand it.

## Register numbers

The monitor prints a name immediately after probing each of the following
registers, which makes these number/name pairs high confidence:

| Number | Name in monitor | Initial emulator behavior |
|---:|---|---|
| 0 | `IOData` | 16-bit GPIO data/shadow |
| 1 | `IODir` | 16-bit GPIO direction |
| 2 | `PosIntEn` | positive/rising-edge interrupt enable |
| 3 | `NegIntEn` | negative/falling-edge interrupt enable |
| 4 | latched GPIO edge status | set on enabled GPIO edges; write-one-to-clear |
| 5 | `TelecomCfgA` | telecom configuration A |
| 6 | `TelecomCfgB` | telecom configuration B |
| 7 | `SoundCfgA` | sound configuration A |
| 8 | `SoundCfgB` | sound configuration B |
| 9 | `TouchCfg` | touchscreen configuration |
| 10 | `AdcCfg` | ADC configuration and conversion control |
| 11 | unknown | retain a 16-bit shadow initially |
| 12 | Betty ID/revision | read-only device identity |
| 13 | unknown | retain a 16-bit shadow initially |
| 14 | unknown | retain a 16-bit shadow initially |
| 15 | unknown | retain a 16-bit shadow initially |

The production `SibExtInterruptHandler` proves register 4's role. It reads
the register after a Betty IRQ, writes the returned set bits back to
acknowledge them, and dispatches callbacks by comparing that saved mask with
`PosIntEn`, `NegIntEn`, and `IOData`. Modeling this write-one-to-clear latch
is what lets a pen-down interrupt deassert and the welcome scene accept a
tap. Register 5 must retain ordinary writes: the ROM diagnostic writes
`0x0028` and requires the same value on readback.

The SDK debug records also expose a 16-entry `bettyShadowRegs` array in the
production SIB globals, confirming the register-file size independently of
the monitor's 0-through-15 dump loop.

## Bring-up behavior

The current driver implements the observable contract used by the boot path:

1. Dino accepts an SF0 command at `+0x080`.
2. It raises/completes the expected SF0/SF1 handshake flags in interrupt bank
   1.
3. Betty reads return the selected 16-bit shadow value through Dino
   `sibSf0Status` at `+0x088`.
4. Betty writes update writable shadows; register 12 remains a plausible,
   stable ID.
5. GPIO edges latch in register 4, assert Dino's SIB IRQ input, and deassert
   when the ROM writes the latched bits back.
6. Betty ID register 12 reads as revision `0x1002`, one of the two revisions
   accepted by `touch_init`.

The production path now also implements the six ADC samples used by the touch
macro. `TouchCfg` values `0x0a12` and `0x0a48` select X and Y; the remaining
arrangements return pressure/contact samples. Main and backup battery ADC
channels 24 and 28 return nominal in-range values. MAME lightgun axes supply
the pen coordinates, and button edges propagate through Betty `IOData`, Dino
SIB pending state, and the normal interrupt dispatcher.

For audio, `SibServerBootBeep` writes `SoundCfgB = 0x8006`, selects Dino's
11.025 kHz divisor, and supplies two signed 16-bit mono samples per write to
the Dino sound-hold register. The driver models that path and gates samples
on `SoundCfgB` bit 15; the ROM's startup tone is covered by
`tools/sound_regression.py`.

## Buffered SIB sound DMA

The OS does not feed real sounds by hand — it hands Dino a buffer and lets the
SIB stream it. `SibServerStartSoundOut` (`0x13c22428`) and the queued
`SibCmdStartSoundOut` (`0x13c23d3c`) program it in this order:

1. clear the sound DMA half and end interrupt enables in `interrupt1Enable`;
2. clear the transmit-DMA enable in `sibDMA`;
3. write the buffer length into `sibSize` (`0x060`), sound field bits 29:18;
4. zero `sibSoundHold`, then write the buffer address to `sibSoundTxStart`
   (`0x068`) after folding the segment bits away;
5. program the sample-rate divisor into `sibControl` bits 14:8;
6. re-enable the half and end interrupts;
7. set `kSibEnSoundTxDmaMask` in `sibDMA` (`0x090`) — playback starts here.

`sibDMA` bits, from the SDK's `Dino.asm.h`:

| Bit(s) | Name | Meaning |
|---|---|---|
| 31 | `kSibSoundDmaOnceMask` | one-shot |
| 30 | `kSibSoundDmaLoopMask` | explicit loop-mode flag |
| 29:18 | `kSibSoundDmaPtrMask` | current position, in 32-bit words, written back by hardware |
| 17, 16 | `kSibEnSoundRxDmaMask`, `kSibEnSoundTxDmaMask` | channel enables |
| 15:0 | telecom equivalents | the same fields for the telecom channel |

Both the size and pointer fields count 32-bit words, and the ROM stores the
last valid index rather than a count, so the driver reads a buffer of
`(field >> 18) + 1` words. Each word carries two signed 16-bit mono samples,
most significant first, exactly like the hold register.

The driver streams one word per sound tick while transmit DMA is enabled,
raising `kIntSoundDmaPtrIncMask` (`interrupt1` bit 18) per word,
`kIntSoundDmaHalfMask` (bit 22) at the halfway point, and
`kIntSoundDmaEndMask` (bit 21) at the end. With neither mode bit set, the ROM
uses the buffer as a continuously serviced two-half ring: its half/full
handlers refill the halves and the hardware wraps without clearing transmit
enable. An explicit one-shot clears its own enable at the end; explicit loop
mode also remains enabled. This distinction matters: stopping every transfer
that lacked `kSibSoundDmaLoopMask` stranded Magic Cap's speaker state during
the development ROM's moving-sound test. The pointer field is written back so
`SibServerSyncSoundOutDma` can read playback progress, and a write to `sibDMA`
keeps the hardware-owned pointer rather than taking one from the CPU.

## SIB telecom DMA

The telecom channel is the built-in software modem's data path, and it is the
same machinery with its fields in the low half of the shared registers:

| Field | Sound | Telecom |
|---|---|---|
| Buffer size in `sibSize` | bits 29:18 | bits 13:2 |
| DMA pointer in `sibDMA` | bits 29:18 | bits 13:2 |
| One-shot / loop in `sibDMA` | bits 31 / 30 | bits 15 / 14 |
| Channel enables in `sibDMA` | 17 (rx), 16 (tx) | 1 (rx), 0 (tx) |
| Buffer addresses | `sibSoundRxStart` / `sibSoundTxStart` | `sibTelRxStart` (`0x06c`) / `sibTelTxStart` (`0x070`) |
| `sibControl` enable, divisor | bit 4, bits 14:8 | bit 5, bits 22:16 |
| `interrupt1` half / end / pointer | bits 22 / 21 / 18 | bits 20 / 19 / 17 |
| Unbuffered hold ready | bit 10 `kIntSoundReceiveMask` | bit 9 `kIntTeleReceiveMask` |

`SibServerStartTelecom` (`0x13c228e4`) programs it exactly like its sound
counterpart: clear the half and end enables (`interrupt1Enable &= 0xffe7ffff`),
clear the channel enables (`sibDMA &= 0xfffffffc`), write the size, then the
buffer address, and arm.

Three behaviors matter in the model. The telecom fields share **one** pointer
for both directions, so transmit and receive run in lockstep at the same
buffer index. An explicit `kSibTelDmaOnceMask` stops both directions at the
end; without it, the 48-word two-half ring used by the software modem wraps
continuously. And `kSibLoopModeMask` (`sibControl` bit 3) is a hardware
loopback: with it set the SIB feeds transmit straight back into the receive
buffer, which is what the modem's own loopback diagnostics rely on. The phone
line and DAA are not modelled, so transmit samples are consumed at the
programmed rate and receive delivers either the loopback or silence.

The driver's own clock for this channel comes from `kSibTelDivMask`, separate
from the sound divisor, so the two channels can run at different rates.

This recovered register behavior now has a public design-level cross-check.
General Magic's
[`SoftModem specifications`](http://www.datarover.com/Softmodem/)
require 7,200 samples/s, 48-sample DMA frames and half/full double-buffer
service for the V.32bis embedded target, and name Betty as its codec. The
source does not expose register numbers, but it independently validates the
ring geometry and interrupt cadence used here; see
[`developer-archives.md`](developer-archives.md#published-softmodem-and-sib-requirements).

`tools/telecom_regression.py` drives a transfer directly instead of waiting for
the OS to dial, with the machine in IDT monitor mode so Magic Cap is not using
the SIB at the same time:

```sh
python3 tools/telecom_regression.py               # loopback
python3 tools/telecom_regression.py --continuous  # ROM modem ring
python3 tools/telecom_regression.py --no-loopback # control
```

The loopback run requires all 64 words to arrive, the half, end and pointer
interrupts to latch, the one-shot to clear its own enables, and the pointer to
wrap. The control run clears `kSibLoopModeMask` and requires the opposite —
receive overwrites the buffer with silence — which is what proves the loop-mode
bit gates the path rather than the check passing regardless.

`tools/builtin_modem_regression.py` supplies the complementary ROM-level
check: it opens Magic Cap's software-modem object, retains the continuous
48-word RX/TX ring, selects V.32, and proves that the ROM reaches its
V32ModulatorFIR and a TX39 `MADD`. See
[`builtin-modem.md`](builtin-modem.md) for the exact symbols, input NVRAM, and
scope.

## Verifying the sound path

The buffered path is what the OS uses for its boot chime: on a cold boot it
programs a 1024-word buffer at about 11 kHz roughly 14 seconds in. Verify with:

```sh
python3 tools/sound_regression.py --checkpoint dma
```

That runs long enough for the chime and requires two audible segments — the
70 ms unbuffered startup beep and a buffered segment lasting between one and
four seconds. Measured result: 2140 ms at `t=14.36s`, peak 6457, repeatably.

The buffer itself is only 1024 words — about 190 ms — because the normal mode
is a **continuously serviced two-half ring**: the ROM refills each half from
its half and full interrupt handlers without ever setting
`kSibSoundDmaLoopMask`, and only an explicitly requested `kSibSoundDmaOnceMask`
transfer stops at the end. A driver that stops any transfer lacking the loop
bit cuts the chime off after a single 190 ms pass; the regression's lower
bound rejects that failure directly. Note that the DAC output lands on the capture's
second channel; the analysis picks the most occupied channel for that reason.

The receive side is implemented through the same pointer, half-buffer and
end-of-buffer lifecycle. The driver writes signed 16-bit microphone samples to
`sibSoundRxStart`, advances the hardware-owned pointer, raises sound-receive
DMA/ready events, and stops a one-shot transfer at its declared end. Its
**Microphone source** setting selects the host input, a deterministic 1 kHz
tone, or silence. Verify the hardware boundary without depending on a host
audio device:

```sh
python3 tools/sound_input_regression.py
```

The regression captures 128 test-tone samples, requires the expected range
and zero crossings, checks half/end/pointer and receive-ready interrupts, then
switches the live source to silence and requires 128 zero samples. This covers
the Dino/Betty receive path directly; it does not yet claim the product UI
workflow.

*Using Magic Cap*, pp. 67–68, supplies the acceptance workflow rather than
leaving “microphone support” abstract: create an email, add the general-drawer
sound stamp, open its recording controls, record from the DataRover microphone,
stop early or at the configured duration, and play the same stamp back. A
deterministic sample fed through sound-RX DMA and recovered through the
already-working speaker path will prove the remaining end-to-end boundary.
The broader product coverage map is in [`user-guide.md`](user-guide.md).

The complete ROM diagnostic now runs as a regression:

```sh
python3 tools/serial_regression.py --checkpoint betty
```

The harness enters `call 13c076b0` at the IDT prompt. Every failed comparison
branches to `StayHere`; a returned status and second `<IDT>` prompt therefore
prove that the sequence completed. The production driver provides the next
ground truth:
`SibCmdTakeAdcReading` writes register 10, and
`SibCmdEnableBettyInt` maintains registers 2 and 3 through shadow values.

## Reproducing the evidence

With the SDK ELF extracted as described in
[`rom-layout.md`](rom-layout.md):

```sh
magic_cap_assets="${MAGIC_CAP_ASSETS:-$PWD/../magic-cap-assets}"
elf="$magic_cap_assets/sdk/extracted/Program_Files/debug/apollo/MagicCAP-USA"

mips-linux-gnu-objdump -d \
  --start-address=0x13c02a80 --stop-address=0x13c03820 "$elf"
mips-linux-gnu-objdump -d \
  --start-address=0x13c04698 --stop-address=0x13c04920 "$elf"
mips-linux-gnu-objdump -d \
  --start-address=0x13c076b0 --stop-address=0x13c08200 "$elf"
mips-linux-gnu-objdump -d \
  --start-address=0x13c23644 --stop-address=0x13c23a00 "$elf"
```

Useful ELF symbols include:

```text
13c02718 ResetSib
13c027c8 HardResetBetty
13c02a80 touch_init
13c04698 dump_betty_regs
13c047c8 write_betty_regs
13c076b0 BettyTest
13c23644 SibCmdWriteBettyRegField
13c236f0 SibCmdReadBettyReg
13c23858 SibCmdTakeAdcReading
13c239f0 SibCmdEnableBettyInt
13c25c34 SyncSibAndBetty
13c25c94 SendSubFrame0Command
```
