"""Current incoming negotiations through GameState and native Lua 5.1 events."""
import asyncio
import pytest
from test_trade_progress import TradeConn, quick_trade
from civ_mcp.game_state import GameState
from civ_mcp import narrate

run = asyncio.run


@pytest.mark.parametrize("reply_error", [OSError, asyncio.CancelledError])
@pytest.mark.parametrize("reply_stage", ["send", "finish"])
def test_restored_zero_submission_can_be_confirmed_after_reply_loss(reply_error, reply_stage):
    conn = IncomingConn()
    conn.setup('''
local original=DealManager.GetWorkingDeal
DealManager.GetWorkingDeal=function(...)
 local deal=original(...)
 deal.Validate=function() return working_outgoing[4]:GetAmount()==3 and 0 or -1 end
 return deal
end
''')
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    execute = conn.execute_in_named_state

    async def lose_zero_reply(name, source, timeout=5):
        rows = await execute(name, source, timeout)
        expected = "INCOMING|NOT_SUBMITTED|INVALID_TERMS" if reply_stage == "send" else "INCOMING_FINISH|CONFIRMED"
        if expected in rows:
            conn.execute_in_named_state = execute
            raise reply_error()
        return rows

    conn.execute_in_named_state = lose_zero_reply
    if reply_error is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    else:
        assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert state.diplomacy_pending
    result = run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=200))
    assert result.startswith("ERR:NOT_SUBMITTED|")
    assert not state.diplomacy_pending
    assert run(state.get_pending_deals()).negotiation_fingerprint == quote.negotiation_fingerprint
    assert not list(conn.lua.globals().sent_actions.values())


@pytest.mark.parametrize("missing_evidence", [
    "__civmcp_incoming=nil",
    '__civmcp_incoming.token="another-attempt"',
    "__civmcp_incoming.sid=72",
    "__civmcp_incoming.target=2",
    "__civmcp_incoming.zero=nil",
    "__civmcp_incoming.sent=true",
    '__civmcp_diplomacy.nonce="another-observer"',
    "__civmcp_diplomacy.trade.response={}",
    'GameConfiguration.GetValue=function() return 99 end',
])
def test_zero_submission_missing_receipt_or_finish_evidence_keeps_gate(missing_evidence):
    conn = IncomingConn()
    conn.setup('''
local original=DealManager.GetWorkingDeal
DealManager.GetWorkingDeal=function(...)
 local deal=original(...)
 deal.Validate=function() return working_outgoing[4]:GetAmount()==3 and 0 or -1 end
 return deal
end
''')
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    execute = conn.execute_in_named_state

    async def lose_zero_reply(name, source, timeout=5):
        rows = await execute(name, source, timeout)
        if "INCOMING|NOT_SUBMITTED|INVALID_TERMS" in rows:
            raise OSError("zero reply lost")
        return rows

    conn.execute_in_named_state = lose_zero_reply
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    conn.setup(missing_evidence)
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=200))
    assert state.diplomacy_pending
    conn.setup('GameConfiguration.GetValue=function() return 7 end')
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    assert state.diplomacy_pending
    assert "UNKNOWN" in run(state.save_game("offline_should_not_save"))
    assert not list(conn.lua.globals().sent_actions.values())


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
    state._pending_end_turn_from = 78
    state._turn_progress.world = ("CIVILIZATION_SUMERIA", 7, 0)
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


def test_incoming_world_mismatch_stays_invalid_when_original_world_returns(quick_trade):
    conn = IncomingConn()
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    conn.deliver_events = False
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert not state.turn_pending
    conn.setup('GameConfiguration.GetValue=function() return 99 end')
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    conn.setup('GameConfiguration.GetValue=function() return 7 end')
    conn.deliver_events = True
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    assert state.diplomacy_pending and not state.turn_pending
    assert list(conn.lua.globals().sent_actions.values()) == [9]


