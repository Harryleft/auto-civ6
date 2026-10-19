"""Charge-consuming unit actions with original-unit native effect evidence."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


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
 local value=city:GetReligion():GetTotalPressureOnCity(religion)
 assert(type(value)=="number" and value>=0 and value<math.huge,"PRESSURE_UNAVAILABLE")
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
'''


def build_unit_effect(kind: str, unit_id: int, token: str) -> str:
    return _CONTEXT + f'''
local ok,r=pcall(function()
 assert(__civmcpUnitEffect==nil or type(__civmcpUnitEffect)=="table","RECEIPT_STORAGE_UNAVAILABLE")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local unit=effectUnit(owner,{unit_id})
 local r={{token={json.dumps(token)},kind={json.dumps(kind)},id={unit_id},owner=owner,
  unitType=unit:GetType(),x=unit:GetX(),y=unit:GetY(),handlers={{}},
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
 if r.kind=="spread_religion" then
  r.mode="spread";r.religion=count(unit:GetReligionType());r.charges=count(unit:GetSpreadCharges())
  assert(r.religion>0 and r.charges>0,"NO_RELIGION_OR_CHARGES")
  local op=GameInfo.UnitOperations["UNITOPERATION_SPREAD_RELIGION"]
  assert(op and type(op.Hash)=="number","OPERATION_UNAVAILABLE")
  r.operation=op.Hash
  local city=spreadTarget(unit,r.operation)
  r.city={{id=count(city:GetID()),owner=count(city:GetOwner()),x=city:GetX(),y=city:GetY()}}
  r.pressure=pressure(city,r.religion)
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
   assert(r.charges>0,"NO_CHARGES")
   local command=GameInfo.UnitCommands["UNITCOMMAND_ACTIVATE_GREAT_PERSON"]
   assert(command and type(command.Hash)=="number" and type(UnitManager.RequestCommand)=="function","COMMAND_UNAVAILABLE")
   assert(UnitManager.CanStartCommand(unit,command.Hash,false,true)==true,"CANNOT_ACTIVATE")
   r.command=command.Hash
  end
 end
 r.event="UnitGreatPersonActivated"
 if r.operation then
  r.event="UnitOperationStarted"
  assert(type(UnitManager.RequestOperation)=="function","OPERATION_UNAVAILABLE")
  assert(unit:GetMovesRemaining()>0 and UnitManager.CanStartOperation(unit,r.operation,nil,r.params,true)==true,"CANNOT_OPERATE")
 end
 for _,name in ipairs({{r.event,"UnitRemovedFromMap"}}) do
  assert(Events and Events[name] and type(Events[name].Add)=="function"
   and type(Events[name].Remove)=="function","OBSERVER_UNAVAILABLE")
 end
 return r
end)
if not ok then print("ERR:UNIT_EFFECT_REFUSED|"..tostring(r));print("{SENTINEL}");return end
retireEffect(__civmcpUnitEffect)
__civmcpUnitEffect=r
-- Keep only scalar identities. Final-charge activation can destroy userdata inside RequestCommand.
if r.mode=="great_person" then
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
r.handlers.UnitRemovedFromMap=function(owner,id)
 if __civmcpUnitEffect==r and r.entered and owner==r.owner and id==r.id and sameScope(r) then r.removed=true end
end
local armed=pcall(function()
 for name,handler in pairs(r.handlers) do Events[name].Add(handler) end
end)
if not armed then retireEffect(r);print("ERR:UNIT_EFFECT_REFUSED|OBSERVER_UNAVAILABLE");print("{SENTINEL}");return end
r.entered=true
if r.mode=="great_person" then UnitManager.RequestCommand(effectUnit(r.owner,r.id),r.command)
else UnitManager.RequestOperation(effectUnit(r.owner,r.id),r.operation,r.params) end
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
 reason="SCOPE_CHANGED";assert(sameScope(r))
 reason="REQUEST_NOT_RETURNED";assert(r.entered==true and r.returned==true)
 reason="CORE_BUSY_OR_UNKNOWN";assert(UI.IsGameCoreBusy()==false)
 reason="ACTIVATION_NOT_OBSERVED"
 if r.mode=="great_person" then assert(r.activated==true) else assert(r.started==true) end
 reason="UNIT_ID_CHANGED"
 local unit=UnitManager.GetUnit(r.owner,r.id%65536)
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
 local city=cityAt(r.city.x,r.city.y)
 assert(city and city:GetID()==r.city.id and city:GetOwner()==r.city.owner)
 reason="PRESSURE_UNCONFIRMED"
 local current=pressure(city,r.religion)
 assert(current>r.pressure)
 return "charges_remaining:"..remaining,"city:"..r.city.owner..":"..r.city.id.." pressure:"..r.pressure.."->"..current
end)
if ok then
 retireEffect(r)
 print("UNIT_EFFECT_CONFIRMED|"..r.token.."|"..r.kind.."|"..r.id.."|"..r.mode.."|"..(r.individual or r.religion)
  .."|"..detail.."|"..target)
else print("UNIT_EFFECT_OBSERVED|{token}|"..reason) end
print("{SENTINEL}")
'''


def parse_unit_effect_confirmation(lines: list[str], kind: str, unit_id: int, token: str) -> str | None:
    rows = [line.split("|") for line in lines if line.startswith("UNIT_EFFECT_CONFIRMED|")]
    if len(rows) == 1 and len(rows[0]) == 8:
        row = rows[0]
        if row[1:4] == [token, kind, str(unit_id)]:
            if row[4] == "spread":
                return f"RELIGION_SPREAD|unit:{unit_id}|religion:{row[5]}|{row[6]}|{row[7]}|Pressure increase confirmed."
            if row[4] == "prophet":
                return f"RELIGION_FOUNDING_READY|unit:{unit_id}|individual:{row[5]}|{row[6]}|{row[7]}|Choose religion and beliefs separately."
            if row[4] == "great_person":
                return f"GP_ACTIVATED|unit:{unit_id}|individual:{row[5]}|{row[6]}|{row[7]}|Activation confirmed; rewards not measured."
    return None
