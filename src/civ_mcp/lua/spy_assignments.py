"""Confirm spy assignment starts independently of their multi-turn results."""
import json
from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE
from civ_mcp.lua.espionage import _SPY_OP_HASHES
from civ_mcp.lua.spy_roster import SPY_ROSTER


OPERATIONS = {name: 'UNITOPERATION_SPY_' + ('TRAVEL_NEW_CITY' if name == 'TRAVEL' else name)
              for name in _SPY_OP_HASHES}
_CONTEXT = _LUA_ACTION_SCOPE + SPY_ROSTER + '''
local function integer(v)
 assert(type(v)=="number" and v>=0 and v<math.huge and v==math.floor(v),"NUMBER_UNAVAILABLE")
 return v
end
local function spyFor(owner,id)
 local spy=UnitManager.GetUnit(owner,id%65536)
 assert(spy and spy:GetOwner()==owner,"SPY_ID_CHANGED")
 local full=integer(spy:GetID())
 assert((id<65536 or full==id) and full%65536==id%65536,"SPY_ID_CHANGED")
 local info=GameInfo.Units[spy:GetType()]
 assert(info and info.UnitType=="UNIT_SPY","NOT_A_SPY")
 return spy
end
local function retireAssignment(r)
 if r and r.handler then pcall(function() Events.UnitOperationStarted.Remove(r.handler) end) end
 if r and r.removalHandler then pcall(function() Events.UnitRemovedFromMap.Remove(r.removalHandler) end) end
end
local function assignmentValue(r,read,valid,expected)
 local ok,value=pcall(read)
 if not ok or not valid(value) then return false end
 if value~=expected then r.invalid=true end
 return true
end
'''


