"""Same-turn native reopening through public GameState and real Lua 5.1."""
import asyncio
import re

import pytest
from civ_mcp.game_state import GameState
from civ_mcp.connection import LuaError
from lupa.lua51 import LuaError as NativeLuaError
from civ_mcp.lua.turn_readiness import build_turn_readiness_query
from test_end_turn import GameStateConn, clock
from test_end_turn_native import NativeTurn


class ReopenedConn(GameStateConn):
    def __init__(self):
        super().__init__()
        self.turn=57;self.seed=-1896272000;self.advance=False
        self.native=NativeTurn()
        self.delay_events=False;self.lose_reply=False
        self.native.lua.execute('''
turn=57;seed=-1896272000
native_watchers=0
local function event()
 local handlers={}
 return {Add=function(fn) if not handlers[fn] then native_watchers=native_watchers+1 end;handlers[fn]=true end,
 Remove=function(fn) if handlers[fn] then native_watchers=native_watchers-1 end;handlers[fn]=nil end,
 Fire=function(...) for fn in pairs(handlers) do fn(...) end end}
end
Events={}
for _,name in ipairs({'LocalPlayerTurnEnd','LocalPlayerTurnBegin','LocalPlayerTurnUnready',
 'LocalPlayerChanged','UnitOperationDeactivated','UnitOperationsCleared','EndTurnBlockingChanged',
 'GameCoreEventPlaybackComplete'}) do Events[name]=event() end
EndTurnBlockingTypes.ENDTURN_BLOCKING_UNITS=23669119
GameInfo={UnitOperations={UNITOPERATION_AUTOMATE_EXPLORE={Hash=-1676983098},UNITOPERATION_SKIP_TURN={Hash=10}}}
unit={id=262146,ready=false,moves=3,activity=1225574625,
 GetID=function(self) return self.id end,GetOwner=function() return 0 end,
 GetX=function() return 35 end,GetY=function() return 18 end,
 IsReadyToMove=function(self) return self.ready end,GetMovesRemaining=function(self) return self.moves end}
units={unit};ActivityTypes.ACTIVITY_HOLD=75236491
UnitManager.GetUnit=function(owner,id) if owner==0 and (id==unit.id or id==unit.id%65536) then return unit end end
UnitManager.GetActivityType=function(u) return u.activity end
UnitManager.CanStartOperation=function() return true end
UnitManager.RequestOperation=function(u,op)
 assert(op==10);unit_requests=(unit_requests or 0)+1
 u.ready=false;u.activity=75236491;u.moves=0;first_ready=nil;blocker=0
end
UI.RequestAction=function() requests=requests+1 end
''')

    def emit_reopen(self,change=""):
        self.native.lua.execute('''
Events.LocalPlayerTurnEnd.Fire()
Events.UnitOperationDeactivated.Fire(0,262146,-1676983098,1)
Events.UnitOperationsCleared.Fire(0,262146,0,0)
Events.LocalPlayerTurnBegin.Fire()
unit.ready=true;first_ready=unit;blocker=23669119
Events.EndTurnBlockingChanged.Fire(0,23669119)
Events.GameCoreEventPlaybackComplete.Fire()
'''+change)

    def run_native(self,source):
        self.native.rows.clear()
        try: self.native.lua.execute(source)
        except NativeLuaError as exc: raise LuaError(str(exc)) from exc
        return [r for r in self.native.rows if r!='---END---']

    async def execute_write(self,source):
        if 'ACTION_ENDTURN' in source:
            self.calls.append(source)
            rows=self.run_native(source)
            if self.native.lua.eval('requests')==1:
                if not self.delay_events: self.emit_reopen()
            else:
                self.turn+=1;self.native.lua.execute(f'turn={self.turn}')
                self.native.lua.execute('Events.LocalPlayerTurnBegin.Fire()')
            if self.lose_reply:
                self.lose_reply=False;raise OSError('native request reply lost')
            return rows
        if 'TURN_REOPENED|' in source or 'ACTION_SCOPE|' in source or '__civmcpReadinessRequest' in source:
            return self.run_native(source)
        if 'TURN_LOCAL|' in source:
            return self.run_native(build_turn_readiness_query())
        return await super().execute_write(source)


