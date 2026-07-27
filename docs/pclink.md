# PCLink package transfer

Magic Cap's Storeroom computer talks to the host over Dino UART A at
19,200 baud, 8 data bits, no parity, and one stop bit. The DataRover driver
exposes that connection as MAME RS-232 port 1. `tools/pclink_regression.py`
acts as the host, starts with clean NVRAM, navigates to the computer, and
installs a real archived package.

*Using Magic Cap*, p. 217, calls the physical accessory jack the **MAGIC BUS
connector** and lists a personal computer among the devices it can connect.
That product label must not be confused with Dino's packet-oriented Magic Bus
controller: the ROM's PCLink service demonstrably uses UART A, while the
`ATKB` accessory uses the separate Magic Bus registers. The board-level
pin/multiplexer relationship at that jack is not yet proven, so the product
name alone is not evidence that PCLink bytes belong in the packet controller.

No package, ROM, or Windows executable is committed. Keep all of them in the
persistent `$MAGIC_CAP_ASSETS/` tree.

## Download the test package and reference host

The repository mirror helper downloads and checksum-verifies every archived
package used below:

```sh
tools/fetch_assets.sh packages
```

The equivalent individual commands are kept here so the inputs and their
original locations remain recoverable even without the helper.

The small Dvorak keyboard package is the default regression input:

```sh
magic_cap_assets="${MAGIC_CAP_ASSETS:-$PWD/../magic-cap-assets}"
mkdir -p \
  "$magic_cap_assets/packages" \
  "$magic_cap_assets/tools/winpclink"

curl --fail --location \
  --output "$magic_cap_assets/packages/DvorakKeyboard.pkg" \
  https://joshcarter.com/magic_cap/packages/DvorakKeyboard.pkg
echo '069456eceb30a6fff044462d6a9f55e0140c4cc099968287f843478fc8fcc339  DvorakKeyboard.pkg' \
  | (cd "$magic_cap_assets/packages" && sha256sum --check)

curl --fail --location \
  --output "$magic_cap_assets/tools/WinPCLink.zip" \
  https://joshcarter.com/magic_cap/packages/WinPCLink.zip
echo '13204d0f2cba4f904c0b7638c5396c3bf7cf13477e395040528ec4d782ff6200  WinPCLink.zip' \
  | (cd "$magic_cap_assets/tools" && sha256sum --check)
unzip -o "$magic_cap_assets/tools/WinPCLink.zip" \
  -d "$magic_cap_assets/tools/winpclink"
echo '866199ca80ca1c1c51fea49dfb02db2b016ad4fc9f8ad8a7300246115b5bfed4  WinPcLink.exe' \
  | (cd "$magic_cap_assets/tools/winpclink/WinPCLink" && sha256sum --check)
```

WinPCLink is not needed to run the regression. It is retained as the original
behavioral reference and for its bundled user guide.

Other useful archived inputs can be kept beside the test package:

```sh
magic_cap_assets="${MAGIC_CAP_ASSETS:-$PWD/../magic-cap-assets}"
mkdir -p "$magic_cap_assets/packages"

curl --fail --location \
  --output "$magic_cap_assets/packages/Betteris.pkg" \
  https://joshcarter.com/magic_cap/packages/Betteris.pkg
curl --fail --location \
  --output "$magic_cap_assets/packages/WebBrowser40.mc2" \
  https://joshcarter.com/magic_cap/packages/WebBrowser40.mc2
curl --fail --location \
  --output "$magic_cap_assets/packages/MagicJavaScript.pkg" \
  https://joshcarter.com/magic_cap/packages/MagicJavaScript.pkg

echo '39b6b3de9da3e52467f2c5c1ec81adad6d47af5a20085929ce7f74fe78a44502  Betteris.pkg
b401b0f82beff0d945a4eb0361c8cf02aa16ec3fd79a3267edd46248c92bc706  WebBrowser40.mc2
beb0de0cdb51207534c280c88402ec11972dd7dfce11cd08514adc92c2f6f406  MagicJavaScript.pkg' \
  | (cd "$magic_cap_assets/packages" && sha256sum --check)
```

## Run the end-to-end regression

Build the sibling MAME fork first, then run:

```sh
python3 tools/pclink_regression.py
```

The harness:

1. boots a fresh USA 3.1.2j system;
2. taps the welcome screen and the three calibration points;
3. opens the Storeroom computer;
4. completes the PCLink handshake;
5. transfers `DvorakKeyboard.pkg`;
6. queues a final `Ping` and uses `Pong` as the completed-stream barrier;
7. sends host-side `GBye`, matching WinPCLink's documented **Close
   Connection** workflow; and
