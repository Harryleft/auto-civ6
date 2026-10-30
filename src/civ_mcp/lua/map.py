"""Map domain — Lua builders and parsers."""

from __future__ import annotations

import math
import re

from civ_mcp.lua._helpers import (
    _LUA_RES_VISIBLE,
    _LUA_KNOWN_RESOURCE,
    SENTINEL,
    _bail,
    _bail_lua,
    _int,
    _lua_get_city,
    _lua_get_unit,
)
from civ_mcp.lua.models import (
    DistrictPlacement,
    FogBoundary,
    OwnershipDelta,
    StaticMapDump,
    StaticMapTile,
    WonderPlacement,
    NearbyResource,
    OwnedResource,
    PurchasableTile,
    ResourceStockpile,
    SettleCandidate,
    StrategicMapData,
    TileInfo,
    UnclaimedResource,
)

from civ_mcp.lua.tile_facts import LUA_TILE_PURCHASE_FACTS
from civ_mcp.lua.resource_facts import LUA_RESOURCE_FACTS

# --- Shared Lua fragments for settle-scan scoring (used by two builders) ---

_SETTLE_PREAMBLE = _LUA_KNOWN_RESOURCE + """
local allCities = {}
-- Distance exclusion uses only our cities or currently visible foreign city locations. Hidden
-- foreign cities must not alter the candidate board or leak through ranking.
for i = 0, 62 do
    if Players[i] and Players[i]:IsAlive() then
        for _, c in Players[i]:GetCities():Members() do
            local plot=Map.GetPlot(c:GetX(),c:GetY())
            if i==me or (plot and vis:IsVisible(plot:GetIndex())) then
                table.insert(allCities,{x=c:GetX(),y=c:GetY()})
            end
        end
    end
end
-- Same warning getter as SettlerInfluenceIconManager. Missing entry is not
-- evidence of zero pressure, and this is not a full post-founding forecast.
local loyaltyWarnings=nil
pcall(function()local v=Map.GetContinentPlotsLoyalty();if type(v)=="table"then loyaltyWarnings=v end end)
local classPrefix={RESOURCECLASS_STRATEGIC="S",RESOURCECLASS_LUXURY="L",RESOURCECLASS_BONUS="B"}
local candidates={}
"""

# Scoring body — expects cx, cy, cPlot, vis, me, allCities, classPrefix, resVisible in scope.
# Appends to `candidates` table.
_SETTLE_SCORE_BODY = """
                local tooClose = false
                for _, city in ipairs(allCities) do
                    if Map.GetPlotDistance(cx, cy, city.x, city.y) <= 3 then tooClose = true; break end
                end
                if not tooClose then
                    local totalF, totalP, totalG = 0, 0, 0
                    local resList = {}
                    local resourcesKnown = true
                    for ry = -3, 3 do
                        for rx = -3, 3 do
                            local tx, ty = cx + rx, cy + ry
                            local tPlot = Map.GetPlot(tx, ty)
                            if tPlot and Map.GetPlotDistance(cx, cy, tx, ty) <= 3 and vis:IsRevealed(tPlot:GetIndex()) then
                                totalF = totalF + tPlot:GetYield(0)
                                totalP = totalP + tPlot:GetYield(1)
                                totalG = totalG + tPlot:GetYield(2)
                                local rIdx = knownResourceIndex(me,tPlot)
                                if rIdx==nil then resourcesKnown=false end
                                if rIdx and rIdx >= 0 then
                                    local resEntry = GameInfo.Resources[rIdx]
                                    if resEntry then
                                        local rName = resEntry.ResourceType:gsub("RESOURCE_", "")
                                        local prefix = classPrefix[resEntry.ResourceClassType] or "B"
                                        table.insert(resList, prefix .. ":" .. rName)
                                    end
                                end
                            end
                        end
                    end
                    local waterType = "none"
                    if cPlot:IsFreshWater() then waterType = "fresh"
                    elseif cPlot:IsCoastalLand() then waterType = "coast" end
                    local defScore = 0
                    if cPlot:IsHills() then defScore = defScore + 2 end
                    if cPlot:IsRiver() then defScore = defScore + 1 end
                    for ady = -1, 1 do
                        for adx = -1, 1 do
                            if adx ~= 0 or ady ~= 0 then
                                local ap = Map.GetPlot(cx + adx, cy + ady)
                                if ap and vis:IsRevealed(ap:GetIndex()) and ap:IsHills() and Map.GetPlotDistance(cx, cy, cx+adx, cy+ady) == 1 then defScore = defScore + 1 end
                            end
                        end
                    end
                    local loyalty=loyaltyWarnings and tonumber(loyaltyWarnings[cPlot:GetIndex()])
                    if loyalty and (loyalty~=loyalty or math.abs(loyalty)==math.huge) then loyalty=nil end
                    table.insert(candidates, {x=cx, y=cy, score=0, f=totalF, p=totalP, water=waterType, def=defScore, res=table.concat(resList, ","), loy=loyalty,known=resourcesKnown})
                end
"""


def _settle_output(limit: int) -> str:
    return f"""
for i = 1, #candidates do
    local c = candidates[i]
    print("SETTLE|" .. c.x .. "," .. c.y .. "|" .. c.score .. "|" .. c.f .. "|" .. c.p .. "|" .. c.water .. "|" .. c.def .. "|" .. c.res .. "|" .. (c.loy==nil and "?" or tostring(c.loy)) .. "|" .. tostring(c.known))
end
if #candidates == 0 then print("NONE") end
print("SETTLE_TOTAL|" .. #candidates)
print("{SENTINEL}")
"""


