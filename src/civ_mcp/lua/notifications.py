"""Notifications domain — Lua builders and parsers."""

from __future__ import annotations

import json
import uuid

from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.lua.models import GameNotification
from civ_mcp.lua.turn_readiness import LUA_TURN_READINESS


# TurnProgress owns this identity; queries emit it beside the facts they qualify.
LUA_TURN_WORLD = r"""
local function turnWorld()
    local ok, value = pcall(function()
        local me = Game.GetLocalPlayer()
        local civ = PlayerConfigurations[me]:GetCivilizationTypeName()
        local seed = GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
        local turn = Game.GetCurrentGameTurn()
        local function integer(n) return type(n)=="number" and n==n and math.abs(n)<math.huge and n==math.floor(n) end
        if not integer(me) or me<0 or me>63 or type(civ)~="string" or not civ:match("^CIVILIZATION_[A-Z0-9_]+$")
            or not integer(seed) or not integer(turn) or turn<0 then return nil end
        return civ.."|"..string.format("%.0f",seed).."|"..me.."|"..turn
    end)
    if ok then return value end
end
local function printTurnWorld(before)
    local after = turnWorld()
    if before then print("TURN_WORLD_SEEN|"..before) end
    if after then print("TURN_WORLD_SEEN|"..after) end
    print("TURN_WORLD|"..(before and after==before and after or "UNKNOWN"))
end
"""

def build_end_turn_blocking_query() -> str:
    """Check for ALL EndTurnBlocking notifications (InGame context).

    Iterates every notification and emits a BLOCKING| line for each unique
    blocking type found.  This lets the caller see everything that needs
    resolving in a single round-trip instead of peeling one blocker at a time.
    """
    return """
local me = Game.GetLocalPlayer()
local list = NotificationManager.GetList(me)
local seen = {}
local found = 0
if list then
    for _, nid in ipairs(list) do
        local entry = NotificationManager.Find(me, nid)
        if entry and not entry:IsDismissed() then
            local bt = entry:GetEndTurnBlocking()
            if bt and bt ~= 0 then
                local typeName = "UNKNOWN"
                for k, v in pairs(EndTurnBlockingTypes) do
                    if v == bt then typeName = k; break end
                end
                if not seen[typeName] then
                    seen[typeName] = true
                    local msg = (entry:GetMessage() or ""):gsub("|", "/")
                    print("BLOCKING|" .. typeName .. "|" .. msg)
                    found = found + 1
                end
            end
        end
    end
end
if found == 0 then print("NONE") end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def build_end_turn(expected_turn: int | None = None, expected_world: tuple[str, int, int] | None = None,
                   *, request_token: str | None = None) -> str:
    """Final native guard and submission share one InGame block.

    NOT_SUBMITTED is emitted only before RequestAction. Errors/response loss
    after entry remain unknown; no native unready action is ever requested.
    """
    token = json.dumps(request_token or uuid.uuid4().hex)
    expected = "Game.GetCurrentGameTurn()" if expected_turn is None else str(int(expected_turn))
    world = "nil" if expected_world is None else repr("|".join(map(str, expected_world)))
    return LUA_TURN_WORLD + LUA_TURN_READINESS + """
local expected = EXPECTED_TURN
local expectedWorld = EXPECTED_WORLD
local function scopeReason()
    local world = turnWorld()
    if not expectedWorld or not world then return "WORLD_UNKNOWN" end
    local expectedScope = expectedWorld.."|"..expected
    if world ~= expectedScope then
        if world:match("^(.*)|[^|]+$") ~= expectedWorld then return "WORLD_CHANGED" end
        return "TURN_CHANGED"
    end
end
local me = Game.GetLocalPlayer()
local p = Players[me]
local function stop(reason)
    print("END_TURN|NOT_SUBMITTED|" .. reason)
