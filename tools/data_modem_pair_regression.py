#!/usr/bin/env python3
"""Train the shipping V.32 ROMs in two DataRovers to paired data mode."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import threading
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
DEFAULT_WORKDIR = ASSETS_ROOT / "runtime" / "data-modem-pair-regression"

STUB = 0x0030_0000
STATE = STUB + 0x800
DONE = STATE + 0x100
OPTIONS = STATE + 0x200
COMMAND = STATE + 0x400
COUNTERS = 0x0030_3000
HALF_DMA_BYTES = 96
MIN_PCM_BYTES = 100_000

SYMBOLS = (
    (0x13C5_9A08, "open"),
    (0x13E4_2F20, "init"),
    (0x13E4_3160, "receive"),
    (0x13E4_31B4, "transmit"),
    (0x13E4_31DC, "install"),
    (0x13E5_0B10, "pump"),
    (0x13E5_0E80, "report_status"),
    (0x13E4_35E0, "report_signal"),
    (0x13C5_AC4C, "status_callback"),
    (0x13E4_B770, "data_mode"),
)
RESULT_PATTERN = re.compile(
    rb"DATA_MODEM_PAIR_RESULT role=(answer|origin) "
    + rb" ".join(name.encode() + rb"=(\d+)" for _, name in SYMBOLS)
    + rb" returned=(\d) detector=(\d+) rates="
    + rb"([0-9A-F]{4}),([0-9A-F]{4}),([0-9A-F]{4}),([0-9A-F]{4}) "
    + rb"enables=(\d+) size=(\d+)"
)


def op(major: int, rs: int = 0, rt: int = 0, immediate: int = 0) -> int:
    return (
        (major << 26)
        | (rs << 21)
        | (rt << 16)
        | (immediate & 0xFFFF)
    )


def r(
    rs: int = 0,
    rt: int = 0,
    rd: int = 0,
    shift: int = 0,
    function: int = 0,
) -> int:
    return (
        (rs << 21)
        | (rt << 16)
        | (rd << 11)
        | (shift << 6)
        | function
    )


def load_address(register: int, address: int) -> list[int]:
    return [
        op(0x0F, rt=register, immediate=address >> 16),
        op(0x0D, rs=register, rt=register, immediate=address),
    ]


def call(address: int) -> list[int]:
    return load_address(25, address) + [
        r(rs=25, rd=31, function=9),
        0,
    ]


def move(destination: int, source: int) -> int:
    return r(rs=source, rd=destination, function=0x21)


def common_prefix() -> list[int]:
    words = [
        op(0x0F, rt=4, immediate=3),
        op(0x23, rs=4, rt=4, immediate=0xCA84),
    ]
    words += call(0x13C5_9A08)
    words += [move(4, 0)]
    words += call(0x13C5_BF80)
    words += [
        op(0x0F, rt=8, immediate=0x30),
        op(0x23, rs=8, rt=23, immediate=0x800),
        op(0x23, rs=8, rt=28, immediate=0x804),
    ]
    return words


def start_telecom(words: list[int]) -> None:
    words += load_address(4, 0xA000_0000 + STATE + 0x300)
    words += call(0x13C2_3B3C)


def softmodem_command(words: list[int], address: int) -> None:
    words += load_address(4, 0xA000_0000 + address)
    words += call(0x13E4_26C4)


def modem_option(words: list[int], operation: int, destination: int) -> None:
    words += [
        op(0x0F, rt=4, immediate=3),
        op(0x23, rs=4, rt=4, immediate=0xCA84),
        op(0x0D, rt=15, immediate=operation),
    ]
    words += call(0x13E9_6448)
    words += load_address(8, 0xA000_0000 + destination)
    words += [op(0x2B, rs=8, rt=2)]


def append_answer_commands(words: list[int]) -> None:
    """Replay the exact command-6/command-2 sequence used by AnswerModem."""
    for operation, destination in (
        (4971, OPTIONS),
        (4973, OPTIONS + 8),
        (4975, OPTIONS + 12),
        (4983, OPTIONS + 24),
        (4985, OPTIONS + 28),
    ):
        modem_option(words, operation, destination)
    words += [
        op(0x0F, rt=4, immediate=3),
        op(0x23, rs=4, rt=4, immediate=0xCA84),
        op(0x0D, rt=5, immediate=0x005C),
    ]
    words += call(0x13C9_334C)
    words += load_address(8, 0xA000_0000 + OPTIONS + 20)
    words += [op(0x2B, rs=8, rt=2)]

    words += load_address(8, 0xA000_0000 + COMMAND)
    words += load_address(10, 0xA000_0000 + OPTIONS + 28)
    words += [
        op(0x0D, rt=9, immediate=6),
        op(0x2B, rs=8, rt=9, immediate=0),
        op(0x23, rs=10, rt=9, immediate=-4),
        op(0x2B, rs=8, rt=9, immediate=4),
        op(0x23, rs=10, rt=9, immediate=0),
        op(0x0D, rs=9, rt=9, immediate=1),
        op(0x2B, rs=8, rt=9, immediate=8),
        op(0x2B, rs=8, rt=0, immediate=12),
        op(0x2B, rs=8, rt=0, immediate=16),
    ]
    softmodem_command(words, COMMAND)

    words += load_address(8, 0xA000_0000 + COMMAND)
    words += load_address(10, 0xA000_0000 + OPTIONS + 20)
    words += [
        op(0x0D, rt=9, immediate=2),
        op(0x2B, rs=8, rt=9, immediate=0),
        op(0x0D, rt=9, immediate=1),
        op(0x2B, rs=8, rt=9, immediate=4),
        op(0x23, rs=10, rt=9, immediate=-20),
        op(0x2B, rs=8, rt=9, immediate=8),
        op(0x09, rt=9, immediate=-1),
        op(0x2B, rs=8, rt=9, immediate=12),
        op(0x23, rs=10, rt=9, immediate=-12),
        op(0x2B, rs=8, rt=9, immediate=16),
        op(0x23, rs=10, rt=9, immediate=-8),
        op(0x2B, rs=8, rt=9, immediate=20),
        op(0x09, rt=9, immediate=-1),
        op(0x2B, rs=8, rt=9, immediate=24),
        op(0x23, rs=10, rt=9, immediate=0),
        op(0x2B, rs=8, rt=9, immediate=28),
    ]
    softmodem_command(words, COMMAND)


def role_words(role: str) -> list[int]:
    if role not in ("answer", "origin"):
        raise ValueError(f"invalid modem role: {role}")
    words = common_prefix()
    if role == "answer":
        append_answer_commands(words)
    else:
        words += [op(0x0D, rt=4, immediate=0x80)]
        words += call(0x13E4_31DC)
    start_telecom(words)
    words += load_address(8, 0xA000_0000 + DONE)
    words += [
        op(0x0D, rt=9, immediate=1),
        op(0x2B, rs=8, rt=9, immediate=4),
        0x1000_FFFF,
        0,
    ]
    return words


def automation_script(
    role: str,
    start_frame: int = 1800,
    result_offset: int = 900,
) -> str:
    words = role_words(role)
    writes = "\n".join(
        f"    program:write_u32(STUB + {index * 4}, 0x{word:08x})"
        for index, word in enumerate(words)
    )
    addresses = ", ".join(f"0x{address:08x}" for address, _ in SYMBOLS)
    fields = " ".join(f"{name}=%d" for _, name in SYMBOLS)
    reads = ", ".join(
        f"program:read_u32(COUNTERS + {index * 4})"
        for index in range(len(SYMBOLS))
    )
    loop = 0xA000_0000 + STUB + (len(words) - 2) * 4
    return f"""local machine = manager.machine
