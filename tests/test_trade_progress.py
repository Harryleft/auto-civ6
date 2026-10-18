"""Real GameState and Lua 5.1 with deferred native diplomacy event delivery."""
import asyncio
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
    assert "SESSION_CLOSED" in asyncio.run(state.diplomacy_respond(1, "EXIT"))
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
        return '''working_incoming=working_outgoing;
''' + ('controls["/InGame/DiplomacyDealView"].hidden=true; controls["/InGame/DiplomacyActionView/ConversationContainer"].hidden=false; native_selections={{Key="CHOICE_EXIT"}};\n' if final else '') + f'''Events.DiplomacyStatement.Emit(1,0,{{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="AI_ACCEPT_DEAL",FromPlayer=1,FromPlayerMood=0,Initiator=0,RespondingToDealAction={action},DealAction=1,ResponseType=1}})'''

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
    second = asyncio.run(state.test_trade(1, [{"type":"GOLD","amount":1}], []))
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
    second = asyncio.run(state.test_trade(1, [{"type":"GOLD","amount":30}], []))
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
        result=await state.propose_trade(2,[{"type":"GOLD","amount":99}],[])
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
    second=asyncio.run(state.propose_trade(1,[{"type":"GOLD","amount":30}],[]))
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
    result=asyncio.run(state.propose_trade(1,[{"type":"GOLD","amount":40}],[]))
    assert "ACCEPTED|" not in result and "OUTCOME_UNKNOWN" in result
    assert list(conn.lua.globals().sent_actions.values())==[9,1]
    assert state.diplomacy_pending


@pytest.mark.parametrize("marker", ["local prepared=pcall", "No EQUALIZE/PROPOSED", "DIPLOMACY_NATIVE_CLOSE"])
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
        # The opposite tool and a different requested target only resume the owned attempt.
        result=await state.propose_trade(2,[{"type":"GOLD","amount":99}],[])
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
            if "local prepared=pcall" in source and not self.interrupted:
                self.interrupted=True
                raise asyncio.CancelledError()
            return rows
    async def scenario():
        conn=InterruptedConn(); fail_favor_preparation(conn); state=GameState(conn)
        with pytest.raises(asyncio.CancelledError):
            await state.test_trade(1,[{"type":"GOLD","amount":20},{"type":"FAVOR","amount":20}],[])
        conn.setup(tamper)
        result=await state.test_trade(1,[{"type":"GOLD","amount":40}],[])
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
        result=await state.propose_trade(2,[{"type":"GOLD","amount":99}],[])
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


def test_ai_inspect_send_refuses_human_target_without_opening_or_clearing_deal(quick_trade):
    conn=TradeConn(); state=GameState(conn)
    conn.setup('''Players[1].IsHuman=function() return true end;
local deal=DealManager.GetWorkingDeal(DealDirection.OUTGOING);local item=deal:AddItemOfType(1,0);item:SetAmount(777)''')
    result=asyncio.run(state.propose_trade(1,[{"type":"GOLD","amount":20}],[]))
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
        result=await state.propose_trade(2,[{"type":"GOLD","amount":99}],[])
        assert "ERR:NOT_SUBMITTED|" in result
        assert conn.lua.eval('__civmcp_diplomacy.trade.evaluation.response.seq')==seq
        assert "cleanup confirmed" in await state.respond_to_deal(1,False)
        assert conn.lua.globals().requests==1
        assert list(conn.lua.globals().sent_actions.values())==[9]
        assert list(conn.lua.globals().mutations.values())==["close"]
        assert not state.diplomacy_pending
    asyncio.run(scenario())