def build_spy_assignment(unit_id: int, mission: str, x: int, y: int, token: str) -> str:
    return _CONTEXT + f'''
local ok,r=pcall(function()
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local civ=PlayerConfigurations[owner]:GetCivilizationTypeName()
 local seed,turn=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),Game.GetCurrentGameTurn()
 local spy=spyFor(owner,{unit_id})
 -- GetSpyOperation is an index; direct GameInfo indexing works even when its iterator is empty.
 local op=GameInfo.UnitOperations[{json.dumps(OPERATIONS[mission])}]
 assert(op and type(op.Hash)=="number" and op.OperationType=={json.dumps(OPERATIONS[mission])},"OPERATION_UNAVAILABLE")
 integer(op.Index)
 assert(spy:GetSpyOperation()==-1,"SPY_ALREADY_ASSIGNED_OR_UNKNOWN")
 local oldEnd=spy:GetSpyOperationEndTurn()
 assert(type(oldEnd)=="number" and oldEnd==math.floor(oldEnd),"ASSIGNMENT_TIME_UNAVAILABLE")
 local plot=Map.GetPlot({x},{y})
 assert(plot and plot:GetX()=={x} and plot:GetY()=={y},"TARGET_PLOT_UNAVAILABLE")
 local city=Cities.GetPlotPurchaseCity(plot)
 assert(city,"TARGET_CITY_UNAVAILABLE")
 local cityId,cityOwner=integer(city:GetID()),integer(city:GetOwner())
 local cityX,cityY=integer(city:GetX()),integer(city:GetY())
 local params={{[UnitOperationTypes.PARAM_X]={x},[UnitOperationTypes.PARAM_Y]={y}}}
 local allowed,noop=false,false
 if "{mission}"=="TRAVEL" then
  assert(cityX=={x} and cityY=={y},"TRAVEL_REQUIRES_CITY_CENTER")
  noop=spy:GetX()==cityX and spy:GetY()==cityY
  if noop then allowed=true else allowed=UnitManager.CanStartOperation(spy,op.Hash,plot,params) end
 else
  local currentPlot=Map.GetPlot(spy:GetX(),spy:GetY())
  local currentCity=currentPlot and Cities.GetPlotPurchaseCity(currentPlot)
  assert(currentCity and currentCity:GetID()==cityId and currentCity:GetOwner()==cityOwner,"SPY_NOT_IN_TARGET_CITY")
  local result
  allowed,result=UnitManager.CanStartOperation(spy,op.Hash,Map.GetPlot(cityX,cityY),false,true)
  if allowed==true then
   local candidates=result and result[UnitOperationResults.PLOTS]
   if candidates~=nil then assert(type(candidates)=="table","TARGETS_UNKNOWN") end
   if candidates and #candidates>0 then
    local found,choices=false,{{}}
    for _,index in ipairs(candidates) do
     if index==plot:GetIndex() then found=true end
     local candidate=Map.GetPlotByIndex(index)
     if candidate then table.insert(choices,candidate:GetX()..","..candidate:GetY()) end
    end
    assert(found,"MISSION_REQUIRES_NATIVE_TARGET_PLOT|"..table.concat(choices,";"))
   else assert({x}==cityX and {y}==cityY,"MISSION_REQUIRES_CITY_CENTER") end
  end
 end
 assert(allowed==true,"CANNOT_ASSIGN_{mission}")
 assert(type(UnitManager.RequestOperation)=="function","OPERATION_UNAVAILABLE")
 assert(Events.UnitOperationStarted and type(Events.UnitOperationStarted.Add)=="function"
  and type(Events.UnitOperationStarted.Remove)=="function","OPERATION_OBSERVER_UNAVAILABLE")
 local identity,mapRoster,offRoster,offCount
 if "{mission}"=="TRAVEL" and not noop then
  assert(Events.UnitRemovedFromMap and type(Events.UnitRemovedFromMap.Add)=="function"
   and type(Events.UnitRemovedFromMap.Remove)=="function","REMOVAL_OBSERVER_UNAVAILABLE")
  identity=spyIdentity(spy)
  local found=Players[owner]:GetUnits():FindID(spy:GetID())
  assert(found and found:GetID()==spy:GetID() and found:GetOwner()==owner,"SPY_LOOKUP_UNAVAILABLE")
  mapRoster=mapSpyRoster(owner)
  assert(sameSpyIdentity(mapRoster[spy:GetID()],identity),"SPY_ROSTER_ID_UNAVAILABLE")
  for id,other in pairs(mapRoster) do
   assert(id==spy:GetID() or not sameSpyIdentity(other,identity),"SPY_IDENTITY_AMBIGUOUS")
  end
  offRoster,offCount=offMapSpyRoster(owner)
  for _,other in pairs(offRoster) do assert(not sameSpyIdentity(other,identity),"SPY_IDENTITY_ALREADY_OFFMAP") end
 end
 return {{token={json.dumps(token)},requested={unit_id},id=spy:GetID(),owner=owner,mission={json.dumps(mission)},
  x={x},y={y},plot=integer(plot:GetIndex()),city=cityId,cityOwner=cityOwner,cityX=cityX,cityY=cityY,
  operation=op.Hash,index=op.Index,params=params,noop=noop,unitType=spy:GetType(),
  spyIdentity=identity,mapRoster=mapRoster,offRoster=offRoster,offCount=offCount,
  civ=civ,seed=seed,turn=turn}}
end)
if not ok then print("ERR:SPY_ASSIGNMENT_REFUSED|"..tostring(r));print("{SENTINEL}");return end
retireAssignment(__civmcpSpyAssignment)
__civmcpSpyAssignment=r
if r.noop then print("OK:SPY_ASSIGNMENT_NOOP|"..r.id);print("{SENTINEL}");return end
r.handler=function(owner,id,operation)
 if __civmcpSpyAssignment==r and r.entered==true and owner==r.owner and id==r.id
  and operation==r.operation and sameScope(r) then r.started=true end
end
r.removalHandler=function(owner,id)
 -- Native travel can remove the map unit before emitting OperationStarted.
 -- Both exact events are required at confirmation; neither proves the other.
 if __civmcpSpyAssignment==r and r.entered==true and owner==r.owner and id==r.id
  and sameScope(r) then r.removed=true end
end
local armed=pcall(function()
 Events.UnitOperationStarted.Add(r.handler)
 if r.mission=="TRAVEL" then Events.UnitRemovedFromMap.Add(r.removalHandler) end
end)
if not armed then retireAssignment(r);print("ERR:SPY_ASSIGNMENT_REFUSED|OPERATION_OBSERVER_UNAVAILABLE");print("{SENTINEL}");return end
local ready,current=pcall(function()
 assert(sameScope(r),"SCOPE_CHANGED_BEFORE_ENTRY")
 local current=spyFor(r.owner,r.id)
 assert(current:GetType()==r.unitType,"SPY_ID_CHANGED")
 if r.spyIdentity then assert(sameSpyIdentity(spyIdentity(current),r.spyIdentity),"SPY_ID_CHANGED") end
 local plot=Map.GetPlot(r.x,r.y)
 local target=plot and Cities.GetPlotPurchaseCity(plot)
 assert(plot and plot:GetIndex()==r.plot and target and target:GetID()==r.city
  and target:GetOwner()==r.cityOwner and target:GetX()==r.cityX and target:GetY()==r.cityY,"TARGET_CITY_CHANGED")
 -- No native identity/target getter may sit between this final scope check
 -- and entry. Submit the unit that was just verified, not the earlier handle.
 assert(sameScope(r),"SCOPE_CHANGED_BEFORE_ENTRY")
 return current
end)
if not ready then retireAssignment(r);print("ERR:SPY_ASSIGNMENT_REFUSED|IDENTITY_CHANGED_BEFORE_ENTRY");print("{SENTINEL}");return end
r.entered=true
UnitManager.RequestOperation(current,r.operation,r.params)
r.returned=true
print("OK:SPY_ASSIGNMENT_REQUESTED|"..r.id)
print("{SENTINEL}")
'''


