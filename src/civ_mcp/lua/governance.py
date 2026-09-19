"""Governance domain — Lua builders and parsers."""

from __future__ import annotations

from civ_mcp.lua._helpers import (
    SENTINEL,
    _LUA_XP_THRESHOLD,
    _bail,
    _bail_lua,
    _int,
    _lua_get_city,
    _lua_require_ruleset,
    _lua_get_unit,
    _lua_get_unit_gamecore,
)
from civ_mcp.lua.models import (
    AppointedGovernor,
    CityStateBonus,
    CityStateInfo,
    CityStateInfluence,
    CityStateQuest,
    DedicationChoice,
    DedicationStatus,
    EnvoyStatus,
    GovernmentStatus,
    GovernorInfo,
    GovernorPromotion,
    GovernorStatus,
    PolicyInfo,
    PolicySlot,
    PromotionOption,
    UnitPromotionStatus,
)


def build_policies_query() -> str:
    """Read current government, policy slots, and available policies (InGame context)."""
    return """
local me = Game.GetLocalPlayer()
local pCulture = Players[me]:GetCulture()
local govIdx = pCulture:GetCurrentGovernment()
local govName = "None"
local govType = "NONE"
if govIdx and govIdx >= 0 then
    local govEntry = GameInfo.Governments[govIdx]
    if govEntry then
        govName = Locale.Lookup(govEntry.Name)
        govType = govEntry.GovernmentType
    end
end
local numSlots = pCulture:GetNumPolicySlots()
print("GOV|" .. govType .. "|" .. govName:gsub("|","/") .. "|" .. numSlots)
local slotNames = {[0]="SLOT_ECONOMIC", [1]="SLOT_MILITARY", [2]="SLOT_DIPLOMATIC", [3]="SLOT_WILDCARD", [4]="SLOT_WILDCARD"}
for s = 0, numSlots - 1 do
    local slotType = slotNames[pCulture:GetSlotType(s)] or ("SLOT_" .. pCulture:GetSlotType(s))
    local policyIdx = pCulture:GetSlotPolicy(s)
    local policyType = "NONE"
    local policyName = "Empty"
    if policyIdx >= 0 then
        local pe = GameInfo.Policies[policyIdx]
        if pe then
            policyType = pe.PolicyType
            policyName = Locale.Lookup(pe.Name)
        end
    end
    print("SLOT|" .. s .. "|" .. slotType .. "|" .. policyType .. "|" .. policyName:gsub("|","/"))
end
for policy in GameInfo.Policies() do
    if pCulture:IsPolicyUnlocked(policy.Index) then
        local canSlot = false
        for s = 0, numSlots - 1 do
            if pCulture:CanSlotPolicy(policy.Index, s) then
                canSlot = true
                break
            end
        end
        if canSlot then
            local slotType = "SLOT_WILDCARD"
            if policy.GovernmentSlotType then slotType = policy.GovernmentSlotType end
            local name = Locale.Lookup(policy.Name)
            local desc = Locale.Lookup(policy.Description):gsub("|", "/"):gsub("\\n", " ")
            print("AVAIL|" .. policy.PolicyType .. "|" .. name:gsub("|","/") .. "|" .. desc .. "|" .. slotType)
        end
    end
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def build_set_policies(assignments: dict[int, str]) -> str:
    """Set policy cards in government slots (InGame context).

    assignments maps slot_index -> policy_type string.
    Slots not listed keep their current policy. Use "NONE" to explicitly clear a slot.
    Three-step: pre-checks (before UNLOCK), UNLOCK_POLICIES, then RequestPolicyChanges.

    Pre-checks run before UNLOCK_POLICIES because CanSlotPolicy is reliable there.
    It becomes stale same-frame after UNLOCK_POLICIES in the same Lua string.
    """
    pre_checks = []  # policy lookup + CanSlotPolicy + type check — BEFORE UNLOCK_POLICIES
    add_entries = []  # addList[slot] = hash — AFTER UNLOCK_POLICIES
    clear_entries = []  # clearList entries — only slots we're touching

    for slot_idx, policy_type in assignments.items():
        # Always clear the slot we're about to reassign (or explicitly clearing)
        clear_entries.append(f"table.insert(clearList, {slot_idx})")

        if policy_type.upper() == "NONE":
            # Explicit clear — add to clearList but skip addList/pre-checks
            continue

        pre_checks.append(
            f'local pe_{slot_idx} = GameInfo.Policies["{policy_type}"]; '
            f"if pe_{slot_idx} == nil then {_bail(f'ERR:POLICY_NOT_FOUND|{policy_type}')} end; "
            # CanSlotPolicy pre-check (reliable before UNLOCK_POLICIES).
            # CanSlotPolicy returns false when the policy is already in that exact slot,
            # so we also check alreadyThere to avoid false positives on replace-in-place.
            f"local canSlot_{slot_idx} = pCulture:CanSlotPolicy(pe_{slot_idx}.Index, {slot_idx}); "
            f"local alreadyThere_{slot_idx} = (pCulture:GetSlotPolicy({slot_idx}) == pe_{slot_idx}.Index); "
            f"if not canSlot_{slot_idx} and not alreadyThere_{slot_idx} then "
            f'local pType_{slot_idx} = pe_{slot_idx}.GovernmentSlotType or "unknown"; '
            f"local st_{slot_idx} = pCulture:GetSlotType({slot_idx}); "
            f'local sName_{slot_idx} = slotNames[st_{slot_idx}] or ("type_" .. st_{slot_idx}); '
            f"{_bail_lua(f''' "ERR:CANNOT_SLOT|{policy_type} (" .. pType_{slot_idx} .. ") rejected for slot {slot_idx} (" .. sName_{slot_idx} .. ")" ''')} end; "
            # Belt-and-suspenders type string check, now covers Economic/Military/Diplomatic (sType < 3)
            f"local sType_{slot_idx} = pCulture:GetSlotType({slot_idx}); "
            f"local pSlot_{slot_idx} = slotTypeMap[pe_{slot_idx}.GovernmentSlotType] or -1; "
            f"if sType_{slot_idx} < 3 and pSlot_{slot_idx} ~= sType_{slot_idx} "
            f"  and pe_{slot_idx}.GovernmentSlotType ~= 'SLOT_WILDCARD' then "
            f'local sName = slotNames[sType_{slot_idx}] or "unknown"; '
            f'local pType = pe_{slot_idx}.GovernmentSlotType or "unknown"; '
            f"{_bail_lua(f''' "ERR:SLOT_MISMATCH|{policy_type} (" .. pType .. ") cannot go in slot {slot_idx} (" .. sName .. ")" ''')} end"
        )
        add_entries.append(f"addList[{slot_idx}] = pe_{slot_idx}.Hash")

    pre_lua = "; ".join(pre_checks)
    add_lua = "; ".join(add_entries)
    clear_lua = "; ".join(clear_entries)

    return f"""
local me = Game.GetLocalPlayer()
local pCulture = Players[me]:GetCulture()
local numSlots = pCulture:GetNumPolicySlots()
if numSlots <= 0 then {_bail("ERR:NO_GOVERNMENT|No government selected")} end
local slotNames = {{[0]="Economic", [1]="Military", [2]="Diplomatic", [3]="Wildcard", [4]="Wildcard"}}
local slotTypeMap = {{SLOT_ECONOMIC=0, SLOT_MILITARY=1, SLOT_DIPLOMATIC=2, SLOT_WILDCARD=3, SLOT_GREAT_PERSON=4}}
{pre_lua}
UI.RequestPlayerOperation(me, PlayerOperations.UNLOCK_POLICIES, {{}})
local clearList = {{}}
{clear_lua}
local addList = {{}}
{add_lua}
pCulture:RequestPolicyChanges(clearList, addList)
print("OK:POLICIES_SET|Policies updated. Use get_policies to verify.")
print("{SENTINEL}")
"""


def build_governors_query() -> str:
    """Read governor status, appointed governors, and available types (InGame context)."""
    return """
