"""GameState plus real Lua 5.1, with only the native engine simulated."""
import asyncio

from lupa.lua51 import LuaRuntime, LuaError as NativeLuaError

from civ_mcp.game_state import GameState
from civ_mcp.connection import LuaError


class ReadyConn:
    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.execute('''
turn, seed, busy, deferred, fail_request = 41, 719665280, false, false, false
submissions, units = {}, {}
Game = {GetLocalPlayer=function() return 0 end,GetCurrentGameTurn=function() return turn end}
GameConfiguration={GetValue=function() return seed end}
PlayerConfigurations={[0]={GetCivilizationTypeName=function() return "CIVILIZATION_SUMERIA" end}}
ActivityTypes={ACTIVITY_AWAKE=0,ACTIVITY_SLEEP=1,ACTIVITY_HOLD=2,ACTIVITY_OPERATION=3}
GameInfo={UnitOperations={UNITOPERATION_SKIP_TURN={Hash=10},UNITOPERATION_FORTIFY={Hash=11},UNITOPERATION_SLEEP={Hash=12}}}
UnitOperationTypes={FORTIFY=11}
function addUnit(id, ready, activity, fortify)
    local u={id=id,ready=ready,activity=activity,fortify=fortify or 0,moves=3,allowed=true}
    u.GetID=function(self) return self.id end
    u.GetX=function() return 19 end
    u.GetY=function() return 17 end
    u.GetOwner=function() return 0 end
    u.GetMovesRemaining=function(self) return self.moves end
    u.IsReadyToMove=function(self) return self.ready end
    u.GetFortifyTurns=function(self) return self.fortify end
    units[id]=u
    return u
end
UnitManager={
 GetUnit=function(owner,id) for _,u in pairs(units) do if u.id==id or u.id%65536==id then return u end end end,
 GetActivityType=function(u) return u.activity end,
 CanStartOperation=function(u,op) return u.allowed end,
 RequestOperation=function(u,op)
    table.insert(submissions,{id=u.id,op=op})
    if fail_request then error("native failure after entry") end
    if not deferred then u.ready=false; u.activity=(op==10 and 2 or 1) end
 end,
}
Players={[0]={GetUnits=function() return {Members=function() return pairs(units) end} end}}
UI={IsGameCoreBusy=function() return busy end}
addUnit(196608,true,0,0)
''')

    async def execute_write(self, code):
        self.rows.clear()
        try:
            self.lua.execute(code)
        except NativeLuaError as exc:
            raise LuaError(str(exc)) from exc
        return [r for r in self.rows if r != '---END---']

    async def execute_read(self, code):
        raise AssertionError('native unit readiness belongs to InGame')


def test_skip_uses_native_operation_without_finish_moves_and_confirms_readiness():
    conn = ReadyConn()
    state = GameState(conn)
    result = asyncio.run(state.skip_unit(196608))
    assert result.startswith('SKIPPED')
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('submissions[1].op') == 10
    assert conn.lua.eval('units[196608].moves') == 3


def test_already_waiting_fortify_is_noop_and_reports_current_activity():
    conn = ReadyConn()
    conn.lua.execute('units[196608].ready=false; units[196608].activity=1; units[196608].fortify=2')
    state = GameState(conn)
    result = asyncio.run(state.fortify_unit(196608))
    assert result.startswith('ALREADY_WAITING')
    assert conn.lua.eval('#submissions') == 0
    assert conn.lua.eval('units[196608].activity') == 1


def test_stale_composite_unit_id_never_submits_to_reused_low_id():
    conn = ReadyConn()
    conn.lua.execute('units[196608]=nil; replacement=addUnit(262144,true,0,0); UnitManager.GetUnit=function() return replacement end')
    state = GameState(conn)
    result = asyncio.run(state.skip_unit(196608))
    assert 'UNIT_ID_CHANGED' in result
    assert conn.lua.eval('#submissions') == 0


def test_history_bonus_does_not_hide_a_ready_unit_after_healing():
    conn = ReadyConn()
    conn.lua.execute('units[196608].fortify=2')
    state = GameState(conn)
    assert asyncio.run(state.fortify_unit(196608)).startswith('FORTIFIED')
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('submissions[1].op') == 11


def test_nonready_operations_traders_and_healing_units_are_preserved():
    conn = ReadyConn()
    conn.lua.execute('units[196608].ready=false; units[196608].activity=3; addUnit(65537,false,1,2)')
    state = GameState(conn)
    assert asyncio.run(state.skip_unit(196608)).startswith('NOT_READY')
    assert asyncio.run(state.fortify_unit(65537)).startswith('ALREADY_WAITING')
    assert conn.lua.eval('#submissions') == 0
    assert conn.lua.eval('units[196608].activity') == 3


def test_fortify_uses_native_sleep_when_fortify_is_explicitly_ineligible():
    conn = ReadyConn()
    conn.lua.execute('UnitManager.CanStartOperation=function(u,op) return op==12 end')
    state = GameState(conn)
    assert asyncio.run(state.fortify_unit(196608)).startswith('SLEEPING')
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('submissions[1].op') == 12


def test_skip_refused_or_unknown_never_falls_back_to_a_different_operation():
    for native, expected in [
        ('units[196608].allowed=false', 'CANNOT_OPERATE'),
        ('UnitManager.CanStartOperation=nil', 'OPERATION_UNAVAILABLE'),
        ('UnitManager.RequestOperation=nil', 'OPERATION_UNAVAILABLE'),
        ('GameInfo.UnitOperations.UNITOPERATION_SKIP_TURN=nil', 'OPERATION_UNAVAILABLE'),
        ('units[196608].IsReadyToMove=nil', 'READINESS_UNKNOWN'),
        ('units[196608].IsReadyToMove=function() return 1 end', 'READINESS_UNKNOWN'),
        ('UnitManager.CanStartOperation=function() error("unavailable") end', 'ELIGIBILITY_UNKNOWN'),
    ]:
        conn = ReadyConn()
        conn.lua.execute(native)
        state = GameState(conn)
        assert expected in asyncio.run(state.skip_unit(196608)), native
        assert conn.lua.eval('#submissions') == 0
        assert state.pending_action_summary is None


