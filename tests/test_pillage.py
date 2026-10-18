"""Explicit pillaging through GameState and an in-memory native Lua 5.1 engine."""
import asyncio
import pytest

from test_unit_readiness import ReadyConn
from civ_mcp.game_state import GameState


class PillageConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
units[196608].x=19;units[196608].y=17
units[196608].GetX=function(self) return self.x end
units[196608].GetY=function(self) return self.y end
GameInfo.UnitOperations.UNITOPERATION_PILLAGE={Hash=13}
UnitOperationTypes.PILLAGE=13
pillage_allowed=true;at_war=true;plot_owner=1;improvement=4;improvement_pillaged=false
district_type=-1;district_pillaged=false;buildings={};pillage_layer='improvement'
plot={GetIndex=function() return 77 end,GetOwner=function() return plot_owner end,
 GetX=function() return 19 end,GetY=function() return 17 end,
 GetImprovementType=function() return improvement end,
 IsImprovementPillaged=function() return improvement_pillaged end,
 GetDistrictType=function() return district_type end}
Map={GetPlot=function(x,y) if x==19 and y==17 then return plot end end}
city={GetID=function() return 65539 end,GetOwner=function() return plot_owner end,
 GetDistricts=function() return {IsPillaged=function() return district_pillaged end} end,
 GetBuildings=function() return {
   GetBuildingsAtLocation=function() local ids={};for id in pairs(buildings) do table.insert(ids,id) end;table.sort(ids);return ids end,
   IsPillaged=function(_,id) return buildings[id] end} end}
Cities={GetPlotPurchaseCity=function() return city end}
Players[0].GetDiplomacy=function() return {IsAtWarWith=function() return at_war end} end
UnitManager.CanStartOperation=function(u,op,parameters,visible,results)
 if op~=13 or visible~=false or results~=true then error('strict pillage eligibility required') end
 if pillage_allowed==nil then return nil end
 return pillage_allowed and u.moves>=3
end
UnitManager.RequestOperation=function(u,op)
 table.insert(submissions,{id=u:GetID(),op=op})
 if fail_request then error('native failure after entry') end
 if not deferred then
   u.moves=0
   if pillage_layer=='improvement' then improvement_pillaged=true
   elseif pillage_layer=='district' then district_pillaged=true
   elseif type(pillage_layer)=='number' then buildings[pillage_layer]=true end
 end
