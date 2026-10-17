"""Overview domain — Lua builders and parsers."""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL, _int
from civ_mcp.lua.models import (
    GameOverStatus,
    GameOverview,
    ReligionInfo,
    ScoreCategory,
    ScoreEntry,
)


def build_overview_query() -> str:
    return """
local id = Game.GetLocalPlayer()
local p = Players[id]
local cfg = PlayerConfigurations[id]
local tr = p:GetTreasury()
local te = p:GetTechs()
local cu = p:GetCulture()
local re = p:GetReligion()
local techIdx = te:GetResearchingTech()
local civicIdx = cu:GetProgressingCivic()
local techName = "None"
if techIdx >= 0 then techName = Locale.Lookup(GameInfo.Technologies[techIdx].Name) end
local civicName = "None"
if civicIdx >= 0 then civicName = Locale.Lookup(GameInfo.Civics[civicIdx].Name) end
local nCities = 0; local totalPop = 0; for i, c in p:GetCities():Members() do nCities = nCities + 1; totalPop = totalPop + c:GetPopulation() end
local nUnits = 0
local unitCounts = {}
local unitMaint = 0
for i, u in p:GetUnits():Members() do
    nUnits = nUnits + 1
    local entry = GameInfo.Units[u:GetType()]
    if entry then
        local shortName = Locale.Lookup(entry.Name)
        unitCounts[shortName] = (unitCounts[shortName] or 0) + 1
        unitMaint = unitMaint + (entry.Maintenance or 0)
    end
end
local myScore = p:GetScore()
local favor = p:GetFavor()
local favorPerTurn = 0
local pDiplo = p:GetDiplomacy()
-- 1. Government tier bonus (govRow.Tier is a string like "GOVERNMENT_TIER_1")
local govIdx = cu:GetCurrentGovernment()
if govIdx >= 0 then
    local govRow = GameInfo.Governments[govIdx]
    if govRow and govRow.Tier then
        local n = tonumber(tostring(govRow.Tier):match("(%d+)$")) or 0
        favorPerTurn = favorPerTurn + math.max(1, n)
    end
end
-- 2. Alliances (level-based, +1 per alliance level)
for i = 0, 62 do
    if i ~= id and Players[i] and Players[i]:IsAlive() and Players[i]:IsMajor() and pDiplo:HasMet(i) then
        local ok, ai = pcall(function() return Players[i]:GetDiplomaticAI() end)
        if ok and ai then
            local ok2, stateIdx = pcall(function() return ai:GetDiplomaticStateIndex(id) end)
            if ok2 and stateIdx then
                local si = tonumber(stateIdx) or -1
                if si == 0 then
                    local ok3, aLvl = pcall(function() return pDiplo:GetAllianceLevel(i) end)
                    favorPerTurn = favorPerTurn + (tonumber(ok3 and aLvl) or 1)
                end
            end
        end
    end
end
-- 3. Suzerainties (+1 each)
for i = 0, 62 do
    if Players[i] and Players[i]:IsAlive() then
        local ok, canRecv = pcall(function() return Players[i]:GetInfluence():CanReceiveInfluence() end)
        if ok and canRecv then
            local suzID = Players[i]:GetInfluence():GetSuzerain()
            if suzID == id then favorPerTurn = favorPerTurn + 1 end
        end
    end
end
print(Game.GetCurrentGameTurn() .. "|" .. id .. "|" .. Locale.Lookup(cfg:GetCivilizationShortDescription()):gsub("|", "/") .. "|" .. Locale.Lookup(cfg:GetLeaderName()):gsub("|", "/") .. "|" .. string.format("%.1f", tr:GetGoldBalance()) .. "|" .. string.format("%.1f", tr:GetGoldYield() - tr:GetTotalMaintenance()) .. "|" .. string.format("%.1f", te:GetScienceYield()) .. "|" .. string.format("%.1f", cu:GetCultureYield()) .. "|" .. string.format("%.1f", re:GetFaithBalance()) .. "|" .. techName .. "|" .. civicName .. "|" .. nCities .. "|" .. nUnits .. "|" .. myScore .. "|" .. favor .. "|" .. favorPerTurn .. "|" .. totalPop .. "|" .. string.format("%.1f", tr:GetGoldYield()) .. "|" .. string.format("%.1f", tr:GetTotalMaintenance()))
-- These scores are already weighted by the engine. Keep GetScore above intact;
-- a complete category set explains native aggregate refresh differences.
local categoryCount = 0
local categoriesOK = pcall(function()
    for row in GameInfo.ScoringCategories() do
        assert(type(row.CategoryType) == "string" and row.CategoryType ~= "")
        assert(type(row.Multiplier) == "number" and row.Multiplier % 1 == 0)
        if row.Multiplier > 0 then
            local value = p:GetCategoryScore(row.Index)
            assert(type(value) == "number" and value >= 0 and value % 1 == 0)
            local name = row.CategoryType
            pcall(function() name = Locale.Lookup(row.Name or row.CategoryType) end)
            print("SCORECATEGORY|" .. row.CategoryType .. "|" .. tostring(name):gsub("[|\\r\\n]", "/") .. "|" .. row.Multiplier .. "|" .. value)
            categoryCount = categoryCount + 1
        end
    end
end)
print("SCORECATEGORIES|" .. (categoriesOK and tostring(categoryCount) or "unknown") .. "|" .. Game.GetCurrentGameTurn() .. "|" .. id)
for i = 0, 62 do
    if i ~= id and Players[i] and Players[i]:IsAlive() and Players[i]:IsMajor() and pDiplo:HasMet(i) then
        local oCfg = PlayerConfigurations[i]
        print("RANK|" .. i .. "|" .. Locale.Lookup(oCfg:GetCivilizationShortDescription()):gsub("|", "/") .. "|" .. Players[i]:GetScore())
    end
end
local pVis = PlayersVisibility[id]
local totalPlots = Map.GetPlotCount()
local revLand, totalLand = 0, 0
for i = 0, totalPlots - 1 do
    local plot = Map.GetPlotByIndex(i)
    if not plot:IsWater() then
        totalLand = totalLand + 1
        if pVis:IsRevealed(plot:GetX(), plot:GetY()) then revLand = revLand + 1 end
    end
end
print("EXPLORE|" .. revLand .. "|" .. totalLand)
local nMajors = 0
local nReligions = 0
for i = 0, 62 do
    if Players[i] and Players[i]:IsMajor() and Players[i]:IsAlive() then
        nMajors = nMajors + 1
        local rType = Players[i]:GetReligion():GetReligionTypeCreated()
        if rType >= 0 then
            nReligions = nReligions + 1
            if i == id or pDiplo:HasMet(i) then
                local rName = "Unknown"
                local rEntry = GameInfo.Religions[rType]
                if rEntry then rName = Locale.Lookup(rEntry.Name) end
                local oCfg = PlayerConfigurations[i]
                local civName = Locale.Lookup(oCfg:GetCivilizationShortDescription())
                print("REL|" .. i .. "|" .. civName .. "|" .. rName)
            end
        end
    end
end
local maxRel = math.floor(nMajors / 2) + 1
print("RELSLOTS|" .. nReligions .. "|" .. maxRel)
local eraManager = Game.GetEras()
local eraIdx = eraManager:GetCurrentEra()
local eraEntry = GameInfo.Eras[eraIdx]
local eraName = eraEntry and Locale.Lookup(eraEntry.Name) or "Unknown"
local eraScore = eraManager:GetPlayerCurrentScore(id)
local darkThresh = eraManager:GetPlayerDarkAgeThreshold(id)
local goldenThresh = eraManager:GetPlayerGoldenAgeThreshold(id)
print("ERA|" .. eraName .. "|" .. eraScore .. "|" .. darkThresh .. "|" .. goldenThresh)
local maxTurns = GameConfiguration.GetValue("GAME_MAX_TURNS") or 0
print("MAXTURNS|" .. maxTurns)
local diffName = "Unknown"
pcall(function()
    local diffHash = PlayerConfigurations[id]:GetHandicapTypeID()
    for d in GameInfo.Difficulties() do
        if GameConfiguration.MakeHash(d.DifficultyType) == diffHash then
            diffName = Locale.Lookup(d.Name)
            break
        end
    end
end)
print("DIFFICULTY|" .. diffName)
local ubParts = {}
for name, count in pairs(unitCounts) do
    table.insert(ubParts, name .. ":" .. count)
end
table.sort(ubParts, function(a, b)
    return tonumber(a:match(":(%d+)$")) > tonumber(b:match(":(%d+)$"))
end)
print("UNITBREAKDOWN|" .. table.concat(ubParts, ",") .. "|" .. unitMaint)
pcall(function()
    local gsIdx = Game.GetGameSpeedType()
    local gsRow = GameInfo.GameSpeeds[gsIdx]
    if gsRow then
        local gsName = Locale.Lookup(gsRow.Name)
        local gsMult = gsRow.CostMultiplier or 100
        print("SPEED|" .. gsRow.GameSpeedType .. "|" .. gsName .. "|" .. gsMult)
    end
end)
local vtypes = {"VICTORY_TECHNOLOGY", "VICTORY_CONQUEST", "VICTORY_CULTURE", "VICTORY_RELIGIOUS", "VICTORY_DIPLOMATIC"}
local vEnabled = {}
for _, vt in ipairs(vtypes) do
    local row = GameInfo.Victories[vt]
    if row then
        local ok, en = pcall(function() return Game.IsVictoryEnabled(row.Index) end)
        if ok and en then table.insert(vEnabled, vt) end
    end
end
if #vEnabled < #vtypes then
    print("VENABLED|" .. table.concat(vEnabled, ","))
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def build_gameover_check_gamecore() -> str:
    """GameCore: minimal game-over check that survives the defeat screen.

    Uses only Game.GetWinningTeam() and Players[] — no ContextPtr or UI
    lookups. Victory type is reported as Unknown (TestVictory may not work
    in GameCore). Better to detect the game-over with unknown type than
    miss it entirely.
    """
    return f"""
