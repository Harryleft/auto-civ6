"""Policy plans execute real Lua through GameState; only native APIs are faked."""
import asyncio
import sys

import pytest
from lupa.lua51 import LuaRuntime, LuaError as NativeLuaError

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from civ_mcp.connection import LuaError


@pytest.fixture(autouse=True)
def short_policy_confirmation(monkeypatch):
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT", 0.1 if sys.platform == "win32" else 0.02)
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL", 0.001)


class PolicyConn:
    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.unlocks = []
        self.submissions = []
        self.receipt_mode = None
        self.lose_after = None
        self.cancel_after = None
        self.delayed_reads = 0
        self.reads = 0
        self.read_error = None
        native = self.lua.globals()
        native.print = lambda row: self.rows.append(str(row))
        native.record_unlock = lambda *args: self.unlocks.append(args)
        native.record_submission = lambda clears, adds: self.submissions.append((list(clears.values()), dict(adds)))
        self.lua.execute('''
turn,seed,busy=41,719665280,false
Game = {GetLocalPlayer=function() return 0 end,GetCurrentGameTurn=function() return turn end}
GameConfiguration={GetValue=function() return seed end}
PlayerConfigurations={[0]={GetCivilizationTypeName=function() return 'CIVILIZATION_SUMERIA' end}}
Locale = {Lookup=function(value) return value end}
PlayerOperations = {UNLOCK_POLICIES='unlock'}
function applyUnlock()
 if GlobalParameters.GOVERNMENT_UNLOCK_WITH_FAITH~=0 then faithBalance=faithBalance-unlockCost
 else goldBalance=goldBalance-unlockCost end
 unlockCost=0;policyMade=false
end
UI = {IsGameCoreBusy=function() return busy end,RequestPlayerOperation=function(...)
 record_unlock(...)
 if not ignoreUnlock then applyUnlock() end
 if throwUnlock then error('native unlock failed after entry') end
end}
GameInfo = {Governments={[0]={GovernmentType='GOVERNMENT_TEST',Name='Test'},
 [2]={GovernmentType='GOVERNMENT_OLIGARCHY',Name='Oligarchy'}}}
local entries = {
    {Index=1,Hash=101,PolicyType='POLICY_NATURAL_PHILOSOPHY',GovernmentSlotType='SLOT_ECONOMIC'},
    {Index=2,Hash=102,PolicyType='POLICY_CIVIL_PRESTIGE',GovernmentSlotType='SLOT_ECONOMIC'},
    {Index=3,Hash=103,PolicyType='POLICY_DISCIPLINE',GovernmentSlotType='SLOT_MILITARY'},
    {Index=4,Hash=104,PolicyType='POLICY_URBAN_PLANNING',GovernmentSlotType='SLOT_ECONOMIC'},
    {Index=5,Hash=105,PolicyType='POLICY_DIPLOMATIC',GovernmentSlotType='SLOT_DIPLOMATIC'},
    {Index=6,Hash=106,PolicyType='POLICY_WILDCARD',GovernmentSlotType='SLOT_WILDCARD'},
    {Index=7,Hash=107,PolicyType='POLICY_GREAT_PERSON',GovernmentSlotType='SLOT_GREAT_PERSON'},
}
GameInfo.Policies = setmetatable({}, {__call=function()
    local i=0
    return function() i=i+1;return entries[i] end
end})
for _, entry in ipairs(entries) do
    entry.Name=entry.PolicyType;entry.Description=entry.PolicyType
    GameInfo.Policies[entry.Index]=entry
    GameInfo.Policies[entry.Hash]=entry
    GameInfo.Policies[entry.PolicyType]=entry
end
-- Deliberately nonordinal indices: slot types come from GovernmentSlots.
GameInfo.GovernmentSlots = {
    [10]={GovernmentSlotType='SLOT_ECONOMIC',AllowsAnyPolicy=0},
    [20]={GovernmentSlotType='SLOT_WILDCARD',AllowsAnyPolicy=1},
    [30]={GovernmentSlotType='SLOT_MILITARY',AllowsAnyPolicy=0},
    [40]={GovernmentSlotType='SLOT_DIPLOMATIC',AllowsAnyPolicy=0},
    [50]={GovernmentSlotType='SLOT_GREAT_PERSON',AllowsAnyPolicy=0},
}
slots = {[0]=3,[1]=4,[2]=1,[3]=-1}
slotTypes = {[0]=30,[1]=10,[2]=10,[3]=20}
locked, obsolete, rejected = {}, {}, {}
numSlots = 4
unlockCost, goldBalance, faithBalance, policyMade = 140, 1000, 1000, false
GlobalParameters = {GOVERNMENT_UNLOCK_WITH_FAITH=0}
culture = {
    GetCostToUnlockPolicies=function() return unlockCost end,
    PolicyChangeMade=function() return policyMade end,
    GetCurrentGovernment=function() return 0 end,
    GetNumPolicySlots=function() return numSlots end,
    GetSlotPolicy=function(_, slot) return slots[slot] end,
    GetSlotType=function(_, slot) return slotTypes[slot] end,
    IsPolicyUnlocked=function(_, key) return not locked[GameInfo.Policies[key].Index] end,
    IsPolicyObsolete=function(_, hash) return obsolete[GameInfo.Policies[hash].Index] == true end,
    CanSlotPolicy=function(_, index, slot)
        for _, active in pairs(slots) do if active==index then return false end end
        local policy=GameInfo.Policies[index]
        local definition=GameInfo.GovernmentSlots[slotTypes[slot]]
        return not locked[index] and not obsolete[index] and not rejected[index]
            and definition ~= nil and policy ~= nil
            and (definition.AllowsAnyPolicy==1 or definition.GovernmentSlotType==policy.GovernmentSlotType)
    end,
    RequestPolicyChanges=function(_, clears, adds)
        record_submission(clears, adds)
        if ignoreSubmission then return end
        for _, slot in ipairs(clears) do slots[slot]=-1 end
        for slot, hash in pairs(adds) do
            for _, policy in ipairs(entries) do
                if policy.Hash==hash then slots[slot]=policy.Index end
            end
        end
        if removeUntouched then slots[0]=-1 end
        if addUnexpectedSlot then numSlots=5;slots[4]=-1;slotTypes[4]=20 end
        policyMade=true
        if throwPolicy then error("native policy request failed after entry") end
    end,
}
Players = {[0]={
    GetCulture=function() return culture end,
    GetTreasury=function() return {GetGoldBalance=function() return goldBalance end} end,
    GetReligion=function() return {GetFaithBalance=function() return faithBalance end} end,
}}
''')

    async def execute_write(self, code):
        self.rows.clear()
        before = (len(self.unlocks), len(self.submissions))
        if self.submissions:
            self.reads += 1
            if self.read_error is not None:
                raise self.read_error
            if self.delayed_reads and self.reads >= self.delayed_reads:
                # Native API boundary advances its pending application on a
                # later frame; no second MCP policy submission is involved.
                clears, adds = self.submissions[0]
                for slot in clears:
                    self.lua.globals().slots[slot] = -1
                for slot, value in adds.items():
                    self.lua.globals().slots[slot] = self.lua.globals().GameInfo.Policies[value].Index
                self.delayed_reads = 0
        try:
            self.lua.execute(code)
        except NativeLuaError as exc:
            raise LuaError(str(exc)) from exc
        stage = ('unlock' if len(self.unlocks)>before[0] else
                 'policy' if len(self.submissions)>before[1] else None)
        if stage is not None and self.cancel_after == stage:
            self.cancel_after = None
            raise asyncio.CancelledError()
        if stage is not None and self.lose_after == stage:
            self.lose_after = None
            raise OSError('native ' + stage + ' ran; response lost')
        rows = [row for row in self.rows if row != '---END---']
        confirmations = [row for row in rows if row.startswith('POLICY_CONFIRMED|')]
        if confirmations:
            receipt = confirmations[0]
            if self.receipt_mode == 'missing': rows.remove(receipt)
            elif self.receipt_mode == 'partial': rows[rows.index(receipt)] = receipt.rsplit('|',1)[0]
            elif self.receipt_mode == 'malformed': rows[rows.index(receipt)] += '|extra'
            elif self.receipt_mode == 'duplicate': rows.append(receipt)
            elif self.receipt_mode == 'wrong_token': rows[rows.index(receipt)] = 'POLICY_CONFIRMED|wrong|SET'
            elif self.receipt_mode == 'wrong_phase': rows[rows.index(receipt)] = receipt.rsplit('|',1)[0]+'|READY'
        return rows

    def current_slots(self):
        return {index: self.lua.globals().slots[index] for index in range(self.lua.globals().numSlots)}


