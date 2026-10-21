"""通过公开 MCP 和实际 Lua 验证文化伟人创作，不伪造运行层回执。"""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import app_of, make_ctx
from test_unit_effect_confirmation import EffectConn


class CulturalConn(EffectConn):
    def __init__(self, kind='WRITER', final=False):
        super().__init__()
        self.lua.globals().culture_kind = kind
        self.lua.globals().last_work = final
        self.lua.execute('''
u.kind=4;u.class=6;u.charges=0
local classType='GREAT_PERSON_CLASS_'..culture_kind
GameInfo.Units[4]={UnitType='UNIT_GREAT_'..culture_kind}
GameInfo.GreatPersonClasses={[6]={GreatPersonClassType=classType}}
local work={Index=38,GreatWorkType='GREATWORK_TEST_1',GreatPersonIndividualType='INDIVIDUAL_TEST'}
GameInfo.GreatPersonIndividuals={[42]={GreatPersonIndividualType='INDIVIDUAL_TEST',
 Name='LOC_CREATOR',GreatPersonClassType=classType,GreatWorkCollection={work}}}
GameInfo.GreatWorks={[38]=work}
GameInfo.Buildings=setmetatable({[14]={Index=14}},
 {__call=function(t) local key;return function() key=next(t,key);if key then return t[key] end end end})
Events.GreatWorkCreated=event()
city.owner=0;slots={[0]=-1,[1]=-1};workData={};created=0
local buildings={HasBuilding=function(_,id) return id==14 end,
 GetNumGreatWorkSlots=function() return 2 end,
 GetGreatWorkInSlot=function(_,building,slot) return slots[slot] end,
 GetGreatWorkTypeFromIndex=function(_,index) return workData[index].GreatWorkType end}
city.GetBuildings=function() return buildings end
Players[0].GetCities=function() return {Members=function() return pairs({city}) end,
 FindID=function(_,id) if city.id==id then return city end end} end
Game.GetGreatWorkDataFromIndex=function(index) return workData[index] end
function applyCulturalEffect()
 created=created+1
 slots[0]=98
 workData[98]={GreatWorkType=38,CreatorName='LOC_CREATOR',TurnCreated=turn}
 Events.UnitGreatPersonActivated.Fire(0,196608,6,42)
 Events.GreatWorkCreated.Fire(0,196608,20,17,14,98)
 u.moves=0
 if last_work then removeUnit() end
end
function request(unit,op,p)
 table.insert(submissions,{id=unit.id,op=op,params=p})
 if not deferred then applyCulturalEffect() end
 if fail_request then error('native creation entered then failed') end
end
UnitManager.RequestCommand=request
''')


@pytest.fixture(autouse=True)
def confirmation_budget(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .1)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def public_state(conn):
    state = GameState(conn)
    ctx = make_ctx()
    app_of(ctx).game = state
    return state, ctx


@pytest.mark.parametrize('kind', ['WRITER', 'ARTIST', 'MUSICIAN'])
@pytest.mark.parametrize('final', [False, True])
def test_zero_action_charge_cultural_person_creates_actual_work(kind, final):
    conn = CulturalConn(kind, final)
    state, ctx = public_state(conn)
    result = asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    assert result.startswith('GREAT_WORK_CREATED|unit:196608|individual:42|')
    assert 'work:GREATWORK_TEST_1 index:98|city:65539 building:14 slot:0' in result
    assert 'yields not measured' in result
    assert 'charges_remaining' not in result
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('slots[0]') == 98
    assert state.pending_action_summary is None
    assert conn.lua.eval('Events.GreatWorkCreated.Count()') == 0


