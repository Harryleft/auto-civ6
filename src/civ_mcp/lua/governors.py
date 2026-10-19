"""Single native governor requests and their title/target evidence."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


_CONTEXT = _LUA_ACTION_SCOPE + '''
local function integer(value)
 assert(type(value)=="number" and value==value and math.abs(value)<math.huge and value==math.floor(value),"NUMBER_UNAVAILABLE")
 return value
end
local function cityFor(owner,id)
 local city=CityManager.GetCity(owner,id%65536)
 assert(city and city:GetOwner()==owner and city:GetID()==id,"CITY_ID_CHANGED")
 return city
end
'''


def build_governor_action(operation: str, governor_type: str, token: str,
                          *, promotion_type: str | None = None, city_id: int | None = None,
                          target_owner: int | None = None) -> str:
    return _CONTEXT + f'''
local token={json.dumps(token)}
local ok,r=pcall(function()
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local pg=Players[owner]:GetGovernors()
 local gov=GameInfo.Governors[{json.dumps(governor_type)}]
 assert(gov and integer(gov.Index)>=0 and integer(gov.Hash),"GOVERNOR_NOT_FOUND")
 local has=pg:HasGovernor(gov.Hash)
 assert(type(has)=="boolean","GOVERNOR_STATUS_UNAVAILABLE")
 local r={{token=token,operation={json.dumps(operation)},governor={json.dumps(governor_type)},
  hash=gov.Hash,owner=owner}}
 if r.operation~="assign_governor" then
  r.spent=integer(pg:GetGovernorPointsSpent());assert(r.spent>=0,"TITLE_BASELINE_UNAVAILABLE")
 end
 if r.operation=="appoint_governor" then
  r.noop=has
  if not r.noop then
   assert(pg:CanAppoint()==true and pg:CanEverAppointGovernor(gov.Hash)==true,"CANNOT_APPOINT")
  end
  r.op=PlayerOperations.APPOINT_GOVERNOR
  r.params={{[PlayerOperations.PARAM_GOVERNOR_TYPE]=gov.Index}}
 elseif r.operation=="promote_governor" then
  assert(has,"GOVERNOR_NOT_APPOINTED")
  local promo=GameInfo.GovernorPromotions[{json.dumps(promotion_type) if promotion_type is not None else 'nil'}]
  assert(promo and integer(promo.Index)>=0 and integer(promo.Hash),"PROMOTION_NOT_FOUND")
  r.promotion={json.dumps(promotion_type) if promotion_type is not None else 'nil'}
  r.promotionHash=promo.Hash
  r.noop=pg:GetGovernor(gov.Hash):HasPromotion(promo.Hash)
  assert(type(r.noop)=="boolean","PROMOTION_STATUS_UNAVAILABLE")
  if not r.noop then assert(pg:CanEarnPromotion(gov.Hash,promo.Hash)==true,"CANNOT_PROMOTE") end
  r.op=PlayerOperations.PROMOTE_GOVERNOR
  r.params={{[PlayerOperations.PARAM_GOVERNOR_TYPE]=gov.Index,[PlayerOperations.PARAM_GOVERNOR_PROMOTION_TYPE]=promo.Index}}
 elseif r.operation=="assign_governor" then
  assert(has,"GOVERNOR_NOT_APPOINTED")
  r.civ=PlayerConfigurations[owner]:GetCivilizationTypeName()
  r.seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED");r.turn=Game.GetCurrentGameTurn()
  r.targetOwner=integer({json.dumps(target_owner) if target_owner is not None else 'owner'})
  assert(r.targetOwner>=0 and r.targetOwner<=63,"TARGET_OWNER_UNAVAILABLE")
  local city=cityFor(r.targetOwner,{city_id if city_id is not None else 'nil'})
  local assigned=pg:GetGovernor(gov.Hash):GetAssignedCity()
  r.city=city:GetID();r.cityX=integer(city:GetX());r.cityY=integer(city:GetY())
  assert(r.cityX>=0 and r.cityY>=0,"CITY_POSITION_UNAVAILABLE")
  r.noop=assigned~=nil and assigned:GetID()==r.city and assigned:GetOwner()==r.targetOwner
   and assigned:GetX()==r.cityX and assigned:GetY()==r.cityY
  if not r.noop then assert(pg:CanAssignGovernor(gov.Hash,city,true)==true,"CANNOT_ASSIGN") end
  r.op=PlayerOperations.ASSIGN_GOVERNOR
  r.params={{[PlayerOperations.PARAM_GOVERNOR_TYPE]=gov.Index,
   [PlayerOperations.PARAM_CITY_DEST]=r.city,[PlayerOperations.PARAM_PLAYER_ONE]=r.targetOwner}}
  assert(sameScope(r),"ASSIGNMENT_SCOPE_CHANGED")
 end
 if not r.noop then
  if r.operation~="assign_governor" then assert(integer(pg:GetGovernorPoints())>r.spent,"NO_GOVERNOR_TITLES") end
  assert(type(UI.RequestPlayerOperation)=="function" and r.op~=nil,"OPERATION_UNAVAILABLE")
 end
 return r
end)
if not ok then print("ERR:GOVERNOR_REFUSED|"..tostring(r));print("{SENTINEL}");return end
if not (__civmcpGovernor and __civmcpGovernor.token==token) then
 __civmcpGovernor=r
 if not r.noop then
  r.entered=true
  UI.RequestPlayerOperation(r.owner,r.op,r.params)
  r.returned=true
 end
end
print("OK:GOVERNOR_REQUESTED|"..r.operation.."|"..r.governor)
print("{SENTINEL}")
'''


def build_governor_confirmation(operation: str, governor_type: str, token: str,
                               *, promotion_type: str | None = None, city_id: int | None = None,
                               target_owner: int | None = None) -> str:
    return _CONTEXT + f'''
local r=__civmcpGovernor
local ok,detail=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.operation=={json.dumps(operation)}
  and r.governor=={json.dumps(governor_type)},"RECEIPT_MISSING_OR_MISMATCHED")
 assert(r.noop or r.entered==true and r.returned==true,"REQUEST_NOT_RETURNED")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local pg=Players[r.owner]:GetGovernors()
 assert(pg:HasGovernor(r.hash)==true and pg:GetGovernor(r.hash)~=nil,"GOVERNOR_UNCONFIRMED")
 local spent
 if r.operation~="assign_governor" then
  spent=integer(pg:GetGovernorPointsSpent())
  assert(spent==r.spent+(r.noop and 0 or 1),"TITLE_DELTA_UNCONFIRMED")
 end
 if r.operation=="promote_governor" then
  assert(r.promotion=={json.dumps(promotion_type) if promotion_type is not None else 'nil'},"PROMOTION_CHANGED")
  assert(pg:GetGovernor(r.hash):HasPromotion(r.promotionHash)==true,"PROMOTION_UNCONFIRMED")
  return "promotion:"..r.promotion..",spent:"..spent
 end
 if r.operation=="assign_governor" then
  assert(sameScope(r),"ASSIGNMENT_SCOPE_CHANGED")
  assert(r.city=={city_id if city_id is not None else 'nil'}
   and r.targetOwner=={json.dumps(target_owner) if target_owner is not None else 'r.owner'},"CITY_TARGET_CHANGED")
  local city=cityFor(r.targetOwner,r.city)
  assert(city:GetX()==r.cityX and city:GetY()==r.cityY,"CITY_POSITION_CHANGED")
  local governor=pg:GetGovernor(r.hash)
  local assigned=governor:GetAssignedCity()
  assert(assigned and assigned:GetID()==r.city and assigned:GetOwner()==r.targetOwner
   and assigned:GetX()==r.cityX and assigned:GetY()==r.cityY,"ASSIGNMENT_UNCONFIRMED")
  -- Assignment finishes here; establishment is a later game process. Missing
  -- progress metadata must not keep an already assigned governor pending.
  local reportOK,established,turns=pcall(function()
   local established=governor:IsEstablished()
   assert(type(established)=="boolean")
   return established,established and 0 or integer(governor:GetTurnsToEstablish())
  end)
  assert(sameScope(r),"ASSIGNMENT_SCOPE_CHANGED")
  return "target_owner:"..r.targetOwner..",city:"..r.city..",established:"..(reportOK and tostring(established) or "unknown")
   ..",turns:"..(reportOK and tostring(turns) or "unknown")
 end
 return "spent:"..spent
end)
if ok then print("GOVERNOR_CONFIRMED|"..r.token.."|"..r.operation.."|"..r.governor.."|"..tostring(r.noop).."|"..detail)
else print("GOVERNOR_OBSERVED|{token}|"..tostring(detail)) end
print("{SENTINEL}")
'''


def parse_governor_confirmation(lines: list[str], operation: str, governor_type: str, token: str) -> str | None:
    rows = [row.split('|') for row in lines if row.startswith('GOVERNOR_CONFIRMED|')]
    if len(rows) != 1 or len(rows[0]) != 6:
        return None
    row = rows[0]
    if row[1:4] != [token, operation, governor_type] or row[4] not in ('true', 'false'):
        return None
    label = 'GOVERNOR_UNCHANGED' if row[4] == 'true' else {
        'appoint_governor': 'APPOINTED', 'promote_governor': 'PROMOTED',
        'assign_governor': 'ASSIGNED',
    }[operation]
    return f'{label}|{governor_type}|{row[5]}'