local winTeam = -1
pcall(function() winTeam = Game.GetWinningTeam() or -1 end)
local me = Game.GetLocalPlayer()
if winTeam < 0 then
    -- No formal winner yet — check for player elimination or turn limit.
    local meAlive = true
    pcall(function() meAlive = Players[me]:IsAlive() end)
    -- Check turn limit: Quick speed = 330, Standard = 500.
    -- Game.GetMaxTurns() returns 0 if no limit is set.
    local curTurn = Game.GetCurrentGameTurn()
    local maxTurns = 0
    pcall(function() maxTurns = Game.GetMaxTurns() end)
    if not meAlive then
        -- Player eliminated — all cities lost
        print("GAME_OVER|DEFEAT|Unknown|Elimination|dead|Unknown|-1")
        print("{SENTINEL}")
        return
    end
    if maxTurns > 0 and curTurn >= maxTurns then
        -- Turn limit reached — "Out of Time"
        print("GAME_OVER|DEFEAT|Unknown|TurnLimit|alive|Unknown|-1")
        print("{SENTINEL}")
        return
    end
    print("GAME_ACTIVE")
    print("{SENTINEL}")
    return
end
local winnerId = -1
local winnerName = "Unknown"
local winnerLeader = "Unknown"
for i = 0, 62 do
    local p = Players[i]
    if p and p:IsMajor() and p:IsAlive() and p:GetTeam() == winTeam then
        winnerId = i
        local cfg = PlayerConfigurations[i]
        pcall(function()
            winnerName = Locale.Lookup(cfg:GetCivilizationShortDescription())
            winnerLeader = Locale.Lookup(cfg:GetLeaderName())
        end)
        break
    end
