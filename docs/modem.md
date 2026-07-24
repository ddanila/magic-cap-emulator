# PC Card modem and PPP

The DataRover driver exposes an emulated PC Card modem as `-pccard1 modem`
or `-pccard2 modem`. It supplies the CIS and configuration registers Magic
Cap expects, a minimal 16550-compatible UART at COM1 (`0x3f8`), Glacier
card-detect/IREQ signaling, and a host PTY. `tools/modem_bridge.py` handles
Hayes command mode and hands the live serial line to classic Slirp after
`CONNECT`.

No guest state, browser package, ROM, or generated capture is committed.
Keep those under `~/fun/magic-cap-assets/`; the bridge creates timestamped
logs under `~/fun/magic-cap-assets/runtime/modem-bridge/`.

## Host dependencies

On Debian or Ubuntu install classic Slirp and Bubblewrap:

```sh
sudo apt-get update
sudo apt-get install slirp bubblewrap
```

This is the original serial-line `slirp` program, not merely the
`libslirp` library. It accepts a PDA PTY through `SLIRP_TTY` and provides a
PPP peer with user-mode TCP/IP.

The bridge runs Slirp inside a small Bubblewrap sandbox with a private
hostname that resolves to Slirp's `10.0.2.2` host address. This avoids
changing the machine's hostname or `/etc/hosts`. It also handles classic
Slirp 1.0.17's `SLIRP_TTY` final-byte quirk, so use the bridge rather than
starting Slirp by hand.

The `-P` option selects PPP. The 57,600 setting is Slirp's link-pacing value;
Magic Cap still sees a 14.4 kbit/s modem result.

## Configure Magic Cap

In the Internet Center, create a provider connection of type **PPP PC Card**.
The phone number is only displayed and sent in `ATDT`, so a test number such
as `+1 (650) 555-1212` is safe. Slirp does not authenticate the username or
password; non-empty placeholder values are sufficient.

Use these IPv4 values when Magic Cap asks:

```text
Local address: automatic
Name server:   10.0.2.3
```

Slirp's useful special addresses are:

```text
10.0.2.2  host machine
10.0.2.3  DNS
10.0.2.15 conventional guest address
```

Save the configured communicator state outside Git. The local regression
default is:

```text
~/fun/magic-cap-assets/runtime/state-card-load/pc-card-only.sta
```

That particular checkpoint is on the provider's connections screen, so the
bridge's default automation taps the configured row. For a state saved
elsewhere, start with `--no-autodial` and initiate the connection in the
visible Magic Cap UI yourself.

The combined browser regression starts from persistent NVRAM rather than
modifying that checkpoint. Export the known configured checkpoint into an
isolated NVRAM directory with:

```sh
provider_state="$HOME/fun/magic-cap-assets/runtime/state-card-load/pc-card-only.sta"
provider_nvram="$HOME/fun/magic-cap-assets/runtime/provider-from-state/nvram"
mkdir -p "$provider_nvram"
cd "$HOME/fun/mame"
./datarover datarover840 \
  -rompath "$HOME/fun/magic-cap-assets/roms" \
  -state "$provider_state" \
  -pccard1 modem \
  -nvram_directory "$provider_nvram" \
  -video none -sound none -nothrottle -skip_gameinfo \
  -seconds_to_run 3
```

The resulting `datarover840/ram` and `datarover840/rtc` files contain the
same PPP PC Card provider as the source checkpoint. They are the
`--nvram-source` used below. They remain outside Git, and every PCLink run
copies them before making changes.

## Verify the guest modem boundary

The probe supplies `OK` and `CONNECT`, captures the first async-HDLC PPP
frame, and exits without needing Slirp:

```sh
cd "$HOME/fun/magic-cap-emulator"
python3 tools/modem_bridge.py --probe \
  --state "$HOME/fun/magic-cap-assets/runtime/state-card-load/pc-card-only.sta"
```

A passing run verifies this real ROM-generated sequence:

