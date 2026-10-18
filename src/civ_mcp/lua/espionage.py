"""Espionage domain — Lua builders and parsers for spy operations."""

from __future__ import annotations

import json

from civ_mcp.lua._helpers import SENTINEL, _bail, _bail_lua, _int, _lua_get_unit
from civ_mcp.lua.models import SpyInfo

# Spy operation hashes — GameInfo.UnitOperations() returns 0 rows so we hardcode these.
# Verified live via UnitOperationTypes enum in InGame Lua context.
_SPY_OP_HASHES: dict[str, int] = {
    "TRAVEL": -1295211657,
    "COUNTERSPY": -2005926703,
    "GAIN_SOURCES": -311249027,
    "SIPHON_FUNDS": 574548564,
    "STEAL_TECH_BOOST": -664243095,
    "SABOTAGE_PRODUCTION": 163704001,
    "GREAT_WORK_HEIST": 485545794,
    "RECRUIT_PARTISANS": -713713573,
    "NEUTRALIZE_GOVERNOR": -1064658215,
    "FABRICATE_SCANDAL": -1766334630,
}

_RANK_NAMES = {1: "Recruit", 2: "Agent", 3: "Special Agent", 4: "Senior Agent"}


def _spy_escape_context() -> str:
    routes = ",".join(json.dumps(name) for name in _ESCAPE_DISTRICTS)
    return r'''
local me=Game.GetLocalPlayer()
local escapeGate={status="UNKNOWN"}
local gateOK,gate=pcall(function()
 local escapeType=EndTurnBlockingTypes and EndTurnBlockingTypes.ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE
 if type(escapeType)~="number" or escapeType==0 then error("escape blocker enum unknown") end
 local list=NotificationManager.GetList(me)
 if list==nil then list={} end
 local active=false
 for _,id in ipairs(list) do
  local entry=NotificationManager.Find(me,id)
  if not entry then error("notification unknown") end
  local dismissed=entry:IsDismissed()
  if type(dismissed)~="boolean" then error("notification dismissal unknown") end
  if not dismissed then
   local blocking=entry:GetEndTurnBlocking()
   if type(blocking)~="number" then error("notification blocker unknown") end
   if blocking==escapeType then active=true end
  end
 end
 if not active then return {status="NONE"} end
 -- This native getter can crash outside an active escape notification.
 -- pcall cannot contain that crash: the live notification guard is mandatory.
 local id=Players[me]:GetDiplomacy():GetNextEscapingSpyID()
 if type(id)~="number" or id<0 or id%1~=0 then error("escaping identity unknown") end
 return {status="ACTIVE",id=id}
end)
if gateOK then escapeGate=gate end
local function verifiedSpy(id)
 local spy=Players[me]:GetUnits():FindID(id)
 if not spy or spy:GetID()~=id or spy:GetOwner()~=me then error("full spy identity unavailable") end
 local info=GameInfo.Units[spy:GetType()]
 if not info or info.UnitType~="UNIT_SPY" then error("not a spy") end
 return spy
end
local function escapeRoutes(spy)
 local x,y=spy:GetX(),spy:GetY()
 if type(x)~="number" or type(y)~="number" or x<0 or y<0 then error("spy city unavailable") end
 local city=Cities.GetPlotPurchaseCity(x,y)
 if not city then error("spy city unavailable") end
 local districts=city:GetDistricts()
 local names={__ESCAPE_DISTRICTS__}
 local routes={}
 for _,name in ipairs(names) do
  local row=GameInfo.Districts[name]
  if not row or type(row.Index)~="number" then error("escape district definition unavailable") end
  local available=name=="DISTRICT_CITY_CENTER" or districts:HasDistrict(row.Index,true,true)
  if type(available)~="boolean" then error("escape route availability unknown") end
  if available then table.insert(routes,name) end
 end
 return routes
end
'''.replace("__ESCAPE_DISTRICTS__", routes)


