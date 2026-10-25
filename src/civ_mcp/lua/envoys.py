"""An InGame submission witness plus authoritative GameCore influence facts."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


_COUNTS = '''
local function count(value)
 assert(type(value)=="number" and value>=0 and value%1==0,"INFLUENCE_COUNT_UNAVAILABLE")
 return value
end
local function readInfluence(owner,target)
 local player,cityState=Players[owner],Players[target]
 assert(player and cityState and cityState:IsAlive()==true
  and cityState:IsMajor()==false and cityState:IsBarbarian()==false,"TARGET_UNAVAILABLE")
 local influence,received=player:GetInfluence(),cityState:GetInfluence()
 assert(received:CanReceiveInfluence()==true,"NOT_A_CITY_STATE")
 return count(influence:GetTokensToGive()),count(received:GetTokensReceived(owner)),
  PlayerConfigurations[target]:GetCivilizationTypeName()
end
'''

_CORE_SCOPE = '''
-- GameCore has no UI local-player authority. The bound owner comes from the
-- InGame scope probe and is checked again by the original InGame witness.
local function coreScope(r)
 if r.invalid then return false end
 local known=true
 local function check(read,valid,expected)
  local ok,value=pcall(read)
  if not ok or not valid(value) then known=false
  elseif value~=expected then r.invalid=true end
 end
 local function integer(value) return type(value)=="number" and value==value and math.abs(value)<math.huge and value%1==0 end
 local function civ(value) return type(value)=="string" and value:match("^CIVILIZATION_[A-Z0-9_]+$")~=nil end
 check(function() return PlayerConfigurations[r.owner]:GetCivilizationTypeName() end,civ,r.civ)
 check(function() return GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED") end,integer,r.seed)
 check(function() return Game.GetCurrentGameTurn() end,integer,r.turn)
 if r.target~=nil then check(function() return PlayerConfigurations[r.target]:GetCivilizationTypeName() end,civ,r.targetCiv) end
 return known and not r.invalid
end
'''


def build_envoy_preparation(owner: int, target: int, civilization: str, seed: int, turn: int, token: str) -> str:
    """Freeze engine counters before the single UI request; no gameplay writes."""
    return _COUNTS + _CORE_SCOPE + f'''
local ok,r=pcall(function()
 local tokens,received,targetCiv=readInfluence({owner},{target})
 local r={{token={json.dumps(token)},owner={owner},target={target},civ={json.dumps(civilization)},
  seed={seed},turn={turn},tokens=tokens,received=received,targetCiv=targetCiv}}
 assert(coreScope(r),"CORE_SCOPE_UNAVAILABLE_OR_CHANGED")
 assert(tokens>0,"NO_ENVOYS")
 assert(Players[r.owner]:GetDiplomacy():HasMet(r.target)==true,"TARGET_NOT_MET")
 return r
end)
if ok then
 __civmcpEnvoyCore=r
 print("ENVOY_BASELINE|"..r.token.."|"..r.owner.."|"..r.target.."|"..r.tokens.."|"..r.received.."|"..r.targetCiv)
else print("ERR:ENVOY_REFUSED|"..tostring(r)) end
print("{SENTINEL}")
'''


def parse_envoy_preparation(lines: list[str], token: str, owner: int, target: int) -> tuple[int, int, str]:
    rows = [row.split('|') for row in lines if row.startswith('ENVOY_BASELINE|')]
    if (len(rows) != 1 or len(rows[0]) != 7 or rows[0][1:4] != [token, str(owner), str(target)]
            or not all(value.isdigit() for value in rows[0][4:6])
            or int(rows[0][4]) <= 0 or not rows[0][6].startswith('CIVILIZATION_')
            or any(row.startswith('ERR:') for row in lines)):
        raise ValueError('; '.join(lines) or 'GameCore envoy baseline unavailable')
    return int(rows[0][4]), int(rows[0][5]), rows[0][6]


def build_send_envoy(target: int, token: str, tokens: int, received: int, target_civ: str) -> str:
    # InGame influence getters can remain stale after a successful operation.
    # Only GameCore's frozen counts are used for the completion baseline.
    return _LUA_ACTION_SCOPE + f'''
local token={json.dumps(token)}
local ok,receipt=pcall(function()
 assert(__civmcpEnvoy==nil or type(__civmcpEnvoy)=="table","RECEIPT_STORAGE_UNAVAILABLE")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local influence=Players[owner]:GetInfluence()
 assert(Players[owner]:GetDiplomacy():HasMet({target})==true,"TARGET_NOT_MET")
 assert(influence:CanGiveInfluence()==true and influence:CanGiveTokensToPlayer({target})==true,"CANNOT_SEND")
 assert(PlayerConfigurations[{target}]:GetCivilizationTypeName()=={json.dumps(target_civ)},"TARGET_CHANGED")
 assert(type(UI.RequestPlayerOperation)=="function" and PlayerOperations.GIVE_INFLUENCE_TOKEN~=nil
  and PlayerOperations.PARAM_PLAYER_ONE~=nil,"OPERATION_UNAVAILABLE")
 return {{token=token,owner=owner,target={target},tokens={tokens},received={received},targetCiv={json.dumps(target_civ)},
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
end)
if not ok then print("ERR:ENVOY_REFUSED|"..tostring(receipt));print("{SENTINEL}");return end
if not (__civmcpEnvoy and __civmcpEnvoy.token==token) then
 __civmcpEnvoy=receipt
 local params={{[PlayerOperations.PARAM_PLAYER_ONE]={target}}}
 receipt.entered=true
 UI.RequestPlayerOperation(receipt.owner,PlayerOperations.GIVE_INFLUENCE_TOKEN,params)
 receipt.returned=true
end
print("OK:ENVOY_REQUESTED|player:{target}")
print("{SENTINEL}")
'''


def build_envoy_query(target: int, token: str) -> str:
    """Observe only the exact UI submission; cached UI counts never confirm it."""
    return _LUA_ACTION_SCOPE + f'''
local r=__civmcpEnvoy
local ok,reason=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.target=={target},"RECEIPT_MISSING_OR_MISMATCHED")
 assert(sameScope(r),"SCOPE_CHANGED_OR_UNKNOWN")
 assert(r.entered==true and r.returned==true,"REQUEST_NOT_RETURNED")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 print("ENVOY_WITNESS|"..r.token.."|"..r.owner.."|"..r.target.."|"..r.tokens.."|"..r.received)
end)
if not ok then print("ENVOY_OBSERVED|{token}|"..tostring(reason)) end
print("{SENTINEL}")
'''


def build_envoy_core_query(target: int, token: str) -> str:
    """Confirm both native engine deltas in the retained request's scope."""
    return _COUNTS + _CORE_SCOPE + f'''
local r=__civmcpEnvoyCore
local observedTokens,observedReceived
local ok,reason=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.target=={target},"CORE_RECEIPT_MISSING_OR_MISMATCHED")
 assert(coreScope(r),"CORE_SCOPE_CHANGED_OR_UNKNOWN")
 local tokens,received=readInfluence(r.owner,r.target)
 observedTokens,observedReceived=tokens,received
 assert(tokens==r.tokens-1 and received>r.received,"INFLUENCE_DELTA_UNCONFIRMED")
 print("ENVOY_CONFIRMED|"..r.token.."|"..r.owner.."|"..r.target.."|"..r.tokens.."|"..tokens.."|"..r.received.."|"..received)
end)
if not ok then
 if type(r)=="table" and r.token=={json.dumps(token)} then
  print("ENVOY_CORE_COUNTS|"..r.token.."|tokens:"..tostring(r.tokens).."->"..tostring(observedTokens)
   .."|received:"..tostring(r.received).."->"..tostring(observedReceived))
 end
 print("ENVOY_OBSERVED|{token}|"..tostring(reason))
end
print("{SENTINEL}")
'''