def test_explicit_relocation_is_one_complete_submission_and_keeps_untouched_slots():
    conn = PolicyConn()
    result = asyncio.run(GameState(conn).set_policies({
        2: 'POLICY_CIVIL_PRESTIGE', 3: 'POLICY_NATURAL_PHILOSOPHY',
    }))
    assert not result.startswith('Error') and 'WARN' not in result
    assert conn.current_slots() == {0: 3, 1: 4, 2: 2, 3: 1}
    assert len(conn.unlocks) == 1
    assert conn.submissions == [([0, 1, 2, 3], {0: 103, 1: 104, 2: 102, 3: 101})]


@pytest.mark.parametrize(('initial', 'assignments', 'expected'), [
    ('slots[3]=2', {2: 'POLICY_CIVIL_PRESTIGE', 3: 'POLICY_NATURAL_PHILOSOPHY'}, {0: 3, 1: 4, 2: 2, 3: 1}),
    ('', {2: 'NONE', 3: 'POLICY_NATURAL_PHILOSOPHY'}, {0: 3, 1: 4, 2: -1, 3: 1}),
    ('', {2: 'POLICY_NATURAL_PHILOSOPHY'}, {0: 3, 1: 4, 2: 1, 3: -1}),
    ('', {3: 'NONE'}, {0: 3, 1: 4, 2: 1, 3: -1}),
    ('', {3: 'POLICY_GREAT_PERSON'}, {0: 3, 1: 4, 2: 1, 3: 7}),
    ('slotTypes[3]=50', {3: 'POLICY_GREAT_PERSON'}, {0: 3, 1: 4, 2: 1, 3: 7}),
])
def test_swaps_clears_and_retained_cards_produce_one_final_plan(initial, assignments, expected):
    conn = PolicyConn()
    conn.lua.execute(initial)
    result = asyncio.run(GameState(conn).set_policies(assignments))
    assert not result.startswith('Error') and 'WARN' not in result
    assert conn.current_slots() == expected
    changed = expected != {0: 3, 1: 4, 2: 1, 3: -1}
    assert len(conn.unlocks) == len(conn.submissions) == int(changed)
    if changed:
        assert conn.submissions[0][0] == [0, 1, 2, 3]


