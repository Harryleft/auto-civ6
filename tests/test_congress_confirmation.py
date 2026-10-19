"""Public Congress ownership with real Lua and a native-engine/transport adapter."""
import asyncio
import json

import pytest

from civ_mcp import server
from civ_mcp.connection import LuaError
from civ_mcp.game_state import GameState
from test_congress_eligibility import CongressConn, proposal_status
from test_server import app_of, make_ctx


class LifecycleCongressConn(CongressConn):
    def __init__(self):
        super().__init__(proposal_status(), stage="STAGE1", resolutions=[{"Type": 13}])
        self.drop = False
        self.cancel = False
        self.fail_read = False
        self.lua.execute('''
turn,seed=67,719665280
Game.GetCurrentGameTurn=function() return turn end
GameConfiguration.GetValue=function() return seed end
native_operation=UI.RequestPlayerOperation
native_action=UI.RequestAction
requests=0
UI.RequestPlayerOperation=function(owner,op,params)
 requests=requests+1
 native_operation(owner,op,params)
 if refuse_at==requests then return false end
 if error_at==requests then error('native operation failed after entry') end
end
UI.RequestAction=function(action)
 requests=requests+1
 native_action(action)
 if refuse_at==requests then return false end
 if error_at==requests then error('native action failed after entry') end
end
''')

    async def execute_write(self, code):
        if self.fail_read:
            raise OSError('native context read unavailable')
        before = (len(self.operations), len(self.actions), len(self.opens))
        try:
            rows = await super().execute_write(code)
        except Exception as exc:
            raise LuaError(str(exc)) from exc
        if before != (len(self.operations), len(self.actions), len(self.opens)):
            if self.cancel:
                self.cancel = False
                raise asyncio.CancelledError()
            if self.drop:
                self.drop = False
                raise OSError('native entered; transport response lost')
        return rows

    async def execute_read(self, code):
        return await self.execute_write(code)


PLAN = [{"hash": 13, "option": 1, "target": 0, "votes": 1}]


def setup():
    conn = LifecycleCongressConn()
    state = GameState(conn)
    ctx = make_ctx()
    app_of(ctx).game = state
    return conn, state, ctx


@pytest.mark.parametrize("method,args", [("vote_world_congress", (13, 1, 0, 1)), ("submit_congress", ())])
def test_direct_partial_vote_or_submit_cannot_bypass_explicit_plan(method, args):
    conn, state, _ = setup()
    result = asyncio.run(getattr(state, method)(*args))
    assert "WC_EXPLICIT_PLAN_REQUIRED" in result
    assert conn.operations == conn.actions == []


def test_lost_vote_reply_retains_plan_blocks_other_entrypoints_and_repeated_call_only_reads():
    conn, state, ctx = setup()
    conn.drop = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.queue_wc_votes(ctx, json.dumps(PLAN)))
    assert state.congress_pending
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_research('TECH_MINING'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.run_lua(ctx, 'write_without_plan=true'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.queue_wc_votes([PLAN[0] | {'option': 2}]))
    result = asyncio.run(state.queue_wc_votes(PLAN))
    assert 'CONTINUE_REQUESTED' in result
    assert state.congress_pending
    assert len(conn.operations) == 2
    assert conn.actions == ['continue']


@pytest.mark.parametrize('failure', ['false', 'error'])
@pytest.mark.parametrize('step', [1, 2, 3])
def test_native_failure_stops_remaining_phases_and_never_replays_even_after_stage_change(step, failure):
    conn, state, ctx = setup()
    conn.lua.execute(f'{"refuse_at" if failure == "false" else "error_at"}={step}')
    result = asyncio.run(server.queue_wc_votes(ctx, json.dumps(PLAN)))
    assert 'OUTCOME_UNKNOWN' in result
    assert len(conn.operations) + len(conn.actions) == step
    conn.lua.execute('turn=68; in_session=false; segment="TURNSEG_NORMAL"')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.queue_wc_votes(PLAN))
    assert state.congress_pending
    assert len(conn.operations) + len(conn.actions) == step


