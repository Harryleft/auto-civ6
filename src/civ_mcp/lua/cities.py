"""Cities domain — Lua builders and parsers."""

from __future__ import annotations

import re
import json
from urllib.parse import unquote

from civ_mcp.lua._helpers import (
    _ITEM_PARAM_MAP,
    _LUA_KNOWN_RESOURCE,
    _ITEM_TABLE_MAP,
    SENTINEL,
    _bail,
    _bail_lua,
    _lua_get_city,
)
from civ_mcp.lua.models import CityInfo, ProductionOption, PurchaseQuote
from civ_mcp.lua.purchase_facts import LUA_PURCHASE_FACTS
from civ_mcp.lua.resource_facts import LUA_RESOURCE_FACTS


def build_cities_query() -> str:
    return _LUA_KNOWN_RESOURCE + LUA_RESOURCE_FACTS + """
local me = Game.GetLocalPlayer()
local hashName = {}
for u in GameInfo.Units() do hashName[u.Hash] = u.UnitType end
for b in GameInfo.Buildings() do hashName[b.Hash] = b.BuildingType end
for d in GameInfo.Districts() do hashName[d.Hash] = d.DistrictType end
for p in GameInfo.Projects() do hashName[p.Hash] = p.ProjectType end
local cityCoords = {}
for i, c in Players[me]:GetCities():Members() do
    local nm = Locale.Lookup(c:GetName()):gsub("|", "/")
    local bq = c:GetBuildQueue()
    local producing = "nothing"
    local turnsLeft = 0
    if bq:GetSize() > 0 then
        local h = bq:GetCurrentProductionTypeHash()
        if h == 0 then
            -- A nonempty queue with no item hash is not evidence of idle.
            -- Queries report the anomaly without editing the queue.
            producing = "CORRUPTED_QUEUE"
        else
            producing = hashName[h] or "UNKNOWN"
        end
        turnsLeft = bq:GetTurnsLeft()
    end
    local g = c:GetGrowth()
    local amTotal = g:GetAmenities()
    -- City defense info
    local defStr, garHP, garMax, wallHP, wallMax = 0, 0, 0, 0, 0
    local ccIdx = GameInfo.Districts["DISTRICT_CITY_CENTER"].Index
    for _, d in c:GetDistricts():Members() do
        if d:GetType() == ccIdx then
            local ok, _ = pcall(function()
                defStr = d:GetDefenseStrength() or 0
                garMax = d:GetMaxDamage(DefenseTypes.DISTRICT_GARRISON) or 0
                garHP = garMax - (d:GetDamage(DefenseTypes.DISTRICT_GARRISON) or 0)
                wallMax = d:GetMaxDamage(DefenseTypes.DISTRICT_OUTER) or 0
                wallHP = wallMax - (d:GetDamage(DefenseTypes.DISTRICT_OUTER) or 0)
            end)
            break
        end
    end
    local cityTargets = {}
    local cityTargetsKnown = wallMax == 0
    if wallMax > 0 then
        local ok = pcall(function()
            -- Native WorldInput uses paired PLOTS/MODIFIERS, not every nearby
            -- foreign unit. Eligibility is current; the action checks it again.
            local result = CityManager.GetCommandTargets(c, CityCommandTypes.RANGE_ATTACK, {})
            assert(type(result) == "table")
            local plots = result[CityCommandResults.PLOTS]
            local modifiers = result[CityCommandResults.MODIFIERS]
            assert(type(plots) == "table" and type(modifiers) == "table" and #plots == #modifiers)
            for index, plotIndex in ipairs(plots) do
                if modifiers[index] == CityCommandResults.MODIFIER_IS_TARGET then
                    local plot = Map.GetPlotByIndex(plotIndex)
                    assert(plot and PlayersVisibility[me]:IsVisible(plotIndex) ~= nil)
                    if PlayersVisibility[me]:IsVisible(plotIndex) then
                        local tx, ty = plot:GetX(), plot:GetY()
                        local params = {[CityCommandTypes.PARAM_X]=tx,[CityCommandTypes.PARAM_Y]=ty}
                        local allowed = CityManager.CanStartCommand(c, CityCommandTypes.RANGE_ATTACK, params)
                        assert(type(allowed) == "boolean")
                        if allowed then
                            local units = Map.GetUnitsAt(tx, ty)
                            if units then for other in units:Units() do
                                local atWar = Players[me]:GetDiplomacy():IsAtWarWith(other:GetOwner())
                                assert(type(atWar) == "boolean")
                                local visible=PlayersVisibility[me]:IsUnitVisible(other)
                                assert(type(visible)=="boolean", "UNIT_VISIBILITY_UNKNOWN")
                                if other:GetOwner() ~= me and atWar and visible then
                                    local info = GameInfo.Units[other:GetType()]
                                    local name = info and info.UnitType or "UNKNOWN"
                                    local hp = other:GetMaxDamage() - other:GetDamage()
                                    table.insert(cityTargets, name .. "@" .. tx .. "," .. ty .. "(" .. hp .. "hp)")
                                end
                            end end
                        end
                    end
                end
            end
        end)
        cityTargetsKnown = ok
        if not ok then cityTargets = {} end
    end
    local pillDistricts = {}
    local distLocs = {}
    for _, d in c:GetDistricts():Members() do
        local dInfo = GameInfo.Districts[d:GetType()]
        if dInfo and dInfo.DistrictType ~= "DISTRICT_CITY_CENTER" then
            table.insert(distLocs, dInfo.DistrictType .. "@" .. d:GetX() .. "," .. d:GetY())
        end
        if d:IsPillaged() then
            if dInfo then table.insert(pillDistricts, dInfo.DistrictType) end
        end
    end
    local pillBuildings = {}
    local allBuildings = {}
    local pBuildings = c:GetBuildings()
    for bldg in GameInfo.Buildings() do
        if pBuildings:HasBuilding(bldg.Index) then
            table.insert(allBuildings, (bldg.BuildingType:gsub("BUILDING_", "")))
            if pBuildings:IsPillaged(bldg.Index) then
                table.insert(pillBuildings, bldg.BuildingType)
            end
        end
    end
    -- Scan owned tiles for unimproved resources and pillaged improvements
    local unimproved = {}
    local archaeology = {}
    local resourceVisibilityKnown = true
    local pillImprov = {}
    local cx2, cy2 = c:GetX(), c:GetY()
    for dy = -3, 3 do for dx = -3, 3 do
        local px, py = cx2 + dx, cy2 + dy
        local plot = Map.GetPlot(px, py)
        if plot and plot:GetOwner() == me then
            -- A placed district already reserves its tile, even while unfinished.
            -- Missing native district facts cannot prove an improvement candidate.
            local okDistrict, district = pcall(function() return plot:GetDistrictType() end)
            if (px ~= cx2 or py ~= cy2) and okDistrict and district == -1 then
                local res = knownResourceIndex(me, plot)
                if res == nil then resourceVisibilityKnown = false end
                local imp = plot:GetImprovementType()
                if res and res >= 0 and imp < 0 then
                    local resInfo = GameInfo.Resources[res]
                    if resInfo then
                        local hint=resInfo.ResourceType:gsub("RESOURCE_","") .. "@" .. px .. "," .. py
                        if resourceWorkUnit(resInfo)=='UNIT_ARCHAEOLOGIST'then table.insert(archaeology,hint)
                        else table.insert(unimproved,hint)end
                    end
                end
                if imp >= 0 then
                    local okP, pil = pcall(function() return plot:IsImprovementPillaged() end)
                    if okP and pil then
                        local impInfo = GameInfo.Improvements[imp]
                        if impInfo then
                            table.insert(pillImprov, impInfo.ImprovementType:gsub("IMPROVEMENT_","") .. "@" .. px .. "," .. py)
                        end
                    end
                end
            end
        end
    end end
    table.insert(cityCoords, {name=nm, x=c:GetX(), y=c:GetY()})
    local loy, loyMax, loyPT, loyFlip = 100, 100, 0, 0
    local cult = c:GetCulturalIdentity()
    if cult then
        loy = cult:GetLoyalty()
        loyMax = cult:GetMaxLoyalty()
        loyPT = cult:GetLoyaltyPerTurn()
        loyFlip = cult:GetTurnsToConversion()
    end
    local garrisonUnit = ""
    local garFound = false
    local garUnitsAt = Map.GetUnitsAt(c:GetX(), c:GetY())
    if garUnitsAt then
        for gu in garUnitsAt:Units() do
            if not garFound and gu:GetOwner() == me then
                local guInfo = GameInfo.Units[gu:GetType()]
                if guInfo then
                    local fc = guInfo.FormationClass
                    if fc == "FORMATION_CLASS_LAND_COMBAT" or fc == "FORMATION_CLASS_NAVAL_COMBAT" then
                        garrisonUnit = guInfo.UnitType
                        garFound = true
                    end
                end
            end
        end
    end
    print(c:GetID() .. "|" .. nm .. "|" .. c:GetX() .. "," .. c:GetY() .. "|" .. c:GetPopulation() .. "|" .. string.format("%.1f|%.1f|%.1f|%.1f|%.1f|%.1f", c:GetYield(0), c:GetYield(1), c:GetYield(2), c:GetYield(3), c:GetYield(4), c:GetYield(5)) .. "|" .. string.format("%.1f", g:GetHousing()) .. "|" .. amTotal .. "|" .. g:GetTurnsUntilGrowth() .. "|" .. producing .. "|" .. turnsLeft .. "|" .. defStr .. "|" .. garHP .. "/" .. garMax .. "|" .. wallHP .. "/" .. wallMax .. "|" .. table.concat(cityTargets, ";") .. "|" .. table.concat(pillDistricts, ";") .. "|" .. table.concat(distLocs, ";") .. "|" .. string.format("%.1f|%.1f|%.1f|%d", loy, loyMax, loyPT, loyFlip) .. "|" .. string.format("%.1f|%.1f|%d", g:GetFoodSurplus(), g:GetFood(), g:GetGrowthThreshold()) .. "|" .. table.concat(pillBuildings, ";") .. "|" .. garrisonUnit)
    print("CITYATTACKS|" .. c:GetID() .. "|" .. tostring(cityTargetsKnown))
    print("CITYRESOURCES|" .. c:GetID() .. "|" .. tostring(resourceVisibilityKnown))
    if #archaeology>0 then print('CITYSITES|'..c:GetID()..'|'..table.concat(archaeology,';'))end
    if #unimproved > 0 or #pillImprov > 0 then
        print("CITYTILES|" .. c:GetID() .. "|" .. table.concat(unimproved, ";") .. "|" .. table.concat(pillImprov, ";"))
    end
    if #allBuildings > 0 then
        print("CITYBLDG|" .. c:GetID() .. "|" .. table.concat(allBuildings, ","))
    end
    -- Emit completion facts only when the native observation is complete.
    -- Missing facts remain unknown; placed districts can still be unfinished.
    pcall(function()
        local completed = {}
        for _, d in c:GetDistricts():Members() do
            local info = GameInfo.Districts[d:GetType()]
            if info and d:IsComplete() then
                table.insert(completed, info.DistrictType .. "@" .. d:GetX() .. "," .. d:GetY())
            end
        end
        local civ = PlayerConfigurations[me]:GetCivilizationTypeName()
        local seed = GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
        print("CITYFACTS|" .. c:GetID() .. "|" .. c:GetOwner() .. "|" .. civ .. "|" .. tostring(seed) .. "|" .. table.concat(completed, ";"))
    end)
end
for i = 1, #cityCoords do for j = i + 1, #cityCoords do
    local d = Map.GetPlotDistance(cityCoords[i].x, cityCoords[i].y, cityCoords[j].x, cityCoords[j].y)
    print("DIST|" .. cityCoords[i].name .. "|" .. cityCoords[j].name .. "|" .. d)
end end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def build_city_attack(city_id: int, target_x: int, target_y: int, *, witness_token: str | None = None) -> str:
    """InGame context: fire city ranged attack at a target tile."""
    witness = ""
    if witness_token is not None:
        witness = f'''
local prior = __civmcpCityAttack
if prior and not prior.closed then
    {_bail("ERR:OUTCOME_UNKNOWN|Prior native city attack witness is unresolved; no shot submitted.")}
end
if prior and prior.handler then
    pcall(function() Events.CityCommandStarted.Remove(prior.handler) end)
end
local cfg = PlayerConfigurations[me]
local q = {{token="{witness_token}", owner=me, cityId=pCity:GetID(),
    districtOwner=cityCenter:GetOwner(), districtId=cityCenter:GetID(),
    civ=cfg:GetCivilizationTypeName(), seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),
    turn=Game.GetCurrentGameTurn(), command=CityCommandTypes.RANGE_ATTACK,
    targetX={target_x}, targetY={target_y}, targetOwner=enemy:GetOwner(),
    targetId=enemy:GetID(), preHP=eHP, started=false, closed=false}}
__civmcpCityAttack = q
q.handler = function(cityOwnerID, cityID, districtOwnerID, districtID, commandType, iData1)
    pcall(function()
        if __civmcpCityAttack ~= q or q.closed or q.started then return end
        if cityOwnerID ~= q.owner or cityID ~= q.cityId
            or districtOwnerID ~= q.districtOwner or districtID ~= q.districtId
            or commandType ~= q.command then return end
        if Game.GetLocalPlayer() ~= q.owner or Game.GetCurrentGameTurn() ~= q.turn then return end
        local currentCfg = PlayerConfigurations[q.owner]
        if currentCfg:GetCivilizationTypeName() ~= q.civ
            or GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED") ~= q.seed then return end
        -- Native events echo neither nonce nor target. Attribution relies on
        -- this prearmed actual target and one serialized unresolved command.
        -- iData1 is untyped; never interpret it as a target or plot ID.
        q.started = true
        q.rawCommand, q.rawData = tostring(commandType), tostring(iData1)
        pcall(function() Events.CityCommandStarted.Remove(q.handler) end)
    end)
end
local armed = pcall(function() Events.CityCommandStarted.Add(q.handler) end)
if not armed then
    q.closed = true
    pcall(function() Events.CityCommandStarted.Remove(q.handler) end)
    {_bail("ERR:CITY_ATTACK_WITNESS_UNAVAILABLE|Native command witness unavailable; no shot submitted.")}
end
'''
    return f"""
{_lua_get_city(city_id)}
local cx, cy = pCity:GetX(), pCity:GetY()
local dist = Map.GetPlotDistance(cx, cy, {target_x}, {target_y})
local enemy = nil
local pu = Map.GetUnitsAt({target_x}, {target_y})
if pu then for other in pu:Units() do if other:GetOwner() ~= me then enemy = other end end end
if not enemy then {_bail("ERR:NO_ENEMY|No hostile unit at target tile")} end
local eInfo = GameInfo.Units[enemy:GetType()]
local eName = eInfo and eInfo.UnitType or "UNKNOWN"
local eHP = enemy:GetMaxDamage() - enemy:GetDamage()
local targetTag = "|target_owner:" .. enemy:GetOwner() .. "|target_id:" .. enemy:GetID()
local params = {{}}
params[CityCommandTypes.PARAM_X] = {target_x}
params[CityCommandTypes.PARAM_Y] = {target_y}
-- Pre-checks for specific error messages
local ccIdx = GameInfo.Districts["DISTRICT_CITY_CENTER"].Index
local hasWalls = false
local cityCenter = nil
for _, d in pCity:GetDistricts():Members() do
    if d:GetType() == ccIdx then
        cityCenter = d
        local wHP = d:GetMaxDamage(DefenseTypes.DISTRICT_OUTER)
        if wHP and wHP > 0 then hasWalls = true end
        break
    end