@pytest.mark.parametrize("boundary", ["send", "quote"])
def test_world_change_at_send_or_final_quote_is_latched_without_pending_turn(boundary, quick_trade):
    conn = IncomingConn()
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    execute = conn.execute_in_named_state
    sent = False

    async def switch_world(name, source, timeout=5):
        nonlocal sent
        if ((boundary == "send" and "local changes=" in source)
                or (boundary == "quote" and sent and 'print("INCOMING_SCOPE|"' in source)):
            conn.setup('GameConfiguration.GetValue=function() return 99 end')
        rows = await execute(name, source, timeout)
        sent = sent or "INCOMING|SENT" in rows
        return rows

    conn.execute_in_named_state = switch_world
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    conn.execute_in_named_state = execute
    conn.setup('GameConfiguration.GetValue=function() return 7 end')
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    assert state.diplomacy_pending and not state.turn_pending
    assert list(conn.lua.globals().sent_actions.values()) == ([] if boundary == "send" else [9])


@pytest.mark.parametrize("unknown_world", [
    'GameConfiguration.GetValue=function() return nil end',
    'GameConfiguration.GetValue=function() return 0.5 end',
    'PlayerConfigurations[0].GetCivilizationTypeName=function() return "UNKNOWN" end',
])
def test_unknown_world_preserves_gate_but_does_not_latch_mismatch(unknown_world, quick_trade):
    conn = IncomingConn()
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    conn.deliver_events = False
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    conn.setup(unknown_world)
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    assert state.diplomacy_pending
    conn.setup('''GameConfiguration.GetValue=function() return 7 end
PlayerConfigurations[0].GetCivilizationTypeName=function() return "CIVILIZATION_SUMERIA" end''')
    conn.deliver_events = True
    assert "WILLING" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    assert not state.diplomacy_pending
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


@pytest.mark.parametrize('world,from_turn,invalidate', [
    (("CIVILIZATION_SUMERIA", 7, 0), 78, True),
    (("CIVILIZATION_SUMERIA", 6, 0), 78, False),
    (("CIVILIZATION_SUMERIA", 7, 1), 78, False),
    (None, 78, False),
    (("CIVILIZATION_SUMERIA", 7, 0), None, False),
])
def test_pending_turn_world_must_authorize_incoming_evaluation(world, from_turn, invalidate):
    conn = IncomingConn()
    state = GameState(conn)
    state._pending_end_turn = True
    state._pending_end_turn_from = from_turn
    state._turn_progress.world = world
    if invalidate:
        state._turn_progress.invalidate_scope()
    quote = run(state.get_pending_deals())
    assert quote.negotiation_fingerprint  # Current world's valid deal is insufficient.
    result = run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert "NOT_SUBMITTED" in result and "end-turn" in result
    assert state.turn_pending
    assert state._pending_end_turn_from == from_turn
    assert not list(conn.lua.globals().sent_actions.values())


def test_fresh_current_world_cache_cannot_authorize_old_pending_turn():
    conn = IncomingConn()
    state = GameState(conn)
    state._pending_end_turn = True
    state._pending_end_turn_from = 78
    state._turn_progress.world = ("CIVILIZATION_SUMERIA", 6, 0)
    assert run(state.get_game_identity()) == ("sumeria", 7)
    assert run(state.get_game_identity()) == ("sumeria", 7)
    quote = run(state.get_pending_deals())
    assert "NOT_SUBMITTED" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert state.turn_pending and state._pending_end_turn_from == 78
    assert not list(conn.lua.globals().sent_actions.values())


def test_zero_seed_and_origin_turn_are_valid_pending_world_binding(quick_trade):
    conn = IncomingConn()
    conn.setup('GameConfiguration.GetValue=function() return 0 end')
    state = GameState(conn)
    state._pending_end_turn = True
    state._pending_end_turn_from = 0
    state._turn_progress.world = ("CIVILIZATION_SUMERIA", 0, 0)
    quote = run(state.get_pending_deals())
    assert "WILLING" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert state.turn_pending and state._pending_end_turn_from == 0
    assert list(conn.lua.globals().sent_actions.values()) == [9]