@pytest.mark.parametrize('change', [
    "u.allowed=false", "Events.GreatWorkCreated=nil", "Game.GetGreatWorkDataFromIndex=nil",
    "GameInfo.GreatPersonIndividuals[42].GreatWorkCollection=nil",
    "GameInfo.GreatPersonIndividuals[42].GreatPersonClassType='OTHER_CLASS'",
    "city.GetBuildings=function() error('slots unreadable') end",
])
def test_unavailable_eligibility_or_evidence_is_zero_submit(change):
    conn = CulturalConn()
    conn.lua.execute(change)
    state, ctx = public_state(conn)
    assert asyncio.run(server.unit_action(ctx, 196608, 'activate')).startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('change', [
    'slots[0]=98',
    'Events.UnitGreatPersonActivated.Fire=function() end',
    'Events.GreatWorkCreated.Fire=function() end',
    "Game.GetGreatWorkDataFromIndex=function() return {GreatWorkType=38,CreatorName='OTHER',TurnCreated=turn} end",
    "Game.GetGreatWorkDataFromIndex=function() return {GreatWorkType=38,CreatorName='LOC_CREATOR',TurnCreated=turn-1} end",
    "local prior=applyCulturalEffect;applyCulturalEffect=function() prior();slots[0]=-1 end",
    "local prior=applyCulturalEffect;applyCulturalEffect=function() prior();city.owner=1 end",
    "local prior=applyCulturalEffect;applyCulturalEffect=function() prior();u.individual=43 end",
    "local prior=applyCulturalEffect;applyCulturalEffect=function() prior();UI.IsGameCoreBusy=function() return true end end",
    "local prior=applyCulturalEffect;applyCulturalEffect=function() prior();workData[98].GreatWorkType=39 end",
])
def test_missing_or_conflicting_creation_proof_keeps_original_pending(change):
    conn = CulturalConn()
    conn.lua.execute(change)
    state, ctx = public_state(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is not None


@pytest.mark.parametrize('event', [
    'Events.GreatWorkCreated.Fire(1,196608,20,17,14,98)',
    'Events.GreatWorkCreated.Fire(0,262144,20,17,14,98)',
    'Events.GreatWorkCreated.Fire(0,196608,21,17,14,98)',
    'Events.UnitGreatPersonActivated.Fire(0,196608,6,43)',
])
def test_other_request_event_cannot_confirm_creation(event):
    conn = CulturalConn()
    conn.lua.execute('deferred=true')
    state, ctx = public_state(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    conn.lua.execute('slots[0]=98;workData[98]={GreatWorkType=38,CreatorName="LOC_CREATOR",TurnCreated=turn}')
    if event.startswith('Events.GreatWorkCreated'):
        conn.lua.execute('Events.UnitGreatPersonActivated.Fire(0,196608,6,42)')
    else:
        conn.lua.execute('Events.GreatWorkCreated.Fire(0,196608,20,17,14,98)')
    conn.lua.execute(event)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('failure', ['lost_reply', 'lost_confirmation'])
def test_lost_responses_confirm_only_without_creating_another_work(failure):
    conn = CulturalConn(final=True)
    state, ctx = public_state(conn)
    if failure == 'lost_reply':
        conn.lose_reply = True
    else:
        original = conn.execute_write

        async def lose_confirmation(code):
            rows = await original(code)
            if any(row.startswith('UNIT_EFFECT_CONFIRMED|') for row in rows):
                conn.execute_write = original
                raise OSError('completed creation confirmation lost')
            return rows

        conn.execute_write = lose_confirmation
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    assert asyncio.run(server.unit_action(ctx, 196608, 'activate')).startswith('GREAT_WORK_CREATED|')
    assert conn.lua.eval('#submissions') == conn.lua.eval('created') == 1
    assert state.pending_action_summary is None


def test_native_throw_does_not_claim_zero_submission_or_replay_creation():
    conn = CulturalConn(final=True)
    conn.lua.execute('fail_request=true')
    state, ctx = public_state(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    assert conn.lua.eval('#submissions') == conn.lua.eval('created') == 1
    assert state.pending_action_summary is not None


def test_next_explicit_activation_confirms_new_work_and_final_unit_consumption():
    conn = CulturalConn()
    conn.lua.execute('''
local work={Index=39,GreatWorkType='GREATWORK_TEST_2',GreatPersonIndividualType='INDIVIDUAL_TEST'}
GameInfo.GreatWorks[39]=work
table.insert(GameInfo.GreatPersonIndividuals[42].GreatWorkCollection,work)
''')
    state, ctx = public_state(conn)
    assert 'work:GREATWORK_TEST_1' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    conn.lua.execute('''
turn=turn+1;u.moves=2
function applyCulturalEffect()
 created=created+1;slots[1]=99
 workData[99]={GreatWorkType=39,CreatorName='LOC_CREATOR',TurnCreated=turn}
 Events.UnitGreatPersonActivated.Fire(0,196608,6,42)
 Events.GreatWorkCreated.Fire(0,196608,20,17,14,99)
 removeUnit()
end
''')
    result = asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    assert 'work:GREATWORK_TEST_2 index:99' in result
    assert conn.lua.eval('#submissions') == conn.lua.eval('created') == 2
    assert conn.lua.eval('units[196608]') is None
    assert state.pending_action_summary is None


@pytest.mark.parametrize('change,restore', [('city.owner=1', 'city.owner=0'), ('city.id=65540', 'city.id=65539')])
def test_known_city_change_is_latched_before_activation_event(change, restore):
    conn = CulturalConn()
    conn.lua.execute('deferred=true')
    state, ctx = public_state(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    conn.lua.execute('''
slots[0]=98;workData[98]={GreatWorkType=38,CreatorName='LOC_CREATOR',TurnCreated=turn}
Events.GreatWorkCreated.Fire(0,196608,20,17,14,98)
''')
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    conn.lua.execute(restore + ';Events.UnitGreatPersonActivated.Fire(0,196608,6,42)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('known_change', [False, True])
def test_unknown_class_can_recover_but_cannot_mask_changed_individual(known_change):
    conn = CulturalConn()
    conn.lua.execute('deferred=true')
    state, ctx = public_state(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    if known_change:
        conn.lua.execute('u.individual=43')
    conn.lua.execute('u.class=nil')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    conn.lua.execute('u.individual=42;u.class=6;applyCulturalEffect()')
    result = asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    if known_change:
        assert 'OUTCOME_UNKNOWN' in result
    else:
        assert result.startswith('GREAT_WORK_CREATED|')
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('known_change', [False, True])
def test_unknown_city_id_can_recover_but_cannot_mask_changed_owner(known_change):
    conn = CulturalConn()
    conn.lua.execute('deferred=true')
    state, ctx = public_state(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    conn.lua.execute('applyCulturalEffect();city.id=nil')
    if known_change:
        conn.lua.execute('city.owner=1')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    conn.lua.execute('city.id=65539;city.owner=0')
    result = asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    if known_change:
        assert 'OUTCOME_UNKNOWN' in result
    else:
        assert result.startswith('GREAT_WORK_CREATED|')
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('change,restore', [('u.x=20','u.x=19'), ('u.individual=43','u.individual=42'), ('seed=12','seed=11')])
def test_observed_scope_or_actor_change_cannot_borrow_later_creation(change, restore):
    conn = CulturalConn()
    conn.lua.execute('deferred=true')
    original_seed = conn.lua.eval('seed')
    if change.startswith('seed='):
        restore = f'seed={original_seed}'
    state, ctx = public_state(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    conn.lua.execute(restore + ';applyCulturalEffect()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    assert conn.lua.eval('#submissions') == 1
