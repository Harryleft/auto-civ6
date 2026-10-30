"""Great People domain — Lua builders and parsers."""

from __future__ import annotations

import math
from urllib.parse import unquote

from civ_mcp.lua._helpers import SENTINEL, _bail, _int, _lua_get_unit
from civ_mcp.lua.models import GPAdvisorCity, GPAdvisorResult, GreatPersonInfo


def build_great_people_query() -> str:
    """Get available Great People and recruitment progress (InGame context)."""
    return f"""
local me = Game.GetLocalPlayer()
local gp = Game.GetGreatPeople()
if gp == nil then {_bail("ERR:NO_GP_SYSTEM|Great People system not available")} end
local timeline = gp:GetTimeline()
if timeline == nil then {_bail("ERR:NO_TIMELINE|No great people timeline")} end
local function getAbility(entry,ind)
    -- The native popup consumes timeline text including usage and passive
    -- conditions. Modifier-name substrings cannot prove those mechanics.
    local parts={{}}
    for _,pair in ipairs({{{{"action",entry.ActionNameText}},{{"usage",entry.ActionUsageText}},
        {{"effect",entry.ActionEffectText}},{{"passive",entry.PassiveEffectText}}}}) do
        if type(pair[2])=="string" and pair[2]~="" then parts[#parts+1]=pair[1]..": "..pair[2] end
    end
    if ind.ActionCharges~=nil then parts[#parts+1]="definition action charges "..tostring(ind.ActionCharges) end
    local ok,conditions=pcall(function()return gp:GetEarnConditionsText(me,entry.Individual)end)
    if ok and type(conditions)=="string" and conditions~="" then parts[#parts+1]="earn conditions: "..conditions end
    if #parts==0 then parts[1]="Native timeline description unavailable; expand current rules before judging effects" end
    return table.concat(parts,"; "):gsub('%%','%%25'):gsub('|','%%7C'):gsub('\\r','%%0D'):gsub('\\n','%%0A')
end
for _, entry in ipairs(timeline) do
    if entry.Class ~= nil and entry.Individual ~= nil then
    local classInfo = GameInfo.GreatPersonClasses[entry.Class]
    local indivInfo = GameInfo.GreatPersonIndividuals[entry.Individual]
    if classInfo and indivInfo then
        local className = Locale.Lookup(classInfo.Name)
        local indivName = Locale.Lookup(indivInfo.Name)
        local eraInfo = GameInfo.Eras[entry.Era]
        local eraName = eraInfo and Locale.Lookup(eraInfo.Name) or "Unknown"
        local claimant = "Unclaimed"
        if entry.Claimant and entry.Claimant >= 0 then
            local cfg = PlayerConfigurations[entry.Claimant]
            if cfg then claimant = Locale.Lookup(cfg:GetCivilizationShortDescription()) end
        end
        local function number(fn)
            local ok,v=pcall(fn)
            if ok and type(v)=='number' and v==v and v>=0 and v<2147483647 then return v end
            return nil
        end
        local function boolean(fn)
            local ok,v=pcall(fn)
            if ok and type(v)=='boolean' then return v end
            return nil
        end
        local function wire(v) if v==nil then return '?' end;if type(v)=='number' then return string.format('%.17g',v) end;return tostring(v) end
        local myPoints=number(function()return Players[me]:GetGreatPeoplePoints():GetPointsTotal(entry.Class)end)
        local threshold=number(function()return entry.Cost end)
        local ability=getAbility(entry,indivInfo)
        -- Same native enum/getters as GreatPeoplePopup and the action preflight.
        -- Each currency is independent: one failed API cannot erase the other.
        local goldCost=number(function()return gp:GetPatronizeCost(me,entry.Individual,YieldTypes.GOLD)end)
        local faithCost=number(function()return gp:GetPatronizeCost(me,entry.Individual,YieldTypes.FAITH)end)
        local canRecruit=boolean(function()return gp:CanRecruitPerson(me,entry.Individual)end)
        local canGold=boolean(function()return gp:CanPatronizePerson(me,entry.Individual,YieldTypes.GOLD)end)
        local canFaith=boolean(function()return gp:CanPatronizePerson(me,entry.Individual,YieldTypes.FAITH)end)
        local goldBalance=number(function()return Players[me]:GetTreasury():GetGoldBalance()end)
        local faithBalance=number(function()return Players[me]:GetReligion():GetFaithBalance()end)
        local costStr='gold:'..wire(goldCost)..',faith:'..wire(faithCost)..',recruit:'..wire(canRecruit)
          ..',can_gold:'..wire(canGold)..',can_faith:'..wire(canFaith)..',gold_balance:'..wire(goldBalance)..',faith_balance:'..wire(faithBalance)
        print("GP|" .. className .. "|" .. indivName .. "|" .. eraName .. "|" .. wire(threshold) .. "|" .. claimant .. "|" .. wire(myPoints) .. "|" .. ability .. "|" .. costStr .. "|" .. entry.Individual)
    end
    end
end
print("{SENTINEL}")
"""