@pytest.mark.parametrize(('initial', 'assignments', 'error'), [
    ('', {2: 'POLICY_CIVIL_PRESTIGE', 3: 'POLICY_CIVIL_PRESTIGE'}, 'DUPLICATE_POLICY'),
    ('', {3: 'POLICY_NATURAL_PHILOSOPHY'}, 'DUPLICATE_POLICY'),
    ('', {3: 'POLICY_DOES_NOT_EXIST'}, 'POLICY_NOT_FOUND'),
    ('locked[2]=true', {2: 'POLICY_CIVIL_PRESTIGE'}, 'POLICY_LOCKED'),
    ('locked[1]=true', {2: 'NONE', 3: 'POLICY_NATURAL_PHILOSOPHY'}, 'POLICY_LOCKED'),
    ('obsolete[1]=true', {2: 'NONE', 3: 'POLICY_NATURAL_PHILOSOPHY'}, 'POLICY_OBSOLETE'),
    ('rejected[2]=true', {2: 'POLICY_CIVIL_PRESTIGE'}, 'CANNOT_SLOT'),
    ('', {2: 'POLICY_WILDCARD'}, 'SLOT_MISMATCH'),
    ('', {2: 'POLICY_GREAT_PERSON'}, 'SLOT_MISMATCH'),
    ('slotTypes[3]=50', {3: 'POLICY_WILDCARD'}, 'SLOT_MISMATCH'),
    ('', {0: 'POLICY_NATURAL_PHILOSOPHY', 2: 'NONE'}, 'SLOT_MISMATCH'),
    ('slotTypes[3]=99', {3: 'POLICY_CIVIL_PRESTIGE'}, 'UNKNOWN_SLOT_TYPE'),
    ('', {-1: 'NONE', 2: 'POLICY_CIVIL_PRESTIGE'}, 'INVALID_POLICY_SLOT'),
    ('', {4: 'POLICY_CIVIL_PRESTIGE'}, 'INVALID_POLICY_SLOT'),
    ('', {1.5: 'POLICY_CIVIL_PRESTIGE'}, 'INVALID_POLICY_SLOT'),
    ('numSlots=0', {0: 'POLICY_CIVIL_PRESTIGE'}, 'NO_GOVERNMENT'),
])
def test_invalid_final_plans_do_not_unlock_or_submit(initial, assignments, error):
    conn = PolicyConn()
    conn.lua.execute(initial)
    before = conn.current_slots()
    result = asyncio.run(GameState(conn).set_policies(assignments))
    assert result.startswith('Error: POLICY_PLAN_REFUSED|') and error in result, result
    assert conn.current_slots() == before
    assert conn.unlocks == conn.submissions == []