8. saves native 480×320 LCD snapshots before and after disconnection.

A passing run prints the persistent artifact directory. It contains the
generated Lua automation, isolated NVRAM, MAME output, both raw serial
directions, and `snapshots/package-installed.png`. The acceptance image shows
`DvorakKeyboard` as a 21K object in built-in storage. An RX overrun, missing
final `Pong`, incomplete disconnect, missing ROM-side Magic Bus counter, or
any entry into `MagicBus_HandleMagicBusFailure` fails the run.

The PCLink configuration keeps the modeled Magic Bus keyboard present: this
ROM treats unanswered address assignment as a peripheral failure, so an empty
bus accumulates failures during a long transfer and eventually posts the
attached-device alert. Selecting an empty Magic Bus is therefore a real
negative test, not isolation.

Use another archived input without copying it into Git:

```sh
python3 tools/pclink_regression.py \
  --package "$MAGIC_CAP_ASSETS/packages/Betteris.pkg"
```

Kaiser's checksum-pinned 461,876-byte modified browser is a more demanding
input:

```sh
python3 tools/pclink_regression.py \
  --package "$MAGIC_CAP_ASSETS/packages/WebBrowser-MIPS-USA.pkg" \
  --workdir \
    "$MAGIC_CAP_ASSETS/runtime/pclink-tls-browser"
```

A clean run accepts the package, returns the final `Pong`, sends `GBye`,
shows a 454K Web Browser object without an alert, and records zero entries
into `MagicBus_HandleMagicBusFailure`. The retained evidence is under
`$MAGIC_CAP_ASSETS/runtime/pclink-tls-browser-clean/20260726T183612/`.
Provenance, download checksum and the real-hardware comparison are in
[`oldvcr-tls.md`](oldvcr-tls.md).

The full Web Browser 4.0 acceptance uses the same transfer path and then
continues through PPP and local HTTP without restarting MAME:

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

The source is the pre-owner-setup NVRAM saved while configuring the two
provider connections described in [`modem.md`](modem.md). The harness copies
it into the timestamped run and never modifies the source. It then completes
the owner card, creates the `home` dialing location (USA, area code 650), and
explicitly assigns `PPP PC Card` to that location. Substitute any non-empty
first and last names made only from letters `a` through `z`; capitalization is
accepted.

`--combined-browser-acceptance` keeps both the PCLink serial endpoint and
modem PTY open, services Hayes commands during the long transfer, waits 1,800
emulated frames after the final `Pong` for the package actor to settle, and
only then sends `GBye`. In that same live process it opens the received
package, follows **go to**, enters `http://10.0.2.2:8080/`, presses **go**,
and services Slirp plus the built-in HTTP/1.0 endpoint. Avoiding any
state/NVRAM relaunch matters: build 3.1.2j can render the restored heap after
its cleanup path while rejecting the scripted touch input.

The run's `nvram/`, screenshots, PCLink and modem wire captures, Hayes
transcript, Slirp PPP debug log, and recorded HTTP requests are persistent
and isolated under `$MAGIC_CAP_ASSETS/`, so the large install is neither
discarded with `/tmp` nor copied into Git. See [`modem.md`](modem.md) for the
combined pass criteria.

## Recovered wire format

The implementation is based on the archived WinPCLink executable and a live
exchange with this ROM:

- the device first writes the unframed four bytes `ChMa`;
- commands contain a four-byte ASCII tag, a big-endian 32-bit payload length,
  and the payload;
- payload bytes `0x0e`, `0x0f`, and `0x10` are prefixed with `0x10`;
- escaping happens before the encoded stream is divided into chunks of at
  most 256 bytes, so an escape pair may cross a frame boundary;
- each frame is a raw big-endian 16-bit length, encoded bytes, then a raw
  big-endian 32-bit `~crc32(frame)`; and
- WinPCLink sends the zero-payload `Cntd` acknowledgement twice after the
  device's `Cnct`.

Package installation uses an `SPkg` command with a `0x404`-byte metadata
block, followed by a separately framed stream containing the package and four
zero bytes. The host queues one final `Ping` behind that tail and uses the
replying `Pong` as proof that the guest parsed the complete stream, then sends
the protocol's zero-payload `GBye` immediately. This ordering provides an
unambiguous completed-stream checkpoint and follows WinPCLink's documented
host-side close operation. The metadata records the file size twice,
`0x80000000`, the filename's character count, and its UTF-16BE name. Unit
tests cover framing, escaping across a frame boundary, CRC rejection,
packets, and this metadata layout.