@pytest.mark.parametrize('next_stage,session,turn', [('TURNSEG_WORLDCONGRESS_2', True, 67), ('TURNSEG_NORMAL', False, 68)])
def test_confirmed_stage_advance_retires_only_original_plan(next_stage, session, turn):
    conn, state, ctx = setup()
    assert 'CONTINUE_REQUESTED' in asyncio.run(server.queue_wc_votes(ctx, json.dumps(PLAN)))
    assert state.congress_pending
    conn.lua.globals().segment = next_stage
    conn.lua.globals().in_session = session
    conn.lua.globals().turn = turn
    assert 'WC_STAGE_FINISHED' in asyncio.run(server.queue_wc_votes(ctx, json.dumps(PLAN)))
    assert not state.congress_pending
    assert len(conn.operations) == 2
    assert conn.actions == ['continue']


@pytest.mark.parametrize('canceled', [False, True])
def test_open_loss_or_cancellation_retains_owner_and_repeat_only_observes_visibility(canceled):
    conn, state, ctx = setup()
    conn.cancel, conn.drop = canceled, not canceled
    if canceled:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(server.open_world_congress(ctx))
    else:
        assert 'OUTCOME_UNKNOWN' in asyncio.run(server.open_world_congress(ctx))
    assert state.congress_pending
    assert 'WC_VOTING_ALREADY_OPEN' in asyncio.run(server.open_world_congress(ctx))
    assert not state.congress_pending
    assert conn.opens == ['open']
    assert conn.actions == conn.operations == []


def test_pending_congress_blocks_direct_end_turn_before_it_can_send():
    conn, state, _ = setup()
    conn.lua.execute('refuse_at=1')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.queue_wc_votes(PLAN))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    assert conn.actions == []


def test_open_already_visible_is_a_confirmed_noop():
    conn, state, _ = setup()
    assert 'WC_VOTING_OPEN' in asyncio.run(state.open_world_congress())
    assert 'WC_VOTING_ALREADY_OPEN' in asyncio.run(state.open_world_congress())
    assert not state.congress_pending
    assert conn.opens == ['open']


@pytest.mark.parametrize('change', [
    'seed=99', 'turn=66',
    'Game.GetLocalPlayer=function() return 1 end; PlayerConfigurations[1]=PlayerConfigurations[0]',
    'PlayerConfigurations[0].GetCivilizationTypeName=function() return "CIVILIZATION_CHINA" end',
])
def test_changed_world_or_backwards_turn_never_confirms_original_request(change):
    conn, state, _ = setup()
    asyncio.run(state.queue_wc_votes(PLAN))
    conn.lua.execute(change + '; in_session=false; segment="TURNSEG_NORMAL"')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.queue_wc_votes(PLAN))
    assert state.congress_pending
    assert len(conn.operations) == 2


def test_queries_do_not_retire_or_resume_a_registered_or_completed_plan():
    conn, state, ctx = setup()
    conn.lua.execute('in_session=false; segment="TURNSEG_NORMAL"')
    assert 'REGISTERED' in asyncio.run(server.queue_wc_votes(ctx, json.dumps(PLAN)))
    for _ in range(2):
        asyncio.run(server.get_world_congress(ctx))
    assert state.congress_pending
    assert conn.operations == conn.actions == []
    conn.lua.execute('in_session=true; segment="TURNSEG_WORLDCONGRESS_1"; Events.WorldCongressStage1.Fire()')
    conn.lua.execute('in_session=false; segment="TURNSEG_NORMAL"; turn=68')
    asyncio.run(server.get_world_congress(ctx))
    assert state.congress_pending
    assert len(conn.operations) == 2 and conn.actions == ['continue']


