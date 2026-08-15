"""Offline tests for the opt-in DSH startup recovery path."""

from __future__ import annotations

import asyncio
import os
import sys
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from civ_mcp import game_launcher, server


def test_macos_hidden_game_pid_is_found_from_ns_workspace(monkeypatch):
    class FakeURL:
        def path(self):
            return "/Applications/Civilization VI/Civ6_Exe_Child"

    class FakeApp:
        def localizedName(self):
            return "Civilization VI"

        def executableURL(self):
            return FakeURL()

        def processIdentifier(self):
            return 70443

    class FakeWorkspace:
        @classmethod
        def sharedWorkspace(cls):
            return cls()

        def runningApplications(self):
            return [FakeApp()]

    appkit = ModuleType("AppKit")
    appkit.NSWorkspace = FakeWorkspace
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setattr(game_launcher.sys, "platform", "darwin")

    assert game_launcher._find_running_game_pid() == 70443


def test_macos_gui_navigation_activates_hidden_running_game(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(game_launcher.sys, "platform", "darwin")
    monkeypatch.setattr(game_launcher, "_require_gui_deps", lambda: None)
    monkeypatch.setattr(game_launcher, "is_game_running", lambda: True)
    monkeypatch.setattr(
        game_launcher,
        "_activate_running_game_app",
        lambda: calls.append("activate") or True,
    )
    monkeypatch.setattr(game_launcher, "_dismiss_crash_dialog", lambda: False)
    monkeypatch.setattr(game_launcher, "_click_aspyr_launcher_sync", lambda: None)
    monkeypatch.setattr(game_launcher, "_click_ui_label", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(game_launcher, "_click_text", lambda *_args, **_kwargs: False)

    result = game_launcher._navigate_to_save_sync("0_MCP_0012", tab=None)

    assert calls == ["activate"]
    assert result.startswith("FAILED: Could not find 'Single Player /")


def test_localized_ui_click_candidates_try_chinese_then_english(monkeypatch):
    calls: list[str] = []

    def fake_click(label, **_kwargs):
        calls.append(label)
        return label == "Load Game"

    monkeypatch.setattr(game_launcher, "_click_text", fake_click)

    assert game_launcher._click_ui_label("load_game", timeout=10) == "Load Game"
    assert calls == ["加载游戏", "Load Game"]


def test_latest_recovery_save_prefers_mcp_checkpoint(tmp_path, monkeypatch):
    """An MCP checkpoint wins even when the game autosave is newer."""

    regular = tmp_path / "regular"
    autos = tmp_path / "auto"
    regular.mkdir()
    autos.mkdir()
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(regular))
    monkeypatch.setattr(game_launcher, "SAVE_DIR", str(autos))

    mcp = regular / "0_MCP_0012.Civ6Save"
    mcp.write_bytes(b"mcp-save")
    os.utime(mcp, (100, 100))
    game_auto = autos / "AutoSave_0099.Civ6Save"
    game_auto.write_bytes(b"game-save")
    os.utime(game_auto, (200, 200))

    assert game_launcher.get_latest_recovery_save() == "0_MCP_0012"


def test_latest_recovery_save_falls_back_to_newest_game_autosave(tmp_path, monkeypatch):
    regular = tmp_path / "regular"
    autos = tmp_path / "auto"
    regular.mkdir()
    autos.mkdir()
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(regular))
    monkeypatch.setattr(game_launcher, "SAVE_DIR", str(autos))

    old_auto = autos / "AutoSave_0007.Civ6Save"
    old_auto.write_bytes(b"old")
    os.utime(old_auto, (100, 100))
    new_auto = autos / "AutoSave_0008.Civ6Save"
    new_auto.write_bytes(b"new")
    os.utime(new_auto, (200, 200))

    assert game_launcher.get_latest_recovery_save() == "AutoSave_0008"


