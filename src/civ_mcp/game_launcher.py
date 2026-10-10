"""Game process lifecycle — kill, launch, save discovery, crash-dialog dismissal.

Safety guardrails:
- Only processes with 'Civ6' in the name are ever killed (pgrep exact match).
- Steam is never killed.
- No other user processes are touched.

Save loading lives in game_lifecycle.py (native FrontEnd API, no GUI
automation).

Platform notes:
- macOS: launches via Steam (`open steam://run/...`). The Aspyr LaunchPad is
  bypassed with Steam Launch Options — see docs/research/bypassing_aspyr_launcher.md.
- Windows: launches via Steam URI (falls back to direct EXE launch).
- Linux: launches via `steam -applaunch` (native or Proton).
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import socket
import subprocess
import sys
import time

log = logging.getLogger(__name__)

STEAM_APP_ID = "289070"

# Only ever kill processes matching this exact name pattern — NEVER "steam"
_ALLOWED_PROCESS_PATTERNS = ["Civ6"]

# Platform-specific paths
if sys.platform == "darwin":
    SINGLE_SAVE_DIR = os.path.expanduser(
        "~/Library/Application Support/Sid Meier's Civilization VI/"
        "Sid Meier's Civilization VI/Saves/Single"
    )
    _PROCESS_NAMES = ["Civ6_Exe_Child", "Civ6_Exe", "CivilizationVI", "Civ6"]
elif sys.platform == "win32":
    SINGLE_SAVE_DIR = os.path.expanduser(
        "~/Documents/My Games/Sid Meier's Civilization VI/Saves/Single"
    )
    _PROCESS_NAMES = ["CivilizationVI.exe", "CivilizationVI_DX12.exe"]
else:
    SINGLE_SAVE_DIR = os.path.expanduser(
        "~/.local/share/aspyr-media/Sid Meier's Civilization VI/Saves/Single"
    )
    _PROCESS_NAMES = ["Civ6", "Civ6_Sub", "CivilizationVI"]

SAVE_DIR = os.path.join(SINGLE_SAVE_DIR, "auto")

# How long to wait for the FireTuner port after the process appears.
_PORT_POLL_TIMEOUT = 180
_PORT_POLL_INTERVAL = 3


# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------


def is_game_running() -> bool:
    """Check if the Civ 6 process is running."""
    if sys.platform == "win32":
        for name in _PROCESS_NAMES:
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if name.lower() in result.stdout.lower():
                    return True
            except Exception:
                pass
        return False

    # macOS / Linux
    for name in _PROCESS_NAMES:
        try:
            result = subprocess.run(
                ["pgrep", "-x", name],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass
    return False


def _dismiss_crash_dialog() -> bool:
    """Dismiss macOS crash reporter dialog via accessibility API. Returns True if clicked."""
    if sys.platform != "darwin":
        return False
    script = """
    tell application "System Events"
        try
            set appList to name of every process
            repeat with appName in appList
                if appName contains "Problem Reporter" or appName contains "Civilization" then
                    tell process appName
                        if exists button "Ignore" of window 1 then
                            click button "Ignore" of window 1
                            return "clicked"
                        end if
                    end tell
                end if
            end repeat
        end try
    end tell
    return "none"
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "clicked" in result.stdout
    except Exception:
        return False


