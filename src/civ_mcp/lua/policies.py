"""Retained policy plan: payment and native slot submission have separate evidence."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE
from civ_mcp.lua.governance import _LUA_POLICY_PAYMENT


def _fail(message: str) -> str:
    return f'error({json.dumps(message)},0)'


def _fail_lua(expression: str) -> str:
    return f'error({expression},0)'


_FACTS = """
local function integer(value)
 assert(type(value)=="number" and value==value and math.abs(value)<math.huge and value==math.floor(value),"POLICY_FACT_UNKNOWN")
 return value
end
local function boolean(value)
 assert(type(value)=="boolean","POLICY_FACT_UNKNOWN");return value
end
local function government(value)
 integer(value)
 -- -1 is the native no-government sentinel; other values must name a row.
 assert(value==-1 or GameInfo.Governments[value]~=nil,"GOVERNMENT_UNKNOWN")
 return value
end
local function slotCount(value)
 integer(value);assert(value>=0,"POLICY_SLOT_COUNT_UNKNOWN");return value
end
local function slotType(value)
 integer(value);assert(GameInfo.GovernmentSlots[value]~=nil,"UNKNOWN_SLOT_TYPE");return value
end
"""


def build_policy_plan(assignments: dict[int, str], token: str) -> str:
    """Resolve and retain every final slot before any payment or submission."""
    entries = ", ".join(f"[{slot}]={json.dumps(policy)}" for slot, policy in assignments.items())
    return _FACTS + f'''
local ok,r=pcall(function()
assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
local me = Game.GetLocalPlayer()
local pCulture = Players[me]:GetCulture()
local numSlots = slotCount(pCulture:GetNumPolicySlots())
if numSlots <= 0 then {_fail("ERR:NO_GOVERNMENT|No government selected")} end
local requested = {{{entries}}}
local current, final, origins = {{}}, {{}}, {{}}
for slot = 0, numSlots - 1 do
    local index = integer(pCulture:GetSlotPolicy(slot))
    assert(index>=-1,"POLICY_SLOT_UNKNOWN")
    current[slot], final[slot] = index, index
    if index >= 0 then origins[index] = slot end
end
for slot, policyType in pairs(requested) do
    if type(slot) ~= "number" or slot % 1 ~= 0 or slot < 0 or slot >= numSlots then
        {_fail_lua('"ERR:INVALID_POLICY_SLOT|" .. tostring(slot)')}
    end
    if string.upper(policyType) == "NONE" then
        final[slot] = -1
    else
        local policy = GameInfo.Policies[policyType]
        if not policy then {_fail_lua('"ERR:POLICY_NOT_FOUND|" .. policyType')} end
        final[slot] = policy.Index
    end
end
local seen = {{}}
for slot = 0, numSlots - 1 do
    local index = final[slot]
    if index >= 0 then
        if seen[index] ~= nil then
            {_fail_lua('"ERR:DUPLICATE_POLICY|" .. GameInfo.Policies[index].PolicyType')}
        end
        seen[index] = slot
    end
end
local clearList, addList, types = {{}}, {{}}, {{}}
for slot = 0, numSlots - 1 do
    types[slot] = slotType(pCulture:GetSlotType(slot))
    local index = final[slot]
    if index >= 0 then
        local policy = GameInfo.Policies[index]
        if not policy then {_fail_lua('"ERR:POLICY_NOT_FOUND|" .. tostring(index)')} end
        local slotType = GameInfo.GovernmentSlots[pCulture:GetSlotType(slot)]
        if not slotType then {_fail_lua('"ERR:UNKNOWN_SLOT_TYPE|" .. slot')} end
        local acceptsAny = slotType.AllowsAnyPolicy == true or slotType.AllowsAnyPolicy == 1
        if not acceptsAny and slotType.GovernmentSlotType ~= policy.GovernmentSlotType then
            {_fail_lua('"ERR:SLOT_MISMATCH|" .. policy.PolicyType .. " cannot go in slot " .. slot .. " (" .. slotType.GovernmentSlotType .. ")"')}
        end
        if not boolean(pCulture:IsPolicyUnlocked(policy.Hash)) then
            {_fail_lua('"ERR:POLICY_LOCKED|" .. policy.PolicyType')}
        end
        if boolean(pCulture:IsPolicyObsolete(policy.Hash)) then
            {_fail_lua('"ERR:POLICY_OBSOLETE|" .. policy.PolicyType')}
        end
        local origin = origins[index]
        local alreadyThere = current[slot] == index
        local relocating = origin ~= nil and origin ~= slot
            and requested[origin] ~= nil and final[origin] ~= index
        if not boolean(pCulture:CanSlotPolicy(index, slot)) and not alreadyThere and not relocating then
            {_fail_lua('"ERR:CANNOT_SLOT|" .. policy.PolicyType .. " rejected for slot " .. slot')}
        end
        addList[slot] = integer(policy.Hash)
    end
    table.insert(clearList, slot)
end
local changed = false
for slot = 0, numSlots - 1 do
    if current[slot] ~= final[slot] then changed = true end
end
local r={{token={json.dumps(token)},owner=me,civ=PlayerConfigurations[me]:GetCivilizationTypeName(),
 seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn(),
 government=government(pCulture:GetCurrentGovernment()),count=numSlots,types=types,
 current=current,final=final,origins=origins,requested=requested,clear=clearList,add=addList,noop=not changed}}
assert(r.government>=0,"NO_GOVERNMENT")
if r.noop then return r end
-- Match GovernmentScreen's native price and configured currency. Never guess
-- affordability or directly mutate resources; the engine performs the payment.
{_LUA_POLICY_PAYMENT}
if policyCost == nil then
    {_fail('ERR:POLICY_UNLOCK_UNKNOWN|Policy unlock cost unavailable; no submission.')}
end
if policyCost > 0 then
    if policyCurrency == nil or policyBalance == nil then
        {_fail_lua('"ERR:POLICY_UNLOCK_UNKNOWN|required=" .. policyCost .. "|currency=" .. (policyCurrency or "UNKNOWN") .. "|balance=" .. tostring(policyBalance or "UNKNOWN") .. "|No submission."')}
    end
    if policyBalance < policyCost then
        {_fail_lua('"ERR:POLICY_UNLOCK_UNAFFORDABLE|required=" .. policyCost .. "|balance=" .. policyBalance .. "|currency=" .. policyCurrency .. "|No submission."')}
    end
end
r.cost,r.currency,r.balance=policyCost,policyCurrency,policyBalance
assert(type(pCulture.RequestPolicyChanges)=="function","POLICY_OPERATION_UNAVAILABLE")
if r.cost==0 then
 assert(boolean(pCulture:PolicyChangeMade())==false,"POLICIES_ALREADY_CHANGED")
else
 assert(type(UI.RequestPlayerOperation)=="function" and PlayerOperations.UNLOCK_POLICIES~=nil,"UNLOCK_OPERATION_UNAVAILABLE")
end
return r
end)
if not ok then print("ERR:POLICY_PLAN_REFUSED|"..tostring(r));print("{SENTINEL}");return end
__civmcpPolicyPlan=r
if not r.noop then
 if r.cost>0 then
  r.unlockEntered=true
  UI.RequestPlayerOperation(r.owner,PlayerOperations.UNLOCK_POLICIES,{{}})
  r.unlockReturned=true
 else
  r.paid=true
  r.policyEntered=true
  Players[r.owner]:GetCulture():RequestPolicyChanges(r.clear,r.add)
  r.policyReturned=true
 end
end
print("OK:POLICY_PLAN_REQUESTED|"..r.token)
print("{SENTINEL}")
'''


_OBSERVE = _LUA_ACTION_SCOPE + _FACTS + '''
local function observe(r,token)
 assert(type(r)=="table" and r.token==token,"POLICY_RECEIPT_UNAVAILABLE")
 assert(sameScope(r),r.invalid==true and "POLICY_SCOPE_CHANGED" or r.invalid or "POLICY_SCOPE_UNAVAILABLE")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local me=r.owner
 local pCulture=Players[me]:GetCulture()
 -- Missing layout facts block this read; only valid changes retire the plan.
 if government(pCulture:GetCurrentGovernment())~=r.government then r.invalid="POLICY_LAYOUT_CHANGED" end
 if slotCount(pCulture:GetNumPolicySlots())~=r.count then r.invalid="POLICY_LAYOUT_CHANGED" end
 for slot=0,r.count-1 do if slotType(pCulture:GetSlotType(slot))~=r.types[slot] then r.invalid="POLICY_LAYOUT_CHANGED" end end
 assert(not r.invalid,r.invalid)
 if r.noop or r.policyEntered then
  assert(r.noop or r.policyReturned==true and r.paid==true,"POLICY_REQUEST_NOT_RETURNED")
  for slot=0,r.count-1 do assert(pCulture:GetSlotPolicy(slot)==r.final[slot],"POLICY_SLOT_UNCONFIRMED:"..slot) end
  return r.noop and "UNCHANGED" or "SET"
 end
 if not r.paid then
  assert(r.unlockEntered==true and r.unlockReturned==true,"UNLOCK_REQUEST_NOT_RETURNED")
  {PAYMENT}
  assert(policyCost==0 and policyCurrency==r.currency and policyBalance~=nil
   and math.abs(policyBalance-(r.balance-r.cost))<0.000001,"UNLOCK_PAYMENT_UNCONFIRMED")
  assert(boolean(pCulture:PolicyChangeMade())==false,"POLICY_UNLOCK_NOT_AVAILABLE")
  -- This is an observation of an already completed payment, never a request.
  r.paid=true
 end
 assert(pCulture:GetCostToUnlockPolicies()==0 and boolean(pCulture:PolicyChangeMade())==false,"POLICY_SUBMISSION_NOT_AVAILABLE")
 for slot=0,r.count-1 do
  assert(pCulture:GetSlotPolicy(slot)==r.current[slot],"POLICY_BASELINE_CHANGED:"..slot)
  local index=r.final[slot]
  if index>=0 then
   local policy=GameInfo.Policies[index]
   assert(policy and boolean(pCulture:IsPolicyUnlocked(policy.Hash)) and not boolean(pCulture:IsPolicyObsolete(policy.Hash)),"POLICY_ELIGIBILITY_CHANGED")
   local origin=r.origins[index]
   local relocating=origin~=nil and origin~=slot and r.requested[origin]~=nil and r.final[origin]~=index
   assert(boolean(pCulture:CanSlotPolicy(index,slot)) or r.current[slot]==index or relocating,"POLICY_CANNOT_SLOT")
  end
 end
 assert(type(pCulture.RequestPolicyChanges)=="function","POLICY_OPERATION_UNAVAILABLE")
 return "READY"
end
'''.replace('{PAYMENT}', _LUA_POLICY_PAYMENT)


def build_policy_confirmation(token: str) -> str:
    """Read payment/plan evidence; never perform either mutation."""
    return _OBSERVE + f'''
local r=__civmcpPolicyPlan
local ok,status=pcall(observe,r,{json.dumps(token)})
if type(r)=="table" and r.token=={json.dumps(token)} then
 print("POLICY_PROGRESS|"..r.token.."|unlock_entered:"..tostring(r.unlockEntered==true).."|unlock_returned:"..tostring(r.unlockReturned==true)
  .."|paid:"..tostring(r.paid==true).."|policy_entered:"..tostring(r.policyEntered==true).."|policy_returned:"..tostring(r.policyReturned==true))
end
if ok and status=="READY" then print("POLICY_READY|"..r.token)
elseif ok then print("POLICY_CONFIRMED|"..r.token.."|"..status)
else print("POLICY_OBSERVED|{token}|"..tostring(status)) end
print("{SENTINEL}")
'''


def build_policy_continuation(token: str) -> str:
    """An explicit same-plan call may enter only its proven-unstarted phase."""
    return _OBSERVE + f'''
local r=__civmcpPolicyPlan
local ok,status=pcall(observe,r,{json.dumps(token)})
if ok and status=="READY" then
 r.policyEntered=true
 Players[r.owner]:GetCulture():RequestPolicyChanges(r.clear,r.add)
 r.policyReturned=true
 print("OK:POLICY_CONTINUED|"..r.token)
else print("POLICY_OBSERVED|{token}|"..tostring(status)) end
print("{SENTINEL}")
'''


def policy_plan_ready(lines: list[str], token: str) -> bool:
    return [row for row in lines if row.startswith('POLICY_READY|')] == [f'POLICY_READY|{token}']


def parse_policy_confirmation(lines: list[str], token: str) -> str | None:
    rows = [row.split('|') for row in lines if row.startswith('POLICY_CONFIRMED|')]
    if len(rows) != 1 or len(rows[0]) != 3 or rows[0][1] != token:
        return None
    if rows[0][2] == 'SET':
        return 'POLICIES_SET|Complete original policy plan confirmed.'
    if rows[0][2] == 'UNCHANGED':
        return 'POLICIES_UNCHANGED|Requested policies already active; no payment or submission.'
    return None
