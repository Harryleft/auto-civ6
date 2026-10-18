"""Policy plans execute real Lua through GameState; only native APIs are faked."""
import asyncio

import pytest
from lupa.lua51 import LuaRuntime

from civ_mcp.game_state import GameState
from civ_mcp.connection import LuaError


@pytest.fixture(autouse=True)
def short_policy_confirmation(monkeypatch):
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT", 0.02)
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL", 0.001)


class PolicyConn:
    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.unlocks = []
        self.submissions = []
        self.receipt_mode = None
        self.delayed_reads = 0
        self.reads = 0
        self.read_error = None
        native = self.lua.globals()
        native.print = lambda row: self.rows.append(str(row))
        native.record_unlock = lambda *args: self.unlocks.append(args)
        native.record_submission = lambda clears, adds: self.submissions.append((list(clears.values()), dict(adds)))
        self.lua.execute('''
Game = {GetLocalPlayer=function() return 0 end}
Locale = {Lookup=function(value) return value end}
PlayerOperations = {UNLOCK_POLICIES='unlock'}
UI = {RequestPlayerOperation=record_unlock}
GameInfo = {Governments={[0]={GovernmentType='GOVERNMENT_TEST',Name='Test'}}}
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
unlockCost, goldBalance, faithBalance = 140, 1000, 1000
GlobalParameters = {GOVERNMENT_UNLOCK_WITH_FAITH=0}
culture = {
    GetCostToUnlockPolicies=function() return unlockCost end,
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
        if 'print("GOV|' in code:
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
        self.lua.execute(code)
        rows = [row for row in self.rows if row != '---END---']
        if any(row.startswith('OK:POLICIES_SET') for row in rows):
            if self.receipt_mode == 'missing':
                rows = [row for row in rows if not row.startswith('POLICY_PLAN')]
            elif self.receipt_mode == 'partial':
                rows = [row for row in rows if not row.startswith('POLICY_PLAN|3|')]
            elif self.receipt_mode == 'malformed':
                rows.append('POLICY_PLAN|not-a-slot|POLICY_DISCIPLINE')
            elif self.receipt_mode == 'duplicate':
                rows.append('POLICY_PLAN|0|POLICY_DISCIPLINE')
            elif self.receipt_mode == 'bad_count':
                rows = [row for row in rows if not row.startswith('POLICY_PLAN_COUNT')]
                rows.append('POLICY_PLAN_COUNT|invalid')
            elif self.receipt_mode == 'wrong_request':
                rows = ['POLICY_PLAN|2|POLICY_URBAN_PLANNING' if row.startswith('POLICY_PLAN|2|') else row for row in rows]
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
    assert result.startswith('Error: ' + error), result
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
    assert result.startswith('POLICIES_UNCONFIRMED|')
    assert 'engine rejected' not in result and 'retry' not in result
    assert 'wanted POLICY_CIVIL_PRESTIGE, got POLICY_NATURAL_PHILOSOPHY' in result
    assert len(conn.unlocks) == len(conn.submissions) == 1


def test_readback_detects_unexpected_changes_to_an_untouched_slot():
    conn = PolicyConn()
    conn.lua.execute('removeUntouched=true')
    result = asyncio.run(GameState(conn).set_policies({2: 'POLICY_CIVIL_PRESTIGE'}))
    assert result.startswith('POLICIES_UNCONFIRMED|')
    assert 'engine rejected' not in result and 'retry' not in result
    assert 'slot 0 (wanted POLICY_DISCIPLINE, got EMPTY)' in result
    assert len(conn.unlocks) == len(conn.submissions) == 1


@pytest.mark.parametrize('mode', ['missing', 'partial', 'malformed', 'duplicate', 'bad_count', 'wrong_request'])
def test_missing_or_invalid_plan_receipt_cannot_claim_complete_verification(mode):
    conn = PolicyConn()
    conn.receipt_mode = mode
    result = asyncio.run(GameState(conn).set_policies({2: 'POLICY_CIVIL_PRESTIGE'}))
    assert 'WARN:POLICY_PLAN_UNVERIFIED' in result
    assert len(conn.unlocks) == len(conn.submissions) == 1


def test_readback_with_new_slots_reports_the_final_plan_unverified():
    conn = PolicyConn()
    conn.lua.execute('addUnexpectedSlot=true')
    result = asyncio.run(GameState(conn).set_policies({2: 'POLICY_CIVIL_PRESTIGE'}))
    assert 'POLICIES_UNCONFIRMED|' in result
    assert 'slot layout differs' in result
    assert len(conn.unlocks) == len(conn.submissions) == 1


def test_unaffordable_policy_change_has_no_native_submissions():
    conn = PolicyConn()
    conn.lua.execute('goldBalance=139')
    result = asyncio.run(GameState(conn).set_policies({2: 'POLICY_CIVIL_PRESTIGE'}))
    assert result.startswith('Error: POLICY_UNLOCK_UNAFFORDABLE'), result
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
    assert result.startswith('Error: POLICY_UNLOCK_UNKNOWN'), result
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
    assert result.startswith('POLICIES_UNCONFIRMED|')
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
    assert result.startswith('Error: SLOT_MISMATCH')
    assert conn.unlocks == conn.submissions == []
