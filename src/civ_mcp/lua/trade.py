"""Native statement witnesses and the existing trade tools' bounded lifecycle."""
from __future__ import annotations

import json

from civ_mcp.lua._helpers import SENTINEL


def _scope() -> str:
    return '''
local function currentScope()
 local me=Game.GetLocalPlayer()
 local seed=GameConfiguration.GetValue("GAME_SYNC_RANDOM_SEED")
 local civ=PlayerConfigurations[me]:GetCivilizationTypeName()
 if type(me)~="number" or me<0 or type(seed)~="number" or type(civ)~="string" then error("world unknown") end
 return tostring(me).."|"..civ.."|"..tostring(seed)
end
'''


def build_observer_install(nonce: str) -> str:
    return _scope() + f'''
local nonce={json.dumps(nonce)}
local ok,scope=pcall(currentScope)
if not ok then print("DIPLO_OBSERVER|UNKNOWN|WORLD"); return end
if ContextPtr:GetID()~="DiplomacyActionView" then print("DIPLO_OBSERVER|UNKNOWN|CONTEXT"); return end
local old=__civmcp_diplomacy
if old and old.nonce==nonce then
 if old.scope~=scope then print("DIPLO_OBSERVER|UNKNOWN|WORLD_CHANGED"); return end
 print("DIPLO_OBSERVER|READY"); print("DIPLO_SCOPE|"..scope); return
end
if old and old.trade and not old.trade.finished then print("DIPLO_OBSERVER|UNKNOWN|OLDER_TRADE"); return end
if old then
 Events.DiplomacyStatement.Remove(old.statementHandler)
 Events.DiplomacySessionClosed.Remove(old.closedHandler)
 if old.incomingHandler and Events.DiplomacyIncomingDeal then Events.DiplomacyIncomingDeal.Remove(old.incomingHandler) end
end
local observer={{nonce=nonce,scope=scope,seq=0,diagnosticSeq=0,records={{}}}}
-- Diagnostic witnesses never feed choices or response completion. Keep only the
-- bound attempt's current session, including fields rejected by the strict filter.
local function boundTrade(sid)
 local valid,now=pcall(currentScope)
 local me=Game.GetLocalPlayer()
 local trade=observer.trade
 if not valid or now~=observer.scope or not trade or trade.finished or type(sid)~="number" or trade.sid~=sid then return nil end
 local info=DiplomacyManager.GetSessionInfo(sid)
 if not info or not ((info.FromPlayer==me and info.ToPlayer==trade.target) or (info.FromPlayer==trade.target and info.ToPlayer==me)) then return nil end
 return trade
end
observer.statementHandler=function(from,to,k)
 local diagnostic
 if type(k)=="table" then
  local trade=boundTrade(k.SessionID)
  if trade then
   observer.diagnosticSeq=observer.diagnosticSeq+1
   diagnostic={{seq=observer.diagnosticSeq,from=from,to=to,expected=trade.action,variant={{}},filter="UNKNOWN"}}
   for _,key in ipairs({{"SessionID","StatementType","StatementSubType","FromPlayer","RespondingToDealAction","DealAction","ResponseType"}}) do
    local value=k[key]; if type(value)=="number" or type(value)=="string" then diagnostic.variant[key]=value end
   end
   if not ((from==Game.GetLocalPlayer() and to==trade.target) or (from==trade.target and to==Game.GetLocalPlayer())) then diagnostic.filter="EVENT_PAIR_MISMATCH"
   elseif k.FromPlayer~=from then diagnostic.filter="FROM_PLAYER_MISMATCH"
   elseif trade.action==nil then diagnostic.filter="NO_EXPECTED_ACTION"
   elseif k.RespondingToDealAction~=trade.action then diagnostic.filter="RESPONDING_ACTION_MISMATCH"
   else diagnostic.filter="ACCEPTED_EVENT" end
   trade.latestEvent=diagnostic
  end
 end
 local valid,now=pcall(currentScope)
 local me=Game.GetLocalPlayer()
 if not valid or now~=observer.scope or (from~=me and to~=me) or type(k)~="table" or type(k.SessionID)~="number" or k.FromPlayer~=from then return end
 local other=from==me and to or from
 local sid=k.SessionID
 local info=DiplomacyManager.GetSessionInfo(sid)
 if not info or not ((info.FromPlayer==me and info.ToPlayer==other) or (info.FromPlayer==other and info.ToPlayer==me)) then return end
 local copy={{}}
 for _,key in ipairs({{"SessionID","StatementType","StatementSubType","FromPlayer","FromPlayerMood","Initiator","RespondingToDealAction","DealAction","ResponseType"}}) do
  local value=k[key]
  if type(value)=="number" or type(value)=="string" then copy[key]=value end
 end
 observer.seq=observer.seq+1
 local record={{seq=observer.seq,sid=sid,other=other,from=from,to=to,variant=copy}}
 observer.records[sid]=record
 if diagnostic then diagnostic.acceptedSeq=record.seq end
 local trade=observer.trade
 if trade and trade.sid==sid and trade.target==other and trade.action~=nil and
    copy.RespondingToDealAction==trade.action and record.seq>trade.afterSeq then
  trade.response=record
 end
end
observer.closedHandler=function(sid)
 local valid,now=pcall(currentScope)
 if not valid or now~=observer.scope then return end
 observer.seq=observer.seq+1
 if observer.records[sid] then observer.records[sid].closed=true end
 if observer.trade and observer.trade.sid==sid then observer.trade.closed=true end
end
observer.incomingHandler=function(from,to,action)
 local trade=observer.trade
 local me=Game.GetLocalPlayer()
 if not trade or not ((from==me and to==trade.target) or (from==trade.target and to==me)) then return end
 if boundTrade(trade.sid)~=trade or DiplomacyManager.FindOpenSessionID(me,trade.target)~=trade.sid then return end
 observer.diagnosticSeq=observer.diagnosticSeq+1
 trade.latestIncoming={{seq=observer.diagnosticSeq,from=from,to=to,action=action,sid=trade.sid}}
end
Events.DiplomacyStatement.Add(observer.statementHandler)
Events.DiplomacySessionClosed.Add(observer.closedHandler)
if Events.DiplomacyIncomingDeal then Events.DiplomacyIncomingDeal.Add(observer.incomingHandler) end
__civmcp_diplomacy=observer
print("DIPLO_OBSERVER|READY")
print("DIPLO_SCOPE|"..scope)
print("{SENTINEL}")
'''


