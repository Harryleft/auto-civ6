"""District advice through GameState and the native Lua 5.1 query boundary."""

import asyncio

from lupa.lua51 import LuaRuntime
import pytest

from civ_mcp.game_state import GameState


class DistrictAdvisorConn:
    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.execute('''
native_requests=0
function native_request()
 native_requests=native_requests+1
 error("advisor attempted a native mutation")
end
Game={GetLocalPlayer=function() return 0 end}
Locale={Lookup=function(text) return text end}
CityOperationTypes={BUILD=1,PARAM_DISTRICT_TYPE=2}
CityOperationResults={PLOTS=1933683541,BLOCKED_PLOTS=745888826}
can_produce=true
local queue={CanProduce=function(_,hash,check)
 assert((hash==10 or hash==11) and check==true)
 return can_produce
end,CreateIncompleteDistrict=native_request,Add=native_request}
city={GetID=function() return 65536 end,GetBuildQueue=function() return queue end}
targets={[1933683541]={1776},[745888826]={1659},[123]={1700}}
CityManager={GetCity=function(owner,index) assert(owner==0 and index==0); return city end,
 GetOperationTargets=function(c,op,params)
  assert(c==city and op==1 and (params[2]==10 or params[2]==11))
  return targets
 end,RequestOperation=native_request,RequestCommand=native_request}
UnitManager={RequestOperation=native_request,RequestCommand=native_request}
Players={[0]={GetTechs=function() return {HasTech=function() return true end} end}}
GameInfo={Districts={DISTRICT_HARBOR={Hash=10,Index=2},DISTRICT_CAMPUS={Hash=11,Index=1}},
 Yields={YIELD_FOOD={Index=0},YIELD_PRODUCTION={Index=1},YIELD_GOLD={Index=2},YIELD_SCIENCE={Index=3},YIELD_CULTURE={Index=4},YIELD_FAITH={Index=5}},
 Features={},Improvements={},Technologies={},Resources={[0]={ResourceType="RESOURCE_FISH"}},
 Terrains={[0]={Name="Coast"},[1]={Name="Plains"}}}
function plot(x,y,water,mountain,impassable)
 return {GetX=function() return x end,GetY=function() return y end,
 IsWater=function() return water end,IsMountain=function() return mountain or false end,
 IsImpassable=function() return impassable or false end,IsRiver=function() return false end,
 GetTerrainType=function() return water and 0 or 1 end,IsHills=function() return false end,
 GetFeatureType=function() return -1 end,GetDistrictType=function() return -1 end,
 GetImprovementType=function() return -1 end,GetResourceType=function() return -1 end,
 GetWonderType=function() return -1 end,
 GetAdjacencyYield=function(_,owner,cid,district,yield)
  assert(owner==0 and cid==65536)
  if x==36 and y==29 and district==2 and yield==2 then return 1 end
  if x==35 and y==28 and district==1 and yield==3 then return 1 end
  return 0
 end}
end
plots={[1776]=plot(36,29,true),[1659]=plot(39,27,false,true),
 [1700]=plot(40,28,false),[1701]=plot(35,28,false)}
local fish=plot(37,29,true)
fish.GetResourceType=function() return 0 end
local mountain=plot(35,27,false,true)
Map={GetPlotByIndex=function(index) return plots[index] end,
 GetAdjacentPlot=function(x,y,direction)
  if x==36 and y==29 and direction==0 then return fish end
  if x==35 and y==28 and direction==0 then return mountain end
 end}
''')

    async def execute_write(self, source):
        # InGame selection uses execute_write even for these pure queries.
        self.rows.clear()
        self.lua.execute(source)
        return [row for row in self.rows if row != "---END---"]

    def advice(self, district="DISTRICT_HARBOR"):
        return asyncio.run(GameState(self).get_district_advisor(65536, district))


def native_adjacency(conn, science=4.5):
    conn.lua.execute(f'''
GameInfo.Districts.DISTRICT_CAMPUS.Index=1
GameInfo.Districts.DISTRICT_HARBOR.Index=2
GameInfo.Yields={{YIELD_FOOD={{Index=0}},YIELD_PRODUCTION={{Index=1}},YIELD_GOLD={{Index=2}},
 YIELD_SCIENCE={{Index=3}},YIELD_CULTURE={{Index=4}},YIELD_FAITH={{Index=5}}}}
for _,p in pairs(plots)do
 p.GetAdjacencyYield=function(_,owner,cid,district,yield)
  assert(owner==0 and cid==65536 and (district==1 or district==2))
  return yield==3 and {science} or 0
 end
end
''')


def test_native_adjacency_overrides_the_old_incomplete_terrain_formula():
    conn=DistrictAdvisorConn();native_adjacency(conn)
    conn.lua.execute('targets={[1933683541]={1701}}')
    placement=conn.advice('DISTRICT_CAMPUS')[0]
    assert placement.adjacency['science']==4.5
    assert placement.total_adjacency==4.5
    assert conn.lua.eval('native_requests')==0


