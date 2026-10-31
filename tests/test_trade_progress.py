"""Real GameState and Lua 5.1 with deferred native diplomacy event delivery."""
import asyncio
from copy import deepcopy
import pytest
from test_diplomacy_context_lua import DiplomacyConn
from civ_mcp.game_state import GameState


class AsyncDiplomacyConn(DiplomacyConn):
    def __init__(self):
        super().__init__()
        self.setup('''
GameConfiguration={GetValue=function() return 7 end}
PlayerConfigurations[0]={GetCivilizationTypeName=function() return "CIVILIZATION_SUMERIA" end}
Game.GetCurrentGameTurn=function() return 78 end
Players[1].IsHuman=function() return false end
Players[2].IsHuman=function() return false end
local function event()
 local listeners={}
 return {Add=function(fn) listeners[fn]=true end, Remove=function(fn) listeners[fn]=nil end,
         Emit=function(...) for fn in pairs(listeners) do fn(...) end end}
end
Events.DiplomacyStatement=event(); Events.DiplomacySessionClosed=event(); Events.DiplomacyIncomingDeal=event()
DiplomacyManager.GetKeyName=function(key) return key end
DiplomacyManager.HasQueuedSession=function() return false end
DealManager.HasPendingDeal=function() return false end
GetStatementMood=function(_, mood) return mood end
native_selections={{Key="CHOICE_POSITIVE"},{Key="CHOICE_NEGATIVE"}}
local handler={ExtractStatement=function() return {Selections=native_selections} end,
 RemoveInvalidSelections=function() end}
handler.OnSelectionButtonClicked=function(key)
 if key=="CHOICE_EXIT" then DiplomacyManager.CloseSession(71); infos[71]=nil
 else DiplomacyManager.AddResponse(71,0,key) end
end
GetStatementHandler=function() return handler end
local original=ContextPtr
ContextPtr.GetID=function() return "DiplomacyActionView" end
ContextPtr.IsHidden=function() return controls["/InGame/DiplomacyActionView"].hidden end
''')

    async def execute_in_named_state(self, name, source, timeout=5):
        assert name in {"DiplomacyActionView", "DiplomacyDealView"}
        return await self.execute_write(source)

    def statement(self, subtype="INITIAL"):
        self.setup('''Events.DiplomacyStatement.Emit(1,0,{SessionID=71,StatementType="MAKE_DEAL",
 StatementSubType="''' + subtype + '''",FromPlayer=1,FromPlayerMood=0,Initiator=0,
 RespondingToDealAction=0,DealAction=0,ResponseType=1})''')


def test_loaded_dialogue_without_statement_witness_keeps_choices_unknown():
    conn = AsyncDiplomacyConn()
    state = GameState(conn)
    context = asyncio.run(state.get_diplomacy_sessions())
    assert context.sessions[0].choice_status == "UNKNOWN"
    result = asyncio.run(state.diplomacy_respond(1, "POSITIVE"))
    assert "UNKNOWN" in result
    assert list(conn.lua.globals().mutations.values()) == []


def test_natural_statement_exposes_native_keys_and_exit_only_rejects_positive():
    conn = AsyncDiplomacyConn()
    state = GameState(conn)
    asyncio.run(state.get_diplomacy_sessions())  # Own observer before future events.
    conn.setup('native_selections={{Key="CHOICE_EXIT"}}')
    conn.statement("AI_ACCEPT_DEAL")
    context = asyncio.run(state.get_diplomacy_sessions())
    assert context.sessions[0].choice_status == "KNOWN"
    assert context.sessions[0].choice_keys == ["CHOICE_EXIT"]
    assert "NOT_AVAILABLE" in asyncio.run(state.diplomacy_respond(1, "POSITIVE"))
    assert list(conn.lua.globals().mutations.values()) == []
    assert asyncio.run(state.diplomacy_respond(1, "EXIT")) == "OK:RESPONDED|SESSION_CLOSED"
    assert list(conn.lua.globals().mutations.values()) == ["close"]


class TradeConn(AsyncDiplomacyConn):
    def __init__(self):
        super().__init__()
        self.queue = []
        self.deliver_events = True
        self.lua.globals().queue_open = lambda: self.queue.append(self.open_event())
        self.lua.globals().queue_response = lambda action: self.queue.append(self.response_event(int(action)))
        self.lua.globals().queue_close = lambda sid: self.queue.append(f'sessions={{}}; controls["/InGame/DiplomacyDealView"].hidden=true; Events.DiplomacySessionClosed.Emit({int(sid)})')
        self.setup('''
sessions={}; infos={}; requests=0; inspect_count=0; close_callbacks=0; sent_actions={}; pending=false
working_outgoing={}; working_incoming={}
controls["/InGame/DiplomacyActionView"].hidden=true
local function item(kind,owner)
 local value={kind=kind,owner=owner,amount=0,duration=0,value=-1,subtype=-1}
 value.GetFromPlayerID=function(self) return self.owner end; value.GetType=function(self) return self.kind end
 value.GetValueType=function(self) return self.value end; value.GetSubType=function(self) return self.subtype end
 value.GetAmount=function(self) return self.amount end; value.GetDuration=function(self) return self.duration end
 value.GetValueTypeID=function() return "" end; value.GetSubTypeID=function() return "" end
 value.SetAmount=function(self,n) self.amount=n end; value.SetDuration=function(self,n) self.duration=n end
 value.SetValueType=function(self,n) self.value=n end; value.SetSubType=function(self,n) self.subtype=n end
 value.SetParameterValue=function(self,key,n) self.parameters=self.parameters or {};self.parameters[key]=n end
 value.GetParameterValue=function(self,key) return self.parameters and self.parameters[key] end
 return value
end
function dealFor(direction)
 local function entries() return direction==DealDirection.OUTGOING and working_outgoing or working_incoming end
 return {GetItemCount=function() return #entries() end, IsValid=function() return true end, Validate=function() return 0 end,
 Items=function() local n=0; return function() n=n+1; return entries()[n] end end,
 AddItemOfType=function(_,kind,owner) local value=item(kind,owner); table.insert(entries(),value); return value end}
end
DealManager.GetWorkingDeal=function(direction) return dealFor(direction) end
DealManager.ClearWorkingDeal=function(direction) if direction==DealDirection.OUTGOING then working_outgoing={} end end
DealManager.HasPendingDeal=function() return pending end
DealManager.SendWorkingDeal=function(action) table.insert(sent_actions,action); if action==9 then inspect_count=inspect_count+1 end; queue_response(action) end
DealManager.AreWorkingDealsEqual=function() return true end
DealProposalAction={ACCEPTED=1,REJECTED=2,EQUALIZE=3,PROPOSED=4,ADJUSTED=5,PENDING=6,EQUALIZE_FAILED=7,INSPECT=9}
DealValidationResult={VALID=0}
DiplomacyManager.RequestSession=function() requests=requests+1; queue_open() end
DiplomacyManager.CloseSession=function(sid) table.insert(mutations,"close"); queue_close(sid) end
ContextPtr.IsHidden=function() return controls["/InGame/"..context_name].hidden end
ContextPtr.GetID=function() return context_name end
controls["/InGame/DiplomacyDealView"].hidden=true
UI={IsProcessingMessages=function() return false end}
Controls={AcceptDeal={IsHidden=function() return false end,IsDisabled=function() return false end}}
function Close() close_callbacks=close_callbacks+1; controls["/InGame/DiplomacyActionView"].hidden=true end
function OnRefuseDeal() DiplomacyManager.CloseSession(71) end
function OnProposeOrAcceptDeal() DealManager.SendWorkingDeal(DealProposalAction.ACCEPTED,0,1) end
function nativeAutoInspect()
 local pDeal=DealManager.GetWorkingDeal(DealDirection.OUTGOING,0,1)
 pDeal:Validate()
 if pDeal:IsValid() and not DealManager.HasPendingDeal(0,1) and pDeal:GetItemCount()>0 then
  DealManager.SendWorkingDeal(DealProposalAction.INSPECT,0,1)
 end
end
''')

    def open_event(self):
        return '''sessions={[1]=71}; infos={[71]={FromPlayer=0,ToPlayer=1}};
ms_OtherPlayerID=1; ms_InitiatedByPlayerID=0; g_LocalPlayer={GetID=function() return 0 end};g_OtherPlayer={GetID=function() return 1 end};
controls["/InGame/DiplomacyActionView"].hidden=false;controls["/InGame/DiplomacyDealView"].hidden=false;
controls["/InGame/DiplomacyActionView/ConversationContainer"].hidden=true;nativeAutoInspect();
Events.DiplomacyStatement.Emit(0,1,{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="NONE",FromPlayer=0,FromPlayerMood=0,Initiator=0,RespondingToDealAction=0,DealAction=0,ResponseType=1})'''

    def response_is_final(self, action):
        return action in (1, 4)

    def response_event(self, action):
        final = self.response_is_final(action)
        subtype="HUMAN_ACCEPT_DEAL" if action==1 else "AI_ACCEPT_DEAL"
        responding=0 if action==1 else action
        incoming_action=0 if action==1 else 1
        return '''working_incoming=working_outgoing;
''' + ('controls["/InGame/DiplomacyDealView"].hidden=true; controls["/InGame/DiplomacyActionView/ConversationContainer"].hidden=false; native_selections={{Key="CHOICE_EXIT"}};\n' if final else '') + f'''Events.DiplomacyStatement.Emit(1,0,{{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="{subtype}",FromPlayer=1,FromPlayerMood=0,Initiator=0,RespondingToDealAction={responding},DealAction={incoming_action},ResponseType=1}})'''

    async def execute_in_named_state(self, name, source, timeout=5):
        if self.deliver_events and self.queue:
            self.setup(self.queue.pop(0))
        self.lua.globals().context_name = name
        return await super().execute_in_named_state(name, source, timeout)


