"""GameState runs real Lua; native capabilities, eligibility and combat are isolated."""
import asyncio

import pytest
from lupa.lua51 import LuaRuntime

from civ_mcp.game_state import GameState


class AttackConn:
    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.operations = []
        self.previews = []
        self.checks = []
        native = self.lua.globals()
        native.print = lambda row: self.rows.append(str(row))
        native.record_operation = lambda op, params: self.operations.append((op, dict(params)))
        native.record_preview = self.previews.append
        native.record_check = lambda op, param_type: self.checks.append((op, param_type))
        self.lua.execute('''
x,y,moves,attacks,enemyHP = 20,16,2,1,6
onlyMove,genericAllowed = false,false
allowed = {move=true,range=false,air=false}
Game = {GetLocalPlayer=function() return 0 end,GetCurrentGameTurn=function() return 113 end}
GameConfiguration = {GetValue=function() return 719665280 end}
PlayerConfigurations = {[0]={GetCivilizationTypeName=function() return 'CIVILIZATION_SUMERIA' end}}
Locale = {Lookup=function(value) return value end}
UI = {IsGameCoreBusy=function() return false end}
unitInfo = {UnitType='UNIT_CROSSBOWMAN',Domain='DOMAIN_LAND',Combat=30,RangedCombat=40,Bombard=0,Range=2}
enemyInfo = {UnitType='UNIT_SWEDEN_CAROLEAN',Combat=55}
GameInfo = {Units={[1]=unitInfo,[2]=enemyInfo},Districts={}}
local function component(owner,id) return {player=owner,id=id,type=0} end
unit = {
    GetOwner=function() return 0 end,GetID=function() return 1245192 end,
    GetType=function() return 1 end,GetX=function() return x end,GetY=function() return y end,
    GetMovesRemaining=function() return moves end,GetAttacksRemaining=function() return attacks end,
    GetMaxDamage=function() return 100 end,GetDamage=function() return 0 end,
    HasMovedIntoZOC=function() return movedIntoZOC==true end,
    GetComponentID=function() return component(0,1245192) end,
    GetCombat=function() return unitInfo.Combat end,
    GetRangedCombat=function() return unitInfo.RangedCombat end,
    GetBombardCombat=function() return unitInfo.Bombard end,
    GetRange=function() return unitInfo.Range end,
}
enemy = {
    GetOwner=function() return 2 end,GetID=function() return 5111813 end,
    GetType=function() return 2 end,GetX=function() return 19 end,GetY=function() return 16 end,
    GetMaxDamage=function() return 100 end,GetDamage=function() return 100-enemyHP end,
    GetComponentID=function() return component(2,5111813) end,
}
local function objects(value)
    return function() local done=false;return function() if not done then done=true;return value end end end
end
local function members(value)
    return function() local done=false;return function() if not done then done=true;return 1,value end end end
end
local function player(value)
    return {IsAlive=function() return true end,
        GetUnits=function() return {Members=members(value),FindID=function() return value end} end,
        GetCities=function() return {Members=function() return function() end end} end,
        GetDiplomacy=function() return {IsAtWarWith=function() return atPeace~=true end} end}
end
Players = {[0]=player(unit),[2]=player(enemy)}
PlayersVisibility = {[0]={IsVisible=function() return true end}}
Map = {
    GetPlotDistance=function() return distance or 1 end,
    GetPlot=function() return {GetIndex=function() return 1 end} end,
    GetUnitsAt=function() return {Units=objects(enemy)} end,
}
UnitOperationTypes = {MOVE_TO='move',RANGE_ATTACK='range',AIR_ATTACK='air',PARAM_X='x',PARAM_Y='y',PARAM_MODIFIERS='modifiers'}
UnitOperationMoveModifiers = {ATTACK=7}
UnitManager = {
    GetUnit=function() return unit end,
    CanStartOperation=function(_,op,unused,params)
        record_check(op,type(params))
        if type(params)~='table' then return genericAllowed end
        return allowed[op]==true
    end,
    RequestOperation=function(_,op,params)
        record_operation(op,params)
        moves=moves-1
        if onlyMove then x=21 else attacks=attacks-1;enemyHP=enemyHP-5 end
    end,
}
CityManager = {GetCityAt=function() return nil end}
CombatTypes = {RANGED='Ranged',BOMBARD='Bombard'}
CombatResultParameters = {ATTACKER='attacker',DEFENDER='defender',ID='id',COMBAT_STRENGTH='strength',STRENGTH_MODIFIER='modifier',DAMAGE_TO='damage'}
local function simulate(attackerID,defenderID,mode)
    record_preview(mode or 'Melee')
    return {attacker={id=attackerID,strength=50,modifier=0,damage=0},
            defender={id=defenderID,strength=55,modifier=0,damage=5}}
end
CombatManager = {SimulateAttackVersus=simulate}
''')
        self.stop_ambiguous_read = False

    async def execute_write(self, code):
        self.rows.clear()
        self.lua.execute(code)
        if self.stop_ambiguous_read and any(row.startswith('ATTACKER|') for row in self.rows):
            raise ConnectionError('confirmation interrupted after native execution')
        return [row for row in self.rows if row != '---END---']


def state_for(conn):
    state = GameState(conn)
    async def no_popup_context():
        return ''
    state.dismiss_popup = no_popup_context
    return state


def test_adjacent_ranged_target_refusal_never_becomes_a_melee_move_or_preview():
    conn = AttackConn()
    state = state_for(conn)
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert result.startswith('Error:'), result
    assert conn.operations == []
    assert conn.previews == []
    assert state.pending_action_summary is None


