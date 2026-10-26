"""Unit attacks must bind a visible hostile city even without a garrison."""

import asyncio

import pytest

from test_attack_mode_lua import AttackConn, state_for


class SiegeConn(AttackConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
ComponentType={UNIT=0,DISTRICT=2};CombatVisType={ATTACKER=0,DEFENDER=1}
local function event()
 local handlers={}
 return {Add=function(h) handlers[h]=true end,Remove=function(h) handlers[h]=nil end,
 Fire=function(...) for h in pairs(handlers) do h(...) end end}
end
Events={CombatVisEnd=event(),UnitRemovedFromMap=event()}
UnitOperationTypes.MOVE_TO=1;UnitOperationTypes.RANGE_ATTACK=2;UnitOperationTypes.AIR_ATTACK=3
allowed[1],allowed[2],allowed[3]=true,true,true
cityOwner,cityID,cityHP,wallHP=2,65536,200,100
city={GetOwner=function() return cityOwner end,GetID=function() return cityID end,
 GetName=function() return 'Uppsala' end,GetX=function() return 19 end,
 GetY=function() return 16 end,GetComponentID=function() return {player=cityOwner,id=65536,type=1} end}
center={GetOwner=function() return cityOwner end,GetID=function() return 7 end,
 GetType=function() return 0 end,GetX=function() return 19 end,GetY=function() return 16 end,
 GetComponentID=function() return {player=cityOwner,id=7,type=2} end,
 GetDefenseStrength=function() return 60 end,
 GetMaxDamage=function(_,kind) return kind==1 and 100 or 200 end,
 GetDamage=function(_,kind) return kind==1 and 100-wallHP or 200-cityHP end}
DefenseTypes={DISTRICT_GARRISON=0,DISTRICT_OUTER=1}
GameInfo.Districts={DISTRICT_CITY_CENTER={Index=0},[0]={DistrictType='DISTRICT_CITY_CENTER'}}
city.GetDistricts=function() return {Members=function()
 local seen=false;return function() if not seen then seen=true;return 7,center end end
end} end
CityManager.GetCityAt=function(px,py) if px==19 and py==16 then return city end end
Map.GetUnitsAt=function() return nil end
Players[2].GetCities=function() return {FindID=function(_,id) if id==65536 then return city end end} end
UnitManager.RequestOperation=function(_,op,params)
 record_operation(op,params);moves=0;attacks=attacks-1;wallHP=wallHP-30
 Events.CombatVisEnd.Fire({[0]={componentType=0,playerID=0,componentID=unit:GetID()},
  [1]={componentType=2,playerID=cityOwner,componentID=7}})
end
CombatManager.IsAttackChangeWarState=function() return nil end
CombatManager.SimulateAttackInto=function(actor,mode,px,py)
 return {attacker={id=actor},defender={id=center:GetComponentID()}}
end
''')


def terminal_capture_conn():
    """Native T237 pattern: a sea capture ends the game before final entry."""
    conn = SiegeConn()
    conn.lua.execute('''
unitInfo.Domain='DOMAIN_SEA';unitInfo.RangedCombat=0;unitInfo.Range=0;wallHP=0;cityHP=0
winner,selector=-1,-77
Game.GetWinningTeam=function() return winner,selector end
GameInfo.Victories={[-77]={VictoryType='VICTORY_CONQUEST'}}
Players[0].GetTeam=function() return 7 end
UnitManager.RequestOperation=function(_,op,params)
 record_operation(op,params);moves=0;attacks=0
 cityOwner=0;cityID=262144;wallHP=100;cityHP=200;winner=7
 Events.CombatVisEnd.Fire({[0]={componentType=0,playerID=0,componentID=unit:GetID()},
  [1]={componentType=2,playerID=2,componentID=7}})
 if capture_change then capture_change() end
end
''')
    return conn


@pytest.mark.usefixtures('fast_action_confirmation')
def test_conquest_capture_confirms_when_attacker_remains_on_adjacent_sea_tile():
    conn = terminal_capture_conn()
    state = state_for(conn)
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert 'UNIT_CITY_ATTACK_CONFIRMED' in result, result
    assert 'owner:2->0' in result and 'completion:terminal_conquest' in result
    assert (conn.lua.globals().x, conn.lua.globals().y) == (20, 16)
    assert len(conn.operations) == 1 and state.pending_action_summary is None


@pytest.mark.parametrize('change,restore', [
    ('winner=2', 'winner=7'),
    ("GameInfo.Victories[-77].VictoryType='VICTORY_TECHNOLOGY'",
     "GameInfo.Victories[-77].VictoryType='VICTORY_CONQUEST'"),
    ('unit.GetType=function() return 99 end', 'unit.GetType=function() return 1 end'),
    ('cityOwner=3', 'cityOwner=0'),
])
@pytest.mark.usefixtures('fast_action_confirmation')
def test_terminal_capture_latches_conflicts_observed_during_confirmation(change, restore):
    conn = terminal_capture_conn()
    conn.lua.execute('''
Game.GetWinningTeam=function()
 local value=winner
 if value>=0 and not changed then changed=true;CHANGE end
 return value,selector
end
'''.replace('CHANGE', change))
    state = state_for(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.attack_unit(8, 19, 16))
    conn.lua.execute(restore)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    assert len(conn.operations) == 1 and state.pending_action_summary is not None


@pytest.mark.parametrize('conflict', ['winner', 'team', 'victory'])
@pytest.mark.usefixtures('fast_action_confirmation')
def test_terminal_capture_does_not_hide_known_conflict_behind_an_unknown_getter(conflict):
    conn = terminal_capture_conn()
    conn.lua.globals().conflict = conflict
    conn.lua.execute('''
reads=0
Game.GetWinningTeam=function()
 if winner<0 then return winner,selector end
 reads=reads+1
 if reads==1 then return winner,selector end
 if conflict=='winner' then return 2,selector end
 if conflict=='team' then error('winner unavailable') end
 return winner,selector
end
Players[0].GetTeam=function()
 if reads==1 then return 7 end
 if conflict=='team' then return 9 end
 if conflict=='victory' then GameInfo.Victories[-77].VictoryType='VICTORY_CULTURE' end
 return nil
end
''')
    state = state_for(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.attack_unit(8, 19, 16))
    conn.lua.execute('''
Game.GetWinningTeam=function() return 7,-77 end
Players[0].GetTeam=function() return 7 end
GameInfo.Victories[-77].VictoryType='VICTORY_CONQUEST'
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    assert len(conn.operations) == 1 and state.pending_action_summary is not None


@pytest.mark.usefixtures('fast_action_confirmation')
def test_terminal_capture_cannot_use_a_victory_that_precedes_the_attack():
    conn = terminal_capture_conn()
    conn.lua.execute('Game.GetWinningTeam=function() return 7,-77 end')
    state = state_for(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.attack_unit(8, 19, 16))
    assert len(conn.operations) == 1 and state.pending_action_summary is not None


@pytest.mark.usefixtures('fast_action_confirmation')
def test_terminal_capture_rechecks_actor_after_captured_city_readback():
    conn = terminal_capture_conn()
    conn.lua.execute('''
winnerReads=0
Game.GetWinningTeam=function()
 if winner>=0 then winnerReads=winnerReads+1 end
 return winner,selector
end
local damage=center.GetDamage
center.GetDamage=function(self,kind)
 if winnerReads>=2 then unit.GetType=function() return 99 end end
 return damage(self,kind)
end
''')
    state = state_for(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.attack_unit(8, 19, 16))
    conn.lua.execute('unit.GetType=function() return 1 end')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    assert len(conn.operations) == 1 and state.pending_action_summary is not None


@pytest.mark.usefixtures('fast_action_confirmation')
def test_terminal_capture_latches_result_conflict_even_when_city_health_is_unknown():
    conn = terminal_capture_conn()
    conn.lua.execute('''
nativeDamage=center.GetDamage
Game.GetWinningTeam=function()
 if winner>=0 then center.GetDamage=function() return nil end end
 return winner,selector
end
''')
    state = state_for(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.attack_unit(8, 19, 16))
    conn.lua.execute('winner=2;Game.GetWinningTeam=function() return winner,selector end')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    conn.lua.execute('winner=7;center.GetDamage=nativeDamage')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    assert len(conn.operations) == 1 and state.pending_action_summary is not None


@pytest.mark.parametrize('setup,change', [
    ('Game.GetWinningTeam=nil', ''),
    ('Game.GetWinningTeam=function() error("unavailable") end', ''),
    ('Game.GetWinningTeam=function() return -1,-77 end', ''),
    ('Game.GetWinningTeam=function() return 9,-77 end', ''),
    ('Game.GetWinningTeam=function() return "7",-77 end', ''),
    ('Game.GetWinningTeam=function() return 0/0,-77 end', ''),
    ('Game.GetWinningTeam=function() return 7,nil end', ''),
    ('Players[0].GetTeam=function() return nil end', ''),
    ('Players[0].GetTeam=function() return "7" end', ''),
    ('GameInfo.Victories={}', ''),
    ("GameInfo.Victories[-77].VictoryType='VICTORY_TECHNOLOGY'", ''),
    ("GameInfo.Victories[-77].VictoryType='VICTORY_CULTURE'", ''),
    ("GameInfo.Victories[-77].VictoryType='VICTORY_RELIGIOUS'", ''),
    ("GameInfo.Victories[-77].VictoryType='VICTORY_DIPLOMATIC'", ''),
    ("GameInfo.Victories[-77].VictoryType='VICTORY_SCORE'", ''),
    ("GameInfo.Victories[-77].VictoryType='unknown'", ''),
    ('Events.CombatVisEnd.Fire=function() end', ''),
    ('local fire=Events.CombatVisEnd.Fire;Events.CombatVisEnd.Fire=function(data) data[1].componentID=99;fire(data) end', ''),
    ('unitInfo.RangedCombat=40;unitInfo.Range=2;wallHP=100;cityHP=200', ''),
    ('', 'cityOwner=2;cityID=65536;cityHP=0;wallHP=0'),
    ('', 'distance=2'),
    ('', 'unit.GetDamage=function() return 100 end'),
    ('', 'unit.GetType=function() return 99 end'),
    ('', 'unit.GetID=function() return 1245193 end'),
    ('', 'unit.GetComponentID=function() return {type=0,player=0,id=99} end'),
    ('', 'UnitManager.GetUnit=function() return nil end'),
    ('', 'Game.GetLocalPlayer=function() return 2 end'),
    ('', 'Game.GetCurrentGameTurn=function() return 114 end'),
    ('', 'GameConfiguration.GetValue=function() return 99 end'),
    ('', "PlayerConfigurations[0].GetCivilizationTypeName=function() return 'CIVILIZATION_ROME' end"),
])
@pytest.mark.usefixtures('fast_action_confirmation')
def test_terminal_outcome_does_not_replace_missing_or_conflicting_capture_evidence(setup, change):
    conn = terminal_capture_conn()
    conn.lua.execute(setup)
    conn.lua.execute('capture_change=function() ' + change + ' end')
    state = state_for(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.attack_unit(8, 19, 16))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    assert len(conn.operations) == 1 and state.pending_action_summary is not None


@pytest.mark.usefixtures('fast_action_confirmation')
def test_adjacent_gdr_captures_defeated_city_with_one_native_melee_operation():
    conn = SiegeConn()
    conn.lua.execute('''
unitInfo.UnitType='UNIT_GIANT_DEATH_ROBOT';unitInfo.Combat=130
unitInfo.RangedCombat=120;unitInfo.Range=3;wallHP=0;cityHP=0
UnitManager.RequestOperation=function(_,op,params)
 record_operation(op,params);moves=0;attacks=0
 if op==UnitOperationTypes.MOVE_TO then x=19;y=16;cityOwner=0;cityID=262144;cityHP=100 end
 Events.CombatVisEnd.Fire({[0]={componentType=0,playerID=0,componentID=unit:GetID()},
  [1]={componentType=2,playerID=2,componentID=7}})
end
''')
    state = state_for(conn)
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert 'UNIT_CITY_ATTACK_CONFIRMED' in result and 'owner:2->0' in result, result
    assert len(conn.operations) == 1 and conn.operations[0][0] == 1
    assert conn.operations[0][1]['modifiers'] == 7
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup', [
    '',
    "unitInfo.UnitType='UNIT_GIANT_DEATH_ROBOT';unitInfo.Range=3;distance=2",
    "unitInfo.Domain='DOMAIN_AIR';unitInfo.Range=3",
])
@pytest.mark.usefixtures('fast_action_confirmation')
def test_ranged_shot_at_defeated_city_is_refused_without_spending_attack(setup):
    conn = SiegeConn()
    conn.lua.execute('wallHP=0;cityHP=0;' + setup)
    state = state_for(conn)
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert 'CITY_ALREADY_DEFEATED' in result, result
    assert conn.operations == [] and conn.lua.globals().attacks == 1
    assert state.pending_action_summary is None


