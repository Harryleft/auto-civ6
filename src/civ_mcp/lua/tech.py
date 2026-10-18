"""Tech domain — Lua builders and parsers."""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL, _bail
from civ_mcp.lua.models import (
    CivicOption,
    LockedCivic,
    LockedTech,
    TechCivicStatus,
    TechOption,
)


def build_tech_civics_query() -> str:
    return """
local id = Game.GetLocalPlayer()
local te = Players[id]:GetTechs()
local cu = Players[id]:GetCulture()
local techIdx = te:GetResearchingTech()
local civicIdx = cu:GetProgressingCivic()
local techName = "None"
local techTurns = -1
if techIdx >= 0 then
    techName = Locale.Lookup(GameInfo.Technologies[techIdx].Name)
    techTurns = te:GetTurnsToResearch(techIdx)
end
-- One read per civic keeps the current header and candidate facts identical.
-- These APIs mirror this installation's CivicsTree.lua. Native progress already
-- includes inspiration; applying the boost again would double count it.
local function readNumber(fn)
    local ok, value = pcall(fn)
    if ok and type(value) == "number" and value == value
        and value ~= math.huge and value ~= -math.huge and value >= 0 then
        return value
    end
    return nil
end
local function field(value)
    if value == nil then return "UNKNOWN" end
    return tostring(value)
end
local cultureYield = readNumber(function() return cu:GetCultureYield() end)
local civicFacts = {}
local function readCivic(index)
    if civicFacts[index] then return civicFacts[index] end
    local cost = readNumber(function() return cu:GetCultureCost(index) end)
    local progress = readNumber(function() return cu:GetCulturalProgress(index) end)
    local pct, turns, turnsSource = nil, nil, "unknown"
    if cost and cost > 0 and progress then
        pct = math.floor(progress * 100 / cost)
        if cultureYield and cultureYield > 0 then
            if index == civicIdx then
                turns = readNumber(function() return cu:GetTurnsLeft() end)
                if turns ~= nil then turnsSource = "native_current" end
            end
            if turns == nil then
                turns = readNumber(function() return cu:GetTurnsToProgressCivic(index) end)
                if turns ~= nil then turnsSource = "native_civic" end
            end
            if turns == nil then
                turns = math.ceil(math.max(0, cost - progress) / cultureYield)
                turnsSource = "remaining_cost_estimate"
            end
        end
    end
    local facts = {cost=cost, progress=progress, pct=pct, turns=turns, turnsSource=turnsSource}
    civicFacts[index] = facts
    return facts
end
local civicName = "None"
local civicTurns, civicPct, civicTurnsSource = nil, nil, "unknown"
if civicIdx >= 0 then
    civicName = Locale.Lookup(GameInfo.Civics[civicIdx].Name)
    local facts = readCivic(civicIdx)
    civicTurns, civicPct, civicTurnsSource = facts.turns, facts.pct, facts.turnsSource
end
print("CURRENT|" .. techName .. "|" .. techTurns .. "|" .. civicName .. "|" .. field(civicTurns) .. "|" .. field(civicPct) .. "|" .. civicTurnsSource)
-- Build boost lookup
local boostsByTech = {}
local boostsByCivic = {}
for b in GameInfo.Boosts() do
    if b.TechnologyType then boostsByTech[b.TechnologyType] = b end
    if b.CivicType then boostsByCivic[b.CivicType] = b end
end
-- Build tech prereqs lookup
local techPrereqs = {}
pcall(function()
    for row in GameInfo.TechnologyPrereqs() do
        if not techPrereqs[row.Technology] then techPrereqs[row.Technology] = {} end
        table.insert(techPrereqs[row.Technology], row.PrereqTech)
    end
end)
local techEligibility = {}
for tech in GameInfo.Technologies() do
    if not te:HasTech(tech.Index) then
        local checked, allowed = pcall(function() return te:CanResearch(tech.Index) end)
        if not checked or type(allowed) ~= "boolean" then
            print("ERR:ELIGIBILITY_UNKNOWN|" .. tech.TechnologyType)
            print("{SENTINEL}")
            return
        end
        techEligibility[tech.Index] = allowed
    end
    if techEligibility[tech.Index] == true then
        local cost = te:GetResearchCost(tech.Index)
        local progress = te:GetResearchProgress(tech.Index)
        local turns = te:GetTurnsToResearch(tech.Index)
        local pct = cost > 0 and math.floor(progress * 100 / cost) or 0
        local boosted = te:HasBoostBeenTriggered(tech.Index)
        local boostDesc = ""
        local b = boostsByTech[tech.TechnologyType]
        if b and b.TriggerDescription then
            boostDesc = Locale.Lookup(b.TriggerDescription):gsub("|", "/")
        end
        local unlocks = {}
        for u in GameInfo.Units() do if u.PrereqTech == tech.TechnologyType then table.insert(unlocks, Locale.Lookup(u.Name)) end end
        for bld in GameInfo.Buildings() do if bld.PrereqTech == tech.TechnologyType then table.insert(unlocks, Locale.Lookup(bld.Name)) end end
        for d in GameInfo.Districts() do if d.PrereqTech == tech.TechnologyType then table.insert(unlocks, Locale.Lookup(d.Name)) end end
        for imp in GameInfo.Improvements() do if imp.PrereqTech == tech.TechnologyType then table.insert(unlocks, Locale.Lookup(imp.Name)) end end
        for r in GameInfo.Resources() do
            if r.PrereqTech == tech.TechnologyType then table.insert(unlocks, "Reveals " .. Locale.Lookup(r.Name)) end
        end
        pcall(function()
            for proj in GameInfo.Projects() do
                if proj.PrereqTech == tech.TechnologyType then table.insert(unlocks, "Project: " .. Locale.Lookup(proj.Name)) end
            end
        end)
        local unlockStr = table.concat(unlocks, ", "):gsub("|", "/")
        local boostTag = boosted and "BOOSTED" or "UNBOOSTED"
        local prereqStr = ""
        if techPrereqs[tech.TechnologyType] then
            prereqStr = table.concat(techPrereqs[tech.TechnologyType], ",")
        end
        print("TECH|" .. Locale.Lookup(tech.Name) .. "|" .. tech.TechnologyType .. "|" .. cost .. "|" .. pct .. "|" .. turns .. "|" .. boostTag .. "|" .. boostDesc .. "|" .. unlockStr .. "|" .. prereqStr .. "|" .. (tech.EraType or ""))
    end
end
local completedTechs = 0
for tech in GameInfo.Technologies() do
    if te:HasTech(tech.Index) then completedTechs = completedTechs + 1 end
end
local completedCivics = 0
for civic in GameInfo.Civics() do
    if cu:HasCivic(civic.Index) then completedCivics = completedCivics + 1 end
end
print("COMPLETED|" .. completedTechs .. "|" .. completedCivics)
local curEra = Game.GetEras():GetCurrentEra()
local prereqs = {}
for row in GameInfo.CivicPrereqs() do
    if not prereqs[row.Civic] then prereqs[row.Civic] = {} end
    table.insert(prereqs[row.Civic], row.PrereqCivic)
end
local eraLookup = {}
for e in GameInfo.Eras() do eraLookup[e.EraType] = e.Index end
local civicEligibility = {}
for civic in GameInfo.Civics() do
    if not cu:HasCivic(civic.Index) then
        local civicEra = eraLookup[civic.EraType] or 99
        if civicEra <= curEra + 2 then
            -- CivicsTree/CivicsChooser use CanProgress for this game's tree.
            -- Static CivicPrereqs do not describe randomized future branches.
            local checked, canProgress = pcall(function() return cu:CanProgress(civic.Index) end)
            if not checked or type(canProgress) ~= "boolean" then
                print("ERR:ELIGIBILITY_UNKNOWN|" .. civic.CivicType)
                print("{SENTINEL}")
                return
            end
            civicEligibility[civic.Index] = canProgress
            if canProgress == true then
                local facts = readCivic(civic.Index)
                local cost, pct2, turns2 = facts.cost, facts.pct, facts.turns
                local boosted2 = cu:HasBoostBeenTriggered(civic.Index)
                local boostDesc2 = ""
                local b2 = boostsByCivic[civic.CivicType]
                if b2 and b2.TriggerDescription then
                    boostDesc2 = Locale.Lookup(b2.TriggerDescription):gsub("|", "/")
                end
                local boostTag2 = boosted2 and "BOOSTED" or "UNBOOSTED"
                local civicPrereqStr = ""
                if prereqs[civic.CivicType] then
                    civicPrereqStr = table.concat(prereqs[civic.CivicType], ",")
                end
                print("CIVIC|" .. Locale.Lookup(civic.Name) .. "|" .. civic.CivicType .. "|" .. field(cost) .. "|" .. field(pct2) .. "|" .. field(turns2) .. "|" .. boostTag2 .. "|" .. boostDesc2 .. "|" .. civicPrereqStr .. "|" .. (civic.EraType or "") .. "|" .. facts.turnsSource)
            end
        end
    end
end
-- Locked civics: within curEra + 2 only (skip far-future clutter)
for civic in GameInfo.Civics() do
    local civicEra = eraLookup[civic.EraType] or 99
    if civicEligibility[civic.Index] == false and civicEra <= curEra + 2 then
        local missing = {}
        if prereqs[civic.CivicType] then
            for _, pType in ipairs(prereqs[civic.CivicType]) do
                local pEntry = GameInfo.Civics[pType]
                if pEntry and not cu:HasCivic(pEntry.Index) then
                    table.insert(missing, (Locale.Lookup(pEntry.Name):gsub("|", "/")))
                end
            end
        end
        if #missing > 0 then
            local boostDesc = ""
            local b = boostsByCivic[civic.CivicType]
            if b and b.TriggerDescription then boostDesc = Locale.Lookup(b.TriggerDescription):gsub("|", "/") end
            local boostTag = cu:HasBoostBeenTriggered(civic.Index) and "BOOSTED" or "UNBOOSTED"
            print("LOCKED_CIVIC|" .. Locale.Lookup(civic.Name):gsub("|", "/") .. "|" .. civic.CivicType .. "|" .. table.concat(missing, ",") .. "|" .. (civic.EraType or "") .. "|" .. boostTag .. "|" .. boostDesc)
        end
    end
end
-- Locked techs: within curEra + 2 only (skip far-future clutter)
for tech in GameInfo.Technologies() do
    local techEra = eraLookup[tech.EraType] or 99
    if techEligibility[tech.Index] == false and techEra <= curEra + 2 then
        local missing = {}
        if techPrereqs[tech.TechnologyType] then
            for _, pType in ipairs(techPrereqs[tech.TechnologyType]) do
                local pEntry = GameInfo.Technologies[pType]
                if pEntry and not te:HasTech(pEntry.Index) then
                    table.insert(missing, (Locale.Lookup(pEntry.Name):gsub("|", "/")))
                end
            end
        end
        if #missing > 0 then
            local boostDesc = ""
            local b = boostsByTech[tech.TechnologyType]
            if b and b.TriggerDescription then boostDesc = Locale.Lookup(b.TriggerDescription):gsub("|", "/") end
            local boostTag = te:HasBoostBeenTriggered(tech.Index) and "BOOSTED" or "UNBOOSTED"
            print("LOCKED_TECH|" .. Locale.Lookup(tech.Name):gsub("|", "/") .. "|" .. tech.TechnologyType .. "|" .. table.concat(missing, ",") .. "|" .. (tech.EraType or "") .. "|" .. boostTag .. "|" .. boostDesc)
        end
    end
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def _build_selection_guard(name: str, gi_table: str) -> str:
    """Re-read native facts immediately before either existing write path.

    Static prerequisites cannot authorize a selection in the randomized tree.
    A current unfinished target is already selected and needs no submission.
    """
    is_tech = gi_table == "Technologies"
    player_method = "GetTechs" if is_tech else "GetCulture"
    has_method = "HasTech" if is_tech else "HasCivic"
    current_method = "GetResearchingTech" if is_tech else "GetProgressingCivic"
    can_method = "CanResearch" if is_tech else "CanProgress"
    return f"""
