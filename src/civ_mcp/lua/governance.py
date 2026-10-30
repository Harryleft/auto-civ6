"""Governance domain — Lua builders and parsers."""

from __future__ import annotations

import math
import json
import re

from civ_mcp.lua._helpers import (
    SENTINEL,
    _LUA_XP_THRESHOLD,
    _bail,
    _bail_lua,
    _int,
    _lua_get_city,
    _lua_get_unit,
    _lua_get_unit_gamecore,
)
from civ_mcp.lua.models import (
    AppointedGovernor,
    CityStateInfo,
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


# Native UI payment facts are shared by the read-only decision surface and
# final submission preflight. Missing APIs never imply free or affordable.
_LUA_POLICY_PAYMENT = """
local function finiteNonnegative(value)
    return type(value) == "number" and value == value
        and value >= 0 and value < math.huge
end
local policyCost, policyCurrency, policyBalance
local costOK, cost = pcall(function() return pCulture:GetCostToUnlockPolicies() end)
if costOK and finiteNonnegative(cost) then policyCost = cost end
local flagOK, flag = pcall(function() return GlobalParameters.GOVERNMENT_UNLOCK_WITH_FAITH end)
if flagOK and finiteNonnegative(flag) then
    policyCurrency = flag ~= 0 and "FAITH" or "GOLD"
    local balanceOK, balance = pcall(function()
        if policyCurrency == "FAITH" then return Players[me]:GetReligion():GetFaithBalance() end
        return Players[me]:GetTreasury():GetGoldBalance()
    end)
    if balanceOK and finiteNonnegative(balance) then policyBalance = balance end
end
"""


def build_policies_query(policy_type: str | None = None) -> str:
    """Current policy facts and the existing plan preconditions (InGame)."""
    if policy_type is not None and not re.fullmatch(r"POLICY_[A-Z0-9_]+", policy_type):
        raise ValueError("Expected a POLICY_ identifier")
    return """
local me = Game.GetLocalPlayer()
local pCulture = Players[me]:GetCulture()
local wanted={POLICY_FILTER}
if wanted and not GameInfo.Policies[wanted] then print("ERR:POLICY_NOT_FOUND|"..wanted);print("{SENTINEL}");return end
local function bool(fn)local ok,v=pcall(fn);if ok and type(v)=="boolean"then return v end;return nil end
local function field(v)if v==nil then return "?"end;return tostring(v)end
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
{POLICY_PAYMENT}
print("POLICY_PAYMENT|" .. tostring(policyCost or "UNKNOWN") .. "|"
    .. (policyCurrency or "UNKNOWN") .. "|" .. tostring(policyBalance or "UNKNOWN"))
local numSlots = pCulture:GetNumPolicySlots()
print("GOV|" .. govType .. "|" .. govName:gsub("|","/") .. "|" .. numSlots)
for s = 0, numSlots - 1 do
    local slotEntry = GameInfo.GovernmentSlots[pCulture:GetSlotType(s)]
    local slotType = slotEntry and slotEntry.GovernmentSlotType or ("SLOT_" .. pCulture:GetSlotType(s))
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
local changed=bool(function()return pCulture:PolicyChangeMade()end)
local plan,reason=nil,"OBSERVATION_UNAVAILABLE"
if policyCost==0 and changed==true then plan=false;reason="POLICIES_ALREADY_CHANGED"
elseif policyCost and policyCost>0 and policyBalance and policyBalance<policyCost then plan=false;reason="POLICY_UNLOCK_UNAFFORDABLE"
elseif type(pCulture.RequestPolicyChanges)~="function" then plan=false;reason="POLICY_OPERATION_UNAVAILABLE"
elseif policyCost==0 and changed==false then plan=true;reason="GENERAL_CHECKS_PASSED"
elseif policyCost and policyCost>0 and policyBalance and policyBalance>=policyCost and policyCurrency then plan=true;reason="PAID_UNLOCK_REQUIRED" end
print("POLICY_PLAN|"..field(plan).."|"..reason.."|"..field(changed))
local eligibleCount,shown,obsoleteCount,unslottable,unknown=0,0,0,0,0
for policy in GameInfo.Policies() do
    local unlocked=bool(function()return pCulture:IsPolicyUnlocked(policy.Hash)end)
    if unlocked==true or unlocked==nil or wanted==policy.PolicyType then
        if unlocked==true then eligibleCount=eligibleCount+1 end
        local obsolete=bool(function()return pCulture:IsPolicyObsolete(policy.Hash)end)
        local slotAllowed=false
        for slot=0,numSlots-1 do
            local can=bool(function()return pCulture:CanSlotPolicy(policy.Index,slot)end)
            if can==true then slotAllowed=true;break elseif can==nil then slotAllowed=nil end
        end
        local canUse,why=nil,"OBSERVATION_UNAVAILABLE"
        if unlocked==false then canUse=false;why="POLICY_LOCKED"
        elseif obsolete==true then canUse=false;why="POLICY_OBSOLETE"
        elseif plan==false then canUse=false;why=reason
        elseif slotAllowed==false then canUse=false;why="NO_CURRENT_ADD_SLOT_OR_ALREADY_SLOTTED"
        elseif unlocked==true and obsolete==false and slotAllowed==true and plan==true then canUse=true;why="GENERAL_CHECKS_PASSED" end
        if obsolete==true then obsoleteCount=obsoleteCount+1 end
        if slotAllowed==false then unslottable=unslottable+1 end
        if obsolete==nil or unlocked==nil or slotAllowed==nil then unknown=unknown+1 end
        if wanted==policy.PolicyType or (wanted==nil and obsolete~=true and slotAllowed~=false) then
            shown=shown+1
            local slotType=policy.GovernmentSlotType or "SLOT_WILDCARD"
            local name=Locale.Lookup(policy.Name):gsub("|","/")
            local desc=Locale.Lookup(policy.Description):gsub("|","/"):gsub("\\n"," ")
            print("AVAIL|"..policy.PolicyType.."|"..name.."|"..desc.."|"..slotType.."|"..field(obsolete).."|"..field(canUse).."|"..why)
        end
    end
end
print("POLICY_COVERAGE|"..eligibleCount.."|"..shown.."|"..obsoleteCount.."|"..unslottable.."|"..unknown)
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL).replace("{POLICY_PAYMENT}", _LUA_POLICY_PAYMENT).replace("{POLICY_FILTER}", "nil" if policy_type is None else json.dumps(policy_type))


def build_governors_query(include_ineligible: bool = False) -> str:
    """Read governor status, appointed governors, and available types (InGame context)."""
    return """
local me = Game.GetLocalPlayer()
local pGovs = Players[me]:GetGovernors()
local expand={EXPAND}
local omittedPromotions,omittedAppointments=0,0
local function bool(fn)local ok,v=pcall(fn);if ok and type(v)=="boolean"then return v end;return nil end
local function field(v)if v==nil then return "?"end;return tostring(v)end
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
                if promoRow and not g:HasPromotion(promoRow.Hash) then
                    local allowed=bool(function()return pGovs:CanEarnPromotion(row.Hash,promoRow.Hash)end)
                    if expand or allowed~=false then
                    local pName = Locale.Lookup(promoRow.Name)
                    local pDesc = Locale.Lookup(promoRow.Description):gsub("|", "/"):gsub("\\n", " ")
                    local lvl = promoRow.Level or 0
                    local col = promoRow.Column or 0
                    print("GOV_PROMO|" .. row.GovernorType .. "|" .. promoRow.GovernorPromotionType .. "|" .. pName:gsub("|","/") .. "|" .. pDesc .. "|" .. lvl .. "|" .. col .. "|" .. field(allowed))
                    else omittedPromotions=omittedPromotions+1 end
                end
            end
        end
    end
end
for gov in GameInfo.Governors() do
    if gov.TransitionStrength and gov.TransitionStrength > 0 and not appointedTypes[gov.GovernorType] then
        local ever=bool(function()return pGovs:CanEverAppointGovernor(gov.Hash)end)
        local now=bool(function()return pGovs:CanAppoint()end)
        local allowed=nil
        if ever==false or now==false then allowed=false elseif ever==true and now==true then allowed=true end
        if expand or allowed~=false then
        local gName = Locale.Lookup(gov.Name)
        local gTitle = Locale.Lookup(gov.Title)
        local gDesc = gov.Description and Locale.Lookup(gov.Description):gsub("|", "/"):gsub("\\n", " ") or ""
        print("AVAILABLE|" .. gov.GovernorType .. "|" .. gName:gsub("|","/") .. "|" .. gTitle:gsub("|","/") .. "|" .. gDesc .. "|" .. field(allowed))
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
        else omittedAppointments=omittedAppointments+1 end
    end
end
print("GOV_COVERAGE|"..omittedPromotions.."|"..omittedAppointments)
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL).replace("{EXPAND}", str(include_ineligible).lower())


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