def test_latest_recovery_save_ignores_empty_or_unrelated_files(tmp_path, monkeypatch):
    regular = tmp_path / "regular"
    autos = tmp_path / "auto"
    regular.mkdir()
    autos.mkdir()
    monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(regular))
    monkeypatch.setattr(game_launcher, "SAVE_DIR", str(autos))

    (regular / "0_MCP_0012.Civ6Save").touch()
    (regular / "not-a-save.txt").write_bytes(b"noise")

    assert game_launcher.get_latest_recovery_save() is None


def test_dsh_auto_resume_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv(server.DSH_AUTO_RESUME_ENV, raising=False)
    assert server._dsh_auto_resume_enabled() is False


def test_dsh_auto_resume_requires_explicit_truthy_value(monkeypatch):
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv(server.DSH_AUTO_RESUME_ENV, value)
        assert server._dsh_auto_resume_enabled() is True
    for value in ("0", "false", "", "random"):
        monkeypatch.setenv(server.DSH_AUTO_RESUME_ENV, value)
        assert server._dsh_auto_resume_enabled() is False


def test_dsh_auto_resume_skips_an_already_loaded_game(monkeypatch):
    class InGameConnection:
        gamecore_index = 10
        ingame_index = 11

        async def connect(self):
            raise AssertionError("already-loaded game should not reconnect")

    monkeypatch.setattr(game_launcher, "get_latest_recovery_save", lambda: "0_MCP_0012")
    load_calls: list[str] = []
    monkeypatch.setattr(
        game_launcher,
        "load_save_from_menu",
        lambda save: load_calls.append(save),
    )
    monkeypatch.setattr(server.heartbeat, "write", lambda *_args, **_kwargs: None)

    asyncio.run(server._auto_resume(InGameConnection()))

    assert load_calls == []


def test_dsh_auto_resume_uses_gui_for_main_menu_and_reconnects(monkeypatch):
    events: list[str] = []

    class MainMenuConnection:
        gamecore_index = None
        ingame_index = None

        @property
        def is_connected(self):
            return "connected" in events and "disconnected" not in events

        async def connect(self):
            events.append("connect")
            # The first connection represents the main menu; the next one
            # represents the loaded game after GUI navigation.
            if events.count("connect") > 1:
                self.gamecore_index = 10
                self.ingame_index = 11

        async def disconnect(self):
            events.append("disconnected")

        async def reconnect(self):
            events.append("reconnect")
            self.gamecore_index = 10
            self.ingame_index = 11

    monkeypatch.setattr(game_launcher, "get_latest_recovery_save", lambda: "0_MCP_0012")
    loaded: list[str] = []

    async def fake_load(save):
        loaded.append(save)
        return "Save loading (GUI)"

    monkeypatch.setattr(game_launcher, "load_save_from_menu", fake_load)
    monkeypatch.setattr(game_launcher, "_is_tuner_port_open", lambda: False)
    monkeypatch.setattr(server.heartbeat, "write", lambda *_args, **_kwargs: None)

    asyncio.run(server._auto_resume(MainMenuConnection()))

    assert loaded == ["0_MCP_0012"]
    assert "disconnected" in events
    assert "connect" in events


def test_auto_resume_background_does_not_start_watchers_until_recovery_finishes(
    monkeypatch,
):
    """The shared FireTuner watchers stay stopped during GUI recovery."""

    class Service:
        def __init__(self):
            self.started = False

        def start(self):
            self.started = True

    release = asyncio.Event()
    camera = Service()
    popup_watcher = Service()
    watchdog = Service()

    async def blocked_resume(_conn):
        await release.wait()

    async def exercise():
        monkeypatch.setattr(server, "_auto_resume", blocked_resume)
        task = asyncio.create_task(
            server._auto_resume_then_start_services(
                object(), camera, popup_watcher, watchdog
            )
        )
        await asyncio.sleep(0)
        assert camera.started is False
        assert popup_watcher.started is False
        assert watchdog.started is False
        release.set()
        await task

    asyncio.run(exercise())

    assert camera.started is True
    assert popup_watcher.started is True
    assert watchdog.started is True


