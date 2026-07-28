# Power, sleep, and the wake path

This note records the Dino power/wake hardware interface and the OS logic
around it, recovered from the unstripped Icras SDK ELF, the SDK's `Dino.h` /
`Dino.asm.h` platform headers, and the release ROM image.

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

On the `EMER` path bank 5 enables only bit 18 (`kIntSpiEmptyMask`), so the
on-button bits 22/23 are deliberately *not* wake sources: a power-button press
after an emergency shutdown is designed to be ignored. A restored heap that
wakes with bank 1 masked to zero is therefore showing a **software branch, not
a missing register**. When investigating a rejected wake, reading one word —
`shutdownReason` — distinguishes two cases:

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

## Battery levels

The OS samples two Betty ADC channels — 24 for the main cells and 28 for the
backup cell, selected by `MainBatteryServer_InitAtoDChannel` (`0x13c399ac`) and
`BackupBatteryServer_InitAtoDChannel` (`0x13c39ad0`).

`BatteryServer_CalculateLevel` (`0x13c35988`) turns a reading into a percentage
in 16.16 fixed point: it clamps below the object's "empty" field and above its
"full" field, and otherwise returns `100 * (reading - empty) / (full - empty)`.
Those fields come from a **calibration record** that
`*_InitializeBatteryFields` picks through a jump table indexed by
`SibServerBettyRevision`, which returns 1 for the revision `0x1002` the driver
reports. Decoding the selected records — the words are counts scaled by 4096 —
gives the thresholds the model has to satisfy:

| Channel | Record | Empty | Low | Full |
|---|---|---:|---:|---:|
| Main, 24 | `0x13e96dc0` | 80 | 320 | 800 |
| Backup, 28 | `0x13e96e20` | 400 | 816 | 1600 |

**Why the defaults matter.** A backup reading below that channel's empty
point makes the OS compute 0% and post a backup-battery warning over the desk
on every boot. The healthy
default is 1000, which clears the 816 warning point with margin. The backup
channel's full point sits above the 10-bit value the ADC returns, so a healthy
cell reads mid-scale rather than 100%; the main channel's full point is 800, so
a full main cell does report 100%.

**Levels are selectable** through a **Main battery** / **Backup battery**
machine configuration, so the low paths can be exercised without waiting for a
cell to drain. Each setting is positioned inside a regime the record defines:

| Setting | Reading | Observed behavior |
|---|---:|---|
| Main Full | 800 | boots normally; 100% |
| Main Low | 200 | between empty and the warning point — in testing the machine stayed on the welcome scene rather than reaching the desk, which is not yet explained |
| Main Empty | 60 | below empty |
| Backup Good | 1000 | no warning |
| Backup Low | 700 | between empty and the warning point; no visible change on the desk |
| Backup Empty | 300 | the OS posts *"your communicator's backup battery is completely out of power"* |

The exact readings at which the OS switches between its "almost out of power"
and "completely out of power" wording are not pinned down: 300 produces the
"completely" alert while 350 and 700 produce no visible alert at all, which does
not follow from the percentage formula alone. Treat the table above as measured
behavior rather than a complete model of the OS's messaging.

`tools/battery_regression.py` locks this in by booting twice and requiring the
OS to react — a healthy desk and an empty-backup desk must produce different
screen checksums:

```sh
python3 tools/battery_regression.py
```

The control matters here more than usual: a model that always reported a
healthy cell would pass a one-sided check.

Why this is worth more than cosmetic cleanup: `MainBatteryIsLow` is broadcast to
roughly a dozen servers, including `Modem_MainBatteryIsLow`,
`PCLinkServer_MainBatteryIsLow`, `PhoneServer_MainBatteryIsLow`,
`PostOffice_MainBatteryIsLow`, `SerialServer_MainBatteryIsLow`, and
`DisplayServer_MainBatteryIsLow`. A wrongly-low battery state can therefore
perturb subsystems that have nothing to do with power.

## External power and the battery cover

`PowerSupplyGen2MFS` reads two more inputs, and both are now modelled:

| Signal | Accessor | Register | Bit | Sense |
|---|---|---|---|---|
| AC adapter attached | `PowerSupplyGen2MFS_ACAdapterAttached` (`0x13c3b0d4`) | `powerControl` `0x1c4` | 30 (`kPowerInterruptStatusMask`) | 1 = attached |
| Battery cover fitted | `PowerSupplyGen2MFS_BatteryCoverAttached` (`0x13c3b118`) | `ioControl` `0x180` | 2 | **inverted** — the bit is set while the cover is off |