def test_pending_turn_invalidation_during_evaluation_keeps_both_owners_pending(quick_trade):
    conn = IncomingConn()
    state = GameState(conn)
    state._pending_end_turn = True
    state._pending_end_turn_from = 78
    state._turn_progress.world = ("CIVILIZATION_SUMERIA", 7, 0)
    quote = run(state.get_pending_deals())
    conn.deliver_events = False
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    state._turn_progress.invalidate_scope()
    assert run(state.get_game_identity()) == ("sumeria", 7)
    conn.deliver_events = True
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    assert state.turn_pending and state.diplomacy_pending
    assert state._pending_end_turn_from == 78
    assert list(conn.lua.globals().sent_actions.values()) == [9]


def test_mismatching_quote_latches_turn_gate_even_after_original_world_returns():
    conn = IncomingConn()
    state = GameState(conn)
    assert run(state.get_game_identity()) == ("sumeria", 7)
    state._pending_end_turn = True
    state._pending_end_turn_from = 78
    state._turn_progress.world = ("CIVILIZATION_SUMERIA", 6, 0)
    quote = run(state.get_pending_deals())
    assert "NOT_SUBMITTED" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    conn.setup('GameConfiguration.GetValue=function() return 6 end')
    assert run(state.get_game_identity()) == ("sumeria", 6)
    quote = run(state.get_pending_deals())
    assert quote.negotiation_fingerprint
    assert "NOT_SUBMITTED" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert state.turn_pending and not list(conn.lua.globals().sent_actions.values())


def test_native_world_mismatch_during_confirmation_latches_original_turn(quick_trade):
    conn = IncomingConn()
    state = GameState(conn)
    state._pending_end_turn = True
    state._pending_end_turn_from = 78
    state._turn_progress.world = ("CIVILIZATION_SUMERIA", 7, 0)
    quote = run(state.get_pending_deals())
    conn.deliver_events = False
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    conn.setup('GameConfiguration.GetValue=function() return 99 end')
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    conn.setup('GameConfiguration.GetValue=function() return 7 end')
    conn.deliver_events = True
    assert run(state.get_game_identity()) == ("sumeria", 7)
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    assert state.turn_pending and state.diplomacy_pending
    assert list(conn.lua.globals().sent_actions.values()) == [9]


def test_turn_scope_invalidated_after_arming_never_submits_evaluation():
    conn = IncomingConn()
    state = GameState(conn)
    state._pending_end_turn = True
    state._pending_end_turn_from = 78
    state._turn_progress.world = ("CIVILIZATION_SUMERIA", 7, 0)
    quote = run(state.get_pending_deals())
    original_execute = conn.execute_in_named_state

    async def invalidate_after_arm(name, source, timeout=5):
        rows = await original_execute(name, source, timeout)
        if rows == ["INCOMING|ARMED"]:
            state._turn_progress.invalidate_scope()
        return rows

    conn.execute_in_named_state = invalidate_after_arm
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=100))
    assert state.turn_pending and state.diplomacy_pending
    assert not list(conn.lua.globals().sent_actions.values())


def test_rejected_native_terms_are_never_narrated_as_a_fair_counteroffer(quick_trade):
    conn = IncomingConn()
    conn.response_event = lambda action: '''working_incoming=working_outgoing;accept_visible=false;
Events.DiplomacyStatement.Emit(1,0,{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="AI_REFUSE_DEAL",FromPlayer=1,RespondingToDealAction=9,DealAction=2})'''
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    result = run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, their_gold=50))
    assert result.startswith("REJECTED|")
    assert "AI response: REJECTED" in result
    assert "fair" not in result and "counter-offer" not in result
    assert "will not trade at all" not in result


def test_native_great_work_identity_is_shown_instead_of_its_unused_amount(quick_trade):
    conn = IncomingConn()
    conn.setup('working_outgoing[3]:SetValueType(5);working_outgoing[3]:SetAmount(0)')
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    result = run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    assert result.startswith("WILLING|")
    assert "AI response: ACCEPTABLE" in result and "counter-offer" not in result
    assert "Great Work (id=5)" in result
    assert "GREAT_WORK (0)" not in result
    assert "Agreement (subtype=12" in result


def test_numeric_native_resource_identity_is_explicit_in_report(quick_trade):
    conn = IncomingConn()
    conn.setup('''GameInfo={Resources={[9]={Name="Iron"}}};
local resource=DealManager.GetWorkingDeal(DealDirection.OUTGOING,0,1):AddItemOfType(DealItemTypes.RESOURCES,1)
resource:SetValueType(9);resource:SetAmount(5);resource:SetDuration(30)''')
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    result = run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    assert "Resource (id=9) x5 (30 turns)" in result