def test_cancelled_vote_retains_plan_and_defensive_copy_of_input():
    conn, state, _ = setup()
    plan = [dict(PLAN[0])]
    conn.cancel = True
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(state.queue_wc_votes(plan))
    plan[0]['option'] = 2
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.queue_wc_votes(plan))
    assert 'CONTINUE_REQUESTED' in asyncio.run(state.queue_wc_votes(PLAN))
    assert state.congress_pending
    assert conn.operations[0][2]['option'] == 1
    assert len(conn.operations) == 2


def test_original_plan_confirmation_does_not_submit_next_stage_plan():
    conn, state, _ = setup()
    asyncio.run(state.queue_wc_votes(PLAN))
    conn.lua.globals().segment = 'TURNSEG_WORLDCONGRESS_2'
    assert 'WC_STAGE_FINISHED' in asyncio.run(state.queue_wc_votes(PLAN))
    assert len(conn.operations) == 2
    assert 'WC_STAGE_CONTINUE_REQUESTED|stage:STAGE2' in asyncio.run(state.queue_wc_votes([]))
    assert len(conn.operations) == 3 and conn.actions == ['continue', 'continue']


def test_world_change_observed_then_restored_cannot_resurrect_original_receipt():
    conn, state, _ = setup()
    asyncio.run(state.get_game_identity())
    asyncio.run(state.queue_wc_votes(PLAN))
    conn.lua.execute('seed=99')
    asyncio.run(state.get_game_identity())
    conn.lua.execute('seed=719665280; in_session=false; segment="TURNSEG_NORMAL"; turn=68')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.queue_wc_votes(PLAN))
    assert state.congress_pending


def test_stage_receipt_is_finished_when_native_continuation_advances_turn():
    conn, state, _ = setup()
    asyncio.run(state.queue_wc_votes(PLAN))
    conn.lua.execute('turn=68; in_session=false; segment="TURNSEG_NORMAL"')
    status = asyncio.run(state.get_world_congress())
    assert status.local_request == 'FINISHED'
    assert state.congress_pending  # Queries describe; explicit mutation entrypoints reconcile.


def install_turn_engine(conn):
    conn.lua.execute('''
Players[0].IsTurnActive=function() return true end
Players[0].IsTurnActiveComplete=function() return true end
Players[0].CanUnreadyTurn=function() return false end
Players[0].GetUnits=function() return {GetFirstReadyUnit=function() return nil end,Members=function() return ipairs({}) end} end
Players[0].GetCities=function() return {Members=function() return ipairs({}) end} end
UserConfiguration={IsAutoEndTurn=function() return false end,IsCityRangeAttackTurnBlocking=function() return false end}
Game.IsAllowTacticalCommands=function() return true end
Game.GetWinningTeam=function() return -1 end
UI.IsProcessingMessages=function() return false end
UI.HasSentTurnComplete=function() return false end
UI.CanEndTurn=function() return true end
UI.GetInterfaceMode=function() return 1 end
UI.IsGameCoreBusy=function() return false end
InterfaceModeTypes={SELECTION=1}
NotificationManager={GetList=function() return {} end,GetFirstEndTurnBlocking=function() return 0 end,FindType=function() return nil end}
NotificationTypes={CITY_RANGE_ATTACK=15}
EndTurnBlockingTypes={NO_ENDTURN_BLOCKING=0}
DiplomacyManager={HasQueuedSession=function() return false end,FindOpenSessionID=function() return nil end}
local lookup=ContextPtr.LookUpControl
ContextPtr.LookUpControl=function(self,path)
 if path=="/InGame/DiplomacyActionView" or path=="/InGame/DiplomacyDealView" then return {IsHidden=function() return true end} end
 return lookup(self,path)
end
local action=UI.RequestAction
UI.RequestAction=function(value)
 action(value)
 if not in_session then
  in_session=true;segment="TURNSEG_WORLDCONGRESS_1";Events.WorldCongressStage1.Fire()
 else
  turn=68;in_session=false;segment="TURNSEG_NORMAL"
 end
end
''')


