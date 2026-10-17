"""World Congress domain — Lua builders and parsers."""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL, _bail
from civ_mcp.lua.models import CongressProposal, CongressResolution, WorldCongressStatus


def build_congress_phase_query(*, sentinel: bool = True) -> str:
    """Read native segment and this context's request record; never vote/resume."""
    ending = f'print("{SENTINEL}")' if sentinel else ""
    return f"""
local phaseMe = Game.GetLocalPlayer()
local phaseWC = Game.GetWorldCongress()
local phaseSession = phaseWC and phaseWC:IsInSession() or false
local phaseSegment = Game.GetCurrentTurnSegment()
local phaseStage = "NONE"
if phaseSegment == DB.MakeHash("TURNSEG_WORLDCONGRESS_1") then phaseStage = "STAGE1"
elseif phaseSegment == DB.MakeHash("TURNSEG_WORLDCONGRESS_2") then phaseStage = "STAGE2"
elseif phaseSegment == DB.MakeHash("TURNSEG_WORLDCONGRESS_RESOLUTION") then phaseStage = "RESOLUTION"
elseif phaseSession then phaseStage = "UNKNOWN" end
local phaseReceipt = "NONE"
local phaseQ = __civmcp_wc_request
if phaseQ then
    local cfg = PlayerConfigurations[phaseMe]
    local same = phaseQ.owner == phaseMe and phaseQ.civ == cfg:GetCivilizationTypeName()
        and phaseQ.seed == GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
        and phaseQ.turn == Game.GetCurrentGameTurn()
    if same and not phaseSession and phaseStage == "NONE" and phaseQ.state == "CONTINUE_REQUESTED" then
        phaseReceipt = "FINISHED"
    elseif same and (phaseQ.stage == phaseStage or phaseQ.state == "REGISTERED") then
        phaseReceipt = phaseQ.state
    elseif phaseSession then phaseReceipt = "STALE" end
end
print("WC_PHASE|" .. tostring(phaseSession) .. "|" .. phaseStage .. "|"
    .. Game.GetCurrentGameTurn() .. "|" .. phaseReceipt .. "|" .. tostring(phaseSegment))
{ending}
"""


def parse_congress_phase(lines: list[str]) -> tuple[bool, str, int, str]:
    rows = [line.split("|") for line in lines if line.startswith("WC_PHASE|")]
    if len(rows) != 1 or len(rows[0]) not in (5, 6):
        raise ValueError("World Congress native phase is unknown")
    _, session, stage, turn, receipt, *_ = rows[0]
    if (session not in ("true", "false") or stage not in ("NONE", "STAGE1", "STAGE2", "RESOLUTION", "UNKNOWN")
            or receipt not in ("NONE", "REGISTERED", "VOTES_REQUESTED", "SUBMIT_REQUESTED", "CONTINUE_REQUESTED", "FINISHED", "FAILED", "STALE")):
        raise ValueError("World Congress native phase is malformed")
    return session == "true", stage, int(turn), receipt


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
local costs = wc:GetVotesandFavorCost(me)
local maxVotes = costs.MaxVotes or 5
local costStr = ""
for i = 0, maxVotes do
    if i > 0 then costStr = costStr .. "," end
    costStr = costStr .. tostring(costs[i] or 0)
end
print("WC_STATUS|" .. tostring(inSession) .. "|" .. turnsLeft .. "|" .. favor .. "|" .. maxVotes .. "|" .. costStr)
local ress = wc:GetResolutions(me)
if ress and (not inSession or Game.GetCurrentTurnSegment() == DB.MakeHash("TURNSEG_WORLDCONGRESS_1")) then
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
    local proposalStatus = "UNKNOWN"
    pcall(function()
        local container = wc:GetProposals(me)
        if type(container) ~= "table" then return end
        local categories = container.Proposals
        if categories ~= nil and type(categories) ~= "table" then return end
        proposalStatus = "EMPTY"
        for _, category in pairs(categories or {{}}) do
            if type(category) ~= "table" or type(category.ProposalsOfType) ~= "table" then
                proposalStatus = "UNKNOWN"; return
            end
            if next(category.ProposalsOfType) ~= nil then proposalStatus = "CHOICES" end
        end
    end)
    print("WC_PROPOSAL_STATUS|" .. proposalStatus)