end
if not hasWalls then
    {_bail("ERR:NO_WALLS|City has no walls — build Ancient Walls first")}
end
if dist > 2 then
    {_bail_lua('"ERR:OUT_OF_RANGE|Target is " .. dist .. " tiles away (city attack range is 2)"')}
end
-- Match native WorldInput's paired arrays. Other numeric results do not
-- authorize a shot; an empty set cannot diagnose already-fired versus LOS.
local validTargets = CityManager.GetCommandTargets(pCity, CityCommandTypes.RANGE_ATTACK, {{}})
local plots = validTargets and validTargets[CityCommandResults.PLOTS]
local modifiers = validTargets and validTargets[CityCommandResults.MODIFIERS]
if type(plots) ~= "table" or type(modifiers) ~= "table" or #plots ~= #modifiers then
    {_bail("ERR:TARGETS_UNKNOWN|Native city ranged targets unavailable; no shot submitted")}
end
local targetPlotIdx = {target_y} * Map.GetGridSize() + {target_x}
local inTargets = false
for i,idx in ipairs(plots) do
    if idx == targetPlotIdx and modifiers[i] == CityCommandResults.MODIFIER_IS_TARGET then inTargets=true end
end
if not inTargets then
    {_bail("ERR:TARGET_NOT_AVAILABLE|Native ranged targets do not allow this tile; cause not reported")}
