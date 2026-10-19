"""Final incoming responses use the same native observer and retained attempt."""
import asyncio
import pytest
from test_incoming_negotiation import IncomingConn
from test_trade_progress import quick_trade
from civ_mcp.game_state import GameState

@pytest.mark.parametrize('accept',[True,False])
def test_lost_final_reply_remains_owned_and_is_never_resent(accept,quick_trade):
    async def scenario():
        conn=IncomingConn();state=GameState(conn)
        conn.deliver_events=False
        execute=conn.execute_in_named_state
        lost=False
        async def native(name,source,timeout=5):
            nonlocal lost
            rows=await execute(name,source,timeout)
            if len(conn.lua.globals().sent_actions) and not lost:
                lost=True
                raise OSError('lost final response')
            return rows
        conn.execute_in_named_state=native
        first=await state.respond_to_deal(1,accept)
        assert 'OUTCOME_UNKNOWN' in first
        assert state.diplomacy_pending
        assert 'OUTCOME_UNKNOWN' in await state.respond_to_deal(1,accept)
        assert list(conn.lua.globals().sent_actions.values())==[1 if accept else 2]
        conn.deliver_events=True
        final=await state.respond_to_deal(1,accept)
        assert final.startswith('DEAL_ACCEPTED|' if accept else 'DEAL_REJECTED|'),final
        assert not state.diplomacy_pending
        assert list(conn.lua.globals().sent_actions.values())==[1 if accept else 2]
    asyncio.run(scenario())

@pytest.mark.parametrize('accept',[True,False])
@pytest.mark.parametrize('failure',['throw','false','cancel'])
def test_native_response_interruption_cannot_replay_or_switch_intent(accept,failure,quick_trade):
    async def scenario():
        from civ_mcp.connection import LuaError
        from lupa.lua51 import LuaError as NativeLuaError
        conn=IncomingConn();state=GameState(conn)
        conn.deliver_events=False
        conn.lua.globals().cancel_now=lambda: (_ for _ in ()).throw(asyncio.CancelledError())
        conn.setup('''local original=DealManager.SendWorkingDeal
DealManager.SendWorkingDeal=function(...)
 original(...)
 '''+('error("native failure")' if failure=='throw' else 'return false' if failure=='false' else 'cancel_now()')+''' end''')
        original=conn.execute_in_named_state
        async def adapter(*args,**kwargs):
            try:return await original(*args,**kwargs)
            except NativeLuaError as exc:raise LuaError(str(exc)) from exc
        conn.execute_in_named_state=adapter
        try:
            assert 'OUTCOME_UNKNOWN' in await state.respond_to_deal(1,accept)
        except asyncio.CancelledError:
            assert failure=='cancel'
        count=list(conn.lua.globals().sent_actions.values())
        assert count==[1 if accept else 2]
        assert 'OUTCOME_UNKNOWN' in await state.respond_to_deal(1,not accept)
        assert 'OUTCOME_UNKNOWN' in await state.respond_to_deal(2,accept)
        await state.get_pending_deals()
        await state.get_diplomacy_sessions()
        assert 'OUTCOME_UNKNOWN' in await state.respond_to_deal(1,accept)
        assert list(conn.lua.globals().sent_actions.values())==count
        assert state.diplomacy_pending
    asyncio.run(scenario())

@pytest.mark.parametrize('replacement',[
    'StatementSubType="AI_ACCEPT_DEAL"',
    'FromPlayer=2',
    'SessionID=72',
    'RespondingToDealAction=1',
    'DealAction=1',
])
def test_final_accept_requires_exact_new_native_human_accept_event(replacement,quick_trade):
    conn=IncomingConn();state=GameState(conn)
    original=conn.response_event
    old={'StatementSubType':'StatementSubType="HUMAN_ACCEPT_DEAL"','FromPlayer':'FromPlayer=1','SessionID':'SessionID=71','RespondingToDealAction':'RespondingToDealAction=0','DealAction':'DealAction=0'}[replacement.split('=')[0]]
    conn.response_event=lambda action:original(action).replace(old,replacement)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.respond_to_deal(1,True))
    assert state.diplomacy_pending
    assert list(conn.lua.globals().sent_actions.values())==[1]