def _observer(nonce: str) -> str:
    return _scope() + f'''
local observer=__civmcp_diplomacy
local ok,scope=pcall(currentScope)
if ContextPtr:GetID()~="DiplomacyActionView" or not ok or not observer or observer.nonce~={json.dumps(nonce)} or observer.scope~=scope then
 print("DIPLO_OBSERVER|UNKNOWN|SCOPE"); return
end
'''


def _choices(sid: int, target: int) -> str:
    return f'''
local sid,target={int(sid)},{int(target)}
local function readChoices()
 local me=Game.GetLocalPlayer()
 local record=observer.records[sid]
 if not record or record.closed or record.other~=target then return nil,"NO_CURRENT_STATEMENT" end
 if DiplomacyManager.FindOpenSessionID(me,target)~=sid then return nil,"SESSION_CHANGED" end
 local info=DiplomacyManager.GetSessionInfo(sid)
 if not info or not ((info.FromPlayer==me and info.ToPlayer==target) or (info.FromPlayer==target and info.ToPlayer==me)) then return nil,"PARTICIPANTS_UNKNOWN" end
 local conversation=ContextPtr:LookUpControl("/InGame/DiplomacyActionView/ConversationContainer")
 if ContextPtr:IsHidden() or not conversation or conversation:IsHidden() then return nil,"NO_CURRENT_CONVERSATION" end
 local k=record.variant
 local kind=DiplomacyManager.GetKeyName(k.StatementType)
 local subtype=DiplomacyManager.GetKeyName(k.StatementSubType)
 if type(kind)~="string" or type(subtype)~="string" or k.FromPlayer==nil or k.Initiator==nil then return nil,"STATEMENT_UNKNOWN" end
 local handler=GetStatementHandler(kind)
 local mood=GetStatementMood(k.FromPlayer,k.FromPlayerMood)
 local parsed=handler.ExtractStatement(handler,kind,subtype,k.FromPlayer,mood,k.Initiator)
 handler.RemoveInvalidSelections(parsed,me,target)
 local choices={{}}
 for _,selection in ipairs(parsed.Selections or {{}}) do
  if type(selection.Key)~="string" or not selection.Key:match("^CHOICE_[A-Z0-9_]+$") then return nil,"SELECTION_UNKNOWN" end
  if selection.IsDisabled==nil or selection.IsDisabled==false then table.insert(choices,selection.Key) end
 end
 return choices,record,handler
end
'''