@pytest.mark.usefixtures('fast_action_confirmation')
def test_visible_hostile_city_without_military_unit_is_attacked_once():
    conn = SiegeConn()
    result = asyncio.run(state_for(conn).attack_unit(8, 19, 16))
    assert 'NO_ENEMY' not in result, result
    assert len(conn.operations) == 1, result
    assert conn.lua.globals().wallHP == 70
    assert 'UNIT_CITY_ATTACK_CONFIRMED' in result, result


@pytest.mark.parametrize('setup,operation', [
    ("unitInfo.UnitType='UNIT_GIANT_DEATH_ROBOT';unitInfo.RangedCombat=120;unitInfo.Range=3", 2),
    ("unitInfo.Domain='DOMAIN_SEA'", 2),
    ('unitInfo.RangedCombat=0;unitInfo.Bombard=55', 2),
    ('unitInfo.RangedCombat=0;unitInfo.Range=0', 1),
    ("unitInfo.Domain='DOMAIN_SEA';unitInfo.RangedCombat=0;unitInfo.Range=0", 1),
    ("unitInfo.Domain='DOMAIN_AIR';unitInfo.Range=4", 3),
])
@pytest.mark.usefixtures('fast_action_confirmation')
def test_native_city_attack_modes(setup, operation):
    conn = SiegeConn()
    conn.lua.execute(setup)
    state = state_for(conn)
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert 'UNIT_CITY_ATTACK_CONFIRMED' in result, result
    assert len(conn.operations) == 1 and conn.operations[0][0] == operation
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup', ['allowed[1]=false', 'arrivalTurn=2', 'movedIntoZOC=true'])
@pytest.mark.usefixtures('fast_action_confirmation')
def test_gdr_capture_still_requires_native_current_turn_melee_eligibility(setup):
    conn = SiegeConn()
    conn.lua.execute("unitInfo.UnitType='UNIT_GIANT_DEATH_ROBOT';wallHP=0;cityHP=0;" + setup)
    state = state_for(conn)
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert result.startswith('Error:'), result
    assert conn.operations == [] and state.pending_action_summary is None


