"""界面压力未变化时，独立核对原城市的 GameCore 信徒读数。"""

import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


def build_preflight(unit_id, token, owner, civ, seed, turn):
    return _LUA_ACTION_SCOPE + f'''
local ok,r=pcall(function()
 local r={{token={json.dumps(token)},id={unit_id},owner={owner},civ={json.dumps(civ)},seed={seed},turn={turn}}}
 assert(sameScope(r))
 local u=Players[r.owner]:GetUnits():FindID(r.id)
 assert(u and u:GetID()==r.id and u:GetOwner()==r.owner)
 r.x=u:GetX();r.y=u:GetY();r.religion=u:GetReligion():GetReligionType()
 assert(scopeIndex(r.x) and scopeIndex(r.y) and scopeIndex(r.religion) and r.religion>0)
 local found
 for _,c in Players[r.owner]:GetCities():Members() do
  if c:GetX()==r.x and c:GetY()==r.y then assert(not found);found=c end
 end
 assert(found and found:GetOwner()==r.owner)
 r.city=found:GetID();r.before=found:GetReligion():GetNumFollowers(r.religion)
 assert(scopeIndex(r.city) and scopeIndex(r.before) and sameScope(r))
 return r
end)
if ok then
 __civmcpSpreadCore=r
 print("SPREAD_CORE_PREPARED|"..r.token.."|"..r.id.."|"..r.owner.."|"..r.city.."|"..r.x.."|"..r.y.."|"..r.religion.."|"..r.before)
else print("SPREAD_CORE_UNAVAILABLE|"..tostring(r)) end
print("{SENTINEL}")
'''


def parse_preflight(lines, unit_id, token, owner):
    rows = [line.split("|") for line in lines if line.startswith("SPREAD_CORE_PREPARED|")]
    if len(rows) == 1 and len(rows[0]) == 9:
        row = rows[0]
        if row[1:4] == [token, str(unit_id), str(owner)] and all(x.isdigit() for x in row[4:]):
            return tuple(int(x) for x in row[4:])
    return None


def build_query(unit_id, token):
    return _LUA_ACTION_SCOPE + f'''
local r=__civmcpSpreadCore
local ok,after,unitState=pcall(function()
 assert(r and r.token=={json.dumps(token)} and r.id=={unit_id})
 local scoped=sameScope(r)
 local c=Players[r.owner]:GetCities():FindID(r.city)
 if c==nil then r.invalid=true end
 assert(c)
 local function matchingCity()
  local known=true
  for _,pair in ipairs({{{{"GetID",r.city}},{{"GetOwner",r.owner}},{{"GetX",r.x}},{{"GetY",r.y}}}}) do
   local valid,value=pcall(function() return c[pair[1]](c) end)
   if not valid or not scopeIndex(value) then known=false
   elseif value~=pair[2] then r.invalid=true end
  end
  return known and not r.invalid
 end
 local known=matchingCity()
 local after=c:GetReligion():GetNumFollowers(r.religion)
 local finalCity=matchingCity()
 local finalScope=sameScope(r)
 assert(scoped and known and finalCity and scopeIndex(after) and after>=r.before and finalScope)
 local unitState
 if after==r.before then
  -- Unchanged followers are not an effect proof. This alternate receipt only
  -- supports the independent InGame last-charge/removal witness, and requires
  -- GameCore can retain a consumed unit with IsDelayedDeath=true (T195).
  local unit=Players[r.owner]:GetUnits():FindID(r.id)
  if unit==nil then unitState="absent"
  else
   for _,pair in ipairs({{{{"GetID",r.id}},{{"GetOwner",r.owner}}}}) do
    local value=unit[pair[1]](unit)
    assert(scopeIndex(value))
    if value~=pair[2] then r.invalid=true end
   end
   local religion=unit:GetReligion():GetReligionType()
   assert(scopeIndex(religion))
   if religion~=r.religion then r.invalid=true end
   assert(not r.invalid and unit:IsDelayedDeath()==true)
   unitState="delayed_death"
  end
  assert(matchingCity() and sameScope(r))
 end
 return after,unitState
end)
if ok then
 local outcome=after>r.before and "SPREAD_CORE_CONFIRMED|" or "SPREAD_CORE_UNCHANGED|"
 print(outcome..r.token.."|"..r.id.."|"..r.owner.."|"..r.religion.."|"..r.city.."|"..r.civ.."|"..r.seed.."|"..r.turn.."|"..r.before.."|"..after..(unitState and "|unit_state:"..unitState or ""))
else print("SPREAD_CORE_OBSERVED|{token}") end
print("{SENTINEL}")
'''
