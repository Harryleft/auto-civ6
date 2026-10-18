"""Victory domain — Lua builders and parsers."""

from __future__ import annotations

import math

from civ_mcp.lua._helpers import SENTINEL, _int
from civ_mcp.lua.models import (
    DemographicEntry,
    SpaceProject,
    VictoryPlayerProgress,
    VictoryProgress,
)



def _number(value: str) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError):
        return None


# pcall preserves false; unknown observations must not become production eligibility.
_LUA_READ = r"""
local function observed(fn)
    local ok, value = pcall(fn)
    if ok then return value end
end
local function number(value)
    if type(value) == "number" and value == value and value >= 0 and value < math.huge then return value end
end
local function wire(value)
    if value == nil then return "unknown" end
    return tostring(value):gsub("|", "/"):gsub(".", function(c)
        local byte = string.byte(c)
        return (byte < 32 or byte == 127) and " " or c
    end)
end
local function projectTypes()
    local types = {"PROJECT_LAUNCH_EARTH_SATELLITE", "PROJECT_LAUNCH_MOON_LANDING"}
    if GameInfo.Projects["PROJECT_LAUNCH_MARS_BASE"] then
        table.insert(types, "PROJECT_LAUNCH_MARS_BASE")
    else
        for _, key in ipairs({"PROJECT_LAUNCH_MARS_REACTOR", "PROJECT_LAUNCH_MARS_HABITATION", "PROJECT_LAUNCH_MARS_HYDROPONICS"}) do
            table.insert(types, key)
        end
    end
    table.insert(types, "PROJECT_LAUNCH_EXOPLANET_EXPEDITION")
    return types
end
"""

_LUA_PROJECTS = r"""
for _, projType in ipairs(projectTypes()) do
    local row = GameInfo.Projects[projType]
    if row then
        local techType = row.PrereqTech or ""
        local tech = GameInfo.Technologies[techType]
        local hasTech = techType == "" or observed(function() return p:GetTechs():HasTech(tech.Index) end)
        local completed = number(observed(function() return p:GetStats():GetNumProjectsAdvanced(row.Index) end))
        local status, pct, turns, cost, cityName = "unknown", nil, nil, nil, ""
        local building, queuesKnown, eligibilityKnown, canProduce = false, true, true, false
        local okCities = pcall(function()
            for _, city in p:GetCities():Members() do
                local bq = city:GetBuildQueue()
                local okHash, hash = pcall(function() return bq:GetCurrentProductionTypeHash() end)
                if not okHash or (hash ~= nil and (type(hash) ~= "number" or hash ~= hash or math.abs(hash) == math.huge)) then
                    queuesKnown = false
                end
                if okHash and hash == row.Hash then
                    building = true
                    cityName = Locale.Lookup(city:GetName())
                    cost = number(observed(function() return bq:GetProjectCost(row.Index) end))
                    local progress = number(observed(function() return bq:GetProjectProgress(row.Index) end))
                    if cost and cost > 0 and progress then pct = math.floor(progress * 100 / cost) end
                    turns = number(observed(function() return bq:GetTurnsLeft(row.Hash) end))
                end
                local listed = observed(function() return bq:CanProduce(row.Hash, true) end)
                local ready = observed(function() return bq:CanProduce(row.Hash, false, true) end)
                if type(listed) ~= "boolean" or type(ready) ~= "boolean" then
                    eligibilityKnown = false
                elseif listed and ready then
                    canProduce = true
                    if not cost then cost = number(observed(function() return bq:GetProjectCost(row.Index) end)) end
                end
            end
        end)
        if not okCities then queuesKnown, eligibilityKnown = false, false end
        if not canProduce and not eligibilityKnown then canProduce = nil end
        if building then status = "building"
        elseif completed and completed > 0 then status = "completed"
        elseif queuesKnown and completed == 0 then
            if hasTech == false then status = "locked"
            elseif hasTech == true and canProduce ~= nil then status = canProduce and "ready" or "unlocked" end
        end
        print("SPACEPROJ|" .. wire(projType) .. "|" .. wire(Locale.Lookup(row.Name)) .. "|" .. status .. "|" .. wire(pct) .. "|" .. wire(turns) .. "|" .. wire(cost) .. "|" .. wire(techType) .. "|" .. wire(hasTech) .. "|" .. wire(cityName) .. "|" .. wire(canProduce) .. "|" .. wire(completed))
    end
end
"""

