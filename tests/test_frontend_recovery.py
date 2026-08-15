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

        async def reconnect(self):
            self.lua_states = {24: "MainMenu"}

    async def fake_frontend(_conn, save_name):
        assert save_name == "0_MCP_0012"
        return "Loading save 0_MCP_0012 via FrontEnd API"

    monkeypatch.setattr(game_lifecycle, "load_save_from_frontend", fake_frontend)

    result = asyncio.run(
        game_launcher.restart_and_load(
            "0_MCP_0012", conn=RestartConnection()
        )
    )

    assert "Loading save 0_MCP_0012 via FrontEnd API" in result


async def _empty_dismiss():
    return []


def _done(result: str):
    async def _run():
        return result

    return _run
