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
    conn = OpenConn([["OK:WC_VOTING_OPEN|stage:STAGE1|turn:67"]])
    result = asyncio.run(GameState(conn).open_world_congress())
    assert result == "WC_VOTING_OPEN|stage:STAGE1|turn:67"
    assert len(conn.calls) == 1
    code = conn.calls[0]
    assert code.index("UIManager:DequeuePopup(intro)") < code.index("LuaEvents.WorldCongressIntro_ShowWorldCongress()")
    assert "RequestPlayerOperation" not in code
    assert "RequestAction" not in code
    assert "SetHide(" not in code
    assert code.count("LuaEvents.WorldCongressIntro_ShowWorldCongress()") == 1


def test_lost_open_reply_then_repeated_call_only_confirms_visible_voting():
    conn = OpenConn([ConnectionError("lost"), ["OK:WC_VOTING_ALREADY_OPEN|stage:STAGE1|turn:67"]])
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.open_world_congress())
    assert "WC_VOTING_ALREADY_OPEN" in asyncio.run(state.open_world_congress())
    for code in conn.calls:
        event = code.index("LuaEvents.WorldCongressIntro_ShowWorldCongress()")
        assert code.index("if sameOpen then") < event
        assert code.index('state="REQUESTED"') < event
        assert code.index("if popup:IsVisible() and not intro:IsVisible() then") < event
        for field in ("prior.owner == me", "prior.civ == civ", "prior.seed == seed", "prior.turn == turn", "prior.stage == stage"):
            assert field in code


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
