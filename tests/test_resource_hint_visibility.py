"""Resource advice through public queries against a native Lua 5.1 boundary."""

import asyncio

from lupa.lua51 import LuaRuntime
import pytest

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_cities


class ResourceConn:
    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.execute('''
function rows(items)
    local lookup={}
    for _,row in ipairs(items) do
        if row.Index then lookup[row.Index]=row end
        for _,key in ipairs({"ResourceType","ImprovementType","DistrictType"}) do
            if row[key] then lookup[row[key]]=row end
        end
    end
    return setmetatable(lookup,{__call=function()
        local i=0
        return function() i=i+1; return items[i] end
    end})
end
local function members(items)
    return {Members=function() local i=0; return function() i=i+1; if items[i] then return i,items[i] end end end}
end
native_requests=0
function native_request() native_requests=native_requests+1; error("query attempted a native write") end
CityManager={RequestCommand=native_request}
UnitManager={RequestOperation=native_request,RequestCommand=native_request}
Game={GetLocalPlayer=function() return 0 end}
Locale={Lookup=function(text) return text end}
GameInfo={
    Units=rows({}),Buildings=rows({}),Projects=rows({}),
    Districts=rows({{Index=0,Hash=100,DistrictType="DISTRICT_CITY_CENTER"}}),
    Resources=rows({{Index=7,ResourceType="RESOURCE_OIL",ResourceClassType="RESOURCECLASS_STRATEGIC"}}),
    Improvements=rows({{Index=1,ImprovementType="IMPROVEMENT_MINE"},{Index=2,ImprovementType="IMPROVEMENT_OIL_WELL"}}),
    Technologies=rows({}),Features=rows({}),
    Terrains={[0]={TerrainType="TERRAIN_GRASS"}}
}
visible=false
resource_read={IsResourceVisible=function(_,idx) assert(idx==7); return visible end}
local growth={GetAmenitiesNeeded=function() return 1 end,GetAmenities=function() return 0 end,
    GetHousing=function() return 5 end,GetTurnsUntilGrowth=function() return 3 end,
    GetFoodSurplus=function() return 2 end,GetFood=function() return 4 end,GetGrowthThreshold=function() return 10 end}
city={GetID=function() return 65536 end,GetName=function() return "Kish" end,
    GetX=function() return 0 end,GetY=function() return 0 end,
    GetOwner=function() return 0 end,GetPopulation=function() return 3 end,
    GetYield=function() return 2 end,GetGrowth=function() return growth end,
    GetBuildQueue=function() return {GetSize=function() return 0 end} end,
    GetDistricts=function() return members({}) end,
    GetBuildings=function() return {HasBuilding=function() return false end} end,
    GetCulturalIdentity=function() return nil end}
Players={[0]={GetResources=function() return resource_read end,
    GetCities=function() return members({city}) end,GetUnits=function() return members({}) end,
    GetTechs=function() return {HasTech=function() return true end} end}}
local function plot(x,resource,improvement)
    return {GetOwner=function() return 0 end,GetResourceType=function() return resource end,
        GetImprovementType=function() return improvement end,IsImprovementPillaged=function() return true end,
        IsWater=function() return false end,IsMountain=function() return false end,
        GetDistrictType=function() return -1 end,GetTerrainType=function() return 0 end,
        GetFeatureType=function() return -1 end,IsHills=function() return x==1 end}
end
plots={[1]=plot(1,7,-1),[2]=plot(2,-1,-1),[3]=plot(3,-1,1)}
Map={GetPlot=function(x,y) if y==0 then return plots[x] end end,
    GetUnitsAt=function() return nil end,GetPlotDistance=function() return 1 end}
''')

    async def execute_read(self, source):
        self.rows.clear()
        self.lua.execute(source)
        return [row for row in self.rows if row != "---END---"]

    async def execute_write(self, source):
        # This transport selects InGame; all native mutation entry points trap.
        return await self.execute_read(source)

    async def disconnect(self):
        pass

    def advice(self):
        async def query():
            game = GameState(self)
            cities, _ = await game.get_cities()
            tasks, _ = await game.get_builder_tasks()
            return cities[0], tasks
        return asyncio.run(query())


def test_hidden_resource_uses_ordinary_terrain_then_native_reveal_changes_both_queries():
    conn = ResourceConn()
    city, tasks = conn.advice()
    assert city.unimproved_resources == []
    assert [(t.x, t.improvement, t.resource, t.priority) for t in tasks] == [
        (1, "IMPROVEMENT_MINE", "", "normal"),
        (2, "IMPROVEMENT_FARM", "", "normal"),
        (3, "REPAIR", "MINE", "urgent"),
    ]
    conn.lua.execute('visible=true')
    city, tasks = conn.advice()
    assert "OIL@1" in city.unimproved_resources
    assert [(t.x, t.improvement, t.resource, t.priority) for t in tasks] == [
        (1, "IMPROVEMENT_OIL_WELL", "OIL", "urgent"),
        (2, "IMPROVEMENT_FARM", "", "normal"),
        (3, "REPAIR", "MINE", "urgent"),
    ]
    assert conn.lua.globals().native_requests == 0


@pytest.mark.parametrize("patch", [
    'resource_read.IsResourceVisible=nil',
    'Players[0].GetResources=nil',
    'resource_read.IsResourceVisible=function() error("native unavailable") end',
    'resource_read.IsResourceVisible=function() return nil end',
    'resource_read.IsResourceVisible=function() return "true" end',
])
def test_unknown_resource_visibility_is_explicit_not_secret_advice_or_empty_success(patch):
    conn = ResourceConn()
    conn.lua.execute(patch)
    cities, _ = asyncio.run(GameState(conn).get_cities())
    assert cities[0].unimproved_resources == []
    assert cities[0].resource_visibility_known is False
    assert "native resource visibility unknown" in narrate_cities(cities)
    assert "RESOURCE_OIL" not in "\n".join(conn.rows)
    with pytest.raises(ValueError, match="resource visibility.*unknown"):
        asyncio.run(GameState(conn).get_builder_tasks())
    assert conn.lua.globals().native_requests == 0
