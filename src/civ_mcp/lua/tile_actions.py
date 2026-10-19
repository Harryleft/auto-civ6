"""Bound tile effects and the native unit/charge facts that produced them."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


_OPERATIONS = {'improve': ('BUILD_IMPROVEMENT', 'IMPROVED'), 'repair': ('REPAIR', 'REPAIRED'),
    'remove_feature': ('REMOVE_FEATURE', 'REMOVED_FEATURE'),
    'remove_improvement': ('REMOVE_IMPROVEMENT', 'REMOVED_IMPROVEMENT'),
    'build_route': ('BUILD_ROUTE', 'ROUTE_BUILT')}
_CONTEXT = _LUA_ACTION_SCOPE + '''
local function integer(v)
 assert(type(v)=="number" and v>=0 and v<math.huge and v==math.floor(v),"NUMBER_UNAVAILABLE")
 return v
end
local function unitFor(owner,id)
 local unit=UnitManager.GetUnit(owner,id%65536)
 assert(unit and unit:GetOwner()==owner,"UNIT_ID_CHANGED")
 local fullId=integer(unit:GetID())
 assert(id<65536 or fullId==id,"UNIT_ID_CHANGED")
 return unit
end
local function routeCosts(owner,unit)
 local info=GameInfo.Units[unit:GetType()]
 assert(info,"UNIT_TYPE_UNAVAILABLE")
 local valid,costs={},{}
 for row in GameInfo.Route_ValidBuildUnits() do
  if row.UnitType==info.UnitType then valid[row.RouteType]=true end
 end
 -- The operation chooses the route. Capture every supported native cost now,
 -- then verify the route actually produced, without inventing a best-route API.
 for row in GameInfo.Routes_XP2() do
  if valid[row.RouteType] then
   local route=GameInfo.Routes[row.RouteType]
   assert(route and type(route.Index)=="number","ROUTE_UNAVAILABLE")
   costs[route.Index]={cost=integer(row.BuildWithUnitChargeCost),resources={},name=row.RouteType}
  end
 end
 assert(next(costs)~=nil,"ROUTE_COST_UNAVAILABLE")
 for row in GameInfo.Route_ResourceCosts() do
  local route=GameInfo.Routes[row.RouteType]
  local record=route and costs[route.Index]
  if record then
   local resource=GameInfo.Resources[row.ResourceType]
   assert(resource and type(resource.Index)=="number","RESOURCE_UNAVAILABLE")
   record.resources[resource.Index]={cost=integer(row.BuildWithUnitCost),
    before=integer(Players[owner]:GetResources():GetResourceAmount(resource.Index))}
  end
 end
 return costs
end
local function retireTile(r)
 for name,handler in pairs(r and r.handlers or {}) do pcall(function() Events[name].Remove(handler) end) end
end
'''


def build_tile_action(unit_id: int, kind: str, improvement: str, token: str) -> str:
    operation, label = _OPERATIONS[kind]
    return _CONTEXT + f'''
local ok,r=pcall(function()
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local unit=unitFor(owner,{unit_id})
 local x,y=integer(unit:GetX()),integer(unit:GetY())
 local plot=Map.GetPlot(x,y)
 assert(plot and plot:GetX()==x and plot:GetY()==y,"PLOT_UNAVAILABLE")
 local charges=integer(unit:GetBuildCharges())
 local op=GameInfo.UnitOperations["UNITOPERATION_{operation}"]
 assert(op and type(op.Hash)=="number" and type(UnitManager.RequestOperation)=="function","OPERATION_UNAVAILABLE")
 local params={{[UnitOperationTypes.PARAM_X]=x,[UnitOperationTypes.PARAM_Y]=y}}
 local target,cost,routes,noop=nil,nil,nil,false
 local before=plot:GetImprovementType()
 if "{kind}"=="improve" then
  target=GameInfo.Improvements[{json.dumps(improvement)}]
  assert(target and type(target.Index)=="number" and type(target.Hash)=="number","IMPROVEMENT_NOT_FOUND")
  assert(plot:IsImprovementPillaged()==false,"PILLAGED_IMPROVEMENT_USE_REPAIR")
  noop=plot:GetImprovementType()==target.Index
 elseif "{kind}"=="repair" then
  target=GameInfo.Improvements[plot:GetImprovementType()]
  assert(target and type(target.Hash)=="number","IMPROVEMENT_NOT_FOUND")
  local pillaged=plot:IsImprovementPillaged()
  assert(type(pillaged)=="boolean","PILLAGE_STATE_UNKNOWN")
  noop=not pillaged
 elseif "{kind}"=="build_route" then
  before=plot:GetRouteType()
  assert(type(before)=="number" and before>=-1 and before==math.floor(before),"ROUTE_UNAVAILABLE")
  routes=routeCosts(owner,unit)
  target={{Index=-1}}
 elseif "{kind}"=="remove_feature" then
  before=plot:GetFeatureType()
  assert(type(before)=="number" and before>=-1 and before==math.floor(before),"FEATURE_UNKNOWN")
  noop=before==-1
  target={{Index=-1}}
 else
  before=plot:GetImprovementType()
  assert(type(before)=="number" and before>=-1 and before==math.floor(before),"IMPROVEMENT_UNKNOWN")
  noop=before==-1
  target={{Index=-1}}
 end
 if target.Hash then params[UnitOperationTypes.PARAM_IMPROVEMENT_TYPE]=target.Hash end
 if not noop then
  local allowed=UnitManager.CanStartOperation(unit,op.Hash,nil,params,true)
  if allowed~=true then
   local hint=""
   if "{kind}"=="improve" then
    local alternatives={{}}
    for candidate in GameInfo.Improvements() do
     if candidate.Buildable==true then
      local candidateParams={{[UnitOperationTypes.PARAM_X]=x,[UnitOperationTypes.PARAM_Y]=y,
       [UnitOperationTypes.PARAM_IMPROVEMENT_TYPE]=candidate.Hash}}
      local known,canBuild=pcall(UnitManager.CanStartOperation,unit,op.Hash,nil,candidateParams,true)
      if known and canBuild==true then table.insert(alternatives,candidate.ImprovementType) end
     end
    end
    if #alternatives>0 then hint="|can build here: "..table.concat(alternatives,", ") end
   end
   error("CANNOT_{operation}"..hint)
  end
 end
 if "{kind}"=="improve" and not noop then
  local info=GameInfo.Units[unit:GetType()]
  assert(info,"UNIT_TYPE_UNAVAILABLE")
  for row in GameInfo.Improvement_ValidBuildUnits() do
   if row.ImprovementType==target.ImprovementType and row.UnitType==info.UnitType then
    assert(type(row.ConsumesCharge)=="boolean" and row.ValidRepairOnly==false,"BUILD_COST_UNAVAILABLE")
    cost=row.ConsumesCharge and 1 or 0
   end
  end
  assert(cost~=nil and charges>=cost,"BUILD_COST_UNAVAILABLE_OR_INSUFFICIENT_CHARGES")
 end
 if noop then cost=0 end
 for _,name in ipairs({{"UnitOperationStarted","UnitOperationAdded","UnitOperationDeactivated","UnitChargesChanged","UnitRemovedFromMap"}}) do
  assert(Events[name] and type(Events[name].Add)=="function" and type(Events[name].Remove)=="function","OPERATION_OBSERVER_UNAVAILABLE")
 end
 return {{token={json.dumps(token)},requested={unit_id},kind={json.dumps(kind)},improvement={json.dumps(improvement)},label={json.dumps(label)},
  owner=owner,id=unit:GetID(),noop=noop,x=x,y=y,plot=integer(plot:GetIndex()),plotOwner=plot:GetOwner(),
  before=before,target=target.Index,charges=charges,cost=cost,routes=routes,op=op.Hash,params=params,handlers={{}},
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
end)
if not ok then print("ERR:TILE_ACTION_REFUSED|"..tostring(r));print("{SENTINEL}");return end
retireTile(__civmcpTileAction)
__civmcpTileAction=r
if r.noop then print("OK:TILE_ACTION_NOOP|"..r.id);print("{SENTINEL}");return end
local function matches(owner,id)
 return __civmcpTileAction==r and r.entered==true and owner==r.owner and id==r.id and sameScope(r)
end
r.handlers.UnitOperationStarted=function(owner,id,op) if matches(owner,id) and op==r.op then r.started=true end end
r.handlers.UnitOperationAdded=r.handlers.UnitOperationStarted
r.handlers.UnitOperationDeactivated=function(owner,id,op) if matches(owner,id) and op==r.op then r.ended=true end end
r.handlers.UnitChargesChanged=function(owner,id,after,before)
 if matches(owner,id) then r.chargeBefore=before;r.chargeAfter=after end
end
r.handlers.UnitRemovedFromMap=function(owner,id) if matches(owner,id) then r.removed=true end end
local armed=pcall(function() for name,handler in pairs(r.handlers) do Events[name].Add(handler) end end)
if not armed then retireTile(r);print("ERR:TILE_ACTION_REFUSED|OPERATION_OBSERVER_UNAVAILABLE");print("{SENTINEL}");return end
r.entered=true
UnitManager.RequestOperation(unitFor(r.owner,r.id),r.op,r.params)
r.returned=true
print("OK:TILE_ACTION_REQUESTED|"..r.id)
print("{SENTINEL}")
'''


def build_tile_action_query(unit_id: int, kind: str, improvement: str, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpTileAction
local ok,after=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.requested=={unit_id}
  and r.kind=={json.dumps(kind)} and r.improvement=={json.dumps(improvement)})
 assert(sameScope(r))
 if not r.noop then assert(r.entered==true and r.returned==true and r.started==true) end
 assert(UI.IsGameCoreBusy()==false)
 local plot=Map.GetPlot(r.x,r.y)
 assert(plot and plot:GetIndex()==r.plot and plot:GetOwner()==r.plotOwner)
 local cost=r.cost
 if r.kind=="build_route" then
  local actual=integer(plot:GetRouteType())
  local route=r.routes[actual]
  assert(actual~=r.before and route,"ROUTE_EFFECT_UNCONFIRMED")
  cost=route.cost
  for resource,expected in pairs(route.resources) do
   assert(integer(Players[r.owner]:GetResources():GetResourceAmount(resource))==expected.before-expected.cost)
  end
 elseif r.kind=="remove_feature" then assert(plot:GetFeatureType()==-1)
 elseif r.kind=="remove_improvement" then assert(plot:GetImprovementType()==-1)
 else assert(plot:GetImprovementType()==r.target and plot:IsImprovementPillaged()==false) end
 local unit=UnitManager.GetUnit(r.owner,r.id%65536)
 if unit and unit:GetID()==r.id then
  assert(unit:GetOwner()==r.owner and unit:GetX()==r.x and unit:GetY()==r.y)
  local remaining=integer(unit:GetBuildCharges())
  if cost~=nil then assert(remaining==r.charges-cost)
  else
   -- Operations without a published charge cost need completion plus actual
   -- charge facts; zero-cost repair is not inferred from an unchanged plot.
   assert(r.ended==true and remaining<=r.charges)
   if remaining<r.charges then assert(r.chargeBefore==r.charges and r.chargeAfter==remaining) end
  end
  return remaining
 end
 assert(r.removed==true and r.charges>0 and (cost==nil or cost==r.charges))
 assert(r.chargeBefore==r.charges and r.chargeAfter==0)
 return 0
end)
if ok then
 retireTile(r)
 print("TILE_ACTION_CONFIRMED|{token}|{unit_id}|{kind}|"..r.id.."|"..(r.noop and "ALREADY_"..r.label or r.label).."|"..r.x..","..r.y.."|charges:"..r.charges.."->"..after)
else print("TILE_ACTION_OBSERVED|{token}|UNKNOWN") end
print("{SENTINEL}")
'''


def parse_tile_action(lines: list[str], unit_id: int, kind: str, token: str) -> str | None:
    rows = [line.split('|') for line in lines if line.startswith('TILE_ACTION_CONFIRMED|')]
    if len(rows) == 1 and len(rows[0]) == 8:
        row = rows[0]
        if row[1:4] == [token, str(unit_id), kind] and row[4].isdigit() and row[5] in {_OPERATIONS[kind][1], 'ALREADY_' + _OPERATIONS[kind][1]}:
            return f'{row[5]}|unit:{row[4]}|at:{row[6]}|{row[7]}'
    return None
