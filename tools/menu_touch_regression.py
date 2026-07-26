#!/usr/bin/env python3
"""Verify touch input after opening and closing MAME's Tab menu."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAME = REPO_ROOT.parent / "mame" / "datarover"
DEFAULT_ROMPATH = Path.home() / "fun" / "magic-cap-assets" / "roms"
DEFAULT_WORKDIR = (
    Path.home()
    / "fun"
    / "magic-cap-assets"
    / "runtime"
    / "menu-touch-regression"
)
EXPECTED_BINDING = (
    "GUNCODE_1_XAXIS",
    "GUNCODE_1_YAXIS",
    "GUNCODE_1_BUTTON1",
)
BINDING_PATTERN = re.compile(
    rb"MENU_TOUCH_BINDING x=(\S+) y=(\S+) button=(\S+)"
)
EVENT_PATTERN = re.compile(
    rb"MENU_TOUCH frame=(\d+) menu=([01]) "
    rb"button=([01]) x=([0-9A-F]{4}) y=([0-9A-F]{4})"
)


@dataclass(frozen=True)
class TouchEvent:
    frame: int
    menu: bool
    button: bool
    x: int
    y: int


def automation_script() -> str:
    """Return Lua that traces binding and raw port state."""
    return r"""local machine = manager.machine
local ui = manager.ui
local ports = machine.ioport.ports
local button_port = ports[":TOUCH_BUTTON"]
local x_port = ports[":TOUCH_X"]
local y_port = ports[":TOUCH_Y"]
local button_field = button_port.fields["Touch screen"]
local x_field = x_port.fields["Pen X"]
local y_field = y_port.fields["Pen Y"]
local frames = 0
local last_button = -1
local last_x = -1
local last_y = -1
local last_menu = nil

print("MENU_TOUCH_BINDING x="
    .. machine.input:seq_to_tokens(x_field:input_seq("standard"))
    .. " y="
    .. machine.input:seq_to_tokens(y_field:input_seq("standard"))
    .. " button="
    .. machine.input:seq_to_tokens(button_field:input_seq("standard")))

