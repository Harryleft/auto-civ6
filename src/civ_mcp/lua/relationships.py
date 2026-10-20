"""Retain each native relationship phase; confirmations never replay diplomacy."""
import json
from civ_mcp.lua import diplomacy
from civ_mcp.lua._helpers import SENTINEL

_ACTIONS = {
    'DECLARE_FRIENDSHIP', 'DIPLOMATIC_DELEGATION', 'RESIDENT_EMBASSY', 'DENOUNCE',
    'DECLARE_SURPRISE_WAR', 'DECLARE_FORMAL_WAR', 'DECLARE_HOLY_WAR',
    'DECLARE_LIBERATION_WAR', 'DECLARE_RECONQUEST_WAR', 'DECLARE_PROTECTORATE_WAR',
    'DECLARE_COLONIAL_WAR', 'DECLARE_TERRITORIAL_WAR', 'MAKE_PEACE',
    'ALLIANCE_RESEARCH', 'ALLIANCE_MILITARY', 'ALLIANCE_CULTURAL',
    'ALLIANCE_ECONOMIC', 'ALLIANCE_RELIGIOUS',
}


def supported(action: str) -> bool:
    return action in _ACTIONS


def build_request(target: int, action: str, token: str) -> str:
    body = (diplomacy._build_form_alliance(target, action.removeprefix('ALLIANCE_'))
            if action.startswith('ALLIANCE_') else diplomacy._build_propose_peace(target)
            if action == 'MAKE_PEACE' else diplomacy._build_send_diplo_action(target, action))
    return diplomacy._native_relationship_lua() + f'''
local token={json.dumps(token)}
if __civmcpRelationship and __civmcpRelationship.token==token then
 print("OK:RELATIONSHIP_REQUESTED|"..token);print("{SENTINEL}");return
end
local previous=__civmcpRelationship
if previous and previous.dispose then previous.dispose() end
local r={{token=token,target={target},action={json.dumps(action)},owner=Game.GetLocalPlayer(),steps={{}}}}
__civmcpRelationship=r
local function _rel_step(name, fn)
 assert(not r.steps[name],"RELATIONSHIP_STAGE_ALREADY_ENTERED")
 r.entered=true;r.steps[name]={{entered=true}}
 local result=fn()
 r.steps[name].returned=true
 assert(result~=false,"NATIVE_REFUSED_AFTER_ENTRY")
 return result
end
local function _rel_session(sid)
 local info=DiplomacyManager.GetSessionInfo(sid)
 assert(info and info.FromPlayer==r.owner and info.ToPlayer==r.target,"SESSION_PAIR_UNKNOWN")
 r.sid=sid
end
local output={{}}
local function submit()
 local print=function(row) table.insert(output,row) end
 assert(r.owner~=r.target and Players[r.target] and Players[r.target]:IsAlive(),"INVALID_PLAYER")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 r.name=Locale.Lookup(PlayerConfigurations[r.target]:GetCivilizationShortDescription())
 if r.action=="DIPLOMATIC_DELEGATION" or r.action=="RESIDENT_EMBASSY" then
  local diplo=Players[r.owner]:GetDiplomacy()
  local present
  if r.action=="DIPLOMATIC_DELEGATION" then present=diplo:HasDelegationAt(r.target)
  else present=diplo:HasEmbassyAt(r.target) end
  assert(type(present)=="boolean","ACCESS_BASELINE_UNKNOWN")
  if present then r.noop=true;return end
  r.cost=diplo:GetDiplomaticActionCost("DIPLOACTION_"..r.action)
  r.gold=Players[r.owner]:GetTreasury():GetGoldBalance()
  assert(type(r.cost)=="number" and r.cost==r.cost and r.cost<math.huge and r.cost>=0 and type(r.gold)=="number" and r.gold==r.gold and r.gold<math.huge and r.gold>=r.cost,"ACCESS_COST_UNKNOWN_OR_UNAFFORDABLE")
 end
 if r.action=="DIPLOMATIC_DELEGATION" or r.action=="RESIDENT_EMBASSY" or r.action=="DECLARE_FRIENDSHIP" then
  -- 仅采信本请求、当前世界与回合中的原生明确拒绝，不能用未扣费推断拒绝。
  local turn=Game.GetCurrentGameTurn()
  local seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
  local civ=PlayerConfigurations[r.owner]:GetCivilizationTypeName()
  local handler=function(from,to,k)
   if __civmcpRelationship~=r or Game.GetLocalPlayer()~=r.owner or Game.GetCurrentGameTurn()~=turn
    or GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")~=seed
    or PlayerConfigurations[r.owner]:GetCivilizationTypeName()~=civ then return end
   if from~=r.target or to~=r.owner or type(k)~="table" or k.FromPlayer~=from or k.SessionID~=r.sid then return end
   local ok,refused=pcall(function()
    local statement=r.action=="DECLARE_FRIENDSHIP" and "DECLARE_FRIEND" or r.action
    return DiplomacyManager.GetKeyName(k.StatementType)==statement
     and DiplomacyManager.GetKeyName(k.StatementSubType)=="AI_REFUSE_DEAL"
     and DiplomacyManager.GetKeyName(k.ResponseType)=="NEGATIVE"
   end)
   if ok and refused then r.refused=true end
  end
  pcall(function()
   Events.DiplomacyStatement.Add(handler)
   r.dispose=function() Events.DiplomacyStatement.Remove(handler) end
  end)
 end
 if r.action=="DECLARE_FRIENDSHIP" then
  local ourState=nativeRelationship(r.owner,r.target)
  local theirState=nativeRelationship(r.target,r.owner)
  local ours=matchesRelationship(ourState,"DIPLO_STATE_DECLARED_FRIEND",DiplomaticStates.DECLARED_FRIEND)
  local theirs=matchesRelationship(theirState,"DIPLO_STATE_DECLARED_FRIEND",DiplomaticStates.DECLARED_FRIEND)
  assert(ours~=nil and theirs~=nil,"RELATIONSHIP_UNKNOWN")
  r.ourStateType=ourState.StateType;r.ourStateHash=ourState.Hash
  r.theirStateType=theirState.StateType;r.theirStateHash=theirState.Hash
  if ours and theirs then r.noop=true;return end
 end
 if r.action=="MAKE_PEACE" or r.action:find("^DECLARE_.+_WAR$") then
  local ours=Players[r.owner]:GetDiplomacy():IsAtWarWith(r.target)
  local theirs=Players[r.target]:GetDiplomacy():IsAtWarWith(r.owner)
  assert(type(ours)=="boolean" and type(theirs)=="boolean" and ours==theirs,"WAR_STATE_UNKNOWN")
  if ours==(r.action~="MAKE_PEACE") then r.noop=true;return end
 end
 if r.action:find("^ALLIANCE_") then
  local ours=matchesRelationship(nativeRelationship(r.owner,r.target),"DIPLO_STATE_ALLIED",DiplomaticStates.ALLIED)
  local theirs=matchesRelationship(nativeRelationship(r.target,r.owner),"DIPLO_STATE_ALLIED",DiplomaticStates.ALLIED)
  if ours and theirs then
   local index=Players[r.owner]:GetDiplomacy():GetAllianceType(r.target)
   local row=integer(index) and GameInfo.Alliances[index]
   if row and row.AllianceType==r.action then r.noop=true;return end
  end
 end
 if r.action=="DENOUNCE" then
  r.denounceTurn=Players[r.owner]:GetDiplomacy():GetDenounceTurn(r.target)
  assert(integer(r.denounceTurn),"DENOUNCE_BASELINE_UNKNOWN")
  r.turn=Game.GetCurrentGameTurn()
  if r.denounceTurn==r.turn and matchesRelationship(nativeRelationship(r.owner,r.target),"DIPLO_STATE_DENOUNCED",DiplomaticStates.DENOUNCED)==true then r.noop=true;return end
 end
 {body}
end
local ok,err=pcall(submit)
if not ok then
 if r.entered then error(err) end
 print("ERR:RELATIONSHIP_REFUSED|"..tostring(err));print("{SENTINEL}");return
end
for _,row in ipairs(output) do
 if row:find("^ERR:") and not r.entered then
  print("ERR:RELATIONSHIP_REFUSED|"..row:sub(5));print("{SENTINEL}");return
 end
 if row:find("^OK:ALREADY_ESTABLISHED|") then r.noop=true end
end
r.returned=true
print("OK:RELATIONSHIP_REQUESTED|"..token)
print("{SENTINEL}")
'''


