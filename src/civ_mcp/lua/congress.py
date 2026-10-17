"""World Congress domain — Lua builders and parsers."""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL, _bail
from civ_mcp.lua.models import CongressProposal, CongressResolution, WorldCongressStatus


def _proposal_eligibility_lua() -> str:
    """Read-only native ballot facts shared by query, opening and submission."""
    return """
local function integer(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge and value == math.floor(value)
end
local function voteCosts(costs)
    if type(costs) ~= "table" or not integer(costs.MaxVotes) or costs.MaxVotes < 1 then return nil end
    local previous = 0
    for i = 0, costs.MaxVotes - 1 do
        local value = costs[i]
        if not integer(value) or value < previous then return nil end
        previous = value
    end
    return costs
end
local function nativeVoteCosts(wc, player)
    local ok, costs = pcall(function() return wc:GetVotesandFavorCost(player) end)
    return ok and voteCosts(costs) or nil
end
local function hasEligibleProposals(proposals)
    if type(proposals) ~= "table" or (proposals.Proposals ~= nil and type(proposals.Proposals) ~= "table") then
        return nil, "Proposal status unknown"
    end
    local rows, keys, nativePairs, hasChoices = {}, {}, {}, false
    local wc = Game.GetWorldCongress()
    for proposalType, category in pairs(proposals.Proposals or {}) do
        if not GameInfo.ProposalTypes[proposalType] or type(category) ~= "table" or type(category.ProposalsOfType) ~= "table" then
            return nil, "Proposal category unknown"
        end
        for _, proposal in pairs(category.ProposalsOfType) do
            if type(proposal) ~= "table" or (proposal.Disabled ~= nil and type(proposal.Disabled) ~= "boolean") then
                return nil, "Proposal eligibility unknown"
            end
            if type(proposal.TypeName) ~= "string" or proposal.TypeName == "" or proposal.TypeName:find("[|%c]")
                or (proposal.Type ~= nil and not integer(proposal.Type)) or not integer(proposal.Target)
                or (proposal.Sender ~= nil and not integer(proposal.Sender))
                or type(proposal.Name) ~= "string" or type(proposal.Description) ~= "string" then
                return nil, "Proposal identity or description unknown"
            end
            local key = proposal.TypeName .. "_" .. proposal.Target .. "_" .. (proposal.Sender ~= nil and proposal.Sender or "NONE")
            if keys[key] then return nil, "Duplicate proposal key" end
            keys[key] = true
            local blocker, proposalBlocker = nil, nil
            if proposal.VotingRestriction then
                blocker = GameInfo.VotingBlockers[proposal.VotingRestriction]
                if not blocker or type(blocker.NoUpvote) ~= "boolean" or type(blocker.NoDownvote) ~= "boolean" then
                    return nil, "Proposal voting restriction unknown"
                end
            end
            if proposal.ProposalBlockerType then
                proposalBlocker = GameInfo.ProposalBlockers and GameInfo.ProposalBlockers[proposal.ProposalBlockerType]
                if not proposalBlocker then return nil, "Proposal blocker unknown" end
            end
            local costs = category.FavorCost
            if costs == nil then costs = nativeVoteCosts(wc, Game.GetLocalPlayer()) end
            costs = voteCosts(costs)
            if not costs then return nil, "Proposal category costs unknown" end
            local up = not proposal.Disabled and not (blocker and blocker.NoUpvote)
            local down = not proposal.Disabled and not (blocker and blocker.NoDownvote)
            local eligible = up or down
            if eligible then
                -- DISCUSSION_VOTE has only Sender/Target, no discussion-type parameter.
                -- Distinct keys mapping to the same native arguments are not distinguishable.
                local pair = tostring(proposal.Sender) .. ":" .. proposal.Target
                if nativePairs[pair] then return nil, "Ambiguous native proposal identity" end
                nativePairs[pair] = true
                hasChoices = true
            end
            local definition = GameInfo.ProposalTypes[proposalType]
            local categoryName = definition.Name and Locale.Lookup(definition.Name) or definition.ProposalType or tostring(proposalType)
            rows[#rows+1] = {key=key, category=tostring(proposalType), category_name=categoryName, data=proposal, costs=costs,
                eligible=eligible, up=up, down=down,
                reason=proposalBlocker and proposalBlocker.Description or (blocker and blocker.Description)
                    or (proposal.Disabled and "Disabled" or (not eligible and "Both vote directions blocked" or ""))}
        end
    end
    table.sort(rows, function(a, b) return a.key < b.key end)
    return hasChoices, nil, rows
end
"""