end
local canAttack = CityManager.CanStartCommand(pCity, CityCommandTypes.RANGE_ATTACK, params)
if not canAttack then
    {_bail("ERR:CANNOT_ATTACK|City cannot attack this target (unknown reason)")}
end
{witness}
CityManager.RequestCommand(pCity, CityCommandTypes.RANGE_ATTACK, params)
print("OK:CITY_RANGE_ATTACK|" .. Locale.Lookup(pCity:GetName()) .. " -> " .. eName .. "@{target_x},{target_y}|pre_hp:" .. eHP .. "/" .. enemy:GetMaxDamage() .. targetTag)
print("{SENTINEL}")
"""


def build_city_attack_followup(city_id: int, target_x: int, target_y: int, witness_token: str) -> str:
    """Read this prearmed native request record and the ordinary target facts."""
    from civ_mcp.lua.units import build_attack_followup_query

    return f'''
pcall(function()
    local q = __civmcpCityAttack
    local me = Game.GetLocalPlayer()
    if not q or q.token ~= "{witness_token}" or q.owner ~= me
        or q.turn ~= Game.GetCurrentGameTurn() then return end
    local cfg = PlayerConfigurations[me]
    if q.civ ~= cfg:GetCivilizationTypeName()
        or q.seed ~= GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
        or q.command ~= CityCommandTypes.RANGE_ATTACK
        or q.targetX ~= {target_x} or q.targetY ~= {target_y} then return end
    local city = CityManager.GetCity(me, {city_id} % 65536)
    if not city or city:GetID() ~= q.cityId or city:GetOwner() ~= q.owner then return end
    local center = nil
    local ccIdx = GameInfo.Districts["DISTRICT_CITY_CENTER"].Index
    for _, d in city:GetDistricts():Members() do
        if d:GetType() == ccIdx then center = d; break end
    end
    if not center or center:GetID() ~= q.districtId or center:GetOwner() ~= q.districtOwner
        or center:GetCity():GetID() ~= q.cityId then return end
    print("CITY_ATTACK_SOURCE|" .. me .. "|" .. city:GetID() .. "|" .. center:GetOwner() .. "|" .. center:GetID())
    print("CITY_ATTACK_WITNESS|" .. q.token .. "|" .. q.owner .. "|" .. q.cityId
        .. "|" .. q.districtOwner .. "|" .. q.districtId .. "|" .. q.civ .. "|" .. q.seed
        .. "|" .. q.turn .. "|" .. q.targetX .. "|" .. q.targetY .. "|" .. q.targetOwner .. "|" .. q.targetId
        .. "|started:" .. tostring(q.started) .. "|command_matches:" .. tostring(q.command == CityCommandTypes.RANGE_ATTACK)
        .. "|rawCommand:" .. tostring(q.rawCommand) .. "|rawData:" .. tostring(q.rawData))
    if UI.IsGameCoreBusy() ~= false then return end
    local changedHP = false
    -- A started command can outlive the defender; its cleanup must not
    -- depend on looking up the vanished unit. The legacy HP path also
    -- requires the same visible tile used by ordinary combat followup.
    if not q.started then
        local plot = Map.GetPlot(q.targetX, q.targetY)
        local visibility = PlayersVisibility[me]
        if plot and visibility and visibility:IsVisible(plot:GetIndex()) then
            local target = Players[q.targetOwner] and Players[q.targetOwner]:GetUnits():FindID(q.targetId)
            changedHP = target and target:GetID() == q.targetId
                and target:GetX() == q.targetX and target:GetY() == q.targetY
                and target:GetMaxDamage() - target:GetDamage() ~= q.preHP
        end
    end
    if q.started or changedHP then
        q.closed = true
        pcall(function() Events.CityCommandStarted.Remove(q.handler) end)
    end
end)
''' + build_attack_followup_query(target_x, target_y)


def build_city_production_query(city_id: int, item_type: str | None = None, item_name: str | None = None) -> str:
    """Returns Lua that lists what the city can produce. Actual production setting
    needs the item type, so this is a two-step process: list, then set."""
    return f"""
{_lua_get_city(city_id)}
{LUA_PURCHASE_FACTS}
if pCity:GetOwner()~=me then print("ERR:CITY_NOT_OWNED");print("{SENTINEL}");return end
local bq = pCity:GetBuildQueue()
local filterKind={json.dumps(item_type) if item_type else 'nil'}
local filterName={json.dumps(item_name) if item_name else 'nil'}
local productionReadUnknown=false
local function candidate(kind,item)
 local name=kind=="UNIT" and item.UnitType or item.BuildingType
 if filterName and (filterKind~=kind or filterName~=name) then return end
 if not filterName and kind=="BUILDING" and pCity:GetBuildings():HasBuilding(item.Index) then return end
 local prodOK,prodVisible=pcall(function() return bq:CanProduce(item.Hash,true) end)
 if not prodOK or type(prodVisible)~="boolean" then productionReadUnknown=true end
 local canProduce=nil
 if prodOK and type(prodVisible)=="boolean" then
  canProduce=false
  if prodVisible and item.MustPurchase~=1 and item.MustPurchase~=true then
   local params={{}}
   params[kind=="UNIT" and CityOperationTypes.PARAM_UNIT_TYPE or CityOperationTypes.PARAM_BUILDING_TYPE]=item.Hash
   local ok,value=pcall(CityManager.CanStartOperation,pCity,CityOperationTypes.BUILD,params,true)
   if ok and type(value)=="boolean" then canProduce=value else canProduce=nil end
  end
 end
 local gold=purchaseFacts(pCity,kind,item,"YIELD_GOLD",true)
 local faith=purchaseFacts(pCity,kind,item,"YIELD_FAITH",true)
 if gold.capacity_reached or faith.capacity_reached then
  canProduce=false
 end
 if not filterName and prodVisible~=true and gold.visible~=true and faith.visible~=true then return end
 local t=0;if canProduce then pcall(function() t=bq:GetTurnsLeft(item.Hash) end) end
 local cost=item.Cost
 pcall(function() local value=bq:GetProductionCost(item.Hash);if value>=0 then cost=value end end)
 print(kind.."|"..name.."|"..cost.."|"..t.."|"..(gold.cost or -1))
 print("PRODUCIBLE|"..kind.."|"..name.."|"..tostring(canProduce))
 printPurchase(kind,name,gold);printPurchase(kind,name,faith)
