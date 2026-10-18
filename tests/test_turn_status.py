"""Public turn observation preserves uncertainty and never retires a request."""
import asyncio

from civ_mcp.game_state import GameState
from test_end_turn import GameStateConn, clock


def test_status_keeps_native_facts_and_does_not_retire_pending(clock):
    conn = GameStateConn()
    gs = GameState(conn)
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 49
    before = gs._turn_progress.baseline
    result = asyncio.run(gs.get_turn_status())
    assert "LOCAL_READY" in result
    for fact in ("local_turn_active=true", "local_turn_complete=true", "ui_processing=false",
                 "has_sent_turn_complete=false", "can_end_turn=true", "interface_mode=1"):
        assert fact in result
    assert gs.turn_pending and gs._turn_progress.baseline is before
    assert not conn.requests
    assert not any("SetInterfaceMode" in code or "AddResponse" in code for code in conn.calls)


def test_budget_is_waiting_not_hang_or_failed_read_and_elapsed_accumulates(clock):
    conn = GameStateConn()
    conn.advance = False
    gs = GameState(conn)
    first = asyncio.run(gs.end_turn())
    second = asyncio.run(gs.end_turn())
    assert "WAITING:" in first and "HANG" not in first and "queries failed" not in first
    assert "elapsed=180s" in first and "elapsed=360s" in second
    assert len(conn.requests) == 1 and gs.turn_pending


import pytest
from lupa.lua51 import LuaRuntime


class NativeStatusConn:
    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.writes = 0
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.execute('''
PlayerConfigurations={[0]={GetCivilizationTypeName=function() return 'CIVILIZATION_SUMERIA' end}}
GameConfiguration={GetValue=function() return 1 end}
active, complete, processing, sent, allowed, unready, mode = true, true, false, false, true, false, 1
Game={GetLocalPlayer=function() return 0 end, GetCurrentGameTurn=function() return 78 end}
Players={[0]={IsTurnActive=function() return active end, IsTurnActiveComplete=function() return complete end,
              CanUnreadyTurn=function() return unready end}}
UserConfiguration={IsAutoEndTurn=function() return false end,IsCityRangeAttackTurnBlocking=function() return false end}
Game.IsAllowTacticalCommands=function() return true end
Players[0].GetUnits=function() return {GetFirstReadyUnit=function() return nil end,Members=function() return ipairs({}) end} end
NotificationManager={GetFirstEndTurnBlocking=function() return 0 end,FindType=function() return nil end}
NotificationTypes={CITY_RANGE_ATTACK=15};EndTurnBlockingTypes={NO_ENDTURN_BLOCKING=0}
UI={IsGameCoreBusy=function() return false end,IsProcessingMessages=function() return processing end, HasSentTurnComplete=function() return sent end,
    CanEndTurn=function() return allowed end, GetInterfaceMode=function() return mode end,
    RequestAction=function() error("query attempted mutation") end, SetInterfaceMode=function() error("query attempted mode change") end}
''')

    async def execute_read(self, code):
        self.rows.clear()
        self.lua.execute(code)
        return [row for row in self.rows if row != "---END---"]

    async def execute_write(self, code):
        if "DIPLOMACY_PROBE" in code:
            return ["DIPLOMACY_NONE"]
        if "WC_PHASE|" in code:
            return ["WC_PHASE|false|NONE|78|NONE"]
        if "INFORMATION_POPUPS" in code:
            return ["INFORMATION_POPUPS|DONE"]
        return await self.execute_read(code)


@pytest.mark.parametrize("setup,expected,facts", [
    ("active=false;complete=false;processing=true", "WAITING_FOR_PLAYERS", ["local_turn_active=false", "ui_processing=true"]),
    ("complete=false", "LOCAL_TURN_STARTING", ["local_turn_complete=false"]),
    ("processing=true", "UI_PROCESSING", ["ui_processing=true"]),
    ("active=nil", "UNKNOWN", ["local_turn_active=unknown"]),
    ("sent=true;allowed=false;unready=true;mode=4", "LOCAL_READY", ["has_sent_turn_complete=true", "can_end_turn=false", "can_unready_turn=true", "interface_mode=4"]),
    ("UI.CanEndTurn=nil", "LOCAL_READY", ["can_end_turn=unknown"]),
])
def test_native_phase_values_stay_separate_in_public_status(setup, expected, facts):
    conn = NativeStatusConn()
    conn.lua.execute(setup)
    state = GameState(conn)
    result = asyncio.run(state.get_turn_status())
    assert result.startswith(expected)
    for fact in facts:
        assert fact in result
    assert not state.turn_pending


def test_actual_read_error_is_distinct_from_unknown_native_api():
    conn = NativeStatusConn()
    async def failed(_):
        raise ConnectionError("wire dropped")
    conn.execute_read = failed
    result = asyncio.run(GameState(conn).get_turn_status())
    assert "UNKNOWN" in result and "GameCore: wire dropped" in result
    assert "read_failures=1" in result