def _native_stage_lua() -> str:
    """Share segment interpretation; each operation retains its session policy."""
    return """
local function congressStage(segment, inSession)
    if segment == DB.MakeHash("TURNSEG_WORLDCONGRESS_1") then return "STAGE1" end
    if segment == DB.MakeHash("TURNSEG_WORLDCONGRESS_2") then return "STAGE2" end
    if segment == DB.MakeHash("TURNSEG_WORLDCONGRESS_RESOLUTION") then return "RESOLUTION" end
    return inSession and "UNKNOWN" or "NONE"
end
"""


def build_congress_phase_query(*, sentinel: bool = True) -> str:
    """Read native segment and this context's request record; never vote/resume."""
    return _native_stage_lua() + _congress_phase_rows_lua(sentinel=sentinel)


def _congress_phase_rows_lua(*, sentinel: bool) -> str:
    ending = f'print("{SENTINEL}")' if sentinel else ""
    return f"""
local phaseMe = Game.GetLocalPlayer()
local phaseWC = Game.GetWorldCongress()
local phaseSession = phaseWC and phaseWC:IsInSession() or false
local phaseSegment = Game.GetCurrentTurnSegment()
local phaseStage = congressStage(phaseSegment, phaseSession)
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
if phaseQ and phaseQ.summary and phaseReceipt ~= "NONE" and phaseReceipt ~= "STALE" then
    print("WC_ALLOCATION|" .. phaseQ.summary)
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


def build_open_world_congress() -> str:
    """Explicit Intro acceptance, only when native voting choices cannot auto-submit."""
    import uuid

    token = uuid.uuid4().hex
    return f"""
local me = Game.GetLocalPlayer()
local cfg = PlayerConfigurations[me]
local civ = cfg:GetCivilizationTypeName()
local seed = GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
local turn = Game.GetCurrentGameTurn()
local segment = Game.GetCurrentTurnSegment()
local wc = Game.GetWorldCongress()
{_native_stage_lua()}
local stage = congressStage(segment, wc and wc:IsInSession())
if not wc or wc:IsInSession() ~= true or (stage ~= "STAGE1" and stage ~= "STAGE2") then
    {_bail("ERR:WC_OPEN_UNAVAILABLE|No verified native voting stage; no start event sent.")}
end
local submitted = __civmcp_wc_request
if submitted then
    local same = submitted.owner == me and submitted.civ == civ and submitted.seed == seed
        and submitted.turn == turn and submitted.stage == stage
    if (same and submitted.state ~= "REGISTERED" and submitted.state ~= "FAILED")
        or (not same and submitted.state ~= "CONTINUE_REQUESTED" and submitted.state ~= "FAILED") then
        {_bail("ERR:OUTCOME_UNKNOWN|Congress request already submitted, continued, or stale; no start event sent.")}
    end
end
local intro = ContextPtr:LookUpControl("/InGame/WorldCongressIntro")
local popup = ContextPtr:LookUpControl("/InGame/WorldCongressPopup")
if not intro or not popup then
    {_bail("ERR:OUTCOME_UNKNOWN|Congress UI context missing; no start event sent.")}
end
local prior = __civmcp_wc_open
local sameOpen = prior and prior.owner == me and prior.civ == civ and prior.seed == seed
    and prior.turn == turn and prior.stage == stage
if prior and not sameOpen and prior.state ~= "OPENED" then
    {_bail("ERR:OUTCOME_UNKNOWN|Previous congress start remains unknown; no start event resent.")}
end
if popup:IsVisible() and not intro:IsVisible() then
    if sameOpen then prior.state = "OPENED" end
    print("OK:WC_VOTING_ALREADY_OPEN|stage:" .. stage .. "|turn:" .. turn)
    print("{SENTINEL}"); return
end
if sameOpen then
    {_bail("ERR:OUTCOME_UNKNOWN|Congress start already requested but voting UI is not confirmed; no start event resent.")}
end
if not intro:IsVisible() or popup:IsVisible() then
    {_bail("ERR:OUTCOME_UNKNOWN|Congress Intro visibility is ambiguous; no start event sent.")}