end
local ok, reason = pcall(function()
    local mismatch = scopeReason()
    if mismatch then return mismatch end
    if not p or p:IsTurnActive() ~= true or p:IsTurnActiveComplete() ~= true then return "NOT_READY" end
    local processing, sent, unready, allowed = UI.IsProcessingMessages(), UI.HasSentTurnComplete(), p:CanUnreadyTurn(), UI.CanEndTurn()
    if type(processing) ~= "boolean" or type(sent) ~= "boolean" or type(unready) ~= "boolean" or type(allowed) ~= "boolean" then return "UNKNOWN" end
    if sent or unready then return "ALREADY_SENT" end
    if processing then return "UI_PROCESSING" end
    local blocker = NotificationManager.GetFirstEndTurnBlocking(me)
    if blocker == nil or EndTurnBlockingTypes.NO_ENDTURN_BLOCKING == nil then return "UNKNOWN" end
    if blocker ~= EndTurnBlockingTypes.NO_ENDTURN_BLOCKING then return "BLOCKED" end
    if not allowed then return "CANNOT_END_TURN" end
    local localReadiness=localTurnReadiness(me)
    if localReadiness.reason~="NONE" then
        for _,detail in ipairs(localReadiness.details) do print("END_TURN_DETAIL|"..detail) end
        return localReadiness.reason
    end
    local mode = UI.GetInterfaceMode()
    if type(mode) ~= "number" or type(InterfaceModeTypes.SELECTION) ~= "number" then return "UNKNOWN" end
    return nil
end)
if not ok then stop("UNKNOWN"); return end
if reason then stop(reason); return end
reason = scopeReason()
if reason then stop(reason); return end
-- Match ActionPanel.DoEndTurn's mode recovery only after every guard passes.
if UI.GetInterfaceMode() ~= InterfaceModeTypes.SELECTION then
    UI.SetInterfaceMode(InterfaceModeTypes.SELECTION)
end
reason = scopeReason()
if reason then stop(reason); return end
-- A same-turn local reopening needs this request's complete native sequence.
local function retireAttempt(r)
    if r and r.handlers then
        for name,handler in pairs(r.handlers) do pcall(function() Events[name].Remove(handler) end) end
        r.handlers={}
    end
end
local function integer(value)
    return type(value)=="number" and value==value and math.abs(value)<math.huge and value==math.floor(value)
end
retireAttempt(__civmcpTurnAttempt)
local attempt={schema=2,token=REQUEST_TOKEN,scope=expectedWorld.."|"..expected,owner=me,turn=expected,
    stage="ENTERED",handlers={},capable=true}
__civmcpTurnAttempt=attempt
local readable=pcall(function()
    attempt.op=GameInfo.UnitOperations.UNITOPERATION_AUTOMATE_EXPLORE.Hash
    attempt.none=EndTurnBlockingTypes.NO_ENDTURN_BLOCKING
    attempt.blocker=EndTurnBlockingTypes.ENDTURN_BLOCKING_UNITS
    assert(integer(attempt.op) and integer(attempt.none) and integer(attempt.blocker) and attempt.none~=attempt.blocker)
end)
attempt.capable=readable
for _,name in ipairs({"LocalPlayerTurnEnd","UnitOperationDeactivated","UnitOperationsCleared",
    "LocalPlayerTurnBegin","EndTurnBlockingChanged","GameCoreEventPlaybackComplete",
    "LocalPlayerTurnUnready","LocalPlayerChanged"}) do
    local event=Events and Events[name]
    local function capture(a,b,c)
        if __civmcpTurnAttempt~=attempt or attempt.entered~=true or attempt.invalid then return end
        local stage=attempt.stage
        local scope=turnWorld()
        if not scope then
            -- A periodic playback with unavailable identity does not erase an
            -- already bound cycle. Unattributable lifecycle changes stay unknown.
            if name=="LocalPlayerTurnEnd" or name=="LocalPlayerTurnBegin"
                or name=="LocalPlayerTurnUnready" or name=="LocalPlayerChanged"
                or (name=="EndTurnBlockingChanged" and (stage=="BLOCKED" or stage=="SETTLED")) then
                attempt.uncertain=true
            end
            return
        end
        if scope~=attempt.scope then attempt.invalid=true;retireAttempt(attempt);return end
        if name=="LocalPlayerTurnUnready" or name=="LocalPlayerChanged" then attempt.invalid=true
        elseif name=="LocalPlayerTurnEnd" then
            if stage=="ENTERED" then attempt.stage="ENDED" else attempt.invalid=true end
        elseif name=="UnitOperationDeactivated" and stage=="ENDED" then
            if a==attempt.owner and integer(b) and b>=0 and c==attempt.op then
                attempt.unit=b;attempt.stage="DEACTIVATED"
            end
        elseif name=="UnitOperationsCleared" and stage=="DEACTIVATED" then
            if a==attempt.owner and b==attempt.unit then attempt.stage="CLEARED" end
        elseif name=="LocalPlayerTurnBegin" then
            if stage=="CLEARED" then attempt.stage="REOPENED" else attempt.invalid=true end
        elseif name=="EndTurnBlockingChanged" then
            if stage=="REOPENED" and a==attempt.none and b==attempt.blocker then attempt.stage="BLOCKED"
            elseif stage=="BLOCKED" or stage=="SETTLED" then attempt.invalid=true end
        elseif name=="GameCoreEventPlaybackComplete" and stage=="BLOCKED" then attempt.stage="SETTLED" end
        if attempt.invalid then retireAttempt(attempt) end
    end
    if event and type(event.Add)=="function" and type(event.Remove)=="function" then
        local function handler(...)
            if not pcall(capture,...) then attempt.invalid=true;retireAttempt(attempt) end
        end
        attempt.handlers[name]=handler
        if not pcall(event.Add,handler) then attempt.capable=false end
    else attempt.capable=false end