@pytest.fixture
def quick_trade(monkeypatch):
    from civ_mcp import diplomacy
    monkeypatch.setattr(diplomacy, "TRADE_CONFIRM_SECONDS", .04)
    monkeypatch.setattr(diplomacy, "TRADE_POLL_SECONDS", .001)


def test_different_target_cannot_receive_a_pending_trade_result(quick_trade):
    class CountedConn(TradeConn):
        calls=0
        async def execute_in_named_state(self,name,source,timeout=5):
            self.calls+=1
            return await super().execute_in_named_state(name,source,timeout)

    conn=CountedConn(); state=GameState(conn)
    offer=[{"type":"GOLD","amount":20}]
    assert "ACCEPTED|" in asyncio.run(state.propose_trade(1,offer,[]))
    attempt=state._diplomacy_progress.trade
    before=deepcopy(attempt)
    calls=conn.calls
    result=asyncio.run(state.propose_trade(2,offer,[]))
    assert "PENDING_TRADE_MISMATCH" in result and "ACCEPTED" not in result
    assert "player 1" in result and "propose_trade" in result
    assert "new request not submitted" in result.lower()
    assert conn.calls==calls
    assert state._diplomacy_progress.trade is attempt and attempt==before
    assert state.diplomacy_pending


@pytest.mark.parametrize("phase", ["DIALOGUE", "CLEANUP"])
@pytest.mark.parametrize("change", ["target", "mode", "offer", "request"])
def test_pending_trade_requires_the_original_structured_request(phase,change,quick_trade):
    class CountedConn(TradeConn):
        calls=0
        lost_close=False
        async def execute_in_named_state(self,name,source,timeout=5):
            self.calls+=1
            rows=await super().execute_in_named_state(name,source,timeout)
            if phase=="CLEANUP" and "DIPLOMACY_NATIVE_CLOSE" in source and not self.lost_close:
                self.lost_close=True
                raise TimeoutError("native close reply lost")
            return rows
    async def scenario():
        conn=CountedConn(); state=GameState(conn)
        offer=[{"type":"FAVOR","amount":20}]
        request=[{"type":"GOLD","amount":10,"duration":30}]
        assert "ACCEPTED|" in await state.propose_trade(1,offer,request)
        if phase=="CLEANUP":
            assert "SESSION_CLOSED" in await state.diplomacy_respond(1,"EXIT")
            assert "cleanup unknown" in await state.dismiss_popup()
        attempt=state._diplomacy_progress.trade
        assert attempt.phase==phase
        before=deepcopy(attempt)
        calls=conn.calls
        cleanup_pending=state._diplomacy_cleanup.pending
        target=2 if change=="target" else 1
        changed_offer=[{"type":"FAVOR","amount":21}] if change=="offer" else deepcopy(offer)
        changed_request=[{"type":"GOLD","amount":11,"duration":30}] if change=="request" else deepcopy(request)
        operation=state.test_trade if change=="mode" else state.propose_trade
        result=await operation(target,changed_offer,changed_request)
        assert "PENDING_TRADE_MISMATCH" in result and "ACCEPTED" not in result
        assert "player 1" in result and "propose_trade" in result
        assert "new request not submitted" in result.lower()
        assert conn.calls==calls
        assert state._diplomacy_progress.trade is attempt and attempt==before
        assert state._diplomacy_cleanup.pending==cleanup_pending
        assert state.diplomacy_pending
    asyncio.run(scenario())


def test_original_prepared_copy_confirms_and_new_request_starts_only_after_retirement(quick_trade):
    async def scenario():
        conn=TradeConn(); state=GameState(conn)
        conn.deliver_events=False
        offer=[{"type":"GOLD","amount":20}]
        assert "OUTCOME_UNKNOWN" in await state.propose_trade(1,offer,[])
        offer[0]["amount"]=99
        assert "PENDING_TRADE_MISMATCH" in await state.propose_trade(1,offer,[])
        assert state._diplomacy_progress.trade.offer==[{"type":"GOLD","amount":20}]
        conn.deliver_events=True
        # Dict key order is not a term change; the prepared structured values match.
        original=[{"amount":20,"type":"GOLD"}]
        assert "ACCEPTED|" in await state.propose_trade(1,original,[])
        assert conn.lua.globals().requests==1
        assert "SESSION_CLOSED" in await state.diplomacy_respond(1,"EXIT")
        assert "cleanup confirmed" in await state.propose_trade(1,original,[])
        assert not state.diplomacy_pending
        assert "ACCEPTED|" in await state.propose_trade(1,[{"type":"GOLD","amount":30}],[])
        assert conn.lua.globals().requests==2
        assert list(conn.lua.globals().sent_actions.values())==[9,1,9,1]
    asyncio.run(scenario())


def test_closed_dialogue_reports_retained_trade_confirmation_without_resending(quick_trade):
    class CountedConn(TradeConn):
        calls = 0
        async def execute_in_named_state(self, name, source, timeout=5):
            self.calls += 1
            return await super().execute_in_named_state(name, source, timeout)

    async def scenario():
        conn=CountedConn(); state=GameState(conn)
        offer=[{"type":"GOLD","amount":14}]
        request=[{"type":"FAVOR","amount":1}]
        assert "ACCEPTED|" in await state.propose_trade(1,offer,request)
        submitted=list(conn.lua.globals().sent_actions.values())
        assert submitted==[9,1] and conn.lua.globals().requests==1
        attempt=state._diplomacy_progress.trade
        result=await state.diplomacy_respond(1,"EXIT")
        assert result.startswith("OK:RESPONDED|SESSION_CLOSED")
        assert "TRADE_CONFIRMATION_PENDING|player:1|phase:DIALOGUE|mode:send" in result
        assert "trade propose 1 --mode send" in result
        assert "same offer and request" in result and "does not resend" in result
        assert state._diplomacy_progress.trade is attempt and state.diplomacy_pending
        assert state._diplomacy_progress.response is None
        assert list(conn.lua.globals().mutations.values())==["close"]
        assert list(conn.lua.globals().sent_actions.values())==submitted
        assert conn.lua.globals().requests==1
        calls=conn.calls
        mismatch=await state.propose_trade(1,[{"type":"GOLD","amount":15}],request)
        assert "PENDING_TRADE_MISMATCH" in mismatch and conn.calls==calls
        assert "cleanup confirmed" in await state.propose_trade(1,offer,request)
        assert not state.diplomacy_pending
        assert list(conn.lua.globals().sent_actions.values())==submitted
        assert conn.lua.globals().requests==1
        assert list(conn.lua.globals().mutations.values())==["close"]
    asyncio.run(scenario())


def test_closed_dialogue_hint_uses_the_retained_preview_mode():
    from civ_mcp.diplomacy import TradeAttempt

    conn=AsyncDiplomacyConn(); state=GameState(conn)
    asyncio.run(state.get_diplomacy_sessions())
    conn.setup('native_selections={{Key="CHOICE_EXIT"}}')
    conn.statement("AI_ACCEPT_DEAL")
    # Fixed pending-memory fixture: a preview still awaiting native cleanup.
    attempt=TradeAttempt("retained-preview",1,True,[],[],phase="EXIT_ENTERED",sid=71)
    state._diplomacy_progress.trade=attempt
    result=asyncio.run(state.diplomacy_respond(1,"EXIT"))
    assert "TRADE_CONFIRMATION_PENDING|player:1|phase:EXIT_ENTERED|mode:test" in result
    assert "trade propose 1 --mode test" in result
    assert state._diplomacy_progress.trade is attempt and state.diplomacy_pending
    assert state._diplomacy_progress.response is None
    assert list(conn.lua.globals().mutations.values())==["close"]