@pytest.mark.parametrize('action', [1, 4, 5])
def test_equalize_same_terms_with_positive_native_action_is_willing_not_pending(action, quick_trade):
    conn = IncomingConn()
    conn.response_event = lambda sent: f'''working_incoming=working_outgoing;
Events.DiplomacyStatement.Emit(1,0,{{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="NONE",FromPlayer=1,RespondingToDealAction=3,DealAction={action}}})'''
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    result = run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, mode="equalize"))
    assert result.startswith("WILLING|") and "not concluded" in result
    assert not state.diplomacy_pending
    assert list(conn.lua.globals().sent_actions.values()) == [3]


def test_refusal_subtype_overrides_positive_equalize_action(quick_trade):
    conn = IncomingConn()
    conn.response_event = lambda sent: '''working_incoming=working_outgoing;
Events.DiplomacyStatement.Emit(1,0,{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="AI_REFUSE_DEAL",FromPlayer=1,RespondingToDealAction=3,DealAction=5})'''
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    result = run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, mode="equalize"))
    assert result.startswith("REJECTED|")
    assert not state.diplomacy_pending
    assert list(conn.lua.globals().sent_actions.values()) == [3]


def test_inspect_same_terms_nonacceptance_subtype_remains_unknown(quick_trade):
    conn = IncomingConn()
    conn.response_event = lambda sent: '''working_incoming=working_outgoing;
Events.DiplomacyStatement.Emit(1,0,{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="NONE",FromPlayer=1,RespondingToDealAction=9,DealAction=5})'''
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    assert state.diplomacy_pending


def test_turn_status_reports_incoming_event_without_confirming_or_resending(quick_trade):
    conn = IncomingConn()
    from lupa.lua51 import LuaError as NativeLuaError
    from civ_mcp.connection import LuaError
    native_write = conn.execute_write

    async def native_transport(source, timeout=None):
        try:
            return await native_write(source)
        except NativeLuaError as exc:
            raise LuaError(str(exc)) from exc

    conn.execute_read = conn.execute_write = native_transport
    conn.setup('UI.GetInterfaceMode=function() return 1 end')
    conn.response_event = lambda sent: '''working_incoming=working_outgoing;
Events.DiplomacyStatement.Emit(1,0,{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="NONE",FromPlayer=1,RespondingToDealAction=9,DealAction=5,ResponseType=7})'''
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint))
    result = run(state.get_turn_status())
    assert "expected_action=INSPECT(9)" in result
    assert "filter=ACCEPTED_EVENT" in result
    assert "subtype=NONE" in result and "deal_action=ADJUSTED(5)" in result
    assert "response_recorded=true" in result and "response_current=true" in result
    assert "incoming_evaluation_receipt: diagnostic_only=true" in result
    assert "INCOMING_RECEIPT|SENT" in result and "INCOMING_PROPOSAL|" in result
    assert state.diplomacy_pending
    assert list(conn.lua.globals().sent_actions.values()) == [9]


@pytest.mark.parametrize('response', [
    '',
    '''working_incoming=working_outgoing;accept_visible=false;
Events.DiplomacyStatement.Emit(1,0,{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="NONE",FromPlayer=1,RespondingToDealAction=3,DealAction=5})''',
    '''working_incoming=working_outgoing;
Events.DiplomacyStatement.Emit(1,0,{SessionID=71,StatementType="MAKE_DEAL",StatementSubType="NONE",FromPlayer=1,RespondingToDealAction=3,DealAction=999})''',
])
def test_equalize_without_matched_positive_action_and_native_eligibility_stays_unknown(response, quick_trade):
    conn = IncomingConn()
    conn.response_event = lambda sent: response
    state = GameState(conn)
    quote = run(state.get_pending_deals())
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, mode="equalize"))
    assert "UNKNOWN" in run(state.negotiate_incoming_trade(1, quote.negotiation_fingerprint, mode="equalize"))
    assert state.diplomacy_pending
    assert list(conn.lua.globals().sent_actions.values()) == [3]