def parse_envoy_confirmation(lines: list[str], token: str, owner: int, target: int, before: tuple[int, int, str]) -> str | None:
    rows = [row.split('|') for row in lines if row.startswith('ENVOY_CONFIRMED|')]
    witnesses = [row.split('|') for row in lines if row.startswith('ENVOY_WITNESS|')]
    ids = [token, str(owner), str(target)]
    if len(rows) != 1 or len(witnesses) != 1 or len(rows[0]) != 8 or len(witnesses[0]) != 6:
        return None
    row, witness = rows[0], witnesses[0]
    if (row[1:4] == witness[1:4] == ids and all(value.isdigit() for value in row[4:])
            and witness[4:] == [str(before[0]), str(before[1])]
            and [row[4], row[6]] == witness[4:]
            and int(row[4]) - int(row[5]) == 1 and int(row[7]) > int(row[6])):
        return f'ENVOY_SENT|player:{target}|remaining:{row[5]}|envoys:{row[6]}->{row[7]}'
    return None


def build_city_states_observation(owner: int, civilization: str, seed: int, turn: int, eligibility: dict[int, bool]) -> str:
    """Read only met city-states from GameCore in the UI-bound world."""
    from civ_mcp.lua.governance import build_city_states_query
    return _CORE_SCOPE + f'''
do
 local r={{owner={owner},civ={json.dumps(civilization)},seed={seed},turn={turn}}}
 if not coreScope(r) then
  print("ERR:CITY_STATE_SCOPE_UNAVAILABLE|Engine identity does not match the local UI scope")
  print("{SENTINEL}");return
 end
end
''' + build_city_states_query(owner, eligibility)