def test_auto_resume_background_cancellation_does_not_start_watchers(monkeypatch):
    camera = MagicMock()
    popup_watcher = MagicMock()
    watchdog = MagicMock()
    started = asyncio.Event()

    async def blocked_resume(_conn):
        await started.wait()

    async def exercise():
        monkeypatch.setattr(server, "_auto_resume", blocked_resume)
        task = asyncio.create_task(
            server._auto_resume_then_start_services(
                object(), camera, popup_watcher, watchdog
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(exercise())
    camera.start.assert_not_called()
    popup_watcher.start.assert_not_called()
    watchdog.start.assert_not_called()


def test_lifespan_yields_before_dsh_recovery_finishes(monkeypatch):
    """A slow GUI recovery must not block the MCP server handshake."""

    monkeypatch.setenv(server.DSH_AUTO_RESUME_ENV, "1")
    monkeypatch.delenv("CIV_MCP_SAVE_FILE", raising=False)

    class FakeEmitter:
        run_id = "test-run"

        def add_sink(self, _sink):
            pass

        def start(self):
            pass

        async def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.disconnect = AsyncMock()

    class FakeHttpServer:
        def __init__(self, _config):
            self.should_exit = False

        async def serve(self):
            while not self.should_exit:
                await asyncio.sleep(0)

    fake_camera = SimpleNamespace(start=MagicMock(), stop=AsyncMock())
    fake_popup = SimpleNamespace(start=MagicMock(), stop=AsyncMock())
    fake_watchdog = SimpleNamespace(start=MagicMock(), stop=AsyncMock())

    monkeypatch.setattr(server, "TelemetryEmitter", FakeEmitter)
    monkeypatch.setattr(server, "LocalSink", lambda: object())
    monkeypatch.setattr(server, "GameConnection", FakeConnection)
    monkeypatch.setattr(
        server,
        "GameLogger",
        lambda _emitter: SimpleNamespace(session_id="test-session"),
    )
    monkeypatch.setattr(server, "SpatialTracker", lambda _emitter: object())
    monkeypatch.setattr(server, "MapCapture", lambda _emitter: object())
    monkeypatch.setattr(server, "GameState", lambda _conn: object())
    monkeypatch.setattr(server, "BeliefEngine", lambda run_id: object())
    monkeypatch.setattr(server, "CameraController", lambda _conn: fake_camera)
    monkeypatch.setattr(server, "PopupWatcher", lambda _conn: fake_popup)
    monkeypatch.setattr(
        server,
        "GameOverWatchdog",
        lambda _gs, _logger: fake_watchdog,
    )
    monkeypatch.setattr(server, "create_app", lambda _gs: object())
    monkeypatch.setattr(server.uvicorn, "Config", lambda *args, **kwargs: object())
    monkeypatch.setattr(server.uvicorn, "Server", FakeHttpServer)
    monkeypatch.setattr(server.heartbeat, "init", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server.heartbeat, "bind_eval", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server.heartbeat, "write", lambda *_args, **_kwargs: None)

    async def blocked_recovery(*_args):
        try:
            await asyncio.Event().wait()
        finally:
            _args[-1].set()

    monkeypatch.setattr(server, "_auto_resume_then_start_services", blocked_recovery)
    observed_context = []

    async def exercise_lifespan():
        manager = server.lifespan(server.mcp)
        context = await asyncio.wait_for(manager.__aenter__(), timeout=0.25)
        observed_context.append(context)
        assert context.camera is fake_camera
        assert context.auto_resume_ready.is_set() is False
        assert fake_camera.start.call_count == 0
        assert fake_popup.start.call_count == 0
        assert fake_watchdog.start.call_count == 0
        await manager.__aexit__(None, None, None)

    asyncio.run(exercise_lifespan())
    assert observed_context[0].auto_resume_ready.is_set() is True
    assert fake_camera.stop.await_count == 1
    assert fake_popup.stop.await_count == 1
    assert fake_watchdog.stop.await_count == 1
