"""Public inquisition action with executed Lua and a native engine adapter."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import app_of, make_ctx
from test_unit_readiness import ReadyConn


class InquisitionConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply = False
        self.lua.execute('''
turn=139;seed=-1896272000;units={}
u=addUnit(3145737,true,0,0)
u.owner=0;u.kind=4;u.religion=12;u.x=34;u.y=24;u.moves=4;u.charges=3
u.GetOwner=function(self) return self.owner end
u.GetType=function(self) return self.kind end
u.GetReligionType=function(self) return self.religion end
u.GetX=function(self) return self.x end;u.GetY=function(self) return self.y end
u.GetSpreadCharges=function(self) return self.charges end
GameInfo.Units={[4]={UnitType='UNIT_APOSTLE',LaunchInquisition=true,SpreadCharges=3}}
GameInfo.UnitOperations.UNITOPERATION_LAUNCH_INQUISITION={
 Hash=-1128148313,OperationType='UNITOPERATION_LAUNCH_INQUISITION'}
UnitOperationTypes={}
launched=false;founded=12
religion={HasLaunchedInquisition=function() return launched end,
 GetReligionTypeCreated=function() return founded end}
Players[0].GetReligion=function() return religion end
Players[0].GetUnits=function() return {Members=function() return pairs(units) end,
 FindID=function(_,id) return units[id] end} end
function event()
 local handlers={}
 return {Add=function(f) handlers[f]=true end,Remove=function(f) handlers[f]=nil end,
 Fire=function(...) for f in pairs(handlers) do f(...) end end}
end
Events={UnitOperationStarted=event(),UnitRemovedFromMap=event()}
UnitManager.CanStartOperation=function(unit,op,target,params,...)
 assert(unit==u and op==-1128148313 and target==nil and type(params)=='table'
  and next(params)==nil and select('#',...)==0)
 return unit.allowed
end
function applyLaunch()
 local function remove()
  if keep_native~=true then units[3145737]=nil end
  if emitRemoved~=false then Events.UnitRemovedFromMap.Fire(removeOwner or 0,removeId or 3145737) end
 end
 if removedFirst then remove() end
 if emitStarted~=false then Events.UnitOperationStarted.Fire(startOwner or 0,startId or 3145737,startOp or -1128148313,0) end
 if not removedFirst then remove() end
 if setFlag~=false then launched=true end
end
UnitManager.RequestOperation=function(unit,op,params)
 assert(unit==u and op==-1128148313 and next(params)==nil)
 table.insert(submissions,{id=unit.id,op=op})
 if not deferred then applyLaunch() end
 if fail_request then error('entered launch failed after effect') end
end
''')

    async def execute_write(self, code):
        before = self.lua.eval('#submissions')
        rows = await super().execute_write(code)
        if self.lose_reply and self.lua.eval('#submissions') > before:
            self.lose_reply = False
            raise OSError('native launch ran; acknowledgement lost')
        return rows


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


@pytest.mark.parametrize('removed_first', [False, True])
def test_t139_public_action_launches_native_inquisition_and_consumes_original_apostle(removed_first):
    conn=InquisitionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute(f'removedFirst={str(removed_first).lower()}')
    result=asyncio.run(server.unit_action(ctx,3145737,'launch_inquisition'))
    assert result.startswith('INQUISITION_LAUNCHED|unit:3145737|religion:12|')
    assert conn.lua.eval('#submissions')==1 and conn.lua.eval('launched')
    assert conn.lua.eval('UnitManager.GetUnit(0,9)==nil and Players[0]:GetUnits():FindID(3145737)==nil')
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup', [
    'u.allowed=false','u.allowed=nil','launched=true','launched=nil','launched="false"',
    'religion.HasLaunchedInquisition=nil','religion.GetReligionTypeCreated=nil',
    'u.owner=1','u.id=3211273','u.kind=5','u.religion=13','u.religion=nil',
    'founded=13','founded=-1','founded=nil','u.x=nil',
    'GameInfo.Units[4].LaunchInquisition=false','GameInfo.Units[4].UnitType="UNIT_MISSIONARY"',
    'GameInfo.UnitOperations.UNITOPERATION_LAUNCH_INQUISITION=nil',
    'GameInfo.UnitOperations.UNITOPERATION_LAUNCH_INQUISITION.Hash=nil',
    'GameInfo.UnitOperations.UNITOPERATION_LAUNCH_INQUISITION.OperationType="OTHER"',
    'UnitManager.RequestOperation=nil','UnitManager.CanStartOperation=nil',
    'Events.UnitOperationStarted=nil','Events.UnitRemovedFromMap=nil',
    'Players[0].GetUnits=function() return {FindID=function() return nil end} end',
])
def test_unknown_ineligible_or_wrong_pre_identity_refuses_with_zero_submission(setup):
    conn=InquisitionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute(setup)
    assert asyncio.run(server.unit_action(ctx,3145737,'launch_inquisition')).startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions')==0 and state.pending_action_summary is None


@pytest.mark.parametrize('setup', [
    'emitStarted=false','emitRemoved=false','startOwner=1','startId=3211273','startOp=42',
    'removeOwner=1','removeId=3211273','setFlag=false','keep_native=true',
    'local apply=applyLaunch;applyLaunch=function() apply();launched=nil end',
    'local apply=applyLaunch;applyLaunch=function() apply();Players[0].GetUnits=function() return {FindID=function() return u end} end end',
])
@pytest.mark.parametrize('removed_first', [False, True])
def test_partial_or_wrong_native_result_never_confirms_from_flag_or_absence_alone(setup, removed_first):
    conn=InquisitionConn();state=GameState(conn)
    conn.lua.execute(f'removedFirst={str(removed_first).lower()}')
    conn.lua.execute(setup)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert conn.lua.eval('#submissions')==1 and state.pending_action_summary is not None


@pytest.mark.parametrize('event_name', ['UnitOperationStarted','UnitRemovedFromMap'])
@pytest.mark.parametrize('scope_value', ['9','nil'])
@pytest.mark.parametrize('removed_first', [False,True])
def test_matching_events_in_wrong_or_unknown_scope_do_not_authorize_confirmation(event_name, scope_value, removed_first):
    conn=InquisitionConn();state=GameState(conn)
    conn.lua.execute(f'''
removedFirst={str(removed_first).lower()}
local fire=Events.{event_name}.Fire
Events.{event_name}.Fire=function(...)
 seed={scope_value};fire(...);seed=-1896272000
end
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('change,restore', [
    ('u.owner=1','u.owner=0'),('u.id=3211273','u.id=3145737'),
    ('u.kind=5','u.kind=4'),('u.religion=13','u.religion=12'),('u.religion=-1','u.religion=12'),
    ('u.x=35','u.x=34'),('u.y=25','u.y=24'),('founded=13','founded=12'),
    ('founded=-1','founded=12'),
    ('u.religion=13;u.GetOwner=function() return nil end','u.religion=12;u.GetOwner=function(self) return self.owner end'),
    ('founded=13;religion.HasLaunchedInquisition=function() return nil end','founded=12;religion.HasLaunchedInquisition=function() return launched end'),
    ('u.religion=13;seed=nil','u.religion=12;seed=-1896272000'),
    ('seed=9;u.GetOwner=function() return nil end','seed=-1896272000;u.GetOwner=function(self) return self.owner end'),
])
def test_known_identity_or_scope_change_stays_invalid_even_with_an_unknown_peer(change, restore):
    conn=InquisitionConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    conn.lua.execute(restore+';applyLaunch()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert conn.lua.eval('#submissions')==1 and state.pending_action_summary is not None


@pytest.mark.parametrize('change,restore', [
    ('u.religion=nil','u.religion=12'),('founded=nil','founded=12'),
    ('religion.HasLaunchedInquisition=function() return nil end','religion.HasLaunchedInquisition=function() return launched end'),
    ('seed=nil','seed=-1896272000'),
    ('UnitManager.GetUnit=function() error("lookup unknown") end','UnitManager.GetUnit=function() return nil end'),
])
def test_unknown_result_field_can_recover_without_resubmitting(change, restore):
    conn=InquisitionConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    conn.lua.execute(restore+';applyLaunch()')
    assert asyncio.run(state.launch_inquisition(3145737)).startswith('INQUISITION_LAUNCHED|')
    assert conn.lua.eval('#submissions')==1 and state.pending_action_summary is None


@pytest.mark.parametrize('scope_value', ['9','nil'])
def test_final_getter_changed_or_unknown_scope_refuses_before_native_entry(scope_value):
    conn=InquisitionConn();state=GameState(conn)
    conn.lua.execute(f'''
local read=religion.HasLaunchedInquisition
religion.HasLaunchedInquisition=function()
 if __civmcpUnitEffect then
  lateReads=(lateReads or 0)+1
  if lateReads==2 then seed={scope_value} end
 end
 return read()
end
''')
    assert asyncio.run(state.launch_inquisition(3145737)).startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions')==0 and state.pending_action_summary is None


def test_late_native_eligibility_change_proves_zero_submission():
    conn=InquisitionConn();state=GameState(conn);conn.lua.execute('''
local can=UnitManager.CanStartOperation
UnitManager.CanStartOperation=function(...)
 local allowed=can(...)
 eligibilityReads=(eligibilityReads or 0)+1
 return allowed and eligibilityReads==1
end
''')
    assert asyncio.run(state.launch_inquisition(3145737)).startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions')==0


def test_lost_submit_reply_only_confirms_original_consuming_launch():
    conn=InquisitionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,3145737,'launch_inquisition'))
    assert asyncio.run(server.unit_action(ctx,3145737,'launch_inquisition')).startswith('INQUISITION_LAUNCHED|')
    assert conn.lua.eval('#submissions')==1 and state.pending_action_summary is None


def test_cancelled_submit_retains_original_intent_for_read_only_confirmation():
    class CancelConn(InquisitionConn):
        async def execute_write(self,code):
            before=self.lua.eval('#submissions');rows=await super().execute_write(code)
            if self.lua.eval('#submissions')>before:raise asyncio.CancelledError()
            return rows
    conn=CancelConn();state=GameState(conn)
    with pytest.raises(asyncio.CancelledError):asyncio.run(state.launch_inquisition(3145737))
    assert asyncio.run(state.launch_inquisition(3145737)).startswith('INQUISITION_LAUNCHED|')
    assert conn.lua.eval('#submissions')==1


class LostProofConn(InquisitionConn):
    async def execute_write(self,code):
        rows=await super().execute_write(code)
        if not getattr(self,'dropped',False) and any(row.startswith('UNIT_EFFECT_CONFIRMED|') for row in rows):
            self.dropped=True
            raise OSError('confirmed result reply lost')
        return rows


def test_lost_confirmation_keeps_original_event_scope_invalidation():
    conn=LostProofConn();state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    conn.lua.execute('seed=9;Events.UnitRemovedFromMap.Fire(0,3145737);seed=-1896272000')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert conn.lua.eval('#submissions')==1 and state.pending_action_summary is not None


def test_lost_confirmation_known_flag_reversal_cannot_borrow_restored_true():
    conn=LostProofConn();state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    conn.lua.execute('launched=false')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    conn.lua.execute('launched=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert conn.lua.eval('#submissions')==1


def test_lost_confirmation_known_founder_change_is_not_hidden_by_unknown_flag():
    conn=LostProofConn();state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    conn.lua.execute('founded=13;launched=nil')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    conn.lua.execute('founded=12;launched=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert conn.lua.eval('#submissions')==1


def test_consumed_apostle_userdata_is_not_read_after_native_submission():
    conn=InquisitionConn();state=GameState(conn)
    conn.lua.execute('''
local apply=applyLaunch
applyLaunch=function()
 apply()
 for name,value in pairs(u) do
  if type(value)=='function' then u[name]=function() error('consumed apostle userdata') end end
 end
end
''')
    assert asyncio.run(state.launch_inquisition(3145737)).startswith('INQUISITION_LAUNCHED|')
    assert conn.lua.eval('#submissions')==1


def test_native_exception_after_consumption_remains_unknown_and_never_replays():
    conn=InquisitionConn();state=GameState(conn);conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert conn.lua.eval('#submissions')==1


def test_pending_original_blocks_other_mutations_save_and_turn_until_confirmed():
    conn=InquisitionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx,3145737,'launch_inquisition'))
    for call in [lambda: state.launch_inquisition(3211273),lambda: state.spread_religion(3145737),
                 lambda: state.save_game('pending_inquisition'),state.end_turn,
                 lambda: state.set_research('TECH_POTTERY')]:
        assert 'OUTCOME_UNKNOWN' in asyncio.run(call())
    conn.lua.execute('applyLaunch()')
    assert asyncio.run(server.unit_action(ctx,3145737,'launch_inquisition')).startswith('INQUISITION_LAUNCHED|')
    assert conn.lua.eval('#submissions')==1 and state.pending_action_summary is None