def test_pending_skip_rechecks_readiness_without_replay_and_blocks_batch(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, 'ACTION_CONFIRM_TIMEOUT', 0.005)
    conn = ReadyConn()
    conn.lua.execute('deferred=true; addUnit(65537,true,0,0)')
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.skip_unit(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.skip_remaining_units())
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.fortify_unit(65537))
    assert conn.lua.eval('#submissions') == 1
    conn.lua.execute('units[196608].ready=false; units[196608].activity=2')
    assert asyncio.run(state.skip_unit(196608)).startswith('SKIPPED')
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions') == 1


def test_skip_confirmation_needs_idle_core_and_full_original_unit(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, 'ACTION_CONFIRM_TIMEOUT', 0.005)
    conn = ReadyConn()
    conn.lua.execute('busy=true')
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.skip_unit(0))
    conn.lua.execute('busy=false; units[196608]=nil; addUnit(262144,false,2,0)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action())
    assert conn.lua.eval('#submissions') == 1
    conn.lua.execute('units[262144]=nil; addUnit(196608,false,2,0)')
    assert asyncio.run(state.confirm_pending_action()).startswith('SKIPPED')
    assert conn.lua.eval('#submissions') == 1


def test_batch_skips_only_ready_snapshot_once_and_keeps_other_activities():
    conn = ReadyConn()
    conn.lua.execute('addUnit(65537,true,0,0); addUnit(65538,false,1,2); addUnit(65539,false,3,0)')
    state = GameState(conn)
    result = asyncio.run(state.skip_remaining_units())
    assert result.startswith('SKIPPED|2 candidates checked')
    assert conn.lua.eval('#submissions') == 2
    assert conn.lua.eval('submissions[1].id') == 65537
    assert conn.lua.eval('submissions[2].id') == 196608
    assert conn.lua.eval('submissions[1].op') == 10
    assert conn.lua.eval('submissions[2].op') == 10
    assert conn.lua.eval('units[65538].activity') == 1
    assert conn.lua.eval('units[65539].activity') == 3


def test_batch_stops_on_rejection_and_keeps_confirmed_partial_results():
    conn = ReadyConn()
    conn.lua.execute('addUnit(65537,true,0,0); addUnit(65538,true,0,0).allowed=false')
    state = GameState(conn)
    result = asyncio.run(state.skip_remaining_units())
    assert result.startswith('SKIP_BATCH_STOPPED')
    assert 'unit:65537|SKIPPED' in result
    assert 'unit:65538|Error: CANNOT_OPERATE' in result
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('units[196608].ready')
    assert state.pending_action_summary is None


def test_batch_stops_on_unconfirmed_first_unit_without_advancing_candidates(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, 'ACTION_CONFIRM_TIMEOUT', 0.005)
    conn = ReadyConn()
    conn.lua.execute('deferred=true; addUnit(65537,true,0,0)')
    state = GameState(conn)
    result = asyncio.run(state.skip_remaining_units())
    assert result.startswith('SKIP_BATCH_STOPPED')
    assert 'OUTCOME_UNKNOWN' in result
    assert state.pending_action_summary
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('submissions[1].id') == 65537
    assert conn.lua.eval('units[196608].ready')


def test_lost_skip_ack_can_be_confirmed_without_reentering_operation():
    class LoseAckConn(ReadyConn):
        async def execute_write(self, code):
            rows = await super().execute_write(code)
            if 'OK:READINESS_REQUESTED' in code:
                raise ConnectionError('ack lost after native operation')
            return rows

    conn = LoseAckConn()
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.skip_unit(196608))
    assert state.pending_action_summary
    assert asyncio.run(state.skip_unit(196608)).startswith('SKIPPED')
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions') == 1


def test_native_exception_after_entry_leaves_pending_and_never_falls_back(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, 'ACTION_CONFIRM_TIMEOUT', 0.005)
    conn = ReadyConn()
    conn.lua.execute('fail_request=true; addUnit(65537,true,0,0)')
    state = GameState(conn)
    result = asyncio.run(state.skip_remaining_units())
    assert 'SKIP_BATCH_STOPPED' in result and 'OUTCOME_UNKNOWN' in result
    assert conn.lua.eval('#submissions') == 1
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.skip_unit(196608))
    assert conn.lua.eval('#submissions') == 1
    conn.lua.execute('units[65537].ready=false; units[65537].activity=2')
    assert asyncio.run(state.confirm_pending_action()).startswith('SKIPPED')
    assert conn.lua.eval('#submissions') == 1


def test_pending_readiness_cannot_be_confirmed_in_later_turn(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, 'ACTION_CONFIRM_TIMEOUT', 0.005)
    conn = ReadyConn()
    conn.lua.execute('deferred=true')
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.fortify_unit(196608))
    conn.lua.execute('turn=42; units[196608].ready=false; units[196608].activity=1')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action())
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary


def test_one_unknown_readiness_in_batch_prevents_all_submissions():
    conn = ReadyConn()
    conn.lua.execute('addUnit(65537,true,0,0).IsReadyToMove=nil')
    state = GameState(conn)
    assert 'READINESS_UNKNOWN' in asyncio.run(state.skip_remaining_units())
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None
