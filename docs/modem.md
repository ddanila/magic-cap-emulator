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
[`pclink.md`](pclink.md). Install it through the same live PCLink path:

```sh
cd "$HOME/fun/magic-cap-emulator"
python3 tools/pclink_regression.py \
  --package "$HOME/fun/magic-cap-assets/packages/WebBrowser40.mc2" \
  --workdir "$HOME/fun/magic-cap-assets/runtime/webbrowser-install"
```

The resulting run directory contains isolated persistent NVRAM as well as
the install screenshot and wire logs. A verified run installs the 500K
`Web Browser` object into a clean communicator through PCLink. The browser
predates modern TLS, so start with plain HTTP. A deterministic host-side test
is:

```sh
web_root="$HOME/fun/magic-cap-assets/runtime/web-test"
mkdir -p "$web_root"
printf '%s\n' '<html><body><h1>Magic Cap is online</h1></body></html>' \
  > "$web_root/index.html"
python3 -m http.server 8080 --directory "$web_root"
```

Open `http://10.0.2.2:8080/` in Web Browser 4.0. The host alias avoids
depending on an external site and proves PPP, TCP, HTTP, and browser
rendering together. Installing the browser and negotiating live PPP are
independently verified; loading this URL from the browser on the same
configured guest remains the final combined acceptance.