end
if filterName then
 local item=(filterKind=="UNIT" and GameInfo.Units or GameInfo.Buildings)[filterName]
 if not item then print("ERR:ITEM_UNAVAILABLE");print("{SENTINEL}");return end
 candidate(filterKind,item)
else
 for unit in GameInfo.Units() do candidate("UNIT",unit) end
 for bldg in GameInfo.Buildings() do candidate("BUILDING",bldg) end
end
if not filterName and productionReadUnknown then
 print("ERR:PRODUCTION_OPTIONS_INCOMPLETE");print("{SENTINEL}");return
end
if not filterName then
print("DISTRICTS:")
for dist in GameInfo.Districts() do
    if bq:CanProduce(dist.Hash, true) then
        local t = bq:GetTurnsLeft(dist.Hash)
        local adjCost = dist.Cost
        pcall(function() local c = bq:GetProductionCost(dist.Hash); if c > 0 then adjCost = math.floor(c) end end)
        print("DISTRICT|" .. dist.DistrictType .. "|" .. adjCost .. "|" .. t .. "|-1")
    end
end
print("PROJECTS:")
for proj in GameInfo.Projects() do
    if bq:CanProduce(proj.Hash, true) then
        local t = bq:GetTurnsLeft(proj.Hash)
        local adjCost = proj.Cost
        pcall(function() local c = bq:GetProductionCost(proj.Hash); if c > 0 then adjCost = math.floor(c) end end)
        print("PROJECT|" .. proj.ProjectType .. "|" .. adjCost .. "|" .. t .. "|-1")
    end