@pytest.mark.parametrize('setup', [
    'cityOwner=0', 'atPeace=true', 'PlayersVisibility[0].IsVisible=function() return false end',
    'moves=0', 'attacks=0', 'distance=3', 'allowed[2]=false',
    'Events.CombatVisEnd=nil', 'CombatManager.SimulateAttackInto=function() return nil end',
    'CombatManager.SimulateAttackInto=function(a) return {attacker={id=a},defender={id=enemy:GetComponentID()}} end',
    'CombatManager.IsAttackChangeWarState=function() return {3} end',
    'center.GetDamage=function() return nil end',
])
@pytest.mark.usefixtures('fast_action_confirmation')
def test_city_preflight_refusals_never_submit(setup):
    conn = SiegeConn()
    conn.lua.execute(setup)
    state = state_for(conn)
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert result.startswith('Error:'), result
    assert conn.operations == [] and state.pending_action_summary is None


@pytest.mark.usefixtures('fast_action_confirmation')
def test_melee_capture_confirms_replaced_city_identity_and_ownership():
    conn = SiegeConn()
    conn.lua.execute('''
unitInfo.RangedCombat=0;unitInfo.Range=0
UnitManager.RequestOperation=function(_,op,params)
 record_operation(op,params);moves=0;attacks=0;x=19;y=16
 cityOwner=0;cityID=262144;wallHP=0;cityHP=50
 Events.CombatVisEnd.Fire({[0]={componentType=0,playerID=0,componentID=unit:GetID()},
  [1]={componentType=2,playerID=2,componentID=7}})
end
''')
    result = asyncio.run(state_for(conn).attack_unit(8, 19, 16))
    assert 'UNIT_CITY_ATTACK_CONFIRMED' in result and 'owner:2->0' in result, result
    assert len(conn.operations) == 1