end
if not attempt.capable then retireAttempt(attempt) end
reason=scopeReason()
if reason then retireAttempt(attempt);stop(reason);return end
attempt.entered=true
UI.RequestAction(ActionTypes.ACTION_ENDTURN)
attempt.returned=true
print("END_TURN|SUBMITTED")
print("{SENTINEL}")
""".replace("EXPECTED_TURN", expected).replace("EXPECTED_WORLD", world).replace("REQUEST_TOKEN", token).replace("{SENTINEL}", SENTINEL)


def build_turn_reopened_query(token: str, expected_turn: int, expected_world: tuple[str, int, int]) -> str:
    scope=json.dumps("|".join(map(str, (*expected_world, expected_turn))))
    return LUA_TURN_WORLD + f'''
local r=__civmcpTurnAttempt
local ok,id=pcall(function()
    assert(type(r)=="table" and r.schema==2 and r.token=={json.dumps(token)} and r.scope=={scope}
        and r.owner=={expected_world[2]} and r.turn=={expected_turn})
    local function sameAttemptScope()
        local scope=turnWorld()
        if scope and scope~=r.scope then r.invalid=true end
        return scope==r.scope and not r.invalid
    end
    assert(sameAttemptScope() and not r.uncertain and r.capable==true and r.entered==true and r.returned==true and r.stage=="SETTLED")
    local p=Players[r.owner]
    assert(p:IsTurnActive()==true and p:IsTurnActiveComplete()==true)
    assert(UI.IsProcessingMessages()==false and UI.IsGameCoreBusy()==false
        and UI.HasSentTurnComplete()==false and p:CanUnreadyTurn()==false and UI.CanEndTurn()==true)
    assert(EndTurnBlockingTypes.ENDTURN_BLOCKING_UNITS==r.blocker
        and NotificationManager.GetFirstEndTurnBlocking(r.owner)==r.blocker)
    local unit=UnitManager.GetUnit(r.owner,r.unit%65536)
    local first=p:GetUnits():GetFirstReadyUnit()
    assert(unit and first and unit:GetOwner()==r.owner and unit:GetID()==r.unit and unit:GetX()~=-9999
        and first:GetOwner()==r.owner and first:GetID()==r.unit and unit:IsReadyToMove()==true)
    assert(sameAttemptScope())
    return r.unit
end)
-- A proof reply can be lost. Keep witnesses until native invalidation or the
-- next actual request; an observational query cannot detach this pending owner.
if ok then
    print("TURN_REOPENED|{token}|{expected_turn}|ENDTURN_BLOCKING_UNITS|"..id)
else print("TURN_REOPENED|UNKNOWN") end
print("{SENTINEL}")
'''


def build_notifications_query() -> str:
    """Query NotificationManager for active notifications (InGame context)."""
    return """
