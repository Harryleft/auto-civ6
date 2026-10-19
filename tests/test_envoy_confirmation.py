"""Real MCP/GameState/generated Lua; only native game and transport are simulated."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx, app_of
from test_unit_readiness import ReadyConn


class EnvoyConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply = False
        self.lua.execute('''
tokens, allowed, met, bonus = 3, true, true, 1
received = {[3]=2,[4]=0}
local influence = {
 GetTokensToGive=function() return tokens end,
 CanGiveInfluence=function() return true end,
 CanGiveTokensToPlayer=function() return allowed end,
}
Players[0].GetInfluence=function() return influence end
Players[0].GetDiplomacy=function() return {HasMet=function() return met end} end
for _,id in ipairs({3,4}) do
 local target=id
 Players[target]={IsAlive=function() return true end,IsMajor=function() return false end,
  IsBarbarian=function() return false end,
  GetInfluence=function() return {CanReceiveInfluence=function() return true end,
   GetTokensReceived=function(_,owner) assert(owner==0);return received[target] end} end}
 PlayerConfigurations[target]={GetPlayerName=function() return 'Kabul' end,
  GetCivilizationTypeName=function() return 'CIVILIZATION_CITYSTATE_'..target end}
end
Locale={Lookup=function(s) return s end}
PlayerOperations={PARAM_PLAYER_ONE='target',PARAM_FLAGS='flags',GIVE_INFLUENCE_TOKEN=7}
function applyEnvoy(id) tokens=tokens-1;received[id]=received[id]+bonus end
UI.RequestPlayerOperation=function(owner,op,params)
 assert(owner==0 and op==7)
 table.insert(submissions,params.target)
 if not deferred then applyEnvoy(params.target) end
 if fail_request then error('native failure after entry') end
end
''')

    async def execute_write(self, code):
        before = self.lua.eval('#submissions')
        rows = await super().execute_write(code)
        if self.lose_reply and self.lua.eval('#submissions') > before:
            self.lose_reply = False
            raise OSError('native request ran; response lost')
        return rows


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def test_lost_reply_repeat_only_confirms_original_through_mcp():
    conn = EnvoyConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.send_envoy(ctx, 3))
    assert state.pending_action_kind == 'envoy'
    assert conn.lua.eval('tokens') == 2
    assert asyncio.run(server.send_envoy(ctx, 3)).startswith('ENVOY_SENT|player:3')
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('received[3]') == 3
    assert state.pending_action_summary is None


@pytest.mark.parametrize('bonus', [1, 2, 3])
def test_confirmed_send_supports_native_envoy_bonuses(bonus):
    conn = EnvoyConn(); state = GameState(conn)
    conn.lua.execute(f'bonus={bonus}')
    assert asyncio.run(state.send_envoy(3)) == f'ENVOY_SENT|player:3|remaining:2|envoys:2->{2+bonus}'
    assert conn.lua.eval('#submissions') == 1


def test_deferred_send_blocks_different_target_and_only_reads_on_repeat():
    conn = EnvoyConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.send_envoy(ctx, 3))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.send_envoy(ctx, 4))
    conn.lua.execute('applyEnvoy(3)')
    assert asyncio.run(server.send_envoy(ctx, 3)).startswith('ENVOY_SENT|player:3')
    assert conn.lua.eval('#submissions') == 1
    # A later, separate intent may send another envoy after the gate is retired.
    conn.lua.execute('deferred=false')
    assert asyncio.run(server.send_envoy(ctx, 3)).startswith('ENVOY_SENT|player:3')
    assert conn.lua.eval('#submissions') == 2


@pytest.mark.parametrize('effect', [
    '', 'tokens=2', 'received[3]=3', 'applyEnvoy(4)',
    'applyEnvoy(3);applyEnvoy(3)', 'tokens=0;received[3]=3',
])
def test_incomplete_or_ambiguous_effect_does_not_confirm(effect):
    conn = EnvoyConn(); state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    conn.lua.execute(effect)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    assert state.pending_action_kind == 'envoy'
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('change', [
    '__civmcpEnvoy=nil', '__civmcpEnvoy.token="other"', '__civmcpEnvoy.target=4',
    '__civmcpEnvoy.returned=false', 'busy=true', 'busy=nil', 'seed=99', 'turn=42',
    'Players[3]=nil', 'received[3]=nil',
    'PlayerConfigurations[3].GetCivilizationTypeName=function() return "OTHER" end',
    'Game.GetLocalPlayer=function() return 1 end;PlayerConfigurations[1]=PlayerConfigurations[0]',
])
def test_missing_receipt_scope_or_facts_never_release_pending(change):
    conn = EnvoyConn(); state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    conn.lua.execute('applyEnvoy(3);'+change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    assert state.pending_action_kind == 'envoy'
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('observe', ['identity', 'confirmation'])
def test_scope_change_is_latched_even_if_original_world_returns(observe):
    conn = EnvoyConn(); state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    conn.lua.execute('seed=99')
    if observe == 'identity':
        asyncio.run(state.get_game_identity())
    else:
        assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    conn.lua.execute('seed=719665280;applyEnvoy(3)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('setup', [
    'tokens=0', 'tokens=nil', 'allowed=false', 'allowed=nil', 'met=false',
    'busy=true', 'busy=nil', 'Players[3]=nil',
    'Players[3].IsMajor=function() return true end',
    'Players[3].IsAlive=function() return false end',
    'Players[3].IsBarbarian=function() return true end',
    'UI.RequestPlayerOperation=nil', 'PlayerOperations.GIVE_INFLUENCE_TOKEN=nil',
    'received[3]=-1', '__civmcpEnvoy="invalid"',
])
def test_refusal_is_pre_submission_and_does_not_leave_pending(setup):
    conn = EnvoyConn(); state = GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(state.send_envoy(3)).startswith('Error: ENVOY_REFUSED|')
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions') == 0


@pytest.mark.parametrize('target', [-1, 63, '3', True, 3.5])
def test_invalid_target_never_reaches_native_operation(target):
    conn = EnvoyConn(); state = GameState(conn)
    assert 'INVALID_CITY_STATE' in asyncio.run(state.send_envoy(target))
    assert conn.lua.eval('#submissions') == 0


def test_cancellation_after_native_effect_retains_original_intent():
    class CancelConn(EnvoyConn):
        async def execute_write(self, code):
            before = self.lua.eval('#submissions')
            rows = await super().execute_write(code)
            if self.lua.eval('#submissions') > before:
                raise asyncio.CancelledError()
            return rows
    conn = CancelConn(); state = GameState(conn)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(state.send_envoy(3))
    assert state.pending_action_kind == 'envoy'
    assert asyncio.run(state.send_envoy(3)).startswith('ENVOY_SENT|player:3')
    assert conn.lua.eval('#submissions') == 1


def test_native_throw_after_entry_never_claims_definite_failure_or_replays():
    conn = EnvoyConn(); state = GameState(conn); conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('reply', [[], ['ERR:OTHER_CALLBACK'],
    ['ERR:ENVOY_REFUSED|fake', 'OK:ENVOY_REQUESTED|player:3']])
def test_missing_or_conflicting_acknowledgement_does_not_clear_gate(reply):
    class CorruptReplyConn(EnvoyConn):
        async def execute_write(self, code):
            before = self.lua.eval('#submissions')
            rows = await super().execute_write(code)
            return reply if self.lua.eval('#submissions') > before else rows
    conn = CorruptReplyConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    assert conn.lua.eval('#submissions') == 1


def test_pending_envoy_blocks_mcp_mutation_and_direct_lifecycle_gates():
    conn = EnvoyConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.send_envoy(ctx, 3))
    for tool, params in [('set_research', {}), ('run_lua', {}), ('purchase_item', {'city_id':1})]:
        async def forbidden():
            raise AssertionError('pending spend must prevent dispatch')
        assert 'OUTCOME_UNKNOWN' in asyncio.run(server._logged(ctx, tool, params, forbidden))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.skip_unit(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.save_game('pending_envoy'))
    assert asyncio.run(state.get_game_identity()) == ('sumeria',719665280)
    assert state.pending_action_kind == 'envoy'
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('action', ['sleep', 'heal', 'automate', 'alert', 'spread_religion', 'activate'])
def test_untracked_unit_commands_cannot_bypass_pending_envoy(action):
    conn = EnvoyConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.send_envoy(ctx, 3))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, action))
    assert conn.lua.eval('#submissions') == 1


def test_scope_change_between_preparation_and_dispatch_proves_zero_submission():
    class ChangedConn(EnvoyConn):
        async def execute_write(self, code):
            if '-- ACTION_SUBMIT_SCOPE|' in code:
                self.lua.execute('turn=42')
            return await super().execute_write(code)
    conn = ChangedConn(); state = GameState(conn)
    assert 'ACTION_SCOPE_NO_SUBMIT' in asyncio.run(state.send_envoy(3))
    assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions') == 0


def test_timeout_before_native_entry_cannot_be_assumed_to_prove_zero_submission():
    class TimeoutConn(EnvoyConn):
        async def execute_write(self, code):
            if '-- ACTION_SUBMIT_SCOPE|' in code:
                raise TimeoutError('dispatch status unavailable')
            return await super().execute_write(code)
    conn = TimeoutConn(); state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    assert state.pending_action_kind == 'envoy'
    assert conn.lua.eval('#submissions') == 0


def test_readback_failure_keeps_pending_until_transport_recovers():
    class ReadFailureConn(EnvoyConn):
        fail_reads = True
        async def execute_write(self, code):
            if self.fail_reads and 'ENVOY_CONFIRMED|' in code:
                raise OSError('read failed')
            return await super().execute_write(code)
    conn = ReadFailureConn(); state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    assert state.pending_action_kind == 'envoy'
    conn.fail_reads = False
    assert asyncio.run(state.send_envoy(3)).startswith('ENVOY_SENT|player:3')
    assert conn.lua.eval('#submissions') == 1
