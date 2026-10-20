"""界面压力缓存未刷新时，独立核对原城市的 GameCore 信徒变化。"""

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
local ok,after=pcall(function()
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
 assert(scoped and known and finalCity and scopeIndex(after) and after>r.before and finalScope)
 return after
end)
if ok then
 print("SPREAD_CORE_CONFIRMED|"..r.token.."|"..r.id.."|"..r.owner.."|"..r.religion.."|"..r.city.."|"..r.civ.."|"..r.seed.."|"..r.turn.."|"..r.before.."|"..after)
else print("SPREAD_CORE_OBSERVED|{token}") end
print("{SENTINEL}")
'''