emu.register_frame_done(function()
    frames = frames + 1
    local button = button_port:read() & 1
    local x = x_port:read()
    local y = y_port:read()
    local menu = ui.menu_active
    if button ~= last_button or x ~= last_x or y ~= last_y
            or menu ~= last_menu then
        print(string.format(
            "MENU_TOUCH frame=%d menu=%d button=%d x=%04X y=%04X",
            frames, menu and 1 or 0, button, x, y))
        last_button = button
        last_x = x
        last_y = y
        last_menu = menu
    end
end)
"""


def parse_trace(
    output: bytes,
) -> tuple[tuple[str, str, str] | None, list[TouchEvent]]:
    """Extract the effective bindings and state changes from MAME output."""
    binding_match = BINDING_PATTERN.search(output)
    binding = (
        tuple(part.decode("ascii") for part in binding_match.groups())
        if binding_match
        else None
    )
    events = [
        TouchEvent(
            frame=int(match.group(1)),
            menu=match.group(2) == b"1",
            button=match.group(3) == b"1",
            x=int(match.group(4), 16),
            y=int(match.group(5), 16),
        )
        for match in EVENT_PATTERN.finditer(output)
    ]
    return binding, events


def evaluate_trace(output: bytes) -> str | None:
    """Return a failure explanation, or None when the trace passes."""
    binding, events = parse_trace(output)
    if binding != EXPECTED_BINDING:
        return (
            f"touch bindings are {binding!r}, expected one lightgun device "
            f"{EXPECTED_BINDING!r}"
        )
    if not events:
        return "MAME produced no touch state events"

    opened = next((index for index, event in enumerate(events) if event.menu), None)
    if opened is None:
        return "the Tab menu never became active"
    closed = next(
        (
            index
            for index, event in enumerate(events[opened + 1 :], opened + 1)
            if not event.menu
        ),
        None,
    )
    if closed is None:
        return "the second Tab press did not return to emulation"

    pre_down = next(
        (
            event
            for event in reversed(events[:opened])
            if event.button and not event.menu
        ),
        None,
    )
    if pre_down is None:
        return "the pre-menu pen press did not reach the input port"

    post_down = next(
        (
            event
            for event in events[closed + 1 :]
            if event.button and not event.menu
        ),
        None,
    )
    if post_down is None:
        return "the post-menu pen press did not reach the input port"
    if (post_down.x, post_down.y) == (pre_down.x, pre_down.y):
        return "the pen button recovered but its absolute position stayed stale"
    return None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mame",
        type=Path,
        default=DEFAULT_MAME,
        help=f"DataRover MAME executable (default: {DEFAULT_MAME})",
    )
    parser.add_argument(
        "--rompath",
        type=Path,
        default=DEFAULT_ROMPATH,
        help=f"MAME ROM search path (default: {DEFAULT_ROMPATH})",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=DEFAULT_WORKDIR,
        help=f"persistent artifact directory (default: {DEFAULT_WORKDIR})",
    )
    return parser.parse_args(argv)


def _run_xdotool(display: str, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env["DISPLAY"] = display
    return subprocess.run(
        ["xdotool", *arguments],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _find_window(display: str, mame_pid: int) -> str | None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = _run_xdotool(
            display,
            "search",
            "--onlyvisible",
            "--pid",
            str(mame_pid),
        )
        if result.returncode == 0:
            windows = result.stdout.decode("ascii").split()
            if windows:
                return windows[-1]
        time.sleep(0.1)
    return None


def _xdotool_checked(display: str, *arguments: str) -> None:
    result = _run_xdotool(display, *arguments)
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"xdotool {' '.join(arguments)} failed: {detail}")


def _tap_tab(display: str, window: str) -> None:
    _xdotool_checked(display, "keydown", "--window", window, "Tab")
    time.sleep(0.25)
    _xdotool_checked(display, "keyup", "--window", window, "Tab")


def run_regression(args: argparse.Namespace) -> int:
    mame = args.mame.expanduser().resolve()
    rompath = args.rompath.expanduser().resolve()
    workdir = args.workdir.expanduser().resolve()
    missing = [program for program in ("Xvfb", "xdotool") if not shutil.which(program)]

    if not mame.is_file():
        print(f"error: MAME executable not found: {mame}", file=sys.stderr)
        return 2
    if not rompath.is_dir():
        print(f"error: ROM path not found: {rompath}", file=sys.stderr)
        return 2
    if missing:
        print(
            "error: install the host tools needed for the real UI regression: "
            + " ".join(missing),
            file=sys.stderr,
        )
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = workdir / f"{stamp}-{os.getpid()}"
    cfg_dir = run_dir / "cfg"
    nvram_dir = run_dir / "nvram"
    cfg_dir.mkdir(parents=True)
    nvram_dir.mkdir()
    lua_path = run_dir / "menu-touch-regression.lua"
    log_path = run_dir / "mame-output.txt"
    xvfb_log_path = run_dir / "xvfb-output.txt"
    lua_path.write_text(automation_script(), encoding="utf-8")

    xvfb_log = xvfb_log_path.open("wb")
    xvfb = subprocess.Popen(
        ["Xvfb", "-displayfd", "1", "-screen", "0", "1024x768x24", "-nolisten", "tcp"],
        stdout=subprocess.PIPE,
        stderr=xvfb_log,
    )
    mame_process: subprocess.Popen[bytes] | None = None
    try:
        if xvfb.stdout is None:
            raise RuntimeError("Xvfb did not expose its display number")
        display_number = xvfb.stdout.readline().decode("ascii").strip()
        if not display_number:
            raise RuntimeError("Xvfb did not start")
        display = f":{display_number}"
        env = os.environ.copy()
        env.update(
            {
                "DISPLAY": display,
                "SDL_VIDEODRIVER": "x11",
                "SDL_VIDEO_HIGHDPI_DISABLED": "1",
            }
        )
        command = [
            str(mame),
            "datarover840",
            "-rompath",
            str(rompath),
            "-cfg_directory",
            str(cfg_dir),
            "-nvram_directory",
            str(nvram_dir),
            "-window",
            "-resolution",
            "720x480",
            "-skip_gameinfo",
            "-ui_active",
            "-nokeepaspect",
            "-view",
            "LCD",
            "-lightgun",
            "-lightgun_device",
            "lightgun",
            "-sound",
            "none",
            "-autoboot_delay",
            "0",
            "-autoboot_script",
            str(lua_path),
            "-seconds_to_run",
            "10",
        ]
        with log_path.open("wb") as mame_log:
            mame_process = subprocess.Popen(
                command,
                cwd=mame.parent,
                env=env,
                stdout=mame_log,
                stderr=subprocess.STDOUT,
            )
            window = _find_window(display, mame_process.pid)
            if window is None:
                raise RuntimeError("MAME did not create its test window")

            _xdotool_checked(display, "windowfocus", "--sync", window)
            time.sleep(0.6)
            _xdotool_checked(
                display, "mousemove_relative", "--sync", "--", "-100", "-70"
            )
            _xdotool_checked(display, "mousedown", "1")
            time.sleep(0.3)
            _xdotool_checked(display, "mouseup", "1")
            time.sleep(0.4)
            _tap_tab(display, window)
            time.sleep(0.7)
            _tap_tab(display, window)
            time.sleep(0.7)
            _xdotool_checked(
                display, "mousemove_relative", "--sync", "--", "180", "110"
            )
            _xdotool_checked(display, "mousedown", "1")
            time.sleep(0.3)
            _xdotool_checked(display, "mouseup", "1")
            returncode = mame_process.wait(timeout=30)
            if returncode:
                raise RuntimeError(f"MAME exited with status {returncode}")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        if mame_process and mame_process.poll() is None:
            mame_process.terminate()
            try:
                mame_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                mame_process.kill()
                mame_process.wait()
        print(f"error: {error}; artifacts: {run_dir}", file=sys.stderr)
        return 2
    finally:
        if xvfb.poll() is None:
            xvfb.terminate()
            try:
                xvfb.wait(timeout=3)
            except subprocess.TimeoutExpired:
                xvfb.kill()
                xvfb.wait()
        xvfb_log.close()

    output = log_path.read_bytes()
    failure = evaluate_trace(output)
    if failure:
        print(f"FAIL: {failure}; see {log_path}", file=sys.stderr)
        return 1

    binding, events = parse_trace(output)
    assert binding is not None
    post = next(
        event
        for index, event in enumerate(events)
        if event.button
        and not event.menu
        and any(prior.menu for prior in events[:index])
    )
    print(
        "PASS: touch position and pen-down recovered after the Tab menu "
        f"at ({post.x:#06x}, {post.y:#06x})"
    )
    print(f"Bindings: {' / '.join(binding)}")
    print(f"Artifacts: {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run_regression(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
