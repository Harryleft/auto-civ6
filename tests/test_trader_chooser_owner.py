"""Original route confirmation keeps another native trader's current chooser."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from test_trader_confirmation import TraderConn


class ChooserOwnerConn(TraderConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
turn=131;seed=-1896272000;capacity=4
original=units[196608];units[196608]=nil;original.id=1966090;units[original.id]=original
original.kind=2;original.x=37;original.y=28
GameInfo.Units[2]={MakeTradeRoute=true}
ActivityTypes.ACTIVITY_AWAKE=1797587246
origin.id=196610;origin.x=37;origin.y=28
domestic.id=65536;domestic.x=40;domestic.y=25
origin.routes={{TraderUnitID=1111,OriginCityPlayer=0,OriginCityID=196610,DestinationCityPlayer=0,DestinationCityID=65536},
 {TraderUnitID=2222,OriginCityPlayer=0,OriginCityID=196610,DestinationCityPlayer=0,DestinationCityID=65536}}
other=addUnit(2883596,true,ActivityTypes.ACTIVITY_AWAKE)
other.owner=0;other.kind=2;other.x=40;other.y=25;other.moves=2
for _,name in ipairs({'GetOwner','GetType','GetX','GetY'}) do other[name]=original[name] end
UnitManager.GetUnit=function(owner,id)
 for _,unit in pairs(units) do if unit.owner==owner and unit.id%65536==id then return unit end end
end
Players[0].GetUnits=function() return {FindID=function(_,id) return units[id] end} end
UI.GetHeadSelectedUnit=function() return selected end
UnitManager.CanStartOperation=function(actor,operation,unused,params)
 assert(actor==original and operation==21 and unused==nil)
 assert(params.ox==37 and params.oy==28 and params.dx==40 and params.dy==25)
 return allowed
end
local nativeApply=applyTrader
function applyTrader(op,params)
 nativeApply(op,params)
 selected=other;mode=InterfaceModeTypes.MAKE_TRADE_ROUTE
end
''')


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def test_t131_completed_original_route_preserves_other_idle_trader_chooser():
    conn=ChooserOwnerConn();state=GameState(conn)
    result=asyncio.run(server.unit_action(make_ctx(game=state),1966090,'trade_route',40,25))
    assert result.startswith('TRADE_ROUTE_STARTED|unit:1966090|0:196610->0:65536|routes:3/4|')
    assert conn.lua.eval('mode')==conn.lua.eval('InterfaceModeTypes.MAKE_TRADE_ROUTE')
    assert conn.lua.eval('selected.id')==2883596
    assert conn.lua.eval('#modeChanges')==0
    assert conn.lua.eval('#submissions')==1
    assert state.pending_action_summary is None


def test_delayed_original_route_confirms_only_while_other_trader_chooser_stays_open():
    conn=ChooserOwnerConn();state=GameState(conn);ctx=make_ctx(game=state)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,1966090,'trade_route',40,25))
    conn.lua.execute('applyTrader(submissions[1].op,submissions[1].params)')
    assert asyncio.run(server.unit_action(ctx,1966090,'trade_route',40,25)).startswith('TRADE_ROUTE_STARTED|')
    assert conn.lua.eval('#modeChanges')==0
    assert conn.lua.eval('mode')==conn.lua.eval('InterfaceModeTypes.MAKE_TRADE_ROUTE')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('change,restore',[
    ('original.kind=5','original.kind=2'),
    ('origin.id=262146','origin.id=196610'),
])
def test_observed_original_identity_change_cannot_be_cured_by_current_other_chooser(change,restore):
    conn=ChooserOwnerConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    conn.lua.execute(restore+';applyTrader(submissions[1].op,submissions[1].params)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('change,restore',[
    ("original.GetID=function() error('unknown actor') end;origin.id=262146",
     'original.GetID=function(self) return self.id end;origin.id=196610'),
    ("origin.GetID=function() error('unknown origin') end;domestic.id=131072",
     'origin.GetID=function(self) return self.id end;domestic.id=65536'),
    ("original.kind=5;origin.GetID=function() return nil end",
     'original.kind=2;origin.GetID=function(self) return self.id end'),
    ("origin.GetID=function() return nil end;origin.owner=1",
     'origin.GetID=function(self) return self.id end;origin.owner=0'),
])
def test_unknown_original_reader_does_not_mask_known_change_in_another_original_entity(change,restore):
    conn=ChooserOwnerConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    conn.lua.execute(restore+';applyTrader(submissions[1].op,submissions[1].params)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    assert conn.lua.eval('#submissions')==1


def test_unknown_other_context_getter_cannot_mask_scope_change_before_restore():
    conn=ChooserOwnerConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    conn.lua.execute('''
applyTrader(submissions[1].op,submissions[1].params)
UI.GetHeadSelectedUnit=function() seed=1;error('selection unavailable') end
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    conn.lua.execute('seed=-1896272000;UI.GetHeadSelectedUnit=function() return other end')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('change',[
    'selected=nil', 'selected=original', 'other.owner=1', 'other.kind=99',
    'GameInfo.Units[2].MakeTradeRoute=false', 'other.moves=0', 'other.moves=nil',
    'other.moves=math.huge', 'other.moves=0/0', 'other.activity=ActivityTypes.ACTIVITY_SLEEP',
    'other.activity=nil', 'ActivityTypes.ACTIVITY_AWAKE=nil', 'other.x=39', 'domestic.owner=1',
    'other.GetType=function() return nil end',
    'Players[0].GetUnits=function() return {FindID=function() return original end} end',
    'UnitManager.GetUnit=function() return original end',
    'origin.routes[1].TraderUnitID=other.id',
    'UI.GetHeadSelectedUnit=nil', 'UI.GetHeadSelectedUnit=function() return nil end',
    "UI.GetHeadSelectedUnit=function() error('unavailable head selection') end",
])
def test_incomplete_or_ineligible_other_selection_cannot_waive_original_chooser(change):
    conn=ChooserOwnerConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    conn.lua.execute('applyTrader(submissions[1].op,submissions[1].params);'+change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    assert state.pending_action_kind=='trade_route'
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('#modeChanges')==0


@pytest.mark.parametrize('change',[
    'origin.routes[3]=nil', 'origin.routes[3].DestinationCityID=196610',
    'origin.routes[3].TraderUnitID=3333', 'capacity=2',
])
def test_other_valid_chooser_never_substitutes_for_original_economic_facts(change):
    conn=ChooserOwnerConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    conn.lua.execute('applyTrader(submissions[1].op,submissions[1].params);'+change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('#modeChanges')==0


def test_another_current_selection_can_change_and_be_validated_afresh_without_new_route_intent():
    conn=ChooserOwnerConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    conn.lua.execute('applyTrader(submissions[1].op,submissions[1].params);other.moves=0')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    conn.lua.execute('''
third={};for key,value in pairs(other) do third[key]=value end
third.id=2883597;third.moves=2;third.x=37;third.y=28;units[third.id]=third;selected=third
''')
    assert asyncio.run(state.make_trade_route(1966090,40,25)).startswith('TRADE_ROUTE_STARTED|')
    assert conn.lua.eval('selected.id')==2883597
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('#modeChanges')==0


@pytest.mark.parametrize('change,restore,sticky',[
    ('UI.GetHeadSelectedUnit=function() seed=1;return other end',
     'UI.GetHeadSelectedUnit=function() return other end;seed=-1896272000',True),
    ('UI.GetHeadSelectedUnit=function() seed=nil;return other end',
     'UI.GetHeadSelectedUnit=function() return other end;seed=-1896272000',False),
    ('domestic.GetOwner=function() seed=1;return 0 end',
     'domestic.GetOwner=function(self) return self.owner end;seed=-1896272000',True),
])
def test_scope_after_final_other_selected_and_city_getters_is_required(change,restore,sticky):
    conn=ChooserOwnerConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    conn.lua.execute('applyTrader(submissions[1].op,submissions[1].params);'+change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    conn.lua.execute(restore)
    result=asyncio.run(state.make_trade_route(1966090,40,25))
    assert ('OUTCOME_UNKNOWN' in result) if sticky else result.startswith('TRADE_ROUTE_STARTED|')
    assert conn.lua.eval('#modeChanges')==0
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('scope_value',['1','nil'])
def test_entry_cleanup_does_not_close_other_chooser_after_scope_changes_in_selection_read(scope_value):
    conn=ChooserOwnerConn();state=GameState(conn)
    conn.lua.execute('UI.GetHeadSelectedUnit=function() seed='+scope_value+';return other end')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    assert conn.lua.eval('mode')==conn.lua.eval('InterfaceModeTypes.MAKE_TRADE_ROUTE')
    assert conn.lua.eval('#modeChanges')==0
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('interruption',['lost','cancelled'])
def test_lost_or_cancelled_original_operation_keeps_other_chooser_and_never_resubmits(interruption):
    class CancelConn(ChooserOwnerConn):
        async def execute_write(self,code):
            before=self.lua.eval('#submissions')
            rows=await super().execute_write(code)
            if self.lua.eval('#submissions')>before:
                raise asyncio.CancelledError()
            return rows

    conn=CancelConn() if interruption=='cancelled' else ChooserOwnerConn()
    state=GameState(conn);ctx=make_ctx(game=state)
    if interruption=='cancelled':
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(server.unit_action(ctx,1966090,'trade_route',40,25))
    else:
        conn.lose_reply=True
        assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,1966090,'trade_route',40,25))
    assert asyncio.run(server.unit_action(ctx,1966090,'trade_route',40,25)).startswith('TRADE_ROUTE_STARTED|')
    assert conn.lua.eval('mode')==conn.lua.eval('InterfaceModeTypes.MAKE_TRADE_ROUTE')
    assert conn.lua.eval('#modeChanges')==0
    assert conn.lua.eval('#submissions')==1


def test_unknown_original_chooser_owner_keeps_save_turn_and_other_mutation_gates():
    conn=ChooserOwnerConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    conn.lua.execute('applyTrader(submissions[1].op,submissions[1].params);selected=nil')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(2883596,37,28))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.skip_unit(2883596))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.save_game('pending_route'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    conn.lua.execute('selected=other')
    assert asyncio.run(state.make_trade_route(1966090,40,25)).startswith('TRADE_ROUTE_STARTED|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('change',[
    'other.GetType=function() return nil end',
    "other.GetType=function() error('unreadable type') end",
    'other.owner=1', 'other.kind=99', 'selected=nil',
    'other.moves=0', 'other.activity=ActivityTypes.ACTIVITY_SLEEP',
    'origin.routes[1].TraderUnitID=other.id',
    'Players[0].GetUnits=function() return {FindID=function() return original end} end',
])
def test_first_entry_cleanup_keeps_unknown_or_inconsistent_other_chooser_pending(change):
    conn=ChooserOwnerConn();state=GameState(conn)
    conn.lua.execute('''
local apply=applyTrader
function applyTrader(op,params)
 apply(op,params)
 '''+change+'''
end
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    assert conn.lua.eval('mode')==conn.lua.eval('InterfaceModeTypes.MAKE_TRADE_ROUTE')
    assert conn.lua.eval('#modeChanges')==0
    assert conn.lua.eval('#submissions')==1
    assert state.pending_action_kind=='trade_route'


def test_entry_cleanup_preserves_mode_changed_by_selected_unit_getter():
    conn=ChooserOwnerConn();state=GameState(conn)
    conn.lua.execute('UI.GetHeadSelectedUnit=function() mode=InterfaceModeTypes.DIPLOMACY;return other end')
    assert asyncio.run(state.make_trade_route(1966090,40,25)).startswith('TRADE_ROUTE_STARTED|')
    assert conn.lua.eval('mode')==conn.lua.eval('InterfaceModeTypes.DIPLOMACY')
    assert conn.lua.eval('#modeChanges')==0
    assert conn.lua.eval('#submissions')==1


def test_entry_cleanup_does_not_overwrite_unknown_mode_after_selection_read():
    conn=ChooserOwnerConn();state=GameState(conn)
    conn.lua.execute('UI.GetHeadSelectedUnit=function() mode=nil;return other end')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.make_trade_route(1966090,40,25))
    assert conn.lua.eval('mode') is None
    assert conn.lua.eval('#modeChanges')==0
    assert conn.lua.eval('#submissions')==1