def build_map_area_query(center_x: int, center_y: int, radius: int = 2) -> str:
    return f"""
local cx, cy, r = {center_x}, {center_y}, {radius}
local me = Game.GetLocalPlayer()
local vis = PlayersVisibility[me]
local pTech = Players[me]:GetTechs()
{_LUA_RES_VISIBLE}
for dy = -r, r do
    for dx = -r, r do
        local x, y = cx + dx, cy + dy
        local plot = Map.GetPlot(x, y)
        if plot then
            local plotIdx = plot:GetIndex()
            local revealed = vis:IsRevealed(plotIdx)
            local visible = vis:IsVisible(plotIdx)
            if revealed then
                local terrain = GameInfo.Terrains[plot:GetTerrainType()].TerrainType
                local hills = plot:IsHills() and "1" or "0"
                local river = plot:IsRiver() and "1" or "0"
                local coastal = plot:IsCoastalLand() and "1" or "0"
                local owner = plot:GetOwner()
                local ownerName = ""
                if owner >= 0 then
                    local cfg = PlayerConfigurations[owner]
                    local p = Players[owner]
                    if cfg and p and p:IsAlive() then
                        ownerName = Locale.Lookup(cfg:GetCivilizationShortDescription())
                        if not p:IsMajor() then ownerName = ownerName .. ":CS" end
                    elseif owner == 63 then
                        ownerName = "Barbarian"
                    end
                end
                local featureIdx = plot:GetFeatureType()
                local feature = "none"
                if featureIdx >= 0 then feature = GameInfo.Features[featureIdx].FeatureType end
                local resource = "none"
                local resIdx = plot:GetResourceType()
                if resIdx >= 0 then
                    local resEntry = GameInfo.Resources[resIdx]
                    if resVisible(resEntry) then
                        resource = resEntry.ResourceType .. ":" .. (resEntry.ResourceClassType or "")
                    end
                end
                local imp = "none"
                local impIdx = plot:GetImprovementType()
                if impIdx >= 0 then
                    imp = GameInfo.Improvements[impIdx].ImprovementType
                    if plot:IsImprovementPillaged() then imp = imp .. ":PILLAGED" end
                end
                local freshWater = "0"
                local yields = "0,0,0,0,0,0"
                local unitStr = "none"
                local visTag = "revealed"
                if visible then
                    visTag = "visible"
                    freshWater = plot:IsFreshWater() and "1" or "0"
                    yields = plot:GetYield(0) .. "," .. plot:GetYield(1) .. "," .. plot:GetYield(2) .. "," .. plot:GetYield(3) .. "," .. plot:GetYield(4) .. "," .. plot:GetYield(5)
                    local uParts = {{}}
                    for i = 0, 63 do
                        if i ~= me and Players[i] and Players[i]:IsAlive() then
                            local units = Players[i]:GetUnits()
                            if units then
                                for _, u in units:Members() do
                                    if u:GetX() == x and u:GetY() == y then
                                        local entry = GameInfo.Units[u:GetType()]
                                        local ut = entry and entry.UnitType or "UNKNOWN"
                                        local label = ""
                                        if i == 63 then label = "Barbarian"
                                        else
                                            local oCfg = PlayerConfigurations[i]
                                            label = Locale.Lookup(oCfg:GetCivilizationShortDescription())
                                        end
                                        table.insert(uParts, label .. " " .. ut:gsub("UNIT_", ""))
                                    end
                                end
                            end
                        end
                    end
                    if #uParts > 0 then unitStr = table.concat(uParts, ";") end
                end
                -- Own units: always known regardless of visibility
                local myUnitStr = "none"
                local myParts = {{}}
                local myPlayerUnits = Players[me]:GetUnits()
                if myPlayerUnits then
                    for _, u in myPlayerUnits:Members() do
                        if u:GetX() == x and u:GetY() == y then
                            local entry = GameInfo.Units[u:GetType()]
                            local ut = entry and entry.UnitType or "UNKNOWN"
                            table.insert(myParts, (ut:gsub("UNIT_", "")))
                        end
                    end
                end
                if #myParts > 0 then myUnitStr = table.concat(myParts, ";") end
                local distName = "none"
                local distIdx = plot:GetDistrictType()
                if distIdx >= 0 then
                    local dInfo = GameInfo.Districts[distIdx]
                    if dInfo then distName = dInfo.DistrictType end
                end
                local routeType = -1
                pcall(function() routeType = plot:GetRouteType() end)
                local moveCost = 1
                if terrain ~= "TERRAIN_OCEAN" and terrain ~= "TERRAIN_COAST" then
                    if plot:IsHills() then moveCost = 2 end
                    if featureIdx >= 0 then
                        local fInfo = GameInfo.Features[featureIdx]
                        if fInfo and fInfo.MovementChange then
                            moveCost = moveCost + math.max(0, fInfo.MovementChange)
                        end
                    end
                    if routeType >= 0 then moveCost = 1 end
                end
                print(x .. "," .. y .. "|" .. terrain .. "|" .. feature .. "|" .. resource .. "|" .. hills .. "|" .. river .. "|" .. coastal .. "|" .. imp .. "|" .. owner .. "|" .. unitStr .. "|" .. visTag .. "|" .. freshWater .. "|" .. yields .. "|" .. distName .. "|" .. ownerName .. "|" .. myUnitStr .. "|" .. routeType .. "|" .. moveCost)
            end
        end
    end
end
print("{SENTINEL}")
"""


def build_strategic_map_query() -> str:
    """GameCore context: fog boundary per city + unclaimed luxury/strategic resources."""
    return """
local me = Game.GetLocalPlayer()
local vis = PlayersVisibility[me]
local pTech = Players[me]:GetTechs()
local w, h = Map.GetGridSize()
-- Hex direction offsets (NE, E, SE, SW, W, NW) for offset coords
local dirs = {{0,-1},{1,0},{0,1},{-1,1},{-1,0},{0,-1}}
-- Actually use precise ray-cast: step along each cardinal hex direction
-- For offset coordinates, direction deltas depend on row parity
-- Simplified: use angular sectors by scanning radius rings
for _, c in Players[me]:GetCities():Members() do
    local cx, cy = c:GetX(), c:GetY()
    local nm = Locale.Lookup(c:GetName())
    -- For 6 directions, scan along a ray and find first unrevealed tile
    -- Directions: N(0,-1), NE(+1,-1), SE(+1,+1), S(0,+1), SW(-1,+1), NW(-1,-1) approx
    local dirVecs = {{0,-1},{1,-1},{1,1},{0,1},{-1,1},{-1,-1}}
    local fogDists = {}
    for _, dv in ipairs(dirVecs) do
        local fogDist = -1
        for dist = 3, 15 do
            local tx = cx + dv[1] * dist
            local ty = cy + dv[2] * dist
            local plot = Map.GetPlot(tx, ty)
            if plot then
                if not vis:IsRevealed(plot:GetIndex()) then
                    fogDist = dist
                    break
                end
            else
                fogDist = dist
                break
            end
        end
        table.insert(fogDists, fogDist)
    end
    print("FOG|" .. nm .. "|" .. cx .. "," .. cy .. "|" .. table.concat(fogDists, ","))
end
-- Pass 2: unclaimed luxury/strategic resources on revealed land
for y = 0, h - 1 do
    for x = 0, w - 1 do
        local plot = Map.GetPlot(x, y)
        if plot and vis:IsRevealed(plot:GetIndex()) and plot:GetOwner() == -1 then
            local resIdx = plot:GetResourceType()
            if resIdx >= 0 then
                local res = GameInfo.Resources[resIdx]
                if res and res.ResourceClassType ~= "RESOURCECLASS_BONUS" then
                    -- Check tech visibility
                    local visible = true
                    if res.PrereqTech then
                        local t = GameInfo.Technologies[res.PrereqTech]
                        if t and not pTech:HasTech(t.Index) then visible = false end
                    end
                    if visible then
                        print("UNCLAIMED|" .. res.ResourceType .. "|" .. x .. "," .. y .. "|" .. res.ResourceClassType)
                    end
                end
            end
        end
    end
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def parse_strategic_map_response(lines: list[str]) -> StrategicMapData:
    """Parse FOG| and UNCLAIMED| lines from strategic map query."""
    fog_boundaries: list[FogBoundary] = []
    unclaimed: list[UnclaimedResource] = []

    for line in lines:
        if line.startswith("FOG|"):
            parts = line.split("|")
            if len(parts) >= 4:
                cx, cy = parts[2].split(",")
                dists = [int(d) for d in parts[3].split(",")]
                fog_boundaries.append(
                    FogBoundary(
                        city_name=parts[1],
                        city_x=int(cx),
                        city_y=int(cy),
                        fog_distances=dists,
                    )
                )
        elif line.startswith("UNCLAIMED|"):
            parts = line.split("|")
            if len(parts) >= 4:
                rx, ry = parts[2].split(",")
                unclaimed.append(
                    UnclaimedResource(
                        resource_type=parts[1],
                        x=int(rx),
                        y=int(ry),
                        resource_class=parts[3],
                    )
                )

    return StrategicMapData(
        fog_boundaries=fog_boundaries, unclaimed_resources=unclaimed
    )


def build_found_city(unit_index: int) -> str:
    return f"""
{_lua_get_unit(unit_index)}
if not UnitManager.CanStartOperation(unit, UnitOperationTypes.FOUND_CITY, nil, true) then
    {_bail("ERR:CANNOT_FOUND|Unit cannot found cities (not a settler or no moves)")}
