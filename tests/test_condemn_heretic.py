"""Condemn heretic through public MCP/GameState and the native Lua boundary."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from test_unit_effect_confirmation import EffectConn


class CondemnConn(EffectConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
turn=127;seed=-1896272000
units[196608]=nil;u.id=2424845;units[u.id]=u
u.kind=51;u.x=34;u.y=23;u.moves=1
GameInfo.Units[51]={UnitType='UNIT_MUSKETMAN',TrackReligion=false,ReligiousStrength=0}
GameInfo.Units[4]={UnitType='UNIT_APOSTLE',TrackReligion=true,ReligiousStrength=110}
GameInfo.UnitCommands.UNITCOMMAND_CONDEMN_HERETIC={CommandType='UNITCOMMAND_CONDEMN_HERETIC',Hash=-597630620,Index=15}
Events.UnitCommandStarted=event()
enemy={id=5767168,owner=1,kind=4,religion=8,x=34,y=23}
for _,name in ipairs({'GetID','GetOwner','GetType','GetReligionType','GetX','GetY'}) do enemy[name]=u[name] end
units[enemy.id]=enemy
Players[1]={GetUnits=function() return {FindID=function(_,id) local unit=units[id];if unit and unit.owner==1 then return unit end end} end}
UnitManager.GetUnit=function(owner,id)
 for _,unit in pairs(units) do if unit.owner==owner and unit.id%65536==id then return unit end end
end
visible=true
PlayersVisibility={[0]={IsVisible=function(_,x,y) assert(x==34 and y==23);return visible end}}
MapLayers={ANY=-1,RELIGIOUS=3}
Units={GetUnitsInPlotLayerID=function(x,y,layer)
 assert(x==34 and y==23 and layer==-1)
 local result={}
 for _,unit in pairs(units) do if unit.x==x and unit.y==y then table.insert(result,unit) end end
 return result
end}
UnitManager.CanStartCommand=function(actor,command,visibleOnly,results)
 assert(actor==u and command==-597630620 and visibleOnly==false and results==true)
 return u.allowed
end
function applyCondemn()
 Events.UnitCommandStarted.Fire(0,2424845,-597630620,0)
 units[5767168]=nil
 Events.UnitRemovedFromMap.Fire(1,5767168)
end
UnitManager.RequestCommand=function(actor,command,params)
 assert(actor==u and params==nil)
 table.insert(submissions,{id=actor.id,op=command})
 if not deferred then applyCondemn() end
 if fail_request then error('native command entered then failed') end
end
''')


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def test_t127_native_condemn_heretic_confirms_original_target_removal_without_claiming_rewards():
    conn = CondemnConn(); state = GameState(conn)
    result = asyncio.run(server.unit_action(make_ctx(game=state), 2424845, 'condemn_heretic'))
    assert result.startswith('HERETIC_CONDEMNED|unit:2424845|target:1:5767168|')
    assert 'religion:8' in result and 'at:34,23' in result
    assert 'rewards not measured' in result
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('submissions[1].op') == -597630620
    assert conn.lua.eval('units[5767168]') is None
    assert state.pending_action_summary is None
    assert conn.lua.eval('Events.UnitCommandStarted.Count()') == 0
    assert conn.lua.eval('Events.UnitRemovedFromMap.Count()') == 0


def test_target_change_during_native_eligibility_refuses_before_command_entry():
    conn = CondemnConn(); state = GameState(conn)
    conn.lua.execute('UnitManager.CanStartCommand=function() enemy.religion=9;return true end')
    result = asyncio.run(state.condemn_heretic(2424845))
    assert result.startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('change,restore', [
    ('enemy.religion=9', 'enemy.religion=8'),
    ('enemy.kind=51', 'enemy.kind=4'),
    ('enemy.x=35', 'enemy.x=34'),
    ('enemy.id=5832704', 'enemy.id=5767168'),
    ('u.kind=4', 'u.kind=51'),
    ('u.x=35', 'u.x=34'),
    ('u.id=2490381', 'u.id=2424845'),
])
def test_observed_identity_change_cannot_later_borrow_target_removal_to_confirm(change, restore):
    conn = CondemnConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.condemn_heretic(2424845))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.condemn_heretic(2424845))
    conn.lua.execute(restore + ';applyCondemn()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.condemn_heretic(2424845))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('setup', [
    'u.allowed=false', 'u.allowed=nil', 'u.allowed=1', 'busy=true',
    'UnitManager.CanStartCommand=nil',
    "UnitManager.CanStartCommand=function() error('unreadable') end",
    'UnitManager.RequestCommand=nil', 'GameInfo.UnitCommands.UNITCOMMAND_CONDEMN_HERETIC=nil',
    'GameInfo.UnitCommands.UNITCOMMAND_CONDEMN_HERETIC.Hash=nil',
    'GameInfo.UnitCommands.UNITCOMMAND_CONDEMN_HERETIC.Hash=0/0',
    'GameInfo.UnitCommands.UNITCOMMAND_CONDEMN_HERETIC.Index=nil',
    "GameInfo.UnitCommands.UNITCOMMAND_CONDEMN_HERETIC.CommandType='UNITCOMMAND_DELETE'",
    'visible=false', 'visible=nil', 'Units.GetUnitsInPlotLayerID=nil', 'units[5767168]=nil',
    'enemy.owner=0', 'enemy.owner=nil', 'enemy.kind=51', 'enemy.kind=999',
    'enemy.religion=nil', 'enemy.religion=0', 'enemy.x=35',
    'GameInfo.Units[4].TrackReligion=nil', 'GameInfo.Units[4].ReligiousStrength=nil',
    'u.owner=1', 'u.id=2490381', 'u.kind=nil', 'u.x=nil', 'u.y=nil',
    'Events.UnitCommandStarted=nil', 'Events.UnitRemovedFromMap=nil',
    "another={};for k,v in pairs(enemy) do another[k]=v end;another.id=5832704;units[another.id]=another",
])
def test_unavailable_ambiguous_or_native_ineligible_condemnation_is_zero_submission(setup):
    conn = CondemnConn(); state = GameState(conn); conn.lua.execute(setup)
    result = asyncio.run(server.unit_action(make_ctx(game=state), 2424845, 'condemn_heretic'))
    assert result.startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('events', [
    'units[5767168]=nil;Events.UnitRemovedFromMap.Fire(1,5767168)',
    'Events.UnitCommandStarted.Fire(0,2424845,-597630620,0)',
    'Events.UnitCommandStarted.Fire(1,2424845,-597630620,0);units[5767168]=nil;Events.UnitRemovedFromMap.Fire(1,5767168)',
    'Events.UnitCommandStarted.Fire(0,2490381,-597630620,0);units[5767168]=nil;Events.UnitRemovedFromMap.Fire(1,5767168)',
    'Events.UnitCommandStarted.Fire(0,2424845,30,0);units[5767168]=nil;Events.UnitRemovedFromMap.Fire(1,5767168)',
    'Events.UnitCommandStarted.Fire(0,2424845,-597630620,0);units[5767168]=nil;Events.UnitRemovedFromMap.Fire(0,5767168)',
    'Events.UnitCommandStarted.Fire(0,2424845,-597630620,0);units[5767168]=nil;Events.UnitRemovedFromMap.Fire(1,5832704)',
    'Events.UnitCommandStarted.Fire(0,2424845,-597630620,0);Events.UnitRemovedFromMap.Fire(1,5767168)',
    'applyCondemn();units[2424845]=nil',
])
def test_incomplete_or_other_native_events_never_confirm_or_resend(events):
    conn = CondemnConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.condemn_heretic(2424845))
    conn.lua.execute(events)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.condemn_heretic(2424845))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.condemn_heretic(2424845))
    assert state.pending_action_kind == 'condemn_heretic'
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('change,restore', [
    ('turn=128', 'turn=127'), ('seed=1', 'seed=-1896272000'),
    ("PlayerConfigurations[0].GetCivilizationTypeName=function() return 'CIVILIZATION_SWEDEN' end",
     "PlayerConfigurations[0].GetCivilizationTypeName=function() return 'CIVILIZATION_SUMERIA' end"),
    ('Game.GetLocalPlayer=function() return 1 end', 'Game.GetLocalPlayer=function() return 0 end'),
])
def test_changed_scope_in_callback_stays_invalid_after_restoring_original_scope(change, restore):
    conn = CondemnConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.condemn_heretic(2424845))
    conn.lua.execute(change + ';applyCondemn();' + restore)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.condemn_heretic(2424845))
    assert conn.lua.eval('#submissions') == 1


def test_temporarily_unreadable_original_target_can_later_confirm_without_resubmitting():
    conn = CondemnConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.condemn_heretic(2424845))
    conn.lua.execute('enemy.religion=nil')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.condemn_heretic(2424845))
    conn.lua.execute('enemy.religion=8;applyCondemn()')
    assert asyncio.run(state.condemn_heretic(2424845)).startswith('HERETIC_CONDEMNED|')
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('interruption', ['lost', 'cancelled'])
def test_lost_or_cancelled_response_only_confirms_frozen_target_without_retargeting(interruption):
    class CancelConn(CondemnConn):
        async def execute_write(self, code):
            before = self.lua.eval('#submissions')
            rows = await super().execute_write(code)
            if self.lua.eval('#submissions') > before:
                raise asyncio.CancelledError()
            return rows

    conn = CancelConn() if interruption == 'cancelled' else CondemnConn()
    state = GameState(conn); ctx = make_ctx(game=state)
    if interruption == 'cancelled':
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(server.unit_action(ctx, 2424845, 'condemn_heretic'))
    else:
        conn.lose_reply = True
        assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 2424845, 'condemn_heretic'))
    conn.lua.execute('enemy.id=5832705;units[enemy.id]=enemy')
    result = asyncio.run(server.unit_action(ctx, 2424845, 'condemn_heretic'))
    assert 'target:1:5767168' in result
    assert conn.lua.eval('units[5832705] ~= nil') is True
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


def test_native_command_throw_after_entry_stays_unknown_and_does_not_replay():
    conn = CondemnConn(); state = GameState(conn); conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.condemn_heretic(2424845))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.condemn_heretic(2424845))
    assert conn.lua.eval('#submissions') == 1


def test_pending_condemnation_blocks_other_mutations_save_and_turn_without_retargeting():
    conn = CondemnConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 2424845, 'condemn_heretic'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.condemn_heretic(2490381))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 2424845, 'activate'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.skip_unit(2424845))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.save_game('pending_condemn'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    conn.lua.execute('applyCondemn()')
    assert asyncio.run(state.condemn_heretic(2424845)).startswith('HERETIC_CONDEMNED|')
    assert conn.lua.eval('#submissions') == 1