# Both public views consume the same native facts. Threshold policy stays in narration.
_LUA_VICTORY_FACTS = r"""
do
local me = Game.GetLocalPlayer()
local diplo = Players[me]:GetDiplomacy()
print("VICTORY_CONTEXT|" .. wire(Game.GetCurrentGameTurn()) .. "|" .. wire(me))
local enabledKnown = true
local enabled = {}
local okVictories = pcall(function()
    for row in GameInfo.Victories() do
        local value = observed(function() return Game.IsVictoryEnabled(row.Index) end)
        if type(value) ~= "boolean" then enabledKnown = false
        elseif value then table.insert(enabled, row.VictoryType) end
    end
end)
if not okVictories then enabledKnown = false end
print("VICTORY_ENABLED_KNOWN|" .. tostring(enabledKnown))
for _, key in ipairs(enabled) do print("VENABLED|" .. wire(key)) end
print("DIPLO_REQUIRED|" .. wire(number(observed(function() return GlobalParameters.DIPLOMATIC_VICTORY_POINTS_REQUIRED end))))
local majors, religionCount, religionKnown = {}, {}, true
for id = 0, 62 do
    local p = Players[id]
    if p and p:IsMajor() and p:IsAlive() then
        table.insert(majors, id)
        local religion = observed(function() return p:GetReligion():GetReligionInMajorityOfCities() end)
        if type(religion) ~= "number" then religionKnown = false
        elseif religion >= 0 then religionCount[religion] = (religionCount[religion] or 0) + 1 end
    end
end
for _, id in ipairs(majors) do
    local p = Players[id]
    if id == me or observed(function() return diplo:HasMet(id) end) == true then
        local name = observed(function() return Locale.Lookup(PlayerConfigurations[id]:GetCivilizationShortDescription()) end)
        local stats = p:GetStats()
        local science = number(observed(function() return stats:GetScienceVictoryPoints() end))
        local needed = number(observed(function() return stats:GetScienceVictoryPointsTotalNeeded() end))
        local rate = number(observed(function() return stats:GetScienceVictoryPointsPerTurn() end))
        local diplomatic = number(observed(function() return stats:GetDiplomaticVictoryPoints() end))
        local spaceports = 0
        local okPorts = pcall(function()
            for _, city in p:GetCities():Members() do
                for _, district in city:GetDistricts():Members() do
                    local row = GameInfo.Districts[district:GetType()]
                    if row and row.DistrictType == "DISTRICT_SPACEPORT" and district:IsComplete() then spaceports = spaceports + 1 end
                end
            end
        end)
        if not okPorts then spaceports = nil end
        local advanced, advancedKnown = 0, true
        for _, key in ipairs(projectTypes()) do
            local row = GameInfo.Projects[key]
            if row then
                local count = number(observed(function() return stats:GetNumProjectsAdvanced(row.Index) end))
                if count then advanced = advanced + count else advancedKnown = false end
            end
        end
        if not advancedKnown then advanced = nil end
        local visiting = number(observed(function() return p:GetCulture():GetTouristsTo() end))
        local required, cultureKnown = 0, true
        local team = observed(function() return p:GetTeam() end)
        if type(team) ~= "number" then cultureKnown = false end
        for _, other in ipairs(majors) do
            if other ~= id then
                local otherTeam = observed(function() return Players[other]:GetTeam() end)
                if type(otherTeam) ~= "number" then cultureKnown = false
                elseif otherTeam ~= team then
                    local domestic = number(observed(function() return Players[other]:GetCulture():GetStaycationers() end))
                    if domestic then required = math.max(required, domestic) else cultureKnown = false end
                end
            end
        end
        if cultureKnown then required = required + 1 else required = nil end
        local founded = observed(function() return p:GetReligion():GetReligionTypeCreated() end)
        local converted = nil
        if religionKnown and type(founded) == "number" then converted = founded >= 0 and (religionCount[founded] or 0) or 0 end
        print("VFACT|" .. wire(id) .. "|" .. wire(name) .. "|" .. wire(science) .. "|" .. wire(needed) .. "|" .. wire(spaceports) .. "|" .. wire(rate) .. "|" .. wire(diplomatic) .. "|" .. wire(visiting) .. "|" .. wire(required) .. "|" .. wire(advanced) .. "|" .. wire(converted) .. "|" .. wire(religionKnown and #majors or nil))
    end
end
print("VICTORY_COMPLETE")
end
"""