def test_status_reports_interactions_without_dismissing_or_responding():
    conn = NativeStatusConn()
    original = conn.execute_write
    async def interaction(code):
        if "DIPLOMACY_PROBE" in code:
            return ["DIPLOMACY|2"]
        if "INFORMATION_POPUPS" in code:
            return ["INFORMATION_POPUPS|VISIBLE|NaturalDisasterPopup", "INFORMATION_POPUPS|DONE"]
        return await original(code)
    conn.execute_write = interaction
    gs = GameState(conn)
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 78
    result = asyncio.run(gs.get_turn_status())
    assert "diplomacy response required" in result and "NaturalDisasterPopup" in result
    assert gs.turn_pending


def test_atomic_zero_submission_does_not_leave_pending(clock):
    conn = GameStateConn()
    original = conn.execute_write
    async def refused(code):
        if "ACTION_ENDTURN" in code:
            return ["END_TURN|NOT_SUBMITTED|UI_PROCESSING"]
        return await original(code)
    conn.execute_write = refused
    state = GameState(conn)
    result = asyncio.run(state.end_turn())
    assert "NOT_SUBMITTED (UI_PROCESSING)" in result
    assert not state.turn_pending and not conn.requests


def test_ambiguous_atomic_receipt_preserves_pending_and_never_resends(clock):
    conn = GameStateConn()
    original = conn.execute_write
    submissions = []
    async def lost_receipt(code):
        if "ACTION_ENDTURN" in code:
            submissions.append(code)
            return ["unexpected receipt"]
        return await original(code)
    conn.execute_write = lost_receipt
    state = GameState(conn)
    assert "result unknown" in asyncio.run(state.end_turn())
    assert state.turn_pending
    conn.turn = 51
    assert "Turn 50 -> 51" in asyncio.run(state.end_turn())
    assert len(submissions) == 1


def test_native_already_sent_is_observed_without_request(clock):
    conn = GameStateConn()
    original = conn.execute_write
    native_calls = []
    async def native_sent(code):
        if "ACTION_ENDTURN" in code:
            native_calls.append(code)
            conn.turn = 51
            return ["END_TURN|NOT_SUBMITTED|ALREADY_SENT"]
        return await original(code)
    conn.execute_write = native_sent
    state = GameState(conn)
    assert "Turn 50 -> 51" in asyncio.run(state.end_turn())
    assert not conn.requests and len(native_calls) == 1
    assert not state.turn_pending


def test_slow_other_players_over_one_budget_later_confirm_original_only(clock):
    conn = GameStateConn()
    conn.advance = False
    def native_ready(_):
        if not conn.requests:
            return True
        if clock.now >= 200:
            conn.turn = 51
            return True
        return False
    conn.readiness = native_ready
    state = GameState(conn)
    first = asyncio.run(state.end_turn())
    assert "WAITING_FOR_PLAYERS" in first and "read_failures=0" in first
    assert "queries failed" not in first and "HANG" not in first
    assert state.turn_pending
    second = asyncio.run(state.end_turn())
    assert "Turn 50 -> 51" in second
    assert not state.turn_pending and len(conn.requests) == 1


def test_already_sent_native_request_has_unknown_original_submission_time(clock):
    conn = GameStateConn()
    original = conn.execute_write
    async def native_sent(code):
        if "ACTION_ENDTURN" in code:
            return ["END_TURN|NOT_SUBMITTED|ALREADY_SENT"]
        return await original(code)
    conn.execute_write = native_sent
    state = GameState(conn)
    result = asyncio.run(state.end_turn())
    assert "elapsed=unknown" in result
    assert state.turn_pending and not conn.requests


@pytest.mark.parametrize("native_value", ["nil", '"processing"', "0"])
def test_nonboolean_native_processing_stays_unknown(native_value):
    conn = NativeStatusConn()
    conn.lua.execute('UI.IsProcessingMessages=function() return ' + native_value + ' end')
    result = asyncio.run(GameState(conn).get_turn_status())
    assert result.startswith("UNKNOWN") and "ui_processing=unknown" in result
    assert "read_failures=0" in result


def test_higher_turn_with_unknown_native_processing_cannot_retire_pending(clock):
    class PollingStatusConn(NativeStatusConn):
        async def execute_read(self, code):
            if "Game.GetWinningTeam" in code:
                return ["GAME_ACTIVE", "TURN_WORLD|CIVILIZATION_SUMERIA|1|0|78"]
            return await super().execute_read(code)
    conn = PollingStatusConn()
    conn.lua.execute('UI.IsProcessingMessages=function() return nil end')
    state = GameState(conn)
    state._pending_end_turn = True
    state._pending_end_turn_from = 77
    state._turn_progress.world = ("CIVILIZATION_SUMERIA", 1, 0)
    result = asyncio.run(state.end_turn())
    assert state.turn_pending
    assert "Turn 77 -> 78" not in result
    assert "ui_processing=unknown" in result


