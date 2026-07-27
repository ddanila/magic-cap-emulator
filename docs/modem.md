# PC Card modem and PPP

The DataRover driver exposes an emulated PC Card modem as `-pccard1 modem`
or `-pccard2 modem`. It supplies the CIS and configuration registers Magic
Cap expects, a minimal 16550-compatible UART at COM1 (`0x3f8`), Glacier
card-detect/IREQ signaling, and a host PTY. `tools/modem_bridge.py` handles
Hayes command mode and hands the live serial line to classic Slirp after
`CONNECT`.

No guest state, browser package, ROM, or generated capture is committed.
Keep those under `$MAGIC_CAP_ASSETS/`; the bridge creates timestamped
logs under `$MAGIC_CAP_ASSETS/runtime/modem-bridge/`.

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

This is the same product-level route documented in *Using Magic Cap*,
pp. 105–120 and 165–174: provider configuration in Internet Center followed by
dial-up Web access. The current acceptance stops at deterministic plain HTTP;
mail-service behavior and the separate built-in telephone-line modem are not
implied by this passing PC Card path. See
[`user-guide.md`](user-guide.md).

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
$MAGIC_CAP_ASSETS/runtime/state-card-load/pc-card-only.sta
```

That particular checkpoint is on the provider's connections screen, so the
bridge's default automation taps the configured row. For a state saved
elsewhere, start with `--no-autodial` and initiate the connection in the
visible Magic Cap UI yourself.

PCLink-only package probes can start from persistent NVRAM rather than
modifying that checkpoint. Export the known configured checkpoint into an
isolated NVRAM directory with:

```sh
provider_state="$MAGIC_CAP_ASSETS/runtime/state-card-load/pc-card-only.sta"
provider_nvram="$MAGIC_CAP_ASSETS/runtime/provider-from-state/nvram"
mkdir -p "$provider_nvram"
cd ../mame
./datarover datarover840 \
  -rompath "$MAGIC_CAP_ASSETS/roms" \
  -state "$provider_state" \
  -pccard1 modem \
  -nvram_directory "$provider_nvram" \
  -video none -sound none -nothrottle -skip_gameinfo \
  -seconds_to_run 3
```

The resulting `datarover840/ram` and `datarover840/rtc` files contain the
same PPP PC Card provider as the source checkpoint. They are the
`--nvram-source` used by package-only probes. They remain outside Git, and
every PCLink run copies them before making changes.

For the combined browser acceptance, preserve a copy of the configured heap
*before* completing the first-run owner-card dialog:

```text
$MAGIC_CAP_ASSETS/runtime/state-card-load/nvram/
  datarover840/ram
  datarover840/rtc
```

That seed must show the provider's `PPP PC Card` and `PPP dialup` rows when
booted. The combined harness completes the owner dialog, creates a `home`
dialing location, and binds that location to `PPP PC Card` in the same
process. It copies the seed into each run, so the preserved source remains
unchanged.

## Verify the guest modem boundary

The probe supplies `OK` and `CONNECT`, captures the first async-HDLC PPP
frame, and exits without needing Slirp:

```sh
python3 tools/modem_bridge.py --probe \
  --state "$MAGIC_CAP_ASSETS/runtime/state-card-load/pc-card-only.sta"
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
python3 tools/modem_bridge.py \
  --state "$MAGIC_CAP_ASSETS/runtime/state-card-load/pc-card-only.sta"
```

The bridge launches MAME with the modem card, waits for `ATDT`, starts Slirp
on the card's PTY, sends `CONNECT 14400`, and then leaves all serial input to
Slirp. Closing MAME stops the bridge and Slirp. Use `--no-autodial` if the
saved screen does not match the local regression checkpoint, and
`--headless` only for unattended diagnostics.

For the finite, unattended acceptance check:

```sh
python3 tools/modem_bridge.py --acceptance \
  --state "$MAGIC_CAP_ASSETS/runtime/state-card-load/pc-card-only.sta"
