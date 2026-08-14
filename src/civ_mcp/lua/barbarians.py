"""Barbarian intelligence queries — camps and visible barbarian units."""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.lua.models import BarbarianCamp, BarbarianOverview, BarbarianUnit


def build_barbarian_overview_query() -> str:
    """GameCore: scan revealed camps and currently visible barbarian units.

    Camps remain known after leaving visibility as long as their tile has been
    revealed. Units require current visibility, matching the game's fog rules.
    Distances are calculated to the nearest own city and own military unit so
    the caller can prioritize source clearance and immediate defense together.
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
                if improvement and improvement.ImprovementType == "IMPROVEMENT_BARBARIAN_CAMP" then
                    local visibility = vis:IsVisible(plotIndex) and "visible" or "revealed"
                    print(
                        "BARB_CAMP|" .. x .. "," .. y .. "|" .. visibility
                        .. "|" .. nearestDistance(cityPositions, x, y)
                        .. "|" .. nearestDistance(militaryPositions, x, y)
                    )
                end
            end
        end
    end
end

local barbarians = Players[63]
if barbarians and barbarians:IsAlive() then
    for _, unit in barbarians:GetUnits():Members() do
        local x, y = unit:GetX(), unit:GetY()
        local entry = GameInfo.Units[unit:GetType()]
        if x ~= -9999 and entry and vis:IsVisible(x, y)
            and ((entry.Combat or 0) > 0 or (entry.RangedCombat or 0) > 0) then
            local hp = unit:GetMaxDamage() - unit:GetDamage()
            local maxHp = unit:GetMaxDamage()
            local combat = entry.Combat or 0
            local ranged = entry.RangedCombat or 0
            local unitType = entry.UnitType or "UNKNOWN"
            print(
                "BARB_UNIT|" .. unit:GetID() .. "|" .. unitType .. "|" .. x .. "," .. y
                .. "|" .. hp .. "/" .. maxHp .. "|" .. combat .. "|" .. ranged
                .. "|" .. nearestDistance(cityPositions, x, y)
                .. "|" .. nearestDistance(militaryPositions, x, y)
            )
        end
    end
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def parse_barbarian_overview_response(lines: list[str]) -> BarbarianOverview:
    """Parse ``BARB_CAMP`` and ``BARB_UNIT`` records from Lua."""

    camps: list[BarbarianCamp] = []
    units: list[BarbarianUnit] = []
    for line in lines:
        parts = line.split("|")
        try:
            if line.startswith("BARB_CAMP|") and len(parts) >= 5:
                x, y = (int(value) for value in parts[1].split(","))
                camps.append(
                    BarbarianCamp(
                        x=x,
                        y=y,
                        visibility=parts[2] or "revealed",
                        distance_to_city=int(parts[3]),
                        distance_to_military=int(parts[4]),
                    )
                )
            elif line.startswith("BARB_UNIT|") and len(parts) >= 9:
                x, y = (int(value) for value in parts[3].split(","))
                hp, max_hp = (int(value) for value in parts[4].split("/"))
                units.append(
                    BarbarianUnit(
                        unit_id=int(parts[1]),
                        unit_type=parts[2],
                        x=x,
                        y=y,
                        hp=hp,
                        max_hp=max_hp,
                        combat_strength=int(parts[5]),
                        ranged_strength=int(parts[6]),
                        distance_to_city=int(parts[7]),
                        distance_to_military=int(parts[8]),
                    )
                )
        except (IndexError, TypeError, ValueError):
            # A malformed line should not discard valid records from the same
            # scan; FireTuner output can contain unrelated diagnostic lines.
            continue
    return BarbarianOverview(camps=camps, units=units)
