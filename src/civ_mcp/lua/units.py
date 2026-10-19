"""Units domain — Lua builders and parsers."""

from __future__ import annotations

from civ_mcp.lua._helpers import (
    _LUA_KNOWN_RESOURCE,
    _LUA_RES_VISIBLE,
    SENTINEL,
    _bail,
    _bail_lua,
    _lua_get_unit,
    _lua_get_unit_gamecore,
)
from civ_mcp.lua.models import (
    BuilderInfo,
    BuilderTask,
    BuilderTaskReport,
    CombatEstimate,
    PathingEstimate,
    ThreatInfo,
    UnitInfo,
)
from civ_mcp.lua.turn_readiness import LUA_TURN_READINESS


def build_units_query() -> str:
    """InGame context: lists all units with upgrade and builder improvement info."""
    return _LUA_ATTACK_MODE + _LUA_MELEE_ELIGIBILITY + """
local id = Game.GetLocalPlayer()
for i, u in Players[id]:GetUnits():Members() do
    local x, y = u:GetX(), u:GetY()
    if x ~= -9999 then
        local uid = u:GetID()
        local entry = GameInfo.Units[u:GetType()]
        local ut = entry and entry.UnitType or "UNKNOWN"
        local nm = Locale.Lookup(u:GetName())
        local cs = entry and entry.Combat or 0
        local rs = entry and entry.RangedCombat or 0
        local charges = u:GetBuildCharges() or 0
        local gp = u:GetGreatPerson()
        if gp then
            local ok_gp, gp_charges = pcall(function() return gp:GetActionCharges() end)
            if ok_gp and gp_charges and gp_charges > 0 then charges = gp_charges end
            if charges == 0 then
                -- Cultural GPs (Writers/Artists/Musicians) return 0 from
                -- GetActionCharges(). Fall back to the individual definition.
                pcall(function()
                    local indIdx = gp:GetIndividual()
                    for ind in GameInfo.GreatPersonIndividuals() do
                        if ind.Index == indIdx then
                            charges = ind.ActionCharges or 0
                            break
                        end
                    end
                end)
            end
        end
        if charges == 0 then
            local ok_sp, sp = pcall(function() return u:GetSpreadCharges() end)
            if ok_sp and sp and sp > 0 then charges = sp end
        end
        local relName = ""
        local ok_r, rIdx = pcall(function() return u:GetReligionType() end)
        if ok_r and rIdx and rIdx >= 0 then
            for row in GameInfo.Religions() do
                if row.Index == rIdx then relName = row.ReligionType; break end
            end
        end
        -- Scan for attackable enemies if unit has moves
        local targets = ""
        if u:GetMovesRemaining() > 0 and (cs > 0 or rs > 0) then
            local rng = (rs > 0) and (entry and entry.Range or 1) or 1
            local tgtList = {}
            for dy = -rng, rng do
                for dx = -rng, rng do
                    local tx, ty = x + dx, y + dy
                    local d = Map.GetPlotDistance(x, y, tx, ty)
                    if d >= 1 and d <= rng then
                        local plotUnits = Map.GetUnitsAt(tx, ty)
                        if plotUnits then
                            for other in plotUnits:Units() do
                                local otherOwner = other:GetOwner()
                                if otherOwner ~= id and (otherOwner == 63 or Players[id]:GetDiplomacy():IsAtWarWith(otherOwner)) then
                                    local mode = classifyUnitAttack(u)
                                    local losOK = mode ~= nil
                                    if mode == "MELEE" then
                                        local mp = {}
                                        mp[UnitOperationTypes.PARAM_X] = tx
                                        mp[UnitOperationTypes.PARAM_Y] = ty
                                        mp[UnitOperationTypes.PARAM_MODIFIERS] = UnitOperationMoveModifiers.ATTACK
                                        losOK = meleeAttackEligibility(u, mp) == nil
                                    elseif rs > 0 and d > 1 then
                                        local lp = {{}}
                                        lp[UnitOperationTypes.PARAM_X] = tx
                                        lp[UnitOperationTypes.PARAM_Y] = ty
                                        losOK = UnitManager.CanStartOperation(u, UnitOperationTypes.RANGE_ATTACK, nil, lp)
                                    end
                                    if losOK then
                                        local eInfo = GameInfo.Units[other:GetType()]
                                        local eName = eInfo and eInfo.UnitType or "UNKNOWN"
                                        local eHP = other:GetMaxDamage() - other:GetDamage()
                                        table.insert(tgtList, eName .. "@" .. tx .. "," .. ty .. "(" .. eHP .. "hp)")
                                    end
                                end
                            end
                        end
                    end
                end
            end
            if #tgtList > 0 then targets = table.concat(tgtList, ";") end
        end
        -- NOTE: promotion detection is intentionally omitted here.
        -- GetExperiencePoints() >= GetExperienceForNextLevel() stays true after
        -- SetPromotion() (level and XP are not consumed), so any mid-turn check
        -- fires one turn early AND causes double-promotions when end_turn's
        -- authoritative GameCore CanPromote check also fires.
        -- All promotion handling is routed through the end_turn blocker (which
        -- uses CanPromote in GameCore — the only correct check).
        local promo = "0"
        -- Upgrade info (InGame only: CanStartCommand)
        local canUp, upName, upCost = "0", "", "0"
        local ok1, _ = pcall(function()
            if UnitManager.CanStartCommand(u, UnitCommandTypes.UPGRADE, nil, true) then
                canUp = "1"
                local c2 = u:GetUpgradeCost()
                if c2 then upCost = tostring(c2) end
                if entry and entry.UpgradeUnitCollection then
                    for _, row in ipairs(entry.UpgradeUnitCollection) do
                        if row.UpgradeUnit then upName = row.UpgradeUnit end
                        break
                    end
                end
            end
        end)
        -- Builder improvement advisor (InGame only: CanStartOperation)
        local validImps = ""
        if ut == "UNIT_BUILDER" and u:GetMovesRemaining() > 0 then
            local plot = Map.GetPlot(x, y)
            if plot and plot:GetOwner() == id then
                local impList = {}
                for imp in GameInfo.Improvements() do
                    if imp.Buildable then
                        local bParams = {}
                        bParams[UnitOperationTypes.PARAM_X] = x
                        bParams[UnitOperationTypes.PARAM_Y] = y
                        bParams[UnitOperationTypes.PARAM_IMPROVEMENT_TYPE] = imp.Hash
                        local ok2, _ = pcall(function()
                            if UnitManager.CanStartOperation(u, UnitOperationTypes.BUILD_IMPROVEMENT, nil, bParams) == true then
                                table.insert(impList, imp.ImprovementType)
                            end
                        end)
                    end
                end
                if #impList > 0 then validImps = table.concat(impList, ";") end
            end
        end
        -- Military Engineer advisor (BUILD_ROUTE + fort/airstrip)
        if ut == "UNIT_MILITARY_ENGINEER" and u:GetMovesRemaining() > 0 then
            local meList = {}
            pcall(function()
                local opRow = GameInfo.UnitOperations["UNITOPERATION_BUILD_ROUTE"]
                if opRow then
                    local rp = {}
                    rp[UnitOperationTypes.PARAM_X] = x
                    rp[UnitOperationTypes.PARAM_Y] = y
                    if UnitManager.CanStartOperation(u, opRow.Hash, nil, rp) then
                        table.insert(meList, "BUILD_ROUTE")
                    end
                end
            end)
            local plot = Map.GetPlot(x, y)
            if plot and plot:GetOwner() == id then
                for imp in GameInfo.Improvements() do
                    if imp.Buildable then
                        pcall(function()
                            local bp = {}
                            bp[UnitOperationTypes.PARAM_X] = x
                            bp[UnitOperationTypes.PARAM_Y] = y
                            bp[UnitOperationTypes.PARAM_IMPROVEMENT_TYPE] = imp.Hash
                            if UnitManager.CanStartOperation(u, UnitOperationTypes.BUILD_IMPROVEMENT, nil, bp) == true then
                                table.insert(meList, imp.ImprovementType)
                            end
                        end)
                    end
                end
            end
            if #meList > 0 then validImps = table.concat(meList, ";") end
        end
        print(uid .. "|" .. (uid % 65536) .. "|" .. nm .. "|" .. ut .. "|" .. x .. "," .. y .. "|" .. u:GetMovesRemaining() .. "/" .. u:GetMaxMoves() .. "|" .. (u:GetMaxDamage() - u:GetDamage()) .. "/" .. u:GetMaxDamage() .. "|" .. cs .. "|" .. rs .. "|" .. charges .. "|" .. targets .. "|" .. promo .. "|" .. canUp .. "|" .. upName .. "|" .. upCost .. "|" .. validImps .. "|" .. relName)
    end
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def build_move_unit(
    unit_index: int, target_x: int, target_y: int, *, witness_token: str | None = None,
) -> str:
    witness = ""
    if witness_token is not None:
        witness = f"""
-- This record is armed before exactly one submission. The native callback
-- has no request nonce; serial actions and fresh identity/target facts fence it.
local prior = __civmcpMoveSegment
if prior and not prior.closed then
    {_bail("ERR:OUTCOME_UNKNOWN|Prior native movement witness is unresolved; no move submitted.")}
end
if prior and prior.handler then
    pcall(function() Events.UnitOperationSegmentComplete.Remove(prior.handler) end)
end
local cfg = PlayerConfigurations[me]
local q = {{token="{witness_token}", owner=me, id=unit:GetID(),
    civ=cfg:GetCivilizationTypeName(), seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),
    turn=Game.GetCurrentGameTurn(), fromX=fromX, fromY=fromY,
    targetX={target_x}, targetY={target_y}, done=false, closed=false}}
__civmcpMoveSegment = q
q.handler = function(player, unitID, hCommand, iData1)
    pcall(function()
        if __civmcpMoveSegment ~= q or q.closed or q.done then return end
        if player ~= q.owner or unitID ~= q.id then return end
        if Game.GetLocalPlayer() ~= q.owner or Game.GetCurrentGameTurn() ~= q.turn then return end
        local currentCfg = PlayerConfigurations[q.owner]
        if currentCfg:GetCivilizationTypeName() ~= q.civ
            or GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED") ~= q.seed then return end
        q.done = true
        -- Shipped UI does not type these two fields. Keep raw diagnostics;
        -- do not assume hCommand equals the MOVE_TO hash.
        q.rawCommand, q.rawData = tostring(hCommand), tostring(iData1)
        pcall(function() Events.UnitOperationSegmentComplete.Remove(q.handler) end)
    end)
end
local armed = pcall(function() Events.UnitOperationSegmentComplete.Add(q.handler) end)
if not armed then
    q.closed = true
    pcall(function() Events.UnitOperationSegmentComplete.Remove(q.handler) end)
    {_bail("ERR:MOVE_WITNESS_UNAVAILABLE|Native movement witness unavailable; no move submitted.")}
end
"""
    return f"""
{_lua_get_unit(unit_index)}
if unit:GetMovesRemaining() <= 0 then
    {_bail("ERR:NO_MOVES|Unit has no movement points remaining this turn. Use skip or wait until next turn.")}
end
local moveOK, canMove = pcall(function()
    return UnitManager.CanStartOperation(unit, UnitOperationTypes.MOVE_TO, nil, true)
end)
if not moveOK or canMove ~= true then
    {_bail("ERR:CANNOT_MOVE|Native movement eligibility unavailable or refused; no move submitted.")}
end
-- Pre-check: stacking conflict at target tile
local unitInfo = GameInfo.Units[unit:GetType()]
local isCivilian = (unitInfo and unitInfo.FormationClass == "FORMATION_CLASS_CIVILIAN")
local tgtUnits = Map.GetUnitsAt({target_x}, {target_y})
if tgtUnits then
    for other in tgtUnits:Units() do
        if other:GetOwner() == me then
            local otherInfo = GameInfo.Units[other:GetType()]
            local otherCivilian = (otherInfo and otherInfo.FormationClass == "FORMATION_CLASS_CIVILIAN")
            if isCivilian == otherCivilian then
                local otherName = otherInfo and otherInfo.UnitType or "unit"
                {_bail_lua(f'"ERR:STACKING_CONFLICT|Friendly " .. otherName .. " already on ({target_x},{target_y}). Cannot stack same formation class."')}
            end
        end
    end
end
local fromX, fromY = unit:GetX(), unit:GetY()
local params = {{}}
params[UnitOperationTypes.PARAM_X] = {target_x}
params[UnitOperationTypes.PARAM_Y] = {target_y}
-- Foreign units may be allies or neutral. Only an existing native war
-- authorizes the ATTACK modifier needed for hostile civilian capture.
local hasHostile = false
if tgtUnits then
    for other in tgtUnits:Units() do
        local owner = other:GetOwner()
        if owner ~= me then
            local relationOK, atWar = pcall(function()
                return Players[me]:GetDiplomacy():IsAtWarWith(owner)
            end)
            if not relationOK or type(atWar) ~= "boolean" then
                {_bail("ERR:MOVE_RELATION_UNKNOWN|Native target war relationship unavailable; no move submitted.")}
            end
            if atWar then hasHostile = true end
        end
    end