local me = Game.GetLocalPlayer()
{GOVERNOR_RULESET}
-- Governors were added by Rise and Fall.  The base ruleset can expose a
-- partial player API, so verify both the database and runtime object before
-- reading it rather than allowing a Lua method error to escape.
local pGovs = nil
local gotGovs = pcall(function() pGovs = Players[me]:GetGovernors() end)
if not gotGovs or pGovs == nil or GameInfo.Governors == nil or GameInfo.Governors["GOVERNOR_THE_EDUCATOR"] == nil then {NO_GOVERNORS} end
local pts = pGovs:GetGovernorPoints()
local spent = pGovs:GetGovernorPointsSpent()
local canAppoint = pGovs:CanAppoint() and "1" or "0"
print("STATUS|" .. pts .. "|" .. spent .. "|" .. canAppoint)
local appointedTypes = {}
for row in GameInfo.Governors() do
    if row.TransitionStrength and row.TransitionStrength > 0 and pGovs:HasGovernor(row.Hash) then
        appointedTypes[row.GovernorType] = true
        local g = pGovs:GetGovernor(row.Hash)
        local gName = Locale.Lookup(row.Name)
        local gTitle = Locale.Lookup(row.Title)
        local cityID = -1
        local cityName = "Unassigned"
        local established = "0"
        local turnsLeft = 0
        local assignedCity = g:GetAssignedCity()
        if assignedCity then
            cityID = assignedCity:GetID()
            cityName = Locale.Lookup(assignedCity:GetName())
            established = g:IsEstablished() and "1" or "0"
            if not g:IsEstablished() then turnsLeft = g:GetTurnsToEstablish() end
        end
        print("APPOINTED|" .. row.GovernorType .. "|" .. gName:gsub("|","/") .. "|" .. gTitle:gsub("|","/") .. "|" .. cityID .. "|" .. cityName:gsub("|","/") .. "|" .. established .. "|" .. turnsLeft)
        for promo in GameInfo.GovernorPromotionSets() do
            if promo.GovernorType == row.GovernorType then
                local promoRow = GameInfo.GovernorPromotions[promo.GovernorPromotion]
                if promoRow and not g:HasPromotion(promoRow.Index) then
                    local pName = Locale.Lookup(promoRow.Name)
                    local pDesc = Locale.Lookup(promoRow.Description):gsub("|", "/"):gsub("\\n", " ")
                    local lvl = promoRow.Level or 0
                    local col = promoRow.Column or 0
                    print("GOV_PROMO|" .. row.GovernorType .. "|" .. promoRow.GovernorPromotionType .. "|" .. pName:gsub("|","/") .. "|" .. pDesc .. "|" .. lvl .. "|" .. col)
                end
            end
        end
    end
end
for gov in GameInfo.Governors() do
    if gov.TransitionStrength and gov.TransitionStrength > 0 and not appointedTypes[gov.GovernorType] then
        local gName = Locale.Lookup(gov.Name)
        local gTitle = Locale.Lookup(gov.Title)
        local gDesc = gov.Description and Locale.Lookup(gov.Description):gsub("|", "/"):gsub("\\n", " ") or ""
        print("AVAILABLE|" .. gov.GovernorType .. "|" .. gName:gsub("|","/") .. "|" .. gTitle:gsub("|","/") .. "|" .. gDesc)
        for promo in GameInfo.GovernorPromotionSets() do
            if promo.GovernorType == gov.GovernorType then
                local promoRow = GameInfo.GovernorPromotions[promo.GovernorPromotion]
                if promoRow then
                    local pName = Locale.Lookup(promoRow.Name)
                    local pDesc = Locale.Lookup(promoRow.Description):gsub("|", "/"):gsub("\\n", " ")
                    local lvl = promoRow.Level or 0
                    local col = promoRow.Column or 0
                    print("GOV_PROMO|" .. gov.GovernorType .. "|" .. promoRow.GovernorPromotionType .. "|" .. pName:gsub("|","/") .. "|" .. pDesc .. "|" .. lvl .. "|" .. col)
                end
            end
        end
    end
end
print("{SENTINEL}")
""".replace(
        "{GOVERNOR_RULESET}",
        _lua_require_ruleset(
            ("RULESET_EXPANSION_1", "RULESET_EXPANSION_2"),
            "ERR:NO_GOVERNORS_IN_RULESET",
        ),
    ).replace("{NO_GOVERNORS}", _bail("ERR:NO_GOVERNORS_IN_RULESET")).replace("{SENTINEL}", SENTINEL)


def build_appoint_governor(governor_type: str) -> str:
    """Appoint a new governor (InGame context)."""
    return f"""
local me = Game.GetLocalPlayer()
{_lua_require_ruleset(("RULESET_EXPANSION_1", "RULESET_EXPANSION_2"), "ERR:NO_GOVERNORS_IN_RULESET")}
local pGovs = nil
local gotGovs = pcall(function() pGovs = Players[me]:GetGovernors() end)
if not gotGovs or pGovs == nil or GameInfo.Governors == nil or GameInfo.Governors["GOVERNOR_THE_EDUCATOR"] == nil then {_bail("ERR:NO_GOVERNORS_IN_RULESET")} end
if not pGovs:CanAppoint() then {_bail("ERR:CANNOT_APPOINT|No governor points available")} end
local gov = GameInfo.Governors["{governor_type}"]
if gov == nil then {_bail(f"ERR:GOVERNOR_NOT_FOUND|{governor_type}")} end
if PlayerOperations.APPOINT_GOVERNOR == nil then {_bail("ERR:API_MISSING|PlayerOperations.APPOINT_GOVERNOR is nil")} end
if PlayerOperations.PARAM_GOVERNOR_TYPE == nil then {_bail("ERR:API_MISSING|PlayerOperations.PARAM_GOVERNOR_TYPE is nil")} end
local prePts = pGovs:GetGovernorPointsSpent()
local params = {{}}
params[PlayerOperations.PARAM_GOVERNOR_TYPE] = gov.Index
UI.RequestPlayerOperation(me, PlayerOperations.APPOINT_GOVERNOR, params)
-- Read back: the governor is appointed once pGovs:HasGovernor() returns true.
local hasGov = pGovs:HasGovernor(gov.Hash)
if hasGov then
    print("OK:APPOINTED|" .. Locale.Lookup(gov.Name) .. " (" .. Locale.Lookup(gov.Title) .. ") (verified)")
else
    print("OK:APPOINT_REQUESTED|" .. Locale.Lookup(gov.Name) .. " — verify with get_governors()")
end
print("{SENTINEL}")
"""


def build_assign_governor(governor_type: str, city_id: int) -> str:
    """Assign a governor to a city (InGame context)."""
    return f"""
local me = Game.GetLocalPlayer()
{_lua_require_ruleset(("RULESET_EXPANSION_1", "RULESET_EXPANSION_2"), "ERR:NO_GOVERNORS_IN_RULESET")}
{_lua_get_city(city_id)}
local pGovs = nil
local gotGovs = pcall(function() pGovs = Players[me]:GetGovernors() end)
if not gotGovs or pGovs == nil or GameInfo.Governors == nil or GameInfo.Governors["GOVERNOR_THE_EDUCATOR"] == nil then {_bail("ERR:NO_GOVERNORS_IN_RULESET")} end
local gov = GameInfo.Governors["{governor_type}"]
if gov == nil then {_bail(f"ERR:GOVERNOR_NOT_FOUND|{governor_type}")} end
if not pGovs:HasGovernor(gov.Hash) then {_bail(f"ERR:NOT_APPOINTED|{governor_type} not appointed")} end
if PlayerOperations.ASSIGN_GOVERNOR == nil then {_bail("ERR:API_MISSING|PlayerOperations.ASSIGN_GOVERNOR is nil")} end
local params = {{}}
params[PlayerOperations.PARAM_GOVERNOR_TYPE] = gov.Index
params[PlayerOperations.PARAM_CITY_DEST] = pCity:GetID()
params[PlayerOperations.PARAM_PLAYER_ONE] = me
UI.RequestPlayerOperation(me, PlayerOperations.ASSIGN_GOVERNOR, params)
-- Read back: assignment is verified by the governor's assigned city id.
local assignedOk = false
local gCheck = nil
local gotG = pcall(function() gCheck = pGovs:GetGovernor(gov.Hash) end)
if gotG and gCheck then
    local aCity = gCheck:GetAssignedCity()
    if aCity and aCity:GetID() == pCity:GetID() then assignedOk = true end