end
-- Pillaged districts/buildings that can be repaired via production queue
print("REPAIRS:")
local pBuildings = pCity:GetBuildings()
for _, d in pCity:GetDistricts():Members() do
    if d:IsPillaged() then
        local dInfo = GameInfo.Districts[d:GetType()]
        if dInfo and dInfo.DistrictType ~= "DISTRICT_CITY_CENTER" then
            local repParams = {{}}
            repParams[CityOperationTypes.PARAM_DISTRICT_TYPE] = dInfo.Hash
            repParams[CityOperationTypes.PARAM_X] = d:GetX()
            repParams[CityOperationTypes.PARAM_Y] = d:GetY()
            local canRepair = CityManager.CanStartOperation(pCity, CityOperationTypes.BUILD, repParams, true)
            if canRepair then
                local t = bq:GetTurnsLeft(dInfo.Hash)
                local adjCost = dInfo.Cost
                pcall(function() local c = bq:GetProductionCost(dInfo.Hash); if c > 0 then adjCost = math.floor(c) end end)
                print("DISTRICT|" .. dInfo.DistrictType .. "|" .. adjCost .. "|" .. t .. "|-1|REPAIR|" .. d:GetX() .. "," .. d:GetY())
            end
        end
    end
end
for bldg in GameInfo.Buildings() do
    if pBuildings:HasBuilding(bldg.Index) and pBuildings:IsPillaged(bldg.Index) then
        local repCheck = {{}}
        repCheck[CityOperationTypes.PARAM_BUILDING_TYPE] = bldg.Hash
        local canRepair = CityManager.CanStartOperation(pCity, CityOperationTypes.BUILD, repCheck, true)
        if canRepair then
            local t = bq:GetTurnsLeft(bldg.Hash)
            local adjCost = bldg.Cost
            pcall(function() local c = bq:GetProductionCost(bldg.Hash); if c > 0 then adjCost = math.floor(c) end end)
            print("BUILDING|" .. bldg.BuildingType .. "|" .. adjCost .. "|" .. t .. "|-1|REPAIR")
        end
    end
end
end
print("{SENTINEL}")
"""


def build_produce_item(
    city_id: int,
    item_type: str,
    item_name: str,
    target_x: int | None = None,
    target_y: int | None = None,
) -> str:
    """Set production for a city via CityManager.RequestOperation (InGame context).

    item_type is UNIT/BUILDING/DISTRICT, item_name is e.g. UNIT_WARRIOR.
    Uses .Hash for item refs and VALUE_REPLACE_AT position 0 to replace current production.
    For districts, pass target_x/target_y to specify placement tile.
    """
    itype = item_type.upper()
    table_name = _ITEM_TABLE_MAP.get(itype, "Units")
    param_key = _ITEM_PARAM_MAP.get(itype, "PARAM_UNIT_TYPE")
    # Districts require placement coordinates
    if itype == "DISTRICT" and (target_x is None or target_y is None):
        return (
            f'print("ERR:MISSING_COORDS|{item_name} is a district and requires '
            f"target_x/target_y for placement. Use get_district_advisor(city_id, "
            f"'{item_name}') to find the best tile.\")\n"
            f'print("{SENTINEL}")'
        )
    # Extra params for district placement
    xy_params = ""
    if target_x is not None and target_y is not None:
        xy_params = f"tParams[CityOperationTypes.PARAM_X] = {target_x}\ntParams[CityOperationTypes.PARAM_Y] = {target_y}"
    return f"""
{_lua_get_city(city_id)}
local item = GameInfo.{table_name}["{item_name}"]
if item == nil then {_bail(f"ERR:ITEM_NOT_FOUND|{item_name}")} end
local bq = pCity:GetBuildQueue()
if "{itype}" ~= "DISTRICT" and not bq:CanProduce(item.Hash, true) then
    -- Diagnose why production is blocked
    local reason = ""
    pcall(function()
        if item.PrereqDistrict then
            local hasDistrict = false
            for _, d in pCity:GetDistricts():Members() do
                local dInfo = GameInfo.Districts[d:GetType()]
                if dInfo and dInfo.DistrictType == item.PrereqDistrict then hasDistrict = true; break end
            end
            if not hasDistrict then reason = " (requires " .. item.PrereqDistrict .. " district)" end
        end
        if reason == "" and item.PrereqBuildingType then
            reason = " (requires " .. item.PrereqBuildingType .. ")"
        end
        if reason == "" then
            -- Check if building is already built in this city
            local buildings = pCity:GetBuildings()
            if buildings and buildings:HasBuilding(item.Index) then
                reason = " (already built)"
            end
        end
    end)
    {
        _bail_lua(
            f'"ERR:CANNOT_PRODUCE|{item_name} cannot be produced in this city" .. reason'
        )
    }
end
{
        ""
        if itype != "BUILDING" or (target_x is not None and target_y is not None)
        else f'''if item.IsWonder then
    {_bail(f"ERR:MISSING_COORDS|{item_name} is a wonder and requires target_x/target_y for placement. Use get_wonder_advisor(city_id, '{item_name}') to find valid tiles.")}
end'''
    }
-- Trader cap check: game silently rejects when count >= route capacity
if "{item_name}" == "UNIT_TRADER" then
    local pTrade = Players[me]:GetTrade()
    local traderCount = 0
    for _, u in Players[me]:GetUnits():Members() do
        if GameInfo.Units[u:GetType()].UnitType == "UNIT_TRADER" then traderCount = traderCount + 1 end
    end
    local routeCap = pTrade:GetOutgoingRouteCapacity()
    if traderCount >= routeCap then
        print("ERR:TRADER_CAP|Cannot build Trader: you have " .. traderCount .. " Traders but only " .. routeCap .. " trade route capacity. Build Markets or Lighthouses to increase capacity.")
        print("{SENTINEL}")
        return
    end
