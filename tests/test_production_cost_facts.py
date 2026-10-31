"""Native current production is not a static Cost row or an invented zero."""
import asyncio

import pytest

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_city_production
from test_purchase_quotes import QuoteConn


def query(setup=''):
    conn=QuoteConn();conn.lua.execute('''
queue={CanProduce=function()return true end,
 GetTurnsLeft=function()return 4 end,
 GetUnitCost=function(_,index)assert(index==11);return 68.75 end,
 GetUnitProgress=function(_,index)assert(index==11);return 5.25 end,
 GetProductionCost=function()error('Not the production-panel API')end}
city.GetBuildQueue=function()return queue end
'''+setup)
    value=asyncio.run(GameState(conn).list_city_production(65537,'UNIT','UNIT_WARRIOR'))[0]
    return value,conn


def test_native_cost_progress_and_static_base_are_separate():
    item,conn=query()
    assert item.cost==68.75 and item.production_progress==5.25 and item.base_cost==40
    assert item.turns==4 and item.can_produce is True
    text=narrate_city_production([item])
    assert 'production 68.75'in text and 'invested 5.25'in text and 'base 40'in text
    assert conn.lua.eval('#submissions')==0


def test_native_getter_failure_is_unknown_while_production_eligibility_stays_independent():
    item,conn=query("queue.GetUnitCost=function()error('unavailable')end;queue.GetTurnsLeft=function()error('unavailable')end")
    assert item.cost is None and item.turns is None and item.base_cost==40
    assert item.can_produce is True
    text=narrate_city_production([item])
    assert 'production unknown'in text and 'turns unknown'in text and 'base 40'in text
    assert conn.lua.eval('#submissions')==0


@pytest.mark.parametrize('value',['0/0','math.huge','true','-1'])
def test_invalid_native_numeric_fields_are_unknown(value):
    item,_=query(f'queue.GetUnitCost=function()return {value} end;queue.GetUnitProgress=function()return {value} end;queue.GetTurnsLeft=function()return {value} end')
    assert item.cost is None and item.production_progress is None and item.turns is None


def test_native_zero_is_preserved_and_not_dropped():
    item,_=query('queue.GetUnitCost=function()return 0 end;queue.GetUnitProgress=function()return 0 end;queue.GetTurnsLeft=function()return 0 end')
    assert item.cost==0 and item.production_progress==0 and item.turns==0
    assert 'production 0, 0 turns'in narrate_city_production([item])


def test_native_category_cost_and_progress_are_used_for_district_project_and_repair():
    conn=QuoteConn();conn.lua.execute('''
local district={Index=9,Hash=9001,DistrictType='DISTRICT_CAMPUS',Cost=80}
local project={Index=8,Hash=8001,ProjectType='PROJECT_TEST',Cost=100}
GameInfo.Districts=rows({district});GameInfo.Districts[9]=district
GameInfo.Projects=rows({project});GameInfo.Projects[8]=project
CityOperationTypes.PARAM_DISTRICT_TYPE='district';CityOperationTypes.PARAM_X='x';CityOperationTypes.PARAM_Y='y'
city.GetBuildQueue=function()return{
 CanProduce=function()return true end,GetProductionCost=function()error('wrong API')end,GetTurnsLeft=function()return 2 end,
 GetUnitCost=function()return 60 end,GetUnitProgress=function()return 0 end,
 GetBuildingCost=function(_,i)assert(i==21);return 75.5 end,GetBuildingProgress=function()return 3.25 end,
 GetDistrictCost=function(_,i)assert(i==9);return 140.75 end,GetDistrictProgress=function()return 120.125 end,
 GetProjectCost=function(_,i)assert(i==8);return 500.25 end,GetProjectProgress=function()return 15.125 end}end
city.GetDistricts=function()return{Members=function()return ipairs({{IsPillaged=function()return true end,GetType=function()return 9 end,GetX=function()return 11 end,GetY=function()return 12 end}})end}end
''')
    options=asyncio.run(GameState(conn).list_city_production(65537))
    district=next(o for o in options if o.category=='DISTRICT'and not o.is_repair)
    repair=next(o for o in options if o.category=='DISTRICT'and o.is_repair)
    project=next(o for o in options if o.category=='PROJECT')
    building=next(o for o in options if o.category=='BUILDING')
    assert district.cost==repair.cost==140.75 and repair.production_progress==120.125
    assert (repair.repair_x,repair.repair_y)==(11,12) and repair.base_cost==80
    assert project.cost==500.25 and project.production_progress==15.125 and project.base_cost==100
    assert building.cost==75.5 and building.production_progress==3.25
    assert conn.lua.eval('#submissions')==0
