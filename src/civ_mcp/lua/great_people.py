"""Great People domain — Lua builders and parsers."""

from __future__ import annotations

import math
import json
import re
from urllib.parse import unquote

from civ_mcp.lua._helpers import SENTINEL, _bail, _int, _lua_get_unit
from civ_mcp.lua.models import GPAdvisorCity, GPAdvisorResult, GreatPersonInfo, GreatPeopleReport


def build_great_people_query(individual_id: int | None = None, class_type: str | None = None) -> str:
    """Filter the current native timeline before reading per-person details."""
    if individual_id is not None and (type(individual_id) is not int or individual_id < 0):
        raise ValueError('individual_id must be a nonnegative integer')
    if class_type is not None and (not isinstance(class_type, str) or not re.fullmatch(r'GREAT_PERSON_CLASS_[A-Z0-9_]+', class_type)):
        raise ValueError('class_type must be a GREAT_PERSON_CLASS_ identifier')
    return f"""
local me = Game.GetLocalPlayer()
local requestedId = {json.dumps(str(individual_id)) if individual_id is not None else 'nil'}
local requestedClass = {json.dumps(class_type) if class_type is not None else 'nil'}
local gp = Game.GetGreatPeople()
if gp == nil then {_bail("ERR:NO_GP_SYSTEM|Great People system not available")} end
local timelineOK,timeline = pcall(function()return gp:GetTimeline()end)
if not timelineOK or type(timeline)~='table' then {_bail("ERR:NO_TIMELINE|No complete great people timeline")} end
""" + r'''
local function escape(value)
 return tostring(value):gsub('%%','%%25'):gsub('|','%%7C'):gsub('\r','%%0D'):gsub('\n','%%0A')
end
local function number(fn)
 local ok,v=pcall(fn)
 if ok and type(v)=='number' and v==v and v>=0 and v<2147483647 then return v end
end
local function boolean(fn)
 local ok,v=pcall(fn);if ok and type(v)=='boolean'then return v end
end
local function wire(v)
 if v==nil then return '?'end
 if type(v)=='number'then return string.format('%.17g',v)end
 return escape(v)
end
local function name(row)
 local ok,value=pcall(function()return Locale.Lookup(row.Name)end)
 return ok and type(value)=='string' and value or 'Unknown'
end
local function integer(v)return type(v)=='number'and v==v and v>=0 and v<2147483647 and v==math.floor(v)end
local scanComplete=true
local reasons={}
local function incomplete(reason)
 scanComplete=false
 if not reasons[reason]then reasons[reason]=true;print('GP_TIMELINE_ERROR|'..reason)end
end
local function getAbility(entry,ind)
    -- The native popup consumes timeline text including usage and passive
    -- conditions. Modifier-name substrings cannot prove those mechanics.
    local parts={}
    for _,pair in ipairs({{"action",entry.ActionNameText},{"usage",entry.ActionUsageText},
        {"effect",entry.ActionEffectText},{"passive",entry.PassiveEffectText}}) do
        if type(pair[2])=="string" and pair[2]~="" then parts[#parts+1]=pair[1]..": "..pair[2] end
    end
    if ind.ActionCharges~=nil then parts[#parts+1]="definition action charges "..tostring(ind.ActionCharges) end
    local ok,conditions=pcall(function()return gp:GetEarnConditionsText(me,entry.Individual)end)
    if ok and type(conditions)=="string" and conditions~="" then parts[#parts+1]="earn conditions: "..conditions end
    if #parts==0 then parts[1]="Native timeline description unavailable; expand current rules before judging effects" end
    return table.concat(parts,"; ")
end
-- A hole, malformed row or missing definition may hide the requested ID.
-- Such a scan cannot establish that a filter has no current match.
local slots={};local nativeTotal=0
for key in pairs(timeline)do
 nativeTotal=nativeTotal+1
 if integer(key)and key>=1 then slots[#slots+1]=key else incomplete('TIMELINE_SHAPE_UNKNOWN')end
end
table.sort(slots)
for index,key in ipairs(slots)do if index~=key then incomplete('TIMELINE_SHAPE_UNKNOWN')end end
local matched,shown=0,0
local seen={}
local exhaustedCount=0
local exhaustedClasses={}
for _,key in ipairs(slots)do
 local entry=timeline[key]
 if type(entry)~='table'or not integer(entry.Class)then
  incomplete('ENTRY_IDENTITY_UNKNOWN')
 elseif entry.Individual==nil and entry.Cost==2147483647 then
  -- The native popup's noneAvailable row closes this class. It is not a
  -- person or an unknown identity; never synthesize an individual from DB.
  local bound,classInfo=pcall(function()return GameInfo.GreatPersonClasses[entry.Class]end)
  local currentClass=bound and classInfo and classInfo.GreatPersonClassType
  if type(currentClass)~='string'or not currentClass:match('^GREAT_PERSON_CLASS_[A-Z0-9_]+$')then
   incomplete('EXHAUSTED_CLASS_BINDING_UNKNOWN')
  elseif exhaustedClasses[currentClass]then incomplete('DUPLICATE_EXHAUSTED_CLASS')
  else
   exhaustedClasses[currentClass]=true;exhaustedCount=exhaustedCount+1
   print('GP_EXHAUSTED|'..wire(entry.Class)..'|'..wire(currentClass))
  end
 elseif not integer(entry.Individual)then
  incomplete('ENTRY_IDENTITY_UNKNOWN')
 elseif seen[entry.Individual]then incomplete('DUPLICATE_INDIVIDUAL')
 else
  seen[entry.Individual]=true
  local bound,classInfo,indivInfo=pcall(function()return GameInfo.GreatPersonClasses[entry.Class],GameInfo.GreatPersonIndividuals[entry.Individual]end)
  if not bound or not classInfo or not indivInfo then incomplete('ENTRY_BINDING_UNKNOWN')
  else
   local currentClass=classInfo.GreatPersonClassType
   local classKnown=type(currentClass)=='string'and currentClass:match('^GREAT_PERSON_CLASS_[A-Z0-9_]+$')~=nil
   if not classKnown then incomplete('CLASS_TYPE_UNKNOWN')end
   local classConsistent=indivInfo.GreatPersonClassType==nil or indivInfo.GreatPersonClassType==currentClass
   local idConsistent=indivInfo.Index==nil or indivInfo.Index==entry.Individual
   if not classConsistent or not idConsistent then incomplete('ENTRY_BINDING_MISMATCH')
   elseif (requestedId==nil or requestedId==string.format('%.17g',entry.Individual))and
       (requestedClass==nil or(classKnown and requestedClass==currentClass))then
    matched=matched+1
    local observed=pcall(function()
        local className=name(classInfo)
        local indivName=name(indivInfo)
        local eraInfo=entry.Era~=nil and GameInfo.Eras and GameInfo.Eras[entry.Era]
        local eraName=eraInfo and name(eraInfo)or 'Unknown'
        local claimant = "Unclaimed"
        if entry.Claimant and entry.Claimant >= 0 then
            local cfg = PlayerConfigurations[entry.Claimant]
            if cfg then claimant = Locale.Lookup(cfg:GetCivilizationShortDescription()) end
        end
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
        print('GP|'..wire(className)..'|'..wire(indivName)..'|'..wire(eraName)..'|'..wire(threshold)..'|'..wire(claimant)..'|'..wire(myPoints)..'|'..wire(ability)..'|'..costStr..'|'..wire(entry.Individual)..'|'..wire(currentClass))
    end)
    if observed then shown=shown+1 else incomplete('ENTRY_OBSERVATION_UNKNOWN')end
   end
  end
 end
end
print('GP_TIMELINE|'..nativeTotal..'|'..matched..'|'..shown..'|'..tostring(scanComplete)..'|'..wire(requestedId)..'|'..wire(requestedClass)..'|'..exhaustedCount)
''' + f'print("{SENTINEL}")'