The cover switch is wired to IO interrupt 2 (`Gen2MFS.asm.h`:
`kMainBatteryCoverPositive` = `kioPositiveInterrupt2`,
`kIntIOMainCoverOpenPosMask` = `kIntIOInt2PosMask`), so its edges latch in
`interrupt5` bit 9 when the cover comes off and bit 2 when it goes back on,
which is what `MainBatteryCoverPositive` (`0x13c3aa1c`) and its negative
counterpart service. Both signals are exposed as an **AC adapter** /
**Battery cover** machine configuration, defaulting to a machine running on its
own cells with the cover fitted — which is what the boot path expects, and what
the driver reported implicitly before.

Note that `PowerDownDevice` (`0x13c3a550`) reads both: it leaves its wait loop
as soon as `powerControl` bit 30 is set, and otherwise keeps retrying while
`ioControl` bit 2 says the cover is off. The defaults keep that loop
terminating exactly as before.

Observed behavior:

| Change | Effect |
|---|---|
| Cover removed **while the desk is up** | The OS reacts — 163 pixels change in the name-bar region, deterministically. Covered by `tools/battery_regression.py` |
| Cover removed **before power-on** | The machine never brings the display up. Reasonable for hardware whose cover holds the cells, so the harness always toggles mid-session instead |
| AC adapter attached | With charger enable asserted and the cover fitted, the modelled main-battery ADC rises over emulated time; detaching AC stops it. Covered by `tools/power_outputs_regression.py` |

## Outputs the OS writes

The charger and the supply rails are outputs. `mfioDataOutput` (`0x184`) stores
what the OS writes and reads it back, so
`PowerSupplyGen2MFS_BatteryChargerEnabled`, `VccLCDEnabled` and the rest observe
their own settings. `Gen2MFS.asm.h` names the pins:

| MFIO | Signal |
|---|---|
| 26 | DRAM Vcc on |
| 19 | Li-ion status (input) |
| 18 | Vpp on, card programming voltage |
| 17 | LCD power |
| 16 | MagicBus Vcc off |
| 2 | Card interrupt |
| 1 | Charger enable |
| 0 | Telecom ring-detect status (input) |

The outputs with represented consumers now act:

- MFIO 17 blanks the LCD scanout while leaving the 2 bpp framebuffer intact,
  so restoring the rail restores the same image.
- Setting active-high MFIO 16 removes Magic Bus accessory power. Its request,
  address and pending transaction are lost; after power returns, the ROM's
  broadcast discovery assigns the keyboard again.
- MFIO 1 advances the selected synthetic main-battery level only while AC is
  attached and the cover is fitted. The deterministic qualitative rate is
  four ADC counts per emulated second, capped at the release calibration's
  800-count full point. Detaching AC, opening the cover or clearing charger
  enable freezes it. A machine-configuration battery change supplies a fresh
  starting level.

This is intentionally not a cell-chemistry or real-time charge-duration model:
the Full/Low/Empty inputs and their rate are acceptance fixtures around the
ROM's real 80/320/800 thresholds.

The product guide turns two of these from speculative fidelity into observable
acceptance requirements. *Using Magic Cap*, pp. 209–211, says AC power
recharges the main cell while the communicator remains in use. It also defines
a five-minute automatic-shutoff default, adjustable from 1–60 minutes, with a
separate choice for shutting off while plugged in. Both the charger effect and
the complete Power Controls policy are now covered below. The same window
displays storage-card battery state, which belongs to the storage-card
lifecycle tracked in [`user-guide.md`](user-guide.md).

Run the direct output acceptance check with:

```sh
python3 tools/power_outputs_regression.py
```

It starts the IDT monitor with a low battery and proves all three output
effects. The accepted run held the ADC at 200 with AC detached, raised it to
208 after two emulated seconds with AC attached, held 208 after charger
disable, observed a Magic Bus request change from present to absent across Vcc
removal and return after restoration, and compared powered/blank native LCD
snapshots.

### Power Controls idle policy

Run the real UI acceptance check with:

```sh
python3 tools/power_policy_regression.py
```

It opens Hallway → Controls → Power and uses the actual minus, plus and
checkbox controls. OCR verifies the release defaults to five minutes, clamps
at one minute after ten minus presses, clamps at 60 after 70 plus presses and
returns to one after 70 minus presses. The checkbox interior is checked
independently so a coincidental OCR result cannot hide a missed touch.

With AC attached and “even when plugged in” initially clear, waiting 70
emulated seconds leaves VCC on in the ordinary doze loop: PC `0x13c3b270`,
shutdown reason zero and power control `0x6000241b`. After selecting the
checkbox and waiting the same interval, the ROM reaches `WaitForPowerDown` at
`0x13c3b1c8` with normal-sleep reason `SLEE` (`0x534c4545`) and power control
`0x60002408`, with VCC off. This covers both the numeric bounds and the
external-power exception rather than substituting direct register writes for
the user policy.

