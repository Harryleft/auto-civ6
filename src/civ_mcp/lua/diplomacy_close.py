"""显式结束指定外交会话，并确认原会话原生关闭。"""

import json

from civ_mcp.lua._helpers import SENTINEL, _LUA_ACTION_SCOPE


def build_close(target: int, token: str) -> str:
    return _LUA_ACTION_SCOPE + f'''
local token={json.dumps(token)}
local ok,r=pcall(function()
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 local owner=Game.GetLocalPlayer()
 assert(owner~={target} and Players[{target}],"TARGET_UNAVAILABLE")
 local sid=DiplomacyManager.FindOpenSessionID(owner,{target})
 assert(scopeIndex(sid) and DiplomacyManager.IsSessionIDOpen(sid)==true,"NO_OPEN_SESSION")
 local info=DiplomacyManager.GetSessionInfo(sid)
 assert(info and ((info.FromPlayer==owner and info.ToPlayer=={target})
  or (info.FromPlayer=={target} and info.ToPlayer==owner)),"PARTICIPANTS_UNKNOWN")
 assert(type(DiplomacyManager.CloseSession)=="function" and Events.DiplomacySessionClosed
  and type(Events.DiplomacySessionClosed.Add)=="function"
  and type(Events.DiplomacySessionClosed.Remove)=="function","CLOSE_UNAVAILABLE")
 local targetCiv=PlayerConfigurations[{target}]:GetCivilizationTypeName()
 assert(type(targetCiv)=="string" and #targetCiv>0,"TARGET_IDENTITY_UNAVAILABLE")
 return {{token=token,owner=owner,target={target},sid=sid,targetCiv=targetCiv,
  civ=PlayerConfigurations[owner]:GetCivilizationTypeName(),
  seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED"),turn=Game.GetCurrentGameTurn()}}
end)
if not ok then print("ERR:DIPLOMACY_CLOSE_REFUSED|"..tostring(r));print("{SENTINEL}");return end
local old=__civmcpDiplomacyClose
if old and old.token==token then print("OK:DIPLOMACY_CLOSE_REQUESTED|existing");print("{SENTINEL}");return end
if old and old.handler then Events.DiplomacySessionClosed.Remove(old.handler) end
__civmcpDiplomacyClose=r
r.handler=function(sid)
 if __civmcpDiplomacyClose==r and r.entered and sid==r.sid and sameScope(r) then r.closed=true end
end
Events.DiplomacySessionClosed.Add(r.handler)
local valid,detail=pcall(function()
 assert(sameScope(r),"SCOPE_CHANGED")
 assert(PlayerConfigurations[r.target]:GetCivilizationTypeName()==r.targetCiv,"TARGET_CHANGED")
 assert(DiplomacyManager.FindOpenSessionID(r.owner,r.target)==r.sid
  and DiplomacyManager.IsSessionIDOpen(r.sid)==true,"SESSION_CHANGED")
 local info=DiplomacyManager.GetSessionInfo(r.sid)
 assert(info and ((info.FromPlayer==r.owner and info.ToPlayer==r.target)
  or (info.FromPlayer==r.target and info.ToPlayer==r.owner)),"PARTICIPANTS_CHANGED")
end)
if not valid then
 Events.DiplomacySessionClosed.Remove(r.handler)
 print("ERR:DIPLOMACY_CLOSE_REFUSED|"..tostring(detail));print("{SENTINEL}");return
end
r.entered=true
DiplomacyManager.CloseSession(r.sid)
r.returned=true
print("OK:DIPLOMACY_CLOSE_REQUESTED|"..r.sid)
print("{SENTINEL}")
'''


def build_query(target: int, token: str) -> str:
    return _LUA_ACTION_SCOPE + f'''
local r=__civmcpDiplomacyClose
local ok=pcall(function()
 assert(type(r)=="table" and r.token=={json.dumps(token)} and r.target=={target})
 assert(sameScope(r) and r.entered==true and r.returned==true and r.closed==true)
 assert(UI.IsGameCoreBusy()==false)
 local civ=PlayerConfigurations[r.target]:GetCivilizationTypeName()
 if type(civ)=="string" and civ~=r.targetCiv then r.invalid=true end
 assert(civ==r.targetCiv and not r.invalid)
 assert(DiplomacyManager.IsSessionIDOpen(r.sid)==false)
 local current=DiplomacyManager.FindOpenSessionID(r.owner,r.target)
 -- 原生关闭后可不返回任何值；必须同时有精确关闭事件和 IsSessionIDOpen=false。
 assert(current==nil or (scopeInteger(current) and current<0))
 assert(sameScope(r))
end)
if ok then print("DIPLOMACY_CLOSE_CONFIRMED|"..r.token.."|"..r.target.."|"..r.sid)
else print("DIPLOMACY_CLOSE_OBSERVED|{token}") end
print("{SENTINEL}")
'''


def parse_confirmation(lines: list[str], target: int, token: str) -> str | None:
    rows = [line.split("|") for line in lines if line.startswith("DIPLOMACY_CLOSE_CONFIRMED|")]
    if len(rows) == 1 and len(rows[0]) == 4:
        row = rows[0]
        if row[1:3] == [token, str(target)] and row[3].isdigit():
            return f"DIPLOMACY_SESSION_CLOSED|player:{target}|session:{row[3]}"
    return None
