"""Public MCP/GameState with real Lua and a simulated native city purchase API."""
import asyncio
import inspect

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx, app_of
from test_unit_readiness import ReadyConn


class TileConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply = False
        self.lua.execute('''
balance, cost, eligible, commandAllowed = 120.5, 35.25, true, true
city = {id=65537, owner=0, x=10, y=10}
city.GetID=function(self) return self.id end
city.GetOwner=function(self) return self.owner end
city.GetX=function(self) return self.x end
city.GetY=function(self) return self.y end
city.GetGold=function() return {GetPlotPurchaseCost=function() return cost end} end
plots={}
for _,x in ipairs({11,12}) do
 local p={x=x,y=10,index=x,owner=-1,purchaseCity=nil}
 p.GetX=function(self) return self.x end
 p.GetY=function(self) return self.y end
 p.GetIndex=function(self) return self.index end
 p.GetOwner=function(self) return self.owner end
 plots[x]=p
end
Map={GetPlot=function(x,y) if y==10 then return plots[x] end end,
 GetPlotByIndex=function(index) return plots[index] end}
Cities={GetPlotPurchaseCity=function(plot) return plot.purchaseCity end}
Players[0].GetTreasury=function() return {GetGoldBalance=function() return balance end} end
CityCommandTypes={PURCHASE=1,PARAM_PLOT_PURCHASE='plot',PARAM_X='x',PARAM_Y='y'}
CityCommandResults={PLOTS='plots'}
function applyPurchase(x)
 balance=balance-cost;plots[x].owner=0;plots[x].purchaseCity=city
end
CityManager={
 GetCity=function(owner,id) if id==city.id%65536 then return city end end,
 GetCommandTargets=function(c,command,params) return {plots=eligible and {11,12} or {}} end,
 CanStartCommand=function(c,command,params)
  assert(c==city and command==1 and params.plot==1 and params.y==10)
  return commandAllowed
 end,
 RequestCommand=function(c,command,params)
  assert(c==city and command==1 and params.plot==1 and params.y==10)
  table.insert(submissions,params.x)
  if not deferred then applyPurchase(params.x) end
  if fail_request then error('native purchase threw after entry') end
 end,
}
''')

    async def execute_write(self, code):
        before = self.lua.eval('#submissions')
        rows = await super().execute_write(code)
        if self.lose_reply and self.lua.eval('#submissions') > before:
            self.lose_reply = False
            raise OSError('purchase ran; response lost')
        return rows


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def test_lost_purchase_reply_repeated_mcp_call_only_confirms_original():
    conn = TileConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.purchase_tile(ctx, 65537, 11, 10))
    assert state.pending_action_kind == 'purchase_tile'
    assert asyncio.run(server.purchase_tile(ctx, 65537, 11, 10)).startswith('TILE_PURCHASED|')
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('balance') == 85.25
    assert conn.lua.eval('plots[11].owner') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('method,args', [
    ('set_research', ('TECH_POTTERY',)), ('set_civic', ('CIVIC_CODE_OF_LAWS',)),
    ('appoint_governor', ('GOVERNOR_THE_BUILDER',)), ('assign_governor', ('GOVERNOR_THE_BUILDER',65537)),
    ('promote_governor', ('GOVERNOR_THE_BUILDER','PROMOTION')), ('promote_unit',(196608,'PROMOTION')),
    ('choose_pantheon',('BELIEF',)), ('found_religion',('RELIGION', 'BELIEF1','BELIEF2')),
    ('change_government',('GOVERNMENT',)), ('choose_dedication',(1,)),
    ('upgrade_unit',(196608,)), ('automate_explore',(196608,)), ('heal_unit',(196608,)),
    ('alert_unit',(196608,)), ('sleep_unit',(196608,)), ('delete_unit',(196608,)),
    ('improve_tile',(196608,'IMPROVEMENT_FARM')), ('remove_feature',(196608,)),
    ('repair_improvement',(196608,)), ('remove_improvement',(196608,)), ('build_route',(196608,)),
    ('recruit_great_person',(1,)), ('patronize_great_person',(1,)), ('reject_great_person',(1,)),
    ('activate_great_person',(196608,)), ('spread_religion',(196608,)),
    ('spy_travel',(196608,11,10)), ('spy_mission',(196608,'MISSION',11,10)), ('spy_escape',(196608,'DISTRICT')),
    ('make_trade_route',(196608,11,10)), ('teleport_to_city',(196608,11,10)),
    ('purchase_item',(65537,'UNIT','UNIT_WARRIOR')), ('purchase_tile',(65537,12,10)),
    ('set_policies',({0:'POLICY'},)), ('resolve_city_capture',('keep',)),
    ('send_diplomatic_action',(1,'DENOUNCE')), ('form_alliance',(1,'ALLIANCE')), ('propose_peace',(1,)),
    ('respond_to_deal',(1,True)), ('diplomacy_respond',(1,'CHOICE')),
    ('negotiate_incoming_trade',(1,'fingerprint')), ('propose_trade',(1,[],[])), ('test_trade',(1,[],[])),
    ('open_world_congress',()), ('queue_wc_votes',([],)), ('vote_world_congress',(1,0,0,1)), ('submit_congress',()),
    ('execute_lua',('error("must not dispatch")',)), ('kill_game',()), ('launch_game',()),
    ('load_save',(1,)), ('load_game_save',('save',)), ('reload_game_save',('save',)),
    ('set_city_focus',(65537,'food')), ('set_city_production',(65537,'UNIT','UNIT_WARRIOR')),
    ('skip_unit',(196608,)), ('move_unit',(196608,11,10)), ('send_envoy',(3,)),
    ('end_turn',()), ('save_game',('blocked_purchase',)),
])
def test_pending_purchase_blocks_direct_game_state_mutations(method, args):
    conn = TileConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    inspect.signature(getattr(state,method)).bind(*args)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state,method)(*args))
    assert conn.lua.eval('#submissions') == 1
    assert asyncio.run(state.get_game_identity()) == ('sumeria',719665280)


