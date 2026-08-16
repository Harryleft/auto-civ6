"""Offline coverage for API-first save recovery and OCR guardrails."""

from __future__ import annotations

import asyncio

from civ_mcp import game_launcher, game_lifecycle


class _MainMenuConnection:
    lua_states = {24: "MainMenu"}
    gamecore_index = None

    async def ensure_connected(self):
        return None

    async def execute_in_state(self, state, code, **kwargs):
        assert state == 24
        assert "Automation.SetAutoStartEnabled(true)" in code
        assert 'loadGame.Name = "0_MCP_0012"' in code
        assert kwargs["mutation"] is True
        return ["MCP_FRONTEND_AUTOSTART|true", "MCP_FRONTEND_LOAD|true"]


def test_load_game_save_uses_frontend_api_at_main_menu(monkeypatch):
    monkeypatch.delenv(game_launcher.OCR_RECOVERY_ENV, raising=False)

    async def unexpected_ocr(_save_name):
        raise AssertionError("main-menu API success must not invoke OCR")

    monkeypatch.setattr(game_launcher, "load_save_from_menu", unexpected_ocr)

    result = asyncio.run(
        game_lifecycle.load_game_save(_MainMenuConnection(), "0_MCP_0012")
    )

    assert result.startswith("Loading save 0_MCP_0012 via FrontEnd API")


def test_load_game_save_refuses_ocr_when_frontend_api_fails(monkeypatch):
    monkeypatch.delenv(game_launcher.OCR_RECOVERY_ENV, raising=False)

    class RefusingConnection(_MainMenuConnection):
        async def execute_in_state(self, _state, _code, **_kwargs):
            return ["MCP_FRONTEND_AUTOSTART|true", "MCP_FRONTEND_LOAD|false"]

    result = asyncio.run(
        game_lifecycle.load_game_save(RefusingConnection(), "0_MCP_0012")
    )

    assert result.startswith("Error: FrontEnd refused save 0_MCP_0012")
    assert "OCR fallback is disabled by default" in result


def test_low_level_menu_loader_is_opt_in_ocr_only(monkeypatch):
    monkeypatch.delenv(game_launcher.OCR_RECOVERY_ENV, raising=False)

    result = asyncio.run(game_launcher.load_save_from_menu("0_MCP_0012"))

    assert result.startswith("Error: OCR recovery is disabled by default")


def test_restart_reuses_shared_connection_for_frontend_api(monkeypatch):
    monkeypatch.delenv(game_launcher.OCR_RECOVERY_ENV, raising=False)
    monkeypatch.setattr(game_launcher, "dismiss_crash_dialogs", _empty_dismiss)
    monkeypatch.setattr(game_launcher, "kill_game", _done("killed"))
    monkeypatch.setattr(game_launcher, "launch_game", _done("launched"))

    class RestartConnection:
        lua_states = {}
        gamecore_index = None
        ingame_index = None

        def __init__(self):
            self.loaded = False

        async def reconnect(self):
            if self.loaded:
                self.lua_states = {3: "GameCore_Tuner", 4: "InGame"}
                self.gamecore_index = 3
                self.ingame_index = 4
            else:
                self.lua_states = {24: "MainMenu"}

    async def fake_frontend(_conn, save_name):
        assert save_name == "0_MCP_0012"
        _conn.loaded = True
        return "Loading save 0_MCP_0012 via FrontEnd API"

    monkeypatch.setattr(game_lifecycle, "load_save_from_frontend", fake_frontend)
    monkeypatch.setattr(game_launcher.asyncio, "sleep", _no_sleep)

    conn = RestartConnection()
    result = asyncio.run(
        game_launcher.restart_and_load(
            "0_MCP_0012", conn=conn
        )
    )

    assert "Loading save 0_MCP_0012 via FrontEnd API" in result
    assert "GameCore/InGame ready" in result


