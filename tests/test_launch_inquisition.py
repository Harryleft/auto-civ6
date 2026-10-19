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
        self.core=ReadyConn()
        self.core.lua.globals().readNativeFlag=lambda: self.lua.eval('religion:HasLaunchedInquisition()')
        self.core.lua.globals().readNativeCreated=lambda: self.lua.eval('founded')
        self.core.lua.globals().readNativeSeed=lambda: self.lua.eval('seed')
        self.core.lua.globals().readNativeTurn=lambda: self.lua.eval('turn')
        self.core.lua.globals().readNativeOwner=lambda: self.lua.eval('Game.GetLocalPlayer()')
        self.core.lua.globals().readNativeCiv=lambda: self.lua.eval('PlayerConfigurations[0]:GetCivilizationTypeName()')
        self.core.lua.execute('''
Game.GetLocalPlayer=function() return readNativeOwner() end
Game.GetCurrentGameTurn=function() return readNativeTurn() end
GameConfiguration.GetValue=function() return readNativeSeed() end
PlayerConfigurations[0].GetCivilizationTypeName=function() return readNativeCiv() end
coreReligion={HasLaunchedInquisition=function() return readNativeFlag() end,
 GetReligionTypeCreated=function() return readNativeCreated() end}
Players[0].GetReligion=function() return coreReligion end
''')

    async def execute_read(self,code):
        return await self.core.execute_write(code)

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


def test_real_native_context_split_uses_gamecore_flag_with_ingame_consumption_witness():
    conn=InquisitionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('religion.HasLaunchedInquisition=function() return false end')
    conn.core.lua.globals().readNativeFlag=lambda: conn.lua.eval('launched')
    result=asyncio.run(server.unit_action(ctx,3145737,'launch_inquisition'))
    assert result.startswith('INQUISITION_LAUNCHED|unit:3145737|religion:12|')
    assert conn.lua.eval('religion:HasLaunchedInquisition()') is False
    assert conn.core.lua.eval('coreReligion:HasLaunchedInquisition()') is True
    assert conn.lua.eval('#submissions')==1 and state.pending_action_summary is None


@pytest.mark.parametrize('core_flag', ['true','nil','"false"'])
def test_authoritative_core_preflag_must_be_false_even_with_cached_ingame_false(core_flag):
    conn=InquisitionConn();state=GameState(conn)
    conn.core.lua.execute(f'coreReligion.HasLaunchedInquisition=function() return {core_flag} end')
    assert asyncio.run(state.launch_inquisition(3145737)).startswith('Error: UNIT_EFFECT_REFUSED|GAMECORE_PREFLIGHT')
    assert conn.lua.eval('#submissions')==0 and state.pending_action_summary is None


