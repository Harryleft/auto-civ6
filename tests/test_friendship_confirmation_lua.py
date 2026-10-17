"""Execute friendship submission and native-state reads through real GameState/Lua."""

import asyncio

import pytest

from civ_mcp.game_state import GameState
from civ_mcp.connection import LuaError
from test_diplomacy_context_lua import DiplomacyConn


class FriendshipConn(DiplomacyConn):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.lua.execute('''
sessions, infos, friendship_requests, observations = {}, {}, {}, 0
valid=true
local diplo=Players[0]:GetDiplomacy()
diplo.IsDiplomaticActionValid=function() return valid, {FailureReasons={"native refused"}} end
DiplomacyManager.RequestSession=function(me, target, action)
    table.insert(friendship_requests, {owner=me, target=target, action=action})
    sessions[target]=80
end
DiplomaticStates={DECLARED_FRIEND=9001}
GameInfo={DiplomaticStates={
    [60]={Hash=7004, StateType="DIPLO_STATE_UNFRIENDLY"},
    [22]={Hash=7002, StateType="DIPLO_STATE_FRIENDLY"},
    [87]={Hash=9001, StateType="DIPLO_STATE_DECLARED_FRIEND"},
    [44]={Hash=7000, StateType="DIPLO_STATE_ALLIED"},
}}
-- Arbitrary indices deliberately differ from the native state hashes/enums.
readbacks={{[0]=60,[1]=60}}
for owner=0,1 do
    local playerOwner=owner
    Players[owner].GetDiplomaticAI=function() return {
        GetDiplomaticStateIndex=function(_, target)
            assert(target==1-playerOwner, "query must read the reciprocal player pair")
            if playerOwner==0 then observations=observations+1 end
            local row=readbacks[math.min(observations,#readbacks)]
            return row[playerOwner]
        end,
    } end
end
''')

    async def execute_write(self, source):
        self.calls.append(source)
        return await super().execute_write(source)


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture
def short_confirmation(monkeypatch):
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT", 0.015)
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL", 0.001)


def test_valid_but_persistently_unfriendly_is_unconfirmed_and_never_submitted_twice(short_confirmation):
    conn = FriendshipConn()
    result = run(GameState(conn).send_diplomatic_action(1, "DECLARE_FRIENDSHIP"))
    assert result.startswith("PROPOSED_UNCONFIRMED|"), result
    assert "get_diplomacy" in result and "do not automatically resend" in result
    assert "ACCEPTED" not in result and "REJECTED" not in result
    assert len(conn.lua.globals().friendship_requests) == 1
    assert conn.lua.globals().friendship_requests[1].action == "DECLARE_FRIEND"
    assert conn.lua.globals().observations >= 1


def assert_one_submission_and_readonly_confirmation(conn):
    assert len(conn.lua.globals().friendship_requests) == 1
    assert conn.lua.globals().friendship_requests[1].owner == 0
    assert conn.lua.globals().friendship_requests[1].target == 1
    # Existing request/response/close protocol is confined to the first call.
    assert list(conn.lua.globals().mutations.values()) == ["response", "response", "close", "show", "hide"]


def test_immediate_bilateral_declared_friend_uses_native_mapping_not_assumed_indices():
    conn = FriendshipConn()
    conn.lua.execute('readbacks={{[0]=87,[1]=87}}')
    result = run(GameState(conn).send_diplomatic_action(1, "DECLARE_FRIENDSHIP"))
    assert result == "ACCEPTED|Friendship established with Sweden"
    assert conn.lua.globals().observations == 1
    assert_one_submission_and_readonly_confirmation(conn)


def test_delayed_friendship_waits_through_friendly_and_one_sided_states_without_resubmission(short_confirmation):
    conn = FriendshipConn()
    conn.lua.execute('readbacks={{[0]=22,[1]=22},{[0]=87,[1]=60},{[0]=87,[1]=87}}')
    result = run(GameState(conn).send_diplomatic_action(1, "declare_friendship"))
    assert result == "ACCEPTED|Friendship established with Sweden"
    assert conn.lua.globals().observations == 3
    assert_one_submission_and_readonly_confirmation(conn)


