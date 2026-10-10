"""World Congress domain — Lua builders and parsers."""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL, _bail
from civ_mcp.lua.models import CongressProposal, CongressResolution, WorldCongressStatus


def build_world_congress_query() -> str:
    """Get World Congress status, resolutions, and proposals (InGame context)."""
    return f"""
local me = Game.GetLocalPlayer()
local pDiplo = Players[me]:GetDiplomacy()
local wc = Game.GetWorldCongress()
if not wc then {_bail("ERR:NO_WORLD_CONGRESS|World Congress not available yet")} end
local inSession = wc:IsInSession()
local meeting = wc:GetMeetingStatus()
local turnsLeft = meeting and meeting.TurnsLeft or -1
local favor = Players[me]:GetFavor()
local costs = wc:GetVotesandFavorCost()
local maxVotes = costs.MaxVotes or 5
local costStr = ""
for i = 0, maxVotes do
    if i > 0 then costStr = costStr .. "," end
    costStr = costStr .. tostring(costs[i] or 0)
end
print("WC_STATUS|" .. tostring(inSession) .. "|" .. turnsLeft .. "|" .. favor .. "|" .. maxVotes .. "|" .. costStr)
local ress = wc:GetResolutions()
if ress then
    for _, res in ipairs(ress) do
        local rType = res.Type
        local gRes = nil
        for row in GameInfo.Resolutions() do
            if row.Hash == rType then gRes = row end
        end
        local typeName = gRes and gRes.ResolutionType or ("HASH_" .. tostring(rType))
        local name = gRes and Locale.Lookup(gRes.Name) or "Unknown"
        local targetKind = gRes and (gRes.TargetKind or "") or ""
        local effectA = gRes and gRes.Effect1Description and Locale.Lookup(gRes.Effect1Description) or ""
        local effectB = gRes and gRes.Effect2Description and Locale.Lookup(gRes.Effect2Description) or ""
        local isPassed = "0"
        local winner = -1
        local chosen = ""
        if not inSession then
            isPassed = "1"
            winner = res.Winner or -1
            if res.ChosenThing then
                if res.TargetType == "PlayerType" then
                    local pid = tonumber(res.ChosenThing)
                    if pid and PlayerConfigurations[pid] and pDiplo:HasMet(pid) then
                        chosen = Locale.Lookup(PlayerConfigurations[pid]:GetCivilizationShortDescription())
                    else
                        chosen = "Unmet Player"
                    end
                else
                    chosen = Locale.Lookup(res.ChosenThing)
                end
            end
        end
        local targets = ""
        if res.PossibleTargets then
            local isPlayerType = (res.TargetType == "PlayerType")
            for ti, tgt in ipairs(res.PossibleTargets) do
                if ti > 1 then targets = targets .. "~" end
                local tName = ""
                local tId = tostring(ti - 1)  -- 0-based index as fallback ID
                if isPlayerType then
                    -- PlayerType: targets are player IDs (numbers)
                    local pid = tonumber(tgt)
                    tId = tostring(pid or (ti - 1))
                    if pid and PlayerConfigurations[pid] and pDiplo:HasMet(pid) then
                        tName = Locale.Lookup(PlayerConfigurations[pid]:GetCivilizationShortDescription())
                    else
                        tName = "Unmet Player"
                    end
                else
                    -- Other types (District, Yield, etc.): targets are LOC key strings
                    local ok, resolved = pcall(Locale.Lookup, tostring(tgt))
                    if ok and resolved then tName = resolved
                    else tName = tostring(tgt) end
                end
                targets = targets .. tId .. ":" .. tName
            end
        end
        effectA = effectA:gsub("|", "/"):gsub("~", "-")
        effectB = effectB:gsub("|", "/"):gsub("~", "-")
        name = name:gsub("|", "/"):gsub("~", "-")
        chosen = chosen:gsub("|", "/"):gsub("~", "-")
        print("WC_RES|" .. rType .. "|" .. typeName .. "|" .. name .. "|" .. targetKind .. "|" .. effectA .. "|" .. effectB .. "|" .. isPassed .. "|" .. winner .. "|" .. chosen .. "|" .. targets)
    end
end
if inSession then
    local props = wc:GetProposals()
    if props then
        for _, prop in ipairs(props) do
            local sid = prop.SenderID or -1
            local tid = prop.TargetID or -1
            local sName = sid >= 0 and Locale.Lookup(PlayerConfigurations[sid]:GetCivilizationShortDescription()) or "Unknown"
            local tName = tid >= 0 and Locale.Lookup(PlayerConfigurations[tid]:GetCivilizationShortDescription()) or "Unknown"
            local pType = prop.Type or 0
            local desc = prop.Description and Locale.Lookup(prop.Description) or ""
            desc = desc:gsub("|", "/"):gsub("~", "-")
            sName = sName:gsub("|", "/")
            tName = tName:gsub("|", "/")
            print("WC_PROP|" .. sid .. "|" .. sName .. "|" .. tid .. "|" .. tName .. "|" .. pType .. "|" .. desc)
        end
    end
end
print("{SENTINEL}")
"""