def test_mcp_new_trade_cannot_claim_the_previous_receipt_or_release_its_gate(monkeypatch,tmp_path,quick_trade):
    import anyio
    from mcp import ClientSession
    from civ_mcp import server
    from civ_mcp.logger import GameLogger

    class CountedConn(TradeConn):
        calls=0
        async def execute_in_named_state(self,name,source,timeout=5):
            self.calls+=1
            return await super().execute_in_named_state(name,source,timeout)
        async def disconnect(self):
            pass

    conn=CountedConn()
    monkeypatch.setattr(server,"GameConnection",lambda:conn)
    log_path=tmp_path/"trade-identity.jsonl"
    monkeypatch.setattr(server,"GameLogger",lambda:GameLogger(log_path))

    async def scenario():
        client_write,server_read=anyio.create_memory_object_stream(10)
        server_write,client_read=anyio.create_memory_object_stream(10)
        lowlevel=server.mcp._mcp_server
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(lowlevel.run,server_read,server_write,lowlevel.create_initialization_options())
            async with ClientSession(client_read,client_write) as session:
                await session.initialize()
                original={"other_player_id":1,"offer_gold":20,"mode":"send"}
                accepted=await session.call_tool("propose_trade",original)
                assert "ACCEPTED|" in accepted.content[0].text
                calls=conn.calls
                mismatch=await session.call_tool("propose_trade",{"other_player_id":2,"offer_gold":30,"mode":"test"})
                assert "PENDING_TRADE_MISMATCH" in mismatch.content[0].text
                assert "ACCEPTED" not in mismatch.content[0].text and conn.calls==calls
                blocked=await session.call_tool("save_game",{"save_name":"identity_guard"})
                assert "OUTCOME_UNKNOWN" in blocked.content[0].text and conn.calls==calls
                repeated=await session.call_tool("propose_trade",original)
                assert "ACCEPTED|" in repeated.content[0].text
                assert conn.lua.globals().requests==1
            tasks.cancel_scope.cancel()
    anyio.run(scenario)


def test_preview_waits_for_session_onshow_and_own_response_before_native_exit(quick_trade):
    conn = TradeConn()
    state = GameState(conn)
    result = asyncio.run(state.test_trade(1, [{"type":"FAVOR","amount":20}], [{"type":"GOLD","amount":10,"duration":30}]))
    assert "Trade test" in result and "cleanup confirmed" in result
    assert conn.lua.globals().requests == 1
    assert conn.lua.globals().inspect_count == 0
    assert list(conn.lua.globals().sent_actions.values()) == [3]
    assert list(conn.lua.globals().mutations.values()) == ["close"]
    assert not state.diplomacy_pending


def test_missing_asynchronous_reply_is_pending_not_rejection_and_never_resent(quick_trade):
    conn = TradeConn()
    conn.deliver_events = False
    state = GameState(conn)
    first = asyncio.run(state.propose_trade(1, [{"type":"FAVOR","amount":20}], [{"type":"GOLD","amount":10,"duration":30}]))
    second = asyncio.run(state.propose_trade(1, [{"type":"FAVOR","amount":20}], [{"type":"GOLD","amount":10,"duration":30}]))
    assert "OUTCOME_UNKNOWN" in first and "REJECTED" not in first
    assert "OUTCOME_UNKNOWN" in second
    assert state.diplomacy_pending and conn.lua.globals().requests == 1
    assert list(conn.lua.globals().sent_actions.values()) == []


def test_send_waits_for_current_native_response_and_only_accepts_original_terms(quick_trade):
    conn = TradeConn()
    state = GameState(conn)
    result = asyncio.run(state.propose_trade(1, [{"type":"FAVOR","amount":20}], [{"type":"GOLD","amount":10,"duration":30}]))
    assert "ACCEPTED|" in result and "REJECTED" not in result
    assert list(conn.lua.globals().sent_actions.values()) == [9, 1]
    assert conn.lua.globals().requests == 1 and state.diplomacy_pending
    context = asyncio.run(state.get_diplomacy_sessions())
    assert context.sessions[0].choice_keys == ["CHOICE_EXIT"]
    assert "NOT_AVAILABLE" in asyncio.run(state.diplomacy_respond(1, "POSITIVE"))
    assert "SESSION_CLOSED" in asyncio.run(state.diplomacy_respond(1, "EXIT"))
    assert "cleanup confirmed" in asyncio.run(state.dismiss_popup())
    assert not state.diplomacy_pending


def test_changed_counter_requires_explicit_accept_and_query_never_submits(quick_trade):
    class CounterConn(TradeConn):
        def response_is_final(self, action):
            return action == 1

        def response_event(self, action):
            source = super().response_event(action)
            if action == 9:
                source = 'working_outgoing[2]:SetAmount(5);' + source.replace('StatementSubType="AI_ACCEPT_DEAL"','StatementSubType="NONE"').replace('DealAction=1,','DealAction=5,')
            return source
    conn = CounterConn()
    state = GameState(conn)
    result = asyncio.run(state.propose_trade(1, [{"type":"FAVOR","amount":20}], [{"type":"GOLD","amount":10,"duration":30}]))
    assert "COUNTER_OFFER" in result and list(conn.lua.globals().sent_actions.values()) == [9]
    pending = asyncio.run(state.get_pending_deals())
    assert "COUNTER_OFFER" in pending.outgoing_summary
    assert list(conn.lua.globals().sent_actions.values()) == [9]
    result = asyncio.run(state.respond_to_deal(1, True))
    assert "ACCEPTED|" in result and list(conn.lua.globals().sent_actions.values()) == [9, 1]


def test_preexisting_native_session_rejects_request_before_clearing_any_terms(quick_trade):
    conn = TradeConn()
    conn.setup('sessions={[1]=71}; infos={[71]={FromPlayer=0,ToPlayer=1}}; local d=DealManager.GetWorkingDeal(DealDirection.OUTGOING);local i=d:AddItemOfType(1,0);i:SetAmount(777)')
    state = GameState(conn)
    result = asyncio.run(state.test_trade(1, [{"type":"GOLD","amount":20}], []))
    assert "NOT_SUBMITTED" in result and "OPEN_SESSION" in result
    assert conn.lua.globals().requests == 0
    assert conn.lua.eval('working_outgoing[1]:GetAmount()') == 777
    assert not state.diplomacy_pending


@pytest.mark.parametrize("change", ["SessionID=72", "RespondingToDealAction=4", "FromPlayer=2"])
def test_wrong_response_witness_never_resolves_or_resubmits(change, quick_trade):
    class WrongConn(TradeConn):
        def response_event(self, action):
            source = super().response_event(action)
            key=change.split("=",1)[0]
            import re
            return re.sub(key+r'=\d+', change, source)
    conn = WrongConn()
    state = GameState(conn)
    first = asyncio.run(state.propose_trade(1, [{"type":"GOLD","amount":20}], []))
    second = asyncio.run(state.propose_trade(1, [{"type":"GOLD","amount":20}], []))
    assert "OUTCOME_UNKNOWN" in first and "OUTCOME_UNKNOWN" in second
    assert conn.lua.globals().requests == 1 and list(conn.lua.globals().sent_actions.values()) == [9]


def test_pending_preparation_blocks_turn_save_and_menu_replacement_without_submission(quick_trade):
    conn = TradeConn(); conn.deliver_events=False
    state = GameState(conn)
    asyncio.run(state.test_trade(1, [{"type":"GOLD","amount":20}], []))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.end_turn())
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.save_game("offline_trade"))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.reload_game_save("checkpoint"))
    assert conn.lua.globals().requests == 1


@pytest.mark.parametrize("marker", ["observer.trade.stage=\"OPEN_REQUESTED\"", 'print("TRADE|SENT")', "TRADE_EXIT|REQUESTED", "DIPLOMACY_NATIVE_CLOSE"])
def test_cancellation_after_each_native_phase_retains_one_owned_attempt(marker, quick_trade):
    class CancelConn(TradeConn):
        cancelled=False
        async def execute_in_named_state(self,name,source,timeout=5):
            rows=await super().execute_in_named_state(name,source,timeout)
            if marker in source and not self.cancelled:
                self.cancelled=True
                raise asyncio.CancelledError()
            return rows
    async def scenario():
        conn=CancelConn(); state=GameState(conn)
        with pytest.raises(asyncio.CancelledError):
            await state.test_trade(1,[{"type":"GOLD","amount":20}],[])
        assert state.diplomacy_pending
        result=await state.test_trade(1,[{"type":"GOLD","amount":20}],[])
        assert "cleanup confirmed" in result
        assert conn.lua.globals().requests==1
        assert list(conn.lua.globals().sent_actions.values())==[3]
        assert list(conn.lua.globals().mutations.values())==["close"]
        assert not state.diplomacy_pending
    asyncio.run(scenario())


@pytest.mark.parametrize("tamper", ['GameConfiguration.GetValue=function() return 99 end', '__civmcp_diplomacy.nonce="other-owner"'])
def test_world_or_nonce_mismatch_keeps_pending_without_new_proposal(tamper, quick_trade):
    class WrongScope(TradeConn):
        def response_event(self, action):
            return tamper + ';' + super().response_event(action)
    conn=WrongScope(); state=GameState(conn)
    first=asyncio.run(state.test_trade(1,[{"type":"GOLD","amount":20}],[]))
    second=asyncio.run(state.test_trade(1,[{"type":"GOLD","amount":20}],[]))
    assert "OUTCOME_UNKNOWN" in first and "OUTCOME_UNKNOWN" in second
    assert state.diplomacy_pending and conn.lua.globals().requests==1
    assert list(conn.lua.globals().sent_actions.values())==[3]


