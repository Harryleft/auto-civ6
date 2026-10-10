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


@pytest.mark.parametrize("votes", [None, [], {}, "[]", [None], [1]])
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


def test_votes_for_a_paused_session_do_not_require_another_end_turn():
    lua = build_register_wc_voter([VOTE])
    assert "if current and current:IsInSession() then handler()" in lua
    assert "ACTION_ENDTURN)" not in lua
    assert 'print("OK:WC_VOTES_SUBMITTED")' in lua