end
local tParams = {{}}
tParams[CityOperationTypes.{param_key}] = item.Hash
{xy_params}
-- Check exactly the operation that will be submitted, including replacement
-- mode. A reliable refusal is a completed rejection, never a pending action.
tParams[CityOperationTypes.PARAM_INSERT_MODE] = CityOperationTypes.VALUE_EXCLUSIVE
local checkOk, canStart, checkResults = pcall(function()
    return CityManager.CanStartOperation(pCity, CityOperationTypes.BUILD, tParams, true)
end)
local target = "{f'target:{target_x},{target_y}|' if target_x is not None and target_y is not None else ''}"
if not checkOk or type(canStart) ~= "boolean" then
    print("ERR:PREFLIGHT_UNAVAILABLE|{item_name}|" .. target .. "not submitted|native eligibility unknown")
    print("{SENTINEL}")
    return
end
if canStart == false then
    local facts = {{}}
    -- This enum differs by context/build. Do not assume Command and Operation
    -- result keys are interchangeable, or turn missing reasons into a guess.
    pcall(function()
        local reasonKey = CityOperationResults and CityOperationResults.FAILURE_REASONS
        local reasons = reasonKey and type(checkResults) == "table" and checkResults[reasonKey]
        if type(reasons) == "table" then
            for _, reason in ipairs(reasons) do
                if type(reason) == "string" then
                    table.insert(facts, "native:" .. Locale.Lookup(reason):gsub("|", "/"))
                end
            end
        end
    end)
    pcall(function()
        local x, y = tParams[CityOperationTypes.PARAM_X], tParams[CityOperationTypes.PARAM_Y]
        local plot = x ~= nil and y ~= nil and Map.GetPlot(x, y)
        if plot then
            table.insert(facts, "owner:" .. tostring(plot:GetOwner()))
            local terrain = GameInfo.Terrains[plot:GetTerrainType()]
            if terrain then table.insert(facts, "terrain:" .. terrain.TerrainType) end
            local feature = GameInfo.Features[plot:GetFeatureType()]
            if feature then table.insert(facts, "feature:" .. feature.FeatureType) end
            local district = GameInfo.Districts[plot:GetDistrictType()]
            if district then table.insert(facts, "existing_district:" .. district.DistrictType) end
        end
    end)
    pcall(function()
        local tech = item.PrereqTech and GameInfo.Technologies[item.PrereqTech]
        if tech then
            table.insert(facts, "prereq_tech:" .. item.PrereqTech .. "|has_tech:" .. tostring(Players[me]:GetTechs():HasTech(tech.Index)))
        end
    end)
    if #facts == 0 then table.insert(facts, "native reason unavailable") end
    print("ERR:CANNOT_START|{item_name}|" .. target .. "not submitted|" .. table.concat(facts, "|"))
    print("{SENTINEL}")
    return
end
CityManager.RequestOperation(pCity, CityOperationTypes.BUILD, tParams)
local turnsLeft = bq:GetTurnsLeft(item.Hash)
print("OK:PRODUCING|{item_name}|" .. turnsLeft .. " turns")
print("{SENTINEL}")
"""


def build_verify_production(
    city_id: int, item_name: str, district_position: tuple[int, int] | None = None
) -> str:
    """Read-only verification after RequestOperation, in the native Lua context.

    District entity coordinates and queue hashes require InGame. Other
    production keeps the existing GameCore CurrentlyBuilding() readback.
    """
    if district_position is not None:
        current_item = '''
local productionHash = bq:GetCurrentProductionTypeHash()
local row = productionHash and productionHash ~= 0 and GameInfo.Districts[productionHash]
local cur = row and row.DistrictType
'''
        # Queue identity alone cannot prove which tile received a district.
        # Report city-owned district entities; GameState matches the exact intent.
        verification = '''
print("PRODUCTION|" .. tostring(cur))
for _, district in pCity:GetDistricts():Members() do
    local row = GameInfo.Districts[district:GetType()]
    if row then
        print("DISTRICT|" .. row.DistrictType .. "|" .. district:GetX() .. "|" .. district:GetY())
    end
end
'''
    else:
        current_item = "local cur = bq:CurrentlyBuilding()"
        verification = f'''
if cur == "{item_name}" then
    print("CONFIRMED|production queue verified")
else
    print("NOT_SET|current=" .. tostring(cur) .. "|expected={item_name}")
end
'''
    return f"""
local me = Game.GetLocalPlayer()
local pCity = Players[me]:GetCities():FindID({city_id} % 65536)
if pCity == nil or pCity:GetID() ~= {city_id} then print("NOT_FOUND"); print("{SENTINEL}"); return end
local bq = pCity:GetBuildQueue()
{current_item}
-- GameCore GetTurnsLeft is not implemented on macOS. Confirmation uses
-- queue/placement facts; the submission response supplies the estimate.
{verification}
print("{SENTINEL}")
"""


def _city_focus_state(city_id: int, focus: str) -> str:
    return f"""
{_lua_get_city(city_id)}
local focus={json.dumps(focus)}
local ok,state=pcall(function()
 local owner,id,x,y=pCity:GetOwner(),pCity:GetID(),pCity:GetX(),pCity:GetY()
 local identity={{id,owner,x,y}}
 for i=1,4 do local value=identity[i];if type(value)~="number" or value%1~=0 then error("city identity unknown") end end
 if owner~=me or ({city_id}>=65536 and id~={city_id}) then error("city identity changed") end
 local citizens=pCity:GetCitizens()
 local result={{city=table.concat({{id,owner,x,y}},","),rows={{}},current={{}},goal={{}}}}
 local indices,names={{}},{{}}
 local targetFound=focus=="DEFAULT"
 for row in GameInfo.Yields() do
  local index,name=row.Index,row.YieldType
  if type(index)~="number" or index%1~=0 or index<0 or type(name)~="string" or not name:match("^YIELD_[A-Z0-9_]+$") or indices[index] or names[name] then error("yield identity unknown") end
  indices[index]=true;names[name]=true
  local favored,ignored=citizens:IsFavoredYield(index),citizens:IsDisfavoredYield(index)
  if type(favored)~="boolean" or type(ignored)~="boolean" then error("citizen focus unknown") end
  local wantedFavored=focus==name
  local wantedIgnored=focus~="DEFAULT" and focus~=name and ignored
  if focus==name then targetFound=true end
  local prefix=index..","..name..","
  table.insert(result.current,prefix..(favored and "1" or "0")..","..(ignored and "1" or "0"))
  table.insert(result.goal,prefix..(wantedFavored and "1" or "0")..","..(wantedIgnored and "1" or "0"))
  table.insert(result.rows,{{index=index,name=name,favored=favored,ignored=ignored,wantedFavored=wantedFavored,wantedIgnored=wantedIgnored}})
 end
 if #result.rows==0 or not targetFound then error("yield unavailable") end
 table.sort(result.current);table.sort(result.goal)
 result.current=table.concat(result.current,";");result.goal=table.concat(result.goal,";")
 return result
end)
if not ok then print("ERR:FOCUS_STATE_UNKNOWN|City identity or complete Citizens preferences unavailable"); print("{SENTINEL}"); return end
"""


def build_city_yield_focus_query(city_id: int, focus: str = "DEFAULT", *, witness_token: str | None = None, expected_city: str = "", expected_goal: str = "") -> str:
    """Read complete native citizen flags; optionally confirm one entered request."""
    code = _city_focus_state(city_id, focus)
    if witness_token is not None:
        return code + f"""