def test_current_slot_types_are_read_from_native_definitions():
    conn = PolicyConn()
    status = asyncio.run(GameState(conn).get_policies())
    assert [(slot.slot_index, slot.slot_type) for slot in status.slots] == [
        (0, 'SLOT_MILITARY'), (1, 'SLOT_ECONOMIC'),
        (2, 'SLOT_ECONOMIC'), (3, 'SLOT_WILDCARD'),
    ]
    assert conn.unlocks == conn.submissions == []


def test_native_non_application_stays_unconfirmed_after_one_submission():
    conn = PolicyConn()
    conn.lua.execute('ignoreSubmission=true')
    result = asyncio.run(GameState(conn).set_policies({2: 'POLICY_CIVIL_PRESTIGE'}))
    assert result.startswith('ERR:OUTCOME_UNKNOWN|')
    assert 'engine rejected' not in result and 'retry' not in result
    assert 'POLICY_SLOT_UNCONFIRMED:2' in result
    assert len(conn.unlocks) == len(conn.submissions) == 1


def test_readback_detects_unexpected_changes_to_an_untouched_slot():
    conn = PolicyConn()
    conn.lua.execute('removeUntouched=true')
    result = asyncio.run(GameState(conn).set_policies({2: 'POLICY_CIVIL_PRESTIGE'}))
    assert result.startswith('ERR:OUTCOME_UNKNOWN|')
    assert 'engine rejected' not in result and 'retry' not in result
    assert 'POLICY_SLOT_UNCONFIRMED:0' in result
    assert len(conn.unlocks) == len(conn.submissions) == 1


@pytest.mark.parametrize('mode', ['missing', 'partial', 'malformed', 'duplicate', 'wrong_token', 'wrong_phase'])
def test_missing_or_invalid_plan_receipt_cannot_claim_complete_verification(mode):
    conn = PolicyConn()
    conn.receipt_mode = mode
    result = asyncio.run(GameState(conn).set_policies({2: 'POLICY_CIVIL_PRESTIGE'}))
    assert 'OUTCOME_UNKNOWN' in result
    assert len(conn.unlocks) == len(conn.submissions) == 1


def test_readback_with_new_slots_reports_the_final_plan_unverified():
    conn = PolicyConn()
    conn.lua.execute('addUnexpectedSlot=true')
    result = asyncio.run(GameState(conn).set_policies({2: 'POLICY_CIVIL_PRESTIGE'}))
    assert 'OUTCOME_UNKNOWN' in result
    assert 'POLICY_LAYOUT_CHANGED' in result
    assert len(conn.unlocks) == len(conn.submissions) == 1


def test_unaffordable_policy_change_has_no_native_submissions():
    conn = PolicyConn()
    conn.lua.execute('goldBalance=139')
    result = asyncio.run(GameState(conn).set_policies({2: 'POLICY_CIVIL_PRESTIGE'}))
    assert 'POLICY_UNLOCK_UNAFFORDABLE' in result and result.startswith('Error:'), result
    assert conn.current_slots()[2] == 1
    assert conn.unlocks == conn.submissions == []