def test_all_native_placements_are_returned_instead_of_silent_top_ten():
    conn=DistrictAdvisorConn()
    conn.lua.execute('''
local list={}
for i=1,12 do list[i]=1800+i;plots[1800+i]=plot(i,29,false)end
targets={[1933683541]=list}
''')
    native_adjacency(conn)
    assert len(conn.advice('DISTRICT_CAMPUS'))==12


def test_unavailable_native_yield_is_unknown_not_a_zero_or_handwritten_fallback():
    conn=DistrictAdvisorConn();native_adjacency(conn)
    conn.lua.execute("targets={[1933683541]={1701}};plots[1701].GetAdjacencyYield=function()error('unavailable')end")
    placement=conn.advice('DISTRICT_CAMPUS')[0]
    assert placement.total_adjacency is None
    assert placement.adjacency['science'] is None


def test_harbor_lists_native_water_plot_and_native_adjacency():
    conn = DistrictAdvisorConn()
    placements = conn.advice()
    assert [(p.x, p.y, p.adjacency, p.total_adjacency, p.terrain_desc) for p in placements] == [
        (36, 29, {"gold": 1}, 1, "Coast")
    ]
    assert conn.lua.eval("native_requests") == 0


def test_native_available_list_owns_terrain_legality_even_for_modded_rules():
    conn = DistrictAdvisorConn()
    conn.lua.execute('''
plots[1777]=plot(37,29,true)
plots[1778]=plot(38,29,true,false,true)
targets={[1933683541]={1776,1701,1659,1778},[745888826]={1777},[123]={1700}}
''')
    placements = conn.advice()
    assert [(p.x, p.y) for p in placements] == [(36, 29), (35, 28), (39, 27), (38, 29)]
    assert conn.lua.eval("native_requests") == 0


def test_land_district_retains_native_candidate_and_native_yield():
    conn = DistrictAdvisorConn()
    conn.lua.execute("targets={[1933683541]={1701},[745888826]={1776},[123]={1700}}")
    placements = conn.advice("DISTRICT_CAMPUS")
    assert [(p.x, p.y, p.adjacency, p.total_adjacency, p.terrain_desc) for p in placements] == [
        (35, 28, {"science": 1}, 1, "Plains")
    ]
    assert conn.lua.eval("native_requests") == 0


@pytest.mark.parametrize("targets,expected", [
    ("nil", "ERR:NO_TARGETS|No valid placement targets found"),
    ("{}", "ERR:NO_TILES|No valid placement tiles found"),
    ("{[745888826]={1776},[123]={1700}}", "ERR:NO_TILES|No valid placement tiles found"),
    ("{[1933683541]={}}", "ERR:NO_TILES|No valid placement tiles found"),
    ("{[1933683541]=false}", "ERR:TARGETS_UNKNOWN|Native placement plots are unavailable"),
    ("{[1933683541]='unknown'}", "ERR:TARGETS_UNKNOWN|Native placement plots are unavailable"),
    ("{[1933683541]={9999}}", "ERR:TARGETS_UNKNOWN|A native placement plot could not be read"),
])
def test_missing_or_invalid_targets_do_not_fabricate_candidates(targets, expected):
    conn = DistrictAdvisorConn()
    conn.lua.execute(f"targets={targets}")
    assert conn.advice() == expected
    assert conn.lua.eval("native_requests") == 0


@pytest.mark.parametrize("setup,expected", [
    ("city=nil", "ERR:CITY_NOT_FOUND"),
    ("city.GetID=function() return 131072 end", "ERR:CITY_ID_CHANGED"),
    ("GameInfo.Districts.DISTRICT_HARBOR=nil", "ERR:DISTRICT_NOT_FOUND|DISTRICT_HARBOR"),
    ("can_produce=false", "ERR:CANNOT_PRODUCE|DISTRICT_HARBOR cannot be produced in this city"),
])
def test_city_and_build_eligibility_remain_required(setup, expected):
    conn = DistrictAdvisorConn()
    conn.lua.execute(setup)
    assert conn.advice() == expected
    assert conn.lua.eval("native_requests") == 0


def test_native_fraction_negative_food_and_unknown_are_preserved_in_display():
    from civ_mcp.narrate import narrate_district_advisor
    conn = DistrictAdvisorConn()
    conn.lua.execute("targets={[1933683541]={1701}};plots[1701].GetAdjacencyYield=function(_,owner,cid,district,yield) if yield==0 then return -0.5 elseif yield==3 then return 4.5 elseif yield==2 then return nil else return 0 end end")
    placements = conn.advice('DISTRICT_CAMPUS')
    assert placements[0].adjacency == {'science': 4.5, 'gold': None, 'food': -0.5}
    text = narrate_district_advisor(placements, 'DISTRICT_CAMPUS')
    assert 'gold unknown' in text and '4.5 science' in text and '-0.5 food' in text
    assert 'adjacency: unknown' in text and 'unranked' in text


def test_invalid_district_identifier_never_reaches_native_query():
    conn = DistrictAdvisorConn()
    with pytest.raises(ValueError, match='DISTRICT_'):
        conn.advice('DISTRICT_CAMPUS";native_request();--')
    assert conn.lua.eval('native_requests') == 0