@pytest.mark.parametrize("setup", ['DiplomacyManager.HasQueuedSession=function() return true end', 'pending=true'])
def test_queued_or_pending_trade_is_not_overwritten(setup, quick_trade):
    conn=TradeConn(); conn.setup(setup)
    state=GameState(conn)
    result=asyncio.run(state.propose_trade(1,[{"type":"GOLD","amount":20}],[]))
    assert "NOT_SUBMITTED" in result and conn.lua.globals().requests==0
    assert list(conn.lua.globals().sent_actions.values())==[]


def test_natural_new_statement_allows_one_explicit_native_positive_response():
    conn=AsyncDiplomacyConn(); state=GameState(conn)
    asyncio.run(state.get_diplomacy_sessions()); conn.statement()
    conn.setup('''DiplomacyManager.AddResponse=function()
 table.insert(mutations,"response")
 Events.DiplomacyStatement.Emit(1,0,{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="FOLLOWUP",FromPlayer=1,FromPlayerMood=0,Initiator=0,RespondingToDealAction=0,DealAction=0,ResponseType=2})
end''')
    assert "SESSION_CONTINUES" in asyncio.run(state.diplomacy_respond(1,"POSITIVE"))
    assert list(conn.lua.globals().mutations.values())==["response"]
    assert not state.diplomacy_pending


def fail_favor_preparation(conn):
    conn.setup('''local original=DealManager.GetWorkingDeal
DealManager.GetWorkingDeal=function(direction)
 local deal=original(direction); local add=deal.AddItemOfType
 deal.AddItemOfType=function(self,kind,owner)
  if kind==DealItemTypes.FAVOR then return nil end
  return add(self,kind,owner)
 end
 return deal
end''')


@pytest.mark.parametrize("entry", ["test_trade", "propose_trade"])
def test_partial_terms_failure_finishes_owned_zero_submission_session(entry, quick_trade):
    conn=TradeConn(); fail_favor_preparation(conn); state=GameState(conn)
    result=asyncio.run(getattr(state,entry)(1,[{"type":"GOLD","amount":20},{"type":"FAVOR","amount":20}],[]))
    assert "NOT_SUBMITTED" in result and "cleanup confirmed" in result
    assert not state.diplomacy_pending
    assert conn.lua.globals().requests==1
    assert list(conn.lua.globals().sent_actions.values())==[]
    assert list(conn.lua.globals().mutations.values())==["close"]


def test_inspect_acceptance_is_evaluation_until_fresh_final_accepted_response(quick_trade):
    class NoFinalConn(TradeConn):
        def response_event(self, action):
            return "" if action==1 else super().response_event(action)
    conn=NoFinalConn(); state=GameState(conn)
    result=asyncio.run(state.propose_trade(1,[{"type":"GOLD","amount":20}],[]))
    assert "ACCEPTED|" not in result and "OUTCOME_UNKNOWN" in result
    assert state._diplomacy_progress.trade.phase=="ACCEPT_WAIT"
    assert list(conn.lua.globals().sent_actions.values())==[9,1]
    result=asyncio.run(state.propose_trade(1,[{"type":"GOLD","amount":20}],[]))
    assert "ACCEPTED|" not in result and "OUTCOME_UNKNOWN" in result
    assert list(conn.lua.globals().sent_actions.values())==[9,1]
    assert state.diplomacy_pending


@pytest.mark.parametrize("marker", ["local prepared,prepareError=pcall", "No EQUALIZE/PROPOSED", "DIPLOMACY_NATIVE_CLOSE"])
@pytest.mark.parametrize("error", [asyncio.CancelledError, TimeoutError])
def test_partial_terms_failure_cancellation_or_lost_reply_only_confirms_native_exit(marker,error,quick_trade):
    class InterruptedConn(TradeConn):
        interrupted=False
        async def execute_in_named_state(self,name,source,timeout=5):
            rows=await super().execute_in_named_state(name,source,timeout)
            if marker in source and not self.interrupted:
                self.interrupted=True
                raise error()
            return rows
    async def scenario():
        conn=InterruptedConn(); fail_favor_preparation(conn); state=GameState(conn)
        if error is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError):
                await state.test_trade(1,[{"type":"GOLD","amount":20},{"type":"FAVOR","amount":20}],[])
        else:
            first=await state.test_trade(1,[{"type":"GOLD","amount":20},{"type":"FAVOR","amount":20}],[])
            assert "unknown" in first.lower()
        assert state.diplomacy_pending
        # Only the original request may confirm this owned attempt.
        result=await state.test_trade(1,[{"type":"GOLD","amount":20},{"type":"FAVOR","amount":20}],[])
        assert "NOT_SUBMITTED" in result and "cleanup confirmed" in result
        assert not state.diplomacy_pending
        assert conn.lua.globals().requests==1
        assert list(conn.lua.globals().sent_actions.values())==[]
        assert list(conn.lua.globals().mutations.values())==["close"]
        assert conn.lua.globals().close_callbacks==1
    asyncio.run(scenario())


@pytest.mark.parametrize("tamper", [
    'GameConfiguration.GetValue=function() return 99 end',
    '__civmcp_diplomacy.nonce="other-owner"',
    'sessions={[1]=72};infos[72]={FromPlayer=0,ToPlayer=1}',
    'working_outgoing[1]:SetAmount(777)',
])
def test_partial_terms_failure_never_closes_replaced_world_owner_session_or_terms(tamper,quick_trade):
    class InterruptedConn(TradeConn):
        interrupted=False
        async def execute_in_named_state(self,name,source,timeout=5):
            rows=await super().execute_in_named_state(name,source,timeout)
            if "local prepared,prepareError=pcall" in source and not self.interrupted:
                self.interrupted=True
                raise asyncio.CancelledError()
            return rows
    async def scenario():
        conn=InterruptedConn(); fail_favor_preparation(conn); state=GameState(conn)
        with pytest.raises(asyncio.CancelledError):
            await state.test_trade(1,[{"type":"GOLD","amount":20},{"type":"FAVOR","amount":20}],[])
        conn.setup(tamper)
        result=await state.test_trade(1,[{"type":"GOLD","amount":20},{"type":"FAVOR","amount":20}],[])
        assert "OUTCOME_UNKNOWN" in result
        assert "OUTCOME_UNKNOWN" in await state.dismiss_popup()
        assert state.diplomacy_pending and conn.lua.globals().requests==1
        assert list(conn.lua.globals().sent_actions.values())==[]
        assert list(conn.lua.globals().mutations.values())==[]
        assert conn.lua.globals().close_callbacks==0
    asyncio.run(scenario())


@pytest.mark.parametrize("case,expected", [
    ("missing", "statement=NONE"),
    ("wrong_from", "filter=FROM_PLAYER_MISMATCH"),
    ("wrong_action", "filter=RESPONDING_ACTION_MISMATCH"),
    ("matched", "response_recorded=true"),
])
def test_turn_status_diagnoses_owned_native_event_without_confirmation_or_mutation(case,expected,quick_trade):
    async def scenario():
        conn=TradeConn(); state=GameState(conn)
        conn.response_event=lambda action: ''
        assert "OUTCOME_UNKNOWN" in await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        if case!="missing":
            kfrom=2 if case=="wrong_from" else 1
            action=3 if case=="wrong_action" else 9
            conn.setup(f'''Events.DiplomacyStatement.Emit(1,0,{{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="AI_ACCEPT_DEAL",FromPlayer={kfrom},FromPlayerMood=0,Initiator=0,RespondingToDealAction={action},DealAction=1,ResponseType=7}})''')
        async def only_status(gs): return "LOCAL_READY"
        state._turn_progress.get_status=only_status
        before=(state._diplomacy_progress.trade.phase,conn.lua.globals().requests,list(conn.lua.globals().sent_actions.values()),list(conn.lua.globals().mutations.values()),conn.lua.globals().__civmcp_diplomacy.seq)
        result=await state.get_turn_status()
        assert expected in result
        assert "expected_action=INSPECT(9)" in result
        assert "owner=MATCHED" in result and "session=MATCHED" in result
        if case!="missing":
            assert "subtype=AI_ACCEPT_DEAL" in result and "response_type=7" in result
            assert "event_from=1" in result and "event_to=0" in result
            assert f"k_from={kfrom}" in result
        assert state.diplomacy_pending
        after=(state._diplomacy_progress.trade.phase,conn.lua.globals().requests,list(conn.lua.globals().sent_actions.values()),list(conn.lua.globals().mutations.values()),conn.lua.globals().__civmcp_diplomacy.seq)
        assert after==before
    asyncio.run(scenario())


