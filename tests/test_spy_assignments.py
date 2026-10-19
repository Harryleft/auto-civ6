"""Public spy assignment lifecycle with native API adapters and real Lua."""
import asyncio
import pytest
from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx, app_of
from test_unit_readiness import ReadyConn


class AssignmentConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply=False
        self.lua.execute('''
function event()
 local listeners={}
 return {Add=function(f) listeners[f]=true end,Remove=function(f) listeners[f]=nil end,
 Fire=function(...) for f in pairs(listeners) do f(...) end end}
end
Events={UnitOperationStarted=event()}
u=units[196608];u.x=19;u.y=17;u.op=-1;u.finish=-1
u.GetX=function(self) return self.x end;u.GetY=function(self) return self.y end
u.GetType=function() return 1 end
u.GetSpyOperation=function(self) return self.op end
u.GetSpyOperationEndTurn=function(self) return self.finish end
GameInfo.Units={[1]={UnitType='UNIT_SPY',Spy=true}}
local travel={Index=12,Hash=-1295211657,OperationType='UNITOPERATION_SPY_TRAVEL_NEW_CITY'}
local gain={Index=13,Hash=-311249027,OperationType='UNITOPERATION_SPY_GAIN_SOURCES'}
local funds={Index=14,Hash=574548564,OperationType='UNITOPERATION_SPY_SIPHON_FUNDS'}
local counter={Index=15,Hash=-2005926703,OperationType='UNITOPERATION_SPY_COUNTERSPY'}
GameInfo.UnitOperations=setmetatable({[12]=travel,[13]=gain,[14]=funds,[15]=counter,
 UNITOPERATION_SPY_TRAVEL_NEW_CITY=travel,UNITOPERATION_SPY_GAIN_SOURCES=gain,
 UNITOPERATION_SPY_SIPHON_FUNDS=funds,UNITOPERATION_SPY_COUNTERSPY=counter},
 {__call=function() return function() return nil end end})
UnitOperationTypes={PARAM_X='x',PARAM_Y='y',PARAM_X0='x',PARAM_Y0='y'}
UnitOperationResults={PLOTS='plots'}
city={id=65537,owner=1,x=10,y=10}
city.GetID=function(self) return self.id end;city.GetOwner=function(self) return self.owner end
city.GetX=function(self) return self.x end;city.GetY=function(self) return self.y end
plot={x=10,y=10,index=1010};district={x=11,y=10,index=1110}
for _,p in ipairs({plot,district}) do
 p.GetX=function(self) return self.x end;p.GetY=function(self) return self.y end
 p.GetIndex=function(self) return self.index end
end
Map={GetPlot=function(x,y) if x==10 and y==10 then return plot elseif x==11 and y==10 then return district end end,
 GetPlotByIndex=function(i) if i==1010 then return plot elseif i==1110 then return district end end}
Cities={GetPlotPurchaseCity=function(p) if p==plot or p==district then return city end end}
CityManager={GetCityAt=function(x,y) if x==10 and y==10 then return city end end}
UnitManager.CanStartOperation=function(unit,op,target,params,check)
 if op==-1295211657 then
  assert((target==plot or target==nil) and params.x==10 and params.y==10)
  return unit.allowed
 end
 assert(target==plot and params==false and check==true)
 return unit.allowed,{plots=op==-311249027 and {} or {1110}}
end
function applyAssignment(op,params)
 Events.UnitOperationStarted.Fire(0,u.id,op)
 if op==-1295211657 then u.op=12;u.x=-9999;u.y=-9999
 elseif op==-311249027 then u.op=13;u.x=params.x;u.y=params.y
 elseif op==574548564 then u.op=14;u.x=params.x;u.y=params.y
 else u.op=15;u.x=params.x;u.y=params.y end
 u.finish=49
end
UnitManager.RequestOperation=function(unit,op,params)
 table.insert(submissions,{id=unit.id,op=op,x=params.x,y=params.y})
 if not deferred then applyAssignment(op,params) end
 if fail_request then error('native threw after assignment') end
end
unsafeReads=0
Players[0].GetDiplomacy=function() return {GetNextEscapingSpyID=function() unsafeReads=unsafeReads+1;error('unsafe getter') end} end
''')

    async def execute_write(self,code):
        before=self.lua.eval('#submissions');result=await super().execute_write(code)
        if self.lose_reply and self.lua.eval('#submissions')>before:
            self.lose_reply=False;raise OSError('assignment started; reply lost')
        return result


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT',.01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL',.001)