## ROM evidence

Where to look, all in the release build unless noted:

| Address | Symbol | What it establishes |
|---|---|---|
| `0x13c399ac` | `MainBatteryServer_InitAtoDChannel` | Main cell uses Betty ADC channel 24 |
| `0x13c39ad0` | `BackupBatteryServer_InitAtoDChannel` | Backup cell uses Betty ADC channel 28 |
| `0x13c399cc` / `0x13c39af0` | `*_InitializeBatteryFields` | Calibration thresholds and field layout |
| `0x13c3b118` | `PowerSupplyGen2MFS_BatteryCoverAttached` | Cover state is separate from voltage |
| `0x13c3b0d4` | `PowerSupplyGen2MFS_ACAdapterAttached` | External-power input |
| `0x13c3aeb0` / `0x13c3af14` | `PowerSupplyGen2MFS_{,Set}BatteryChargerEnabled` | MFIO 1 charger control |
| `0x13c3ad30` / `0x13c3ad78` | `PowerSupplyGen2MFS_{,Set}VccMagicBusEnabled` | MFIO 16 is active-high Vcc-off |
| `0x13c3ade4` / `0x13c3ae64` | `PowerSupplyGen2MFS_{,Set}VccLCDEnabled` | MFIO 17 controls LCD power |

The platform header names the signals: `Gen2MFS.asm.h` defines
`kMainBatteryCoverPositive` / `kMainBatteryCoverNegative` as
`kioPositiveInterrupt2` / `kioNegativeInterrupt2`, plus
`kIOMfioChargerEnableMask` (MFIO select 1) and `kIOMfioLCDPowerMask`
(MFIO select 17). Disassembly also prevents over-modelling: in this Apollo
release, the Sound, IR and Modem1 Vcc accessors at `0x13c3ae80`–`0x13c3aea8`
are literal stubs (getters always return true and setters do nothing), rather
than unimplemented MFIO rails. LCD, Magic Bus and charger are the actual
software-visible output paths.

### Package-level reset evidence

The public General Magic `Flasher` sample provides a useful software-side
boundary. Its “flashing” choice is persistent, but its `Timer` and callback
parameter buffer are transient. When package transient clusters are
reinitialized, the class recreates the timer if the persistent choice was
set. This is consistent with Magic Cap rebuilding transient state after a
reset and preserving normal package state across power transitions.

It also prevents two misleading emulator conclusions: pausing MAME's host UI
does not itself constitute a guest sleep, and a lost transient callback after
a real reset is the package's responsibility rather than evidence that DRAM
retention failed. The source inventory and related `MemoryMonger` test are in
[`developer-archives.md`](developer-archives.md#reset-memory-and-source-level-acceptance-material).

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

One more link is required. `interrupt6` is Dino's read-only priority summary:
bits 31/30 report high/low-priority enabled pending interrupts from banks 1–5.
`DeepDoze` masks CPU interrupts and polls those two bits directly, so a driver
that asserts the R3900 IRQ line without populating bank 6 lets the ROM latch a
correct on-button edge and still spin in `RefreshMemory`.

The driver therefore:

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
python3 tools/power_regression.py
```

The harness uses a fresh private NVRAM directory and two MAME processes:

1. Boot, calibrate, reach the desk, press power, and verify two stable
   `WaitForPowerDown` samples with `shutdownReason == 'POFF'` and VCC off.
2. Relaunch only the persisted RAM/RTC. The ROM moves through `DeepDoze` to a
   VCC-on cleanup path; the scheduled stop-timer status may release that
   intermediate doze, so acceptance requires observed entry rather than two
   permanently stopped samples. One button event then lets the retained
   shutdown transaction finish at VCC-off `WaitForPowerDown`.
3. Hold the on-button from the final sleep. The acceptance checkpoint
   requires `interrupt5 & 0x00800000`, live `powerControl & 0x80000000`, and
   StopCpu released; later checkpoints must have VCC on and PCs outside
   `DeepDoze` / `WaitForPowerDown`.

The verified run latched exactly `interrupt5 = 0x00800000` while the button
was held, read `powerControl = 0xa0002409`, and settled in the normal OS idle
path with `powerControl = 0x20002c09`. All generated states, NVRAM, logs, Lua,
and PNGs remain outside Git under
`$MAGIC_CAP_ASSETS/runtime/power-regression/`.

## Reproducing this analysis

The SDK ELF and headers come from `tools/fetch_assets.sh sdk`
(see [`rom-layout.md`](rom-layout.md)). With Homebrew LLVM on `PATH`
(`brew install llvm`; `binutils-mips-linux-gnu` works the same way on Debian):

```sh
assets="${MAGIC_CAP_ASSETS:-$PWD/../magic-cap-assets}"
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
