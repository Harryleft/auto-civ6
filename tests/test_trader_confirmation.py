"""Real MCP/GameState/Lua with native route records and transport simulated."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx, app_of
from test_unit_readiness import ReadyConn


class TraderConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply=False
        self.lua.execute('''
capacity,allowed=2,true
InterfaceModeTypes={SELECTION=1,MAKE_TRADE_ROUTE=-1756334557,TELEPORT_TO_CITY=3,DIPLOMACY=4}
mode,modeChanges=InterfaceModeTypes.SELECTION,{}
UI.GetInterfaceMode=function() return mode end
UI.SetInterfaceMode=function(value) mode=value;table.insert(modeChanges,value) end
local unit=units[196608];unit.x=10;unit.y=10;unit.owner=0;unit.kind=5
UI.GetHeadSelectedUnit=function() return unit end
Players[0].GetUnits=function() return {Members=function() return pairs(units) end,
 FindID=function(_,id) return units[id] end} end
unit.GetX=function(self) return self.x end
unit.GetY=function(self) return self.y end
unit.GetOwner=function(self) return self.owner end
unit.GetType=function(self) return self.kind end
GameInfo.Units={[5]={MakeTradeRoute=true}}
GameInfo.UnitOperations.UNITOPERATION_TELEPORT_TO_CITY={Hash=20}
UnitOperationTypes={MAKE_TRADE_ROUTE=21,PARAM_X0='dx',PARAM_Y0='dy',PARAM_X1='ox',PARAM_Y1='oy',PARAM_X='x',PARAM_Y='y'}
function newCity(id,owner,x,y)
 local c={id=id,owner=owner,x=x,y=y,routes={}}
 c.GetID=function(self) return self.id end
 c.GetOwner=function(self) return self.owner end
 c.GetX=function(self) return self.x end
 c.GetY=function(self) return self.y end
 c.GetName=function() return 'City' end
 c.GetTrade=function(self) return {GetOutgoingRoutes=function() return self.routes end} end
 return c
end
origin=newCity(65537,0,10,10);domestic=newCity(65538,0,20,20);foreign=newCity(65539,1,30,30)
cities={origin,domestic}
function cityAt(x,y)
 for _,c in pairs({origin,domestic,foreign}) do if c.x==x and c.y==y then return c end end
end
CityManager={GetCityAt=cityAt}
function activeCount() return #origin.routes+#domestic.routes end
Players[0].GetCities=function() return {Members=function() return pairs(cities) end} end
Players[0].GetTrade=function() return {GetOutgoingRouteCapacity=function() return capacity end,GetNumOutgoingRoutes=activeCount} end
Locale={Lookup=function(s) return s end}
UnitManager.CanStartOperation=function(u,op,visibility,params)
 assert(u.id==196608 and visibility==nil)
 return allowed and (op==20 or u.moves>0)
end
function applyTrader(op,p)
 if op==21 then
  local from,to=cityAt(p.ox,p.oy),cityAt(p.dx,p.dy)
  table.insert(from.routes,{TraderUnitID=unit.id,OriginCityPlayer=from.owner,OriginCityID=from.id,
   DestinationCityPlayer=to.owner,DestinationCityID=to.id})
  unit.x=11;unit.y=10
 else unit.x=p.x;unit.y=p.y end
end
UnitManager.RequestOperation=function(u,op,p)
 table.insert(submissions,{op=op,params=p})
 if not deferred then applyTrader(op,p) end
 if fail_request then error('native operation entered then failed') end
end
''')

    async def execute_write(self,code):
        before=self.lua.eval('#submissions')
        rows=await super().execute_write(code)
        if self.lose_reply and self.lua.eval('#submissions')>before:
            self.lose_reply=False
            raise OSError('native trader operation ran; response lost')
        return rows


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT',.01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL',.001)


@pytest.mark.parametrize('method,x,y,chooser',[
    ('make_trade_route',30,30,'MAKE_TRADE_ROUTE'),
    ('teleport_to_city',20,20,'TELEPORT_TO_CITY'),
])
def test_native_trader_operation_returns_its_chooser_to_selection(method,x,y,chooser):
    conn=TraderConn();state=GameState(conn)
    conn.lua.execute('mode=InterfaceModeTypes.'+chooser)
    result=asyncio.run(getattr(state,method)(196608,x,y))
    assert result.startswith('TRADE_ROUTE_STARTED|' if method=='make_trade_route' else 'TELEPORTED|')
    assert conn.lua.eval('mode')==1
    assert conn.lua.eval('#modeChanges')==1
    assert conn.lua.eval('#submissions')==1


def test_established_route_with_failed_cleanup_stays_explicit_and_never_replays():
    conn=TraderConn();state=GameState(conn)
    conn.lua.execute('''
mode=InterfaceModeTypes.MAKE_TRADE_ROUTE;cleanupCalls=0
UI.SetInterfaceMode=function(value) cleanupCalls=cleanupCalls+1;error('UI cleanup failed') end
''')
    result=asyncio.run(state.make_trade_route(196608,30,30))
    assert 'OUTCOME_UNKNOWN' in result
    assert 'ROUTE_ESTABLISHED_INTERFACE_CLEANUP_UNCONFIRMED' in result
    assert conn.lua.eval('activeCount()')==1
    assert conn.lua.eval('cleanupCalls')==1
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(196608,30,30))
    assert conn.lua.eval('cleanupCalls')==1
    # A subsequent native/UI action may close the chooser; confirmation is read-only.
    conn.lua.execute('mode=InterfaceModeTypes.SELECTION')
    assert asyncio.run(state.make_trade_route(196608,30,30)).startswith('TRADE_ROUTE_STARTED|')
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('cleanupCalls')==1


@pytest.mark.parametrize('method,x,y,chooser',[
    ('make_trade_route',30,30,'MAKE_TRADE_ROUTE'),
    ('teleport_to_city',20,20,'TELEPORT_TO_CITY'),
])
@pytest.mark.parametrize('new_mode',[False,True])
def test_trader_cleanup_preserves_unrelated_interface_mode(method,x,y,chooser,new_mode):
    conn=TraderConn();state=GameState(conn)
    conn.lua.execute('mode=InterfaceModeTypes.'+(chooser if new_mode else 'DIPLOMACY'))
    if new_mode:
        conn.lua.execute('nativeApply=applyTrader;function applyTrader(op,p) nativeApply(op,p);mode=InterfaceModeTypes.DIPLOMACY end')
    assert 'OUTCOME_UNKNOWN' not in asyncio.run(getattr(state,method)(196608,x,y))
    assert conn.lua.eval('mode')==4
    assert conn.lua.eval('#modeChanges')==0
    assert conn.lua.eval('#submissions')==1


def test_closed_route_chooser_cannot_replace_exact_route_evidence():
    conn=TraderConn();state=GameState(conn)
    conn.lua.execute('deferred=true;mode=InterfaceModeTypes.MAKE_TRADE_ROUTE')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(196608,30,30))
    assert conn.lua.eval('mode')==1
    assert conn.lua.eval('activeCount()')==0
    conn.lua.execute('applyTrader(submissions[1].op,submissions[1].params)')
    assert asyncio.run(state.make_trade_route(196608,30,30)).startswith('TRADE_ROUTE_STARTED|')
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('#modeChanges')==1


def test_unknown_interface_read_after_route_acceptance_stays_unknown_without_replay():
    conn=TraderConn();state=GameState(conn)
    conn.lua.execute('nativeApply=applyTrader;function applyTrader(op,p) nativeApply(op,p);UI.GetInterfaceMode=function() return nil end end')
    result=asyncio.run(state.make_trade_route(196608,30,30))
    assert 'ROUTE_ESTABLISHED_INTERFACE_CLEANUP_UNCONFIRMED' in result
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(196608,30,30))
    assert conn.lua.eval('activeCount()')==1
    assert conn.lua.eval('#submissions')==1


def test_lost_route_reply_repeated_mcp_call_only_confirms_original_trader():
    conn=TraderConn(); state=GameState(conn); ctx=make_ctx(); app_of(ctx).game=state
    conn.lua.execute('mode=InterfaceModeTypes.MAKE_TRADE_ROUTE')
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,'trade_route',30,30))
    assert state.pending_action_kind=='trade_route'
    result=asyncio.run(server.unit_action(ctx,196608,'trade_route',30,30))
    assert result.startswith('TRADE_ROUTE_STARTED|unit:196608|')
    assert '0:65537->1:65539' in result
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('activeCount()')==1
    assert conn.lua.eval('mode')==1
    assert conn.lua.eval('#modeChanges')==1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('action,method,x,y,prefix',[
    ('trade_route','make_trade_route',30,30,'TRADE_ROUTE_STARTED|'),
    ('teleport','teleport_to_city',20,20,'TELEPORTED|'),
])
def test_normal_operation_confirms_exact_route_or_transfer(action,method,x,y,prefix):
    conn=TraderConn(); state=GameState(conn)
    result=asyncio.run(getattr(state,method)(196608,x,y))
    assert result.startswith(prefix)
    assert conn.lua.eval('#submissions')==1
    if action=='trade_route':
        assert 'future yields not yet earned' in result
        assert conn.lua.eval('activeCount()')==1
    else:
        assert 'no trade route started' in result
        assert conn.lua.eval('units[196608].x')==20
        assert conn.lua.eval('activeCount()')==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('action,x,y,prefix',[
    ('trade_route',30,30,'TRADE_ROUTE_STARTED|'),('teleport',20,20,'TELEPORTED|'),
])
def test_delayed_operation_repeated_mcp_call_never_resubmits(action,x,y,prefix):
    conn=TraderConn(); state=GameState(conn); ctx=make_ctx(); app_of(ctx).game=state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,action,x,y))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,action,x,y))
    conn.lua.execute('applyTrader(submissions[1].op,submissions[1].params)')
    assert asyncio.run(server.unit_action(ctx,196608,action,x,y)).startswith(prefix)
    assert conn.lua.eval('#submissions')==1


def test_lost_transfer_reply_confirms_without_a_second_teleport():
    conn=TraderConn(); state=GameState(conn); ctx=make_ctx(); app_of(ctx).game=state
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,'teleport',20,20))
    assert asyncio.run(server.unit_action(ctx,196608,'teleport',20,20)).startswith('TELEPORTED|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('action,x,y',[('trade_route',30,30),('teleport',20,20)])
def test_cancelled_native_operation_keeps_original_intent(action,x,y):
    class CancelConn(TraderConn):
        async def execute_write(self,code):
            before=self.lua.eval('#submissions')
            rows=await super().execute_write(code)
            if self.lua.eval('#submissions')>before:
                raise asyncio.CancelledError()
            return rows
    conn=CancelConn(); state=GameState(conn); ctx=make_ctx(); app_of(ctx).game=state
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(server.unit_action(ctx,196608,action,x,y))
    assert state.pending_action_kind==action
    assert 'OUTCOME_UNKNOWN' not in asyncio.run(server.unit_action(ctx,196608,action,x,y))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('change',[
    'origin.routes[1].TraderUnitID=196609',
    'origin.routes[1].OriginCityID=65538',
    'origin.routes[1].OriginCityPlayer=1',
    'origin.routes[1].DestinationCityID=65538',
    'origin.routes[1].DestinationCityPlayer=0',
    'capacity=0', 'foreign.id=131075', 'foreign.owner=2', 'origin.id=131073',
    'origin.x=12', 'units[196608].id=262144', 'units[196608].owner=1',
    'origin.routes={}', 'origin.routes[2]=origin.routes[1]',
])
def test_route_requires_original_trader_cities_capacity_and_exact_route(change):
    conn=TraderConn(); state=GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(196608,30,30))
    conn.lua.execute('applyTrader(submissions[1].op,submissions[1].params);'+change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(196608,30,30))
    assert state.pending_action_kind=='trade_route'
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('change',[
    'units[196608].x=19', 'domestic.id=131074', 'domestic.owner=1',
    'units[196608].id=262144', 'origin.id=131073',
])
def test_transfer_requires_original_unit_at_original_destination(change):
    conn=TraderConn(); state=GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.teleport_to_city(196608,20,20))
    conn.lua.execute('applyTrader(submissions[1].op,submissions[1].params);'+change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.teleport_to_city(196608,20,20))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('setup',[
    'capacity=0', 'capacity=nil', 'allowed=false', 'allowed=nil', 'units[196608].moves=0',
    'origin.owner=1', 'foreign=nil', 'GameInfo.Units[5].MakeTradeRoute=false',
    'units[196608].id=262144', 'units[196608].owner=1',
    'UnitManager.RequestOperation=nil', 'busy=true',
    'UI.GetInterfaceMode=nil', 'UI.SetInterfaceMode=nil',
    'UI.GetInterfaceMode=function() return nil end',
    'InterfaceModeTypes.MAKE_TRADE_ROUTE=nil', 'InterfaceModeTypes.SELECTION=nil',
    'origin.routes={{TraderUnitID=196608,OriginCityPlayer=0,OriginCityID=65537,DestinationCityPlayer=0,DestinationCityID=65538}}',
])
def test_native_route_refusal_is_zero_submission(setup):
    conn=TraderConn(); state=GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(state.make_trade_route(196608,30,30)).startswith('Error: TRADER_REFUSED|')
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('x,y',[(30,30),(10,10),(99,99)])
def test_transfer_refuses_foreign_current_or_missing_city(x,y):
    conn=TraderConn(); state=GameState(conn)
    assert asyncio.run(state.teleport_to_city(196608,x,y)).startswith('Error: TRADER_REFUSED|')
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('action,x,y',[('trade_route',30,30),('teleport',20,20)])
def test_mcp_full_native_trader_identity_is_not_truncated(action,x,y):
    conn=TraderConn(); state=GameState(conn); ctx=make_ctx(); app_of(ctx).game=state
    assert 'TRADER_ID_CHANGED' in asyncio.run(server.unit_action(ctx,262144,action,x,y))
    assert conn.lua.eval('#submissions')==0


def test_capacity_is_confirmed_from_actual_route_records():
    conn=TraderConn(); state=GameState(conn)
    conn.lua.execute('''
origin.routes={{TraderUnitID=999,OriginCityPlayer=0,OriginCityID=65537,DestinationCityPlayer=0,DestinationCityID=65538}}
Players[0].GetTrade=function() return {GetOutgoingRouteCapacity=function() return 2 end,
 GetNumOutgoingRoutes=function() error('unreliable aggregate is not route evidence') end} end
''')
    assert 'routes:2/2' in asyncio.run(state.make_trade_route(196608,30,30))
    assert conn.lua.eval('#submissions')==1


def test_other_intent_and_other_mutation_cannot_replace_pending_route():
    conn=TraderConn(); state=GameState(conn); ctx=make_ctx(); app_of(ctx).game=state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,'trade_route',30,30))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,'trade_route',20,20))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,196608,'teleport',20,20))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR'))
    assert state.pending_action_kind=='trade_route'
    assert conn.lua.eval('#submissions')==1


def test_native_transfer_eligibility_is_not_replaced_by_route_movement_rule():
    conn=TraderConn(); state=GameState(conn); conn.lua.execute('units[196608].moves=0')
    assert asyncio.run(state.teleport_to_city(196608,20,20)).startswith('TELEPORTED|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('method,x,y',[('make_trade_route',30,30),('teleport_to_city',20,20)])
def test_native_failure_after_entry_remains_unknown_without_replay(method,x,y):
    conn=TraderConn(); state=GameState(conn); conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state,method)(196608,x,y))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state,method)(196608,x,y))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('change',['seed=99','turn=42','__civmcpTraderAction=nil','__civmcpTraderAction.token="other"'])
def test_missing_receipt_or_wrong_world_never_confirms_the_new_route(change):
    conn=TraderConn(); state=GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(196608,30,30))
    conn.lua.execute('applyTrader(submissions[1].op,submissions[1].params);'+change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(196608,30,30))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('args',[(True,30,30),(196608,-1,30),(196608,30.5,30),('196608',30,30)])
def test_invalid_intent_never_submits_native_operation(args):
    conn=TraderConn(); state=GameState(conn)
    assert 'INVALID_TRADER_INTENT' in asyncio.run(state.make_trade_route(*args))
    assert conn.lua.eval('#submissions')==0
