"""Cross-entry acceptance through public MCP/GameState and the actual Lua builders."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx, app_of
from test_unit_readiness import ReadyConn
from test_move_target_lua import TargetMovementConn
from test_attack_mode_lua import AttackConn

run = asyncio.run


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def context(state):
    ctx = make_ctx()
    app_of(ctx).game = state
    return ctx


@pytest.mark.parametrize('action', ['skip', 'fortify'])
def test_mcp_preserves_full_identity_when_low_unit_index_is_reused(action):
    conn = ReadyConn()
    conn.lua.execute('units[196608]=nil; replacement=addUnit(262144,true,0); UnitManager.GetUnit=function() return replacement end')
    result = run(server.unit_action(context(GameState(conn)), 196608, action))
    assert 'UNIT_ID_CHANGED' in result
    assert conn.lua.eval('#submissions') == 0


@pytest.mark.parametrize('action', ['move', 'attack', 'found_city'])
def test_mcp_legacy_unit_mutations_reject_replacement_before_native_request(action):
    conn = TargetMovementConn() if action != 'attack' else AttackConn()
    conn.lua.execute('unit.GetID=function() return 917510 end')
    if action != 'attack':
        conn.lua.execute('can_target=true; UnitOperationTypes.FOUND_CITY=99')
    result = run(server.unit_action(context(GameState(conn)), 851974, action, target_x=18, target_y=18))
    assert 'UNIT_ID_CHANGED' in result
    assert (conn.lua.eval('submissions') if action != 'attack' else len(conn.operations)) == 0


@pytest.mark.parametrize("unit_id", [851974, 6])
def test_movement_destination_of_reused_identity_cannot_confirm_original_request(unit_id):
    conn = TargetMovementConn()
    conn.lua.execute('can_target=true; perform_move=false')
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in run(state.move_unit(unit_id, 18, 18))
    conn.lua.execute('unit.GetID=function() return 917510 end; x,y=18,18')
    assert 'OUTCOME_UNKNOWN' in run(state.move_unit(unit_id, 18, 18))
    assert conn.lua.eval('submissions') == 1


@pytest.mark.parametrize('scope_change,entry', [('turn=42','readback'), ('seed=1','readback'), ('seed=1','identity_query')])
def test_legacy_pending_latches_observed_world_or_turn_change(scope_change, entry):
    conn = ReadyConn(); state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.skip_unit(196608))
    conn.lua.execute(scope_change)
    if entry == 'identity_query':
        run(state.get_game_identity())
    else:
        assert 'OUTCOME_UNKNOWN' in run(state.confirm_pending_action(wait=False))
    conn.lua.execute('turn=41; seed=719665280; units[196608].ready=false; units[196608].activity=2')
    assert 'OUTCOME_UNKNOWN' in run(state.skip_unit(196608))
    assert conn.lua.eval('#submissions') == 1


class ProductionConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
city_id=65537; production="UNIT_OLD"; production_deferred=false
city={GetID=function() return city_id end,GetOwner=function() return 0 end}
city.GetBuildQueue=function() return {CanProduce=function() return true end,
 GetTurnsLeft=function() return 3 end,CurrentlyBuilding=function() return production end} end
Players[0].GetCities=function() return {FindID=function() return city end} end
GameInfo.Units={UNIT_WARRIOR={Hash=10}}
CityOperationTypes={BUILD=1,PARAM_UNIT_TYPE='unit',PARAM_INSERT_MODE='insert',VALUE_EXCLUSIVE=1}
CityManager={GetCity=function() return city end,CanStartOperation=function() return true end,
 RequestOperation=function() table.insert(submissions,'production');if not production_deferred then production='UNIT_WARRIOR' end end}
''')

    async def execute_read(self, code):
        return await self.execute_write(code)


def test_production_submission_rejects_reused_full_city_identity():
    conn = ProductionConn(); conn.lua.execute('city_id=131073')
    result = run(GameState(conn).set_city_production(65537, 'UNIT', 'UNIT_WARRIOR'))
    assert 'CITY_ID_CHANGED' in result
    assert conn.lua.eval('#submissions') == 0


def test_production_readback_rejects_same_queue_in_replacement_city():
    conn = ProductionConn(); state = GameState(conn)
    conn.lua.execute('production_deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.set_city_production(65537, 'UNIT', 'UNIT_WARRIOR'))
    conn.lua.execute('city_id=131073; production="UNIT_WARRIOR"')
    assert 'OUTCOME_UNKNOWN' in run(state.set_city_production(65537, 'UNIT', 'UNIT_WARRIOR'))
    assert conn.lua.eval('#submissions') == 1

from test_terminal_boundary_lua import SaveBoundaryConn, save_environment
from test_trade_progress import TradeConn, quick_trade
from test_end_turn import GameStateConn


@pytest.fixture
def process_adapter(monkeypatch):
    """Only the native process boundary is replaced; no real game can start/stop."""
    from civ_mcp import game_launcher
    calls = []
    async def start():
        calls.append('start')
        return game_launcher.ProcessResult(False, 'adapter startup unconfirmed')
    async def stop():
        calls.append('stop')
        return game_launcher.ProcessResult(False, 'adapter shutdown unconfirmed')
    monkeypatch.setattr(game_launcher, 'start_game', start)
    monkeypatch.setattr(game_launcher, 'stop_game', stop)
    return calls


def isolate_disconnect(conn, calls):
    async def disconnect():
        calls.append('disconnect')
    conn.disconnect = disconnect


LIFECYCLE_MUTATIONS = [
    ('kill_game', ()), ('launch_game', ()), ('execute_lua', ('intrusion=true', 'ingame')),
    ('load_game_save', ('checkpoint',)), ('reload_game_save', ('checkpoint',)),
    ('open_world_congress', ()), ('queue_wc_votes', ([],)),
]


@pytest.mark.parametrize('method,args', LIFECYCLE_MUTATIONS + [('dismiss_popup', ()), ('end_turn', ())])
def test_direct_calls_preserve_unknown_save_barrier(method, args, save_environment, process_adapter):
    conn = SaveBoundaryConn(save_environment); state = GameState(conn)
    isolate_disconnect(conn, process_adapter)
    conn.lua.execute('winner=nil; finish_save=false')
    assert 'OUTCOME_UNKNOWN' in run(state.save_game('pending'))
    calls = len(conn.calls)
    assert 'OUTCOME_UNKNOWN' in run(getattr(state, method)(*args))
    assert len(conn.calls) == calls and process_adapter == []
    assert conn.lua.globals().save_requests == 1 and state.save_pending


@pytest.mark.parametrize('method,args', LIFECYCLE_MUTATIONS + [('dismiss_popup', ()), ('end_turn', ())])
def test_direct_calls_preserve_unknown_replacement_barrier(method, args, process_adapter):
    conn = ReadyConn(); state = GameState(conn)
    isolate_disconnect(conn, process_adapter)
    assert 'shutdown unconfirmed' in run(state.kill_game())
    assert state.replacement_pending
    process_adapter.clear()
    assert 'OUTCOME_UNKNOWN' in run(getattr(state, method)(*args))
    assert process_adapter == [] and state.replacement_pending


@pytest.mark.parametrize('method,args', LIFECYCLE_MUTATIONS)
def test_direct_calls_preserve_pending_diplomatic_owner(method, args, process_adapter, quick_trade):
    conn = TradeConn(); state = GameState(conn); conn.deliver_events = False
    isolate_disconnect(conn, process_adapter)
    assert 'OUTCOME_UNKNOWN' in run(state.propose_trade(1, [{'type':'GOLD','amount':20}], []))
    assert state.diplomacy_pending
    assert 'OUTCOME_UNKNOWN' in run(getattr(state, method)(*args))
    assert process_adapter == [] and conn.lua.globals().requests == 1


@pytest.mark.parametrize('method,args', LIFECYCLE_MUTATIONS[:5])
def test_direct_calls_preserve_pending_turn_owner(method, args, process_adapter):
    conn = GameStateConn(); state = GameState(conn)
    isolate_disconnect(conn, process_adapter)
    conn.send_error = OSError('lost end-turn acknowledgement')
    assert 'unknown' in run(state.end_turn())
    assert state.turn_pending
    before = len(conn.calls)
    assert 'OUTCOME_UNKNOWN' in run(getattr(state, method)(*args))
    assert process_adapter == [] and len(conn.calls) == before
    assert len(conn.requests) == 1


@pytest.mark.parametrize('method,args', LIFECYCLE_MUTATIONS)
def test_direct_calls_cannot_bypass_legacy_action_owner(method, args, process_adapter):
    conn = ReadyConn(); state = GameState(conn)
    isolate_disconnect(conn, process_adapter)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.skip_unit(196608))
    assert 'OUTCOME_UNKNOWN' in run(getattr(state, method)(*args))
    assert process_adapter == [] and conn.lua.eval('#submissions') == 1


def test_repeated_legacy_mcp_intent_confirms_and_stops_in_same_call():
    conn = ReadyConn(); state = GameState(conn); ctx = context(state)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(server.unit_action(ctx, 196608, 'skip'))
    # First read confirms; a second dispatch would be observable even if the
    # current readiness later changes between native confirmation and request.
    conn.lua.execute('units[196608].ready=false; units[196608].activity=2')
    assert run(server.unit_action(ctx, 196608, 'skip')).startswith('SKIPPED|')
    assert conn.lua.eval('#submissions') == 1


def test_public_catalog_marks_all_pure_queries_read_only():
    tools = {tool.name: tool for tool in run(server.mcp.list_tools())}
    for name in ('get_religion_beliefs', 'get_dedications', 'get_gp_advisor'):
        assert tools[name].annotations is not None
        assert tools[name].annotations.readOnlyHint is True


@pytest.mark.parametrize('method,args', [
    ('run_lua', ('intrusion=true', 'ingame')), ('kill_game', ()), ('launch_game', ()),
    ('open_world_congress', ()), ('queue_wc_votes', ([],)),
    ('save_game', ('pending',)), ('load_game_save', ('checkpoint',)),
    ('set_research', ('TECH_POTTERY',)), ('send_diplomatic_action', (1, 'DENOUNCE')),
])
def test_mcp_cannot_bypass_actual_legacy_pending_action(method, args, process_adapter):
    conn = ReadyConn(); state = GameState(conn)
    isolate_disconnect(conn, process_adapter)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in run(state.skip_unit(196608))
    assert 'OUTCOME_UNKNOWN' in run(getattr(server, method)(context(state), *args))
    assert process_adapter == [] and conn.lua.eval('#submissions') == 1


def test_full_id_mcp_movement_can_complete_and_old_low_index_direct_api_still_works():
    conn = TargetMovementConn(); state = GameState(conn)
    conn.lua.execute('can_target=true')
    assert 'now_at:18,18' in run(server.unit_action(context(state), 851974, 'move', 18, 18))
    assert 'now_at:19,18' in run(state.move_unit(6, 19, 18))
    assert conn.lua.eval('submissions') == 2


@pytest.mark.parametrize('mcp_entry', [False, True])
def test_open_borders_alias_reenters_its_original_trade_owner(mcp_entry, quick_trade):
    conn = TradeConn(); state = GameState(conn); ctx = context(state)
    conn.setup('DealAgreementTypes={OPEN_BORDERS=5}')
    conn.deliver_events = False
    def call():
        return (server.send_diplomatic_action(ctx, 1, 'OPEN_BORDERS') if mcp_entry
                else state.send_diplomatic_action(1, 'OPEN_BORDERS'))
    assert 'OUTCOME_UNKNOWN' in run(call())
    assert state.diplomacy_pending and conn.lua.globals().requests == 1
    other = (server.send_diplomatic_action(ctx, 2, 'OPEN_BORDERS') if mcp_entry
             else state.send_diplomatic_action(2, 'OPEN_BORDERS'))
    assert 'PENDING_TRADE_MISMATCH' in run(other)
    unrelated = (server.send_diplomatic_action(ctx, 1, 'DENOUNCE') if mcp_entry
                 else state.send_diplomatic_action(1, 'DENOUNCE'))
    assert 'OUTCOME_UNKNOWN' in run(unrelated)
    conn.deliver_events = True
    assert run(call()).startswith('ACCEPTED|')
    assert conn.lua.globals().requests == 1
    assert list(conn.lua.globals().sent_actions.values()) == [9, 1]
