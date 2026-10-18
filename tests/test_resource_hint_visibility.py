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
            report = await game.get_builder_tasks()
            assert report.resource_visibility_known is True
            return cities[0], report.tasks
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
    assert city.unimproved_resources == ["OIL@1,0"]
    assert [(t.x, t.improvement, t.resource, t.priority) for t in tasks] == [
        (1, "IMPROVEMENT_OIL_WELL", "OIL", "urgent"),
        (2, "IMPROVEMENT_FARM", "", "normal"),
        (3, "REPAIR", "MINE", "urgent"),
    ]
    assert conn.lua.globals().native_requests == 0


@pytest.mark.parametrize("resource_rows,repair_rows", [
    ("SILK@1,-2;SILK@-3,4;BANANAS@0,2", "MINE@-1,-2;FARM@3,0"),
    ("SILK@1,-2,SILK@-3,4,BANANAS@0,2", "MINE@-1,-2,FARM@3,0"),
    ("SILK@1,-2,99,SILK@-3,4,BANANAS@0,2,INCOMPLETE@1", "MINE@-1,-2,99,FARM@3,0"),
])
def test_city_hint_protocol_preserves_complete_coordinates_without_phantom_resources(
    resource_rows, repair_rows,
):
    class HintConn:
        async def execute_write(self, _source):
            return [
                "65536|Kish|0,0|3|2|2|2|2|2|2|5|1|3|nothing|0",
                f"CITYTILES|65536|{resource_rows}|{repair_rows}",
            ]

    cities, _ = asyncio.run(GameState(HintConn()).get_cities())
    assert cities[0].unimproved_resources == ["SILK@1,-2", "SILK@-3,4", "BANANAS@0,2"]
    assert cities[0].pillaged_improvements == ["MINE@-1,-2", "FARM@3,0"]
    text = narrate_cities(cities)
    assert "Needs builder: SILK (1,-2), SILK (-3,4), BANANAS (0,2)" in text
    assert "PILLAGED TILES: MINE (-1,-2), FARM (3,0)" in text


def test_city_native_hints_exclude_centers_placed_districts_and_unknown_sites():
    conn = ResourceConn()
    conn.lua.execute('''
visible=true
local function copy(p) local q={}; for k,v in pairs(p) do q[k]=v end; return q end
plots[0]=copy(plots[1]) -- center: resource remains, but cannot receive an improvement
plots[2].GetResourceType=function() return 7 end -- ordinary, same resource twice
plots[-1]=copy(plots[1])
plots[-1].GetDistrictType=function() return 4 end -- placed, not necessarily complete
plots[-2]=copy(plots[1])
plots[-2].GetDistrictType=function() return nil end
plots[-3]=copy(plots[1])
plots[-3].GetDistrictType=function() error("native unavailable") end
''')
    cities, _ = asyncio.run(GameState(conn).get_cities())
    assert cities[0].unimproved_resources == ["OIL@1,0", "OIL@2,0"]
    assert cities[0].pillaged_improvements == ["MINE@3,0"]
    assert "Needs builder: OIL (1,0), OIL (2,0)" in narrate_cities(cities)
    assert conn.lua.globals().native_requests == 0


def test_city_native_pillage_hints_keep_distinct_sites_and_exclude_reserved_tiles():
    conn = ResourceConn()
    conn.lua.execute('''
local function copy(p) local q={}; for k,v in pairs(p) do q[k]=v end; return q end
plots[-1]=copy(plots[3])
plots[0]=copy(plots[3])
plots[1]=copy(plots[3]); plots[1].GetDistrictType=function() return 4 end
plots[2]=copy(plots[3]); plots[2].GetDistrictType=function() return nil end
''')
    cities, _ = asyncio.run(GameState(conn).get_cities())
    assert cities[0].pillaged_improvements == ["MINE@-1,0", "MINE@3,0"]
    assert "PILLAGED TILES: MINE (-1,0), MINE (3,0)" in narrate_cities(cities)
    assert conn.lua.globals().native_requests == 0


