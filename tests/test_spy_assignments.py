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
Events={UnitOperationStarted=event(),UnitRemovedFromMap=event()}
u=units[196608];u.x=19;u.y=17;u.op=-1;u.finish=-1
u.name='LOC_CITIZEN_SUMERIA_MODERN_FEMALE_8';u.level=1
u.GetName=function(self) return self.name end
u.GetExperience=function(self) return {GetLevel=function() return self.level end} end
u.GetX=function(self) return self.x end;u.GetY=function(self) return self.y end
u.GetType=function() return 1 end
u.GetSpyOperation=function(self) return self.op end
u.GetSpyOperationEndTurn=function(self) return self.finish end
GameInfo.Units={[1]={UnitType='UNIT_SPY',Spy=true}}
local travel={Index=12,Hash=-1295211657,OperationType='UNITOPERATION_SPY_TRAVEL_NEW_CITY'}
local gain={Index=13,Hash=-311249027,OperationType='UNITOPERATION_SPY_GAIN_SOURCES'}
local funds={Index=14,Hash=574548564,OperationType='UNITOPERATION_SPY_SIPHON_FUNDS'}
local counter={Index=15,Hash=-2005926703,OperationType='UNITOPERATION_SPY_COUNTERSPY'}
local rocketry={Index=40,Hash=-1021949976,OperationType='UNITOPERATION_SPY_DISRUPT_ROCKETRY'}
GameInfo.UnitOperations=setmetatable({[12]=travel,[13]=gain,[14]=funds,[15]=counter,[40]=rocketry,
 UNITOPERATION_SPY_TRAVEL_NEW_CITY=travel,UNITOPERATION_SPY_GAIN_SOURCES=gain,
 UNITOPERATION_SPY_SIPHON_FUNDS=funds,UNITOPERATION_SPY_COUNTERSPY=counter,
 UNITOPERATION_SPY_DISRUPT_ROCKETRY=rocketry},
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
 elseif op==-1021949976 then u.op=40;u.x=params.x;u.y=params.y
 else u.op=15;u.x=params.x;u.y=params.y end
 u.finish=49
end
UnitManager.RequestOperation=function(unit,op,params)
 table.insert(submissions,{id=unit.id,op=op,x=params.x,y=params.y})
 if not deferred then applyAssignment(op,params) end
 if fail_request then error('native threw after assignment') end
end
unsafeReads=0
offmap={}
Players[0].GetUnits=function() return {Members=function() return pairs(units) end,
 FindID=function(_,id) return units[id] end} end
Players[0].GetDiplomacy=function() return {
 GetNumSpiesOffMap=function() return #offmap end,
 GetNthOffMapSpy=function(_,owner,index) assert(owner==0);return offmap[index+1] end,
 GetNextEscapingSpyID=function() unsafeReads=unsafeReads+1;error('unsafe getter') end} end
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


class OffMapAssignmentConn(AssignmentConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
travelFinish=49
function applyAssignment(op,params)
 local function remove()
  units[u.id]=nil
  if emitRemoved~=false then Events.UnitRemovedFromMap.Fire(removeOwner or 0,removeId or u.id) end
 end
 if removedFirst then remove() end
 if emitStarted~=false then Events.UnitOperationStarted.Fire(startOwner or 0,startId or u.id,startOperation or op,0) end
 if not removedFirst then remove() end
 offmap={{NameIndex=37,Name=u.name,Level=u.level,XLocation=params.x,YLocation=params.y,ReturnTurn=travelFinish}}
end
''')


@pytest.mark.parametrize('removed_first', [False, True])
def test_native_offmap_travel_confirms_original_removed_spy_and_future_return_turn(removed_first):
    conn=OffMapAssignmentConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute(f'removedFirst={str(removed_first).lower()}')
    conn.lua.execute('''
turn=128;travelFinish=132
units[196608]=nil;u.id=2752514;units[u.id]=u;u.x=40;u.y=25
city.id=65536;city.owner=3;city.x=18;city.y=13;plot.x=18;plot.y=13
Map.GetPlot=function(x,y) if x==18 and y==13 then return plot end end
UnitManager.CanStartOperation=function(unit,op,target,params)
 assert(unit==u and op==-1295211657 and target==plot and params.x==18 and params.y==13);return true
end
''')
    result=asyncio.run(server.spy_action(ctx,2752514,'travel',18,13))
    assert result.startswith('SPY_ASSIGNMENT_STARTED|spy:2752514|TRAVEL|target:18,13|city:3,65536')
    assert 'end_turn:132' in result
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('UnitManager.GetUnit(0,2752514%65536)==nil and Players[0]:GetUnits():FindID(2752514)==nil')
    assert state.pending_action_summary is None


def test_public_query_includes_own_offmap_travel_without_fabricating_actionable_unit_id():
    from civ_mcp.narrate import narrate_spies
    conn=OffMapAssignmentConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('''
units={}
offmap={{NameIndex=37,Name=u.name,Level=1,XLocation=10,YLocation=10,ReturnTurn=49}}
Locale={Lookup=function(value) return value end}
city.GetName=function() return 'Target City' end
EndTurnBlockingTypes={ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE=7}
NotificationManager={GetList=function() return {} end}
''')
    spies=asyncio.run(state.get_spies())
    assert len(spies)==1
    spy=spies[0]
    assert spy.unit_id is None and spy.unit_index is None
    assert spy.status=='in_transit' and spy.return_turn==49
    assert (spy.x,spy.y)==(10,10) and spy.available_ops==[]
    text=narrate_spies(spies)
    assert 'in transit' in text and 'return_turn:49' in text
    assert 'id:0' not in text and 'spy_action' not in text
    assert conn.lua.eval('#submissions')==0 and conn.lua.eval('unsafeReads')==0


def test_observed_spy_generation_change_stays_invalid_after_original_id_returns():
    conn=OffMapAssignmentConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute('u.id=262144')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute('u.id=196608;applyAssignment(-1295211657,{x=10,y=10})')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('setup', [
    'u.name=nil', 'u.name=""', 'u.level=nil', 'u.level=0',
    'Events.UnitRemovedFromMap=nil',
    'Players[0].GetUnits=function() return {Members=function() return pairs(units) end} end',
    'Players[0].GetDiplomacy=function() return {} end',
    'Players[0].GetDiplomacy=function() return {GetNumSpiesOffMap=function() return nil end,GetNthOffMapSpy=function() end} end',
    'offmap={{NameIndex=37,Name=u.name,Level=u.level,XLocation=10,YLocation=10,ReturnTurn=49}}',
    'offmap={{Name="Other",Level=1,XLocation=10,YLocation=10,ReturnTurn=49}}',
    'offmap={{NameIndex=10,Name="Other",Level=1,XLocation=10,YLocation=10,ReturnTurn=49}};offmap[2]=offmap[1]',
    'other=addUnit(196609,true,0,0);other.GetType=u.GetType;other.GetName=u.GetName;other.GetExperience=u.GetExperience;other.name=u.name;other.level=u.level',
    'local add=Events.UnitOperationStarted.Add;Events.UnitOperationStarted.Add=function(handler) add(handler);u.name="Different" end',
])
def test_unbindable_native_travel_identity_or_roster_refuses_before_mutation(setup):
    conn=OffMapAssignmentConn();state=GameState(conn);conn.lua.execute(setup)
    assert 'SPY_ASSIGNMENT_REFUSED' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup', [
    'emitStarted=false', 'startOwner=1', 'startId=262144', 'startOperation=-311249027',
    'emitRemoved=false', 'removeOwner=1', 'removeId=262144',
    'travelFinish=41', 'travelFinish=-1',
    'local apply=applyAssignment;applyAssignment=function(op,p) apply(op,p);offmap[1].Name="Other" end',
    'local apply=applyAssignment;applyAssignment=function(op,p) apply(op,p);offmap[1].Level=2 end',
    'local apply=applyAssignment;applyAssignment=function(op,p) apply(op,p);offmap[1].XLocation=11 end',
    'local apply=applyAssignment;applyAssignment=function(op,p) apply(op,p);offmap[1].YLocation=11 end',
    'local apply=applyAssignment;applyAssignment=function(op,p) apply(op,p);offmap[1].ReturnTurn=nil end',
    'local apply=applyAssignment;applyAssignment=function(op,p) apply(op,p);offmap[1].NameIndex=nil end',
    'local apply=applyAssignment;applyAssignment=function(op,p) apply(op,p);offmap[2]=offmap[1] end',
    'local apply=applyAssignment;applyAssignment=function(op,p) apply(op,p);offmap={} end',
    'local apply=applyAssignment;applyAssignment=function(op,p) apply(op,p);units[u.id]=u end',
    'local apply=applyAssignment;applyAssignment=function(op,p) apply(op,p);Players[0].GetUnits=function() return {Members=function() return pairs(units) end,FindID=function() return u end} end end',
])
@pytest.mark.parametrize('removed_first', [False, True])
def test_absence_target_or_partial_offmap_facts_cannot_confirm_original_spy(setup, removed_first):
    conn=OffMapAssignmentConn();state=GameState(conn)
    conn.lua.execute(f'removedFirst={str(removed_first).lower()}')
    conn.lua.execute(setup)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('#submissions')==1
    assert state.pending_action_summary is not None


@pytest.mark.parametrize('seed_during_removal', ['9', 'nil'])
def test_removal_before_started_in_changed_or_unknown_scope_cannot_confirm(seed_during_removal):
    conn=OffMapAssignmentConn();state=GameState(conn)
    conn.lua.execute(f'''
removedFirst=true
local fire=Events.UnitRemovedFromMap.Fire
Events.UnitRemovedFromMap.Fire=function(owner,id)
 seed={seed_during_removal};fire(owner,id);seed=719665280
end
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('#submissions')==1
    assert state.pending_action_summary is not None


def test_existing_unrelated_offmap_roster_must_be_preserved_and_new_identity_unique():
    conn=OffMapAssignmentConn();state=GameState(conn)
    conn.lua.execute('''
offmap={{NameIndex=12,Name="Other",Level=2,XLocation=11,YLocation=10,ReturnTurn=50}}
local apply=applyAssignment
applyAssignment=function(op,p)
 local old=offmap[1];apply(op,p);offmap[2]=offmap[1];offmap[1]=old
end
''')
    assert asyncio.run(state.spy_travel(196608,10,10)).startswith('SPY_ASSIGNMENT_STARTED|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('change,restore', [
    ('city.id=131073', 'city.id=65537'), ('city.owner=2', 'city.owner=1'),
    ('u.name="Other"', 'u.name="LOC_CITIZEN_SUMERIA_MODERN_FEMALE_8"'),
    ('u.level=2', 'u.level=1'),
    ('GameInfo.Units[2]={UnitType="UNIT_WARRIOR"};u.GetType=function() return 2 end', 'u.GetType=function() return 1 end'),
])
def test_observed_spy_or_city_identity_change_is_sticky(change, restore):
    conn=OffMapAssignmentConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute(restore+';applyAssignment(-1295211657,{x=10,y=10})')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('#submissions')==1


def test_unknown_city_field_does_not_poison_later_complete_offmap_confirmation():
    conn=OffMapAssignmentConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute('city.id=nil')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute('city.id=65537;applyAssignment(-1295211657,{x=10,y=10})')
    assert asyncio.run(state.spy_travel(196608,10,10)).startswith('SPY_ASSIGNMENT_STARTED|')


def test_lost_offmap_submit_reply_confirms_once_and_pending_gates_remain_closed():
    conn=OffMapAssignmentConn();state=GameState(conn);conn.lose_reply=True
    conn.lua.execute('emitRemoved=false')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    for call in [lambda: state.spy_travel(196609,10,10),state.end_turn,lambda: state.save_game('offmap_unknown')]:
        assert 'OUTCOME_UNKNOWN' in asyncio.run(call())
    conn.lua.execute('Events.UnitRemovedFromMap.Fire(0,u.id)')
    assert asyncio.run(state.spy_travel(196608,10,10)).startswith('SPY_ASSIGNMENT_STARTED|')
    assert conn.lua.eval('#submissions')==1


def test_lost_confirmation_keeps_observers_to_latch_native_scope_change():
    class LostConfirmationConn(OffMapAssignmentConn):
        async def execute_write(self,code):
            rows=await super().execute_write(code)
            if any(row.startswith('SPY_ASSIGNMENT_CONFIRMED|') for row in rows):
                self.lua.execute('seed=9;Events.UnitOperationStarted.Fire(0,u.id,-1295211657);seed=719665280')
                raise OSError('confirmation reply lost')
            return rows
    conn=LostConfirmationConn();state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('__civmcpSpyAssignment.invalid')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('#submissions')==1


def test_final_scope_recheck_blocks_offmap_roster_read_that_changes_world():
    conn=OffMapAssignmentConn();state=GameState(conn);conn.lua.execute('''
local original=Players[0].GetDiplomacy
Players[0].GetDiplomacy=function()
 local result=original()
 local read=result.GetNthOffMapSpy
 result.GetNthOffMapSpy=function(self,owner,index) local row=read(self,owner,index);seed=9;return row end
 return result
end
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute('seed=719665280')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('value', ['9', 'nil'])
def test_last_pre_entry_getter_scope_change_or_unknown_proves_zero_submission(value):
    conn=OffMapAssignmentConn();state=GameState(conn)
    conn.lua.execute(f'''
city.GetY=function(self)
 if __civmcpSpyAssignment then seed={value} end
 return self.y
end
''')
    result=asyncio.run(state.spy_travel(196608,10,10))
    assert 'SPY_ASSIGNMENT_REFUSED' in result
    assert conn.lua.eval('#submissions')==0 and conn.lua.eval('#offmap')==0
    assert state.pending_action_summary is None


class LostFirstOffMapProofConn(OffMapAssignmentConn):
    def __init__(self):
        super().__init__()
        self.drop_proof=True

    async def execute_write(self,code):
        rows=await super().execute_write(code)
        if self.drop_proof and any(row.startswith('SPY_ASSIGNMENT_CONFIRMED|') for row in rows):
            self.drop_proof=False
            raise OSError('first completed travel confirmation lost')
        return rows


@pytest.mark.parametrize('change,restore', [
    ('offmap[1].Name="Other"', 'offmap[1].Name=u.name'),
    ('offmap[1].Level=2', 'offmap[1].Level=1'),
    ('offmap[1].XLocation=11', 'offmap[1].XLocation=10'),
    ('offmap[1].YLocation=11', 'offmap[1].YLocation=10'),
    ('offmap[1].ReturnTurn=50', 'offmap[1].ReturnTurn=49'),
    ('offmap[1].NameIndex=38', 'offmap[1].NameIndex=37'),
    ('offmap[1].NameIndex=38;offmap[1].Level=nil', 'offmap[1].NameIndex=37;offmap[1].Level=1'),
    ('offmap[1].Name="Other";offmap[1].Level=nil', 'offmap[1].Name=u.name;offmap[1].Level=1'),
])
def test_lost_confirmation_bound_offmap_identity_change_stays_invalid_after_restore(change, restore):
    conn=LostFirstOffMapProofConn();state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute(restore)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    assert state.pending_action_summary is not None and conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('field,value', [
    ('Name','u.name'),('Level','1'),('XLocation','10'),('YLocation','10'),
    ('ReturnTurn','49'),('NameIndex','37'),
])
def test_lost_confirmation_unknown_bound_offmap_field_can_recover_without_resubmit(field,value):
    conn=LostFirstOffMapProofConn();state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute(f'offmap[1].{field}=nil')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute(f'offmap[1].{field}={value}')
    assert asyncio.run(state.spy_travel(196608,10,10)).startswith('SPY_ASSIGNMENT_STARTED|')
    assert state.pending_action_summary is None and conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('bad_first', [False, True])
@pytest.mark.parametrize('failure', ['unknown_key', 'row_read_error', 'field_read_error'])
@pytest.mark.parametrize('identity_changed', [False, True])
def test_unrelated_bad_rows_cannot_hide_readable_bound_identity_changes(bad_first, failure, identity_changed):
    conn=LostFirstOffMapProofConn();state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute(f'''
boundRow=offmap[1]
badRow={{}}
badIndex={0 if bad_first else 1}
if "{failure}"=="field_read_error" then
 badRow.NameIndex=99
 setmetatable(badRow,{{__index=function() error("row field unavailable") end}})
end
offmap={ '{badRow,boundRow}' if bad_first else '{boundRow,badRow}' }
badRead=true
local diplomacy=Players[0].GetDiplomacy
Players[0].GetDiplomacy=function()
 local result=diplomacy()
 local nth=result.GetNthOffMapSpy
 result.GetNthOffMapSpy=function(self,owner,index)
  if badRead and "{failure}"=="row_read_error" and index==badIndex then error("row unavailable") end
  return nth(self,owner,index)
 end
 return result
end
''')
    if identity_changed:
        conn.lua.execute('boundRow.Name="Other"')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spy_travel(196608,10,10))
    conn.lua.execute('boundRow.Name=u.name;offmap={boundRow};badRead=false')
    result=asyncio.run(state.spy_travel(196608,10,10))
    if identity_changed:
        assert 'OUTCOME_UNKNOWN' in result and state.pending_action_summary is not None
    else:
        assert result.startswith('SPY_ASSIGNMENT_STARTED|') and state.pending_action_summary is None
    assert conn.lua.eval('#submissions')==1


def test_cancelled_offmap_submission_only_confirms_original_travel():
    class CancelConn(OffMapAssignmentConn):
        async def execute_write(self,code):
            before=self.lua.eval('#submissions');rows=await super().execute_write(code)
            if self.lua.eval('#submissions')>before:
                raise asyncio.CancelledError()
            return rows
    conn=CancelConn();state=GameState(conn)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(state.spy_travel(196608,10,10))
    assert asyncio.run(state.spy_travel(196608,10,10)).startswith('SPY_ASSIGNMENT_STARTED|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('row', [
    '{NameIndex=37,Name="Spy",Level=1,XLocation=10,YLocation=10,ReturnTurn=-1}',
    '{NameIndex=37,Name="Spy",Level=1,XLocation=10,YLocation=10}',
])
def test_query_does_not_report_captured_or_unknown_rows_as_travelling(row):
    from civ_mcp.connection import LuaError
    conn=OffMapAssignmentConn();state=GameState(conn)
    conn.lua.execute('''
units={}
Locale={Lookup=function(value) return value end}
EndTurnBlockingTypes={ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE=7}
NotificationManager={GetList=function() return {} end}
offmap={'''+row+'''}
''')
    if 'ReturnTurn=-1' in row:
        assert asyncio.run(state.get_spies())==[]
    else:
        with pytest.raises(LuaError,match='off-map spy roster is unknown'):
            asyncio.run(state.get_spies())
    assert conn.lua.eval('#submissions')==0


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


@pytest.mark.parametrize('mission,x,op', [('GAIN_SOURCES',10,13),('SIPHON_FUNDS',11,14),('COUNTERSPY',11,15),('DISRUPT_ROCKETRY',11,40)])
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
