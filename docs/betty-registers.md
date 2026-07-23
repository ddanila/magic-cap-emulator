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
| 4 | unknown interrupt-related register | retain a 16-bit shadow initially |
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

Register 4 is repeatedly exercised during `touch_init`, but its final role is
not yet proven. Register 5 must retain ordinary writes: the ROM diagnostic
writes `0x0028` and requires the same value on readback. Pen edges are
therefore not modeled as register-4/5 status; they update `IOData` and the
documented Dino SIB interrupt/pending state.

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
5. Betty ID register 12 reads as revision `0x1002`, one of the two revisions
   accepted by `touch_init`.

The production path now also implements the six ADC samples used by the touch
macro. `TouchCfg` values `0x0a12` and `0x0a48` select X and Y; the remaining
arrangements return pressure/contact samples. Main and backup battery ADC
channels 24 and 28 return nominal in-range values. MAME lightgun axes supply
the pen coordinates, and button edges propagate through Betty `IOData`, Dino
SIB pending state, and the normal interrupt dispatcher. Audio remains
unimplemented.
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
