# PCLink package transfer

Magic Cap's Storeroom computer talks to the host over Dino UART A at
19,200 baud, 8 data bits, no parity, and one stop bit. The DataRover driver
exposes that connection as MAME RS-232 port 1. `tools/pclink_regression.py`
acts as the host, starts with clean NVRAM, navigates to the computer, and
installs a real archived package.

No package, ROM, or Windows executable is committed. Keep all of them in the
persistent `~/fun/magic-cap-assets/` tree.

## Download the test package and reference host

The small Dvorak keyboard package is the default regression input:

```sh
magic_cap_assets="$HOME/fun/magic-cap-assets"
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
magic_cap_assets="$HOME/fun/magic-cap-assets"
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
cd "$HOME/fun/magic-cap-emulator"
python3 tools/pclink_regression.py
```

The harness:

1. boots a fresh USA 3.1.2j system;
2. taps the welcome screen and the three calibration points;
3. opens the Storeroom computer;
4. completes the PCLink handshake;
5. transfers `DvorakKeyboard.pkg`; and
6. saves a native 480×320 LCD snapshot after installation.

A passing run prints the persistent artifact directory. It contains the
generated Lua automation, isolated NVRAM, MAME output, both raw serial
directions, and `snapshots/package-installed.png`. The acceptance image shows
`DvorakKeyboard` as a 21K object in built-in storage. An RX overrun or the
device's `GBye` response fails the run.

Use another archived input without copying it into Git:

```sh
python3 tools/pclink_regression.py \
  --package "$HOME/fun/magic-cap-assets/packages/Betteris.pkg"
```

Web Browser 4.0 uses the same path:

```sh
python3 tools/pclink_regression.py \
  --package "$HOME/fun/magic-cap-assets/packages/WebBrowser40.mc2" \
  --workdir "$HOME/fun/magic-cap-assets/runtime/webbrowser-install"
```

The run's `nvram/` directory is persistent and isolated, so a successful
large-package install is not discarded with a temporary directory. The
archived 500K Web Browser 4.0 package has been verified through this exact
path. See
[`modem.md`](modem.md) for the PPP bridge and browser test URL.

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
zero bytes. The metadata records the file size twice, `0x80000000`, the
filename's character count, and its UTF-16BE name. Unit tests cover framing,
escaping across a frame boundary, CRC rejection, packets, and this metadata
layout.