@pytest.mark.parametrize('phase', ['preflight','confirmation'])
def test_lost_gamecore_reply_never_replays_the_consuming_operation(phase):
    class LostCoreConn(InquisitionConn):
        async def execute_read(self,code):
            rows=await super().execute_read(code)
            tag='INQUISITION_CORE_PREPARED|' if phase=='preflight' else 'INQUISITION_CORE_CONFIRMED|'
            if not getattr(self,'dropped',False) and any(row.startswith(tag) for row in rows):
                self.dropped=True
                raise OSError('GameCore reply lost')
            return rows
    conn=LostCoreConn();state=GameState(conn)
    result=asyncio.run(state.launch_inquisition(3145737))
    if phase=='preflight':
        assert result.startswith('Error: UNIT_EFFECT_REFUSED|')
        assert conn.lua.eval('#submissions')==0 and state.pending_action_summary is None
    else:
        assert 'OUTCOME_UNKNOWN' in result and state.pending_action_summary is not None
    assert asyncio.run(state.launch_inquisition(3145737)).startswith('INQUISITION_LAUNCHED|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('phase', ['core','final_ingame'])
def test_cancelled_cross_context_confirmation_retains_the_original_owner(phase):
    class CancelProofConn(InquisitionConn):
        async def execute_read(self,code):
            rows=await super().execute_read(code)
            if any(row.startswith('INQUISITION_CORE_CONFIRMED|') for row in rows):
                self.core_seen=True
                if phase=='core' and not getattr(self,'cancelled',False):
                    self.cancelled=True
                    raise asyncio.CancelledError()
            return rows
        async def execute_write(self,code):
            rows=await super().execute_write(code)
            if (phase=='final_ingame' and getattr(self,'core_seen',False) and not getattr(self,'cancelled',False)
                    and any(row.startswith('UNIT_EFFECT_CONFIRMED|') for row in rows)):
                self.cancelled=True
                raise asyncio.CancelledError()
            return rows
    conn=CancelProofConn();state=GameState(conn)
    with pytest.raises(asyncio.CancelledError):asyncio.run(state.launch_inquisition(3145737))
    assert state.pending_action_summary is not None and conn.lua.eval('#submissions')==1
    assert asyncio.run(state.launch_inquisition(3145737)).startswith('INQUISITION_LAUNCHED|')
    assert conn.lua.eval('#submissions')==1


def test_final_ingame_confirmation_reply_loss_can_only_confirm_the_original_request():
    conn=LostProofConn();state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert conn.core.lua.eval('__civmcpInquisitionCore.launchedObserved')
    assert asyncio.run(state.launch_inquisition(3145737)).startswith('INQUISITION_LAUNCHED|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('change,restore', [
    ('GameConfiguration.GetValue=function() return 9 end','GameConfiguration.GetValue=function() return readNativeSeed() end'),
    ('Game.GetCurrentGameTurn=function() return 140 end','Game.GetCurrentGameTurn=function() return readNativeTurn() end'),
    ('Game.GetLocalPlayer=function() return 1 end','Game.GetLocalPlayer=function() return readNativeOwner() end'),
    ('PlayerConfigurations[0].GetCivilizationTypeName=function() return "CIVILIZATION_SWEDEN" end','PlayerConfigurations[0].GetCivilizationTypeName=function() return readNativeCiv() end'),
    ('coreReligion.GetReligionTypeCreated=function() return 13 end;coreReligion.HasLaunchedInquisition=function() return nil end',
     'coreReligion.GetReligionTypeCreated=function() return readNativeCreated() end;coreReligion.HasLaunchedInquisition=function() return readNativeFlag() end'),
    ('GameConfiguration.GetValue=function() return nil end;coreReligion.GetReligionTypeCreated=function() return 13 end',
     'GameConfiguration.GetValue=function() return readNativeSeed() end;coreReligion.GetReligionTypeCreated=function() return readNativeCreated() end'),
])
def test_readable_core_context_or_religion_changes_stay_invalid_with_unknown_peers(change, restore):
    conn=InquisitionConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    conn.core.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    conn.core.lua.execute(restore)
    conn.lua.execute('applyLaunch()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert conn.lua.eval('#submissions')==1


def test_world_change_after_core_proof_is_caught_by_final_ingame_reread():
    class ChangeAfterCoreConn(InquisitionConn):
        async def execute_read(self,code):
            rows=await super().execute_read(code)
            if not getattr(self,'changed',False) and any(row.startswith('INQUISITION_CORE_CONFIRMED|') for row in rows):
                self.changed=True;self.lua.execute('seed=9')
            return rows
    conn=ChangeAfterCoreConn();state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    conn.lua.execute('seed=-1896272000')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert conn.lua.eval('#submissions')==1


def test_late_core_flag_getter_scope_change_cannot_confirm_and_restore_cannot_clear_it():
    conn=InquisitionConn();state=GameState(conn)
    conn.core.lua.execute('''
coreReligion.HasLaunchedInquisition=function()
 local value=readNativeFlag()
 if value then GameConfiguration.GetValue=function() return 9 end end
 return value
end
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    conn.core.lua.execute('GameConfiguration.GetValue=function() return readNativeSeed() end;coreReligion.HasLaunchedInquisition=function() return readNativeFlag() end')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('setup,reason', [
    ('emitStarted=false','STARTED_OR_REMOVED_EVENT_MISSING'),
    ('keep_native=true','DUAL_UNIT_ABSENCE_UNCONFIRMED'),
    ('setFlag=false','CORE_LAUNCH_FLAG_STILL_FALSE'),
])
def test_unknown_output_names_the_actual_incomplete_evidence(setup,reason):
    conn=InquisitionConn();state=GameState(conn);conn.lua.execute(setup)
    result=asyncio.run(state.launch_inquisition(3145737))
    assert 'OUTCOME_UNKNOWN' in result and reason in result
    assert 'RECEIPT_MISSING_OR_MISMATCHED' not in result


@pytest.mark.parametrize('receipt', ['core','ingame'])
def test_missing_original_context_receipt_cannot_be_replaced_by_the_other_context(receipt):
    conn=InquisitionConn();state=GameState(conn);conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    if receipt=='core':conn.core.lua.execute('__civmcpInquisitionCore=nil')
    else:conn.lua.execute('__civmcpUnitEffect=nil')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert conn.lua.eval('#submissions')==1 and state.pending_action_summary is not None


@pytest.mark.parametrize('corruption', ['token','owner','religion','turn','seed','duplicate','unknown'])
def test_mismatched_or_ambiguous_core_proof_cannot_confirm_the_ingame_request(corruption):
    class CorruptCoreReplyConn(InquisitionConn):
        async def execute_read(self,code):
            rows=await super().execute_read(code)
            if not getattr(self,'clean',False) and any(row.startswith('INQUISITION_CORE_CONFIRMED|') for row in rows):
                if corruption=='duplicate':return rows+rows
                if corruption=='unknown':return rows+['INQUISITION_CORE_OBSERVED|unknown|CORE_RELIGION_OR_FLAG_UNKNOWN']
                parts=rows[0].split('|')
                index={'token':1,'owner':3,'religion':4,'seed':6,'turn':7}[corruption]
                parts[index]='other' if corruption=='token' else '99'
                return ['|'.join(parts)]
            return rows
    conn=CorruptCoreReplyConn();state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.launch_inquisition(3145737))
    assert state.pending_action_summary is not None and conn.lua.eval('#submissions')==1
    conn.clean=True
    assert asyncio.run(state.launch_inquisition(3145737)).startswith('INQUISITION_LAUNCHED|')
    assert conn.lua.eval('#submissions')==1


def test_unknown_core_postflag_recovers_while_ingame_cache_stays_false():
    conn=InquisitionConn();state=GameState(conn)
    conn.lua.execute('religion.HasLaunchedInquisition=function() return false end')
    conn.core.lua.globals().readNativeFlag=lambda: conn.lua.eval('launched')
    conn.core.lua.execute('coreReligion.HasLaunchedInquisition=function() if readNativeFlag() then return nil end;return false end')
    result=asyncio.run(state.launch_inquisition(3145737))
    assert 'OUTCOME_UNKNOWN' in result and 'CORE_RELIGION_OR_FLAG_UNKNOWN' in result
    conn.core.lua.execute('coreReligion.HasLaunchedInquisition=function() return readNativeFlag() end')
    assert asyncio.run(state.launch_inquisition(3145737)).startswith('INQUISITION_LAUNCHED|')
    assert conn.lua.eval('#submissions')==1


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
local read=religion.GetReligionTypeCreated
religion.GetReligionTypeCreated=function()
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
    async def execute_read(self,code):
        rows=await super().execute_read(code)
        if any(row.startswith('INQUISITION_CORE_CONFIRMED|') for row in rows):
            self.core_seen=True
        return rows

    async def execute_write(self,code):
        rows=await super().execute_write(code)
        if (getattr(self,'core_seen',False) and not getattr(self,'dropped',False)
                and any(row.startswith('UNIT_EFFECT_CONFIRMED|') for row in rows)):
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