local receipt=type(__civmcpCityFocus)=="table" and __civmcpCityFocus[{city_id}%65536]
if receipt and receipt.token=={json.dumps(witness_token)} and receipt.complete==true and receipt.city==state.city and receipt.goal==state.current and state.city=={json.dumps(expected_city)} and state.current=={json.dumps(expected_goal)} then
 print("CITY_FOCUS_CONFIRMED|"..receipt.token.."|"..pCity:GetID().."|"..state.current)
else print("CITY_FOCUS_OBSERVED|"..pCity:GetID().."|"..state.current) end
print("{SENTINEL}")
"""
    return code + f"""
print("CITY_FOCUS_CITY|"..state.city)
print("CITY_FOCUS_CURRENT|"..state.current)
print("CITY_FOCUS_GOAL|"..state.goal)
for _,row in ipairs(state.rows) do print("FOCUS|"..row.name.."|"..(row.favored and "favored" or (row.ignored and "disfavored" or "neutral"))) end
print("{SENTINEL}")
"""


def build_set_yield_focus(city_id: int, yield_type: str, *, expected_city: str, expected_current: str, expected_goal: str, witness_token: str) -> str:
    """Submit an atomic target-state plan using official SET_FOCUS parameters."""
    return _city_focus_state(city_id, yield_type) + f"""
if state.city~={json.dumps(expected_city)} or state.current~={json.dumps(expected_current)} or state.goal~={json.dumps(expected_goal)} then print("ERR:FOCUS_CHANGED_NO_SUBMIT|City or citizen preferences changed"); print("{SENTINEL}"); return end
if type(CityManager.RequestCommand)~="function" or CityCommandTypes.SET_FOCUS==nil or CityCommandTypes.PARAM_FLAGS==nil or CityCommandTypes.PARAM_DATA0==nil or CityCommandTypes.PARAM_YIELD_TYPE==nil then print("ERR:FOCUS_COMMAND_NO_SUBMIT|Native SET_FOCUS unavailable"); print("{SENTINEL}"); return end
if __civmcpCityFocus~=nil and type(__civmcpCityFocus)~="table" then print("ERR:FOCUS_COMMAND_NO_SUBMIT|Receipt unavailable"); print("{SENTINEL}"); return end
__civmcpCityFocus=__civmcpCityFocus or {{}}
local receipt={{token={json.dumps(witness_token)},city=state.city,goal=state.goal,complete=false}}
__civmcpCityFocus[{city_id}%65536]=receipt
local function setFlag(index,flag,value)
 local params={{}}
 params[CityCommandTypes.PARAM_FLAGS]=flag
 params[CityCommandTypes.PARAM_YIELD_TYPE]=index
 params[CityCommandTypes.PARAM_DATA0]=value and 1 or 0
 CityManager.RequestCommand(pCity,CityCommandTypes.SET_FOCUS,params)
end
for _,row in ipairs(state.rows) do
 -- Official CityPanel SetYieldIgnore clears an ignored target before favoring it.
 if row.ignored~=row.wantedIgnored then setFlag(row.index,1,row.wantedIgnored) end
 if row.favored~=row.wantedFavored then setFlag(row.index,0,row.wantedFavored) end