local cpu = machine.devices[":maincpu"]
local program = cpu.spaces["program"]
local frames, injected_frame = 0, 0
local restored, result_printed = false, false
local saved_state = nil
local STUB, DONE = 0x{STUB:08x}, 0x{DONE:08x}
local OPTIONS, COUNTERS = 0x{OPTIONS:08x}, 0x{COUNTERS:08x}
local LOOP = 0x{loop:08x}
local addresses = {{ {addresses} }}
local register_names = {{ "HI", "LO", "SR" }}
for index = 1, 31 do
  table.insert(register_names, "R" .. index)
end
for index,address in ipairs(addresses) do
  local counter = COUNTERS + (index - 1) * 4
  local action = string.format(
    "do d@0x%08x=d@0x%08x+1; g", counter, counter)
  if address == 0x13e42f20 then
    action = string.format(
      "do d@0x%08x=d@0x%08x+1; d@0x00300800=R23; d@0x00300804=R28; g",
      counter, counter)
  end
  cpu.debug:bpset(
    address, string.format("d@0x%08x==0", counter), action)
end
cpu.debug:go()

local function inject()
{writes}
  for index = 0, #addresses - 1 do
    program:write_u32(COUNTERS + index * 4, 0)
  end
  program:write_u32(DONE, 0xffffffff)
  program:write_u32(DONE + 4, 0)
  program:write_u32(0x{STATE + 0x304:08x}, 39)
  program:write_u32(0x10c00080, 0x04000200)
  saved_state = {{ PC = cpu.state["PC"].value }}
  for _,name in ipairs(register_names) do
    saved_state[name] = cpu.state[name].value
  end
  machine.debugger:command("resume :maincpu")
  cpu.state["SR"].value = saved_state["SR"] & 0xfffffffc
  cpu.state["PC"].value = 0xa0300000
  injected_frame = frames
  print("DATA_MODEM_PAIR_INJECT role={role}")
