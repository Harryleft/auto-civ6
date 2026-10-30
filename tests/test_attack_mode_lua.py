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
PlayersVisibility = {[0]={IsVisible=function() return true end,IsUnitVisible=function()return true end}}
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
local function plot(px,py)
    return {GetIndex=function() return px+1000*py end,
            GetX=function() return px end,GetY=function() return py end}
end
Map.GetPlot=function(px,py) return plot(px,py) end
Map.GetPlotByIndex=function(index) return plot(index%1000,math.floor(index/1000)) end
UnitManager.GetMoveToPathEx=function(u,destination)
    return {plots={u:GetX()+1000*u:GetY(),destination},turns={1,arrivalTurn or 1}}
end
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


@pytest.mark.usefixtures('fast_action_confirmation')
def test_hidden_target_never_submits_or_previews_attack():
    conn = AttackConn()
    # Reproduce a target leaving sight after another friendly unit withdraws.
    # Native Map data can still expose the object and generic eligibility can
    # still allow an ATTACK-modified move; neither establishes current sight.
    conn.lua.execute('''
unitInfo.RangedCombat=0;PlayersVisibility[0].IsVisible=function() return false end
UnitManager.RequestOperation=function(_,op,params) record_operation(op,params) end
''')
    state = state_for(conn)
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert "TARGET_NOT_VISIBLE" in result
    assert conn.operations == []
    assert conn.previews == []
    assert state.pending_action_summary is None


@pytest.mark.parametrize('visibility', ['nil', 'error("unavailable")'])
def test_unknown_target_visibility_never_submits_or_reads_defender(visibility):
    conn = AttackConn()
    body = f'return {visibility}' if visibility == 'nil' else visibility
    conn.lua.execute(f'PlayersVisibility[0].IsVisible=function() {body} end;allowed.range=true')
    state = state_for(conn)
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert "TARGET_VISIBILITY_UNKNOWN" in result
    assert conn.operations == [] and conn.previews == []
    assert state.pending_action_summary is None


def test_target_leaving_sight_after_preview_is_rechecked_before_submission():
    conn = AttackConn()
    conn.lua.execute('''
allowed.range=true;visibilityReads=0
PlayersVisibility[0].IsVisible=function()
 visibilityReads=visibilityReads+1;return visibilityReads==1
end
''')
    state = state_for(conn)
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert "TARGET_NOT_VISIBLE" in result
    assert conn.previews == ['Ranged'] and conn.operations == []
    assert state.pending_action_summary is None


def test_native_civilian_entry_without_attack_consumption_confirms_once():
    conn = AttackConn()
    conn.lua.execute('''
unitInfo.RangedCombat=0;unitInfo.Range=0;enemyInfo.Combat=0
enemyInfo.UnitType='UNIT_MILITARY_ENGINEER'
UnitManager.RequestOperation=function(_,op,params)
    record_operation(op,params)
    x=params.x;y=params.y;moves=moves-1
end
''')
    result = asyncio.run(state_for(conn).attack_unit(8, 19, 16))
    assert "CIVILIAN_TILE_ENTERED" in result
    assert "origin_x:20|origin_y:16|moves_before:2" in result
    assert "Target fate unknown" in result
    assert len(conn.operations) == 1


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