@pytest.mark.parametrize(('initial', 'assignments', 'unlocks', 'submissions'), [
    ('unlockCost=0;goldBalance=0', {2: 'POLICY_CIVIL_PRESTIGE'}, 0, 1),
    ('unlockCost=0;GlobalParameters=nil', {2: 'POLICY_CIVIL_PRESTIGE'}, 0, 1),
    ('goldBalance=0', {2: 'POLICY_NATURAL_PHILOSOPHY'}, 0, 0),
    ('unlockCost=nil', {3: 'NONE'}, 0, 0),
    ('goldBalance=140', {2: 'POLICY_CIVIL_PRESTIGE'}, 1, 1),
    ('GlobalParameters.GOVERNMENT_UNLOCK_WITH_FAITH=1;goldBalance=0;faithBalance=140',
     {2: 'POLICY_CIVIL_PRESTIGE'}, 1, 1),
])
def test_policy_payment_only_for_an_affordable_changed_plan(initial, assignments, unlocks, submissions):
    conn = PolicyConn()
    conn.lua.execute(initial)
    result = asyncio.run(GameState(conn).set_policies(assignments))
    assert not result.startswith('Error'), result
    assert len(conn.unlocks) == unlocks
    assert len(conn.submissions) == submissions


@pytest.mark.parametrize('initial', [
    'unlockCost=nil', 'unlockCost=-1', 'unlockCost=0/0', 'unlockCost=math.huge',
    'unlockCost="140"', 'culture.GetCostToUnlockPolicies=nil',
    'culture.GetCostToUnlockPolicies=function() error("read failed") end',
    'GlobalParameters=nil', 'GlobalParameters=false', 'GlobalParameters.GOVERNMENT_UNLOCK_WITH_FAITH=nil',
    'GlobalParameters.GOVERNMENT_UNLOCK_WITH_FAITH="0"',
    'goldBalance=nil', 'goldBalance=0/0', 'goldBalance=math.huge', 'goldBalance=-1',
    'Players[0].GetTreasury=function() error("unavailable") end',
    'GlobalParameters.GOVERNMENT_UNLOCK_WITH_FAITH=1;faithBalance=nil',
])
def test_unknown_policy_payment_is_rejected_without_submission(initial):
    conn = PolicyConn()
    conn.lua.execute(initial)
    result = asyncio.run(GameState(conn).set_policies({2: 'POLICY_CIVIL_PRESTIGE'}))
    assert 'POLICY_UNLOCK_UNKNOWN' in result and result.startswith('Error:'), result
    assert conn.unlocks == conn.submissions == []


def test_faith_policy_payment_checks_faith_not_gold():
    conn = PolicyConn()
    conn.lua.execute('GlobalParameters.GOVERNMENT_UNLOCK_WITH_FAITH=1;goldBalance=1000;faithBalance=139')
    result = asyncio.run(GameState(conn).set_policies({2: 'POLICY_CIVIL_PRESTIGE'}))
    assert 'POLICY_UNLOCK_UNAFFORDABLE|required=140|balance=139|currency=FAITH' in result
    assert conn.unlocks == conn.submissions == []


def test_policy_change_is_confirmed_on_a_later_native_frame_without_resubmission():
    conn = PolicyConn()
    conn.lua.execute('ignoreSubmission=true')
    conn.delayed_reads = 3
    result = asyncio.run(GameState(conn).set_policies({2: 'POLICY_CIVIL_PRESTIGE'}))
    assert result.startswith('POLICIES_SET') and 'UNCONFIRMED' not in result
    assert conn.reads == 3
    assert conn.current_slots()[2] == 2
    assert len(conn.unlocks) == len(conn.submissions) == 1


@pytest.mark.parametrize('error', [OSError('disconnected'), TimeoutError('read timeout'), ValueError('bad native read'), LuaError('native Lua read failed')])
def test_policy_read_error_keeps_unconfirmed_without_replay(error):
    conn = PolicyConn()
    conn.lua.execute('ignoreSubmission=true')
    conn.read_error = error
    result = asyncio.run(GameState(conn).set_policies({2: 'POLICY_CIVIL_PRESTIGE'}))
    assert result.startswith('ERR:OUTCOME_UNKNOWN|')
    assert 'engine rejected' not in result and 'retry' not in result
    assert len(conn.unlocks) == len(conn.submissions) == 1