end
{build_congress_phase_query(sentinel=False)}
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
    if any(line.startswith("WC_PHASE|") for line in lines):
        _, status.native_stage, _, status.local_request = parse_congress_phase(lines)
    for line in lines:
        if line.startswith("WC_PROPOSAL_STATUS|"):
            status.proposal_status = line.split("|", 1)[1]
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
    An empty list is explicit consent only for a native stage with no choices.
    """
    import json
    import uuid

    if not isinstance(votes, list):
        raise ValueError("World Congress votes must be a list of explicit preferences.")
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

    token = uuid.uuid4().hex
    return f"""
local me = Game.GetLocalPlayer()
local cfg = PlayerConfigurations[me]
local civ = cfg:GetCivilizationTypeName()
local seed = GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
local turn = Game.GetCurrentGameTurn()
local function stageNow()
    local s = Game.GetCurrentTurnSegment()
    if s == DB.MakeHash("TURNSEG_WORLDCONGRESS_1") then return "STAGE1" end
    if s == DB.MakeHash("TURNSEG_WORLDCONGRESS_2") then return "STAGE2" end
    if s == DB.MakeHash("TURNSEG_WORLDCONGRESS_RESOLUTION") then return "RESOLUTION" end
    return "NONE"
end
local current = Game.GetWorldCongress()
local stage = stageNow()
local prior = __civmcp_wc_request
if not prior and __civmcp_wc_handler then
    {_bail("ERR:OUTCOME_UNKNOWN|Untracked prior congress handler; no votes submitted.")}
end
if prior then
    local same = prior.owner == me and prior.civ == civ and prior.seed == seed and prior.turn == turn
    if same and prior.state ~= "FAILED" and (prior.stage == stage or prior.state == "REGISTERED") then
        local tag = (prior.state == "REGISTERED" or prior.state == "CONTINUE_REQUESTED") and "OK:WC_REQUEST|" or "ERR:OUTCOME_UNKNOWN|WC_REQUEST|"
        print(tag .. prior.state .. "|stage:" .. prior.stage .. "|token:" .. prior.token)
        print("{SENTINEL}"); return
    end
    -- A known pre-validation failure sent no vote. A continuation can
    -- retire only at a new phase/turn. A partial vote/submission is never replayed.
    if prior.state ~= "CONTINUE_REQUESTED" and prior.state ~= "FAILED" then
        {_bail("ERR:OUTCOME_UNKNOWN|Stale or partial World Congress request; no votes or continuation resent.")}
    end
end
if current and current:IsInSession() and stage ~= "STAGE1" and stage ~= "STAGE2" then
    {_bail("ERR:WC_PHASE_UNKNOWN|No verified local voting stage; no votes submitted.")}
end
if GameConfiguration.IsPaused() then {_bail("ERR:WC_PAUSED|Unpause before voting; no votes submitted.")} end
local q = {{token="{token}", owner=me, civ=civ, seed=seed, turn=turn,
    stage=stage, state="REGISTERED", votes={prefs_lua}}}
local function cleanup()
    if q.handler then
        pcall(function() Events.WorldCongressStage1.Remove(q.handler) end)
        pcall(function() Events.WorldCongressStage2.Remove(q.handler) end)
    end
    if __civmcp_wc_handler == q.handler then __civmcp_wc_handler = nil end
end
local function stop(message)
    q.state = "FAILED"
    cleanup()
    print("ERR:WC_VOTE_PREFERENCES|" .. message)