end
''')


def test_pillage_submits_once_and_confirms_native_improvement_damage():
    conn = PillageConn()
    state = GameState(conn)
    result = asyncio.run(state.pillage_unit(196608))
    assert result.startswith('PILLAGED|unit:196608|at:19,17|damaged:improvement:4')
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('submissions[1].op') == 13
    assert state.pending_action_summary is None


def test_district_pillage_confirms_one_building_layer_then_a_separate_explicit_layer():
    conn = PillageConn()
    conn.lua.execute("improvement=-1;district_type=2;buildings={[9]=false,[10]=false};pillage_layer=10")
    state = GameState(conn)
    assert 'damaged:building:10' in asyncio.run(state.pillage_unit(196608))
    assert conn.lua.eval('buildings[9]') is False
    assert conn.lua.eval('district_pillaged') is False
    assert conn.lua.eval('#submissions') == 1
    conn.lua.execute('pillage_layer=9;units[196608].moves=3')
    assert 'damaged:building:9' in asyncio.run(state.pillage_unit(196608))
    assert conn.lua.eval('#submissions') == 2
    conn.lua.execute("pillage_layer='district';units[196608].moves=3")
    assert 'damaged:district:2' in asyncio.run(state.pillage_unit(196608))
    assert conn.lua.eval('#submissions') == 3


@pytest.mark.parametrize('setup', [
    'plot_owner=0', 'plot_owner=-1', 'at_war=false', 'at_war=nil',
    'pillage_allowed=false', 'pillage_allowed=nil', 'busy=true', 'busy=nil',
    'units[196608].moves=2',
    'UnitManager.CanStartOperation=nil', 'UnitManager.RequestOperation=nil',
    'UnitManager.CanStartOperation=function() error("unknown") end',
    'GameInfo.UnitOperations.UNITOPERATION_PILLAGE=nil', 'UnitOperationTypes.PILLAGE=99',
    'improvement=-1', 'improvement_pillaged=nil',
    'improvement=-1;district_type=2;Cities.GetPlotPurchaseCity=function() return nil end',
    'improvement=-1;district_type=2;city.GetBuildings=function() return nil end',
    'improvement=-1;district_type=2;buildings[9]=false;district_pillaged=nil',
    'district_type=2;improvement_pillaged=nil',
    'improvement=-1;district_type=2;city.GetBuildings=function() return {GetBuildingsAtLocation=function() return {9} end,IsPillaged=function() return nil end} end',
])
def test_illegal_or_unreadable_pillage_has_zero_submissions(setup):
    conn = PillageConn()
    conn.lua.execute(setup)
    state = GameState(conn)
    assert 'PILLAGE_REFUSED' in asyncio.run(state.pillage_unit(196608))
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('tamper', [
    'seed=99', 'turn=42', 'units[196608].x=20', 'units[196608].id=262144',
    'plot_owner=2', 'improvement=5', 'improvement_pillaged=true', 'pillage_allowed=false',
    'busy=true', 'at_war=false',
])
def test_atomic_guard_refuses_changed_prepared_intent_without_submitting(tamper):
    class ChangedConn(PillageConn):
        async def execute_write(self, code):
            if '-- ACTION_SUBMIT_SCOPE|' in code:
                self.lua.execute(tamper)
            return await super().execute_write(code)
    conn = ChangedConn()
    state = GameState(conn)
    assert asyncio.run(state.pillage_unit(196608)).startswith('Error:')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('error', [TimeoutError, asyncio.CancelledError])
def test_lost_reply_or_cancel_retains_attempt_and_repeat_only_confirms(error):
    class LostConn(PillageConn):
        lost = False
        async def execute_write(self, code):
            rows = await super().execute_write(code)
            if 'UnitManager.RequestOperation(unit,operation.Hash)' in code and not self.lost:
                self.lost = True
                raise error()
            return rows
    async def scenario():
        conn = LostConn()
        state = GameState(conn)
        if error is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError):
                await state.pillage_unit(196608)
        else:
            assert 'OUTCOME_UNKNOWN' in await state.pillage_unit(196608)
        assert state.pending_action_summary
        assert (await state.pillage_unit(196608)).startswith('PILLAGED')
        assert conn.lua.eval('#submissions') == 1
        assert state.pending_action_summary is None
    asyncio.run(scenario())


@pytest.mark.parametrize('tamper', [
    'seed=99', 'turn=42', 'units[196608].x=20', 'units[196608].id=262144',
    'plot_owner=2', 'improvement=5', 'busy=true', 'busy=nil',
    'Cities.GetPlotPurchaseCity=function() return nil end',
    '__civmcpPillageRequest.token="another"',
])
def test_unknown_pillage_cannot_confirm_in_different_scope_or_target(tamper, monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, 'ACTION_CONFIRM_TIMEOUT', .005)
    conn = PillageConn()
    conn.lua.execute('deferred=true')
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.pillage_unit(196608))
    conn.lua.execute('improvement_pillaged=true;' + tamper)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.pillage_unit(196608))
    assert state.pending_action_summary
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.fortify_unit(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.save_game('no_unconfirmed_checkpoint'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    assert conn.lua.eval('#submissions') == 1


def test_movement_or_yields_without_damage_never_confirm_pillage(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, 'ACTION_CONFIRM_TIMEOUT', .005)
    conn = PillageConn()
    conn.lua.execute('deferred=true')
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.pillage_unit(196608))
    conn.lua.execute('units[196608].moves=0;gold=999;science=999;culture=999')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action())
    assert state.pending_action_summary
    conn.lua.execute('improvement_pillaged=true')
    assert 'damaged:improvement:4' in asyncio.run(state.confirm_pending_action())
    assert conn.lua.eval('#submissions') == 1


def test_native_failure_after_entry_preserves_owned_attempt_until_damage_is_observed(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, 'ACTION_CONFIRM_TIMEOUT', .005)
    conn = PillageConn()
    conn.lua.execute('fail_request=true;addUnit(65537,true,0,0)')
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.pillage_unit(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.pillage_unit(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.skip_unit(65537))
    conn.lua.execute('fail_request=false;improvement_pillaged=true')
    assert (asyncio.run(state.confirm_pending_action())).startswith('PILLAGED')
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('tamper', [
    'buildings[11]=false', 'buildings[9]=nil', 'buildings[9]=true',
    'city.GetID=function() return 65540 end',
    'city.GetOwner=function() return 2 end',
    'district_type=3', 'district_pillaged=true',
])
def test_district_confirmation_requires_same_complete_target_and_one_changed_layer(tamper, monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, 'ACTION_CONFIRM_TIMEOUT', .005)
    conn = PillageConn()
    conn.lua.execute('deferred=true;improvement=-1;district_type=2;buildings={[9]=false,[10]=false}')
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.pillage_unit(196608))
    conn.lua.execute('buildings[10]=true;' + tamper)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action())
    assert state.pending_action_summary
    assert conn.lua.eval('#submissions') == 1


def test_stale_composite_id_never_targets_replacement_unit():
    conn = PillageConn()
    conn.lua.execute('units[196608].id=262144;UnitManager.GetUnit=function() return units[196608] end')
    state = GameState(conn)
    assert 'UNIT_ID_CHANGED' in asyncio.run(state.pillage_unit(196608))
    assert conn.lua.eval('#submissions') == 0


def test_mcp_repeat_of_unknown_pillage_confirms_original_without_a_second_layer(monkeypatch):
    from civ_mcp import server
    from test_server import make_ctx, app_of
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, 'ACTION_CONFIRM_TIMEOUT', .005)
    conn = PillageConn()
    conn.lua.execute('deferred=true;improvement=-1;district_type=2;buildings={[9]=false,[10]=false};pillage_layer=10')
    ctx = make_ctx()
    app_of(ctx).game = state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'pillage'))
    conn.lua.execute('buildings[10]=true;deferred=false;pillage_layer=9')
    assert 'damaged:building:10' in asyncio.run(server.unit_action(ctx, 196608, 'pillage'))
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('buildings[9]') is False
    assert state.pending_action_summary is None


@pytest.mark.parametrize('action,kwargs', [
    ('improve', {'improvement': 'IMPROVEMENT_FARM'}), ('automate', {}),
    ('heal', {}), ('sleep', {}), ('trade_route', {'target_x': 1, 'target_y': 2}),
])
def test_mcp_other_units_cannot_cross_unconfirmed_pillage_gate(action, kwargs, monkeypatch):
    from civ_mcp import server
    from test_server import make_ctx, app_of
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, 'ACTION_CONFIRM_TIMEOUT', .005)
    conn = PillageConn()
    conn.lua.execute('deferred=true;addUnit(65537,true,0,0)')
    ctx = make_ctx()
    app_of(ctx).game = state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.pillage_unit(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 65537, action, **kwargs))
    assert state.pending_action_summary
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('tool,args', [
    ('upgrade_unit', (65537,)), ('promote_unit', (65537, 'PROMOTION_BATTLECRY')),
])
def test_separate_unit_mcp_tools_respect_unconfirmed_pillage_gate(tool, args, monkeypatch):
    from civ_mcp import server
    from test_server import make_ctx, app_of
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, 'ACTION_CONFIRM_TIMEOUT', .005)
    conn = PillageConn()
    ctx = make_ctx()
    app_of(ctx).game = state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.pillage_unit(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(server, tool)(ctx, *args))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('pending', ['_pending_end_turn', '_pending_load', '_pending_save'])
def test_unconfirmed_turn_or_checkpoint_refuses_pillage_before_any_operation(pending):
    conn = PillageConn()
    state = GameState(conn)
    setattr(state, pending, True)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.pillage_unit(196608))
    assert conn.lua.eval('#submissions') == 0
