"""Native excavation and explicit artifact selection, each a single request.

Sources: shipped UnitOperations.xml / UnitPanel.lua, ChooseArtifact.lua and
GreatWorksOverview.lua. Excavation stops at the native selection; it never
chooses a civilization. Selection proves a new artifact in an owned slot.
"""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE

_CONTEXT = _LUA_ACTION_SCOPE + '''
local function actor(owner,id)
 local u=UnitManager.GetUnit(owner,id%65536)
 assert(u and u:GetID()==id and u:GetOwner()==owner,"UNIT_ID_CHANGED")
 assert(GameInfo.Units[u:GetType()].UnitType=="UNIT_ARCHAEOLOGIST","NOT_ARCHAEOLOGIST")
 return u
end
local function selection(owner)
 local u=Players[owner]:GetUnits():GetNextExtractingArchaeologist()
 if not u then return nil end
 assert(u:GetOwner()==owner,"EXTRACTOR_OWNER_CHANGED")
 local index=u:GetArchaeology():GetArtifactIndex()
 assert(scopeIndex(index),"ARTIFACT_INDEX_UNKNOWN")
 local a=Game.GetArtifactByIndex(index)
 assert(a and scopePlayer(a.ActingPlayerID) and scopeIndex(a.Type)
  and scopeIndex(a.ActingPlayerEra),"ARTIFACT_UNKNOWN")
 local single=a.Type==1 or a.Type==2 or a.Type==5
 assert(single or scopePlayer(a.TargetPlayerID),"ARTIFACT_TARGET_UNKNOWN")
 return {id=u:GetID(),index=index,acting=a.ActingPlayerID,target=single and -1 or a.TargetPlayerID,
  era=a.ActingPlayerEra,kind=a.Type,x=u:GetX(),y=u:GetY()}
end
local function selectionText(s)
 return "unit:"..s.id.."|artifact:"..s.index.."|era:"..s.era.."|type:"..s.kind
  .."|acting_player:"..s.acting.."|target_player:"..s.target
end
local function slots(owner)
 local result={}
 for _,city in Players[owner]:GetCities():Members() do
  assert(city:GetOwner()==owner,"CITY_OWNER_CHANGED")
  local b=city:GetBuildings()
  for building in GameInfo.Buildings() do
   if b:HasBuilding(building.Index) then
    local n=b:GetNumGreatWorkSlots(building.Index)
    assert(scopeIndex(n),"SLOT_COUNT_UNKNOWN")
    for slot=0,n-1 do
     local work=b:GetGreatWorkInSlot(building.Index,slot)
     assert(scopeInteger(work) and work>=-1,"SLOT_UNKNOWN")
     local key=city:GetID()..":"..building.Index..":"..slot
     result[key]={work=work,city=city:GetID(),x=city:GetX(),y=city:GetY(),building=building.Index,slot=slot}
    end
   end
  end
 end
 return result
end
local function retire(r)
 if r and r.handlers then
  for name,handler in pairs(r.handlers) do pcall(function() Events[name].Remove(handler) end) end
 end
end
local function sameActor(r)
 local ok,u=pcall(function() return UnitManager.GetUnit(r.owner,r.id%65536) end)
 local ownOK,owned=pcall(function() return Players[r.owner]:GetUnits():FindID(r.id) end)
 local known=ok and ownOK
 for _,unit in pairs({native=u,owned=owned}) do
  for _,field in ipairs({{"GetID",r.id},{"GetOwner",r.owner},{"GetType",r.unitType},{"GetX",r.x},{"GetY",r.y}}) do
   local read,value=pcall(function() return unit[field[1]](unit) end)
   if not read or not scopeIndex(value) then known=false
   elseif value~=field[2] then r.invalid=true end
  end
 end
 local present=u~=nil and owned~=nil
 local removed=u==nil and owned==nil and r.kind=="choose_artifact" and r.removed==true
 return known and not r.invalid and (present or removed)
end
'''


def build_choices() -> str:
    return _CONTEXT + f'''
local ok,s=pcall(selection,Game.GetLocalPlayer())
if not ok then print("ERR:ARTIFACT_CHOICES_UNKNOWN|"..tostring(s))
elseif s then print("ARTIFACT_CHOICE|"..selectionText(s))
else print("NO_ARTIFACT_CHOICE") end
print("{SENTINEL}")
'''


