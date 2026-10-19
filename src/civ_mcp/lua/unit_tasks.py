"""Native unit task selection; task start is distinct from its eventual result."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE
from civ_mcp.lua.units import _LUA_UNIT_READINESS


# Native Started carries the operation hash; Added carries its row Index in
# the fourth field. Automation start does not prove future exploration results.
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
 -- UnitPanel's fourth argument true is only its loose UI visibility check.
 assert(UnitManager.CanStartOperation(unit,op.Hash,nil,false)==true,"CANNOT_OPERATE")
 assert(type(UnitManager.RequestOperation)=="function","OPERATION_UNAVAILABLE")
 if "{kind}"=="automate" then assert(scopeIndex(op.Index),"OPERATION_INDEX_UNAVAILABLE") end
 local events={{}}
 local addedAvailable=false
 for _,name in ipairs({{"UnitOperationStarted","UnitOperationAdded"}}) do
  local event=Events and Events[name]
  if event and type(event.Add)=="function" and type(event.Remove)=="function"
   and (name~="UnitOperationAdded" or scopeIndex(op.Index)) then
   table.insert(events,name)
   if name=="UnitOperationAdded" then addedAvailable=true end
  end
 end
 -- Automation emitted Added but no Started in the real engine. Require its
 -- usable observer before submission, rather than hoping Started will fire.
 assert(#events>0 and ("{kind}"~="automate" or addedAvailable),"OPERATION_OBSERVER_UNAVAILABLE")
 r.op=op.Hash;r.opIndex=op.Index;r.events=events
 return r
end)
if not ok then print("ERR:UNIT_TASK_REFUSED|"..tostring(r));print("{SENTINEL}");return end
retireTask(__civmcpUnitTask)
__civmcpUnitTask=r
if r.noop then print("OK:UNIT_TASK_NOOP|"..r.id);print("{SENTINEL}");return end
local armed=pcall(function()
 for _,name in ipairs(r.events) do
  local function accept(owner,id,matches)
   if __civmcpUnitTask==r and r.entered==true and owner==r.owner and id==r.id and matches
    and sameScope(r) then r.started=true end
  end
  if name=="UnitOperationStarted" then
   r.handlers[name]=function(owner,id,operationHash) accept(owner,id,operationHash==r.op) end
  else
   -- T11 native Added carries row Index in field four; field three is not
   -- the operation hash and is not used as task identity.
   r.handlers[name]=function(owner,id,unused,operationIndex)
    accept(owner,id,r.opIndex~=nil and operationIndex==r.opIndex)
   end
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
 local ready=unitReady(unit)
 assert(type(ready)=="boolean")
 -- A native exploration attempt can finish immediately and leave the unit
 -- ready (T29 live capture). Its exact event below still proves acceptance.
 assert(ready==false or (r.kind=="automate" and not r.noop))
 if r.noop then
  assert(r.activity~=nil and UnitManager.GetActivityType(unit)==r.activity)
  return
 end
 assert(r.entered==true and r.returned==true and r.started==true)
 if r.activity~=nil then
  local current=UnitManager.GetActivityType(unit)
  local operation=ActivityTypes and ActivityTypes.ACTIVITY_OPERATION
  -- Native alert remains OPERATION after its exact start (T16 live capture).
  -- This confirms selection, not arrival at the later SENTRY activity.
  assert(current==r.activity or (r.kind=="alert" and scopeInteger(operation) and current==operation))
 end
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
            if row[5].startswith('ALREADY_'):
                detail = 'No new operation submitted.'
            elif kind == 'automate':
                detail = 'Native exploration request accepted; movement and continued exploration not measured.'
            else:
                detail = 'Native task started; completion not measured.'
            return f'{row[5]}|unit:{row[4]}|{detail}'
    return None