end
local isDefeat = (winnerId ~= me)
local meAlive = true
pcall(function() meAlive = Players[me]:IsAlive() end)
print("GAME_OVER|" .. (isDefeat and "DEFEAT" or "VICTORY") .. "|" .. winnerName .. "|Unknown|" .. (meAlive and "alive" or "dead") .. "|" .. winnerLeader .. "|" .. tostring(winnerId))
print("{SENTINEL}")
"""


def build_gameover_check() -> str:
    """InGame: check if the game is over (EndGameMenu visible).

    Tries Game.TestVictory() first (direct engine query), then falls back to
    heuristic checks: religious majority, diplo VP >= 20 (scans all winning-team
    players), science VP >= needed, domination capitals, and culture tourism.
    Each check is pcall-wrapped so failures don't block detection.
    """
    return """
-- Primary check: GetWinningTeam is reliable regardless of UI state.
-- EndGameMenu control lookup can fail when blockers coexist with victory screen.
local winTeam = -1
pcall(function() winTeam = Game.GetWinningTeam() or -1 end)
local me = Game.GetLocalPlayer()
if winTeam < 0 then
    -- No formal winner — check EndGameMenu, elimination, and turn limit
    local egm = ContextPtr:LookUpControl("/InGame/EndGameMenu")
    if not egm or egm:IsHidden() then
        local meAlive = true
        pcall(function() meAlive = Players[me]:IsAlive() end)
        local curTurn = Game.GetCurrentGameTurn()
        local maxTurns = 0
        pcall(function() maxTurns = Game.GetMaxTurns() end)
        if not meAlive then
            print("GAME_OVER|DEFEAT|Unknown|Elimination|dead|Unknown|-1")
            print("{SENTINEL}")
            return
        end
        if maxTurns > 0 and curTurn >= maxTurns then
            print("GAME_OVER|DEFEAT|Unknown|TurnLimit|alive|Unknown|-1")
            print("{SENTINEL}")
            return
        end
        print("GAME_ACTIVE")
        print("{SENTINEL}")
        return
    end