end
local x, y = unit:GetX(), unit:GetY()
local plot = Map.GetPlot(x, y)
if plot:IsWater() then {_bail("ERR:CANNOT_FOUND|Cannot found city on water")} end
if plot:IsMountain() then {_bail("ERR:CANNOT_FOUND|Cannot found city on mountain")} end
for i = 0, 62 do
    if Players[i] and Players[i]:IsAlive() then
        local cities = Players[i]:GetCities()
        if cities then
            for _, c in cities:Members() do
                local dist = Map.GetPlotDistance(x, y, c:GetX(), c:GetY())
                if dist <= 3 then
                    {_bail_lua('"ERR:CANNOT_FOUND|Too close to " .. Locale.Lookup(c:GetName()) .. " (settler at " .. x .. "," .. y .. ", distance " .. dist .. ", need > 3)"')}
                end
            end
        end
    end
end
local params = {{}}
params[UnitOperationTypes.PARAM_X] = x
params[UnitOperationTypes.PARAM_Y] = y
pcall(function() LuaEvents.DiplomacyActionView_ShowIngameUI() end)
UnitManager.RequestOperation(unit, UnitOperationTypes.FOUND_CITY, params)
print("OK:FOUNDED|" .. x .. "," .. y)
print("{SENTINEL}")
"""


def build_verify_city_at(x: int, y: int) -> str:
    """Check if a city exists at the given coordinates."""
    return f"""
local plot = Map.GetPlot({x}, {y})
if plot and plot:IsCity() and plot:GetOwner() == Game.GetLocalPlayer() then
    print("OK:CITY_EXISTS|owner:" .. plot:GetOwner())
else
    print("OK:NO_CITY")
end
print("{SENTINEL}")
"""


def parse_verify_city_at(lines: list[str]) -> bool:
    """Return True if a city was found at the coordinates."""
    for line in lines:
        if "CITY_EXISTS" in line:
            return True
    return False


def build_settle_advisor_query(unit_index: int) -> str:
    """Scan radius 5 around settler for valid + scored settle candidates.

    Scores by weighted yields, water bonus, defense, and resource value.
    Resources are classified (S=strategic, L=luxury, B=bonus).
    Returns all observed terrain candidates, unranked; eligibility is unverified.
    """
    return f"""
local me = Game.GetLocalPlayer()
local unit = UnitManager.GetUnit(me, {unit_index} % 65536)
if unit == nil then {_bail("ERR:UNIT_NOT_FOUND")} end
if {unit_index} >= 65536 and unit:GetID() ~= {unit_index} then {_bail("ERR:UNIT_ID_CHANGED")} end
local sx, sy = unit:GetX(), unit:GetY()
local vis = PlayersVisibility[me]
local pTech = Players[me]:GetTechs()
{_SETTLE_PREAMBLE}
for dy = -5, 5 do
    for dx = -5, 5 do
        local cx, cy = sx + dx, sy + dy
        local cPlot = Map.GetPlot(cx, cy)
        if cPlot and Map.GetPlotDistance(sx,sy,cx,cy)<=5 and vis:IsVisible(cPlot:GetIndex()) and cPlot:GetOwner()==-1 and not cPlot:IsWater() and not cPlot:IsMountain() then
{_SETTLE_SCORE_BODY}
        end
    end
end
{_settle_output(5)}
"""


def build_global_settle_scan(center_x=None, center_y=None, radius=None) -> str:
    """Read all revealed terrain candidates in the explicit scope (InGame)."""
    region=(center_x,center_y,radius)
    if any(v is not None for v in region):
        if any(type(v)is not int for v in region) or not 0<=radius<=20:
            raise ValueError("center_x, center_y and radius (0..20) must be provided together")
        guard=f"Map.GetPlotDistance(cx,cy,{center_x},{center_y})<={radius}"
        validate=f'if Map.GetPlot({center_x},{center_y})==nil then {_bail("ERR:INVALID_CENTER")} end'
    else:
        guard='true';validate=''
    return f"""
local me = Game.GetLocalPlayer()
local vis = PlayersVisibility[me]
local pTech = Players[me]:GetTechs()
local w, h = Map.GetGridSize()
{validate}
{_SETTLE_PREAMBLE}
for y = 0, h - 1 do
    for x = 0, w - 1 do
        local cx, cy = x, y
        local cPlot = Map.GetPlot(cx, cy)
        if cPlot and {guard} and vis:IsRevealed(cPlot:GetIndex()) and cPlot:GetOwner()==-1 and not cPlot:IsWater() and not cPlot:IsMountain() then
{_SETTLE_SCORE_BODY}
        end
    end
end
{_settle_output(10)}
"""


def build_empire_resources_query() -> str:
    """Scan owned tiles for resources, stockpile counts, and nearby unclaimed.

    Returns STOCKPILE lines for strategic resource amounts/caps,
    OWNED lines for resources on player-owned tiles, and
    NEARBY lines for unclaimed resources within 5 tiles of cities.
    Runs in InGame context for GetResourceStockpileCap/GetResourceAccumulationPerTurn.
    """
    return _LUA_KNOWN_RESOURCE + LUA_RESOURCE_FACTS + """
