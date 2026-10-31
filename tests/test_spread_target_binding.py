"""Explicit local spread targets bind expectations, never SDK coordinate steering."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from test_unit_effect_confirmation import EffectConn
from test_spread_core_followers import CoreFollowersConn


LOCAL_MAP = '''
OperationResultsTypes={NO_TARGETS=0}
local offsets={{1,0},{1,-1},{0,-1},{-1,0},{-1,1},{0,1}}
local function plot(x,y)
 return {GetX=function()return x end,GetY=function()return y end,GetIndex=function()return y*1000+x end}
end
Map.GetPlot=plot
Map.GetAdjacentPlot=function(x,y,d)local v=offsets[d+1];return plot(x+v[1],y+v[2])end
Map.GetPlotByIndex=function(i)if i==300 then return plot(city.x,city.y)end end
PlayersVisibility={[0]={IsVisible=function(_,i)if i==hiddenPlot then return false end;return true end}}
local originalCityAt=CityManager.GetCityAt
CityManager.GetCityAt=function(x,y)
 if extraCity and x==extraCity.x and y==extraCity.y then return extraCity end
 return originalCityAt(x,y)
end
UnitManager.CanStartOperation=function(actor,op,unused,visible,results)
 assert(actor==u and op==32 and unused==nil and visible==false and results==OperationResultsTypes.NO_TARGETS)
 return u.allowed
end
local originalRequest=UnitManager.RequestOperation
UnitManager.RequestOperation=function(...)
 assert(select('#',...)==2,'SPREAD_MUST_USE_NATIVE_UI_TWO_ARGUMENT_FORM')
 return originalRequest(...)
end
'''


class LocalSpreadConn(EffectConn):
    def __init__(self):
        super().__init__('spread')
        self.lua.execute(LOCAL_MAP)


class LocalCoreConn(CoreFollowersConn):
    def __init__(self):
        super().__init__()
        self.lua.execute(LOCAL_MAP+';u.y=27;targets=nil')


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT',.02)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL',.001)


def test_generic_spread_coordinates_are_not_discarded_before_dispatch():
    conn=EffectConn('spread');state=GameState(conn)
    result=asyncio.run(server.unit_action(make_ctx(game=state),196608,'spread_religion',99,99))
    assert result.startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions')==0


def test_explicit_single_visible_neighbor_without_target_arrays_confirms_actual_effect():
    conn=LocalSpreadConn();state=GameState(conn);conn.lua.execute('targets=nil')
    result=asyncio.run(server.unit_action(make_ctx(game=state),196608,'spread_religion',20,17))
    assert result.startswith('RELIGION_SPREAD|') and 'city:1:65539 pressure:100->300' in result
    assert conn.lua.eval('u.x')==19 and conn.lua.eval('u.spreads')==1
    assert conn.lua.eval('#submissions')==1 and state.pending_action_summary is None


@pytest.mark.parametrize('setup,reason',[
    ('hiddenPlot=17018','TARGET_AREA_NOT_OBSERVABLE'),
    ('extraCity={};for k,v in pairs(city)do extraCity[k]=v end;extraCity.x=19;extraCity.y=16;extraCity.id=65540','AMBIGUOUS_CITY_TARGET'),
    ('targets={plots={},modifiers={}}','TARGET_NATIVE_CONFLICT'),
    ('targets={plots={300}}','TARGET_NATIVE_CONFLICT'),
    ('Map.GetAdjacentPlot=nil','TARGET_AREA_UNAVAILABLE'),
    ('Map.GetAdjacentPlot=function()return nil end','TARGET_AREA_UNAVAILABLE'),
    ('PlayersVisibility=nil','TARGET_AREA_NOT_OBSERVABLE'),
    ('Map.GetPlotByIndex=function()return {GetX=function()return 99 end,GetY=function()return 99 end}end','TARGET_NATIVE_CONFLICT'),
    ('u.allowed=false','CANNOT_OPERATE'),
])
def test_explicit_spread_refuses_uncertain_or_ineligible_local_binding(setup,reason):
    conn=LocalSpreadConn();state=GameState(conn);conn.lua.execute(setup)
    result=asyncio.run(state.spread_religion(196608,20,17))
    assert result.startswith('Error: UNIT_EFFECT_REFUSED|') and reason in result
    assert conn.lua.eval('#submissions')==0 and state.pending_action_summary is None
    if reason.startswith('TARGET') or reason=='AMBIGUOUS_CITY_TARGET':
        assert '[string' not in result and 'no spread submitted' in result


@pytest.mark.parametrize('x,y',[(19,16),(99,99)])
def test_explicit_spread_does_not_choose_a_different_or_distant_city(x,y):
    conn=LocalSpreadConn();state=GameState(conn)
    assert 'UNIT_EFFECT_REFUSED' in asyncio.run(state.spread_religion(196608,x,y))
    assert conn.lua.eval('#submissions')==0


@pytest.mark.parametrize('x,y',[(20,None),(None,17),(True,17),(-1,17),(20,17.5)])
def test_invalid_spread_coordinate_pair_never_submits(x,y):
    conn=LocalSpreadConn();state=GameState(conn)
    assert 'INVALID_SPREAD_TARGET' in asyncio.run(state.spread_religion(196608,x,y))
    assert conn.lua.eval('#submissions')==0


def test_pending_explicit_spread_keeps_original_target_and_never_resubmits():
    conn=LocalSpreadConn();state=GameState(conn);conn.lua.execute('targets=nil;deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(196608,20,17))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(196608,19,16))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(196608))
    conn.lua.execute('applyEffect(32)')
    assert asyncio.run(state.spread_religion(196608,20,17)).startswith('RELIGION_SPREAD|')
    assert conn.lua.eval('#submissions')==1


def test_scope_change_inside_unavailable_native_target_read_is_zero_submit():
    conn=LocalSpreadConn();state=GameState(conn)
    conn.lua.execute("UnitManager.GetOperationTargets=function()seed=99;error('unavailable target read')end")
    assert 'UNIT_EFFECT_REFUSED' in asyncio.run(state.spread_religion(196608,20,17))
    assert conn.lua.eval('#submissions')==0 and state.pending_action_summary is None


@pytest.mark.parametrize('change,restore', [('city.id=999','city.id=65539'),('city.owner=2','city.owner=1'),('u.x=18','u.x=19'),('seed=99','seed=719665280')])
def test_original_explicit_effect_identity_or_scope_change_is_sticky(change,restore):
    conn=LocalSpreadConn();state=GameState(conn);conn.lua.execute('targets=nil;deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(196608,20,17))
    conn.lua.execute('applyEffect(32);'+change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(196608,20,17))
    conn.lua.execute(restore)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(196608,20,17))
    assert conn.lua.eval('#submissions')==1


def test_native_operation_without_expected_city_effect_stays_pending_without_replay():
    conn=LocalSpreadConn();state=GameState(conn);conn.lua.execute('''targets=nil
local apply=applyEffect
function applyEffect(op)local before=city.pressure;apply(op);city.pressure=before end''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(196608,20,17))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(196608,20,17))
    assert conn.lua.eval('#submissions')==1 and state.pending_action_summary is not None


@pytest.mark.parametrize('spreads,after,prefix',[(1,4,'RELIGION_SPREAD|'),(1,3,'RELIGION_SPREAD_EXECUTED|'),(2,3,'RELIGION_SPREAD_EXECUTED|')])
def test_adjacent_owned_city_core_evidence_keeps_actor_and_city_coordinates_separate(spreads,after,prefix):
    conn=LocalCoreConn();state=GameState(conn);conn.lua.execute(f'u.spreads={spreads};afterFollowers={after}')
    result=asyncio.run(state.spread_religion(2686987,34,26))
    assert result.startswith(prefix) and f'GameCore followers:3->{after}' in result
    assert conn.lua.eval('u.y')==27 and conn.lua.eval('city.y')==26
    assert conn.lua.eval('#submissions')==1 and state.pending_action_summary is None


def test_foreign_city_does_not_borrow_owned_core_followers_fallback():
    conn=LocalCoreConn();state=GameState(conn);conn.lua.execute('city.owner=1;afterFollowers=4')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987,34,26))
    assert conn.lua.eval('#submissions')==1 and state.pending_action_summary is not None


def test_owned_core_preflight_actor_position_must_match_ingame_actor_not_city():
    conn=LocalCoreConn();state=GameState(conn);conn.lua.execute('''
u.GetY=function(self)if core then return 28 end;return self.y end
''')
    result=asyncio.run(state.spread_religion(2686987,34,26))
    assert 'CORE_CITY_OR_RELIGION_CHANGED' in result
    assert conn.lua.eval('#submissions')==0 and state.pending_action_summary is None
