"""Explicit spy escape commands: safe Lua ordering and no automatic route choice."""

import asyncio

import pytest

from civ_mcp.game_state import GameState
from civ_mcp.lua.espionage import build_spy_escape_route


class FakeConn:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def execute_write(self, code):
        self.calls.append(code)
        return self.response


def test_escape_blocker_guard_precedes_native_spy_lookup():
    # pcall cannot catch the documented native ACCESS_VIOLATION. The emitted
    # chunk must return before that lookup when no live escape blocker exists.
    lua = build_spy_escape_route(7, "DISTRICT_AERODROME")
    guard = lua.index('if not escapeBlocked then')
    lookup = lua.index('GetNextEscapingSpyID()')
    assert lua.index('not entry:IsDismissed()') < guard < lookup
    assert 'ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE' in lua[:guard]
    assert '; return end' in lua[guard:lookup]


def test_wrong_spy_and_missing_route_are_rejected_before_operation():
    lua = build_spy_escape_route(7, "DISTRICT_HARBOR")
    request = lua.index('UI.RequestPlayerOperation')
    assert lua.index('if spyID ~= 7 then') < request
    assert 'ERR:SPY_NOT_ESCAPING' in lua[:request]
    assert 'HasDistrict(chosen.Index, true, true)' in lua[:request]
    assert 'ERR:ESCAPE_ROUTE_UNAVAILABLE' in lua[:request]


@pytest.mark.parametrize("district", [
    "DISTRICT_AERODROME", "DISTRICT_HARBOR",
    "DISTRICT_COMMERCIAL_HUB", "DISTRICT_CITY_CENTER",
])
def test_requested_route_is_the_only_selected_district(district):
    lua = build_spy_escape_route(7, district)
    assert f'local chosen = GameInfo.Districts["{district}"]' in lua
    assert lua.count('local chosen =') == 1
    assert 'if not chosen then' in lua  # missing GameInfo is an error, no fallback
    assert 'chosen = GameInfo.Districts["DISTRICT_CITY_CENTER"]' not in lua or district == 'DISTRICT_CITY_CENTER'
    assert lua.count('UI.RequestPlayerOperation') == 1


@pytest.mark.parametrize("district", [
    "", "DISTRICT_CAMPUS", 'DISTRICT_HARBOR\"]; print(\"injected\") --',
])
def test_unknown_route_is_rejected_before_transport(district):
    conn = FakeConn([])
    gs = GameState(conn)
    with pytest.raises(ValueError, match="Unknown spy escape route"):
        asyncio.run(gs.spy_escape(7, district))
    assert conn.calls == []


@pytest.mark.parametrize("response, expected", [
    (["OK:ESCAPE_ROUTE|Agent escaping via DISTRICT_HARBOR"], "ESCAPE_ROUTE|Agent escaping via DISTRICT_HARBOR"),
    (["ERR:ESCAPE_ROUTE_UNAVAILABLE"], "Error: ESCAPE_ROUTE_UNAVAILABLE"),
    ([], "Error: OUTCOME_UNKNOWN"),
])
def test_game_state_submits_once_and_surfaces_result(response, expected):
    conn = FakeConn(response)
    result = asyncio.run(GameState(conn).spy_escape(7, "DISTRICT_HARBOR"))
    assert result.startswith(expected)
    assert len(conn.calls) == 1