@pytest.mark.parametrize(('initial', 'cost', 'currency', 'balance', 'text'), [
    ('goldBalance=139', 140, 'GOLD', 139, '140 GOLD'),
    ('unlockCost=0;goldBalance=0', 0, 'GOLD', 0, 'free'),
    ('GlobalParameters.GOVERNMENT_UNLOCK_WITH_FAITH=1;faithBalance=200', 140, 'FAITH', 200, '140 FAITH'),
    ('unlockCost=nil;GlobalParameters=nil', None, None, None, 'unknown'),
])
def test_policy_query_exposes_native_payment_facts_without_submitting(initial, cost, currency, balance, text):
    from civ_mcp.narrate import narrate_policies
    conn = PolicyConn()
    conn.lua.execute(initial)
    status = asyncio.run(GameState(conn).get_policies())
    assert status.policy_unlock_cost == cost
    assert status.policy_unlock_currency == currency
    assert status.policy_unlock_balance == balance
    assert text in narrate_policies(status)
    assert conn.unlocks == conn.submissions == []


def test_legacy_policy_response_keeps_payment_unknown():
    from civ_mcp.lua import parse_policies_response
    from civ_mcp.narrate import narrate_policies
    status = parse_policies_response(['GOV|GOVERNMENT_TEST|Test|0'])
    assert status.policy_unlock_cost is None
    assert status.policy_unlock_currency is None
    assert status.policy_unlock_balance is None
    assert 'Policy change cost: unknown' in narrate_policies(status)


def test_invalid_policy_plan_is_rejected_before_unknown_payment():
    conn = PolicyConn()
    conn.lua.execute('unlockCost=nil')
    result = asyncio.run(GameState(conn).set_policies({0: 'POLICY_CIVIL_PRESTIGE'}))
    assert 'SLOT_MISMATCH' in result and result.startswith('Error:')
    assert conn.unlocks == conn.submissions == []



def test_delayed_unlock_preserves_paid_plan_and_never_replays_payment():
    conn = PolicyConn(); state = GameState(conn)
    conn.lua.execute('ignoreUnlock=true')
    plan = {2: 'POLICY_CIVIL_PRESTIGE'}
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_policies(plan))
    assert len(conn.unlocks) == 1 and conn.submissions == []
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_policies(plan))
    assert len(conn.unlocks) == 1 and conn.submissions == []
    conn.lua.execute('applyUnlock()')
    assert asyncio.run(state.set_policies(plan)).startswith('POLICIES_SET|')
    assert len(conn.unlocks) == len(conn.submissions) == 1
    assert conn.lua.eval('goldBalance') == 860
    assert conn.current_slots() == {0:3,1:4,2:2,3:-1}
    assert state.pending_action_summary is None



@pytest.mark.parametrize('boundary', ['lost', 'cancelled'])
def test_paid_unlock_recovery_only_owner_continues_never_queries_or_other_gates(boundary):
    conn = PolicyConn(); state = GameState(conn); ctx = make_ctx(game=state)
    if boundary == 'lost': conn.lose_after = 'unlock'
    else: conn.cancel_after = 'unlock'
    plan = '2=POLICY_CIVIL_PRESTIGE,3=POLICY_NATURAL_PHILOSOPHY'
    if boundary == 'cancelled':
        with pytest.raises(asyncio.CancelledError): asyncio.run(server.set_policies(ctx,plan))
    else:
        assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_policies(ctx,plan))
    assert conn.lua.eval('goldBalance') == 860
    assert len(conn.unlocks) == 1 and conn.submissions == []
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action())
    status = asyncio.run(state.get_policies())
    assert status.policy_unlock_cost == 0
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_policies(ctx,'2=NONE'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_research(ctx,'TECH_POTTERY'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    assert len(conn.unlocks) == 1 and conn.submissions == []
    # Assignment order is not a new intent. Continue the saved complete plan.
    assert asyncio.run(server.set_policies(ctx,'3=POLICY_NATURAL_PHILOSOPHY,2=POLICY_CIVIL_PRESTIGE')).startswith('POLICIES_SET|')
    assert conn.current_slots() == {0:3,1:4,2:2,3:1}
    assert len(conn.unlocks) == len(conn.submissions) == 1
    assert conn.lua.eval('goldBalance') == 860
    assert state.pending_action_summary is None


@pytest.mark.parametrize('patch', [
    'slots[0]=-2', 'slotTypes[3]=99', 'GameInfo.Policies[2].Hash=nil',
    'culture.GetCurrentGovernment=function() return -1 end',
])
def test_incomplete_original_slot_or_policy_identity_refuses_before_payment(patch):
    conn = PolicyConn(); state = GameState(conn)
    conn.lua.execute(patch)
    result = asyncio.run(state.set_policies({2:'POLICY_CIVIL_PRESTIGE'}))
    assert result.startswith('Error: POLICY_PLAN_REFUSED|'), result
    assert conn.unlocks == conn.submissions == []
    assert state.pending_action_summary is None


@pytest.mark.parametrize('phase', ['unlock', 'policy'])
def test_native_throw_after_effect_keeps_original_stage_unknown_without_replay(phase):
    conn = PolicyConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('throwUnlock=true' if phase=='unlock' else 'throwPolicy=true')
    plan = '2=POLICY_CIVIL_PRESTIGE'
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_policies(ctx,plan))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_policies(ctx,plan))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    assert len(conn.unlocks) == 1
    assert len(conn.submissions) == (phase=='policy')
    assert conn.lua.eval('goldBalance') == 860
    assert state.pending_action_blocks_mutations


