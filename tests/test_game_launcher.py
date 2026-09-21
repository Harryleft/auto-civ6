"""Unit tests for `civ_mcp.game_launcher`.

The module does three kinds of work: pure logic (OCR result mapping, text
matching, save discovery, VDF parsing), subprocess orchestration (pgrep/pkill/
steam/xdotool), and platform-GUI work (Quartz/Vision/AppKit, winrt, mss,
pytesseract). Only the pure logic runs unmodified here. Subprocess, sockets,
clocks and sleeps are replaced with recorders; platform libraries are replaced
with fakes in ``sys.modules`` (their imports are all function-local, so a fake
module object is enough). No game is launched, no screen is captured, no input
events are posted.

A recurring theme: the launcher reports problems as human-readable strings
("WARNING: ... Check Steam."), so several classes pin the exact wording of the
actionable error messages a user sees when the game will not start.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from civ_mcp import game_launcher as gl

run = asyncio.run


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_rejected_ocr(monkeypatch):
    """Give every test a fresh low-confidence-OCR buffer.

    `_find_text` reads the module-global `_last_rejected_ocr` as a fuzzy
    fallback, so leftovers from one test could leak into another.
    """
    monkeypatch.setattr(gl, "_last_rejected_ocr", [])


def _cp(returncode=0, stdout="", stderr=""):
    """A completed-process stand-in for faked subprocess.run calls."""
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)


def _script_time(monkeypatch, *values, tail=10_000):
    """Replace time.time with a scripted sequence (then a constant tail)."""
    seq = iter(list(values) + [tail] * 1000)
    monkeypatch.setattr(time, "time", lambda: next(seq))


def _win(window_id=1, x=0, y=0, w=800, h=600, pid=4321):
    return gl.WindowInfo(window_id=window_id, x=x, y=y, w=w, h=h, pid=pid)


# ---------------------------------------------------------------------------
# Hardcoded safety constants
# ---------------------------------------------------------------------------


class TestConstants:
    """The module's safety posture lives in these constants — pin them."""

    def test_steam_app_id_is_civ6(self):
        assert gl.STEAM_APP_ID == "289070"

    def test_tuner_port_is_firetuner_default(self):
        assert gl._TUNER_PORT == 4318

    def test_process_pattern_only_matches_civ6(self):
        """Broadening this pattern is how Steam itself gets killed."""
        assert gl._ALLOWED_PROCESS_PATTERNS == ("Civ6",)

    def test_kill_list_contains_only_civ6_binaries(self):
        for name in gl._PROCESS_NAMES:
            assert "civ6" in name.lower() or "civilizationvi" in name.lower()


# ---------------------------------------------------------------------------
# _require_gui_deps
# ---------------------------------------------------------------------------