def build_submit(kind: str, unit_id: int, token: str, artifact_index: int = -1, player_id: int = -1) -> str:
    return _CONTEXT + f'''
local kind,owner,id={json.dumps(kind)},Game.GetLocalPlayer(),{unit_id}
local ok,r=pcall(function()
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local u=actor(owner,id)
 local r={{token={json.dumps(token)},kind=kind,id=id,owner=owner,x=u:GetX(),y=u:GetY(),unitType=u:GetType(),
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),
  turn=Game.GetCurrentGameTurn(),handlers={{}},entered=false,returned=false}}
 if kind=="excavate" then
  assert(selection(owner)==nil,"ARTIFACT_CHOICE_ALREADY_PENDING")
  local op=GameInfo.UnitOperations.UNITOPERATION_EXCAVATE
  assert(op and scopeInteger(op.Hash),"EXCAVATE_OPERATION_UNAVAILABLE")
  assert(UnitManager.CanStartOperation(u,op.Hash,nil,false)==true,"CANNOT_EXCAVATE")
  r.operation=op.Hash
 else
  local s=selection(owner)
  assert(s and s.id==id and s.index=={artifact_index},"ARTIFACT_CHOICE_CHANGED")
  assert(s.kind<=4,"HEROIC_RELIC_NOT_SUPPORTED")
  assert({player_id}==s.acting or (s.target>=0 and {player_id}==s.target),"INVALID_ARTIFACT_PLAYER")
  assert(scopeInteger(PlayerOperations.CHOOSE_ARTIFACT_PLAYER)
   and PlayerOperations.PARAM_PLAYER_ONE~=nil,"ARTIFACT_OPERATION_UNAVAILABLE")
  r.selection=s;r.operation=PlayerOperations.CHOOSE_ARTIFACT_PLAYER
  r.artifact=s.index;r.player={player_id};r.era=s.era;r.before=slots(owner)
 end
 return r
end)
if not ok then print("ERR:ARCHAEOLOGY_REFUSED|"..tostring(r));print("{SENTINEL}");return end
if __civmcpArchaeology~=nil and type(__civmcpArchaeology)~="table" then
 print("ERR:ARCHAEOLOGY_REFUSED|RECEIPT_STORAGE_UNKNOWN");print("{SENTINEL}");return
end
retire(__civmcpArchaeology);__civmcpArchaeology=r
r.handlers.UnitOperationStarted=function(p,uid,op)
 if __civmcpArchaeology==r and r.entered and p==owner and uid==id and sameScope(r) then
  if kind=="excavate" and op==r.operation then r.started=true else r.invalid=true end
 end
end
r.handlers.UnitRemovedFromMap=function(p,uid)
 if __civmcpArchaeology==r and r.entered and p==owner and uid==id and sameScope(r) then r.removed=true end
end
r.handlers.GreatWorkCreated=function(p,creator,x,y,building,index)
 if __civmcpArchaeology~=r or not r.entered or p~=owner or creator~=id or not sameScope(r) then return end
 if r.work and r.work.index~=index then r.invalid=true end
 r.work={{x=x,y=y,building=building,index=index}}
end
local armed,reason=pcall(function()
 for name,handler in pairs(r.handlers) do
  assert(type(Events[name].Add)=="function" and type(Events[name].Remove)=="function","OBSERVER_UNAVAILABLE")
  Events[name].Add(handler)
 end
end)
if not armed then retire(r);print("ERR:ARCHAEOLOGY_REFUSED|"..tostring(reason));print("{SENTINEL}");return end
-- PlayerOperations binds only a player, not an archaeologist/artifact. Slot
-- reads and observer installation happen before this final native-target check.
-- A changed next extractor must be refused before entering the request, never
-- discovered only after a selection has been applied to another unit.
local ready,prepared=pcall(function()
 local scoped,matched=sameScope(r),sameActor(r)
 assert(scoped and matched,"PRE_SUBMIT_SCOPE_OR_ACTOR_CHANGED")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local u=actor(owner,id)
 if kind=="excavate" then
  local op=GameInfo.UnitOperations.UNITOPERATION_EXCAVATE
  assert(op and op.Hash==r.operation,"EXCAVATE_OPERATION_CHANGED")
  assert(UnitManager.CanStartOperation(u,r.operation,nil,false)==true,"CANNOT_EXCAVATE")
  assert(selection(owner)==nil,"ARTIFACT_CHOICE_ALREADY_PENDING")
 else
  assert(PlayerOperations.CHOOSE_ARTIFACT_PLAYER==r.operation,"ARTIFACT_OPERATION_CHANGED")
  local current=selection(owner)
  assert(current,"ARTIFACT_CHOICE_CHANGED")
  for _,key in ipairs({{"id","index","acting","target","era","kind","x","y"}}) do
   assert(current[key]==r.selection[key],"ARTIFACT_CHOICE_CHANGED")
  end
 end
 local scopedAgain,matchedAgain=sameScope(r),sameActor(r)
 assert(scopedAgain and matchedAgain,"PRE_SUBMIT_SCOPE_OR_ACTOR_CHANGED")
 return u
end)
if not ready then retire(r);print("ERR:ARCHAEOLOGY_REFUSED|"..tostring(prepared));print("{SENTINEL}");return end
r.entered=true
if kind=="excavate" then UnitManager.RequestOperation(prepared,r.operation)
else
 local params={{}};params[PlayerOperations.PARAM_PLAYER_ONE]=r.player
 UI.RequestPlayerOperation(owner,r.operation,params)
end
r.returned=true
print("ARCHAEOLOGY_REQUESTED|"..r.token)
print("{SENTINEL}")
'''