@pytest.mark.parametrize('boundary', ['lost', 'cancelled'])
@pytest.mark.parametrize('paid', [True,False])
def test_policy_submission_transport_loss_confirms_complete_plan_without_second_request(boundary,paid):
    conn = PolicyConn(); state = GameState(conn); ctx = make_ctx(game=state)
    if not paid: conn.lua.execute('unlockCost=0')
    if boundary == 'lost': conn.lose_after = 'policy'
    else: conn.cancel_after = 'policy'
    plan = '2=POLICY_CIVIL_PRESTIGE'
    if boundary == 'cancelled':
        with pytest.raises(asyncio.CancelledError): asyncio.run(server.set_policies(ctx,plan))
    else:
        assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_policies(ctx,plan))
    assert asyncio.run(server.set_policies(ctx,plan)).startswith('POLICIES_SET|')
    assert len(conn.unlocks) == int(paid)
    assert len(conn.submissions) == 1
    assert conn.lua.eval('goldBalance') == (860 if paid else 1000)
    assert state.pending_action_summary is None


@pytest.mark.parametrize('patch', [
    'unlockCost=0',
    'goldBalance=860',
    'unlockCost=0;faithBalance=860',
    'unlockCost=0;goldBalance=859',
    'unlockCost=0;goldBalance=860;policyMade=true',
    'unlockCost=0;goldBalance=860;GlobalParameters.GOVERNMENT_UNLOCK_WITH_FAITH=1',
])
def test_partial_payment_facts_cannot_unlock_the_policy_submission_stage(patch):
    conn = PolicyConn(); state = GameState(conn)
    conn.lua.execute('ignoreUnlock=true')
    plan = {2:'POLICY_CIVIL_PRESTIGE'}
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_policies(plan))
    conn.lua.execute(patch)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_policies(plan))
    assert len(conn.unlocks) == 1 and conn.submissions == []
    assert state.pending_action_blocks_mutations


def test_faith_payment_confirmation_uses_original_currency_and_amount():
    conn = PolicyConn(); state = GameState(conn)
    conn.lua.execute('GlobalParameters.GOVERNMENT_UNLOCK_WITH_FAITH=1;goldBalance=10;faithBalance=200')
    assert asyncio.run(state.set_policies({2:'POLICY_CIVIL_PRESTIGE'})).startswith('POLICIES_SET|')
    assert conn.lua.eval('faithBalance') == 60
    assert conn.lua.eval('goldBalance') == 10
    assert len(conn.unlocks) == len(conn.submissions) == 1


def test_paid_plan_keeps_its_complete_baseline_if_later_eligibility_is_unknown():
    conn = PolicyConn(); state = GameState(conn)
    conn.lose_after = 'unlock'
    plan = {2:'POLICY_CIVIL_PRESTIGE',3:'POLICY_NATURAL_PHILOSOPHY'}
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_policies(plan))
    conn.lua.execute('rejected[2]=true')
    result = asyncio.run(state.set_policies(plan))
    assert 'OUTCOME_UNKNOWN' in result and 'paid:true' in result and 'policy_entered:false' in result
    assert len(conn.unlocks) == 1 and conn.submissions == []
    conn.lua.execute('rejected[2]=false')
    assert asyncio.run(state.set_policies(plan)).startswith('POLICIES_SET|')
    assert conn.current_slots() == {0:3,1:4,2:2,3:1}
    assert len(conn.unlocks) == len(conn.submissions) == 1


