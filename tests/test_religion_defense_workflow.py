"""Cross-feature public workflows use one Lua engine and native-only adapters."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_remove_heresy import PurgeConn
from test_religious_attack import ReligiousCombatConn
from test_server import make_ctx


class ObservationAdapter:
    """Add shipped native read APIs to the action adapters, sharing their entities."""

    def add_observation(self):
        self.lua.execute('''
faith,createdReligion,inquisition=77,12,true
Locale={Lookup=function(value) return value end}
for _,entry in pairs(GameInfo.Units) do
 entry.TrackReligion=entry.UnitType=='UNIT_INQUISITOR' or entry.UnitType=='UNIT_APOSTLE' or entry.UnitType=='UNIT_MISSIONARY'
 entry.ReligiousStrength=entry.TrackReligion and 100 or 0
end
Players[0].GetCities=function() return {Members=function() return ipairs({city}) end} end
Players[0].GetReligion=function() return {
 GetFaithBalance=function() return faith end,GetReligionTypeCreated=function() return createdReligion end,
 HasLaunchedInquisition=function() return inquisition end,
 GetReligionInMajorityOfCities=function() return city.majority end} end
Players[0].GetStats=function() return {GetNumCitiesFollowingReligion=function() return 9 end} end
Players[0].GetDiplomacy=function() return {IsAtWarWith=function() return false end} end
for _,pid in ipairs({0,1}) do
 local player=Players[pid] or {};Players[pid]=player
 player.GetUnits=function()
  return {Members=function()
   local found={};for _,unit in pairs(units) do if unit.owner==pid then table.insert(found,unit) end end
   return ipairs(found)
  end,FindID=function(_,id) local unit=units[id];if unit and unit.owner==pid then return unit end end}
 end
end
city.GetName=function() return 'Eridu' end
city.GetPopulation=function() return 5 end
city.majority=8
city.GetReligion=function() return {
 GetMajorityReligion=function() return city.majority end,
 GetReligionsInCity=function() return religionRows end,
 GetTotalPressureOnCity=function(_,religion) return religion==8 and 6.25 or 3.75 end} end
GameInfo.UnitCommands.UNITCOMMAND_CONDEMN_HERETIC={CommandType='UNITCOMMAND_CONDEMN_HERETIC',Hash=99}
UnitManager.CanStartCommand=function(actor,operation,visible,results)
 assert(operation==99 and visible==false and results==true);return false
end
PlayersVisibility={[0]={IsVisible=function(_,x,y) return x~=80 end,
 IsUnitVisible=function(_,unit) return unit.visible~=false end}}
UnitManager.GetUnit=function(owner,id)
 for _,unit in pairs(units) do
  if unit.owner==owner and (unit.id==id or unit.id%65536==id) then return unit end
 end
end
''')

    async def execute_read(self, code):
        # Execute the actual GameCore query in the same native world adapter.
        return await self.execute_write(code)


class PurgeWorkflowConn(ObservationAdapter, PurgeConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
GameInfo.Units[6]={UnitType='UNIT_MISSIONARY'}
enemy={id=262145,owner=1,kind=6,religion=8,x=35,y=22,spreads=3,moves=2,visible=true}
for _,method in ipairs({'GetID','GetOwner','GetType','GetReligionType','GetX','GetY','GetSpreadCharges','GetMovesRemaining','IsReadyToMove'}) do enemy[method]=u[method] end
units[enemy.id]=enemy
religionRows[1].Followers=0;religionRows[2].Followers=3;religionRows[3].Followers=0;religionRows[4].Followers=2
local nativeApply=applyPurge
applyPurge=function()
 nativeApply()
 for _,row in pairs(religionRows) do row.Followers=row.Religion==8 and 3 or row.Religion==12 and 2 or 0 end
end
''')
        self.add_observation()


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def test_observe_purge_refresh_reports_real_stock_drop_without_inventing_conversion():
    conn = PurgeWorkflowConn(); state = GameState(conn); ctx = make_ctx(game=state)
    before = asyncio.run(server.get_religion_defense(ctx))
    assert 'context_scope=consistent' in before
    assert 'city=65539 religion=8 followers=3 accumulated_pressure=400 incoming_pressure=6.25' in before
    assert 'unit=262145 owner=1' in before and 'at_war=false' in before
    assert 'unit=196608 owner=0' in before and 'charges=3' in before
    assert conn.lua.eval('#submissions') == 0

    result = asyncio.run(server.unit_action(ctx, 196608, 'remove_heresy'))
    assert result.startswith('HERESY_REMOVED|unit:196608|religion:12|charges_remaining:2|')
    assert '8:400->120' in result and '9:200->60' in result

    after = asyncio.run(server.get_religion_defense(ctx))
    assert 'city=65539 religion=8 followers=3 accumulated_pressure=120 incoming_pressure=6.25' in after
    assert 'city=65539 owner=0 name=Eridu plot=35,22 population=5 majority=8' in after
    assert 'unit=196608 owner=0' in after and 'charges=2' in after
    assert 'owned_city_majorities: religion=8 count=1 / owned_cities=1' in after
    assert 'native_religion_city_stat=9 (engine statistic; not owned-city count)' in after
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


def test_observations_before_and_after_native_effect_cannot_release_original_pending_owner():
    conn = PurgeWorkflowConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'remove_heresy'))
    original = state.pending_action_summary
    first = asyncio.run(server.get_religion_defense(ctx))
    assert 'accumulated_pressure=400 incoming_pressure=6.25' in first
    assert state.pending_action_summary == original
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.save_game('pending_religion_workflow'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    assert conn.lua.eval('#submissions') == 1

    conn.lua.execute('applyPurge()')
    after_effect = asyncio.run(server.get_religion_defense(ctx))
    assert 'accumulated_pressure=120 incoming_pressure=6.25' in after_effect
    assert state.pending_action_summary == original
    assert conn.lua.eval('#submissions') == 1
    result = asyncio.run(server.unit_action(ctx, 196608, 'remove_heresy'))
    assert result.startswith('HERESY_REMOVED|')
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions') == 1


def test_unknown_observation_after_lost_submission_does_not_replay_or_clear_pending():
    conn = PurgeWorkflowConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'remove_heresy'))
    original = state.pending_action_summary
    conn.lua.execute('''
local nativeReligion=Players[0].GetReligion
Players[0].GetReligion=function()
 local religion=nativeReligion()
 religion.HasLaunchedInquisition=function() error('unavailable in observation') end
 return religion
end
''')
    observed = asyncio.run(server.get_religion_defense(ctx))
    assert 'inquisition: InGame=unknown GameCore=unknown' in observed
    assert 'accumulated_pressure=120' in observed
    assert state.pending_action_summary == original
    assert conn.lua.eval('#submissions') == 1
    assert asyncio.run(server.unit_action(ctx, 196608, 'remove_heresy')).startswith('HERESY_REMOVED|')
    assert conn.lua.eval('#submissions') == 1


class CombatWorkflowConn(ObservationAdapter, ReligiousCombatConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
city.owner=0;city.x=19;city.y=17
religionRows={{Religion=8,Followers=3,Pressure=400},{Religion=12,Followers=2,Pressure=100}}
local nativeCombat=applyCombat
applyCombat=function()
 nativeCombat()
 enemy.damage=100
 units[5767169]=nil
 Events.UnitRemovedFromMap.Fire(1,5767169)
 -- A same-tile military survivor is a separate map layer, not the defender.
end
GameInfo.Units[6]={UnitType='UNIT_WARRIOR',TrackReligion=false}
military={id=5832706,owner=1,kind=6,religion=-1,x=20,y=17,moves=2,spreads=0}
for _,method in ipairs({'GetID','GetOwner','GetType','GetReligionType','GetX','GetY','GetSpreadCharges','GetMovesRemaining','IsReadyToMove'}) do military[method]=u[method] end
units[military.id]=military
''')
        self.add_observation()


def test_visible_peaceful_religious_target_can_be_attacked_and_fresh_scan_keeps_layers_separate():
    conn = CombatWorkflowConn(); state = GameState(conn); ctx = make_ctx(game=state)
    before = asyncio.run(server.get_religion_defense(ctx))
    assert 'unit=5767169 owner=1' in before and 'at_war=false' in before
    assert 'unit=5832706' not in before
    assert conn.lua.eval('#submissions') == 0
    result = asyncio.run(server.unit_action(ctx, 196608, 'religious_attack', target_x=20, target_y=17))
    assert result.startswith('RELIGIOUS_COMBAT_CONFIRMED|unit:196608|target:1:5767169|')
    assert 'defender_hp:100->0' in result
    after = asyncio.run(server.get_religion_defense(ctx))
    assert 'unit=5767169' not in after and 'unit=5832706' not in after
    assert 'unit=196608 owner=0' in after
    assert conn.lua.eval('units[5832706] ~= nil') is True
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


def test_fresh_stdio_metadata_advertises_defense_without_any_network_connection():
    import sys
    from pathlib import Path
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # A connection attempt fails the fresh child service immediately. Metadata
    # discovery must work before any FireTuner or game operation is requested.
    child = '''
import socket

def forbidden_connection(*args, **kwargs):
    raise AssertionError("Metadata discovery must not connect to FireTuner or any network")

socket.socket.connect = forbidden_connection
from civ_mcp.server import main
main()
'''

    async def discover():
        params = StdioServerParameters(
            command=sys.executable, args=['-c', child],
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert tools['get_religion_defense'].annotations.readOnlyHint is True
                action = tools['unit_action']
                assert 'remove_heresy' in action.description
                assert 'religious_attack' in action.description
                assert {'unit_id', 'action'} <= set(action.inputSchema['required'])
                assert 'target_x' in action.inputSchema['properties']
                assert 'target_y' in action.inputSchema['properties']

    asyncio.run(discover())