def build_confirm(kind: str, unit_id: int, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpArchaeology
local ok,result=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.kind=={json.dumps(kind)} and r.id=={unit_id},"RECEIPT_MISMATCH")
 local scope,unit=sameScope(r),sameActor(r)
 assert(scope and unit and not r.invalid,"SCOPE_OR_ACTOR_UNKNOWN")
 assert(r.entered and r.returned,"REQUEST_NOT_RETURNED")
 if r.kind=="excavate" then
  assert(r.started,"EXCAVATE_EVENT_UNCONFIRMED")
  local s=selection(r.owner)
  assert(s and s.id==r.id and s.x==r.x and s.y==r.y,"EXCAVATION_SELECTION_UNCONFIRMED")
  return "EXCAVATED|"..selectionText(s).."|artifact selection still required"
 end
 local s=selection(r.owner)
 assert(not s or s.id~=r.id or s.index~=r.artifact,"ARTIFACT_SELECTION_STILL_PENDING")
 local w=r.work
 assert(w and scopeIndex(w.index),"ARTIFACT_CREATED_EVENT_UNCONFIRMED")
 local found
 for key,slot in pairs(slots(r.owner)) do
  if slot.work==w.index then
   local before=r.before[key]
   assert(not found and before and before.work==-1 and before.x==slot.x and before.y==slot.y
    and w.x==slot.x and w.y==slot.y and w.building==slot.building,"ARTIFACT_SLOT_CHANGED")
   found=slot
  end
 end
 assert(found,"ARTIFACT_NOT_IN_OWNED_SLOT")
 for _,before in pairs(r.before) do assert(before.work~=w.index,"ARTIFACT_ALREADY_EXISTED") end
 local city=CityManager.GetCityAt(found.x,found.y)
 assert(city and city:GetID()==found.city and city:GetOwner()==r.owner,"ARTIFACT_CITY_CHANGED")
 local wt=city:GetBuildings():GetGreatWorkTypeFromIndex(w.index)
 local info=GameInfo.GreatWorks[wt]
 assert(info and info.GreatWorkObjectType=="GREATWORKOBJECT_ARTIFACT","NOT_ARTIFACT")
 assert(Game.GetGreatWorkPlayer(w.index)==r.player,"ARTIFACT_PLAYER_UNCONFIRMED")
 assert(GameInfo.Eras[r.era] and info.EraType==GameInfo.Eras[r.era].EraType,"ARTIFACT_ERA_UNCONFIRMED")
 assert(sameScope(r) and sameActor(r),"SCOPE_OR_ACTOR_UNKNOWN")
 return "ARTIFACT_CHOSEN|unit:"..r.id.."|artifact:"..r.artifact.."|player:"..r.player
  .."|work:"..w.index.."|city:"..found.city.."|building:"..found.building.."|slot:"..found.slot
end)
if ok then
 retire(r);print("ARCHAEOLOGY_CONFIRMED|"..r.token.."|"..r.kind.."|"..r.id.."|"..result)
else print("ARCHAEOLOGY_PENDING|"..tostring(result)) end
print("{SENTINEL}")
'''


def parse_confirmation(rows: list[str], kind: str, unit_id: int, token: str) -> str | None:
    prefix = f"ARCHAEOLOGY_CONFIRMED|{token}|{kind}|{unit_id}|"
    matches = [row[len(prefix):] for row in rows if row.startswith(prefix)]
    if len(matches) == 1 and matches[0].startswith("EXCAVATED|" if kind == "excavate" else "ARTIFACT_CHOSEN|"):
        return matches[0]
    return None