@pytest.mark.parametrize("setup", [
    'readbacks={{[0]=22,[1]=22}}',
    'readbacks={{[0]=44,[1]=44}}',
    'readbacks={{[0]=87,[1]=60}}',
    'readbacks={{[0]=60,[1]=87}}',
    'readbacks={{[0]=87}}',
    'readbacks={{[0]=1,[1]=1}}',
    'readbacks={{[0]=87,[1]=87}}; GameInfo.DiplomaticStates[87].Hash=7004',
    'readbacks={{[0]=87,[1]=87}}; GameInfo.DiplomaticStates[87].StateType="DIPLO_STATE_FRIENDLY"',
    'readbacks={{[0]=87,[1]=87}}; GameInfo.DiplomaticStates[87].Hash=nil',
    'readbacks={{[0]=87,[1]=87}}; DiplomaticStates.DECLARED_FRIEND=nil',
    'readbacks={{[0]=87,[1]=87}}; Players[0].GetDiplomaticAI=function() error("native read failed") end',
    'readbacks={{[0]=87,[1]=87}}; Players[1].GetDiplomaticAI=nil',
    'Game.GetLocalPlayer=function() return 2 end',
])
def test_nonfriend_inconsistent_missing_or_unknown_native_state_cannot_report_acceptance(short_confirmation, setup):
    conn = FriendshipConn()
    # Change native state only after the original request has been submitted.
    original = conn.execute_write

    async def execute(source):
        if conn.calls:
            conn.lua.execute(setup)
        return await original(source)

    conn.execute_write = execute
    result = run(GameState(conn).send_diplomatic_action(1, "DECLARE_FRIENDSHIP"))
    assert result.startswith("PROPOSED_UNCONFIRMED|")
    assert "ACCEPTED" not in result and "REJECTED" not in result
    assert_one_submission_and_readonly_confirmation(conn)


@pytest.mark.parametrize("reason", ["native refused", "OUTCOME_UNKNOWN_RULE"])
def test_native_prevalidation_rejection_stays_original_and_has_zero_requests_or_confirmation(reason):
    conn = FriendshipConn()
    conn.lua.globals().valid = False
    conn.lua.globals().native_refusal = reason
    conn.lua.execute('Players[0]:GetDiplomacy().IsDiplomaticActionValid=function() return false, {FailureReasons={native_refusal}} end')
    result = run(GameState(conn).send_diplomatic_action(1, "DECLARE_FRIENDSHIP"))
    assert result == f"Error: INVALID|{reason}"
    assert len(conn.lua.globals().friendship_requests) == 0
    assert conn.lua.globals().observations == 0
    assert len(conn.calls) == 1
    assert list(conn.lua.globals().mutations.values()) == []


@pytest.mark.parametrize("rows", [
    [], None, {}, ["DECLARED_FRIEND"], ["FRIENDSHIP|0|1|DECLARED_FRIEND_EXTRA"],
    ["FRIENDSHIP|2|1|DECLARED_FRIEND"], ["FRIENDSHIP|0|2|DECLARED_FRIEND"],
    ["FRIENDSHIP|0|1|DECLARED_FRIEND", "FRIENDSHIP|0|1|NOT_DECLARED_FRIEND"],
    ["FRIENDSHIP|0|1|DECLARED_FRIEND", "FRIENDSHIP|0|1|DECLARED_FRIEND"],
    ["FRIENDSHIP|0|1|DECLARED_FRIEND", ""],
])
def test_only_one_exact_scoped_readback_authorizes_acceptance(short_confirmation, rows):
    conn = FriendshipConn()
    original = conn.execute_write

    async def execute(source):
        if conn.calls:
            return rows
        return await original(source)

    conn.execute_write = execute
    result = run(GameState(conn).send_diplomatic_action(1, "DECLARE_FRIENDSHIP"))
    assert result.startswith("PROPOSED_UNCONFIRMED|")
    assert_one_submission_and_readonly_confirmation(conn)


@pytest.mark.parametrize("error", [OSError("read lost"), TimeoutError("read timeout"), ValueError("bad frame"), LuaError("native transport error")])
def test_transport_read_error_is_unconfirmed_without_another_submission(error):
    conn = FriendshipConn()
    original = conn.execute_write

    async def execute(source):
        if conn.calls:
            raise error
        return await original(source)

    conn.execute_write = execute
    result = run(GameState(conn).send_diplomatic_action(1, "DECLARE_FRIENDSHIP"))
    assert result.startswith("PROPOSED_UNCONFIRMED|")
    assert_one_submission_and_readonly_confirmation(conn)