def test_city_resource_visibility_failure_keeps_safe_visible_and_repair_hints():
    conn = ResourceConn()
    conn.lua.execute('''
resource_read.IsResourceVisible=function(_,idx)
    if idx==7 then error("unavailable") end
    return idx==8
end
GameInfo.Resources[8]={Index=8,ResourceType="RESOURCE_CATTLE",ResourceClassType="RESOURCECLASS_BONUS"}
plots[2].GetResourceType=function() return 8 end
''')
    cities, _ = asyncio.run(GameState(conn).get_cities())
    assert cities[0].resource_visibility_known is False
    assert cities[0].unimproved_resources == ["CATTLE@2,0"]
    assert cities[0].pillaged_improvements == ["MINE@3,0"]
    text = narrate_cities(cities)
    assert "native resource visibility unknown" in text
    assert "CATTLE (2,0)" in text and "MINE (3,0)" in text and "OIL" not in text
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
    conn.lua.execute("""
GameInfo.Units=rows({{Index=0,Hash=1000,UnitType="UNIT_BUILDER"}})
local builder={GetType=function() return 0 end,GetBuildCharges=function() return 3 end,
    GetX=function() return 0 end,GetY=function() return 0 end,
    GetID=function() return 99 end,GetMovesRemaining=function() return 2 end}
Players[0].GetUnits=function() return {Members=function()
    local delivered=false
    return function() if not delivered then delivered=true; return 1,builder end end
end} end
""")
    report = asyncio.run(GameState(conn).get_builder_tasks())
    assert report.resource_visibility_known is False
    assert [(task.x, task.improvement, task.resource) for task in report.tasks] == [
        (1, "IMPROVEMENT_MINE", ""),
        (2, "IMPROVEMENT_FARM", ""),
        (3, "REPAIR", "MINE"),
    ]
    assert [(b.unit_id, b.charges, b.moves) for b in report.builders] == [(99, 3, 2)]
    assert conn.lua.globals().native_requests == 0


def builder_tool_text(conn):
    from types import SimpleNamespace
    from civ_mcp import server

    class Logger:
        async def log_tool_call(self, *_args):
            pass

    app = server.AppContext(game=GameState(conn), logger=Logger())
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))
    return asyncio.run(server.get_builder_tasks(ctx))


def test_mcp_partial_advice_displays_warning_safe_tasks_and_no_builders_together():
    conn = ResourceConn()
    conn.lua.execute('resource_read.IsResourceVisible=function() error("unavailable") end')
    text = builder_tool_text(conn)
    assert "Resource advice incomplete: native resource visibility unknown" in text
    assert "No builders with charges available." in text
    assert "(1,0): build MINE" in text and "(2,0): build FARM" in text
    assert "(3,0): repair MINE" in text
    assert "OIL" not in text
    assert conn.lua.globals().native_requests == 0


def test_mcp_empty_partial_advice_never_claims_no_tiles_need_improvement():
    conn = ResourceConn()
    conn.lua.execute('''
resource_read.IsResourceVisible=nil
plots[2]=nil; plots[3]=nil
plots[1].IsHills=function() return false end
plots[1].GetTerrainType=function() return 1 end
GameInfo.Terrains[1]={TerrainType="TERRAIN_DESERT"}
''')
    text = builder_tool_text(conn)
    assert "Resource advice incomplete: native resource visibility unknown" in text
    assert "No confirmed improvement candidates identified; resource advice is incomplete." in text
    assert "No builders with charges available." in text
    assert "No tiles need improvement" not in text
    assert "OIL" not in text
    assert conn.lua.globals().native_requests == 0


def test_one_failed_resource_does_not_discard_an_independently_visible_resource():
    conn = ResourceConn()
    conn.lua.execute('''
resource_read.IsResourceVisible=function(_,idx)
    if idx==7 then error("unavailable") end
    return idx==8
end
GameInfo.Resources[8]={Index=8,ResourceType="RESOURCE_CATTLE",ResourceClassType="RESOURCECLASS_BONUS"}
plots[2].GetResourceType=function() return 8 end
''')
    report = asyncio.run(GameState(conn).get_builder_tasks())
    assert report.resource_visibility_known is False
    assert [(t.x, t.improvement, t.resource) for t in report.tasks] == [
        (1, "IMPROVEMENT_MINE", ""),
        (2, "IMPROVEMENT_PASTURE", "CATTLE"),
        (3, "REPAIR", "MINE"),
    ]
    assert conn.lua.globals().native_requests == 0


def test_legacy_resource_rows_remain_readable_with_explicit_unknown_completeness():
    class LegacyConn:
        async def execute_write(self, _source):
            return ["TASK|normal|1,0|IMPROVEMENT_MINE||none|Kish|99|1",
                    "BUILDER|99|99|0,0|3|2.0"]

    report = asyncio.run(GameState(LegacyConn()).get_builder_tasks())
    assert report.resource_visibility_known is None
    assert [(t.x, t.improvement) for t in report.tasks] == [(1, "IMPROVEMENT_MINE")]
    assert [b.unit_id for b in report.builders] == [99]
    text = builder_tool_text(LegacyConn())
    assert "Resource advice incomplete: native resource visibility unknown" in text
    assert "id:99" in text and "(1,0): build MINE" in text