local me = Game.GetLocalPlayer()
local vis = PlayersVisibility[me]
local pRes = Players[me]:GetResources()
local resourceObservationKnown=true
local classMap = {RESOURCECLASS_STRATEGIC="strategic", RESOURCECLASS_LUXURY="luxury", RESOURCECLASS_BONUS="bonus",RESOURCECLASS_ARTIFACT="artifact"}
-- Stockpile info for strategic and luxury resources
for row in GameInfo.Resources() do
    local visibilityOK,visible=pcall(function()return pRes:IsResourceVisible(row.Index)end)
    if not visibilityOK or type(visible)~='boolean'then resourceObservationKnown=false end
    if visibilityOK and visible==true then
        local cls = classMap[row.ResourceClassType]
        if cls == "strategic" then
            local amt = pRes:GetResourceAmount(row.Index)
            local cap = pRes:GetResourceStockpileCap(row.Index)
            local accum = pRes:GetResourceAccumulationPerTurn(row.Index)
            local demand = pRes:GetUnitResourceDemandPerTurn(row.Index)
            local imported = pRes:GetResourceImportPerTurn(row.Index)
            -- 与原生顶栏一致：总收入包含进口和奖励，总消耗包含发电。
            accum = accum + imported + pRes:GetBonusResourcePerTurn(row.Index)
            demand = demand + pRes:GetPowerResourceDemandPerTurn(row.Index)
            local rName = row.ResourceType:gsub("RESOURCE_", "")
            print("STOCKPILE|" .. rName .. "|" .. amt .. "|" .. cap .. "|" .. accum .. "|" .. demand .. "|" .. imported)
        elseif cls == "luxury" then
            local amt = pRes:GetResourceAmount(row.Index)
            if amt > 0 then
                local rName = row.ResourceType:gsub("RESOURCE_", "")
                print("LUXURY_OWNED|" .. rName .. "|" .. amt)
            end
        end
    end
end
local myCities = {}
local seen = {}
for _, c in Players[me]:GetCities():Members() do
    table.insert(myCities, {name=Locale.Lookup(c:GetName()), x=c:GetX(), y=c:GetY()})
end
-- Scan all owned tiles
local mapW, mapH = Map.GetGridSize()
for x = 0, mapW - 1 do
    for y = 0, mapH - 1 do
        local plot = Map.GetPlot(x, y)
        if plot and plot:GetOwner() == me then
            local rIdx = knownResourceIndex(me,plot)
            if rIdx==nil then resourceObservationKnown=false end
            if rIdx and rIdx >= 0 then
                local resEntry = GameInfo.Resources[rIdx]
                local rName = resEntry.ResourceType:gsub("RESOURCE_", "")
                local rClass = classMap[resEntry.ResourceClassType] or "unknown"
                local facts=resourcePlotFacts(me,plot,resEntry)
                local improved=facts.improvement_type==nil and '?'or(facts.improvement_type=='NONE'and '0'or '1')
                print("OWNED|" .. rName .. "|" .. rClass .. "|" .. improved .. "|" .. x .. "," .. y)
                printResourceFacts('owned',me,plot,resEntry,facts)
                seen[x .. "," .. y] = true
            end
        end
    end
end
-- Scan radius 5 around each city for unowned visible resources
for _, city in ipairs(myCities) do
    for dy = -5, 5 do
        for dx = -5, 5 do
            local tx, ty = city.x + dx, city.y + dy
            local key = tx .. "," .. ty
            if not seen[key] then
                local tPlot = Map.GetPlot(tx, ty)
                if tPlot and vis:IsRevealed(tPlot:GetIndex()) and tPlot:GetOwner() ~= me then
                    local rIdx = knownResourceIndex(me,tPlot)
                    if rIdx==nil then resourceObservationKnown=false end
                    if rIdx and rIdx >= 0 then
                        local resEntry = GameInfo.Resources[rIdx]
                        local consumable=resEntry.ResourceClassType=='RESOURCECLASS_BONUS'or resourceWorkUnit(resEntry)=='UNIT_ARCHAEOLOGIST'
                        if not consumable or vis:IsVisible(tPlot:GetIndex())then
                        local rName = resEntry.ResourceType:gsub("RESOURCE_", "")
                        local rClass = classMap[resEntry.ResourceClassType] or "unknown"
                        local nearest,dist=nil,math.huge
                        for _,c in ipairs(myCities)do local d=Map.GetPlotDistance(c.x,c.y,tx,ty);if d<dist then nearest=c;dist=d end end
                        if dist<=5 then
                         print("NEARBY|" .. rName .. "|" .. rClass .. "|" .. tx .. "," .. ty .. "|" .. nearest.name .. "|" .. dist)
                         printResourceFacts('nearby',me,tPlot,resEntry)
                         seen[key] = true
                        end
                        end
                    end
                end
            end
        end
    end
end
print('RESOURCE_SCAN|true|'..tostring(resourceObservationKnown))
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def build_district_advisor_query(city_id: int, district_type: str) -> str:
    """Read every native available plot and its native adjacency (InGame).

    The engine owns terrain/civilization/mod rules. Do not rank or reimplement
    them here; failed getters stay unknown, including the aggregate.
    """
    if not re.fullmatch(r"DISTRICT_[A-Z0-9_]+", district_type):
        raise ValueError("district_type must be a DISTRICT_ identifier")
    return f"""
{_lua_get_city(city_id)}
local me = Game.GetLocalPlayer()
local dist = GameInfo.Districts["{district_type}"]
if dist == nil then {_bail(f"ERR:DISTRICT_NOT_FOUND|{district_type}")} end
local bq = pCity:GetBuildQueue()
if not bq:CanProduce(dist.Hash, true) then
    {_bail(f"ERR:CANNOT_PRODUCE|{district_type} cannot be produced in this city")}
end
local targets = CityManager.GetOperationTargets(pCity, CityOperationTypes.BUILD, {{[CityOperationTypes.PARAM_DISTRICT_TYPE] = dist.Hash}})
if targets == nil then {_bail("ERR:NO_TARGETS|No valid placement targets found")} end
if type(targets) ~= "table" then {_bail("ERR:TARGETS_UNKNOWN|Native placement result is unavailable")} end
-- Match the native UI: only PLOTS, never BLOCKED_PLOTS or other arrays.
local plotIndices = targets[CityOperationResults.PLOTS]
if plotIndices ~= nil and type(plotIndices) ~= "table" then
    {_bail("ERR:TARGETS_UNKNOWN|Native placement plots are unavailable")}
end
if plotIndices == nil or #plotIndices == 0 then {_bail("ERR:NO_TILES|No valid placement tiles found")} end
local function adjacency(plot, name)
    local info = GameInfo.Yields and GameInfo.Yields[name]
    local ok, value = pcall(function()
        return plot:GetAdjacencyYield(me, pCity:GetID(), dist.Index, info.Index)
    end)
    if ok and type(value) == "number" and value == value and math.abs(value) < math.huge then
        return value
    end
    return nil
end
for _, pIdx in ipairs(plotIndices) do
    local plot = Map.GetPlotByIndex(pIdx)
    if not plot then {_bail("ERR:TARGETS_UNKNOWN|A native placement plot could not be read")} end
    local s = adjacency(plot, "YIELD_SCIENCE")
    local p = adjacency(plot, "YIELD_PRODUCTION")
    local g = adjacency(plot, "YIELD_GOLD")
    local f = adjacency(plot, "YIELD_FAITH")
    local c = adjacency(plot, "YIELD_CULTURE")
    local food = adjacency(plot, "YIELD_FOOD")
    local total = nil
    if s and p and g and f and c and food then total = s+p+g+f+c+food end
    local tInfo = GameInfo.Terrains[plot:GetTerrainType()]
    local terrain = tInfo and Locale.Lookup(tInfo.Name) or "unknown"
    if plot:IsHills() then terrain = terrain .. " Hills" end
    local feature = GameInfo.Features[plot:GetFeatureType()]
    if feature then terrain = terrain .. " " .. Locale.Lookup(feature.Name) end
    terrain = terrain:gsub("|", "/"):gsub("[\\r\\n]", " ")
    local function value(v) return v == nil and "?" or string.format("%.17g",v) end
    print("DPLOT|" .. plot:GetX() .. "," .. plot:GetY() .. "|" .. value(s) .. "|" .. value(p) .. "|" .. value(g) .. "|" .. value(f) .. "|" .. value(c) .. "|" .. value(total) .. "|" .. terrain .. "|" .. value(food))
end
print("{SENTINEL}")
"""