end
if assignedOk then
    print("OK:ASSIGNED|" .. Locale.Lookup(gov.Name) .. " to " .. Locale.Lookup(pCity:GetName()) .. " (verified)")
else
    print("OK:ASSIGN_REQUESTED|" .. Locale.Lookup(gov.Name) .. " to " .. Locale.Lookup(pCity:GetName()) .. " — verify with get_governors()")
end
print("{SENTINEL}")
"""


def build_promote_governor(governor_type: str, promotion_type: str) -> str:
    """Promote a governor with a new ability (InGame context).

    Uses PROMOTE_GOVERNOR operation (NOT APPOINT_GOVERNOR).
    Both governor and promotion use .Index (NOT .Hash).
    Source: GovernorDetailsPanel.lua — SetVoid1(m_GovernorIndex), SetVoid2(kPromotion.Index)
    """
    return f"""
local me = Game.GetLocalPlayer()
{_lua_require_ruleset(("RULESET_EXPANSION_1", "RULESET_EXPANSION_2"), "ERR:NO_GOVERNORS_IN_RULESET")}
local pGovs = nil
local gotGovs = pcall(function() pGovs = Players[me]:GetGovernors() end)
if not gotGovs or pGovs == nil or GameInfo.Governors == nil or GameInfo.Governors["GOVERNOR_THE_EDUCATOR"] == nil then {_bail("ERR:NO_GOVERNORS_IN_RULESET")} end
local gov = GameInfo.Governors["{governor_type}"]
if gov == nil then {_bail(f"ERR:GOVERNOR_NOT_FOUND|{governor_type}")} end
if not pGovs:HasGovernor(gov.Hash) then {_bail(f"ERR:NOT_APPOINTED|{governor_type} not appointed")} end
local promo = GameInfo.GovernorPromotions["{promotion_type}"]
if promo == nil then {_bail(f"ERR:PROMOTION_NOT_FOUND|{promotion_type}")} end
local g = pGovs:GetGovernor(gov.Hash)
-- Check HasPromotion BEFORE CanPromoteGovernor so the agent gets
-- a specific "already earned" error instead of a generic "can't promote"
if g:HasPromotion(promo.Index) then {_bail(f"ERR:ALREADY_PROMOTED|{promotion_type} already earned")} end
local pts = pGovs:GetGovernorPoints() - pGovs:GetGovernorPointsSpent()
if pts <= 0 then {_bail("ERR:CANNOT_PROMOTE|No governor points available (0 remaining)")} end
if not pGovs:CanPromoteGovernor(gov.Hash) then {_bail("ERR:CANNOT_PROMOTE|Governor has no available promotions")} end
-- Check prerequisites (OR-based: need at least one satisfied)
local prereqs = {{}}
for row in GameInfo.GovernorPromotionPrereqs() do
    if row.GovernorPromotionType == "{promotion_type}" then
        table.insert(prereqs, row.PrereqGovernorPromotion)
    end
end
if #prereqs > 0 then
    local anyMet = false
    local names = {{}}
    for _, reqType in ipairs(prereqs) do
        local reqRow = GameInfo.GovernorPromotions[reqType]
        if reqRow and g:HasPromotion(reqRow.Index) then anyMet = true; break end
        if reqRow then table.insert(names, Locale.Lookup(reqRow.Name)) end
    end
    if not anyMet then print("ERR:PREREQ_NOT_MET|{promotion_type} requires one of: " .. table.concat(names, ", ")); print("{SENTINEL}"); return end
end
local params = {{}}
params[PlayerOperations.PARAM_GOVERNOR_TYPE] = gov.Index
params[PlayerOperations.PARAM_GOVERNOR_PROMOTION_TYPE] = promo.Index
UI.RequestPlayerOperation(me, PlayerOperations.PROMOTE_GOVERNOR, params)
-- Read back: the promotion is verified once HasPromotion returns true.
local promoted = false
if g:HasPromotion(promo.Index) then promoted = true end
if promoted then
    print("OK:PROMOTED|" .. Locale.Lookup(gov.Name) .. " with " .. Locale.Lookup(promo.Name) .. " (verified)")
else
    print("OK:PROMOTE_REQUESTED|" .. Locale.Lookup(gov.Name) .. " with " .. Locale.Lookup(promo.Name) .. " — verify with get_governors()")
end
print("{SENTINEL}")
"""


def build_unit_promotions_query(unit_index: int) -> str:
    """List available promotions for a unit (GameCore context)."""
    return f"""
{_lua_get_unit_gamecore(unit_index)}
local x, y = unit:GetX(), unit:GetY()
if x == -9999 then {_bail("ERR:UNIT_CONSUMED")} end
local typeIdx = unit:GetType()
if typeIdx == nil then {_bail("ERR:UNIT_NO_TYPE")} end
local info = GameInfo.Units[typeIdx]
local ut = info and info.UnitType or "UNKNOWN"
local promClass = info and info.PromotionClass or ""
print("UNIT|" .. {unit_index} .. "|" .. (unit:GetID() % 65536) .. "|" .. ut)
local exp = unit:GetExperience()
{_LUA_XP_THRESHOLD}
print("XP|" .. xp .. "|" .. xpNeeded .. "|" .. xpPromoCount)
for promo in GameInfo.UnitPromotions() do
    if promo.PromotionClass == promClass and exp:HasPromotion(promo.Index) then
        print("OWNED|" .. promo.UnitPromotionType)
    end
end
if xp < xpNeeded then
    print("{SENTINEL}")
    return
end
local prereqMap = {{}}
for row in GameInfo.UnitPromotionPrereqs() do
    local pt = row.UnitPromotion
    if not prereqMap[pt] then prereqMap[pt] = {{}} end
    table.insert(prereqMap[pt], row.PrereqUnitPromotion)
end
for promo in GameInfo.UnitPromotions() do
    if promo.PromotionClass == promClass then
        if not exp:HasPromotion(promo.Index) then
            local prereqs = prereqMap[promo.UnitPromotionType]
            local prereqMet = true
            if prereqs and #prereqs > 0 then
                prereqMet = false
                for _, reqType in ipairs(prereqs) do
                    local reqInfo = GameInfo.UnitPromotions[reqType]
                    if reqInfo and exp:HasPromotion(reqInfo.Index) then
                        prereqMet = true
                        break
                    end
                end
            end
            if prereqMet then
                local canPromote = false
                pcall(function() canPromote = exp:CanPromote(promo.Index) end)
                if canPromote then
                    local name = Locale.Lookup(promo.Name)
                    local desc = Locale.Lookup(promo.Description):gsub("|","/"):gsub("\\n"," ")
                    print("PROMO|" .. promo.UnitPromotionType .. "|" .. name:gsub("|","/") .. "|" .. desc)
                end
            end
        end
    end