def test_restart_finishes_leader_screen_with_opt_in_continue(monkeypatch):
    monkeypatch.setenv(game_launcher.OCR_RECOVERY_ENV, "1")
    monkeypatch.setattr(game_launcher, "dismiss_crash_dialogs", _empty_dismiss)
    monkeypatch.setattr(game_launcher, "kill_game", _done("killed"))
    monkeypatch.setattr(game_launcher, "launch_game", _done("launched"))
    monkeypatch.setattr(game_launcher.asyncio, "sleep", _no_sleep)

    class LeaderScreenConnection:
        def __init__(self):
            self.reconnects = 0
            self.loaded = False
            self.lua_states = {}
            self.gamecore_index = None
            self.ingame_index = None

        async def reconnect(self):
            self.reconnects += 1
            if not self.loaded:
                self.lua_states = {24: "MainMenu"}
            elif self.reconnects == 2:
                self.lua_states = {7: "LeaderScene"}
                self.gamecore_index = None
                self.ingame_index = None
            else:
                self.lua_states = {3: "GameCore_Tuner", 4: "InGame"}
                self.gamecore_index = 3
                self.ingame_index = 4

    conn = LeaderScreenConnection()
    continue_calls: list[tuple[str, dict[str, object]]] = []

    async def fake_frontend(_conn, save_name):
        assert save_name == "0_MCP_0012"
        _conn.loaded = True
        return "Loading save 0_MCP_0012 via FrontEnd API"

    def fake_click(target, **kwargs):
        continue_calls.append((target, kwargs))
        return True

    monkeypatch.setattr(game_lifecycle, "load_save_from_frontend", fake_frontend)
    monkeypatch.setattr(game_launcher, "_click_text", fake_click)

    result = asyncio.run(game_launcher.restart_and_load("0_MCP_0012", conn=conn))

    assert continue_calls == [("CONTINUE", {"timeout": 105, "post_delay": 1})]
    assert conn.reconnects == 3
    assert "GameCore/InGame ready" in result


def test_restart_does_not_claim_success_without_gui_or_opt_in_ocr(monkeypatch):
    monkeypatch.delenv(game_launcher.OCR_RECOVERY_ENV, raising=False)
    monkeypatch.setattr(game_launcher, "dismiss_crash_dialogs", _empty_dismiss)
    monkeypatch.setattr(game_launcher, "kill_game", _done("killed"))
    monkeypatch.setattr(game_launcher, "launch_game", _done("launched"))
    monkeypatch.setattr(game_launcher.asyncio, "sleep", _no_sleep)

    class LeaderScreenConnection:
        def __init__(self):
            self.loaded = False
            self.lua_states = {}
            self.gamecore_index = None
            self.ingame_index = None

        async def reconnect(self):
            self.lua_states = (
                {7: "LeaderScene"} if self.loaded else {24: "MainMenu"}
            )

    conn = LeaderScreenConnection()

    async def fake_frontend(_conn, save_name):
        assert save_name == "0_MCP_0012"
        _conn.loaded = True
        return "Loading save 0_MCP_0012 via FrontEnd API"

    def unexpected_ocr(*_args, **_kwargs):
        raise AssertionError("OCR must remain disabled without explicit opt-in")

    monkeypatch.setattr(game_lifecycle, "load_save_from_frontend", fake_frontend)
    monkeypatch.setattr(game_launcher, "_click_text", unexpected_ocr)
    monkeypatch.setattr(game_launcher, "_click_continue_positional", unexpected_ocr)

    result = asyncio.run(game_launcher.restart_and_load("0_MCP_0012", conn=conn))

    assert "Error: FrontEnd load started" in result
    assert "OCR post-load recovery is disabled by default" in result
    assert "GameCore/InGame ready" not in result


def test_restart_waits_for_main_menu_not_just_any_lua_state(monkeypatch):
    monkeypatch.delenv(game_launcher.OCR_RECOVERY_ENV, raising=False)
    monkeypatch.setattr(game_launcher, "dismiss_crash_dialogs", _empty_dismiss)
    monkeypatch.setattr(game_launcher, "kill_game", _done("killed"))
    monkeypatch.setattr(game_launcher, "launch_game", _done("launched"))

    class BootingConnection:
        lua_states = {}
        gamecore_index = None
        ingame_index = None

        def __init__(self):
            self.reconnects = 0
            self.loaded = False

        async def reconnect(self):
            self.reconnects += 1
            if self.reconnects == 1:
                self.lua_states = {3: "GameCore_Tuner"}
            elif not self.loaded:
                self.lua_states = {24: "MainMenu"}
            else:
                self.lua_states = {3: "GameCore_Tuner", 4: "InGame"}
                self.gamecore_index = 3
                self.ingame_index = 4

    conn = BootingConnection()
    loaded: list[str] = []

    async def fake_frontend(_conn, save_name):
        loaded.append(save_name)
        _conn.loaded = True
        return "Loading save 0_MCP_0012 via FrontEnd API"

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(game_lifecycle, "load_save_from_frontend", fake_frontend)
    monkeypatch.setattr(game_launcher.asyncio, "sleep", no_sleep)

    result = asyncio.run(game_launcher.restart_and_load("0_MCP_0012", conn=conn))

    assert conn.reconnects == 3
    assert loaded == ["0_MCP_0012"]
    assert "Loading save 0_MCP_0012 via FrontEnd API" in result


async def _empty_dismiss():
    return []


async def _no_sleep(_seconds):
    return None


def _done(result: str):
    async def _run():
        return result

    return _run