end
local function handler(eventPlayer)
    if __civmcp_wc_request ~= q or q.state ~= "REGISTERED" then return end
    if eventPlayer ~= nil and eventPlayer ~= me then return end
    local wc = Game.GetWorldCongress()
    if not wc or not wc:IsInSession() then return end
    local nowCfg = PlayerConfigurations[Game.GetLocalPlayer()]
    if Game.GetLocalPlayer() ~= q.owner or nowCfg:GetCivilizationTypeName() ~= q.civ
        or GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED") ~= q.seed
        or Game.GetCurrentGameTurn() ~= q.turn then stop("Request identity/turn changed"); return end
    local nowStage = stageNow()
    if nowStage ~= "STAGE1" and nowStage ~= "STAGE2" then stop("Voting stage unavailable"); return end
    if q.stage ~= "NONE" and q.stage ~= nowStage then stop("Request stage changed"); return end
    q.stage = nowStage
    -- Unknown proposals cannot be accepted/abstained by a resolution-only plan.
    local props = wc:GetProposals(me)
    if type(props) ~= "table" then stop("Proposal status unavailable"); return end
    local categories = props.Proposals
    if categories ~= nil and type(categories) ~= "table" then stop("Malformed proposal categories"); return end
    for _, category in pairs(categories or {{}}) do
        if type(category) ~= "table" or type(category.ProposalsOfType) ~= "table" then
            stop("Malformed proposal choices"); return
        end
        if next(category.ProposalsOfType) ~= nil then
            stop("Explicit proposal votes are required; no defaults supplied"); return
        end
    end
    -- Native Stage2 only handles proposals; its old resolution list is not a
    -- fresh voting plan and must never cause a repeat of Stage1 votes.
    local ress = {{}}
    if nowStage == "STAGE1" then ress = wc:GetResolutions(me) end
    if type(ress) ~= "table" then stop("Resolution status unavailable"); return end
    local plan, seen = {{}}, {{}}
    -- Validate every preference and target before spending any favor.
    for key, res in pairs(ress) do
      if key ~= "Stage" then
        if type(res) ~= "table" or type(res.Type) ~= "number" then stop("Malformed resolution"); return end
        local pref = q.votes[tostring(res.Type)]
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
        seen[tostring(res.Type)] = true
        table.insert(plan, {{hash=res.Type, pref=pref, target=targetIdx}})
      end
    end
    for hash, _ in pairs(q.votes) do
        if not seen[hash] then stop("Preference does not match this native stage"); return end
    end
    if #plan > 0 then
    local favor = Players[me]:GetFavor()
    local costs = wc:GetVotesandFavorCost(me)
    if type(favor) ~= "number" or type(costs) ~= "table" or type(costs.MaxVotes) ~= "number" then
        stop("Favor/vote costs unavailable"); return
    end
    local maxV = costs.MaxVotes
    for _, item in ipairs(plan) do
        local count, cost = 1, 0
        for v = 2, math.min(item.pref.v, maxV) do
            local totalCost = costs[v - 1]
            if type(totalCost) ~= "number" or totalCost > favor then break end
            count, cost = v, totalCost
        end
        favor = favor - cost
        item.count = count
    end
    end
    -- Latch before every possibly sent stage, even if Lua/transport fails.
    -- Re-entering this handler or queue tool can only report this record.
    q.state = "VOTES_REQUESTED"
    cleanup()
    for _, item in ipairs(plan) do
        local params = {{}}
        params[PlayerOperations.PARAM_RESOLUTION_TYPE] = item.hash
        params[PlayerOperations.PARAM_WORLD_CONGRESS_VOTES] = item.count
        params[PlayerOperations.PARAM_RESOLUTION_OPTION] = item.pref.o
        params[PlayerOperations.PARAM_RESOLUTION_SELECTION] = item.target
        UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_RESOLUTION_VOTE, params)
    end
    q.state = "SUBMIT_REQUESTED"
    UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_SUBMIT_TURN, {{}})
    q.state = "CONTINUE_REQUESTED"
    UI.RequestAction(ActionTypes.ACTION_ENDTURN)
    -- Native close routes dequeue/input cleanup; never raw SetHide, OnAccept,
    -- or ResumeCongress, any of which could duplicate voting/skip owned locks.
    pcall(function()
        local popup = ContextPtr:LookUpControl("/InGame/WorldCongressPopup")
        if popup and popup:IsVisible() then LuaEvents.DiplomacyActionView_HideCongress() end
    end)
    pcall(function()
        local intro = ContextPtr:LookUpControl("/InGame/WorldCongressIntro")
        if intro and intro:IsVisible() then UIManager:DequeuePopup(intro) end
    end)
    pcall(function() LuaEvents.WorldCongressPopup_HideWorldCongressBetweenTurns() end)
    print("OK:WC_STAGE_CONTINUE_REQUESTED|stage:" .. q.stage .. "|token:" .. q.token)
end
q.handler = function(eventPlayer)
    local ok, err = pcall(handler, eventPlayer)
    if not ok then
        if q.state == "REGISTERED" then q.state = "FAILED"; cleanup() end
        print("ERR:OUTCOME_UNKNOWN|WC_REQUEST|" .. q.state .. "|" .. tostring(err))
    end
end
__civmcp_wc_request = q
__civmcp_wc_handler = q.handler
local armed = pcall(function()
    Events.WorldCongressStage1.Add(q.handler)
    Events.WorldCongressStage2.Add(q.handler)
end)
if not armed then stop("Native congress handler unavailable")
elseif current and current:IsInSession() then q.handler()
else print("OK:WC_VOTER_REGISTERED") end
print("{SENTINEL}")
"""
