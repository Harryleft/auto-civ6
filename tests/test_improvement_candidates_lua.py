"""Improvement candidates use native build eligibility, including trait content."""

import asyncio

import pytest

from test_attack_mode_lua import CandidateAttackConn, state_for


def builder_conn(unit_type='UNIT_BUILDER'):
    conn = CandidateAttackConn()
    conn.lua.globals().builder_type = unit_type
    conn.lua.execute('''
unitInfo.UnitType=builder_type
unitInfo.Combat=0
unitInfo.RangedCombat=0
unit.GetBuildCharges=function() return 3 end
local improvements={
    {ImprovementType='IMPROVEMENT_FARM',Hash=101,Buildable=true},
    {ImprovementType='IMPROVEMENT_ZIGGURAT',Hash=102,Buildable=true,TraitType='TRAIT_CIVILIZATION_IMPROVEMENT_ZIGGURAT'},
    {ImprovementType='IMPROVEMENT_GREAT_WALL',Hash=103,Buildable=true,TraitType='TRAIT_CIVILIZATION_IMPROVEMENT_GREAT_WALL'},
    {ImprovementType='IMPROVEMENT_UNRESEARCHED',Hash=104,Buildable=true},
    {ImprovementType='IMPROVEMENT_UNKNOWN',Hash=105,Buildable=true},
    {ImprovementType='IMPROVEMENT_NOT_BUILDABLE',Hash=106,Buildable=false},
}
GameInfo.Improvements=setmetatable({}, {__call=function()
    local index=0
    return function() index=index+1;return improvements[index] end
end})
local originalPlot=Map.GetPlot
Map.GetPlot=function(px,py)
    local plot=originalPlot(px,py)
    plot.GetOwner=function() return 0 end
    plot.IsImprovementPillaged=function() return false end
    plot.GetImprovementType=function() return -1 end
    plot.GetFeatureType=function() return -1 end
    return plot
end
UnitOperationTypes.BUILD_IMPROVEMENT='improvement'
UnitOperationTypes.PARAM_IMPROVEMENT_TYPE='improvement_type'
UnitManager.CanStartOperation=function(_,operation,_,params)
    if operation~='improvement' then return false end
    local imp=params.improvement_type
    if imp==105 then error('native eligibility unavailable') end
    return imp==101 or imp==102
end
''')
    return conn


def test_own_trait_improvement_and_common_allowed_improvement_are_visible_without_submitting():
    conn = builder_conn()
    units = asyncio.run(state_for(conn).get_units())
    assert len(units) == 1
    assert units[0].valid_improvements == ['IMPROVEMENT_FARM', 'IMPROVEMENT_ZIGGURAT']
    assert conn.operations == []


def test_engineer_trait_candidates_also_follow_native_unit_eligibility():
    conn = builder_conn('UNIT_MILITARY_ENGINEER')
    units = asyncio.run(state_for(conn).get_units())
    assert units[0].valid_improvements == ['IMPROVEMENT_FARM', 'IMPROVEMENT_ZIGGURAT']
    assert conn.operations == []


@pytest.mark.parametrize('unit_type', ['UNIT_BUILDER', 'UNIT_MILITARY_ENGINEER'])
@pytest.mark.parametrize('outcome', ['nil', '1', '"true"', 'false', 'error("native eligibility unavailable")'])
def test_unconfirmed_native_improvement_eligibility_is_not_advertised(unit_type, outcome):
    conn = builder_conn(unit_type)
    result = outcome if outcome.startswith('error(') else 'return ' + outcome
    conn.lua.execute('UnitManager.CanStartOperation=function() '+result+' end')
    assert asyncio.run(state_for(conn).get_units())[0].valid_improvements == []
    assert conn.operations == []


@pytest.mark.parametrize('unit_type', ['UNIT_BUILDER', 'UNIT_MILITARY_ENGINEER'])
def test_missing_native_improvement_eligibility_is_unknown_and_query_remains_read_only(unit_type):
    conn = builder_conn(unit_type)
    conn.lua.execute('UnitManager.CanStartOperation=nil')
    assert asyncio.run(state_for(conn).get_units())[0].valid_improvements == []
    assert conn.operations == []


def test_rejected_improvement_hint_includes_own_native_allowed_trait_content_without_submitting():
    from civ_mcp.game_state import GameState
    from test_tile_actions import TileActionConn

    conn = TileActionConn()
    conn.lua.execute("""
local improvements={
 {Index=6,ImprovementType='IMPROVEMENT_FARM',Hash=106,Buildable=true},
 {Index=7,ImprovementType='IMPROVEMENT_ZIGGURAT',Hash=107,Buildable=true,TraitType='TRAIT_CIVILIZATION_IMPROVEMENT_ZIGGURAT'},
 {Index=8,ImprovementType='IMPROVEMENT_GREAT_WALL',Hash=108,Buildable=true,TraitType='TRAIT_CIVILIZATION_IMPROVEMENT_GREAT_WALL'},
 {Index=9,ImprovementType='IMPROVEMENT_UNKNOWN',Hash=109,Buildable=true},
}
GameInfo.Improvements=rows(improvements,{IMPROVEMENT_GREAT_WALL=improvements[3]})
UnitManager.CanStartOperation=function(unit,op,unknown,params,check)
 assert(op==31 and unknown==nil and check==true)
 assert(params.x==19 and params.y==17)
 if params.improvement==109 then error('eligibility unavailable') end
 return params.improvement==106 or params.improvement==107
end
""")
    result = asyncio.run(GameState(conn).improve_tile(196608, 'IMPROVEMENT_GREAT_WALL'))
    assert result.startswith('Error: TILE_ACTION_REFUSED|'), result
    assert 'IMPROVEMENT_FARM, IMPROVEMENT_ZIGGURAT' in result
    assert conn.lua.eval('#submissions') == 0