@pytest.mark.parametrize("tamper,reason", [
    ('__civmcp_diplomacy.nonce="other-owner"', "OWNER_OR_CONTEXT"),
    ('GameConfiguration.GetValue=function() return 99 end', "WORLD_CHANGED"),
    ('sessions={[1]=72};infos[72]={FromPlayer=0,ToPlayer=1}', "SESSION_NOT_CURRENT"),
])
def test_status_diagnostics_preserve_unknown_owner_world_or_session(tamper,reason,quick_trade):
    async def scenario():
        conn=TradeConn(); conn.response_event=lambda action: ''; state=GameState(conn)
        await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        conn.setup(tamper)
        async def only_status(gs): return "LOCAL_READY"
        state._turn_progress.get_status=only_status
        result=await state.get_turn_status()
        assert reason in result and "subtype=" not in result
        assert state.diplomacy_pending and conn.lua.globals().requests==1
        assert list(conn.lua.globals().sent_actions.values())==[9]
        assert list(conn.lua.globals().mutations.values())==[]
    asyncio.run(scenario())


def test_incoming_deal_diagnostic_does_not_complete_or_accept_trade(quick_trade):
    async def scenario():
        conn=TradeConn(); conn.response_event=lambda action: ''; state=GameState(conn)
        await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        conn.setup('''Events.DiplomacyIncomingDeal.Emit(2,0,5)
Events.DiplomacyIncomingDeal.Emit(1,0,1)''')
        async def only_status(gs): return "LOCAL_READY"
        state._turn_progress.get_status=only_status
        result=await state.get_turn_status()
        assert "diplomacy_incoming: event_seq=1 event_from=1 event_to=0 session_id=71 deal_action=ACCEPTED(1) diagnostic_only=true" in result
        assert "statement=NONE" in result and "response_recorded=false" in result
        assert state._diplomacy_progress.trade.phase=="RESPONSE_WAIT"
        assert state.diplomacy_pending and list(conn.lua.globals().sent_actions.values())==[9]
    asyncio.run(scenario())


def test_observation_ignores_unowned_statement_and_read_failure_is_unknown(quick_trade):
    async def scenario():
        conn=TradeConn(); conn.response_event=lambda action: ''; state=GameState(conn)
        await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        conn.setup('''infos[72]={FromPlayer=0,ToPlayer=1}; Events.DiplomacyStatement.Emit(1,0,{SessionID=72,StatementType="MAKE_DEAL",StatementSubType="UNOWNED",FromPlayer=2,RespondingToDealAction=4,DealAction=1,ResponseType=7})''')
        async def only_status(gs): return "LOCAL_READY"
        state._turn_progress.get_status=only_status
        result=await state.get_turn_status()
        assert "statement=NONE" in result and "UNOWNED" not in result
        async def failed(*args,**kwargs): raise TimeoutError()
        conn.execute_in_named_state=failed
        assert "UNKNOWN reason=READ_FAILED" in await state.get_turn_status()
        assert state.diplomacy_pending and list(conn.lua.globals().sent_actions.values())==[9]
    asyncio.run(scenario())


def test_inspect_refusal_is_evaluation_and_can_be_explicitly_declined(quick_trade):
    class RefusedConn(TradeConn):
        def response_event(self, action):
            source=super().response_event(action)
            if action==9:
                source=source.replace('StatementSubType="AI_ACCEPT_DEAL"','StatementSubType="AI_REFUSE_DEAL"').replace('DealAction=1,','DealAction=2,')
                source='Controls.AcceptDeal.IsHidden=function() return true end;' + source
            return source
    conn=RefusedConn(); state=GameState(conn)
    result=asyncio.run(state.propose_trade(1,[{"type":"GOLD","amount":20}],[]))
    assert "EVALUATION_REJECTED" in result and "ACCEPTED|" not in result
    assert state._diplomacy_progress.trade.phase=="COUNTER"
    assert conn.lua.eval('controls["/InGame/DiplomacyDealView"].hidden') is False
    assert list(conn.lua.globals().sent_actions.values())==[9]
    assert "NOT_SUBMITTED" in asyncio.run(state.respond_to_deal(1,True))
    assert "cleanup confirmed" in asyncio.run(state.respond_to_deal(1,False))
    assert list(conn.lua.globals().sent_actions.values())==[9]
    assert not state.diplomacy_pending


@pytest.mark.parametrize("hidden", ["true","nil"])
def test_inspect_matching_terms_with_unavailable_native_accept_stays_zero_accept_and_can_exit(hidden,quick_trade):
    conn=TradeConn(); state=GameState(conn)
    conn.setup(f'Controls.AcceptDeal.IsHidden=function() return {hidden} end')
    result=asyncio.run(state.propose_trade(1,[{"type":"GOLD","amount":20}],[]))
    assert "NOT_SUBMITTED" in result
    assert list(conn.lua.globals().sent_actions.values())==[9]
    assert state.diplomacy_pending
    assert "cleanup confirmed" in asyncio.run(state.respond_to_deal(1,False))
    assert list(conn.lua.globals().sent_actions.values())==[9]
    assert not state.diplomacy_pending


@pytest.mark.parametrize("marker", ["receipt.acceptEntered=true", "EVALUATION_RESTORED"])
def test_inspect_acceptance_or_known_zero_restore_cancellation_never_repeats_send(marker,quick_trade):
    class CancelConn(TradeConn):
        cancelled=False
        async def execute_in_named_state(self,name,source,timeout=5):
            rows=await super().execute_in_named_state(name,source,timeout)
            if marker in source and not self.cancelled:
                self.cancelled=True
                raise asyncio.CancelledError()
            return rows
    async def scenario():
        conn=CancelConn(); state=GameState(conn)
        if marker=="EVALUATION_RESTORED":
            conn.setup('Controls.AcceptDeal.IsHidden=function() return true end')
        with pytest.raises(asyncio.CancelledError):
            await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        assert state.diplomacy_pending
        result=await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        if marker=="EVALUATION_RESTORED":
            assert "NOT_SUBMITTED" in result
            assert "cleanup confirmed" in await state.respond_to_deal(1,False)
            assert list(conn.lua.globals().sent_actions.values())==[9]
        else:
            assert "ACCEPTED|" in result
            assert list(conn.lua.globals().sent_actions.values())==[9,1]
        assert conn.lua.globals().requests==1
    asyncio.run(scenario())


@pytest.mark.parametrize("empty", [True,False])
def test_inspect_requires_nonempty_valid_native_terms_before_any_evaluation(empty,quick_trade):
    conn=TradeConn(); state=GameState(conn)
    if not empty:
        conn.setup('local original=DealManager.GetWorkingDeal; DealManager.GetWorkingDeal=function(direction) local deal=original(direction);deal.Validate=function() return 1 end;return deal end')
    result=asyncio.run(state.propose_trade(1,[] if empty else [{"type":"GOLD","amount":20}],[]))
    assert "NOT_SUBMITTED" in result and "cleanup confirmed" in result
    assert list(conn.lua.globals().sent_actions.values())==[]
    assert not state.diplomacy_pending


@pytest.mark.parametrize("preview", [False, True])
def test_ai_trade_refuses_human_target_without_opening_or_clearing_deal(preview, quick_trade):
    conn=TradeConn(); state=GameState(conn)
    conn.setup('''Players[1].IsHuman=function() return true end;
local deal=DealManager.GetWorkingDeal(DealDirection.OUTGOING);local item=deal:AddItemOfType(1,0);item:SetAmount(777)''')
    trade = state.test_trade if preview else state.propose_trade
    result=asyncio.run(trade(1,[{"type":"GOLD","amount":20}],[]))
    assert "NOT_SUBMITTED" in result and "TARGET_NOT_AI" in result
    assert conn.lua.globals().requests==0
    assert conn.lua.eval('working_outgoing[1]:GetAmount()')==777
    assert list(conn.lua.globals().sent_actions.values())==[]
    assert not state.diplomacy_pending


@pytest.mark.parametrize("error", [TimeoutError,asyncio.CancelledError])
def test_lost_accept_arming_reply_preserves_evaluation_for_zero_accept_and_decline(error,quick_trade):
    class LostArmConn(TradeConn):
        lost=False
        async def execute_in_named_state(self,name,source,timeout=5):
            rows=await super().execute_in_named_state(name,source,timeout)
            if 'if DealProposalAction.ACCEPTED==DealProposalAction.ACCEPTED' in source and not self.lost:
                self.lost=True
                raise error()
            return rows
    async def scenario():
        conn=LostArmConn(); state=GameState(conn)
        conn.setup('Controls.AcceptDeal.IsHidden=function() return true end')
        if error is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError):
                await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        else:
            assert "OUTCOME_UNKNOWN" in await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        assert state.diplomacy_pending
        seq=conn.lua.eval('__civmcp_diplomacy.trade.evaluation.response.seq')
        result=await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        assert "ERR:NOT_SUBMITTED|" in result
        assert conn.lua.eval('__civmcp_diplomacy.trade.evaluation.response.seq')==seq
        assert "cleanup confirmed" in await state.respond_to_deal(1,False)
        assert conn.lua.globals().requests==1
        assert list(conn.lua.globals().sent_actions.values())==[9]
        assert list(conn.lua.globals().mutations.values())==["close"]
        assert not state.diplomacy_pending
    asyncio.run(scenario())