def build_wonder_advisor_query(city_id: int, wonder_name: str) -> str:
    """Find valid tiles for placing a wonder, ranked by displacement cost (InGame context).

    Uses CityManager.GetOperationTargets with PARAM_BUILDING_TYPE — the same API
    used by the game UI — so all terrain/river/coast/feature constraints are enforced
    automatically. Ranks tiles by how much productive value is lost (lower = better choice).
    """
    if not re.fullmatch(r"BUILDING_[A-Z0-9_]+", wonder_name):
        raise ValueError("wonder_name must be a BUILDING_ identifier")
    return _LUA_KNOWN_RESOURCE + f"""
{_lua_get_city(city_id)}
local brow = GameInfo.Buildings["{wonder_name}"]
if brow == nil then {_bail(f"ERR:WONDER_NOT_FOUND|{wonder_name}")} end
if not brow.IsWonder then {_bail(f"ERR:NOT_A_WONDER|{wonder_name} is not a wonder")} end
local bq = pCity:GetBuildQueue()
if not bq:CanProduce(brow.Hash, true) then
  {_bail(f"ERR:CANNOT_PRODUCE|{wonder_name} cannot be produced in this city")}
end
local targets = CityManager.GetOperationTargets(pCity, CityOperationTypes.BUILD,
  {{[CityOperationTypes.PARAM_BUILDING_TYPE] = brow.Hash}})
if targets == nil then {_bail("ERR:NO_TARGETS|No valid placement targets found")} end
if type(targets) ~= "table" then {_bail("ERR:TARGETS_UNKNOWN|Native placement result is unavailable")} end
local plotIndices = targets[CityOperationResults.PLOTS]
if plotIndices ~= nil and type(plotIndices) ~= "table" then {_bail("ERR:TARGETS_UNKNOWN|Native placement plots are unavailable")} end
if plotIndices == nil or #plotIndices == 0 then {_bail("ERR:NO_TILES|No valid placement tiles found")} end
local results = {{}}
for _, pIdx in ipairs(plotIndices) do
  local plot = Map.GetPlotByIndex(pIdx)
  if not plot then {_bail("ERR:TARGETS_UNKNOWN|A native placement plot could not be read")} end
  if plot then
    local px, py = plot:GetX(), plot:GetY()
    local terrainName = "?"
    local tInfo = GameInfo.Terrains[plot:GetTerrainType()]
    if tInfo then terrainName = tInfo.TerrainType end
    local featName = "none"
    local feat = plot:GetFeatureType()
    if feat >= 0 then
      local fInfo = GameInfo.Features[feat]
      if fInfo then featName = fInfo.FeatureType end
    end
    local isRiver = plot:IsRiver()
    local isCoastal = plot:IsCoastalLand()
    local resName = "none"
    local res = knownResourceIndex(me,plot)
    if res==nil then resName="unknown"
    elseif res >= 0 then
      local rInfo = GameInfo.Resources[res]
      if rInfo then resName = rInfo.ResourceType end
    end
    local impName = "none"
    local imp = plot:GetImprovementType()
    if imp >= 0 then
      local iInfo = GameInfo.Improvements[imp]
      if iInfo then impName = iInfo.ImprovementType end
    end
    table.insert(results, {{x=px, y=py, terrain=terrainName, feat=featName,
      river=tostring(isRiver), coastal=tostring(isCoastal),
      resource=resName, improvement=impName, score=0}})
  end
end
for i = 1, #results do
  local r = results[i]
  print("WPLOT|"..r.x..","..r.y.."|"..r.terrain.."|"..r.feat.."|"..r.river.."|"..r.coastal.."|"..r.resource.."|"..r.improvement.."|"..r.score)
end
print("{SENTINEL}")
"""


def build_purchasable_tiles_query(city_id: int) -> str:
    """All native purchase candidates with current price and eligibility."""
    return _LUA_KNOWN_RESOURCE + LUA_TILE_PURCHASE_FACTS + f"""
{_lua_get_city(city_id)}
local targets = CityManager.GetCommandTargets(pCity, CityCommandTypes.PURCHASE, {{[CityCommandTypes.PARAM_PLOT_PURCHASE] = 1}})
if targets == nil then {_bail("ERR:NO_TARGETS|Native purchasable tiles unavailable")} end
if type(targets) ~= "table" then {_bail("ERR:TARGETS_UNKNOWN|Native purchasable tiles unavailable")} end
local indices = targets[CityCommandResults.PLOTS]
if indices ~= nil and type(indices) ~= "table" then {_bail("ERR:TARGETS_UNKNOWN|Native purchasable tiles unavailable")} end
if indices == nil then indices = {{}} end
for _, index in ipairs(indices) do
    local plot = Map.GetPlotByIndex(index)
    if not plot then {_bail("ERR:TARGETS_UNKNOWN|Native purchase plot unavailable")} end
    local cost,balance,allowed,reason=tilePurchaseQuote(pCity,me,plot)
    local info=GameInfo.Terrains[plot:GetTerrainType()]
    local terrain=info and Locale.Lookup(info.Name) or "unknown"
    if plot:IsHills() then terrain=terrain .. " Hills" end
    local resource,class="",""
    local res=knownResourceIndex(me,plot)
    if res==nil then resource="unknown"
    elseif res>=0 then
        local row=GameInfo.Resources[res]
        if row then
            resource=Locale.Lookup(row.Name)
            class=({{RESOURCECLASS_STRATEGIC="strategic",RESOURCECLASS_LUXURY="luxury",RESOURCECLASS_BONUS="bonus"}})[row.ResourceClassType] or "unknown"
        else resource="unknown" end
    end
    local function wire(v) if v==nil then return "?" end;if type(v)=="number" then return string.format("%.17g",v) end;return tostring(v):gsub("|","/") end
    print("PTILE|" .. plot:GetX() .. "," .. plot:GetY() .. "|" .. wire(cost) .. "|" .. wire(terrain) .. "|" .. wire(resource) .. "|" .. wire(class) .. "|" .. wire(balance) .. "|" .. wire(allowed) .. "|" .. reason)
end
print("{SENTINEL}")
"""