```

A pass requires Slirp to start, complete LCP and IPCP, and leave a guest
snapshot in the printed persistent run directory. The verified negotiation
assigns `10.0.2.15` to Magic Cap, enables Van Jacobson compression, and
passes IPv4 packets from the guest.

## Install and test Web Browser 4.0

Download and checksum commands for `WebBrowser40.mc2` are in
[`pclink.md`](pclink.md). The deterministic combined acceptance installs it
into a copy of the provider-configured NVRAM and reaches the HTTP result in
one uninterrupted MAME process:

```sh
python3 tools/pclink_regression.py \
  --package "$MAGIC_CAP_ASSETS/packages/WebBrowser40.mc2" \
  --nvram-source \
    "$MAGIC_CAP_ASSETS/runtime/state-card-load/nvram" \
  --owner-first-name Ada \
  --owner-last-name Lovelace \
  --combined-browser-acceptance \
  --workdir \
    "$MAGIC_CAP_ASSETS/runtime/combined-browser/live-http"
```

The harness starts its fixed HTTP/1.0 endpoint on port 8080, owns both host
PTYs from boot, and answers the modem's Hayes initialization even while
PCLink transfers the package. Its first-run automation completes the owner
card, creates the `home` dialing location with the seed's USA/650 defaults,
and explicitly changes that location's connection from the default `PPP
dialup` to `PPP PC Card`. After the final `Pong`, it gives the guest package
actor 1,800 emulated frames to finish, takes the installed-package snapshot,
and sends `GBye`. It then opens Web Browser 4.0, enters
`http://10.0.2.2:8080/`, presses **go**, and hands the modem line to Slirp
after `CONNECT 14400`.

This deliberately avoids relaunching a MAME save state or copied NVRAM
between installation and dialing. The 3.1.2j running heap can return through
a cleanup/suspend path that renders the saved screen but does not accept the
scripted touches, whereas keeping the original process alive preserves the
interactive browser session.

A pass requires all of the following from one run: the ROM's Hayes dial
sequence, Slirp LCP/IPCP with the guest at `10.0.2.15`, an exact `GET /` at
the built-in server, and `snapshots/browser-result.png`. The timestamped
directory also retains `http-requests.txt`, Slirp's PPP debug log, modem
transcript, both protocols' raw wire captures, isolated NVRAM, and
intermediate browser screenshots. No package, state, or generated binary is
placed in this Git checkout.

The first complete combined pass on 2026-07-25 recorded:

```text
HAYES ATE0V1&d2W2
HAYES atl0
HAYES at&n0&u0
HAYES ATDT555-1212
remote IP address 10.0.2.15
slirppp: PPP is up now
GET /
```

The final LCD capture renders the deterministic page heading **Magic Cap is
online** and its text identifying Web Browser 4.0 and Slirp PPP. The harness
printed `PASS: installed Web Browser through PCLink and fetched the local
HTTP page over PPP`.

The stock browser predates modern TLS, so plain HTTP is intentional. Slirp's
`10.0.2.2` host alias avoids an external site and proves PPP, TCP, HTTP, and
browser rendering together.

## Optional proxy-assisted HTTPS

Kaiser's [2023 DataRover field report](oldvcr-tls.md) modifies Web Browser
3.5.1 to add separate HTTP and HTTPS proxy Rules. For an HTTPS URL the package
still opens an ordinary Magic Internet Kit TCP stream to the configured proxy
and sends the full URL; the host-side Crypto Ancienne `carl -p` process
performs TLS. This is compatible with the existing PC Card PPP path and does
not require emulated crypto hardware.

The checksum-pinned MIPS package installs cleanly through PCLink, and the
complete flow is now automated over the original EtherLink III driver.
`tools/https_proxy_regression.py` uses Browser 3.5's HTTP proxy Rule 13 plus
Crypto Ancienne's `-u` upgrade, a local HTTPS endpoint, and an isolated pinned
proxy. It requires both the exact decrypted request and rendered result.

The same host-side design can be reused for a future PPP-specific acceptance;
the current HTTPS regression proves EtherLink, not the modem path. Crypto
Ancienne does not bind its own socket, has no proxy authentication or access
control, and currently does not validate certificates, so the result is
protocol interoperability rather than secure browsing. Native HTTPS Rule 14
dispatch remains a browser-level gap. Full setup and evidence are in
[`oldvcr-tls.md`](oldvcr-tls.md#deterministic-https-regression).
