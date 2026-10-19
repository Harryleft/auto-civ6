"""Tile operations through public MCP/GameState and actual generated Lua."""
import asyncio
import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx, app_of
from test_unit_readiness import ReadyConn


class TileActionConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply=False
        self.lua.execute('''
function rows(values,keys)
 return setmetatable(keys or {},{__call=function()
  local i=0;return function() i=i+1;return values[i] end
 end})
end
function event()
 local listeners={}
 return {Add=function(f) listeners[f]=true end,Remove=function(f) listeners[f]=nil end,
 Fire=function(...) for f in pairs(listeners) do f(...) end end}
end
Events={UnitOperationStarted=event(),UnitOperationAdded=event(),UnitOperationDeactivated=event(),UnitChargesChanged=event(),UnitRemovedFromMap=event()}
u=units[196608];u.charges=3
u.GetBuildCharges=function(self) return self.charges end
u.GetType=function() return 1 end
GameInfo.Units={[1]={UnitType='UNIT_BUILDER'}}
plot={x=19,y=17,index=77,owner=0,improvement=-1,feature=-1,route=-1,pillaged=false}
plot.GetX=function(self) return self.x end
plot.GetY=function(self) return self.y end
plot.GetIndex=function(self) return self.index end
plot.GetOwner=function(self) return self.owner end
plot.GetImprovementType=function(self) return self.improvement end
plot.IsImprovementPillaged=function(self) return self.pillaged end
plot.GetFeatureType=function(self) return self.feature end
plot.GetRouteType=function(self) return self.route end
Map={GetPlot=function(x,y) if x==19 and y==17 then return plot end end}
local farm={Index=6,Hash=106,ImprovementType='IMPROVEMENT_FARM',Buildable=true}
GameInfo.Improvements=rows({farm},{IMPROVEMENT_FARM=farm,[6]=farm})
buildCost={ImprovementType='IMPROVEMENT_FARM',UnitType='UNIT_BUILDER',ConsumesCharge=true,ValidRepairOnly=false}
GameInfo.Improvement_ValidBuildUnits=rows({buildCost})
UnitOperationTypes={PARAM_X='x',PARAM_Y='y',PARAM_IMPROVEMENT_TYPE='improvement'}
GameInfo.UnitOperations={
 UNITOPERATION_BUILD_IMPROVEMENT={Hash=31},UNITOPERATION_REPAIR={Hash=32},
 UNITOPERATION_REMOVE_FEATURE={Hash=33},UNITOPERATION_REMOVE_IMPROVEMENT={Hash=34},
 UNITOPERATION_BUILD_ROUTE={Hash=35}}
UnitManager.CanStartOperation=function(unit,op,unknown,params,check)
 assert(unknown==nil and check==true)
 assert(params.x==19 and params.y==17)
 return unit.allowed
end
function applyTile(op,params)
 Events.UnitOperationStarted.Fire(0,u.id,op)
 if op==31 then assert(params.improvement==106);plot.improvement=6;plot.pillaged=false end
 local cost=buildCost.ConsumesCharge and 1 or 0
 local before=u.charges
 u.charges=u.charges-cost
 if cost>0 then Events.UnitChargesChanged.Fire(0,u.id,u.charges,before) end
 if remove_unit and u.charges==0 then
  units[u.id]=nil;Events.UnitRemovedFromMap.Fire(0,u.id)
  u.GetX=function() error('consumed userdata') end
  u.GetY=function() error('consumed userdata') end
  u.GetBuildCharges=function() error('consumed userdata') end
 end
 Events.UnitOperationDeactivated.Fire(0,u.id,op)
end
UnitManager.RequestOperation=function(unit,op,params)
 table.insert(submissions,{id=unit.id,op=op,params=params})
 if not deferred then applyTile(op,params) end
 if fail_request then error('native op threw after entry') end
end
''')

    async def execute_write(self,code):
        before=self.lua.eval('#submissions')
        rows=await super().execute_write(code)
        if self.lose_reply and self.lua.eval('#submissions')>before:
            self.lose_reply=False
            raise OSError('effect applied; response lost')
        return rows


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT',.01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL',.001)


def test_last_charge_improvement_lost_reply_only_confirms_original_plot():
    conn=TileActionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('u.charges=1;remove_unit=true');conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,'improve',improvement='IMPROVEMENT_FARM'))
    assert asyncio.run(server.unit_action(ctx,196608,'improve',improvement='IMPROVEMENT_FARM')).startswith('IMPROVED|')
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('plot.improvement')==6
    assert conn.lua.eval('units[196608]==nil')
    assert state.pending_action_summary is None