end

emu.register_frame_done(function()
  frames = frames + 1
  local pc = cpu.state["PC"].value
  if saved_state ~= nil and not restored
      and (program:read_u32(DONE + 4) == 1
        or pc == LOOP or pc == LOOP + 4) then
    for _,name in ipairs(register_names) do
      cpu.state[name].value = saved_state[name]
    end
    cpu.state["PC"].value = saved_state.PC
    restored = true
    print("DATA_MODEM_PAIR_RETURN role={role}")
  end
  if frames == {start_frame} then
    inject()
  elseif injected_frame > 0 and not result_printed
      and frames >= injected_frame + {result_offset} then
    result_printed = true
    local v32 = program:read_u32(0x00300800)
    local enables = program:read_u32(0x10c00090) & 3
    local size =
      ((program:read_u32(0x10c00060) & 0x3ffc) >> 2) + 1
    print(string.format(
      "DATA_MODEM_PAIR_RESULT role={role} {fields} returned=%d detector=%d rates=%04X,%04X,%04X,%04X enables=%d size=%d",
      {reads}, restored and 1 or 0,
      program:read_u8(v32 - 0x2006),
      program:read_u16(v32 - 0x1fcf),
      program:read_u16(v32 - 0x1fcd),
      program:read_u16(v32 - 0x1fcb),
      program:read_u16(v32 - 0x1fc9),
      enables, size))
    machine:exit()
  end
end)
"""


def external_bridge_config(system: str) -> str:
    return f"""<?xml version="1.0"?>
