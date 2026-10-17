"""Isolated process, socket and save-discovery tests for the launcher.

All external process/socket calls are blocked unless a test supplies a fake.
Save discovery uses temporary directories; no game or real saves are touched.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from civ_mcp import game_launcher as gl


@pytest.fixture(autouse=True)
def isolated_launcher(monkeypatch, tmp_path):
    def external_call(*args, **kwargs):
        raise AssertionError("Test attempted an external process or socket call")

    monkeypatch.setattr(subprocess, "run", external_call)
    monkeypatch.setattr(subprocess, "Popen", external_call)
    real_socket = socket.socket

    def local_socket(family=socket.AF_INET, *args, **kwargs):
        # asyncio uses an AF_UNIX socketpair for its internal wake-up pipe.
        if family == socket.AF_UNIX:
            return real_socket(family, *args, **kwargs)
        return external_call(family, *args, **kwargs)

    monkeypatch.setattr(socket, "socket", local_socket)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(time, "sleep", lambda _: None)
    monkeypatch.setattr(gl, "STEAM_PROCESS_LOG", tmp_path / "gameprocess_log.txt")
    monkeypatch.setattr(gl, "SAVE_DIR", str(tmp_path / "auto"))
    monkeypatch.setattr(gl, "SINGLE_SAVE_DIR", str(tmp_path))


def _cp(returncode=0, stdout=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout)


class TestProcessManagement:
    def test_running_checks_only_exact_civ6_binary_names(self, monkeypatch):
        calls = []
        monkeypatch.setattr(gl, "_PROCESS_NAMES", ["Civ6_Exe_Child", "Civ6"])
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _cp(0)
        )
        assert gl.is_game_running()
        assert calls == [["pgrep", "-x", "Civ6_Exe_Child"]]

    def test_missing_process_returns_false(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _cp(1))
        assert not gl.is_game_running()

    def test_windows_matches_case_insensitively(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(gl, "_PROCESS_NAMES", ["CivilizationVI.exe"])
        calls = []
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: calls.append(cmd) or _cp(0, "civilizationvi.exe 1234"),
        )
        assert gl.is_game_running()
        assert calls == [["tasklist", "/FI", "IMAGENAME eq CivilizationVI.exe", "/NH"]]

    def test_kill_without_running_process_does_not_spawn(self, monkeypatch):
        monkeypatch.setattr(gl, "is_game_running", lambda: False)
        assert "not running" in gl._kill_game_sync()

    def test_kill_uses_exact_names_and_never_steam(self, monkeypatch):
        states = iter([True, False])
        monkeypatch.setattr(gl, "is_game_running", lambda: next(states))
        monkeypatch.setattr(gl, "_PROCESS_NAMES", ["Civ6_Exe_Child", "Civ6"])
        calls = []
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _cp(0)
        )
        gl._kill_game_sync()
        assert calls == [
            ["pkill", "-9", "-x", "Civ6_Exe_Child"],
            ["pkill", "-9", "-x", "Civ6"],
        ]

    def test_windows_taskkill_targets_each_binary(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(gl, "_PROCESS_NAMES", ["CivilizationVI.exe"])
        states = iter([True, False])
        monkeypatch.setattr(gl, "is_game_running", lambda: next(states))
        calls = []
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _cp(0)
        )
        gl._kill_game_sync()
        assert calls == [["taskkill", "/F", "/IM", "CivilizationVI.exe"]]

    def test_macos_shutdown_allows_steam_to_release_exited_process(self, monkeypatch):
        states = iter([True, False])
        monkeypatch.setattr(gl, "is_game_running", lambda: next(states))
        monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _cp())
        sleeps = []
        monkeypatch.setattr(time, "sleep", sleeps.append)
        assert gl._kill_game_sync() == "Killed Civ 6."
        assert sleeps == [2]

    def test_windows_failed_kill_is_reported(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(gl, "is_game_running", lambda: True)
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _cp(1))
        assert gl._kill_game_sync().startswith("WARNING:")

    def test_process_remaining_after_kill_is_not_success(self, monkeypatch):
        monkeypatch.setattr(gl, "is_game_running", lambda: True)
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _cp(1))
        assert gl._kill_game_sync() == "WARNING: Game process is still running."

    def test_wait_for_process_returns_when_it_appears(self, monkeypatch):
        states = iter([False, False, True])
        monkeypatch.setattr(gl, "is_game_running", lambda: next(states))
        assert gl._wait_for_game_process(timeout=5) == 2

    def test_process_wait_is_bounded(self, monkeypatch):
        checks = []
        monkeypatch.setattr(gl, "is_game_running", lambda: checks.append(1) or False)
        assert gl._wait_for_game_process(timeout=3) is None
        assert len(checks) == 3


class TestCrashDialogs:
    def test_macos_clicks_ignore_without_reopening_game(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _cp(0, "clicked"),
        )
        assert gl._dismiss_crash_dialog()
        assert calls[0][0] == "osascript"
        assert 'click button "Ignore"' in calls[0][2]
        assert 'click button "Reopen"' not in calls[0][2]

    def test_crash_reporter_failure_does_not_break_recovery(self, monkeypatch):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("osascript", 5)

        monkeypatch.setattr(subprocess, "run", timeout)
        assert not gl._dismiss_crash_dialog()

    def test_linux_does_not_attempt_dialog_automation(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert gl._dismiss_crash_dialogs_sync() == []


class TestTunerPort:
    def test_probe_targets_local_firetuner_and_closes_on_success(self, monkeypatch):
        calls = []
        sock = SimpleNamespace(
            settimeout=lambda timeout: calls.append(("timeout", timeout)),
            connect=lambda addr: calls.append(("connect", addr)),
            close=lambda: calls.append(("close",)),
        )
        monkeypatch.setattr(socket, "socket", lambda *args: sock)
        assert gl._is_tuner_port_open()
        assert calls == [("timeout", 2), ("connect", ("127.0.0.1", 4318)), ("close",)]

    @pytest.mark.parametrize("exc", [ConnectionRefusedError(), OSError()])
    def test_unreachable_port_returns_false(self, monkeypatch, exc):
        closed = []
        def refused(addr):
            raise exc

        monkeypatch.setattr(socket, "socket", lambda *args: SimpleNamespace(
            settimeout=lambda _: None, connect=refused, close=lambda: closed.append(True),
        ))
        assert not gl._is_tuner_port_open()
        assert closed == [True]

    def test_wait_stops_on_first_open_probe(self, monkeypatch):
        states = iter([False, False, True])
        monkeypatch.setattr(gl, "_is_tuner_port_open", lambda: next(states))
        assert gl._wait_for_tuner_port(timeout=30)

    def test_wait_is_bounded_including_final_probe(self, monkeypatch):
        checks = []
        monkeypatch.setattr(gl, "_is_tuner_port_open", lambda: checks.append(1) or False)
        assert not gl._wait_for_tuner_port(timeout=9)
        assert len(checks) == 4


class TestLaunch:
    @pytest.fixture(autouse=True)
    def fake_launch_seams(self, monkeypatch):
        monkeypatch.setattr(gl, "_dismiss_crash_dialogs_sync", lambda: [])
        monkeypatch.setattr(gl, "is_game_running", lambda: False)
        monkeypatch.setattr(gl, "_wait_for_game_process", lambda timeout: 3)
        monkeypatch.setattr(gl, "_wait_for_tuner_port", lambda: True)

    def test_crashed_process_still_tracked_by_steam_does_not_launch(self, monkeypatch):
        gl.STEAM_PROCESS_LOG.write_text("[2026-10-17 09:35:14] AppID 289070 adding PID 123 as a tracked process")
        now = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: now[0])
        monkeypatch.setattr(time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
        result = gl._launch_game_sync()
        assert result.startswith("FAILED:")
        assert "Steam" in result

    def test_launch_waits_for_steam_release_after_crash(self, monkeypatch):
        gl.STEAM_PROCESS_LOG.write_text("AppID 289070 adding PID 123 as a tracked process")
        events = []
        def release(seconds):
            events.append("wait")
            gl.STEAM_PROCESS_LOG.write_text("AppID 289070 adding PID 123 as a tracked process\nRemove 289070 from running list")
        monkeypatch.setattr(time, "sleep", release)
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: events.append("launch") or _cp())
        assert "FireTuner port is open" in gl._launch_game_sync()
        assert events == ["wait", "launch"]

    def test_running_and_connected_does_not_launch_again(self, monkeypatch):
        monkeypatch.setattr(gl, "is_game_running", lambda: True)
        monkeypatch.setattr(gl, "_is_tuner_port_open", lambda: True)
        assert "already running" in gl._launch_game_sync()

    def test_unavailable_tuner_does_not_implicitly_restart(self, monkeypatch):
        monkeypatch.setattr(gl, "is_game_running", lambda: True)
        monkeypatch.setattr(gl, "_is_tuner_port_open", lambda: False)
        assert gl._launch_game_sync().startswith("Error:")

    @pytest.mark.parametrize("platform, expected", [
        ("darwin", ["open", "steam://run/289070"]),
        ("linux", ["steam", "-applaunch", "289070"]),
        ("win32", ["cmd", "/c", "start", "steam://run/289070"]),
    ])
    def test_launch_command_by_platform(self, monkeypatch, platform, expected):
        monkeypatch.setattr(sys, "platform", platform)
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _cp())
        monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
        assert "FireTuner port is open" in gl._launch_game_sync()
        assert calls == [expected]

    def test_launch_failure_names_missing_process(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _cp())
        monkeypatch.setattr(gl, "_wait_for_game_process", lambda timeout: None)
        assert "process not detected" in gl._launch_game_sync()

    def test_port_failure_explains_macos_launcher_bypass(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _cp())
        monkeypatch.setattr(gl, "_wait_for_tuner_port", lambda: False)
        result = gl._launch_game_sync()
        assert result.startswith("FAILED:")
        assert "FireTuner" in result and "Launch Options bypass" in result


class TestWindowsDiscovery:
    def test_discovers_exe_in_external_library(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "win32")
        steam = tmp_path / "Steam"
        monkeypatch.setattr(os.path, "expandvars", lambda _: str(steam))
        library = tmp_path / "external"
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        vdf.parent.mkdir(parents=True)
        vdf.write_text(f'"libraryfolders" {{ "0" {{ "path" "{library}" }} }}')
        exe = library / "steamapps/common/Sid Meier's Civilization VI/Base/Binaries/Win64Steam/CivilizationVI.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("MZ")
        assert gl._find_game_exe_win32() == str(exe)

    def test_missing_vdf_and_exe_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(os.path, "expandvars", lambda _: str(tmp_path / "missing"))
        assert gl._find_game_exe_win32() is None

    def test_non_windows_does_not_scan_steam(self):
        assert gl._find_game_exe_win32() is None


class TestAutosaveDiscovery:
    def test_empty_directory_returns_no_saves(self):
        assert gl.get_latest_autosave() is None
        assert gl.list_autosaves() == []

    def test_newest_autosave_wins_and_regular_saves_are_excluded(self, monkeypatch, tmp_path):
        monkeypatch.setattr(gl, "SAVE_DIR", str(tmp_path))
        for name, mtime in [("AutoSave_0001", 100), ("AutoSave_0002", 300), ("Manual", 500)]:
            path = tmp_path / f"{name}.Civ6Save"
            path.write_text("save")
            os.utime(path, (mtime, mtime))
        assert gl.get_latest_autosave() == "AutoSave_0002"
        assert gl.list_autosaves(limit=1) == ["AutoSave_0002"]


class TestRecovery:
    def patch_recovery(self, monkeypatch, launch_result):
        events = []

        async def dismiss():
            events.append("dismiss")
            return []

        async def kill():
            events.append("kill")
            return "Killed"

        async def launch():
            events.append("launch")
            return launch_result

        async def load(conn, name):
            events.append(("load", name))
            return "Loading save"

        class FakeConn:
            async def connect(self):
                events.append("connect")

        monkeypatch.setattr(gl, "dismiss_crash_dialogs", dismiss)
        monkeypatch.setattr(gl, "kill_game", kill)
        monkeypatch.setattr(gl, "launch_game", launch)
        monkeypatch.setattr("civ_mcp.connection.GameConnection", FakeConn)
        monkeypatch.setattr("civ_mcp.game_lifecycle.load_save_from_frontend", load)
        return events

    def test_recovery_connects_then_uses_native_loader_once(self, monkeypatch):
        events = self.patch_recovery(monkeypatch, "Game launched")
        result = asyncio.run(gl.restart_and_load("MySave"))
        assert events == ["dismiss", "kill", "launch", "dismiss", "connect", ("load", "MySave")]
        assert "Load: Loading save" in result

    def test_launch_failure_does_not_connect_or_load(self, monkeypatch):
        events = self.patch_recovery(monkeypatch, "FAILED: Game process not detected")
        assert "ABORTED" in asyncio.run(gl.restart_and_load("MySave"))
        assert events == ["dismiss", "kill", "launch"]

    def test_async_process_wrappers_are_safe_to_call(self, monkeypatch):
        monkeypatch.setattr(gl, "_kill_game_sync", lambda: "K")
        monkeypatch.setattr(gl, "_launch_game_sync", lambda: "L")
        monkeypatch.setattr(gl, "_dismiss_crash_dialogs_sync", lambda: ["dialog"])

        async def wrappers():
            return await gl.kill_game(), await gl.launch_game(), await gl.dismiss_crash_dialogs()

        assert asyncio.run(wrappers()) == ("K", "L", ["dialog"])