def test_lost_travel_reply_only_confirms_bound_assignment_start():
    conn=AssignmentConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state;conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.spy_action(ctx,196608,'travel',10,10))
    result=asyncio.run(server.spy_action(ctx,196608,'travel',10,10))
    assert result.startswith('SPY_ASSIGNMENT_STARTED|spy:196608|TRAVEL|')
    assert 'end_turn:49' in result and 'ARRIVED' not in result
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('u.x')==-9999
    assert conn.lua.eval('unsafeReads')==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('mission,x,op', [('GAIN_SOURCES',10,13),('SIPHON_FUNDS',11,14),('COUNTERSPY',11,15)])
def test_mission_confirms_exact_native_target_assignment_not_mission_outcome(mission,x,op):
    conn=AssignmentConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('u.x=10;u.y=10');conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.spy_action(ctx,196608,mission,x,10))
    result=asyncio.run(server.spy_action(ctx,196608,mission,x,10))
    assert result.startswith(f'SPY_ASSIGNMENT_STARTED|spy:196608|{mission}|')
    assert f'target:{x},10' in result and 'end_turn:49' in result
    assert conn.lua.eval('u.op')==op
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('unsafeReads')==0


def test_travel_already_at_destination_is_noop_without_an_assignment():
    conn=AssignmentConn();state=GameState(conn);conn.lua.execute('u.x=10;u.y=10;u.allowed=false')
    result=asyncio.run(state.spy_travel(196608,10,10))
    assert result.startswith('SPY_ALREADY_AT_DESTINATION|')
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('action', ['travel','GAIN_SOURCES'])
def test_mcp_preserves_full_spy_identity_on_reused_low_id(action):
    conn=AssignmentConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('u.id=262144')
    assert 'SPY_ID_CHANGED' in asyncio.run(server.spy_action(ctx,196608,action,10,10))
    assert conn.lua.eval('#submissions')==0


@pytest.mark.parametrize('setup', [
    'u.allowed=false','u.allowed=nil','UnitManager.CanStartOperation=nil',
    'UnitManager.CanStartOperation=function() error("unavailable") end',
    'UnitManager.RequestOperation=nil','Events.UnitOperationStarted=nil',
    'GameInfo.UnitOperations.UNITOPERATION_SPY_TRAVEL_NEW_CITY=nil',
    'u.GetSpyOperation=nil','u.GetSpyOperationEndTurn=nil','u.finish=nil',
    'GameInfo.Units[1].UnitType="UNIT_WARRIOR"','city.id=nil',
    'Cities.GetPlotPurchaseCity=function() return nil end',
    'u.op=12;u.finish=49;u.x=-9999;u.y=-9999','busy=true','busy=nil',
])
def test_unknown_native_spy_or_target_capability_refuses_before_entry(setup):
    conn=AssignmentConn();state=GameState(conn);conn.lua.execute(setup)
    assert 'SPY_ASSIGNMENT_REFUSED' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None
    assert conn.lua.eval('unsafeReads')==0


