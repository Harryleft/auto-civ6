"""Explicit native cancellation preserves the position and one owned intent."""
import asyncio
import pytest
from test_unit_readiness import ReadyConn
from civ_mcp.game_state import GameState


class CancelConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
commands={};cancel_deferred=false;cancel_allowed=true;cancel_fail=false;route_active=false
GameInfo.Units={[1]={Spy=false,IgnoreMoves=false}}
GameInfo.UnitCommands={UNITCOMMAND_CANCEL={Hash=20}}
UnitCommandTypes={CANCEL=20}
local u=units[196608];u.x=19;u.y=17;u.queued=2;u.ready=false;u.activity=3
u.GetX=function(self) return self.x end;u.GetY=function(self) return self.y end;u.GetType=function() return 1 end
local function plot(x,y) return {GetX=function() return x end,GetY=function() return y end} end
Map={GetPlotByIndex=function(id) if id==1 then return plot(19,17) elseif id==2 then return plot(20,17) end end}
UnitManager.GetQueuedDestination=function(unit) return unit.queued end
UnitManager.GetMoveToPathEx=function() return {plots={1,2},turns={1,1}} end
UnitManager.CanStartCommand=function(unit,hash,visible,results)
 if visible~=false or results~=true then error('not a strict native check') end
 return cancel_allowed
end
UnitManager.RequestCommand=function(unit,hash)
 table.insert(commands,{id=unit:GetID(),hash=hash})
 if cancel_fail then error('failure after native entry') end
 if not cancel_deferred then unit.queued=nil;unit.ready=true;unit.activity=0 end
end
Players[0].GetCities=function() return {Members=function() return ipairs({{GetTrade=function() return {GetOutgoingRoutes=function() return route_active and {{TraderUnitID=196608}} or {} end} end}}) end} end
''')


def test_explicit_cancel_uses_native_command_and_leaves_position_for_separate_decision():
    conn=CancelConn();state=GameState(conn)
    result=asyncio.run(state.cancel_unit_move(196608))
    assert result.startswith('MOVE_CANCELLED|unit:196608')
    assert conn.lua.eval('#commands')==1 and conn.lua.eval('commands[1].hash')==20
    assert conn.lua.eval('units[196608].queued') is None
    assert conn.lua.eval('units[196608].x')==19 and conn.lua.eval('units[196608].y')==17
    assert conn.lua.eval('#submissions')==0  # No hidden fortify/skip/heal.
    assert state.pending_action_summary is None
    assert asyncio.run(state.fortify_unit(196608)).startswith('FORTIFIED')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('setup,reason', [
    ('GameInfo.Units[1].Spy=true','NOT_MOVE_QUEUE'),
    ('GameInfo.Units[1].IgnoreMoves=true','NOT_MOVE_QUEUE'),
    ('GameInfo.Units[1].Spy=nil','READINESS_UNKNOWN'),
    ('route_active=true','ACTIVE_TRADE_ROUTE'),
    ('units[196608].queued=nil','NO_QUEUED_MOVE'),
    ('cancel_allowed=false','CANNOT_CANCEL_MOVE'),
    ('cancel_allowed=nil','ELIGIBILITY_UNKNOWN'),
    ('UnitManager.CanStartCommand=nil','COMMAND_UNAVAILABLE'),
    ('GameInfo.UnitCommands.UNITCOMMAND_CANCEL.Hash=21','COMMAND_UNAVAILABLE'),
    ('UnitManager.GetMoveToPathEx=function() return nil end','READINESS_UNKNOWN'),
    ('Players[0].GetCities=nil','READINESS_UNKNOWN'),
    ('busy=true','READINESS_UNKNOWN'),
])
def test_cancel_refuses_other_activities_routes_and_unknown_without_any_unit_command(setup,reason):
    conn=CancelConn();conn.lua.execute(setup);state=GameState(conn)
    result=asyncio.run(state.cancel_unit_move(196608))
    assert reason in result
    assert conn.lua.eval('#commands')==0 and conn.lua.eval('#submissions')==0
    assert conn.lua.eval('units[196608].activity')==3
    assert conn.lua.eval('units[196608].x')==19
    assert state.pending_action_summary is None


def test_explicit_cancel_can_remove_verified_future_queue_without_spending_moves():
    conn=CancelConn();state=GameState(conn)
    conn.lua.execute('units[196608].moves=0;UnitManager.GetMoveToPathEx=function() return {plots={1,2},turns={1,2}} end')
    assert asyncio.run(state.cancel_unit_move(196608)).startswith('MOVE_CANCELLED')
    assert conn.lua.eval('units[196608].moves')==0 and conn.lua.eval('units[196608].x')==19
    assert conn.lua.eval('#commands')==1


@pytest.mark.parametrize('error',[TimeoutError,asyncio.CancelledError])
def test_lost_cancel_reply_repeats_only_confirmation_and_never_command(error):
    class LostConn(CancelConn):
        lost=False
        async def execute_write(self,code):
            rows=await super().execute_write(code)
            if 'UnitManager.RequestCommand(unit,command.Hash)' in code and not self.lost:
                self.lost=True;raise error()
            return rows
    async def scenario():
        conn=LostConn();state=GameState(conn)
        if error is asyncio.CancelledError:
            with pytest.raises(asyncio.CancelledError): await state.cancel_unit_move(196608)
        else: assert 'OUTCOME_UNKNOWN' in await state.cancel_unit_move(196608)
        assert state.pending_action_summary
        assert (await state.cancel_unit_move(196608)).startswith('MOVE_CANCELLED')
        assert conn.lua.eval('#commands')==1 and conn.lua.eval('#submissions')==0
        assert state.pending_action_summary is None
    asyncio.run(scenario())


@pytest.mark.parametrize('tamper',[
    'seed=99', 'turn=42', 'units[196608].x=20',
    'units[196608].id=262144', '__civmcpCancelMoveRequest.token="another"',
    'UnitManager.GetQueuedDestination=function() return false end',
])
def test_unconfirmed_cancel_keeps_scope_position_and_unit_gates(tamper,monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module,'ACTION_CONFIRM_TIMEOUT',.005)
    conn=CancelConn();conn.lua.execute('cancel_deferred=true');state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.cancel_unit_move(196608))
    conn.lua.execute('units[196608].queued=nil;'+tamper)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.cancel_unit_move(196608))
    assert state.pending_action_summary
    assert conn.lua.eval('#commands')==1 and conn.lua.eval('#submissions')==0
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.fortify_unit(196608))


def test_cancel_refuses_stale_composite_id_and_existing_end_turn_gate():
    conn=CancelConn();state=GameState(conn)
    conn.lua.execute('UnitManager.GetUnit=function() return units[196608] end')
    assert 'UNIT_ID_CHANGED' in asyncio.run(state.cancel_unit_move(262144))
    assert conn.lua.eval('#commands')==0
    state._pending_end_turn=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.cancel_unit_move(196608))
    assert conn.lua.eval('#commands')==0 and conn.lua.eval('units[196608].queued')==2
