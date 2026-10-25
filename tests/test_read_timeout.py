"""Read tools must fail fast instead of hanging until the CLI RPC deadline.

A 300-second hung read latches the CLI's transport-unknown gate and leaves the
session usable only for reads. Bounding game-backed read tools converts that
into an explicit, non-pending error the caller can act on.
"""

from __future__ import annotations

import asyncio

import pytest

from civ_mcp import server


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def short_budget(monkeypatch):
    monkeypatch.setattr(server, "READ_TIMEOUT_SECONDS", 0.05)


def test_read_tool_classification_covers_game_queries():
    for name in ("get_game_overview", "get_units", "list_saves", "search_research"):
        assert server._is_read_tool(name)
    for name in ("end_turn", "unit_action", "set_city_production", "restart_and_load"):
        assert not server._is_read_tool(name)


def test_fast_read_returns_its_value():
    async def fast():
        return "OVERVIEW|ok"

    assert run(server._bounded_read("get_game_overview", fast)) == "OVERVIEW|ok"


def test_slow_read_is_cancelled_with_explicit_error():
    async def slow():
        await asyncio.sleep(5)
        return "never"

    result = run(server._bounded_read("get_game_overview", slow))

    assert result.startswith("ERR:TOOL_TIMEOUT|get_game_overview exceeded")
    assert "cancelled without confirming or replaying" in result
    assert "main menu" in result


def test_timeout_names_the_tool_under_test():
    async def slow():
        await asyncio.sleep(5)

    assert "get_units" in run(server._bounded_read("get_units", slow))