@pytest.mark.usefixtures('fast_action_confirmation')
def test_lost_city_attack_acknowledgement_confirms_without_resubmitting():
    class LostAck(SiegeConn):
        async def execute_write(self, code):
            rows = await super().execute_write(code)
            if 'UnitManager.RequestOperation(unit,r.operation,r.params)' in code:
                raise ConnectionError('lost acknowledgement')
            return rows
    conn = LostAck()
    state = state_for(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.attack_unit(8, 19, 16))
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert 'UNIT_CITY_ATTACK_CONFIRMED' in result, result
    assert len(conn.operations) == 1 and state.pending_action_summary is None


@pytest.mark.parametrize('event', [
    '',
    'Events.CombatVisEnd.Fire({[0]={componentType=0,playerID=0,componentID=unit:GetID()},[1]={componentType=2,playerID=2,componentID=99}})',
])
@pytest.mark.usefixtures('fast_action_confirmation')
def test_city_damage_and_consumed_attack_without_exact_combat_event_keep_pending(event):
    conn = SiegeConn()
    conn.lua.execute('''
UnitManager.RequestOperation=function(_,op,params)
 record_operation(op,params);moves=0;attacks=0;wallHP=70
 EVENT
end
'''.replace('EVENT', event))
    state = state_for(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.attack_unit(8, 19, 16))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.attack_unit(8, 19, 16))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.move_unit(8, 20, 16))
    assert len(conn.operations) == 1 and state.pending_action_summary is not None