def build_choices_query(nonce: str, sid: int, target: int) -> str:
    return _observer(nonce) + _choices(sid, target) + f'''
local ok,choices,record=pcall(readChoices)
if not ok or not choices then print("DIPLO_CHOICES|UNKNOWN"); return end
print("DIPLO_CHOICES|KNOWN|"..record.seq)
for _,key in ipairs(choices) do print("DIPLO_CHOICE|"..key) end
print("{SENTINEL}")
'''


def build_native_response(nonce: str, token: str, sid: int, target: int, seq: int, key: str) -> str:
    return _observer(nonce) + _choices(sid, target) + f'''
local ok,choices,record,handler=pcall(readChoices)
local key={json.dumps(key)}
if not ok or not choices or record.seq~={int(seq)} then print("DIPLO_RESPONSE|NOT_SUBMITTED|STATEMENT_CHANGED"); return end
local allowed=false
for _,candidate in ipairs(choices) do if candidate==key then allowed=true end end
if not allowed then print("DIPLO_RESPONSE|NOT_SUBMITTED|NOT_AVAILABLE"); return end
observer.response={{token={json.dumps(token)},sid=sid,seq=record.seq,key=key,done=false}}
handler.OnSelectionButtonClicked(key)
observer.response.done=true
print("DIPLO_RESPONSE|REQUESTED")
print("{SENTINEL}")
'''


def build_response_query(nonce: str, token: str, sid: int, target: int, seq: int) -> str:
    return _observer(nonce) + f'''
local response=observer.response
if not response or response.token~={json.dumps(token)} or response.sid~={int(sid)} then print("DIPLO_RESPONSE|UNKNOWN"); return end
if DiplomacyManager.IsSessionIDOpen({int(sid)})==false then print("DIPLO_RESPONSE|CLOSED"); return end
local record=observer.records[{int(sid)}]
if response.done and record and record.seq>{int(seq)} then print("DIPLO_RESPONSE|CONTINUES"); return end
print("DIPLO_RESPONSE|WAITING")
print("{SENTINEL}")
'''


def _trade(nonce: str, token: str) -> str:
    return _observer(nonce) + f'''
local trade=observer.trade
if not trade or trade.token~={json.dumps(token)} then print("TRADE|UNKNOWN|TOKEN"); return end
local me=Game.GetLocalPlayer()
local target=trade.target
'''


def build_trade_open(nonce: str, token: str, target: int) -> str:
    return _observer(nonce) + f'''
local me,target=Game.GetLocalPlayer(),{int(target)}
local function blocked()
 if not Players[target] or not Players[me]:GetDiplomacy():HasMet(target) or Players[me]:GetDiplomacy():IsAtWarWith(target) then return "TARGET" end
 local queued=DiplomacyManager.HasQueuedSession(me)
 if type(queued)~="boolean" then return "QUEUED_UNKNOWN" end
 if queued then return "QUEUED_SESSION" end
 for other=0,63 do
  if other~=me and Players[other] then
   local sid=DiplomacyManager.FindOpenSessionID(me,other)
   local pending=DealManager.HasPendingDeal(me,other)
   if (sid~=nil and type(sid)~="number") or type(pending)~="boolean" then return "SESSION_UNKNOWN" end
   if sid and sid>=0 then return "OPEN_SESSION" end
   if pending then return "PENDING_DEAL" end
  end
 end
end
local ok,reason=pcall(blocked)
if not ok or reason then print("TRADE|NOT_SUBMITTED|"..tostring(reason or "UNKNOWN")); return end
observer.trade={{token={json.dumps(token)},target=target,stage="OPEN_ENTERED",afterSeq=observer.seq}}
DealManager.ClearWorkingDeal(DealDirection.OUTGOING,me,target)
local deal=DealManager.GetWorkingDeal(DealDirection.OUTGOING,me,target)
if not deal or deal:GetItemCount()~=0 then observer.trade.finished=true; print("TRADE|NOT_SUBMITTED|EMPTY_DEAL_UNKNOWN"); return end
DiplomacyManager.RequestSession(me,target,"MAKE_DEAL")
observer.trade.stage="OPEN_REQUESTED"
print("TRADE|OPEN_REQUESTED")
'''


