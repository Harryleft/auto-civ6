"""Current incoming negotiations through GameState and native Lua 5.1 events."""
import asyncio
import pytest
from test_trade_progress import TradeConn, quick_trade
from civ_mcp.game_state import GameState
from civ_mcp import narrate

run = asyncio.run


class IncomingConn(TradeConn):
    def __init__(self):
        super().__init__()
        self.setup('''
sessions={[1]=71}; infos={[71]={FromPlayer=1,ToPlayer=0}};
ms_OtherPlayerID=1; ms_InitiatedByPlayerID=1;
g_LocalPlayer={GetID=function() return 0 end}; g_OtherPlayer={GetID=function() return 1 end};
controls["/InGame/DiplomacyDealView"].hidden=false;
controls["/InGame/DiplomacyActionView"].hidden=false;
controls["/InGame/DiplomacyActionView/ConversationContainer"].hidden=true;
ms_bIsDemand=false;DealAgreementTypes={OPEN_BORDERS=1,JOINT_WAR=2,ALLIANCE=3};accept_visible=true
Controls.AcceptDeal.IsHidden=function() return not accept_visible end
controls["/InGame/DiplomacyDealView/AcceptDeal"]=Controls.AcceptDeal
local out=DealManager.GetWorkingDeal(DealDirection.OUTGOING,0,1)
local peace=out:AddItemOfType(DealItemTypes.AGREEMENTS,0);peace:SetSubType(12);peace:SetDuration(9)
peace=out:AddItemOfType(DealItemTypes.AGREEMENTS,1);peace:SetSubType(12);peace:SetDuration(9)
local work=out:AddItemOfType(DealItemTypes.GREATWORK,1);work:SetValueType(83);work:SetAmount(1)
local gold=out:AddItemOfType(DealItemTypes.GOLD,1);gold:SetAmount(3)
local gpt=out:AddItemOfType(DealItemTypes.GOLD,1);gpt:SetAmount(2);gpt:SetDuration(30)
for i,item in ipairs(working_outgoing) do
 item.IsLocked=function() return false end; item.GetMaxAmount=function() return 500 end
end
working_incoming=working_outgoing
''')


def test_query_exposes_bound_original_current_and_acceptance_phase():
    state = GameState(IncomingConn())
    context = run(state.get_pending_deals())
    assert context.negotiation_fingerprint
    text = narrate.narrate_pending_deals(context)
    assert "ACCEPTABLE" in text
    assert "incoming_terms=" in text and "current_terms=" in text
    assert "9" in text and "83" in text


def test_gold_inspection_preserves_peace_great_work_gpt_and_never_accepts(quick_trade):
    conn = IncomingConn()
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    before = quote.negotiation_summary
    result = run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert "WILLING" in result and "not concluded" in result
    assert list(conn.lua.globals().sent_actions.values()) == [9]
    assert conn.lua.globals().requests == 0
    after = run(state.get_pending_deals())
    assert "1,1,-1,-1,100,0" in after.negotiation_summary
    for preserved in ("0,3,-1,12,0,9", "1,3,-1,12,0,9", "1,6,83,-1,1,0", "1,1,-1,-1,2,30"):
        assert preserved in before and preserved in after.negotiation_summary
    assert not state.diplomacy_pending


def test_native_rejection_even_with_equal_terms_cannot_be_accepted(quick_trade):
    conn = IncomingConn()
    conn.response_event = lambda action: '''working_incoming=working_outgoing;accept_visible=false;
Events.DiplomacyStatement.Emit(1,0,{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="AI_REFUSE_DEAL",FromPlayer=1,FromPlayerMood=0,Initiator=1,RespondingToDealAction=9,DealAction=2,ResponseType=1})'''
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    assert "REJECTED" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    result = run(state.respond_to_deal(1, True))
    assert "NOT_ACCEPTABLE" in result
    assert list(conn.lua.globals().sent_actions.values()) == [9]
    assert not list(conn.lua.globals().mutations.values())


def test_manually_edited_draft_cannot_be_finally_accepted():
    conn = IncomingConn()
    conn.setup('DealManager.AreWorkingDealsEqual=function() return false end')
    state = GameState(conn)
    context = run(state.get_pending_deals())
    assert "EDITED_UNSENT" in narrate.narrate_diplomacy_sessions(run(state.get_diplomacy_sessions()))
    assert "NOT_ACCEPTABLE" in run(state.respond_to_deal(1, True))
    assert not list(conn.lua.globals().sent_actions.values())


@pytest.mark.parametrize('change', [
    'working_outgoing[4]:SetAmount(4)',
    'GameConfiguration.GetValue=function() return 99 end',
    'sessions={[1]=72};infos[72]={FromPlayer=1,ToPlayer=0}',
    'controls["/InGame/DiplomacyDealView"].hidden=true',
    'accept_visible=false',
])
def test_changed_quote_or_world_fails_without_native_submission(change):
    conn = IncomingConn()
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    conn.setup(change)
    assert "NOT_SUBMITTED" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert not list(conn.lua.globals().sent_actions.values())


@pytest.mark.parametrize('edits', [{'our_gold': 10}, {'their_gold': 501}, {'their_gold': 0}, {'their_gold': True}, {'their_gold': 1.5}])
def test_unavailable_or_invalid_gold_does_not_mutate_any_terms(edits):
    conn = IncomingConn()
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    assert "NOT_SUBMITTED" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, **edits))
    assert not state.diplomacy_pending
    assert quote.negotiation_fingerprint == run(state.get_pending_deals()).negotiation_fingerprint
    assert not list(conn.lua.globals().sent_actions.values())