def parse_map_response(lines: list[str]) -> list[TileInfo]:
    tiles = []
    for line in lines:
        parts = line.split("|")
        if len(parts) < 9:
            continue
        x_str, y_str = parts[0].split(",")
        unit_list = None
        if len(parts) > 9 and parts[9] != "none":
            unit_list = parts[9].split(";")
        visibility = "visible"
        if len(parts) > 10:
            visibility = parts[10]
        is_fresh_water = False
        if len(parts) > 11:
            is_fresh_water = parts[11] == "1"
        yields = None
        if len(parts) > 12 and parts[12] != "0,0,0,0,0,0":
            yield_parts = parts[12].split(",")
            if len(yield_parts) == 6:
                yields = tuple(_int(y) for y in yield_parts)
        elif len(parts) > 12 and visibility == "visible":
            # Visible tile with all-zero yields — still valid
            yields = (0, 0, 0, 0, 0, 0)
        # Parse resource field — may contain "RESOURCE_X:RESOURCECLASS_Y" or "none"
        resource_name = None
        resource_class = None
        if parts[3] != "none":
            res_parts = parts[3].split(":", 1)
            resource_name = res_parts[0]
            if len(res_parts) > 1:
                _CLASS_MAP = {
                    "RESOURCECLASS_STRATEGIC": "strategic",
                    "RESOURCECLASS_LUXURY": "luxury",
                    "RESOURCECLASS_BONUS": "bonus",
                }
                resource_class = _CLASS_MAP.get(res_parts[1])
        # Parse improvement — may have :PILLAGED suffix
        imp_raw = parts[7]
        imp_name = None
        imp_pillaged = False
        if imp_raw != "none":
            if imp_raw.endswith(":PILLAGED"):
                imp_name = imp_raw[:-9]  # strip ":PILLAGED"
                imp_pillaged = True
            else:
                imp_name = imp_raw
        owner_name = parts[14] if len(parts) > 14 and parts[14] else None
        own_unit_list = None
        if len(parts) > 15 and parts[15] != "none":
            own_unit_list = [p for p in parts[15].split(";") if p]
        route_type = -1
        if len(parts) > 16:
            try:
                route_type = int(parts[16])
            except ValueError:
                pass
        movement_cost = 1
        if len(parts) > 17:
            try:
                movement_cost = int(parts[17])
            except ValueError:
                pass
        tiles.append(
            TileInfo(
                x=int(x_str),
                y=int(y_str),
                terrain=parts[1],
                feature=None if parts[2] == "none" else parts[2],
                resource=resource_name,
                is_hills=parts[4] == "1",
                is_river=parts[5] == "1",
                is_coastal=parts[6] == "1",
                improvement=imp_name,
                owner_id=int(parts[8]),
                visibility=visibility,
                is_fresh_water=is_fresh_water,
                yields=yields,
                units=unit_list,
                resource_class=resource_class,
                is_pillaged=imp_pillaged,
                district=None
                if (len(parts) <= 13 or parts[13] == "none")
                else parts[13],
                owner_name=owner_name,
                own_units=own_unit_list,
                route_type=route_type,
                movement_cost=movement_cost,
            )
        )
    return tiles


def parse_settle_advisor_response(lines: list[str]) -> list[SettleCandidate]:
    candidates = []
    for line in lines:
        if line == "NONE":
            break
        if not line.startswith("SETTLE|"):
            continue
        parts = line.split("|")
        if len(parts) < 8:
            continue
        x_str, y_str = parts[1].split(",")
        resources = [r for r in parts[7].split(",") if r] if parts[7] else []
        lux = sum(1 for r in resources if r.startswith("L:"))
        strat = sum(1 for r in resources if r.startswith("S:"))
        candidates.append(
            SettleCandidate(
                x=int(x_str),
                y=int(y_str),
                score=float(parts[2]),
                total_food=int(parts[3]),
                total_prod=int(parts[4]),
                water_type=parts[5],
                resources=resources,
                defense_score=int(parts[6]),
                luxury_count=lux,
                strategic_count=strat,
                loyalty_pressure=float(parts[8]) if len(parts) > 8 and parts[8] != "?" else None,
                    resource_visibility_known={"true":True,"false":False}.get(parts[9]) if len(parts)>9 else None,
            )
        )
    return candidates


