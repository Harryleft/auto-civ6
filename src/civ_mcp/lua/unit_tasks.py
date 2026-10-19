"""Native unit task selection; task start is distinct from its eventual result."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE
from civ_mcp.lua.units import _LUA_UNIT_READINESS


# Native UnitOperationStarted/Added bind the exact operation. Automation has
# no supported current-task getter; report observed start, never explored results.
_TASKS = {
    'automate': ('AUTOMATE_EXPLORE', 'AUTOMATED', None),
    'heal': ('HEAL', 'HEALING', 'ACTIVITY_HEAL'),
    'alert': ('ALERT', 'ALERT', 'ACTIVITY_SENTRY'),
    'sleep': ('SLEEP', 'SLEEPING', 'ACTIVITY_SLEEP'),
}

_CONTEXT = _LUA_ACTION_SCOPE + _LUA_UNIT_READINESS + '''
local function taskUnit(owner,id)
 local unit=UnitManager.GetUnit(owner,id%65536)
 assert(unit and unit:GetOwner()==owner,"UNIT_ID_CHANGED")
 local fullId=unit:GetID()
 assert(type(fullId)=="number" and fullId>=0 and fullId<math.huge and fullId==math.floor(fullId),"UNIT_ID_UNAVAILABLE")
 assert(id<65536 or fullId==id,"UNIT_ID_CHANGED")
 assert(unit:GetX()~=-9999,"UNIT_CONSUMED")
 return unit
end
local function retireTask(r)
 for _,name in ipairs({"UnitOperationStarted","UnitOperationAdded"}) do
  if r and r.handlers and r.handlers[name] then
   pcall(function() Events[name].Remove(r.handlers[name]) end)
  end
 end
end
'''


def build_task(unit_id: int, kind: str, token: str) -> str:
    operation, label, activity = _TASKS[kind]
    expected_activity = f"(ActivityTypes and ActivityTypes.{activity})" if activity else "nil"
    return _CONTEXT + f'''
local ok,r=pcall(function()
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local unit=taskUnit(owner,{unit_id})
 assert(type(unitReady(unit))=="boolean","READINESS_UNKNOWN")
 local activity={expected_activity}
 assert(activity==nil or type(activity)=="number","ACTIVITY_UNAVAILABLE")
 if "{kind}"=="sleep" then assert(activity~=nil,"ACTIVITY_UNAVAILABLE") end
 local r={{token={json.dumps(token)},requested={unit_id},kind={json.dumps(kind)},label={json.dumps(label)},
  id=unit:GetID(),owner=owner,activity=activity,handlers={{}},
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
 if "{kind}"=="heal" then
  local damage=unit:GetDamage()
  assert(type(damage)=="number" and damage>=0 and damage<math.huge,"DAMAGE_UNKNOWN")
  if damage==0 then r.noop="ALREADY_HEALTHY";return r end
 end
 if activity~=nil and unitReady(unit)==false and UnitManager.GetActivityType(unit)==activity then
  r.noop="ALREADY_{label}";return r
 end
 local op=GameInfo.UnitOperations["UNITOPERATION_{operation}"]
 assert(op and type(op.Hash)=="number","OPERATION_UNAVAILABLE")
 assert(UnitManager.CanStartOperation(unit,op.Hash,nil,true)==true,"CANNOT_OPERATE")
 assert(type(UnitManager.RequestOperation)=="function","OPERATION_UNAVAILABLE")
 local events={{}}
 for _,name in ipairs({{"UnitOperationStarted","UnitOperationAdded"}}) do
  local event=Events and Events[name]
  if event and type(event.Add)=="function" and type(event.Remove)=="function" then table.insert(events,name) end
 end
 assert(#events>0,"OPERATION_OBSERVER_UNAVAILABLE")
 r.op=op.Hash;r.events=events
 return r
end)
if not ok then print("ERR:UNIT_TASK_REFUSED|"..tostring(r));print("{SENTINEL}");return end
retireTask(__civmcpUnitTask)
__civmcpUnitTask=r
if r.noop then print("OK:UNIT_TASK_NOOP|"..r.id);print("{SENTINEL}");return end
local armed=pcall(function()
 for _,name in ipairs(r.events) do
  r.handlers[name]=function(owner,id,op)
   if __civmcpUnitTask==r and r.entered==true and owner==r.owner and id==r.id and op==r.op
    and sameScope(r) then r.started=true end
  end
  Events[name].Add(r.handlers[name])
 end
end)
if not armed then retireTask(r);print("ERR:UNIT_TASK_REFUSED|OPERATION_OBSERVER_UNAVAILABLE");print("{SENTINEL}");return end
r.entered=true
UnitManager.RequestOperation(taskUnit(r.owner,r.id),r.op,{{}})
r.returned=true
print("OK:UNIT_TASK_REQUESTED|"..r.id)
print("{SENTINEL}")
'''


def build_task_query(unit_id: int, kind: str, token: str) -> str:
    return _CONTEXT + f'''
local r=__civmcpUnitTask
local ok=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.requested=={unit_id} and r.kind=={json.dumps(kind)})
 assert(sameScope(r))
 assert(UI.IsGameCoreBusy()==false)
 local unit=taskUnit(r.owner,r.id)
 if r.noop=="ALREADY_HEALTHY" then assert(unit:GetDamage()==0);return end
 assert(unitReady(unit)==false)
 if r.activity~=nil then assert(UnitManager.GetActivityType(unit)==r.activity) end
 if r.noop then return end
 assert(r.entered==true and r.returned==true and r.started==true)
end)
if ok then
 retireTask(r)
 print("UNIT_TASK_CONFIRMED|{token}|{unit_id}|{kind}|"..r.id.."|"..(r.noop or r.label))
else print("UNIT_TASK_OBSERVED|{token}|UNKNOWN") end
print("{SENTINEL}")
'''


def parse_task_confirmation(lines: list[str], unit_id: int, kind: str, token: str) -> str | None:
    rows = [line.split('|') for line in lines if line.startswith('UNIT_TASK_CONFIRMED|')]
    labels = {_TASKS[kind][1]}
    if kind != 'automate':
        labels.add('ALREADY_' + _TASKS[kind][1])
    if kind == 'heal':
        labels.add('ALREADY_HEALTHY')
    if len(rows) == 1 and len(rows[0]) == 6:
        row = rows[0]
        if row[1:4] == [token, str(unit_id), kind] and row[4].isdigit() and row[5] in labels:
            detail = 'No new operation submitted.' if row[5].startswith('ALREADY_') else 'Native task started; completion not measured.'
            return f'{row[5]}|unit:{row[4]}|{detail}'
    return None