def test_fresh_native_human_accept_zero_action_confirms_only_entered_owned_accept_once(quick_trade):
    async def scenario():
        conn=TradeConn(); state=GameState(conn)
        result=await state.propose_trade(1,[{"type":"FAVOR","amount":20}],[{"type":"GOLD","amount":50}])
        assert "ACCEPTED|" in result
        assert "HUMAN_ACCEPT_DEAL" in result
        assert list(conn.lua.globals().sent_actions.values())==[9,1]
        # Repeated confirmation keeps the final result and never sends a new trade.
        again=await state.propose_trade(1,[{"type":"FAVOR","amount":20}],[{"type":"GOLD","amount":50}])
        assert "ACCEPTED|" in again
        assert list(conn.lua.globals().sent_actions.values())==[9,1]
        assert conn.lua.globals().requests==1 and state.diplomacy_pending
        assert "SESSION_CLOSED" in await state.diplomacy_respond(1,"EXIT")
        assert "cleanup confirmed" in await state.dismiss_popup()
        assert not state.diplomacy_pending
    asyncio.run(scenario())


@pytest.mark.parametrize("tamper", [
    'world', 'nonce', 'session', 'stale_seq', 'subtype', 'statement_type',
    'responding_action', 'deal_action', 'from_player', 'accept_receipt',
])
def test_human_accept_zero_action_rejects_unmatched_final_witness(tamper,quick_trade):
    class ChangedFinalConn(TradeConn):
        def response_event(self,action):
            source=super().response_event(action)
            if action!=1: return source
            if tamper=='world': return 'GameConfiguration.GetValue=function() return 99 end;' + source
            if tamper=='nonce': return '__civmcp_diplomacy.nonce="other-owner";' + source
            if tamper=='session': return source.replace('SessionID=71','SessionID=72')
            if tamper=='stale_seq': return source+';__civmcp_diplomacy.trade.afterSeq=__civmcp_diplomacy.seq'
            if tamper=='subtype': return source.replace('StatementSubType="HUMAN_ACCEPT_DEAL"','StatementSubType="AI_ACCEPT_DEAL"')
            if tamper=='statement_type': return source.replace('StatementType="MAKE_DEAL"','StatementType="MAKE_DEMAND"')
            if tamper=='responding_action': return source.replace('RespondingToDealAction=0','RespondingToDealAction=7')
            if tamper=='deal_action': return source.replace('DealAction=0,','DealAction=2,')
            if tamper=='from_player': return source.replace('FromPlayer=1,','FromPlayer=2,')
            if tamper=='accept_receipt': return source+';__civmcp_trade_deal.token="other-attempt"'
            return source
    async def scenario():
        conn=ChangedFinalConn(); state=GameState(conn)
        first=await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        second=await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        assert "ACCEPTED|" not in first and "ACCEPTED|" not in second
        assert "OUTCOME_UNKNOWN" in first and "OUTCOME_UNKNOWN" in second
        assert conn.lua.globals().requests==1
        assert list(conn.lua.globals().sent_actions.values())==[9,1]
        assert state.diplomacy_pending
    asyncio.run(scenario())


def test_final_human_accept_requires_actual_native_accept_entry_not_an_armed_action(quick_trade):
    class NoEntryConn(TradeConn):
        async def execute_in_named_state(self,name,source,timeout=5):
            if "receipt.acceptEntered=true" in source:
                self.setup(self.response_event(1))
                return ["TRADE_ACCEPT|REQUESTED"]  # Lost/ambiguous adapter reply, no callback entry.
            return await super().execute_in_named_state(name,source,timeout)
    async def scenario():
        conn=NoEntryConn(); state=GameState(conn)
        for _ in range(2):
            result=await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
            assert "ACCEPTED|" not in result and "OUTCOME_UNKNOWN" in result
        assert list(conn.lua.globals().sent_actions.values())==[9]
        assert state.diplomacy_pending
    asyncio.run(scenario())


@pytest.mark.parametrize("error", [TimeoutError,asyncio.CancelledError])
def test_entered_accept_lost_reply_confirms_human_final_once(error,quick_trade):
    class LostAcceptConn(TradeConn):
        lost=False
        async def execute_in_named_state(self,name,source,timeout=5):
            rows=await super().execute_in_named_state(name,source,timeout)
            if "receipt.acceptEntered=true" in source and not self.lost:
                self.lost=True
                raise error()
            return rows
    async def scenario():
        conn=LostAcceptConn(); state=GameState(conn)
        if error is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError):
                await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        else:
            assert "OUTCOME_UNKNOWN" in await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        result=await state.propose_trade(1,[{"type":"GOLD","amount":20}],[])
        assert "ACCEPTED|" in result and "HUMAN_ACCEPT_DEAL" in result
        assert list(conn.lua.globals().sent_actions.values())==[9,1]
        assert conn.lua.globals().requests==1
    asyncio.run(scenario())


def test_failed_no_send_draft_cleared_by_native_ui_closes_only_owned_session(quick_trade):
    class ClearedConn(TradeConn):
        async def execute_in_named_state(self,name,source,timeout=5):
            rows=await super().execute_in_named_state(name,source,timeout)
            if 'local prepared,prepareError=pcall' in source:
                self.setup('working_outgoing={}')
            return rows
    conn=ClearedConn();fail_favor_preparation(conn);state=GameState(conn)
    result=asyncio.run(state.test_trade(1,[{'type':'GOLD','amount':20},{'type':'FAVOR','amount':20}],[]))
    assert 'NOT_SUBMITTED' in result and 'cleanup confirmed' in result
    assert not state.diplomacy_pending
    assert conn.lua.globals().requests==1
    assert list(conn.lua.globals().sent_actions.values())==[]
    assert list(conn.lua.globals().mutations.values())==['close']


class WarTradeConn(TradeConn):
    def __init__(self):
        super().__init__()
        self.setup('''
DealAgreementTypes={JOINT_WAR=594401930,THIRD_PARTY_WAR=-1258934878}
DealValidationResult.MISSING_DEPENDENCY=2
war_options={ [0]={[594401930]={},[-1258934878]={}},
              [1]={[594401930]={},[-1258934878]={
                {SubType=-1258934878,ForType=2,Parameters={WarType=1941490022}},
              }} }
DealManager.GetPossibleDealItems=function(from,to,kind,subtype,forDeal)
 assert(to==(from==0 and 1 or 0) and kind==DealItemTypes.AGREEMENTS)
 if forDeal==nil then
  return {{SubType=DealAgreementTypes.JOINT_WAR,Duration=25,IsValid=false,ValidationResult=2},
          {SubType=DealAgreementTypes.THIRD_PARTY_WAR,Duration=25,IsValid=false,ValidationResult=2}}
 end
 return war_options[from][subtype]
end
''')


def test_native_ongoing_war_agreement_binds_counterpart_target_type_and_duration(quick_trade):
    conn=WarTradeConn();state=GameState(conn)
    result=asyncio.run(state.test_trade(1,[],[{'type':'WAR_AGREEMENT','target':2}]))
    assert 'cleanup confirmed' in result and 'NOT_SUBMITTED' not in result
    assert conn.lua.eval('#working_outgoing')==1
    assert conn.lua.eval('working_outgoing[1]:GetFromPlayerID()')==1
    assert conn.lua.eval('working_outgoing[1]:GetSubType()')==-1258934878
    assert conn.lua.eval('working_outgoing[1]:GetValueType()')==2
    assert conn.lua.eval('working_outgoing[1]:GetDuration()')==25
    assert conn.lua.eval('working_outgoing[1]:GetParameterValue("WarType")')==1941490022
    assert list(conn.lua.globals().sent_actions.values())==[3]


def test_unbound_legacy_joint_war_item_is_not_evaluated(quick_trade):
    conn=WarTradeConn();state=GameState(conn)
    result=asyncio.run(state.test_trade(1,[],[{'type':'AGREEMENT','subtype':'JOINT_WAR'}]))
    assert 'NOT_SUBMITTED' in result and 'cleanup confirmed' in result
    assert list(conn.lua.globals().sent_actions.values())==[]


def test_native_mutual_new_war_requires_both_sides_with_same_war_type(quick_trade):
    conn=WarTradeConn();state=GameState(conn)
    conn.setup('''
war_options[1][DealAgreementTypes.THIRD_PARTY_WAR]={}
for _,owner in ipairs({0,1}) do
 war_options[owner][DealAgreementTypes.JOINT_WAR]={{SubType=DealAgreementTypes.JOINT_WAR,ForType=2,Parameters={WarType=1941490022}}}
end
''')
    result=asyncio.run(state.test_trade(1,[],[{'type':'WAR_AGREEMENT','target':2}]))
    assert 'cleanup confirmed' in result and 'NOT_SUBMITTED' not in result
    assert conn.lua.eval('#working_outgoing')==2
    assert conn.lua.eval('working_outgoing[1]:GetFromPlayerID()')==0
    assert conn.lua.eval('working_outgoing[2]:GetFromPlayerID()')==1
    for index in (1,2):
        assert conn.lua.eval(f'working_outgoing[{index}]:GetValueType()')==2
        assert conn.lua.eval(f'working_outgoing[{index}]:GetParameterValue("WarType")')==1941490022
        assert conn.lua.eval(f'working_outgoing[{index}]:GetDuration()')==25
    assert list(conn.lua.globals().sent_actions.values())==[3]


