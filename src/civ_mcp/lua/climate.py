"""Climate domain — Lua builders and parsers (Gathering Storm only)."""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL, _bail, _int, _lua_require_ruleset
from civ_mcp.lua.models import (
    ClimateAffectedCity,
    ClimateContributor,
    ClimateEventRecord,
    ClimateOverview,
)


def build_climate_overview_query(history_turns: int = 30) -> str:
    """Get world climate report: CO2, phase, risks, current + recent events (InGame context)."""

    history_turns = max(1, min(int(history_turns), 200))
    return f"""
local me = Game.GetLocalPlayer()
{_lua_require_ruleset("RULESET_EXPANSION_2", "ERR:NO_CLIMATE_IN_RULESET")}
if GameClimate == nil or GameRandomEvents == nil or GameInfo.RandomEvents == nil
then {_bail("ERR:NO_CLIMATE_IN_RULESET")} end
local curTurn = Game.GetCurrentGameTurn()

-- Phase derivation mirrors ClimateScreen.lua:UpdateClimateChangeEventsData()
local firstSeaEvent = -1
local phase = 0
local phaseName = "Phase 0"
local lastThreshold = GameClimate.GetClimateChangeForLastSeaLevelEvent()
for row in GameInfo.RandomEvents() do
    if row.EffectOperatorType == "SEA_LEVEL" then
        if firstSeaEvent == -1 then firstSeaEvent = row.Index end
        if row.ClimateChangePoints == lastThreshold then
            phase = row.Index - firstSeaEvent + 1
            local lok, lname = pcall(Locale.Lookup, row.Name)
            phaseName = (lok and lname) or tostring(row.RandomEventType)
        end
    end
end

local co2Total = GameClimate.GetTotalCO2Footprint()
local co2Self = GameClimate.GetPlayerCO2Footprint(me, false)
local co2SelfLast = GameClimate.GetPlayerCO2Footprint(me, true)
local defor = GameClimate.GetDeforestationType()
local deforName = ""
if defor ~= nil and defor >= 0 and GameInfo.DeforestationLevels[defor] then
    local dok, dname = pcall(Locale.Lookup, GameInfo.DeforestationLevels[defor].Name)
    deforName = (dok and dname) or ""
end
phaseName = phaseName:gsub("|", "/"):gsub("~", "-")
deforName = deforName:gsub("|", "/"):gsub("~", "-")
print("CLIMATE|" .. phase .. "|" .. GameClimate.GetClimateChangeLevel()
    .. "|" .. GameClimate.GetClimateChangeFromRealism()
    .. "|" .. GameClimate.GetClimateChangeFromTemperature()
    .. "|" .. lastThreshold
    .. "|" .. GameClimate.GetNextSeaLevelRiseTurns()
    .. "|" .. GameClimate.GetNextIceLossTurns()
    .. "|" .. GameClimate.GetTilesFlooded()
    .. "|" .. GameClimate.GetTilesSubmerged()
    .. "|" .. GameClimate.GetTemperatureChange()
    .. "|" .. co2Total .. "|" .. co2Self .. "|" .. co2SelfLast
    .. "|" .. GameClimate.GetCO2FootprintModifier() .. "|" .. deforName)
print("CLIMATE_RISK|" .. GameClimate.GetStormPercentChance()
    .. "|" .. GameClimate.GetStormClimateIncreasedChance()
    .. "|" .. GameClimate.GetFloodPercentChance()
    .. "|" .. GameClimate.GetFloodClimateIncreasedChance()
    .. "|" .. GameClimate.GetEruptionPercentChance()
    .. "|" .. GameClimate.GetDroughtPercentChance()
    .. "|" .. GameClimate.GetDroughtClimateIncreasedChance()
    .. "|" .. RiverManager.GetNumRivers() .. "|" .. RiverManager.GetNumFloodableRivers()
    .. "|" .. MapFeatureManager.GetNumNormalVolcanoes()
    .. "|" .. MapFeatureManager.GetNumActiveVolcanoes()
    .. "|" .. MapFeatureManager.GetNumEruptions())

-- Per-player CO2 (all ever-alive majors, mirroring TabCO2ByCiviliation;
-- unmet civs get a masked name)
local pDiplo = Players[me]:GetDiplomacy()
for _, pPlayer in ipairs(PlayerManager.GetWasEverAliveMajors()) do
    local pid = pPlayer:GetID()
    local co2 = GameClimate.GetPlayerCO2Footprint(pid, false)
    local cName = "Unmet Player"
    if pid == me then
        cName = "You"
    elseif pDiplo:HasMet(pid) then
        local cok, cres = pcall(Locale.Lookup,
            PlayerConfigurations[pid]:GetCivilizationShortDescription())
        if cok and cres then cName = cres end
    end
    print("CLIMATE_CO2|" .. pid .. "|" .. cName:gsub("|", "/"):gsub("~", "-") .. "|" .. co2)
end

local function fmtEvent(ev, evDef, turn, useStart)
    local evType = tostring(evDef.RandomEventType)
    local op = tostring(evDef.EffectOperatorType or "")
    local isGlobal = evDef.Global == true
    local plotIdx = useStart and ev.StartLocation or ev.CurrentLocation
    local x, y = -1, -1
    local revealed = isGlobal  -- global events are always reportable
    local pPlot = plotIdx ~= nil and Map.GetPlotByIndex(plotIdx) or nil
    if pPlot ~= nil then
        local vis = PlayersVisibility[me]
        if vis ~= nil and vis:IsRevealed(pPlot:GetX(), pPlot:GetY()) then
            revealed = true
            x = pPlot:GetX(); y = pPlot:GetY()
        end
    end
    local nm = ""
    if ev.Name ~= nil then
        local nok, nres = pcall(Locale.Lookup, ev.Name)
        nm = (nok and nres) or tostring(ev.Name)
    end
    nm = nm:gsub("|", "/"):gsub("~", "-")
    print("CLIMATE_EV|" .. turn .. "|" .. evType .. "|" .. op .. "|" .. nm
        .. "|" .. (isGlobal and 1 or 0) .. "|" .. (revealed and 1 or 0)
        .. "|" .. x .. "|" .. y
        .. "|" .. (ev.FertilityAdded or 0) .. "|" .. (ev.TilesDamaged or 0)
        .. "|" .. (ev.UnitsLost or 0) .. "|" .. (ev.PopLost or 0))
end

-- Current event + affected cities
local cur = GameRandomEvents.GetCurrentTurnEvent()
if cur ~= nil then
    local curDef = GameInfo.RandomEvents[cur.RandomEvent]
    if curDef ~= nil then
        local evType = tostring(curDef.RandomEventType)
        local op = tostring(curDef.EffectOperatorType or "")
        local isGlobal = curDef.Global == true
        local x, y = -1, -1
        local revealed = isGlobal
        local pPlot = cur.CurrentLocation ~= nil and Map.GetPlotByIndex(cur.CurrentLocation) or nil
        if pPlot ~= nil then
            local vis = PlayersVisibility[me]
            if vis ~= nil and vis:IsRevealed(pPlot:GetX(), pPlot:GetY()) then
                revealed = true; x = pPlot:GetX(); y = pPlot:GetY()
            end
        end
        local nm = ""
        if cur.Name ~= nil then
            local nok, nres = pcall(Locale.Lookup, cur.Name)
            nm = (nok and nres) or tostring(cur.Name)
        end
        nm = nm:gsub("|", "/"):gsub("~", "-")
        print("CLIMATE_CUR|" .. curTurn .. "|" .. evType .. "|" .. op .. "|" .. nm
            .. "|" .. (isGlobal and 1 or 0) .. "|" .. (revealed and 1 or 0)
            .. "|" .. x .. "|" .. y
            .. "|" .. (cur.FertilityAdded or 0) .. "|" .. (cur.TilesDamaged or 0)
            .. "|" .. (cur.UnitsLost or 0) .. "|" .. (cur.PopLost or 0))
        for _, ac in ipairs(GameRandomEvents.GetCurrentAffectedCities() or {{}}) do
            local cName = "Unmet Player City"
            if ac.CityOwner == me or pDiplo:HasMet(ac.CityOwner) then
                local pCity = Players[ac.CityOwner]:GetCities():FindID(ac.CityID)
                if pCity ~= nil then
                    local ck, cr = pcall(Locale.Lookup, pCity:GetName())
                    if ck and cr then cName = cr end
                end
            end
            print("CLIMATE_CITY|" .. ac.CityOwner .. "|" .. ac.CityID
                .. "|" .. cName:gsub("|", "/"):gsub("~", "-"))
        end
    end
end

-- Bounded history scan (UI scans to turn 0; we bound by history_turns).
-- Nuclear accidents stay visible here on purpose: "my own plant blew up"
-- is decision-relevant even though the official history tab excludes it.
local fromTurn = math.max(0, curTurn - {history_turns})
for t = curTurn, fromTurn, -1 do
    local ev = GameRandomEvents.GetEventsForTurn(t)
    if ev ~= nil then
        local evDef = GameInfo.RandomEvents[ev.RandomEvent]
        if evDef ~= nil then
            fmtEvent(ev, evDef, t, true)
        end
    end
end
print("{SENTINEL}")
"""