def test_pending_purchase_blocks_unrelated_mcp_mutation_before_dispatch():
    conn = TileConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_research(ctx,'TECH_POTTERY'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.run_lua(ctx,'balance=0',context='ingame'))
    assert conn.lua.eval('balance') == 120.5
    assert conn.lua.eval('#submissions') == 1


def test_delayed_ownership_and_gold_must_both_arrive_before_completion():
    conn = TileConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    conn.lua.execute('plots[11].owner=0;plots[11].purchaseCity=city')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    conn.lua.execute('balance=85.25')
    assert asyncio.run(state.purchase_tile(65537,11,10)) == 'TILE_PURCHASED|(11,10)|city:65537|cost:35.25|gold:120.5->85.25'
    assert conn.lua.eval('#submissions') == 1
    conn.lua.execute('deferred=false')
    assert asyncio.run(state.purchase_tile(65537,12,10)).startswith('TILE_PURCHASED|')
    assert conn.lua.eval('#submissions') == 2


@pytest.mark.parametrize('effect', [
    '', 'balance=85.25', 'applyPurchase(12)', 'applyPurchase(11);balance=85',
    'applyPurchase(11);plots[11].purchaseCity=nil',
    'applyPurchase(11);plots[11].purchaseCity={GetID=function() return 65538 end}',
    'applyPurchase(11);city.owner=1', 'applyPurchase(11);city.id=131073',
    'applyPurchase(11);city.x=12', 'applyPurchase(11);plots[11].index=99',
    'applyPurchase(11);busy=true', 'applyPurchase(11);busy=nil',
    'applyPurchase(11);__civmcpTilePurchase=nil',
    'applyPurchase(11);__civmcpTilePurchase.token="other"',
    'applyPurchase(11);__civmcpTilePurchase.returned=false',
])
def test_partial_wrong_target_or_missing_native_evidence_stays_pending(effect):
    conn = TileConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    conn.lua.execute(effect)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_kind == 'purchase_tile'


