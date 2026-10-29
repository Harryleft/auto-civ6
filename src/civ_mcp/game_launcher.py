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
from pathlib import Path
from dataclasses import dataclass
from civ_mcp.platform_paths import single_save_directory, windows_steam_roots

log = logging.getLogger(__name__)

STEAM_APP_ID = "289070"
STEAM_PROCESS_LOG = Path.home() / "Library/Application Support/Steam/logs/gameprocess_log.txt"
MACOS_STEAM_BUNDLE = Path.home() / "Library/Application Support/Steam/Steam.AppBundle/Steam"
STEAM_RELEASE_TIMEOUT = 30.0

# Only ever kill processes matching this exact name pattern — NEVER "steam"
_ALLOWED_PROCESS_PATTERNS = ["Civ6"]

# Platform-specific paths
SINGLE_SAVE_DIR = single_save_directory()
if sys.platform == "darwin":
    _PROCESS_NAMES = ["Civ6_Exe_Child", "Civ6_Exe", "CivilizationVI", "Civ6"]
elif sys.platform == "win32":
    _PROCESS_NAMES = ["CivilizationVI.exe", "CivilizationVI_DX12.exe"]
else:
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
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    errors="replace",
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
                stdin=subprocess.DEVNULL,
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
            stdin=subprocess.DEVNULL,
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
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    errors="replace",
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
                    stdin=subprocess.DEVNULL,
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

    # 优先使用实际注册的 Steam 位置，兼容非 C 盘安装。
    steam_roots = windows_steam_roots()

    for steam_root in steam_roots:
        vdf_path = os.path.join(steam_root, "steamapps", "libraryfolders.vdf")
        try:
            content = Path(vdf_path).read_text(encoding="utf-8") if os.path.isfile(vdf_path) else ""

            # Parse library paths and app IDs from VDF
            import re

            libs = re.findall(r'"path"\s+"([^"]+)"', content)

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


def _steam_tracking_game() -> bool | None:
    """Read Steam's own process bookkeeping, without starting another app.

    Local macOS gameprocess_log.txt records adding tracked PIDs and the final
    Remove-from-running-list transition. Unknown format/missing file is not
    readiness proof. Other platforms retain a conservative launch path.
    """
    if sys.platform != "darwin":
        return None
    try:
        with STEAM_PROCESS_LOG.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 131072))
            lines = stream.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if f"Remove {STEAM_APP_ID} from running list" in line:
            return False
        if f"AppID {STEAM_APP_ID} adding PID" in line:
            return True
    return None


def _wait_for_steam_release() -> str | None:
    """Return a failure before launch if Steam still owns the exited game."""
    if sys.platform != "darwin":
        return None
    deadline = time.monotonic() + STEAM_RELEASE_TIMEOUT
    while time.monotonic() < deadline:
        tracked = _steam_tracking_game()
        if tracked is False:
            return None
        if tracked is None:
            # Explicitly a fallback, not a completion signal. It covers both
            # an active shutdown and a process that disappeared before kill.
            log.warning("Steam readiness unavailable; applying a 15s conservative grace, not a readiness guarantee")
            time.sleep(min(15, max(0, deadline - time.monotonic())))
            return None
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    return "FAILED: Steam still tracks Civ6 after exit; no launch submitted."


def _launch_windows_application(command: list[str]) -> None:
    """委托交互式桌面启动应用，避免 MCP 退出时清理 Steam 和游戏。"""
    try:
        import pythoncom
        import win32com.client

        # 此函数由工作线程调用，COM 初始化与释放必须在同一线程完成。
        pythoncom.CoInitialize()
        try:
            # 取现有桌面的 Application；新建 Shell.Application 仍可能在 MCP 内执行。
            windows = win32com.client.Dispatch("{9BA05972-F6A8-11CF-A442-00A0C90A8F39}")
            # SWC_DESKTOP=8，SWFO_NEEDDISPATCH=1，不要求用户打开文件管理器窗口。
            desktop = windows.FindWindowSW(0, 0, 8, 0, 1)
            shell = desktop.Document.Application
            try:
                shell.ShellExecute(
                    command[0], subprocess.list2cmdline(command[1:]), "", "open", 1,
                )
            finally:
                del shell
                del desktop
                del windows
        finally:
            pythoncom.CoUninitialize()
    except Exception as exc:
        # 不回退到受 MCP 作业对象管理的 Popen，以免重现会话结束即退出。
        raise OSError(f"Windows desktop launch failed: {exc}") from exc


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

    release_error = _wait_for_steam_release()
    if release_error:
        return release_error

    # Launch via Steam
    if sys.platform == "darwin":
        options = dict(stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        uri = f"steam://run/{STEAM_APP_ID}"
        dispatch = subprocess.run(["open", uri], **options)
        # Steam's self-update can leave LaunchServices pointing at its old bundle.
        # -600 means no process received the URL; retry only this known dispatch failure.
        stderr = getattr(dispatch, "stderr", b"")
        detail = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr
        steam_executable = MACOS_STEAM_BUNDLE / "Contents/MacOS/steam_osx"
        if dispatch.returncode and "error -600" in detail and steam_executable.is_file():
            subprocess.Popen([str(steam_executable), "-applaunch", STEAM_APP_ID],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        elif dispatch.returncode:
            detail = dispatch.stderr
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            return f"Error: Steam URL dispatch failed ({dispatch.returncode}): {detail.strip()}"
    elif sys.platform == "win32":
        # 使用桌面 Shell 代理，Steam 及游戏不归 MCP 客户端进程树管理。
        steam_exe = next((os.path.join(root, "steam.exe") for root in windows_steam_roots()
                          if os.path.isfile(os.path.join(root, "steam.exe"))), None)
        launch_command = [steam_exe, "-applaunch", STEAM_APP_ID] if steam_exe else [
            f"steam://run/{STEAM_APP_ID}"
        ]
        _launch_windows_application(launch_command)
    else:
        subprocess.Popen(
            ["steam", f"-applaunch", STEAM_APP_ID],
            stdin=subprocess.DEVNULL,
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
            _launch_windows_application([exe])
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


@dataclass(frozen=True)
class ProcessResult:
    confirmed: bool
    message: str


async def stop_game() -> ProcessResult:
    """Stop once and determine completion from the actual process fact."""
    message = await kill_game()
    stopped = not await asyncio.to_thread(is_game_running)
    return ProcessResult(stopped, message)


async def start_game() -> ProcessResult:
    """Launch once; presentation text cannot establish process readiness."""
    message = await launch_game()
    running = await asyncio.to_thread(is_game_running)
    # A successful startup probe can briefly occupy FireTuner's sole client
    # slot. Recheck readiness within a bounded window without launching again.
    ready = running and await asyncio.to_thread(_wait_for_tuner_port, timeout=30)
    return ProcessResult(ready, message)


async def restart_and_load(save_name: str | None = None) -> str:
    """Compatibility entry point using the single verified replacement owner."""
    from civ_mcp.connection import GameConnection
    from civ_mcp.game_state import GameState

    conn = GameConnection()
    try:
        return await GameState(conn).restart_and_load(save_name)
    finally:
        await conn.disconnect()