def build_stockpile_query() -> str:
    """Lightweight strategic resource stockpile query (InGame context).

    Only emits STOCKPILE lines — no tile scanning. Used for turn snapshots.
    """
    return """
local me = Game.GetLocalPlayer()
local pRes = Players[me]:GetResources()
for row in GameInfo.Resources() do
    if row.ResourceClassType == "RESOURCECLASS_STRATEGIC" and pRes:IsResourceVisible(row.Index) then
        local amt = pRes:GetResourceAmount(row.Index)
        local cap = pRes:GetResourceStockpileCap(row.Index)
        local accum = pRes:GetResourceAccumulationPerTurn(row.Index)
        local demand = pRes:GetUnitResourceDemandPerTurn(row.Index)
        local imported = pRes:GetResourceImportPerTurn(row.Index)
        -- 与帝国资源查询保持相同的完整收支口径。
        accum = accum + imported + pRes:GetBonusResourcePerTurn(row.Index)
        demand = demand + pRes:GetPowerResourceDemandPerTurn(row.Index)
        local rName = row.ResourceType:gsub("RESOURCE_", "")
        print("STOCKPILE|" .. rName .. "|" .. amt .. "|" .. cap .. "|" .. accum .. "|" .. demand .. "|" .. imported)
    end
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def parse_stockpile_response(lines: list[str]) -> list[ResourceStockpile]:
    """Parse STOCKPILE lines into ResourceStockpile objects."""
    stockpiles = []
    for line in lines:
        if line.startswith("STOCKPILE|"):
            parts = line.split("|")
            if len(parts) < 7:
                continue
            stockpiles.append(
                ResourceStockpile(
                    name=parts[1],
                    amount=int(parts[2]),
                    cap=int(parts[3]),
                    per_turn=int(parts[4]),
                    demand=int(parts[5]),
                    imported=int(parts[6]),
                )
            )
    return stockpiles


def parse_empire_resources_response(
    lines: list[str],
) -> tuple[
    list[ResourceStockpile], list[OwnedResource], list[NearbyResource], dict[str, int]
]:
    """Returns (stockpiles, owned_tiles, nearby_unclaimed, luxury_counts)."""
    stockpiles = []
    owned = []
    nearby = []
    luxuries: dict[str, int] = {}
    for line in lines:
        if line.startswith("STOCKPILE|"):
            parts = line.split("|")
            if len(parts) < 7:
                continue
            stockpiles.append(
                ResourceStockpile(
                    name=parts[1],
                    amount=int(parts[2]),
                    cap=int(parts[3]),
                    per_turn=int(parts[4]),
                    demand=int(parts[5]),
                    imported=int(parts[6]),
                )
            )
        elif line.startswith("LUXURY_OWNED|"):
            parts = line.split("|")
            if len(parts) >= 3:
                luxuries[parts[1]] = int(parts[2])
        elif line.startswith("OWNED|"):
            parts = line.split("|")
            if len(parts) < 5:
                continue
            x_str, y_str = parts[4].split(",")
            owned.append(
                OwnedResource(
                    name=parts[1],
                    resource_class=parts[2],
                    improved=parts[3] == "1",
                    x=int(x_str),
                    y=int(y_str),
                )
            )
        elif line.startswith("NEARBY|"):
            parts = line.split("|")
            if len(parts) < 6:
                continue
            x_str, y_str = parts[3].split(",")
            nearby.append(
                NearbyResource(
                    name=parts[1],
                    resource_class=parts[2],
                    x=int(x_str),
                    y=int(y_str),
                    nearest_city=parts[4],
                    distance=int(parts[5]),
                )
            )
    details={}
    for line in lines:
        if line.startswith('RESOURCE_DETAIL|'):
            parts=line.split('|')
            if len(parts)==8:
                def boolean(v): return {'true':True,'false':False}.get(v)
                details[(parts[1],parts[2])]=dict(owner_id=int(parts[3]) if re.fullmatch(r'-?\d+',parts[3]) else None,
                    visible_now=boolean(parts[4]),improvement_type=None if parts[5]=='?'else parts[5],
                    pillaged=boolean(parts[6]),work_unit=None if parts[7]=='?'else parts[7])
    for scope,items in (('owned',owned),('nearby',nearby)):
        for item in items:
            detail=details.get((scope,f'{item.x},{item.y}'),{})
            for key,value in detail.items():setattr(item,key,value)
            if scope=='owned' and detail:
                item.improved=None if item.improvement_type is None else item.improvement_type!='NONE'
    return stockpiles, owned, nearby, luxuries


def parse_district_advisor_response(lines: list[str]) -> list[DistrictPlacement]:
    """Parse DPLOT| lines from build_district_advisor_query."""
    results: list[DistrictPlacement] = []
    for line in lines:
        if line.startswith("DPLOT|"):
            parts = line.split("|")
            if len(parts) >= 9:
                coords = parts[1].split(",")
                def number(value: str) -> float | None:
                    try:
                        result = float(value)
                        return result if math.isfinite(result) else None
                    except ValueError:
                        return None

                adjacency = {}
                values = list(zip(
                    ("science", "production", "gold", "faith", "culture"),
                    parts[2:7],
                ))
                if len(parts) >= 10:
                    values.append(("food", parts[9]))
                for name, text in values:
                    value = number(text)
                    if value is None or value != 0:
                        adjacency[name] = value
                results.append(
                    DistrictPlacement(
                        x=int(coords[0]),
                        y=int(coords[1]),
                        adjacency=adjacency,
                        total_adjacency=number(parts[7]),
                        terrain_desc=parts[8],
                    )
                )
    return results


def parse_wonder_advisor_response(lines: list[str]) -> list[WonderPlacement]:
    """Parse WPLOT| lines from build_wonder_advisor_query."""
    results: list[WonderPlacement] = []
    for line in lines:
        if line.startswith("WPLOT|"):
            parts = line.split("|")
            if len(parts) >= 9:
                coords = parts[1].split(",")
                results.append(
                    WonderPlacement(
                        x=int(coords[0]),
                        y=int(coords[1]),
                        terrain=parts[2],
                        feature=parts[3],
                        has_river=parts[4] == "true",
                        is_coastal=parts[5] == "true",
                        resource=parts[6],
                        improvement=parts[7],
                        displacement_score=int(parts[8]),
                    )
                )
    return results


def parse_purchasable_tiles_response(lines: list[str]) -> list[PurchasableTile]:
    """Parse PTILE| lines from build_purchasable_tiles_query."""
    results: list[PurchasableTile] = []
    for line in lines:
        if line.startswith("PTILE|"):
            parts = line.split("|")
            if len(parts) >= 6:
                coords = parts[1].split(",")
                results.append(
                    PurchasableTile(
                        x=int(coords[0]),
                        y=int(coords[1]),
                        cost=None if parts[2] == "?" else float(parts[2]),
                        terrain=parts[3],
                        resource=parts[4] if parts[4] else None,
                        resource_class=parts[5] if parts[5] else None,
                        balance=None if len(parts) < 7 or parts[6] == "?" else float(parts[6]),
                        can_purchase={"true": True, "false": False}.get(parts[7]) if len(parts) >= 8 else None,
                        reason=parts[8] if len(parts) >= 9 else "OBSERVATION_UNAVAILABLE",
                    )
                )
    return results


# ── Strategic map dump (for web replay) ──────────────────────────────────


def build_static_map_dump() -> str:
    """One-time full terrain extraction for strategic map replay.

    Iterates every tile on the map and outputs static terrain data,
    initial ownership, route types, city positions, and player-civ mapping.
    Also initializes Lua globals for subsequent delta calls.

    Uses InGame context (execute_write) for broadest API compatibility.
    """
    return """
local w, h = Map.GetGridSize()
local me = Game.GetLocalPlayer()
print("SIZE|" .. w .. "|" .. h)
-- Initialize delta tracking globals
__civmcp_prev_owners = {}
__civmcp_prev_routes = {}
for y = 0, h - 1 do
    local parts = {}
    for x = 0, w - 1 do
        local idx = y * w + x
        local plot = Map.GetPlot(x, y)
        local terrIdx = plot:GetTerrainType()
        local featIdx = plot:GetFeatureType()
        local hills = plot:IsHills() and 1 or 0
        local river = plot:IsRiver() and 1 or 0
        local coastal = plot:IsCoastalLand() and 1 or 0
        local resIdx = plot:GetResourceType()
        local owner = plot:GetOwner()
        local routeType = -1
        if pcall(function() routeType = plot:GetRouteType() end) then else routeType = -1 end
        __civmcp_prev_owners[idx] = owner
        __civmcp_prev_routes[idx] = routeType
        table.insert(parts, terrIdx .. "," .. featIdx .. "," .. hills .. "," .. river .. "," .. coastal .. "," .. resIdx .. "," .. owner .. "," .. routeType)
    end
    print("ROW|" .. y .. "|" .. table.concat(parts, "|"))
end
for i = 0, 62 do
    if Players[i] and Players[i]:IsAlive() then
        for _, c in Players[i]:GetCities():Members() do
            local name = c:GetName() or ""
            print("CITY|" .. c:GetX() .. "," .. c:GetY() .. "|" .. i .. "|" .. c:GetPopulation() .. "|" .. name)
        end
    end