@pytest.mark.parametrize('setup', [
    'balance=1', 'balance=nil', 'cost=0', 'cost=nil', 'eligible=false',
    'city.owner=1', 'city.id=131073', 'plots[11].owner=0', 'plots[11]=nil',
    'busy=true', 'busy=nil', 'CityManager.RequestCommand=nil',
])
def test_native_pre_entry_refusal_proves_no_purchase(setup):
    conn = TileConn(); state = GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(state.purchase_tile(65537,11,10)).startswith('Error: TILE_PURCHASE_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('args', [(True,11,10),(65537,11.5,10),(65537,-1,10),('65537',11,10)])
def test_invalid_purchase_intent_does_not_reach_native_command(args):
    conn = TileConn(); state = GameState(conn)
    assert 'INVALID_TILE_PURCHASE' in asyncio.run(state.purchase_tile(*args))
    assert conn.lua.eval('#submissions') == 0


@pytest.mark.parametrize('scope', ['seed=99', 'turn=42'])
def test_changed_scope_remains_invalid_when_original_scope_returns(scope):
    conn = TileConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    conn.lua.execute(scope)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    conn.lua.execute('seed=719665280;turn=41;applyPurchase(11)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    assert conn.lua.eval('#submissions') == 1


def test_cancellation_after_purchase_does_not_release_or_replay_intent():
    class CancelConn(TileConn):
        async def execute_write(self,code):
            before = self.lua.eval('#submissions')
            rows = await super().execute_write(code)
            if self.lua.eval('#submissions') > before:
                raise asyncio.CancelledError()
            return rows
    conn = CancelConn(); state = GameState(conn)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(state.purchase_tile(65537,11,10))
    assert state.pending_action_kind == 'purchase_tile'
    assert asyncio.run(state.purchase_tile(65537,11,10)).startswith('TILE_PURCHASED|')
    assert conn.lua.eval('#submissions') == 1


def test_native_throw_after_entry_is_unknown_and_not_replayed():
    conn = TileConn(); state = GameState(conn); conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('setup', ['commandAllowed=false', 'commandAllowed=nil', 'CityManager.CanStartCommand=nil',
    'CityManager.GetCommandTargets=function() return {other={11,12}} end'])
def test_command_eligibility_and_exact_native_candidate_kind_are_required(setup):
    conn = TileConn(); state = GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(state.purchase_tile(65537,11,10)).startswith('Error: TILE_PURCHASE_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('reply', [[], ['ERR:UNRELATED_CALLBACK'],
    ['ERR:TILE_PURCHASE_REFUSED|ambiguous','OK:TILE_PURCHASE_REQUESTED|city:65537']])
def test_missing_or_conflicting_receipt_cannot_release_pending(reply):
    class ReplyConn(TileConn):
        async def execute_write(self,code):
            before = self.lua.eval('#submissions')
            rows = await super().execute_write(code)
            return reply if self.lua.eval('#submissions') > before else rows
    conn = ReplyConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    assert conn.lua.eval('#submissions') == 1


def test_changed_world_before_native_entry_proves_no_submission():
    class ChangedConn(TileConn):
        async def execute_write(self,code):
            if '-- ACTION_SUBMIT_SCOPE|' in code:
                self.lua.execute('turn=42')
            return await super().execute_write(code)
    conn = ChangedConn(); state = GameState(conn)
    assert 'ACTION_SCOPE_NO_SUBMIT' in asyncio.run(state.purchase_tile(65537,11,10))
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


def test_confirmation_read_failure_keeps_original_purchase_and_gate():
    class ReadFailureConn(TileConn):
        async def execute_write(self,code):
            if self.lua.eval('#submissions'):
                raise OSError('confirmation unavailable')
            return await super().execute_write(code)
    conn = ReadFailureConn(); state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    assert state.pending_action_kind == 'purchase_tile'
    assert conn.lua.eval('#submissions') == 1


def test_wrong_city_intent_cannot_replace_pending_original():
    conn = TileConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(65537,11,10))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_tile(131073,11,10))
    conn.lua.execute('applyPurchase(11)')
    assert asyncio.run(state.purchase_tile(65537,11,10)).startswith('TILE_PURCHASED|')
    assert conn.lua.eval('#submissions') == 1
