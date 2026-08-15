"""Era progress queries — chronological eras and (XP1+) era score."""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.lua.models import (
    EraAgeDetail,
    EraProgress,
    EraProgressPlayer,
    EraTypeRow,
)


def build_era_progress_query() -> str:
    """Read world era, per-player eras, and (XP1+) era score progress."""
    return """
local id = Game.GetLocalPlayer()
local activeRuleset = GameConfiguration.GetValue("RULESET")
if GameConfiguration.GetRuleSet ~= nil then
    pcall(function() activeRuleset = GameConfiguration.GetRuleSet() end)
end
print("RULESET|" .. tostring(activeRuleset or "UNKNOWN"))
print("LOCAL|" .. id)
local xp1 = activeRuleset ~= nil and activeRuleset ~= "RULESET_STANDARD"

-- Era sequence for this game (GameInfo.Eras, all rulesets)
local eras = {}
for row in GameInfo.Eras() do table.insert(eras, {Index=row.Index, Type=row.EraType, Chron=row.ChronologyIndex or 99, Name=Locale.Lookup(row.Name):gsub("|","/")}) end
table.sort(eras, function(a,b) return a.Chron < b.Chron end)
for _, e in ipairs(eras) do print("ERAS|idx" .. e.Index .. "|" .. e.Type .. "|" .. e.Name) end

-- World era (needs Game.GetEras; nil only on installs without Rise and Fall)
local pGameEras = nil
pcall(function() if Game.GetEras ~= nil then pGameEras = Game.GetEras() end end)
local finalEra = false
if pGameEras ~= nil then
    local cur = pGameEras:GetCurrentEra()
    local entry = GameInfo.Eras[cur]
    finalEra = (cur == pGameEras:GetFinalEra())
    print("GAMEERA|" .. cur .. "|" .. (entry and entry.EraType or "UNKNOWN")
      .. "|" .. (entry and Locale.Lookup(entry.Name):gsub("|","/") or "Unknown")
      .. "|" .. tostring(finalEra))
end

-- XP1+ era clock (ruleset-gated; countdown/min/max are Eras_XP1 mechanics)
if xp1 and pGameEras ~= nil then
    local st, cd, mn, mx, ma, ml = -1, -1, -1, -1, -1, -1
    pcall(function() st = pGameEras:GetCurrentEraStartTurn() end)
    pcall(function() cd = pGameEras:GetNextEraCountdown() end)
    pcall(function() mn = pGameEras:GetCurrentEraMinimumEndTurn() end)
    pcall(function() mx = pGameEras:GetCurrentEraMaximumEndTurn() end)
    pcall(function() ma = pGameEras:GetCurrentEraNumPlayersMoreAdvanced() end)
    pcall(function() ml = pGameEras:GetCurrentEraNumPlayersAsOrLessAdvanced() end)
    print("CLOCK|" .. st .. "|" .. cd .. "|" .. mn .. "|" .. mx .. "|" .. ma .. "|" .. ml)
end

-- Per-player chronological era (all rulesets) + age/score (XP1+ only)
for i = 0, 62 do
    local p = Players[i]
    if p and p:IsMajor() and p:IsAlive() then
        local cfg = PlayerConfigurations[i]
        local civName = Locale.Lookup(cfg:GetCivilizationShortDescription()):gsub("|","/")
        local eraIdx = -1
        pcall(function() eraIdx = p:GetEra() end)
        local entry = GameInfo.Eras[eraIdx]
        local eraType = (eraIdx >= 0 and entry) and entry.EraType or "UNKNOWN"
        local eraName = (eraIdx >= 0 and entry) and Locale.Lookup(entry.Name):gsub("|","/") or "Unknown"
        local age, score = "-", -1
        if xp1 and pGameEras ~= nil then
            pcall(function()
                if pGameEras:HasHeroicGoldenAge(i) then age = "Heroic"
                elseif pGameEras:HasGoldenAge(i) then age = "Golden"
                elseif pGameEras:HasDarkAge(i) then age = "Dark"
                else age = "Normal" end
                score = pGameEras:GetPlayerCurrentScore(i)
            end)
        end
        print("PERA|" .. i .. "|" .. civName .. "|" .. eraIdx .. "|" .. eraType
          .. "|" .. eraName .. "|" .. age .. "|" .. score)
    end
end

-- Local player age progress detail (XP1+ only)
if xp1 and pGameEras ~= nil then
    local score, dark, golden, baseline, prev = 0, 0, 0, 0, 0
    pcall(function() score = pGameEras:GetPlayerCurrentScore(id) end)
    pcall(function() dark = pGameEras:GetPlayerDarkAgeThreshold(id) end)
    pcall(function() golden = pGameEras:GetPlayerGoldenAgeThreshold(id) end)
    pcall(function() baseline = pGameEras:GetPlayerThresholdBaseline(id) end)
    pcall(function() prev = pGameEras:GetPlayerPreviousScore(id) end)
    print("AGE|" .. score .. "|" .. dark .. "|" .. golden .. "|" .. baseline .. "|" .. prev)
    pcall(function()
        local bd = pGameEras:GetPlayerCurrentEraScoreBreakdown(id)
        if bd then
            for _, source in ipairs(bd) do
                for sourceString, sourceValue in pairs(source) do
                    if sourceValue and sourceValue ~= 0 then
                        print("AGEDETAIL|" .. tostring(sourceString):gsub("[|,]","/") .. "|" .. sourceValue)
                    end
                end
            end
        end
    end)
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def _opt_int(value: str) -> int | None:
    """Map the -1 Lua sentinel to None."""

    number = int(value)
    return number if number >= 0 else None


def parse_era_progress_response(lines: list[str]) -> EraProgress:
    """Parse RULESET/ERAS/GAMEERA/CLOCK/PERA/AGE/AGEDETAIL rows.

    A missing ``RULESET|`` row is a protocol error (ValueError); every other
    missing row simply leaves its field at the None/empty default because
    "no GameEras install" and "Standard ruleset" are both legal line sets.
    Malformed lines are skipped.
    """

    ruleset: str | None = None
    era_sequence: list[EraTypeRow] = []
    current_era_index: int | None = None
    current_era_type = ""
    current_era_name = ""
    final_era = False
    era_start_turn: int | None = None
    next_era_countdown: int | None = None
    min_end_turn: int | None = None
    max_end_turn: int | None = None
    players_more_advanced: int | None = None
    players_as_or_less_advanced: int | None = None
    players: list[EraProgressPlayer] = []
    local_age: EraAgeDetail | None = None
    local_player: int | None = None

    for line in lines:
        parts = line.split("|")
        try:
            if line.startswith("RULESET|") and len(parts) >= 2:
                ruleset = parts[1] or "UNKNOWN"
            elif line.startswith("LOCAL|") and len(parts) >= 2:
                local_player = int(parts[1])
            elif line.startswith("ERAS|") and len(parts) >= 4:
                index_text = parts[1]
                if index_text.startswith("idx"):
                    index_text = index_text[3:]
                era_sequence.append(
                    EraTypeRow(
                        era_index=int(index_text),
                        era_type=parts[2],
                        era_name=parts[3],
                    )
                )
            elif line.startswith("GAMEERA|") and len(parts) >= 5:
                current_era_index = int(parts[1])
                current_era_type = parts[2]
                current_era_name = parts[3]
                final_era = parts[4] == "true"
            elif line.startswith("CLOCK|") and len(parts) >= 7:
                era_start_turn = _opt_int(parts[1])
                next_era_countdown = _opt_int(parts[2])
                min_end_turn = _opt_int(parts[3])
                max_end_turn = _opt_int(parts[4])
                players_more_advanced = _opt_int(parts[5])
                players_as_or_less_advanced = _opt_int(parts[6])
            elif line.startswith("PERA|") and len(parts) >= 8:
                era_index = int(parts[3])
                age_text = parts[6]
                score_text = parts[7]
                players.append(
                    EraProgressPlayer(
                        player_id=int(parts[1]),
                        civ_name=parts[2],
                        is_local=False,  # patched below once the local id is known
                        era_index=era_index,
                        era_type=parts[4],
                        era_name=parts[5],
                        age=None if age_text == "-" else age_text,
                        era_score=None
                        if score_text == "-" or int(score_text) < 0
                        else int(score_text),
                    )
                )
            elif line.startswith("AGE|") and len(parts) >= 6:
                local_age = EraAgeDetail(
                    era_score=int(parts[1]),
                    dark_threshold=int(parts[2]),
                    golden_threshold=int(parts[3]),
                    threshold_baseline=int(parts[4]),
                    previous_era_score=int(parts[5]),
                )
            elif line.startswith("AGEDETAIL|") and len(parts) >= 3 and local_age is not None:
                local_age.score_breakdown.append((parts[1], int(parts[2])))
        except (IndexError, TypeError, ValueError):
            continue

    if ruleset is None:
        raise ValueError("era progress query missing RULESET row")

    for player in players:
        player.is_local = player.player_id == local_player

    return EraProgress(
        ruleset=ruleset,
        ages_supported=ruleset != "RULESET_STANDARD",
        current_era_index=current_era_index,
        current_era_type=current_era_type,
        current_era_name=current_era_name,
        final_era=final_era,
        era_sequence=era_sequence,
        era_start_turn=era_start_turn,
        next_era_countdown=next_era_countdown,
        min_end_turn=min_end_turn,
        max_end_turn=max_end_turn,
        players_more_advanced=players_more_advanced,
        players_as_or_less_advanced=players_as_or_less_advanced,
        players=players,
        local_age=local_age,
    )
