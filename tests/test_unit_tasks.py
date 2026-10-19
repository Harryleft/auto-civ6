"""Unit task selection through public MCP/GameState and real native Lua."""
import asyncio
import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx, app_of
from test_unit_readiness import ReadyConn


class TaskConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply=False
        self.lua.execute('''
ActivityTypes.ACTIVITY_HEAL=4;ActivityTypes.ACTIVITY_SENTRY=5
GameInfo.UnitOperations.UNITOPERATION_AUTOMATE_EXPLORE={Hash=20}
GameInfo.UnitOperations.UNITOPERATION_HEAL={Hash=21}
GameInfo.UnitOperations.UNITOPERATION_ALERT={Hash=22}
function event()
 local listeners={}
 return {Add=function(f) listeners[f]=true end,Remove=function(f) listeners[f]=nil end,
 Fire=function(...) for f in pairs(listeners) do f(...) end end}
end
Events={UnitOperationStarted=event(),UnitOperationAdded=event()}
u=units[196608];u.damage=25
u.GetDamage=function(self) return self.damage end
u.GetMaxDamage=function() return 100 end
UnitManager.CanStartOperation=function(unit,op,params,check)
 assert(params==nil and check==true)
 return unit.allowed
end
function applyTask(op)
 u.ready=false
 u.activity=({[12]=1,[20]=3,[21]=4,[22]=5})[op]
 Events.UnitOperationStarted.Fire(0,u.id,op)
end
UnitManager.RequestOperation=function(unit,op,params)
 table.insert(submissions,{id=unit.id,op=op})
 if not deferred then applyTask(op) end
 if fail_request then error('native request threw after entry') end
end
''')

    async def execute_write(self,code):
        before=self.lua.eval('#submissions')
        rows=await super().execute_write(code)
        if self.lose_reply and self.lua.eval('#submissions')>before:
            self.lose_reply=False
            raise OSError('task applied but response lost')
        return rows


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT',.01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL',.001)


def test_lost_automate_reply_is_confirmed_without_replacing_native_task():
    conn=TaskConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,'automate'))
    assert asyncio.run(server.unit_action(ctx,196608,'automate')).startswith('AUTOMATED|unit:196608')
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('u.moves')==3
    assert state.pending_action_summary is None


@pytest.mark.parametrize('kind,label',[('heal','HEALING'),('alert','ALERT'),('sleep','SLEEPING')])
def test_native_task_event_and_activity_complete_requested_selection(kind,label):
    conn=TaskConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,kind))
    result=asyncio.run(server.unit_action(ctx,196608,kind))
    assert result.startswith(label+'|unit:196608')
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('u.moves')==3
    assert conn.lua.eval('u.damage')==25


@pytest.mark.parametrize('kind,setup,label',[
    ('heal','u.damage=0','ALREADY_HEALTHY'),
    ('heal','u.ready=false;u.activity=4','ALREADY_HEALING'),
    ('alert','u.ready=false;u.activity=5','ALREADY_ALERT'),
    ('sleep','u.ready=false;u.activity=1','ALREADY_SLEEPING'),
])
def test_already_satisfied_native_goal_is_noop(kind,setup,label):
    conn=TaskConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute(setup+';u.allowed=false')
    assert asyncio.run(server.unit_action(ctx,196608,kind)).startswith(label+'|')
    assert conn.lua.eval('#submissions')==0
    assert conn.lua.eval('u.moves')==3
    assert state.pending_action_summary is None


TASKS=[('automate',20,'AUTOMATED'),('heal',21,'HEALING'),('alert',22,'ALERT'),('sleep',12,'SLEEPING')]


@pytest.mark.parametrize('kind,op,label',TASKS)
def test_delayed_task_needs_exact_native_start_and_fresh_nonready_state(kind,op,label):
    conn=TaskConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,kind))
    conn.lua.execute('u.ready=false;u.activity=2')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,kind))
    conn.lua.execute(f'Events.UnitOperationStarted.Fire(0,262144,{op});Events.UnitOperationAdded.Fire(0,196608,999)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,kind))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.upgrade_unit(196608))
    conn.lua.execute(f'applyTask({op})')
    assert asyncio.run(server.unit_action(ctx,196608,kind)).startswith(label+'|')
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('u.moves')==3


@pytest.mark.parametrize('kind,op,label',TASKS[1:])
def test_correct_event_cannot_substitute_for_wrong_native_activity(kind,op,label):
    conn=TaskConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,kind))
    conn.lua.execute(f'applyTask({op});u.activity=2')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,kind))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('setup',[
    'u.allowed=false','u.allowed=nil','UnitManager.CanStartOperation=nil',
    'UnitManager.CanStartOperation=function() error("unknown") end',
    'UnitManager.RequestOperation=nil','Events={}','u.id=262144',
    'u.IsReadyToMove=nil','busy=true','busy=nil',
    'GameInfo.UnitOperations.UNITOPERATION_AUTOMATE_EXPLORE=nil',
])
def test_task_preflight_refusal_is_zero_submission(setup):
    conn=TaskConn();state=GameState(conn);conn.lua.execute(setup)
    assert 'UNIT_TASK_REFUSED' in asyncio.run(state.automate_explore(196608))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('kind,op,label',TASKS)