def build_city_states_query(
    player_id: int | None = None, eligibility: dict[int, bool] | None = None,
) -> str:
    """List met recipients; explicit owner/eligibility selects the core read."""
    source = """
local me = {OWNER}
local pInfluence = Players[me]:GetInfluence()
local pDiplo = Players[me]:GetDiplomacy()
print("TOKENS|" .. pInfluence:GetTokensToGive())
local csTypeMap = {}
csTypeMap["LEADER_MINOR_CIV_SCIENTIFIC"] = "Scientific"
csTypeMap["LEADER_MINOR_CIV_CULTURAL"] = "Cultural"
csTypeMap["LEADER_MINOR_CIV_MILITARISTIC"] = "Militaristic"
csTypeMap["LEADER_MINOR_CIV_RELIGIOUS"] = "Religious"
csTypeMap["LEADER_MINOR_CIV_TRADE"] = "Trade"
csTypeMap["LEADER_MINOR_CIV_INDUSTRIAL"] = "Industrial"
for i = 0, 62 do
    if Players[i] and Players[i]:IsAlive() and Players[i]:IsMajor() == false and Players[i]:IsBarbarian() == false and pDiplo:HasMet(i) then
        -- Match CityStates.lua: free cities are non-major/non-barbarian too,
        -- but only native influence recipients belong in the city-state list.
        local csInfluence = Players[i]:GetInfluence()
        local canReceive = csInfluence:CanReceiveInfluence()
        assert(type(canReceive) == "boolean", "CITY_STATE_IDENTITY_UNKNOWN")
        if canReceive then
            local cfg = PlayerConfigurations[i]
            local name = Locale.Lookup(cfg:GetPlayerName())
            local leaderType = cfg:GetLeaderTypeName() or ""
            local csType = "Unknown"
            local leader = GameInfo.Leaders[leaderType]
            if leader and leader.InheritFrom then
                csType = csTypeMap[leader.InheritFrom] or leader.InheritFrom
            end
            local envoys = csInfluence:GetTokensReceived(me)
            local suzID = csInfluence:GetSuzerain() or -1
            local suzName = "None"
            if suzID >= 0 and suzID ~= 63 then
                local sCfg = PlayerConfigurations[suzID]
                if sCfg then suzName = Locale.Lookup(sCfg:GetCivilizationShortDescription()) end
            end
            local canSend = pInfluence:GetTokensToGive()>0 and {CAN_SEND} and "1" or "0"
            print("CS|" .. i .. "|" .. name:gsub("|","/") .. "|" .. csType .. "|" .. envoys .. "|" .. suzID .. "|" .. suzName:gsub("|","/") .. "|" .. canSend)
        end
    end
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)

    ui_eligibility = 'pInfluence:CanGiveInfluence()==true and pInfluence:CanGiveTokensToPlayer(i)==true'
    if player_id is None:
        return source.replace("{OWNER}", "Game.GetLocalPlayer()").replace("{CAN_SEND}", ui_eligibility)
    # These eligibility predicates are UI-only. The supplied UI flags are
    # combined with GameCore's authoritative remaining tokens.
    flags = ",".join(f"[{target}]={str(allowed).lower()}" for target, allowed in (eligibility or {}).items())
    source = source.replace("{OWNER}", str(player_id)).replace("{CAN_SEND}", 'nativeCanSend[i]==true')
    return "local nativeCanSend={" + flags + "}\n" + source


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


def build_dedications_query() -> str:
    """Read current era age, available dedications, and active ones."""
    return """