def build_get_spies_query() -> str:
    """InGame context: list all spy units with rank, position, city, and available ops."""
    # Build the op table literal for Lua (key=name, value=hash)
    op_entries = ", ".join(
        f"{name}={hash_val}" for name, hash_val in _SPY_OP_HASHES.items()
    )
    sentinel = SENTINEL
    return _spy_escape_context() + f"""
if escapeGate.status=="UNKNOWN" then print("SPY_ESCAPE_QUERY_UNKNOWN") end
local escapeMatched=false
local SPY_OPS = {{{op_entries}}}
for i, u in Players[me]:GetUnits():Members() do
    local entry = GameInfo.Units[u:GetType()]
    if entry and entry.UnitType == "UNIT_SPY" then
        local ok_spy, err_spy = pcall(function()
            local x, y = u:GetX(), u:GetY()
            local name = Locale.Lookup(u:GetName())
            local uid = u:GetID()
            local rank = 1
            local xp = 0
            local exp = u:GetExperience()
            if exp then
                local ok_r, lv = pcall(function() return exp:GetLevel() end)
                if ok_r and lv then rank = lv end
                local ok_x, ep = pcall(function() return exp:GetExperiencePoints() end)
                if ok_x and ep then xp = ep end
            end
            local moves = u:GetMovesRemaining()
            local cityName = "none"
            local cityOwner = -1
            local opStr = ""
            local currentOp = "none"
            local status = "idle"
            if x == -9999 then
                status = "in_transit"
            else
                local pCity = CityManager.GetCityAt(x, y)
                if pCity then
                    cityName = Locale.Lookup(pCity:GetName())
                    cityOwner = pCity:GetOwner()
                end
                if not (escapeGate.status=="ACTIVE" and uid==escapeGate.id) then
                    local params = {{[UnitOperationTypes.PARAM_X0]=x, [UnitOperationTypes.PARAM_Y0]=y}}
                    local availOps = {{}}
                    for opName, opHash in pairs(SPY_OPS) do
                        local ok, can = pcall(function() return UnitManager.CanStartOperation(u, opHash, nil, params) end)
                        if ok and can then
                            table.insert(availOps, opName)
                        end
                    end
                    opStr = table.concat(availOps, ",")
                    local ok_op, opIdx = pcall(function() return u:GetSpyOperation() end)
                    if ok_op and opIdx and opIdx >= 0 then
                        for row in GameInfo.UnitOperations() do
                            if row.Index == opIdx then
                                currentOp = row.OperationType:gsub("UNITOPERATION_SPY_", ""):gsub("UNITOPERATION_", "")
                                break
                            end
                        end
                        status = "on_mission"
                    end
                end
            end
            local routeStr=""
            if escapeGate.status=="ACTIVE" and uid==escapeGate.id then
                verifiedSpy(uid)
                escapeMatched=true
                routeStr=table.concat(escapeRoutes(u),",")
                status="escaping";currentOp="none";opStr=""
            end
            print(uid.."|"..name.."|"..x.."|"..y.."|"..rank.."|"..xp.."|"..moves.."|"..cityName.."|"..cityOwner.."|"..opStr.."|"..currentOp.."|"..status.."|"..routeStr)
        end)
        if not ok_spy then
            if escapeGate.status=="ACTIVE" then print("SPY_ESCAPE_QUERY_UNKNOWN") end
            print("0|ERROR_SPY|0|0|1|0|0|none|-1||none|idle")
        end
    end
end
if escapeGate.status=="ACTIVE" and not escapeMatched then print("SPY_ESCAPE_QUERY_UNKNOWN") end
print("{sentinel}")
"""


def parse_spies_response(lines: list[str]) -> list[SpyInfo]:
    """Parse pipe-delimited spy rows into SpyInfo list."""
    spies = []
    for line in lines:
        if not line or line == SENTINEL:
            continue
        parts = line.split("|")
        if len(parts) < 10:
            continue
        try:
            uid = int(parts[0])
            name = parts[1]
            x = int(parts[2])
            y = int(parts[3])
            rank = int(parts[4])
            xp = int(parts[5])
            moves = _int(parts[6])
            city_name = parts[7]
            city_owner = int(parts[8])
            ops_str = parts[9].strip()
            available_ops = [op for op in ops_str.split(",") if op]
            current_mission = parts[10].strip() if len(parts) > 10 else "none"
            status_str = parts[11].strip() if len(parts) > 11 else "idle"
            is_escaping = status_str == "escaping"
            spies.append(
                SpyInfo(
                    unit_id=uid,
                    unit_index=uid % 65536,
                    name=name,
                    x=x,
                    y=y,
                    rank=rank,
                    xp=xp,
                    moves=moves,
                    city_name=city_name,
                    city_owner=city_owner,
                    available_ops=available_ops,
                    current_mission=current_mission,
                    is_escaping=is_escaping,
                    status=status_str,
                    escape_routes=(parts[12].split(",") if len(parts) > 12 and parts[12] else []),
                )
            )
        except (ValueError, IndexError):
            continue
    return spies