end
if hasHostile then
    params[UnitOperationTypes.PARAM_MODIFIERS] = UnitOperationMoveModifiers.ATTACK
end
-- General movement eligibility does not validate this destination. Use the
-- final parameters before arming a witness or submitting any operation.
local targetOK, canTarget = pcall(function()
    return UnitManager.CanStartOperation(unit, UnitOperationTypes.MOVE_TO, nil, params)
end)
if not targetOK or canTarget ~= true then
    {_bail("ERR:CANNOT_MOVE_TARGET|Native target movement eligibility unavailable or refused; no move submitted.")}
end
-- CanStartOperation may allow a target with no usable movement path. The
-- stock UI marks an empty path bad, but its click handling can still send it.
local pathOK, pathStatus = pcall(function()
    if UI.IsGameCoreBusy() ~= false then return "UNKNOWN" end
    local destination = Map.GetPlot({target_x}, {target_y})
    if not destination then return "UNKNOWN" end
    local path = UnitManager.GetMoveToPathEx(unit, destination:GetIndex())
    if type(path) ~= "table" or type(path.plots) ~= "table" or type(path.turns) ~= "table" then return "UNKNOWN" end
    local function integer(value, minimum)
        return type(value) == "number" and value >= minimum and value < math.huge and value == math.floor(value)
    end
    local function length(values)
        local count = 0
        for key in pairs(values) do
            if not integer(key, 1) then return nil end
            count = count + 1
        end
        for i = 1, count do if values[i] == nil then return nil end end
        return count
    end
    local count = length(path.plots)
    if count == nil or length(path.turns) ~= count then return "UNKNOWN" end
    if count == 0 then
        local visible = PlayersVisibility[me]:IsVisible({target_x}, {target_y})
        return visible == true and "NO_PATH" or "UNKNOWN"
    end
    if count < 2 then return "UNKNOWN" end
    for i = 1, count do
        if not integer(path.plots[i], 0) or not integer(path.turns[i], 1) then return "UNKNOWN" end
        if i > 1 and path.turns[i] < path.turns[i-1] then return "UNKNOWN" end
        local plot = Map.GetPlotByIndex(path.plots[i])
        if not plot then return "UNKNOWN" end
        if i == 1 and (plot:GetX() ~= fromX or plot:GetY() ~= fromY) then return "UNKNOWN" end
        if i == count and (plot:GetX() ~= {target_x} or plot:GetY() ~= {target_y}) then return "UNKNOWN" end
    end
    -- WorldInput.lua starts its path-turn counter at 1. A native MOVE_TO
    -- may accept a queue whose first step is next turn; it cannot provide
    -- this request's displacement evidence, so defer before touching orders
    -- or the prior segment witness. A later destination is still legal when
    -- its first step is current-turn (normal partial movement).
    if path.turns[1] ~= 1 then return "UNKNOWN" end
    local firstStep = Map.GetPlotByIndex(path.plots[2])
    if firstStep:GetX() == fromX and firstStep:GetY() == fromY then return "UNKNOWN" end
    if path.turns[2] > 1 then return "DEFERRED" end
    return "VALID"
end)
if pathOK and pathStatus == "DEFERRED" then
    {_bail("ERR:MOVE_DEFERRED|Native first movement step is after this turn; no move submitted; existing orders unchanged. Wait until the next turn or choose another reachable destination.")}
end
if pathOK and pathStatus == "NO_PATH" then
    {_bail("ERR:NO_PATH|Visible destination has no native movement path; no move submitted.")}
end
if not pathOK or pathStatus ~= "VALID" then
    {_bail("ERR:PATH_UNKNOWN|Native movement path unavailable or unverified; no move submitted.")}
end
{witness}
UnitManager.RequestOperation(unit, UnitOperationTypes.MOVE_TO, params)
local tag = hasHostile and "OK:CAPTURE_MOVE|" or "OK:MOVING_TO|"
print(tag .. {target_x} .. "," .. {target_y} .. "|from:" .. fromX .. "," .. fromY)
print("{SENTINEL}")
"""


def build_unit_position_query(
    unit_index: int,
    move_target_x: int | None = None,
    move_target_y: int | None = None,
    *, witness_token: str | None = None,
) -> str:
    """InGame gameplay read-only probe; retires only its own event listener.

    Native effective movement / ZOC facts are optional on unsupported contexts.
    Their absence must remain unknown, not become a zero or false value.

    When *move_target_x/y* are provided, also diagnoses why a blocked move
    failed (water, mountain, foreign border) so the caller doesn't need a
    second round-trip.
    """
    diag_block = ""
    if move_target_x is not None and move_target_y is not None:
        diag_block = f"""
-- Diagnose blocked move target
pcall(function()
    local plot = Map.GetPlot({move_target_x}, {move_target_y})
    if not plot then print("DIAG|UNKNOWN|tile does not exist"); return end
    if plot:IsWater() then
        local hasShip = false
        pcall(function()
            local tech = GameInfo.Technologies["TECH_SHIPBUILDING"]
            if tech then hasShip = Players[me]:GetTechs():HasTech(tech.Index) end
        end)
        if hasShip then print("DIAG|WATER_OK|water tile (can embark)")
        else print("DIAG|WATER|water tile - land units need Shipbuilding tech to embark") end
    elseif plot:IsMountain() then
        moveImpassable = true
        print("DIAG|MOUNTAIN|impassable mountain")
    elseif plot:IsImpassable() then
        moveImpassable = true
        print("DIAG|IMPASSABLE|impassable terrain (ice or natural wonder)")
    else
        local owner = plot:GetOwner()
        if owner >= 0 and owner ~= me then
            local atWar = false
            pcall(function() atWar = Players[me]:GetDiplomacy():IsAtWarWith(owner) end)
            if atWar then
                print("DIAG|UNKNOWN|tile is enemy territory but movement still blocked - check path")
            else
                local civName = "player " .. owner
                pcall(function()
                    local cfg = PlayerConfigurations[owner]
                    civName = cfg and Locale.Lookup(cfg:GetCivilizationShortDescription()) or civName
                end)
                -- Ownership alone says nothing about received Open Borders,
                -- alliance access, city entry, or an intermediate path tile.
                print("DIAG|FOREIGN_TERRITORY|foreign territory (" .. civName .. ") - entry or path restriction unconfirmed")
            end
        else
            print("DIAG|UNKNOWN|tile appears passable - path may be blocked by intermediate tiles")
        end
    end
end)
"""
    path_probe = ""
    witness_probe = ""
    if move_target_x is not None and move_target_y is not None:
        # Shipped WorldInput.lua uses GetMoveToPathEx().plots/.turns and
        # interprets turn numbers >1 as later-turn movement. The older
        # get_pathing_estimate heuristic is not completion evidence.
        path_probe = f"""