end
print("{SENTINEL}")
"""


def build_promote_unit(unit_index: int, promotion_type: str) -> str:
    """Apply a promotion to a unit (GameCore context).

    Uses GameCore SetPromotion because InGame RequestCommand(PROMOTE)
    silently fails (persistent bug across Games 1, 4, and 5).
    """
    return f"""
{_lua_get_unit_gamecore(unit_index)}
local x, y = unit:GetX(), unit:GetY()
if x == -9999 then {_bail("ERR:UNIT_CONSUMED")} end
local promo = GameInfo.UnitPromotions["{promotion_type}"]
if promo == nil then {_bail(f"ERR:PROMOTION_NOT_FOUND|{promotion_type}")} end
local exp = unit:GetExperience()
if exp == nil then {_bail("ERR:NO_EXPERIENCE|Unit has no experience object")} end
-- XP-threshold gate: prevent infinite promotions from SetPromotion desync.
local ui = GameInfo.Units[unit:GetType()]
local promClass = ui and ui.PromotionClass or ""
{_LUA_XP_THRESHOLD}
if xp < xpNeeded then {_bail_lua('"ERR:INSUFFICIENT_XP|Have " .. xp .. " XP, need " .. xpNeeded .. " for promotion " .. (xpPromoCount + 1) .. " (have " .. xpPromoCount .. " already)"')} end
local canPromote = false
pcall(function() canPromote = exp:CanPromote(promo.Index) end)
if not canPromote then {_bail("ERR:CANNOT_PROMOTE|Unit cannot receive this promotion (wrong class, missing prereq, or insufficient XP)")} end
if exp:HasPromotion(promo.Index) then {_bail(f"ERR:ALREADY_HAS_PROMOTION|{promotion_type}")} end
exp:SetPromotion(promo.Index)
if not exp:HasPromotion(promo.Index) then {_bail("ERR:PROMOTION_FAILED|SetPromotion did not apply")} end
-- Sync engine state: zero out stored promotions so the engine stops
-- generating ENDTURN_BLOCKING_UNIT_PROMOTION notifications.
-- ChangeStoredPromotions(-1) is insufficient when stored > 1 (e.g. unit
-- earned enough XP for two promotions). Read the current count and zero it.
local stored = 0
pcall(function() stored = exp:GetStoredPromotions() end)
if stored > 0 then
    pcall(function() exp:ChangeStoredPromotions(-stored) end)
end
-- Verify: if stored is still > 0 after zeroing, log it for diagnostics
local storedAfter = 0
pcall(function() storedAfter = exp:GetStoredPromotions() end)
pcall(function() unit:SetDamage(0) end)
local promoName = Locale.Lookup(promo.Name)
print("OK:PROMOTED|" .. promoName .. "|stored:" .. stored .. "->" .. storedAfter)
print("{SENTINEL}")
"""


def build_city_states_query() -> str:
    """List known city-states with decision-grade envoy intelligence.

    The base ``CS|`` row is intentionally backward compatible.  Optional
    child rows carry known competition, the three envoy thresholds, the
    suzerain bonus, active quests, and military-levy availability.  Every
    expansion/UI-only call is isolated behind ``pcall`` so Standard rulesets
    and partially loaded UI states return ``?``/empty sections instead of
    making the whole city-state query fail.
    """
    return """
local me = Game.GetLocalPlayer()
local pInfluence = Players[me]:GetInfluence()
local pDiplo = Players[me]:GetDiplomacy()

local function clean(value)
    if value == nil then return "" end
    local text = tostring(value)
    text = text:gsub("|", "/")
    text = text:gsub("\\r", " ")
    text = text:gsub("\\n", " ")
    return text
end
local function optional(fn)
    local ok, value = pcall(fn)
    if ok and value ~= nil then return value end
    return nil
end
local function field(value)
    if value == nil then return "?" end
    return clean(value)
end
local function boolField(value)
    if value == nil then return "?" end
    return value and "1" or "0"
end
local function lookup(tag)
    if tag == nil or tag == "" then return "" end
    local value = optional(function() return Locale.Lookup(tag) end)
    if value == nil then return "" end
    return clean(value)
end

local tokens = optional(function() return pInfluence:GetTokensToGive() end) or 0
print("TOKENS|" .. tokens)
local csTypeMap = {}
csTypeMap["LEADER_MINOR_CIV_SCIENTIFIC"] = "Scientific"
csTypeMap["LEADER_MINOR_CIV_CULTURAL"] = "Cultural"
csTypeMap["LEADER_MINOR_CIV_MILITARISTIC"] = "Militaristic"
csTypeMap["LEADER_MINOR_CIV_RELIGIOUS"] = "Religious"
csTypeMap["LEADER_MINOR_CIV_TRADE"] = "Trade"
csTypeMap["LEADER_MINOR_CIV_INDUSTRIAL"] = "Industrial"
local bonusTitleTags = {
    [1] = "LOC_MINOR_CIV_SMALL_INFLUENCE_ENVOYS",
    [3] = "LOC_MINOR_CIV_MEDIUM_INFLUENCE_ENVOYS",
    [6] = "LOC_MINOR_CIV_LARGE_INFLUENCE_ENVOYS",
}
local bonusTags = {
    Scientific = {
        [1] = "LOC_MINOR_CIV_SCIENTIFIC_TRAIT_SMALL_INFLUENCE_BONUS",
        [3] = "LOC_MINOR_CIV_SCIENTIFIC_TRAIT_MEDIUM_INFLUENCE_BONUS",
        [6] = "LOC_MINOR_CIV_SCIENTIFIC_TRAIT_LARGE_INFLUENCE_BONUS",
    },
    Cultural = {
        [1] = "LOC_MINOR_CIV_CULTURAL_TRAIT_SMALL_INFLUENCE_BONUS",
        [3] = "LOC_MINOR_CIV_CULTURAL_TRAIT_MEDIUM_INFLUENCE_BONUS",
        [6] = "LOC_MINOR_CIV_CULTURAL_TRAIT_LARGE_INFLUENCE_BONUS",
    },
    Militaristic = {
        [1] = "LOC_MINOR_CIV_MILITARISTIC_TRAIT_SMALL_INFLUENCE_BONUS",
        [3] = "LOC_MINOR_CIV_MILITARISTIC_TRAIT_MEDIUM_INFLUENCE_BONUS",
        [6] = "LOC_MINOR_CIV_MILITARISTIC_TRAIT_LARGE_INFLUENCE_BONUS",
    },
    Religious = {
        [1] = "LOC_MINOR_CIV_RELIGIOUS_TRAIT_SMALL_INFLUENCE_BONUS",
        [3] = "LOC_MINOR_CIV_RELIGIOUS_TRAIT_MEDIUM_INFLUENCE_BONUS",
        [6] = "LOC_MINOR_CIV_RELIGIOUS_TRAIT_LARGE_INFLUENCE_BONUS",
    },
    Trade = {
        [1] = "LOC_MINOR_CIV_TRADE_TRAIT_SMALL_INFLUENCE_BONUS",
        [3] = "LOC_MINOR_CIV_TRADE_TRAIT_MEDIUM_INFLUENCE_BONUS",
        [6] = "LOC_MINOR_CIV_TRADE_TRAIT_LARGE_INFLUENCE_BONUS",
    },
    Industrial = {
        [1] = "LOC_MINOR_CIV_INDUSTRIAL_TRAIT_SMALL_INFLUENCE_BONUS",
        [3] = "LOC_MINOR_CIV_INDUSTRIAL_TRAIT_MEDIUM_INFLUENCE_BONUS",
        [6] = "LOC_MINOR_CIV_INDUSTRIAL_TRAIT_LARGE_INFLUENCE_BONUS",
    },
}
local questsManager = optional(function() return Game.GetQuestsManager() end)

local function bonusText(cityStateID, cityStateType, threshold)
    local title = lookup(bonusTitleTags[threshold])
    local details = lookup(bonusTags[cityStateType] and bonusTags[cityStateType][threshold])
    -- The stock UI helper includes DLC-specific bonus wording.  Prefer it
    -- when the UI context has loaded it, then retain the database-tag
    -- fallback above for headless/partially loaded states.
    local ok, uiTitle, uiDetails = pcall(function()
        if type(GetBonusText) == "function" then
            return GetBonusText(cityStateID, threshold)
        end
        return nil, nil
    end)
    if ok and uiTitle ~= nil and clean(uiTitle) ~= "" then title = clean(uiTitle) end
    if ok and uiDetails ~= nil and clean(uiDetails) ~= "" then details = clean(uiDetails) end
    return title, details
