"""Native aircraft rebasing; destination capacity belongs to the engine."""

import json
import math

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


def build_rebase(unit_id: int, x: int, y: int, token: str) -> str:
    # WorldInput.lua/AirUnitReBase uses REBASE with PARAM_X/Y, not MOVE_TO.
    # Its operation targets include cities, airfields and carriers; ground-unit
    # stacking and movement-path checks would incorrectly reject valid bases.
    return _LUA_ACTION_SCOPE + f'''
-- ACTION_REBASE_SUBMIT
local token={json.dumps(token)}
local ok,r=pcall(function()
 assert(__civmcpRebase==nil or type(__civmcpRebase)=="table","RECEIPT_STORAGE_UNAVAILABLE")
 assert(__civmcpRebase==nil or __civmcpRebase.closed==true,"PRIOR_REBASE_UNRESOLVED")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local scope={{owner=owner,civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
 local unit=UnitManager.GetUnit(owner,{unit_id})
 assert(unit and unit:GetID()=={unit_id} and unit:GetOwner()==owner,"UNIT_CHANGED_OR_UNAVAILABLE")
 local row=GameInfo.Units[unit:GetType()]
 assert(row and row.Domain=="DOMAIN_AIR","NOT_AN_AIRCRAFT")
 local moves=unit:GetMovesRemaining()
 assert(type(moves)=="number" and moves>0 and moves<math.huge,"NO_MOVES_OR_UNKNOWN")
 local fromX,fromY=unit:GetX(),unit:GetY()
 assert(scopeIndex(fromX) and scopeIndex(fromY),"ORIGIN_UNKNOWN")
 assert(fromX~={x} or fromY~={y},"ALREADY_AT_DESTINATION")
 local operation=UnitOperationTypes.REBASE
 assert(scopeInteger(operation) and type(UnitManager.RequestOperation)=="function","OPERATION_UNAVAILABLE")
 local plot=Map.GetPlot({x},{y});assert(plot,"TARGET_UNKNOWN")
 local results=UnitManager.GetOperationTargets(unit,operation)
 local plots=results[UnitOperationResults.PLOTS]
 assert(type(plots)=="table","TARGETS_UNKNOWN")
 local matches=0
 for _,index in pairs(plots) do
  assert(scopeIndex(index),"TARGETS_UNKNOWN")
  if index==plot:GetIndex() then matches=matches+1 end
 end
 assert(matches==1,"TARGET_NOT_ELIGIBLE")
 local params={{}}
 params[UnitOperationTypes.PARAM_X],params[UnitOperationTypes.PARAM_Y]={x},{y}
 assert(UnitManager.CanStartOperation(unit,operation,nil,params)==true,"CANNOT_REBASE")
 assert(sameScope(scope),"SCOPE_CHANGED")
 scope.token,scope.id,scope.unitType=token,{unit_id},unit:GetType()
 scope.fromX,scope.fromY,scope.x,scope.y=fromX,fromY,{x},{y}
 scope.moves,scope.params,scope.operation=moves,params,operation
 return scope
end)
if not ok then print("ERR:REBASE_REFUSED|"..tostring(r));print("{SENTINEL}");return end
__civmcpRebase=r
r.entered=true
local sent,result=pcall(function()
 return UnitManager.RequestOperation(UnitManager.GetUnit(r.owner,r.id),r.operation,r.params)
end)
if not sent or result==false then
 print("ERR:OUTCOME_UNKNOWN|Rebase native request entered without a complete return")
 print("{SENTINEL}");return
end
r.returned=true
print("OK:REBASE_REQUESTED|unit:{unit_id}|at:{x},{y}")
print("{SENTINEL}")
'''


def build_rebase_query(unit_id: int, x: int, y: int, token: str) -> str:
    return _LUA_ACTION_SCOPE + f'''
-- ACTION_REBASE_QUERY
local r=__civmcpRebase
local ok,moves=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.id=={unit_id}
  and r.x=={x} and r.y=={y},"RECEIPT_MISMATCH")
 assert(sameScope(r),"SCOPE_CHANGED")
 assert(r.entered==true and r.returned==true,"REQUEST_NOT_RETURNED")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local unit=UnitManager.GetUnit(r.owner,r.id)
 assert(unit and unit:GetID()==r.id and unit:GetOwner()==r.owner and unit:GetType()==r.unitType,"UNIT_CHANGED")
 assert(unit:GetX()==r.x and unit:GetY()==r.y,"TRANSFER_UNCONFIRMED")
 local remaining=unit:GetMovesRemaining()
 assert(type(remaining)=="number" and remaining>=0 and remaining<r.moves,"MOVES_UNCONFIRMED")
 assert(sameScope(r),"SCOPE_CHANGED")
 return remaining
end)
-- A late getter can observe a known world change even when another read fails.
if type(r)=="table" and r.token=={json.dumps(token)} and not sameScope(r) then ok=false end
if ok then
 r.closed=true
 print("REBASE_CONFIRMED|"..r.token.."|"..r.id.."|"..r.fromX.."|"..r.fromY
  .."|"..r.x.."|"..r.y.."|"..r.moves.."|"..moves)
else print("REBASE_OBSERVED|{token}|"..tostring(moves)) end
print("{SENTINEL}")
'''


def parse_rebase_confirmation(lines: list[str], unit_id: int, x: int, y: int, token: str) -> str | None:
    rows = [line.split('|') for line in lines if line.startswith('REBASE_CONFIRMED|')]
    if len(rows) != 1 or len(rows[0]) != 9:
        return None
    row = rows[0]
    if row[1:3] != [token, str(unit_id)] or row[5:7] != [str(x), str(y)]:
        return None
    try:
        origin = tuple(map(int, row[3:5]))
        before, after = map(float, row[7:9])
        if min(origin) < 0 or origin == (x, y) or not math.isfinite(before) or not 0 <= after < before:
            return None
    except ValueError:
        return None
    return f'REBASED|unit:{unit_id}|at:{x},{y}|from:{origin[0]},{origin[1]}|moves:{before:g}->{after:g}'