end
receipt.complete=true
print("OK:CITY_FOCUS_REQUESTED|"..pCity:GetID())
print("{SENTINEL}")
"""


def _parse_tile_hints(value: str) -> list[str]:
    # Old rows also used commas between entries. Match complete NAME@x,y
    # tokens so their y coordinate can never become a separate resource.
    return re.findall(r"(?:^|[;,])([A-Z][A-Z0-9_]*@-?\d+,-?\d+)(?=$|[;,])", value)


def parse_cities_response(lines: list[str]) -> tuple[list[CityInfo], list[str]]:
    """Returns (cities, distance_lines) where distance_lines are 'A|B|N' strings."""
    cities = []
    distances: list[str] = []
    city_by_id: dict[int, CityInfo] = {}
    for line in lines:
        if line.startswith("DIST|"):
            p = line.split("|")
            if len(p) >= 4:
                distances.append(f"{p[1]} <-> {p[2]}: {p[3]} tiles")
            continue
        if line.startswith("CITYATTACKS|"):
            p = line.split("|")
            if len(p) == 3 and p[1].isdigit() and int(p[1]) in city_by_id:
                city_by_id[int(p[1])].attack_targets_known = {"true": True, "false": False}.get(p[2])
            continue
        if line.startswith("CITYRESOURCES|"):
            parts = line.split("|")
            if len(parts) == 3 and parts[1].isdigit():
                city = city_by_id.get(int(parts[1]))
                if city and parts[2] in ("true", "false"):
                    city.resource_visibility_known = parts[2] == "true"
            continue
        if line.startswith('CITYSITES|'):
            parts=line.split('|')
            if len(parts)==3 and parts[1].isdigit() and int(parts[1])in city_by_id:
                city_by_id[int(parts[1])].archaeology_sites=_parse_tile_hints(parts[2])
            continue
        if line.startswith("CITYTILES|"):
            p = line.split("|")
            if len(p) >= 4:
                cid = int(p[1])
                if cid in city_by_id:
                    city_by_id[cid].unimproved_resources = _parse_tile_hints(p[2])
                    city_by_id[cid].pillaged_improvements = _parse_tile_hints(p[3])
            continue
        if line.startswith("CITYFACTS|"):
            p = line.split("|")
            try:
                cid, owner, seed = int(p[1]), int(p[2]), int(p[4])
                if len(p) == 6 and p[3].startswith("CIVILIZATION_") and cid in city_by_id:
                    city = city_by_id[cid]
                    city.owner_id = owner
                    city.game_identity = (p[3].removeprefix("CIVILIZATION_").lower(), seed)
                    city.completed_districts = [d for d in p[5].split(";") if d]
            except (IndexError, ValueError):
                pass  # Partial/malformed observation cannot prove completion.
            continue
        if line.startswith("CITYBLDG|"):
            p = line.split("|")
            if len(p) >= 3:
                cid = int(p[1])
                if cid in city_by_id:
                    city_by_id[cid].buildings = [b for b in p[2].split(",") if b]
            continue
        parts = line.split("|")
        if len(parts) < 14:
            continue
        x_str, y_str = parts[2].split(",")

        def _split_hp(s: str) -> tuple[int, int]:
            if "/" in s:
                a, b = s.split("/")
                return int(a), int(b)
            return 0, 0

        def_str = int(parts[15]) if len(parts) > 15 and parts[15].isdigit() else 0
        gar_hp, gar_max = _split_hp(parts[16]) if len(parts) > 16 else (0, 0)
        wall_hp, wall_max = _split_hp(parts[17]) if len(parts) > 17 else (0, 0)
        cities.append(
            CityInfo(
                city_id=int(parts[0]),
                name=parts[1],
                x=int(x_str),
                y=int(y_str),
                population=int(parts[3]),
                food=float(parts[4]),
                production=float(parts[5]),
                gold=float(parts[6]),
                science=float(parts[7]),
                culture=float(parts[8]),
                faith=float(parts[9]),
                housing=float(parts[10]),
                amenities=int(parts[11]),
                turns_to_grow=int(parts[12]),
                currently_building=parts[13],
                production_turns_left=int(parts[14]) if len(parts) > 14 else 0,
                defense_strength=def_str,
                garrison_hp=gar_hp,
                garrison_max_hp=gar_max,
                wall_hp=wall_hp,
                wall_max_hp=wall_max,
                attack_targets=[
                    t for t in (parts[18].split(";") if len(parts) > 18 else []) if t
                ],
                pillaged_districts=[
                    d for d in (parts[19].split(";") if len(parts) > 19 else []) if d
                ],
                districts=[
                    d for d in (parts[20].split(";") if len(parts) > 20 else []) if d
                ],
                loyalty=float(parts[21]) if len(parts) > 21 else 100.0,
                loyalty_max=float(parts[22]) if len(parts) > 22 else 100.0,
                loyalty_per_turn=float(parts[23]) if len(parts) > 23 else 0.0,
                turns_to_loyalty_flip=int(parts[24]) if len(parts) > 24 else 0,
                food_surplus=float(parts[25]) if len(parts) > 25 else 0.0,
                food_stored=float(parts[26]) if len(parts) > 26 else 0.0,
                growth_threshold=int(parts[27]) if len(parts) > 27 else 0,
                pillaged_buildings=[
                    b for b in (parts[28].split(";") if len(parts) > 28 else []) if b
                ],
                garrison_unit=parts[29] if len(parts) > 29 else "",
            )
        )
        city_by_id[cities[-1].city_id] = cities[-1]
    return cities, distances


def parse_city_production_response(lines: list[str]) -> list[ProductionOption]:
    """Parse available production options from build_city_production_query query."""
    options = []
    indexed = {}
    for line in lines:
        if line.startswith('ERR:'):
            raise ValueError(line)
        if line.startswith(
            ("UNITS:", "BUILDINGS:", "DISTRICTS:", "PROJECTS:", "REPAIRS:")
        ):
            continue
        parts = line.split("|")
        if parts[0] == 'PRODUCIBLE':
            indexed[(parts[1], parts[2])].can_produce = {'true': True, 'false': False, 'nil': None}[parts[3]]
            continue
        if parts[0] == 'PURCHASE':
            if len(parts) != 9:
                raise ValueError('Malformed purchase quote')
            quote = PurchaseQuote(
                currency=parts[3], cost=float(parts[4]) if parts[4] != 'nil' else None,
                balance=float(parts[5]) if parts[5] != 'nil' else None,
                can_purchase={'true': True, 'false': False, 'nil': None}[parts[6]],
                visible={'true': True, 'false': False, 'nil': None}[parts[7]],
                reasons=[unquote(value) for value in parts[8].split(';') if value],
            )
            indexed[(parts[1], parts[2])].purchases.append(quote)
            continue
        if len(parts) >= 3 and parts[0] in ("UNIT", "BUILDING", "DISTRICT", "PROJECT"):
            is_repair = len(parts) > 5 and parts[5] == "REPAIR"
            repair_x = None
            repair_y = None
            if is_repair and parts[0] == "DISTRICT" and len(parts) > 6:
                coords = parts[6].split(",")
                if len(coords) == 2:
                    repair_x, repair_y = int(coords[0]), int(coords[1])
            options.append(
                ProductionOption(
                    category=parts[0],
                    item_name=parts[1],
                    cost=float(parts[2]),
                    turns=int(parts[3]) if len(parts) > 3 else 0,
                    gold_cost=float(parts[4]) if len(parts) > 4 else -1,
                    is_repair=is_repair,
                    repair_x=repair_x,
                    repair_y=repair_y,
                )
            )
            if not is_repair:
                indexed[(parts[0], parts[1])] = options[-1]
    return options