def test_lost_submission_reply_keeps_gate_and_repeat_only_confirms(quick_trade):
    conn = IncomingConn()
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    original_execute = conn.execute_in_named_state
    lost = False

    async def lose_reply(name, source, timeout=5):
        nonlocal lost
        rows = await original_execute(name, source, timeout)
        if 'INCOMING|SENT' in rows and not lost:
            lost = True
            raise OSError('reply lost after native evaluation submission')
        return rows

    conn.execute_in_named_state = lose_reply
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert state.diplomacy_pending
    assert "pending" in run(state.respond_to_deal(1, True))
    assert "WILLING" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=200))
    assert list(conn.lua.globals().sent_actions.values()) == [9]
    assert not state.diplomacy_pending


def test_equalize_returns_native_counter_without_acceptance(quick_trade):
    conn = IncomingConn()
    base_event = conn.response_event
    conn.response_event = lambda action: 'working_outgoing[4]:SetAmount(50);\n' + base_event(action)
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    result = run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100, mode='equalize'))
    assert "COUNTER_OFFER" in result and "not concluded" in result
    assert list(conn.lua.globals().sent_actions.values()) == [3]


def test_cancelled_evaluation_is_not_replayed(quick_trade):
    conn = IncomingConn()
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    original_execute = conn.execute_in_named_state
    cancelled = False

    async def cancel_reply(name, source, timeout=5):
        nonlocal cancelled
        rows = await original_execute(name, source, timeout)
        if 'INCOMING|SENT' in rows and not cancelled:
            cancelled = True
            raise asyncio.CancelledError()
        return rows

    conn.execute_in_named_state = cancel_reply
    with pytest.raises(asyncio.CancelledError):
        run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert state.diplomacy_pending
    assert "WILLING" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    assert list(conn.lua.globals().sent_actions.values()) == [9]


def test_native_delayed_reply_retains_original_turn_and_all_write_gates(quick_trade):
    conn = IncomingConn()
    state = GameState(conn)
    state._pending_end_turn = True
    quote = run(state.get_pending_deals())
    conn.deliver_events = False
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert state.turn_pending and state.diplomacy_pending
    assert "pending" in run(state.end_turn())
    assert "pending" in run(state.dismiss_popup())
    assert "UNKNOWN" in run(state.save_game("new_save"))
    assert "pending" in run(state.test_trade(2, [], []))
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=200))
    assert list(conn.lua.globals().sent_actions.values()) == [9]
    conn.deliver_events = True
    assert "WILLING" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    assert state.turn_pending and not state.diplomacy_pending


def test_world_changes_after_submission_preserve_unknown_gate(quick_trade):
    conn = IncomingConn()
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    conn.deliver_events = False
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    conn.setup('GameConfiguration.GetValue=function() return 99 end')
    conn.deliver_events = True
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    assert state.diplomacy_pending
    assert list(conn.lua.globals().sent_actions.values()) == [9]


def test_unrelated_native_event_does_not_confirm_evaluation(quick_trade):
    conn = IncomingConn()
    conn.response_event = lambda action: '''Events.DiplomacyStatement.Emit(1,0,{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="AI_ACCEPT_DEAL",FromPlayer=1,RespondingToDealAction=3,DealAction=1})'''
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert state.diplomacy_pending
    assert list(conn.lua.globals().sent_actions.values()) == [9]


def test_local_statement_echo_cannot_claim_ai_willingness(quick_trade):
    conn = IncomingConn()
    conn.response_event = lambda action: '''Events.DiplomacyStatement.Emit(0,1,{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="AI_ACCEPT_DEAL",FromPlayer=0,RespondingToDealAction=9,DealAction=1})'''
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert state.diplomacy_pending


def test_changed_war_parameter_is_part_of_quote_identity():
    conn = IncomingConn()
    conn.setup('working_outgoing[1]:SetSubType(2);war_type=1;working_outgoing[1].GetParameterValue=function() return war_type end')
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    assert "WarType=1" in quote.negotiation_summary
    conn.setup('war_type=2')
    assert "NOT_SUBMITTED" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert not list(conn.lua.globals().sent_actions.values())


@pytest.mark.parametrize('native_change', [
    'working_outgoing[4].IsLocked=function() return true end',
    'local extra=DealManager.GetWorkingDeal(DealDirection.OUTGOING,0,1):AddItemOfType(DealItemTypes.GOLD,1);extra:SetAmount(5)',
])
def test_native_locked_or_ambiguous_gold_preserves_draft(native_change):
    conn = IncomingConn()
    conn.setup(native_change)
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    assert "NOT_SUBMITTED" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert not state.diplomacy_pending
    assert quote.negotiation_fingerprint == run(state.get_pending_deals()).negotiation_fingerprint
    assert not list(conn.lua.globals().sent_actions.values())


def test_atomic_final_guard_rejects_quote_changed_after_python_check():
    conn = IncomingConn()
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    original_execute = conn.execute_in_named_state

    async def change_at_native_boundary(name, source, timeout=5):
        if 'local changes=' in source:
            conn.setup('working_outgoing[4]:SetAmount(4)')
        return await original_execute(name, source, timeout)

    conn.execute_in_named_state = change_at_native_boundary
    assert "NOT_SUBMITTED" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert not state.diplomacy_pending
    assert not list(conn.lua.globals().sent_actions.values())


def test_unknown_native_action_cannot_be_reported_as_counteroffer(quick_trade):
    conn = IncomingConn()
    conn.response_event = lambda action: '''working_outgoing[4]:SetAmount(50);working_incoming=working_outgoing;
Events.DiplomacyStatement.Emit(1,0,{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="NONE",FromPlayer=1,RespondingToDealAction=9,DealAction=999})'''
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert state.diplomacy_pending