def parse_world_congress_response(lines: list[str]) -> WorldCongressStatus:
    """Parse WC_STATUS / WC_RES / WC_PROP lines into WorldCongressStatus."""
    status = WorldCongressStatus(
        is_in_session=False,
        turns_until_next=-1,
        favor=0,
        max_votes=5,
        favor_costs=[],
        resolutions=[],
        proposals=[],
    )
    for line in lines:
        if line.startswith("WC_STATUS|"):
            parts = line.split("|")
            status.is_in_session = parts[1] == "true"
            status.turns_until_next = int(parts[2])
            status.favor = int(parts[3])
            status.max_votes = int(parts[4])
            if len(parts) > 5 and parts[5]:
                status.favor_costs = [int(x) for x in parts[5].split(",")]
        elif line.startswith("WC_RES|"):
            parts = line.split("|")
            targets = parts[10].split("~") if len(parts) > 10 and parts[10] else []
            status.resolutions.append(
                CongressResolution(
                    resolution_type=parts[2],
                    resolution_hash=int(parts[1]),
                    name=parts[3],
                    target_kind=parts[4],
                    effect_a=parts[5],
                    effect_b=parts[6],
                    possible_targets=targets,
                    is_passed=parts[7] == "1",
                    winner=int(parts[8]),
                    chosen_thing=parts[9],
                )
            )
        elif line.startswith("WC_PROP|"):
            parts = line.split("|")
            status.proposals.append(
                CongressProposal(
                    sender_id=int(parts[1]),
                    sender_name=parts[2],
                    target_id=int(parts[3]),
                    target_name=parts[4],
                    proposal_type=int(parts[5]),
                    description=parts[6] if len(parts) > 6 else "",
                )
            )
    return status


def build_congress_vote(
    resolution_hash: int, option: int, target_index: int, num_votes: int
) -> str:
    """Vote on a World Congress resolution (InGame context).

    option: 1=A, 2=B
    target_index: 0-based index into PossibleTargets
    num_votes: total votes to commit (A.votes + B.votes = this value, allocated to chosen option)
    """
    return f"""
local me = Game.GetLocalPlayer()
local kParams = {{}}
kParams[PlayerOperations.PARAM_RESOLUTION_TYPE] = {resolution_hash}
kParams[PlayerOperations.PARAM_WORLD_CONGRESS_VOTES] = {num_votes}
kParams[PlayerOperations.PARAM_RESOLUTION_OPTION] = {option}
kParams[PlayerOperations.PARAM_RESOLUTION_SELECTION] = {target_index}
UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_RESOLUTION_VOTE, kParams)
print("OK:VOTED|res:{resolution_hash}|option:{option}|target:{target_index}|votes:{num_votes}")
print("{SENTINEL}")
"""


def build_congress_submit() -> str:
    """Submit all World Congress votes and resume turn processing (InGame context).

    Mirrors WorldCongressPopup.lua OnAccept(): submit votes then ACTION_ENDTURN
    to resume turn-segment processing after the WC stage.
    """
    return """
local me = Game.GetLocalPlayer()
local intro = ContextPtr:LookUpControl("/InGame/WorldCongressIntro")
if intro then intro:SetHide(true) end
local popup = ContextPtr:LookUpControl("/InGame/WorldCongressPopup")
if popup then popup:SetHide(true) end
UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_SUBMIT_TURN, {})
UI.RequestAction(ActionTypes.ACTION_ENDTURN)
print("OK:CONGRESS_SUBMITTED")
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def build_register_wc_voter(votes: list[dict] | None = None) -> str:
    """Register explicit votes; each entry supplies hash, option, target, votes.

    Votes are a maximum, limited by available favor. Player targets are IDs;
    other targets are the query's zero-based index or a raw string value.
    Missing resolutions or unmatched targets stop the handler before any vote.
    """
    import json

    if not isinstance(votes, list) or not votes:
        raise ValueError("World Congress votes must be a non-empty list of explicit preferences.")
    entries = []
    hashes = set()
    for vote in votes:
        if not isinstance(vote, dict) or not all(key in vote for key in ("hash", "option", "target", "votes")):
            raise ValueError("Each vote requires hash, option, target and votes.")
        h, o, t, n = (vote[key] for key in ("hash", "option", "target", "votes"))
        if type(h) is not int or type(o) is not int or o not in (1, 2) or type(n) is not int or n < 1:
            raise ValueError("hash must be an integer, option 1 or 2, and votes a positive integer.")
        if not (type(t) is int and t >= 0) and not (isinstance(t, str) and t and not any(ord(c) < 32 for c in t)):
            raise ValueError("target must be a nonnegative integer or a non-empty raw string.")
        if h in hashes:
            raise ValueError("Each resolution hash must appear only once.")
        hashes.add(h)
        entries.append(f'["{h}"] = {{o={o}, t={json.dumps(t, ensure_ascii=False)}, v={n}}}')
    prefs_lua = "{" + ", ".join(entries) + "}"

    return f"""