def test_registered_plan_allows_explicit_end_turn_to_trigger_native_handler():
    conn, state, ctx = setup()
    install_turn_engine(conn)
    conn.lua.execute('in_session=false; segment="TURNSEG_NORMAL"')
    assert 'REGISTERED' in asyncio.run(server.queue_wc_votes(ctx, json.dumps(PLAN)))
    result = asyncio.run(server.end_turn(ctx))
    assert 'result unknown' in result  # The synchronous native event also printed its Congress receipt.
    assert conn.actions == ['continue', 'continue']  # Ordinary end-turn then Congress continuation.
    assert len(conn.operations) == 2
    assert state.congress_pending
    assert 'Turn 67 -> 68' in asyncio.run(server.end_turn(ctx))
    assert not state.congress_pending
    assert len(conn.operations) == 2 and len(conn.actions) == 2


def test_phase_change_between_scope_read_and_entry_refuses_without_registering_new_plan():
    conn, state, _ = setup()
    conn.lua.execute('''
reads=0
Game.GetCurrentTurnSegment=function()
 reads=reads+1
 if reads>1 then segment="TURNSEG_WORLDCONGRESS_2" end
 return segment
end
''')
    result = asyncio.run(state.queue_wc_votes([]))
    assert 'WC_SCOPE_REFUSED' in result
    assert not state.congress_pending
    assert conn.operations == conn.actions == []


@pytest.mark.parametrize('changed_world', [False, True])
def test_explicit_congress_plan_can_resolve_only_its_pending_turn_world(changed_world):
    conn, state, ctx = setup()
    install_turn_engine(conn)
    conn.lua.execute('in_session=false; segment="TURNSEG_NORMAL"')
    assert 'World Congress voting stage active' in asyncio.run(server.end_turn(ctx))
    assert state.turn_pending
    assert conn.actions == ['continue'] and conn.operations == []
    if changed_world:
        conn.lua.execute('seed=999')
    result = asyncio.run(server.queue_wc_votes(ctx, json.dumps(PLAN)))
    if changed_world:
        assert 'OUTCOME_UNKNOWN' in result
        assert conn.operations == [] and conn.actions == ['continue']
    else:
        assert 'WC_STAGE_FINISHED' in result
        assert len(conn.operations) == 2 and len(conn.actions) == 2
        assert 'Turn 67 -> 68' in asyncio.run(server.end_turn(ctx))
        assert not state.turn_pending and not state.congress_pending


def test_registered_receipt_read_failure_does_not_allow_end_turn_from_cached_registration():
    conn, state, _ = setup()
    conn.lua.execute('in_session=false; segment="TURNSEG_NORMAL"')
    assert 'REGISTERED' in asyncio.run(state.queue_wc_votes(PLAN))
    conn.fail_read = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    assert state.congress_pending
    assert conn.operations == conn.actions == []


def test_registered_plan_native_rejection_after_turn_change_releases_without_voting():
    conn, state, _ = setup()
    conn.lua.execute('in_session=false; segment="TURNSEG_NORMAL"')
    assert 'REGISTERED' in asyncio.run(state.queue_wc_votes(PLAN))
    conn.lua.execute('turn=68; in_session=true; segment="TURNSEG_WORLDCONGRESS_1"; Events.WorldCongressStage1.Fire()')
    assert 'WC_VOTE_PREFERENCES' in asyncio.run(state.queue_wc_votes(PLAN))
    assert not state.congress_pending
    assert conn.operations == conn.actions == []


def test_missing_open_context_is_known_zero_submission_and_does_not_leave_a_gate():
    conn, state, _ = setup()
    conn.lua.execute('old_lookup=ContextPtr.LookUpControl; ContextPtr.LookUpControl=function() return nil end')
    assert 'WC_OPEN_REFUSED' in asyncio.run(state.open_world_congress())
    assert not state.congress_pending
    conn.lua.execute('ContextPtr.LookUpControl=old_lookup')
    assert 'WC_VOTING_OPEN' in asyncio.run(state.open_world_congress())
    assert conn.opens == ['open']
