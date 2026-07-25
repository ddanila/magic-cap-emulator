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
| 30 | `kSibSoundDmaLoopMask` | restart at the buffer start on completion |
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
`kIntSoundDmaEndMask` (bit 21) at the end. A looping buffer wraps; a one-shot
buffer clears its own enable, which is what the ROM's boot chime uses. The
pointer field is written back so `SibServerSyncSoundOutDma` can read playback
progress, and a write to `sibDMA` keeps the hardware-owned pointer rather than
taking one from the CPU.

The buffered path is what the OS uses for its boot chime: on a cold boot it
programs a 1024-word buffer at about 11 kHz roughly 14 seconds in. Verify with:

```sh
python3 tools/sound_regression.py --checkpoint dma
```

That runs long enough for the chime and requires two audible segments — the
70 ms unbuffered startup beep and a buffered segment of 120-300 ms. Measured
result: 200 ms at `t=14.36s`, about 820 Hz, peak 5593. With the DMA dispatch
disabled the same capture contains only the beep, which is how the feature was
confirmed rather than assumed. Note that the DAC output lands on the capture's
second channel; the analysis picks the most occupied channel for that reason.
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
magic_cap_assets="$HOME/fun/magic-cap-assets"
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