def parse_great_people_response(lines: list[str]) -> list[GreatPersonInfo]:
    """Parse GP| lines from build_great_people_query."""
    results: list[GreatPersonInfo] = []
    for line in lines:
        if line.startswith("GP|"):
            parts = line.split("|")
            if len(parts) >= 7:
                # New rows carry native class identity and escaped names;
                # historical rows escaped only the ability field.
                text = unquote if len(parts) > 10 else lambda value: value
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
                        class_name=text(parts[1]),
                        individual_name=text(parts[2]),
                        era_name=text(parts[3]),
                        cost=None if parts[4] == "?" else float(parts[4]),
                        claimant=text(parts[5]),
                        player_points=None if parts[6] == "?" else float(parts[6]),
                        ability=ability,
                        gold_cost=gold_cost,
                        faith_cost=faith_cost,
                        can_recruit=can_recruit,
                        individual_id=individual_id,
                        can_patronize_gold=values.get("can_gold"), can_patronize_faith=values.get("can_faith"),
                        gold_balance=values.get("gold_balance"), faith_balance=values.get("faith_balance"),
                        class_type=unquote(parts[10]) if len(parts)>10 and parts[10]!='?' else None,
                    )
                )
    return results


def parse_great_people_report(lines: list[str]) -> GreatPeopleReport:
    """Keep legacy rows readable, but only a complete new scan proves absence."""
    people=[]
    errors=[unquote(row.partition('|')[2]) for row in lines if row.startswith('GP_TIMELINE_ERROR|')]
    markers=[row.split('|') for row in lines if row.startswith('GP_TIMELINE|')]
    for row in lines:
        if not row.startswith('GP|'):
            continue
        try:
            parsed=parse_great_people_response([row])
            if len(parsed)!=1:
                raise ValueError('candidate row missing')
            people.extend(parsed)
            parts=row.split('|')
            if markers and (len(parts)!=11 or not parts[9].isdigit() or parsed[0].class_type is None):
                errors.append('CANDIDATE_IDENTITY_INCOMPLETE')
        except (ValueError, IndexError, OverflowError):
            errors.append('CANDIDATE_ROW_INVALID')
    result=GreatPeopleReport(people=people,shown=len(people),errors=errors)
    for row in lines:
        if not row.startswith('GP_EXHAUSTED|'):
            continue
        parts=row.split('|')
        class_type=unquote(parts[2]) if len(parts)==3 else ''
        if len(parts)!=3 or not parts[1].isdigit() or not re.fullmatch(r'GREAT_PERSON_CLASS_[A-Z0-9_]+',class_type):
            result.errors.append('EXHAUSTED_CLASS_INVALID')
        elif class_type in result.exhausted_classes:
            result.errors.append('EXHAUSTED_CLASS_DUPLICATED')
        else:
            result.exhausted_classes.append(class_type)
    if len(markers)!=1 or len(markers[0]) not in (7,8):
        result.errors.append('TIMELINE_COMPLETENESS_UNKNOWN')
        return result
    meta=markers[0]
    if not all(v.isdigit() for v in meta[1:4]) or meta[4] not in ('true','false'):
        result.errors.append('TIMELINE_METADATA_INVALID')
        return result
    result.native_total,result.matched,native_shown=map(int,meta[1:4])
    if len(meta)==8 and meta[7].isdigit():
        result.exhausted_count=int(meta[7])
    elif len(meta)==8:
        result.errors.append('TIMELINE_METADATA_INVALID')
    if result.exhausted_count!=len(result.exhausted_classes):
        result.errors.append('EXHAUSTED_COUNT_MISMATCH')
    if meta[5]!='?':
        if not meta[5].isdigit():
            result.errors.append('TIMELINE_FILTER_INVALID')
        else:
            result.individual_id=int(meta[5])
    if meta[6]!='?':
        result.class_type=unquote(meta[6])
        if not re.fullmatch(r'GREAT_PERSON_CLASS_[A-Z0-9_]+',result.class_type):
            result.errors.append('TIMELINE_FILTER_INVALID')
    if not result.native_total>=result.matched+result.exhausted_count or not result.matched>=native_shown or native_shown!=len(people):
        result.errors.append('TIMELINE_COUNT_MISMATCH')
    if len({p.individual_id for p in people})!=len(people):
        result.errors.append('TIMELINE_IDENTITY_DUPLICATED')
    if any((result.individual_id is not None and p.individual_id!=result.individual_id) or
           (result.class_type is not None and p.class_type!=result.class_type) for p in people):
        result.errors.append('TIMELINE_FILTER_MISMATCH')
    result.scan_complete=meta[4]=='true' and not result.errors
    return result


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