def build_trade_session(nonce: str, token: str) -> str:
    return _trade(nonce, token) + '''
local sid=DiplomacyManager.FindOpenSessionID(me,target)
if sid==nil or sid<0 then
 print(trade.closed and "TRADE_SESSION|CLOSED" or "TRADE_SESSION|WAITING"); return
end
local info=DiplomacyManager.GetSessionInfo(sid)
if not info or info.FromPlayer~=me or info.ToPlayer~=target then print("TRADE_SESSION|UNKNOWN"); return end
if trade.sid and trade.sid~=sid then print("TRADE_SESSION|UNKNOWN"); return end
trade.sid=sid
print("TRADE_SESSION|BOUND|"..sid)
'''


def _fingerprint() -> str:
    return '''
local function fingerprint(deal)
 if not deal or type(deal:GetItemCount())~="number" then return nil end
 local rows={}
 for item in deal:Items() do
  local values={item:GetFromPlayerID(),item:GetType(),item:GetValueType(),item:GetSubType(),item:GetAmount(),item:GetDuration()}
  for index=1,6 do if values[index]==nil then values[index]="?" else values[index]=tostring(values[index]) end end
  table.insert(rows,table.concat(values,","))
 end
 table.sort(rows); return table.concat(rows,";")
end
'''


def _deal_guard(scope: str, target: int, sid: int) -> str:
    return _scope() + f'''
local me,target,sid=Game.GetLocalPlayer(),{int(target)},{int(sid)}
local function ready()
 if currentScope()~={json.dumps(scope)} or ContextPtr:GetID()~="DiplomacyDealView" or ContextPtr:IsHidden() then return false end
 if ms_OtherPlayerID~=target or ms_InitiatedByPlayerID~=me or not g_LocalPlayer or g_LocalPlayer:GetID()~=me or not g_OtherPlayer or g_OtherPlayer:GetID()~=target then return false end
 if DiplomacyManager.FindOpenSessionID(me,target)~=sid then return false end
 local info=DiplomacyManager.GetSessionInfo(sid)
 if not info or info.FromPlayer~=me or info.ToPlayer~=target or DiplomacyManager.HasQueuedSession(me)~=false then return false end
 if DealManager.HasPendingDeal(me,target)~=false then return false end
 return true
end
local ok,isReady=pcall(ready)
if not ok or not isReady then print("TRADE_DEAL|NOT_READY"); return end
'''


def build_trade_deal_ready(scope: str, target: int, sid: int) -> str:
    return _deal_guard(scope, target, sid) + '''
local deal=DealManager.GetWorkingDeal(DealDirection.OUTGOING,me,target)
if deal and deal:GetItemCount()==0 then print("TRADE_DEAL|READY") else print("TRADE_DEAL|UNKNOWN|NOT_EMPTY") end
'''


def build_trade_arm_action(nonce: str, token: str, action: str) -> str:
    return _trade(nonce, token) + f'''
if not trade.sid or DiplomacyManager.FindOpenSessionID(me,target)~=trade.sid then print("TRADE|UNKNOWN|SESSION"); return end
trade.action=DealProposalAction.{action}
trade.afterSeq=observer.seq
trade.response=nil
print("TRADE|ARMED")
'''