@pytest.mark.parametrize('accept',[True,False])
def test_changed_world_cannot_finish_old_final_response_even_when_restored(accept,quick_trade):
    async def scenario():
        conn=IncomingConn();state=GameState(conn);conn.deliver_events=False
        assert 'OUTCOME_UNKNOWN' in await state.respond_to_deal(1,accept)
        conn.setup('GameConfiguration.GetValue=function() return 99 end')
        assert 'OUTCOME_UNKNOWN' in await state.respond_to_deal(1,accept)
        conn.setup('GameConfiguration.GetValue=function() return 7 end')
        conn.deliver_events=True
        assert 'OUTCOME_UNKNOWN' in await state.respond_to_deal(1,accept)
        assert state.diplomacy_pending
        assert list(conn.lua.globals().sent_actions.values())==[1 if accept else 2]
    asyncio.run(scenario())

@pytest.mark.parametrize('accept',[True,False])
def test_lost_final_confirmation_reply_does_not_repeat_response(accept,quick_trade):
    async def scenario():
        conn=IncomingConn();state=GameState(conn)
        original=conn.execute_in_named_state
        lost=False
        async def execute(*args,**kwargs):
            nonlocal lost
            rows=await original(*args,**kwargs)
            if 'INCOMING_FINAL|CONFIRMED' in rows and not lost:
                lost=True
                raise OSError('final acknowledgement lost')
            return rows
        conn.execute_in_named_state=execute
        assert 'OUTCOME_UNKNOWN' in await state.respond_to_deal(1,accept)
        assert (await state.respond_to_deal(1,accept)).startswith('DEAL_ACCEPTED|' if accept else 'DEAL_REJECTED|')
        assert list(conn.lua.globals().sent_actions.values())==[1 if accept else 2]
    asyncio.run(scenario())

@pytest.mark.parametrize('failure',['throw','false'])
def test_native_send_must_return_normally_even_when_accept_event_arrives(failure,quick_trade):
    async def scenario():
        from civ_mcp.connection import LuaError
        from lupa.lua51 import LuaError as NativeLuaError
        conn=IncomingConn();gs=GameState(conn)
        conn.setup('''local original=DealManager.SendWorkingDeal
DealManager.SendWorkingDeal=function(...)
 original(...)
 '''+('error("send interrupted")' if failure=='throw' else 'return false')+''' end''')
        original=conn.execute_in_named_state
        async def native(*args,**kwargs):
            try:return await original(*args,**kwargs)
            except NativeLuaError as exc:raise LuaError(str(exc)) from exc
        conn.execute_in_named_state=native
        assert 'OUTCOME_UNKNOWN' in await gs.respond_to_deal(1,True)
        assert 'OUTCOME_UNKNOWN' in await gs.respond_to_deal(1,True)
        assert list(conn.lua.globals().sent_actions.values())==[1]
    asyncio.run(scenario())

def test_public_mcp_final_response_confirms_without_replay(quick_trade):
    from civ_mcp import server
    from test_server import make_ctx,app_of
    async def scenario():
        conn=IncomingConn();ctx=make_ctx();app_of(ctx).game=GameState(conn)
        conn.deliver_events=False
        assert 'OUTCOME_UNKNOWN' in await server.respond_to_trade(ctx,1,True)
        conn.deliver_events=True
        assert (await server.respond_to_trade(ctx,1,True)).startswith('DEAL_ACCEPTED|')
        assert list(conn.lua.globals().sent_actions.values())==[1]
    asyncio.run(scenario())

@pytest.mark.parametrize('accept',[True,False])
def test_existing_incoming_demand_can_be_finally_answered_without_evaluation(accept,quick_trade):
    conn=IncomingConn();state=GameState(conn)
    conn.setup('ms_bIsDemand=true')
    original=conn.response_event
    conn.response_event=lambda action:original(action).replace('StatementType="MAKE_DEAL"','StatementType="MAKE_DEMAND"')
    result=asyncio.run(state.respond_to_deal(1,accept))
    assert result.startswith('DEAL_ACCEPTED|' if accept else 'DEAL_REJECTED|'),result
    assert list(conn.lua.globals().sent_actions.values())==[1 if accept else 2]
    assert not state.diplomacy_pending