class TestRequireGuiDeps:
    """Dependency errors must name the exact extras to install."""

    def test_macos_without_pyobjc_gives_install_instructions(self, monkeypatch):
        # sys.modules entry of None forces ImportError even where pyobjc exists.
        monkeypatch.setitem(sys.modules, "Quartz", None)
        monkeypatch.setitem(sys.modules, "Vision", None)
        with pytest.raises(RuntimeError, match=r"launcher-macos"):
            gl._require_gui_deps()

    def test_macos_with_pyobjc_passes(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace())
        monkeypatch.setitem(sys.modules, "Vision", SimpleNamespace())
        gl._require_gui_deps()

    def test_windows_without_pywin32_gives_install_instructions(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setitem(sys.modules, "win32gui", None)
        with pytest.raises(RuntimeError, match="pywin32"):
            gl._require_gui_deps()

    def test_windows_with_all_deps_passes(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace())
        monkeypatch.setitem(sys.modules, "winrt", SimpleNamespace())
        monkeypatch.setitem(sys.modules, "winrt.windows", SimpleNamespace())
        monkeypatch.setitem(sys.modules, "winrt.windows.media", SimpleNamespace())
        monkeypatch.setitem(
            sys.modules,
            "winrt.windows.media.ocr",
            SimpleNamespace(OcrEngine=object),
        )
        gl._require_gui_deps()

    def test_linux_lists_every_missing_tool(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("shutil.which", lambda _name: None)
        monkeypatch.setitem(sys.modules, "mss", None)
        monkeypatch.setitem(sys.modules, "pytesseract", None)
        with pytest.raises(RuntimeError) as excinfo:
            gl._require_gui_deps()
        msg = str(excinfo.value)
        assert "xdotool" in msg
        assert "tesseract-ocr" in msg
        assert "pytesseract" in msg
        assert "launcher-linux" in msg

    def test_linux_with_all_deps_passes(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setitem(sys.modules, "mss", SimpleNamespace())
        monkeypatch.setitem(sys.modules, "pytesseract", SimpleNamespace())
        gl._require_gui_deps()

    def test_unknown_platform_is_not_implemented(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "freebsd")
        with pytest.raises(NotImplementedError, match="freebsd"):
            gl._require_gui_deps()


# ---------------------------------------------------------------------------
# is_game_running
# ---------------------------------------------------------------------------


class TestIsGameRunning:
    def test_macos_uses_pgrep_with_the_civ6_pattern(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return _cp(0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert gl.is_game_running() is True
        assert calls == [["pgrep", "-f", "Civ6"]]

    def test_macos_pgrep_miss_means_not_running(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _cp(1))
        assert gl.is_game_running() is False

    def test_windows_matches_tasklist_output_case_insensitively(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        # _PROCESS_NAMES is fixed at import time for the host platform.
        monkeypatch.setattr(
            gl, "_PROCESS_NAMES", ("CivilizationVI_DX12.exe", "CivilizationVI.exe")
        )

        def fake_run(cmd, **kw):
            if "CivilizationVI.exe" in cmd[2]:
                return _cp(0, "civilizationvi.exe              1234 Console")
            return _cp(0, "INFO: No tasks are running which match.")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert gl.is_game_running() is True

    def test_windows_no_match_means_not_running(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: _cp(0, "INFO: No tasks")
        )
        assert gl.is_game_running() is False

    def test_unknown_platform_raises(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "freebsd")
        with pytest.raises(NotImplementedError):
            gl.is_game_running()


# ---------------------------------------------------------------------------
# _dismiss_crash_dialog (macOS crash reporter)
# ---------------------------------------------------------------------------


class TestDismissCrashDialog:
    def test_non_macos_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")

        def boom(cmd, **kw):  # pragma: no cover - must not be called
            raise AssertionError("subprocess should not run off macOS")

        monkeypatch.setattr(subprocess, "run", boom)
        assert gl._dismiss_crash_dialog() is False

    def test_true_output_means_dismissed(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return _cp(0, "true\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert gl._dismiss_crash_dialog() is True
        assert calls[0][0] == "osascript"
        # The script must click "Ignore", never "Reopen" (which relaunches).
        assert 'click button "Ignore"' in calls[0][2]

    def test_false_output_means_no_dialog(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _cp(0, "false\n"))
        assert gl._dismiss_crash_dialog() is False

    def test_osascript_failure_is_swallowed(self, monkeypatch):
        def boom(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 5)

        monkeypatch.setattr(subprocess, "run", boom)
        assert gl._dismiss_crash_dialog() is False


# ---------------------------------------------------------------------------
# _kill_game_sync
# ---------------------------------------------------------------------------


class TestKillGameSync:
    def test_not_running_is_a_noop_message(self, monkeypatch):
        monkeypatch.setattr(gl, "is_game_running", lambda: False)

        def boom(cmd, **kw):  # pragma: no cover - must not be called
            raise AssertionError("pkill should not run when game is not running")

        monkeypatch.setattr(subprocess, "run", boom)
        assert gl._kill_game_sync() == "Game is not running."

    def test_kills_each_civ6_binary_with_exact_match(self, monkeypatch):
        """Safety contract: pkill -9 -x per binary name, never a broad -f pattern."""
        states = iter([True] + [False] * 50)
        monkeypatch.setattr(gl, "is_game_running", lambda: next(states))
        _no_sleep(monkeypatch)
        calls = []
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _cp(0)
        )

        assert gl._kill_game_sync() == "Game killed. Steam deregistration wait complete."
        assert calls == [
            ["pkill", "-9", "-x", "Civ6_Exe_Child"],
            ["pkill", "-9", "-x", "Civ6_Exe"],
            ["pkill", "-9", "-x", "Civ6"],
        ]

    def test_windows_uses_taskkill_per_binary(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        # _PROCESS_NAMES is fixed at import time for the host platform;
        # stand in the Windows kill list.
        monkeypatch.setattr(
            gl, "_PROCESS_NAMES", ("CivilizationVI_DX12.exe", "CivilizationVI.exe")
        )
        states = iter([True] + [False] * 50)
        monkeypatch.setattr(gl, "is_game_running", lambda: next(states))
        _no_sleep(monkeypatch)
        calls = []
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _cp(0)
        )

        gl._kill_game_sync()
        assert calls == [
            ["taskkill", "/IM", "CivilizationVI_DX12.exe", "/F"],
            ["taskkill", "/IM", "CivilizationVI.exe", "/F"],
        ]

    def test_still_running_after_kill_produces_a_warning(self, monkeypatch):
        monkeypatch.setattr(gl, "is_game_running", lambda: True)
        _no_sleep(monkeypatch)
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _cp(0))

        assert (
            gl._kill_game_sync()
            == "WARNING: Game process may still be running after kill attempt."
        )


# ---------------------------------------------------------------------------
# _wait_for_game_process
# ---------------------------------------------------------------------------


class TestWaitForGameProcess:
    def test_returns_zero_when_already_running(self, monkeypatch):
        monkeypatch.setattr(gl, "is_game_running", lambda: True)
        assert gl._wait_for_game_process(timeout=5) == 0

    def test_returns_seconds_waited_when_process_appears(self, monkeypatch):
        states = iter([False, False, True])
        monkeypatch.setattr(gl, "is_game_running", lambda: next(states))
        _no_sleep(monkeypatch)
        assert gl._wait_for_game_process(timeout=5) == 2

    def test_returns_none_on_timeout(self, monkeypatch):
        checks = []
        monkeypatch.setattr(gl, "is_game_running", lambda: checks.append(1) or False)
        _no_sleep(monkeypatch)
        assert gl._wait_for_game_process(timeout=3) is None
        assert len(checks) == 3


# ---------------------------------------------------------------------------
# _is_tuner_port_open / _wait_for_tuner_port
# ---------------------------------------------------------------------------


class TestTunerPort:
    def test_open_port_returns_true_and_closes_the_probe(self, monkeypatch):
        sock = SimpleNamespace(closed=False)
        sock.close = lambda: setattr(sock, "closed", True)
        addrs = []

        def fake_create_connection(addr, timeout=None):
            addrs.append((addr, timeout))
            return sock

        monkeypatch.setattr("socket.create_connection", fake_create_connection)
        assert gl._is_tuner_port_open() is True
        assert addrs == [(("127.0.0.1", 4318), 2)]
        assert sock.closed is True

    @pytest.mark.parametrize("exc", [ConnectionRefusedError("nope"), OSError("nope")])
    def test_refused_or_unreachable_returns_false(self, monkeypatch, exc):
        def boom(addr, timeout=None):
            raise exc

        monkeypatch.setattr("socket.create_connection", boom)
        assert gl._is_tuner_port_open() is False

    def test_wait_returns_true_as_soon_as_port_opens(self, monkeypatch):
        states = iter([False, False, True])
        monkeypatch.setattr(gl, "_is_tuner_port_open", lambda: next(states))
        _no_sleep(monkeypatch)
        assert gl._wait_for_tuner_port(timeout=30) is True

    def test_wait_polls_every_three_seconds_until_timeout(self, monkeypatch):
        checks = []
        monkeypatch.setattr(gl, "_is_tuner_port_open", lambda: checks.append(1) or False)
        _no_sleep(monkeypatch)
        assert gl._wait_for_tuner_port(timeout=9) is False
        assert len(checks) == 3

    def test_wait_with_timeout_below_the_interval_still_checks_once(self, monkeypatch):
        """A timeout shorter than the poll interval must not mean zero polls —
        int(1 / 3) == 0 iterations would report closed without ever trying."""
        checks = []
        monkeypatch.setattr(gl, "_is_tuner_port_open", lambda: checks.append(1) or True)
        _no_sleep(monkeypatch)
        assert gl._wait_for_tuner_port(timeout=1) is True
        assert checks == [1]


# ---------------------------------------------------------------------------
# _launch_game_sync
# ---------------------------------------------------------------------------


class TestLaunchGameSync:
    """The launch flow's user-facing strings are the "game won't start"
    diagnostics — pin each branch's exact, actionable message."""

    def _patch_common(self, monkeypatch):
        _no_sleep(monkeypatch)
        monkeypatch.setattr(gl, "_dismiss_crash_dialogs_sync", lambda: [])
        monkeypatch.setattr(gl, "_click_aspyr_launcher_sync", lambda: None)
        self.run_calls = []
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: self.run_calls.append(cmd) or _cp(0)
        )

    def test_already_running_with_open_port_short_circuits(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(gl, "is_game_running", lambda: True)
        monkeypatch.setattr(gl, "_is_tuner_port_open", lambda: True)

        assert (
            gl._launch_game_sync()
            == "Game is already running and FireTuner port is open."
        )
        assert self.run_calls == []  # Steam was not poked

    def test_running_process_waits_for_port_to_open(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(gl, "is_game_running", lambda: True)
        monkeypatch.setattr(gl, "_is_tuner_port_open", lambda: False)
        monkeypatch.setattr(gl, "_wait_for_tuner_port", lambda timeout=180: True)

        assert (
            gl._launch_game_sync()
            == "Game was starting up. FireTuner port is now open."
        )
        assert self.run_calls == []

    def test_stale_process_is_killed_then_relaunched(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(gl, "is_game_running", lambda: True)
        monkeypatch.setattr(gl, "_is_tuner_port_open", lambda: False)
        port_waits = iter([False, True])  # stale check fails, post-launch succeeds
        monkeypatch.setattr(
            gl, "_wait_for_tuner_port", lambda timeout=180: next(port_waits)
        )
        kills = []
        monkeypatch.setattr(gl, "_kill_game_sync", lambda: kills.append(1) or "killed")
        monkeypatch.setattr(gl, "_wait_for_game_process", lambda timeout=60: 3)

        assert (
            gl._launch_game_sync()
            == "Game launched. Process started after 3s, FireTuner port is open."
        )
        assert kills == [1]
        assert ["open", "steam://run/289070"] in self.run_calls

    def test_macos_happy_path_launches_via_steam_url(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(gl, "is_game_running", lambda: False)
        monkeypatch.setattr(gl, "_wait_for_game_process", lambda timeout=60: 5)
        monkeypatch.setattr(gl, "_wait_for_tuner_port", lambda timeout=180: True)

        assert (
            gl._launch_game_sync()
            == "Game launched. Process started after 5s, FireTuner port is open."
        )
        assert self.run_calls == [["open", "steam://run/289070"]]

    def test_linux_launches_via_applaunch_not_steam_url(self, monkeypatch):
        """The steam:// URI is silently ignored by some Linux Steam builds."""
        self._patch_common(monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(gl, "is_game_running", lambda: False)
        monkeypatch.setattr(gl, "_wait_for_game_process", lambda timeout=60: 2)
        monkeypatch.setattr(gl, "_wait_for_tuner_port", lambda timeout=180: True)
        popens = []
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda cmd, **kw: popens.append((cmd, kw)) or SimpleNamespace(),
        )

        gl._launch_game_sync()
        assert popens[0][0] == ["steam", "-applaunch", "289070"]
        assert popens[0][1]["stdout"] is subprocess.DEVNULL
        assert popens[0][1]["stderr"] is subprocess.DEVNULL

    def test_process_never_appears_tells_user_to_check_steam(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(gl, "is_game_running", lambda: False)
        monkeypatch.setattr(gl, "_wait_for_game_process", lambda timeout=60: None)

        assert (
            gl._launch_game_sync()
            == "FAILED: Game process not detected after launch. Check Steam."
        )

    def test_process_without_port_reports_both_facts(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(gl, "is_game_running", lambda: False)
        monkeypatch.setattr(gl, "_wait_for_game_process", lambda timeout=60: 5)
        monkeypatch.setattr(gl, "_wait_for_tuner_port", lambda timeout=180: False)

        assert gl._launch_game_sync() == (
            "FAILED: Game launched (process after 5s) but FireTuner port "
            "not detected within 180s."
        )

    def test_aspyr_launcher_failure_still_waits_for_process_and_port(
        self, monkeypatch
    ):
        """A launcher-click failure must not end the launch: Steam may have
        launched the game fine, so the flow keeps waiting for the process
        and the tuner port, and reports the GUI warning alongside success."""
        self._patch_common(monkeypatch)
        monkeypatch.setattr(gl, "is_game_running", lambda: False)
        monkeypatch.setattr(
            gl,
            "_click_aspyr_launcher_sync",
            lambda: "GUI deps not available. Click PLAY on the Aspyr launcher manually.",
        )
        monkeypatch.setattr(gl, "_wait_for_game_process", lambda timeout=60: 5)
        monkeypatch.setattr(gl, "_wait_for_tuner_port", lambda timeout=180: True)

        assert gl._launch_game_sync() == (
            "Game launched. Process started after 5s, FireTuner port is open. "
            "(WARNING: GUI deps not available. "
            "Click PLAY on the Aspyr launcher manually.)"
        )
        assert self.run_calls == [["open", "steam://run/289070"]]

    def test_aspyr_launcher_failure_plus_no_process_reports_both(self, monkeypatch):
        """When the launcher click fails AND the game never starts, the
        failure report carries both facts (and the failure marker)."""
        self._patch_common(monkeypatch)
        monkeypatch.setattr(gl, "is_game_running", lambda: False)
        monkeypatch.setattr(
            gl,
            "_click_aspyr_launcher_sync",
            lambda: "GUI deps not available. Click PLAY on the Aspyr launcher manually.",
        )
        monkeypatch.setattr(gl, "_wait_for_game_process", lambda timeout=60: None)

        assert gl._launch_game_sync() == (
            "FAILED: Game process not detected after launch. Check Steam. "
            "Also: WARNING: GUI deps not available. "
            "Click PLAY on the Aspyr launcher manually."
        )

    def test_macos_without_pyobjc_launch_still_succeeds_via_steam(self, monkeypatch):
        """Real-machine path: no pyobjc installed, everything else real. The
        GUI warning no longer short-circuits the launch — the process and
        port waits still run and the launch can succeed."""
        _no_sleep(monkeypatch)
        monkeypatch.setattr(gl, "_dismiss_crash_dialogs_sync", lambda: [])
        monkeypatch.setattr(gl, "is_game_running", lambda: False)
        monkeypatch.setattr(gl, "_wait_for_game_process", lambda timeout=60: 5)
        monkeypatch.setattr(gl, "_wait_for_tuner_port", lambda timeout=180: True)
        monkeypatch.setitem(sys.modules, "Quartz", None)
        monkeypatch.setitem(sys.modules, "Vision", None)
        run_calls = []
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: run_calls.append(cmd) or _cp(0)
        )

        assert gl._launch_game_sync() == (
            "Game launched. Process started after 5s, FireTuner port is open. "
            "(WARNING: GUI deps not available. "
            "Click PLAY on the Aspyr launcher manually.)"
        )
        assert run_calls == [["open", "steam://run/289070"]]


# ---------------------------------------------------------------------------
# _click_aspyr_launcher_sync
# ---------------------------------------------------------------------------


class TestClickAspyrLauncher:
    def test_non_macos_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")

        def boom():  # pragma: no cover - must not be called
            raise AssertionError("OCR should not run off macOS")

        monkeypatch.setattr(gl, "_ocr_fullscreen", boom)
        assert gl._click_aspyr_launcher_sync() is None

    def test_missing_gui_deps_returns_actionable_warning(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "Quartz", None)
        monkeypatch.setitem(sys.modules, "Vision", None)
        assert gl._click_aspyr_launcher_sync() == (
            "GUI deps not available. Click PLAY on the Aspyr launcher manually."
        )

    def test_finding_play_double_clicks_it(self, monkeypatch):
        """The first click can be absorbed by the focus change, hence two."""
        monkeypatch.setattr(gl, "_require_gui_deps", lambda: None)
        monkeypatch.setattr(gl, "_find_game_window", lambda: None)
        monkeypatch.setattr(gl, "_ocr_fullscreen", lambda: [("PLAY", 10, 20, 30, 40)])
        monkeypatch.setattr(gl, "_bring_to_front", lambda pid=None: None)
        _no_sleep(monkeypatch)
        clicks = []
        monkeypatch.setattr(gl, "_click", lambda x, y: clicks.append((x, y)))

        assert gl._click_aspyr_launcher_sync() is None
        assert clicks == [(10, 20), (10, 20)]

    def test_no_play_button_within_30s_is_not_an_error(self, monkeypatch):
        """The launcher may legitimately be skipped — None, not a failure string."""
        monkeypatch.setattr(gl, "_require_gui_deps", lambda: None)
        monkeypatch.setattr(gl, "_find_game_window", lambda: None)
        monkeypatch.setattr(gl, "_ocr_fullscreen", lambda: [])
        _no_sleep(monkeypatch)
        _script_time(monkeypatch, 0, 0, 31)  # start, first check, expired

        assert gl._click_aspyr_launcher_sync() is None


# ---------------------------------------------------------------------------
# _find_game_exe_win32 (Steam library VDF parsing)
# ---------------------------------------------------------------------------


def _write_exe(base, *parts):
    exe = base.joinpath(*parts)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("MZ")
    return exe


class TestFindGameExeWin32:
    _EXE_PARTS = (
        "steamapps",
        "common",
        "Sid Meier's Civilization VI",
        "Base",
        "Binaries",
        "Win64Steam",
        "CivilizationVI_DX12.exe",
    )

    def _vdf(self, lib_path, app_id="289070"):
        return (
            '"libraryfolders"\n{\n\t"0"\n\t{\n'
            f'\t\t"path"\t\t"{lib_path}"\n'
            '\t\t"apps"\n\t\t{\n'
            f'\t\t\t"{app_id}"\t\t"123456"\n'
            "\t\t}\n\t}\n}\n"
        )

    def test_missing_vdf_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
        assert gl._find_game_exe_win32() is None

    def test_finds_exe_in_the_library_that_owns_the_app(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
        steam = tmp_path / "Steam"
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        vdf.parent.mkdir(parents=True)
        library = tmp_path / "elsewhere" / "SteamLibrary"
        vdf.write_text(self._vdf(str(library)))
        exe = _write_exe(library, *self._EXE_PARTS)

        assert gl._find_game_exe_win32() == str(exe)

    def test_library_without_the_app_falls_back_to_default_steam_dir(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
        steam = tmp_path / "Steam"
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        vdf.parent.mkdir(parents=True)
        vdf.write_text(self._vdf(str(tmp_path / "other"), app_id="999999"))
        exe = _write_exe(steam, *self._EXE_PARTS)

        assert gl._find_game_exe_win32() == str(exe)

    def test_app_listed_but_exe_missing_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
        steam = tmp_path / "Steam"
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        vdf.parent.mkdir(parents=True)
        vdf.write_text(self._vdf(str(tmp_path / "empty-library")))

        assert gl._find_game_exe_win32() is None


# ---------------------------------------------------------------------------
# _find_game_window (macOS Quartz path)
# ---------------------------------------------------------------------------


def _cg_window(owner, layer=0, x=10, y=20, w=300, h=200, wid=42, pid=777):
    return {
        "kCGWindowOwnerName": owner,
        "kCGWindowLayer": layer,
        "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h},
        "kCGWindowNumber": wid,
        "kCGWindowOwnerPID": pid,
    }


def _install_quartz_windows(monkeypatch, windows):
    quartz = SimpleNamespace(
        kCGWindowListOptionOnScreenOnly=1,
        kCGWindowListExcludeDesktopElements=2,
        kCGNullWindowID=0,
        CGWindowListCopyWindowInfo=lambda opts, wid: windows,
    )
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    monkeypatch.setattr(gl, "_require_gui_deps", lambda: None)


class TestFindGameWindowMac:
    def test_matches_by_app_name_and_maps_bounds(self, monkeypatch):
        _install_quartz_windows(monkeypatch, [_cg_window("Civilization VI")])
        info = gl._find_game_window()
        assert info == gl.WindowInfo(
            window_id=42, x=10, y=20, w=300, h=200, pid=777
        )

    def test_matches_by_process_name(self, monkeypatch):
        _install_quartz_windows(monkeypatch, [_cg_window("Civ6_Exe")])
        assert gl._find_game_window() is not None

    def test_skips_non_zero_layer_windows(self, monkeypatch):
        """Layer 0 is the normal app-window layer; menus/tooltips sit elsewhere."""
        _install_quartz_windows(
            monkeypatch,
            [_cg_window("Civilization VI", layer=5, wid=1), _cg_window("Civilization VI", wid=2)],
        )
        assert gl._find_game_window().window_id == 2

    def test_unrelated_windows_do_not_match(self, monkeypatch):
        _install_quartz_windows(monkeypatch, [_cg_window("Google Chrome")])
        assert gl._find_game_window() is None

    def test_empty_window_list_returns_none(self, monkeypatch):
        _install_quartz_windows(monkeypatch, None)
        assert gl._find_game_window() is None


# ---------------------------------------------------------------------------
# _find_game_window_linux (xdotool)
# ---------------------------------------------------------------------------


class TestFindGameWindowLinux:
    def _install_xdotool(self, monkeypatch, wids, names, geos, pids=None):
        def fake_run(cmd, **kw):
            if cmd[:2] == ["xdotool", "search"]:
                if wids is None:
                    return _cp(1, "")
                return _cp(0, "".join(f"{w}\n" for w in wids))
            if cmd[1] == "getwindowname":
                return _cp(0, names.get(cmd[2], "") + "\n")
            if cmd[1] == "getwindowgeometry":
                return _cp(0, geos[cmd[3]])
            if cmd[1] == "getwindowpid":
                return _cp(0, str((pids or {}).get(cmd[2], 0)) + "\n")
            raise AssertionError(f"unexpected command {cmd}")  # pragma: no cover

        monkeypatch.setattr(subprocess, "run", fake_run)

    def test_exact_title_match_wins(self, monkeypatch):
        """A browser tab titled 'Civilization VI - ...' must not be picked."""
        self._install_xdotool(
            monkeypatch,
            wids=["100", "200"],
            names={"100": "Civilization VI", "200": "Civilization VI - YouTube"},
            geos={"100": "X=1\nY=2\nWIDTH=3\nHEIGHT=4\n"},
            pids={"100": 555},
        )
        info = gl._find_game_window_linux()
        assert info == gl.WindowInfo(window_id=100, x=1, y=2, w=3, h=4, pid=555)

    def test_prefers_the_smaller_of_two_exact_matches(self, monkeypatch):
        """The inner client window (no decorations) is the smaller one."""
        self._install_xdotool(
            monkeypatch,
            wids=["100", "200"],
            names={"100": "Civilization VI", "200": "Civilization VI"},
            geos={
                "100": "X=0\nY=0\nWIDTH=1920\nHEIGHT=1080\n",
                "200": "X=10\nY=40\nWIDTH=1900\nHEIGHT=1000\n",
            },
        )
        assert gl._find_game_window_linux().window_id == 200

    def test_failed_search_returns_none(self, monkeypatch):
        self._install_xdotool(monkeypatch, wids=None, names={}, geos={})
        assert gl._find_game_window_linux() is None

    def test_xdotool_crash_returns_none(self, monkeypatch):
        def boom(cmd, **kw):
            raise OSError("xdotool missing")

        monkeypatch.setattr(subprocess, "run", boom)
        assert gl._find_game_window_linux() is None


# ---------------------------------------------------------------------------
# _get_frame_extents
# ---------------------------------------------------------------------------


class TestGetFrameExtents:
    def test_parses_xprop_output(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kw: _cp(0, "_NET_FRAME_EXTENTS(CARDINAL) = 2, 2, 28, 2\n"),
        )
        assert gl._get_frame_extents(99) == (2, 2, 28, 2)

    def test_garbage_output_returns_zeros(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: _cp(0, "not found")
        )
        assert gl._get_frame_extents(99) == (0, 0, 0, 0)

    def test_xprop_failure_returns_zeros(self, monkeypatch):
        def boom(cmd, **kw):
            raise OSError("no xprop")

        monkeypatch.setattr(subprocess, "run", boom)
        assert gl._get_frame_extents(99) == (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# _capture_window dispatch + macOS fallback
# ---------------------------------------------------------------------------


class TestCaptureWindow:
    def test_macos_returns_cgimage_when_available(self, monkeypatch):
        quartz = SimpleNamespace(
            CGRectNull=None,
            kCGWindowListOptionIncludingWindow=1,
            kCGWindowImageBoundsIgnoreFraming=2,
            CGWindowListCreateImage=lambda *a: "CGIMAGE",
        )
        monkeypatch.setitem(sys.modules, "Quartz", quartz)
        assert gl._capture_window(7) == "CGIMAGE"

    def test_macos_nil_image_falls_back_to_screencapture(self, monkeypatch):
        """macOS 15 obsoleted CGWindowListCreateImage — the fallback matters."""
        quartz = SimpleNamespace(
            CGRectNull=None,
            kCGWindowListOptionIncludingWindow=1,
            kCGWindowImageBoundsIgnoreFraming=2,
            CGWindowListCreateImage=lambda *a: None,
        )
        monkeypatch.setitem(sys.modules, "Quartz", quartz)
        monkeypatch.setattr(gl, "_capture_window_screencapture", lambda wid: "SC")
        assert gl._capture_window(7) == "SC"

    def test_dispatches_to_platform_impl(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(gl, "_capture_window_win32", lambda wid: "W32")
        assert gl._capture_window(7) == "W32"
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(gl, "_capture_window_linux", lambda wid: "LNX")
        assert gl._capture_window(7) == "LNX"


# ---------------------------------------------------------------------------
# _capture_window_linux (mss region clamping)
# ---------------------------------------------------------------------------


class _FakeSct:
    def __init__(self, display):
        self.monitors = [display, display]

    def grab(self, monitor):
        w, h = monitor["width"], monitor["height"]
        return SimpleNamespace(size=(w, h), rgb=b"\x00" * (w * h * 3))


class _FakeMssCM:
    def __init__(self, sct):
        self._sct = sct

    def __enter__(self):
        return self._sct

    def __exit__(self, *exc):
        return False


class TestCaptureWindowLinux:
    def _install(self, monkeypatch, geometry, display=None):
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: _cp(0, geometry)
        )
        sct = _FakeSct(display or {"left": 0, "top": 0, "width": 1920, "height": 1080})
        monkeypatch.setitem(
            sys.modules, "mss", SimpleNamespace(mss=lambda: _FakeMssCM(sct))
        )

    def test_captures_the_window_region(self, monkeypatch):
        self._install(monkeypatch, "X=100\nY=50\nWIDTH=800\nHEIGHT=600\n")
        img, region = gl._capture_window_linux(1)
        assert region == {"left": 100, "top": 50, "width": 800, "height": 600}
        assert img.size == (800, 600)

    def test_region_is_clamped_to_the_display(self, monkeypatch):
        """mss raises XGetImage errors when the region exceeds the screen."""
        self._install(monkeypatch, "X=1800\nY=0\nWIDTH=800\nHEIGHT=600\n")
        _, region = gl._capture_window_linux(1)
        assert region["width"] == 120

    def test_off_screen_window_raises(self, monkeypatch):
        self._install(monkeypatch, "X=5000\nY=0\nWIDTH=800\nHEIGHT=600\n")
        with pytest.raises(RuntimeError, match="off-screen"):
            gl._capture_window_linux(1)

    def test_zero_geometry_raises(self, monkeypatch):
        self._install(monkeypatch, "X=0\nY=0\nWIDTH=0\nHEIGHT=0\n")
        with pytest.raises(RuntimeError, match="no geometry"):
            gl._capture_window_linux(1)


# ---------------------------------------------------------------------------
# _ocr_vision (macOS Vision coordinate mapping)
# ---------------------------------------------------------------------------


class _FakeVnCandidate:
    def __init__(self, text, confidence):
        self._text = text
        self._confidence = confidence

    def string(self):
        return self._text

    def confidence(self):
        return self._confidence


class _FakeVnObservation:
    def __init__(self, text, confidence, x, y, w, h):
        self._candidate = _FakeVnCandidate(text, confidence)
        self._bbox = SimpleNamespace(
            origin=SimpleNamespace(x=x, y=y),
            size=SimpleNamespace(width=w, height=h),
        )

    def topCandidates_(self, n):
        return [self._candidate]

    def boundingBox(self):
        return self._bbox


class _FakeVnRequest:
    def __init__(self):
        self._results = []

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self

    def setRecognitionLevel_(self, level):
        self.level = level

    def results(self):
        return self._results


def _install_vision(monkeypatch, observations, success=True):
    class _FakeHandler:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithCGImage_options_(self, image, options):
            return self

        def performRequests_error_(self, requests, error):
            if success:
                requests[0]._results = observations
                return True, None
            return False, "vision exploded"

    vision = SimpleNamespace(
        VNRecognizeTextRequest=_FakeVnRequest,
        VNImageRequestHandler=_FakeHandler,
        VNRequestTextRecognitionLevelAccurate=1,
    )
    monkeypatch.setitem(sys.modules, "Vision", vision)


class TestOcrVision:
    def test_maps_normalized_boxes_to_screen_points_with_y_flip(self, monkeypatch):
        """Vision's origin is bottom-left; screen points are top-left."""
        obs = [_FakeVnObservation("HELLO", 0.9, x=0.4, y=0.7, w=0.2, h=0.1)]
        _install_vision(monkeypatch, obs)
        assert gl._ocr_vision("IMG", 0, 0, 1000, 1000) == [
            ("HELLO", 500, 250, 200, 100)
        ]

    def test_origin_and_extent_are_applied(self, monkeypatch):
        obs = [_FakeVnObservation("X", 0.9, x=0.0, y=0.9, w=0.2, h=0.1)]
        _install_vision(monkeypatch, obs)
        # norm center (0.1, 0.05) -> 100 + 0.1*400, 200 + 0.05*300
        assert gl._ocr_vision("IMG", 100, 200, 400, 300) == [("X", 140, 215, 80, 30)]

    def test_low_confidence_goes_to_the_rejected_buffer(self, monkeypatch):
        obs = [
            _FakeVnObservation("KEEP", 0.5, 0.0, 0.0, 0.1, 0.1),
            _FakeVnObservation("DROP", 0.49, 0.0, 0.0, 0.1, 0.1),
        ]
        _install_vision(monkeypatch, obs)
        results = gl._ocr_vision("IMG", 0, 0, 100, 100)
        assert [r[0] for r in results] == ["KEEP"]  # 0.5 is the boundary, kept
        assert [r[0] for r in gl._last_rejected_ocr] == ["DROP"]

    def test_engine_failure_returns_empty(self, monkeypatch):
        _install_vision(monkeypatch, [], success=False)
        assert gl._ocr_vision("IMG", 0, 0, 100, 100) == []

    def test_null_results_are_tolerated(self, monkeypatch):
        _install_vision(monkeypatch, None)
        assert gl._ocr_vision("IMG", 0, 0, 100, 100) == []


# ---------------------------------------------------------------------------
# _ocr_tesseract (two-pass merge, line grouping, gap split, dedup)
# ---------------------------------------------------------------------------


def _tess(words):
    """Build a pytesseract image_to_data dict from word tuples.

    Each word: (text, conf, left, top, width, height, block, par, line).
    """
    keys = ("text", "conf", "left", "top", "width", "height",
            "block_num", "par_num", "line_num")
    return {k: [w[i] for w in words] for i, k in enumerate(keys)}


def _install_tesseract(monkeypatch, normal, threshold):
    calls = {"n": 0}

    def image_to_data(img, config=None, output_type=None):
        calls["n"] += 1
        src = normal if calls["n"] == 1 else threshold
        return {k: list(v) for k, v in src.items()}

    fake = SimpleNamespace(
        image_to_data=image_to_data,
        Output=SimpleNamespace(DICT="dict"),
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake)


def _img(w=200, h=100):
    from PIL import Image

    return Image.new("RGB", (w, h))


class TestOcrTesseract:
    def test_maps_word_boxes_to_screen_points(self, monkeypatch):
        _install_tesseract(
            monkeypatch,
            _tess([("HELLO", 90, 20, 10, 40, 20, 1, 1, 1)]),
            _tess([]),
        )
        assert gl._ocr_tesseract(_img(), 1000, 500, 200, 100) == [
            ("HELLO", 1040, 520, 40, 20)
        ]

    def test_words_on_one_line_join_in_left_to_right_order(self, monkeypatch):
        _install_tesseract(
            monkeypatch,
            _tess(
                [
                    ("TWO", 90, 60, 10, 20, 10, 1, 1, 1),
                    ("ONE", 90, 10, 10, 20, 10, 1, 1, 1),
                ]
            ),
            _tess([]),
        )
        results = gl._ocr_tesseract(_img(), 0, 0, 200, 100)
        assert [r[0] for r in results] == ["ONE TWO"]

    def test_different_lines_stay_separate(self, monkeypatch):
        _install_tesseract(
            monkeypatch,
            _tess(
                [
                    ("A", 90, 10, 10, 20, 10, 1, 1, 1),
                    ("B", 90, 10, 40, 20, 10, 1, 1, 2),
                ]
            ),
            _tess([]),
        )
        results = gl._ocr_tesseract(_img(), 0, 0, 200, 100)
        assert [r[0] for r in results] == ["A", "B"]

    def test_large_horizontal_gap_splits_a_line(self, monkeypatch):
        """Two-column menus get merged by tesseract; the gap split undoes that."""
        _install_tesseract(
            monkeypatch,
            _tess(
                [
                    ("LEFT", 90, 0, 10, 10, 10, 1, 1, 1),
                    ("RIGHT", 90, 100, 10, 10, 10, 1, 1, 1),
                ]
            ),
            _tess([]),
        )
        results = gl._ocr_tesseract(_img(), 0, 0, 200, 100)
        assert [r[0] for r in results] == ["LEFT", "RIGHT"]

    def test_confidence_49_is_dropped_but_50_is_kept(self, monkeypatch):
        _install_tesseract(
            monkeypatch,
            _tess(
                [
                    ("KEPT", 50, 10, 10, 20, 10, 1, 1, 1),
                    ("DROPPED", 49, 60, 10, 20, 10, 1, 1, 1),
                ]
            ),
            _tess([]),
        )
        results = gl._ocr_tesseract(_img(), 0, 0, 200, 100)
        assert [r[0] for r in results] == ["KEPT"]

    def test_rejected_words_land_in_the_fuzzy_fallback_buffer(self, monkeypatch):
        _install_tesseract(
            monkeypatch,
            _tess([("faded", 30, 20, 10, 40, 20, 1, 1, 1)]),
            _tess([]),
        )
        assert gl._ocr_tesseract(_img(), 1000, 500, 200, 100) == []
        assert gl._last_rejected_ocr == [("faded", 1040, 520, 40, 20)]

    def test_empty_text_is_never_reported_or_rejected(self, monkeypatch):
        _install_tesseract(
            monkeypatch,
            _tess([("", 10, 20, 10, 40, 20, 1, 1, 1)]),
            _tess([]),
        )
        assert gl._ocr_tesseract(_img(), 0, 0, 200, 100) == []
        assert gl._last_rejected_ocr == []

    def test_threshold_pass_duplicates_in_other_blocks_are_deduped(self, monkeypatch):
        """The second (thresholded) pass re-finds the same text; keep one."""
        _install_tesseract(
            monkeypatch,
            _tess([("PLAY", 90, 100, 50, 20, 10, 1, 1, 1)]),
            _tess([("PLAY", 80, 101, 51, 20, 10, 2, 1, 1)]),  # different block
        )
        results = gl._ocr_tesseract(_img(), 0, 0, 200, 100)
        assert [r[0] for r in results] == ["PLAY"]

    def test_threshold_pass_duplicate_on_the_same_line_is_deduped_before_joining(
        self, monkeypatch
    ):
        """When tesseract's two passes assign the same block/par/line key,
        the duplicate token is dropped BEFORE the words are joined — the
        normal-pass (first, higher-confidence) word wins."""
        _install_tesseract(
            monkeypatch,
            _tess([("PLAY", 90, 100, 50, 20, 10, 1, 1, 1)]),
            _tess([("PLAY", 80, 101, 51, 20, 10, 1, 1, 1)]),  # same line key
        )
        results = gl._ocr_tesseract(_img(), 0, 0, 200, 100)
        assert [r[0] for r in results] == ["PLAY"]


# ---------------------------------------------------------------------------
# _ocr_game_window (title-bar offset detection)
# ---------------------------------------------------------------------------


class TestOcrGameWindow:
    def _install_quartz_sizes(self, monkeypatch, px_w, px_h):
        quartz = SimpleNamespace(
            CGImageGetWidth=lambda img: px_w,
            CGImageGetHeight=lambda img: px_h,
        )
        monkeypatch.setitem(sys.modules, "Quartz", quartz)

    def test_full_window_capture_maps_one_to_one(self, monkeypatch):
        self._install_quartz_sizes(monkeypatch, 1600, 1200)  # 2x retina of 800x600
        monkeypatch.setattr(gl, "_capture_window", lambda wid: "IMG")
        seen = []
        monkeypatch.setattr(
            gl, "_ocr_vision", lambda *a: seen.append(a) or []
        )
        gl._ocr_game_window(_win(x=100, y=100, w=800, h=600))
        assert seen == [("IMG", 100, 100, 800, 600)]

    def test_title_bar_gap_offsets_the_origin(self, monkeypatch):
        """Older macOS captures content only; bounds include the title bar."""
        self._install_quartz_sizes(monkeypatch, 1600, 1140)  # 570pt content height
        monkeypatch.setattr(gl, "_capture_window", lambda wid: "IMG")
        seen = []
        monkeypatch.setattr(gl, "_ocr_vision", lambda *a: seen.append(a) or [])
        gl._ocr_game_window(_win(x=100, y=100, w=800, h=600))
        assert seen == [("IMG", 100, 130, 800, 570)]

    def test_linux_uses_capture_geometry_not_window_geometry(self, monkeypatch):
        """xdotool geometry can include decorations; the mss region is truth."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            gl,
            "_capture_window_linux",
            lambda wid: ("IMG", {"left": 1, "top": 2, "width": 3, "height": 4}),
        )
        seen = []
        monkeypatch.setattr(gl, "_ocr_tesseract", lambda *a: seen.append(a) or [])
        gl._ocr_game_window(_win(x=100, y=200, w=800, h=600))
        assert seen == [("IMG", 1, 2, 3, 4)]


# ---------------------------------------------------------------------------
# _ocr_fullscreen (macOS path)
# ---------------------------------------------------------------------------


class TestOcrFullscreen:
    def _install_quartz(self, monkeypatch, image):
        return SimpleNamespace(
            CGMainDisplayID=lambda: 7,
            CGDisplayBounds=lambda did: SimpleNamespace(
                size=SimpleNamespace(width=2560, height=1440)
            ),
            CGWindowListCreateImage=lambda *a: image,
            kCGWindowListOptionAll=1,
            kCGNullWindowID=0,
            kCGWindowImageDefault=0,
        )

    def test_maps_via_display_dimensions(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "Quartz", self._install_quartz(monkeypatch, "IMG"))
        seen = []
        monkeypatch.setattr(gl, "_ocr_vision", lambda *a: seen.append(a) or [("X", 1, 1, 1, 1)])
        assert gl._ocr_fullscreen() == [("X", 1, 1, 1, 1)]
        assert seen == [("IMG", 0, 0, 2560, 1440)]

    def test_nil_image_falls_back_to_screencapture(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "Quartz", self._install_quartz(monkeypatch, None))
        monkeypatch.setattr(gl, "_capture_fullscreen_screencapture", lambda: "IMG2")
        seen = []
        monkeypatch.setattr(gl, "_ocr_vision", lambda *a: seen.append(a) or [])
        gl._ocr_fullscreen()
        assert seen == [("IMG2", 0, 0, 2560, 1440)]

    def test_screencapture_failure_returns_empty(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "Quartz", self._install_quartz(monkeypatch, None))
        monkeypatch.setattr(gl, "_capture_fullscreen_screencapture", lambda: None)
        assert gl._ocr_fullscreen() == []


# ---------------------------------------------------------------------------
# _normalize / _fuzzy_suffix_match / _find_text
# ---------------------------------------------------------------------------


class TestNormalize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("PLAY", "play"),
            ("AutoSave_0221", "autosave o221"),  # 0 -> o
            ("0_MCP_0135", "o mcp o135"),  # underscores become spaces
            (": Load Game =:", "load game"),  # OCR punctuation noise stripped
            ("  CONTINUE GAME  ", "continue game"),
            ("", ""),
        ],
    )
    def test_normalization_rules(self, raw, expected):
        assert gl._normalize(raw) == expected


class TestFuzzySuffixMatch:
    def test_matches_on_trailing_digits(self, monkeypatch):
        """Tesseract mangles save-name prefixes but keeps trailing digits."""
        rejected = [("VICP_0135", 1, 2, 3, 4)]
        assert gl._fuzzy_suffix_match("0_MCP_0135", rejected) == (
            "VICP_0135",
            1,
            2,
            3,
            4,
        )

    def test_target_without_three_digits_never_matches(self):
        rejected = [("anything", 1, 2, 3, 4)]
        assert gl._fuzzy_suffix_match("AutoSave_02", rejected) is None
        assert gl._fuzzy_suffix_match("no digits", rejected) is None

    def test_suffix_must_be_at_the_end(self):
        rejected = [("X0135_beta", 1, 2, 3, 4)]
        assert gl._fuzzy_suffix_match("0_MCP_0135", rejected) is None


class TestFindText:
    def test_exact_match_normalizes_underscores_and_zero(self):
        results = [("Load_Game", 1, 2, 3, 4)]
        assert gl._find_text(results, "Load Game", exact=True) == (
            "Load_Game",
            1,
            2,
            3,
            4,
        )
        assert gl._find_text([("0_MCP_0135", 1, 2, 3, 4)], "O MCP o135", exact=True)

    def test_exact_rejects_mere_substrings(self):
        results = [("Load Game Now", 1, 2, 3, 4)]
        assert gl._find_text(results, "Load Game", exact=True) is None

    def test_substring_match_by_default(self):
        results = [("CONTINUE GAME", 1, 2, 3, 4)]
        assert gl._find_text(results, "CONTINUE") == ("CONTINUE GAME", 1, 2, 3, 4)

    def test_first_match_wins_without_prefer_bottom(self):
        results = [("X", 1, 100, 1, 1), ("X", 1, 900, 1, 1)]
        assert gl._find_text(results, "X", exact=True)[2] == 100

    def test_prefer_bottom_picks_the_lowest_match(self):
        """A page title and its button share text; the button is lower."""
        results = [("Load Game", 1, 100, 1, 1), ("Load Game", 1, 900, 1, 1)]
        assert gl._find_text(results, "Load Game", exact=True, prefer_bottom=True)[2] == 900

    def test_min_y_fraction_filters_top_matches(self):
        results = [("A", 0, 500, 1, 1), ("noise", 0, 1000, 1, 1), ("A", 0, 800, 1, 1)]
        match = gl._find_text(results, "A", exact=True, min_y_fraction=0.7)
        assert match[2] == 800  # only y >= 700 survives

    def test_min_y_fraction_prefers_window_geometry_when_available(self):
        """With screen_bottom given (real window geometry), the filter zone
        is anchored to the window, not to the lowest OCR result — text-free
        screen areas no longer shrink it."""
        results = [("A", 0, 500, 1, 1), ("noise", 0, 1000, 1, 1)]
        # Proxy (max OCR y = 1000) would put the cutoff at 700 and reject
        # the match at 500; window bottom 714 puts it at 499.8 and keeps it.
        match = gl._find_text(results, "A", exact=True, min_y_fraction=0.7,
                              screen_bottom=714)
        assert match is not None and match[2] == 500
        # Without geometry the max-OCR-y proxy still applies.
        assert gl._find_text(results, "A", exact=True, min_y_fraction=0.7) is None

    def test_fuzzy_falls_back_to_rejected_low_confidence_results(self):
        gl._last_rejected_ocr.append(("VICP_0135", 9, 9, 9, 9))
        assert gl._find_text([("unrelated", 1, 1, 1, 1)], "0_MCP_0135") == (
            "VICP_0135",
            9,
            9,
            9,
            9,
        )

    def test_fuzzy_does_not_match_confident_results_by_suffix(self):
        """The suffix fallback is limited to the rejected low-confidence
        buffer — a confident result whose normalized text mismatched must
        NOT win on trailing digits (that could click the wrong save)."""
        results = [("VICP_0135", 5, 5, 5, 5)]
        assert gl._find_text(results, "0_MCP_0135") is None

    def test_no_match_returns_none(self):
        assert gl._find_text([("nothing", 1, 1, 1, 1)], "missing") is None


# ---------------------------------------------------------------------------
# Clicking and focus
# ---------------------------------------------------------------------------


class TestClick:
    def test_linux_moves_then_clicks(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        _no_sleep(monkeypatch)
        calls = []
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _cp(0)
        )
        gl._click(10, 20)
        assert calls == [
            ["xdotool", "mousemove", "10", "20"],
            ["xdotool", "click", "1"],
        ]

    def test_macos_without_gui_deps_raises(self, monkeypatch):
        """Pins current behaviour: _click hard-fails instead of no-oping."""
        monkeypatch.setitem(sys.modules, "Quartz", None)
        monkeypatch.setitem(sys.modules, "Vision", None)
        with pytest.raises(RuntimeError, match="launcher-macos"):
            gl._click(1, 1)


class TestBringToFront:
    def test_linux_activates_the_found_window(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(gl, "_find_game_window_linux", lambda: _win(window_id=99))
        _no_sleep(monkeypatch)
        calls = []
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _cp(0)
        )
        gl._bring_to_front()
        assert calls == [["xdotool", "windowactivate", "--sync", "99"]]

    def test_linux_without_window_does_nothing(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(gl, "_find_game_window_linux", lambda: None)

        def boom(cmd, **kw):  # pragma: no cover - must not be called
            raise AssertionError("no window, no xdotool")

        monkeypatch.setattr(subprocess, "run", boom)
        gl._bring_to_front()

    def test_macos_without_appkit_logs_and_returns(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "AppKit", None)
        gl._bring_to_front(pid=123)  # must not raise

    def test_macos_activates_the_pid_from_the_game_window(self, monkeypatch):
        activations = []
        app = SimpleNamespace(
            activateWithOptions_=lambda opts: activations.append(opts),
            isActive=lambda: True,
        )
        seen_pids = []
        appkit = SimpleNamespace(
            NSApplicationActivateIgnoringOtherApps=1,
            NSRunningApplication=SimpleNamespace(
                runningApplicationWithProcessIdentifier_=lambda pid: seen_pids.append(pid)
                or app
            ),
        )
        monkeypatch.setitem(sys.modules, "AppKit", appkit)
        monkeypatch.setattr(gl, "_find_game_window", lambda: _win(pid=555))
        _no_sleep(monkeypatch)

        gl._bring_to_front()  # no pid given -> looked up via the window
        assert seen_pids == [555]
        assert activations == [1]

    def test_macos_unknown_pid_returns_quietly(self, monkeypatch):
        appkit = SimpleNamespace(
            NSApplicationActivateIgnoringOtherApps=1,
            NSRunningApplication=SimpleNamespace(
                runningApplicationWithProcessIdentifier_=lambda pid: None
            ),
        )
        monkeypatch.setitem(sys.modules, "AppKit", appkit)
        gl._bring_to_front(pid=999)  # must not raise


class TestIsWindowFocused:
    def test_macos_matches_frontmost_app_name(self, monkeypatch):
        app = SimpleNamespace(localizedName=lambda: "Civilization VI")
        workspace = SimpleNamespace(frontmostApplication=lambda: app)
        monkeypatch.setitem(
            sys.modules,
            "AppKit",
            SimpleNamespace(
                NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace)
            ),
        )
        assert gl._is_window_focused() is True

    def test_macos_other_app_is_not_focused(self, monkeypatch):
        app = SimpleNamespace(localizedName=lambda: "Safari")
        workspace = SimpleNamespace(frontmostApplication=lambda: app)
        monkeypatch.setitem(
            sys.modules,
            "AppKit",
            SimpleNamespace(
                NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace)
            ),
        )
        assert gl._is_window_focused() is False

    def test_macos_without_appkit_reports_unfocused(self, monkeypatch):
        """ImportError is swallowed — a missing dep reads as 'not focused'."""
        monkeypatch.setitem(sys.modules, "AppKit", None)
        assert gl._is_window_focused() is False

    def test_linux_matches_active_window_title(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            subprocess, "run", lambda cmd, **kw: _cp(0, "Civilization VI\n")
        )
        assert gl._is_window_focused() is True

    def test_linux_other_title_or_failure_is_unfocused(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _cp(0, "Firefox\n"))
        assert gl._is_window_focused() is False
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _cp(1, ""))
        assert gl._is_window_focused() is False


# ---------------------------------------------------------------------------
# _wait_for_text / _click_text
# ---------------------------------------------------------------------------


class TestWaitForText:
    def test_finds_text_via_fullscreen_when_no_window(self, monkeypatch):
        monkeypatch.setattr(gl, "_require_gui_deps", lambda: None)
        monkeypatch.setattr(gl, "_find_game_window", lambda: None)
        monkeypatch.setattr(gl, "_ocr_fullscreen", lambda: [("HELLO", 1, 2, 3, 4)])
        _no_sleep(monkeypatch)
        assert gl._wait_for_text("HELLO", exact=True, timeout=5) == ("HELLO", 1, 2, 3, 4)

    def test_window_capture_failure_falls_back_to_fullscreen(self, monkeypatch):
        monkeypatch.setattr(gl, "_require_gui_deps", lambda: None)
        monkeypatch.setattr(gl, "_find_game_window", lambda: _win())
        monkeypatch.setattr(gl, "_bring_to_front", lambda pid=None: None)

        def boom(win):
            raise RuntimeError("capture failed")

        monkeypatch.setattr(gl, "_ocr_game_window", boom)
        monkeypatch.setattr(gl, "_ocr_fullscreen", lambda: [("X", 5, 6, 7, 8)])
        _no_sleep(monkeypatch)
        assert gl._wait_for_text("X", exact=True, timeout=5) == ("X", 5, 6, 7, 8)

    def test_timeout_returns_none(self, monkeypatch):
        monkeypatch.setattr(gl, "_require_gui_deps", lambda: None)
        monkeypatch.setattr(gl, "_find_game_window", lambda: None)
        monkeypatch.setattr(gl, "_ocr_fullscreen", lambda: [("A", 1, 1, 1, 1)])
        _no_sleep(monkeypatch)
        _script_time(monkeypatch, 0, 100)  # start, first loop check (expired)
        assert gl._wait_for_text("B", timeout=60) is None

    def test_missing_gui_deps_raise_before_any_ocr(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "Quartz", None)
        monkeypatch.setitem(sys.modules, "Vision", None)
        with pytest.raises(RuntimeError, match="launcher-macos"):
            gl._wait_for_text("anything", timeout=1)


class TestClickText:
    def test_clicks_the_match_center_plus_y_offset(self, monkeypatch):
        monkeypatch.setattr(
            gl, "_wait_for_text", lambda *a, **kw: ("T", 10, 20, 30, 40)
        )
        monkeypatch.setattr(gl, "_bring_to_front", lambda pid=None: None)
        _no_sleep(monkeypatch)
        clicks = []
        monkeypatch.setattr(gl, "_click", lambda x, y: clicks.append((x, y)))

        assert gl._click_text("T", y_offset=15) is True
        assert clicks == [(10, 35)]

    def test_no_match_means_no_click(self, monkeypatch):
        monkeypatch.setattr(gl, "_wait_for_text", lambda *a, **kw: None)

        def boom(x, y):  # pragma: no cover - must not be called
            raise AssertionError("must not click without a match")

        monkeypatch.setattr(gl, "_click", boom)
        assert gl._click_text("T") is False


# ---------------------------------------------------------------------------
# _click_continue_positional
# ---------------------------------------------------------------------------


class TestClickContinuePositional:
    def test_no_window_means_no_clicks(self, monkeypatch):
        monkeypatch.setattr(gl, "_find_game_window", lambda: None)

        def boom(x, y):  # pragma: no cover - must not be called
            raise AssertionError("must not click without a window")

        monkeypatch.setattr(gl, "_click", boom)
        gl._click_continue_positional()

    def test_clicks_the_whole_fallback_grid(self, monkeypatch):
        """Nine hardcoded relative positions, tried in order."""
        quartz = SimpleNamespace(
            CGImageGetWidth=lambda img: 1000, CGImageGetHeight=lambda img: 1000
        )
        monkeypatch.setitem(sys.modules, "Quartz", quartz)
        monkeypatch.setattr(gl, "_capture_window", lambda wid: "IMG")
        monkeypatch.setattr(gl, "_find_game_window", lambda: _win(x=0, y=0, w=1000, h=1000))
        monkeypatch.setattr(gl, "_bring_to_front", lambda pid=None: None)
        _no_sleep(monkeypatch)
        clicks = []
        monkeypatch.setattr(gl, "_click", lambda x, y: clicks.append((x, y)))

        gl._click_continue_positional()
        assert len(clicks) == 9
        assert clicks[0] == (320, 770)  # (0.32, 0.77) — verified Linux windowed
        assert clicks[-1] == (380, 750)  # (0.38, 0.75) — 4K fullscreen


# ---------------------------------------------------------------------------
# Save discovery
# ---------------------------------------------------------------------------


def _make_save(directory, name, mtime):
    path = directory / f"{name}.Civ6Save"
    path.write_text("save")
    import os

    os.utime(path, (mtime, mtime))
    return path


class TestAutosaveDiscovery:
    def test_no_saves_returns_none_and_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(gl, "SAVE_DIR", str(tmp_path))
        assert gl.get_latest_autosave() is None
        assert gl.list_autosaves() == []

    def test_latest_is_the_newest_by_mtime(self, monkeypatch, tmp_path):
        monkeypatch.setattr(gl, "SAVE_DIR", str(tmp_path))
        _make_save(tmp_path, "AutoSave_0001", mtime=100)
        _make_save(tmp_path, "AutoSave_0002", mtime=300)
        _make_save(tmp_path, "AutoSave_0003", mtime=200)
        assert gl.get_latest_autosave() == "AutoSave_0002"

    def test_list_is_newest_first_and_respects_limit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(gl, "SAVE_DIR", str(tmp_path))
        for i in range(5):
            _make_save(tmp_path, f"AutoSave_000{i}", mtime=100 + i)
        assert gl.list_autosaves(limit=3) == [
            "AutoSave_0004",
            "AutoSave_0003",
            "AutoSave_0002",
        ]

    def test_only_autosave_glob_matches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(gl, "SAVE_DIR", str(tmp_path))
        _make_save(tmp_path, "QuickSave", mtime=999)
        _make_save(tmp_path, "AutoSave_0001", mtime=100)
        assert gl.list_autosaves() == ["AutoSave_0001"]


# ---------------------------------------------------------------------------
# _navigate_to_save_sync — menu OCR failure messages
# ---------------------------------------------------------------------------


class TestNavigateToSave:
    """Each FAILED string is what the user sees when OCR navigation breaks;
    they must name the step and the likely cause."""

    def _patch_common(self, monkeypatch, running=True):
        monkeypatch.setattr(gl, "_require_gui_deps", lambda: None)
        monkeypatch.setattr(gl, "is_game_running", lambda: running)
        monkeypatch.setattr(gl, "_dismiss_crash_dialog", lambda: False)
        monkeypatch.setattr(gl, "_click_aspyr_launcher_sync", lambda: None)
        _no_sleep(monkeypatch)

    def test_main_menu_not_reached_is_actionable(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(gl, "_click_text", lambda *a, **kw: False)
        assert gl._navigate_to_save_sync("X") == (
            "FAILED: Could not find 'Single Player' on main menu. "
            "Is the game at the main menu?"
        )

    def test_not_running_launches_first(self, monkeypatch):
        self._patch_common(monkeypatch, running=False)
        launches = []
        monkeypatch.setattr(
            gl, "_launch_game_sync", lambda: launches.append(1) or "launched"
        )
        monkeypatch.setattr(gl, "_click_text", lambda *a, **kw: False)
        gl._navigate_to_save_sync("X")
        assert launches == [1]

    def test_missing_load_game_button_names_the_button(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(
            gl, "_click_text", lambda target, **kw: target == "Single Player"
        )
        assert gl._navigate_to_save_sync("X") == (
            "FAILED: Could not find 'Load Game' button."
        )

    def test_missing_save_lists_completed_steps(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(
            gl,
            "_click_text",
            lambda target, **kw: target in ("Single Player", "Load Game"),
        )
        assert gl._navigate_to_save_sync("MySave") == (
            "FAILED: Save 'MySave' not found. Steps completed: "
            "Clicked Single Player, Clicked Load Game, "
            "Autosaves filter (may already be active)"
        )

    def test_happy_path_clicks_continue_and_confirms_the_port(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(gl, "_click_text", lambda *a, **kw: True)
        monkeypatch.setattr(gl, "_find_game_window", lambda: _win())
        monkeypatch.setattr(
            gl, "_ocr_game_window", lambda win: [("CONTINUE GAME", 400, 500, 200, 40)]
        )
        monkeypatch.setattr(gl, "_bring_to_front", lambda pid=None: None)
        clicks = []
        monkeypatch.setattr(gl, "_click", lambda x, y: clicks.append((x, y)))
        monkeypatch.setattr(gl, "_is_tuner_port_open", lambda: True)

        result = gl._navigate_to_save_sync("MySave")
        assert "Clicked CONTINUE" in result
        assert "FireTuner port confirmed open" in result
        assert result.endswith("Wait ~10s then use get_game_overview to verify.")
        assert (400, 500) in clicks

    def test_happy_path_without_port_says_so(self, monkeypatch):
        self._patch_common(monkeypatch)
        monkeypatch.setattr(gl, "_click_text", lambda *a, **kw: True)
        monkeypatch.setattr(gl, "_find_game_window", lambda: _win())
        monkeypatch.setattr(
            gl, "_ocr_game_window", lambda win: [("CONTINUE GAME", 400, 500, 200, 40)]
        )
        monkeypatch.setattr(gl, "_bring_to_front", lambda pid=None: None)
        monkeypatch.setattr(gl, "_click", lambda x, y: None)
        monkeypatch.setattr(gl, "_is_tuner_port_open", lambda: False)

        assert "WARNING: FireTuner port not open after load" in gl._navigate_to_save_sync(
            "MySave"
        )


# ---------------------------------------------------------------------------
# load_save_from_menu (async public API)
# ---------------------------------------------------------------------------


class TestLoadSaveFromMenu:
    def _patch_dirs(self, monkeypatch, tmp_path):
        auto = tmp_path / "auto"
        auto.mkdir()
        monkeypatch.setattr(gl, "SAVE_DIR", str(auto))
        monkeypatch.setattr(gl, "SINGLE_SAVE_DIR", str(tmp_path))
        self.nav_calls = []

        def fake_nav(save_name, tab="Autosaves"):
            self.nav_calls.append((save_name, tab))
            return f"nav:{save_name}:{tab}"

        monkeypatch.setattr(gl, "_navigate_to_save_sync", fake_nav)
        return auto

    def test_no_autosaves_says_so_without_navigating(self, monkeypatch, tmp_path):
        self._patch_dirs(monkeypatch, tmp_path)

        async def go():
            return await gl.load_save_from_menu()

        assert run(go()) == "No autosaves found in save directory."
        assert self.nav_calls == []

    def test_default_save_is_the_latest_autosave(self, monkeypatch, tmp_path):
        auto = self._patch_dirs(monkeypatch, tmp_path)
        _make_save(auto, "AutoSave_0001", mtime=100)
        _make_save(auto, "AutoSave_0002", mtime=200)

        async def go():
            return await gl.load_save_from_menu()

        assert run(go()) == "nav:AutoSave_0002:Autosaves"
        assert self.nav_calls == [("AutoSave_0002", "Autosaves")]

    def test_autosave_gets_the_autosaves_tab(self, monkeypatch, tmp_path):
        auto = self._patch_dirs(monkeypatch, tmp_path)
        _make_save(auto, "AutoSave_0007", mtime=100)

        async def go():
            return await gl.load_save_from_menu("AutoSave_0007")

        assert run(go()) == "nav:AutoSave_0007:Autosaves"

    def test_regular_save_uses_the_default_view(self, monkeypatch, tmp_path):
        """Regular saves show by default — no tab click needed (tab=None)."""
        self._patch_dirs(monkeypatch, tmp_path)
        _make_save(tmp_path, "Benchmark", mtime=100)

        async def go():
            return await gl.load_save_from_menu("Benchmark")

        assert run(go()) == "nav:Benchmark:None"
        assert self.nav_calls == [("Benchmark", None)]

    def test_missing_save_lists_what_is_available(self, monkeypatch, tmp_path):
        auto = self._patch_dirs(monkeypatch, tmp_path)
        _make_save(auto, "AutoSave_0001", mtime=100)
        _make_save(tmp_path, "Benchmark", mtime=200)

        async def go():
            return await gl.load_save_from_menu("Ghost")

        result = run(go())
        assert result.startswith("Save 'Ghost' not found. Available: ")
        assert "AutoSave_0001" in result and "Benchmark" in result
        assert self.nav_calls == []

    def test_missing_save_with_empty_dirs_lists_none(self, monkeypatch, tmp_path):
        self._patch_dirs(monkeypatch, tmp_path)

        async def go():
            return await gl.load_save_from_menu("Ghost")

        assert run(go()) == "Save 'Ghost' not found. Available: none"


# ---------------------------------------------------------------------------
# Async wrappers and the restart_and_load recovery sequence
# ---------------------------------------------------------------------------


class TestAsyncWrappers:
    def test_async_apis_delegate_to_their_sync_impls(self, monkeypatch):
        """Wiring smoke test: the async wrappers must reach the sync impls and
        pass their returns through."""
        monkeypatch.setattr(gl, "_kill_game_sync", lambda: "K")
        monkeypatch.setattr(gl, "_launch_game_sync", lambda: "L")
        monkeypatch.setattr(gl, "_dismiss_crash_dialogs_sync", lambda: ["dlg"])

        async def go():
            return (
                await gl.kill_game(),
                await gl.launch_game(),
                await gl.dismiss_crash_dialogs(),
            )

        assert run(go()) == ("K", "L", ["dlg"])


class TestDismissCrashDialogsSync:
    def test_non_windows_is_always_empty(self, monkeypatch):
        assert gl._dismiss_crash_dialogs_sync() == []


class TestRestartAndLoad:
    def _patch(self, monkeypatch, launch_result):
        events = []

        async def fake_dismiss():
            events.append("dismiss")
            return []

        async def fake_kill():
            events.append("kill")
            return "Game killed."

        async def fake_launch():
            events.append("launch")
            return launch_result

        async def fake_load(name):
            events.append(("load", name))
            return "Save loading."

        monkeypatch.setattr(gl, "dismiss_crash_dialogs", fake_dismiss)
        monkeypatch.setattr(gl, "kill_game", fake_kill)
        monkeypatch.setattr(gl, "launch_game", fake_launch)
        monkeypatch.setattr(gl, "load_save_from_menu", fake_load)
        return events

    def test_full_sequence_joins_step_reports(self, monkeypatch):
        events = self._patch(
            monkeypatch, "Game launched. Process started after 5s, FireTuner port is open."
        )

        async def go():
            return await gl.restart_and_load("0_MCP_0042")

        result = run(go())
        assert result == (
            "Kill: Game killed. | "
            "Launch: Game launched. Process started after 5s, FireTuner port is open. | "
            "Load: Save loading."
        )
        assert events == ["dismiss", "kill", "launch", "dismiss", ("load", "0_MCP_0042")]

    def test_failed_launch_aborts_before_loading(self, monkeypatch):
        """All _launch_game_sync failure paths now carry both the FAILED:
        marker and the words 'not detected', so the abort sniff catches
        them even when the GUI-deps warning rides along."""
        events = self._patch(
            monkeypatch,
            "FAILED: Game process not detected after launch. Check Steam. "
            "Also: WARNING: GUI deps not available. "
            "Click PLAY on the Aspyr launcher manually.",
        )

        async def go():
            return await gl.restart_and_load("0_MCP_0042")

        result = run(go())
        assert result.endswith("| ABORTED: Game failed to launch.")
        assert ("load", "0_MCP_0042") not in events

    def test_successful_launch_with_gui_warning_proceeds_to_load(self, monkeypatch):
        """A launch that succeeded despite the Aspyr-click warning is not a
        failure — recovery must push on into the save load."""
        events = self._patch(
            monkeypatch,
            "Game launched. Process started after 5s, FireTuner port is open. "
            "(WARNING: GUI deps not available. "
            "Click PLAY on the Aspyr launcher manually.)",
        )

        async def go():
            return await gl.restart_and_load("0_MCP_0042")

        result = run(go())
        assert "ABORTED" not in result
        assert ("load", "0_MCP_0042") in events