def build_victory_proximity_query() -> str:
    """InGame: pure-read, lightweight facts for a confirmed turn report."""
    return _LUA_READ + _LUA_VICTORY_FACTS + f'\nprint("{SENTINEL}")\n'


def build_victory_progress_query() -> str:
    """Build a Lua query for victory progress of all players (InGame context).

    Outputs lines:
      PLAYER|pid|name|score|sciVP|sciNeeded|diploVP|tourism|milStr|techs|civics|relCities|staycationers|hasReligion
      CULTURE|civName|ourTourists|theirStaycationers|dominant
      CAPITAL|civName|holdsOwn
      RELMAJ|civName|religionName
    """
    return f"""
{_LUA_READ}
local me = Game.GetLocalPlayer()
local pDiplo = Players[me]:GetDiplomacy()
local pCul = Players[me]:GetCulture()

local dPop, dMil, dFood, dGold, dLand, dProd = {{}}, {{}}, {{}}, {{}}, {{}}, {{}}
for i = 0, 62 do
    local p = Players[i]
    if p and p:IsMajor() and p:IsAlive() then
        local met = pDiplo:HasMet(i) or i == me
        if met then
            local cfg = PlayerConfigurations[i]
            local name = wire(Locale.Lookup(cfg:GetCivilizationShortDescription()))
            local st = p:GetStats()
            local sciVP = wire(number(observed(function() return st:GetScienceVictoryPoints() end)))
            local sciNeeded = wire(number(observed(function() return st:GetScienceVictoryPointsTotalNeeded() end)))
            local diploVP = wire(number(observed(function() return st:GetDiplomaticVictoryPoints() end)))
            local tourism = st:GetTourism()
            local milStr = st:GetMilitaryStrength()
            local techs = st:GetNumTechsResearched()
            local civics = st:GetNumCivicsCompleted()
            local relCities = st:GetNumCitiesFollowingReligion()
            local stay = wire(number(observed(function() return p:GetCulture():GetStaycationers() end)))
            local hasRel = p:GetReligion():GetReligionTypeCreated() >= 0
            local nCities = 0; for _ in p:GetCities():Members() do nCities = nCities + 1 end
            local pSci = p:GetTechs():GetScienceYield()
            local pCulYield = p:GetCulture():GetCultureYield()
            local pGold2 = p:GetTreasury():GetGoldYield() - p:GetTreasury():GetTotalMaintenance()
            print("PLAYER|" .. i .. "|" .. name .. "|" .. p:GetScore() .. "|" .. sciVP .. "|" .. sciNeeded .. "|" .. diploVP .. "|" .. tourism .. "|" .. milStr .. "|" .. techs .. "|" .. civics .. "|" .. relCities .. "|" .. stay .. "|" .. tostring(hasRel) .. "|" .. nCities .. "|" .. string.format("%.1f", pSci) .. "|" .. string.format("%.1f", pCulYield) .. "|" .. string.format("%.1f", pGold2))
            if i == me then
                {_LUA_PROJECTS}
            end
            if i ~= me then
                local ourTourists = wire(number(observed(function() return pCul:GetTouristsFrom(i) end)))
                local theirStay = wire(number(observed(function() return p:GetCulture():GetStaycationers() end)))
                local dominant = pCul:IsDominantOver(i)
                print("CULTURE|" .. name .. "|" .. ourTourists .. "|" .. theirStay .. "|" .. tostring(dominant))
            end
            local cap = p:GetCities():GetCapitalCity()
            local holdsOwn = cap and cap:IsOriginalCapital() or false
            print("CAPITAL|" .. name .. "|" .. tostring(holdsOwn))
            local majRel = p:GetReligion():GetReligionInMajorityOfCities()
            local relName = "none"
            if majRel >= 0 then
                local r = GameInfo.Religions[majRel]
                if r then relName = r.ReligionType end
            end
            print("RELMAJ|" .. name .. "|" .. relName)
            local createdRel = p:GetReligion():GetReligionTypeCreated()
            if createdRel >= 0 then
                local rEntry = GameInfo.Religions[createdRel]
                local fName = rEntry and Locale.Lookup(rEntry.Name) or "Unknown"
                print("RELFOUNDED|" .. name .. "|" .. fName)
            end
        end
        -- Demographics: collect for ALL alive majors (anonymized aggregates)
        local totalPop = 0
        local totalFood, totalProd = 0, 0
        local ownedPlots = 0
        for _, c in p:GetCities():Members() do
            totalPop = totalPop + c:GetPopulation()
            totalFood = totalFood + c:GetYield(0)
            totalProd = totalProd + c:GetYield(1)
            local ok, pp = pcall(function() return Map.GetCityPlots():GetPurchasedPlots(c) end)
            if ok and pp then ownedPlots = ownedPlots + #pp end
        end
        local milVal = p:GetStats():GetMilitaryStrength()
        local netGold = p:GetTreasury():GetGoldYield() - p:GetTreasury():GetTotalMaintenance()
        table.insert(dPop, {{v=totalPop, mine=(i==me)}})
        table.insert(dMil, {{v=milVal, mine=(i==me)}})
        table.insert(dFood, {{v=totalFood, mine=(i==me)}})
        table.insert(dGold, {{v=netGold, mine=(i==me)}})
        table.insert(dLand, {{v=ownedPlots, mine=(i==me)}})
        table.insert(dProd, {{v=totalProd, mine=(i==me)}})
    end
end
local function emitDemo(label, arr)
    table.sort(arr, function(a, b) return a.v > b.v end)
    local best, worst, total = arr[1].v, arr[#arr].v, 0
    local myRank, myVal = 0, 0
    for idx, e in ipairs(arr) do
        total = total + e.v
        if e.mine then myRank = idx; myVal = e.v end
    end
    local avg = total / #arr
    print("DEMO|" .. label .. "|" .. myRank .. "|" .. string.format("%.1f", myVal) .. "|" .. string.format("%.1f", best) .. "|" .. string.format("%.1f", avg) .. "|" .. string.format("%.1f", worst))
end
emitDemo("Population", dPop)
emitDemo("Soldiers", dMil)
emitDemo("CropYield", dFood)
emitDemo("GNP", dGold)
emitDemo("Land", dLand)
emitDemo("Goods", dProd)
local nMajors = 0
local nRels = 0
for i = 0, 62 do
    if Players[i] and Players[i]:IsMajor() and Players[i]:IsAlive() then
        nMajors = nMajors + 1
        if Players[i]:GetReligion():GetReligionTypeCreated() >= 0 then nRels = nRels + 1 end
    end
end
print("RELSLOTS|" .. nRels .. "|" .. (math.floor(nMajors / 2) + 1))
{_LUA_VICTORY_FACTS}
print("{SENTINEL}")
"""