end
-- Native HasChoices tests allocated resolution/proposal widgets. Reproduce
-- only its proven source-data prerequisites; never call the start route for
-- empty/unknown choices because SetupWorldCongress would auto-submit/resume.
local hasChoices = false
if stage == "STAGE1" then
    local resolutions = wc:GetResolutions(me)
    if type(resolutions) ~= "table" then {_bail("ERR:WC_CHOICES_UNKNOWN|Resolution status unknown; no start event sent.")} end
    for key, resolution in pairs(resolutions) do
        if type(key) == "number" then
            if type(resolution) ~= "table" or not GameInfo.Resolutions[resolution.Type] then
                {_bail("ERR:WC_CHOICES_UNKNOWN|Resolution choice unknown; no start event sent.")}
            end
            hasChoices = true
        end
    end
end
{_proposal_eligibility_lua()}
local eligibleProposals, proposalError = hasEligibleProposals(wc:GetProposals(me))
if eligibleProposals == nil then
    print("ERR:WC_CHOICES_UNKNOWN|" .. proposalError .. "; no start event sent.")
    print("{SENTINEL}"); return
end
hasChoices = hasChoices or eligibleProposals
if not hasChoices then
    {_bail("ERR:WC_NO_CHOICES|No native voting choices; use an explicit empty vote plan instead of starting the Intro.")}
end
-- Bind before the only start event. A lost response can only inspect this
-- record and current visibility, never replay the native callback.
local q = {{token="{token}", owner=me, civ=civ, seed=seed, turn=turn, stage=stage, state="REQUESTED"}}
__civmcp_wc_open = q
if Game.GetLocalPlayer() ~= me or PlayerConfigurations[me]:GetCivilizationTypeName() ~= civ
    or GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED") ~= seed or Game.GetCurrentGameTurn() ~= turn
    or Game.GetCurrentTurnSegment() ~= segment then
    {_bail("ERR:OUTCOME_UNKNOWN|Congress scope changed; no start event sent.")}
end
UIManager:DequeuePopup(intro)
LuaEvents.WorldCongressIntro_ShowWorldCongress()
if popup:IsVisible() and not intro:IsVisible() and Game.GetLocalPlayer() == me
    and PlayerConfigurations[me]:GetCivilizationTypeName() == civ
    and GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED") == seed
    and Game.GetCurrentGameTurn() == turn and Game.GetCurrentTurnSegment() == segment then
    q.state = "OPENED"
    print("OK:WC_VOTING_OPEN|stage:" .. stage .. "|turn:" .. turn)
else
    print("ERR:OUTCOME_UNKNOWN|Congress start requested; voting UI not confirmed. Inspect; do not resend.")
end
print("{SENTINEL}")
"""


def build_world_congress_query() -> str:
    """Get World Congress status, resolutions, and proposals (InGame context)."""
    return f"""
{_proposal_eligibility_lua()}
local me = Game.GetLocalPlayer()
local pDiplo = Players[me]:GetDiplomacy()
{_native_stage_lua()}
local wc = Game.GetWorldCongress()
if not wc then {_bail("ERR:NO_WORLD_CONGRESS|World Congress not available yet")} end
local inSession = wc:IsInSession()
local meeting = wc:GetMeetingStatus()
local turnsLeft = meeting and meeting.TurnsLeft or -1
local favor = Players[me]:GetFavor()
local costs = nativeVoteCosts(wc, me)
local maxVotes = costs and costs.MaxVotes or 0
local costStr = ""
for i = 0, maxVotes - 1 do
    if i > 0 then costStr = costStr .. "," end
    costStr = costStr .. tostring(costs[i])
