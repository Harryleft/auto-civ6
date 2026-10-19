"""Unknown native scope reads may recover; observed changes remain invalid.

Exercise public MCP/GameState and generated Lua. Faults replace only native
identity getters, after the outer identity probe, to reach the retained receipt.
"""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from test_tile_purchase_confirmation import TileConn
from test_item_purchase_confirmation import PurchaseConn
from test_unit_transforms import TransformConn
from test_unit_tasks import TaskConn
from test_tile_actions import TileActionConn
from test_trader_confirmation import TraderConn
from test_great_person_pool_confirmation import GreatPeopleConn
from test_unit_effect_confirmation import EffectConn
from test_city_decisions import DecisionConn
from test_spy_assignments import AssignmentConn
from test_religion_plan_confirmation import ReligionConn
from test_envoy_confirmation import EnvoyConn
from test_royal_contribution import ContributionConn
from test_player_choices import ChoiceConn
from test_policy_lua_execution import PolicyConn


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def pending_purchase():
    conn = TileConn()
    state = GameState(conn)
    conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.purchase_tile(make_ctx(game=state), 65537, 11, 10))
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('balance') == 85.25
    assert conn.lua.eval('plots[11].owner') == 0
    return conn, state


GETTERS = {
    'owner': 'Game.GetLocalPlayer',
    'turn': 'Game.GetCurrentGameTurn',
    'civ': 'PlayerConfigurations[0].GetCivilizationTypeName',
    'seed': 'GameConfiguration.GetValue',
}


def interrupt_scope(conn, field, fault):
    getter = GETTERS[field]
    behavior = {'throw': "error('native identity temporarily unavailable')",
                'nil': 'return nil', 'missing': 'return nil',
                'malformed': "return 'unavailable'", 'nan': 'return 0/0',
                'changed': 'return ' + {'owner': '1', 'turn': '42',
                                       'civ': "'CIVILIZATION_ROME'", 'seed': '123'}[field]}[fault]
    conn.lua.execute(f'''
scope_original={getter};scope_reads=0
{getter}=function(...)
 scope_reads=scope_reads+1
 if scope_reads>1 then {behavior} end
 local value=scope_original(...)
 {'%s=nil' % getter if fault == 'missing' else ''}
 return value
end
''')


@pytest.mark.parametrize('field', GETTERS)
@pytest.mark.parametrize('fault', ['throw', 'nil', 'missing', 'malformed', 'nan'])
def test_paid_tile_purchase_can_recover_unknown_scope_without_second_charge(field, fault):
    conn, state = pending_purchase()
    interrupt_scope(conn, field, fault)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    assert state.pending_action_summary is not None
    conn.lua.execute(f'{GETTERS[field]}=scope_original')
    result = asyncio.run(state.confirm_pending_action(wait=False))
    assert result.startswith('TILE_PURCHASED|'), result
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('balance') == 85.25


DOMAINS = [
    (TileConn, 'purchase_tile', (65537, 11, 10), 'TILE_PURCHASED|'),
    (PurchaseConn, 'purchase_item', (65537, 'UNIT', 'UNIT_WARRIOR'), 'PURCHASED|'),
    (TransformConn, 'upgrade_unit', (196608,), 'UPGRADED|'),
    (TaskConn, 'automate_explore', (196608,), 'AUTOMATED|'),
    (TileActionConn, 'improve_tile', (196608, 'IMPROVEMENT_FARM'), 'IMPROVED|'),
    (TraderConn, 'make_trade_route', (196608, 30, 30), 'TRADE_ROUTE_STARTED|'),
    (GreatPeopleConn, 'patronize_great_person', (42,), 'PATRONIZED|'),
    (EffectConn, 'activate_great_person', (196608,), 'GP_ACTIVATED|'),
    (DecisionConn, 'resolve_city_capture', (65537, 'keep'), 'KEEP|'),
    (AssignmentConn, 'spy_travel', (196608, 10, 10), 'SPY_ASSIGNMENT_STARTED|'),
    (ReligionConn, 'choose_pantheon', ('BELIEF_SPARK',), 'PANTHEON_FOUNDED|'),
    (EnvoyConn, 'send_envoy', (3,), 'ENVOY_SENT|'),
    (ContributionConn, 'sacrifice_builder_charges', (196608,), 'SACRIFICED|'),
]


def pending_domain(domain):
    adapter, method, args, prefix = domain

    class LostReply(adapter):
        async def execute_write(self, code):
            before = self.lua.eval('#submissions')
            rows = await super().execute_write(code)
            if self.lua.eval('#submissions') > before:
                raise OSError('native operation completed; response lost')
            return rows

    conn = LostReply()
    state = GameState(conn)
    result = asyncio.run(getattr(state, method)(*args))
    assert 'OUTCOME_UNKNOWN' in result, result
    assert conn.lua.eval('#submissions') == 1
    return conn, state, prefix


@pytest.mark.parametrize('domain', DOMAINS, ids=[d[1] for d in DOMAINS])
@pytest.mark.parametrize('field,fault', [
    ('seed', 'throw'), ('seed', 'nil'), ('civ', 'missing'),
])
def test_domain_scope_unknown_can_recover_without_resubmission(domain, field, fault):
    conn, state, prefix = pending_domain(domain)
    interrupt_scope(conn, field, fault)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    conn.lua.execute(f'{GETTERS[field]}=scope_original')
    result = asyncio.run(state.confirm_pending_action(wait=False))
    assert result.startswith(prefix), result
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('domain', DOMAINS, ids=[d[1] for d in DOMAINS])
@pytest.mark.parametrize('field', GETTERS)
def test_observed_native_scope_change_remains_invalid_after_original_returns(domain, field):
    conn, state, _ = pending_domain(domain)
    interrupt_scope(conn, field, 'changed')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    conn.lua.execute(f'{GETTERS[field]}=scope_original')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    assert state.pending_action_summary is not None
    assert conn.lua.eval('#submissions') == 1