def build_trade_send(scope: str, target: int, sid: int, token: str, offer: list[dict], request: list[dict], action: str) -> str:
    from civ_mcp.lua.diplomacy import _lua_deal_item
    items = '\n'.join([*(_lua_deal_item("me", item) for item in offer), *(_lua_deal_item("target", item) for item in request)])
    return _deal_guard(scope, target, sid) + _fingerprint() + f'''
local deal=DealManager.GetWorkingDeal(DealDirection.OUTGOING,me,target)
if not deal or deal:GetItemCount()~=0 then print("TRADE|NOT_SUBMITTED|NOT_EMPTY"); return end
__civmcp_trade_deal={{token={json.dumps(token)},sid=sid,target=target,sent=false}}
local prepared=pcall(function()
{items}
end)
local receipt=__civmcp_trade_deal
receipt.original=fingerprint(deal)
if not prepared or deal:GetItemCount()~={len(offer)+len(request)} then
 receipt.failed=true
 print("TRADE_PREPARE|NOT_SUBMITTED|TERMS_UNAVAILABLE"); return
end
if not receipt.original then receipt.failed=true; print("TRADE_PREPARE|NOT_SUBMITTED|TERMS_UNKNOWN"); return end
receipt.sent=true
DealManager.SendWorkingDeal(DealProposalAction.{action},me,target)
print("TRADE|SENT")
print("TRADE_ORIGINAL|"..__civmcp_trade_deal.original)
'''


def build_trade_response(nonce: str, token: str) -> str:
    return _trade(nonce, token) + _fingerprint() + '''
local record=trade.response
if not record then print("TRADE_RESPONSE|WAITING"); return end
if observer.records[trade.sid]~=record or record.other~=target or record.sid~=trade.sid or record.variant.RespondingToDealAction~=trade.action then print("TRADE_RESPONSE|UNKNOWN"); return end
if DealManager.HasPendingDeal(me,target)~=false then print("TRADE_RESPONSE|WAITING"); return end
local action="UNKNOWN"
for key,value in pairs(DealProposalAction) do if value==record.variant.DealAction then action=key end end
local subtype=DiplomacyManager.GetKeyName(record.variant.StatementSubType)
local deal=DealManager.GetWorkingDeal(DealDirection.INCOMING,me,target)
local signature=fingerprint(deal)
if signature==nil then print("TRADE_RESPONSE|UNKNOWN"); return end
print("TRADE_RESPONSE|KNOWN|"..action.."|"..tostring(subtype).."|"..record.seq)
print("TRADE_COUNTER|"..signature)
local name=Locale.Lookup(PlayerConfigurations[target]:GetCivilizationShortDescription())
print("CIV|"..target.."|"..name:gsub("|","/"))
print("AI_COUNTER")
for item in deal:Items() do
 local itemType=item:GetType()
 local kind="UNKNOWN"
 if itemType==DealItemTypes.GOLD then kind="GOLD"
 elseif itemType==DealItemTypes.RESOURCES then kind="RESOURCE"
 elseif itemType==DealItemTypes.AGREEMENTS then kind="AGREEMENT"
 elseif itemType==DealItemTypes.FAVOR then kind="FAVOR"
 elseif itemType==DealItemTypes.CITIES then kind="CITY"
 elseif itemType==DealItemTypes.GREATWORK then kind="GREAT_WORK" end
 local value,subtype="",""
 pcall(function() value=tostring(item:GetValueTypeID() or "") end)
 pcall(function() subtype=tostring(item:GetSubTypeID() or "") end)
 print("ITEM|"..(item:GetFromPlayerID()==me and "US" or "THEM").."|"..kind.."|"..(item:GetAmount() or 0).."|"..(item:GetDuration() or 0).."|"..value.."|"..subtype)
end
'''


def build_trade_close_preview(scope: str, target: int, sid: int, token: str) -> str:
    return _deal_guard(scope, target, sid) + f'''
local receipt=__civmcp_trade_deal
if not receipt or receipt.token~={json.dumps(token)} or not receipt.sent then print("TRADE_EXIT|NOT_SUBMITTED"); return end
if type(OnRefuseDeal)~="function" then print("TRADE_EXIT|NOT_SUBMITTED"); return end
receipt.exitEntered=true
-- Local-initiated branch is the native exit action, not an AI rejection.
OnRefuseDeal()
receipt.exitReturned=true
print("TRADE_EXIT|REQUESTED")
'''


