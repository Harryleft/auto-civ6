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
GameInfo={Districts={DISTRICT_HARBOR={Hash=10},DISTRICT_CAMPUS={Hash=11}},
 Features={},Improvements={},Technologies={},Resources={[0]={ResourceType="RESOURCE_FISH"}},
 Terrains={[0]={Name="Coast"},[1]={Name="Plains"}}}
function plot(x,y,water,mountain,impassable)
 return {GetX=function() return x end,GetY=function() return y end,
 IsWater=function() return water end,IsMountain=function() return mountain or false end,
 IsImpassable=function() return impassable or false end,IsRiver=function() return false end,
 GetTerrainType=function() return water and 0 or 1 end,IsHills=function() return false end,
 GetFeatureType=function() return -1 end,GetDistrictType=function() return -1 end,
 GetImprovementType=function() return -1 end,GetResourceType=function() return -1 end,
 GetWonderType=function() return -1 end}
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


def test_harbor_lists_native_water_plot_and_existing_adjacency_estimate():
    conn = DistrictAdvisorConn()
    placements = conn.advice()
    assert [(p.x, p.y, p.adjacency, p.total_adjacency, p.terrain_desc) for p in placements] == [
        (36, 29, {"gold": 1}, 1, "Coast")
    ]
    assert conn.lua.eval("native_requests") == 0


def test_only_native_available_plots_are_offered_with_existing_terrain_guards():
    conn = DistrictAdvisorConn()
    conn.lua.execute('''
plots[1777]=plot(37,29,true)
plots[1778]=plot(38,29,true,false,true)
targets={[1933683541]={1776,1701,1659,1778},[745888826]={1777},[123]={1700}}
''')
    placements = conn.advice()
    assert [(p.x, p.y) for p in placements] == [(36, 29), (35, 28)]
    assert conn.lua.eval("native_requests") == 0


def test_land_district_retains_native_candidate_and_existing_estimate():
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
    ("{[1933683541]=false}", "ERR:NO_TILES|No valid placement tiles found"),
    ("{[1933683541]='unknown'}", "ERR:NO_TILES|No valid placement tiles found"),
    ("{[1933683541]={9999}}", []),
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
