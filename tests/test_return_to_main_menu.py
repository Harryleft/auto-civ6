"""`game menu` must confirm the native main menu and keep the replacement gate."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from civ_mcp import game_lifecycle
from civ_mcp.game_state import GameState


def run(coro):
    return asyncio.run(coro)


def state():
    return GameState(SimpleNamespace())


def patch_result(monkeypatch, transition):
    async def result(conn, timeout=30.0):
        return transition

    monkeypatch.setattr(game_lifecycle, "return_to_main_menu_result", result)
    monkeypatch.setattr(game_lifecycle, "clear_save_listing", lambda: None)


def test_confirmed_menu_clears_the_replacement_gate(monkeypatch):
    patch_result(monkeypatch, game_lifecycle.NativeTransition("confirmed", "Main menu confirmed."))
    game = state()

    result = run(game.return_to_main_menu())

    assert result.startswith("MAIN_MENU|")
    assert game.replacement_pending is False


def test_unconfirmed_exit_retains_the_gate(monkeypatch):
    patch_result(monkeypatch, game_lifecycle.NativeTransition(
        "unknown",
        "ERR:OUTCOME_UNKNOWN|Return to main menu was attempted once but not confirmed; do not resend.",
    ))
    game = state()

    result = run(game.return_to_main_menu())

    assert result.startswith("ERR:OUTCOME_UNKNOWN|")
    assert result.count("ERR:OUTCOME_UNKNOWN|") == 1
    assert game.replacement_pending is True


def test_refused_exit_releases_the_gate(monkeypatch):
    patch_result(monkeypatch, game_lifecycle.NativeTransition(
        "not_submitted", "Error: Native frontend state unavailable; no exit sent."))
    game = state()

    result = run(game.return_to_main_menu())

    assert result.startswith("Error:")
    assert game.replacement_pending is False


def test_menu_is_blocked_while_a_replacement_is_unconfirmed(monkeypatch):
    patch_result(monkeypatch, game_lifecycle.NativeTransition("confirmed", "Main menu confirmed."))
    game = state()
    game._pending_load = True

    result = run(game.return_to_main_menu())

    assert result.startswith("ERR:OUTCOME_UNKNOWN|")
    assert "Load or save completion" in result
    assert game.replacement_pending is True
