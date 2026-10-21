"""Unit commands and operations with original-unit native effect evidence."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE
from civ_mcp.lua.great_works import LUA_GREAT_WORK


_CONTEXT = _LUA_ACTION_SCOPE + '''
local function count(value)
 assert(type(value)=="number" and value>=0 and value<math.huge and value%1==0,"COUNT_UNAVAILABLE")
 return value
end
local function effectUnit(owner,id)
 local unit=UnitManager.GetUnit(owner,id%65536)
 assert(unit and unit:GetID()==id and unit:GetOwner()==owner,"UNIT_ID_CHANGED")
 return unit
end
local function pressure(city,religion)
 -- CityBannerManager calls row.Pressure LifetimePressure; its separate
 -- GetTotalPressureOnCity reading is not the accumulated spread effect.
 local rows=city:GetReligion():GetReligionsInCity()
 assert(type(rows)=="table","PRESSURE_UNAVAILABLE")
 local seen,value={},0
 for _,row in pairs(rows) do
  assert(type(row)=="table" and scopeInteger(row.Religion) and row.Religion>=-1
   and not seen[row.Religion],"PRESSURE_UNAVAILABLE")
  assert(type(row.Pressure)=="number" and row.Pressure>=0 and row.Pressure<math.huge,"PRESSURE_UNAVAILABLE")
  seen[row.Religion]=true
  if row.Religion==religion then value=row.Pressure end
 end
 -- Absence means zero only after every native record has been validated.
 return value
end
local function cityAt(x,y)
 local city=CityManager.GetCityAt(x,y)
 if city then assert(city:GetX()==x and city:GetY()==y,"CITY_POSITION_CHANGED") end
 return city
end
local function spreadTarget(unit,operation)
 -- Bind an actual operated city plot. Adjacent targets come only from native
 -- PLOTS/MODIFIERS, as in WorldInput, never a nearest-city heuristic.
 local target=cityAt(unit:GetX(),unit:GetY())
 if target then return target end
 local results=UnitManager.GetOperationTargets(unit,operation)
 assert(type(results)=="table","TARGET_UNAVAILABLE")
 local plots,modifiers=results[UnitOperationResults.PLOTS],results[UnitOperationResults.MODIFIERS]
 assert(type(plots)=="table" and type(modifiers)=="table","TARGET_UNAVAILABLE")
 for i,modifier in ipairs(modifiers) do
  if modifier==UnitOperationResults.MODIFIER_IS_TARGET then
   local plot=Map.GetPlotByIndex(plots[i])
   assert(plot,"TARGET_UNAVAILABLE")
   local city=cityAt(plot:GetX(),plot:GetY())
   if city then assert(target==nil,"AMBIGUOUS_CITY_TARGET");target=city end
  end
 end
 assert(target,"TARGET_UNAVAILABLE")
 return target
end
local function retireEffect(r)
 if not r or not r.handlers then return end
 for name,handler in pairs(r.handlers) do pcall(function() Events[name].Remove(handler) end) end
end
local function condemnTarget(owner,x,y)
 assert(PlayersVisibility[owner]:IsVisible(x,y)==true,"TARGET_NOT_VISIBLE")
 -- WorldInput inspects ANY layer; the default plot layer omits religious units.
 assert(scopeInteger(MapLayers.ANY),"TARGET_LAYER_UNAVAILABLE")
 local units=Units.GetUnitsInPlotLayerID(x,y,MapLayers.ANY)
 assert(type(units)=="table","TARGET_UNAVAILABLE")
 local target
 for _,unit in ipairs(units) do
  local other=unit:GetOwner()
  assert(scopePlayer(other),"TARGET_OWNER_UNAVAILABLE")
  if other~=owner then
   local entry=GameInfo.Units[unit:GetType()]
   assert(entry,"TARGET_TYPE_UNAVAILABLE")
   assert(type(entry.TrackReligion)=="boolean","TARGET_TYPE_UNAVAILABLE")
   if entry.TrackReligion==true then
    assert(type(entry.ReligiousStrength)=="number" and entry.ReligiousStrength>0
     and entry.ReligiousStrength<math.huge,"TARGET_TYPE_UNAVAILABLE")
    assert(target==nil,"AMBIGUOUS_RELIGIOUS_TARGET")
    target={owner=other,id=count(unit:GetID()),unitType=count(unit:GetType()),religion=count(unit:GetReligionType()),
     x=unit:GetX(),y=unit:GetY()}
    assert(target.religion>0 and target.x==x and target.y==y,"TARGET_IDENTITY_UNAVAILABLE")
   end
  end
 end
 assert(target,"NO_RELIGIOUS_TARGET")
 return target
end
local function sameEffectEntity(r,unit,before)
 local known=true
 local checks={{"GetID","id"},{"GetOwner","owner"},{"GetX","x"},{"GetY","y"}}
 if before.unitType~=nil then table.insert(checks,{"GetType","unitType"}) end
 if before.religion~=nil then table.insert(checks,{"GetReligionType","religion"}) end
 for _,check in ipairs(checks) do
  local ok,value=pcall(function() return unit[check[1]](unit) end)
  local valid=ok and (check[2]=="owner" and scopePlayer(value)
   or check[2]=="religion" and scopeInteger(value) and value>=-1
   or check[2]~="owner" and check[2]~="religion" and scopeIndex(value))
  if not valid then known=false elseif value~=before[check[2]] then r.invalid=true end
 end
 return known and not r.invalid
end
local function inquisitionFacts(r)
 -- Read both actor views and founder state independently. A missing getter
 -- must not hide a readable identity change in another original fact.
 -- InGame's launch flag is a stale UI cache; GameCore owns that result proof.
 local known=true
 local function read(getter)
  local ok,value=pcall(getter)
  if not ok then known=false end
  return ok,value
 end
 local nativeOK,native=read(function() return UnitManager.GetUnit(r.owner,r.id%65536) end)
 local ownedOK,owned=read(function() return Players[r.owner]:GetUnits():FindID(r.id) end)
 local function inspect(unit)
  if unit then
   local ok,matching=pcall(sameEffectEntity,r,unit,r)
   if not ok or not matching then known=false end
  end
 end
 inspect(native);inspect(owned)
 local religionOK,religion=read(function() return Players[r.owner]:GetReligion() end)
 if religionOK and religion then
  local createdOK,created=read(function() return religion:GetReligionTypeCreated() end)
  if not createdOK or not scopeInteger(created) or created< -1 then known=false
  elseif created~=r.created then r.invalid=true end
 else known=false end
 return known and nativeOK and ownedOK and not r.invalid,native,owned
end
''' + LUA_GREAT_WORK


def build_unit_effect(kind: str, unit_id: int, token: str, *, core_religion: int | None = None, core_followers: tuple | None = None) -> str:
    follower_lua = "nil" if core_followers is None else "{" + ",".join(map(str, core_followers)) + "}"
    return _CONTEXT + f'''
local ok,r=pcall(function()
 assert(__civmcpUnitEffect==nil or type(__civmcpUnitEffect)=="table","RECEIPT_STORAGE_UNAVAILABLE")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local civ=PlayerConfigurations[owner]:GetCivilizationTypeName()
 local seed,turn=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),Game.GetCurrentGameTurn()
 local unit=effectUnit(owner,{unit_id})
 local r={{token={json.dumps(token)},kind={json.dumps(kind)},id={unit_id},owner=owner,
  unitType=unit:GetType(),x=unit:GetX(),y=unit:GetY(),handlers={{}},
  civ=civ,seed=seed,turn=turn}}
 if r.kind=="launch_inquisition" then
  r.mode="inquisition"
  assert(scopeIndex(r.unitType) and scopeIndex(r.x) and scopeIndex(r.y),"UNIT_IDENTITY_UNAVAILABLE")
  local info=GameInfo.Units[r.unitType]
  assert(info and info.UnitType=="UNIT_APOSTLE" and info.LaunchInquisition==true,"NOT_AN_ELIGIBLE_APOSTLE")
  r.religion=count(unit:GetReligionType())
  local religion=Players[owner]:GetReligion()
  r.created={core_religion if core_religion is not None else 'nil'}
  assert(scopeIndex(r.created) and r.created>0 and r.religion==r.created
   and religion:GetReligionTypeCreated()==r.created,"OWN_FOUNDED_RELIGION_REQUIRED")
  local op=GameInfo.UnitOperations["UNITOPERATION_LAUNCH_INQUISITION"]
  assert(op and op.OperationType=="UNITOPERATION_LAUNCH_INQUISITION"
   and scopeInteger(op.Hash),"OPERATION_UNAVAILABLE")
  r.operation=op.Hash;r.params={{}}
 elseif r.kind=="condemn_heretic" then
  r.mode="condemn"
  assert(scopeIndex(r.unitType) and scopeIndex(r.x) and scopeIndex(r.y),"UNIT_IDENTITY_UNAVAILABLE")
  r.target=condemnTarget(owner,r.x,r.y)
  local command=GameInfo.UnitCommands["UNITCOMMAND_CONDEMN_HERETIC"]
  assert(command and command.CommandType=="UNITCOMMAND_CONDEMN_HERETIC"
   and scopeInteger(command.Hash) and scopeIndex(command.Index)
   and type(UnitManager.RequestCommand)=="function","COMMAND_UNAVAILABLE")
  assert(UnitManager.CanStartCommand(unit,command.Hash,false,true)==true,"CANNOT_CONDEMN")
  local current=condemnTarget(owner,r.x,r.y)
  for key,value in pairs(r.target) do assert(current[key]==value,"TARGET_CHANGED") end
  assert(unit:GetID()==r.id and unit:GetOwner()==r.owner and unit:GetType()==r.unitType
   and unit:GetX()==r.x and unit:GetY()==r.y,"UNIT_ID_CHANGED")
  r.command=command.Hash
 elseif r.kind=="spread_religion" then
  assert(scopeIndex(r.unitType) and scopeIndex(r.x) and scopeIndex(r.y),"UNIT_IDENTITY_UNAVAILABLE")
  r.mode="spread";r.religion=count(unit:GetReligionType());r.charges=count(unit:GetSpreadCharges())
  assert(r.religion>0 and r.charges>0,"NO_RELIGION_OR_CHARGES")
  local op=GameInfo.UnitOperations["UNITOPERATION_SPREAD_RELIGION"]
  assert(op and type(op.Hash)=="number","OPERATION_UNAVAILABLE")
  r.operation=op.Hash
  local city=spreadTarget(unit,r.operation)
  r.city={{id=count(city:GetID()),owner=count(city:GetOwner()),x=city:GetX(),y=city:GetY()}}
  r.pressure=pressure(city,r.religion)
   r.coreFollowers={follower_lua}
   if r.coreFollowers then
    local f=r.coreFollowers
    assert(r.city.owner==owner and r.city.id==f[1] and r.city.x==f[2] and r.city.y==f[3]
     and r.religion==f[4],"CORE_CITY_OR_RELIGION_CHANGED")
   end
  r.params={{}}
  r.params[UnitOperationTypes.PARAM_X],r.params[UnitOperationTypes.PARAM_Y]=r.city.x,r.city.y
 else
  local gp=unit:GetGreatPerson()
  r.individual=count(gp:GetIndividual());r.class=count(gp:GetClass())
  r.charges=count(gp:GetActionCharges());r.mode="great_person"
  if GameInfo.Units[r.unitType].UnitType=="UNIT_GREAT_PROPHET" then
   -- Native GreatPeople_Prophets.xml sets ActionCharges=0 for every prophet.
   r.mode="prophet"
   local religion=Players[owner]:GetReligion()
   r.beliefs=count(religion:GetNumBeliefsEarned())
   r.created=religion:GetReligionTypeCreated()
   -- The aggregate founding-unit flag can be false for an eligible prophet.
   -- Exact FOUND_RELIGION eligibility below decides whether to submit.
   assert(type(r.created)=="number" and r.created<0,"FOUNDING_NOT_AVAILABLE")
   local op=GameInfo.UnitOperations["UNITOPERATION_FOUND_RELIGION"]
   assert(op and type(op.Hash)=="number","OPERATION_UNAVAILABLE")
   r.operation=op.Hash;r.params={{}}
   r.params[UnitOperationTypes.PARAM_X],r.params[UnitOperationTypes.PARAM_Y]=r.x,r.y
  else
   if culturalUnits[GameInfo.Units[r.unitType].UnitType] then
    r.mode="great_work";prepareGreatWork(r)
   else assert(r.charges>0,"NO_CHARGES") end
   local command=GameInfo.UnitCommands["UNITCOMMAND_ACTIVATE_GREAT_PERSON"]
   assert(command and type(command.Hash)=="number" and type(UnitManager.RequestCommand)=="function","COMMAND_UNAVAILABLE")
   assert(UnitManager.CanStartCommand(unit,command.Hash,false,true)==true,"CANNOT_ACTIVATE")
   r.command=command.Hash
  end
 end
 r.event="UnitGreatPersonActivated"
 if r.mode=="condemn" then r.event="UnitCommandStarted" end
 if r.operation then
  r.event="UnitOperationStarted"
  assert(type(UnitManager.RequestOperation)=="function","OPERATION_UNAVAILABLE")
  if r.mode=="inquisition" then
   assert(UnitManager.CanStartOperation(unit,r.operation,nil,r.params)==true,"CANNOT_LAUNCH_INQUISITION")
  else
   assert(unit:GetMovesRemaining()>0 and UnitManager.CanStartOperation(unit,r.operation,nil,r.params,true)==true,"CANNOT_OPERATE")
  end
 end
 local eventNames={{r.event,"UnitRemovedFromMap"}}
 if r.mode=="great_work" then table.insert(eventNames,"GreatWorkCreated") end
 for _,name in ipairs(eventNames) do
  assert(Events and Events[name] and type(Events[name].Add)=="function"
   and type(Events[name].Remove)=="function","OBSERVER_UNAVAILABLE")
 end
 return r
end)
if not ok then print("ERR:UNIT_EFFECT_REFUSED|"..tostring(r));print("{SENTINEL}");return end
retireEffect(__civmcpUnitEffect)
__civmcpUnitEffect=r
-- Keep only scalar identities. Final-charge activation can destroy userdata inside RequestCommand.
if r.mode=="condemn" then
 r.handlers.UnitCommandStarted=function(owner,id,command)
  if __civmcpUnitEffect~=r or not r.entered or owner~=r.owner or id~=r.id or not sameScope(r) then return end
  if command==r.command then r.started=true else r.invalid=true end
 end
elseif r.mode=="great_person" or r.mode=="great_work" then
 r.handlers.UnitGreatPersonActivated=function(owner,id,class,individual)
  if __civmcpUnitEffect==r and r.entered and owner==r.owner and id==r.id
   and class==r.class and individual==r.individual and sameScope(r) then r.activated=true end
 end
else
 r.handlers.UnitOperationStarted=function(owner,id,operation)
  if __civmcpUnitEffect==r and r.entered and owner==r.owner and id==r.id
   and operation==r.operation and sameScope(r) then r.started=true end
 end
end
if r.mode=="great_work" then
 r.handlers.GreatWorkCreated=function(owner,creator,x,y,building,index)
  if __civmcpUnitEffect~=r or not r.entered or owner~=r.owner or creator~=r.id or not sameScope(r) then return end
  local prior=r.createdWork
  if prior and (prior.x~=x or prior.y~=y or prior.building~=building or prior.index~=index) then r.invalid=true end
  r.createdWork={{x=x,y=y,building=building,index=index}}
 end
end
r.handlers.UnitRemovedFromMap=function(owner,id)
 local target=r.mode=="condemn" and r.target or r
 if __civmcpUnitEffect==r and r.entered and owner==target.owner and id==target.id and sameScope(r) then r.removed=true end
end
local armed=pcall(function()
 for name,handler in pairs(r.handlers) do Events[name].Add(handler) end
end)
if not armed then retireEffect(r);print("ERR:UNIT_EFFECT_REFUSED|OBSERVER_UNAVAILABLE");print("{SENTINEL}");return end
local resolvedUnit
if r.mode=="condemn" or r.mode=="spread" or r.mode=="inquisition" or r.mode=="great_work" then
 local entryOK,actor=pcall(function()
  if r.mode=="inquisition" then
   local known,current,owned=inquisitionFacts(r)
   assert(known and current and owned)
   assert(UnitManager.CanStartOperation(current,r.operation,nil,r.params)==true,"CANNOT_LAUNCH_INQUISITION")
   known,current,owned=inquisitionFacts(r)
   local scopeKnown=sameScope(r)
   assert(known and current and owned and scopeKnown)
   return current
  end
  local actor=effectUnit(r.owner,r.id)
  assert(sameEffectEntity(r,actor,r))
  if r.mode=="great_work" then
   local gp=actor:GetGreatPerson()
   assert(gp:GetIndividual()==r.individual and gp:GetClass()==r.class,"GREAT_WORK_ACTOR_CHANGED")
   assert(UnitManager.CanStartCommand(actor,r.command,false,true)==true,"CANNOT_ACTIVATE")
  end
  if r.mode=="spread" then
   local city=CityManager.GetCityAt(r.city.x,r.city.y)
   assert(city and sameEffectEntity(r,city,r.city) and pressure(city,r.religion)==r.pressure)
   assert(sameEffectEntity(r,actor,r) and sameEffectEntity(r,city,r.city))
  end
  assert(sameScope(r))
  return actor
 end)
 if not entryOK then
  retireEffect(r);print("ERR:UNIT_EFFECT_REFUSED|IDENTITY_OR_SCOPE_CHANGED_OR_UNAVAILABLE");print("{SENTINEL}");return
 end
 resolvedUnit=actor
end
r.entered=true
if r.command then UnitManager.RequestCommand(resolvedUnit or effectUnit(r.owner,r.id),r.command)
else UnitManager.RequestOperation(resolvedUnit or effectUnit(r.owner,r.id),r.operation,r.params) end
r.returned=true
print("OK:UNIT_EFFECT_REQUESTED|"..r.kind.."|unit:{unit_id}")
print("{SENTINEL}")
'''


def build_unit_effect_query(kind: str, unit_id: int, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpUnitEffect
local reason="RECEIPT_MISSING_OR_MISMATCHED"
local ok,detail,target=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.kind=={json.dumps(kind)} and r.id=={unit_id})
 local scopeKnown=sameScope(r)
 if r.mode=="inquisition" then
  local known,native,owned=inquisitionFacts(r)
  local busyOK,busy=pcall(function() return UI.IsGameCoreBusy() end)
  local finalScope=sameScope(r)
  reason="INGAME_SCOPE_OR_IDENTITY_CHANGED";assert(not r.invalid)
  reason="INGAME_SCOPE_UNKNOWN";assert(scopeKnown and finalScope)
  reason="INGAME_UNIT_OR_RELIGION_UNKNOWN";assert(known)
  reason="CORE_BUSY_OR_UNKNOWN";assert(busyOK and busy==false)
  reason="REQUEST_NOT_RETURNED";assert(r.entered==true and r.returned==true)
  reason="STARTED_OR_REMOVED_EVENT_MISSING";assert(r.started==true and r.removed==true)
  reason="DUAL_UNIT_ABSENCE_UNCONFIRMED";assert(native==nil and owned==nil)
  print("INQUISITION_INGAME_WITNESS|"..r.token.."|"..r.id.."|"..r.owner.."|"..r.created.."|"..r.civ.."|"..r.seed.."|"..r.turn.."|events+absent")
  return "apostle:removed","at:"..r.x..","..r.y
 end
 reason="SCOPE_CHANGED";assert(scopeKnown)
 reason="REQUEST_NOT_RETURNED";assert(r.entered==true and r.returned==true)
 reason="CORE_BUSY_OR_UNKNOWN";assert(UI.IsGameCoreBusy()==false)
 if r.mode=="condemn" then
  -- Observe still-live original identities before event/effect completeness.
  -- A known change cannot later borrow removal of the restored identity.
  reason="UNIT_ID_CHANGED"
  local actor=UnitManager.GetUnit(r.owner,r.id%65536)
  assert(actor and sameEffectEntity(r,actor,r))
  reason="TARGET_ID_CHANGED"
  local native=UnitManager.GetUnit(r.target.owner,r.target.id%65536)
  local owned=Players[r.target.owner]:GetUnits():FindID(r.target.id)
  if native then assert(sameEffectEntity(r,native,r.target)) end
  if owned then assert(sameEffectEntity(r,owned,r.target)) end
 elseif r.mode=="spread" then
  reason="UNIT_ID_CHANGED"
  local actor=UnitManager.GetUnit(r.owner,r.id%65536)
  if actor then assert(sameEffectEntity(r,actor,r)) end
  reason="CITY_ID_CHANGED"
  local city=CityManager.GetCityAt(r.city.x,r.city.y)
  assert(city and sameEffectEntity(r,city,r.city))
 elseif r.mode=="great_work" then
  reason="GREAT_WORK_ACTOR_UNKNOWN";observeGreatWorkActor(r)
  if r.createdWork then reason="GREAT_WORK_CITY_UNKNOWN";observeGreatWorkCity(r) end
 end
 reason="ACTIVATION_NOT_OBSERVED"
 if r.mode=="great_person" or r.mode=="great_work" then assert(r.activated==true) else assert(r.started==true) end
 if r.mode=="great_work" then reason="GREAT_WORK_UNCONFIRMED";return confirmGreatWork(r) end
 reason="UNIT_ID_CHANGED"
 local unit=UnitManager.GetUnit(r.owner,r.id%65536)
 if r.mode=="condemn" then
  assert(unit and unit:GetID()==r.id and unit:GetOwner()==r.owner and unit:GetType()==r.unitType)
  assert(unit:GetX()==r.x and unit:GetY()==r.y)
  reason="TARGET_REMOVAL_UNCONFIRMED"
  assert(r.removed==true and UnitManager.GetUnit(r.target.owner,r.target.id%65536)==nil
   and Players[r.target.owner]:GetUnits():FindID(r.target.id)==nil)
  reason="SCOPE_CHANGED";assert(sameScope(r))
  return "type:"..r.target.unitType.." religion:"..r.target.religion,"at:"..r.x..","..r.y
 end
 local remaining
 if unit then
  assert(unit:GetID()==r.id and unit:GetOwner()==r.owner and unit:GetType()==r.unitType)
  assert(unit:GetX()==r.x and unit:GetY()==r.y)
  if r.mode=="spread" then
   assert(unit:GetReligionType()==r.religion)
   remaining=count(unit:GetSpreadCharges())
  else
   local gp=unit:GetGreatPerson()
   assert(gp:GetIndividual()==r.individual and gp:GetClass()==r.class)
   remaining=count(gp:GetActionCharges())
  end
  if r.mode~="prophet" then reason="CHARGES_UNCONFIRMED";assert(remaining==r.charges-1) end
 else
  reason="REMOVAL_UNCONFIRMED"
  assert(r.removed==true and Players[r.owner]:GetUnits():FindID(r.id)==nil)
  if r.mode~="prophet" then assert(r.charges==1) end
  remaining=0
 end
 if r.mode=="great_person" then return "charges_remaining:"..remaining,"at:"..r.x..","..r.y end
 if r.mode=="prophet" then
  reason="FOUNDING_READINESS_UNCONFIRMED"
  local religion=Players[r.owner]:GetReligion()
  local earned=count(religion:GetNumBeliefsEarned())
  assert(earned>r.beliefs and religion:GetReligionTypeCreated()==r.created and religion:HasReligiousFoundingUnit()==true)
  return "beliefs_earned:"..r.beliefs.."->"..earned,"at:"..r.x..","..r.y
 end
 reason="CITY_ID_CHANGED"
 local city=CityManager.GetCityAt(r.city.x,r.city.y)
 assert(city and sameEffectEntity(r,city,r.city))
 reason="PRESSURE_UNCONFIRMED"
 local current=pressure(city,r.religion)
  assert(current>r.pressure or (current==r.pressure and r.coreFollowers))
 if unit then assert(sameEffectEntity(r,unit,r)) end
 assert(sameEffectEntity(r,city,r.city) and sameScope(r))
  if current==r.pressure then
   print("SPREAD_INGAME_WITNESS|"..r.token.."|"..r.id.."|"..r.owner.."|"..r.religion.."|"..r.city.id.."|"..r.civ.."|"..r.seed.."|"..r.turn)
   return "charges_remaining:"..remaining,"city:"..r.city.owner..":"..r.city.id.." pressure:unchanged"
  end
  return "charges_remaining:"..remaining,"city:"..r.city.owner..":"..r.city.id.." pressure:"..r.pressure.."->"..current
end)
if ok then
 -- A confirmation response can be lost before ActionLifecycle retires its
 -- owner. Consuming religious witnesses must still latch later scope changes. The next
 -- actual unit-effect request replaces and retires these bounded listeners.
 if r.mode~="condemn" and r.mode~="spread" and r.mode~="inquisition" then retireEffect(r) end
 local subject=r.mode=="condemn" and (r.target.owner..":"..r.target.id) or (r.individual or r.religion)
 print("UNIT_EFFECT_CONFIRMED|"..r.token.."|"..r.kind.."|"..r.id.."|"..r.mode.."|"..subject
  .."|"..detail.."|"..target)
else print("UNIT_EFFECT_OBSERVED|{token}|"..reason) end
print("{SENTINEL}")
'''


def parse_unit_effect_confirmation(lines: list[str], kind: str, unit_id: int, token: str) -> str | None:
    rows = [line.split("|") for line in lines if line.startswith("UNIT_EFFECT_CONFIRMED|")]
    if len(rows) == 1 and len(rows[0]) == 8:
        row = rows[0]
        if row[1:4] == [token, kind, str(unit_id)]:
            if row[4] == "inquisition" and kind == "launch_inquisition":
                if any(line.startswith(('INQUISITION_CORE_OBSERVED|','UNIT_EFFECT_OBSERVED|')) for line in lines):
                    return None
                native = [line.split('|') for line in lines if line.startswith('INQUISITION_INGAME_WITNESS|')]
                core = [line.split('|') for line in lines if line.startswith('INQUISITION_CORE_CONFIRMED|')]
                if not (len(native) == len(core) == 1 and len(native[0]) == len(core[0]) == 9
                        and native[0][1:8] == core[0][1:8]
                        and native[0][1:3] == [token, str(unit_id)] and native[0][4] == row[5]
                        and native[0][8] == 'events+absent' and core[0][8] == 'false->true'):
                    return None
                return f"INQUISITION_LAUNCHED|unit:{unit_id}|religion:{row[5]}|launched:false->true|{row[7]}|Apostle consumption confirmed."
            if row[4] == "condemn" and kind == "condemn_heretic":
                return f"HERETIC_CONDEMNED|unit:{unit_id}|target:{row[5]}|{row[6]}|{row[7]}|Target removal confirmed; rewards not measured."
            if row[4] == "spread":
                if "pressure:unchanged" in row[7]:
                    native = [line.split('|') for line in lines if line.startswith('SPREAD_INGAME_WITNESS|')]
                    core = [line.split('|') for line in lines if line.startswith('SPREAD_CORE_CONFIRMED|')]
                    if not (len(native) == len(core) == 1 and len(native[0]) == 9 and len(core[0]) == 11
                            and native[0][1:] == core[0][1:9] and native[0][1:3] == [token, str(unit_id)]
                            and core[0][4] == row[5] and core[0][9].isdigit() and core[0][10].isdigit()
                            and int(core[0][10]) > int(core[0][9])
                            and row[7] == f"city:{core[0][3]}:{core[0][5]} pressure:unchanged"
                            and not any(line.startswith('SPREAD_CORE_OBSERVED|') for line in lines)):
                        return None
                    return (f"RELIGION_SPREAD|unit:{unit_id}|religion:{row[5]}|{row[6]}|{row[7]}"
                            f"|GameCore followers:{core[0][9]}->{core[0][10]}; UI pressure unchanged.")
                return f"RELIGION_SPREAD|unit:{unit_id}|religion:{row[5]}|{row[6]}|{row[7]}|Pressure increase confirmed."
            if row[4] == "prophet":
                return f"RELIGION_FOUNDING_READY|unit:{unit_id}|individual:{row[5]}|{row[6]}|{row[7]}|Choose religion and beliefs separately."
            if row[4] == "great_person":
                return f"GP_ACTIVATED|unit:{unit_id}|individual:{row[5]}|{row[6]}|{row[7]}|Activation confirmed; rewards not measured."
            if row[4] == "great_work" and kind == "activate_great_person":
                return f"GREAT_WORK_CREATED|unit:{unit_id}|individual:{row[5]}|{row[6]}|{row[7]}|Creation confirmed; yields not measured."
    return None