end
local meAlive = Players[me]:IsAlive()
local winTeam = -1
pcall(function() winTeam = Game.GetWinningTeam() or -1 end)
local winnerId = -1
local winnerName = "Unknown"
local winnerLeader = "Unknown"
local victoryType = "Unknown"
if winTeam >= 0 then
    for i = 0, 62 do
        local p = Players[i]
        if p and p:IsMajor() and p:IsAlive() and p:GetTeam() == winTeam then
            winnerId = i
            local cfg = PlayerConfigurations[i]
            winnerName = Locale.Lookup(cfg:GetCivilizationShortDescription())
            winnerLeader = Locale.Lookup(cfg:GetLeaderName())
            break
        end
    end
end
if winnerId >= 0 then
    local wp = Players[winnerId]
    -- Try each GameInfo.Victories row with Game.TestVictory first (direct engine query)
    pcall(function()
        for row in GameInfo.Victories() do
            if Game.TestVictory(row.Index) then
                victoryType = row.VictoryType
                break
            end
        end
    end)
    -- Fallback heuristics if TestVictory didn't work (can be stale on EndGameMenu)
    if victoryType == "Unknown" then
        -- Religious: winner's religion is majority in all (or nearly all) major civs
        pcall(function()
            local relCreated = wp:GetReligion():GetReligionTypeCreated()
            if relCreated >= 0 then
                local totalM, convM = 0, 0
                for i = 0, 62 do
                    local q = Players[i]
                    if q and q:IsMajor() and q:IsAlive() then
                        totalM = totalM + 1
                        if q:GetReligion():GetReligionInMajorityOfCities() == relCreated then convM = convM + 1 end
                    end
                end
                if convM >= totalM - 1 then victoryType = "VICTORY_RELIGIOUS" end
            end
        end)
    end
    if victoryType == "Unknown" then
        -- Diplomatic: any player with 20+ diplo VP (check all, not just winner — GetStats may fail on winner)
        pcall(function()
            for i = 0, 62 do
                local q = Players[i]
                if q and q:IsMajor() and q:IsAlive() and q:GetTeam() == winTeam then
                    local ok, dvp = pcall(function() return q:GetStats():GetDiplomaticVictoryPoints() end)
                    if ok and dvp and dvp >= 20 then victoryType = "VICTORY_DIPLOMATIC" end
                end
            end
        end)
    end
    if victoryType == "Unknown" then
        -- Science: completed space projects
        pcall(function()
            local ok, svp = pcall(function() return wp:GetStats():GetScienceVictoryPoints() end)
            local ok2, needed = pcall(function() return wp:GetStats():GetScienceVictoryPointsTotalNeeded() end)
            if ok and ok2 and svp and needed and svp >= needed then victoryType = "VICTORY_TECHNOLOGY" end
        end)
    end
    if victoryType == "Unknown" then
        -- Domination: owns all original capitals
        pcall(function()
            local caps, owned = 0, 0
            for i = 0, 62 do
                local q = Players[i]
                if q and q:IsMajor() and q:IsAlive() and i ~= winnerId then
                    caps = caps + 1
                    local oCap = q:GetCities():GetOriginalCapital()
                    if oCap and oCap:GetOwner() == winnerId then owned = owned + 1 end
                end
            end
            if caps > 0 and owned >= caps then victoryType = "VICTORY_CONQUEST" end
        end)
    end
    if victoryType == "Unknown" then
        -- Culture: winner's visiting tourists exceed all others' domestic tourists
        pcall(function()
            local dominated = true
            local checked = 0
            for i = 0, 62 do
                local q = Players[i]
                if q and q:IsMajor() and q:IsAlive() and i ~= winnerId then
                    local okS, stay = pcall(function() return q:GetCulture():GetStaycationers() end)
                    local okV, visit = pcall(function() return wp:GetCulture():GetTouristsFrom(i) end)
                    if okS and okV and stay and visit then
                        checked = checked + 1
                        if visit < stay then
                            dominated = false
                            break
                        end
                    end
                end
            end
            if checked > 0 and dominated then victoryType = "VICTORY_CULTURE" end
        end)
    end
    if victoryType == "Unknown" then
        -- Score: turn limit reached without any other victory condition
        victoryType = "VICTORY_SCORE"
    end