```text
ATE0V1&d2W2
atl0
at&n0&u0
ATDT1 (650) 555-1212
```

The acceptance frame begins with async-HDLC flag `7e`, address/control
`ff 03`, and protocol `c0 21` (PPP LCP). Unit tests separately cover
fragmented Hayes commands, echo changes, escaping, and LCP recognition.

## Run the live PPP bridge

For an interactive session:

```sh
cd "$HOME/fun/magic-cap-emulator"
python3 tools/modem_bridge.py \
  --state "$HOME/fun/magic-cap-assets/runtime/state-card-load/pc-card-only.sta"
```

The bridge launches MAME with the modem card, waits for `ATDT`, starts Slirp
on the card's PTY, sends `CONNECT 14400`, and then leaves all serial input to
Slirp. Closing MAME stops the bridge and Slirp. Use `--no-autodial` if the
saved screen does not match the local regression checkpoint, and
`--headless` only for unattended diagnostics.

For the finite, unattended acceptance check:

```sh
python3 tools/modem_bridge.py --acceptance \
  --state "$HOME/fun/magic-cap-assets/runtime/state-card-load/pc-card-only.sta"
```

A pass requires Slirp to start, complete LCP and IPCP, and leave a guest
snapshot in the printed persistent run directory. The verified negotiation
assigns `10.0.2.15` to Magic Cap, enables Van Jacobson compression, and
passes IPv4 packets from the guest.

## Install and test Web Browser 4.0

Download and checksum commands for `WebBrowser40.mc2` are in
[`pclink.md`](pclink.md). Install it into a copy of the
provider-configured NVRAM, open it once, and save the combined checkpoint:

```sh
cd "$HOME/fun/magic-cap-emulator"
python3 tools/pclink_regression.py \
  --package "$HOME/fun/magic-cap-assets/packages/WebBrowser40.mc2" \
  --nvram-source \
    "$HOME/fun/magic-cap-assets/runtime/provider-from-state/nvram" \
  --internet-center-source \
  --probe-package \
  --workdir \
    "$HOME/fun/magic-cap-assets/runtime/combined-browser/browser-live-state"
```

The printed run directory contains isolated NVRAM, install screenshots, wire
logs, and `post-install.sta`. No binary is placed in this Git checkout.

Run the deterministic combined acceptance against that state:

```sh
browser_root="$HOME/fun/magic-cap-assets/runtime/combined-browser"
browser_run="$browser_root/browser-live-state/RUN_TIMESTAMP"
python3 tools/modem_bridge.py --browser-acceptance \
  --state "$browser_run/post-install.sta" \
  --workdir \
    "$HOME/fun/magic-cap-assets/runtime/combined-browser/http-acceptance"
```

Replace `RUN_TIMESTAMP` with the directory printed by the PCLink run. The
bridge starts its own fixed HTTP/1.0 endpoint on port 8080 and automates Web
Browser 4.0 to open `http://10.0.2.2:8080/`. It first enters the full URL,
writes `browser-ready.sta`, and relaunches that state before pressing
**go**. The relaunch ends the completed PCLink process cleanly while retaining
the installed browser, provider settings, and entered URL. The lengthy PCLink
transfer leaves the PC Card modem absent. URL preparation and live acceptance
both insert it, and the bridge opens the new PTY immediately and answers the
ROM's Hayes initialization. This prevents the saved modem actor from retaining
a stale pre-save host session.

A pass requires all of the following from one run: the ROM's Hayes dial
sequence, Slirp LCP/IPCP with the guest at `10.0.2.15`, an exact `GET /` at
the built-in server, and `snapshots/browser-result.png`. The timestamped
directory also retains `http-requests.txt`, Slirp's PPP debug log, modem
transcript, raw wire captures, and intermediate browser screenshots.

The browser predates modern TLS, so plain HTTP is intentional. Slirp's
`10.0.2.2` host alias avoids an external site and proves PPP, TCP, HTTP, and
browser rendering together.