local completedRead, completed = pcall(function() return Players[id]:{player_method}():{has_method}(idx) end)
if not completedRead or type(completed) ~= "boolean" then
    {_bail(f"ERR:ELIGIBILITY_UNKNOWN|Native completion unavailable for {name}")}
end
if completed then
    {_bail(f"ERR:ALREADY_COMPLETED|{name} is already researched")}
end
local currentRead, current = pcall(function() return Players[id]:{player_method}():{current_method}() end)
if not currentRead or type(current) ~= "number" or current < -1 or current % 1 ~= 0 then
    {_bail(f"ERR:ELIGIBILITY_UNKNOWN|Native current target unavailable for {name}")}
end
if current == idx then
    {_bail(f"OK:ALREADY_SELECTED|{name}")}
end
local checked, allowed = pcall(function() return Players[id]:{player_method}():{can_method}(idx) end)
if not checked or type(allowed) ~= "boolean" then
    {_bail(f"ERR:ELIGIBILITY_UNKNOWN|Native eligibility unavailable for {name}")}
end
if not allowed then
    {_bail(f"ERR:NOT_ELIGIBLE|Native eligibility denied for {name}")}
end
"""


def _build_set_ingame(
    name: str,
    gi_table: str,
    type_field: str,
    param: str,
    operation: str,
    blocking: str,
    ok_prefix: str,
) -> str:
    """Shared builder for set_research / set_civic via InGame UI."""
    err_label = "TECH" if "Tech" in gi_table else "CIVIC"
    return f"""
