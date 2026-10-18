"""One native interpretation for local-turn observations and final submission."""

from civ_mcp.lua._helpers import SENTINEL


LUA_TURN_READINESS = r'''
local function localTurnReadiness(me,inspectUnit)
 local p=Players[me]
 local r={details={},queues={},queued_move_current=false}
 local function read(fn,kind)
  local ok,value=pcall(fn)
  if ok and type(value)==kind then return value end
 end
 local function both(a,b)
  if a==false or b==false then return false end
  if a==true and b==true then return true end
 end
 local function text(fn)
  local ok,value=pcall(fn)
  if not ok or value==nil then return "unknown" end
  return tostring(value):gsub("[|\r\n]","/")
 end
 local function unitDetail(u)
  return "unit_id="..text(function() return u:GetID() end)
   .." at="..text(function() return u:GetX() end)..","..text(function() return u:GetY() end)
   .." moves="..text(function() return u:GetMovesRemaining() end)
   .." ready="..text(function() return u:IsReadyToMove() end)
   .." activity="..text(function() return UnitManager.GetActivityType(u) end)
 end
 r.auto_end_turn=read(function() return UserConfiguration.IsAutoEndTurn() end,"boolean")
 r.city_range_turn_blocking=read(function() return UserConfiguration.IsCityRangeAttackTurnBlocking() end,"boolean")
 r.allow_tactical_commands=read(function() return Game.IsAllowTacticalCommands(me) end,"boolean")
 r.core_busy=read(function() return UI.IsGameCoreBusy() end,"boolean")
 local firstOK,first=pcall(function() return p:GetUnits():GetFirstReadyUnit() end)
 if firstOK and first==nil then r.first_ready_unit_present=false
 elseif firstOK and (type(first)=="table" or type(first)=="userdata") then
  r.first_ready_unit_present=true; table.insert(r.details,"first_ready "..unitDetail(first))
 end
 r.units_have_moves=both(both(r.auto_end_turn,r.allow_tactical_commands),r.first_ready_unit_present)
 local rangeOK,notice=pcall(function() return NotificationManager.FindType(NotificationTypes.CITY_RANGE_ATTACK,me) end)
 if rangeOK and notice==nil then r.city_range_notification=false
 elseif rangeOK then
  local dismissed=read(function() return notice:IsDismissed() end,"boolean")
  if dismissed~=nil then r.city_range_notification=not dismissed end
 end
 r.city_range_attack=both(both(r.city_range_turn_blocking,r.allow_tactical_commands),r.city_range_notification)
 if r.city_range_attack then table.insert(r.details,"city_range_attack: choose an attack or explicitly acknowledge the native city-range notification") end
 local blocker=read(function() return NotificationManager.GetFirstEndTurnBlocking(me) end,"number")
 if blocker~=nil and EndTurnBlockingTypes and type(EndTurnBlockingTypes.NO_ENDTURN_BLOCKING)=="number" then
  r.native_blocker=blocker~=EndTurnBlockingTypes.NO_ENDTURN_BLOCKING
  if r.native_blocker then
   local name="unknown("..blocker..")"
   for key,value in pairs(EndTurnBlockingTypes) do if value==blocker then name=key end end
   table.insert(r.details,"native_blocker="..name)
  end
 end
 -- A queued destination is the same movement witness used by WorldInput.
 -- ACTIVITY_OPERATION alone also describes traders and is never a blocker.
 local routes,routeKnown
 local function activeRoute(id)
  if routes==nil then
   routes={}
   routeKnown=pcall(function()
    for _,city in p:GetCities():Members() do
     local outgoing=city:GetTrade():GetOutgoingRoutes()
     if outgoing~=nil and type(outgoing)~="table" then error("route records unknown") end
     for _,route in ipairs(outgoing or {}) do
      if type(route.TraderUnitID)~="number" then error("route unit unknown") end
      routes[route.TraderUnitID]=true
     end
    end
   end)
  end
  if routeKnown then return routes[id]==true end
 end
 local function denseLength(values)
  if type(values)~="table" then return nil end
  local count=0
  for index in pairs(values) do
   if type(index)~="number" or index<1 or index%1~=0 then return nil end
   count=count+1
  end
  for index=1,count do if values[index]==nil then return nil end end
  return count
 end
 local function nextTurn(u,target,goal)
  if r.core_busy~=false then return nil end
  local path=UnitManager.GetMoveToPathEx(u,target)
  if type(path)~="table" then return nil end
  local count=denseLength(path.plots)
  if not count or count<2 or denseLength(path.turns)~=count then return nil end
  for index=1,count do
   local plotID,turn=path.plots[index],path.turns[index]
   if type(plotID)~="number" or plotID<0 or plotID%1~=0 or type(turn)~="number" or turn<1 or turn%1~=0 then return nil end
   if index>1 and turn<path.turns[index-1] then return nil end
   local plot=Map.GetPlotByIndex(plotID)
   if not plot then return nil end
   if index==1 and (plot:GetX()~=u:GetX() or plot:GetY()~=u:GetY()) then return nil end
   if index==count and (plot:GetX()~=goal:GetX() or plot:GetY()~=goal:GetY()) then return nil end
   if index==2 and plot:GetX()==u:GetX() and plot:GetY()==u:GetY() then return nil end
  end
  if path.turns[1]~=1 then return nil end
  return path.turns[2]
 end
 local queueUnknown,queueIdentityUnknown=false,false
 local function queuedUnit(u)
  local x,y=u:GetX(),u:GetY()
  if x==-9999 or y==-9999 then return end
  if type(x)~="number" or type(y)~="number" or x<0 or y<0 or x%1~=0 or y%1~=0 then error("unit position unknown") end
  local moves=u:GetMovesRemaining()
  if type(moves)~="number" or moves~=moves or moves<0 then error("unit moves unknown") end
  if moves<=0 and not inspectUnit then return end
  local target=UnitManager.GetQueuedDestination(u)
  if target==nil then return end
  if type(target)~="number" or target<0 or target%1~=0 then error("queue target unknown") end
  local goal=Map.GetPlotByIndex(target)
  if not goal then error("queue plot unknown") end
  local goalX,goalY=goal:GetX(),goal:GetY()
  if type(goalX)~="number" or type(goalY)~="number" or goalX<0 or goalY<0 or goalX%1~=0 or goalY%1~=0 then error("queue position unknown") end
  if goalX==x and goalY==y then return end
  local queue={id=u:GetID(),x=x,y=y,target=target,goalX=goalX,goalY=goalY,valid=false}
  r.queues[u:GetID()]=queue
  local route=activeRoute(u:GetID())
  queue.active_route=route
  local detail="queued_move "..unitDetail(u).." goal="..goal:GetX()..","..goal:GetY()
  if route==true then table.insert(r.details,detail.." active_route=true (excluded)"); return end
  if route==nil then queueUnknown=true; queueIdentityUnknown=true; table.insert(r.details,detail.." active_route=unknown"); return end
  -- A stale intent can still be cancelled when its path is unavailable.
  -- Turn submission continues to require known path timing.
  local pathOK,step=pcall(nextTurn,u,target,goal)
  if not pathOK then step=nil end
  if step==nil then queueUnknown=true; table.insert(r.details,detail.." next_turn=unknown"); return end
  table.insert(r.details,detail.." next_turn="..step.." active_route=false")
  queue.valid=true; queue.next_turn=step
  if step==1 then r.queued_move_current=true end
 end
 local scanOK=pcall(function()
  local function scan(u)
   local ok=pcall(queuedUnit,u)
   if not ok then queueUnknown=true; queueIdentityUnknown=true; table.insert(r.details,"queued_move "..unitDetail(u).." target_or_path=unknown") end
  end
  if inspectUnit then scan(inspectUnit)
  else for _,u in p:GetUnits():Members() do scan(u) end
  end
 end)
 if not scanOK then queueUnknown=true; queueIdentityUnknown=true end
 if queueUnknown and r.queued_move_current~=true then r.queued_move_current=nil end
 r.queue_known=not queueUnknown
 r.queue_identity_known=not queueIdentityUnknown
 if r.native_blocker then r.reason="BLOCKED"
 elseif r.queued_move_current then r.reason="QUEUED_MOVE_CURRENT"
 elseif r.units_have_moves then r.reason="UNITS_HAVE_MOVES"
 elseif r.city_range_attack then r.reason="CITY_RANGE_ATTACK"
 elseif r.native_blocker==nil or r.queued_move_current==nil or r.units_have_moves==nil or r.city_range_attack==nil then r.reason="READINESS_UNKNOWN"
 else r.reason="NONE" end
 return r
end
'''


def build_turn_readiness_query() -> str:
    return LUA_TURN_READINESS + '''
local r=localTurnReadiness(Game.GetLocalPlayer())
local fields={}
for _,key in ipairs({"auto_end_turn","city_range_turn_blocking","allow_tactical_commands","core_busy","first_ready_unit_present","city_range_notification","native_blocker","units_have_moves","city_range_attack","queued_move_current"}) do
 local value=r[key]
 table.insert(fields,key.."="..(value==nil and "?" or value and "1" or "0"))
end
print("TURN_LOCAL|"..Game.GetCurrentGameTurn().."|"..r.reason.."|"..table.concat(fields,"|"))
for _,detail in ipairs(r.details) do print("TURN_LOCAL_DETAIL|"..detail) end
print("TURN_LOCAL_DONE")
print("{SENTINEL}")
'''.replace("{SENTINEL}", SENTINEL)