class CandidateAttackConn(AttackConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
unit.GetName=function() return 'Warrior' end
unit.GetBuildCharges=function() return 0 end
unit.GetGreatPerson=function() return nil end
unit.GetMaxMoves=function() return 2 end
Map.GetPlotDistance=function(sx,sy,tx,ty)
    if tx==19 and ty==16 then return 1 end
    return 99
end
Map.GetUnitsAt=function(tx,ty)
    if tx~=19 or ty~=16 then return nil end
    return {Units=function()
        local seen=false
        return function() if not seen then seen=true;return enemy end end
    end}
end
''')


def test_one_move_cross_river_rainforest_path_deferred_to_next_turn_is_not_an_attack_candidate_or_submitted():
    conn = CandidateAttackConn()
    # Native path, rather than guessed terrain costs, proves the turn boundary.
    conn.lua.execute('unitInfo.RangedCombat=0;moves=1;arrivalTurn=2')
    state = state_for(conn)
    units = asyncio.run(state.get_units())
    assert len(units) == 1 and units[0].targets == []
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert result.startswith('Error: ATTACK_DEFERRED|'), result
    assert conn.operations == [] and conn.previews == []
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup', [
    'UnitManager.GetMoveToPathEx=nil',
    'UnitManager.GetMoveToPathEx=function() error("path unavailable") end',
    'UnitManager.GetMoveToPathEx=function() return nil end',
    'UnitManager.GetMoveToPathEx=function() return {} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={},turns={}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={16020},turns={1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={16020,16019},turns={1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={16020,16019},turns={1,"1"}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={16020,16019},turns={1,0/0}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={16020,16019},turns={1,math.huge}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={16020,16019},turns={2,1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={16020,16019},turns={0,1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={16020,16019},turns={2,2}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={16021,16019},turns={1,1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={16020,16018},turns={1,1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={[1]=16020,[3]=16019},turns={[1]=1,[3]=1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={16020,16019,extra=16018},turns={1,1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={16020,16019.5},turns={1,1}} end',
    'Map.GetPlotByIndex=function() return nil end',
    'UI.IsGameCoreBusy=function() return true end',
    'UI.IsGameCoreBusy=nil',
    'unit.HasMovedIntoZOC=nil',
    'unit.HasMovedIntoZOC=function() return nil end',
    'UnitManager.CanStartOperation=function() error("eligibility unavailable") end',
])
def test_unprovable_native_melee_path_is_neither_advertised_nor_submitted(setup):
    conn = CandidateAttackConn()
    conn.lua.execute('unitInfo.RangedCombat=0;' + setup)
    state = state_for(conn)
    assert asyncio.run(state.get_units())[0].targets == []
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert result.startswith('Error: ATTACK_PATH_UNKNOWN|'), result
    assert conn.operations == [] and conn.previews == []
    assert state.pending_action_summary is None


@pytest.mark.parametrize(('setup', 'code'), [
    ('movedIntoZOC=true', 'ZOC'),
    ('moves=0', 'NO_MOVES'),
    ('allowed.move=false', 'ATTACK_BLOCKED'),
    ('UnitManager.CanStartOperation=function() return 1 end', 'ATTACK_BLOCKED'),
])
def test_blocked_melee_paths_use_the_same_candidate_and_submission_guards(setup, code):
    conn = CandidateAttackConn()
    conn.lua.execute('unitInfo.RangedCombat=0;' + setup)
    state = state_for(conn)
    assert asyncio.run(state.get_units())[0].targets == []
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert result.startswith('Error: '+code+'|'), result
    assert conn.operations == [] and state.pending_action_summary is None


@pytest.mark.parametrize('moves', [1, 2])
def test_native_current_turn_melee_path_is_a_candidate_and_submits_exactly_once(moves):
    conn = CandidateAttackConn()
    conn.lua.execute(f'unitInfo.RangedCombat=0;moves={moves}')
    state = state_for(conn)
    assert asyncio.run(state.get_units())[0].targets == ['UNIT_SWEDEN_CAROLEAN@19,16(6hp)']
    assert conn.operations == []
    result = asyncio.run(state.attack_unit(8, 19, 16))
    assert 'MELEE_ATTACK|' in result and 'Native combat preview (Melee)' in result
    assert conn.operations == [('move', {'x': 19, 'y': 16, 'modifiers': 7})]
    assert state.pending_action_summary is None


def test_recorded_turn10_one_move_river_target_does_not_queue_an_attack():
    conn = CandidateAttackConn()
    conn.lua.execute('''
x,y,moves,attacks=18,23,1,1
unitInfo.RangedCombat=0
arrivalTurn=2
unit.GetDamage=function() return 8 end
Map.GetPlotDistance=function(_,_,tx,ty)
    if tx==17 and ty==23 then return 1 end
    return 99
end
Map.GetUnitsAt=function(tx,ty)
    if tx~=17 or ty~=23 then return nil end
    return {Units=function()
        local seen=false
        return function() if not seen then seen=true;return enemy end end
    end}
end
''')
    state = state_for(conn)
    assert asyncio.run(state.get_units())[0].targets == []
    result = asyncio.run(state.attack_unit(8, 17, 23))
    assert result.startswith('Error: ATTACK_DEFERRED|'), result
    assert conn.operations == [] and state.pending_action_summary is None
    assert (conn.lua.globals().attacks, conn.lua.globals().moves) == (1, 1)


@pytest.mark.parametrize('visibility', ['false', 'nil', 'error("visibility unknown")'])
def test_unit_candidate_board_never_exposes_a_hidden_or_unverified_unit(visibility):
    conn=CandidateAttackConn()
    result = visibility if visibility.startswith('error(') else 'return '+visibility
    conn.lua.execute('PlayersVisibility[0].IsUnitVisible=function() '+result+' end;unitInfo.RangedCombat=0')
    assert asyncio.run(state_for(conn).get_units())[0].targets==[]
    assert conn.operations==[]


def test_adjacent_ranged_target_still_requires_native_attack_eligibility():
    conn=CandidateAttackConn()
    conn.lua.execute('allowed.range=false')
    assert asyncio.run(state_for(conn).get_units())[0].targets==[]
    assert conn.operations==[]