def build_trade_abort_prepare(scope: str, target: int, sid: int, token: str) -> str:
    return _deal_guard(scope, target, sid) + _fingerprint() + f'''
local receipt=__civmcp_trade_deal
local deal=DealManager.GetWorkingDeal(DealDirection.OUTGOING,me,target)
if not receipt or receipt.token~={json.dumps(token)} or receipt.sid~=sid or receipt.target~=target or receipt.sent~=false or receipt.failed~=true or not receipt.original or fingerprint(deal)~=receipt.original then print("TRADE_EXIT|NOT_SUBMITTED"); return end
if type(OnRefuseDeal)~="function" then print("TRADE_EXIT|NOT_SUBMITTED"); return end
receipt.exitEntered=true
-- No EQUALIZE/PROPOSED was entered. Exit only this still-matching local session;
-- keep partial terms untouched rather than clear an unowned/replaced deal.
OnRefuseDeal()
receipt.exitReturned=true
print("TRADE_EXIT|REQUESTED")
'''


def build_trade_closed(nonce: str, token: str, finish: bool = False) -> str:
    return _trade(nonce, token) + f'''
if not trade.sid or not trade.closed or DiplomacyManager.FindOpenSessionID(me,target)==trade.sid or DiplomacyManager.HasQueuedSession(me)~=false or DealManager.HasPendingDeal(me,target)~=false then
 print("TRADE_CLOSED|WAITING"); return
end
{'trade.finished=true' if finish else ''}
print("TRADE_CLOSED|CONFIRMED")
'''


def build_trade_deal_receipt(scope: str, token: str) -> str:
    return _scope() + f'''
if currentScope()~={json.dumps(scope)} or ContextPtr:GetID()~="DiplomacyDealView" then print("TRADE_RECEIPT|UNKNOWN"); return end
local receipt=__civmcp_trade_deal
if not receipt or receipt.token~={json.dumps(token)} then print("TRADE_RECEIPT|UNKNOWN"); return end
if receipt.sent~=true and receipt.failed~=true then print("TRADE_RECEIPT|UNKNOWN"); return end
if receipt.failed and receipt.sent==false then print("TRADE_PREPARE|NOT_SUBMITTED|TERMS_UNAVAILABLE") end
if receipt.original then print("TRADE_ORIGINAL|"..receipt.original) end
print(ContextPtr:IsHidden() and "TRADE_VIEW|HIDDEN" or "TRADE_VIEW|VISIBLE")
'''


def build_trade_accept(scope: str, target: int, sid: int, token: str, signature: str) -> str:
    return _deal_guard(scope, target, sid) + _fingerprint() + f'''
local receipt=__civmcp_trade_deal
local incoming=DealManager.GetWorkingDeal(DealDirection.INCOMING,me,target)
local outgoing=DealManager.GetWorkingDeal(DealDirection.OUTGOING,me,target)
if not receipt or receipt.token~={json.dumps(token)} or fingerprint(incoming)~={json.dumps(signature)} or fingerprint(outgoing)~={json.dumps(signature)} or DealManager.AreWorkingDealsEqual(me,target)~=true then print("TRADE_ACCEPT|NOT_SUBMITTED|TERMS_CHANGED"); return end
if not Controls.AcceptDeal or Controls.AcceptDeal:IsHidden() or Controls.AcceptDeal:IsDisabled() then print("TRADE_ACCEPT|NOT_SUBMITTED|NATIVE_NOT_READY"); return end
if type(OnProposeOrAcceptDeal)~="function" then print("TRADE_ACCEPT|NOT_SUBMITTED|CALLBACK_UNKNOWN"); return end
receipt.acceptEntered=true
OnProposeOrAcceptDeal()
receipt.acceptReturned=true
print("TRADE_ACCEPT|REQUESTED")
'''


