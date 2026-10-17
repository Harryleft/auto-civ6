"""Validate explicit vote input before the fake connection can send any Lua."""

import asyncio
import json

import pytest

from civ_mcp.game_state import GameState
from civ_mcp.lua.congress import build_register_wc_voter


VOTE = {"hash": -513644209, "option": 2, "target": 7, "votes": 3}


class FakeConn:
    def __init__(self):
        self.calls = []

    async def execute_write(self, lua):
        self.calls.append(lua)
        return ["OK:WC_VOTER_REGISTERED"]


@pytest.mark.parametrize("votes", [None, {}, "[]", [None], [1]])
def test_empty_or_non_object_votes_never_reach_connection(votes):
    conn = FakeConn()
    with pytest.raises(ValueError):
        asyncio.run(GameState(conn).queue_wc_votes(votes))
    assert conn.calls == []


@pytest.mark.parametrize("field", ["hash", "option", "target", "votes"])
def test_each_field_is_required_before_send(field):
    conn = FakeConn()
    vote = {k: value for k, value in VOTE.items() if k != field}
    with pytest.raises(ValueError):
        asyncio.run(GameState(conn).queue_wc_votes([vote]))
    assert conn.calls == []


@pytest.mark.parametrize(("field", "value"), [
    ("hash", "-513644209"), ("hash", True), ("hash", 1.0),
    ("option", 0), ("option", 3), ("option", True), ("option", "1"),
    ("votes", 0), ("votes", -1), ("votes", 1.5), ("votes", True),
    ("target", None), ("target", []), ("target", True), ("target", -1),
    ("target", ""), ("target", "A\nB"),
])
def test_invalid_values_never_send(field, value):
    conn = FakeConn()
    with pytest.raises(ValueError):
        asyncio.run(GameState(conn).queue_wc_votes([VOTE | {field: value}]))
    assert conn.calls == []


def test_all_entries_are_validated_before_registering_handler():
    conn = FakeConn()
    with pytest.raises(ValueError):
        asyncio.run(GameState(conn).queue_wc_votes([VOTE, {"hash": 1}]))
    assert conn.calls == []


def test_duplicate_resolution_is_rejected_instead_of_silently_overwriting():
    conn = FakeConn()
    with pytest.raises(ValueError):
        asyncio.run(GameState(conn).queue_wc_votes([VOTE, VOTE | {"option": 1}]))
    assert conn.calls == []


def test_explicit_preferences_are_registered_without_defaults():
    conn = FakeConn()
    votes = [VOTE, {"hash": 13, "option": 1, "target": 2, "votes": 8}]
    result = asyncio.run(GameState(conn).queue_wc_votes(votes))
    assert result == "WC_VOTER_REGISTERED"
    assert len(conn.calls) == 1
    lua = conn.calls[0]
    assert '["-513644209"] = {o=2, t=7, v=3}' in lua
    assert '["13"] = {o=1, t=2, v=8}' in lua
    assert "budgetPerRes" not in lua
    assert "pref and pref.o or 1" not in lua
    assert "pref and pref.v or maxV" not in lua
    assert "math.min(item.pref.v, maxV)" in lua  # Requested count remains a maximum.


@pytest.mark.parametrize("target", [0, 2, "YIELD_SCIENCE", 'DISTRICT_"QUOTED"'])
def test_non_player_target_accepts_query_index_or_escaped_raw_value(target):
    lua = build_register_wc_voter([VOTE | {"target": target}])
    assert f"t={json.dumps(target, ensure_ascii=False)}" in lua
    assert 'elseif type(pref.t) == "number" then' in lua
    assert "pref.t < #targets then targetIdx = pref.t" in lua
    assert "tostring(tgt) == pref.t" in lua


def test_missing_preference_or_unmatched_target_stops_before_any_vote():
    lua = build_register_wc_voter([VOTE])
    # The entire resolution/target plan is validated before the first vote;
    # unknown targets stay nil and have no default target-index assignment.
    vote_start = lua.index("UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_RESOLUTION_VOTE")
    assert lua.index('if not pref then stop("Missing explicit vote') < vote_start
    assert lua.index('if targetIdx == nil then stop("Target not found') < vote_start
    assert "local targetIdx = nil" in lua
    assert "or 1" not in lua
    assert "resLeft" not in lua


def test_votes_for_a_paused_session_use_one_distinct_congress_stage_continuation():
    lua = build_register_wc_voter([VOTE])
    assert "current and current:IsInSession() then q.handler()" in lua
    assert lua.count("UI.RequestAction(ActionTypes.ACTION_ENDTURN)") == 1
    assert 'print("OK:WC_STAGE_CONTINUE_REQUESTED|stage:"' in lua