@pytest.mark.parametrize('patch,reason',[
    ('war_options[1][DealAgreementTypes.THIRD_PARTY_WAR]={}','WAR_OPTIONS_NONE'),
    ('war_options[1][DealAgreementTypes.THIRD_PARTY_WAR]=nil','WAR_OPTIONS_UNREADABLE'),
    ('DealManager.GetPossibleDealItems=nil','WAR_OPTIONS_UNAVAILABLE'),
    ('DealManager.GetPossibleDealItems=function() error("native choices read failed") end','WAR_OPTIONS_UNREADABLE'),
    ('war_options[1][DealAgreementTypes.THIRD_PARTY_WAR][1].Parameters=nil','WAR_OPTIONS_UNREADABLE'),
    ('war_options[1][DealAgreementTypes.THIRD_PARTY_WAR][1].ForType=7','WAR_OPTIONS_NONE'),
    ('war_options[1][DealAgreementTypes.THIRD_PARTY_WAR][1].Parameters.WarType="unknown"','WAR_OPTIONS_UNREADABLE'),
    ('war_options[1][DealAgreementTypes.THIRD_PARTY_WAR]={};war_options[1][DealAgreementTypes.JOINT_WAR]={{SubType=DealAgreementTypes.JOINT_WAR,ForType=2,Parameters={WarType=1941490022}}}','WAR_OPTIONS_NONE'),
])
def test_unavailable_or_changed_native_war_options_never_evaluate_and_cleanup_owned_draft(patch,reason,quick_trade):
    conn=WarTradeConn();state=GameState(conn);conn.setup(patch)
    result=asyncio.run(state.test_trade(1,[],[{'type':'WAR_AGREEMENT','target':2}]))
    assert reason in result and 'NOT_SUBMITTED' in result and 'cleanup confirmed' in result
    assert list(conn.lua.globals().sent_actions.values())==[]
    assert not state.diplomacy_pending


@pytest.mark.parametrize('selected',[None,1941490022,-999])
def test_ambiguous_native_war_types_require_explicit_matching_selector(selected,quick_trade):
    conn=WarTradeConn();state=GameState(conn)
    conn.setup('''table.insert(war_options[1][DealAgreementTypes.THIRD_PARTY_WAR],
 {SubType=DealAgreementTypes.THIRD_PARTY_WAR,ForType=2,Parameters={WarType=2000000000}})''')
    intent={'type':'WAR_AGREEMENT','target':2}
    if selected is not None: intent['war_type']=selected
    result=asyncio.run(state.test_trade(1,[],[intent]))
    if selected==1941490022:
        assert 'NOT_SUBMITTED' not in result
        assert conn.lua.eval('working_outgoing[1]:GetParameterValue("WarType")')==selected
        assert list(conn.lua.globals().sent_actions.values())==[3]
    else:
        assert ('WAR_OPTIONS_AMBIGUOUS' if selected is None else 'WAR_OPTIONS_NONE') in result
        assert 'THIRD_PARTY_WAR:1941490022' in result and 'THIRD_PARTY_WAR:2000000000' in result
        assert list(conn.lua.globals().sent_actions.values())==[]
    assert 'cleanup confirmed' in result


@pytest.mark.parametrize('tamper',[
    '__civmcp_trade_deal.sent=true', '__civmcp_trade_deal.failed=false',
    '__civmcp_trade_deal.token="other"','__civmcp_trade_deal.sid=72',
    '__civmcp_trade_deal.target=2', '__civmcp_diplomacy.nonce="other"',
    'pending=true', 'DiplomacyManager.HasQueuedSession=function() return true end',
    'infos[71]={FromPlayer=2,ToPlayer=0}', 'GameConfiguration.GetValue=function() return 99 end',
    'local new=dealFor(DealDirection.OUTGOING):AddItemOfType(DealItemTypes.GOLD,0);new:SetAmount(777)',
    'DealManager.GetWorkingDeal=function() return nil end',
])
def test_native_empty_failed_draft_never_weakens_original_ownership_guards(tamper,quick_trade):
    class InterruptedEmptyConn(TradeConn):
        interrupted=False
        async def execute_in_named_state(self,name,source,timeout=5):
            rows=await super().execute_in_named_state(name,source,timeout)
            if 'local prepared,prepareError=pcall' in source and not self.interrupted:
                self.interrupted=True;self.setup('working_outgoing={}')
                raise asyncio.CancelledError()
            return rows
    async def scenario():
        conn=InterruptedEmptyConn();fail_favor_preparation(conn);state=GameState(conn)
        terms=[{'type':'GOLD','amount':20},{'type':'FAVOR','amount':20}]
        with pytest.raises(asyncio.CancelledError): await state.test_trade(1,terms,[])
        conn.setup(tamper)
        assert 'OUTCOME_UNKNOWN' in await state.test_trade(1,terms,[])
        assert state.diplomacy_pending
        assert list(conn.lua.globals().sent_actions.values())==[]
        assert list(conn.lua.globals().mutations.values())==[]
    asyncio.run(scenario())


@pytest.mark.parametrize('change',[
    'working_incoming[1]:SetValueType(7)',
    'working_incoming[1]:SetParameterValue("WarType",2000000000)',
])
def test_war_target_and_type_change_are_counteroffers_never_auto_accepted(change,quick_trade):
    class ChangedWarConn(WarTradeConn):
        def response_event(self,action):
            return super().response_event(action)+';'+change
    conn=ChangedWarConn();state=GameState(conn)
    result=asyncio.run(state.propose_trade(1,[],[{'type':'WAR_AGREEMENT','target':2}]))
    assert 'COUNTER_OFFER|' in result and 'ACCEPTED|' not in result
    assert list(conn.lua.globals().sent_actions.values())==[9]
    assert state.diplomacy_pending


def test_lost_native_war_preview_reply_does_not_repeat_options_or_evaluation(quick_trade):
    class LostWarConn(WarTradeConn):
        lost=False
        async def execute_in_named_state(self,name,source,timeout=5):
            rows=await super().execute_in_named_state(name,source,timeout)
            if 'local prepared,prepareError=pcall' in source and not self.lost:
                self.lost=True;raise TimeoutError('native war preview ran; reply lost')
            return rows
    conn=LostWarConn();state=GameState(conn);intent=[{'type':'WAR_AGREEMENT','target':2}]
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.test_trade(1,[],intent))
    assert 'cleanup confirmed' in asyncio.run(state.test_trade(1,[],intent))
    assert conn.lua.globals().requests==1
    assert list(conn.lua.globals().sent_actions.values())==[3]


class OpenBordersTradeConn(TradeConn):
    def __init__(self):
        super().__init__()
        self.setup('''
DealAgreementTypes={OPEN_BORDERS=-242352742}
agreement_summaries={
 [0]={{SubType=-242352742,Duration=25,ForType=-1,IsValid=true,ValidationResult=0}},
 [1]={{SubType=-242352742,Duration=25,ForType=-1,IsValid=true,ValidationResult=0}},
}
local originalWorkingDeal=DealManager.GetWorkingDeal
DealManager.GetWorkingDeal=function(direction,from,to)
 assert(from==0 and to==1,'working deal pair changed')
 local result=originalWorkingDeal(direction)
 if direction==DealDirection.OUTGOING then boundWorkingDeal=result end
 return result
end
summary_reads=0
DealManager.GetPossibleDealItems=function(from,to,kind,workingDeal,...)
 assert(select('#',...)==0,'native summary needs exactly four arguments')
 assert(to==(from==0 and 1 or 0) and kind==DealItemTypes.AGREEMENTS,'native option pair changed')
 assert(workingDeal==boundWorkingDeal,'native option working deal changed')
 summary_reads=summary_reads+1
 return agreement_summaries[from]
end
''')