end
-- Emit per-player VP data so Python can cross-check victory type
-- (EndGameMenu APIs for tourism/staycationers are unreliable)
pcall(function()
    for i = 0, 62 do
        local q = Players[i]
        if q and q:IsMajor() and q:IsAlive() then
            local tourism, stay, sciVP, sciNeeded, diploVP = 0, 0, 0, 50, 0
            pcall(function() tourism = q:GetStats():GetTourism() end)
            pcall(function() stay = q:GetCulture():GetStaycationers() end)
            pcall(function() sciVP = q:GetStats():GetScienceVictoryPoints() end)
            pcall(function() sciNeeded = q:GetStats():GetScienceVictoryPointsTotalNeeded() end)
            pcall(function() diploVP = q:GetStats():GetDiplomaticVictoryPoints() end)
            print("VP_DATA|" .. i .. "|" .. tourism .. "|" .. stay .. "|" .. sciVP .. "|" .. sciNeeded .. "|" .. diploVP)
        end
    end
end)
local isDefeat = winTeam >= 0 and Players[me]:GetTeam() ~= winTeam
local result = isDefeat and "DEFEAT" or "VICTORY"
print("GAME_OVER|" .. result .. "|" .. winnerName .. "|" .. victoryType .. "|" .. (meAlive and "alive" or "dead") .. "|" .. winnerLeader .. "|" .. winnerId)
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def _infer_victory_from_vp_data(vp_data: dict[int, dict], winner_id: int) -> str | None:
    """Infer victory type from per-player VP data when Lua detection fails.

    Returns a VICTORY_* string or None if Score is genuine.
    """
    winner = vp_data.get(winner_id)
    if not winner:
        return None

    # Science: winner completed all space projects
    if winner["sci_vp"] > 0 and winner["sci_vp"] >= winner["sci_needed"]:
        return "VICTORY_TECHNOLOGY"

    # Diplomatic: 20+ DVP
    if winner["diplo_vp"] >= 20:
        return "VICTORY_DIPLOMATIC"

    # Culture: winner's tourism exceeds all rivals' staycationers
    rivals = {pid: d for pid, d in vp_data.items() if pid != winner_id}
    if winner["tourism"] > 0 and rivals:
        max_rival_stay = max(r["stay"] for r in rivals.values())
        if winner["tourism"] > max_rival_stay:
            return "VICTORY_CULTURE"

    return None