def parse_great_people_response(lines: list[str]) -> list[GreatPersonInfo]:
    """Parse GP| lines from build_great_people_query."""
    results: list[GreatPersonInfo] = []
    for line in lines:
        if line.startswith("GP|"):
            parts = line.split("|")
            if len(parts) >= 7:
                ability = unquote(parts[7]) if len(parts) >= 8 else ""
                gold_cost = faith_cost = can_recruit = None
                values = {}
                individual_id = 0
                if len(parts) >= 10:
                    cost_str = parts[8]  # "gold:X,faith:Y,recruit:true/false"
                    for kv in cost_str.split(","):
                        k, _, v = kv.partition(":")
                        if k in ('recruit', 'can_gold', 'can_faith'):
                            values[k] = {'true': True, 'false': False}.get(v)
                        else:
                            try:
                                value = float(v)
                                values[k] = value if math.isfinite(value) and 0 <= value < 2147483647 else None
                            except ValueError:
                                values[k] = None
                    gold_cost, faith_cost, can_recruit = values.get('gold'), values.get('faith'), values.get('recruit')
                    individual_id = _int(parts[9])
                results.append(
                    GreatPersonInfo(
                        class_name=parts[1],
                        individual_name=parts[2],
                        era_name=parts[3],
                        cost=None if parts[4] == "?" else float(parts[4]),
                        claimant=parts[5],
                        player_points=None if parts[6] == "?" else float(parts[6]),
                        ability=ability,
                        gold_cost=gold_cost,
                        faith_cost=faith_cost,
                        can_recruit=can_recruit,
                        individual_id=individual_id,
                        can_patronize_gold=values.get("can_gold"), can_patronize_faith=values.get("can_faith"),
                        gold_balance=values.get("gold_balance"), faith_balance=values.get("faith_balance"),
                    )
                )
    return results