@pytest.mark.parametrize('ours,theirs', [(25,25),(17,41)])
def test_open_borders_preview_binds_each_native_summary_and_full_terms(ours,theirs,quick_trade):
    conn=OpenBordersTradeConn();state=GameState(conn)
    conn.setup(f'agreement_summaries[0][1].Duration={ours};agreement_summaries[1][1].Duration={theirs}')
    terms=[{'type':'AGREEMENT','subtype':'OPEN_BORDERS'}]
    result=asyncio.run(state.test_trade(1,terms,terms))
    assert 'cleanup confirmed' in result and 'NOT_SUBMITTED' not in result
    assert f'duration={ours} turns' in result and f'duration={theirs} turns' in result
    assert conn.lua.eval('working_outgoing[1]:GetFromPlayerID()')==0
    assert conn.lua.eval('working_outgoing[2]:GetFromPlayerID()')==1
    assert conn.lua.eval('working_outgoing[1]:GetDuration()')==ours
    assert conn.lua.eval('working_outgoing[2]:GetDuration()')==theirs
    assert conn.lua.eval('working_outgoing[1]:GetSubType()')==-242352742
    assert conn.lua.eval('working_outgoing[2]:GetSubType()')==-242352742
    assert conn.lua.eval('working_outgoing[1]:GetValueType()')==-1
    assert conn.lua.eval('working_outgoing[2]:GetValueType()')==-1
    assert conn.lua.eval('__civmcp_trade_deal.original')==f'0,3,-1,-242352742,0,{ours};1,3,-1,-242352742,0,{theirs}'
    assert list(conn.lua.globals().sent_actions.values())==[3]
    assert not state.diplomacy_pending


def test_open_borders_changed_by_native_validation_is_not_evaluated(quick_trade):
    conn=OpenBordersTradeConn();state=GameState(conn)
    conn.setup('''
local original=DealManager.GetWorkingDeal
DealManager.GetWorkingDeal=function(...)
 local result=original(...)
 result.Validate=function()
  if working_outgoing[1] then working_outgoing[1]:SetDuration(0) end
  return DealValidationResult.VALID
 end
 return result
end
''')
    terms=[{'type':'AGREEMENT','subtype':'OPEN_BORDERS'}]
    result=asyncio.run(state.test_trade(1,terms,terms))
    assert 'NOT_SUBMITTED' in result and 'cleanup confirmed' in result
    assert list(conn.lua.globals().sent_actions.values())==[]
    assert not state.diplomacy_pending


@pytest.mark.parametrize('owner',[0,1])
@pytest.mark.parametrize('change',[
    'agreement_summaries[OWNER]=nil',
    'agreement_summaries[OWNER]={}',
    'table.insert(agreement_summaries[OWNER],agreement_summaries[OWNER][1])',
    'agreement_summaries[OWNER][1].Duration=nil',
    'agreement_summaries[OWNER][1].Duration="25"',
    'agreement_summaries[OWNER][1].Duration=-1',
    'agreement_summaries[OWNER][1].IsValid=nil',
    'agreement_summaries[OWNER][1].IsValid=false',
    'agreement_summaries[OWNER][1].ForType="unknown"',
    'agreement_summaries[OWNER][1].ForType=9',
])
def test_open_borders_unavailable_native_direction_refuses_evaluation_and_closes(change,owner,quick_trade):
    conn=OpenBordersTradeConn();state=GameState(conn);conn.setup(change.replace('OWNER',str(owner)))
    terms=[{'type':'AGREEMENT','subtype':'OPEN_BORDERS'}]
    result=asyncio.run(state.test_trade(1,terms,terms))
    assert 'NOT_SUBMITTED' in result and 'cleanup confirmed' in result
    assert list(conn.lua.globals().sent_actions.values())==[]
    assert not state.diplomacy_pending


@pytest.mark.parametrize('change',[
    'DealManager.GetPossibleDealItems=nil',
    'DealManager.GetPossibleDealItems=function() error("unreadable") end',
    'DealAgreementTypes.OPEN_BORDERS=nil',
    'DealAgreementTypes.OPEN_BORDERS=false',
    'agreement_summaries[0]={[2]=agreement_summaries[0][1]}',
])
def test_open_borders_native_capability_unreadable_is_zero_send_with_cleanup(change,quick_trade):
    conn=OpenBordersTradeConn();state=GameState(conn);conn.setup(change)
    terms=[{'type':'AGREEMENT','subtype':'OPEN_BORDERS'}]
    result=asyncio.run(state.test_trade(1,terms,terms))
    assert 'NOT_SUBMITTED' in result and 'cleanup confirmed' in result
    assert list(conn.lua.globals().sent_actions.values())==[]
    assert not state.diplomacy_pending


def test_open_borders_native_default_value_does_not_require_optional_summary_for_type(quick_trade):
    conn=OpenBordersTradeConn();state=GameState(conn)
    conn.setup('agreement_summaries[0][1].ForType=nil;agreement_summaries[1][1].ForType=nil')
    terms=[{'type':'AGREEMENT','subtype':'OPEN_BORDERS'}]
    assert 'cleanup confirmed' in asyncio.run(state.test_trade(1,terms,terms))
    assert conn.lua.eval('working_outgoing[1]:GetValueType()')==-1
    assert conn.lua.eval('working_outgoing[2]:GetValueType()')==-1
    assert list(conn.lua.globals().sent_actions.values())==[3]


@pytest.mark.parametrize('change',[
    'working_incoming[1]:SetDuration(0)',
    'working_incoming[2]:SetDuration(40)',
    'working_incoming[1]:SetValueType(9)',
    'working_incoming[1]:SetSubType(999)',
])
def test_modified_open_borders_terms_are_counteroffers_never_auto_accepted(change,quick_trade):
    class ChangedBordersConn(OpenBordersTradeConn):
        def response_event(self,action):
            return super().response_event(action)+';'+change
    conn=ChangedBordersConn();state=GameState(conn)
    terms=[{'type':'AGREEMENT','subtype':'OPEN_BORDERS'}]
    result=asyncio.run(state.propose_trade(1,terms,terms))
    assert 'COUNTER_OFFER|' in result and 'ACCEPTED|' not in result
    assert list(conn.lua.globals().sent_actions.values())==[9]
    assert state.diplomacy_pending


def test_lost_open_borders_preview_reply_only_observes_original_duration_and_evaluation(quick_trade):
    class LostBordersConn(OpenBordersTradeConn):
        lost=False
        async def execute_in_named_state(self,name,source,timeout=5):
            rows=await super().execute_in_named_state(name,source,timeout)
            if 'local prepared,prepareError=pcall' in source and not self.lost:
                self.lost=True;raise TimeoutError('native open borders evaluation reply lost')
            return rows
    conn=LostBordersConn();state=GameState(conn)
    terms=[{'type':'AGREEMENT','subtype':'OPEN_BORDERS'}]
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.test_trade(1,terms,terms))
    assert 'cleanup confirmed' in asyncio.run(state.test_trade(1,terms,terms))
    assert conn.lua.globals().summary_reads==2
    assert conn.lua.globals().requests==1
    assert list(conn.lua.globals().sent_actions.values())==[3]


def test_native_validation_does_not_replace_original_economic_intent_fingerprint(quick_trade):
    conn=TradeConn();state=GameState(conn)
    conn.setup('''
local original=DealManager.GetWorkingDeal
DealManager.GetWorkingDeal=function(...)
 local result=original(...)
 result.Validate=function()
  if working_outgoing[1] then working_outgoing[1]:SetAmount(19) end
  return DealValidationResult.VALID
 end
 return result
end
''')
    result=asyncio.run(state.propose_trade(1,[{'type':'GOLD','amount':20}],[]))
    assert 'COUNTER_OFFER|' in result and 'ACCEPTED|' not in result
    assert list(conn.lua.globals().sent_actions.values())==[9]
    assert conn.lua.eval('__civmcp_trade_deal.original')=='0,1,-1,-1,20,0'


@pytest.mark.parametrize('tamper',[
    '__civmcp_trade_deal.sent=true',
    '__civmcp_trade_deal.token="another"',
    '__civmcp_trade_deal.sid=72',
    'pending=true',
    'GameConfiguration.GetValue=function() return 999 end',
    'infos[71]={FromPlayer=2,ToPlayer=0}',
    'local extra=dealFor(DealDirection.OUTGOING):AddItemOfType(DealItemTypes.GOLD,0);extra:SetAmount(777)',
])
def test_changed_open_borders_no_send_draft_still_requires_exact_cleanup_ownership(tamper,quick_trade):
    class ChangedDraftConn(OpenBordersTradeConn):
        changed=False
        async def execute_in_named_state(self,name,source,timeout=5):
            rows=await super().execute_in_named_state(name,source,timeout)
            if 'local prepared,prepareError=pcall' in source and not self.changed:
                self.changed=True;self.setup(tamper)
            return rows
    conn=ChangedDraftConn();state=GameState(conn)
    conn.setup('''
local original=DealManager.GetWorkingDeal
DealManager.GetWorkingDeal=function(...)
 local result=original(...)
 result.Validate=function()
  if working_outgoing[1] then working_outgoing[1]:SetDuration(0) end
  return DealValidationResult.VALID
 end
 return result
end
''')
    terms=[{'type':'AGREEMENT','subtype':'OPEN_BORDERS'}]
    result=asyncio.run(state.test_trade(1,terms,terms))
    assert 'OUTCOME_UNKNOWN' in result and 'cleanup confirmed' not in result
    assert state.diplomacy_pending
    assert list(conn.lua.globals().sent_actions.values())==[]
    assert list(conn.lua.globals().mutations.values())==[]