def test_target_specific_ranged_permission_does_not_require_a_generic_permission():
    conn = AttackConn()
    conn.lua.execute('allowed.range=true')
    state = state_for(conn)
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert 'RANGE_ATTACK|' in result and 'Native combat preview (Ranged)' in result
    assert conn.operations == [('range', {'x': 19, 'y': 16})]
    assert conn.previews == ['Ranged']
    assert all(param_type == 'table' for _, param_type in conn.checks)
    assert (conn.lua.globals().x, conn.lua.globals().y) == (20, 16)
    assert state.pending_action_summary is None


def test_unavailable_bombard_preview_mapping_stays_unknown_without_changing_attack_mode():
    conn = AttackConn()
    conn.lua.execute('unitInfo.RangedCombat=0;unitInfo.Bombard=55;allowed.range=true;CombatTypes.BOMBARD=nil')
    result = asyncio.run(state_for(conn).attack_unit(8, 19, 16))
    assert 'RANGE_ATTACK|' in result and 'Combat estimate unknown' in result
    assert conn.operations == [('range', {'x': 19, 'y': 16})]
    assert conn.previews == []


@pytest.mark.parametrize('setup', [
    'distance=2',
    'genericAllowed=true',
    'unitInfo.RangedCombat=0;unitInfo.Bombard=55',
    "unitInfo.Domain='DOMAIN_AIR'",
    "unitInfo.Domain='DOMAIN_AIR';genericAllowed=true",
    'unitInfo.Combat=0;unitInfo.RangedCombat=0;unitInfo.Bombard=0',
    "unitInfo.Domain='DOMAIN_AIR';unitInfo.Combat=0;unitInfo.RangedCombat=0;unitInfo.Bombard=0;allowed.air=true",
    'GameInfo.Units[1]=nil',
    'GameInfo.Units=nil',
    'GameInfo=nil',
    'unitInfo.Domain=nil',
    'unitInfo.RangedCombat=nil;unitInfo.Bombard=nil',
    'unit.GetRangedCombat=function() return 0 end',
    'moves=0;allowed.range=true',
    'distance=3;allowed.range=true',
    'atPeace=true;allowed.range=true',
    'unitInfo.RangedCombat=0;allowed.move=false',
    'unitInfo.RangedCombat=0;movedIntoZOC=true',
])
def test_unavailable_or_unknown_attack_never_submits_any_operation_or_melee_preview(setup):
    conn = AttackConn()
    conn.lua.execute(setup)
    state = state_for(conn)
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert result.startswith('Error:'), result
    assert conn.operations == []
    assert conn.previews == []
    assert state.pending_action_summary is None


@pytest.mark.parametrize(('setup', 'operation', 'preview'), [
    ('allowed.range=true;distance=2', 'range', 'Ranged'),
    ('allowed.range=true;unitInfo.RangedCombat=0;unitInfo.Bombard=55', 'range', 'Bombard'),
    ("allowed.air=true;unitInfo.Domain='DOMAIN_AIR'", 'air', None),
    ("allowed.air=true;unitInfo.Domain='DOMAIN_AIR';genericAllowed=true;allowed.range=true", 'air', None),
    ('unitInfo.RangedCombat=0', 'move', 'Melee'),
    ("unitInfo.Domain='DOMAIN_SEA';allowed.range=true", 'range', 'Ranged'),
    ("unitInfo.Domain='DOMAIN_SEA';unitInfo.RangedCombat=0", 'move', 'Melee'),
    ('allowed.range=true;unit.GetRangedCombat=function() return 0 end', 'range', 'Ranged'),
    ('allowed.range=true;unitInfo.RangedCombat=0;unit.GetRangedCombat=function() return 40 end', 'range', 'Ranged'),
    ('allowed.range=true;unit.GetRangedCombat=function() error("unavailable") end', 'range', 'Ranged'),
    ('allowed.range=true;distance=3;unit.GetRange=function() return 3 end', 'range', 'Ranged'),
    ('allowed.range=true;movedIntoZOC=true', 'range', 'Ranged'),
])
def test_valid_capability_uses_one_correct_operation_and_matching_preview(setup, operation, preview):
    conn = AttackConn()
    conn.lua.execute(setup)
    result = asyncio.run(state_for(conn).attack_unit(8, 19, 16))
    assert 'OUTCOME_UNKNOWN' not in result and not result.startswith('Error:'), result
    expected = {'x': 19, 'y': 16}
    if operation == 'move':
        expected['modifiers'] = 7
    assert conn.operations == [(operation, expected)]
    assert conn.previews == ([] if preview is None else [preview])
    if preview is None:
        assert 'Combat estimate unknown' in result
    assert all(param_type == 'table' for _, param_type in conn.checks)


def test_old_move_only_attack_stays_unknown_and_same_intent_only_observes():
    conn = AttackConn()
    conn.lua.execute('unitInfo.RangedCombat=0;onlyMove=true')
    conn.stop_ambiguous_read = True
    state = state_for(conn)
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert 'OUTCOME_UNKNOWN' in result
    assert conn.lua.globals().x == 21 and conn.lua.globals().moves == 1
    assert conn.lua.globals().attacks == 1 and conn.lua.globals().enemyHP == 6
    assert state.pending_action_summary is not None
    # Corrected capability information cannot retire or resubmit an old intent.
    conn.lua.execute('unitInfo.RangedCombat=40')
    repeated = asyncio.run(state.attack_unit(8, 19, 16))
    assert 'OUTCOME_UNKNOWN' in repeated
    conn.stop_ambiguous_read = False
    observed = asyncio.run(state.confirm_pending_action(wait=False))
    assert 'OUTCOME_UNKNOWN' in observed and 'attacks:1' in observed
    assert len(conn.operations) == 1 and conn.operations[0][0] == 'move'
    assert state.pending_action_summary is not None