def build_spy_travel(unit_index: int, target_x: int, target_y: int) -> str:
    """InGame context: send spy to a target city tile.

    Valid targets: own cities and city-states. Allied civ cities return ERR:CANNOT_TRAVEL.
    Travel is queued end-of-turn — spy does not immediately appear at destination.
    """
    travel_hash = _SPY_OP_HASHES["TRAVEL"]
    sentinel = SENTINEL
    # Build the Lua error message expression without backslash escapes in f-strings
    err_lua = (
        '"ERR:CANNOT_TRAVEL|Cannot send spy to (" .. '
        f"{target_x} .. ',' .. {target_y} .. "
        '"). Allied civ cities are not valid; only own cities and city-states."'
    )
    return " ".join(
        [
            _lua_get_unit(unit_index),
            "local entry = GameInfo.Units[unit:GetType()]",
            f'if not entry or entry.UnitType ~= "UNIT_SPY" then {_bail("ERR:NOT_A_SPY")} end',
            f"local params = {{[UnitOperationTypes.PARAM_X0]={target_x}, [UnitOperationTypes.PARAM_Y0]={target_y}}}",
            f"local ok, can = pcall(function() return UnitManager.CanStartOperation(unit, {travel_hash}, nil, params) end)",
            f"if not ok or not can then",
            f"  {_bail_lua(err_lua)}",
            "end",
            f"UnitManager.RequestOperation(unit, {travel_hash}, params)",
            f'print("OK:SPY_TRAVEL|Spy en route to ({target_x},{target_y}). Travel completes at end of turn.")',
            f'print("{sentinel}")',
        ]
    )


def build_spy_mission(
    unit_index: int, mission_type: str, target_x: int, target_y: int
) -> str:
    """InGame context: launch a spy mission at a target city tile.

    Offensive missions (anything except COUNTERSPY) require the spy to be physically
    IN the target city. CanStartOperation will return false until arrival.
    """
    op_hash = _SPY_OP_HASHES.get(mission_type.upper())
    sentinel = SENTINEL
    if op_hash is None:
        valid = ", ".join(k for k in _SPY_OP_HASHES if k != "TRAVEL")
        # Escape the mission_type for Lua string embedding
        safe_mission = mission_type.replace('"', '\\"')
        return " ".join(
            [
                f'print("ERR:UNKNOWN_MISSION|Unknown mission type {safe_mission}. Valid missions: {valid}")',
                f'print("{sentinel}")',
            ]
        )
    err_lua = (
        f'"ERR:CANNOT_MISSION|{mission_type} not available at (" .. '
        f"{target_x} .. ',' .. {target_y} .. "
        '"). Spy must be in the target city first (use spy_action travel)."'
    )
    return " ".join(
        [
            _lua_get_unit(unit_index),
            "local entry = GameInfo.Units[unit:GetType()]",
            f'if not entry or entry.UnitType ~= "UNIT_SPY" then {_bail("ERR:NOT_A_SPY")} end',
            f"local params = {{[UnitOperationTypes.PARAM_X0]={target_x}, [UnitOperationTypes.PARAM_Y0]={target_y}}}",
            f"local ok, can = pcall(function() return UnitManager.CanStartOperation(unit, {op_hash}, nil, params) end)",
            f"if not ok or not can then",
            f"  {_bail_lua(err_lua)}",
            "end",
            f"UnitManager.RequestOperation(unit, {op_hash}, params)",
            f'print("OK:SPY_MISSION|{mission_type} mission launched at ({target_x},{target_y}).")',
            f'print("{sentinel}")',
        ]
    )


