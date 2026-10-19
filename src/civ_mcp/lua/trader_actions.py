"""Economic trader operations, bound to full unit and both native city identities."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


_CONTEXT = _LUA_ACTION_SCOPE + '''
local function integer(value)
 assert(type(value)=="number" and value>=0 and value%1==0,"IDENTITY_OR_COUNT_UNAVAILABLE")
 return value
end
local function traderFor(owner,id)
 local u=UnitManager.GetUnit(owner,id%65536)
 assert(u and u:GetID()==id and u:GetOwner()==owner,"TRADER_ID_CHANGED")
 local row=GameInfo.Units[u:GetType()]
 assert(row and row.MakeTradeRoute==true,"NOT_A_TRADER")
 return u
end
local function cityAt(x,y)
 local c=CityManager.GetCityAt(x,y)
 assert(c and c:GetX()==x and c:GetY()==y,"CITY_UNAVAILABLE")
 return {id=integer(c:GetID()),owner=integer(c:GetOwner()),x=x,y=y}
end
local function sameCity(c)
 local now=cityAt(c.x,c.y)
 return now.id==c.id and now.owner==c.owner
end
local function routesFor(owner)
 local result={}
 for _,city in Players[owner]:GetCities():Members() do
  local routes=city:GetTrade():GetOutgoingRoutes()
  assert(type(routes)=="table","ROUTES_UNAVAILABLE")
  for _,route in ipairs(routes) do
   integer(route.TraderUnitID);integer(route.OriginCityPlayer);integer(route.OriginCityID)
   integer(route.DestinationCityPlayer);integer(route.DestinationCityID)
   assert(route.OriginCityPlayer==owner and route.OriginCityID==city:GetID(),"ROUTE_SOURCE_UNAVAILABLE")
   table.insert(result,route)
  end
 end
 return result,integer(Players[owner]:GetTrade():GetOutgoingRouteCapacity())
end
'''


def build_trader_action(kind: str, unit_id: int, x: int, y: int, token: str) -> str:
    return _CONTEXT + f'''
local token,kind={json.dumps(token)},{json.dumps(kind)}
local ok,r=pcall(function()
 assert(__civmcpTraderAction==nil or type(__civmcpTraderAction)=="table","RECEIPT_STORAGE_UNAVAILABLE")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local unit=traderFor(owner,{unit_id})
 local origin,destination=cityAt(unit:GetX(),unit:GetY()),cityAt({x},{y})
 assert(origin.owner==owner,"ORIGIN_NOT_OWNED")
 assert(origin.owner~=destination.owner or origin.id~=destination.id,"ALREADY_AT_DESTINATION")
 local routes,capacity=routesFor(owner)
 for _,route in ipairs(routes) do assert(route.TraderUnitID~={unit_id},"TRADER_ALREADY_HAS_ROUTE") end
 local params,operation={{}},nil
 if kind=="trade_route" then
  assert(unit:GetMovesRemaining()>0,"NO_MOVES")
  assert(#routes<capacity,"CAPACITY_FULL")
  operation=UnitOperationTypes.MAKE_TRADE_ROUTE
  params[UnitOperationTypes.PARAM_X0],params[UnitOperationTypes.PARAM_Y0]={x},{y}
  params[UnitOperationTypes.PARAM_X1],params[UnitOperationTypes.PARAM_Y1]=origin.x,origin.y
 else
  assert(destination.owner==owner,"DESTINATION_NOT_OWNED")
  operation=GameInfo.UnitOperations["UNITOPERATION_TELEPORT_TO_CITY"].Hash
  params[UnitOperationTypes.PARAM_X],params[UnitOperationTypes.PARAM_Y]={x},{y}
 end
 assert(operation~=nil and type(UnitManager.RequestOperation)=="function","OPERATION_UNAVAILABLE")
 -- TradeRouteChooser and TradeOriginChooser both use these four arguments.
 assert(UnitManager.CanStartOperation(unit,operation,nil,params)==true,"CANNOT_START_OPERATION")
 local chooserName=kind=="trade_route" and "MAKE_TRADE_ROUTE" or "TELEPORT_TO_CITY"
 local chooser=InterfaceModeTypes and InterfaceModeTypes[chooserName]
 local selection=InterfaceModeTypes and InterfaceModeTypes.SELECTION
 assert(scopeInteger(chooser) and scopeInteger(selection) and chooser~=selection,"INTERFACE_MODE_UNAVAILABLE")
 assert(type(UI.GetInterfaceMode)=="function" and type(UI.SetInterfaceMode)=="function","INTERFACE_API_UNAVAILABLE")
 assert(scopeInteger(UI.GetInterfaceMode()),"INTERFACE_MODE_UNKNOWN")
 return {{token=token,kind=kind,owner=owner,unit={unit_id},unitType=unit:GetType(),chooser=chooser,selection=selection,
  origin=origin,destination=destination,active=#routes,capacity=capacity,params=params,operation=operation,
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
end)
if not ok then print("ERR:TRADER_REFUSED|"..tostring(r));print("{SENTINEL}");return end
if not (__civmcpTraderAction and __civmcpTraderAction.token==token) then
 __civmcpTraderAction=r
 r.entered=true
 UnitManager.RequestOperation(traderFor(r.owner,r.unit),r.operation,r.params)
 r.returned=true
 -- Both shipped trader choosers return to SELECTION after RequestOperation.
 -- A native event may have opened a different mode; preserve it.
 r.cleanupOK,r.cleanupError=pcall(function()
  assert(sameScope(r),"SCOPE_CHANGED")
  local mode=UI.GetInterfaceMode()
  assert(scopeInteger(mode),"INTERFACE_MODE_UNKNOWN")
  if mode==r.chooser then
   UI.SetInterfaceMode(r.selection)
   assert(UI.GetInterfaceMode()==r.selection,"INTERFACE_TRANSITION_UNCONFIRMED")
  end
 end)
end
print("OK:TRADER_REQUESTED|"..kind.."|unit:{unit_id}")
print("{SENTINEL}")
'''


def build_trader_action_query(kind: str, unit_id: int, x: int, y: int, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpTraderAction
local reason="RECEIPT_MISSING_OR_MISMATCHED"
local ok,active,capacity=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.kind=={json.dumps(kind)} and r.unit=={unit_id}
  and r.destination.x=={x} and r.destination.y=={y})
 reason="SCOPE_CHANGED";assert(sameScope(r))
 reason="REQUEST_NOT_RETURNED";assert(r.entered==true and r.returned==true)
 reason="CORE_BUSY_OR_UNKNOWN";assert(UI.IsGameCoreBusy()==false)
 reason="TRADER_CHANGED";local unit=traderFor(r.owner,r.unit);assert(unit:GetType()==r.unitType)
 reason="CITY_CHANGED";assert(sameCity(r.origin) and sameCity(r.destination))
 reason="ROUTE_UNCONFIRMED"
 local routes,capacity=routesFor(r.owner)
 local matches=0
 for _,route in ipairs(routes) do
  if route.TraderUnitID==r.unit then
   assert(r.kind=="trade_route" and route.OriginCityPlayer==r.origin.owner and route.OriginCityID==r.origin.id
    and route.DestinationCityPlayer==r.destination.owner and route.DestinationCityID==r.destination.id)
   matches=matches+1
  end
 end
 if r.kind=="trade_route" then
  assert(matches==1 and #routes==r.active+1 and #routes<=capacity)
 else
  reason="TRANSFER_UNCONFIRMED"
  assert(matches==0 and #routes==r.active and unit:GetX()==r.destination.x and unit:GetY()==r.destination.y)
 end
 -- Route/relocation evidence is established before checking UI readiness.
 -- Confirmation only reads: it never repeats the economic operation or cleanup.
 reason=(r.kind=="trade_route" and "ROUTE_ESTABLISHED" or "TRANSFER_ESTABLISHED").."_INTERFACE_CLEANUP_UNCONFIRMED"
 local mode=UI.GetInterfaceMode()
 assert(scopeInteger(mode) and mode~=r.chooser)
 return #routes,capacity
end)
if ok then
 print("TRADER_CONFIRMED|"..r.token.."|"..r.kind.."|"..r.unit.."|"..r.origin.owner..":"..r.origin.id
  .."->"..r.destination.owner..":"..r.destination.id.."|at:{x},{y}|routes:"..active.."/"..capacity)
else print("TRADER_OBSERVED|{token}|"..reason) end
print("{SENTINEL}")
'''


def parse_trader_confirmation(lines: list[str], token: str, kind: str, unit_id: int) -> str | None:
    rows=[row.split("|") for row in lines if row.startswith("TRADER_CONFIRMED|")]
    if len(rows)==1 and len(rows[0])==7:
        row=rows[0]
        if row[1:4]==[token,kind,str(unit_id)]:
            if kind=="trade_route":
                return f"TRADE_ROUTE_STARTED|unit:{unit_id}|{row[4]}|{row[6]}|Route established; future yields not yet earned."
            return f"TELEPORTED|unit:{unit_id}|{row[4]}|{row[5]}|Trader relocated; no trade route started."
    return None
