"""Great Person pool requests bind the original individual and resource baseline."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


_CONTEXT = _LUA_ACTION_SCOPE + '''
local function amount(value)
 assert(type(value)=="number" and value>=0 and value<math.huge,"RESOURCE_UNAVAILABLE")
 return value
end
local function balance(owner,currency,class)
 if currency=="GP_POINTS" then return amount(Players[owner]:GetGreatPeoplePoints():GetPointsTotal(class)) end
 if currency=="YIELD_GOLD" then return amount(Players[owner]:GetTreasury():GetGoldBalance()) end
 return amount(Players[owner]:GetReligion():GetFaithBalance())
end
local function ownedUnits(owner)
 local result={}
 for _,unit in Players[owner]:GetUnits():Members() do
  local id=unit:GetID()
  assert(type(id)=="number" and id>=0 and id%1==0 and unit:GetOwner()==owner,"UNIT_ID_UNAVAILABLE")
  result[id]=unit
 end
 return result
end
'''


def build_great_person_pool_action(kind: str, individual: int, currency: str, token: str) -> str:
    return _CONTEXT + f'''
local token,kind,currency={json.dumps(token)},{json.dumps(kind)},{json.dumps(currency)}
local ok,r=pcall(function()
 assert(__civmcpGPPool==nil or type(__civmcpGPPool)=="table","RECEIPT_STORAGE_UNAVAILABLE")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local gp=Game.GetGreatPeople()
 local entry=nil
 for _,e in ipairs(gp:GetTimeline()) do
  if e.Individual=={individual} then assert(entry==nil,"AMBIGUOUS_CANDIDATE");entry=e end
 end
 -- The native current-timeline UI uses each operation's Can* result. Claimant
 -- may already be the local player before recruitment; it is not eligibility.
 assert(entry,"CANDIDATE_UNAVAILABLE")
 local class=entry.Class
 assert(type(class)=="number" and class>=0 and class%1==0,"CLASS_UNAVAILABLE")
 local params={{}}
 params[PlayerOperations.PARAM_GREAT_PERSON_INDIVIDUAL_TYPE]={individual}
 local cost,operation
 if kind=="recruit_great_person" then
  assert(gp:CanRecruitPerson(owner,{individual})==true,"CANNOT_RECRUIT")
  cost=amount(entry.Cost)
  operation=PlayerOperations.RECRUIT_GREAT_PERSON
 elseif kind=="reject_great_person" then
  assert(gp:CanRejectPerson(owner,{individual})==true,"CANNOT_REJECT")
  cost=amount(gp:GetRejectCost(owner,{individual}))
  operation=PlayerOperations.REJECT_GREAT_PERSON
 else
  local yield
  if currency=="YIELD_GOLD" then yield=YieldTypes.GOLD else yield=YieldTypes.FAITH end
  assert(yield~=nil,"CURRENCY_UNAVAILABLE")
  assert(gp:CanPatronizePerson(owner,{individual},yield)==true,"CANNOT_PATRONIZE")
  cost=amount(gp:GetPatronizeCost(owner,{individual},yield))
  params[PlayerOperations.PARAM_YIELD_TYPE]=yield
  operation=PlayerOperations.PATRONIZE_GREAT_PERSON
 end
 local before=balance(owner,currency,class)
 assert(before>=cost,"INSUFFICIENT_RESOURCE")
 assert(operation~=nil and type(UI.RequestPlayerOperation)=="function","OPERATION_UNAVAILABLE")
 local ids={{}}
 for id,_ in pairs(ownedUnits(owner)) do ids[id]=true end
 return {{token=token,kind=kind,individual={individual},currency=currency,owner=owner,class=class,
  cost=cost,before=before,ids=ids,params=params,operation=operation,
  name=Locale.Lookup(GameInfo.GreatPersonIndividuals[{individual}].Name),
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
end)
if not ok then print("ERR:GP_POOL_REFUSED|"..tostring(r));print("{SENTINEL}");return end
if not (__civmcpGPPool and __civmcpGPPool.token==token) then
 __civmcpGPPool=r
 r.entered=true
 UI.RequestPlayerOperation(r.owner,r.operation,r.params)
 r.returned=true
end
print("OK:GP_POOL_REQUESTED|"..kind.."|individual:{individual}")
print("{SENTINEL}")
'''


def build_great_person_pool_query(kind: str, individual: int, currency: str, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpGPPool
local reason="RECEIPT_MISSING_OR_MISMATCHED"
local ok,unitID=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.kind=={json.dumps(kind)}
  and r.individual=={individual} and r.currency=={json.dumps(currency)})
 reason="SCOPE_CHANGED";assert(sameScope(r))
 reason="REQUEST_NOT_RETURNED";assert(r.entered==true and r.returned==true)
 reason="CORE_BUSY_OR_UNKNOWN";assert(UI.IsGameCoreBusy()==false)
 reason="RESOURCE_UNCONFIRMED";assert(math.abs(balance(r.owner,r.currency,r.class)-(r.before-r.cost))<0.01)
 reason="NEW_INDIVIDUAL_UNIT_UNCONFIRMED"
 local found=nil
 for id,unit in pairs(ownedUnits(r.owner)) do
  if not r.ids[id] then
   local greatPerson=unit:GetGreatPerson()
   if greatPerson and greatPerson:GetIndividual()==r.individual then
    assert(found==nil);found=id
   end
  end
 end
 if r.kind=="reject_great_person" then
  -- InGameText LOC_GREAT_PEOPLE_PASS_DETAILS spends class points. The candidate
  -- can remain available to other players; global pool advancement is not proof.
  reason="ORIGINAL_REJECTION_UNCONFIRMED"
  local gp=Game.GetGreatPeople()
  assert(found==nil and gp:CanRejectPerson(r.owner,r.individual)==false
   and gp:CanRecruitPerson(r.owner,r.individual)==false)
  return -1
 end
 assert(found~=nil)
 return found
end)
if ok then
 print("GP_POOL_CONFIRMED|"..r.token.."|"..r.kind.."|"..r.individual.."|"..r.currency
  .."|"..r.name.."|"..r.cost.."|unit:"..unitID)
else print("GP_POOL_OBSERVED|{token}|"..reason) end
print("{SENTINEL}")
'''


def parse_great_person_pool_confirmation(lines: list[str], token: str, kind: str, individual: int, currency: str) -> str | None:
    rows = [line.split("|") for line in lines if line.startswith("GP_POOL_CONFIRMED|")]
    if len(rows) == 1 and len(rows[0]) == 8:
        row = rows[0]
        if row[1:5] == [token, kind, str(individual), currency]:
            if kind == "reject_great_person":
                return f"REJECTED|{row[5]}|individual:{individual}|gp_points_cost:{row[6]}"
            if kind == "recruit_great_person":
                return f"RECRUITED|{row[5]}|individual:{individual}|gp_points_cost:{row[6]}|{row[7]}"
            return f"PATRONIZED|{row[5]}|individual:{individual}|cost:{row[6]} {currency.removeprefix('YIELD_').lower()}|{row[7]}"
    return None