local me = Game.GetLocalPlayer()
local nm = NotificationManager
local list = nm.GetList(me)
local total = 0
local emitted = 0
local function notificationText(value)
    return tostring(Locale.Lookup(value or "") or ""):gsub("|", "/"):gsub("[\\r\\n]+", " ")
end
if list then
    total = #list
    for _, nID in ipairs(list) do
        pcall(function()
            local entry = nm.Find(me, nID)
            if entry and not entry:IsDismissed() then
                local typeName = entry:GetTypeName() or "UNKNOWN"
                local msg = notificationText(entry:GetMessage())
                local summary = ""
                pcall(function() summary = notificationText(entry:GetSummary()) end)
                local turn = entry:GetAddedTurn() or -1
                local x, y = -1, -1
                pcall(function() x, y = entry:GetLocation() end)
                if x == nil then x = -1 end
                if y == nil then y = -1 end
                print("NOTIF|" .. typeName .. "|" .. msg .. "|" .. turn .. "|" .. x .. "," .. y .. "|" .. summary)
                emitted = emitted + 1
            end
        end)
    end
end
print("TOTAL|" .. total .. "|" .. emitted)
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def parse_notifications_response(lines: list[str]) -> list[GameNotification]:
    """Parse NOTIF| lines from build_notifications_query."""
    notifs = []
    for line in lines:
        if not line.startswith("NOTIF|"):
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        x_str, y_str = parts[4].split(",")
        type_name = parts[1]
        is_action = any(kw in type_name.upper() for kw in _ACTION_KEYWORDS)
        hint = NOTIFICATION_TOOL_MAP.get(type_name)
        notifs.append(
            GameNotification(
                type_name=type_name,
                message=parts[2],
                turn=int(parts[3]),
                x=int(x_str),
                y=int(y_str),
                is_action_required=is_action,
                resolution_hint=hint,
                summary=parts[5] if len(parts) > 5 else "",
            )
        )
    return notifs


def parse_end_turn_blocking(lines: list[str]) -> list[tuple[str, str]]:
    """Parse blocking query response.

    Returns a list of (blocking_type, message) tuples — one per unique blocker.
    Empty list means no blockers.
    """
    blockers: list[tuple[str, str]] = []
    for line in lines:
        if line == "NONE":
            return []
        if line.startswith("BLOCKING|"):
            parts = line.split("|")
            blocking_type = parts[1] if len(parts) > 1 else "UNKNOWN"
            msg = parts[2] if len(parts) > 2 else ""
            blockers.append((blocking_type, msg))
    return blockers


