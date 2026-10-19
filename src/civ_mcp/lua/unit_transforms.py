"""Unit transformations retain full native identity and their original effects."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


_CONTEXT = _LUA_ACTION_SCOPE + '''
local function number(v)
 assert(type(v)=="number" and v==v and v~=math.huge and v~=-math.huge,"NUMBER_UNAVAILABLE")
 return v
end
local function unitFor(owner,id)
 local unit=UnitManager.GetUnit(owner,id%65536)
 assert(unit and unit:GetOwner()==owner and (id<65536 or unit:GetID()==id),"UNIT_ID_CHANGED")
 return unit
end
'''


def build_upgrade(unit_id: int, token: str) -> str:
    return _CONTEXT + f'''
local token={json.dumps(token)}
local ok,r=pcall(function()
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local unit=unitFor(owner,{unit_id})
 local allowed,results=UnitManager.CanStartCommand(unit,UnitCommandTypes.UPGRADE,false,true)
 assert(allowed==true,"CANNOT_UPGRADE")
 local target=number(results[UnitCommandResults.UNIT_TYPE])
 local before=number(unit:GetType())
 assert(GameInfo.Units[target] and target~=before,"UPGRADE_TARGET_UNAVAILABLE")
 local cost=number(unit:GetUpgradeCost())
 local balance=number(Players[owner]:GetTreasury():GetGoldBalance())
 assert(cost>=0 and balance>=cost,"INSUFFICIENT_GOLD")
 local resource,resourceCost,stock=-1,0,0
 if GameInfo.Units_XP2~=nil then
  resource,resourceCost=unit:GetUpgradeResourceCost()
  resource=number(resource)
  if resource>=0 then
   resourceCost=number(resourceCost)
   stock=number(Players[owner]:GetResources():GetResourceAmount(resource))
   assert(resourceCost>=0 and stock>=resourceCost,"INSUFFICIENT_RESOURCE")
  else resourceCost=0 end
 end
 assert(type(UnitManager.RequestCommand)=="function","COMMAND_UNAVAILABLE")
 local owned={{}}
 for _,other in Players[owner]:GetUnits():Members() do
  local id=other:GetID()
  assert(scopeIndex(id) and other:GetOwner()==owner and not owned[id],"OWNED_IDS_UNAVAILABLE")
  owned[id]=true
 end
 local id,x,y=unit:GetID(),unit:GetX(),unit:GetY()
 assert(scopeIndex(id) and owned[id] and scopeIndex(x) and scopeIndex(y),"UPGRADE_SOURCE_UNAVAILABLE")
 assert(scopeInteger(UnitCommandTypes.UPGRADE),"COMMAND_UNAVAILABLE")
 return {{token=token,owner=owner,id=unit:GetID(),requested={unit_id},before=before,target=target,
  owned=owned,x=x,y=y,command=UnitCommandTypes.UPGRADE,
  cost=cost,balance=balance,resource=resource,resourceCost=resourceCost,stock=stock,
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
end)
if not ok then print("ERR:UNIT_UPGRADE_REFUSED|"..tostring(r));print("{SENTINEL}");return end
local previous=__civmcpUpgrade
for name,handler in pairs(previous and previous.handlers or {{}}) do
 pcall(function() Events[name].Remove(handler) end)
end
__civmcpUpgrade=r
r.handlers={{}}
local armed=pcall(function()
 local function bind(name,observe)
  local event=Events[name]
  assert(type(event.Add)=="function" and type(event.Remove)=="function")
  local handler=function(owner,id,a,b,c)
   if __civmcpUpgrade~=r or r.entered~=true or owner~=r.owner then return end
   local known=pcall(function()
    -- invalid belongs to scope history and constrains both completion paths.
    -- chainInvalid/uncertain only reject the replacement event chain.
    if not sameScope(r) then r.uncertain=true;return end
    if not r.chainInvalid and not r.uncertain then observe(id,a,b,c) end
   end)
   if not known then r.uncertain=true end
  end
  r.handlers[name]=handler
  event.Add(handler)
 end
 bind("UnitCommandStarted",function(id,command,a,b)
  if id~=r.id then return end
  if command==r.command and a==0 and b==0 then
   -- The engine repeats this exact Started after Upgraded; it is not a retry.
   if r.stage==nil then r.stage="STARTED" end
  else r.chainInvalid=true end
 end)
 bind("UnitRemovedFromMap",function(id)
  if id==r.id then
   if r.stage=="STARTED" then r.stage="REMOVED" else r.chainInvalid=true end
  elseif id==r.newID then r.chainInvalid=true end
 end)
 bind("UnitAddedToMap",function(id,x,y)
  if r.stage~="REMOVED" and r.stage~="ADDED" and r.stage~="UPGRADED" then return end
  if not scopeIndex(id) or not scopeIndex(x) or not scopeIndex(y) then r.uncertain=true;return end
  if id==r.id then r.chainInvalid=true;return end
  if x~=r.x or y~=r.y then return end
  if r.stage~="REMOVED" or r.owned[id] then r.chainInvalid=true;return end
  r.newID,r.stage=id,"ADDED"
 end)
 bind("UnitUpgraded",function(id,target)
  if r.stage=="ADDED" and id==r.newID and target==r.target then r.stage="UPGRADED"
  else r.chainInvalid=true end
 end)
end)
r.replacementCapable=armed
if not armed then
 -- A context without these events can still prove an existing same-ID upgrade.
 for name,handler in pairs(r.handlers) do pcall(function() Events[name].Remove(handler) end) end
end
r.entered=true
UnitManager.RequestCommand(unitFor(r.owner,r.id),r.command,{{}})
r.returned=true
print("OK:UNIT_UPGRADE_REQUESTED|"..r.id)
print("{SENTINEL}")
'''


def build_upgrade_query(unit_id: int, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpUpgrade
local ok,id,details=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.requested=={unit_id})
 assert(sameScope(r) and r.entered==true and r.returned==true)
 assert(UI.IsGameCoreBusy()==false)
 local original=UnitManager.GetUnit(r.owner,r.id%65536)
 local id=r.id
 local details="type:"..r.before.."->"..r.target
 local replaced=not (original and original:GetID()==r.id)
 if not replaced then
  assert(original:GetOwner()==r.owner and original:GetType()==r.target)
 else
  assert(r.replacementCapable==true and r.stage=="UPGRADED" and not r.chainInvalid and not r.uncertain)
  assert(scopeIndex(r.newID) and r.newID~=r.id and r.owned[r.id] and not r.owned[r.newID])
  local replacement=unitFor(r.owner,r.newID)
  assert(replacement:GetID()==r.newID and replacement:GetType()==r.target
   and replacement:GetX()==r.x and replacement:GetY()==r.y)
  id=r.newID
  details=details..",old_unit:"..r.id..",replacement:confirmed"
 end
 assert(number(Players[r.owner]:GetTreasury():GetGoldBalance())==r.balance-r.cost)
 if r.resource>=0 then
  assert(number(Players[r.owner]:GetResources():GetResourceAmount(r.resource))==r.stock-r.resourceCost)
 end
 -- Resource getters can dispatch events. Recheck shared scope and the chain
 -- immediately before emitting either proof; never detach on a pure read.
 assert(sameScope(r))
 if replaced then assert(r.stage=="UPGRADED" and not r.chainInvalid and not r.uncertain) end
 return id,details
end)
if ok then print("UNIT_TRANSFORM_CONFIRMED|{token}|{unit_id}|UPGRADED|"..id.."|"..details)
else print("UNIT_TRANSFORM_OBSERVED|{token}|UNKNOWN") end
print("{SENTINEL}")
'''


def parse_confirmation(lines: list[str], token: str, unit_id: int, result: str) -> str | None:
    rows = [row.split('|') for row in lines if row.startswith('UNIT_TRANSFORM_CONFIRMED|')]
    if len(rows) == 1 and len(rows[0]) == 6:
        row = rows[0]
        if row[1:4] == [token, str(unit_id), result] and row[4].isdigit():
            suffix = '|Use dismiss_popup if a stale promotion notification remains.' if result == 'PROMOTED' else ''
            return f'{result}|unit:{row[4]}|{row[5]}' + suffix
    return None


def build_delete(unit_id: int, token: str) -> str:
    return _CONTEXT + f'''
local ok,r=pcall(function()
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local unit=unitFor(owner,{unit_id})
 assert(UnitManager.CanStartCommand(unit,UnitCommandTypes.DELETE,false,true)==true,"CANNOT_DELETE")
 assert(type(UnitManager.RequestCommand)=="function","COMMAND_UNAVAILABLE")
 assert(type(Events.UnitRemovedFromMap.Add)=="function" and type(Events.UnitRemovedFromMap.Remove)=="function","REMOVAL_OBSERVER_UNAVAILABLE")
 return {{token={json.dumps(token)},owner=owner,id=unit:GetID(),requested={unit_id},
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
end)
if not ok then print("ERR:UNIT_DELETE_REFUSED|"..tostring(r));print("{SENTINEL}");return end
local previous=__civmcpDelete
if previous and previous.handler then pcall(function() Events.UnitRemovedFromMap.Remove(previous.handler) end) end
__civmcpDelete=r
r.handler=function(owner,id)
 if __civmcpDelete==r and r.entered and owner==r.owner and id==r.id and sameScope(r) then r.removed=true end
end
local armed=pcall(function() Events.UnitRemovedFromMap.Add(r.handler) end)
if not armed then print("ERR:UNIT_DELETE_REFUSED|REMOVAL_OBSERVER_UNAVAILABLE");print("{SENTINEL}");return end
r.entered=true
UnitManager.RequestCommand(unitFor(r.owner,r.id),UnitCommandTypes.DELETE)
r.returned=true
print("OK:UNIT_DELETE_REQUESTED|"..r.id)
print("{SENTINEL}")
'''


def build_delete_query(unit_id: int, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpDelete
local ok=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.requested=={unit_id})
 assert(sameScope(r) and r.entered==true and r.returned==true and r.removed==true)
 assert(UI.IsGameCoreBusy()==false)
 local unit=UnitManager.GetUnit(r.owner,r.id%65536)
 assert(unit==nil or unit:GetID()~=r.id)
end)
if ok then
 pcall(function() Events.UnitRemovedFromMap.Remove(r.handler) end)
 print("UNIT_TRANSFORM_CONFIRMED|{token}|{unit_id}|DELETED|"..r.id.."|removal:confirmed")
else print("UNIT_TRANSFORM_OBSERVED|{token}|UNKNOWN") end
print("{SENTINEL}")
'''


def build_promote(unit_id: int, promotion: str, token: str) -> str:
    # Mirror UnitPromotionPopup: PROMOTIONS contains row indices, not hashes.
    # The native command alone controls XP, recovery and movement expenditure.
    return _CONTEXT + f'''
local function eligible(unit,index)
 local allowed,results=UnitManager.CanStartCommand(unit,UnitCommandTypes.PROMOTE,false,true)
 assert(allowed==true,"CANNOT_PROMOTE")
 assert(type(results)=="table","PROMOTION_OPTIONS_UNAVAILABLE")
 local options=results[UnitCommandResults.PROMOTIONS]
 assert(type(options)=="table","PROMOTION_OPTIONS_UNAVAILABLE")
 local matches=0
 for _,option in pairs(options) do
  assert(scopeIndex(option),"PROMOTION_OPTION_UNREADABLE")
  if option==index then matches=matches+1 end
 end
 assert(matches==1,"PROMOTION_NOT_ELIGIBLE")
 assert(unit:GetExperience():HasPromotion(index)==false,"ALREADY_HAS_PROMOTION_OR_UNKNOWN")
end
local ok,r,unit,parameters=pcall(function()
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 assert(type(UnitManager.RequestCommand)=="function" and scopeInteger(UnitCommandTypes.PROMOTE)
  and scopeInteger(UnitCommandTypes.PARAM_PROMOTION_TYPE) and UnitCommandResults.PROMOTIONS~=nil,
  "PROMOTION_COMMAND_UNAVAILABLE")
 local owner=Game.GetLocalPlayer()
 local unit=unitFor(owner,{unit_id})
 assert(scopeIndex(unit:GetID()) and unit:GetX()~=-9999,"UNIT_CONSUMED_OR_UNKNOWN")
 local promo=GameInfo.UnitPromotions[{json.dumps(promotion)}]
 assert(promo and scopeIndex(promo.Index),"PROMOTION_UNAVAILABLE")
 assert(unit:GetExperience():HasPromotion(promo.Index)==false,"ALREADY_HAS_PROMOTION_OR_UNKNOWN")
 local receipt={{token={json.dumps(token)},owner=owner,id=unit:GetID(),requested={unit_id},
  promotion={json.dumps(promotion)},index=promo.Index,
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
 eligible(unit,receipt.index)
 local parameters={{[UnitCommandTypes.PARAM_PROMOTION_TYPE]=receipt.index}}
 -- Refuse a changed identity/choice while still before native entry.
 unit=unitFor(receipt.owner,receipt.id)
 assert(sameScope(receipt),"PROMOTION_SCOPE_CHANGED")
 eligible(unit,receipt.index)
 unit=unitFor(receipt.owner,receipt.id)
 assert(sameScope(receipt),"PROMOTION_SCOPE_CHANGED")
 return receipt,unit,parameters
end)
if not ok then print("ERR:UNIT_PROMOTION_REFUSED|"..tostring(r));print("{SENTINEL}");return end
__civmcpPromotion=r
r.entered=true
UnitManager.RequestCommand(unit,UnitCommandTypes.PROMOTE,parameters)
r.returned=true
print("OK:UNIT_PROMOTION_REQUESTED|"..r.id)
print("{SENTINEL}")
'''


def build_promote_query(unit_id: int, promotion: str, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpPromotion
local ok,details=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.requested=={unit_id}
  and r.promotion=={json.dumps(promotion)})
 assert(sameScope(r) and r.entered==true and r.returned==true)
 assert(UI.IsGameCoreBusy()==false)
 assert(GameInfo.UnitPromotions[r.promotion].Index==r.index)
 local unit=unitFor(r.owner,r.id)
 local exp=unit:GetExperience()
 assert(exp:HasPromotion(r.index)==true)
 local damage=number(unit:GetDamage())
 local moves=number(unit:GetMovesRemaining())
 assert(damage>=0 and moves>=0)
 -- This counter is absent from the shipped InGame experience object.
 local storedOK,stored=pcall(function() return exp:GetStoredPromotions() end)
 if not storedOK or not scopeIndex(stored) then stored="unknown" end
 return r.promotion..",damage:"..damage..",moves:"..moves..",stored_promotions:"..stored
end)
if ok then print("UNIT_TRANSFORM_CONFIRMED|{token}|{unit_id}|PROMOTED|"..r.id.."|"..details)
else print("UNIT_TRANSFORM_OBSERVED|{token}|UNKNOWN") end
print("{SENTINEL}")
'''


def build_stale_promotion_cleanup() -> str:
    """Explicit popup recovery only: never alter XP/stored counts on other units."""
    return '''
do
 local owner=Game.GetLocalPlayer()
 local candidates={}
 local listed=pcall(function()
  local list=NotificationManager.GetList(owner)
  assert(type(list)=="table")
  for _,id in ipairs(list) do
   local entry=NotificationManager.Find(owner,id)
   if entry and not entry:IsDismissed()
    and ((EndTurnBlockingTypes.ENDTURN_BLOCKING_UNIT_PROMOTION~=nil
     and entry:GetEndTurnBlocking()==EndTurnBlockingTypes.ENDTURN_BLOCKING_UNIT_PROMOTION)
     or entry:GetTypeName()=="NOTIFICATION_UNIT_PROMOTION_AVAILABLE") then
    table.insert(candidates,id)
   end
  end
 end)
 if listed and #candidates>0 then
  local known,stale=pcall(function()
   for _,unit in Players[owner]:GetUnits():Members() do
    assert(unit:GetOwner()==owner and unit:GetX()~=-9999)
    assert(UnitCommandTypes.PROMOTE~=nil)
    local allowed=UnitManager.CanStartCommand(unit,UnitCommandTypes.PROMOTE,false,true)
    assert(type(allowed)=="boolean")
    if allowed then return false end
   end
   return true
  end)
  if known and stale then
   for _,id in ipairs(candidates) do
    pcall(function()
     local entry=NotificationManager.Find(owner,id)
     if entry and not entry:IsDismissed() then NotificationManager.Dismiss(owner,id) end
    end)
   end
  end
  local allClosed=true
  for _,id in ipairs(candidates) do
   local ok,closed=pcall(function()
    local entry=NotificationManager.Find(owner,id)
    return entry==nil or entry:IsDismissed()==true
   end)
   if not ok or not closed then allClosed=false end
  end
  if allClosed then print("DISMISSED|UnitPromotion")
  else print("PENDING|UnitPromotion") end
 end
end
'''