def test_native_throw_after_task_start_never_releases_or_replays(kind,op,label):
    conn=TaskConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,kind))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,kind))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('kind,op,label',TASKS)
def test_cancellation_after_native_task_retains_original_request(kind,op,label):
    class CancelConn(TaskConn):
        async def execute_write(self,code):
            before=self.lua.eval('#submissions')
            rows=await super().execute_write(code)
            if self.lua.eval('#submissions')>before: raise asyncio.CancelledError()
            return rows
    conn=CancelConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    with pytest.raises(asyncio.CancelledError): asyncio.run(server.unit_action(ctx,196608,kind))
    assert state.pending_action_summary is not None
    assert asyncio.run(server.unit_action(ctx,196608,kind)).startswith(label+'|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('kind,op,label',TASKS)
def test_stale_full_id_never_selects_task_on_replacement(kind,op,label):
    conn=TaskConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('u.id=262144')
    assert 'UNIT_ID_CHANGED' in asyncio.run(server.unit_action(ctx,196608,kind))
    assert conn.lua.eval('#submissions')==0


@pytest.mark.parametrize('kind,activity',[('heal','ACTIVITY_HEAL'),('alert','ACTIVITY_SENTRY')])
def test_missing_optional_activity_enum_confirms_only_observed_task_start(kind,activity):
    conn=TaskConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('ActivityTypes.'+activity+'=nil')
    assert 'Native task started; completion not measured.' in asyncio.run(server.unit_action(ctx,196608,kind))
    assert conn.lua.eval('#submissions')==1


def test_missing_sleep_activity_prevents_unverifiable_task_submission():
    conn=TaskConn();state=GameState(conn);conn.lua.execute('ActivityTypes.ACTIVITY_SLEEP=nil')
    assert 'ACTIVITY_UNAVAILABLE' in asyncio.run(state.sleep_unit(196608))
    assert conn.lua.eval('#submissions')==0


def test_added_event_can_prove_start_when_started_event_is_unavailable():
    conn=TaskConn();state=GameState(conn)
    conn.lua.execute('''
Events.UnitOperationStarted=nil
function applyTask(op)
 u.ready=false;u.activity=3;Events.UnitOperationAdded.Fire(0,u.id,op)
end
''')
    assert asyncio.run(state.automate_explore(196608)).startswith('AUTOMATED|')
    assert conn.lua.eval('#submissions')==1


def test_unknown_task_blocks_other_unit_intents_and_empire_mutations():
    conn=TaskConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('deferred=true;addUnit(196609,true,0,0)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.automate_explore(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196609,'sleep'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_research('TECH_POTTERY'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.save_game('task_pending'))
    assert conn.lua.eval('#submissions')==1


def test_scope_change_before_native_entry_proves_no_submission():
    class ChangedConn(TaskConn):
        async def execute_write(self,code):
            if '-- ACTION_SUBMIT_SCOPE|' in code: self.lua.execute('turn=42')
            return await super().execute_write(code)
    conn=ChangedConn();state=GameState(conn)
    assert 'ACTION_SCOPE_NO_SUBMIT' in asyncio.run(state.automate_explore(196608))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


def test_observed_changed_world_stays_invalid_after_original_world_returns():
    conn=TaskConn();state=GameState(conn);conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.automate_explore(196608))
    conn.lua.execute('seed=9')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.automate_explore(196608))
    conn.lua.execute('seed=719665280')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.automate_explore(196608))
    assert conn.lua.eval('#submissions')==1


def test_confirmation_read_error_cannot_turn_into_a_second_request():
    class ReadErrorConn(TaskConn):
        async def execute_write(self,code):
            if self.lua.eval('#submissions'): raise OSError('readback unavailable')
            return await super().execute_write(code)
    conn=ReadErrorConn();state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.automate_explore(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.automate_explore(196608))
    assert conn.lua.eval('#submissions')==1


def test_legacy_low_id_mcp_repeat_only_confirms_original_task():
    conn=TaskConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state;conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,0,'automate'))
    assert asyncio.run(server.unit_action(ctx,0,'automate')).startswith('AUTOMATED|unit:196608')
    assert conn.lua.eval('#submissions')==1


def test_legacy_low_id_requires_readable_full_native_identity_before_submit():
    conn=TaskConn();state=GameState(conn);conn.lua.execute('u.GetID=function() return nil end')
    assert 'UNIT_TASK_REFUSED' in asyncio.run(state.automate_explore(0))
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions')==0