NOTIFICATION_TOOL_MAP: dict[str, str] = {
    "NOTIFICATION_CHOOSE_TECH": "set_research(tech_or_civic=..., category='tech')",
    "NOTIFICATION_CHOOSE_CIVIC": "set_research(tech_or_civic=..., category='civic')",
    "NOTIFICATION_CHOOSE_CITY_PRODUCTION": "set_city_production(city_id=..., item_type=..., item_name=...)",
    "NOTIFICATION_FILL_CIVIC_SLOT": "get_policies() then set_policies(assignments='...')",
    "NOTIFICATION_CONSIDER_GOVERNMENT_CHANGE": "get_policies() then set_policies()",
    "NOTIFICATION_CHOOSE_PANTHEON": "get_pantheon_beliefs() then choose_pantheon(belief_type=...)",
    "NOTIFICATION_CHOOSE_RELIGION": "get_religion_beliefs() then found_religion(religion_type=..., follower_belief=..., founder_belief=...)",
    "NOTIFICATION_CHOOSE_BELIEF": "get_religion_beliefs() then found_religion(religion_type=..., follower_belief=..., founder_belief=...)",
    "NOTIFICATION_DIPLOMACY_SESSION": "get_pending_diplomacy() then respond_to_diplomacy()",
    "NOTIFICATION_UNIT_PROMOTION_AVAILABLE": "get_unit_promotions(unit_id=...) then promote_unit()",
    "NOTIFICATION_CLAIM_GREAT_PERSON": "get_great_people() then recruit_great_person(individual_id=...) or patronize_great_person(individual_id=...) or reject_great_person(individual_id=...)",
    "NOTIFICATION_GIVE_INFLUENCE_TOKEN": "get_city_states() then send_envoy(player_id=...)",
    "NOTIFICATION_GOVERNOR_APPOINTMENT_AVAILABLE": "get_governors() then appoint_governor()",
    "NOTIFICATION_GOVERNOR_PROMOTION_AVAILABLE": "get_governors() then appoint_governor()",
    "NOTIFICATION_COMMEMORATION_AVAILABLE": "get_dedications() then choose_dedication(dedication_index=...)",
    "NOTIFICATION_WORLD_CONGRESS_BLOCKING": "get_world_congress() then queue_wc_votes()",
    "NOTIFICATION_WORLD_CONGRESS_RESULTS": "get_world_congress() (review results)",
    "NOTIFICATION_WORLD_CONGRESS_SPECIAL_SESSION_BLOCKING": "get_world_congress() then queue_wc_votes()",
    "NOTIFICATION_COMMAND_UNITS": "Units have moves remaining — move them or use skip_remaining_units()",
}


_ACTION_KEYWORDS = (
    "CHOOSE",
    "FILL",
    "CONSIDER",
    "GOVERNOR",
    "PANTHEON",
    "PROMOTION",
    "CLAIM",
    "INFLUENCE_TOKEN",
    "COMMEMORATION",
    "WORLD_CONGRESS",
    "ESCAPE",
)


BLOCKING_TOOL_MAP: dict[str, str] = {
    "ENDTURN_BLOCKING_GOVERNOR_APPOINTMENT": "Use get_governors() then appoint_governor()",
    "ENDTURN_BLOCKING_UNIT_PROMOTION": "Use get_unit_promotions(unit_id=...) then promote_unit()",
    "ENDTURN_BLOCKING_FILL_CIVIC_SLOT": "Use get_policies() then set_policies()",
    "ENDTURN_BLOCKING_PRODUCTION": "Use set_city_production()",
    "ENDTURN_BLOCKING_RESEARCH": "Use set_research()",
    "ENDTURN_BLOCKING_CIVIC": "Use set_research(category='civic')",
    "ENDTURN_BLOCKING_UNITS": "Move or skip remaining units",
    "ENDTURN_BLOCKING_PANTHEON": "Use get_pantheon_beliefs() then choose_pantheon(belief_type=...)",
    "ENDTURN_BLOCKING_STACKED_UNITS": "Move units — cannot stack military units",
    "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE": "Consider Changing Governments",
    "ENDTURN_BLOCKING_COMMEMORATION_AVAILABLE": "Use get_dedications() then choose_dedication(dedication_index=...)",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION": "Use get_world_congress() to review resolutions, then queue_wc_votes() and end_turn().",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK": "Use get_world_congress() to review results, then dismiss_popup() to acknowledge them",
    "ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION": "Inspect get_world_congress() and handle the special session explicitly",
    "ENDTURN_BLOCKING_CONSIDER_RAZE_CITY": "Use city_action(city_id=..., action='keep') or 'raze'/'liberate'",
    "ENDTURN_BLOCKING_CONSIDER_DISLOYAL_CITY": "Use city_action(city_id=..., action='keep') or 'reject'",
    "ENDTURN_BLOCKING_GIVE_INFLUENCE_TOKEN": "Use get_city_states() then send_envoy(player_id=...)",
    "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE": "Use get_spies(), then spy_action(action='escape:DISTRICT_CITY_CENTER' or another available district) to choose the route",
}