def native_local_status_conn(setup=''):
    from test_end_turn_native import NativeTurn
    native=NativeTurn()
    native.lua.execute(setup)
    conn=NativeStatusConn()
    conn.lua,conn.rows=native.lua,native.rows
    return conn,native


@pytest.mark.parametrize('setup,detail', [
    ('auto_end=true;first_ready=makeUnit(2228231,21,18,1)','unit_id=2228231'),
    ('range_block=true;range_notice={IsDismissed=function() return false end}','city_range_attack'),
    ('units={makeUnit(2228231,21,18,2)};queued_targets[2228231]=2;paths[2228231]={plots={1,2},turns={1,1}}','goal=20,18'),
])
def test_public_status_exposes_native_local_actions_without_clearing_pending(setup,detail):
    conn,native=native_local_status_conn(setup)
    state=GameState(conn)
    state._pending_end_turn=True;state._pending_end_turn_from=78
    result=asyncio.run(state.get_turn_status())
    assert result.startswith('LOCAL_ACTION_REQUIRED') and detail in result
    assert 'request_state=LOCAL_COMPLETION_UNCONFIRMED' in result
    assert state.turn_pending
    assert native.lua.globals().requests==0 and native.lua.globals().mode_changes==0


def test_missing_native_option_stays_unknown_without_read_error_or_pending_retirement():
    conn,native=native_local_status_conn('UserConfiguration.IsAutoEndTurn=nil;first_ready=makeUnit(2228231,21,18,1)')
    state=GameState(conn);state._pending_end_turn=True;state._pending_end_turn_from=78
    result=asyncio.run(state.get_turn_status())
    assert 'auto_end_turn=unknown' in result and 'local_actions=READINESS_UNKNOWN' in result
    assert 'read_failures=0' in result and 'request_state=LOCAL_COMPLETION_UNCONFIRMED' in result
    assert state.turn_pending and native.lua.globals().requests==0


def test_native_queued_move_preflight_returns_specific_decision_with_zero_request(clock):
    from civ_mcp.lua.turn_readiness import build_turn_readiness_query
    from test_end_turn_native import NativeTurn
    native=NativeTurn();native.lua.execute('turn=50;units={makeUnit(2228231,21,18,2)};queued_targets[2228231]=2;paths[2228231]={plots={1,2},turns={1,1}}')
    native.lua.execute(build_turn_readiness_query())
    conn=GameStateConn();conn.local_readiness=list(native.rows)
    state=GameState(conn)
    result=asyncio.run(state.end_turn())
    assert 'NOT_SUBMITTED (QUEUED_MOVE_CURRENT)' in result
    assert 'unit_id=2228231' in result and 'goal=20,18' in result
    assert not state.turn_pending and not conn.requests
    assert native.lua.eval('queued_targets[2228231]')==2


def test_queue_completion_then_new_local_action_preserves_original_turn_request(clock):
    from civ_mcp.lua.turn_readiness import build_turn_readiness_query
    from test_end_turn_native import NativeTurn
    native=NativeTurn();native.lua.execute('turn=50;auto_end=true')
    conn=GameStateConn();conn.advance=False
    def local_rows(current):
        native.rows.clear()
        if current.requests:
            native.lua.execute('first_ready=makeUnit(2228231,20,18,1)')
        native.lua.execute(build_turn_readiness_query())
        return list(native.rows)
    conn.local_readiness=local_rows
    state=GameState(conn)
    first=asyncio.run(state.end_turn())
    second=asyncio.run(state.end_turn())
    assert 'LOCAL_ACTION_REQUIRED' in first and 'LOCAL_COMPLETION_UNCONFIRMED' in first
    assert 'LOCAL_ACTION_REQUIRED' in second
    assert state.turn_pending and len(conn.requests)==1
    assert clock.now<10


def test_native_world_change_inside_observation_latches_pending_scope(clock):
    conn = GameStateConn()
    state = GameState(conn)
    conn.send_error = ConnectionError("reply lost")
    assert "result unknown" in asyncio.run(state.end_turn())
    native = NativeStatusConn()
    native.lua.execute('''
seed=1
GameConfiguration.GetValue=function() return seed end
Game.GetCurrentGameTurn=function() return 51 end
Players[0].IsTurnActive=function() seed=2;return true end
''')
    original = conn.execute_read
    async def changing_native_observation(code):
        if "TURN_STATE|" in code:
            return await native.execute_read(code)
        return await original(code)
    conn.execute_read = changing_native_observation
    result = asyncio.run(state.get_turn_status())
    assert "world_identity=mismatch" in result
    assert state.turn_pending and len(conn.requests) == 1
    conn.execute_read = original
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.end_turn())
    assert state.turn_pending and len(conn.requests) == 1
