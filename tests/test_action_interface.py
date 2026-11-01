"""Owner outcomes survive presentation, and domain reads retain completeness."""
import asyncio

import pytest

from civ_mcp import lua as lq
from civ_mcp.game_state import GameState
from civ_mcp.results import ResultText
from test_item_purchase_confirmation import PurchaseConn


pytestmark = pytest.mark.usefixtures('fast_action_confirmation')


@pytest.mark.parametrize('mode,status', [('confirmed', 'confirmed'), ('lost', 'unknown'), ('refused', 'not_submitted')])
def test_public_purchase_result_keeps_owner_state_without_reading_prose(mode, status):
    conn = PurchaseConn()
    state = GameState(conn)
    if mode == 'lost':
        conn.lose_reply = True
    elif mode == 'refused':
        conn.lua.execute('allowed=false')
    result = asyncio.run(state.purchase_item(65537, 'UNIT', 'UNIT_WARRIOR'))
    assert isinstance(result, ResultText)
    assert result.outcome['kind'] == 'action'
    assert result.outcome['status'] == status
    assert conn.lua.eval('#submissions') == (0 if mode == 'refused' else 1)
    if mode == 'lost':
        assert state.pending_action_summary is not None
        result = asyncio.run(state.purchase_item(65537, 'UNIT', 'UNIT_WARRIOR'))
        assert result.outcome['status'] == 'confirmed'
        assert conn.lua.eval('#submissions') == 1


def test_confirmed_native_receipt_can_change_its_display_without_changing_outcome(monkeypatch):
    original = lq.parse_item_purchase_confirmation

    def new_display(*args):
        result = original(*args)
        return 'Error: words in presentation are not a domain failure' if result is not None else None

    monkeypatch.setattr(lq, 'parse_item_purchase_confirmation', new_display)
    conn = PurchaseConn()
    state = GameState(conn)
    result = asyncio.run(state.purchase_item(65537, 'UNIT', 'UNIT_WARRIOR'))
    assert result.startswith('Error:')
    assert result.outcome['status'] == 'confirmed' and result.outcome['code'] is None
    assert state.pending_action_summary is None and conn.lua.eval('#submissions') == 1


def test_unit_query_does_not_turn_a_truncated_entity_into_an_empty_list():
    class TruncatedRead:
        async def execute_write(self, _code):
            return ['65536|0|Warrior']

    with pytest.raises(ValueError, match='Unit rows could not all be parsed'):
        asyncio.run(GameState(TruncatedRead()).get_units())


def test_native_refusal_code_survives_action_presentation():
    conn = PurchaseConn()
    conn.lua.execute('allowed=false')
    result = asyncio.run(GameState(conn).purchase_item(65537, 'UNIT', 'UNIT_WARRIOR'))
    assert result.outcome['code'] == 'ITEM_PURCHASE_REFUSED'
    assert result.outcome['status'] == 'not_submitted'


def test_native_scope_refusal_keeps_specific_code_without_a_confirmation_read():
    from test_action_confirmation import Engine
    conn = Engine(writes=[["ERR:ACTION_SCOPE_NO_SUBMIT|Native action scope changed or unavailable"]])
    result = asyncio.run(GameState(conn).set_city_production(65536, 'UNIT', 'UNIT_SCOUT'))
    assert result.outcome['code'] == 'ACTION_SCOPE_NO_SUBMIT'
    assert result.outcome['status'] == 'not_submitted'


from test_end_turn import GameStateConn, clock


def test_truncated_post_turn_units_limit_report_without_claiming_a_disappearance(clock):
    from test_game_state import _UNIT_LINE

    class TruncatedAfterAdvance(GameStateConn):
        async def execute_write(self, code):
            if code == lq.build_units_query():
                return [_UNIT_LINE] if self.turn == 50 else ['65536|0|Warrior']
            return await super().execute_write(code)

    conn = TruncatedAfterAdvance()
    state = GameState(conn)
    conn.send_error = ConnectionError('reply lost')
    assert 'result unknown' in asyncio.run(state.end_turn())
    report = asyncio.run(state.end_turn())
    assert 'Turn 50 -> 51' in report
    assert 'Post-turn snapshot unavailable' in report
    assert 'no longer appears' not in report
    assert not state.turn_pending and len(conn.requests) == 1


def test_skip_batch_keeps_unknown_owner_and_completed_units_in_its_result():
    from test_unit_readiness import ReadyConn
    conn = ReadyConn()
    conn.lua.execute('fail_request=true; addUnit(65537,true,0,0)')
    state = GameState(conn)
    result = asyncio.run(state.skip_remaining_units())
    assert result.startswith('SKIP_BATCH_STOPPED|')
    assert result.outcome['status'] == 'unknown'
    assert result.outcome['code'] is None  # Retain the old batch exit semantics.
    data = result.outcome['data']
    assert data['completed_units'] == [] and data['stopped_unit'] == 65537
    assert data['results'][0]['outcome']['code'] == 'OUTCOME_UNKNOWN'
    assert conn.lua.eval('#submissions') == 1 and state.pending_action_summary


def test_skip_batch_uses_confirmed_outcome_after_display_changes(monkeypatch):
    from civ_mcp.actions import ActionLifecycle
    from test_unit_readiness import ReadyConn

    original = ActionLifecycle._confirmed_action

    def translated(self, pending):
        confirmed = original(self, pending)
        return '跳过已确认' if confirmed is not None else None

    monkeypatch.setattr(ActionLifecycle, '_confirmed_action', translated)
    conn = ReadyConn()
    conn.lua.execute('addUnit(65537,true,0,0)')
    state = GameState(conn)
    result = asyncio.run(state.skip_remaining_units())
    assert result.outcome['status'] == 'confirmed'
    assert result.outcome['data']['completed_units'] == [65537, 196608]
    assert conn.lua.eval('#submissions') == 2
    assert state.pending_action_summary is None


@pytest.mark.parametrize('activity', [1, 3])
@pytest.mark.parametrize('noise', [False, True])
def test_skip_batch_preserves_native_noop_after_display_changes(monkeypatch, activity, noise):
    from test_unit_readiness import ReadyConn

    conn = ReadyConn()
    conn.lua.execute('addUnit(65537,true,0,0)')
    execute = conn.execute_write

    async def noisy_receipt(code):
        rows = await execute(code)
        if noise and any(row.startswith(('OK:NOT_READY|', 'OK:ALREADY_WAITING|')) for row in rows):
            rows.insert(0, 'native debug output')
        return rows

    monkeypatch.setattr(conn, 'execute_write', noisy_receipt)
    state = GameState(conn)
    original = state._set_unit_readiness

    async def changes_after_candidate_read(unit_id, kind, **options):
        if unit_id == 65537:
            conn.lua.execute(f'units[65537].ready=false; units[65537].activity={activity}')
        result = await original(unit_id, kind, **options)
        if result.outcome['status'] == 'not_submitted':
            result = ResultText('保持已有指令', **{k: result.outcome[k]
                for k in ('kind', 'status', 'code', 'data')})
        return result

    monkeypatch.setattr(state, '_set_unit_readiness', changes_after_candidate_read)
    result = asyncio.run(state.skip_remaining_units())
    assert result.outcome['status'] == 'confirmed'
    assert result.outcome['data']['completed_units'] == [65537, 196608]
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('units[65537].activity') == activity