def replace_fact(conn, getter, fault):
    behavior = {'throw': "function() error('native fact unavailable') end",
                'nil': 'function() return nil end', 'missing': 'nil',
                'malformed': "function() return 'unavailable' end",
                'nan': 'function() return 0/0 end'}[fault]
    conn.lua.execute(f'fact_original={getter};{getter}={behavior}')


@pytest.mark.parametrize('fault', ['throw', 'nil', 'missing', 'malformed', 'nan'])
def test_dedication_unknown_era_recovers_original_choice_without_resending(fault):
    conn = ChoiceConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.choose_dedication(ctx, 7))
    replace_fact(conn, 'eras.GetCurrentEra', fault)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    conn.lua.execute('eras.GetCurrentEra=fact_original')
    assert asyncio.run(state.confirm_pending_action(wait=False)).startswith('DEDICATION_CHOSEN|')
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('#active') == 1


def test_observed_era_change_still_invalidates_original_dedication():
    conn = ChoiceConn(); state = GameState(conn); conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.choose_dedication(7))
    conn.lua.execute('era=3')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    conn.lua.execute('era=2')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.choose_dedication(7))
    assert conn.lua.eval('#submissions') == 1


POLICY_PLAN = {2: 'POLICY_CIVIL_PRESTIGE', 3: 'POLICY_NATURAL_PHILOSOPHY'}
POLICY_GETTERS = {
    'government': 'culture.GetCurrentGovernment',
    'slot_count': 'culture.GetNumPolicySlots',
    'slot_type': 'culture.GetSlotType',
}


def pending_paid_policy():
    conn = PolicyConn(); state = GameState(conn)
    conn.lose_after = 'unlock'
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_policies(POLICY_PLAN))
    assert len(conn.unlocks) == 1 and conn.submissions == []
    return conn, state


def finish_paid_policy(conn, state):
    # Read-only recovery may acknowledge payment, but cannot submit the plan.
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    assert len(conn.unlocks) == 1 and conn.submissions == []
    result = asyncio.run(server.set_policies(make_ctx(game=state),
        '2=POLICY_CIVIL_PRESTIGE,3=POLICY_NATURAL_PHILOSOPHY'))
    assert result.startswith('POLICIES_SET|'), result
    assert state.pending_action_summary is None
    assert len(conn.unlocks) == len(conn.submissions) == 1
    assert conn.lua.eval('goldBalance') == 860
    assert conn.current_slots() == {0: 3, 1: 4, 2: 2, 3: 1}


@pytest.mark.parametrize('field', POLICY_GETTERS)
@pytest.mark.parametrize('fault', ['throw', 'nil', 'missing', 'malformed', 'nan'])
def test_paid_policy_plan_recovers_unknown_layout_without_paying_again(field, fault):
    conn, state = pending_paid_policy()
    getter = POLICY_GETTERS[field]
    replace_fact(conn, getter, fault)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    conn.lua.execute(f'{getter}=fact_original')
    finish_paid_policy(conn, state)


@pytest.mark.parametrize('field', GETTERS)
@pytest.mark.parametrize('fault', ['throw', 'nil', 'missing'])
def test_paid_policy_plan_recovers_unknown_identity_without_paying_again(field, fault):
    conn, state = pending_paid_policy()
    interrupt_scope(conn, field, fault)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    conn.lua.execute(f'{GETTERS[field]}=scope_original')
    finish_paid_policy(conn, state)


@pytest.mark.parametrize('field', GETTERS)
def test_observed_policy_identity_change_still_blocks_paid_plan(field):
    conn, state = pending_paid_policy()
    interrupt_scope(conn, field, 'changed')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    conn.lua.execute(f'{GETTERS[field]}=scope_original')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_policies(POLICY_PLAN))
    assert len(conn.unlocks) == 1 and conn.submissions == []
    assert conn.lua.eval('goldBalance') == 860


@pytest.mark.parametrize('field,changed', [('government', 2), ('slot_count', 5), ('slot_type', 20)])
def test_observed_policy_layout_change_still_blocks_paid_plan(field, changed):
    conn, state = pending_paid_policy()
    getter = POLICY_GETTERS[field]
    conn.lua.execute(f'layout_original={getter};{getter}=function() return {changed} end')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    conn.lua.execute(f'{getter}=layout_original')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_policies(POLICY_PLAN))
    assert len(conn.unlocks) == 1 and conn.submissions == []
    assert conn.lua.eval('goldBalance') == 860


def test_valid_scope_mismatch_latches_even_if_another_field_is_unknown():
    conn, state = pending_purchase()
    interrupt_scope(conn, 'owner', 'changed')
    conn.lua.execute('civ_original=PlayerConfigurations[0].GetCivilizationTypeName;civ_reads=0;'
        'PlayerConfigurations[0].GetCivilizationTypeName=function() '
        'civ_reads=civ_reads+1;if civ_reads==1 then return civ_original() end end')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    conn.lua.execute('Game.GetLocalPlayer=scope_original;PlayerConfigurations[0].GetCivilizationTypeName=civ_original')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    assert conn.lua.eval('#submissions') == 1