@pytest.mark.parametrize('patch,restore', [
    ('seed=999','seed=719665280'),
    ('turn=42','turn=41'),
    ('culture.GetCurrentGovernment=function() return 2 end','culture.GetCurrentGovernment=function() return 0 end'),
    ('slotTypes[3]=30','slotTypes[3]=20'),
])
def test_paid_plan_world_turn_government_or_layout_change_never_submits_after_returning_to_old_facts(patch,restore):
    conn = PolicyConn(); state = GameState(conn)
    conn.lose_after = 'unlock'
    plan = {2:'POLICY_CIVIL_PRESTIGE'}
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_policies(plan))
    conn.lua.execute(patch)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_policies(plan))
    conn.lua.execute(restore)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_policies(plan))
    assert len(conn.unlocks) == 1 and conn.submissions == []


def test_partial_native_policy_application_never_rebuilds_or_replays_the_final_plan():
    conn = PolicyConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('removeUntouched=true')
    plan = '2=POLICY_CIVIL_PRESTIGE,3=POLICY_NATURAL_PHILOSOPHY'
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_policies(ctx,plan))
    assert conn.current_slots() == {0:-1,1:4,2:2,3:1}
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_policies(ctx,plan))
    conn.lua.execute('slots[0]=3')
    assert asyncio.run(server.set_policies(ctx,plan)).startswith('POLICIES_SET|')
    assert len(conn.unlocks) == len(conn.submissions) == 1
    assert conn.lua.eval('goldBalance') == 860


def test_unlock_native_false_without_payment_is_unknown_and_not_a_zero_submission_refusal():
    conn = PolicyConn(); state = GameState(conn)
    conn.lua.execute('UI.RequestPlayerOperation=function(...) record_unlock(...);return false end')
    plan = {2:'POLICY_CIVIL_PRESTIGE'}
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_policies(plan))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_policies(plan))
    assert len(conn.unlocks) == 1 and conn.submissions == []
    assert conn.lua.eval('goldBalance') == 1000
    assert state.pending_action_blocks_mutations


def test_preexisting_complete_plan_remains_noop_without_unlock_eligibility():
    conn = PolicyConn(); state = GameState(conn)
    conn.lua.execute('unlockCost=nil;policyMade=true;GlobalParameters=nil')
    result = asyncio.run(state.set_policies({0:'POLICY_DISCIPLINE',1:'POLICY_URBAN_PLANNING',2:'POLICY_NATURAL_PHILOSOPHY',3:'none'}))
    assert result.startswith('POLICIES_UNCHANGED|')
    assert conn.unlocks == conn.submissions == []
    assert state.pending_action_summary is None


def test_policy_query_uses_native_hash_for_unlock_status():
    conn = PolicyConn(); state = GameState(conn)
    conn.lua.execute('culture.IsPolicyUnlocked=function(_,hash) assert(hash>=101,"unlock status needs Hash");return true end')
    status = asyncio.run(state.get_policies())
    assert any(policy.policy_type=='POLICY_CIVIL_PRESTIGE' for policy in status.available_policies)
    assert conn.unlocks == conn.submissions == []


def test_paid_plan_can_wait_for_missing_policy_api_without_entering_or_repaying():
    conn = PolicyConn(); state = GameState(conn)
    conn.lose_after = 'unlock'
    plan = {2:'POLICY_CIVIL_PRESTIGE'}
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_policies(plan))
    conn.lua.execute('savedPolicyRequest=culture.RequestPolicyChanges;culture.RequestPolicyChanges=nil')
    result = asyncio.run(state.set_policies(plan))
    assert 'OUTCOME_UNKNOWN' in result
    assert len(conn.unlocks) == 1 and conn.submissions == []
    conn.lua.execute('culture.RequestPolicyChanges=savedPolicyRequest')
    assert asyncio.run(state.set_policies(plan)).startswith('POLICIES_SET|')
    assert len(conn.unlocks) == len(conn.submissions) == 1