local id = Game.GetLocalPlayer()
local idx = nil
for row in GameInfo.{gi_table}() do
    if row.{type_field} == "{name}" then idx = row.Index; break end
end
if idx == nil then {_bail(f"ERR:{err_label}_NOT_FOUND|{name}")} end
{_build_selection_guard(name, gi_table)}
local params = {{}}
params[PlayerOperations.{param}] = idx
UI.RequestPlayerOperation(id, PlayerOperations.{operation}, params)
local list = NotificationManager.GetList(id)
if list then
    for _, nid in ipairs(list) do
        local e = NotificationManager.Find(id, nid)
        if e and not e:IsDismissed() then
            local bt = e:GetEndTurnBlocking()
            if bt and bt == EndTurnBlockingTypes.{blocking} then
                pcall(function() NotificationManager.SendActivated(id, nid) end)
                pcall(function() NotificationManager.Dismiss(id, nid) end)
            end
        end
    end
end
print("{ok_prefix}|{name}")
print("{SENTINEL}")
"""


def build_set_research(tech_name: str) -> str:
    return _build_set_ingame(
        tech_name,
        "Technologies",
        "TechnologyType",
        "PARAM_TECH_TYPE",
        "RESEARCH",
        "ENDTURN_BLOCKING_RESEARCH",
        "OK:RESEARCHING",
    )


def build_set_civic(civic_name: str) -> str:
    return _build_set_ingame(
        civic_name,
        "Civics",
        "CivicType",
        "PARAM_CIVIC_TYPE",
        "PROGRESS_CIVIC",
        "ENDTURN_BLOCKING_CIVIC",
        "OK:PROGRESSING",
    )


def _build_set_gamecore(
    name: str,
    gi_table: str,
    type_field: str,
    player_method: str,
    setter: str,
    getter: str | None,
    ok_prefix: str,
) -> str:
    """Shared builder for set_research / set_civic via GameCore fallback."""
    err_label = "TECH" if "Tech" in gi_table else "CIVIC"
    verify = ""
    if getter:
        verify = f"""
