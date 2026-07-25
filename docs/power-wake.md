# Power, sleep, and the wake path

This note records the Dino power/wake hardware interface and the OS logic
around it, recovered from the unstripped Icras SDK ELF, the SDK's `Dino.h` /
`Dino.asm.h` platform headers, and the release ROM image. It also records the
driver fix for the former wake-path blocker: a warm boot of a heap saved while
suspended could enter the retained shutdown path but could not complete a
power-button wake.

Every address below was checked against the emulated ROM, not only the ELF —
see [Reproducing this analysis](#reproducing-this-analysis).

## Registers

Offsets are into the Dino module at `0xb0c0_0000` (kseg1 → physical
`0x10c0_0000`), derived from the `DinoModule` struct in the SDK's `Dino.h`.

| Offset | Register | Role in this path |
|---|---|---|
| `0x010` | `memoryConfiguration4` | DRAM behavior across sleep (`DeepDoze` sets bit 29, clears bits 15:14) |
| `0x100`–`0x114` | `interrupt1`–`interrupt6` | Interrupt status; writing acts as clear (`interruptNClear` aliases the same address) |
| `0x110` | `interrupt5` | Holds the on-button edge latches — the wake reason |
| `0x118`–`0x12c` | `interrupt1Enable`–`interrupt6Enable` | Per-bank enables; the shutdown path rewrites all six |
| `0x180` | `ioControl` | Polled while powering down (bit 2) |
| `0x1c4` | `powerControl` | Power state, stop-CPU, and live on-button status |

`powerControl` bits, named by `Dino.asm.h`:

| Bit | Name | Notes |
|---|---|---|
| 31 | `kPowerOnButtonStatusMask` | On-button currently asserted. Read-only: not in `kPowerWriteMask` |
| 30 | `kPowerInterruptStatusMask` | Read-only |
| 29 | `kPowerOkStatusMask` | Read-only |
| 15:12 | `kPowerStopTimerValueMask` | |
| 11 | `kPowerEnableStopTimerMask` | |
| 10, 9, 8 | `kPowerEnableForceShutdownMask`, `kPowerForceShutdownMask`, `kPowerForceShutdownOccurredMask` | |
| 7, 5 | `kPowerSelectShortWakeUpDebounceMask`, `kPowerDisableWakeUpDebounceMask` | Wake-up debounce selection |
| 4 | `kPowerStopCpuMask` | Writing 1 stops the CPU |
| 3 | `kPowerEnableOnButtonDebounceMask` | |
| 2 | `kPowerColdStartMask` | |
| 1, 0 | `kPowerCsMask`, `kPowerVccOnMask` | |

The wake-relevant `interrupt5` bits:

| Bit | Name |
|---|---|
| 23 | `kIntOnButPosMask` — on-button rising edge |
| 22 | `kIntOnButNegMask` — on-button falling edge |
| 19, 18 | `kIntSpiReceiveMask`, `kIntSpiEmptyMask` |
| 17 | `kIntIrConsumerMask` |
| 16 | `kIntCarDetPinMask` |

## The wake-reason handshake

Two boot-time predicates, `ShouldEnableDisplay(shutdownReason, vault)`
(`0x13c1daf4`) and `ShouldBeep(shutdownReason, vault)` (`0x13c1d9d8`), end with
the identical hardware test:

```c
if (DINO->interrupt5 & (kIntOnButPosMask | kIntOnButNegMask))  /* 0x00c00000 */
    return true;                       /* an on-button edge is latched */
if (DINO->powerControl & kPowerOnButtonStatusMask)             /* bit 31 */
    return true;                       /* the button is down right now */
return <decision from shutdownReason alone>;
```

So the ROM asks two independent questions — *was* there an on-button edge
(latched in `interrupt5`), and *is* the button down (live in `powerControl`).
The OS's own button poll, `PowerButton_ButtonPushed` (`0x13c26ee8`), is just
`powerControl >> 31` under `SetSupervisorMode`.

`Power_CausedWakeUp` (`0x13c39e00`) does **not** read hardware. It indexes the
RAM globals `wakeInterrupt1mirror`…`wakeInterrupt5mirror` (`0x0000e8b0` …
`0x0000e8c0`) by bank and tests one bit, so the wake-event bitmap the OS
reports is whatever the suspend path mirrored into RAM.

## The shutdown-reason branch

`EnableInterruptsForShutdown` (`0x13c39e64`) programs all six interrupt-enable
banks on the way into shutdown, and it has two branches keyed on the
`shutdownReason` global at `0x0000e880`:

```c
if (shutdownReason == 'EMER') {
    DINO->interrupt1Enable = 0;        /* banks 1-4 fully masked */
    DINO->interrupt2Enable = 0;
    DINO->interrupt3Enable = 0;
    DINO->interrupt4Enable = 0;
    DINO->interrupt5Enable = 0x00040000;   /* kIntSpiEmptyMask only */
} else {
    DINO->interrupt1Enable = wakeInterrupt1mirror;   /* restore saved wake masks */
    ...
    DINO->interrupt5Enable = wakeInterrupt5mirror;
}
DINO->interrupt6Enable = 0x00040000;
```

This matched one observed heap exactly — bank 1 masked to zero — and
identified that capture as a **software branch, not a missing register**: the
machine took the `EMER` path. On that path bank 5 enables only bit 18
(`kIntSpiEmptyMask`), so the on-button bits 22/23 are deliberately *not* wake
sources. A power-button press after an emergency shutdown is designed to be
ignored, which is why the subsequent wake is rejected rather than mishandled.

That observation was state-specific, not the general retained-RAM failure.
The clean automated reproduction described below retains `POFF`, restores the
normal wake masks, and still failed in the old driver. Reading one word remains
the way to distinguish the two cases:

1. `shutdownReason` genuinely holds `EMER` in the restored heap — the ROM is
   behaving correctly and the emulator is reproducing a state the OS treats as
   an emergency shutdown. The interesting question then moves to *why* the
   suspend that produced the saved heap recorded `EMER`.
2. `shutdownReason` holds a sleep-ish code but the mirrors are stale or zero,
   so the `else` branch restores an all-zero bank 1 — visually the same
   symptom, different fix.

Observed `shutdownReason` constants and the functions that write or test them
(four-character codes; the expansions are inferred from usage, not documented
in the SDK headers):

| Code | Written by | Likely meaning |
|---|---|---|
| `SLEE` | `AdvanceSleepState`, `CheckShouldSleep`, `Power_SleepNow`, `SerialWatcher_Main`, `Monkey_Main` | normal sleep |
| `POFF` | `Power_TryToSleep`, `Power_SleepNow` | power off |
| `EMER` | `NonPatchableLoBoot`, `Power_SleepNow` | emergency shutdown |
| `BATT` | `PowerIsCompletelyOut` | battery exhausted |
| `EXPT`, `RSET` | tested by the boot predicates | exception, reset |
| `BK 1`, `BK 2`, `BKF2`, `BKAE` | tested by the boot predicates | backup variants |
| `RS 1`, `RS 2`, `RSAE`, `RSNT` | tested by the boot predicates | restore variants |
| `SLNT`, `EMAL` | tested by the boot predicates | silent boot, emergency alert |

`EarlySetShutdownReason` and `DeviceDependentBoot` also write the global, and
`LastShutdownWasWithVccOff`, `RebootingForBackupOrRestore`, and
`InitObjectRuntime` read it — the code is the OS's primary cross-boot signal,
so it is worth watching in any wake investigation.

## Open lead: the battery and power-supply model

Every development-ROM boot posts *"Your communicator's backup battery is almost
out of power"* over the desk, and longer release sessions can post the
main-battery warning. The driver already answers the two battery ADC channels —
`touch_adc_value()` returns fixed readings for Betty ADC inputs 24 (main) and
28 (backup), chosen to sit between the Apollo calibration table's low and full
thresholds — so the values, the channel mapping, or the thresholds are not
matching what the OS expects, or the complaint comes from a different signal
entirely.

This is worth more than cosmetic cleanup: `MainBatteryIsLow` is broadcast to
roughly a dozen servers, including `Modem_MainBatteryIsLow`,
`PCLinkServer_MainBatteryIsLow`, `PhoneServer_MainBatteryIsLow`,
`PostOffice_MainBatteryIsLow`, `SerialServer_MainBatteryIsLow`, and
`DisplayServer_MainBatteryIsLow`. A wrongly-low battery state can therefore
perturb subsystems that have nothing to do with power, so it is a plausible
confounder for any flaky acceptance run.

Where to look, all in the release build unless noted:

| Address | Symbol | Why |
|---|---|---|
| `0x13c399ac` | `MainBatteryServer_InitAtoDChannel` | Which Betty ADC channel the OS actually programs |
| `0x13c39ad0` | `BackupBatteryServer_InitAtoDChannel` | Same for the backup cell |
| `0x13c399cc` / `0x13c39af0` | `*_InitializeBatteryFields` | The thresholds and field layout |
| `0x13c3b118` | `PowerSupplyGen2MFS_BatteryCoverAttached` | Cover state, a separate signal from voltage |
| `0x13c3b0d4` | `PowerSupplyGen2MFS_ACAdapterAttached` | External power |
| `0x13c3aeb0` / `0x13c3af14` | `PowerSupplyGen2MFS_{,Set}BatteryChargerEnabled` | Charger control |
| `0x13c3aa1c` / `0x13c3aaf8` | `MainBatteryCoverPositive/Negative` | Cover-switch edge handlers |
| `0x13c35370` | `BackupBatteryServer_Charging` | Whether the OS thinks the backup cell is charging |

The platform header names the signals: `Gen2MFS.asm.h` defines
`kMainBatteryCoverPositive` / `kMainBatteryCoverNegative` as
`kioPositiveInterrupt2` / `kioNegativeInterrupt2`, plus
`kIOMfioChargerEnableMask` (MFIO select 1) and `kIOMfioLCDPowerMask`
(MFIO select 17). The same class also gates the Vcc rails for the LCD, IR,
sound, MagicBus, and modem, so it is the right place to model board power as a
whole rather than patching ADC constants.

## Entering sleep

`Doze` (`0x13c3b250`) is minimal — clear then set `kPowerStopCpuMask`:

```c
DINO->powerControl &= ~kPowerStopCpuMask;
DINO->powerControl |=  kPowerStopCpuMask;   /* CPU stops here */
```

`DeepDoze` (`0x13c3b28c`) additionally reconfigures DRAM and enables one more
interrupt before falling into `RefreshMemory`:

```c
DINO->memoryConfiguration4 = (cfg | 0x20000000) & 0xffff3fff;
DINO->powerControl        &= ~kPowerStopCpuMask;
DINO->interrupt5Enable    |= 0x10000000;
```

`PowerDownDevice` (`0x13c3a550`) loops on `WaitForPowerDown` (`0x13c3b180`)
until `powerControl & kPowerInterruptStatusMask` is set, otherwise retrying
while `ioControl & 0x4` holds.

## Driver behavior

Requirements pinned down by the analysis and now implemented by the driver:

- **Latch on-button edges in `interrupt5` bits 23/22** and keep them latched
  across the suspend → wake boundary until the OS clears them by writing
  `interrupt5`. The boot predicates read the latch, not the live pin, so an
  edge that is consumed or reset by the wake itself is invisible to the ROM.
- **Report `powerControl` bit 31 while the button is held.** Bit 31 is
  read-only hardware status; it is the only thing `PowerButton_ButtonPushed`
  looks at.
- **Keep bits 31:29 out of the writable set.** `kPowerWriteMask` in
  `Dino.asm.h` is the authoritative list of writable `powerControl` bits.
- **Model `kPowerStopCpuMask` writes as the stop**, including the
  clear-then-set sequence `Doze` uses.

The runtime trace exposed one more required link. `interrupt6` is Dino's
read-only priority summary: bits 31/30 report high/low-priority enabled
pending interrupts from banks 1–5. `DeepDoze` masks CPU interrupts and polls
those two bits directly. The old driver asserted the R3900 IRQ line but never
populated bank 6, so the ROM could latch a correct on-button edge and still
spin in `RefreshMemory`.

The fix therefore:

- computes the low-priority bank-6 summary from enabled bank 1–5 status;
- makes bank 6 read-only rather than write-to-clear;
- suspends on a `StopCpu` rising edge as well as loss of VCC;
- releases a VCC-on doze on an enabled interrupt, while VCC-off power-down
  still requires the physical on-button;
- derives power-control bit 31 from the live input port and applies the SDK's
  exact `kPowerWriteMask` (`0x0000ffbf`); and
- resets Betty/SIB on a VCC-off wake, but preserves them across a VCC-on doze.

## Automated acceptance

Run:

```sh
cd "$HOME/fun/magic-cap-emulator"
python3 tools/power_regression.py
```

The harness uses a fresh private NVRAM directory and two MAME processes:

1. Boot, calibrate, reach the desk, press power, and verify two stable
   `WaitForPowerDown` samples with `shutdownReason == 'POFF'` and VCC off.
2. Relaunch only the persisted RAM/RTC. The ROM moves through `DeepDoze` to a
   VCC-on cleanup `Doze`; one button event lets that retained shutdown
   transaction finish at VCC-off `WaitForPowerDown`.
3. Hold the on-button from the final sleep. The acceptance checkpoint
   requires `interrupt5 & 0x00800000`, live `powerControl & 0x80000000`, and
   StopCpu released; later checkpoints must have VCC on and PCs outside
   `DeepDoze` / `WaitForPowerDown`.

The verified run latched exactly `interrupt5 = 0x00800000` while the button
was held, read `powerControl = 0xa0002409`, and settled in the normal OS idle
path with `powerControl = 0x20002c09`. All generated states, NVRAM, logs, Lua,
and PNGs remain outside Git under
`~/fun/magic-cap-assets/runtime/power-regression/`.

## Reproducing this analysis

The SDK ELF and headers come from `tools/fetch_assets.sh sdk`
(see [`rom-layout.md`](rom-layout.md)). With Homebrew LLVM on `PATH`
(`brew install llvm`; `binutils-mips-linux-gnu` works the same way on Debian):

```sh
assets="$HOME/fun/magic-cap-assets"
elf="$assets/sdk/extracted/Program_Files/debug/apollo/MagicCAP-USA"

llvm-objdump -d --disassemble-symbols=\
Doze,DeepDoze,EnableInterruptsForShutdown,ShouldEnableDisplay__FUlPC5Vault,\
ShouldBeep__FUlPC5Vault,PowerButton_ButtonPushed,Power_CausedWakeUp "$elf"

llvm-readelf --symbols "$elf" | grep -E "shutdownReason|wakeInterrupt[0-9]mirror"
```

The register names and bit masks are in
`$assets/sdk/extracted/Program_Files/include/Dino.asm.h` (bit definitions) and
`Dino.h` (the `DinoModule` struct that fixes the offsets).

Each address above was confirmed present in the emulated image, so the
analysis applies to the ROM the driver runs rather than only to the SDK build:

```sh
ROM="$assets/roms/datarover840/magiccap-usa.image" python3 - <<'EOF'
import os
rom = open(os.environ['ROM'], 'rb').read()
for va, expect in ((0x13c39ed8, '3c 01 b0 c0 ac 20 01 18'),   # EnableInterruptsForShutdown, EMER branch
                   (0x13c1db88, '3c 02 b0 c0 8c 42 01 10'),   # ShouldEnableDisplay, interrupt5 read
                   (0x13c26efc, '3c 10 b0 c0 8e 10 01 c4'),   # PowerButton_ButtonPushed, powerControl read
                   (0x13c3b250, '3c 08 b0 c0 8d 09 01 c4')):  # Doze, powerControl read
    got = rom[va - 0x13c00000:][:len(expect.split())].hex(' ')
    print('OK  ' if got == expect else 'DIFF', hex(va), got)
EOF
```

## Useful breakpoints

For MAME debugger sessions on a wake failure:

| Address | Symbol | Why |
|---|---|---|
| `0x13c1daf4` | `ShouldEnableDisplay` | Does boot decide to light the display? `$a0` is `shutdownReason` |
| `0x13c39e64` | `EnableInterruptsForShutdown` | Which branch runs, and what lands in the enables |
| `0x13c39e00` | `Power_CausedWakeUp` | What the OS believes woke it |
| `0x13c3b250` / `0x13c3b28c` | `Doze` / `DeepDoze` | The actual CPU stop |
| `0x13c26ee8` | `PowerButton_ButtonPushed` | Live button polling |

`shutdownReason` at `0x0000e880` and `wakeInterrupt1mirror`…`5mirror` at
`0x0000e8b0`…`0x0000e8c0` are the two RAM locations worth dumping first: they
decide the branch before any hardware is consulted.

Those global addresses are specific to the release build. The 1998-04-07
development ROM shifts its RAM globals up by `0x40` — `shutdownReason` sits at
`0x0000e8c0` there — so re-resolve them against the matching ELF if you
investigate on that image; see [`dev-rom.md`](dev-rom.md).