pcall(function()
    if not u or UI.IsGameCoreBusy() then return end
    local destination = Map.GetPlot({move_target_x}, {move_target_y})
    if not destination then return end
    local path = UnitManager.GetMoveToPathEx(u, destination:GetIndex())
    if type(path) ~= "table" or type(path.plots) ~= "table" or type(path.turns) ~= "table" then return end
    if #path.plots < 2 or #path.plots ~= #path.turns then return end
    local first = Map.GetPlotByIndex(path.plots[1])
    local last = Map.GetPlotByIndex(path.plots[#path.plots])
    if not first or not last or first:GetX() ~= u:GetX() or first:GetY() ~= u:GetY() then return end
    if last:GetX() ~= {move_target_x} or last:GetY() ~= {move_target_y} then return end
    -- A truncated/malformed middle of a path cannot support completion.
    for i = 1, #path.plots do
        if not Map.GetPlotByIndex(path.plots[i]) or type(path.turns[i]) ~= "number"
            or path.turns[i] < 1 or path.turns[i] % 1 ~= 0 then return end
        if i > 1 and path.turns[i] < path.turns[i-1] then return end
    end
    local nextPlot = Map.GetPlotByIndex(path.plots[2])
    local nextTurn = tonumber(path.turns[2])
    if nextPlot and nextTurn then
        print("PATH_STEP|" .. nextPlot:GetX() .. "|" .. nextPlot:GetY() .. "|turn:" .. nextTurn)
    end
end)
"""
    if witness_token is not None:
        witness_probe = f"""
pcall(function()
    local q = __civmcpMoveSegment
    if not q or q.token ~= "{witness_token}" or not u then return end
    local cfg = PlayerConfigurations[me]
    if q.owner ~= me or q.id ~= u:GetID() or q.turn ~= Game.GetCurrentGameTurn()
        or q.civ ~= cfg:GetCivilizationTypeName()
        or q.seed ~= GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
        or q.targetX ~= {move_target_x} or q.targetY ~= {move_target_y} then return end
    print("MOVE_WITNESS|" .. q.token .. "|" .. q.owner .. "|" .. q.id .. "|" .. q.civ
        .. "|" .. q.seed .. "|" .. q.turn .. "|" .. q.fromX .. "|" .. q.fromY
        .. "|" .. q.targetX .. "|" .. q.targetY .. "|" .. tostring(q.done)
        .. "|rawCommand:" .. tostring(q.rawCommand) .. "|rawData:" .. tostring(q.rawData))
    local plot = nil
    pcall(function()
        local queued = UnitManager.GetQueuedDestination(u)
        plot = queued and Map.GetPlotByIndex(queued)
    end)
    local queuedMatches = plot and plot:GetX() == q.targetX and plot:GetY() == q.targetY
    if plot then print("QUEUED_TARGET|" .. plot:GetX() .. "|" .. plot:GetY()) end
    if UI.IsGameCoreBusy() ~= false then return end
    local moved = u:GetX() ~= q.fromX or u:GetY() ~= q.fromY
    local reached = u:GetX() == q.targetX and u:GetY() == q.targetY
    local nativeStop = u:GetMovesRemaining() <= 0
    pcall(function() nativeStop = nativeStop or u:GetMovementMovesRemaining() <= 0 end)
    pcall(function() nativeStop = nativeStop or u:HasMovedIntoZOC() end)
    -- This event completes the original segment, even if a newly planned
    -- route now backtracks or is unavailable. It does not prove arrival.
    local segmentStop = q.done and queuedMatches
    if reached or u:GetMovesRemaining() <= 0 or nativeStop and moved
        or segmentStop and moved or moveImpassable and not moved then
        q.closed = true
        pcall(function() Events.UnitOperationSegmentComplete.Remove(q.handler) end)
    end
end)
"""
    return f"""
local moveImpassable = false
local me = Game.GetLocalPlayer()
local u = Players[me]:GetUnits():FindID({unit_index})
if u then
    local busy = "unknown"
    pcall(function() busy = tostring(UI.IsGameCoreBusy()) end)
    local movementMoves = ""
    pcall(function()
        local value = u:GetMovementMovesRemaining()
        if type(value) == "number" then movementMoves = "|movement_moves:" .. value end
    end)
    local zoc = ""
    pcall(function()
        local value = u:HasMovedIntoZOC()
        if type(value) == "boolean" then zoc = "|zoc:" .. tostring(value) end
    end)
    print("POS|" .. u:GetX() .. "|" .. u:GetY() .. "|moves:" .. u:GetMovesRemaining() .. movementMoves .. zoc .. "|busy:" .. busy .. "|owner:" .. me .. "|id:" .. u:GetID())
else print("POS|GONE") end
{path_probe}
{diag_block}
{witness_probe}
print("{SENTINEL}")
"""


# Capability is independent of CanStartOperation's current eligibility. In
# particular, a ranged refusal must never turn into MOVE_TO + ATTACK.
_LUA_ATTACK_MODE = """
local function classifyUnitAttack(unit)
    local info = GameInfo and GameInfo.Units and GameInfo.Units[unit:GetType()]
    if not info or (info.Domain ~= "DOMAIN_AIR" and info.Domain ~= "DOMAIN_LAND" and info.Domain ~= "DOMAIN_SEA") then
        return nil
    end
    local function valid(value)
        return type(value) == "number" and value == value and value >= 0 and value < math.huge
    end
    local function strength(method, field)
        local base = info[field]
        local ok, native = pcall(function() return unit[method](unit) end)
        if ok and valid(native) then
            -- A temporary effective zero does not erase the rule capability.
            return valid(base) and math.max(base, native) or native
        end
        if valid(base) then return base end
    end
    local combat = strength("GetCombat", "Combat")
    local ranged = strength("GetRangedCombat", "RangedCombat")
    local bombard = strength("GetBombardCombat", "Bombard")
    local range = strength("GetRange", "Range")
    if info.Domain == "DOMAIN_AIR" then
        if (combat and combat > 0) or (ranged and ranged > 0) or (bombard and bombard > 0) then
            return "AIR", info, range
        end
    elseif ranged ~= nil and bombard ~= nil then
        if bombard > ranged and bombard > 0 then return "BOMBARD", info, range end
        if ranged > 0 or bombard > 0 then return "RANGED", info, range end
        if combat and combat > 0 then return "MELEE", info, range end
    end
    return nil
end
"""


# CanStartOperation admits deferred MOVE_TO+ATTACK orders. The shipped
# WorldInput.lua draws GetMoveToPathEx paths using .plots/.turns and treats
# arrival turns >1 as queued attacks. Require a complete current-turn path
# before either advertising or submitting a melee attack; never infer it
# from terrain costs or positive remaining movement alone.
_LUA_MELEE_ELIGIBILITY = """
local function meleeAttackEligibility(unit, params)
    local ok, code, reason = pcall(function()
        local moves = unit:GetMovesRemaining()
        if type(moves) ~= "number" or moves ~= moves or moves == math.huge then
            return "ATTACK_PATH_UNKNOWN", "Native movement points unavailable; no attack submitted."
        end
        if moves <= 0 then return "NO_MOVES", "Unit has no movement points for melee attack." end
        local zoc = unit:HasMovedIntoZOC()
        if zoc == true then return "ZOC", "Unit entered Zone of Control this turn; no attack submitted." end
        if zoc ~= false then return "ATTACK_PATH_UNKNOWN", "Native Zone of Control status unavailable; no attack submitted." end
        if UnitManager.CanStartOperation(unit, UnitOperationTypes.MOVE_TO, nil, params) ~= true then
            return "ATTACK_BLOCKED", "Native melee target unavailable; no attack submitted."
        end
        if UI.IsGameCoreBusy() ~= false then
            return "ATTACK_PATH_UNKNOWN", "Native movement path unavailable while core is busy; no attack submitted."
        end
        local tx, ty = params[UnitOperationTypes.PARAM_X], params[UnitOperationTypes.PARAM_Y]
        local destination = Map.GetPlot(tx, ty)
        if not destination then return "ATTACK_PATH_UNKNOWN", "Native target plot unavailable; no attack submitted." end
        local path = UnitManager.GetMoveToPathEx(unit, destination:GetIndex())
        local function integer(value, minimum)
            return type(value) == "number" and value >= minimum and value < math.huge and value == math.floor(value)
        end
        local function length(values)
            if type(values) ~= "table" then return nil end
            local count = 0
            for key in pairs(values) do
                if not integer(key, 1) then return nil end
                count = count + 1
            end
            for i = 1, count do if values[i] == nil then return nil end end
            return count
        end
        if type(path) ~= "table" then return "ATTACK_PATH_UNKNOWN", "Native movement path unavailable; no attack submitted." end
        local count = length(path.plots)
        if count == nil or count < 2 or length(path.turns) ~= count then
            return "ATTACK_PATH_UNKNOWN", "Native movement path incomplete; no attack submitted."
        end
        for i = 1, count do
            if not integer(path.plots[i], 0) or not integer(path.turns[i], 1)
                or (i > 1 and path.turns[i] < path.turns[i-1]) then
                return "ATTACK_PATH_UNKNOWN", "Native movement path malformed; no attack submitted."
            end
            local plot = Map.GetPlotByIndex(path.plots[i])
            if not plot or (i == 1 and (plot:GetX() ~= unit:GetX() or plot:GetY() ~= unit:GetY()))
                or (i == count and (plot:GetX() ~= tx or plot:GetY() ~= ty)) then
                return "ATTACK_PATH_UNKNOWN", "Native movement path does not match attack; no attack submitted."
            end
        end
        if path.turns[1] ~= 1 then
            return "ATTACK_PATH_UNKNOWN", "Native movement path has no current-turn origin; no attack submitted."
        end
        if path.turns[count] > 1 then
            return "ATTACK_DEFERRED", "Native melee path arrives after this turn; no attack submitted."
        end
    end)
    if not ok then return "ATTACK_PATH_UNKNOWN", "Native melee eligibility unavailable; no attack submitted." end
    return code, reason
end
"""


def build_attack_unit(unit_index: int, target_x: int, target_y: int) -> str:
    return f"""
{_lua_get_unit(unit_index)}
{_LUA_ATTACK_MODE}
{_LUA_MELEE_ELIGIBILITY}
local mode, unitInfo, attackRange = classifyUnitAttack(unit)
if not mode then {_bail("ERR:ATTACK_CAPABILITY_UNKNOWN|Unit has no known attack capability")} end
local isRanged = mode == "RANGED" or mode == "BOMBARD"
local isAir = mode == "AIR"
local ux, uy = unit:GetX(), unit:GetY()
local dist = Map.GetPlotDistance(ux, uy, {target_x}, {target_y})
-- Find hostile unit on target tile (prefer military over civilian)
local enemy = nil
local enemyName = "unknown"
local tgtUnits = Map.GetUnitsAt({target_x}, {target_y})
if tgtUnits then
    local fallback = nil
    local fallbackName = "unknown"
    for other in tgtUnits:Units() do
        if other:GetOwner() ~= me then
            local eInfo = GameInfo.Units[other:GetType()]
            local eName = eInfo and eInfo.UnitType or "UNKNOWN"
            local eCombat = eInfo and eInfo.Combat or 0
            if eCombat > 0 then
                enemy = other
                enemyName = eName
                break
            elseif fallback == nil then
                fallback = other
                fallbackName = eName
            end
        end
    end
    if enemy == nil and fallback then enemy = fallback; enemyName = fallbackName end
end
if enemy == nil then
    {_bail(f"ERR:NO_ENEMY|No hostile unit at ({target_x},{target_y})")}
end
-- Check diplomatic status — can only attack units you're at war with (barbarians always attackable)
local enemyOwner = enemy:GetOwner()
if enemyOwner ~= 63 then
    local pDiplo = Players[me]:GetDiplomacy()
    if not pDiplo:IsAtWarWith(enemyOwner) then
        local ownerCfg = PlayerConfigurations[enemyOwner]
        local ownerName = ownerCfg and Locale.Lookup(ownerCfg:GetCivilizationDescription()) or ("player " .. enemyOwner)
        {_bail_lua('"ERR:NOT_AT_WAR|Cannot attack " .. enemyName .. " — you are at peace with " .. ownerName .. ". Declare war first or target a different unit."')}
    end
end
local enemyHP = enemy:GetMaxDamage() - enemy:GetDamage()
local enemyMaxHP = enemy:GetMaxDamage()
local myHP = unit:GetMaxDamage() - unit:GetDamage()
local beforeTag = "|target_owner:" .. enemyOwner .. "|target_id:" .. enemy:GetID() .. "|attacks_before:" .. unit:GetAttacksRemaining()
local params = {{}}
params[UnitOperationTypes.PARAM_X] = {target_x}
params[UnitOperationTypes.PARAM_Y] = {target_y}
if isRanged then
    if unit:GetMovesRemaining() <= 0 then
        {_bail("ERR:NO_MOVES|Unit has no movement points for ranged attack. Ranged attacks require movement. Move and attack on separate turns, or attack before moving.")}
    end
    local rng = attackRange
    if not rng or rng <= 0 then {_bail("ERR:ATTACK_CAPABILITY_UNKNOWN|Attack range unavailable")} end
    if dist > rng then
        {_bail_lua('"ERR:OUT_OF_RANGE|Target at distance " .. dist .. " but range is " .. rng .. ". Move closer first."')}
    end
    -- LOS check: CanStartOperation with target params is authoritative;
    -- GetOperationTargets returns empty for some valid targets (naval units, etc.)
    local canRanged = UnitManager.CanStartOperation(unit, UnitOperationTypes.RANGE_ATTACK, nil, params)
    if canRanged then
        UnitManager.RequestOperation(unit, UnitOperationTypes.RANGE_ATTACK, params)
        print("OK:RANGE_ATTACK|target:" .. enemyName .. " at ({target_x},{target_y})|pre_hp:" .. enemyHP .. "/" .. enemyMaxHP .. "|your HP:" .. myHP .. "|range:" .. rng .. " dist:" .. dist .. beforeTag)
        print("{SENTINEL}"); return
    else
        {_bail_lua(f'"ERR:NO_LOS|Cannot ranged-attack target at ({target_x},{target_y}) from (" .. ux .. "," .. uy .. "). LOS blocked or unit already attacked this turn."')}
    end
end
if isAir then
    -- Air units (jet bombers, jet fighters, bombers, fighters): use AIR_ATTACK operation.
    -- Combat resolves asynchronously in the UI so post-combat HP reads may be stale.
    local rng = attackRange
    if not rng or rng <= 0 then {_bail("ERR:ATTACK_CAPABILITY_UNKNOWN|Attack range unavailable")} end
    if dist > rng then
        {_bail_lua('"ERR:OUT_OF_RANGE|Target at distance " .. dist .. " but air range is " .. rng .. ". Rebase closer first."')}
    end
    if not UnitManager.CanStartOperation(unit, UnitOperationTypes.AIR_ATTACK, nil, params) then
        {_bail("ERR:AIR_ATTACK_BLOCKED|Native air target unavailable")}
    end
    UnitManager.RequestOperation(unit, UnitOperationTypes.AIR_ATTACK, params)
    print("OK:AIR_ATTACK|target:" .. enemyName .. " at ({target_x},{target_y})|pre_hp:" .. enemyHP .. "/" .. enemyMaxHP .. "|bomber HP:" .. myHP .. "|range:" .. rng .. " dist:" .. dist .. beforeTag)
else
    local myCS = unitInfo and unitInfo.Combat or 0
    params[UnitOperationTypes.PARAM_MODIFIERS] = UnitOperationMoveModifiers.ATTACK
    local code, reason = meleeAttackEligibility(unit, params)
    if code then
        {_bail_lua('"ERR:" .. code .. "|" .. reason')}
    end
    UnitManager.RequestOperation(unit, UnitOperationTypes.MOVE_TO, params)
    print("OK:MELEE_ATTACK|target:" .. enemyName .. " at ({target_x},{target_y})|pre_hp:" .. enemyHP .. "/" .. enemyMaxHP .. "|your HP:" .. myHP .. " CS:" .. myCS .. beforeTag)
end
print("{SENTINEL}")
"""


def build_attack_followup_query(target_x: int, target_y: int, attacker_index: int | None = None) -> str:
    """InGame context: get actual HP of units at target tile after combat.

    Also checks for city defenses (walls/garrison) at the target — when
    attacking a walled city, damage goes to walls first so the garrison
    unit's HP stays unchanged even though the attack succeeded.

    Runs in InGame context because enemy city district APIs
    (GetDistricts, GetMaxDamage) are not available in GameCore.
    """
    attacker_probe = ""
    if attacker_index is not None:
        attacker_probe = f"""
local me = Game.GetLocalPlayer()
local attacker = Players[me]:GetUnits():FindID({attacker_index})
if attacker then
    local busy = "unknown"
    pcall(function() busy = tostring(UI.IsGameCoreBusy()) end)
    print("ATTACKER|hp:" .. (attacker:GetMaxDamage()-attacker:GetDamage()) .. "|moves:" .. attacker:GetMovesRemaining() .. "|attacks:" .. attacker:GetAttacksRemaining() .. "|busy:" .. busy)
else print("ATTACKER|gone") end
"""
    return f"""
{attacker_probe}
local coreBusy = "unknown"
pcall(function() coreBusy = tostring(UI.IsGameCoreBusy()) end)
print("CORE_BUSY|" .. coreBusy)
local found = false
local visible = false
pcall(function()
    local visibility = PlayersVisibility[Game.GetLocalPlayer()]
    local plot = Map.GetPlot({target_x}, {target_y})
    visible = visibility and plot and visibility:IsVisible(plot:GetIndex())
end)
if visible then
for i = 0, 63 do
    if Players[i] and Players[i]:IsAlive() then
        for _, u in Players[i]:GetUnits():Members() do
            if u:GetX() == {target_x} and u:GetY() == {target_y} then
                local hp = u:GetMaxDamage() - u:GetDamage()
                local entry = GameInfo.Units[u:GetType()]
                local name = entry and entry.UnitType or "UNKNOWN"
                print("UNIT|" .. name .. "|" .. hp .. "/" .. u:GetMaxDamage() .. "|owner:" .. i .. "|id:" .. u:GetID())
                found = true
            end
        end
        pcall(function()
            for _, c in Players[i]:GetCities():Members() do
                if c:GetX() == {target_x} and c:GetY() == {target_y} then
                    local ccIdx = GameInfo.Districts["DISTRICT_CITY_CENTER"].Index
                    for _, d in c:GetDistricts():Members() do
                        if d:GetType() == ccIdx then
                            pcall(function()
                                local wMax = d:GetMaxDamage(DefenseTypes.DISTRICT_OUTER) or 0
                                local wHP = wMax - (d:GetDamage(DefenseTypes.DISTRICT_OUTER) or 0)
                                local gMax = d:GetMaxDamage(DefenseTypes.DISTRICT_GARRISON) or 0
                                local gHP = gMax - (d:GetDamage(DefenseTypes.DISTRICT_GARRISON) or 0)
                                if wMax > 0 or gMax > 0 then
                                    print("CITY_DEF|wall:" .. wHP .. "/" .. wMax .. "|garrison:" .. gHP .. "/" .. gMax)
                                end
                            end)
                            break
                        end
                    end
                end
            end
        end)
    end
end
end
if not visible then print("TARGET_NOT_VISIBLE") elseif not found then print("EMPTY") end
print("{SENTINEL}")
"""


def parse_blocked_diagnostic(lines: list[str]) -> str:
    """Extract human-readable block reason from diagnostic Lua output."""
    for line in lines:
        if line.startswith("DIAG|"):
            parts = line.split("|", 2)
            if len(parts) >= 3:
                return parts[2]
    return "unit did not move — impassable terrain, border, or no path"


def build_combat_estimate_query(unit_index: int, target_x: int, target_y: int) -> str:
    """InGame: native combat preview, without executing an attack.

    UnitPanel.lua uses SimulateAttackVersus for an exact unit and
    SimulateAttackInto for a plot. The engine evaluates conditional promotions;
    an unavailable preview must never fall back to summing modifier amounts.
    """
    return f"""
{_lua_get_unit(unit_index)}
{_LUA_ATTACK_MODE}
{_LUA_MELEE_ELIGIBILITY}
local function preview()
    if not UI or not UI.IsGameCoreBusy or UI.IsGameCoreBusy() ~= false then
        return "ESTIMATE_UNKNOWN|core busy or state unavailable"
    end
    if not CombatManager or not CombatResultParameters then
        return "ESTIMATE_UNKNOWN|native combat preview unavailable"
    end
    local params = {{}}
    params[UnitOperationTypes.PARAM_X] = {target_x}
    params[UnitOperationTypes.PARAM_Y] = {target_y}
    local mode, info, range = classifyUnitAttack(unit)
    if not mode then return "ESTIMATE_UNKNOWN|unit attack capability unavailable" end
    -- Shipped UnitPanel verifies RANGED/BOMBARD and nil melee. Its air
    -- preview mapping remains unverified; do not guess one.
    if mode == "AIR" then return "ESTIMATE_UNKNOWN|air preview mode unverified" end
    if unit:GetMovesRemaining() <= 0 then return "ESTIMATE_UNKNOWN|unit has no moves" end
    local isRanged = mode == "RANGED" or mode == "BOMBARD"
    local combatType = nil
    if isRanged then
        if not range or range <= 0 or Map.GetPlotDistance(unit:GetX(), unit:GetY(), {target_x}, {target_y}) > range then
            return "ESTIMATE_UNKNOWN|ranged target out of known range"
        end
        if not UnitManager.CanStartOperation(unit, UnitOperationTypes.RANGE_ATTACK, nil, params) then
            return "ESTIMATE_UNKNOWN|ranged target unavailable"
        end
        if mode == "BOMBARD" then combatType = CombatTypes.BOMBARD else combatType = CombatTypes.RANGED end
        if combatType == nil then return "ESTIMATE_UNKNOWN|ranged combat type unavailable" end
    else
        params[UnitOperationTypes.PARAM_MODIFIERS] = UnitOperationMoveModifiers.ATTACK
        local code, reason = meleeAttackEligibility(unit, params)
        if code then return "ESTIMATE_UNKNOWN|" .. reason end
    end
    local enemy = nil
    local tgtUnits = Map.GetUnitsAt({target_x}, {target_y})
    if tgtUnits then
        for other in tgtUnits:Units() do
            if other:GetOwner() ~= me then
                local info = GameInfo.Units[other:GetType()]
                local military = info and (info.Combat or 0) > 0
                if military or enemy == nil then enemy = other end
                if military then break end
            end
        end
    end
    local city = CityManager.GetCityAt({target_x}, {target_y})
    local defenderID, defenderType, defenderHP, owner
    if city then
        owner = city:GetOwner()
        local center = GameInfo.Districts["DISTRICT_CITY_CENTER"]
        for _, district in city:GetDistricts():Members() do
            if center and district:GetType() == center.Index then
                defenderID = district:GetComponentID()
                defenderType = "DISTRICT_CITY_CENTER"
                defenderHP = district:GetMaxDamage(DefenseTypes.DISTRICT_GARRISON) - district:GetDamage(DefenseTypes.DISTRICT_GARRISON)
                break
            end
        end
    elseif enemy then
        owner = enemy:GetOwner()
        defenderID = enemy:GetComponentID()
        local info = GameInfo.Units[enemy:GetType()]
        defenderType = info and info.UnitType or "UNKNOWN"
        defenderHP = enemy:GetMaxDamage() - enemy:GetDamage()
    end
    if not defenderID or owner == me then return "ESTIMATE_UNKNOWN|hostile target unavailable" end
    if owner ~= 63 and not Players[me]:GetDiplomacy():IsAtWarWith(owner) then
        return "ESTIMATE_UNKNOWN|target is not at war"
    end
    local result
    if city then
        result = CombatManager.SimulateAttackInto(unit:GetComponentID(), combatType, {target_x}, {target_y})
    else
        result = CombatManager.SimulateAttackVersus(unit:GetComponentID(), enemy:GetComponentID(), combatType)
    end
    if type(result) ~= "table" then return "ESTIMATE_UNKNOWN|native preview missing" end
    local attacker = result[CombatResultParameters.ATTACKER]
    local defender = result[CombatResultParameters.DEFENDER]
    local function sameComponent(actual, expected)
        return type(actual) == "table" and type(expected) == "table"
            and actual.player ~= nil and actual.id ~= nil and actual.type ~= nil
            and actual.player == expected.player and actual.id == expected.id and actual.type == expected.type
    end
    if type(attacker) ~= "table" or type(defender) ~= "table"
        or not sameComponent(attacker[CombatResultParameters.ID], unit:GetComponentID())
        or not sameComponent(defender[CombatResultParameters.ID], defenderID) then
        return "ESTIMATE_UNKNOWN|native preview target mismatch or malformed"
    end
    local function finite(value)
        return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge
    end
    local function strength(side)
        if not finite(side[CombatResultParameters.COMBAT_STRENGTH]) or not finite(side[CombatResultParameters.STRENGTH_MODIFIER]) then
            return nil
        end
        return side[CombatResultParameters.COMBAT_STRENGTH] + side[CombatResultParameters.STRENGTH_MODIFIER]
    end
    local attCS, defCS = strength(attacker), strength(defender)
    -- DAMAGE_TO is damage suffered by that side, not damage it inflicts.
    local attDamage = attacker[CombatResultParameters.DAMAGE_TO]
    local defDamage = defender[CombatResultParameters.DAMAGE_TO]
    local attHP = unit:GetMaxDamage() - unit:GetDamage()
    if not finite(attCS) or not finite(defCS) or not finite(attDamage) or not finite(defDamage)
        or not finite(attHP) or not finite(defenderHP) or attDamage < 0 or defDamage < 0 then
        return "ESTIMATE_UNKNOWN|native preview values malformed"
    end
    local info = GameInfo.Units[unit:GetType()]
    local attackerType = info and info.UnitType or "UNKNOWN"
    return "NATIVE_ESTIMATE|" .. attackerType .. "|" .. defenderType .. "|" .. attCS .. "|" .. defCS
        .. "|" .. (isRanged and "1" or "0") .. "|" .. attHP .. "|" .. defenderHP .. "|" .. attDamage .. "|" .. defDamage
end
local ok, result = pcall(preview)
print(ok and result or "ESTIMATE_UNKNOWN|native preview unavailable")
print("{SENTINEL}")
"""


def parse_combat_estimate(
    lines: list[str], att_cs: int, def_cs: int
) -> CombatEstimate | None:
    """Read native damage preview; never infer conditional modifiers or damage."""
    for line in lines:
        if not line.startswith("NATIVE_ESTIMATE|"):
            continue
        p = line.split("|")
        if len(p) != 10 or p[5] not in ("0", "1") or not p[1] or not p[2]:
            return None
        try:
            eff_att, eff_def, my_hp, enemy_hp, att_damage, def_damage = (
                int(p[index]) for index in (3, 4, 6, 7, 8, 9)
            )
        except ValueError:
            return None
        if my_hp <= 0 or enemy_hp <= 0 or att_damage < 0 or def_damage < 0:
            return None
        return CombatEstimate(
            attacker_type=p[1], defender_type=p[2],
            attacker_cs=eff_att, defender_cs=eff_def, is_ranged=p[5] == "1",
            modifiers=["native rules; individual modifiers not listed"],
            attacker_hp=my_hp, defender_hp=enemy_hp,
            est_damage_to_attacker=att_damage, est_damage_to_defender=def_damage,
        )
    return None


def build_threat_scan_query() -> str:
    """GameCore: scan for foreign military units visible to the player.

    Scans all players (not just barbarians) but only reports units on tiles
    the player can currently see (PlayersVisibility:IsVisible). No arbitrary
    distance limits — fog of war is the natural filter.

    Uses GameCore context but filters by fog of war — only reports units
    on tiles the player can currently see (PlayersVisibility:IsVisible).
    Reports owner, HP, combat strength, and distance from nearest friendly position.
    """
    return """
local me = Game.GetLocalPlayer()
local pDiplo = Players[me]:GetDiplomacy()
local pVis = PlayersVisibility[me]
local myPos = {}
for _, c in Players[me]:GetCities():Members() do
    table.insert(myPos, {c:GetX(), c:GetY()})
end
for _, u in Players[me]:GetUnits():Members() do
    local ux, uy = u:GetX(), u:GetY()
    if ux ~= -9999 then table.insert(myPos, {ux, uy}) end
end
local found = false
for pid = 0, 63 do
    if pid ~= me and Players[pid] and Players[pid]:IsAlive() then
        local isMajor = Players[pid]:IsMajor()
        local isBarbarian = (pid == 63)
        -- Skip city-state units unless we're at war with them
        if not isMajor and not isBarbarian and not pDiplo:IsAtWarWith(pid) then
            -- City-state, not at war — not a threat
        else
        local ownerName = "Barbarian"
        if pid ~= 63 then
            local cfg = PlayerConfigurations[pid]
            if cfg then ownerName = Locale.Lookup(cfg:GetCivilizationShortDescription()) end
        end
        for _, bu in Players[pid]:GetUnits():Members() do
            local bx, by = bu:GetX(), bu:GetY()
            if bx ~= -9999 and pVis:IsVisible(bx, by) then
                local uType = bu:GetType()
                if uType then
                    local entry = GameInfo.Units[uType]
                    local bcs = entry and entry.Combat or 0
                    if bcs > 0 or (entry and entry.RangedCombat and entry.RangedCombat > 0) then
                        local minDist = 999
                        for _, pos in ipairs(myPos) do
                            local d = Map.GetPlotDistance(pos[1], pos[2], bx, by)
                            if d < minDist then minDist = d end
                        end
                        local name = entry and entry.UnitType or "UNKNOWN"
                        local hp = bu:GetMaxDamage() - bu:GetDamage()
                        local brs = entry and entry.RangedCombat or 0
                        local isCS = Players[pid]:IsMajor() and "0" or "1"
                        print("THREAT|" .. pid .. "|" .. ownerName:gsub("|","/") .. "|" .. name .. "|" .. bx .. "," .. by .. "|" .. hp .. "/" .. bu:GetMaxDamage() .. "|CS:" .. bcs .. "|RS:" .. brs .. "|dist:" .. minDist .. "|cs:" .. isCS .. "|uid:" .. bu:GetID())
                        found = true
                    end
                end
            end
        end
        end -- close city-state skip if/else
    end -- close if pid alive
end -- close for pid
if not found then print("NO_THREATS") end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


_LUA_UNIT_READINESS = """
local function unitReady(unit)
    local ok, value = pcall(function() return unit:IsReadyToMove() end)
    if ok and type(value) == "boolean" then return value end
    return nil
end
"""


def build_fortify_unit(unit_index: int, witness_token: str = "") -> str:
    # Fortify turns describe historical defense, not current readiness.
    return _build_readiness_action(unit_index, "fortify", witness_token)


def build_skip_unit(unit_index: int, witness_token: str = "") -> str:
    """Native skip operation, without changing movement points directly."""
    return _build_readiness_action(unit_index, "skip", witness_token)


def build_cancel_unit_move(unit_index: int, witness_token: str) -> str:
    """Cancel one verified queued movement through the native command."""
    return LUA_TURN_READINESS + f'''
{_lua_get_unit(unit_index)}
if {unit_index} >= 65536 and unit:GetID() ~= {unit_index} then
    {_bail("ERR:UNIT_ID_CHANGED|Runtime unit identity no longer matches")}
end
local inspected,reason,queue=pcall(function()
    local info=GameInfo.Units[unit:GetType()]
    if not info or type(info.Spy)~="boolean" or type(info.IgnoreMoves)~="boolean" then return "READINESS_UNKNOWN" end
    if info.Spy or info.IgnoreMoves then return "NOT_MOVE_QUEUE" end
    local facts=localTurnReadiness(me,unit)
    local queued=facts.queues[unit:GetID()]
    if not facts.queue_identity_known or facts.core_busy~=false then return "READINESS_UNKNOWN" end
    if not queued then return "NO_QUEUED_MOVE" end
    if queued.active_route==true then return "ACTIVE_TRADE_ROUTE" end
    if queued.active_route~=false then return "READINESS_UNKNOWN" end
    return nil,queued
end)
if not inspected then {_bail("ERR:READINESS_UNKNOWN|Cannot verify this unit's queued movement")} end
if reason=="NO_QUEUED_MOVE" then {_bail("OK:NO_QUEUED_MOVE|Existing activity preserved")} end
if reason then {_bail_lua('"ERR:"..reason.."|Queued movement was not cancelled"')} end
local command=GameInfo.UnitCommands["UNITCOMMAND_CANCEL"]
if not command or type(command.Hash)~="number" or not UnitCommandTypes or command.Hash~=UnitCommandTypes.CANCEL
    or type(UnitManager.CanStartCommand)~="function" or type(UnitManager.RequestCommand)~="function" then
    {_bail("ERR:COMMAND_UNAVAILABLE|Native cancel command unavailable")}
end
local checked,allowed=pcall(function() return UnitManager.CanStartCommand(unit,command.Hash,false,true) end)
if not checked or type(allowed)~="boolean" then {_bail("ERR:ELIGIBILITY_UNKNOWN|Native cancel eligibility unknown")} end
if not allowed then {_bail("ERR:CANNOT_CANCEL_MOVE|Native cancel command refused")} end
__civmcpCancelMoveRequest={{token="{witness_token}",owner=me,id=unit:GetID(),
    civ=PlayerConfigurations[me]:GetCivilizationTypeName(),seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),
    turn=Game.GetCurrentGameTurn(),x=queue.x,y=queue.y,target=queue.target,entered=true}}
UnitManager.RequestCommand(unit,command.Hash)
print("OK:CANCEL_MOVE_REQUESTED|unit:"..unit:GetID())
print("{SENTINEL}")
'''


def build_cancel_unit_move_query(unit_index: int, witness_token: str) -> str:
    return f'''
local me=Game.GetLocalPlayer()
local record=__civmcpCancelMoveRequest
local unit=UnitManager.GetUnit(me,{unit_index})
if record and record.token=="{witness_token}" and record.entered==true
    and record.owner==me and record.turn==Game.GetCurrentGameTurn()
    and record.civ==PlayerConfigurations[me]:GetCivilizationTypeName()
    and record.seed==GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
    and unit and record.id==unit:GetID() then
    local busy=nil
    pcall(function() busy=UI.IsGameCoreBusy() end)
    local known,target=pcall(function() return UnitManager.GetQueuedDestination(unit) end)
    local queue=known and target==nil and "none" or "unknown"
    print("CANCEL_MOVE|"..record.token.."|"..me.."|"..record.id
        .."|from:"..record.x..","..record.y.."|now:"..unit:GetX()..","..unit:GetY()
        .."|queue:"..queue.."|busy:"..tostring(busy).."|entered:true")
end
print("{SENTINEL}")
'''


def build_skip_remaining_units() -> str:
    """Read a finite candidate list; callers submit each candidate once."""
    return _LUA_UNIT_READINESS + """
local me = Game.GetLocalPlayer()
for _, unit in Players[me]:GetUnits():Members() do
    if unit:GetX() ~= -9999 then
        local ready = unitReady(unit)
        if ready == nil then
            print("ERR:READINESS_UNKNOWN|Cannot determine unit readiness")
            print("{SENTINEL}"); return
        end
        if ready then print("READY_UNIT|" .. unit:GetID()) end
    end
end
print("OK:READY_UNITS")
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def _build_readiness_action(unit_index: int, kind: str, witness_token: str) -> str:
    operation = "SKIP_TURN" if kind == "skip" else "FORTIFY"
    result = "SKIPPED" if kind == "skip" else "FORTIFIED"
    return f'''
{_lua_get_unit(unit_index)}
if {unit_index} >= 65536 and unit:GetID() ~= {unit_index} then
    {_bail("ERR:UNIT_ID_CHANGED|Runtime unit identity no longer matches")}
end
{_LUA_UNIT_READINESS}
local ready = unitReady(unit)
if ready == nil then {_bail("ERR:READINESS_UNKNOWN|Cannot determine current unit readiness")} end
if not ready then
    local activity = nil
    pcall(function() activity = UnitManager.GetActivityType(unit) end)
    local waiting = ActivityTypes and activity ~= nil
        and (activity == ActivityTypes.ACTIVITY_SLEEP or activity == ActivityTypes.ACTIVITY_HOLD)
    if waiting then
        print("OK:ALREADY_WAITING|activity:" .. tostring(activity) .. "|Current native activity preserved")
    else
        print("OK:NOT_READY|activity:" .. tostring(activity) .. "|Existing activity preserved")
    end
    print("{SENTINEL}"); return
end
if not UnitManager.CanStartOperation or not UnitManager.RequestOperation then
    {_bail("ERR:OPERATION_UNAVAILABLE|Native unit operation API unavailable")}
end
local op = GameInfo.UnitOperations["UNITOPERATION_{operation}"]
local label = "{result}"
local ok, allowed = pcall(function()
    return op and UnitManager.CanStartOperation(unit, op.Hash, nil, true)
end)
if "{kind}" == "fortify" and ok and allowed == false then
    op = GameInfo.UnitOperations["UNITOPERATION_SLEEP"]
    label = "SLEEPING"
    ok, allowed = pcall(function()
        return op and UnitManager.CanStartOperation(unit, op.Hash, nil, true)
    end)
end
if not op then {_bail("ERR:OPERATION_UNAVAILABLE|Native unit operation missing")} end
if not ok or type(allowed) ~= "boolean" then
    {_bail("ERR:ELIGIBILITY_UNKNOWN|Native operation eligibility unavailable")}
end
if not allowed then {_bail("ERR:CANNOT_OPERATE|Native unit operation rejected")} end
-- Capture the full runtime ID before entering RequestOperation. Lost replies
-- may be confirmed only from this request's original unit and native scope.
__civmcpReadinessRequest = {{token="{witness_token}", kind="{kind}", owner=me,
    id=unit:GetID(), turn=Game.GetCurrentGameTurn(),
    civ=PlayerConfigurations[me]:GetCivilizationTypeName(),
    seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"), label=label}}
UnitManager.RequestOperation(unit, op.Hash, {{}})
print("OK:READINESS_REQUESTED|" .. label)
print("{SENTINEL}")
'''


def build_unit_readiness_query(unit_index: int, kind: str, witness_token: str) -> str:
    return f'''
{_LUA_UNIT_READINESS}
local me = Game.GetLocalPlayer()
local record = __civmcpReadinessRequest
local unit = UnitManager.GetUnit(me, {unit_index})
if record and record.token == "{witness_token}" and record.kind == "{kind}"
    and record.owner == me and record.turn == Game.GetCurrentGameTurn()
    and record.civ == PlayerConfigurations[me]:GetCivilizationTypeName()
    and record.seed == GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
    and unit and record.id == unit:GetID() then
    local busy = nil
    pcall(function() busy = UI.IsGameCoreBusy() end)
    print("READINESS|" .. record.token .. "|" .. record.owner .. "|" .. record.id
        .. "|ready:" .. tostring(unitReady(unit)) .. "|busy:" .. tostring(busy)
        .. "|result:" .. record.label)
end
print("{SENTINEL}")
'''


def build_automate_explore(unit_index: int) -> str:
    """Automate a unit's exploration (InGame context)."""
    return f"""
{_lua_get_unit(unit_index)}
local hash = GameInfo.UnitOperations["UNITOPERATION_AUTOMATE_EXPLORE"].Hash
if not UnitManager.CanStartOperation(unit, hash, nil, nil) then
    {_bail("ERR:CANNOT_AUTOMATE|Unit cannot auto-explore")}
end
UnitManager.RequestOperation(unit, hash, {{}})
print("OK:AUTOMATED|" .. unit:GetX() .. "," .. unit:GetY())
print("{SENTINEL}")
"""


def build_heal_unit(unit_index: int) -> str:
    """Fortify until healed (InGame context). Distinct from plain fortify."""
    return f"""
{_lua_get_unit(unit_index)}
local hp = unit:GetMaxDamage() - unit:GetDamage()
local maxHP = unit:GetMaxDamage()
if hp >= maxHP then {_bail_lua('"ERR:FULL_HP|Unit already at full health (" .. hp .. "/" .. maxHP .. ")"')} end
local healHash = GameInfo.UnitOperations["UNITOPERATION_HEAL"].Hash
if UnitManager.CanStartOperation(unit, healHash, nil, nil) then
    UnitManager.RequestOperation(unit, healHash, {{}})
    print("OK:HEALING|HP:" .. hp .. "/" .. maxHP)
else
    {_bail("ERR:CANNOT_HEAL|Unit cannot fortify-until-healed")}
end
print("{SENTINEL}")
"""


def build_alert_unit(unit_index: int) -> str:
    """Put unit on alert — sleeps but auto-wakes when enemy enters sight (InGame context)."""
    return f"""
{_lua_get_unit(unit_index)}
if UnitManager.CanStartOperation(unit, UnitOperationTypes.ALERT, nil, nil) then
    UnitManager.RequestOperation(unit, UnitOperationTypes.ALERT, {{}})
    print("OK:ALERT|" .. unit:GetX() .. "," .. unit:GetY())
else
    {_bail("ERR:CANNOT_ALERT|Unit cannot be put on alert")}
end
print("{SENTINEL}")
"""


def build_sleep_unit(unit_index: int) -> str:
    """Put unit to sleep — stays until manually woken (InGame context)."""
    return f"""
{_lua_get_unit(unit_index)}
local sleepHash = GameInfo.UnitOperations["UNITOPERATION_SLEEP"].Hash
if UnitManager.CanStartOperation(unit, sleepHash, nil, nil) then
    UnitManager.RequestOperation(unit, sleepHash, {{}})
    print("OK:SLEEPING|" .. unit:GetX() .. "," .. unit:GetY())
else
    {_bail("ERR:CANNOT_SLEEP|Unit cannot sleep")}
end
print("{SENTINEL}")
"""


def build_improve_tile(unit_index: int, improvement_name: str) -> str:
    """Build an improvement with a builder unit (InGame context).

    improvement_name is e.g. IMPROVEMENT_FARM, IMPROVEMENT_MINE, etc.
    """
    return f"""
{_lua_get_unit(unit_index)}
local imp = GameInfo.Improvements["{improvement_name}"]
if imp == nil then
    -- Feature removals (IMPROVEMENT_REMOVE_*) may not be in Improvements table.
    -- Try scanning by ImprovementType name in case indexed lookup fails.
    for row in GameInfo.Improvements() do
        if row.ImprovementType == "{improvement_name}" then imp = row; break end
    end
    if imp == nil then
        -- List all available improvements so the agent can find the correct name
        local available = {{}}
        local params0 = {{}}
        params0[UnitOperationTypes.PARAM_X] = unit:GetX()
        params0[UnitOperationTypes.PARAM_Y] = unit:GetY()
        for row in GameInfo.Improvements() do
            if row.Buildable then
                params0[UnitOperationTypes.PARAM_IMPROVEMENT_TYPE] = row.Hash
                local ok2, canBuild2 = pcall(function()
                    return UnitManager.CanStartOperation(unit, UnitOperationTypes.BUILD_IMPROVEMENT, nil, params0)
                end)
                if ok2 and canBuild2 == true then table.insert(available, row.ImprovementType) end
            end
        end
        local hint = #available > 0 and ". Available here: " .. table.concat(available, ", ") or ""
        {_bail_lua(f'"ERR:IMPROVEMENT_NOT_FOUND|{improvement_name} not in game database" .. hint')}
    end
end
local plot = Map.GetPlot(unit:GetX(), unit:GetY())
if plot:GetOwner() ~= me then {_bail_lua('"ERR:NOT_YOUR_TERRITORY|Tile at " .. unit:GetX() .. "," .. unit:GetY() .. " is not in your territory"')} end
local params = {{}}
params[UnitOperationTypes.PARAM_X] = unit:GetX()
params[UnitOperationTypes.PARAM_Y] = unit:GetY()
params[UnitOperationTypes.PARAM_IMPROVEMENT_TYPE] = imp.Hash
if plot:IsImprovementPillaged() then
    local repairHash = GameInfo.UnitOperations["UNITOPERATION_REPAIR"] and GameInfo.UnitOperations["UNITOPERATION_REPAIR"].Hash
    if repairHash then
        local rParams = {{}}
        rParams[UnitOperationTypes.PARAM_X] = unit:GetX()
        rParams[UnitOperationTypes.PARAM_Y] = unit:GetY()
        -- Include improvement type — REPAIR may need to know WHICH improvement to restore
        local impType = plot:GetImprovementType()
        if impType >= 0 then
            local impRow = GameInfo.Improvements[impType]
            if impRow then rParams[UnitOperationTypes.PARAM_IMPROVEMENT_TYPE] = impRow.Hash end
        end
        local canRepair = UnitManager.CanStartOperation(unit, repairHash, nil, rParams)
        if canRepair then
            UnitManager.RequestOperation(unit, repairHash, rParams)
            print("OK:REPAIRING|{improvement_name}|" .. unit:GetX() .. "," .. unit:GetY())
            print("{SENTINEL}"); return
        else
            -- CanStartOperation is unreliable (stale InGame state) — attempt anyway
            pcall(function() UnitManager.RequestOperation(unit, repairHash, rParams) end)
            -- Check if it worked by re-reading pillage state next frame
            print("WARN:REPAIR_ATTEMPTED|CanStartOperation=false but RequestOperation sent. Verify next turn.")
            print("{SENTINEL}"); return
        end
    end
end
if unit:GetMovesRemaining() <= 0 then
    print("ERR:CANNOT_IMPROVE|Builder has no moves remaining this turn")
    print("{SENTINEL}"); return
end
local canBuild, opResult = UnitManager.CanStartOperation(unit, UnitOperationTypes.BUILD_IMPROVEMENT, nil, params, true)
if not canBuild then
    local reasons = {{}}
    if opResult and opResult.FailureReasons then
        for _, r in ipairs(opResult.FailureReasons) do
            table.insert(reasons, tostring(r))
        end
    end
    local reasonStr = #reasons > 0 and table.concat(reasons, "; ") or "unknown reason"
    -- Add diagnostic context
    local diag = {{}}
    local charges = unit:GetBuildCharges()
    if charges <= 0 then
        table.insert(diag, "builder has 0 charges (will be consumed)")
    end
    local existImp = plot:GetImprovementType()
    if existImp >= 0 then
        local eiRow = GameInfo.Improvements[existImp]
        table.insert(diag, "tile already has " .. (eiRow and eiRow.ImprovementType or "improvement"))
    end
    local fType = plot:GetFeatureType()
    if fType >= 0 then
        local fInfo = GameInfo.Features[fType]
        local fName = fInfo and fInfo.FeatureType or "UNKNOWN"
        table.insert(diag, "tile has " .. fName .. " (use remove_feature first)")
    end
    -- List what CAN be built here
    local alts = {{}}
    for altImp in GameInfo.Improvements() do
        if altImp.Buildable then
            local aParams = {{}}
            aParams[UnitOperationTypes.PARAM_X] = unit:GetX()
            aParams[UnitOperationTypes.PARAM_Y] = unit:GetY()
            aParams[UnitOperationTypes.PARAM_IMPROVEMENT_TYPE] = altImp.Hash
            local ok3, canAlt = pcall(function()
                return UnitManager.CanStartOperation(unit, UnitOperationTypes.BUILD_IMPROVEMENT, nil, aParams)
            end)
            if ok3 and canAlt == true then table.insert(alts, altImp.ImprovementType) end
        end
    end
    if #alts > 0 then
        table.insert(diag, "can build here: " .. table.concat(alts, ", "))
    else
        table.insert(diag, "no improvements can be built on this tile")
    end
    local diagStr = #diag > 0 and ". " .. table.concat(diag, ". ") or ""
    print("ERR:CANNOT_IMPROVE|" .. reasonStr .. diagStr .. ". Builder at " .. unit:GetX() .. "," .. unit:GetY())
    print("{SENTINEL}"); return
end
UnitManager.RequestOperation(unit, UnitOperationTypes.BUILD_IMPROVEMENT, params)
print("OK:IMPROVING|{improvement_name}|" .. unit:GetX() .. "," .. unit:GetY())
print("{SENTINEL}")
"""


def build_remove_feature(unit_index: int) -> str:
    """Remove (chop/harvest) a feature from the tile the builder is standing on.

    Uses UNITOPERATION_REMOVE_FEATURE — works on forest, jungle, marsh.
    The game auto-detects which feature is present; no feature param needed.
    """
    return f"""
{_lua_get_unit(unit_index)}
if unit:GetMovesRemaining() <= 0 then
    {_bail("ERR:NO_MOVES|Builder has no moves remaining this turn")}
end
local plot = Map.GetPlot(unit:GetX(), unit:GetY())
local fType = plot:GetFeatureType()
if fType < 0 then
    {_bail_lua('"ERR:NO_FEATURE|No feature on tile (" .. unit:GetX() .. "," .. unit:GetY() .. ") to remove"')}
end
local fInfo = GameInfo.Features[fType]
local fName = fInfo and fInfo.FeatureType or "UNKNOWN"
local opRow = GameInfo.UnitOperations["UNITOPERATION_REMOVE_FEATURE"]
if not opRow then
    {_bail("ERR:OP_NOT_FOUND|UNITOPERATION_REMOVE_FEATURE not available")}
end
local params = {{}}
params[UnitOperationTypes.PARAM_X] = unit:GetX()
params[UnitOperationTypes.PARAM_Y] = unit:GetY()
local canStart = UnitManager.CanStartOperation(unit, opRow.Hash, nil, params, true)
if not canStart then
    {_bail_lua('"ERR:CANNOT_REMOVE|Cannot remove " .. fName .. " at (" .. unit:GetX() .. "," .. unit:GetY() .. ")"')}
end
UnitManager.RequestOperation(unit, opRow.Hash, params)
print("OK:REMOVING_FEATURE|" .. fName .. " at " .. unit:GetX() .. "," .. unit:GetY())
print("{SENTINEL}")
"""


def build_repair_improvement(unit_index: int) -> str:
    """Repair a pillaged improvement at the builder's current tile (InGame context).

    Auto-detects the pillaged improvement — no improvement name needed.
    """
    return f"""
{_lua_get_unit(unit_index)}
local ux, uy = unit:GetX(), unit:GetY()
if unit:GetMovesRemaining() <= 0 then
    {_bail("ERR:NO_MOVES|Builder has no moves remaining this turn")}
end
local plot = Map.GetPlot(ux, uy)
if not plot then {_bail("ERR:NO_PLOT|Invalid plot")} end
local impType = plot:GetImprovementType()
if impType < 0 then
    {_bail_lua('"ERR:NO_IMPROVEMENT|No improvement on tile (" .. ux .. "," .. uy .. ") to repair"')}
end
local okPil, isPillaged = pcall(function() return plot:IsImprovementPillaged() end)
if not okPil or not isPillaged then
    local impInfo = GameInfo.Improvements[impType]
    local impName = impInfo and impInfo.ImprovementType or "UNKNOWN"
    {_bail_lua('"ERR:NOT_PILLAGED|" .. impName .. " at (" .. ux .. "," .. uy .. ") is not pillaged"')}
end
local impInfo = GameInfo.Improvements[impType]
local impName = impInfo and impInfo.ImprovementType or "UNKNOWN"
local repairOp = GameInfo.UnitOperations["UNITOPERATION_REPAIR"]
if not repairOp then {_bail("ERR:OP_NOT_FOUND|UNITOPERATION_REPAIR not available")} end
local rParams = {{}}
rParams[UnitOperationTypes.PARAM_X] = ux
rParams[UnitOperationTypes.PARAM_Y] = uy
if impInfo then rParams[UnitOperationTypes.PARAM_IMPROVEMENT_TYPE] = impInfo.Hash end
local canRepair = UnitManager.CanStartOperation(unit, repairOp.Hash, nil, rParams)
if canRepair then
    UnitManager.RequestOperation(unit, repairOp.Hash, rParams)
    print("OK:REPAIRING|" .. impName .. " at (" .. ux .. "," .. uy .. ")")
else
    pcall(function() UnitManager.RequestOperation(unit, repairOp.Hash, rParams) end)
    print("WARN:REPAIR_ATTEMPTED|CanStartOperation=false but RequestOperation sent for " .. impName .. " at (" .. ux .. "," .. uy .. "). Verify next turn.")
end
print("{SENTINEL}")
"""


def build_remove_improvement(unit_index: int) -> str:
    """Remove (demolish) an intact improvement from the builder's current tile.

    Uses UNITOPERATION_REMOVE_IMPROVEMENT. The game auto-detects which
    improvement is present; no improvement param needed. Costs one builder charge.
    """
    return f"""
{_lua_get_unit(unit_index)}
local ux, uy = unit:GetX(), unit:GetY()
if unit:GetMovesRemaining() <= 0 then
    {_bail("ERR:NO_MOVES|Builder has no moves remaining this turn")}
end
local plot = Map.GetPlot(ux, uy)
if not plot then {_bail("ERR:NO_PLOT|Invalid plot")} end
local impType = plot:GetImprovementType()
if impType < 0 then
    {_bail_lua('"ERR:NO_IMPROVEMENT|No improvement on tile (" .. ux .. "," .. uy .. ") to remove"')}
end
local impInfo = GameInfo.Improvements[impType]
local impName = impInfo and impInfo.ImprovementType or "UNKNOWN"
local opRow = GameInfo.UnitOperations["UNITOPERATION_REMOVE_IMPROVEMENT"]
if not opRow then
    {_bail("ERR:OP_NOT_FOUND|UNITOPERATION_REMOVE_IMPROVEMENT not available in this game version")}
end
local params = {{}}
params[UnitOperationTypes.PARAM_X] = ux
params[UnitOperationTypes.PARAM_Y] = uy
local canStart = UnitManager.CanStartOperation(unit, opRow.Hash, nil, params, true)
if not canStart then
    {_bail_lua('"ERR:CANNOT_REMOVE|Cannot remove " .. impName .. " at (" .. ux .. "," .. uy .. "). Builder must be on the tile with moves and charges."')}
end
UnitManager.RequestOperation(unit, opRow.Hash, params)
print("OK:REMOVING_IMPROVEMENT|" .. impName .. " at (" .. ux .. "," .. uy .. ")")
print("{SENTINEL}")
"""



def build_build_route(unit_index: int) -> str:
    """Build a route (road/railroad) on the Military Engineer's current tile.

    Uses UNITOPERATION_BUILD_ROUTE — after Steam Power tech this builds
    railroads (route type 4).  Does NOT consume charges.  Costs 1 Iron +
    1 Coal per railroad tile from the player's stockpile.
    """
    return f"""
{_lua_get_unit(unit_index)}
if unit:GetMovesRemaining() <= 0 then
    {_bail("ERR:NO_MOVES|Military Engineer has no moves remaining this turn")}
end
local x, y = unit:GetX(), unit:GetY()
local plot = Map.GetPlot(x, y)
if not plot or plot:GetOwner() ~= me then
    {_bail_lua('"ERR:NOT_YOUR_TERRITORY|Tile (" .. x .. "," .. y .. ") is not in your territory"')}
end
local opRow = GameInfo.UnitOperations["UNITOPERATION_BUILD_ROUTE"]
if not opRow then
    {_bail("ERR:OP_NOT_FOUND|UNITOPERATION_BUILD_ROUTE not in game database")}
end
local params = {{}}
params[UnitOperationTypes.PARAM_X] = x
params[UnitOperationTypes.PARAM_Y] = y
local canStart = UnitManager.CanStartOperation(unit, opRow.Hash, nil, params, true)
if not canStart then
    local rt = plot:GetRouteType()
    local reason = "unknown reason"
    if rt == 4 then reason = "tile already has a railroad"
    elseif plot:IsCity() then reason = "cannot build on city center"
    end
    {_bail_lua('"ERR:CANNOT_BUILD_ROUTE|" .. reason .. " at (" .. x .. "," .. y .. ")"')}
end
UnitManager.RequestOperation(unit, opRow.Hash, params)
-- Read back route type (may be stale same-frame, but try)
local newRoute = plot:GetRouteType()
local routeName = "ROUTE"
if newRoute == 4 then routeName = "RAILROAD"
elseif newRoute >= 0 then routeName = "ROAD"
end
print("OK:BUILT_" .. routeName .. "|" .. x .. "," .. y)
print("{SENTINEL}")
"""


def parse_units_response(lines: list[str]) -> list[UnitInfo]:
    units = []
    for line in lines:
        parts = line.split("|")
        if len(parts) < 7:
            continue
        x_str, y_str = parts[4].split(",")
        moves_cur, moves_max = parts[5].split("/")
        hp_cur, hp_max = parts[6].split("/")
        cs = int(parts[7]) if len(parts) > 7 else 0
        rs = int(parts[8]) if len(parts) > 8 else 0
        charges = int(parts[9]) if len(parts) > 9 else 0
        targets_raw = parts[10] if len(parts) > 10 else ""
        targets = [t for t in targets_raw.split(";") if t] if targets_raw else []
        needs_promo = parts[11] == "1" if len(parts) > 11 else False
        can_upgrade = parts[12] == "1" if len(parts) > 12 else False
        upgrade_target = parts[13] if len(parts) > 13 else ""
        upgrade_cost = int(parts[14]) if len(parts) > 14 and parts[14].isdigit() else 0
        valid_imps_raw = parts[15] if len(parts) > 15 else ""
        valid_imps = (
            [v for v in valid_imps_raw.split(";") if v] if valid_imps_raw else []
        )
        religion = parts[16] if len(parts) > 16 else ""
        units.append(
            UnitInfo(
                unit_id=int(parts[0]),
                unit_index=int(parts[1]),
                name=parts[2],
                unit_type=parts[3],
                x=int(x_str),
                y=int(y_str),
                moves_remaining=float(moves_cur),
                max_moves=float(moves_max),
                health=int(hp_cur),
                max_health=int(hp_max),
                combat_strength=cs,
                ranged_strength=rs,
                build_charges=charges,
                targets=targets,
                needs_promotion=needs_promo,
                can_upgrade=can_upgrade,
                upgrade_target=upgrade_target,
                upgrade_cost=upgrade_cost,
                valid_improvements=valid_imps,
                religion=religion,
            )
        )
    return units


def parse_threat_scan_response(lines: list[str]) -> list[ThreatInfo]:
    threats: list[ThreatInfo] = []
    for line in lines:
        if not line.startswith("THREAT|"):
            continue
        parts = line.split("|")
        # Format: THREAT|owner_id|owner_name|unit_type|x,y|hp/max|CS:n|RS:n|dist:n|cs:0/1|uid:N
        if len(parts) >= 9:
            x_str, y_str = parts[4].split(",")
            hp_str, max_str = parts[5].split("/")
            cs = int(parts[6].replace("CS:", "")) if parts[6].startswith("CS:") else 0
            rs = int(parts[7].replace("RS:", "")) if parts[7].startswith("RS:") else 0
            dist = (
                int(parts[8].replace("dist:", ""))
                if parts[8].startswith("dist:")
                else 0
            )
            uid = 0
            if len(parts) > 10 and parts[10].startswith("uid:"):
                uid = int(parts[10][4:])
            threats.append(
                ThreatInfo(
                    unit_type=parts[3],
                    x=int(x_str),
                    y=int(y_str),
                    hp=int(hp_str),
                    max_hp=int(max_str),
                    combat_strength=cs,
                    ranged_strength=rs,
                    distance=dist,
                    owner_id=int(parts[1]),
                    owner_name=parts[2],
                    is_city_state=len(parts) > 9
                    and parts[9].startswith("cs:")
                    and parts[9][3:] == "1",
                    unit_id=uid,
                )
            )
        elif len(parts) >= 7:
            # Legacy format fallback: THREAT|unit_type|x,y|hp/max|CS:n|RS:n|dist:n
            x_str, y_str = parts[2].split(",")
            hp_str, max_str = parts[3].split("/")
            cs = int(parts[4].replace("CS:", "")) if parts[4].startswith("CS:") else 0
            rs = int(parts[5].replace("RS:", "")) if parts[5].startswith("RS:") else 0
            dist = (
                int(parts[6].replace("dist:", ""))
                if parts[6].startswith("dist:")
                else 0
            )
            threats.append(
                ThreatInfo(
                    unit_type=parts[1],
                    x=int(x_str),
                    y=int(y_str),
                    hp=int(hp_str),
                    max_hp=int(max_str),
                    combat_strength=cs,
                    ranged_strength=rs,
                    distance=dist,
                )
            )
    return threats


def build_fog_neighbor_query(positions: list[tuple[int, int]]) -> str:
    """GameCore: for each position, report which adjacent tiles are in fog."""
    checks = "\n".join(f"check({x},{y})" for x, y in positions)
    return f"""
local me = Game.GetLocalPlayer()
local pVis = PlayersVisibility[me]
local dirNames = {{"NE","E","SE","SW","W","NW"}}
function check(cx, cy)
    local plot = Map.GetPlot(cx, cy)
    if not plot then return end
    local fog = {{}}
    for i = 0, 5 do
        local adj = Map.GetAdjacentPlot(cx, cy, i)
        if adj and not pVis:IsVisible(adj:GetX(), adj:GetY()) then
            table.insert(fog, dirNames[i+1])
        end
    end
    if #fog > 0 then
        print("FOG|" .. cx .. "," .. cy .. "|" .. table.concat(fog, ","))
    end
end
{checks}
print("{SENTINEL}")
"""


def parse_fog_neighbor_response(
    lines: list[str],
) -> dict[tuple[int, int], list[str]]:
    """Parse FOG|x,y|dir1,dir2,... lines into {(x,y): [directions]}."""
    result: dict[tuple[int, int], list[str]] = {}
    for line in lines:
        if not line.startswith("FOG|"):
            continue
        parts = line.split("|")
        x_str, y_str = parts[1].split(",")
        result[(int(x_str), int(y_str))] = parts[2].split(",")
    return result


def diff_threats(
    before: list[ThreatInfo], after: list[ThreatInfo]
) -> tuple[list[ThreatInfo], list[ThreatInfo], list[ThreatInfo]]:
    """Compare threat snapshots: (disappeared, new, moved).

    Match by unit_id when available, otherwise by (owner_id, unit_type, x, y).
    """
    after_by_uid: dict[int, ThreatInfo] = {}
    after_by_key: dict[tuple, ThreatInfo] = {}
    after_matched: set[int] = set()

    for i, t in enumerate(after):
        if t.unit_id:
            after_by_uid[t.unit_id] = t
        after_by_key[(t.owner_id, t.unit_type, t.x, t.y)] = t

    disappeared: list[ThreatInfo] = []
    moved: list[ThreatInfo] = []

    for bt in before:
        at = None
        if bt.unit_id and bt.unit_id in after_by_uid:
            at = after_by_uid[bt.unit_id]
        elif (bt.owner_id, bt.unit_type, bt.x, bt.y) in after_by_key:
            at = after_by_key[(bt.owner_id, bt.unit_type, bt.x, bt.y)]

        if at is None:
            disappeared.append(bt)
        else:
            idx = after.index(at)
            after_matched.add(idx)
            if at.x != bt.x or at.y != bt.y:
                moved.append(at)

    new_threats = [t for i, t in enumerate(after) if i not in after_matched]
    return disappeared, new_threats, moved


def build_pathing_estimate_query(unit_index: int, target_x: int, target_y: int) -> str:
    """InGame context: estimate turns for a unit to reach a destination.

    Uses UnitManager.GetMoveToPath for the full path and
    UnitManager.GetReachableMovement for this-turn reachable tiles.
    """
    return f"""
{_lua_get_unit(unit_index)}
-- Guard: GetMoveToPath returns degenerate paths for units with 0 moves
if unit:GetMovesRemaining() <= 0 then
    print("PATH|-2|0|0")
    print("WAYPOINTS|")
    print("{SENTINEL}")
    return
end
local targetPlot = Map.GetPlot({target_x}, {target_y})
if not targetPlot then {_bail(f"ERR:INVALID_TARGET|Target ({target_x},{target_y}) is out of bounds")} end
local path = UnitManager.GetMoveToPath(unit, targetPlot:GetIndex())
if not path or #path == 0 then
    print("PATH|-1|0|0")
    print("WAYPOINTS|")
    print("{SENTINEL}")
    return
end
-- Validate path reaches destination (GetMoveToPath returns garbage for unreachable targets)
local lastPlot = Map.GetPlotByIndex(path[#path])
if lastPlot:GetX() ~= {target_x} or lastPlot:GetY() ~= {target_y} then
    print("PATH|-1|" .. #path .. "|0")
    print("WAYPOINTS|")
    print("{SENTINEL}")
    return
end
local reach = UnitManager.GetReachableMovement(unit)
local reachSet = {{}}
if reach then
    for _, pIdx in ipairs(reach) do reachSet[pIdx] = true end
end
-- Count how many path tiles are reachable this turn
local reachCount = 0
for _, pIdx in ipairs(path) do
    if reachSet[pIdx] then reachCount = reachCount + 1 end
end
local totalTiles = #path
local tilesPerTurn = math.max(reachCount, 1)
local turnsNeeded
if reachCount >= totalTiles then
    turnsNeeded = 0
else
    turnsNeeded = math.ceil((totalTiles - reachCount) / tilesPerTurn)
end
print("PATH|" .. turnsNeeded .. "|" .. totalTiles .. "|" .. reachCount)
-- Emit waypoints for context (first tile, last reachable, destination)
local waypoints = {{}}
for i, pIdx in ipairs(path) do
    local plot = Map.GetPlotByIndex(pIdx)
    waypoints[#waypoints + 1] = "(" .. plot:GetX() .. "," .. plot:GetY() .. ")"
end
print("WAYPOINTS|" .. table.concat(waypoints, ";"))
print("{SENTINEL}")
"""


def parse_pathing_estimate(lines: list[str]) -> PathingEstimate:
    """Parse PATH| and WAYPOINTS| output."""
    est = PathingEstimate(turns=0, total_tiles=0, reachable_this_turn=0, waypoints=[])
    for line in lines:
        if line.startswith("PATH|"):
            parts = line.split("|")
            if len(parts) >= 4:
                est.turns = int(parts[1])
                est.total_tiles = int(parts[2])
                est.reachable_this_turn = int(parts[3])
        elif line.startswith("WAYPOINTS|"):
            est.waypoints = line.split("|", 1)[1].split(";")
    return est


# ── Post-move visibility ────────────────────────────────────────────────


def build_post_move_visibility_query(now_x: int, now_y: int, radius: int = 4) -> str:
    """GameCore: scan tiles around a position and return revealed tile data.

    Used after a unit move to compute newly-revealed tiles via Python-side diff.
    Radius 4 covers all standard sight ranges (2 for most units, 3 for scouts).
    Output: ``TILE|x,y|terrain|feature|resource:class|hills|camp|units|city``
    """
    return f"""
local cx, cy, r = {now_x}, {now_y}, {radius}
local me = Game.GetLocalPlayer()
local vis = PlayersVisibility[me]
local pTech = Players[me]:GetTechs()
{_LUA_RES_VISIBLE}
for dy = -r, r do
    for dx = -r, r do
        local x, y = cx + dx, cy + dy
        local plot = Map.GetPlot(x, y)
        if plot and vis:IsRevealed(plot:GetX(), plot:GetY()) then
            local terrain = GameInfo.Terrains[plot:GetTerrainType()].TerrainType
            local feature = "none"
            local fi = plot:GetFeatureType()
            if fi >= 0 then feature = GameInfo.Features[fi].FeatureType end
            local resource = "none"
            local ri = plot:GetResourceType()
            if ri >= 0 then
                local re = GameInfo.Resources[ri]
                if resVisible(re) then
                    resource = re.ResourceType .. ":" .. (re.ResourceClassType or "")
                end
            end
            local hills = plot:IsHills() and "1" or "0"
            local camp = "0"
            local ii = plot:GetImprovementType()
            if ii >= 0 then
                local iInfo = GameInfo.Improvements[ii]
                if iInfo and iInfo.ImprovementType == "IMPROVEMENT_BARBARIAN_CAMP" then
                    camp = "1"
                end
            end
            local units = "none"
            if vis:IsVisible(plot:GetX(), plot:GetY()) then
                local uParts = {{}}
                for pid = 0, 63 do
                    if pid ~= me and Players[pid] and Players[pid]:IsAlive() then
                        for _, u in Players[pid]:GetUnits():Members() do
                            if u:GetX() == x and u:GetY() == y then
                                local entry = GameInfo.Units[u:GetType()]
                                local nm = entry and entry.UnitType or "UNKNOWN"
                                local ownerLabel = "Barbarian"
                                if pid ~= 63 then
                                    local cfg = PlayerConfigurations[pid]
                                    if cfg then ownerLabel = Locale.Lookup(cfg:GetCivilizationShortDescription()) end
                                end
                                table.insert(uParts, ownerLabel .. " " .. nm:gsub("UNIT_", ""))
                            end
                        end
                    end
                end
                if #uParts > 0 then units = table.concat(uParts, ";") end
            end
            local cityName = "none"
            if plot:IsCity() then
                local cOwner = plot:GetOwner()
                if cOwner >= 0 and cOwner ~= me then
                    pcall(function()
                        for _, c in Players[cOwner]:GetCities():Members() do
                            if c:GetX() == x and c:GetY() == y then
                                cityName = Locale.Lookup(c:GetName())
                                break
                            end
                        end
                    end)
                end
            end
            print("TILE|" .. x .. "," .. y .. "|" .. terrain .. "|" .. feature .. "|" .. resource .. "|" .. hills .. "|" .. camp .. "|" .. units .. "|" .. cityName)
        end
    end
end
print("{SENTINEL}")
"""


def parse_post_move_visibility(
    lines: list[str],
) -> list[tuple[int, int, dict]]:
    """Parse TILE| lines from post-move visibility query.

    Returns (x, y, metadata) tuples where metadata contains terrain, feature,
    resource, resource_class, hills, camp, units, and city fields.
    """
    results: list[tuple[int, int, dict]] = []
    for line in lines:
        if not line.startswith("TILE|"):
            continue
        parts = line.split("|")
        if len(parts) < 9:
            continue
        xy = parts[1].split(",")
        x, y = int(xy[0]), int(xy[1])
        # Parse resource into name + class
        resource = None
        resource_class = None
        if parts[4] != "none":
            rp = parts[4].split(":", 1)
            resource = rp[0]
            if len(rp) > 1 and rp[1]:
                resource_class = rp[1].replace("RESOURCECLASS_", "").lower()
        meta = {
            "terrain": parts[2],
            "feature": None if parts[3] == "none" else parts[3],
            "resource": resource,
            "resource_class": resource_class,
            "hills": parts[5] == "1",
            "camp": parts[6] == "1",
            "units": None if parts[7] == "none" else parts[7].split(";"),
            "city": None if parts[8] == "none" else parts[8],
        }
        results.append((x, y, meta))
    return results


def build_builder_tasks_query() -> str:
    """InGame context: scans all owned tiles for improvement tasks and all idle builders.

    Outputs TASK| lines for tiles needing work and BUILDER| lines for builder units.
    Uses hardcoded resource mapping and terrain heuristics for improvement recommendations.
    Does NOT use CanStartOperation with remote tiles (corrupts engine state → crash).
    """
    return _LUA_KNOWN_RESOURCE + """
local me = Game.GetLocalPlayer()
local pCities = Players[me]:GetCities()

-- Gather all builders with charges
local builders = {}
for _, u in Players[me]:GetUnits():Members() do
    local entry = GameInfo.Units[u:GetType()]
    if entry and entry.UnitType == "UNIT_BUILDER" and u:GetBuildCharges() > 0 then
        local bx, by = u:GetX(), u:GetY()
        if bx ~= -9999 then
            table.insert(builders, {id=u:GetID(), idx=u:GetID() % 65536, x=bx, y=by, charges=u:GetBuildCharges(), moves=u:GetMovesRemaining()})
        end
    end
end

-- Hardcoded resource -> improvement mapping (avoids GameInfo.Improvement_ValidResources()
-- iterator which can crash the game engine with EXCEPTION_ACCESS_VIOLATION)
local resImpMap = {
    -- Strategic
    RESOURCE_HORSES="IMPROVEMENT_PASTURE", RESOURCE_IRON="IMPROVEMENT_MINE",
    RESOURCE_NITER="IMPROVEMENT_MINE", RESOURCE_COAL="IMPROVEMENT_MINE",
    RESOURCE_ALUMINUM="IMPROVEMENT_MINE", RESOURCE_URANIUM="IMPROVEMENT_MINE",
    RESOURCE_OIL="IMPROVEMENT_OIL_WELL",
    -- Luxury (mined/quarried)
    RESOURCE_DIAMONDS="IMPROVEMENT_MINE", RESOURCE_JADE="IMPROVEMENT_MINE",
    RESOURCE_MERCURY="IMPROVEMENT_MINE", RESOURCE_SALT="IMPROVEMENT_MINE",
    RESOURCE_SILVER="IMPROVEMENT_MINE", RESOURCE_AMBER="IMPROVEMENT_MINE",
    RESOURCE_GYPSUM="IMPROVEMENT_QUARRY", RESOURCE_MARBLE="IMPROVEMENT_QUARRY",
    -- Luxury (plantation)
    RESOURCE_CITRUS="IMPROVEMENT_PLANTATION", RESOURCE_COCOA="IMPROVEMENT_PLANTATION",
    RESOURCE_COFFEE="IMPROVEMENT_PLANTATION", RESOURCE_COTTON="IMPROVEMENT_PLANTATION",
    RESOURCE_DYES="IMPROVEMENT_PLANTATION", RESOURCE_INCENSE="IMPROVEMENT_PLANTATION",
    RESOURCE_OLIVES="IMPROVEMENT_PLANTATION", RESOURCE_SILK="IMPROVEMENT_PLANTATION",
    RESOURCE_SPICES="IMPROVEMENT_PLANTATION", RESOURCE_SUGAR="IMPROVEMENT_PLANTATION",
    RESOURCE_TEA="IMPROVEMENT_PLANTATION", RESOURCE_TOBACCO="IMPROVEMENT_PLANTATION",
    RESOURCE_WINE="IMPROVEMENT_PLANTATION",
    -- Luxury (camp)
    RESOURCE_FURS="IMPROVEMENT_CAMP", RESOURCE_IVORY="IMPROVEMENT_CAMP",
    RESOURCE_TRUFFLES="IMPROVEMENT_CAMP", RESOURCE_HONEY="IMPROVEMENT_CAMP",
    -- Bonus
    RESOURCE_BANANAS="IMPROVEMENT_PLANTATION", RESOURCE_CATTLE="IMPROVEMENT_PASTURE",
    RESOURCE_SHEEP="IMPROVEMENT_PASTURE", RESOURCE_DEER="IMPROVEMENT_CAMP",
    RESOURCE_COPPER="IMPROVEMENT_MINE", RESOURCE_STONE="IMPROVEMENT_QUARRY",
    RESOURCE_MAIZE="IMPROVEMENT_FARM", RESOURCE_RICE="IMPROVEMENT_FARM",
    RESOURCE_WHEAT="IMPROVEMENT_FARM",
    -- Water (builders can't reach, but listed for completeness)
    RESOURCE_FISH="IMPROVEMENT_FISHING_BOATS", RESOURCE_CRABS="IMPROVEMENT_FISHING_BOATS",
    RESOURCE_PEARLS="IMPROVEMENT_FISHING_BOATS", RESOURCE_TURTLES="IMPROVEMENT_FISHING_BOATS",
    RESOURCE_WHALES="IMPROVEMENT_FISHING_BOATS",
}

-- Scan city territory for tasks
local seen = {}
local normalCount = 0
local resourceVisibilityKnown = true
local maxNormal = 20
for _, city in pCities:Members() do
    local cx, cy = city:GetX(), city:GetY()
    local cityName = Locale.Lookup(city:GetName())
    for dy = -3, 3 do for dx = -3, 3 do
        local px, py = cx + dx, cy + dy
        local key = px .. "," .. py
        if not seen[key] then
            seen[key] = true
            local plot = Map.GetPlot(px, py)
            if plot and plot:GetOwner() == me and not plot:IsWater() and not plot:IsMountain() then
                local distIdx = plot:GetDistrictType()
                local impIdx = plot:GetImprovementType()
                local resIdx = knownResourceIndex(me, plot)
                if resIdx == nil then resourceVisibilityKnown = false end

                -- Skip tiles with districts
                if distIdx < 0 then
                    -- Check for pillaged improvements
                    if impIdx >= 0 then
                        local okP, pil = pcall(function() return plot:IsImprovementPillaged() end)
                        if okP and pil then
                            local impInfo = GameInfo.Improvements[impIdx]
                            local impName = impInfo and impInfo.ImprovementType or "UNKNOWN"
                            -- Find nearest builder
                            local nearId, nearDist = -1, 999
                            for _, b in ipairs(builders) do
                                local d = Map.GetPlotDistance(b.x, b.y, px, py)
                                if d < nearDist then nearDist = d; nearId = b.id end
                            end
                            print("TASK|urgent|" .. px .. "," .. py .. "|REPAIR|" .. impName:gsub("IMPROVEMENT_", "") .. "|pillaged|" .. cityName .. "|" .. nearId .. "|" .. nearDist)
                        end
                    -- Check for unimproved resource tiles
                    elseif resIdx and resIdx >= 0 and impIdx < 0 then
                        local resInfo = GameInfo.Resources[resIdx]
                        if resInfo then
                            local resClass = resInfo.ResourceClassType or ""
                            local resName = resInfo.ResourceType:gsub("RESOURCE_", "")
                            local priority = "normal"
                            if resClass == "RESOURCECLASS_STRATEGIC" then priority = "urgent"
                            elseif resClass == "RESOURCECLASS_LUXURY" then priority = "high"
                            elseif resClass == "RESOURCECLASS_BONUS" then priority = "high"
                            end
                            -- Find valid improvement via resource lookup table
                            local validImp = resImpMap[resInfo.ResourceType] or "UNKNOWN"
                            -- Check tech prerequisite
                            if validImp ~= "UNKNOWN" then
                                local impInfo = GameInfo.Improvements[validImp]
                                if impInfo and impInfo.PrereqTech then
                                    local techInfo = GameInfo.Technologies[impInfo.PrereqTech]
                                    if techInfo and not Players[me]:GetTechs():HasTech(techInfo.Index) then
                                        validImp = validImp .. "_LOCKED"
                                    end
                                end
                            end
                            -- Find nearest builder
                            local nearId, nearDist = -1, 999
                            for _, b in ipairs(builders) do
                                local d = Map.GetPlotDistance(b.x, b.y, px, py)
                                if d < nearDist then nearDist = d; nearId = b.id end
                            end
                            local classShort = "bonus"
                            if resClass == "RESOURCECLASS_STRATEGIC" then classShort = "strategic"
                            elseif resClass == "RESOURCECLASS_LUXURY" then classShort = "luxury"
                            end
                            print("TASK|" .. priority .. "|" .. px .. "," .. py .. "|" .. validImp .. "|" .. resName .. "|" .. classShort .. "|" .. cityName .. "|" .. nearId .. "|" .. nearDist)
                        end
                    -- Check for empty tiles that could use standard improvements (capped)
                    -- Uses terrain heuristics instead of CanStartOperation (which corrupts
                    -- engine state when called with remote tile coordinates, causing
                    -- EXCEPTION_ACCESS_VIOLATION during end_turn)
                    elseif impIdx < 0 and (resIdx == nil or resIdx < 0) and normalCount < maxNormal then
                        local featureIdx = plot:GetFeatureType()
                        local terrIdx = plot:GetTerrainType()
                        local terrInfo = terrIdx >= 0 and GameInfo.Terrains[terrIdx] or nil
                        local terrName = terrInfo and terrInfo.TerrainType or ""
                        local bestImp = nil
                        if plot:IsHills() then
                            bestImp = "IMPROVEMENT_MINE"
                        elseif featureIdx >= 0 then
                            local fInfo = GameInfo.Features[featureIdx]
                            local fName = fInfo and fInfo.FeatureType or ""
                            if fName == "FEATURE_FOREST" then
                                bestImp = "IMPROVEMENT_LUMBER_MILL"
                            end
                            -- Jungle/marsh need removal first, skip
                        elseif terrName == "TERRAIN_DESERT" or terrName == "TERRAIN_SNOW" or terrName == "TERRAIN_TUNDRA" then
                            -- Low-yield terrain, skip
                        else
                            bestImp = "IMPROVEMENT_FARM"
                        end
                        if bestImp then
                            local nearId, nearDist = -1, 999
                            for _, b in ipairs(builders) do
                                local d = Map.GetPlotDistance(b.x, b.y, px, py)
                                if d < nearDist then nearDist = d; nearId = b.id end
                            end
                            print("TASK|normal|" .. px .. "," .. py .. "|" .. bestImp .. "||none|" .. cityName .. "|" .. nearId .. "|" .. nearDist)
                            normalCount = normalCount + 1
                        end
                    end
                end
            end
        end
    end end
end

-- Do not present a partially observed resource board as complete/empty.
print("RESOURCE_VISIBILITY|" .. tostring(resourceVisibilityKnown))

-- Print builder info
for _, b in ipairs(builders) do
    print("BUILDER|" .. b.id .. "|" .. b.idx .. "|" .. b.x .. "," .. b.y .. "|" .. b.charges .. "|" .. string.format("%.1f", b.moves))
end
print("{SENTINEL}")
""".replace("{SENTINEL}", SENTINEL)


def parse_builder_tasks(
    lines: list[str],
) -> BuilderTaskReport:
    """Keep safe advice and explicitly mark incomplete resource observations."""
    resource_visibility_known = None
    if "RESOURCE_VISIBILITY|true" in lines:
        resource_visibility_known = True
    if "RESOURCE_VISIBILITY|false" in lines or "RESOURCE_VISIBILITY_UNKNOWN" in lines:
        resource_visibility_known = False
    tasks: list[BuilderTask] = []
    builders: list[BuilderInfo] = []

    for line in lines:
        try:
            if line.startswith("TASK|"):
                parts = line.split("|")
                if len(parts) < 9:
                    continue
                xy = parts[2].split(",")
                if len(xy) != 2:
                    continue
                imp = parts[3]
                # Skip tasks where tech prerequisite isn't met
                if imp.endswith("_LOCKED"):
                    continue
                tasks.append(
                    BuilderTask(
                        priority=parts[1],
                        x=int(xy[0]),
                        y=int(xy[1]),
                        improvement=imp,
                        resource=parts[4],
                        resource_class=parts[5],
                        city_name=parts[6],
                        nearest_builder_id=int(parts[7]),
                        distance=int(parts[8]),
                    )
                )
            elif line.startswith("BUILDER|"):
                parts = line.split("|")
                if len(parts) < 6:
                    continue
                xy = parts[3].split(",")
                if len(xy) != 2:
                    continue
                builders.append(
                    BuilderInfo(
                        unit_id=int(parts[1]),
                        unit_index=int(parts[2]),
                        x=int(xy[0]),
                        y=int(xy[1]),
                        charges=int(parts[4]),
                        moves=float(parts[5]),
                    )
                )
        except (ValueError, IndexError):
            continue

    return BuilderTaskReport(tasks, builders, resource_visibility_known)
