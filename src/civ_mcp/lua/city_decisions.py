"""A city decision owns one native pending city, never the next queue item."""
import json
from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


DIRECTIVES = {'keep': 'KEEP', 'reject': 'REJECT', 'raze': 'RAZE',
              'liberate_founder': 'LIBERATE_FOUNDER', 'liberate_previous': 'LIBERATE_PREVIOUS_OWNER'}
_CONTEXT = _LUA_ACTION_SCOPE + '''
local function integer(v)
 assert(type(v)=="number" and v>=0 and v<math.huge and v==math.floor(v),"NUMBER_UNAVAILABLE")
 return v
end
local function pendingCity(owner,source)
 local cities=Players[owner]:GetCities()
 if source=="captured" then return cities:GetNextCapturedCity() end
 return cities:GetNextRebelledCity()
end
local function retireDecision(r)
 for name,handler in pairs(r and r.handlers or {}) do pcall(function() Events[name].Remove(handler) end) end
end
'''


def build_city_decision(city_id: int, action: str, token: str) -> str:
    return _CONTEXT + f'''
local ok,r,selected=pcall(function()
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local city,source=nil,nil
 for _,candidateSource in ipairs({{"rebelled","captured"}}) do
  local candidate=pendingCity(owner,candidateSource)
  if candidate and candidate:GetID()=={city_id} then
   assert(city==nil,"AMBIGUOUS_PENDING_SOURCE")
   city,source=candidate,candidateSource
  end
 end
 assert(city and city:GetOwner()==owner,"PENDING_CITY_ID_MISMATCH")
 assert("{action}"=="keep" or ("{action}"=="reject" and source=="rebelled")
  or ("{action}"~="reject" and source=="captured"),"WRONG_PENDING_SOURCE")
 local owned=Players[owner]:GetCities():FindID({city_id})
 assert(owned and owned:GetID()=={city_id} and owned:GetOwner()==owner,"OWNED_CITY_ID_MISMATCH")
 local x,y=integer(city:GetX()),integer(city:GetY())
 local plot=Map.GetPlot(x,y)
 local at=CityManager.GetCityAt(x,y)
 assert(plot and at and at:GetID()==city:GetID() and at:GetOwner()==owner,"CITY_PLOT_MISMATCH")
 local expectedOwner,founder=nil,nil
 if "{action}"~="keep" and "{action}"~="raze" then
  founder=integer(city:GetOriginalOwner())
  if "{action}"=="liberate_founder" then expectedOwner=founder
  elseif "{action}"=="liberate_previous" then expectedOwner=integer(city:GetOwnerBeforeOccupation())
  else expectedOwner=integer(PlayerManager.GetFreeCitiesPlayerID()) end
  assert(expectedOwner~=owner and Players[expectedOwner],"DESTINATION_OWNER_UNAVAILABLE")
  assert(Events.CityTransfered and type(Events.CityTransfered.Add)=="function"
   and type(Events.CityTransfered.Remove)=="function","TRANSFER_OBSERVER_UNAVAILABLE")
 end
 local command=CityCommandTypes.DESTROY
 local directive=CityDestroyDirectives.{DIRECTIVES[action]}
 assert(type(command)=="number" and type(directive)=="number" and type(CityManager.RequestCommand)=="function","COMMAND_UNAVAILABLE")
 local params={{[UnitOperationTypes.PARAM_FLAGS]=directive}}
 assert(CityManager.CanStartCommand(city,command,params)==true,"CANNOT_{action.upper()}")
 assert(Events.CityCommandStarted and type(Events.CityCommandStarted.Add)=="function"
  and type(Events.CityCommandStarted.Remove)=="function","COMMAND_OBSERVER_UNAVAILABLE")
 if "{action}"=="raze" then
  assert(Events.CityRemovedFromMap and type(Events.CityRemovedFromMap.Add)=="function"
   and type(Events.CityRemovedFromMap.Remove)=="function","REMOVAL_OBSERVER_UNAVAILABLE")
 end
 return {{token={json.dumps(token)},action={json.dumps(action)},id={city_id},owner=owner,source=source,
  x=x,y=y,plot=integer(plot:GetIndex()),command=command,params=params,handlers={{}},
  expectedOwner=expectedOwner,founder=founder,transferredIds={{}},
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}},city
end)
if not ok then print("ERR:CITY_DECISION_REFUSED|"..tostring(r));print("{SENTINEL}");return end
retireDecision(__civmcpCityDecision)
__civmcpCityDecision=r
r.handlers.CityCommandStarted=function(owner,id,districtOwner,districtId,command)
 if __civmcpCityDecision==r and r.entered==true and owner==r.owner and id==r.id
  and command==r.command and sameScope(r) then r.started=true end
end
if r.action=="raze" then
 r.handlers.CityRemovedFromMap=function(owner,id)
  if __civmcpCityDecision==r and r.entered==true and owner==r.owner and id==r.id and sameScope(r) then r.removed=true end
 end
end
if r.expectedOwner~=nil then
 -- Shipped TutorialLoader_Expansion1 uses these as the new city owner and ID.
 -- Bind them to the original command; confirm the corresponding city at its plot.
 r.handlers.CityTransfered=function(owner,id)
  if __civmcpCityDecision==r and r.entered==true and owner==r.expectedOwner and sameScope(r) then
   if type(id)=="number" then r.transferredIds[id]=true end
  end
 end
end
local armed=pcall(function() for name,handler in pairs(r.handlers) do Events[name].Add(handler) end end)
if not armed then retireDecision(r);print("ERR:CITY_DECISION_REFUSED|COMMAND_OBSERVER_UNAVAILABLE");print("{SENTINEL}");return end
r.entered=true
CityManager.RequestCommand(selected,r.command,r.params)
r.returned=true
print("OK:CITY_DECISION_REQUESTED|"..r.id)
print("{SENTINEL}")
'''


