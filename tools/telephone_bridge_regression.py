#!/usr/bin/env python3
"""Exchange telephone PCM between two independent DataRover processes.

Each machine runs in the IDT monitor, takes Betty off-hook, and starts the
real Dino telecom DMA channel with a distinct constant sample word. A local
TCP relay connects their MAME bitbanger streams. Both receive rings must fill
with the other machine's word, proving a bidirectional external line without
synthesizing or interpreting modem audio.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.telephone_pcm_relay import PcmRelay


ASSETS_ROOT = Path(
    os.environ.get("MAGIC_CAP_ASSETS", REPO_ROOT.parent / "magic-cap-assets")
).expanduser()
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = ASSETS_ROOT / "roms"
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "telephone-bridge-regression"

TX_BUFFER = 0x0020_0000
RX_BUFFER = 0x0021_0000
WORDS = 64
RESULT_PATTERN = re.compile(
    rb"PHONE_BRIDGE_RESULT received=(\d+)/(\d+) "
    rb"expected=([0-9A-F]{8}) enables=(\d) tx=([0-9A-F]{8}) "
    rb"rx=([0-9A-F]{8})"
)


def monitor_bridge_config(system: str) -> str:
    """Select IDT monitor boot and the external telephone PCM bridge."""
    return f"""<?xml version="1.0"?>
<mameconfig version="10">
    <system name="{system}">
        <input>
            <port tag=":BOOT_MODE" type="CONFIG"
                  mask="8" defvalue="8" value="0" />
            <port tag=":PHONE_PEER" type="CONFIG"
                  mask="3" defvalue="1" value="2" />
        </input>
    </system>
</mameconfig>
"""


def automation_script(
    transmit_word: int,
    expected_word: int,
    *,
    start_frame: int = 120,
    result_frame: int = 240,
) -> str:
    """Return a continuous telecom-DMA stream and peer-word check."""
    return f"""local machine = manager.machine
local program = machine.devices[":maincpu"].spaces["program"]
local frames = 0

local TX, RX, WORDS = 0x{TX_BUFFER:08x}, 0x{RX_BUFFER:08x}, {WORDS}
local SIB_SIZE, SIB_CONTROL, SIB_DMA = 0x10c00060, 0x10c00074, 0x10c00090
local TEL_RX_START, TEL_TX_START = 0x10c0006c, 0x10c00070
local SIB_SF0_AUX = 0x10c00080

emu.register_frame_done(function()
    frames = frames + 1
    if frames == {start_frame} then
        for index = 0, WORDS - 1 do
            program:write_u32(TX + index * 4, 0x{transmit_word:08x})
            program:write_u32(RX + index * 4, 0)
        end
        program:write_u32(TEL_TX_START, TX)
        program:write_u32(TEL_RX_START, RX)
        -- Betty connected plus off-hook.
        program:write_u32(SIB_SF0_AUX, 0x04000200)
        program:write_u32(SIB_SIZE, ((WORDS - 1) * 4) & 0x3ffc)
        -- 7,200 samples/s, SIB and telecom enabled, no internal loopback.
        program:write_u32(SIB_CONTROL, (0x27 << 16) | 0x20 | 0x01)
        -- Continuous RX and TX.
        program:write_u32(SIB_DMA, 0x0003)
    elseif frames == {result_frame} then
        local received = 0
        for index = 0, WORDS - 1 do
            if program:read_u32(RX + index * 4) == 0x{expected_word:08x} then
                received = received + 1
            end
        end
        local dma = program:read_u32(SIB_DMA)
        print(string.format(
            "PHONE_BRIDGE_RESULT received=%d/%d expected=%08X "
            .. "enables=%d tx=%08X rx=%08X",
            received, WORDS, 0x{expected_word:08x}, dma & 3,
            program:read_u32(TEL_TX_START),
            program:read_u32(TEL_RX_START)))
        machine:exit()
    end
end)
"""


def parse_result(output: bytes) -> dict[str, int] | None:
    """Parse one machine's bridge checkpoint."""
    match = RESULT_PATTERN.search(output)
    if match is None:
        return None
    names = ("received", "words", "expected", "enables", "tx", "rx")
    return {
        name: int(value, 16 if name in {"expected", "tx", "rx"} else 10)
        for name, value in zip(names, match.groups(), strict=True)
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--system", default="datarover840")
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    workdir = args.workdir.expanduser().resolve()
    if not mame.is_file():
        print(f"error: MAME executable not found: {mame}", file=sys.stderr)
        return 2
    if not rompath.is_dir():
        print(f"error: ROM path not found: {rompath}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = workdir / f"{stamp}-{os.getpid()}"
    run_dir.mkdir(parents=True)
    relay = PcmRelay()
    relay.start()
    processes: list[subprocess.Popen[bytes]] = []
    outputs: list[bytes] = []
    transmit_words = (0x1111_2222, 0x3333_4444)
    try:
        for index in range(2):
            peer_dir = run_dir / f"peer-{index + 1}"
            cfg_dir = peer_dir / "cfg"
            nvram_dir = peer_dir / "nvram"
            cfg_dir.mkdir(parents=True)
            nvram_dir.mkdir()
            (cfg_dir / f"{args.system}.cfg").write_text(
                monitor_bridge_config(args.system),
                encoding="utf-8",
            )
            lua_path = peer_dir / "telephone-bridge.lua"
            lua_path.write_text(
                automation_script(
                    transmit_words[index],
                    transmit_words[1 - index],
                ),
                encoding="utf-8",
            )
            command = [
                str(mame),
                args.system,
                "-rompath",
                str(rompath),
                "-cfg_directory",
                str(cfg_dir),
                "-nvram_directory",
                str(nvram_dir),
                "-autoboot_delay",
                "0",
                "-autoboot_script",
                str(lua_path),
                "-bitb",
                f"socket.127.0.0.1:{relay.port}",
                "-video",
                "none",
                "-sound",
                "none",
                "-videodriver",
                "dummy",
                "-audiodriver",
                "dummy",
                "-throttle",
                "-skip_gameinfo",
                "-seconds_to_run",
                "10",
            ]
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=mame.parent,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
            )

        for index, process in enumerate(processes):
            try:
                output, _ = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate()
            outputs.append(output)
            (run_dir / f"peer-{index + 1}" / "mame-output.txt").write_bytes(
                output
            )
    except OSError as error:
        print(f"error: unable to run bridge peers: {error}", file=sys.stderr)
        return 2
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
        relay.stop()

    results = [parse_result(output) for output in outputs]
    valid = all(
        result is not None
        and result["received"] == result["words"] == WORDS
        and result["enables"] == 3
        and result["tx"] == TX_BUFFER
        and result["rx"] == RX_BUFFER
        for result in results
    )
    if relay.error or min(relay.forwarded, default=0) < WORDS * 4 or not valid:
        print(
            "FAIL: bidirectional telephone PCM bridge incomplete "
            f"(results={results!r}, forwarded={relay.forwarded!r}, "
            f"relay_error={relay.error!r}); artifacts: {run_dir}",
            file=sys.stderr,
        )
        return 1

    print(
        "PASS: two independent DataRovers exchanged all 64 words in both "
        "continuous telecom DMA rings through the external PCM bridge"
    )
    print(
        f"Relay bytes: peer 1 → peer 2 {relay.forwarded[0]}, "
        f"peer 2 → peer 1 {relay.forwarded[1]}"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
