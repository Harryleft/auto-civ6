"""Wonder candidates come only from the native available placement array."""
import asyncio

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_wonder_advisor
from test_district_advisor_native import DistrictAdvisorConn


def connection():
    conn = DistrictAdvisorConn()
    conn.lua.execute('''
GameInfo.Buildings={BUILDING_EIFFEL_TOWER={Hash=11,IsWonder=true}}
GameInfo.Terrains[0].TerrainType="TERRAIN_COAST"
GameInfo.Terrains[1].TerrainType="TERRAIN_PLAINS"
CityOperationTypes.PARAM_BUILDING_TYPE=3
CityManager.GetOperationTargets=function(c,op,params)assert(params[3]==11);return targets end
for _,p in pairs(plots)do p.IsCoastalLand=function()return false end end
''')
    return conn


def read(conn):
    return asyncio.run(GameState(conn).get_wonder_advisor(65536, 'BUILDING_EIFFEL_TOWER'))


def test_blocked_and_unrelated_arrays_never_become_wonder_placement_candidates():
    result = read(connection())
    assert [(p.x, p.y) for p in result] == [(36, 29)]


def test_all_native_candidates_are_unranked_without_fixed_weight_recommendation():
    conn=connection()
    conn.lua.execute('''
local available={}
for i=1,18 do
 local p=plot(i,29,false);p.IsCoastalLand=function()return false end
 plots[1800+i]=p;available[i]=1800+i
end
targets={[1933683541]=available}
''')
    result=read(conn)
    assert len(result)==18
    text=narrate_wonder_advisor(result,'BUILDING_EIFFEL_TOWER')
    assert 'Recommended' not in text and 'score:' not in text
    assert 'unranked' in text
    assert conn.lua.eval('native_requests')==0


def test_native_failure_is_preserved_instead_of_becoming_no_candidates():
    conn=connection();conn.lua.execute('can_produce=false')
    assert read(conn).startswith('ERR:CANNOT_PRODUCE')


def test_hidden_resource_never_appears_in_wonder_displacement_advice():
    conn=connection()
    conn.lua.execute('''
plots[1776].GetResourceType=function()return 7 end
GameInfo.Resources[7]={ResourceType='RESOURCE_OIL'}
Players[0].GetResources=function()return {IsResourceVisible=function()return false end}end
''')
    assert read(conn)[0].resource=='none'