end
print("WC_COST_STATUS|" .. (costs and "KNOWN" or "UNKNOWN"))
print("WC_STATUS|" .. tostring(inSession) .. "|" .. turnsLeft .. "|" .. favor .. "|" .. maxVotes .. "|" .. costStr)
local ress = wc:GetResolutions(me)
if ress and (not inSession or congressStage(Game.GetCurrentTurnSegment(), inSession) == "STAGE1") then
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
    local eligible, proposalError, proposals
    local ok = pcall(function() eligible, proposalError, proposals = hasEligibleProposals(wc:GetProposals(me)) end)
    local status = ok and eligible ~= nil and (eligible and "CHOICES" or "EMPTY") or "UNKNOWN"
    print("WC_PROPOSAL_STATUS|" .. status)
    local function clean(value) return tostring(value or ""):gsub("[|%c]", " ") end
    print("WC_PROPOSAL_ERROR|" .. clean(proposalError or (not ok and "Native proposal read failed" or "")))
    local function playerName(player)
        if player == nil then return "NONE" end
        local cfg = PlayerConfigurations[player]
        if not cfg then return "Generic target " .. tostring(player) end
        if player ~= me and not pDiplo:HasMet(player) then return "Unmet player" end
        return Locale.Lookup(cfg:GetCivilizationShortDescription())
    end
    for _, row in ipairs(proposals or {{}}) do
        local p = row.data
        local costs = {{}}
        for i = 0, row.costs.MaxVotes-1 do costs[#costs+1]=tostring(row.costs[i]) end
        local targetName = playerName(p.Target)
        local displayName = Locale.Lookup(p.Name, targetName)
        local description = Locale.Lookup(p.Description, targetName)
        print("WC_PROP|" .. row.key .. "|" .. clean(row.category) .. "|" .. p.TypeName .. "|" .. (p.Type ~= nil and p.Type or "NONE")
            .. "|" .. (p.Sender ~= nil and p.Sender or "NONE") .. "|" .. clean(playerName(p.Sender))
            .. "|" .. p.Target .. "|" .. clean(targetName) .. "|" .. clean(displayName) .. "|" .. clean(description)
            .. "|" .. clean(p.EmergencyType) .. "|" .. (p.Disabled and "1" or "0")
            .. "|" .. (row.eligible and "1" or "0") .. "|" .. (row.up and "1" or "0") .. "|" .. (row.down and "1" or "0")
            .. "|" .. clean(p.VotingRestriction) .. "|" .. clean(Locale.Lookup(row.reason))
            .. "|" .. row.costs.MaxVotes .. "|" .. table.concat(costs, ",") .. "|" .. clean(row.category_name)
            .. "|" .. clean(p.ProposalBlockerType))
    end
end
{_congress_phase_rows_lua(sentinel=False)}
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
        elif line.startswith("WC_PROPOSAL_ERROR|"):
            status.proposal_error = line.split("|", 1)[1]
        elif line.startswith("WC_COST_STATUS|"):
            status.vote_cost_status = line.split("|", 1)[1]
        elif line.startswith("WC_ALLOCATION|"):
            status.vote_allocation = line.split("|", 1)[1]
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
            if len(parts) != 22:
                status.proposal_status = "UNKNOWN"
                status.proposal_error = "Malformed proposal row"
                continue
            status.proposals.append(
                CongressProposal(
                    proposal_key=parts[1], category=parts[2], type_name=parts[3],
                    proposal_type=None if parts[4] == "NONE" else int(parts[4]), sender_id=None if parts[5] == "NONE" else int(parts[5]),
                    sender_name=parts[6], target_id=int(parts[7]), target_name=parts[8],
                    name=parts[9], description=parts[10], emergency_type=parts[11], disabled=parts[12]=="1",
                    eligible=parts[13]=="1", can_upvote=parts[14]=="1", can_downvote=parts[15]=="1",
                    voting_restriction=parts[16], restriction_reason=parts[17], max_votes=int(parts[18]),
                    favor_costs=[int(value) for value in parts[19].split(",")],
                    category_name=parts[20], proposal_blocker_type=parts[21],
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
    """Register an ordered, complete native-stage resolution/proposal plan.

    Resolution entries retain hash/option/target/votes. Proposal entries use
    proposal_key/option/votes, with 1=up and 2=down. Votes are maxima; reserve
    all first-vote costs, then allocate extras in input order under one budget.
    An empty plan consents only to a verified native stage with no choices.
    """
    import json
    import uuid

    if not isinstance(votes, list):
        raise ValueError("World Congress votes must be a list of explicit preferences.")
    entries = []
    seen = set()
    for vote in votes:
        if not isinstance(vote, dict):
            raise ValueError("Each vote must be an object.")
        is_proposal = "proposal_key" in vote
        fields = {"proposal_key", "option", "votes"} if is_proposal else {"hash", "option", "target", "votes"}
        if set(vote) != fields:
            raise ValueError("Use exactly hash/option/target/votes or proposal_key/option/votes.")
        o, n = vote["option"], vote["votes"]
        if type(o) is not int or o not in (1, 2) or type(n) is not int or n < 1:
            raise ValueError("option must be 1 or 2 and votes a positive integer.")
        if is_proposal:
            key = vote["proposal_key"]
            if not isinstance(key, str) or not key or any(ord(c) < 32 or c == "|" for c in key):
                raise ValueError("proposal_key must be the exact nonempty native key from the query.")
            kind, details = "proposal", ""
        else:
            h, t = vote["hash"], vote["target"]
            if type(h) is not int:
                raise ValueError("hash must be an integer.")
            if not (type(t) is int and t >= 0) and not (isinstance(t, str) and t and not any(ord(c) < 32 for c in t)):
                raise ValueError("target must be a nonnegative integer or a non-empty raw string.")
            key, kind = str(h), "resolution"
            details = f", hash={h}, t={json.dumps(t, ensure_ascii=False)}"
        if (kind, key) in seen:
            raise ValueError("Each resolution hash or proposal key must appear only once.")
        seen.add((kind, key))
        entries.append(f'{{kind="{kind}", key={json.dumps(key, ensure_ascii=False)}, o={o}, v={n}{details}}}')
    prefs_lua = "{" + ", ".join(entries) + "}"

    token = uuid.uuid4().hex
    return f"""
local me = Game.GetLocalPlayer()
local cfg = PlayerConfigurations[me]
local civ = cfg:GetCivilizationTypeName()
local seed = GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
local turn = Game.GetCurrentGameTurn()
{_native_stage_lua()}
local current = Game.GetWorldCongress()
local stage = congressStage(Game.GetCurrentTurnSegment(), current and current:IsInSession())
local prior = __civmcp_wc_request
if not prior and __civmcp_wc_handler then
    {_bail("ERR:OUTCOME_UNKNOWN|Untracked prior congress handler; no votes submitted.")}
end
if prior then
    local same = prior.owner == me and prior.civ == civ and prior.seed == seed and prior.turn == turn
    if same and prior.state ~= "FAILED" and (prior.stage == stage or prior.state == "REGISTERED") then
        local tag = (prior.state == "REGISTERED" or prior.state == "CONTINUE_REQUESTED") and "OK:WC_REQUEST|" or "ERR:OUTCOME_UNKNOWN|WC_REQUEST|"
        print(tag .. prior.state .. "|stage:" .. prior.stage .. "|token:" .. prior.token .. (prior.summary and "|allocation:" .. prior.summary or ""))
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
{_proposal_eligibility_lua()}
local function handler(eventPlayer)
    if __civmcp_wc_request ~= q or q.state ~= "REGISTERED" then return end
    if eventPlayer ~= nil and eventPlayer ~= me then return end
    local wc = Game.GetWorldCongress()
    if not wc or not wc:IsInSession() then return end
    local nowCfg = PlayerConfigurations[Game.GetLocalPlayer()]
    if Game.GetLocalPlayer() ~= q.owner or nowCfg:GetCivilizationTypeName() ~= q.civ
        or GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED") ~= q.seed
        or Game.GetCurrentGameTurn() ~= q.turn then stop("Request identity/turn changed"); return end
    local nowStage = congressStage(Game.GetCurrentTurnSegment(), wc:IsInSession())
    if nowStage ~= "STAGE1" and nowStage ~= "STAGE2" then stop("Voting stage unavailable"); return end
    if q.stage ~= "NONE" and q.stage ~= nowStage then stop("Request stage changed"); return end
    q.stage = nowStage
    local eligibleProposals, proposalError, proposals = hasEligibleProposals(wc:GetProposals(me))
    if eligibleProposals == nil then stop(proposalError); return end
    -- Stage2 does not include the old Stage1 resolution list.
    local ress = {{}}
    if nowStage == "STAGE1" then ress = wc:GetResolutions(me) end
    if type(ress) ~= "table" then stop("Resolution status unavailable"); return end
    local resByHash, propByKey = {{}}, {{}}
    for key, res in pairs(ress) do
        if key ~= "Stage" then
            if type(res) ~= "table" or not integer(res.Type) or not GameInfo.Resolutions[res.Type] then
                stop("Malformed or unknown resolution"); return
            end
            local hash = tostring(res.Type)
            if resByHash[hash] then stop("Duplicate native resolution"); return end
            resByHash[hash] = res
        end
    end
    for _, proposal in ipairs(proposals) do propByKey[proposal.key] = proposal end
    local plan, seenRes, seenProp = {{}}, {{}}, {{}}
    local generalCosts = nil
    for _, pref in ipairs(q.votes) do
        if pref.kind == "resolution" then
            local res = resByHash[pref.key]
            if not res then stop("Preference does not match this native stage"); return end
            local targetIdx = nil
            local targets = res.PossibleTargets
            if targets ~= nil and type(targets) ~= "table" then stop("Resolution targets unavailable"); return end
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
            if targetIdx == nil then stop("Target not found for " .. pref.key); return end
            generalCosts = generalCosts or nativeVoteCosts(wc, me)
            if not generalCosts then stop("Resolution vote costs unavailable"); return end
            seenRes[pref.key] = true
            plan[#plan+1] = {{hash=res.Type, pref=pref, target=targetIdx, costs=generalCosts}}
        else
            local proposal = propByKey[pref.key]
            if not proposal or not proposal.eligible then stop("Proposal is absent, disabled or not eligible"); return end
            if (pref.o == 1 and not proposal.up) or (pref.o == 2 and not proposal.down) then
                stop("Proposal vote direction is blocked"); return
            end
            seenProp[pref.key] = true
            plan[#plan+1] = {{proposal=proposal, pref=pref, costs=proposal.costs}}
        end
    end
    for hash, _ in pairs(resByHash) do
        if not seenRes[hash] then stop("Missing explicit vote for " .. hash); return end
    end
    for key, proposal in pairs(propByKey) do
        if proposal.eligible and not seenProp[key] then
            stop("Explicit proposal votes are required; missing " .. key .. "; no defaults supplied"); return
        end
    end
    local spent, startingFavor = 0, 0
    if #plan > 0 then
        startingFavor = Players[me]:GetFavor()
        if not integer(startingFavor) or startingFavor < 0 then stop("Favor unavailable"); return end
        -- Reserve every mandatory first vote, including nonzero category costs.
        for _, item in ipairs(plan) do item.count=1; item.cost=item.costs[0]; spent=spent+item.cost end
        if spent > startingFavor then stop("Favor cannot fund every required first vote"); return end
        local remaining = startingFavor-spent
        for _, item in ipairs(plan) do
            local maxV = item.costs.MaxVotes
            for v = 2, math.min(item.pref.v, maxV) do
                local extra = item.costs[v-1]-item.cost
                if extra > remaining then break end
                remaining=remaining-extra; spent=spent+extra; item.count=v; item.cost=item.costs[v-1]
            end
        end
    end
    local summary = {{}}
    for _, item in ipairs(plan) do
        local signed = item.proposal and item.pref.o == 2 and -item.count or item.count
        summary[#summary+1] = item.pref.kind .. ":" .. item.pref.key .. " option=" .. item.pref.o
            .. " votes=" .. signed .. " cost=" .. item.cost
    end
    summary[#summary+1] = "favor=" .. spent .. "/" .. startingFavor
    q.summary=table.concat(summary, "; ")
    if Game.GetLocalPlayer() ~= q.owner or PlayerConfigurations[me]:GetCivilizationTypeName() ~= q.civ
        or GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED") ~= q.seed
        or Game.GetCurrentGameTurn() ~= q.turn or congressStage(Game.GetCurrentTurnSegment(), wc:IsInSession()) ~= nowStage then
        stop("Request scope changed during validation"); return
    end
    -- Latch before every possibly sent stage, even if Lua/transport fails.
    -- Re-entering this handler or queue tool can only report this record.
    q.state = "VOTES_REQUESTED"
    cleanup()
    -- Native order is resolutions first, then proposals. Favor priority above
    -- remains the caller's input order, independently of operation grouping.
    for _, item in ipairs(plan) do
        if not item.proposal then
            local params = {{}}
            params[PlayerOperations.PARAM_RESOLUTION_TYPE] = item.hash
            params[PlayerOperations.PARAM_WORLD_CONGRESS_VOTES] = item.count
            params[PlayerOperations.PARAM_RESOLUTION_OPTION] = item.pref.o
            params[PlayerOperations.PARAM_RESOLUTION_SELECTION] = item.target
            UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_RESOLUTION_VOTE, params)
        end
    end
    for _, item in ipairs(plan) do
        if item.proposal then
            local params = {{}}
            params[PlayerOperations.PARAM_PLAYER_ONE] = item.proposal.data.Sender
            params[PlayerOperations.PARAM_PLAYER_TWO] = item.proposal.data.Target
            params[PlayerOperations.PARAM_WORLD_CONGRESS_VOTES] = item.count * (item.pref.o == 1 and 1 or -1)
            UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_DISCUSSION_VOTE, params)
        end
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
    print("OK:WC_STAGE_CONTINUE_REQUESTED|stage:" .. q.stage .. "|token:" .. q.token .. "|allocation:" .. q.summary)
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