# ---------------------------------------------------------------------------
# Escape route resolution
# ---------------------------------------------------------------------------

# These are the four choices exposed by the native EspionageEscape popup.
_ESCAPE_DISTRICTS = (
    "DISTRICT_AERODROME",
    "DISTRICT_HARBOR",
    "DISTRICT_COMMERCIAL_HUB",
    "DISTRICT_CITY_CENTER",
)


def build_spy_escape_route(unit_id: int, district_type: str, *, witness_token: str) -> str:
    """Select this full native identity's one explicit legal escape district."""
    district_type = district_type.strip().upper()
    if district_type not in _ESCAPE_DISTRICTS:
        raise ValueError(f"Unknown spy escape route {district_type!r}; choose {', '.join(_ESCAPE_DISTRICTS)}")
    return _spy_escape_context() + f"""
if escapeGate.status~="ACTIVE" then print("ERR:ESCAPE_GATE_UNAVAILABLE|No verified active escape notification");print("{SENTINEL}");return end
if escapeGate.id~={unit_id} then print("ERR:SPY_NOT_ESCAPING|Requested full identity is not the pending spy");print("{SENTINEL}");return end
local requested={json.dumps(district_type)}
local ok,available=pcall(function()
 local spy=verifiedSpy({unit_id})
 for _,route in ipairs(escapeRoutes(spy)) do if route==requested then return true end end
 return false
end)
if not ok or available~=true then print("ERR:ESCAPE_ROUTE_UNAVAILABLE|Spy identity, city or route changed");print("{SENTINEL}");return end
if not PlayerOperations or PlayerOperations.SET_ESCAPE_ROUTE==nil or PlayerOperations.PARAM_DISTRICT_TYPE==nil or type(UI.RequestPlayerOperation)~="function" then print("ERR:ESCAPE_COMMAND_UNAVAILABLE");print("{SENTINEL}");return end
if __civmcpSpyEscape~=nil and type(__civmcpSpyEscape)~="table" then print("ERR:ESCAPE_RECEIPT_UNAVAILABLE");print("{SENTINEL}");return end
__civmcpSpyEscape=__civmcpSpyEscape or {{}}
local receipt={{token={json.dumps(witness_token)},id={unit_id},owner=me,route=requested,complete=false}}
__civmcpSpyEscape[{unit_id}]=receipt
local params={{[PlayerOperations.PARAM_DISTRICT_TYPE]=GameInfo.Districts[requested].Index}}
UI.RequestPlayerOperation(me,PlayerOperations.SET_ESCAPE_ROUTE,params)
receipt.complete=true
local popup=ContextPtr:LookUpControl("/InGame/EspionageEscape")
if popup then popup:SetHide(true) end
print("OK:SPY_ESCAPE_REQUESTED|{unit_id}|"..requested)
print("{SENTINEL}")
"""


def build_verify_spy_escape(unit_id: int, district_type: str, witness_token: str) -> str:
    """Read the actual escape gate; never probe the unsafe getter without it."""
    return _spy_escape_context() + f"""
local receipt=type(__civmcpSpyEscape)=="table" and __civmcpSpyEscape[{unit_id}]
local ok=pcall(function() verifiedSpy({unit_id}) end)
if not ok or not receipt or receipt.token~={json.dumps(witness_token)} or receipt.id~={unit_id} or receipt.owner~=me or receipt.route~={json.dumps(district_type)} or receipt.complete~=true then print("SPY_ESCAPE_UNCONFIRMED|IDENTITY_OR_RECEIPT");print("{SENTINEL}");return end
-- Another verified full identity means the queue advanced past this choice.
local advanced=false
if escapeGate.status=="ACTIVE" and escapeGate.id~={unit_id} then
 advanced=pcall(function() verifiedSpy(escapeGate.id) end)
end
if escapeGate.status=="NONE" or advanced then
 print("SPY_ESCAPE_CONFIRMED|"..receipt.token.."|{unit_id}|"..receipt.route.."|gate:"..escapeGate.status.."|next:"..(escapeGate.id or "none"))
else print("SPY_ESCAPE_UNCONFIRMED|GATE_"..escapeGate.status) end
print("{SENTINEL}")
"""
