"""Native inquisitor operation with bound city pressure and charge evidence."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


_CONTEXT = _LUA_ACTION_SCOPE + '''
local function number(value)
 return type(value)=="number" and value==value and value>=0 and value<math.huge
end
local function entity(r,value,before,religious)
 local known=true
 local fields={{"GetID","id",scopeIndex},{"GetOwner","owner",scopePlayer},
  {"GetX","x",scopeIndex},{"GetY","y",scopeIndex}}
 if religious then
  table.insert(fields,{"GetType","unitType",scopeIndex})
  table.insert(fields,{"GetReligionType","religion",scopeIndex})
 end
 for _,field in ipairs(fields) do
  local ok,actual=pcall(function() return value[field[1]](value) end)
  if not ok or not field[3](actual) then known=false
  elseif actual~=before[field[2]] then r.invalid=true end
 end
 return known and not r.invalid
end
local function pressures(city)
 -- CityBannerManager calls these rows LifetimePressure; the separate
 -- GetTotalPressureOnCity is incoming flow, not the accumulated stock.
 local rows=city:GetReligion():GetReligionsInCity()
 assert(type(rows)=="table","PRESSURE_UNAVAILABLE")
 local values={}
 for _,row in pairs(rows) do
  assert(type(row)=="table" and scopeInteger(row.Religion) and row.Religion>=-1
   and values[row.Religion]==nil and number(row.Pressure),"PRESSURE_UNAVAILABLE")
  values[row.Religion]=row.Pressure
 end
 return values
end
local function cityTarget(unit,operation,owner)
 -- UnitPanel's self-action can return description-only results. An exact
 -- owned city center is authoritative then; no adjacent/nearest-city guess.
 local x,y=unit:GetX(),unit:GetY()
 assert(scopeIndex(x) and scopeIndex(y),"UNIT_POSITION_UNAVAILABLE")
 local center=CityManager.GetCityAt(x,y)
 local centerID
 if center then
  assert(center:GetOwner()==owner,"OWNED_CITY_REQUIRED")
  assert(center:GetX()==x and center:GetY()==y,"CITY_POSITION_CHANGED")
  centerID=center:GetID()
  assert(scopeIndex(centerID),"CITY_IDENTITY_UNAVAILABLE")
 end
 local function confirmCenter()
  if center then
   local current=CityManager.GetCityAt(x,y)
   assert(current and current:GetID()==centerID and current:GetOwner()==owner
    and current:GetX()==x and current:GetY()==y,"CITY_TARGET_CHANGED")
  end
 end
 local results=UnitManager.GetOperationTargets(unit,operation)
 assert(type(results)=="table","NATIVE_TARGET_UNCONFIRMED")
 local plots,modifiers=results[UnitOperationResults.PLOTS],results[UnitOperationResults.MODIFIERS]
 if plots==nil and modifiers==nil then
  assert(center,"NATIVE_TARGET_UNCONFIRMED")
  confirmCenter()
  return center
 end
 -- If either coordinate field is supplied, all native target facts must be
 -- readable and agree with the center. Never hide partial/conflicting rows.
 assert(type(plots)=="table" and type(modifiers)=="table","NATIVE_TARGET_UNCONFIRMED")
 for i,plot in pairs(plots) do
  assert(scopeIndex(i) and scopeIndex(plot) and modifiers[i]~=nil,"NATIVE_TARGET_UNCONFIRMED")
 end
 local target
 for i,modifier in pairs(modifiers) do
  assert(scopeIndex(i) and scopeInteger(modifier) and plots[i]~=nil,"NATIVE_TARGET_UNCONFIRMED")
  if modifier==UnitOperationResults.MODIFIER_IS_TARGET then
   local plot=Map.GetPlotByIndex(plots[i]);assert(plot,"NATIVE_TARGET_UNCONFIRMED")
   local px,py=plot:GetX(),plot:GetY()
   assert(scopeIndex(px) and scopeIndex(py),"NATIVE_TARGET_UNCONFIRMED")
   local city=CityManager.GetCityAt(px,py)
   assert(city,"NATIVE_TARGET_UNCONFIRMED")
   assert(city:GetOwner()==owner,"OWNED_CITY_REQUIRED")
   assert(target==nil,"AMBIGUOUS_CITY_TARGET")
   assert(scopeIndex(city:GetID()) and city:GetX()==px and city:GetY()==py,"CITY_POSITION_CHANGED")
   if center then
    assert(city:GetID()==centerID and px==x and py==y,"CITY_TARGET_CHANGED")
   end
   target=city
  end
 end
 assert(target,"NATIVE_TARGET_UNCONFIRMED")
 confirmCenter()
 return target
end
local function facts(r)
 -- Inspect every readable original identity even when another lookup is
 -- unknown, so a valid mismatch remains sticky after later restoration.
 local known=true
 local function read(fn)
  local ok,value=pcall(fn)
  if not ok then known=false end
  return ok,value
 end
 local nativeOK,native=read(function() return UnitManager.GetUnit(r.owner,r.id%65536) end)
 local ownedOK,owned=read(function() return Players[r.owner]:GetUnits():FindID(r.id) end)
 for _,unit in pairs({native=native,owned=owned}) do
  if not entity(r,unit,r,true) then known=false end
 end
 local cityOK,city=read(function() return CityManager.GetCityAt(r.city.x,r.city.y) end)
 if city then
  if not entity(r,city,r.city,false) then known=false end
 else
  known=false
  if cityOK then r.invalid=true end
 end
 local scopeKnown=sameScope(r)
 return known and nativeOK and ownedOK and cityOK and scopeKnown and not r.invalid,native,owned,city
end
local function retire(r)
 if r and r.handlers then
  for name,handler in pairs(r.handlers) do pcall(function() Events[name].Remove(handler) end) end
 end
end
'''


def build_remove_heresy(unit_id: int, token: str) -> str:
    return _CONTEXT + f'''
local ok,r=pcall(function()
 assert(__civmcpRemoveHeresy==nil or type(__civmcpRemoveHeresy)=="table","RECEIPT_STORAGE_UNAVAILABLE")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local unit=UnitManager.GetUnit(owner,{unit_id}%65536)
 assert(unit and unit:GetID()=={unit_id} and unit:GetOwner()==owner,"ORIGINAL_UNIT_REQUIRED")
 local r={{token={json.dumps(token)},id={unit_id},owner=owner,unitType=unit:GetType(),
  religion=unit:GetReligionType(),x=unit:GetX(),y=unit:GetY(),charges=unit:GetSpreadCharges(),
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn(),handlers={{}}}}
 assert(scopeIndex(r.unitType) and scopeIndex(r.religion) and r.religion>0 and scopeIndex(r.x)
  and scopeIndex(r.y) and scopeIndex(r.charges) and r.charges>0,"UNIT_IDENTITY_OR_CHARGES_UNAVAILABLE")
 assert(GameInfo.Units[r.unitType].UnitType=="UNIT_INQUISITOR","INQUISITOR_REQUIRED")
 local operation=GameInfo.UnitOperations["UNITOPERATION_REMOVE_HERESY"]
 assert(operation and operation.OperationType=="UNITOPERATION_REMOVE_HERESY"
  and scopeInteger(operation.Hash),"OPERATION_UNAVAILABLE")
 r.operation=operation.Hash
 assert(type(UnitManager.RequestOperation)=="function","OPERATION_UNAVAILABLE")
 local city=cityTarget(unit,r.operation,owner)
 r.city={{id=city:GetID(),owner=city:GetOwner(),x=city:GetX(),y=city:GetY()}}
 assert(scopeIndex(r.city.id) and r.city.owner==owner and scopeIndex(r.city.x)
  and scopeIndex(r.city.y),"CITY_IDENTITY_UNAVAILABLE")
 r.pressures=pressures(city)
 local foreign=false
 for religion,pressure in pairs(r.pressures) do
  if religion>=0 and religion~=r.religion and pressure>0 then foreign=true end
 end
 assert(foreign,"NO_FOREIGN_PRESSURE")
 for _,name in ipairs({{"UnitOperationStarted","UnitRemovedFromMap"}}) do
  assert(Events[name] and type(Events[name].Add)=="function"
   and type(Events[name].Remove)=="function","OBSERVER_UNAVAILABLE")
 end
 assert(sameScope(r),"SCOPE_UNAVAILABLE")
 return r
end)
if not ok then print("ERR:REMOVE_HERESY_REFUSED|"..tostring(r));print("{SENTINEL}");return end
retire(__civmcpRemoveHeresy)
__civmcpRemoveHeresy=r
r.handlers.UnitOperationStarted=function(owner,id,operation)
 if __civmcpRemoveHeresy==r and r.entered and owner==r.owner and id==r.id then
  local known=sameScope(r)
  if operation~=r.operation then r.invalid=true elseif known then r.started=true end
 end
end
r.handlers.UnitRemovedFromMap=function(owner,id)
 if __civmcpRemoveHeresy==r and r.entered and owner==r.owner and id==r.id and sameScope(r) then r.removed=true end
end
local armed=pcall(function() for name,handler in pairs(r.handlers) do Events[name].Add(handler) end end)
local ready,actor=pcall(function()
 assert(armed,"OBSERVER_UNAVAILABLE")
 local known,unit,owned,city=facts(r)
 assert(known and unit and owned,"IDENTITY_OR_SCOPE_UNKNOWN_OR_CHANGED")
 -- UnitPanel's generic action checks native strict eligibility with NO_TARGETS;
 -- submission below deliberately omits spread religion's coordinate params.
 assert(UnitManager.CanStartOperation(unit,r.operation,nil,false,OperationResultsTypes.NO_TARGETS)==true,
  "CANNOT_REMOVE_HERESY")
 local target=cityTarget(unit,r.operation,r.owner)
 assert(entity(r,target,r.city,false),"CITY_TARGET_CHANGED")
 local current=pressures(city)
 for religion,value in pairs(r.pressures) do assert(current[religion]==value,"PRESSURE_BASELINE_CHANGED") end
 for religion,value in pairs(current) do assert(r.pressures[religion]==value,"PRESSURE_BASELINE_CHANGED") end
 known,unit,owned,city=facts(r)
 assert(known and unit and owned and unit:GetSpreadCharges()==r.charges,"IDENTITY_OR_SCOPE_UNKNOWN_OR_CHANGED")
 known,unit,owned,city=facts(r)
 assert(known and unit and owned,"IDENTITY_OR_SCOPE_UNKNOWN_OR_CHANGED")
 assert(sameScope(r),"SCOPE_UNKNOWN_OR_CHANGED")
 return unit
end)
if not ready then
 retire(r);print("ERR:REMOVE_HERESY_REFUSED|"..tostring(actor));print("{SENTINEL}");return
end
r.entered=true
UnitManager.RequestOperation(actor,r.operation)
r.returned=true
print("OK:REMOVE_HERESY_REQUESTED|unit:{unit_id}")
print("{SENTINEL}")
'''


def build_remove_heresy_query(unit_id: int, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpRemoveHeresy
local ok,detail=pcall(function()
 assert(type(r)=="table" and r.id=={unit_id} and r.token=={json.dumps(token)},"RECEIPT_MISSING_OR_MISMATCHED")
 local known,native,owned,city=facts(r)
 local effectOK,effect=pcall(function()
 assert(known,"IDENTITY_OR_SCOPE_UNKNOWN_OR_CHANGED")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 assert(r.entered==true and r.returned==true,"REQUEST_NOT_RETURNED")
 assert(r.started==true,"ORIGINAL_STARTED_EVENT_MISSING")
 local remaining
 if native and owned then
  remaining=native:GetSpreadCharges()
  assert(scopeIndex(remaining) and remaining==r.charges-1
   and owned:GetSpreadCharges()==remaining,"ONE_CHARGE_UNCONFIRMED")
 else
  assert(native==nil and owned==nil and r.charges==1 and r.removed==true,"FINAL_CONSUMPTION_UNCONFIRMED")
  remaining=0
 end
 local current=pressures(city)
 local changed,parts=false,{{}}
 for religion,value in pairs(r.pressures) do
  if religion>=0 and religion~=r.religion then
   local after=current[religion] or 0
   assert(after<=value,"FOREIGN_PRESSURE_INCREASED")
   if after<value then changed=true end
   table.insert(parts,religion..":"..value.."->"..after)
  end
 end
 for religion,value in pairs(current) do
  if religion>=0 and religion~=r.religion then
   assert(value<=(r.pressures[religion] or 0),"UNBOUND_FOREIGN_PRESSURE_INCREASED")
  end
 end
 assert(changed,"FOREIGN_PRESSURE_DECREASE_UNCONFIRMED")
 table.sort(parts)
 return "religion:"..r.religion.."|charges_remaining:"..remaining.."|city:"..r.city.owner..":"..r.city.id
  .."|foreign_pressure:"..table.concat(parts,",")
 end)
 -- Always re-observe identity after effect getters, even if one was unknown.
 -- A lost reply or an unrelated unreadable pressure must not erase a change.
 local finalKnown=facts(r)
 assert(finalKnown,"IDENTITY_OR_SCOPE_UNKNOWN_OR_CHANGED")
 assert(effectOK,effect)
 return effect
end)
if ok then print("REMOVE_HERESY_CONFIRMED|"..r.token.."|"..r.id.."|"..detail)
else print("REMOVE_HERESY_OBSERVED|{token}|"..tostring(detail)) end
print("{SENTINEL}")
'''


def parse_remove_heresy_confirmation(lines: list[str], unit_id: int, token: str) -> str | None:
    prefix = f"REMOVE_HERESY_CONFIRMED|{token}|{unit_id}|"
    matches = [line for line in lines if line.startswith(prefix)]
    if len(matches) == 1:
        return f"HERESY_REMOVED|unit:{unit_id}|" + matches[0][len(prefix):] + "|Foreign accumulated pressure decreased; conversion not inferred."
    return None
