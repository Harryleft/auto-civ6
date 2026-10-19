"""Confirm alliance state/type after the unchanged native deal submission sequence."""

import asyncio

import pytest

from civ_mcp.game_state import GameState
from test_friendship_confirmation_lua import FriendshipConn, short_confirmation, checked_rows


class AllianceConn(FriendshipConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
DiplomaticStates.ALLIED=7000
GameInfo.DiplomaticStates[1]=GameInfo.DiplomaticStates[87]
GameInfo.Civics={CIVIC_DIPLOMATIC_SERVICE={Index=67}}
local research={Index=23,AllianceType="ALLIANCE_RESEARCH"}
local military={Index=9,AllianceType="ALLIANCE_MILITARY"}
GameInfo.Alliances={ALLIANCE_RESEARCH=research, [23]=research,
                   ALLIANCE_MILITARY=military, [9]=military}
has_civic=true
Players[0].GetCulture=function() return {HasCivic=function() return has_civic end} end
submitted, pre_state, observations=false,1,0
alliance_steps={{[0]=1,[1]=1,alliance=23},{[0]=44,[1]=44,alliance=23}}
for owner=0,1 do
    local playerOwner=owner
    Players[owner].GetDiplomaticAI=function() return {
        GetDiplomaticStateIndex=function(_, target)
            assert(target==1-playerOwner)
            if not submitted then return pre_state end
            if playerOwner==0 then observations=observations+1 end
            return alliance_steps[math.max(1,math.min(observations,#alliance_steps))][playerOwner]
        end,
    } end
end
Players[0]:GetDiplomacy().GetAllianceType=function(_, target)
    assert(target==1)
    return alliance_steps[math.max(1,math.min(observations,#alliance_steps))].alliance
end
DealAgreementTypes={ALLIANCE=77}
DealProposalAction.PROPOSED=3
alliance_sends={}
DealManager.HasPendingDeal=function() return false end
DealManager.AreWorkingDealsEqual=function() return true end
local outgoing={AddItemOfType=function(_, itemType, owner)
    return {SetSubType=function(_, value) submitted_subtype=value end,
            SetValueType=function(_, value) submitted_alliance=value end}
end}
local incoming=deal
DealManager.GetWorkingDeal=function(direction)
    reads=reads+1
    return direction==DealDirection.OUTGOING and outgoing or incoming
end
DealManager.SendWorkingDeal=function(action, owner, target)
    table.insert(alliance_sends, {action=action,owner=owner,target=target})
    table.insert(mutations,"send:"..action)
    if action==DealProposalAction.PROPOSED then submitted=true end
end
''')


def run(awaitable):
    return asyncio.run(awaitable)


def test_delayed_research_alliance_is_confirmed_instead_of_same_frame_failed(short_confirmation):
    conn = AllianceConn()
    result = run(GameState(conn).form_alliance(1, "RESEARCH"))
    assert result == "ACCEPTED|RESEARCH alliance formed with Sweden", result
    assert conn.lua.globals().observations == 2
    assert len(conn.lua.globals().friendship_requests) == 1
    assert conn.lua.globals().friendship_requests[1].action == "MAKE_DEAL"
    assert [item.action for item in conn.lua.globals().alliance_sends.values()] == [3, 1]
    assert conn.lua.globals().submitted_alliance == 23
    assert conn.lua.globals().reads == 2


def assert_one_alliance_submission(conn, expected_sends=(3, 1)):
    requests = list(conn.lua.globals().friendship_requests.values())
    assert [(row.owner, row.target, row.action) for row in requests] == [(0, 1, "MAKE_DEAL")]
    sends = list(conn.lua.globals().alliance_sends.values())
    assert [(row.action, row.owner, row.target) for row in sends] == [(action, 0, 1) for action in expected_sends]
    assert conn.lua.globals().submitted_alliance == 23
    # No working-deal reads, response, or cleanup are repeated by confirmation.
    assert conn.lua.globals().reads == 2
    assert list(conn.lua.globals().mutations.values()) == [
        "clear", *[f"send:{action}" for action in expected_sends], "close",
    ]


def test_immediate_exact_alliance_uses_native_type_and_state_mapping():
    conn = AllianceConn()
    conn.lua.execute('pre_state=87; alliance_steps={{[0]=44,[1]=44,alliance=23}}')
    assert run(GameState(conn).form_alliance(1, "research")) == "ACCEPTED|RESEARCH alliance formed with Sweden"
    assert conn.lua.globals().observations == 1
    assert_one_alliance_submission(conn)


@pytest.mark.parametrize("setup", [
    'alliance_steps={{[0]=1,[1]=1,alliance=23}}',
    'alliance_steps={{[0]=44,[1]=1,alliance=23}}',
    'alliance_steps={{[0]=1,[1]=44,alliance=23}}',
    'alliance_steps={{[0]=44,[1]=44,alliance=9}}',
    'alliance_steps={{[0]=44,[1]=44}}',
    'alliance_steps={{[0]=44,[1]=44,alliance=-1}}',
    'alliance_steps={{[0]=44,[1]=44,alliance=99}}',
    'alliance_steps={{[0]=44,[1]=44,alliance=23.5}}; GameInfo.Alliances[23.5]=GameInfo.Alliances[23]',
    'alliance_steps={{[0]=44,[1]=44,alliance=math.huge}}',
    'alliance_steps={{[0]=44,[1]=44,alliance=23}}; GameInfo.Alliances[23].AllianceType=nil',
    'alliance_steps={{[0]=44,[1]=44,alliance=23}}; GameInfo.DiplomaticStates[44].Hash=9001',
    'alliance_steps={{[0]=44,[1]=44,alliance=23}}; GameInfo.DiplomaticStates[44].StateType="DIPLO_STATE_FRIENDLY"',
    'alliance_steps={{[0]=44,[1]=44,alliance=23}}; DiplomaticStates.ALLIED=nil',
    'Players[0]:GetDiplomacy().GetAllianceType=function() error("native type unavailable") end',
    'Game.GetLocalPlayer=function() return 2 end',
])
def test_nonallied_wrong_type_missing_or_inconsistent_native_witness_remains_unconfirmed(short_confirmation, setup):
    conn = AllianceConn()
    original = conn.execute_write

    async def execute(source):
        if len(conn.lua.globals().friendship_requests):
            conn.lua.execute(setup)
        return await original(source)

    conn.execute_write = execute
    result = run(GameState(conn).form_alliance(1, "RESEARCH"))
    assert result.startswith("ERR:OUTCOME_UNKNOWN"), result
    assert "get_diplomacy" in result and "confirm only" in result
    assert "ACCEPTED" not in result and "REJECTED" not in result and "FAILED" not in result
    assert_one_alliance_submission(conn)


@pytest.mark.parametrize("setup, error", [
    ('GameInfo.Alliances.ALLIANCE_RESEARCH=nil', "UNKNOWN_ALLIANCE_TYPE"),
    ('GameInfo.Alliances.ALLIANCE_RESEARCH.Index=nil', "UNKNOWN_ALLIANCE_TYPE"),
    ('GameInfo.Alliances.ALLIANCE_RESEARCH.Index=0/0', "UNKNOWN_ALLIANCE_TYPE"),
    ('GameInfo.Alliances.ALLIANCE_RESEARCH.Index=-1', "UNKNOWN_ALLIANCE_TYPE"),
    ('GameInfo.Alliances.ALLIANCE_RESEARCH.AllianceType="ALLIANCE_MILITARY"', "UNKNOWN_ALLIANCE_TYPE"),
    ('pre_state=44;alliance_steps={{[0]=44,[1]=44,alliance=9}}', "ALREADY_ALLIED"),
    ('pre_state=60', "NOT_FRIENDS"),
    ('pre_state=999', "RELATIONSHIP_UNKNOWN"),
    ('Players[1].IsAlive=function() return false end', "INVALID_PLAYER"),
    ('Players[0]:GetDiplomacy().HasMet=function() return false end', "NOT_MET"),
    ('Players[0]:GetDiplomacy().IsAtWarWith=function() return true end', "AT_WAR"),
    ('has_civic=false', "NO_CIVIC"),
])
def test_native_prechecks_reject_before_any_deal_mutation_or_confirmation(setup, error):
    conn = AllianceConn()
    conn.lua.execute(setup)
    result = run(GameState(conn).form_alliance(1, "RESEARCH"))
    assert result.startswith("Error: RELATIONSHIP_REFUSED|") and error in result, result
    assert len(conn.calls) == 2
    assert len(conn.lua.globals().friendship_requests) == len(conn.lua.globals().alliance_sends) == 0
    assert conn.lua.globals().reads == conn.lua.globals().observations == 0
    assert list(conn.lua.globals().mutations.values()) == []


def test_unsupported_requested_type_never_falls_back_to_another_native_alliance():
    conn = AllianceConn()
    result = run(GameState(conn).form_alliance(1, "NOT_A_TYPE"))
    assert result.startswith("Error: UNSUPPORTED_DIPLOMATIC_ACTION|"), result
    assert list(conn.lua.globals().mutations.values()) == []
    assert len(conn.lua.globals().alliance_sends) == 0


@pytest.mark.parametrize("empty", [True, False])
def test_missing_or_empty_incoming_deal_is_not_a_rejection_and_submission_is_not_replayed(short_confirmation, empty):
    conn = AllianceConn()
    conn.lua.execute('''
alliance_steps={{[0]=1,[1]=1,alliance=23}}
local original=DealManager.GetWorkingDeal
DealManager.GetWorkingDeal=function(direction, ...)
    if direction==DealDirection.INCOMING then
        reads=reads+1
        return INCOMING_REPLACEMENT
    end
    return original(direction, ...)
end
'''.replace("INCOMING_REPLACEMENT", '{GetItemCount=function() return 0 end}' if empty else 'nil'))
    result = run(GameState(conn).form_alliance(1, "RESEARCH"))
    assert result.startswith("ERR:OUTCOME_UNKNOWN")
    assert_one_alliance_submission(conn, expected_sends=(3,))


@pytest.mark.parametrize("error", [OSError("read lost"), TimeoutError("read timed out")])
def test_alliance_read_error_preserves_uncertainty_without_resubmission(error):
    conn = AllianceConn()
    original = conn.execute_write

    async def execute(source):
        if len(conn.lua.globals().friendship_requests):
            raise error
        return await original(source)

    conn.execute_write = execute
    assert run(GameState(conn).form_alliance(1, "RESEARCH")).startswith("ERR:OUTCOME_UNKNOWN")
    assert_one_alliance_submission(conn)


def test_alliance_confirmation_deadline_includes_a_hung_read(short_confirmation):
    conn = AllianceConn()
    original = conn.execute_write

    async def execute(source):
        if len(conn.lua.globals().friendship_requests):
            await asyncio.Future()
        return await original(source)

    conn.execute_write = execute

    async def call():
        return await asyncio.wait_for(GameState(conn).form_alliance(1, "RESEARCH"), 0.3)

    assert run(call()).startswith("ERR:OUTCOME_UNKNOWN")
    assert_one_alliance_submission(conn)


@pytest.mark.parametrize("after_native", [True, False])
def test_alliance_write_exception_never_claims_failure_or_resends(after_native):
    conn = AllianceConn()
    original = conn.execute_write

    async def execute(source):
        if not conn.calls:
            return await original(source)
        if after_native:
            await original(source)
        raise OSError("submission receipt lost")

    conn.execute_write = execute
    assert run(GameState(conn).form_alliance(1, "RESEARCH")).startswith("ERR:OUTCOME_UNKNOWN")
    assert len(conn.lua.globals().friendship_requests) == int(after_native)
    assert len(conn.lua.globals().alliance_sends) == 2 * int(after_native)
    assert conn.lua.globals().observations == 0


@pytest.mark.parametrize("rows", [
    ["ALLIANCE|0|1|ALLIANCE_MILITARY"],
    ["ALLIANCE|2|1|ALLIANCE_RESEARCH"],
    ["ALLIANCE|0|2|ALLIANCE_RESEARCH"],
    ["ALLIANCE|0|1|ALLIANCE_RESEARCH", "ALLIANCE|0|1|TYPE_MISMATCH"],
])
def test_readback_requires_the_exact_requested_type_pair_and_unique_witness(short_confirmation, rows):
    conn = AllianceConn()
    original = conn.execute_write

    async def execute(source):
        if len(conn.lua.globals().friendship_requests):
            return checked_rows(rows)
        return await original(source)

    conn.execute_write = execute
    assert run(GameState(conn).form_alliance(1, "RESEARCH")).startswith("ERR:OUTCOME_UNKNOWN")
    assert_one_alliance_submission(conn)


def test_mismatched_requested_type_in_submission_receipt_cannot_authorize_a_different_alliance():
    conn = AllianceConn()
    original = conn.execute_write

    async def execute(source):
        if not conn.calls:
            return await original(source)
        await original(source)
        return ["OK:PROPOSED|Sweden", "ALLIANCE_SCOPE|0|1|ALLIANCE_MILITARY"]

    conn.execute_write = execute
    assert run(GameState(conn).form_alliance(1, "RESEARCH")).startswith("ERR:OUTCOME_UNKNOWN")
    assert_one_alliance_submission(conn)


def test_another_requested_alliance_uses_its_native_row_instead_of_a_fixed_type_order():
    conn = AllianceConn()
    conn.lua.execute('alliance_steps={{[0]=44,[1]=44,alliance=9}}')
    assert run(GameState(conn).form_alliance(1, "MILITARY")) == "ACCEPTED|MILITARY alliance formed with Sweden"
    assert conn.lua.globals().submitted_alliance == 9
    assert len(conn.lua.globals().friendship_requests) == 1
    assert [item.action for item in conn.lua.globals().alliance_sends.values()] == [3, 1]
