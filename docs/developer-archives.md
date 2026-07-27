# Public Magic Cap developer archives

Two public sites preserve substantially more of the Magic Cap developer
ecosystem than the small set of binaries needed to run the DataRover:

| Public source | Principal contents |
|---|---|
| [Josh Carter's Magic Cap archive](https://joshcarter.com/magic_cap/) | DataRover ROMs and packages, eight SDK PDFs, device histories and photographs |
| [Resurrected datarover.com](http://www.datarover.com/) | General Magic developer reference, FAQs, samples, Magic Internet Kit, tools and historical material |

This page records what those live sources add to the emulator and which
material is only historical context. Use the public links below to obtain the
originals. Copyrighted sources, packages and PDFs are not redistributed by
this repository.

Every source URL on this page returned HTTP 200 on 2026-07-27.
datarover.com's restored originals are currently served over plain HTTP;
changing those links to HTTPS produces 404 responses.

## Provenance and adoption policy

This evidence has three levels:

1. General Magic/Icras manuals, SDK source and FAQs are primary software
   evidence.
2. Shipping packages and ROMs are primary artifacts, but strings and
   disassembly require interpretation.
3. Site descriptions, device histories and the 1996 mailing-list archive are
   corroborative historical evidence.

None of these replaces ROM-observed behavior for undocumented Apollo, Dino,
Glacier or Betty registers.

The reviewed General Magic/Icras manuals and source files carry copyright or
all-rights-reserved notices, and the software archives do not provide a
license authorizing redistribution. Their original payloads therefore remain
at the linked public sources. Non-copyrightable facts that matter to emulation
— constants, layouts, checksums, observed package markers and acceptance
requirements — are adopted directly in this repository's documentation.

Josh Carter's separate [Apollo prototype](https://joshcarter.com/magic_cap/apollo/)
history also resolves a naming trap. The SDK calls the DataRover MIPS target
“Apollo”, but the twelve hand-built Oki Apollo prototypes had only 4 MiB of
flash while the production DataRover 840 had 8 MiB; loading an 840 image could
brick a prototype. The emulator's 8 MiB ROM window is the production
DataRover contract, not a claim that every Apollo prototype was identical.

## Inspecting the classic Mac archives

`unar` 1.10.7 successfully opened every BinHex or StuffIt input: 41 `.hqx`
files and two bare `.sit` files, with no failures. Download an archive from
its public URL into a persistent research directory and extract it there:

```sh
research_root="${MAGIC_CAP_ASSETS:-$PWD/../magic-cap-assets}/developer-archives"
mkdir -p "$research_root/downloads" "$research_root/unpacked"
curl --fail --location \
  --output "$research_root/downloads/RandomCode.sit.hqx" \
  http://www.datarover.com/Develop/MagicCap/Tools/Archives/CoolStuff/RandomCode.sit.hqx
unar -o "$research_root/unpacked" \
  "$research_root/downloads/RandomCode.sit.hqx"
```

MacBinary resource forks appear as adjacent `.rsrc` files. Do not discard
them when reconstructing a classic Mac project, although most C and definition
files have usable data forks. Install `unar` with `brew install unar` or
`sudo apt-get install unar`.

## SDK document set

The [developer-document index](https://joshcarter.com/magic_cap/magic_cap_developer_docs/)
provides the following PDFs. Page counts are PDF pages; checksums identify the
linked files.

| Document | Pages | SHA-256 | Emulator use |
|---|---:|---|---|
| [*Design and Magic Cap*](https://joshcarter.com/magic_cap/docs/MagicSDK_Design_and_Magic_Cap.pdf) | 78 | `3cdcbf165ee8b5c9d69de28cdc476f6fb34851cb29d52de80da55a7c2181d4c3` | OS and UI design rationale; behavioral context, not registers |
| [*Guide to Development Tools*](https://joshcarter.com/magic_cap/docs/MagicSDK_Guide_to_Development_Tools.pdf) | 114 | `ff3a0e2ad59f51c053d892c332d930d1eadab4cd0354e3a35f015d91f34e93e0` | MIPS package build, monitor download and debugger workflow |
| [*Magic Internet Kit*](https://joshcarter.com/magic_cap/docs/MagicSDK_Magic_Internet_Kit.pdf) | 26 | `68e0f8542e4b0d3f7f71e46b33a8041b41b3baebf8aa4235d8f05f8a2e974a44` | TCP, DNS, PPP and communications application API |
| [*Package Development Guide*](https://joshcarter.com/magic_cap/docs/MagicSDK_Package_Dev_Guide.pdf) | 114 | `61ecc7048d335f340aae04dec2f8a68e4aaade2f25b46e198bce0f23e557b742` | Clusters, shadows, storage-card objects and package lifecycle |
| [*Magic Developer Roadmap*](https://joshcarter.com/magic_cap/docs/MagicSDK_Roadmap.pdf) | 8 | `a6fa4c4027154591bf0363229a6f9099950123aa5474fa8e86dff2285ff5143d` | SDK orientation |
| [*Magic Cap Tutorial*](https://joshcarter.com/magic_cap/docs/MagicSDK_Tutorial.pdf) | 60 | `16f51903ce7c8ec09ef30df838b253636c97139f092a682ebb2f1c53252f9311` | Source-level sample and package acceptance material |
| [*Magic Cap User Interface Specification*](https://joshcarter.com/magic_cap/docs/Magic_Cap_User_Interface_Spec.pdf) | 324 | `c56d1eafd00b38972bfb1c503b65eea3cf60bfbfd648339c4f8c65183de184ad` | Detailed UI oracle for future workflow automation |
| [*Using Magic Cap*](https://joshcarter.com/magic_cap/docs/Using_Magic_Cap.pdf) | 234 | `20010cefe051b94fde9f8fa16273a6c33547e85cc061fb5e80625296fa21f22a` | Exact duplicate of the already adopted DataRover guide |

The older 56-page
[*Magic Internet Kit Programmer's Guide*](http://www.datarover.com/Develop/MagicCap/Internet/Docs/mikguide.pdf)
has SHA-256
`85c7fe4651b77d7eb3831a8740a01ba51f1432664ac0a7dbf848dc227113c2fe`.
It is not a duplicate of the 26-page SDK document and remains useful for the
earlier kit's architecture and examples.

The
[datarover.com developer documentation](http://www.datarover.com/Develop/MagicCap/Docs/)
also contains 167 API-reference pages, 32 concept pages, 29 FAQ pages, six
CodeWarrior/Magic Developer tool chapters, 22 standard sample archives, and
138 daily Magic Cap list digests from May–October 1996. The reference and
concept collections are a map of the portable OS API. The FAQs and list are
particularly valuable for edge cases, but mailing-list reports should be
corroborated before becoming hardware behavior.

## Storage cards: an exact OS-visible contract

The published
[`PC Cards` FAQ](http://www.datarover.com/Develop/MagicCap/Docs/FAQ/Q+A_PCCards.html)
defines the missing Magic-specific CIS tuple. Tuple code `0xA0` has a
32-byte payload of eight big-endian 32-bit fields:

| Payload offset | Field | Required value or meaning |
|---:|---|---|
| `0x00` | `magicNumber` | `GMMC` |
| `0x04` | `version` | `0x00010001` |
| `0x08` | `cardType` | one of the four-character types below |
| `0x0c` | `clusterOffset` | common-memory offset of the metacluster |
| `0x10` | `uniqueID` | stable card identifier |
| `0x14` | `modificationDate` | zero for the documented construction path |
| `0x18` | `modificationTime` | zero for the documented construction path |
| `0x1c` | `crc` | zero for the documented construction path |

The defined card types are:

| Code | Meaning |
|---|---|
| `NULL` | no card |
| `FORN` | foreign RAM card |
| `DAMG` | damaged RAM card |
| `BLNK` | unformatted RAM card |
| `XRAM` | extended RAM card |
| `RAMC` | formatted RAM card |
| `JROM` | ROM card |
| `ROM+` | ROM with storage |
| `FSRV` | self-hosted flash card |
| `FLSH` | flash card |
| `IOCD` | I/O card |

The standard CIS version-2 tuple `0x40` is informational and is not required
by Magic Cap. A custom self-hosted card may expose a package in common memory.
An ordinary PCMCIA card instead relies on a separately installed package:
that package adds a `CardServer` subclass to `iCardServers`, Magic Cap calls
each server's `CanHandleCard()` after insertion, and the matching server
returns the instance used for subsequent card operations. The archived
EtherLink, NE2000 and wireless packages are concrete examples of that second
model.

This identifies the first concrete difference between the current fixture and
the documented Magic Cap card contract. The MAME linear-card device provides
an 8 MiB SRAM `CISTPL_DEVICE`, version strings and function tuple, but no
`0xA0` tuple; a newly created common-memory image is all `0xff`. The missing
tuple is therefore the leading explanation for why raw reads, writes and
insertion work without reaching setup, but the real ROM flow must still prove
that causal link.

The next storage acceptance can now be specific:

1. Add a configurable `0xA0` tuple to the disposable linear-card device,
   starting with `BLNK`, version `0x00010001`, a stable unique ID and zero
   metacluster offset.
2. Boot or live-insert the card and require Magic Cap's real setup/name flow.
3. Record the tuple and common-memory changes made by the OS. If setup updates
   the tuple as expected, require `RAMC` and a valid metacluster offset;
   either way, require the resulting card to persist across eject/relaunch.
4. Create a user object through the new-items preference, then exercise
   Option-insert erase, BVD battery indications, backup and restore.
5. Install [`Translation.pkg`](https://joshcarter.com/magic_cap/packages/Translation.pkg),
   insert a preserved 1.x fixture, and separately
   cover translation without modifying the source card.

The 114-page package guide supplies the object-runtime side: all objects live
in clusters; ROM changes use transient and persistent shadow clusters; user
fileable objects can follow `iNewItemsGoHere` through `NewItemNear`; packages
on a RAM card normally keep their changes there; and Magic Cap can execute
package code directly from a storage card.

## Package build, card loading and stream formats

The development-tools guide documents the DataRover path precisely:

- a MIPS build first produces ELF, then extracts code and data into frozen
  package attributes, yielding one `.package` file;
- a package can be written to a storage card as a raw byte stream;
- Option-held cold boot enters the blank-screen monitor, and the generated
  downloader writes slot 1 at `0x24000000`;
- after a normal cold boot Magic Cap reads the package into main RAM and asks
  whether to set up the storage card;
- choosing **don't** preserves a reusable loader card, while **set it up**
  repurposes it; multiple packages can be concatenated.

Inside the public
[`RandomCode.sit.hqx`](http://www.datarover.com/Develop/MagicCap/Tools/Archives/CoolStuff/RandomCode.sit.hqx)
archive, the `DownloadPackage` sample documents a different,
MagicXChange-compatible file wrapper. Its outer header is `MCap`, version 0,
display size, filename length and filename. That is followed by `MPkg`,
`STREAM_VERSION`, package size, creation/modification timestamps and the
data-package/has-backup flags; a `Wireline` then serializes the
`FrozenPackage`.

This wrapper must not be confused with the archived DataRover package files
or the current PCLink payload. `DvorakKeyboard.pkg`, `Translation.pkg` and
`WebBrowser40.mc2` begin with the frozen stream marker containing `SALTCOD`,
not `MCap`. The sample itself says ObjectMaker images use another format.
Consequently, the working PCLink harness is correct to send the published raw
package after its `SPkg` metadata rather than prepending the sample's Mac file
wrapper. The sample is nevertheless enough to build a future independent
package-wrapper reader/writer and to validate dates, flags and changes
clusters.

## Internet and PC Card software

The complete
[*Magic Internet Kit*](http://www.datarover.com/Develop/MagicCap/Internet/Downloads/MIK.zip)
source is substantially richer than the small MIK subset in the installed
Mac SDK. The ZIP contains 91 data-fork files (641,134 uncompressed bytes);
the StuffIt edition additionally preserves project metadata and resource
forks. It includes:

- TCP, IP, PPP, Ethernet and DNS class definitions;
- `UsesTCP`, dial-up PPP, Magic Bus PPP, serial-port and modem “means”;
- host lookup and incoming TCP `Listen()` interfaces;
- CujoTerm application templates;
- a Magic Bus external-modem implementation targeting `iSerialBServer` at
  38,400 bit/s, with DTR, modem-power and sleep-confirmation behavior; and
- an older Xircom PC Card Ethernet driver with source.

The high-level material is useful for understanding how browser and
communications actors attach PPP or Ethernet links and register DNS. The
power-aware modem source is also a concrete example: an active connection
denies immediate sleep, asks the user, and aborts/releases the port before a
confirmed shutdown.

Two boundaries are important. `Drivers/Xircom/astro.h` is a broad register map
for the earlier 68K Astro ASIC at `0x21000000`; it is not a Dino/Apollo
register description. Likewise, the Xircom driver's card-ready edge,
interrupt, reset and `CardServer` flow are useful PCMCIA software evidence,
but its hardware and card are not the DataRover's 3Com 3C589. The kit's
prebuilt libraries target older simulator/68K environments and are API
evidence, not MIPS implementations to transplant into MAME.

The [Rosemary software archive](https://joshcarter.com/magic_cap/packages/)
adds a useful MIPS/DataRover package corpus. Except where its page says
otherwise, these were US release builds made on IRIX on 1998-09-09:

- [`WCPack.pkg`](https://joshcarter.com/magic_cap/packages/WCPack.pkg)
  bundles AirSurfer, PM100C CDPD and Ricochet drivers;
- [`WaveLAN.pkg`](https://joshcarter.com/magic_cap/packages/WaveLAN.pkg)
  supports the Lucent WaveLAN Bronze 802.11 card, without
  encryption or SSID selection;
- [`AirSurfer.pkg`](https://joshcarter.com/magic_cap/packages/AirSurfer.pkg)
  supports NetWave wireless Ethernet;
- [`PM100C.pkg`](https://joshcarter.com/magic_cap/packages/PM100C.pkg) and
  [`Ricochet.pkg`](https://joshcarter.com/magic_cap/packages/Ricochet.pkg)
  are wireless modem/ISP paths;
- [`Ne2000.pkg`](https://joshcarter.com/magic_cap/packages/Ne2000.pkg)
  supports Socket LP and SohoWare ND5100 wireline cards;
- [`EtherLinkIII.pkg`](https://joshcarter.com/magic_cap/packages/EtherLinkIII.pkg),
  built 1998-09-20, is the already adopted 3C589 driver;
- [`Translation.pkg`](https://joshcarter.com/magic_cap/packages/Translation.pkg)
  migrates selected Magic Cap 1.x card data into 3.1; and
- [Web Browser 3.5](https://joshcarter.com/magic_cap/packages/WebBrowser35.mc2)
  is a useful stock baseline because its own text explicitly
  rejects secure connections, unlike Kaiser's modified build.

Strings in `Translation.pkg` independently confirm the intended UI: Magic Cap
recognizes a card from another OS version, offers Option-reinsert erase, or
translates to a new location without changing the old card. The package also
contains explicit insufficient-memory and untranslatable-package paths, so a
translation regression must cover failure cleanup as well as success.

The many archived games are not hardware specifications, but they form a
diverse frozen-package corpus for PCLink, package installation, Storeroom,
card execution and persistence testing.

## Published SoftModem and SIB requirements

The General Magic
[`SoftModem specifications`](http://www.datarover.com/Softmodem/)
independently validate several behaviors already recovered from the ROM:

- the original embedded target is a 36 MHz R3000 with 4 KiB instruction and
  1 KiB data caches plus a single-cycle 16×16 multiply-accumulate extension;
- V.32bis needs about 7 MIPS, 150 KiB of RISC code and 64 KiB of RAM;
- the low-cost example names Dino as the processor platform and Betty as the
  14-bit codec, consuming about 52 percent of R3000 bandwidth;
- License 1 uses 7,200 samples/s, grouped into 48-sample frames passed
  normally by DMA; and
- the codec interface requires half/full interrupts or equivalent double
  buffering.

This is a strong independent match for the implemented continuous 48-word
telecom ring, its half/full handlers, the 36.864 MHz TX39, cache sizes and
`MADD`. It does not describe the external DAA, Betty register numbers or Dino
DMA bit layout, so those boundaries remain ROM-derived.

## Magic Bus peripherals

The Mitsubishi M37690 material in
[`ThirdPartyOffers.sit.hqx`](http://www.datarover.com/Develop/MagicCap/Tools/Archives/CoolStuff/ThirdPartyOffers.sit.hqx)
describes a dedicated Magic Bus peripheral MCU rather than the DataRover's
controller:

- enhanced MELPS 740 8-bit core, up to 20 MHz;
- Magic Bus interface plus 32 KiB ROM, 1 KiB RAM, timers, serial I/O, RTC,
  programmable ports and 25 interrupts;
- daisy chaining of up to six peripherals, hot plugging and host-to-peripheral
  software download; and
- an evaluation board with connectors, monitor/debugger, 32 KiB download RAM
  and a PC-keyboard interface.

It supplies an authentic future accessory architecture and a concrete
six-device topology limit. It does not expose the Magic Bus wire commands or
MCU registers, so the current ROM-derived transaction protocol remains the
implementation authority.

## Reset, memory and source-level acceptance material

Several smaller sources in the
[`RandomCode`](http://www.datarover.com/Develop/MagicCap/Tools/Archives/CoolStuff/RandomCode.sit.hqx),
[`standard samples`](http://www.datarover.com/Develop/MagicCap/Docs/Samples/)
and
[`cookbook`](http://www.datarover.com/Develop/MagicCap/Tools/Archives/cookbook_samples.sit.hqx)
archives are useful as tests:

- `Flasher` demonstrates that timer objects are transient while the persistent
  “flashing” state survives; package installation/transient-cluster
  reinitialization recreates the timer. This is a source-level oracle for
  reset and retained-state testing, not evidence that a host GUI pause is a
  guest sleep.
- `MemoryMonger` allocates selectable persistent or transient buffers,
  reports cluster/free/shadow sizes, commits, frees, and catches
  `cannotAllocateMemory`. It is a deterministic way to reproduce memory
  pressure and cleanup behavior.
- The memory-runtime documentation explains metaclusters, clusters, shadows,
  commit and compaction. The debugging FAQ identifies transient-metacluster
  allocation failure as exception `cannotAllocateMemory`; this may help
  investigate user-visible cleanup/error symptoms, but no reviewed source
  contains the literal “too many errors” message.
- `CardDataSample` concerns the Magic Cap UI concept of cards in a stack and
  cross-package form data. It is not a PC Card storage example.
- The 22 standard samples plus cookbook sources provide small, known-source
  package behaviors suitable for simulator-versus-emulator comparisons.
- `DownloadROMImage` and `DownloadAndNubs` preserve corrected classic-Mac
  download scripts and debugger nubs. They are useful toolchain history, but
  the later Apollo SDK and published `WinDownload` already provide the
  relevant DataRover download path; these archives add no register behavior.

[`OrigEnvoy15equates.sit.hqx`](http://www.datarover.com/Develop/MagicCap/Tools/Archives/OrigEnvoy15equates.sit.hqx)
contains a 553,610-byte Motorola Envoy 1.5 ROM equate file.
[`PIC2000Scripts.sit.hqx`](http://www.datarover.com/Develop/MagicCap/Tools/Archives/PIC2000Scripts.sit.hqx)
contains a 549,228-byte PIC-2000 system equate file and target/debugger
scripts. These are valuable if a public ROM for either 68K platform is
recovered, but they do not fill any DataRover Apollo register gap. No public
Envoy or Sony ROM dump was found in the linked archives.

The Telescript material, including MIPS server libraries, describes the
server-side agent ecosystem rather than Magic Cap device hardware. It is
historically useful but not an emulator dependency.

## Key artifact checksums

These checksums pin the non-PDF inputs behind the findings above:

| Public artifact | SHA-256 |
|---|---|
| [`MIK.zip`](http://www.datarover.com/Develop/MagicCap/Internet/Downloads/MIK.zip) | `0e910646116be761c2f02bbb858ee5064801dcbaefa42dd394cf8cc4ece8f3fe` |
| [`MagicInternetKit.sit.hqx`](http://www.datarover.com/Develop/MagicCap/Internet/Downloads/MagicInternetKit.sit.hqx) | `46b02586cbc2719277b1423e498949e86267b6b188889e9116d9811324eb8b6b` |
| [`RandomCode.sit.hqx`](http://www.datarover.com/Develop/MagicCap/Tools/Archives/CoolStuff/RandomCode.sit.hqx) | `d37b28b82075ef3a0df810fcc8b15f7b55f200618854173073a49341e96911f5` |
| [`ThirdPartyOffers.sit.hqx`](http://www.datarover.com/Develop/MagicCap/Tools/Archives/CoolStuff/ThirdPartyOffers.sit.hqx) | `8f846c7a551382ede08df68e6317bed974f35e478f1247d7f229e1a24d05a789` |
| [`cookbook_samples.sit.hqx`](http://www.datarover.com/Develop/MagicCap/Tools/Archives/cookbook_samples.sit.hqx) | `ca4b7776bdaba8588b5471a394d192facebbbf3b1e6313dbe558a10528c17e9a` |
| [`OrigEnvoy15equates.sit.hqx`](http://www.datarover.com/Develop/MagicCap/Tools/Archives/OrigEnvoy15equates.sit.hqx) | `7b17b10a1ad2a517ad53e329e279560cde336cd19d2d26d71d5e79fa3075261b` |
| [`PIC2000Scripts.sit.hqx`](http://www.datarover.com/Develop/MagicCap/Tools/Archives/PIC2000Scripts.sit.hqx) | `8d7aabb3cb59946a61585a35afa578d189b8294ce6123e02c096255b30108309` |
| [`Translation.pkg`](https://joshcarter.com/magic_cap/packages/Translation.pkg) | `4f3be2b81e5868e70b321df8473e98fccdcb3ca90bc1c8999cc823490bd32cd0` |
| [`WCPack.pkg`](https://joshcarter.com/magic_cap/packages/WCPack.pkg) | `82dbb1b75d3456df583fb216504932084e804de0bc583df4a587469b209ce74a` |
| [`WaveLAN.pkg`](https://joshcarter.com/magic_cap/packages/WaveLAN.pkg) | `57c26768a1762e474d6129b43bf5bdc8fade2c3e2483392f28a48c6f52921732` |
| [`AirSurfer.pkg`](https://joshcarter.com/magic_cap/packages/AirSurfer.pkg) | `258c8899b4e3fd86728b3642ec08a062454bb5a0596deca47faa0756d56291c8` |
| [`PM100C.pkg`](https://joshcarter.com/magic_cap/packages/PM100C.pkg) | `3ed7bceaa1d1618c837c5ed50497d0f777c4128d9ce8ffe6850f29ffd1fcc964` |
| [`Ricochet.pkg`](https://joshcarter.com/magic_cap/packages/Ricochet.pkg) | `d71fe857b7ba3c26658f88f3539309decc758720359292244c40d1fe89c6669f` |
| [`Ne2000.pkg`](https://joshcarter.com/magic_cap/packages/Ne2000.pkg) | `bbedb30c71f99bd64d9c982b940b7346a488cb7aeee900503e3208ed7a290fe5` |

## Effect on the roadmap

The public-source audit changes priorities without claiming new implemented
behavior:

1. **Storage cards are the largest immediate unlock.** The exact `0xA0`
   tuple, classification codes and CardServer contract are known, and the
   present generic CIS can be compared directly against them.
2. **Memory-pressure reproduction is now cheap.** Port or build
   `MemoryMonger` before diagnosing vague cleanup/error reports.
3. **Package formats can be independently validated.** Keep the raw frozen
   package, MagicXChange wrapper and PCLink transport layers distinct.
4. **More PC Card devices are feasible but substantial.** The packages name
   real supported cards and provide MIPS binaries, while each underlying NIC
   or radio still needs a hardware model and deterministic host transport.
5. **Magic Bus topology has a concrete upper bound and accessory pattern.**
   Model up to six addressed endpoints before considering an MCU-level
   peripheral implementation.
6. **Earlier 68K targets remain preservation work.** Astro, Envoy and
   PIC-2000 material should stay isolated from the Apollo driver until ROMs
   and matching hardware evidence exist.