<mameconfig version="10"><system name="{system}"><input>
<port tag=":PHONE_PEER" type="CONFIG" mask="3" defvalue="1" value="2" />
</input></system></mameconfig>
"""


def parse_result(output: bytes) -> dict[str, int | str] | None:
    match = RESULT_PATTERN.search(output)
    if match is None:
        return None
    groups = match.groups()
    result: dict[str, int | str] = {"role": groups[0].decode()}
    offset = 1
    for _, name in SYMBOLS:
        result[name] = int(groups[offset])
        offset += 1
    result["returned"] = int(groups[offset])
    result["detector"] = int(groups[offset + 1])
    result["rates"] = tuple(
        int(value, 16) for value in groups[offset + 2 : offset + 6]
    )
    result["enables"] = int(groups[offset + 6])
    result["size"] = int(groups[offset + 7])
    return result


def validate_results(
    results: dict[str, dict[str, int | str]],
    forwarded: list[int],
) -> list[str]:
    failures = []
    for role in ("answer", "origin"):
        result = results.get(role)
        if result is None:
            failures.append(f"{role} did not report a result")
            continue
        for name in (
            "open",
            "init",
            "receive",
            "transmit",
            "install",
            "pump",
            "report_status",
            "data_mode",
            "returned",
        ):
            if not result[name]:
                failures.append(f"{role} missed {name}")
        if result["detector"] != 1:
            failures.append(f"{role} detector did not lock")
        if result["enables"] != 3 or result["size"] != 48:
            failures.append(f"{role} lost its 48-word RX/TX DMA ring")
    if len(results) == 2 and results["answer"]["rates"] != results["origin"]["rates"]:
        failures.append("the peers negotiated different rate words")
    if min(forwarded, default=0) < MIN_PCM_BYTES:
        failures.append(f"insufficient paired PCM: {tuple(forwarded)}")
    if len(forwarded) == 2 and abs(forwarded[0] - forwarded[1]) > HALF_DMA_BYTES:
        failures.append(f"PCM clocks diverged: {tuple(forwarded)}")
    return failures


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mame", type=Path, default=DEFAULT_MAME)
    parser.add_argument("--rompath", type=Path, default=DEFAULT_ROMPATH)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--nvram-source", type=Path, required=True)
    parser.add_argument("--system", default="datarover840")
    return parser.parse_args(argv)


def run_regression(args: argparse.Namespace) -> int:
    args.mame = args.mame.expanduser().resolve()
    args.rompath = args.rompath.expanduser().resolve()
    source = args.nvram_source.expanduser().resolve()
    if not args.mame.is_file():
        print(f"error: MAME executable not found: {args.mame}", file=sys.stderr)
        return 2
    if not args.rompath.is_dir():
        print(f"error: ROM path not found: {args.rompath}", file=sys.stderr)
        return 2
    if args.system != "datarover840":
        print("error: ROM addresses require datarover840", file=sys.stderr)
        return 2
    if not (source / args.system / "ram").is_file():
        print(f"error: NVRAM not found under {source}", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.workdir.expanduser().resolve() / f"{stamp}-{os.getpid()}"
    run_dir.mkdir(parents=True)
    relay = PcmRelay(
        startup_grace=HALF_DMA_BYTES,
        max_skew=HALF_DMA_BYTES,
        capture_limit=500_000,
        read_size=HALF_DMA_BYTES,
    )
    relay.start()
    processes: list[tuple[str, subprocess.Popen[bytes]]] = []
    outputs: dict[str, bytes] = {}
    try:
        for role in ("answer", "origin"):
            peer_dir = run_dir / role
            cfg_dir = peer_dir / "cfg"
            nvram_dir = peer_dir / "nvram"
            cfg_dir.mkdir(parents=True)
            shutil.copytree(source, nvram_dir)
            (cfg_dir / f"{args.system}.cfg").write_text(
                external_bridge_config(args.system), encoding="utf-8"
            )
            lua_path = peer_dir / f"{role}.lua"
            lua_path.write_text(automation_script(role), encoding="utf-8")
            command = [
                str(args.mame),
                args.system,
                "-rompath",
                str(args.rompath),
                "-cfg_directory",
                str(cfg_dir),
                "-nvram_directory",
                str(nvram_dir),
                "-autoboot_script",
                str(lua_path),
                "-autoboot_delay",
                "0",
                "-bitb",
                f"socket.127.0.0.1:{relay.port}",
                "-debug",
                "-debugger",
                "none",
                "-video",
                "none",
                "-sound",
                "none",
                "-videodriver",
                "dummy",
                "-audiodriver",
                "dummy",
                "-nothrottle",
                "-skip_gameinfo",
            ]
            process = subprocess.Popen(
                command,
                cwd=args.mame.parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            processes.append((role, process))
            if not relay.wait_for_peer_count(len(processes), timeout=30):
                raise RuntimeError(f"{role} did not connect to the PCM relay")
            os.kill(process.pid, signal.SIGSTOP)

        def control(index: int, paused: bool) -> None:
            try:
                os.kill(
                    processes[index][1].pid,
                    signal.SIGSTOP if paused else signal.SIGCONT,
                )
            except ProcessLookupError:
                pass

        relay.set_process_controller(control)
        for index, (_, process) in enumerate(processes):
            os.kill(process.pid, signal.SIGCONT)

            def monitor(
                peer_index: int = index,
                peer_process: subprocess.Popen[bytes] = process,
            ) -> None:
                peer_process.wait()
                relay.mark_peer_inactive(peer_index)

            threading.Thread(target=monitor, daemon=True).start()

        for role, process in processes:
            output, _ = process.communicate(timeout=480)
            outputs[role] = output
            (run_dir / role / "mame-output.txt").write_bytes(output)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: paired modem run failed: {error}", file=sys.stderr)
        return 1
    finally:
        relay.disable_process_control()
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
        relay.stop()

    parsed = {
        role: result
        for role, output in outputs.items()
        if (result := parse_result(output)) is not None
    }
    failures = validate_results(parsed, relay.forwarded)
    if relay.error is not None:
        failures.append(f"PCM relay failed: {relay.error}")
    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        print(f"Artifacts: {run_dir}", file=sys.stderr)
        return 1

    rates = parsed["answer"]["rates"]
    assert isinstance(rates, tuple)
    print(
        "PASS: paired shipping V.32 ROMs synchronized through R2/R3, "
        "negotiated matching rates, reported status, and entered data mode "
        f"(rates={','.join(f'{rate:04x}' for rate in rates)}, "
        f"PCM={tuple(relay.forwarded)})"
    )
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