def parse_victory_progress_response(lines: list[str]) -> VictoryProgress:
    """Parse victory progress from Lua output."""
    players: list[VictoryPlayerProgress] = []
    our_tourists: dict[str, float | None] = {}
    their_stay: dict[str, float | None] = {}
    capitals: dict[str, bool] = {}
    rel_majority: dict[str, str] = {}
    rel_founded_names: dict[str, str] = {}
    religions_founded = 0
    religions_max = 0
    demographics: dict[str, DemographicEntry] = {}
    space_projects: list[SpaceProject] = []
    enabled_victories: set[str] = set()
    turn = local_player_id = diplomatic_vp_needed = None
    enabled_known = complete = False
    facts = []
    contexts = completions = enabled_markers = 0

    for line in lines:
        if line.startswith("PLAYER|"):
            p = line.split("|")
            if len(p) < 14:
                continue
            players.append(
                VictoryPlayerProgress(
                    player_id=int(p[1]),
                    name=p[2],
                    score=_int(p[3]),
                    science_vp=_number(p[4]),
                    science_vp_needed=_number(p[5]),
                    diplomatic_vp=_number(p[6]),
                    tourism=_int(p[7]),
                    military_strength=_int(p[8]),
                    techs_researched=_int(p[9]),
                    civics_completed=_int(p[10]),
                    religion_cities=_int(p[11]),
                    staycationers=_number(p[12]),
                    has_religion=p[13] == "true",
                    num_cities=_int(p[14]) if len(p) > 14 else 0,
                    science_yield=float(p[15]) if len(p) > 15 else 0.0,
                    culture_yield=float(p[16]) if len(p) > 16 else 0.0,
                    gold_yield=float(p[17]) if len(p) > 17 else 0.0,
                )
            )
        elif line.startswith("SPACE|"):
            p = line.split("|")
            if len(p) >= 4:
                sp_name = p[1]
                sp_ports = int(p[2])
                sp_prog = p[3]
                for pl in players:
                    if pl.name == sp_name:
                        pl.spaceports = sp_ports
                        pl.space_progress = sp_prog
                        break
        elif line.startswith("CULTURE|"):
            p = line.split("|")
            if len(p) >= 5:
                our_tourists[p[1]] = _number(p[2])
                their_stay[p[1]] = _number(p[3])
        elif line.startswith("CAPITAL|"):
            p = line.split("|")
            if len(p) >= 3:
                capitals[p[1]] = p[2] == "true"
        elif line.startswith("RELMAJ|"):
            p = line.split("|")
            if len(p) >= 3:
                rel_majority[p[1]] = p[2]
        elif line.startswith("RELFOUNDED|"):
            p = line.split("|")
            if len(p) >= 3:
                rel_founded_names[p[1]] = p[2]
        elif line.startswith("RELSLOTS|"):
            p = line.split("|")
            if len(p) >= 3:
                religions_founded = int(p[1])
                religions_max = int(p[2])
        elif line.startswith("SPACEPROJ|"):
            p = line.split("|")
            if len(p) >= 9:
                space_projects.append(
                    SpaceProject(
                        project_type=p[1],
                        name=p[2],
                        status=p[3],
                        progress_pct=_number(p[4]),
                        turns_remaining=_number(p[5]),
                        cost=_number(p[6]),
                        tech_prereq=p[7],
                        has_tech={"true": True, "false": False}.get(p[8]),
                        city_name=p[9] if len(p) > 9 else "",
                        can_produce={"true": True, "false": False}.get(p[10]) if len(p) > 10 else None,
                        completed_count=_number(p[11]) if len(p) > 11 else None,
                    )
                )
        elif line.startswith("DEMO|"):
            p = line.split("|")
            if len(p) >= 6:
                demographics[p[1]] = DemographicEntry(
                    rank=int(p[2]),
                    value=float(p[3]),
                    best=float(p[4]),
                    average=float(p[5]),
                    worst=float(p[6]) if len(p) > 6 else float(p[5]),
                )
        elif line.startswith("VICTORY_CONTEXT|"):
            contexts += 1
            p = line.split("|")
            if len(p) == 3:
                turn, local_player_id = _number(p[1]), _number(p[2])
        elif line.startswith("VICTORY_ENABLED_KNOWN|"):
            enabled_markers += 1
            enabled_known = line == "VICTORY_ENABLED_KNOWN|true"
        elif line.startswith("DIPLO_REQUIRED|"):
            diplomatic_vp_needed = _number(line.split("|", 1)[1])
        elif line.startswith("VFACT|"):
            facts.append(line.split("|"))
        elif line == "VICTORY_COMPLETE":
            completions += 1
            complete = True
        elif line.startswith("VENABLED|"):
            enabled_victories.add(line.split("|", 1)[1])

    seen = set()
    for fact in facts:
        identifier = _number(fact[1]) if len(fact) > 1 else None
        if (len(fact) != 13 or identifier is None or not identifier.is_integer()
                or identifier > 62 or identifier in seen):
            complete = False
            continue
        player_id = int(identifier)
        seen.add(player_id)
        player = next((item for item in players if item.player_id == player_id), None)
        if player is None:
            player = VictoryPlayerProgress(player_id, fact[2], 0, None, None, None, 0, 0, 0, 0, 0)
            players.append(player)
        (player.science_vp, player.science_vp_needed, player.spaceports,
         player.science_rate, player.diplomatic_vp, player.culture_visiting,
         player.culture_required, player.completed_space_projects,
         player.religion_civilizations, player.religion_civilizations_required) = map(_number, fact[3:])
    complete = (complete and contexts == completions == enabled_markers == 1
                and turn is not None and turn.is_integer()
                and local_player_id is not None and local_player_id.is_integer()
                and local_player_id in seen)
    return VictoryProgress(
        players=players,
        turn=turn, local_player_id=local_player_id, complete=complete,
        enabled_known=enabled_known, diplomatic_vp_needed=diplomatic_vp_needed,
        our_tourists_from=our_tourists,
        their_staycationers=their_stay,
        capitals_held=capitals,
        religion_majority=rel_majority,
        religion_founded_names=rel_founded_names,
        religions_founded=religions_founded,
        religions_max=religions_max,
        demographics=demographics,
        space_projects=space_projects,
        enabled_victories=enabled_victories,
    )