if __civmcp_wc_handler then
    pcall(function() Events.WorldCongressStage1.Remove(__civmcp_wc_handler) end)
end
__civmcp_wc_votes = {prefs_lua}

local function handler()
    local me = Game.GetLocalPlayer()
    local wc = Game.GetWorldCongress()
    if not wc or not wc:IsInSession() then return end
    local function stop(message)
        print("ERR:WC_VOTE_PREFERENCES|" .. message)
        __civmcp_wc_votes = nil
        pcall(function() Events.WorldCongressStage1.Remove(__civmcp_wc_handler) end)
        __civmcp_wc_handler = nil
    end
    local ress = wc:GetResolutions()
    if not ress then stop("Resolution status unavailable"); return end
    if #ress == 0 then
        UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_SUBMIT_TURN, {{}})
        __civmcp_wc_votes = nil
        pcall(function() Events.WorldCongressStage1.Remove(__civmcp_wc_handler) end)
        __civmcp_wc_handler = nil
        print("OK:WC_VOTES_SUBMITTED")
        return
    end

    -- Validate every preference and target before spending any favor.
    local plan = {{}}
    for _, res in ipairs(ress) do
        local pref = __civmcp_wc_votes[tostring(res.Type)]
        if not pref then stop("Missing explicit vote for " .. tostring(res.Type)); return end
        local targetIdx = nil
        local targets = res.PossibleTargets
        if not targets or #targets == 0 then
            if res.TargetType ~= "PlayerType" and pref.t == 0 then targetIdx = 0 end
        elseif res.TargetType == "PlayerType" then
            local playerTarget = tonumber(pref.t)
            if playerTarget then
                for ti, tgt in ipairs(targets) do
                    if tonumber(tgt) == playerTarget then targetIdx = ti - 1; break end
                end
            end
        elseif type(pref.t) == "number" then
            if pref.t >= 0 and pref.t < #targets then targetIdx = pref.t end
        else
            for ti, tgt in ipairs(targets) do
                if tostring(tgt) == pref.t then targetIdx = ti - 1; break end
            end
        end
        if targetIdx == nil then stop("Target not found for " .. tostring(res.Type)); return end
        table.insert(plan, {{hash=res.Type, pref=pref, target=targetIdx}})
    end

    local favor = Players[me]:GetFavor()
    local costs = wc:GetVotesandFavorCost()
    local maxV = costs.MaxVotes or 5
    for _, item in ipairs(plan) do
        local count, cost = 1, 0
        -- costs[v-1] is the cumulative favor cost for v total votes.
        for v = 2, math.min(item.pref.v, maxV) do
            local totalCost = costs[v - 1]
            if totalCost == nil or totalCost > favor then break end
            count, cost = v, totalCost
        end
        favor = favor - cost
        local params = {{}}
        params[PlayerOperations.PARAM_RESOLUTION_TYPE] = item.hash
        params[PlayerOperations.PARAM_WORLD_CONGRESS_VOTES] = count
        params[PlayerOperations.PARAM_RESOLUTION_OPTION] = item.pref.o
        params[PlayerOperations.PARAM_RESOLUTION_SELECTION] = item.target
        UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_RESOLUTION_VOTE, params)
    end
    UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_SUBMIT_TURN, {{}})
    __civmcp_wc_votes = nil
    pcall(function() Events.WorldCongressStage1.Remove(__civmcp_wc_handler) end)
    __civmcp_wc_handler = nil
    print("OK:WC_VOTES_SUBMITTED")
end
__civmcp_wc_handler = handler
Events.WorldCongressStage1.Add(handler)
-- A turn already paused inside a session cannot replay ACTION_ENDTURN.
-- Submit the explicit choices now; otherwise keep the one-shot event.
local current = Game.GetWorldCongress()
if current and current:IsInSession() then handler()
else print("OK:WC_VOTER_REGISTERED") end
print("{SENTINEL}")
"""