local me = Game.GetLocalPlayer()
local pEras = Game.GetEras()
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
""".replace("{SENTINEL}", SENTINEL)


def build_available_governments_query() -> str:
    """List unlocked governments with slot info (InGame context)."""
    return """
local me = Game.GetLocalPlayer()
local pCulture = Players[me]:GetCulture()
local curGov = pCulture:GetCurrentGovernment()
for row in GameInfo.Governments() do
    local unlocked = pCulture:IsGovernmentUnlocked(row.Hash)
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


def parse_policies_response(lines: list[str]) -> GovernmentStatus:
    """Parse GOV|, SLOT|, AVAIL| lines from build_policies_query."""
    gov_name = "None"
    gov_type = "NONE"
    slots: list[PolicySlot] = []
    available: list[PolicyInfo] = []
    unlock_cost = unlock_currency = unlock_balance = None
    can_change_now = changed_this_turn = None
    change_reason = "OBSERVATION_UNAVAILABLE"
    coverage = {}

    for line in lines:
        if line.startswith("POLICY_PAYMENT|"):
            parts = line.split("|")
            if len(parts) == 4:
                try:
                    value = float(parts[1])
                    unlock_cost = value if math.isfinite(value) and value >= 0 else None
                except ValueError:
                    pass
                unlock_currency = parts[2] if parts[2] in {"GOLD", "FAITH"} else None
                try:
                    value = float(parts[3])
                    unlock_balance = value if math.isfinite(value) and value >= 0 else None
                except ValueError:
                    pass

        elif line.startswith("POLICY_PLAN|"):
            p = line.split("|")
            if len(p)==4:
                can_change_now={"true":True,"false":False}.get(p[1]);change_reason=p[2]
                changed_this_turn={"true":True,"false":False}.get(p[3])
        elif line.startswith("POLICY_COVERAGE|"):
            p=line.split("|")
            if len(p)==6:
                coverage=dict(zip(("unlocked","shown","obsolete","no_add_slot","unknown"),map(int,p[1:])))
        elif line.startswith("GOV|"):
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
                        obsolete={"true":True,"false":False}.get(parts[5]) if len(parts)>5 else None,
                        can_use_now={"true":True,"false":False}.get(parts[6]) if len(parts)>6 else None,
                        reason=parts[7] if len(parts)>7 else "OBSERVATION_UNAVAILABLE",
                    )
                )

    return GovernmentStatus(
        government_name=gov_name,
        government_type=gov_type,
        slots=slots,
        available_policies=available,
        policy_unlock_cost=unlock_cost,
        policy_unlock_currency=unlock_currency,
        policy_unlock_balance=unlock_balance,
        can_change_now=can_change_now, change_reason=change_reason,
        changed_this_turn=changed_this_turn, coverage=coverage,
    )