def test_bound_reopened_turn_requires_explicit_confirmation_then_unit_choice_and_new_attempt(clock):
    conn=ReopenedConn();state=GameState(conn)
    first=asyncio.run(state.end_turn())
    assert 'LOCAL_COMPLETION_UNCONFIRMED' in first and state.turn_pending
    before=asyncio.run(state.get_turn_status())
    token=re.search('attempt_token=([a-f0-9]{32})',before).group(1)
    assert state.turn_pending
    assert 'End-turn outcome is pending' in asyncio.run(state.skip_unit(262146))
    confirmed=asyncio.run(state.end_turn())
    assert 'REOPENED_LOCAL' in confirmed and 'no turn advanced' in confirmed
    assert 'ENDTURN_BLOCKING_UNITS' in confirmed and '262146' in confirmed
    assert not state.turn_pending and conn.native.lua.eval('requests')==1
    assert 'SKIPPED|' in asyncio.run(state.skip_unit(262146))
    assert conn.native.lua.eval('unit_requests')==1
    assert 'Turn 57 -> 58' in asyncio.run(state.end_turn())
    assert conn.native.lua.eval('requests')==2
    assert conn.native.lua.eval('__civmcpTurnAttempt.token')!=token
    assert conn.native.lua.eval('native_watchers')==0


@pytest.mark.parametrize('change',[
    '__civmcpTurnAttempt.token="another"','__civmcpTurnAttempt.schema=1',
    '__civmcpTurnAttempt.entered=false','__civmcpTurnAttempt.returned=nil',
    '__civmcpTurnAttempt.unit=2','unit.id=327682','unit.GetOwner=function() return 1 end',
    'unit.ready=false','unit.ready=nil','unit.IsReadyToMove=function() error("unit read failed") end',
    'first_ready=nil','blocker=0','sent=true','sent=nil','unready=true','unready=nil',
    'processing=true','processing=nil','core_busy=true','core_busy=nil','allowed=false','allowed=nil',
    'active=false','active=nil','complete=false','complete=nil',
    'seed=1','turn=58',"civ='CIVILIZATION_ROME'",'local_player=1',
    'UI.HasSentTurnComplete=function() error("sent read failed") end',
    'Events.LocalPlayerTurnUnready.Fire()','Events.LocalPlayerChanged.Fire(0,1)',
])
def test_ambiguous_reopened_receipt_or_native_facts_preserve_owner_and_single_submit(change,clock):
    conn=ReopenedConn();state=GameState(conn)
    asyncio.run(state.end_turn())
    conn.native.lua.execute(change)
    for _ in range(2):
        assert 'REOPENED_LOCAL' not in asyncio.run(state.end_turn())
        assert state.turn_pending
    assert conn.native.lua.eval('requests')==1
    assert 'End-turn outcome is pending' in asyncio.run(state.skip_unit(262146))


@pytest.mark.parametrize('event',[
    'LocalPlayerTurnEnd','UnitOperationDeactivated','UnitOperationsCleared',
    'LocalPlayerTurnBegin','EndTurnBlockingChanged','GameCoreEventPlaybackComplete',
])
def test_missing_required_native_observer_cannot_authorize_reopened_result(event,clock):
    conn=ReopenedConn();state=GameState(conn)
    conn.native.lua.execute(f'Events.{event}.Add=nil')
    asyncio.run(state.end_turn())
    assert 'REOPENED_LOCAL' not in asyncio.run(state.end_turn())
    assert state.turn_pending and conn.native.lua.eval('requests')==1


@pytest.mark.parametrize('event',[
    '',
    'Events.LocalPlayerTurnBegin.Fire()',
    'Events.LocalPlayerTurnEnd.Fire();Events.LocalPlayerTurnBegin.Fire()',
    'Events.LocalPlayerTurnEnd.Fire();Events.UnitOperationDeactivated.Fire(1,262146,-1676983098,1);Events.UnitOperationsCleared.Fire(0,262146,0,0);Events.LocalPlayerTurnBegin.Fire()',
    'Events.LocalPlayerTurnEnd.Fire();Events.UnitOperationDeactivated.Fire(0,327682,-1676983098,1);Events.UnitOperationsCleared.Fire(0,262146,0,0);Events.LocalPlayerTurnBegin.Fire()',
    'Events.LocalPlayerTurnEnd.Fire();Events.UnitOperationDeactivated.Fire(0,262146,999,1);Events.UnitOperationsCleared.Fire(0,262146,0,0);Events.LocalPlayerTurnBegin.Fire()',
    'Events.LocalPlayerTurnEnd.Fire();Events.UnitOperationDeactivated.Fire(0,262146,-1676983098,1);Events.UnitOperationsCleared.Fire(0,327682,0,0);Events.LocalPlayerTurnBegin.Fire()',
])
def test_current_blocker_and_playback_without_exact_request_cycle_do_not_retire(event,clock):
    conn=ReopenedConn();conn.delay_events=True;state=GameState(conn)
    asyncio.run(state.end_turn())
    conn.native.lua.execute(event+'\nunit.ready=true;first_ready=unit;blocker=23669119;Events.EndTurnBlockingChanged.Fire(0,23669119);Events.GameCoreEventPlaybackComplete.Fire()')
    assert 'REOPENED_LOCAL' not in asyncio.run(state.end_turn())
    assert state.turn_pending and conn.native.lua.eval('requests')==1