end

local function suzerainBonus(cityStateID)
    local details = ""
    local ok, value = pcall(function()
        if type(GetSuzerainBonusText) == "function" then
            return GetSuzerainBonusText(cityStateID)
        end
        return nil
    end)
    if ok and value ~= nil then details = clean(value) end
    if details ~= "" then return details end
    -- Minimal fallback: resolve the leader trait description directly from
    -- GameInfo, which is available even when the partial screen helper is not.
    pcall(function()
        local cfg = PlayerConfigurations[cityStateID]
        local leaderType = cfg and cfg:GetLeaderTypeName()
        for pair in GameInfo.LeaderTraits() do
            if pair.LeaderType == leaderType then
                local trait = GameInfo.Traits[pair.TraitType]
                if trait and trait.Description then
                    details = lookup(trait.Description)
                    if details ~= "" then break end
                end
            end
        end
    end)
    return details
end

local function printQuests(cityStateID)
    if questsManager == nil or GameInfo.Quests == nil then return end
    pcall(function()
        for questInfo in GameInfo.Quests() do
            if questsManager:HasActiveQuestFromPlayer(me, cityStateID, questInfo.Index) then
                local description = optional(function()
                    return questsManager:GetActiveQuestDescription(me, cityStateID, questInfo.Index)
                end)
                local name = optional(function()
                    return questsManager:GetActiveQuestName(me, cityStateID, questInfo.Index)
                end)
                local reward = optional(function()
                    return questsManager:GetActiveQuestReward(me, cityStateID, questInfo.Index)
                end)
                print("CSQUEST|" .. cityStateID .. "|" .. field(questInfo.QuestType) .. "|" ..
                    clean(name) .. "|" .. clean(description) .. "|" .. clean(reward) .. "|" ..
                    field(questInfo.IconString))
            end
        end
    end)
end

for i = 0, 62 do
    if Players[i] and Players[i]:IsAlive() and Players[i]:IsMajor() == false and Players[i]:IsBarbarian() == false and pDiplo:HasMet(i) then
        local cfg = PlayerConfigurations[i]
        local name = lookup(cfg:GetPlayerName())
        local leaderType = cfg:GetLeaderTypeName() or ""
        local csType = "Unknown"
        local leader = GameInfo.Leaders[leaderType]
        if leader and leader.InheritFrom then
            csType = csTypeMap[leader.InheritFrom] or leader.InheritFrom
        end
        local csInfluence = Players[i]:GetInfluence()
        local envoys = optional(function() return csInfluence:GetTokensReceived(me) end) or 0
        local suzID = optional(function() return csInfluence:GetSuzerain() end) or -1
        local suzName = "None"
        if suzID >= 0 and suzID ~= 63 then
            local sCfg = PlayerConfigurations[suzID]
            if sCfg then suzName = lookup(sCfg:GetCivilizationShortDescription()) end
        end
        local canSend = optional(function() return pInfluence:CanGiveTokensToPlayer(i) end)

        local knownCompetition = true
        -- The official city-state panel exposes this count even when some
        -- competitors have not been met.  It is useful for deciding whether
        -- a visible rival can be overtaken, but its holder remains unknown
        -- until every alive major civilization has been met.
        local leadingEnvoys = optional(function()
            return csInfluence:GetMostTokensReceived()
        end)
        if type(leadingEnvoys) ~= "number" then leadingEnvoys = 0 end
        local knownInfluence = {}
        for pid = 0, 62 do
            if Players[pid] and Players[pid]:IsAlive() and Players[pid]:IsMajor() then
                local visible = pid == me or (pDiplo and optional(function() return pDiplo:HasMet(pid) end))
                if visible then
                    local rivalInfluence = Players[i]:GetInfluence()
                    local rivalEnvoys = optional(function() return rivalInfluence:GetTokensReceived(pid) end)
                    if rivalEnvoys ~= nil then
                        local rivalCfg = PlayerConfigurations[pid]
                        local rivalName = "文明 " .. pid
                        if rivalCfg then
                            rivalName = lookup(rivalCfg:GetCivilizationShortDescription())
                            if rivalName == "" then rivalName = "文明 " .. pid end
                        end
                        table.insert(knownInfluence, {pid=pid, name=rivalName, envoys=rivalEnvoys})
                        if rivalEnvoys > leadingEnvoys then leadingEnvoys = rivalEnvoys end
                    end
                else
                    knownCompetition = false
                end
            end
        end
        local suzNeeded = nil
        if knownCompetition then
            suzNeeded = math.max(3, leadingEnvoys + ((suzID ~= me) and 1 or 0))
        end

        local canLevy = optional(function() return pInfluence:CanLevyMilitary(i) end)
        local levyCost = optional(function() return pInfluence:GetLevyMilitaryCost(i) end)
        local levyLimit = optional(function() return pInfluence:GetLevyTurnLimit() end)
        local levyCounter = optional(function() return csInfluence:GetLevyTurnCounter() end)
        local levyActive = nil
        local levyRemaining = nil
        if type(levyCounter) == "number" then
            levyActive = levyCounter >= 0
            if levyActive and type(levyLimit) == "number" then
                levyRemaining = math.max(0, levyLimit - levyCounter)
            elseif not levyActive then
                levyRemaining = 0
            end
        end

        print("CS|" .. i .. "|" .. clean(name) .. "|" .. csType .. "|" .. envoys .. "|" ..
            suzID .. "|" .. clean(suzName) .. "|" .. boolField(canSend) .. "|" ..
            field(leadingEnvoys) .. "|" .. field(suzNeeded) .. "|" .. boolField(knownCompetition) .. "|" ..
            boolField(canLevy) .. "|" .. field(levyCost) .. "|" .. field(levyLimit) .. "|" ..
            boolField(levyActive) .. "|" .. field(levyRemaining))
        for _, standing in ipairs(knownInfluence) do
            print("CSCOMP|" .. i .. "|" .. standing.pid .. "|" .. clean(standing.name) .. "|" .. standing.envoys)
        end
        for _, threshold in ipairs({1, 3, 6}) do
            local title, details = bonusText(i, csType, threshold)
            print("CSBONUS|" .. i .. "|" .. threshold .. "|" .. clean(title) .. "|" .. clean(details))
        end
        local suzDetails = suzerainBonus(i)
        print("CSSUZ|" .. i .. "|" .. lookup("LOC_CITY_STATES_SUZERAIN_ENVOYS") .. "|" .. clean(suzDetails))
        printQuests(i)
    end
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def build_send_envoy(city_state_player_id: int) -> str:
    """Send an envoy to a city-state (InGame context)."""
    return f"""
local me = Game.GetLocalPlayer()
local pInfluence = Players[me]:GetInfluence()
if pInfluence:GetTokensToGive() <= 0 then {_bail("ERR:NO_ENVOYS|No envoy tokens available")} end
if not pInfluence:CanGiveTokensToPlayer({city_state_player_id}) then
    {_bail(f"ERR:CANNOT_SEND|Cannot send envoy to player {city_state_player_id}")}
end
local params = {{}}
params[PlayerOperations.PARAM_PLAYER_ONE] = {city_state_player_id}
params[PlayerOperations.PARAM_FLAGS] = 0
UI.RequestPlayerOperation(me, PlayerOperations.GIVE_INFLUENCE_TOKEN, params)
-- Read back: envoy count received by that city-state should increase by one.
local cfg = PlayerConfigurations[{city_state_player_id}]
local name = cfg and Locale.Lookup(cfg:GetPlayerName()) or "Unknown"
local csInfl = Players[{city_state_player_id}]:GetInfluence()
local receivedAfter = csInfl:GetTokensReceived(me)
print("OK:ENVOY_SENT|" .. name .. " (received: " .. receivedAfter .. ")")
print("{SENTINEL}")
"""