@pytest.mark.usefixtures('fast_action_confirmation')
def test_capture_owner_transition_waits_for_actor_arrival_without_resubmission():
    conn = SiegeConn()
    conn.lua.execute('''
unitInfo.RangedCombat=0;unitInfo.Range=0
UnitManager.RequestOperation=function(_,op,params)
 record_operation(op,params);moves=0;attacks=0;cityOwner=0;cityID=262144;wallHP=0
 Events.CombatVisEnd.Fire({[0]={componentType=0,playerID=0,componentID=unit:GetID()},
  [1]={componentType=2,playerID=2,componentID=7}})
end
''')
    state = state_for(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.attack_unit(8, 19, 16))
    conn.lua.execute('x=19;y=16')
    assert 'UNIT_CITY_ATTACK_CONFIRMED' in asyncio.run(state.attack_unit(8, 19, 16))
    assert len(conn.operations) == 1


@pytest.mark.parametrize('change,restore', [
    ('unit.GetType=function() return 99 end', 'unit.GetType=function() return 1 end'),
    ('cityID=99', 'cityID=65536'),
    ('cityOwner=3', 'cityOwner=2'),
    ('center.GetComponentID=function() return {type=2,player=2,id=99} end',
     'center.GetComponentID=function() return {type=2,player=2,id=7} end'),
])
@pytest.mark.usefixtures('fast_action_confirmation')
def test_known_identity_mismatch_is_latched_even_when_city_hp_is_unavailable(change, restore):
    class MissingAck(SiegeConn):
        async def execute_write(self, code):
            rows = await super().execute_write(code)
            if 'UnitManager.RequestOperation(unit,r.operation,r.params)' in code:
                raise ConnectionError('lost acknowledgement')
            return rows
    conn = MissingAck()
    state = state_for(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.attack_unit(8, 19, 16))
    conn.lua.execute('oldDamage=center.GetDamage;center.GetDamage=function() return nil end;' + change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.attack_unit(8, 19, 16))
    conn.lua.execute('center.GetDamage=oldDamage;' + restore)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.attack_unit(8, 19, 16))
    assert len(conn.operations) == 1 and state.pending_action_summary is not None


def test_plain_move_to_visible_enemy_city_is_refused_before_submission():
    from test_move_target_lua import TargetMovementConn
    conn = TargetMovementConn()
    conn.lua.execute('''
can_target=true;war=true
CityManager={GetCityAt=function() return {GetOwner=function() return 2 end} end}
''')
    state = state_for(conn)
    result = asyncio.run(state.move_unit(6, 20, 17))
    assert 'ENEMY_CITY_MOVE' in result and 'unit attack' in result, result
    assert conn.lua.globals().submissions == 0 and state.pending_action_summary is None


@pytest.mark.usefixtures('fast_action_confirmation')
def test_city_center_is_bound_before_a_military_garrison():
    conn = SiegeConn()
    conn.lua.execute('''
Map.GetUnitsAt=function() return {Units=function()
 local seen=false;return function() if not seen then seen=true;return enemy end end
end} end
''')
    result = asyncio.run(state_for(conn).attack_unit(8, 19, 16))
    assert 'UNIT_CITY_ATTACK_CONFIRMED' in result, result
    assert conn.lua.globals().enemyHP == 6 and conn.lua.globals().wallHP == 70


@pytest.mark.usefixtures('fast_action_confirmation')
def test_exact_attacker_removal_confirms_failed_assault_without_claiming_capture():
    conn = SiegeConn()
    conn.lua.execute('''
unitInfo.RangedCombat=0;unitInfo.Range=0
UnitManager.GetUnit=function() if not dead then return unit end end
UnitManager.RequestOperation=function(_,op,params)
 record_operation(op,params);moves=0;attacks=0;dead=true
 Events.CombatVisEnd.Fire({[0]={componentType=0,playerID=0,componentID=unit:GetID()},
  [1]={componentType=2,playerID=2,componentID=7}})
 Events.UnitRemovedFromMap.Fire(0,unit:GetID())
end
''')
    state = state_for(conn)
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert 'UNIT_CITY_ATTACK_CONFIRMED' in result and 'owner:2->2' in result, result
    assert 'attacker_hp:100->0' in result and state.pending_action_summary is None