end
local csTypeMap = {}
csTypeMap["LEADER_MINOR_CIV_SCIENTIFIC"] = "Scientific"
csTypeMap["LEADER_MINOR_CIV_CULTURAL"] = "Cultural"
csTypeMap["LEADER_MINOR_CIV_MILITARISTIC"] = "Militaristic"
csTypeMap["LEADER_MINOR_CIV_RELIGIOUS"] = "Religious"
csTypeMap["LEADER_MINOR_CIV_TRADE"] = "Trade"
csTypeMap["LEADER_MINOR_CIV_INDUSTRIAL"] = "Industrial"
for i = 0, 62 do
    if Players[i] and Players[i]:IsAlive() then
        local cfg = PlayerConfigurations[i]
        if cfg then
            local civType = cfg:GetCivilizationTypeName() or "UNKNOWN"
            local csType = ""
            if not Players[i]:IsMajor() then
                local leaderType = cfg:GetLeaderTypeName()
                if leaderType then
                    local ldr = GameInfo.Leaders[leaderType]
                    if ldr and ldr.InheritFrom then
                        csType = csTypeMap[ldr.InheritFrom] or ""
                    end
                end
            end
            print("PLAYER|" .. i .. "|" .. civType .. "|" .. csType)
        end
    end
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def parse_static_map_dump(lines: list[str]) -> StaticMapDump:
    """Parse SIZE|, ROW|, CITY|, PLAYER| lines from build_static_map_dump."""
    grid_w, grid_h = 0, 0
    tiles: list[StaticMapTile] = []
    initial_owners: list[int] = []
    initial_routes: list[int] = []
    cities: list[tuple[int, int, int, int]] = []
    players: list[tuple[int, str, str | None]] = []

    for line in lines:
        if line.startswith("SIZE|"):
            parts = line.split("|")
            grid_w, grid_h = int(parts[1]), int(parts[2])
        elif line.startswith("ROW|"):
            parts = line.split("|")
            # parts[0]="ROW", parts[1]=y, parts[2..]=tile data
            for tile_str in parts[2:]:
                vals = tile_str.split(",")
                if len(vals) >= 8:
                    tiles.append(
                        StaticMapTile(
                            terrain=int(vals[0]),
                            feature=int(vals[1]),
                            hills=vals[2] == "1",
                            river=vals[3] == "1",
                            coastal=vals[4] == "1",
                            resource=int(vals[5]),
                        )
                    )
                    initial_owners.append(int(vals[6]))
                    initial_routes.append(int(vals[7]))
        elif line.startswith("CITY|"):
            parts = line.split("|")
            if len(parts) >= 4:
                xy = parts[1].split(",")
                name = parts[4] if len(parts) >= 5 else ""
                cities.append(
                    (int(xy[0]), int(xy[1]), int(parts[2]), int(parts[3]), name)
                )
        elif line.startswith("PLAYER|"):
            parts = line.split("|")
            if len(parts) >= 3:
                cs_type = parts[3] if len(parts) >= 4 and parts[3] else None
                players.append((int(parts[1]), parts[2], cs_type))

    return StaticMapDump(
        grid_w=grid_w,
        grid_h=grid_h,
        tiles=tiles,
        initial_owners=initial_owners,
        initial_routes=initial_routes,
        initial_cities=cities,
        players=players,
    )


def build_ownership_delta() -> str:
    """Lightweight per-turn ownership/road delta for strategic map replay.

    Uses Lua globals __civmcp_prev_owners and __civmcp_prev_routes
    (initialized by build_static_map_dump) for change detection.
    First call after game load (globals unset) produces a full diff.
    """
    return f"""
local w, h = Map.GetGridSize()
if not __civmcp_prev_owners then __civmcp_prev_owners = {{}} end
if not __civmcp_prev_routes then __civmcp_prev_routes = {{}} end
local ownerChanges = {{}}
local roadChanges = {{}}
for y = 0, h - 1 do
    for x = 0, w - 1 do
        local idx = y * w + x
        local plot = Map.GetPlot(x, y)
        local owner = plot:GetOwner()
        if owner ~= (__civmcp_prev_owners[idx] or -99) then
            table.insert(ownerChanges, idx .. "," .. owner)
            __civmcp_prev_owners[idx] = owner
        end
        local routeType = -1
        pcall(function() routeType = plot:GetRouteType() end)
        if routeType ~= (__civmcp_prev_routes[idx] or -99) then
            table.insert(roadChanges, idx .. "," .. routeType)
            __civmcp_prev_routes[idx] = routeType
        end
    end
end
if #ownerChanges > 0 then
    print("OWNERS|" .. table.concat(ownerChanges, "|"))
end
if #roadChanges > 0 then
    print("ROADS|" .. table.concat(roadChanges, "|"))
end
for i = 0, 62 do
    if Players[i] and Players[i]:IsAlive() then
        for _, c in Players[i]:GetCities():Members() do
            local name = c:GetName() or ""
            print("CITY|" .. c:GetX() .. "," .. c:GetY() .. "|" .. i .. "|" .. c:GetPopulation() .. "|" .. name)
        end
    end
end
print("{SENTINEL}")
"""


def parse_ownership_delta(lines: list[str]) -> OwnershipDelta:
    """Parse OWNERS|, ROADS|, CITY| lines from build_ownership_delta."""
    owner_changes: list[tuple[int, int]] = []
    road_changes: list[tuple[int, int]] = []
    cities: list[tuple[int, int, int, int]] = []

    for line in lines:
        if line.startswith("OWNERS|"):
            for pair in line.split("|")[1:]:
                vals = pair.split(",")
                if len(vals) == 2:
                    owner_changes.append((int(vals[0]), int(vals[1])))
        elif line.startswith("ROADS|"):
            for pair in line.split("|")[1:]:
                vals = pair.split(",")
                if len(vals) == 2:
                    road_changes.append((int(vals[0]), int(vals[1])))
        elif line.startswith("CITY|"):
            parts = line.split("|")
            if len(parts) >= 4:
                xy = parts[1].split(",")
                name = parts[4] if len(parts) >= 5 else ""
                cities.append(
                    (int(xy[0]), int(xy[1]), int(parts[2]), int(parts[3]), name)
                )

    return OwnershipDelta(
        owner_changes=owner_changes,
        road_changes=road_changes,
        cities=cities,
    )


def build_revealed_tiles_seed_query() -> str:
    """GameCore: return all currently revealed tile coordinates.

    Used once per session to seed the spatial tracker's revealed-tile set.
    Output: ``REVEALED|x1,y1;x2,y2;...`` (semicolon-separated pairs).
    """
    return """
local me = Game.GetLocalPlayer()
local vis = PlayersVisibility[me]
local parts = {}
for idx = 0, Map.GetPlotCount() - 1 do
    local p = Map.GetPlotByIndex(idx)
    if vis:IsRevealed(p:GetX(), p:GetY()) then
        table.insert(parts, p:GetX() .. "," .. p:GetY())
    end
end
print("REVEALED|" .. table.concat(parts, ";"))
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def parse_revealed_tiles_seed(lines: list[str]) -> set[tuple[int, int]]:
    """Parse the bulk revealed-tiles response into a coordinate set."""
    tiles: set[tuple[int, int]] = set()
    for line in lines:
        if not line.startswith("REVEALED|"):
            continue
        data = line[len("REVEALED|") :]
        if not data:
            break
        for pair in data.split(";"):
            xy = pair.split(",")
            if len(xy) == 2:
                tiles.add((int(xy[0]), int(xy[1])))
    return tiles