def build_unit_upgrade_query(unit_index: int) -> str:
    """Check if a unit can upgrade and get cost/target info (InGame context)."""
    return f"""
{_lua_get_unit(unit_index)}
local info = GameInfo.Units[unit:GetType()]
local ut = info and info.UnitType or "UNKNOWN"
local params = {{}}
local canUpgrade, upgradeResult = UnitManager.CanStartCommand(unit, UnitCommandTypes.UPGRADE, params, true)
local upgCol = info.UpgradeUnitCollection
local upgradeType = ""
if upgCol and #upgCol > 0 then upgradeType = upgCol[1].UpgradeUnit or "" end
if not canUpgrade then
    local reasons = ""
    if upgradeResult then
        for _, v in pairs(upgradeResult) do
            if type(v) == "table" then
                for _, reason in pairs(v) do
                    if type(reason) == "string" then
                        local clean = reason:gsub("%[ICON_[^%]]*%]", ""):gsub("%s+", " ")
                        reasons = reasons .. (reasons ~= "" and "; " or "") .. clean
                    end
                end
            end
        end
    end
    local suffix = upgradeType ~= "" and (" -> " .. upgradeType) or ""
    local msg = ut .. suffix .. (reasons ~= "" and (" | " .. reasons) or " | cannot upgrade (missing tech, resources, gold, or no path)")
    {_bail_lua('"ERR:CANNOT_UPGRADE|" .. msg')}
end
if upgradeType == "" then {_bail_lua('"ERR:NO_UPGRADE_PATH|" .. ut .. " has no upgrade"')} end
local upInfo = GameInfo.Units[upgradeType]
local upName = upInfo and Locale.Lookup(upInfo.Name) or upgradeType
local gold = Players[me]:GetTreasury():GetGoldBalance()
local cost = 0
pcall(function() cost = unit:GetUpgradeCost() end)
print("UPGRADE|" .. ut .. "|" .. upgradeType .. "|" .. upName:gsub("|","/") .. "|" .. math.floor(cost) .. "|" .. math.floor(gold))
print("{SENTINEL}")
"""


def build_upgrade_unit(unit_index: int) -> str:
    """Execute unit upgrade (InGame context)."""
    return f"""
{_lua_get_unit(unit_index)}
local info = GameInfo.Units[unit:GetType()]
local ut = info and info.UnitType or "UNKNOWN"
local upgCol = info and info.UpgradeUnitCollection
local upType = (upgCol and #upgCol > 0) and upgCol[1].UpgradeUnit or ""
local params = {{}}
local canUpgrade, upgradeResult = UnitManager.CanStartCommand(unit, UnitCommandTypes.UPGRADE, params, true)
if not canUpgrade then
    local cost = 0
    pcall(function() cost = unit:GetUpgradeCost() end)
    local gold = Players[me]:GetTreasury():GetGoldBalance()
    local detail = ut
    if upType ~= "" then detail = detail .. " -> " .. upType end
    detail = detail .. " | cost:" .. math.floor(cost) .. "g have:" .. math.floor(gold) .. "g"
    if upgradeResult then
        for _, v in pairs(upgradeResult) do
            if type(v) == "table" then
                for _, reason in pairs(v) do
                    if type(reason) == "string" then
                        local clean = reason:gsub("%[ICON_[^%]]*%]", ""):gsub("%s+", " ")
                        detail = detail .. " | " .. clean
                    end
                end
            end
        end
    end
    {_bail_lua('"ERR:CANNOT_UPGRADE|" .. detail')}
end
if upType == "" then upType = "UNKNOWN" end
UnitManager.RequestCommand(unit, UnitCommandTypes.UPGRADE, params)
print("OK:UPGRADED|" .. ut .. " -> " .. upType)
print("{SENTINEL}")
"""


def build_dedications_query() -> str:
    """Read current era age, available dedications, and active ones."""
    return """
local me = Game.GetLocalPlayer()
{DEDICATION_RULESET}
-- Ages and dedications were added by Rise and Fall.  Base Civ VI still has
-- chronological eras, but not the commemoration APIs. Each guard names the
-- exact reason in the bail so a ruleset/capability mismatch (the turn-97
-- session saw RULESET_EXPANSION_2 + dedications capability true, yet this
-- probe bailed with a bare NO_DEDICATIONS) is diagnosable from one call.
local pEras = nil
local gotEras = pcall(function() if Game.GetEras ~= nil then pEras = Game.GetEras() end end)
{DEDICATION_GUARDS}
local age = "Normal"
if pEras:HasHeroicGoldenAge(me) then age = "Heroic"
elseif pEras:HasGoldenAge(me) then age = "Golden"
elseif pEras:HasDarkAge(me) then age = "Dark" end
local era = pEras:GetCurrentEra()
local darkT = pEras:GetPlayerDarkAgeThreshold(me) or 0
local goldT = pEras:GetPlayerGoldenAgeThreshold(me) or 0
local allowed = pEras:GetPlayerNumAllowedCommemorations(me)
local score = pEras:GetPlayerCurrentScore(me)
print("STATUS|" .. age .. "|" .. era .. "|" .. score .. "|" .. darkT .. "|" .. goldT .. "|" .. allowed)
-- Active commemorations
local active = pEras:GetPlayerActiveCommemorations(me)
if active then
    for _, a in ipairs(active) do
        local row = GameInfo.CommemorationTypes[a]
        if row then print("ACTIVE|" .. row.CommemorationType) end
    end
end
-- Available choices
local choices = pEras:GetPlayerCommemorateChoices(me)
if choices then
    for _, idx in ipairs(choices) do
        local row = GameInfo.CommemorationTypes[idx]
        if row then
            local norm = row.NormalAgeBonusDescription and Locale.Lookup(row.NormalAgeBonusDescription) or ""
            local gold = row.GoldenAgeBonusDescription and Locale.Lookup(row.GoldenAgeBonusDescription) or ""
            local dark = row.DarkAgeBonusDescription and Locale.Lookup(row.DarkAgeBonusDescription) or ""
            print("CHOICE|" .. idx .. "|" .. row.CommemorationType .. "|" .. norm .. "|" .. gold .. "|" .. dark)
        end
    end
end
print("{SENTINEL}")
""".replace(
        "{DEDICATION_RULESET}",
        _lua_require_ruleset(
            ("RULESET_EXPANSION_1", "RULESET_EXPANSION_2"),
            "ERR:NO_DEDICATIONS_IN_RULESET",
        ),
    ).replace(
        "{DEDICATION_GUARDS}",
        "\n".join(
            f"if {condition} then {_bail(f'ERR:NO_DEDICATIONS_IN_RULESET|guard={tag}')} end"
            # NOTE: no COMMEMORATION_MONUMENTALITY row guard on purpose — some
            # expansion rulesets ship a different commemoration row set while
            # still reporting a pending choice (ENDTURN_BLOCKING_COMMEMORATION_AVAILABLE),
            # so the probe must enumerate whatever rows GetPlayerCommemorateChoices
            # actually returns instead of bailing on one specific row name.
            for condition, tag in (
                ("not gotEras or pEras == nil", "GetEras"),
                ("pEras.GetPlayerNumAllowedCommemorations == nil", "GetPlayerNumAllowedCommemorations"),
                ("GameInfo.CommemorationTypes == nil", "CommemorationTypes"),
            )
        ),
    ).replace("{SENTINEL}", SENTINEL)