def _event_from_parts(parts: list[str]) -> ClimateEventRecord:
    return ClimateEventRecord(
        turn=_int(parts[1]),
        event_type=parts[2],
        operator=parts[3],
        name=parts[4],
        is_global=parts[5] == "1",
        revealed=parts[6] == "1",
        x=_int(parts[7]),
        y=_int(parts[8]),
        fertility_added=_int(parts[9]),
        tiles_damaged=_int(parts[10]),
        units_lost=_int(parts[11]),
        pop_lost=_int(parts[12]),
    )


def parse_climate_response(lines: list[str]) -> ClimateOverview:
    """Parse CLIMATE*/ rows into a full climate overview; bad lines are skipped."""

    overview: ClimateOverview | None = None
    current_event: ClimateEventRecord | None = None
    affected_cities: list[ClimateAffectedCity] = []
    contributors: list[ClimateContributor] = []
    event_history: list[ClimateEventRecord] = []
    for line in lines:
        parts = line.split("|")
        try:
            if line.startswith("CLIMATE|") and len(parts) >= 16 and overview is None:
                overview = ClimateOverview(
                    phase=_int(parts[1]),
                    phase_name=parts[2],
                    climate_change_points=float(parts[3]),
                    points_from_realism=float(parts[4]),
                    points_from_temperature=float(parts[5]),
                    last_sea_level_threshold=float(parts[6]),
                    next_sea_level_rise_turns=_int(parts[7]),
                    next_ice_loss_turns=_int(parts[8]),
                    tiles_flooded=_int(parts[9]),
                    tiles_submerged=_int(parts[10]),
                    temperature_change=float(parts[11]),
                    co2_total=float(parts[12]),
                    co2_self=float(parts[13]),
                    co2_self_last_turn=float(parts[14]),
                    co2_footprint_modifier=float(parts[15]),
                    deforestation_level=parts[16] if len(parts) > 16 else "",
                )
            elif line.startswith("CLIMATE_RISK|") and len(parts) >= 13 and overview is not None:
                overview.storm_chance = float(parts[1])
                overview.storm_increase = float(parts[2])
                overview.flood_chance = float(parts[3])
                overview.flood_increase = float(parts[4])
                overview.eruption_chance = float(parts[5])
                overview.drought_chance = float(parts[6])
                overview.drought_increase = float(parts[7])
                overview.rivers_total = _int(parts[8])
                overview.rivers_floodable = _int(parts[9])
                overview.volcanoes_total = _int(parts[10])
                overview.volcanoes_active = _int(parts[11])
                overview.volcano_eruptions_total = _int(parts[12])
            elif line.startswith("CLIMATE_CO2|") and len(parts) >= 4:
                contributors.append(
                    ClimateContributor(
                        player_id=_int(parts[1]),
                        civ_name=parts[2],
                        co2=float(parts[3]),
                    )
                )
            elif line.startswith("CLIMATE_CUR|") and len(parts) >= 13 and current_event is None:
                current_event = _event_from_parts(parts)
            elif line.startswith("CLIMATE_CITY|") and len(parts) >= 4:
                affected_cities.append(
                    ClimateAffectedCity(
                        owner_id=_int(parts[1]),
                        city_id=_int(parts[2]),
                        name=parts[3],
                    )
                )
            elif line.startswith("CLIMATE_EV|") and len(parts) >= 13:
                event_history.append(_event_from_parts(parts))
        except (IndexError, TypeError, ValueError):
            continue
    if overview is None:
        raise ValueError("climate query missing CLIMATE row")
    contributors.sort(key=lambda row: row.co2, reverse=True)
    overview.current_event = current_event
    overview.affected_cities = affected_cities
    overview.contributors = contributors
    overview.event_history = event_history
    return overview