@pytest.mark.parametrize('mission,x,setup',[
    ('SIPHON_FUNDS',10,'u.x=10;u.y=10'),
    ('GAIN_SOURCES',11,'u.x=10;u.y=10'),
    ('GAIN_SOURCES',10,''),
    ('GAIN_SOURCES',10,'u.x=10;u.y=10;u.op=13;u.finish=49'),
    ('COUNTERSPY',11,'u.x=10;u.y=10;UnitManager.CanStartOperation=function() return true,{plots="unknown"} end'),
])
def test_mission_preserves_explicit_target_and_does_not_replace_existing_assignment(mission,x,setup):
    conn=AssignmentConn();state=GameState(conn);conn.lua.execute(setup)
    assert 'SPY_ASSIGNMENT_REFUSED' in asyncio.run(state.spy_mission(196608,mission,x,10))
    assert conn.lua.eval('#submissions')==0


@pytest.mark.parametrize('effect',[
    '', 'u.x=-9999;u.y=-9999',
    'u.op=12;u.finish=49',
    'Events.UnitOperationStarted.Fire(0,u.id,-1295211657)',
    'u.op=12;u.finish=49;Events.UnitOperationStarted.Fire(0,262144,-1295211657)',
    'u.op=12;u.finish=49;Events.UnitOperationStarted.Fire(1,u.id,-1295211657)',
    'u.op=12;u.finish=49;Events.UnitOperationStarted.Fire(0,u.id,-311249027)',
    'applyAssignment(-1295211657,{x=10,y=10});u.finish=41',
    'applyAssignment(-1295211657,{x=10,y=10});u.op=13',
    'applyAssignment(-1295211657,{x=10,y=10});u.id=262144',
    'applyAssignment(-1295211657,{x=10,y=10});city.id=131073',
    'applyAssignment(-1295211657,{x=10,y=10});city.owner=2',
    'applyAssignment(-1295211657,{x=10,y=10});plot.index=2222',
    'applyAssignment(-1295211657,{x=10,y=10});busy=true',
])
def test_old_or_partial_travel_facts_cannot_confirm_original_assignment(effect):
    conn=AssignmentConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute(effect)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('mission,x,hash_value', [('TRAVEL',10,-1295211657),('SIPHON_FUNDS',11,574548564)])
def test_delayed_assignment_only_confirms_when_exact_operation_and_end_turn_are_visible(mission,x,hash_value):
    conn=AssignmentConn();state=GameState(conn);conn.lua.execute('deferred=true;u.x=10;u.y=10' if mission!='TRAVEL' else 'deferred=true')
    call=lambda: state.spy_travel(196608,x,10) if mission=='TRAVEL' else state.spy_mission(196608,mission,x,10)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(call())
    conn.lua.execute(f'applyAssignment({hash_value},{{x={x},y=10}})')
    assert asyncio.run(call()).startswith('SPY_ASSIGNMENT_STARTED|')
    assert conn.lua.eval('#submissions')==1


def test_started_mission_at_another_tile_cannot_confirm_requested_district():
    conn=AssignmentConn();state=GameState(conn);conn.lua.execute('deferred=true;u.x=10;u.y=10')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_mission(196608,'SIPHON_FUNDS',11,10))
    conn.lua.execute('applyAssignment(574548564,{x=10,y=10})')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_mission(196608,'SIPHON_FUNDS',11,10))
    assert conn.lua.eval('#submissions')==1


def test_pending_assignment_blocks_other_spy_escape_and_turn_until_start_is_confirmed():
    conn=AssignmentConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    for call in [state.spy_travel(196609,10,10),state.spy_mission(196608,'GAIN_SOURCES',10,10),
                 server.spy_action(ctx,196608,'escape:DISTRICT_HARBOR',0,0),state.end_turn(),
                 state.set_research('TECH_POTTERY')]:
        assert 'OUTCOME_UNKNOWN' in asyncio.run(call)
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('unsafeReads')==0


def test_native_throw_after_assignment_never_replays():
    conn=AssignmentConn();state=GameState(conn);conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('#submissions')==1