def build_choose_dedication(dedication_index: int) -> str:
    """Select a dedication/commemoration by its index."""
    return f"""
local me = Game.GetLocalPlayer()
{_lua_require_ruleset(("RULESET_EXPANSION_1", "RULESET_EXPANSION_2"), "ERR:NO_DEDICATIONS_IN_RULESET")}
local pEras = nil
local gotEras = pcall(function() if Game.GetEras ~= nil then pEras = Game.GetEras() end end)
if not gotEras or pEras == nil or pEras.GetPlayerNumAllowedCommemorations == nil or GameInfo.CommemorationTypes == nil then {_bail("ERR:NO_DEDICATIONS_IN_RULESET")} end
local allowed = pEras:GetPlayerNumAllowedCommemorations(me)
if allowed <= 0 then {_bail("ERR:NO_DEDICATION_NEEDED|No dedication selection required (already chosen or not available)")} end
local row = GameInfo.CommemorationTypes[{dedication_index}]
if not row then {_bail(f"ERR:INVALID_INDEX|Dedication index {dedication_index} not found")} end
local params = {{}}
params[PlayerOperations.PARAM_COMMEMORATION_TYPE] = {dedication_index}
UI.RequestPlayerOperation(me, PlayerOperations.COMMEMORATE, params)
-- Read back to verify the commemoration actually became active (no false OK).
local activeAfter = pEras:GetPlayerActiveCommemorations(me)
local verified = false
if activeAfter then
    for _, a in ipairs(activeAfter) do
        if a == {dedication_index} then verified = true break end
    end
end
if verified then
    print("OK:DEDICATION_CHOSEN|" .. row.CommemorationType .. " (verified)")
else
    print("OK:DEDICATION_SUBMITTED|" .. row.CommemorationType .. " requested but not yet reflected — re-check with get_dedications")
end
print("{SENTINEL}")
"""


def build_available_governments_query() -> str:
    """List unlocked governments with slot info (InGame context)."""
    return """
local me = Game.GetLocalPlayer()
local pCulture = Players[me]:GetCulture()
local curGov = pCulture:GetCurrentGovernment()
for row in GameInfo.Governments() do
    local unlocked = pCulture:IsGovernmentUnlocked(row.Index)
    if unlocked then
        local isCurrent = (row.Index == curGov)
        local slots = {}
        for slotRow in GameInfo.Government_SlotCounts() do
            if slotRow.GovernmentType == row.GovernmentType then
                for i = 1, slotRow.NumSlots do
                    table.insert(slots, slotRow.GovernmentSlotType)
                end
            end
        end
        local slotStr = table.concat(slots, ",")
        local name = Locale.Lookup(row.Name)
        local bonus = ""
        if row.BonusType then
            local bRow = GameInfo.GovernmentBonuses and GameInfo.GovernmentBonuses[row.BonusType]
            if bRow then bonus = Locale.Lookup(bRow.Description or "") end
        end
        local tag = isCurrent and "CURRENT" or "AVAILABLE"
        print("GOV|" .. row.GovernmentType .. "|" .. row.Index .. "|" .. tag .. "|" .. name .. "|" .. slotStr .. "|" .. bonus)
    end
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def build_change_government(gov_type: str) -> str:
    """Switch government type (InGame context)."""
    return f"""
local me = Game.GetLocalPlayer()
local pCulture = Players[me]:GetCulture()
local row = GameInfo.Governments["{gov_type}"]
if row == nil then {_bail(f"ERR:GOV_NOT_FOUND|{gov_type}")} end
if not pCulture:IsGovernmentUnlocked(row.Index) then {_bail(f"ERR:GOV_LOCKED|{gov_type} is not unlocked")} end
if row.Index == pCulture:GetCurrentGovernment() then {_bail(f"ERR:ALREADY_CURRENT|{gov_type} is already your government")} end
pCulture:SetGovernmentChangeConsidered(true)
pCulture:RequestChangeGovernment(row.Index)
-- Read back: the government is switched once GetCurrentGovernment matches.
local curAfter = pCulture:GetCurrentGovernment()
if curAfter == row.Index then
    print("OK:GOVERNMENT_CHANGED|{gov_type}|" .. Locale.Lookup(row.Name) .. " (verified)")
else
    print("OK:GOVERNMENT_REQUESTED|{gov_type} requested, currently " .. tostring(curAfter) .. " — verify with get_policies()")