def build_spy_assignment_query(unit_id: int, mission: str, x: int, y: int, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpSpyAssignment
local ok,finish=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.requested=={unit_id}
  and r.mission=={json.dumps(mission)} and r.x=={x} and r.y=={y})
 assert(sameScope(r))
 assert(UI.IsGameCoreBusy()==false)
 local plot=Map.GetPlot(r.x,r.y)
 assert(plot)
 local known=assignmentValue(r,function() return plot:GetIndex() end,scopeIndex,r.plot)
 local city=Cities.GetPlotPurchaseCity(plot)
 assert(city)
 local function check(read,valid,expected)
  if not assignmentValue(r,read,valid,expected) then known=false end
 end
 check(function() return city:GetID() end,scopeIndex,r.city)
 check(function() return city:GetOwner() end,scopePlayer,r.cityOwner)
 check(function() return city:GetX() end,scopeIndex,r.cityX)
 check(function() return city:GetY() end,scopeIndex,r.cityY)
 local unit=UnitManager.GetUnit(r.owner,r.id%65536)
 if unit then
  check(function() return unit:GetID() end,scopeIndex,r.id)
  check(function() return unit:GetOwner() end,scopePlayer,r.owner)
  check(function() return unit:GetType() end,scopeIndex,r.unitType)
  if r.spyIdentity then
   check(function() return unit:GetName() end,function(v) return type(v)=="string" and #v>0 end,r.spyIdentity.name)
   check(function() return unit:GetExperience():GetLevel() end,function(v) return scopeIndex(v) and v>=1 end,r.spyIdentity.level)
  end
 end
 assert(known and not r.invalid)
 if not r.noop then assert(r.entered==true and r.returned==true and r.started==true) end
 if r.mission=="TRAVEL" and not r.noop and unit==nil then
  assert(Players[r.owner]:GetUnits():FindID(r.id)==nil and r.removed==true)
  local remaining=mapSpyRoster(r.owner)
  for id,identity in pairs(remaining) do
   if id==r.id or not sameSpyIdentity(identity,r.mapRoster[id])
    or identity.unitType~=r.mapRoster[id].unitType then r.invalid=true end
  end
  for id,_ in pairs(r.mapRoster) do assert(id==r.id or remaining[id]~=nil) end
  assert(not r.invalid)
  local roster,count=offMapSpyRoster(r.owner,function(key,row)
   local bound=r.offMapIdentity
   if bound and key==bound.key then
    assignmentValue(r,function() return spyName(row.Name) end,function(v) return type(v)=="string" end,bound.row.name)
    assignmentValue(r,function() return spyInteger(row.Level,1) end,scopeIndex,bound.row.level)
    assignmentValue(r,function() return spyInteger(row.XLocation,-9999) end,scopeInteger,bound.row.x)
    assignmentValue(r,function() return spyInteger(row.YLocation,-9999) end,scopeInteger,bound.row.y)
    assignmentValue(r,function() return spyInteger(row.ReturnTurn,-1) end,scopeInteger,bound.row.finish)
   end
  end,function(rows)
   if r.offMapIdentity and rows[r.offMapIdentity.key]==nil then r.invalid=true end
  end)
  assert(not r.invalid)
  assert(count==r.offCount+1)
  for key,old in pairs(r.offRoster) do assert(sameOffMapSpy(roster[key],old)) end
  local found,foundKey=nil,nil
  for key,row in pairs(roster) do
   if r.offRoster[key]==nil then
    assert(found==nil and sameSpyIdentity(row,r.spyIdentity)
     and row.x==r.x and row.y==r.y and row.finish>r.turn)
    found,foundKey=row,key
   end
  end
  assert(found and sameScope(r))
  -- Retain the normalized identity even if this confirmation reply is lost.
  -- Later readable changes cannot be cleared by restoring the old values.
  if not r.offMapIdentity then r.offMapIdentity={{key=foundKey,row=found}} end
  return found.finish
 end
 local spy=spyFor(r.owner,r.id)
 if r.noop then
  assert(spy:GetSpyOperation()==-1 and spy:GetX()==r.x and spy:GetY()==r.y)
  assert(sameScope(r));return 0
 end
 local index=spy:GetSpyOperation()
 local operation=GameInfo.UnitOperations[index]
 assert(index==r.index and operation and operation.Hash==r.operation)
 local ends=integer(spy:GetSpyOperationEndTurn())
 assert(ends>r.turn,"ASSIGNMENT_START_UNCONFIRMED")
 -- Travel can stay at its origin or use the native in-transit coordinates.
 -- The exact request, started event and current operation prove assignment, not arrival.
 if r.mission~="TRAVEL" then assert(spy:GetX()==r.x and spy:GetY()==r.y) end
 assert(sameScope(r));return ends
end)
if ok then
 -- Confirmation is read-only. A lost reply must leave all original observers
 -- and scope invalidation available for a later confirmation-only call.
 print("SPY_ASSIGNMENT_CONFIRMED|{token}|{unit_id}|{mission}|"..r.id.."|"..r.cityOwner..","..r.city.."|{x},{y}|"..finish.."|"..(r.noop and "SPY_ALREADY_AT_DESTINATION" or "SPY_ASSIGNMENT_STARTED"))
else print("SPY_ASSIGNMENT_OBSERVED|{token}|UNKNOWN") end
print("{SENTINEL}")
'''


def parse_spy_assignment(lines: list[str], unit_id: int, mission: str, token: str) -> str | None:
    rows = [line.split('|') for line in lines if line.startswith('SPY_ASSIGNMENT_CONFIRMED|')]
    if len(rows) == 1 and len(rows[0]) == 9:
        row = rows[0]
        if row[1:4] == [token, str(unit_id), mission] and row[4].isdigit() and row[7].isdigit() and row[8] in {"SPY_ASSIGNMENT_STARTED", "SPY_ALREADY_AT_DESTINATION"}:
            suffix = f'|end_turn:{row[7]}' if row[8] == 'SPY_ASSIGNMENT_STARTED' else ''
            return f'{row[8]}|spy:{row[4]}|{mission}|target:{row[6]}|city:{row[5]}' + suffix
    return None
