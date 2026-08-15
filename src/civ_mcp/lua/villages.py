"""Tribal village (goody hut) queries — one-shot rewards on revealed tiles."""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.lua.models import Village, VillageOverview


def build_village_overview_query() -> str:
    """GameCore: scan revealed tribal villages.

    A village is a one-shot reward removed the moment any unit enters its
    tile, so this reports current presence only — absence is never a claim
    about history. Distances target the nearest own city and own combat unit
    (UNIT_SCOUT has Combat 10, so scouts count) for grab-race prioritization.
    """

    return """
local me = Game.GetLocalPlayer()
local vis = PlayersVisibility[me]
local cityPositions = {}
local militaryPositions = {}

for _, city in Players[me]:GetCities():Members() do
    table.insert(cityPositions, {city:GetX(), city:GetY()})
end

for _, unit in Players[me]:GetUnits():Members() do
    local ux, uy = unit:GetX(), unit:GetY()
    local entry = GameInfo.Units[unit:GetType()]
    if ux ~= -9999 and entry and ((entry.Combat or 0) > 0 or (entry.RangedCombat or 0) > 0) then
        table.insert(militaryPositions, {ux, uy})
    end
end

local function nearestDistance(positions, x, y)
    local nearest = 999
    for _, pos in ipairs(positions) do
        local distance = Map.GetPlotDistance(pos[1], pos[2], x, y)
        if distance < nearest then nearest = distance end
    end
    return nearest
end

local width, height = Map.GetGridSize()
for y = 0, height - 1 do
    for x = 0, width - 1 do
        local plot = Map.GetPlot(x, y)
        if plot then
            local plotIndex = plot:GetIndex()
            if vis:IsRevealed(plotIndex) then
                local improvementIndex = plot:GetImprovementType()
                local improvement = improvementIndex >= 0 and GameInfo.Improvements[improvementIndex] or nil
                if improvement and improvement.ImprovementType == "IMPROVEMENT_GOODY_HUT" then
                    local visibility = vis:IsVisible(plotIndex) and "visible" or "revealed"
                    local ownerLabel = "none"
                    local ownerId = plot:GetOwner()
                    if ownerId and ownerId >= 0 and PlayerConfigurations[ownerId] then
                        local cfgName = Locale.Lookup(PlayerConfigurations[ownerId]:GetCivilizationShortDescription())
                        if cfgName then ownerLabel = tostring(cfgName):gsub("|", "/") end
                    end
                    print(
                        "VILLAGE|" .. x .. "," .. y .. "|" .. visibility
                        .. "|" .. ownerLabel
                        .. "|" .. nearestDistance(cityPositions, x, y)
                        .. "|" .. nearestDistance(militaryPositions, x, y)
                    )
                end
            end
        end
    end
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def parse_village_overview_response(lines: list[str]) -> VillageOverview:
    """Parse ``VILLAGE`` records from Lua."""

    huts: list[Village] = []
    for line in lines:
        parts = line.split("|")
        try:
            if line.startswith("VILLAGE|") and len(parts) >= 6:
                x, y = (int(value) for value in parts[1].split(","))
                huts.append(
                    Village(
                        x=x,
                        y=y,
                        visibility=parts[2] or "revealed",
                        owner=parts[3] or "none",
                        distance_to_city=int(parts[4]),
                        distance_to_military=int(parts[5]),
                    )
                )
        except (IndexError, TypeError, ValueError):
            # A malformed line should not discard valid records from the same
            # scan; FireTuner output can contain unrelated diagnostic lines.
            continue
    return VillageOverview(huts=huts)