def _kill_game_sync() -> str:
    """Kill the game process (blocking)."""
    if not is_game_running():
        return "Civ 6 is not running."

    if sys.platform == "win32":
        killed = False
        for name in _PROCESS_NAMES:
            try:
                result = subprocess.run(
                    ["taskkill", "/F", "/IM", name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    killed = True
            except Exception:
                pass
        if not killed:
            return "WARNING: Could not kill game process."
    else:
        for name in _PROCESS_NAMES:
            try:
                subprocess.run(
                    ["pkill", "-9", "-x", name],
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass

    # Wait for process to disappear
    time.sleep(2)
    if is_game_running():
        time.sleep(3)
        if is_game_running():
            return "WARNING: Game process is still running."
    return "Killed Civ 6."


# ---------------------------------------------------------------------------
# Process + port waiting
# ---------------------------------------------------------------------------


def _wait_for_game_process(timeout: int = 30) -> int | None:
    """Wait for the game process to appear. Returns seconds waited, or None."""
    for i in range(timeout):
        if is_game_running():
            return i
        time.sleep(1)
    return None


def _is_tuner_port_open() -> bool:
    """Check if the FireTuner TCP port (4318) is accepting connections."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(2)
        s.connect(("127.0.0.1", _TUNER_PORT))
        return True
    except (ConnectionRefusedError, OSError):
        return False
    finally:
        s.close()


# Tuner port from connection.py — keep in sync
_TUNER_PORT = 4318


def _wait_for_tuner_port(timeout: int = _PORT_POLL_TIMEOUT) -> bool:
    """Wait for the FireTuner port to open. Returns True if open."""
    # At least one check, even when timeout < interval.
    for _ in range(max(1, int(timeout / _PORT_POLL_INTERVAL))):
        if _is_tuner_port_open():
            return True
        time.sleep(_PORT_POLL_INTERVAL)
    return _is_tuner_port_open()


def _find_game_exe_win32() -> str | None:
    """Find the Civ6 executable on Windows via Steam library folders.

    Reads libraryfolders.vdf to find which Steam library has app 289070,
    then returns the path to CivilizationVI.exe.
    """
    if sys.platform != "win32":
        return None

    # Standard Steam install locations
    steam_roots = [
        os.path.expandvars(r"C:\Program Files (x86)\Steam"),
        os.path.expandvars(r"C:\Program Files\Steam"),
    ]

    for steam_root in steam_roots:
        vdf_path = os.path.join(steam_root, "steamapps", "libraryfolders.vdf")
        if not os.path.exists(vdf_path):
            continue

        try:
            with open(vdf_path, encoding="utf-8") as f:
                content = f.read()

            # Parse library paths and app IDs from VDF
            import re

            libs = re.findall(r'"path"\s+"([^"]+)"', content)
            apps = re.findall(r'"289070"', content)

            # Check default library first
            default_exe = os.path.join(
                steam_root,
                "steamapps",
                "common",
                "Sid Meier's Civilization VI",
                "Base",
                "Binaries",
                "Win64Steam",
                "CivilizationVI.exe",
            )
            if os.path.exists(default_exe):
                return default_exe

            # Check other libraries
            for lib in libs:
                lib = lib.replace("\\\\", "\\")
                exe = os.path.join(
                    lib,
                    "steamapps",
                    "common",
                    "Sid Meier's Civilization VI",
                    "Base",
                    "Binaries",
                    "Win64Steam",
                    "CivilizationVI.exe",
                )
                if os.path.exists(exe):
                    return exe
        except Exception as e:
            log.warning("Failed to parse libraryfolders.vdf: %s", e)

    # Fallback: default Steam path
    default = os.path.expandvars(
        r"C:\Program Files (x86)\Steam\steamapps\common"
        r"\Sid Meier's Civilization VI\Base\Binaries\Win64Steam\CivilizationVI.exe"
    )
    return default if os.path.exists(default) else None


def _launch_game_sync() -> str:
    """Launch Civ 6 and wait for the FireTuner port to open. Blocking.

    Launches via Steam. On macOS the Aspyr LaunchPad is bypassed via Steam
    Launch Options (see docs/research/bypassing_aspyr_launcher.md); if the
    port never opens on macOS, check that bypass.
    On Windows, falls back to direct EXE launch if steam://run fails.
    """
    # Dismiss any crash reporter dialogs blocking relaunch
    _dismiss_crash_dialogs_sync()

    if is_game_running():
        if _is_tuner_port_open():
            return "Game already running, FireTuner port is open."
        return "Error: Civ6 is running but FireTuner is unavailable. EnableTuner and wait for startup, or explicitly use restart_and_load."

    # Launch via Steam
    if sys.platform == "darwin":
        subprocess.run(
            ["open", f"steam://run/{STEAM_APP_ID}"],
            capture_output=True,
            timeout=10,
        )
    elif sys.platform == "win32":
        subprocess.Popen(
            ["cmd", "/c", "start", f"steam://run/{STEAM_APP_ID}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            ["steam", f"-applaunch", STEAM_APP_ID],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    log.info("Launched Civ 6 via Steam, waiting for process...")

    # Wait for actual game process (Linux/Proton can be slow to start)
    waited = _wait_for_game_process(timeout=60)
    if waited is None and sys.platform == "win32":
        # Fallback: steam://run may not work in all environments —
        # try direct exe launch
        exe = _find_game_exe_win32()
        if exe:
            log.info("steam://run failed, trying direct exe: %s", exe)
            subprocess.Popen(
                [exe],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            waited = _wait_for_game_process(timeout=60)

    if waited is None:
        return "FAILED: Game process not detected after launch. Check Steam."

    log.info("Game process started after %ds, waiting for FireTuner port...", waited)
    if _wait_for_tuner_port():
        return (
            f"Game launched. Process started after {waited}s, "
            "FireTuner port is open."
        )

    msg = (
        f"FAILED: Game launched (process after {waited}s) but FireTuner "
        f"port not detected within {_PORT_POLL_TIMEOUT}s."
    )
    if sys.platform == "darwin":
        msg += (
            " If the Aspyr LaunchPad is showing, configure the Steam "
            "Launch Options bypass (docs/research/bypassing_aspyr_launcher.md)."
        )
    return msg


# ---------------------------------------------------------------------------
# Crash dialog dismissal (accessibility / Win32 APIs — no image recognition)
# ---------------------------------------------------------------------------


def _dismiss_crash_dialogs_sync() -> list[str]:
    """Find and dismiss crash reporter / exception dialogs.

    Windows: Firaxis crash reporter / unhandled-exception dialogs are standard
    Win32 dialogs that appear on top of the game after an
    EXCEPTION_ACCESS_VIOLATION or similar crash.  The game continues running
    underneath but Lua calls return degraded data until the dialogs are
    dismissed.

    macOS: dismisses the Problem Reporter via the accessibility API.

    Returns list of dismissed dialog descriptions.
    """
    if sys.platform == "darwin":
        return ["Problem Reporter (clicked 'Ignore')"] if _dismiss_crash_dialog() else []

    if sys.platform != "win32":
        return []

    try:
        import win32con
        import win32gui
    except ImportError:
        return []

    dismissed: list[str] = []

    # Dialog signatures: (title_substring, button_text_to_click)
    _CRASH_DIALOGS = [
        ("Unhandled Exception", "OK"),
        ("Firaxis Crash Reporter", "No"),
    ]

    for title_substr, target_button in _CRASH_DIALOGS:
        # Find all top-level windows matching the title
        def _enum_callback(hwnd: int, results: list) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if title_substr in title:
                results.append(hwnd)
            return True

        matches: list[int] = []
        try:
            win32gui.EnumWindows(_enum_callback, matches)
        except Exception:
            continue

        for hwnd in matches:
            # Find the target button among child windows
            def _enum_children(child_hwnd: int, buttons: list) -> bool:
                try:
                    text = win32gui.GetWindowText(child_hwnd)
                    # Strip Win32 accelerator prefix (&Yes -> Yes, &No -> No)
                    clean = text.replace("&", "")
                    if clean == target_button:
                        buttons.append(child_hwnd)
                except Exception:
                    pass
                return True

            buttons: list[int] = []
            try:
                win32gui.EnumChildWindows(hwnd, _enum_children, buttons)
            except Exception:
                continue

            if buttons:
                try:
                    # BM_CLICK message to press the button
                    win32gui.SendMessage(buttons[0], win32con.BM_CLICK, 0, 0)
                    title = win32gui.GetWindowText(hwnd)
                    dismissed.append(f"{title} (clicked '{target_button}')")
                    log.info(
                        "Dismissed crash dialog: '%s' -> clicked '%s'",
                        title,
                        target_button,
                    )
                except Exception as e:
                    log.debug("Failed to click '%s': %s", target_button, e)

    return dismissed


async def dismiss_crash_dialogs() -> list[str]:
    """Async wrapper for crash dialog dismissal."""
    return await asyncio.to_thread(_dismiss_crash_dialogs_sync)


# ---------------------------------------------------------------------------
# Save discovery
# ---------------------------------------------------------------------------


def get_latest_autosave() -> str | None:
    """Find the most recent autosave name (without extension)."""
    saves = glob.glob(os.path.join(SAVE_DIR, "AutoSave_*.Civ6Save"))
    if not saves:
        return None
    saves.sort(key=os.path.getmtime, reverse=True)
    return os.path.basename(saves[0]).replace(".Civ6Save", "")


def list_autosaves(limit: int = 10) -> list[str]:
    """List recent autosave names, newest first."""
    saves = glob.glob(os.path.join(SAVE_DIR, "AutoSave_*.Civ6Save"))
    saves.sort(key=os.path.getmtime, reverse=True)
    return [os.path.basename(s).replace(".Civ6Save", "") for s in saves[:limit]]


# ---------------------------------------------------------------------------
# Public async API (for tools.py)
# ---------------------------------------------------------------------------


async def kill_game() -> str:
    """Kill Civ 6 and wait for Steam to deregister."""
    return await asyncio.to_thread(_kill_game_sync)


async def launch_game() -> str:
    """Launch Civ 6 via Steam and wait for process."""
    return await asyncio.to_thread(_launch_game_sync)


async def restart_and_load(save_name: str | None = None) -> str:
    """Kill game, relaunch, and load a save. Full recovery sequence.

    This is the recommended tool for recovering from game hangs.
    Takes 60-120 seconds total.
    """
    results = []

    # Dismiss crash dialogs before kill — they block the process from exiting
    pre_dismissed = await dismiss_crash_dialogs()
    if pre_dismissed:
        log.info("Pre-kill: dismissed %s", pre_dismissed)

    # Step 1: Kill
    kill_result = await kill_game()
    results.append(f"Kill: {kill_result}")

    # Step 2: Launch
    launch_result = await launch_game()
    results.append(f"Launch: {launch_result}")
    if "not detected" in launch_result:
        return " | ".join(results) + " | ABORTED: Game failed to launch."

    # Dismiss any lingering crash dialog from previous session (both platforms)
    await dismiss_crash_dialogs()

    # Step 3: Load save via the native FrontEnd API.
    # After relaunch the game sits at the main menu, which is exactly the
    # context the FrontEnd loader needs.
    from civ_mcp.connection import GameConnection
    from civ_mcp.game_lifecycle import load_save_from_frontend

    conn = GameConnection()
    load_result = None
    for attempt in range(12):
        try:
            await conn.connect()
            break
        except Exception as e:
            log.info("Post-relaunch connect attempt %d failed: %s", attempt + 1, e)
            if attempt == 11:
                load_result = (
                    "FAILED: FireTuner did not accept a connection after relaunch"
                )
            await asyncio.sleep(5)
    if load_result is None:
        load_result = await load_save_from_frontend(conn, save_name)
    results.append(f"Load: {load_result}")

    return " | ".join(results)