def parse_gameover_response(lines: list[str]) -> GameOverStatus | None:
    """Parse game-over check response. Returns None if game is still active."""
    game_over_line = None
    vp_data: dict[int, dict] = {}

    for line in lines:
        if line == "GAME_ACTIVE":
            return None
        if line.startswith("GAME_OVER|"):
            game_over_line = line
        elif line.startswith("VP_DATA|"):
            parts = line.split("|")
            if len(parts) >= 7:
                pid = int(parts[1])
                vp_data[pid] = {
                    "tourism": int(parts[2]),
                    "stay": int(parts[3]),
                    "sci_vp": int(parts[4]),
                    "sci_needed": int(parts[5]),
                    "diplo_vp": int(parts[6]),
                }

    if not game_over_line:
        return None

    parts = game_over_line.split("|")
    victory_type = parts[3] if len(parts) > 3 else "Unknown"
    winner_name = parts[2] if len(parts) > 2 else "Unknown"

    # Cross-check: if Lua reported Score but VP data suggests otherwise
    winner_id = (
        int(parts[6]) if len(parts) > 6 and parts[6].lstrip("-").isdigit() else -1
    )
    if victory_type == "VICTORY_SCORE" and vp_data and winner_id >= 0:
        corrected = _infer_victory_from_vp_data(vp_data, winner_id)
        if corrected:
            victory_type = corrected

    return GameOverStatus(
        is_game_over=True,
        is_defeat=parts[1] == "DEFEAT",
        winner_name=winner_name,
        winner_leader=parts[5] if len(parts) > 5 else "Unknown",
        victory_type=victory_type,
        player_alive=parts[4] == "alive" if len(parts) > 4 else True,
    )