def test_confirmation_deadline_includes_a_read_that_never_finishes(short_confirmation):
    conn = FriendshipConn()
    original = conn.execute_write

    async def execute(source):
        if conn.calls:
            await asyncio.Future()
        return await original(source)

    conn.execute_write = execute

    async def call():
        return await asyncio.wait_for(GameState(conn).send_diplomatic_action(1, "DECLARE_FRIENDSHIP"), 0.3)

    result = run(call())
    assert result.startswith("PROPOSED_UNCONFIRMED|")
    assert_one_submission_and_readonly_confirmation(conn)


@pytest.mark.parametrize("after_native", [False, True])
def test_write_exception_does_not_claim_acceptance_rejection_or_resend(after_native):
    conn = FriendshipConn()
    original = conn.execute_write

    async def execute(source):
        if after_native:
            await original(source)
        raise OSError("write result unknown")

    conn.execute_write = execute
    result = run(GameState(conn).send_diplomatic_action(1, "DECLARE_FRIENDSHIP"))
    assert result.startswith("PROPOSED_UNCONFIRMED|")
    assert "ACCEPTED" not in result and "REJECTED" not in result
    assert len(conn.lua.globals().friendship_requests) == int(after_native)
    assert conn.lua.globals().observations == 0


@pytest.mark.parametrize("action, result", [
    ("DENOUNCE", "SENT|Denounced Sweden"),
    ("DIPLOMATIC_DELEGATION", "ACCEPTED|Sweden accepted your delegation"),
    ("RESIDENT_EMBASSY", "ACCEPTED|Sweden accepted your embassy"),
])
def test_other_diplomatic_routes_keep_the_existing_protocol_and_result(action, result):
    conn = FriendshipConn()
    assert run(GameState(conn).send_diplomatic_action(1, action)) == result
    assert len(conn.lua.globals().friendship_requests) == 1
    assert conn.lua.globals().friendship_requests[1].action == action
    assert conn.lua.globals().observations == 0
    assert len(conn.calls) == 1


@pytest.mark.parametrize("setup", [
    'readbacks={{[0]=87,[1]=87}}; GameInfo.DiplomaticStates[87].Hash=math.huge; DiplomaticStates.DECLARED_FRIEND=math.huge',
    'readbacks={{[0]=1.5,[1]=1.5}}; GameInfo.DiplomaticStates[1.5]=GameInfo.DiplomaticStates[87]',
])
def test_malformed_numeric_mapping_is_not_a_bilateral_friend_witness(short_confirmation, setup):
    conn = FriendshipConn()
    conn.lua.execute(setup)
    result = run(GameState(conn).send_diplomatic_action(1, "DECLARE_FRIENDSHIP"))
    assert result.startswith("PROPOSED_UNCONFIRMED|")
    assert_one_submission_and_readonly_confirmation(conn)


@pytest.mark.parametrize("rows", [
    None, True, [None], [12], ["ERR:OUTCOME_UNKNOWN|write receipt lost"],
    {"OK:PROPOSED|Sweden": True, "FRIENDSHIP_SCOPE|0|1": True},
    ["OK:PROPOSED|Sweden"],
    ["OK:PROPOSED|Sweden", "FRIENDSHIP_SCOPE|0|2"],
    ["OK:PROPOSED|Sweden", "FRIENDSHIP_SCOPE|invalid|1"],
    ["OK:PROPOSED|Sweden", "FRIENDSHIP_SCOPE|0|1", "FRIENDSHIP_SCOPE|0|1"],
    ["OK:ACCEPTED|Sweden accepted", "FRIENDSHIP_SCOPE|0|1"],
])
def test_missing_or_malformed_submission_receipt_never_invents_confirmation(rows):
    conn = FriendshipConn()
    conn.lua.execute('readbacks={{[0]=87,[1]=87}}')
    original = conn.execute_write

    async def execute(source):
        await original(source)
        return rows

    conn.execute_write = execute
    result = run(GameState(conn).send_diplomatic_action(1, "DECLARE_FRIENDSHIP"))
    assert result.startswith("PROPOSED_UNCONFIRMED|")
    assert len(conn.lua.globals().friendship_requests) == 1
    assert conn.lua.globals().observations == 0
