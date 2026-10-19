"""Explicit native Congress UI entry at the GameState/FireTuner boundary."""
import asyncio

from civ_mcp.game_state import GameState
from action_scope_support import scope_rows


class OpenConn:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def execute_write(self, code):
        if 'print("ACTION_SCOPE|' in code:
            return scope_rows()
        self.calls.append(code)
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def test_explicit_open_enters_native_voting_without_casting_or_advancing():
    from test_congress_confirmation import LifecycleCongressConn
    conn = LifecycleCongressConn()
    result = asyncio.run(GameState(conn).open_world_congress())
    assert result == "WC_VOTING_OPEN|stage:STAGE1|turn:67"
    assert conn.opens == ["open"]
    assert conn.operations == conn.actions == []


def test_lost_open_reply_then_repeated_call_only_confirms_visible_voting():
    from test_congress_confirmation import LifecycleCongressConn
    conn = LifecycleCongressConn()
    state = GameState(conn)
    conn.drop = True
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.open_world_congress())
    assert "WC_VOTING_ALREADY_OPEN" in asyncio.run(state.open_world_congress())
    assert conn.opens == ["open"]
    assert conn.operations == conn.actions == []


def test_unknown_or_empty_native_response_does_not_claim_open():
    state = GameState(OpenConn([[]]))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.open_world_congress())


def test_save_or_load_uncertainty_prevents_opening():
    for attribute in ("_pending_save", "_pending_load"):
        conn = OpenConn([])
        state = GameState(conn)
        setattr(state, attribute, True)
        assert "OUTCOME_UNKNOWN" in asyncio.run(state.open_world_congress())
        assert not conn.calls


def test_unknown_unit_action_must_confirm_before_opening_congress():
    conn = OpenConn([["OK:MOVING_TO|12,20|from:10,20"], ConnectionError("move unknown"), ConnectionError("still unknown")])
    state = GameState(conn)

    async def no_popup():
        return ""

    state.dismiss_popup = no_popup
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(1, 12, 20))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.open_world_congress())
    assert all("LuaEvents.WorldCongressIntro_ShowWorldCongress()" not in code for code in conn.calls)