def build_trade_finish(nonce: str, token: str) -> str:
    return _trade(nonce, token) + '''
if not trade.closed then print("TRADE_FINISH|UNKNOWN"); return end
trade.finished=true
print("TRADE_FINISH|CONFIRMED")
'''


def build_trade_diagnostics(nonce: str, scope: str, token: str, sid: int | None) -> str:
    return _scope() + f'''
local function emit(text) print("DIPLO_DIAGNOSTIC|"..text) end
local function value(v)
 if type(v)=="number" or type(v)=="string" then return tostring(v):gsub("[|\\r\\n]","/") end
 return "unknown"
end
local function action(v)
 if type(v)~="number" then return value(v) end
 for name,number in pairs(DealProposalAction) do if number==v then return name.."("..v..")" end end
 return "unknown("..v..")"
end
local function key(v)
 local ok,name=pcall(DiplomacyManager.GetKeyName,v)
 return ok and type(name)=="string" and value(name) or value(v)
end
local observer=__civmcp_diplomacy
local ok,scope=pcall(currentScope)
if ContextPtr:GetID()~="DiplomacyActionView" or not observer or observer.nonce~={json.dumps(nonce)} then emit("diplomacy_observation: owner=UNKNOWN reason=OWNER_OR_CONTEXT"); return end
if not ok or scope~={json.dumps(scope)} or observer.scope~=scope then emit("diplomacy_observation: owner=MATCHED world=UNKNOWN reason=WORLD_CHANGED"); return end
local trade=observer.trade
if not trade or trade.token~={json.dumps(token)} then emit("diplomacy_observation: owner=MATCHED world=MATCHED session=UNKNOWN reason=ATTEMPT_CHANGED"); return end
local sid=trade.sid
local me=Game.GetLocalPlayer()
local info=sid and DiplomacyManager.GetSessionInfo(sid)
if type(sid)~="number" or sid~={"nil" if sid is None else int(sid)} or DiplomacyManager.FindOpenSessionID(me,trade.target)~=sid or not info or not ((info.FromPlayer==me and info.ToPlayer==trade.target) or (info.FromPlayer==trade.target and info.ToPlayer==me)) then emit("diplomacy_observation: owner=MATCHED world=MATCHED session=UNKNOWN reason=SESSION_NOT_CURRENT"); return end
emit("diplomacy_observation: owner=MATCHED world=MATCHED session=MATCHED nonce="..value(observer.nonce).." world="..value(scope).." session_id="..value(sid).." expected_action="..action(trade.action).." after_seq="..value(trade.afterSeq).." response_recorded="..tostring(trade.response~=nil).." response_current="..tostring(trade.response~=nil and observer.records[sid]==trade.response).." accepted_seq="..value(trade.response and trade.response.seq))
local event=trade.latestEvent
if not event then emit("diplomacy_statement: statement=NONE")
else
 local k=event.variant
 emit("diplomacy_statement: event_seq="..value(event.seq).." statement_seq="..value(event.acceptedSeq).." filter="..value(event.filter).." expected_at_event="..action(event.expected).." event_from="..value(event.from).." event_to="..value(event.to).." k_from="..value(k.FromPlayer).." session_id="..value(k.SessionID).." subtype="..key(k.StatementSubType).." subtype_raw="..value(k.StatementSubType).." responding_action="..action(k.RespondingToDealAction).." deal_action="..action(k.DealAction).." response_type="..value(k.ResponseType))
end
local accepted=observer.records[sid]
if accepted then
 local k=accepted.variant
 emit("diplomacy_accepted_statement: statement_seq="..value(accepted.seq).." subtype="..key(k.StatementSubType).." responding_action="..action(k.RespondingToDealAction).." deal_action="..action(k.DealAction).." response_type="..value(k.ResponseType))
end
local incoming=trade.latestIncoming
if not incoming then emit("diplomacy_incoming: event=NONE")
else emit("diplomacy_incoming: event_seq="..value(incoming.seq).." event_from="..value(incoming.from).." event_to="..value(incoming.to).." session_id="..value(incoming.sid).." deal_action="..action(incoming.action).." diagnostic_only=true") end
print("{SENTINEL}")
'''