def build_confirmation(target: int, action: str, token: str, scope) -> str:
    if action == 'DECLARE_FRIENDSHIP':
        query = diplomacy.build_check_friendship_state(target, scope.owner)
        expected = f'FRIENDSHIP|{scope.owner}|{target}|DECLARED_FRIEND'
        label = '"Friendship established with "..r.name'
    elif action.startswith('ALLIANCE_'):
        kind=action.removeprefix('ALLIANCE_')
        query=diplomacy.build_check_alliance_state(target, scope.owner, kind)
        expected=f'ALLIANCE|{scope.owner}|{target}|{action}'
        label=json.dumps(kind+' alliance formed with ')+'..r.name'
    elif action in {'DIPLOMATIC_DELEGATION','RESIDENT_EMBASSY'}:
        name='Delegation' if action=='DIPLOMATIC_DELEGATION' else 'Embassy'
        query=diplomacy.build_check_diplomatic_access(target, scope.owner, action, scope.seed, scope.civilization)
        expected=f'{action}|{scope.owner}|{target}|PRESENT'
        label=f'(r.noop and "{name} already established with "..r.name or r.name.." accepted your {name.lower()}")'
    else:
        expected='RELATIONSHIP_EFFECT'
        if action=='DENOUNCE':
            query=diplomacy._native_relationship_lua()+'''
local value=Players[r.owner]:GetDiplomacy():GetDenounceTurn(r.target)
local state=nativeRelationship(r.owner,r.target)
if value==r.turn and (r.noop or value~=r.denounceTurn) and matchesRelationship(state,"DIPLO_STATE_DENOUNCED",DiplomaticStates.DENOUNCED)==true then print("RELATIONSHIP_EFFECT") end
'''
            label='"Denounced "..r.name'
        else:
            at_war='false' if action=='MAKE_PEACE' else 'true'
            query=f'''
if Players[r.owner]:GetDiplomacy():IsAtWarWith(r.target)=={at_war}
 and Players[r.target]:GetDiplomacy():IsAtWarWith(r.owner)=={at_war} then print("RELATIONSHIP_EFFECT") end
'''
            label='"Peace established with "..r.name' if action=='MAKE_PEACE' else '"War declared on "..r.name'
    return f'''
local r=__civmcpRelationship
local ok,result=pcall(function()
 assert(r and r.token=={json.dumps(token)} and r.owner=={scope.owner} and r.target=={target} and r.action=={json.dumps(action)},"RECEIPT_MISMATCH")
 assert(r.returned==true and (r.noop or r.sid and r.steps.request and r.steps.request.entered),"REQUEST_NOT_RETURNED")
 assert(UI.IsGameCoreBusy()==false,"CORE_BUSY_OR_UNKNOWN")
 if r.refused then
  assert(r.steps.close and r.steps.close.returned and DiplomacyManager.IsSessionIDOpen(r.sid)==false,"REFUSAL_SESSION_UNCONFIRMED")
  if r.action=="DECLARE_FRIENDSHIP" then
   {diplomacy._native_relationship_lua()}
   local ours=nativeRelationship(r.owner,r.target)
   local theirs=nativeRelationship(r.target,r.owner)
   assert(ours and theirs and ours.StateType==r.ourStateType and ours.Hash==r.ourStateHash
    and theirs.StateType==r.theirStateType and theirs.Hash==r.theirStateHash,"REFUSAL_RELATIONSHIP_CHANGED")
   assert(matchesRelationship(ours,"DIPLO_STATE_DECLARED_FRIEND",DiplomaticStates.DECLARED_FRIEND)==false
    and matchesRelationship(theirs,"DIPLO_STATE_DECLARED_FRIEND",DiplomaticStates.DECLARED_FRIEND)==false,"REFUSAL_EFFECT_UNCONFIRMED")
   if r.dispose then r.dispose();r.dispose=nil end
   return r.name.." refused your friendship"
  end
  assert(r.cost~=nil,"REFUSAL_ACTION_UNKNOWN")
  local diplo=Players[r.owner]:GetDiplomacy()
  local present
  if r.action=="DIPLOMATIC_DELEGATION" then present=diplo:HasDelegationAt(r.target)
  else present=diplo:HasEmbassyAt(r.target) end
  assert(present==false and Players[r.owner]:GetTreasury():GetGoldBalance()==r.gold,"REFUSAL_EFFECT_UNCONFIRMED")
  if r.dispose then r.dispose();r.dispose=nil end
  return r.name.." refused your "..(r.action=="DIPLOMATIC_DELEGATION" and "delegation" or "embassy")
 end
 local matched=false
 local function read()
  local print=function(row) if row=={json.dumps(expected)} then matched=true end end
  {query}
 end
 read()
 if r.cost~=nil then
  assert(Players[r.owner]:GetTreasury():GetGoldBalance()==r.gold-r.cost,"ACCESS_SPEND_UNCONFIRMED")
 end
 assert(matched,"RELATIONSHIP_UNCONFIRMED")
 if r.dispose then r.dispose();r.dispose=nil end
 return {label}
end)
if ok then print((r.refused and "RELATIONSHIP_REJECTED|" or "RELATIONSHIP_CONFIRMED|").."{token}|"..result)
else print("RELATIONSHIP_OBSERVED|{token}|"..tostring(result)) end
print("{SENTINEL}")
'''


def parse_confirmation(rows: list[str], token: str) -> str | None:
    matching=[row.split('|') for row in rows if row.startswith(('RELATIONSHIP_CONFIRMED|', 'RELATIONSHIP_REJECTED|'))]
    if len(matching)==1 and len(matching[0])==3 and matching[0][1]==token:
        outcome = 'REJECTED' if matching[0][0] == 'RELATIONSHIP_REJECTED' else 'ACCEPTED'
        return outcome+'|'+matching[0][2]
    return None