def parse_governors_response(lines: list[str]) -> GovernorStatus:
    """Parse STATUS|, APPOINTED|, GOV_PROMO|, AVAILABLE| lines from build_governors_query."""
    omitted_promotions = omitted_appointments = 0
    pts_avail = 0
    pts_spent = 0
    can_appoint = False
    appointed: list[AppointedGovernor] = []
    available: list[GovernorInfo] = []
    # Collect promotions keyed by governor_type, then attach after
    promos_by_gov: dict[str, list[GovernorPromotion]] = {}

    for line in lines:
        if line.startswith("GOV_COVERAGE|"):
            p=line.split("|")
            if len(p)==3:omitted_promotions,omitted_appointments=map(int,p[1:])
        elif line.startswith("STATUS|"):
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
                        can_earn={"true":True,"false":False}.get(parts[7]) if len(parts)>7 else None,
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
                        can_appoint={"true":True,"false":False}.get(parts[5]) if len(parts)>5 else None,
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
        omitted_promotions=omitted_promotions, omitted_appointments=omitted_appointments,
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

    return UnitPromotionStatus(
        unit_id=unit_id,
        unit_index=unit_index,
        unit_type=unit_type,
        promotions=promotions,
        xp=xp,
        xp_needed=xp_needed,
        promotion_count=promotion_count,
    )


def parse_city_states_response(lines: list[str]) -> EnvoyStatus:
    """Parse TOKENS| and CS| lines from build_city_states_query."""
    tokens = 0
    city_states: list[CityStateInfo] = []

    for line in lines:
        if line.startswith("TOKENS|"):
            tokens = int(line.split("|")[1])
        elif line.startswith("CS|"):
            parts = line.split("|")
            if len(parts) >= 8:
                city_states.append(
                    CityStateInfo(
                        player_id=int(parts[1]),
                        name=parts[2],
                        city_state_type=parts[3],
                        envoys_sent=int(parts[4]),
                        suzerain_id=int(parts[5]),
                        suzerain_name=parts[6],
                        can_send_envoy=parts[7] == "1",
                    )
                )

    return EnvoyStatus(tokens_available=tokens, city_states=city_states)


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

    for line in lines:
        if line.startswith("STATUS|"):
            parts = line.split("|")
            if len(parts) >= 7:
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
