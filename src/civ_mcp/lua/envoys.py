"""One envoy request and its scoped, two-sided influence readback."""
import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


def parse_envoy_confirmation(lines: list[str], token: str, owner: int, target: int) -> str | None:
    rows = [row.split("|") for row in lines if row.startswith("ENVOY_CONFIRMED|")]
    if len(rows) == 1 and len(rows[0]) == 8:
        row = rows[0]
        if (row[1:4] == [token, str(owner), str(target)]
                and all(value.isdigit() for value in row[4:])
                and int(row[4]) - int(row[5]) == 1 and int(row[7]) > int(row[6])):
            return f"ENVOY_SENT|player:{row[3]}|remaining:{row[5]}|envoys:{row[6]}->{row[7]}"
    return None


_CONTEXT = _LUA_ACTION_SCOPE + '''
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


def build_send_envoy(target: int, token: str) -> str:
    return _CONTEXT + f'''
local token={json.dumps(token)}
local ok,receipt=pcall(function()
 assert(__civmcpEnvoy==nil or type(__civmcpEnvoy)=="table","RECEIPT_STORAGE_UNAVAILABLE")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 local tokens,received,targetCiv=readInfluence(owner,{target})
 local influence=Players[owner]:GetInfluence()
 assert(tokens>0,"NO_ENVOYS")
 assert(Players[owner]:GetDiplomacy():HasMet({target})==true,"TARGET_NOT_MET")
 assert(influence:CanGiveInfluence()==true and influence:CanGiveTokensToPlayer({target})==true,"CANNOT_SEND")
 assert(type(UI.RequestPlayerOperation)=="function" and PlayerOperations.GIVE_INFLUENCE_TOKEN~=nil
  and PlayerOperations.PARAM_PLAYER_ONE~=nil,"OPERATION_UNAVAILABLE")
 assert(type(targetCiv)=="string" and #targetCiv>0,"TARGET_IDENTITY_UNAVAILABLE")
 return {{token=token,owner=owner,target={target},tokens=tokens,received=received,targetCiv=targetCiv,
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
end)
if not ok then
 print("ERR:ENVOY_REFUSED|"..tostring(receipt));print("{SENTINEL}");return
end
-- Capture the baseline in the same native dispatch as the request. There is
-- no separate prepare/read window and no dependency on undocumented event args.
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
    return _CONTEXT + f'''
local r=__civmcpEnvoy
local reason="RECEIPT_MISSING_OR_MISMATCHED"
local ok,tokens,received=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.target=={target})
 reason="SCOPE_CHANGED";assert(sameScope(r))
 reason="REQUEST_NOT_RETURNED";assert(r.entered==true and r.returned==true)
 reason="CORE_BUSY_OR_UNKNOWN";assert(UI.IsGameCoreBusy()==false)
 reason="INFLUENCE_UNAVAILABLE"
 local tokens,received,targetCiv=readInfluence(r.owner,r.target)
 reason="TARGET_CHANGED";assert(targetCiv==r.targetCiv)
 -- One spent token, plus an increase at the intended city-state. Bonuses can
 -- award multiple envoys; a token decrement alone never proves the target.
 reason="INFLUENCE_DELTA_UNCONFIRMED"
 assert(tokens==r.tokens-1 and received>r.received)
 return tokens,received
end)
if ok then
 print("ENVOY_CONFIRMED|"..r.token.."|"..r.owner.."|"..r.target.."|"..r.tokens.."|"..tokens.."|"..r.received.."|"..received)
else print("ENVOY_OBSERVED|{token}|"..reason) end
print("{SENTINEL}")
'''