def test_repair_observes_unconsumed_charges_and_never_blind_submits_after_refusal():
    conn=TileActionConn();state=GameState(conn)
    conn.lua.execute('''
plot.improvement=6;plot.pillaged=true;u.allowed=false
function applyTile(op,params)
 assert(op==32 and params.improvement==106)
 Events.UnitOperationStarted.Fire(0,u.id,op)
 plot.pillaged=false
 Events.UnitOperationDeactivated.Fire(0,u.id,op)
end
''')
    assert 'TILE_ACTION_REFUSED' in asyncio.run(state.repair_improvement(196608))
    assert conn.lua.eval('#submissions')==0
    conn.lua.execute('u.allowed=true')
    assert asyncio.run(state.repair_improvement(196608)).startswith('REPAIRED|')
    assert conn.lua.eval('u.charges')==3
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('kind,op,field,cost',[
    ('remove_feature',33,'feature',1),('remove_improvement',34,'improvement',0),
    ('remove_improvement',34,'improvement',1),
])
def test_removal_confirms_actual_consumption_and_original_plot(kind,op,field,cost):
    conn=TileActionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute(f'''
plot.{field}=6
function applyTile(op,params)
 assert(op=={op})
 Events.UnitOperationStarted.Fire(0,u.id,op)
 plot.{field}=-1
 local before=u.charges;u.charges=before-{cost}
 if {cost}>0 then Events.UnitChargesChanged.Fire(0,u.id,u.charges,before) end
 Events.UnitOperationDeactivated.Fire(0,u.id,op)
end
''')
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,kind))
    result=asyncio.run(server.unit_action(ctx,196608,kind))
    assert result.startswith('REMOVED_')
    assert f'charges:3->{3-cost}' in result
    assert conn.lua.eval('#submissions')==1


