"""Delegation and embassy need directional native witnesses after submission."""

import asyncio

import pytest

from civ_mcp.game_state import GameState
from test_friendship_confirmation_lua import FriendshipConn, short_confirmation, checked_rows


class AccessConn(FriendshipConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
seed=123; civilization="CIVILIZATION_SUMERIA"
GameConfiguration={GetValue=function() return seed end}
PlayerConfigurations[0]={GetCivilizationTypeName=function() return civilization end}
submitted=false; access_reads=0; already=false; inbound=false
access_steps={false};gold=100;access_cost=25;charge_access=true
Players[0].GetTreasury=function() return {GetGoldBalance=function() return gold end} end
local original=Players[0]:GetDiplomacy()
local outgoing={IsDiplomaticActionValid=original.IsDiplomaticActionValid,
                HasMet=original.HasMet, IsAtWarWith=original.IsAtWarWith}
local function access(_, target)
    assert(target==1, "outgoing witness must use requested target")
    if not submitted then return already end
    access_reads=access_reads+1
    return access_steps[math.min(access_reads,#access_steps)]
end
outgoing.GetDiplomaticActionCost=function() return access_cost end
outgoing.HasDelegationAt=access; outgoing.HasEmbassyAt=access
Players[0].GetDiplomacy=function() return outgoing end
Players[1].GetDiplomacy=function() return {
    HasDelegationAt=function() return inbound end,
    HasEmbassyAt=function() return inbound end,
} end
local request=DiplomacyManager.RequestSession
DiplomacyManager.RequestSession=function(...)
    request(...); submitted=true;if charge_access then gold=gold-access_cost end
end
''')


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.mark.parametrize("action", ["DIPLOMATIC_DELEGATION", "RESIDENT_EMBASSY"])
def test_completed_session_without_native_access_is_unconfirmed(action, short_confirmation):
    conn = AccessConn()
    result = run(GameState(conn).send_diplomatic_action(1, action))
    assert result.startswith("ERR:OUTCOME_UNKNOWN"), result
    assert "get_diplomacy" in result and "confirm only" in result
    assert len(conn.lua.globals().friendship_requests) == 1
    assert conn.lua.globals().access_reads >= 1


@pytest.mark.parametrize("action, label", [("DIPLOMATIC_DELEGATION", "Delegation"), ("RESIDENT_EMBASSY", "Embassy")])
def test_existing_outgoing_access_is_reported_without_resubmission(action, label, short_confirmation):
    conn = AccessConn()
    conn.lua.execute('already=true; valid=false')
    result = run(GameState(conn).send_diplomatic_action(1, action))
    assert result == f"ACCEPTED|{label} already established with Sweden", result
    assert len(conn.lua.globals().friendship_requests) == 0
    assert list(conn.lua.globals().mutations.values()) == []


@pytest.mark.parametrize("action", ["DIPLOMATIC_DELEGATION", "RESIDENT_EMBASSY"])
@pytest.mark.parametrize("change", ['seed=999', 'civilization="CIVILIZATION_ROME"'])
def test_replaced_game_cannot_confirm_previous_submission(action, change, short_confirmation):
    conn = AccessConn()
    conn.lua.execute('access_steps={true}')
    original = conn.execute_write

    async def execute(source):
        if len(conn.lua.globals().friendship_requests):
            conn.lua.execute(change)
        return await original(source)

    conn.execute_write = execute
    result = run(GameState(conn).send_diplomatic_action(1, action))
    assert result.startswith("ERR:OUTCOME_UNKNOWN"), result
    assert len(conn.lua.globals().friendship_requests) == 1


@pytest.mark.parametrize("action, label", [("DIPLOMATIC_DELEGATION", "delegation"), ("RESIDENT_EMBASSY", "embassy")])
def test_later_outgoing_access_confirms_exactly_one_proposal(action, label, short_confirmation):
    conn = AccessConn()
    conn.lua.execute('access_steps={false,false,true}')
    result = run(GameState(conn).send_diplomatic_action(1, action.lower()))
    assert result == f"ACCEPTED|Sweden accepted your {label}"
    assert conn.lua.globals().access_reads == 3
    requests = list(conn.lua.globals().friendship_requests.values())
    assert [(r.owner, r.target, r.action) for r in requests] == [(0, 1, action)]
    assert list(conn.lua.globals().mutations.values()) == ['response','response','close']


@pytest.mark.parametrize("action", ["DIPLOMATIC_DELEGATION", "RESIDENT_EMBASSY"])
@pytest.mark.parametrize("setup", [
    'inbound=true; gold=75',
    'access_steps={true}; Game.GetLocalPlayer=function() return 2 end',
    'Players[0]:GetDiplomacy().HasDelegationAt=nil; Players[0]:GetDiplomacy().HasEmbassyAt=nil',
    'Players[0]:GetDiplomacy().HasDelegationAt=function() return 1 end; Players[0]:GetDiplomacy().HasEmbassyAt=function() return 1 end',
    'Players[0]:GetDiplomacy().HasDelegationAt=function() error("native read lost") end; Players[0]:GetDiplomacy().HasEmbassyAt=function() error("native read lost") end',
])
def test_incoming_gold_unknown_or_changed_owner_never_confirm_outgoing(action, setup, short_confirmation):
    conn = AccessConn()
    original = conn.execute_write

    async def execute(source):
        if len(conn.lua.globals().friendship_requests):
            conn.lua.execute(setup)
        return await original(source)

    conn.execute_write = execute
    assert run(GameState(conn).send_diplomatic_action(1, action)).startswith('ERR:OUTCOME_UNKNOWN')
    assert len(conn.lua.globals().friendship_requests) == 1


@pytest.mark.parametrize("action", ["DIPLOMATIC_DELEGATION", "RESIDENT_EMBASSY"])
@pytest.mark.parametrize("failure", [OSError('read lost'), TimeoutError('read timeout'), ValueError('bad reply')])
def test_read_failure_keeps_proposal_unknown_without_replay(action, failure):
    conn = AccessConn()
    original = conn.execute_write

    async def execute(source):
        if len(conn.lua.globals().friendship_requests):
            raise failure
        return await original(source)

    conn.execute_write = execute
    assert run(GameState(conn).send_diplomatic_action(1, action)).startswith('ERR:OUTCOME_UNKNOWN')
    assert len(conn.lua.globals().friendship_requests) == 1


@pytest.mark.parametrize("action", ["DIPLOMATIC_DELEGATION", "RESIDENT_EMBASSY"])
def test_hung_confirmation_has_a_deadline_and_does_not_replay(action, short_confirmation):
    conn = AccessConn()
    original = conn.execute_write

    async def execute(source):
        if len(conn.lua.globals().friendship_requests):
            await asyncio.Future()
        return await original(source)

    conn.execute_write = execute

    async def call():
        return await asyncio.wait_for(GameState(conn).send_diplomatic_action(1, action), 0.3)

    assert run(call()).startswith('ERR:OUTCOME_UNKNOWN')
    assert len(conn.lua.globals().friendship_requests) == 1


@pytest.mark.parametrize("action", ["DIPLOMATIC_DELEGATION", "RESIDENT_EMBASSY"])
@pytest.mark.parametrize("after_native", [False, True])
def test_unknown_submission_receipt_does_not_replay_or_claim_acceptance(action, after_native):
    conn = AccessConn()
    original = conn.execute_write

    async def execute(source):
        if not conn.calls:
            return await original(source)
        if after_native:
            await original(source)
        raise OSError('write result lost')

    conn.execute_write = execute
    assert run(GameState(conn).send_diplomatic_action(1, action)).startswith('ERR:OUTCOME_UNKNOWN')
    assert len(conn.lua.globals().friendship_requests) == int(after_native)
    assert conn.lua.globals().access_reads == 0


@pytest.mark.parametrize("action", ["DIPLOMATIC_DELEGATION", "RESIDENT_EMBASSY"])
@pytest.mark.parametrize("readback", [
    'WRONG|0|1|PRESENT', 'ACTION|0|2|PRESENT', 'ACTION|2|1|PRESENT',
    'ACTION|0|1|ABSENT', 'ACTION|0|1|PRESENT|extra',
])
def test_exact_action_and_player_pair_required_in_readback(action, readback, short_confirmation):
    conn = AccessConn()
    original = conn.execute_write

    async def execute(source):
        if len(conn.lua.globals().friendship_requests):
            return [readback.replace('ACTION', action)]
        return await original(source)

    conn.execute_write = execute
    assert run(GameState(conn).send_diplomatic_action(1, action)).startswith('ERR:OUTCOME_UNKNOWN')
    assert len(conn.lua.globals().friendship_requests) == 1


@pytest.mark.parametrize("action", ["DIPLOMATIC_DELEGATION", "RESIDENT_EMBASSY"])
def test_native_refusal_has_no_submission_or_confirmation(action):
    conn = AccessConn()
    conn.lua.execute('valid=false')
    assert run(GameState(conn).send_diplomatic_action(1, action)) == 'Error: RELATIONSHIP_REFUSED|INVALID|native refused'
    assert len(conn.lua.globals().friendship_requests) == 0
    assert len(conn.calls) == 2
    assert list(conn.lua.globals().mutations.values()) == []


@pytest.mark.parametrize("action, other_method", [("DIPLOMATIC_DELEGATION", "HasEmbassyAt"), ("RESIDENT_EMBASSY", "HasDelegationAt")])
def test_another_access_type_cannot_confirm_requested_action(action, other_method, short_confirmation):
    conn = AccessConn()
    conn.lua.execute(f'Players[0]:GetDiplomacy().{other_method}=function() return true end')
    result = run(GameState(conn).send_diplomatic_action(1, action))
    assert result.startswith('ERR:OUTCOME_UNKNOWN')
    assert len(conn.lua.globals().friendship_requests) == 1


@pytest.mark.parametrize("action", ["DIPLOMATIC_DELEGATION", "RESIDENT_EMBASSY"])
@pytest.mark.parametrize("setup", ['seed=nil', 'seed=math.huge', 'seed=0/0', 'civilization=nil', 'civilization=""'])
def test_unknown_game_identity_refuses_before_native_submission(action, setup):
    conn = AccessConn()
    conn.lua.execute(setup)
    assert run(GameState(conn).send_diplomatic_action(1, action)).startswith('Error: ACTION_SCOPE_UNAVAILABLE|')
    assert len(conn.lua.globals().friendship_requests) == 0
    assert list(conn.lua.globals().mutations.values()) == []


@pytest.mark.parametrize("action", ["DIPLOMATIC_DELEGATION", "RESIDENT_EMBASSY"])
@pytest.mark.parametrize("receipt", [
    None, [None], ["OK:ACCEPTED|Sweden"], ["OK:PROPOSED|Sweden"],
    ["OK:PROPOSED|Sweden", "ACTION_SCOPE|0|2|123|CIVILIZATION_SUMERIA"],
    ["OK:PROPOSED|Sweden", "ACTION_SCOPE|0|1|bad|CIVILIZATION_SUMERIA"],
    ["OK:PROPOSED|Sweden", "ACTION_SCOPE|0|1|123|unknown"],
    ["OK:PROPOSED|Sweden", "ACTION_SCOPE|0|1|123|CIVILIZATION_SUMERIA", "ACTION_SCOPE|0|1|123|CIVILIZATION_SUMERIA"],
])
def test_missing_or_wrong_scope_cannot_invent_acceptance(action, receipt):
    conn = AccessConn()
    conn.lua.execute('access_steps={true}')
    original = conn.execute_write

    async def execute(source):
        if not conn.calls:
            return await original(source)
        await original(source)
        return checked_rows([row.replace('ACTION', action) for row in receipt] if receipt and all(isinstance(row,str) for row in receipt) else receipt)

    conn.execute_write = execute
    assert run(GameState(conn).send_diplomatic_action(1, action)).startswith('ERR:OUTCOME_UNKNOWN')
    assert len(conn.lua.globals().friendship_requests) == 1
