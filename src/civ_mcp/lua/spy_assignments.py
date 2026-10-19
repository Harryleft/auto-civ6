"""Confirm spy assignment starts independently of their multi-turn results."""
import json
from civ_mcp.lua._helpers import SENTINEL
from civ_mcp.lua.espionage import _SPY_OP_HASHES


OPERATIONS = {name: 'UNITOPERATION_SPY_' + ('TRAVEL_NEW_CITY' if name == 'TRAVEL' else name)
              for name in _SPY_OP_HASHES}
_CONTEXT = '''
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
local function sameScope(r)
 local ok,same=pcall(function()
  return Game.GetLocalPlayer()==r.owner and Game.GetCurrentGameTurn()==r.turn
   and PlayerConfigurations[r.owner]:GetCivilizationTypeName()==r.civ
   and GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")==r.seed
 end)
 if not ok or not same then r.invalid=true end
 return not r.invalid
end
local function retireAssignment(r)
 if r and r.handler then pcall(function() Events.UnitOperationStarted.Remove(r.handler) end) end
end
'''


def build_spy_assignment(unit_id: int, mission: str, x: int, y: int, token: str) -> str:
    return _CONTEXT + f'''
local ok,r,selected=pcall(function()
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
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
 return {{token={json.dumps(token)},requested={unit_id},id=spy:GetID(),owner=owner,mission={json.dumps(mission)},
  x={x},y={y},plot=integer(plot:GetIndex()),city=cityId,cityOwner=cityOwner,cityX=cityX,cityY=cityY,
  operation=op.Hash,index=op.Index,params=params,noop=noop,
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}},spy
end)
if not ok then print("ERR:SPY_ASSIGNMENT_REFUSED|"..tostring(r));print("{SENTINEL}");return end
retireAssignment(__civmcpSpyAssignment)
__civmcpSpyAssignment=r
if r.noop then print("OK:SPY_ASSIGNMENT_NOOP|"..r.id);print("{SENTINEL}");return end
r.handler=function(owner,id,operation)
 if __civmcpSpyAssignment==r and r.entered==true and owner==r.owner and id==r.id
  and operation==r.operation and sameScope(r) then r.started=true end
end
local armed=pcall(function() Events.UnitOperationStarted.Add(r.handler) end)
if not armed then retireAssignment(r);print("ERR:SPY_ASSIGNMENT_REFUSED|OPERATION_OBSERVER_UNAVAILABLE");print("{SENTINEL}");return end
r.entered=true
UnitManager.RequestOperation(selected,r.operation,r.params)
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
 if not r.noop then assert(r.entered==true and r.returned==true and r.started==true) end
 assert(UI.IsGameCoreBusy()==false)
 local spy=spyFor(r.owner,r.id)
 local plot=Map.GetPlot(r.x,r.y)
 assert(plot and plot:GetIndex()==r.plot)
 local city=Cities.GetPlotPurchaseCity(plot)
 assert(city and city:GetID()==r.city and city:GetOwner()==r.cityOwner and city:GetX()==r.cityX and city:GetY()==r.cityY)
 if r.noop then
  assert(spy:GetSpyOperation()==-1 and spy:GetX()==r.x and spy:GetY()==r.y)
  return 0
 end
 local index=spy:GetSpyOperation()
 local operation=GameInfo.UnitOperations[index]
 assert(index==r.index and operation and operation.Hash==r.operation)
 local ends=integer(spy:GetSpyOperationEndTurn())
 assert(ends>r.turn,"ASSIGNMENT_START_UNCONFIRMED")
 -- Travel can stay at its origin or use the native in-transit coordinates.
 -- The exact request, started event and current operation prove assignment, not arrival.
 if r.mission~="TRAVEL" then assert(spy:GetX()==r.x and spy:GetY()==r.y) end
 return ends
end)
if ok then
 retireAssignment(r)
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