class RouteConn(TileActionConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
GameInfo.Units[1].UnitType='UNIT_MILITARY_ENGINEER'
local road={Index=9,RouteType='ROUTE_ROAD'}
local rail={Index=17,RouteType='ROUTE_RAILROAD'}
GameInfo.Routes=rows({road,rail},{ROUTE_ROAD=road,ROUTE_RAILROAD=rail,[9]=road,[17]=rail})
GameInfo.Routes_XP2=rows({{RouteType='ROUTE_ROAD',BuildWithUnitChargeCost=1},{RouteType='ROUTE_RAILROAD',BuildWithUnitChargeCost=0}})
GameInfo.Route_ResourceCosts=rows({
 {RouteType='ROUTE_RAILROAD',ResourceType='RESOURCE_IRON',BuildWithUnitCost=1},
 {RouteType='ROUTE_RAILROAD',ResourceType='RESOURCE_COAL',BuildWithUnitCost=1}})
GameInfo.Route_ValidBuildUnits=rows({{RouteType='ROUTE_ROAD',UnitType='UNIT_MILITARY_ENGINEER'},{RouteType='ROUTE_RAILROAD',UnitType='UNIT_MILITARY_ENGINEER'}})
GameInfo.Resources={RESOURCE_IRON={Index=81},RESOURCE_COAL={Index=82}}
stock={[81]=5,[82]=5};routeTarget=17
Players[0].GetResources=function() return {GetResourceAmount=function(self,id) return stock[id] end} end
function applyTile(op,params)
 assert(op==35)
 Events.UnitOperationStarted.Fire(0,u.id,op)
 plot.route=routeTarget
 local before=u.charges
 if routeTarget==17 then stock[81]=stock[81]-1;stock[82]=stock[82]-1
 else u.charges=u.charges-1;Events.UnitChargesChanged.Fire(0,u.id,u.charges,before) end
 if remove_unit and u.charges==0 then
  units[u.id]=nil;Events.UnitRemovedFromMap.Fire(0,u.id)
  u.GetX=function() error('consumed userdata') end
  u.GetY=function() error('consumed userdata') end
  u.GetBuildCharges=function() error('consumed userdata') end
 end
 Events.UnitOperationDeactivated.Fire(0,u.id,op)
end
''')


@pytest.mark.parametrize('target,expected_charges,expected_stock',[(9,2,5),(17,3,4)])
def test_route_uses_actual_native_route_cost_not_hardcoded_rail_index(target,expected_charges,expected_stock):
    conn=RouteConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.globals().routeTarget=target;conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,'build_route'))
    assert asyncio.run(server.unit_action(ctx,196608,'build_route')).startswith('ROUTE_BUILT|')
    assert conn.lua.eval('u.charges')==expected_charges
    assert conn.lua.eval('stock[81]')==expected_stock
    assert conn.lua.eval('stock[82]')==expected_stock
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('method,args,setup,label',[
    ('improve_tile',(196608,'IMPROVEMENT_FARM'),'plot.improvement=6','ALREADY_IMPROVED'),
    ('repair_improvement',(196608,),'plot.improvement=6','ALREADY_REPAIRED'),
    ('remove_feature',(196608,),'','ALREADY_REMOVED_FEATURE'),
    ('remove_improvement',(196608,),'','ALREADY_REMOVED_IMPROVEMENT'),
])
def test_already_satisfied_tile_goal_does_not_consume_charges(method,args,setup,label):
    conn=TileActionConn();state=GameState(conn);conn.lua.execute('u.allowed=false;'+setup)
    assert asyncio.run(getattr(state,method)(*args)).startswith(label+'|')
    assert conn.lua.eval('#submissions')==0
    assert conn.lua.eval('u.charges')==3


def test_plot_effect_and_charge_consumption_must_both_be_visible():
    conn=TileActionConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    conn.lua.execute('Events.UnitOperationStarted.Fire(0,u.id,31);plot.improvement=6')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    conn.lua.execute('u.charges=2')
    assert asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM')).startswith('IMPROVED|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('setup',[
    'u.charges=2',
    'plot.improvement=6;u.charges=2;u.id=262144',
    'plot.improvement=6;u.charges=2;plot.index=99',
    'plot.improvement=6;u.charges=2;plot.owner=1',
    'plot.improvement=6;u.charges=2;plot.pillaged=true',
    'plot.improvement=6;u.charges=2;busy=true',
])
def test_partial_or_wrong_identity_evidence_cannot_confirm_improvement(setup):
    conn=TileActionConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    conn.lua.execute('Events.UnitOperationStarted.Fire(0,u.id,31);'+setup)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    assert conn.lua.eval('#submissions')==1


def test_consumed_builder_needs_matching_charge_and_removal_events():
    conn=TileActionConn();state=GameState(conn);conn.lua.execute('deferred=true;u.charges=1')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    conn.lua.execute('Events.UnitOperationStarted.Fire(0,u.id,31);plot.improvement=6;units[u.id]=nil;Events.UnitRemovedFromMap.Fire(0,u.id)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    conn.lua.execute('Events.UnitChargesChanged.Fire(0,u.id,0,1)')
    assert asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM')).startswith('IMPROVED|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('target,effect',[
    (9,'plot.route=9'),
    (17,'plot.route=17;stock[81]=4'),
    (17,'stock[81]=4;stock[82]=4'),
    (17,'plot.route=9;stock[81]=4;stock[82]=4'),
])
def test_route_partial_target_charge_or_resource_effect_remains_unknown(target,effect):
    conn=RouteConn();state=GameState(conn);conn.lua.execute(f'deferred=true;routeTarget={target}')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.build_route(196608))
    conn.lua.execute('Events.UnitOperationStarted.Fire(0,u.id,35);'+effect)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.build_route(196608))
    assert conn.lua.eval('#submissions')==1


def test_road_last_charge_removal_keeps_original_receipt():
    conn=RouteConn();state=GameState(conn);conn.lua.execute('u.charges=1;routeTarget=9;remove_unit=true')
    assert asyncio.run(state.build_route(196608)).startswith('ROUTE_BUILT|')
    assert conn.lua.eval('units[196608]==nil')
    assert conn.lua.eval('#submissions')==1


def test_native_zero_charge_improvement_is_confirmed_without_forcing_a_decrement():
    conn=TileActionConn();state=GameState(conn);conn.lua.execute('buildCost.ConsumesCharge=false;u.charges=0')
    result=asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    assert result.startswith('IMPROVED|')
    assert 'charges:0->0' in result
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('setup',[
    'u.allowed=false','u.allowed=nil','UnitManager.CanStartOperation=nil',
    'UnitManager.CanStartOperation=function() error("unavailable") end',
    'UnitManager.RequestOperation=nil','Events.UnitRemovedFromMap=nil',
    'u.id=262144','u.GetBuildCharges=nil','buildCost.ConsumesCharge=nil',
    'buildCost.ValidRepairOnly=true','busy=true','busy=nil',
])
def test_improvement_preflight_is_strict_and_never_attempts_unknown_eligibility(setup):
    conn=TileActionConn();state=GameState(conn);conn.lua.execute(setup)
    assert 'TILE_ACTION_REFUSED' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


def test_pillaged_improve_requires_explicit_repair_and_cannot_take_a_different_operation():
    conn=TileActionConn();state=GameState(conn);conn.lua.execute('plot.improvement=6;plot.pillaged=true')
    assert 'PILLAGED_IMPROVEMENT_USE_REPAIR' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    assert conn.lua.eval('#submissions')==0


def test_repair_plot_change_alone_cannot_supply_operation_completion():
    conn=TileActionConn();state=GameState(conn);conn.lua.execute('plot.improvement=6;plot.pillaged=true;deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.repair_improvement(196608))
    conn.lua.execute('Events.UnitOperationStarted.Fire(0,u.id,32);plot.pillaged=false')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.repair_improvement(196608))
    conn.lua.execute('Events.UnitOperationDeactivated.Fire(0,u.id,32)')
    assert asyncio.run(state.repair_improvement(196608)).startswith('REPAIRED|')
    assert conn.lua.eval('#submissions')==1


def test_native_throw_after_effect_is_unknown_and_not_replayed():
    conn=TileActionConn();state=GameState(conn);conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    assert conn.lua.eval('#submissions')==1


def test_cancelled_submission_can_only_confirm_original_charge():
    class CancelConn(TileActionConn):
        async def execute_write(self,code):
            before=self.lua.eval('#submissions');rows=await super().execute_write(code)
            if self.lua.eval('#submissions')>before: raise asyncio.CancelledError()
            return rows
    conn=CancelConn();state=GameState(conn)
    with pytest.raises(asyncio.CancelledError): asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    assert state.pending_action_summary is not None
    assert asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM')).startswith('IMPROVED|')
    assert conn.lua.eval('#submissions')==1


def test_pending_tile_operation_blocks_other_unit_empire_and_different_intent():
    conn=TileActionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('deferred=true;addUnit(196609,true,0,0)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196609,'sleep'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_OTHER'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_research('TECH_POTTERY'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    assert conn.lua.eval('#submissions')==1


def test_world_change_latches_old_tile_attempt_even_after_original_world_returns():
    conn=TileActionConn();state=GameState(conn);conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    conn.lua.execute('seed=9')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    conn.lua.execute('seed=719665280')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    assert conn.lua.eval('#submissions')==1


def test_scope_change_before_entry_proves_no_submission():
    class ChangedConn(TileActionConn):
        async def execute_write(self,code):
            if '-- ACTION_SUBMIT_SCOPE|' in code:self.lua.execute('turn=42')
            return await super().execute_write(code)
    conn=ChangedConn();state=GameState(conn)
    assert 'ACTION_SCOPE_NO_SUBMIT' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


def test_unavailable_confirmation_preserves_original_tile_request():
    class ReadErrorConn(TileActionConn):
        async def execute_write(self,code):
            if self.lua.eval('#submissions'):raise OSError('read unavailable')
            return await super().execute_write(code)
    conn=ReadErrorConn();state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(196608,'IMPROVEMENT_FARM'))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('kind', ['improve', 'repair', 'remove_feature', 'remove_improvement', 'build_route'])
def test_mcp_tile_actions_preserve_full_identity_before_any_native_submit(kind):
    conn=TileActionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('u.id=262144')
    result=asyncio.run(server.unit_action(ctx,196608,kind,improvement='IMPROVEMENT_FARM'))
    assert 'UNIT_ID_CHANGED' in result
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


def test_legacy_low_id_captures_original_full_identity_across_delayed_completion():
    conn=TileActionConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(0,'IMPROVEMENT_FARM'))
    conn.lua.execute('Events.UnitOperationStarted.Fire(0,196608,31);plot.improvement=6;u.charges=2;u.id=262144')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.improve_tile(0,'IMPROVEMENT_FARM'))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('setup', [
    'GameInfo.Routes_XP2=nil', 'GameInfo.Route_ResourceCosts=nil',
    'GameInfo.Route_ValidBuildUnits=nil', 'stock[81]=nil',
    'u.allowed=false;plot.route=17',
])
def test_route_without_native_cost_or_eligibility_is_explicitly_not_submitted(setup):
    conn=RouteConn();state=GameState(conn);conn.lua.execute(setup)
    assert 'TILE_ACTION_REFUSED' in asyncio.run(state.build_route(196608))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None