def build_gp_advisor_query(unit_index: int) -> str:
    """InGame context: list candidate cities for a Great Person activation.

    Reports each city that has the matching district, with activation
    eligibility, distance from GP, city yield, and great work slot info.
    """
    sentinel = SENTINEL
    return f"""
{_lua_get_unit(unit_index)}
local uInfo = GameInfo.Units[unit:GetType()]
if not uInfo then {_bail("ERR:UNIT_INFO_NOT_FOUND")} end
local gp=unit:GetGreatPerson()
if not gp then {_bail("ERR:NOT_A_GREAT_PERSON")} end
local classInfo=GameInfo.GreatPersonClasses[gp:GetClass()]
local gpClass=classInfo and classInfo.GreatPersonClassType
if not gpClass then {_bail("ERR:UNKNOWN_GP_CLASS")} end
local classToDistrict = {{
    GREAT_PERSON_CLASS_SCIENTIST = "DISTRICT_CAMPUS",
    GREAT_PERSON_CLASS_ENGINEER = "DISTRICT_INDUSTRIAL_ZONE",
    GREAT_PERSON_CLASS_MERCHANT = "DISTRICT_COMMERCIAL_HUB",
    GREAT_PERSON_CLASS_WRITER = "DISTRICT_THEATER",
    GREAT_PERSON_CLASS_ARTIST = "DISTRICT_THEATER",
    GREAT_PERSON_CLASS_MUSICIAN = "DISTRICT_THEATER",
    GREAT_PERSON_CLASS_PROPHET = "DISTRICT_HOLY_SITE",
    GREAT_PERSON_CLASS_GENERAL = "DISTRICT_ENCAMPMENT",
    GREAT_PERSON_CLASS_ADMIRAL = "DISTRICT_HARBOR",
}}
local classToYield = {{
    GREAT_PERSON_CLASS_SCIENTIST = "YIELD_SCIENCE",
    GREAT_PERSON_CLASS_ENGINEER = "YIELD_PRODUCTION",
    GREAT_PERSON_CLASS_MERCHANT = "YIELD_GOLD",
    GREAT_PERSON_CLASS_WRITER = "YIELD_CULTURE",
    GREAT_PERSON_CLASS_ARTIST = "YIELD_CULTURE",
    GREAT_PERSON_CLASS_MUSICIAN = "YIELD_CULTURE",
    GREAT_PERSON_CLASS_PROPHET = "YIELD_FAITH",
    GREAT_PERSON_CLASS_GENERAL = "YIELD_PRODUCTION",
    GREAT_PERSON_CLASS_ADMIRAL = "YIELD_GOLD",
}}
local targetDist = classToDistrict[gpClass]
if not targetDist then {_bail("ERR:UNKNOWN_GP_CLASS")} end
local charges = -1
if gp then pcall(function() charges = gp:GetActionCharges() end) end
print("GP_INFO|" .. Locale.Lookup(unit:GetName()) .. "|" .. gpClass .. "|" .. targetDist .. "|" .. unit:GetX() .. "|" .. unit:GetY() .. "|" .. charges)
local validPlotSet = {{}}
local highlightKnown = pcall(function()
    local plots = gp:GetActivationHighlightPlots()
    assert(type(plots)=="table")
    for _,index in ipairs(plots) do assert(type(index)=="number");validPlotSet[index]=true end
end)
local citiesKnown=true
local yieldType = classToYield[gpClass]
local yieldIdx = -1
if yieldType then pcall(function() yieldIdx = GameInfo.Yields[yieldType].Index end) end
local isCultural = gpClass == "GREAT_PERSON_CLASS_WRITER" or gpClass == "GREAT_PERSON_CLASS_ARTIST" or gpClass == "GREAT_PERSON_CLASS_MUSICIAN"
local targetDistInfo = GameInfo.Districts[targetDist]
if not targetDistInfo then {_bail("ERR:DISTRICT_NOT_FOUND")} end
for i, city in Players[me]:GetCities():Members() do
    local cityOK = pcall(function()
        local districts = city:GetDistricts()
        if districts:HasDistrict(targetDistInfo.Index, true) then
            local dObj = districts:GetDistrict(targetDistInfo.Index)
            if dObj then
                local dx, dy = dObj:GetX(), dObj:GetY()
                local pPlot = Map.GetPlot(dx, dy)
                local plotIdx = pPlot:GetIndex()
                local canAct = highlightKnown and tostring(validPlotSet[plotIdx] == true) or "?"
                local dist = Map.GetPlotDistance(unit:GetX(), unit:GetY(), dx, dy)
                local cityYield = nil
                if yieldIdx >= 0 then
                    pcall(function() local v=city:GetYield(yieldIdx);if type(v)=="number" and v==v and math.abs(v)<math.huge then cityYield=v end end)
                end
                local slotsFree = -1
                local slotsTotal = -1
                if isCultural then
                    pcall(function()
                        local bldgs = city:GetBuildings()
                        local free = 0
                        local total = 0
                        for bld in GameInfo.Buildings() do
                            local has=bldgs:HasBuilding(bld.Index)
                            assert(type(has)=="boolean", "BUILDING_PRESENCE_UNKNOWN")
                            if has then
                                local count=bldgs:GetNumGreatWorkSlots(bld.Index)
                                assert(type(count)=="number" and count>=0 and count<math.huge and count==math.floor(count), "GREAT_WORK_SLOT_COUNT_UNKNOWN")
                                for slot=0,count-1 do
                                    local typ=bldgs:GetGreatWorkSlotType(bld.Index,slot)
                                    local work=bldgs:GetGreatWorkInSlot(bld.Index,slot)
                                    assert(type(typ)=="number" and typ>=0 and typ<math.huge and typ==math.floor(typ), "GREAT_WORK_SLOT_TYPE_UNKNOWN")
                                    assert(type(work)=="number" and work>=-1 and work<math.huge and work==math.floor(work), "GREAT_WORK_SLOT_UNKNOWN")
                                    total=total+1
                                    if work==-1 then free=free+1 end
                                end
                            end
                        end
                        slotsFree = free
                        slotsTotal = total
                    end)
                end
                local cn = (Locale.Lookup(city:GetName()):gsub("|", "/"))
                print("GP_CITY|" .. cn .. "|" .. city:GetID() .. "|" .. dx .. "|" .. dy .. "|" .. canAct .. "|" .. dist .. "|" .. (cityYield==nil and "?" or string.format("%.17g",cityYield)) .. "|" .. slotsFree .. "|" .. slotsTotal)
            end
        end
    end)
    if not cityOK then citiesKnown=false end
end
print("GP_SCAN|"..tostring(citiesKnown))
print("{sentinel}")
"""


def parse_gp_advisor_response(lines: list[str]) -> GPAdvisorResult | None:
    """Parse GP_INFO and GP_CITY lines from build_gp_advisor_query."""
    gp_name = ""
    gp_class = ""
    target_district = ""
    gp_x = 0
    gp_y = 0
    charges = -1
    cities: list[GPAdvisorCity] = []
    cities_known = None

    for line in lines:
        if line.startswith("GP_INFO|"):
            parts = line.split("|")
            if len(parts) >= 7:
                gp_name = parts[1]
                gp_class = parts[2]
                target_district = parts[3]
                gp_x = int(parts[4])
                gp_y = int(parts[5])
                charges = int(parts[6])
        elif line.startswith("GP_SCAN|"):
            cities_known = {"true": True, "false": False}.get(line.partition("|")[2])
        elif line.startswith("GP_CITY|"):
            parts = line.split("|")
            if len(parts) >= 10:
                try:
                    cities.append(
                        GPAdvisorCity(
                            city_name=parts[1],
                            city_id=int(parts[2]),
                            district_x=int(parts[3]),
                            district_y=int(parts[4]),
                            can_activate={"true": True, "false": False}.get(parts[5]),
                            distance=int(parts[6]),
                            city_yield=None if parts[7] == "?" else float(parts[7]),
                            slots_free=int(parts[8]),
                            slots_total=int(parts[9]),
                        )
                    )
                except (ValueError, IndexError):
                    continue

    if not gp_name:
        return None
    return GPAdvisorResult(
        gp_name=gp_name,
        gp_class=gp_class,
        target_district=target_district,
        gp_x=gp_x,
        gp_y=gp_y,
        charges=charges,
        cities=cities,
        cities_known=cities_known,
    )