@pytest.mark.parametrize('lost_at',['submit','confirm'])
def test_lost_reopened_reply_confirms_original_without_resubmit(lost_at,clock):
    conn=ReopenedConn();state=GameState(conn)
    if lost_at=='submit': conn.lose_reply=True
    asyncio.run(state.end_turn())
    if lost_at=='confirm':
        original=conn.execute_write
        async def lost_query(source):
            rows=await original(source)
            if 'TURN_REOPENED|' in source: raise OSError('confirmation reply lost')
            return rows
        conn.execute_write=lost_query
        asyncio.run(state.end_turn())
        assert state.turn_pending
        conn.execute_write=original
    assert 'REOPENED_LOCAL' in asyncio.run(state.end_turn())
    assert not state.turn_pending and conn.native.lua.eval('requests')==1


def test_delayed_exact_native_reopening_and_status_reads_never_resubmit(clock):
    conn=ReopenedConn();conn.delay_events=True;state=GameState(conn)
    asyncio.run(state.end_turn())
    assert 'WAITING:' in asyncio.run(state.end_turn())
    conn.emit_reopen()
    asyncio.run(state.get_turn_status())
    assert state.turn_pending
    assert 'REOPENED_LOCAL' in asyncio.run(state.end_turn())
    assert conn.native.lua.eval('requests')==1


def test_unavailable_scope_read_can_recover_but_does_not_release_until_readable(clock):
    conn=ReopenedConn();state=GameState(conn);asyncio.run(state.end_turn())
    conn.native.lua.execute('originalSeedRead=GameConfiguration.GetValue;GameConfiguration.GetValue=function() return nil end')
    assert 'REOPENED_LOCAL' not in asyncio.run(state.end_turn())
    assert state.turn_pending
    conn.native.lua.execute('GameConfiguration.GetValue=originalSeedRead')
    assert 'REOPENED_LOCAL' in asyncio.run(state.end_turn())
    assert conn.native.lua.eval('requests')==1


def test_scope_change_during_final_proof_stays_invalid_after_original_scope_returns(clock):
    conn=ReopenedConn();state=GameState(conn);asyncio.run(state.end_turn())
    conn.native.lua.execute('UI.CanEndTurn=function() seed=1;return true end')
    assert 'REOPENED_LOCAL' not in asyncio.run(state.end_turn())
    conn.native.lua.execute('seed=-1896272000;UI.CanEndTurn=function() return true end')
    assert 'REOPENED_LOCAL' not in asyncio.run(state.end_turn())
    assert state.turn_pending and conn.native.lua.eval('requests')==1


@pytest.mark.parametrize('foreign,restore',[
    ('seed=1','seed=-1896272000'),('turn=58','turn=57'),('local_player=1','local_player=0'),
])
def test_native_cycle_that_crossed_scope_cannot_confirm_after_original_returns(foreign,restore,clock):
    conn=ReopenedConn();state=GameState(conn);asyncio.run(state.end_turn())
    conn.native.lua.execute(foreign+';Events.GameCoreEventPlaybackComplete.Fire();'+restore)
    assert 'REOPENED_LOCAL' not in asyncio.run(state.end_turn())
    assert state.turn_pending and conn.native.lua.eval('requests')==1


def test_vanished_and_returned_native_blocker_does_not_reuse_old_cycle(clock):
    conn=ReopenedConn();state=GameState(conn);asyncio.run(state.end_turn())
    conn.native.lua.execute('blocker=0;Events.EndTurnBlockingChanged.Fire(23669119,0);blocker=23669119;Events.EndTurnBlockingChanged.Fire(0,23669119)')
    assert 'REOPENED_LOCAL' not in asyncio.run(state.end_turn())
    assert state.turn_pending and conn.native.lua.eval('requests')==1


@pytest.mark.parametrize('cancel_at',['submit','confirm'])
def test_cancelled_reopened_native_response_keeps_original_owner_without_replay(cancel_at,clock):
    class CancelledConn(ReopenedConn):
        cancelled=False
        async def execute_write(self,source):
            rows=await super().execute_write(source)
            if not self.cancelled and (('ACTION_ENDTURN' in source) if cancel_at=='submit' else ('TURN_REOPENED|' in source)):
                self.cancelled=True;raise asyncio.CancelledError()
            return rows
    conn=CancelledConn();state=GameState(conn)
    if cancel_at=='confirm': asyncio.run(state.end_turn())
    with pytest.raises(asyncio.CancelledError): asyncio.run(state.end_turn())
    assert state.turn_pending
    assert 'REOPENED_LOCAL' in asyncio.run(state.end_turn())
    assert not state.turn_pending and conn.native.lua.eval('requests')==1