def parse_overview_response(lines: list[str]) -> GameOverview:
    if not lines:
        raise ValueError("Empty overview response")
    parts = lines[0].split("|")
    if len(parts) < 14:
        raise ValueError(
            f"Overview response has {len(parts)} fields, expected >=14: {lines[0]}"
        )
    rankings: list[ScoreEntry] = []
    explored_land = 0
    total_land = 0
    religions_founded = 0
    religions_max = 0
    our_religion: str | None = None
    founded_religions: list[ReligionInfo] = []
    era_name = ""
    era_score = 0
    era_dark_threshold = 0
    era_golden_threshold = 0
    max_turns = 0
    difficulty = ""
    unit_breakdown: dict[str, int] | None = None
    unit_maintenance = 0
    game_speed = ""
    game_speed_name = ""
    speed_cost_multiplier = 100
    enabled_victories: set[str] = set()
    score_categories: list[ScoreCategory] = []
    category_markers: list[list[str]] = []
    categories_valid = True
    player_id_parsed = int(parts[1])
    for line in lines[1:]:
        if line.startswith("SCORECATEGORY|"):
            cp = line.split("|")
            try:
                if len(cp) != 5 or not cp[1].isidentifier() or not cp[2].strip():
                    raise ValueError("Malformed category")
                category = ScoreCategory(cp[1], cp[2], int(cp[3]), int(cp[4]))
                if (
                    category.multiplier <= 0 or category.score < 0
                    or any(c.category_type == category.category_type for c in score_categories)
                ):
                    raise ValueError("Invalid or duplicate category")
                score_categories.append(category)
            except ValueError:
                categories_valid = False
        elif line.startswith("SCORECATEGORIES|"):
            category_markers.append(line.split("|"))
        elif line.startswith("RANK|"):
            rp = line.split("|")
            if len(rp) >= 4:
                rankings.append(
                    ScoreEntry(
                        player_id=int(rp[1]),
                        civ_name=rp[2],
                        score=int(rp[3]),
                    )
                )
        elif line.startswith("EXPLORE|"):
            ep = line.split("|")
            if len(ep) >= 3:
                explored_land = int(ep[1])
                total_land = int(ep[2])
        elif line.startswith("REL|"):
            rp = line.split("|")
            if len(rp) >= 4:
                ri = ReligionInfo(
                    player_id=int(rp[1]),
                    civ_name=rp[2],
                    religion_name=rp[3],
                )
                founded_religions.append(ri)
                if int(rp[1]) == player_id_parsed:
                    our_religion = rp[3]
        elif line.startswith("RELSLOTS|"):
            rp = line.split("|")
            if len(rp) >= 3:
                religions_founded = int(rp[1])
                religions_max = int(rp[2])
        elif line.startswith("ERA|"):
            ep = line.split("|")
            if len(ep) >= 5:
                era_name = ep[1]
                era_score = int(ep[2])
                era_dark_threshold = int(ep[3])
                era_golden_threshold = int(ep[4])
        elif line.startswith("MAXTURNS|"):
            ep = line.split("|")
            if len(ep) >= 2:
                max_turns = int(ep[1])
        elif line.startswith("DIFFICULTY|"):
            difficulty = line.split("|", 1)[1]
        elif line.startswith("SPEED|"):
            sp = line.split("|")
            if len(sp) >= 4:
                game_speed = sp[1]
                game_speed_name = sp[2]
                try:
                    speed_cost_multiplier = int(sp[3])
                except ValueError:
                    pass
        elif line.startswith("VENABLED|"):
            enabled_victories = set(line.split("|", 1)[1].split(","))
        elif line.startswith("UNITBREAKDOWN|"):
            bp = line.split("|")
            if len(bp) >= 3:
                unit_breakdown = {}
                for pair in bp[1].split(","):
                    if ":" in pair:
                        name, count = pair.rsplit(":", 1)
                        try:
                            unit_breakdown[name] = int(count)
                        except ValueError:
                            pass
                try:
                    unit_maintenance = int(bp[2])
                except ValueError:
                    pass
    try:
        (marker,) = category_markers
        categories_valid = (
            categories_valid and len(marker) == 4
            and int(marker[1]) == len(score_categories) > 0
            and int(marker[2]) == int(parts[0])
            and int(marker[3]) == player_id_parsed
        )
    except (ValueError, IndexError):
        categories_valid = False
    return GameOverview(
        turn=int(parts[0]),
        player_id=int(parts[1]),
        civ_name=parts[2],
        leader_name=parts[3],
        gold=float(parts[4]),
        gold_per_turn=float(parts[5]),
        science_yield=float(parts[6]),
        culture_yield=float(parts[7]),
        faith=float(parts[8]),
        current_research=parts[9],
        current_civic=parts[10],
        num_cities=int(parts[11]),
        num_units=int(parts[12]),
        score=int(parts[13]) if len(parts) > 13 else 0,
        score_categories=score_categories if categories_valid else None,
        diplomatic_favor=int(parts[14]) if len(parts) > 14 else 0,
        favor_per_turn=_int(parts[15]) if len(parts) > 15 else 0,
        total_population=int(parts[16]) if len(parts) > 16 else 0,
        gold_income=float(parts[17]) if len(parts) > 17 else 0.0,
        total_maintenance=float(parts[18]) if len(parts) > 18 else 0.0,
        unit_maintenance=unit_maintenance,
        unit_breakdown=unit_breakdown,
        explored_land=explored_land,
        total_land=total_land,
        rankings=rankings if rankings else None,
        religions_founded=religions_founded,
        religions_max=religions_max,
        our_religion=our_religion,
        founded_religions=founded_religions if founded_religions else None,
        era_name=era_name,
        era_score=era_score,
        era_dark_threshold=era_dark_threshold,
        era_golden_threshold=era_golden_threshold,
        max_turns=max_turns,
        difficulty=difficulty,
        game_speed=game_speed,
        game_speed_name=game_speed_name,
        speed_cost_multiplier=speed_cost_multiplier,
        enabled_victories=enabled_victories,
    )