end
print("{SENTINEL}")
"""


def parse_policies_response(lines: list[str]) -> GovernmentStatus:
    """Parse GOV|, SLOT|, AVAIL| lines from build_policies_query."""
    gov_name = "None"
    gov_type = "NONE"
    slots: list[PolicySlot] = []
    available: list[PolicyInfo] = []

    for line in lines:
        if line.startswith("GOV|"):
            parts = line.split("|")
            if len(parts) >= 4:
                gov_type = parts[1]
                gov_name = parts[2]
        elif line.startswith("SLOT|"):
            parts = line.split("|")
            if len(parts) >= 5:
                policy_type = None if parts[3] == "NONE" else parts[3]
                policy_name = None if parts[4] == "Empty" else parts[4]
                slots.append(
                    PolicySlot(
                        slot_index=int(parts[1]),
                        slot_type=parts[2],
                        current_policy=policy_type,
                        current_policy_name=policy_name,
                    )
                )
        elif line.startswith("AVAIL|"):
            parts = line.split("|")
            if len(parts) >= 5:
                available.append(
                    PolicyInfo(
                        policy_type=parts[1],
                        name=parts[2],
                        description=parts[3],
                        slot_type=parts[4],
                    )
                )

    return GovernmentStatus(
        government_name=gov_name,
        government_type=gov_type,
        slots=slots,
        available_policies=available,
    )


def parse_governors_response(lines: list[str]) -> GovernorStatus:
    """Parse STATUS|, APPOINTED|, GOV_PROMO|, AVAILABLE| lines from build_governors_query."""
    pts_avail = 0
    pts_spent = 0
    can_appoint = False
    appointed: list[AppointedGovernor] = []
    available: list[GovernorInfo] = []
    # Collect promotions keyed by governor_type, then attach after
    promos_by_gov: dict[str, list[GovernorPromotion]] = {}

    for line in lines:
        if line.startswith("STATUS|"):
            parts = line.split("|")
            if len(parts) >= 4:
                pts_total = int(parts[1])
                pts_spent = int(parts[2])
                pts_avail = pts_total - pts_spent
                can_appoint = parts[3] == "1"
        elif line.startswith("APPOINTED|"):
            parts = line.split("|")
            if len(parts) >= 7:
                appointed.append(
                    AppointedGovernor(
                        governor_type=parts[1],
                        name=parts[2],
                        assigned_city_id=int(parts[4]),
                        assigned_city_name=parts[5],
                        is_established=parts[6] == "1",
                        turns_to_establish=int(parts[7]) if len(parts) >= 8 else 0,
                    )
                )
        elif line.startswith("GOV_PROMO|"):
            parts = line.split("|")
            if len(parts) >= 5:
                gov_type = parts[1]
                promos_by_gov.setdefault(gov_type, []).append(
                    GovernorPromotion(
                        promotion_type=parts[2],
                        name=parts[3],
                        description=parts[4],
                        level=int(parts[5]) if len(parts) > 5 else 0,
                        column=int(parts[6]) if len(parts) > 6 else 0,
                    )
                )
        elif line.startswith("AVAILABLE|"):
            parts = line.split("|")
            if len(parts) >= 4:
                available.append(
                    GovernorInfo(
                        governor_type=parts[1],
                        name=parts[2],
                        title=parts[3],
                        description=parts[4] if len(parts) > 4 else "",
                    )
                )

    # Attach promotions to their governors
    for gov in appointed:
        gov.available_promotions = promos_by_gov.get(gov.governor_type, [])

    # Attach promotions to available governors, split out base ability
    for gov in available:
        all_promos = promos_by_gov.get(gov.governor_type, [])
        for p in all_promos:
            if p.level == 0:
                gov.base_ability = p.name
                gov.base_ability_desc = p.description
            else:
                gov.promotions.append(p)

    return GovernorStatus(
        points_available=pts_avail,
        points_spent=pts_spent,
        can_appoint=can_appoint,
        appointed=appointed,
        available_to_appoint=available,
    )


def parse_unit_promotions_response(lines: list[str]) -> UnitPromotionStatus:
    """Parse UNIT|, XP|, and PROMO| lines from build_unit_promotions_query."""
    unit_id = 0
    unit_index = 0
    unit_type = "UNKNOWN"
    promotions: list[PromotionOption] = []
    xp = 0
    xp_needed = 0
    promotion_count = 0
    owned_promotions: list[str] = []

    for line in lines:
        if line.startswith("ERR:"):
            raise ValueError(line[4:])
        if line.startswith("UNIT|"):
            parts = line.split("|")
            if len(parts) >= 4:
                unit_id = int(parts[1])
                unit_index = int(parts[2])
                unit_type = parts[3]
        elif line.startswith("XP|"):
            parts = line.split("|")
            if len(parts) >= 4:
                xp = _int(parts[1])
                xp_needed = _int(parts[2])
                promotion_count = int(parts[3])
        elif line.startswith("PROMO|"):
            parts = line.split("|")
            if len(parts) >= 4:
                promotions.append(
                    PromotionOption(
                        promotion_type=parts[1],
                        name=parts[2],
                        description=parts[3],
                    )
                )
        elif line.startswith("OWNED|"):
            promotion_type = line.split("|", 1)[1]
            if promotion_type:
                owned_promotions.append(promotion_type)

    return UnitPromotionStatus(
        unit_id=unit_id,
        unit_index=unit_index,
        unit_type=unit_type,
        promotions=promotions,
        xp=xp,
        xp_needed=xp_needed,
        promotion_count=promotion_count,
        owned_promotions=owned_promotions,
    )


def _optional_int(value: str) -> int | None:
    """Parse a nullable numeric field emitted by a Lua query."""
    if value.strip().lower() in {"", "?", "nil", "none"}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: str) -> bool | None:
    """Parse a nullable boolean field emitted as 1/0 by Lua."""
    normalized = value.strip().lower()
    if normalized in {"", "?", "nil", "none"}:
        return None
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    return None


def parse_city_states_response(lines: list[str]) -> EnvoyStatus:
    """Parse the backward-compatible city-state rows and optional child rows.

    Older Lua payloads contain only eight ``CS|`` fields.  Newer payloads add
    nullable competition and levy fields plus ``CSCOMP|``, ``CSBONUS|``,
    ``CSSUZ|``, and ``CSQUEST|`` rows.  Malformed or unavailable optional
    values are ignored or represented as ``None`` so one missing ruleset API
    cannot discard the other city-state data.
    """
    tokens = 0
    city_states: dict[int, CityStateInfo] = {}

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("TOKENS|"):
            parsed_tokens = _optional_int(line.split("|", 1)[1])
            if parsed_tokens is not None:
                tokens = parsed_tokens
            continue

        if line.startswith("CS|"):
            parts = line.split("|")
            if len(parts) < 8:
                continue
            player_id = _optional_int(parts[1])
            envoys_sent = _optional_int(parts[4])
            suzerain_id = _optional_int(parts[5])
            if player_id is None or envoys_sent is None or suzerain_id is None:
                continue
            competition_complete = (
                _optional_bool(parts[10]) if len(parts) > 10 else True
            )
            if competition_complete is None:
                competition_complete = True
            city_states[player_id] = CityStateInfo(
                player_id=player_id,
                name=parts[2],
                city_state_type=parts[3],
                envoys_sent=envoys_sent,
                suzerain_id=suzerain_id,
                suzerain_name=parts[6],
                can_send_envoy=parts[7] == "1",
                competition_complete=competition_complete,
                leading_envoys=(
                    _optional_int(parts[8]) if len(parts) > 8 else None
                ),
                suzerain_tokens_needed=(
                    _optional_int(parts[9]) if len(parts) > 9 else None
                ),
                can_levy_military=(
                    _optional_bool(parts[11]) if len(parts) > 11 else None
                ),
                levy_cost=_optional_int(parts[12]) if len(parts) > 12 else None,
                levy_turn_limit=(
                    _optional_int(parts[13]) if len(parts) > 13 else None
                ),
                levy_active=(
                    _optional_bool(parts[14]) if len(parts) > 14 else None
                ),
                levy_turns_remaining=(
                    _optional_int(parts[15]) if len(parts) > 15 else None
                ),
            )
            continue

        if line.startswith("CSCOMP|"):
            parts = line.split("|")
            if len(parts) < 5:
                continue
            city_state_id = _optional_int(parts[1])
            player_id = _optional_int(parts[2])
            envoys = _optional_int(parts[4])
            city_state = city_states.get(city_state_id) if city_state_id is not None else None
            if city_state is not None and player_id is not None and envoys is not None:
                city_state.influence.append(
                    CityStateInfluence(
                        player_id=player_id,
                        player_name=parts[3],
                        envoys=envoys,
                    )
                )
            continue

        if line.startswith("CSBONUS|"):
            parts = line.split("|")
            if len(parts) < 5:
                continue
            city_state_id = _optional_int(parts[1])
            threshold = _optional_int(parts[2])
            city_state = city_states.get(city_state_id) if city_state_id is not None else None
            if city_state is not None and threshold is not None:
                city_state.bonuses.append(
                    CityStateBonus(
                        threshold=threshold,
                        title=parts[3],
                        details=parts[4],
                    )
                )
            continue

        if line.startswith("CSSUZ|"):
            parts = line.split("|")
            if len(parts) < 4:
                continue
            city_state_id = _optional_int(parts[1])
            city_state = city_states.get(city_state_id) if city_state_id is not None else None
            if city_state is not None:
                city_state.bonuses.append(
                    CityStateBonus(
                        threshold=0,
                        title=parts[2],
                        details=parts[3],
                        is_suzerain=True,
                    )
                )
            continue

        if line.startswith("CSQUEST|"):
            parts = line.split("|")
            if len(parts) < 6:
                continue
            city_state_id = _optional_int(parts[1])
            city_state = city_states.get(city_state_id) if city_state_id is not None else None
            if city_state is not None:
                city_state.quests.append(
                    CityStateQuest(
                        quest_type=parts[2],
                        name=parts[3],
                        description=parts[4],
                        reward=parts[5],
                        icon=parts[6] if len(parts) > 6 else "",
                    )
                )

    return EnvoyStatus(tokens_available=tokens, city_states=list(city_states.values()))


def parse_dedications_response(lines: list[str]) -> DedicationStatus:
    """Parse STATUS|, ACTIVE|, and CHOICE| lines from build_dedications_query."""
    age_type = "Normal"
    era = 0
    era_score = 0
    dark_threshold = 0
    golden_threshold = 0
    selections_allowed = 0
    active: list[str] = []
    choices: list[DedicationChoice] = []
    saw_status = False

    for line in lines:
        if line.startswith("STATUS|"):
            parts = line.split("|")
            if len(parts) >= 7:
                saw_status = True
                age_type = parts[1]
                era = int(parts[2])
                era_score = int(parts[3])
                dark_threshold = int(parts[4])
                golden_threshold = int(parts[5])
                selections_allowed = int(parts[6])
        elif line.startswith("ACTIVE|"):
            active.append(line.split("|", 1)[1])
        elif line.startswith("CHOICE|"):
            parts = line.split("|", 5)
            if len(parts) >= 6:
                choices.append(
                    DedicationChoice(
                        index=int(parts[1]),
                        name=parts[2],
                        normal_desc=parts[3],
                        golden_desc=parts[4],
                        dark_desc=parts[5],
                    )
                )

    if not saw_status:
        raise ValueError("缺少时代着力点 STATUS 响应。")
    return DedicationStatus(
        age_type=age_type,
        era=era,
        era_score=era_score,
        dark_threshold=dark_threshold,
        golden_threshold=golden_threshold,
        selections_allowed=selections_allowed,
        active=active,
        choices=choices,
    )