def test_native_throw_after_reopening_cycle_still_requires_returned_receipt(clock):
    conn=ReopenedConn();state=GameState(conn)
    conn.native.lua.execute('''
UI.RequestAction=function()
 requests=requests+1
 Events.LocalPlayerTurnEnd.Fire()
 Events.UnitOperationDeactivated.Fire(0,262146,-1676983098,1)
 Events.UnitOperationsCleared.Fire(0,262146,0,0)
 Events.LocalPlayerTurnBegin.Fire()
 unit.ready=true;first_ready=unit;blocker=23669119
 Events.EndTurnBlockingChanged.Fire(0,23669119)
 Events.GameCoreEventPlaybackComplete.Fire()
 error('native threw after reopening')
end
''')
    assert 'result unknown' in asyncio.run(state.end_turn())
    assert 'REOPENED_LOCAL' not in asyncio.run(state.end_turn())
    assert state.turn_pending and conn.native.lua.eval('requests')==1


def test_settled_attempt_survives_temporary_unknown_scope_on_irrelevant_playback(clock):
    conn=ReopenedConn();state=GameState(conn);asyncio.run(state.end_turn())
    conn.native.lua.execute('originalSeedRead=GameConfiguration.GetValue;GameConfiguration.GetValue=function() return nil end;Events.GameCoreEventPlaybackComplete.Fire()')
    assert 'REOPENED_LOCAL' not in asyncio.run(state.end_turn())
    assert state.turn_pending
    conn.native.lua.execute('GameConfiguration.GetValue=originalSeedRead')
    assert 'REOPENED_LOCAL' in asyncio.run(state.end_turn())
    assert conn.native.lua.eval('requests')==1


def test_lost_confirmation_reply_keeps_native_invalidation_witnesses_until_owner_retires(clock):
    conn=ReopenedConn();state=GameState(conn);asyncio.run(state.end_turn())
    original=conn.execute_write
    async def lost_query(source):
        rows=await original(source)
        if 'TURN_REOPENED|' in source: raise OSError('proof completed but reply lost')
        return rows
    conn.execute_write=lost_query
    asyncio.run(state.end_turn())
    assert state.turn_pending
    conn.execute_write=original
    conn.native.lua.execute('blocker=0;Events.EndTurnBlockingChanged.Fire(23669119,0);blocker=23669119;Events.EndTurnBlockingChanged.Fire(0,23669119)')
    assert 'REOPENED_LOCAL' not in asyncio.run(state.end_turn())
    assert state.turn_pending and conn.native.lua.eval('requests')==1


@pytest.mark.parametrize('event',[
    'Events.LocalPlayerTurnUnready.Fire()',
    'Events.LocalPlayerChanged.Fire(0,1)',
    'blocker=0;Events.EndTurnBlockingChanged.Fire(23669119,0);blocker=23669119;Events.EndTurnBlockingChanged.Fire(0,23669119)',
])
def test_scope_unknown_during_invalidating_event_keeps_settled_proof_uncertain(event,clock):
    conn=ReopenedConn();state=GameState(conn);asyncio.run(state.end_turn())
    conn.native.lua.execute('originalSeedRead=GameConfiguration.GetValue;GameConfiguration.GetValue=function() return nil end;'+event)
    conn.native.lua.execute('GameConfiguration.GetValue=originalSeedRead')
    assert 'REOPENED_LOCAL' not in asyncio.run(state.end_turn())
    assert state.turn_pending and conn.native.lua.eval('requests')==1


@pytest.mark.parametrize('event',[
    'seed=1;Events.GameCoreEventPlaybackComplete.Fire();seed=-1896272000',
    'turn=58;Events.GameCoreEventPlaybackComplete.Fire();turn=57',
    'Events.LocalPlayerTurnUnready.Fire()',
])
def test_lost_proof_reply_keeps_late_scope_and_unready_invalidation(event,clock):
    conn=ReopenedConn();state=GameState(conn);asyncio.run(state.end_turn())
    original=conn.execute_write
    async def lost_query(source):
        rows=await original(source)
        if 'TURN_REOPENED|' in source: raise OSError('proof reply lost')
        return rows
    conn.execute_write=lost_query;asyncio.run(state.end_turn())
    assert state.turn_pending and conn.native.lua.eval('native_watchers')==8
    conn.execute_write=original
    conn.native.lua.execute(event)
    assert 'REOPENED_LOCAL' not in asyncio.run(state.end_turn())
    assert state.turn_pending and conn.native.lua.eval('requests')==1
    assert conn.native.lua.eval('native_watchers')==0
