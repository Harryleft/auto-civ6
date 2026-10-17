"""Diplomacy domain — Lua builders and parsers."""

from __future__ import annotations

from civ_mcp.lua._helpers import SENTINEL, _bail, _bail_lua, _lua_close_diplo_session
from civ_mcp.lua.models import (
    AgendaInfo,
    CivInfo,
    DealItem,
    DealOptions,
    DiplomacyModifier,
    DiplomacySession,
    DiplomacyContext,
    PendingDeal,
    TestTradeItem,
    TestTradeResult,
    TradeableCity,
    VisibleCity,
)


def build_diplomacy_query() -> str:
    """Rich diplomacy query — runs in InGame context for GetDiplomaticAI access."""
    return """
local me = Game.GetLocalPlayer()
local pDiplo = Players[me]:GetDiplomacy()
local pVis = PlayersVisibility[me]
local states = {"ALLIED","DECLARED_FRIEND","FRIENDLY","NEUTRAL","UNFRIENDLY","DENOUNCED","WAR"}
local checkActions = {"DIPLOACTION_DIPLOMATIC_DELEGATION","DIPLOACTION_DECLARE_FRIENDSHIP","DIPLOACTION_DENOUNCE","DIPLOACTION_RESIDENT_EMBASSY","DIPLOACTION_OPEN_BORDERS","DIPLOACTION_MAKE_ALLIANCE"}
for i = 0, 62 do
    if i ~= me and Players[i] and Players[i]:IsAlive() and Players[i]:IsMajor() then
        local cfg = PlayerConfigurations[i]
        local civName = Locale.Lookup(cfg:GetCivilizationShortDescription())
        local leaderName = Locale.Lookup(cfg:GetLeaderName())
        local met = pDiplo:HasMet(i) and "1" or "0"
        local war = pDiplo:IsAtWarWith(i) and "1" or "0"
        if pDiplo:HasMet(i) then
            local ai = Players[i]:GetDiplomaticAI()
            local stateIdx = ai:GetDiplomaticStateIndex(me)
            local stateName = states[stateIdx + 1] or tostring(stateIdx)
            local grievances = pDiplo:GetGrievancesAgainst(i)
            local vis = pDiplo:GetVisibilityOn(i)
            local hasDel = pDiplo:HasDelegationAt(i) and "1" or "0"
            local hasEmb = pDiplo:HasEmbassyAt(i) and "1" or "0"
            local theyDel = Players[i]:GetDiplomacy():HasDelegationAt(me) and "1" or "0"
            local theyEmb = Players[i]:GetDiplomacy():HasEmbassyAt(me) and "1" or "0"
            print("CIV|" .. i .. "|" .. civName .. "|" .. leaderName .. "|" .. met .. "|" .. war .. "|" .. stateName .. "|" .. grievances .. "|" .. vis .. "|" .. hasDel .. "|" .. hasEmb .. "|" .. theyDel .. "|" .. theyEmb)
            local okMil, milStr = pcall(function() return Players[i]:GetStats():GetMilitaryStrength() end)
            local okMyMil, myMilStr = pcall(function() return Players[me]:GetStats():GetMilitaryStrength() end)
            if okMil and okMyMil then print("MILITARY|" .. i .. "|" .. (milStr or 0) .. "|" .. (myMilStr or 0)) end
            local nCivCities = 0
            for _, ec in Players[i]:GetCities():Members() do
                nCivCities = nCivCities + 1
                local ecx, ecy = ec:GetX(), ec:GetY()
                if pVis:IsRevealed(ecx, ecy) then
                    local ecName = Locale.Lookup(ec:GetName())
                    local ecPop = ec:GetPopulation()
                    local ecLoy, ecLoyPT = 100, 0
                    local ecCult = ec:GetCulturalIdentity()
                    if ecCult then ecLoy = ecCult:GetLoyalty(); ecLoyPT = ecCult:GetLoyaltyPerTurn() end
                    local ecWalls, ecDef = 0, 0
                    pcall(function()
                        for _, d in ec:GetDistricts():Members() do
                            local di = GameInfo.Districts[d:GetType()]
                            if di and di.DistrictType == "DISTRICT_CITY_CENTER" then
                                ecWalls = d:GetMaxDamage(DefenseTypes.DISTRICT_OUTER) or 0
                                ecDef = ec:GetStrengthValue() or 0
                                break
                            end
                        end
                    end)
                    print("ECITY|" .. i .. "|" .. ecName:gsub("|","/") .. "|" .. ecx .. "," .. ecy .. "|" .. ecPop .. "|" .. string.format("%.0f|%.1f", ecLoy, ecLoyPT) .. "|" .. ecWalls .. "|" .. ecDef)
                end
            end
            print("CIVCITIES|" .. i .. "|" .. nCivCities)
            local mods = ai:GetDiplomaticModifiers(me)
            if mods then
                for _, mod in ipairs(mods) do
                    local txt = tostring(mod.Text):gsub("|", "/")
                    print("MOD|" .. i .. "|" .. mod.Score .. "|" .. txt)
                end
            end
            if stateIdx == 0 then
                local ok3, aType = pcall(function() return pDiplo:GetAllianceType(i) end)
                if ok3 and aType and aType >= 0 then
                    local aNames = {"RESEARCH","CULTURAL","ECONOMIC","MILITARY","RELIGIOUS"}
                    local aLevel = 1
                    pcall(function() aLevel = pDiplo:GetAllianceLevel(i) or 1 end)
                    print("ALLIANCE|" .. i .. "|" .. (aNames[aType+1] or tostring(aType)) .. "|" .. aLevel)
                end
            end
            local avail = {}
            for _, aName in ipairs(checkActions) do
                local ok2, valid = pcall(function() return pDiplo:IsDiplomaticActionValid(aName, i, false) end)
                if ok2 and valid then
                    local label = aName:gsub("DIPLOACTION_", "")
                    if label == "OPEN_BORDERS" then label = "Open Borders (via propose_trade)" end
                    table.insert(avail, label)
                end
            end
            if not pDiplo:IsAtWarWith(i) then
                local canWar = false
                pcall(function() canWar = pDiplo:CanDeclareWarOn(i) end)
                if canWar then table.insert(avail, "DECLARE_WAR") end
            end
            if #avail > 0 then print("ACTIONS|" .. i .. "|" .. table.concat(avail, ",")) end
            -- Agendas (visibility-gated: historical always, random only at SECRET+)
            local okAg, agendas = pcall(function() return Players[i]:GetAgendaTypes() end)
            if okAg and agendas then
                local histSet = {}
                local leaderType = cfg:GetLeaderTypeName()
                for ha in GameInfo.HistoricalAgendas() do
                    if ha.LeaderType == leaderType then
                        local aDef = GameInfo.Agendas[ha.AgendaType]
                        if aDef then histSet[aDef.Index] = true end
                    end
                end
                local vis = pDiplo:GetVisibilityOn(i)
                for _, agIdx in ipairs(agendas) do
                    local aDef = GameInfo.Agendas[agIdx]
                    if aDef then
                        local isHist = histSet[agIdx] or false
                        if isHist then
                            print("AGENDA|" .. i .. "|HISTORICAL|" .. Locale.Lookup(aDef.Name) .. "|" .. Locale.Lookup(aDef.Description))
                        elseif vis >= 3 then
                            print("AGENDA|" .. i .. "|HIDDEN|" .. Locale.Lookup(aDef.Name) .. "|" .. Locale.Lookup(aDef.Description))
                        else
                            print("AGENDA|" .. i .. "|HIDDEN|???|Requires Secret diplomatic visibility (spy or alliance)")
                        end
                    end
                end
            end
            local okPact, hasPact = pcall(function() return Players[i]:GetDiplomacy():HasDefensivePact(me) end)
            if okPact and hasPact then print("PACT|" .. i .. "|DEFENSIVE") end
        else
            print("CIV|" .. i .. "|Unmet Civilization|Unknown Leader|" .. met .. "|" .. war .. "|UNKNOWN|0|0|0|0|0|0")
        end
    end
end
-- Scan for third-party defensive pacts
for i = 0, 62 do
    if i ~= me and Players[i] and Players[i]:IsAlive() and Players[i]:IsMajor() and pDiplo:HasMet(i) then
        for j = i+1, 62 do
            if j ~= me and Players[j] and Players[j]:IsAlive() and Players[j]:IsMajor() and pDiplo:HasMet(j) then
                local okP, hp = pcall(function() return Players[i]:GetDiplomacy():HasDefensivePact(j) end)
                if okP and hp then print("PACT|" .. i .. "|" .. j .. "|DEFENSIVE") end
            end
        end
    end
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


# Working deals outlive their statement. Native ActionView shows DealView for both
# MAKE_DEAL and MAKE_DEMAND; no cross-context active-statement getter is available.
# Associate UI only with one verified player pair, never with cached item count.
_LUA_DIPLOMACY_CONTEXT = """
local me = Game.GetLocalPlayer()
local function readDiplomacyContext()
    local c = {status="UNKNOWN", reason="SESSION_READ_FAILED", sessions={}}
    local seen, candidates = {}, {}
    local okScan = pcall(function()
        for i = 0, 62 do
            if i ~= me and Players[i] then
                local sid = DiplomacyManager.FindOpenSessionID(me, i)
                if sid ~= nil then
                    if type(sid) ~= "number" then error("invalid session id") end
                    if sid >= 0 then
                        if not seen[sid] then
                            seen[sid] = {sid=sid, players={}}
                            candidates[#candidates + 1] = seen[sid]
                        end
                        seen[sid].players[i] = true
                    end
                end
            end
        end
    end)
    if not okScan then return c end
    if #candidates == 0 then c.status="NONE"; c.reason="NO_OPEN_SESSION"; return c end
    local invalidPair = false
    for _, candidate in ipairs(candidates) do
        local ok, other, incoming = pcall(function()
            local info = DiplomacyManager.GetSessionInfo(candidate.sid)
            local from, to = info.FromPlayer, info.ToPlayer
            if type(from) ~= "number" or type(to) ~= "number" then return nil end
            local other = from == me and to or (to == me and from or nil)
            if other == me or not candidate.players[other] then return nil end
            return other, from == other and to == me
        end)
        if ok and other ~= nil then
            c.sessions[#c.sessions + 1] = {sid=candidate.sid, other=other, incoming=incoming}
        else invalidPair = true end
    end
    if #candidates ~= 1 then c.reason="MULTIPLE_SESSIONS"; return c end
    if invalidPair then c.reason="SESSION_PARTICIPANTS_UNKNOWN"; return c end
    local session = c.sessions[1]
    c.other = session.other
    local function visible(path)
        local ok, hidden = pcall(function()
            local control = ContextPtr:LookUpControl(path)
            if not control then return nil end
            return control:IsHidden()
        end)
        if not ok or type(hidden) ~= "boolean" then return nil end
        return not hidden
    end
    local dealVisible = visible("/InGame/DiplomacyDealView")
    local actionVisible = visible("/InGame/DiplomacyActionView")
    local conversationVisible = visible("/InGame/DiplomacyActionView/ConversationContainer")
    if dealVisible == nil or actionVisible == nil or conversationVisible == nil then
        c.reason="VIEW_UNAVAILABLE"; return c
    end
    if dealVisible then
        -- Both roots visible is normal. Only an effectively visible conversation
        -- conflicts with the deal; a child's own flag under a hidden root does not.
        if actionVisible and conversationVisible then c.reason="CONFLICTING_VIEWS"; return c end
        if not session.incoming then c.reason="OUTGOING_SESSION"; return c end
        local ok, deal = pcall(function()
            local deal = DealManager.GetWorkingDeal(DealDirection.INCOMING, me, c.other)
            if not deal or deal:IsValid() ~= true then return nil end
            local count = deal:GetItemCount()
            if type(count) ~= "number" or count <= 0 then return nil end
            return deal
        end)
        if not ok or not deal then c.reason="INCOMING_UNAVAILABLE"; return c end
        c.status="INCOMING"; c.reason="CURRENT_INCOMING_VIEW"; c.deal=deal
    elseif actionVisible and conversationVisible then
        c.status="DIALOGUE"; c.reason="CURRENT_DIALOGUE_VIEW"
    else
        c.reason="NO_CURRENT_VIEW"
    end
    return c
end
local context = readDiplomacyContext()
"""


def build_diplomacy_session_query() -> str:
    """Read current diplomacy context, without treating working caches as offers."""
    return _LUA_DIPLOMACY_CONTEXT + r"""
local function text(path)
    local ok, value = pcall(function()
        local control = ContextPtr:LookUpControl(path)
        if not control or control:IsHidden() then return "" end
        return control:GetText()
    end)
    return ok and type(value) == "string" and value:gsub("[|\r\n]", " ") or ""
end
local dialogue, reason, buttons = "", "", {}
if context.status == "DIALOGUE" then
    dialogue = text("/InGame/DiplomacyActionView/LeaderResponseText")
    reason = text("/InGame/DiplomacyActionView/LeaderReasonText")
    for _, path in ipairs({
        "/InGame/DiplomacyActionView/SelectionStack/Selection1Button/SelectionText",
        "/InGame/DiplomacyActionView/SelectionStack/Selection2Button/SelectionText",
    }) do
        local ok, visible = pcall(function()
            local control = ContextPtr:LookUpControl(path)
            return control and not control:GetParent():IsHidden()
        end)
        if ok and visible then local value=text(path); if value ~= "" then buttons[#buttons+1]=value end end
    end
    local ok, goodbye = pcall(function()
        local control = ContextPtr:LookUpControl("/InGame/DiplomacyActionView/GoodbyeButton")
        return control and not control:IsHidden()
    end)
    if ok and goodbye then buttons[#buttons+1]="GOODBYE" end
elseif context.status == "INCOMING" then
    dialogue = text("/InGame/DiplomacyDealView/LeaderResponseText")
end
print("CONTEXT|" .. context.status .. "|" .. context.reason)
for _, session in ipairs(context.sessions) do
    local cfg = PlayerConfigurations[session.other]
    local civName = Locale.Lookup(cfg:GetCivilizationShortDescription()):gsub("[|\r\n]", " ")
    local leaderName = Locale.Lookup(cfg:GetLeaderName()):gsub("[|\r\n]", " ")
    local atWar = Players[me]:GetDiplomacy():IsAtWarWith(session.other) and "1" or "0"
    print("SESSION|" .. session.sid .. "|" .. session.other .. "|" .. civName .. "|" .. leaderName .. "|" .. dialogue .. "|" .. reason .. "|" .. table.concat(buttons, ";") .. "|" .. atWar)
    if context.status == "INCOMING" then
        print("DEAL|" .. session.other .. "|" .. civName .. "|" .. leaderName)
        for item in context.deal:Items() do
            local fromTag = item:GetFromPlayerID() == me and "US" or "THEM"
            local itemType, subType = item:GetType(), item:GetSubType()
            local amount, duration = item:GetAmount() or 0, item:GetDuration() or 0
            local valueType = item:GetValueType() or -1
            local typeName, itemName = "UNKNOWN", "Unrecognized item"
            if itemType == DealItemTypes.GOLD then
                typeName="GOLD"; itemName=duration > 0 and "Gold per turn" or "Gold (lump sum)"
            elseif itemType == DealItemTypes.RESOURCES then
                typeName="RESOURCE"
                local resource = GameInfo.Resources[valueType]
                itemName=resource and Locale.Lookup(resource.Name) or ("Resource#" .. tostring(valueType))
            elseif itemType == DealItemTypes.AGREEMENTS then
                typeName="AGREEMENT"; itemName="Agreement"
                if subType == DealAgreementTypes.OPEN_BORDERS then itemName="Open Borders"
                elseif subType == DealAgreementTypes.JOINT_WAR then itemName="Joint War"
                elseif subType == DealAgreementTypes.ALLIANCE then
                    local names={"Research","Cultural","Economic","Military","Religious"}
                    itemName=(names[valueType+1] or "Unknown") .. " Alliance"
                end
            elseif itemType == DealItemTypes.FAVOR then typeName="FAVOR"; itemName="Diplomatic Favor"
            elseif itemType == DealItemTypes.CITIES then typeName="CITY"; itemName="City"
            elseif itemType == DealItemTypes.GREATWORK then typeName="GREAT_WORK"; itemName="Great Work"
            end
            print("ITEM|" .. session.other .. "|" .. fromTag .. "|" .. typeName .. "|" .. itemName:gsub("[|\r\n]", " ") .. "|" .. amount .. "|" .. duration)
        end
    end
end
print("CONTEXT_COMPLETE")
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def build_diplomacy_choices_query(other_player_id: int) -> str:
    """Get available dialogue choices for an open session with a specific player."""
    return f"""
local me = Game.GetLocalPlayer()
local sid = DiplomacyManager.FindOpenSessionID(me, {other_player_id})
if sid == nil or sid < 0 then {_bail("ERR:NO_SESSION")} end
print("SESSION|" .. sid)
local ctrl = ContextPtr:LookUpControl("/InGame/DiplomacyActionView")
local isVisible = ctrl and not ctrl:IsHidden() or false
print("VISIBLE|" .. tostring(isVisible))
for row in GameInfo.DiplomacySelections() do
    if string.find(row.Type, "FIRST_MEET") or string.find(row.Type, "GREETING") or string.find(row.Type, "DECLARE_FRIEND") or string.find(row.Type, "DENOUNCE") then
        local text = Locale.Lookup(row.Text)
        print("CHOICE|" .. row.Type .. "|" .. row.Key .. "|" .. text)
    end
end
print("{SENTINEL}")
"""


def build_diplomacy_respond(other_player_id: int, response: str) -> str:
    """Respond to a diplomacy session.

    response is 'POSITIVE', 'NEGATIVE', or 'EXIT'.
    EXIT closes the session directly (last-resort for orphaned sessions).
    POSITIVE/NEGATIVE sends AddResponse only — does NOT call CloseSession.
    The C++ engine handles session lifecycle through its own callbacks.
    Caller must check session state in a SEPARATE call to allow the engine
    time to process the response (same-frame checks see stale state).
    """
    return f"""
local me = Game.GetLocalPlayer()
local sid = DiplomacyManager.FindOpenSessionID(me, {other_player_id})
if sid == nil or sid < 0 then {_bail("ERR:NO_SESSION")} end
if "{response}" == "EXIT" then
    DiplomacyManager.CloseSession(sid)
    LuaEvents.DiplomacyActionView_ShowIngameUI()
    pcall(function() Events.HideLeaderScreen() end)
    print("OK:SESSION_CLOSED")
    print("{SENTINEL}"); return
end
DiplomacyManager.AddResponse(sid, me, "{response}")
print("OK:RESPONSE_SENT|{response}")
print("{SENTINEL}")
"""


def build_check_diplomacy_session_state(other_player_id: int) -> str:
    """Check if a diplomacy session is still open after AddResponse.

    Must be called in a SEPARATE TCP round-trip from AddResponse to give
    the C++ engine time to process the response and transition/close
    the session naturally.  Returns SESSION_OPEN or SESSION_CLOSED.
    """
    return f"""
local me = Game.GetLocalPlayer()
local sid = DiplomacyManager.FindOpenSessionID(me, {other_player_id})
if sid and sid >= 0 then
    print("SESSION_OPEN|" .. sid)
else
    pcall(function() LuaEvents.DiplomacyActionView_ShowIngameUI() end)
    pcall(function() Events.HideLeaderScreen() end)
    print("SESSION_CLOSED")
end
print("{SENTINEL}")
"""


def build_send_diplo_action(other_player_id: int, action_name: str) -> str:
    """Send a native action; friendship needs a later relationship witness.

    action_name is e.g. DIPLOMATIC_DELEGATION, DECLARE_FRIENDSHIP, DENOUNCE,
    RESIDENT_EMBASSY, DECLARE_SURPRISE_WAR, DECLARE_FORMAL_WAR, etc.

    Open Borders is NOT supported here — it's a trade deal, not a diplomatic
    action. Use propose_trade with AGREEMENT/OPEN_BORDERS items instead.

    Key discovery: RequestSession uses DIFFERENT action strings from DIPLOACTION_ names:
    - DECLARE_FRIENDSHIP -> session string "DECLARE_FRIEND" (not "DECLARE_FRIENDSHIP")
    - Others use same name as action_name

    Flow: RequestSession -> 2x AddResponse(POSITIVE) -> CloseSession
    No AddStatement needed (that crashes on mismatched session types).
    """
    # Map action_name to the correct RequestSession string
    # Game source: DiplomacyActionView.lua line 472 uses "DECLARE_FRIEND"
    session_string_map = {
        "DECLARE_FRIENDSHIP": "DECLARE_FRIEND",
        "DIPLOMATIC_DELEGATION": "DIPLOMATIC_DELEGATION",
        "RESIDENT_EMBASSY": "RESIDENT_EMBASSY",
        "DENOUNCE": "DENOUNCE",
        # War declarations — session strings match action names
        "DECLARE_SURPRISE_WAR": "DECLARE_SURPRISE_WAR",
        "DECLARE_FORMAL_WAR": "DECLARE_FORMAL_WAR",
        "DECLARE_HOLY_WAR": "DECLARE_HOLY_WAR",
        "DECLARE_LIBERATION_WAR": "DECLARE_LIBERATION_WAR",
        "DECLARE_RECONQUEST_WAR": "DECLARE_RECONQUEST_WAR",
        "DECLARE_PROTECTORATE_WAR": "DECLARE_PROTECTORATE_WAR",
        "DECLARE_COLONIAL_WAR": "DECLARE_COLONIAL_WAR",
        "DECLARE_TERRITORIAL_WAR": "DECLARE_TERRITORIAL_WAR",
    }
    is_war = action_name.endswith("_WAR") and action_name.startswith("DECLARE_")
    session_str = session_string_map.get(action_name, action_name)

    # War declarations use CanDeclareWarOn; other actions use IsDiplomaticActionValid
    if is_war:
        validation_block = f"""
local me = Game.GetLocalPlayer()
local pDiplo = Players[me]:GetDiplomacy()
local target = {other_player_id}
local action = "{action_name}"
if pDiplo:IsAtWarWith(target) then
    {_bail("ERR:ALREADY_AT_WAR|Already at war with this player")}
end
local canWar = false
pcall(function() canWar = pDiplo:CanDeclareWarOn(target) end)
if not canWar then
    {_bail("ERR:CANNOT_DECLARE_WAR|Cannot declare war. Possible reasons: friendship/alliance active, 10-turn peace cooldown, or target is invalid.")}
end"""
    else:
        validation_block = f"""
local me = Game.GetLocalPlayer()
local pDiplo = Players[me]:GetDiplomacy()
local target = {other_player_id}
local action = "{action_name}"
local fullAction = "DIPLOACTION_" .. action
local valid, results = pDiplo:IsDiplomaticActionValid(fullAction, target, true)
if not valid then
    local reasons = "unknown"
    if results and results.FailureReasons then
        local parts = {{}}
        for _, r in ipairs(results.FailureReasons) do
            local s = tostring(r or "")
            if s:find("OBSOLETE_CIVIC") or s:find("ObsoleteCivic") then
                table.insert(parts, "obsolete (Diplomatic Service civic researched — use embassy instead)")
            else
                local loc = Locale.Lookup(s)
                if loc and loc ~= "" then table.insert(parts, loc) else table.insert(parts, s) end
            end
        end
        if #parts > 0 then reasons = table.concat(parts, "; ") end
    end
    if reasons == "unknown" and action == "DIPLOMATIC_DELEGATION" then
        local dipSvcCivic = GameInfo.Civics["CIVIC_DIPLOMATIC_SERVICE"]
        if dipSvcCivic then
            local hasCivic = false
            pcall(function() hasCivic = Players[me]:GetCulture():HasCivic(dipSvcCivic.Index) end)
            if hasCivic then
                reasons = "obsolete (Diplomatic Service civic researched — use embassy instead)"
            end
        end
    end
    {_bail_lua('"ERR:INVALID|" .. reasons')}
end"""

    # War declarations leave the session open so the leader animation plays.
    # Python schedules cleanup after ~8s via build_war_diplo_cleanup().
    close_session_lua = "" if is_war else "    DiplomacyManager.CloseSession(sid)"
    cleanup_lua = "" if is_war else _lua_close_diplo_session()

    return f"""
{validation_block}
-- Clean stale session for THIS target only (not all session IDs).
-- Mass-closing sessions via IsSessionIDOpen loop corrupts AI diplomacy state.
local staleSid = DiplomacyManager.FindOpenSessionID(me, target)
if staleSid and staleSid >= 0 then
    DiplomacyManager.CloseSession(staleSid)
end
-- Open session with the correct action string
DiplomacyManager.RequestSession(me, target, "{session_str}")
local sid = DiplomacyManager.FindOpenSessionID(me, target)
local sessionCompleted = false
if sid and sid >= 0 then
    DiplomacyManager.AddResponse(sid, me, "POSITIVE")
    DiplomacyManager.AddResponse(sid, me, "POSITIVE")
{close_session_lua}
    sessionCompleted = true
end
{cleanup_lua}
-- Report result.
-- NOTE: All post-state queries (HasDelegationAt, HasEmbassyAt, IsDiplomaticActionValid,
-- GetGoldBalance, GetVisibilityOn) are STALE same-frame after CloseSession. The C++ engine
-- commits state changes on the next frame. So we cannot reliably detect acceptance by
-- comparing pre/post state. Friendship reports only a submitted request here;
-- GameState confirms its native bilateral relationship in later read-only calls.
local name = Locale.Lookup(PlayerConfigurations[target]:GetCivilizationShortDescription())
if action:find("_WAR") then
    local atWar = pDiplo:IsAtWarWith(target)
    if atWar then
        print("OK:WAR_DECLARED|" .. action .. " on " .. name .. " — now at war")
    else
        print("WARN:WAR_UNCERTAIN|" .. action .. " session completed but war state not yet confirmed for " .. name .. ". Check next turn.")
    end
elseif action == "DIPLOMATIC_DELEGATION" then
    if sessionCompleted then
        print("OK:ACCEPTED|" .. name .. " accepted your delegation")
    else
        print("OK:ACCEPTED|Delegation sent to " .. name)
    end
elseif action == "RESIDENT_EMBASSY" then
    print("OK:ACCEPTED|" .. name .. " accepted your embassy")
elseif action == "DECLARE_FRIENDSHIP" then
    print("OK:PROPOSED|" .. name)
    print("FRIENDSHIP_SCOPE|" .. me .. "|" .. target)
elseif action == "DENOUNCE" then
    print("OK:SENT|Denounced " .. name)
else
    print("OK:SENT|" .. action .. " sent to " .. name)
end
print("{SENTINEL}")
"""


def _native_relationship_lua() -> str:
    """Official index -> StateType/hash interpretation; no guessed state ordinals."""
    return """
local function integer(value)
    return type(value) == "number" and value == value and math.abs(value) < math.huge and value == math.floor(value)
end
local function nativeRelationship(player, other)
    local ok, row = pcall(function()
        local index = Players[player]:GetDiplomaticAI():GetDiplomaticStateIndex(other)
        if not integer(index) then return nil end
        local row = GameInfo.DiplomaticStates[index]
        if not row or type(row.StateType) ~= "string" or not integer(row.Hash) then return nil end
        return row
    end)
    return ok and row or nil
end
local function matchesRelationship(row, stateType, stateHash)
    if not row or not integer(stateHash) then return nil end
    if row.StateType == stateType and row.Hash == stateHash then return true end
    if row.StateType == stateType or row.Hash == stateHash then return nil end
    return false
end
"""


def build_check_friendship_state(other_player_id: int, owner_id: int) -> str:
    """Read reciprocal native indices through official StateType/hash mappings."""
    return _native_relationship_lua() + f"""
local me = Game.GetLocalPlayer()
local target = {other_player_id}
local status = "UNKNOWN"
local ok, result = pcall(function()
    if me ~= {owner_id} or me == target or not Players[me] or not Players[target] then return "UNKNOWN" end
    local ours = matchesRelationship(nativeRelationship(me, target), "DIPLO_STATE_DECLARED_FRIEND", DiplomaticStates.DECLARED_FRIEND)
    local theirs = matchesRelationship(nativeRelationship(target, me), "DIPLO_STATE_DECLARED_FRIEND", DiplomaticStates.DECLARED_FRIEND)
    if ours == nil or theirs == nil then return "UNKNOWN" end
    return ours and theirs and "DECLARED_FRIEND" or "NOT_DECLARED_FRIEND"
end)
if ok then status = result end
print("FRIENDSHIP|" .. me .. "|" .. target .. "|" .. status)
print("{SENTINEL}")
"""


def build_check_alliance_state(other_player_id: int, owner_id: int, alliance_type: str) -> str:
    """Require bilateral ALLIED and the exact native requested alliance type."""
    import json

    alliance_key = json.dumps(f"ALLIANCE_{alliance_type.upper()}", ensure_ascii=False)
    return _native_relationship_lua() + f"""
local me = Game.GetLocalPlayer()
local target = {other_player_id}
local status = "UNKNOWN"
local ok, result = pcall(function()
    if me ~= {owner_id} or me == target or not Players[me] or not Players[target] then return "UNKNOWN" end
    local ours = matchesRelationship(nativeRelationship(me, target), "DIPLO_STATE_ALLIED", DiplomaticStates.ALLIED)
    local theirs = matchesRelationship(nativeRelationship(target, me), "DIPLO_STATE_ALLIED", DiplomaticStates.ALLIED)
    if ours == nil or theirs == nil then return "UNKNOWN" end
    if not ours or not theirs then return "NOT_ALLIED" end
    local allianceIndex = Players[me]:GetDiplomacy():GetAllianceType(target)
    if not integer(allianceIndex) or allianceIndex < 0 then return "UNKNOWN" end
    local row = GameInfo.Alliances[allianceIndex]
    if not row or type(row.AllianceType) ~= "string" then return "UNKNOWN" end
    return row.AllianceType == {alliance_key} and {alliance_key} or "TYPE_MISMATCH"
end)
if ok then status = result end
print("ALLIANCE|" .. me .. "|" .. target .. "|" .. status)
print("{SENTINEL}")
"""


def build_war_close_session(other_player_id: int) -> str:
    """Phase 1: close the war diplomacy session (InGame context).

    After this, DiplomacyActionView transitions to OVERVIEW_MODE (intel screen).
    Must wait ~1s for the engine event to process before phase 2.
    """
    sentinel = SENTINEL
    return f"""
local me = Game.GetLocalPlayer()
local target = {other_player_id}
local sid = DiplomacyManager.FindOpenSessionID(me, target)
if sid and sid >= 0 then
    DiplomacyManager.CloseSession(sid)
end
print("OK:SESSION_CLOSED")
print("{sentinel}")
"""


def build_war_dismiss_view() -> str:
    """Phase 2: force-dismiss DiplomacyActionView from OVERVIEW_MODE (InGame context).

    NaturalWonderPopup_Shown triggers OnBlockingPopupShown → OnForceClose →
    CloseFocusedState(true) → Close() which calls UninitializeView + ShowIngameUI
    (balancing InitializeView's HideIngameUI).  NaturalWonderPopup_Closed then
    balances NW's own BulkHide(true) entry.

    Must be called in a SEPARATE Lua execution from CloseSession — the engine
    fires OnDiplomacySessionClosed asynchronously, so the view needs a frame to
    transition from CONVERSATION_MODE to OVERVIEW_MODE first.
    """
    return """
LuaEvents.NaturalWonderPopup_Shown()
LuaEvents.NaturalWonderPopup_Closed()
pcall(function() Events.HideLeaderScreen() end)
print("OK:VIEW_DISMISSED")
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def build_deal_options_query(other_player_id: int) -> str:
    """Show what both sides can trade — resources, gold, favor, agreements (InGame)."""
    return f"""
local me = Game.GetLocalPlayer()
local target = {other_player_id}
local pDiplo = Players[me]:GetDiplomacy()
if not Players[target] or not Players[target]:IsAlive() then {_bail(f"ERR:INVALID_PLAYER|Player {other_player_id} not found")} end
if not pDiplo:HasMet(target) then {_bail(f"ERR:NOT_MET|Have not met player {other_player_id}")} end
local name = Locale.Lookup(PlayerConfigurations[target]:GetCivilizationShortDescription())
print("CIV|" .. target .. "|" .. name:gsub("|","/"))
local ourGold = math.floor(Players[me]:GetTreasury():GetGoldBalance())
local ourGPT = math.floor(Players[me]:GetTreasury():GetGoldYield() - Players[me]:GetTreasury():GetTotalMaintenance())
local ourFavor = 0
pcall(function() ourFavor = math.floor(Players[me]:GetFavor() or 0) end)
local theirGold = math.floor(Players[target]:GetTreasury():GetGoldBalance())
local theirGPT = math.floor(Players[target]:GetTreasury():GetGoldYield() - Players[target]:GetTreasury():GetTotalMaintenance())
local theirFavor = 0
pcall(function() theirFavor = math.floor(Players[target]:GetFavor() or 0) end)
print("ECON|" .. ourGold .. "|" .. ourGPT .. "|" .. ourFavor .. "|" .. theirGold .. "|" .. theirGPT .. "|" .. theirFavor)
for row in GameInfo.Resources() do
    local ourAmt = Players[me]:GetResources():GetResourceAmount(row.Index)
    local theirAmt = Players[target]:GetResources():GetResourceAmount(row.Index)
    if ourAmt > 0 or theirAmt > 0 then
        local rClass = row.ResourceClassType or ""
        local rName = Locale.Lookup(row.Name)
        print("RES|" .. rName:gsub("|","/") .. "|" .. row.ResourceType .. "|" .. rClass .. "|" .. ourAmt .. "|" .. theirAmt)
    end
end
local hasOB = false
pcall(function() hasOB = pDiplo:HasOpenBordersFrom(target) end)
if not hasOB then pcall(function() hasOB = pDiplo:GetVisibilityOn(target) >= 2 end) end
print("OB|" .. (hasOB and "1" or "0"))
local ai = Players[target]:GetDiplomaticAI()
local stateIdx = ai:GetDiplomaticStateIndex(me)
local hasDiploService = false
pcall(function()
    local civic = GameInfo.Civics["CIVIC_DIPLOMATIC_SERVICE"]
    if civic then hasDiploService = Players[me]:GetCulture():HasCivic(civic.Index) end
end)
local allianceEligible = (stateIdx == 1 and hasDiploService)
local currentAlliance = ""
if stateIdx == 0 then
    local ok3, aType = pcall(function() return pDiplo:GetAllianceType(target) end)
    if ok3 and aType and aType >= 0 then
        local aNames = {{"RESEARCH","CULTURAL","ECONOMIC","MILITARY","RELIGIOUS"}}
        currentAlliance = aNames[aType+1] or ""
    end
end
print("ALLIANCE|" .. (allianceEligible and "1" or "0") .. "|" .. currentAlliance)
for _, city in Players[me]:GetCities():Members() do
    local cName = Locale.Lookup(city:GetName()):gsub("|", "/")
    local cid = city:GetID()
    local pop = city:GetPopulation()
    local isCapital = city:IsCapital() and "1" or "0"
    print("CITY|OURS|" .. cid .. "|" .. cName .. "|" .. pop .. "|" .. isCapital)
end
for _, city in Players[target]:GetCities():Members() do
    local cName = Locale.Lookup(city:GetName()):gsub("|", "/")
    local cid = city:GetID()
    local pop = city:GetPopulation()
    local isCapital = city:IsCapital() and "1" or "0"
    print("CITY|THEIRS|" .. cid .. "|" .. cName .. "|" .. pop .. "|" .. isCapital)
end
print("{SENTINEL}")
"""


def parse_deal_options_response(lines: list[str]) -> DealOptions:
    """Parse the deal options query response."""
    opts = DealOptions(other_player_id=0, other_civ_name="")
    for line in lines:
        if line.startswith("CIV|"):
            parts = line.split("|")
            if len(parts) >= 3:
                opts.other_player_id = int(parts[1])
                opts.other_civ_name = parts[2]
        elif line.startswith("ECON|"):
            parts = line.split("|")
            if len(parts) >= 7:
                opts.our_gold = int(parts[1])
                opts.our_gpt = int(parts[2])
                opts.our_favor = int(parts[3])
                opts.their_gold = int(parts[4])
                opts.their_gpt = int(parts[5])
                opts.their_favor = int(parts[6])
        elif line.startswith("RES|"):
            parts = line.split("|")
            if len(parts) >= 6:
                name = parts[1]
                res_type = parts[2]
                res_class = parts[3]
                our_amt = int(parts[4])
                their_amt = int(parts[5])
                is_luxury = "LUXURY" in res_class
                is_strategic = "STRATEGIC" in res_class
                if our_amt > 0:
                    label = f"{name} x{our_amt}" if our_amt > 1 else name
                    if is_luxury:
                        opts.our_luxuries.append(label)
                    elif is_strategic:
                        opts.our_strategics.append(label)
                if their_amt > 0:
                    label = f"{name} x{their_amt}" if their_amt > 1 else name
                    if is_luxury:
                        opts.their_luxuries.append(label)
                    elif is_strategic:
                        opts.their_strategics.append(label)
        elif line.startswith("OB|"):
            opts.has_open_borders = line.split("|")[1] == "1"
        elif line.startswith("ALLIANCE|"):
            parts = line.split("|")
            if len(parts) >= 3:
                opts.alliance_eligible = parts[1] == "1"
                if parts[2]:
                    opts.current_alliance = parts[2]
        elif line.startswith("CITY|"):
            parts = line.split("|")
            if len(parts) >= 6:
                city = TradeableCity(
                    city_id=int(parts[2]),
                    name=parts[3],
                    population=int(parts[4]),
                    is_capital=parts[5] == "1",
                )
                if parts[1] == "OURS":
                    opts.our_cities.append(city)
                else:
                    opts.their_cities.append(city)
    return opts


def build_pending_deals_query() -> str:
    """Use the same current-context facts as the diplomacy encounter query."""
    return build_diplomacy_session_query()


def build_respond_to_deal(other_player_id: int, accept: bool) -> str:
    """Submit only against a verified current incoming deal/demand for this target."""
    action = "DealProposalAction.ACCEPTED" if accept else "DealProposalAction.REJECTED"
    verb = "ACCEPTED" if accept else "REJECTED"
    return _LUA_DIPLOMACY_CONTEXT + f"""
local target = {other_player_id}
if context.status ~= "INCOMING" or context.other ~= target then
    {_bail_lua('"ERR:NO_CURRENT_PROPOSAL|" .. context.status .. "|" .. context.reason .. "|Target must match the verified current incoming view"')}
end
local sid = context.sessions[1].sid
DealManager.SendWorkingDeal({action}, me, target)
{_lua_close_diplo_session()}
local name = Locale.Lookup(PlayerConfigurations[target]:GetCivilizationShortDescription())
print("OK:DEAL_{verb}|" .. name)
print("{SENTINEL}")
"""


def _lua_deal_item(from_var: str, item: dict) -> str:
    """Generate Lua snippet to add one item to the working deal.

    from_var: Lua variable name for the player ID (e.g. "me" or "target").
    item: dict with keys type, amount, and optionally name, duration.
    """
    t = item["type"].upper()
    amount = item.get("amount", 0)
    duration = item.get("duration", 0)

    if t == "GOLD":
        return (
            f"do local gi = deal:AddItemOfType(DealItemTypes.GOLD, {from_var}) "
            f"if gi then gi:SetAmount({amount}) gi:SetDuration({duration}) end end"
        )
    elif t == "RESOURCE":
        res_name = item["name"]
        res_amount = item.get("amount", 1)
        res_duration = item.get("duration", 30)
        return (
            f'do local res = GameInfo.Resources["{res_name}"] '
            f"if res then local ri = deal:AddItemOfType(DealItemTypes.RESOURCES, {from_var}) "
            f"if ri then ri:SetValueType(res.Index) ri:SetAmount({res_amount}) "
            f"ri:SetDuration({res_duration}) end end end"
        )
    elif t == "FAVOR":
        return (
            f"do local fi = deal:AddItemOfType(DealItemTypes.FAVOR, {from_var}) "
            f"if fi then fi:SetAmount({amount}) end end"
        )
    elif t == "AGREEMENT":
        subtype = item["subtype"]  # "OPEN_BORDERS", "JOINT_WAR", "ALLIANCE"
        return (
            f"do local ai = deal:AddItemOfType(DealItemTypes.AGREEMENTS, {from_var}) "
            f"if ai then ai:SetSubType(DealAgreementTypes.{subtype}) end end"
        )
    elif t == "CITY":
        city_id = item["city_id"]
        return (
            f"do local ci = deal:AddItemOfType(DealItemTypes.CITIES, {from_var}) "
            f"if ci then ci:SetValueType({city_id}) end end"
        )
    else:
        return f"-- unsupported deal item type: {t}"


def build_propose_trade(
    other_player_id: int,
    offer_items: list[dict],
    request_items: list[dict],
) -> str:
    """Build a trade deal proposal and send it (InGame context).

    offer_items: items we give to them (from us).
    request_items: items we want from them.
    Each item dict: {type: GOLD|RESOURCE|FAVOR|AGREEMENT|CITY, amount: int, name: str, duration: int, subtype: str, city_id: int}
    """
    offer_lua = " ".join(_lua_deal_item("me", item) for item in offer_items)
    request_lua = " ".join(_lua_deal_item("target", item) for item in request_items)

    return f"""
local me = Game.GetLocalPlayer()
local target = {other_player_id}
local pDiplo = Players[me]:GetDiplomacy()
if not pDiplo:HasMet(target) then {_bail("ERR:NOT_MET|Have not met player " + str(other_player_id))} end
if pDiplo:IsAtWarWith(target) then {_bail("ERR:AT_WAR|Cannot trade while at war")} end
local name = Locale.Lookup(PlayerConfigurations[target]:GetCivilizationShortDescription())
if not DealManager.HasPendingDeal(me, target) then
    DealManager.ClearWorkingDeal(DealDirection.OUTGOING, me, target)
end
local deal = DealManager.GetWorkingDeal(DealDirection.OUTGOING, me, target)
if not deal then {_bail("ERR:NO_DEAL_OBJECT|Failed to get working deal")} end
{offer_lua}
{request_lua}
DiplomacyManager.RequestSession(me, target, "MAKE_DEAL")
DealManager.SendWorkingDeal(DealProposalAction.PROPOSED, me, target)
local sid = DiplomacyManager.FindOpenSessionID(me, target)
local result = "PROPOSED"
local termsStr = ""
if sid and sid >= 0 then
    local ok, respDeal = pcall(function()
        return DealManager.GetWorkingDeal(DealDirection.INCOMING, me, target)
    end)
    if ok and respDeal and respDeal:GetItemCount() and respDeal:GetItemCount() > 0 then
        DealManager.SendWorkingDeal(DealProposalAction.ACCEPTED, me, target)
        result = "ACCEPTED"
        -- Report the actual deal terms (AI may have counter-offered different terms)
        local weGive = {{}}
        local theyGive = {{}}
        for item in respDeal:Items() do
            local fromID = item:GetFromPlayerID()
            local iType = item:GetType()
            local subType = item:GetSubType() or -1
            local amount = item:GetAmount() or 0
            local duration = item:GetDuration() or 0
            local desc = "Unknown"
            if iType == DealItemTypes.GOLD then
                if duration > 0 then desc = amount .. " gold/turn (" .. duration .. " turns)"
                else desc = amount .. " gold" end
            elseif iType == DealItemTypes.AGREEMENTS then
                if subType == DealAgreementTypes.OPEN_BORDERS then desc = "Open Borders"
                elseif subType == DealAgreementTypes.JOINT_WAR then desc = "Joint War"
                else desc = "Agreement" end
            elseif iType == DealItemTypes.RESOURCES then
                local res = GameInfo.Resources[item:GetValueType() or -1]
                local rName = res and Locale.Lookup(res.Name) or "Resource"
                if duration > 0 then desc = amount .. "x " .. rName .. " (" .. duration .. "t)"
                else desc = amount .. "x " .. rName end
            elseif iType == DealItemTypes.FAVOR then
                desc = amount .. " Diplomatic Favor"
            elseif iType == DealItemTypes.CITIES then
                desc = "City"
            end
            if fromID == me then table.insert(weGive, desc)
            else table.insert(theyGive, desc) end
        end
        if #weGive > 0 or #theyGive > 0 then
            termsStr = "\\nActual deal terms:"
            if #weGive > 0 then termsStr = termsStr .. "\\n  We give: " .. table.concat(weGive, ", ") end
            if #theyGive > 0 then termsStr = termsStr .. "\\n  They give: " .. table.concat(theyGive, ", ") end
        end
    else
        result = "REJECTED"
    end
    {_lua_close_diplo_session()}
end
print("OK:" .. result .. "|Trade " .. result:lower() .. " with " .. name .. termsStr)
print("{SENTINEL}")
"""


def build_test_trade(
    other_player_id: int,
    offer_items: list[dict],
    request_items: list[dict],
) -> str:
    """Test a trade deal via EQUALIZE — returns what the AI thinks is fair (InGame).

    Same item format as build_propose_trade. Does NOT commit the deal.
    """
    offer_lua = " ".join(_lua_deal_item("me", item) for item in offer_items)
    request_lua = " ".join(_lua_deal_item("target", item) for item in request_items)

    return f"""
local me = Game.GetLocalPlayer()
local target = {other_player_id}
local pDiplo = Players[me]:GetDiplomacy()
if not pDiplo:HasMet(target) then {_bail("ERR:NOT_MET|Have not met player " + str(other_player_id))} end
if pDiplo:IsAtWarWith(target) then {_bail("ERR:AT_WAR|Cannot trade while at war")} end
local name = Locale.Lookup(PlayerConfigurations[target]:GetCivilizationShortDescription())
print("CIV|" .. target .. "|" .. name:gsub("|","/"))
pcall(function()
    local sid = DiplomacyManager.FindOpenSessionID(me, target)
    if sid and sid >= 0 then DiplomacyManager.CloseSession(sid) end
end)
DiplomacyManager.RequestSession(me, target, "MAKE_DEAL")
DealManager.ClearWorkingDeal(DealDirection.OUTGOING, me, target)
local deal = DealManager.GetWorkingDeal(DealDirection.OUTGOING, me, target)
if not deal then {_bail("ERR:NO_DEAL_OBJECT|Failed to get working deal")} end
{offer_lua}
{request_lua}
print("PROPOSED_ITEMS")
for item in deal:Items() do
    local fromTag = item:GetFromPlayerID() == me and "US" or "THEM"
    local itype = item:GetType()
    local typeName = "UNKNOWN"
    local ok3, vid = pcall(function() return item:GetValueTypeID() end); vid = (ok3 and vid) and tostring(vid) or ""
    local ok4, sid2 = pcall(function() return item:GetSubTypeID() end); sid2 = (ok4 and sid2) and tostring(sid2) or ""
    local amount = item:GetAmount() or 0
    local duration = item:GetDuration() or 0
    if itype == DealItemTypes.GOLD then typeName = "GOLD"
    elseif itype == DealItemTypes.RESOURCES then typeName = "RESOURCE"
    elseif itype == DealItemTypes.AGREEMENTS then typeName = "AGREEMENT"
    elseif itype == DealItemTypes.FAVOR then typeName = "FAVOR"
    elseif itype == DealItemTypes.CITIES then typeName = "CITY"
    elseif itype == DealItemTypes.GREATWORK then typeName = "GREAT_WORK"
    end
    print("ITEM|" .. fromTag .. "|" .. typeName .. "|" .. amount .. "|" .. duration .. "|" .. vid .. "|" .. sid2)
end
DealManager.SendWorkingDeal(DealProposalAction.EQUALIZE, me, target)
local inDeal = DealManager.GetWorkingDeal(DealDirection.INCOMING, me, target)
if inDeal and inDeal:GetItemCount() and inDeal:GetItemCount() > 0 then
    print("AI_COUNTER")
    for item in inDeal:Items() do
        local fromTag = item:GetFromPlayerID() == me and "US" or "THEM"
        local itype = item:GetType()
        local typeName = "UNKNOWN"
        local ok3, vid = pcall(function() return item:GetValueTypeID() end); vid = (ok3 and vid) and tostring(vid) or ""
        local ok4, sid2 = pcall(function() return item:GetSubTypeID() end); sid2 = (ok4 and sid2) and tostring(sid2) or ""
        local amount = item:GetAmount() or 0
        local duration = item:GetDuration() or 0
        if itype == DealItemTypes.GOLD then typeName = "GOLD"
        elseif itype == DealItemTypes.RESOURCES then typeName = "RESOURCE"
        elseif itype == DealItemTypes.AGREEMENTS then typeName = "AGREEMENT"
        elseif itype == DealItemTypes.FAVOR then typeName = "FAVOR"
        elseif itype == DealItemTypes.CITIES then typeName = "CITY"
        elseif itype == DealItemTypes.GREATWORK then typeName = "GREAT_WORK"
        end
        print("ITEM|" .. fromTag .. "|" .. typeName .. "|" .. amount .. "|" .. duration .. "|" .. vid .. "|" .. sid2)
    end
else
    print("AI_COUNTER")
    print("REJECTED")
end
pcall(function()
    local sid = DiplomacyManager.FindOpenSessionID(me, target)
    if sid and sid >= 0 then DiplomacyManager.CloseSession(sid) end
end)
print("{SENTINEL}")
"""


def parse_test_trade_response(lines: list[str]) -> TestTradeResult:
    """Parse the test trade response."""
    result = TestTradeResult(
        other_player_id=0,
        other_civ_name="",
        proposed=[],
        counter=[],
        rejected=False,
    )
    section = ""
    for line in lines:
        if line.startswith("CIV|"):
            parts = line.split("|")
            if len(parts) >= 3:
                result.other_player_id = int(parts[1])
                result.other_civ_name = parts[2]
        elif line == "PROPOSED_ITEMS":
            section = "proposed"
        elif line == "AI_COUNTER":
            section = "counter"
        elif line == "REJECTED":
            result.rejected = True
        elif line.startswith("ITEM|") and section:
            parts = line.split("|")
            if len(parts) >= 7:
                item = TestTradeItem(
                    side=parts[1],
                    item_type=parts[2],
                    amount=int(parts[3]),
                    duration=int(parts[4]),
                    value_id=parts[5],
                    subtype_id=parts[6],
                )
                if section == "proposed":
                    result.proposed.append(item)
                else:
                    result.counter.append(item)
    return result


def build_form_alliance(other_player_id: int, alliance_type: str) -> str:
    """Form an alliance with another civilization (InGame context).

    alliance_type: MILITARY, RESEARCH, CULTURAL, ECONOMIC, RELIGIOUS
    """
    import json

    alliance_key = json.dumps(f"ALLIANCE_{alliance_type.upper()}", ensure_ascii=False)
    return _native_relationship_lua() + f"""
local me = Game.GetLocalPlayer()
local target = {other_player_id}
local allianceKey = {alliance_key}
local allianceRow = GameInfo.Alliances and GameInfo.Alliances[allianceKey]
if not allianceRow or allianceRow.AllianceType ~= allianceKey or not integer(allianceRow.Index) or allianceRow.Index < 0 then
    {_bail("ERR:UNKNOWN_ALLIANCE_TYPE|Requested native alliance type unavailable; no proposal submitted")}
end
local type_idx = allianceRow.Index
local pDiplo = Players[me]:GetDiplomacy()
if not Players[target] or not Players[target]:IsAlive() then {_bail("ERR:INVALID_PLAYER|Player not found")} end
if not pDiplo:HasMet(target) then {_bail("ERR:NOT_MET|Have not met this civilization")} end
if pDiplo:IsAtWarWith(target) then {_bail("ERR:AT_WAR|Cannot ally while at war")} end
local relationship = nativeRelationship(target, me)
local allied = matchesRelationship(relationship, "DIPLO_STATE_ALLIED", DiplomaticStates and DiplomaticStates.ALLIED)
local friends = matchesRelationship(relationship, "DIPLO_STATE_DECLARED_FRIEND", DiplomaticStates and DiplomaticStates.DECLARED_FRIEND)
if allied == nil or friends == nil then {_bail("ERR:RELATIONSHIP_UNKNOWN|Native relationship unavailable; no proposal submitted")} end
if allied then {_bail("ERR:ALREADY_ALLIED|Already in an alliance")} end
if not friends then {_bail("ERR:NOT_FRIENDS|Must be declared friends first")} end
local hasDiploService = false
pcall(function()
    local civic = GameInfo.Civics["CIVIC_DIPLOMATIC_SERVICE"]
    if civic then hasDiploService = Players[me]:GetCulture():HasCivic(civic.Index) end
end)
if not hasDiploService then {_bail("ERR:NO_CIVIC|Diplomatic Service civic required for alliances")} end
local name = Locale.Lookup(PlayerConfigurations[target]:GetCivilizationShortDescription())
if not DealManager.HasPendingDeal(me, target) then
    DealManager.ClearWorkingDeal(DealDirection.OUTGOING, me, target)
end
local deal = DealManager.GetWorkingDeal(DealDirection.OUTGOING, me, target)
if not deal then {_bail("ERR:NO_DEAL_OBJECT|Failed to get working deal")} end
do local ai_item = deal:AddItemOfType(DealItemTypes.AGREEMENTS, me)
if ai_item then ai_item:SetSubType(DealAgreementTypes.ALLIANCE) pcall(function() ai_item:SetValueType(type_idx) end) end end
DiplomacyManager.RequestSession(me, target, "MAKE_DEAL")
DealManager.SendWorkingDeal(DealProposalAction.PROPOSED, me, target)
local sid = DiplomacyManager.FindOpenSessionID(me, target)
if sid and sid >= 0 then
    local ok, respDeal = pcall(function()
        return DealManager.GetWorkingDeal(DealDirection.INCOMING, me, target)
    end)
    if ok and respDeal then
        local itemCount = 0
        pcall(function() itemCount = respDeal:GetItemCount() or 0 end)
        if itemCount > 0 then
            DealManager.SendWorkingDeal(DealProposalAction.ACCEPTED, me, target)
        end
    end
    {_lua_close_diplo_session()}
end
-- A working deal and same-frame relation are not an acceptance witness.
print("OK:PROPOSED|" .. name)
print("ALLIANCE_SCOPE|" .. me .. "|" .. target .. "|" .. allianceKey)
print("{SENTINEL}")
"""


def build_propose_peace(other_player_id: int) -> str:
    """Propose white peace to a civilization we're at war with (InGame context).

    Session type is "MAKE_PEACE" (not "PROPOSE_PEACE_DEAL" which silently fails).
    After sending the deal, close the session with NEGATIVE+CloseSession loop,
    then ShowIngameUI + Events.HideLeaderScreen() to restore HUD and dismiss 3D leader.
    """
    return f"""
local me = Game.GetLocalPlayer()
local target = {other_player_id}
local pDiplo = Players[me]:GetDiplomacy()
if not pDiplo:IsAtWarWith(target) then {_bail("ERR:NOT_AT_WAR|Not at war with player " + str(other_player_id))} end
local canPeace = pDiplo:CanMakePeaceWith(target)
if not canPeace then {_bail("ERR:CANNOT_MAKE_PEACE|10-turn war cooldown or other restriction")} end
local name = Locale.Lookup(PlayerConfigurations[target]:GetCivilizationShortDescription())
DiplomacyManager.RequestSession(me, target, "MAKE_PEACE")
local sid = DiplomacyManager.FindOpenSessionID(me, target)
if not sid or sid < 0 then {_bail("ERR:NO_SESSION|Failed to open peace deal session")} end
DealManager.ClearWorkingDeal(DealDirection.OUTGOING, me, target)
DealManager.SendWorkingDeal(DealProposalAction.PROPOSED, me, target)
{_lua_close_diplo_session()}
print("OK:PROPOSED|" .. name)
print("{SENTINEL}")
"""


def build_check_war_state(other_player_id: int) -> str:
    """Check if we're still at war with a player (InGame context)."""
    return f"""
local me = Game.GetLocalPlayer()
local atWar = Players[me]:GetDiplomacy():IsAtWarWith({other_player_id})
print(atWar and "AT_WAR" or "AT_PEACE")
print("{SENTINEL}")
"""


def parse_diplomacy_response(lines: list[str]) -> list[CivInfo]:
    civs: dict[int, CivInfo] = {}
    for line in lines:
        if line.startswith("CIV|"):
            parts = line.split("|")
            if len(parts) < 13:
                continue
            pid = int(parts[1])
            total_score = 0  # will sum modifiers below
            civs[pid] = CivInfo(
                player_id=pid,
                civ_name=parts[2],
                leader_name=parts[3],
                has_met=parts[4] == "1",
                is_at_war=parts[5] == "1",
                diplomatic_state=parts[6],
                grievances=int(parts[7]),
                access_level=int(parts[8]),
                has_delegation=parts[9] == "1",
                has_embassy=parts[10] == "1",
                they_have_delegation=parts[11] == "1",
                they_have_embassy=parts[12] == "1",
                modifiers=[],
                available_actions=[],
            )
        elif line.startswith("MOD|"):
            parts = line.split("|")
            if len(parts) >= 4:
                pid = int(parts[1])
                if pid in civs:
                    civs[pid].modifiers.append(
                        DiplomacyModifier(
                            score=int(parts[2]),
                            text=parts[3],
                        )
                    )
                    civs[pid].relationship_score += int(parts[2])
        elif line.startswith("ALLIANCE|"):
            parts = line.split("|")
            if len(parts) >= 3:
                pid = int(parts[1])
                if pid in civs:
                    civs[pid].alliance_type = parts[2]
                    if len(parts) >= 4:
                        try:
                            civs[pid].alliance_level = int(parts[3])
                        except ValueError:
                            pass
        elif line.startswith("MILITARY|"):
            parts = line.split("|")
            if len(parts) >= 4:
                pid = int(parts[1])
                if pid in civs:
                    try:
                        civs[pid].military_strength = int(parts[2])
                        civs[pid]._our_military = int(parts[3])  # type: ignore[attr-defined]
                    except ValueError:
                        pass
        elif line.startswith("ECITY|"):
            parts = line.split("|")
            if len(parts) >= 9:
                pid = int(parts[1])
                if pid in civs:
                    xy = parts[3].split(",")
                    try:
                        vc = VisibleCity(
                            name=parts[2],
                            x=int(xy[0]),
                            y=int(xy[1]),
                            population=int(parts[4]),
                            loyalty=float(parts[5]),
                            loyalty_per_turn=float(parts[6]),
                            has_walls=int(parts[7]) > 0,
                            defense_strength=int(parts[8]),
                        )
                        civs[pid].visible_cities.append(vc)
                    except (ValueError, IndexError):
                        pass
        elif line.startswith("CIVCITIES|"):
            parts = line.split("|")
            if len(parts) >= 3:
                pid = int(parts[1])
                if pid in civs:
                    civs[pid].num_cities = int(parts[2])
        elif line.startswith("ACTIONS|"):
            parts = line.split("|")
            if len(parts) >= 3:
                pid = int(parts[1])
                if pid in civs:
                    civs[pid].available_actions = parts[2].split(",")
        elif line.startswith("AGENDA|"):
            parts = line.split("|")
            if len(parts) >= 5:
                pid = int(parts[1])
                if pid in civs:
                    civs[pid].agendas.append(
                        AgendaInfo(
                            category=parts[2],
                            name=parts[3],
                            description=parts[4],
                        )
                    )
        elif line.startswith("PACT|"):
            parts = line.split("|")
            if len(parts) == 3:
                # PACT|pid|DEFENSIVE — pact between us and pid
                pid = int(parts[1])
                if pid in civs:
                    # Mark that this civ has a defensive pact (with us)
                    pass  # We don't track pacts with us specially
            elif len(parts) == 4:
                # PACT|pid1|pid2|DEFENSIVE — third-party pact
                pid1, pid2 = int(parts[1]), int(parts[2])
                if pid1 in civs:
                    civs[pid1].defensive_pacts.append(pid2)
                if pid2 in civs:
                    civs[pid2].defensive_pacts.append(pid1)
    return list(civs.values())


def parse_diplomacy_sessions(lines: list[str]) -> DiplomacyContext:
    """Keep context certainty separate from the presence of session/deal rows."""
    result = DiplomacyContext(reason="INCOMPLETE_RESPONSE")
    statuses = [line.split("|", 2) for line in lines if line.startswith("CONTEXT|")]
    if "CONTEXT_COMPLETE" not in lines or len(statuses) != 1:
        return result
    row = statuses[0]
    if len(row) != 3 or row[1] not in {"NONE", "DIALOGUE", "INCOMING", "UNKNOWN"}:
        return result
    result.status, result.reason = row[1:]
    sessions: dict[int, DiplomacySession] = {}
    deals: dict[int, PendingDeal] = {}
    try:
        for line in lines:
            parts = line.split("|")
            if parts[0] == "SESSION" and len(parts) == 9:
                pid = int(parts[2])
                sessions[pid] = DiplomacySession(
                    session_id=int(parts[1]), other_player_id=pid,
                    other_civ_name=parts[3], other_leader_name=parts[4], choices=[],
                    dialogue_text=parts[5], reason_text=parts[6], buttons=parts[7],
                    is_at_war=parts[8] == "1",
                )
            elif parts[0] == "DEAL" and len(parts) == 4:
                pid = int(parts[1])
                deals[pid] = PendingDeal(pid, parts[2], parts[3])
            elif parts[0] == "ITEM" and len(parts) == 7:
                pid = int(parts[1])
                if pid not in deals or pid not in sessions:
                    return DiplomacyContext(reason="MALFORMED_ITEMS")
                is_from_us = parts[2] == "US"
                item = DealItem(
                    from_player_id=-1 if is_from_us else pid,
                    from_player_name="Us" if is_from_us else deals[pid].other_player_name,
                    item_type=parts[3], name=parts[4], amount=int(parts[5]),
                    duration=int(parts[6]), is_from_us=is_from_us,
                )
                (deals[pid].items_from_us if is_from_us else deals[pid].items_from_them).append(item)
                entry = f"{'You offer' if is_from_us else 'They offer'}: {item.name}"
                if item.amount > 0: entry += f" x{item.amount}"
                if item.duration > 0: entry += f" ({item.duration} turns)"
                summary = sessions[pid].deal_summary
                sessions[pid].deal_summary = f"{summary}; {entry}" if summary else entry
            elif parts[0] in {"SESSION", "DEAL", "ITEM"}:
                return DiplomacyContext(reason="MALFORMED_RESPONSE")
    except (ValueError, IndexError):
        return DiplomacyContext(reason="MALFORMED_RESPONSE")
    if (result.status == "NONE" and (sessions or deals)) or (
        result.status in {"DIALOGUE", "INCOMING"} and len(sessions) != 1
    ) or (result.status == "INCOMING" and (
        len(deals) != 1 or set(deals) != set(sessions)
        or not any(d.items_from_us or d.items_from_them for d in deals.values())
    )) or (result.status != "INCOMING" and deals):
        return DiplomacyContext(reason="INCONSISTENT_RESPONSE")
    result.sessions = list(sessions.values())
    result.deals = list(deals.values())
    if result.status == "UNKNOWN":
        for session in result.sessions:
            session.dialogue_text = session.reason_text = session.buttons = ""
    return result


def parse_pending_deals_response(lines: list[str]) -> DiplomacyContext:
    return parse_diplomacy_sessions(lines)