def test_cancelled_assignment_is_retained_for_confirmation_only():
    class CancelConn(AssignmentConn):
        async def execute_write(self,code):
            before=self.lua.eval('#submissions');result=await super().execute_write(code)
            if self.lua.eval('#submissions')>before:raise asyncio.CancelledError()
            return result
    conn=CancelConn();state=GameState(conn)
    with pytest.raises(asyncio.CancelledError):asyncio.run(state.spy_travel(196608,10,10))
    assert state.pending_action_summary is not None
    assert asyncio.run(state.spy_travel(196608,10,10)).startswith('SPY_ASSIGNMENT_STARTED|')
    assert conn.lua.eval('#submissions')==1


def test_world_change_before_spy_submission_proves_zero_request():
    class ChangedConn(AssignmentConn):
        async def execute_write(self,code):
            if '-- ACTION_SUBMIT_SCOPE|' in code:self.lua.execute('turn=42')
            return await super().execute_write(code)
    conn=ChangedConn();state=GameState(conn)
    assert 'ACTION_SCOPE_NO_SUBMIT' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


def test_world_change_latches_original_spy_assignment_even_if_it_returns():
    conn=AssignmentConn();state=GameState(conn);conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute('seed=9')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute('seed=719665280')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('#submissions')==1


def test_spy_readback_failure_keeps_original_assignment():
    class ReadErrorConn(AssignmentConn):
        async def execute_write(self,code):
            if self.lua.eval('#submissions'):raise OSError('read unavailable')
            return await super().execute_write(code)
    conn=ReadErrorConn();state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('#submissions')==1


from test_end_turn import GameStateConn, clock


def test_confirmed_travel_start_releases_gate_for_next_turn_before_arrival(clock):
    class AssignmentAndTurnConn(AssignmentConn):
        def __init__(self):
            super().__init__()
            self.turn_adapter=None
        async def execute_read(self,code):
            return await self.turn_adapter.execute_read(code)
        async def execute_write(self,code):
            if self.turn_adapter is not None:
                return await self.turn_adapter.execute_write(code)
            return await super().execute_write(code)
    conn=AssignmentAndTurnConn();state=GameState(conn)
    conn.lua.execute('turn=50;seed=1;local apply=applyAssignment;applyAssignment=function(op,params) apply(op,params);u.finish=58 end')
    assert 'end_turn:58' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('u.x')==-9999
    # Keep the same world/turn, supplying the established native turn protocol
    # adapter for unrelated overview, diplomacy and turn APIs.
    conn.turn_adapter=GameStateConn()
    report=asyncio.run(state.end_turn())
    assert 'Turn 50 -> 51' in report
    assert len(conn.turn_adapter.requests)==1
    assert conn.lua.eval('#submissions')==1
    assert state.pending_action_summary is None and not state.turn_pending


def test_mission_refusal_names_native_district_candidates_without_choosing_one():
    conn=AssignmentConn();state=GameState(conn);conn.lua.execute('u.x=10;u.y=10')
    result=asyncio.run(state.spy_mission(196608,'SIPHON_FUNDS',10,10))
    assert 'MISSION_REQUIRES_NATIVE_TARGET_PLOT' in result and '11,10' in result
    assert conn.lua.eval('#submissions')==0


def test_public_spy_query_reports_active_native_operation_when_table_iterator_is_empty():
    conn=AssignmentConn();state=GameState(conn)
    conn.lua.execute('''
u.x=10;u.y=10;u.op=13;u.finish=49
u.GetName=function() return 'Spy' end;u.GetExperience=function() return nil end
city.GetName=function() return 'Target City' end
Locale={Lookup=function(v) return v end}
EndTurnBlockingTypes={ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE=7}
NotificationManager={GetList=function() return {} end}
''')
    spies=asyncio.run(state.get_spies())
    assert spies[0].unit_id==196608
    assert spies[0].current_mission=='GAIN_SOURCES'
    assert spies[0].status=='on_mission'
    assert conn.lua.eval('unsafeReads')==0
    assert conn.lua.eval('#submissions')==0