def build_city_decision_query(city_id: int, action: str, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpCityDecision
local ok=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.id=={city_id} and r.action=={json.dumps(action)})
 assert(sameScope(r) and r.entered==true and r.returned==true and r.started==true)
 assert(UI.IsGameCoreBusy()==false)
 local waiting=pendingCity(r.owner,r.source)
 assert(not waiting or waiting:GetID()~=r.id,"ORIGINAL_CITY_STILL_PENDING")
 local plot=Map.GetPlot(r.x,r.y)
 assert(plot and plot:GetIndex()==r.plot)
 local city=CityManager.GetCityAt(r.x,r.y)
 if r.action=="keep" then
  assert(city and city:GetID()==r.id and city:GetOwner()==r.owner and city:GetX()==r.x and city:GetY()==r.y)
 elseif r.action=="raze" then
  assert(r.removed==true and city==nil and Players[r.owner]:GetCities():FindID(r.id)==nil)
 else
  assert(city and r.transferredIds[city:GetID()]==true and city:GetOwner()==r.expectedOwner)
  assert(city:GetX()==r.x and city:GetY()==r.y and city:GetOriginalOwner()==r.founder)
  assert(Players[r.owner]:GetCities():FindID(r.id)==nil)
 end
end)
if ok then
 retireDecision(r)
 print("CITY_DECISION_CONFIRMED|{token}|{city_id}|{action}|"..r.source)
else print("CITY_DECISION_OBSERVED|{token}|UNKNOWN") end
print("{SENTINEL}")
'''


def parse_city_decision(lines: list[str], city_id: int, action: str, token: str) -> str | None:
    rows = [line.split('|') for line in lines if line.startswith('CITY_DECISION_CONFIRMED|')]
    if len(rows) == 1 and len(rows[0]) == 5:
        row = rows[0]
        if row[1:4] == [token, str(city_id), action] and row[4] in {'captured', 'rebelled'}:
            return f'{action.upper()}|city:{city_id}|source:{row[4]}'
    return None
