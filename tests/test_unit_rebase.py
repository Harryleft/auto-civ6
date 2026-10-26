"""Aircraft rebasing through the public action and real Lua; isolated native APIs."""

import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_action_lua_execution import MovementConn
from test_server import app_of, make_ctx


pytestmark = pytest.mark.usefixtures('fast_action_confirmation')


class RebaseConn(MovementConn):
    def __init__(self):
        super().__init__()
        self.lose_observation = False
        self.lose_reply = False
        self.lua.execute('''
moves, allowed, deferred, fail_request = 10, true, false, false
unit.GetOwner=function() return 0 end
unit.GetMovesRemaining=function() return moves end
GameInfo.Units[1].Domain='DOMAIN_AIR'
UnitOperationTypes.REBASE=9
UnitOperationResults={PLOTS='plots'}
targets={18018}
UnitManager.GetOperationTargets=function(u,operation)
 assert(u==unit and operation==9)
 return {plots=targets}
end
UnitManager.CanStartOperation=function(u,operation,exclusions,params)
 assert(u==unit and operation==9 and exclusions==nil)
 assert(params[2]==18 and params[3]==18 and params[4]==nil)
 checked=params
 return allowed
end
function applyRebase() x,y,moves=18,18,0 end
UnitManager.RequestOperation=function(u,operation,params)
 assert(u==unit and operation==9 and params==checked)
 submissions=submissions+1
 if not deferred then applyRebase() end
 if fail_request then error('native threw after entry') end
end
''')

    async def execute_write(self, code):
        before = self.lua.eval('submissions')
        rows = await super().execute_write(code)
        if self.lose_reply and self.lua.eval('submissions') > before:
            self.lose_reply = False
            raise OSError('rebase reply lost')
        return rows


def action(state, x=18, y=18):
    ctx = make_ctx()
    app_of(ctx).game = state
    return asyncio.run(server.unit_action(ctx, 851974, 'rebase', x, y))


def test_rebase_uses_native_operation_and_exact_target_once():
    conn = RebaseConn()
    state = GameState(conn)
    assert action(state).startswith('REBASED|unit:851974|at:18,18')
    assert conn.lua.eval('submissions') == 1
    assert conn.lua.eval('moves') == 0
    assert state.pending_action_summary is None


def test_native_rebase_targets_allow_carrier_or_city_without_ground_stacking_checks():
    conn = RebaseConn()
    conn.lua.execute('''
Map.GetUnitsAt=function() error('ground stacking is not air-base capacity') end
Map.GetPlot=function(px,py) return {
 GetIndex=function() return px+1000*py end,
 IsWater=function() return true end}
end
''')
    assert action(GameState(conn)).startswith('REBASED|')
    assert conn.lua.eval('submissions') == 1


def test_lost_reply_confirms_original_without_replaying():
    conn = RebaseConn()
    state = GameState(conn)
    conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in action(state)
    assert action(state).startswith('REBASED|')
    assert conn.lua.eval('submissions') == 1


def test_deferred_rebase_keeps_gate_until_position_and_consumption_match():
    conn = RebaseConn()
    state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in action(state)
    conn.lua.execute('x,y=18,18')
    assert 'OUTCOME_UNKNOWN' in action(state)
    assert 'OUTCOME_UNKNOWN' in action(state, 19, 18)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.skip_unit(851974))
    conn.lua.execute('moves=0')
    assert action(state).startswith('REBASED|')
    assert conn.lua.eval('submissions') == 1


@pytest.mark.parametrize('setup', [
    "GameInfo.Units[1].Domain='DOMAIN_LAND'", 'moves=0', 'allowed=false',
    'allowed=nil', 'UnitOperationTypes.REBASE=nil',
    'UnitManager.GetOperationTargets=nil', 'targets={18019}',
    'unit.GetID=function() return 917510 end', 'x,y=18,18',
    'UI.IsGameCoreBusy=function() return true end',
])
def test_rebase_native_refusal_or_unknown_eligibility_never_submits(setup):
    conn = RebaseConn()
    state = GameState(conn)
    conn.lua.execute(setup)
    assert action(state).startswith('Error: REBASE_REFUSED|')
    assert conn.lua.eval('submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('change', [
    'unit.GetID=function() return 917510 end',
    'turn=55', 'seed=1', 'moves=10', 'x=17',
])
def test_rebase_does_not_confirm_wrong_scope_unit_position_or_moves(change):
    conn = RebaseConn()
    state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in action(state)
    conn.lua.execute('applyRebase();' + change)
    assert 'OUTCOME_UNKNOWN' in action(state)
    assert conn.lua.eval('submissions') == 1


def test_rebase_native_throw_after_entry_stays_pending_without_replay():
    conn = RebaseConn()
    state = GameState(conn)
    conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in action(state)
    assert 'OUTCOME_UNKNOWN' in action(state)
    assert conn.lua.eval('submissions') == 1


def test_scope_change_during_target_check_refuses_before_submission():
    conn = RebaseConn()
    conn.lua.execute('''
local check=UnitManager.CanStartOperation
UnitManager.CanStartOperation=function(...)
 local allowed=check(...);turn=55;return allowed
end
''')
    assert action(GameState(conn)).startswith('Error: REBASE_REFUSED|')
    assert conn.lua.eval('submissions') == 0


def test_scope_change_during_native_getter_stays_invalid_after_scope_returns():
    conn = RebaseConn()
    state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in action(state)
    conn.lua.execute('applyRebase();unit.GetX=function() turn=55;return x end')
    assert 'OUTCOME_UNKNOWN' in action(state)
    conn.lua.execute('turn=54;unit.GetX=function() return x end')
    assert 'OUTCOME_UNKNOWN' in action(state)
    assert conn.lua.eval('submissions') == 1
