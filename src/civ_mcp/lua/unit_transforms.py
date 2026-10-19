"""Unit transformations retain full native identity and their original effects."""
import json

from civ_mcp.lua._helpers import SENTINEL


_CONTEXT = '''
local function number(v)
 assert(type(v)=="number" and v==v and v~=math.huge and v~=-math.huge,"NUMBER_UNAVAILABLE")
 return v
end
local function unitFor(owner,id)
 local unit=UnitManager.GetUnit(owner,id%65536)
 assert(unit and unit:GetOwner()==owner and (id<65536 or unit:GetID()==id),"UNIT_ID_CHANGED")
 return unit
end
local function sameScope(r)
 local ok,same=pcall(function()
  return Game.GetLocalPlayer()==r.owner and Game.GetCurrentGameTurn()==r.turn
   and PlayerConfigurations[r.owner]:GetCivilizationTypeName()==r.civ
   and GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")==r.seed
 end)
 if not ok or not same then r.invalid=true end
 return not r.invalid
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
 return {{token=token,owner=owner,id=unit:GetID(),requested={unit_id},before=before,target=target,
  cost=cost,balance=balance,resource=resource,resourceCost=resourceCost,stock=stock,
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
end)
if not ok then print("ERR:UNIT_UPGRADE_REFUSED|"..tostring(r));print("{SENTINEL}");return end
__civmcpUpgrade=r
r.entered=true
UnitManager.RequestCommand(unitFor(r.owner,r.id),UnitCommandTypes.UPGRADE,{{}})
r.returned=true
print("OK:UNIT_UPGRADE_REQUESTED|"..r.id)
print("{SENTINEL}")
'''


def build_upgrade_query(unit_id: int, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpUpgrade
local ok=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.requested=={unit_id})
 assert(sameScope(r) and r.entered==true and r.returned==true)
 assert(UI.IsGameCoreBusy()==false)
 assert(unitFor(r.owner,r.id):GetType()==r.target)
 assert(number(Players[r.owner]:GetTreasury():GetGoldBalance())==r.balance-r.cost)
 if r.resource>=0 then
  assert(number(Players[r.owner]:GetResources():GetResourceAmount(r.resource))==r.stock-r.resourceCost)
 end
end)
if ok then print("UNIT_TRANSFORM_CONFIRMED|{token}|{unit_id}|UPGRADED|"..r.id.."|type:"..r.before.."->"..r.target)
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


_CORE_CONTEXT = _CONTEXT.replace(
    'local unit=UnitManager.GetUnit(owner,id%65536)',
    'local unit=Players[owner]:GetUnits():FindID(id%65536)',
)


def build_promote(unit_id: int, promotion: str, token: str) -> str:
    # SetPromotion is the existing verified GameCore path; InGame PROMOTE has
    # historically returned without applying. Never retry via a second channel.
    return _CORE_CONTEXT + f'''
local ok,r=pcall(function()
 local owner=Game.GetLocalPlayer()
 local unit=unitFor(owner,{unit_id})
 assert(unit:GetX()~=-9999,"UNIT_CONSUMED")
 local promo=GameInfo.UnitPromotions[{json.dumps(promotion)}]
 assert(promo and type(promo.Index)=="number","PROMOTION_UNAVAILABLE")
 local info=GameInfo.Units[unit:GetType()]
 assert(info and info.PromotionClass==promo.PromotionClass,"WRONG_PROMOTION_CLASS")
 local exp=unit:GetExperience()
 assert(exp and exp:HasPromotion(promo.Index)==false,"ALREADY_HAS_PROMOTION_OR_UNKNOWN")
 assert(exp:CanPromote(promo.Index)==true,"CANNOT_PROMOTE")
 -- CanPromote alone describes the unit's availability. Bind the requested
 -- promotion's OR-prerequisites as the existing promotion query does.
 local hasPrereq,metPrereq=false,false
 for prereq in GameInfo.UnitPromotionPrereqs() do
  if prereq.UnitPromotion==promo.UnitPromotionType then
   hasPrereq=true
   local required=GameInfo.UnitPromotions[prereq.PrereqUnitPromotion]
   assert(required and type(required.Index)=="number","PROMOTION_PREREQUISITE_UNAVAILABLE")
   local has=exp:HasPromotion(required.Index)
   assert(type(has)=="boolean","PROMOTION_PREREQUISITE_UNKNOWN")
   if has then metPrereq=true end
  end
 end
 assert(not hasPrereq or metPrereq,"PROMOTION_PREREQUISITE_NOT_MET")
 assert(number(exp:GetExperiencePoints())>=number(exp:GetExperienceForNextLevel()),"INSUFFICIENT_XP")
 local stored=number(exp:GetStoredPromotions())
 assert(stored>=0 and stored==math.floor(stored),"STORED_PROMOTIONS_UNKNOWN")
 local damage=number(unit:GetDamage())
 assert(damage>=0,"DAMAGE_UNKNOWN")
 assert(type(exp.SetPromotion)=="function" and type(exp.ChangeStoredPromotions)=="function"
  and type(unit.SetDamage)=="function","PROMOTION_CAPABILITY_UNAVAILABLE")
 return {{token={json.dumps(token)},owner=owner,id=unit:GetID(),requested={unit_id},
  promotion={json.dumps(promotion)},index=promo.Index,stored=stored,damage=damage,
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
end)
if not ok then print("ERR:UNIT_PROMOTION_REFUSED|"..tostring(r));print("{SENTINEL}");return end
__civmcpPromotion=r
local unit=unitFor(r.owner,r.id)
local exp=unit:GetExperience()
r.promotionEntered=true
exp:SetPromotion(r.index)
r.promotionReturned=true
-- Later side effects belong to this one dispatch. A failed/partial dispatch
-- keeps the receipt; confirmation never repeats healing or stored changes.
if exp:HasPromotion(r.index)==true then
 r.storedEntered=true
 if r.stored>0 then exp:ChangeStoredPromotions(-r.stored) end
 r.storedReturned=true
 r.healEntered=true
 if r.damage>0 then unit:SetDamage(0) end
 r.healReturned=true
end
print("OK:UNIT_PROMOTION_REQUESTED|"..r.id)
print("{SENTINEL}")
'''


def build_promote_query(unit_id: int, promotion: str, token: str) -> str:
    return _CORE_CONTEXT + f'''
local r=__civmcpPromotion
local ok=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.requested=={unit_id}
  and r.promotion=={json.dumps(promotion)})
 assert(sameScope(r))
 assert(r.promotionEntered==true and r.promotionReturned==true
  and r.storedEntered==true and r.storedReturned==true
  and r.healEntered==true and r.healReturned==true)
 local unit=unitFor(r.owner,r.id)
 local exp=unit:GetExperience()
 assert(exp:HasPromotion(r.index)==true and exp:GetStoredPromotions()==0 and unit:GetDamage()==0)
end)
if ok then print("UNIT_TRANSFORM_CONFIRMED|{token}|{unit_id}|PROMOTED|"..r.id.."|"..r.promotion)
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
