"""Authoritative GameCore flag observations for the InGame apostle request."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


def build_core_preflight(unit_id: int, token: str, owner: int, civ: str, seed: int, turn: int) -> str:
    return _LUA_ACTION_SCOPE + f'''
local ok,r=pcall(function()
 local r={{token={json.dumps(token)},id={unit_id},owner={owner},civ={json.dumps(civ)},seed={seed},turn={turn}}}
 assert(sameScope(r),"CORE_SCOPE_UNKNOWN_OR_CHANGED")
 local religion=Players[r.owner]:GetReligion()
 local created=religion:GetReligionTypeCreated()
 assert(scopeIndex(created) and created>0,"CORE_FOUNDED_RELIGION_UNKNOWN")
 local flag=religion:HasLaunchedInquisition()
 assert(flag==false,"CORE_PREFLAG_NOT_FALSE")
 assert(sameScope(r),"CORE_SCOPE_UNKNOWN_OR_CHANGED")
 r.created=created;r.before=false
 return r
end)
if ok then
 __civmcpInquisitionCore=r
 print("INQUISITION_CORE_PREPARED|"..r.token.."|"..r.id.."|"..r.owner.."|"..r.created.."|"..r.civ.."|"..r.seed.."|"..r.turn)
else print("INQUISITION_CORE_REFUSED|"..tostring(r)) end
print("{SENTINEL}")
'''


def parse_core_preflight(lines: list[str], unit_id: int, token: str, owner: int, civ: str, seed: int, turn: int) -> int | None:
    rows = [row.split('|') for row in lines if row.startswith('INQUISITION_CORE_PREPARED|')]
    if len(lines) == len(rows) == 1 and len(rows[0]) == 8:
        row = rows[0]
        if (row[1:4] == [token, str(unit_id), str(owner)] and row[5:] == [civ, str(seed), str(turn)]
                and row[4].isdigit() and int(row[4]) > 0):
            return int(row[4])
    return None


def build_core_query(unit_id: int, token: str, created: int) -> str:
    return _LUA_ACTION_SCOPE + f'''
local r=__civmcpInquisitionCore
local reason="CORE_RECEIPT_MISSING_OR_MISMATCHED"
local ok=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.id=={unit_id}
  and r.created=={created} and r.before==false)
 local scopeKnown=sameScope(r)
 local known=true
 local religionOK,religion=pcall(function() return Players[r.owner]:GetReligion() end)
 local flag
 if religionOK and religion then
  local createdOK,current=pcall(function() return religion:GetReligionTypeCreated() end)
  if not createdOK or not scopeInteger(current) or current< -1 then known=false
  elseif current~=r.created then r.invalid=true end
  local flagOK,value=pcall(function() return religion:HasLaunchedInquisition() end)
  if not flagOK or type(value)~="boolean" then known=false
  else
   flag=value
   if value then r.launchedObserved=true elseif r.launchedObserved then r.invalid=true end
  end
 else known=false end
 local finalScope=sameScope(r)
 reason="CORE_SCOPE_RELIGION_OR_FLAG_CHANGED";assert(not r.invalid)
 reason="CORE_SCOPE_UNKNOWN";assert(scopeKnown and finalScope)
 reason="CORE_RELIGION_OR_FLAG_UNKNOWN";assert(known)
 reason="CORE_LAUNCH_FLAG_STILL_FALSE";assert(flag==true)
end)
if ok then
 print("INQUISITION_CORE_CONFIRMED|"..r.token.."|"..r.id.."|"..r.owner.."|"..r.created.."|"..r.civ.."|"..r.seed.."|"..r.turn.."|false->true")
else print("INQUISITION_CORE_OBSERVED|{token}|"..reason) end
print("{SENTINEL}")
'''