local now = Players[id]:{player_method}():{getter}()
if now == idx then
    print("{ok_prefix}|{name}")
else
    {_bail(f"ERR:RESEARCH_FAILED|GameCore also failed to set {name}")}
end"""
    else:
        verify = f'\nprint("{ok_prefix}|{name}")'
    return f"""
local id = Game.GetLocalPlayer()
local idx = nil
for row in GameInfo.{gi_table}() do
    if row.{type_field} == "{name}" then idx = row.Index; break end
end
if idx == nil then {_bail(f"ERR:{err_label}_NOT_FOUND|{name}")} end
{_build_selection_guard(name, gi_table)}
Players[id]:{player_method}():{setter}(idx){verify}
print("{SENTINEL}")
"""


def build_set_research_gamecore(tech_name: str) -> str:
    """Set tech via GameCore — fallback when InGame RequestPlayerOperation silently fails."""
    return _build_set_gamecore(
        tech_name,
        "Technologies",
        "TechnologyType",
        "GetTechs",
        "SetResearchingTech",
        "GetResearchingTech",
        "OK:RESEARCHING_GAMECORE",
    )


def build_set_civic_gamecore(civic_name: str) -> str:
    """Set civic via GameCore — fallback when InGame RequestPlayerOperation silently fails."""
    return _build_set_gamecore(
        civic_name,
        "Civics",
        "CivicType",
        "GetCulture",
        "SetProgressingCivic",
        None,
        "OK:PROGRESSING_GC",
    )


def _optional_int(value: str) -> int | None:
    """The new query emits UNKNOWN; legacy numeric response fields still parse."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def parse_tech_civics_response(lines: list[str]) -> TechCivicStatus:
    current_research = "None"
    current_research_turns = -1
    current_civic = "None"
    current_civic_turns: int | None = -1
    current_civic_progress_pct: int | None = None
    current_civic_turns_source: str | None = None
    available_techs: list[TechOption] = []
    available_civics: list[CivicOption] = []
    completed_tech_count = 0
    completed_civic_count = 0

    locked_civics: list[LockedCivic] = []
    locked_techs: list[LockedTech] = []

    for line in lines:
        if line.startswith("ERR:"):
            raise ValueError(line)
        if line.startswith("COMPLETED|"):
            parts = line.split("|")
            completed_tech_count = int(parts[1]) if len(parts) > 1 else 0
            completed_civic_count = int(parts[2]) if len(parts) > 2 else 0
        elif line.startswith("CURRENT|"):
            parts = line.split("|")
            current_research = parts[1]
            current_research_turns = int(parts[2])
            current_civic = parts[3]
            current_civic_turns = _optional_int(parts[4])
            current_civic_progress_pct = _optional_int(parts[5]) if len(parts) > 5 else None
            current_civic_turns_source = parts[6] if len(parts) > 6 else None
        elif line.startswith("TECH|"):
            parts = line.split("|")
            if len(parts) >= 9:
                available_techs.append(
                    TechOption(
                        name=parts[1],
                        tech_type=parts[2],
                        cost=int(parts[3]),
                        progress_pct=int(parts[4]),
                        turns=int(parts[5]),
                        boosted=parts[6] == "BOOSTED",
                        boost_desc=parts[7],
                        unlocks=parts[8],
                        prereqs=parts[9] if len(parts) > 9 else "",
                        era=parts[10] if len(parts) > 10 else "",
                    )
                )
            elif len(parts) >= 3:
                available_techs.append(
                    TechOption(
                        name=parts[1],
                        tech_type=parts[2],
                        cost=0,
                        progress_pct=0,
                        turns=0,
                        boosted=False,
                        boost_desc="",
                        unlocks="",
                    )
                )
        elif line.startswith("CIVIC|"):
            parts = line.split("|")
            if len(parts) >= 8:
                available_civics.append(
                    CivicOption(
                        name=parts[1],
                        civic_type=parts[2],
                        cost=_optional_int(parts[3]),
                        progress_pct=_optional_int(parts[4]),
                        turns=_optional_int(parts[5]),
                        boosted=parts[6] == "BOOSTED",
                        boost_desc=parts[7],
                        prereqs=parts[8] if len(parts) > 8 else "",
                        era=parts[9] if len(parts) > 9 else "",
                        turns_source=parts[10] if len(parts) > 10 else None,
                    )
                )
            elif len(parts) >= 3:
                available_civics.append(
                    CivicOption(
                        name=parts[1],
                        civic_type=parts[2],
                        cost=0,
                        progress_pct=0,
                        turns=0,
                        boosted=False,
                        boost_desc="",
                    )
                )
        elif line.startswith("LOCKED_CIVIC|"):
            parts = line.split("|")
            if len(parts) >= 4:
                locked_civics.append(
                    LockedCivic(
                        name=parts[1],
                        civic_type=parts[2],
                        missing_prereqs=parts[3].split(","),
                        era=parts[4] if len(parts) > 4 else "",
                        boosted=parts[5] == "BOOSTED" if len(parts) > 5 else False,
                        boost_desc=parts[6] if len(parts) > 6 else "",
                    )
                )
        elif line.startswith("LOCKED_TECH|"):
            parts = line.split("|")
            if len(parts) >= 4:
                locked_techs.append(
                    LockedTech(
                        name=parts[1],
                        tech_type=parts[2],
                        missing_prereqs=parts[3].split(","),
                        era=parts[4] if len(parts) > 4 else "",
                        boosted=parts[5] == "BOOSTED" if len(parts) > 5 else False,
                        boost_desc=parts[6] if len(parts) > 6 else "",
                    )
                )

    return TechCivicStatus(
        current_research=current_research,
        current_research_turns=current_research_turns,
        current_civic=current_civic,
        current_civic_turns=current_civic_turns,
        current_civic_progress_pct=current_civic_progress_pct,
        current_civic_turns_source=current_civic_turns_source,
        available_techs=available_techs,
        available_civics=available_civics,
        completed_tech_count=completed_tech_count,
        completed_civic_count=completed_civic_count,
        locked_civics=locked_civics or None,
        locked_techs=locked_techs or None,
    )