def test_explicit_vote_plan_submits_then_continues_its_native_stage_once():
    lua = build_register_wc_voter([VOTE])
    assert lua.count("UI.RequestAction(ActionTypes.ACTION_ENDTURN)") == 1
    assert lua.index("WORLD_CONGRESS_SUBMIT_TURN, {}") < lua.index("UI.RequestAction(ActionTypes.ACTION_ENDTURN)")
    assert lua.index('q.state = "VOTES_REQUESTED"') < lua.index("PlayerOperations.WORLD_CONGRESS_RESOLUTION_VOTE,")
    assert lua.index('q.state = "CONTINUE_REQUESTED"') < lua.index("UI.RequestAction(ActionTypes.ACTION_ENDTURN)")


def test_read_congress_distinguishes_requested_local_continuation_from_voting_requirement():
    from civ_mcp.narrate import narrate_world_congress

    class StatusConn(FakeConn):
        async def execute_write(self, lua):
            self.calls.append(lua)
            return ["WC_STATUS|true|0|5|5|0,10,30", "WC_PHASE|true|STAGE1|67|CONTINUE_REQUESTED|123"]

    status = asyncio.run(GameState(StatusConn()).get_world_congress())
    text = narrate_world_congress(status)
    assert "CONTINUE_REQUESTED" in text and "STAGE1" in text
    assert "vote required" not in text
    assert "not a receipt" in text


def test_lost_vote_reply_reports_unknown_and_repeat_only_requests_native_record_status():
    class LostReply(FakeConn):
        async def execute_write(self, lua):
            self.calls.append(lua)
            if len(self.calls) == 1:
                raise ConnectionError("lost after possible native submission")
            return ["OK:WC_REQUEST|CONTINUE_REQUESTED|stage:STAGE1|token:original"]

    conn = LostReply()
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.queue_wc_votes([VOTE]))
    assert "CONTINUE_REQUESTED" in asyncio.run(state.queue_wc_votes([VOTE]))
    for code in conn.calls:
        assert code.index("local prior = __civmcp_wc_request") < code.index("WORLD_CONGRESS_RESOLUTION_VOTE,")
        assert 'print("---END---"); return' in code


def test_native_popup_close_only_runs_for_visible_popup_and_does_not_accept_again():
    lua = build_register_wc_voter([VOTE])
    assert "popup and popup:IsVisible()" in lua
    assert "intro and intro:IsVisible() then UIManager:DequeuePopup(intro)" in lua
    assert "SetHide(" not in lua
    assert "LuaEvents.WorldCongressIntro_ShowWorldCongress()" not in lua


def test_empty_explicit_plan_requires_native_no_choices_and_has_one_common_submit_pair():
    conn = FakeConn()
    assert asyncio.run(GameState(conn).queue_wc_votes([])) == "WC_VOTER_REGISTERED"
    code = conn.calls[0]
    assert "votes={}" in code
    assert 'if nowStage == "STAGE1" then ress = wc:GetResolutions(me) end' in code
    assert 'stop("Explicit proposal votes are required; no defaults supplied")' in code
    assert "if #plan > 0 then" in code
    assert code.count("WORLD_CONGRESS_SUBMIT_TURN, {}") == 1
    assert code.count("UI.RequestAction(ActionTypes.ACTION_ENDTURN)") == 1


def test_congress_native_query_is_read_only_and_scope_and_no_replay_guards_precede_spending():
    from civ_mcp.lua.congress import build_congress_phase_query
    query = build_congress_phase_query()
    assert "RequestAction" not in query and "RequestPlayerOperation" not in query
    assert "SetHide(" not in query and ".Add(" not in query
    for field in ("phaseQ.owner", "phaseQ.civ", "phaseQ.seed", "phaseQ.turn", "phaseQ.stage"):
        assert field in query
    code = build_register_wc_voter([VOTE])
    first_vote = code.index("UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_RESOLUTION_VOTE")
    for guard in ("prior.owner == me", "prior.civ == civ", "prior.seed == seed", "prior.turn == turn",
                  "prior.stage == stage", 'q.state ~= "REGISTERED"', 'q.stage ~= nowStage'):
        assert code.index(guard) < first_vote
    assert "Events.WorldCongressStage1.Remove(q.handler)" in code
    assert "Events.WorldCongressStage2.Remove(q.handler)" in code
    assert "Events.WorldCongressStage1.Add(q.handler)" in code
    assert "Events.WorldCongressStage2.Add(q.handler)" in code
