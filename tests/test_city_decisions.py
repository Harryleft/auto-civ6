"""City decisions through public MCP/GameState and real Lua 5.1."""
import asyncio
import pytest
from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx, app_of
from test_unit_readiness import ReadyConn


class DecisionConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply=False
        self.lua.execute('''
function event()
 local listeners={}
 return {Add=function(f) listeners[f]=true end,Remove=function(f) listeners[f]=nil end,
 Fire=function(...) for f in pairs(listeners) do f(...) end end}
end
Events={CityCommandStarted=event(),CityRemovedFromMap=event(),CityTransfered=event()}
CityCommandTypes={DESTROY=71}
CityDestroyDirectives={KEEP=1,REJECT=2,RAZE=3,LIBERATE_FOUNDER=4,LIBERATE_PREVIOUS_OWNER=5}
UnitOperationTypes={PARAM_FLAGS='flags'}
PlayerManager={GetFreeCitiesPlayerID=function() return 62 end}
Locale={Lookup=function(v) return v end}
cityRows={}
function addCity(id,owner,x,y)
 local c={id=id,owner=owner,x=x,y=y,founder=2,previous=3,allowed=true}
 c.GetID=function(self) return self.id end
 c.GetOwner=function(self) return self.owner end
 c.GetX=function(self) return self.x end
 c.GetY=function(self) return self.y end
 c.GetOriginalOwner=function(self) return self.founder end
 c.GetOwnerBeforeOccupation=function(self) return self.previous end
 c.GetName=function() return 'CAPTURED_CITY' end
 c.GetPopulation=function() return 4 end
 table.insert(cityRows,c);return c
end
first=addCity(65537,0,10,10);second=addCity(65538,0,20,20)
captured={first,second};rebelled={}
function cityAt(x,y)
 for _,c in ipairs(cityRows) do if not c.removed and c.x==x and c.y==y then return c end end
end
for _,owner in ipairs({0,2,3,62}) do
 local o=owner
 local cities={FindID=function(self,id)
  for _,c in ipairs(cityRows) do if not c.removed and c.owner==o and c.id==id then return c end end
 end, GetNextCapturedCity=function() return captured[1] end, GetNextRebelledCity=function() return rebelled[1] end}
 Players[o]=Players[o] or {};Players[o].GetCities=function() return cities end
end
Map={GetPlot=function(x,y) return {GetIndex=function() return x*100+y end} end}
function applyDecision(c,flag)
 Events.CityCommandStarted.Fire(c.owner,c.id,c.owner,0,71,999)
 if captured[1]==c then table.remove(captured,1) else table.remove(rebelled,1) end
end
CityManager={GetCityAt=cityAt,
 CanStartCommand=function(c,command,params)
  assert(command==71 and params.flags);return c.allowed
 end,
 RequestCommand=function(c,command,params)
  assert(command==71);table.insert(submissions,{id=c.id,flag=params.flags})
  if not deferred then applyDecision(c,params.flags) end
  if fail_request then error('native command threw') end
 end}
''')

    async def execute_write(self,code):
        before=self.lua.eval('#submissions')
        result=await super().execute_write(code)
        if self.lose_reply and self.lua.eval('#submissions')>before:
            self.lose_reply=False
            raise OSError('effect applied; response lost')
        return result


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT',.01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL',.001)


def test_requested_city_must_match_native_pending_full_identity():
    conn=DecisionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    result=asyncio.run(server.city_action(ctx,131073,'keep'))
    assert 'CITY_DECISION_REFUSED' in result
    assert conn.lua.eval('#submissions')==0
    assert conn.lua.eval('captured[1].id')==65537


@pytest.mark.parametrize('source',['captured','rebelled'])
def test_lost_keep_reply_confirms_original_city_without_processing_next_queue_item(source):
    conn=DecisionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    if source=='rebelled': conn.lua.execute('rebelled=captured;captured={}')
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.city_action(ctx,65537,'keep'))
    assert conn.lua.eval(f'{source}[1].id')==65538
    assert asyncio.run(server.city_action(ctx,65537,'keep'))==f'KEEP|city:65537|source:{source}'
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval(f'{source}[1].id')==65538
    assert state.pending_action_summary is None


def test_raze_needs_original_removal_and_does_not_read_destroyed_userdata():
    conn=DecisionConn();state=GameState(conn)
    conn.lua.execute('''
local baseApply=applyDecision
function applyDecision(c,flag)
 baseApply(c,flag);assert(flag==3)
 local owner,id=c.owner,c.id;c.removed=true
 Events.CityRemovedFromMap.Fire(owner,id)
 c.GetID=function() error('destroyed userdata') end
 c.GetOwner=function() error('destroyed userdata') end
 c.GetX=function() error('destroyed userdata') end
 c.GetY=function() error('destroyed userdata') end
end
''')
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'raze'))
    assert asyncio.run(state.resolve_city_capture(65537,'raze'))=='RAZE|city:65537|source:captured'
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('captured[1].id')==65538


@pytest.mark.parametrize('action,target,flag',[
    ('reject',62,2),('liberate_founder',2,4),('liberate_previous',3,5),
])
def test_transfer_decision_binds_precommand_owner_and_original_city_heritage(action,target,flag):
    conn=DecisionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    if action=='reject': conn.lua.execute('rebelled=captured;captured={}')
    conn.lua.execute(f'''
local baseApply=applyDecision
function applyDecision(c,flag)
 baseApply(c,flag);assert(flag=={flag})
 local x,y,founder=c.x,c.y,c.founder
 c.removed=true
 local new=addCity(196609,{target},x,y);new.founder=founder
 Events.CityTransfered.Fire({target},new.id)
 c.GetOriginalOwner=function() error('transferred userdata') end
 c.GetOwnerBeforeOccupation=function() error('transferred userdata') end
 c.GetID=function() error('transferred userdata') end
end
''')
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.city_action(ctx,65537,action))
    assert asyncio.run(server.city_action(ctx,65537,action)).startswith(action.upper()+'|city:65537|')
    assert conn.lua.eval('cityAt(10,10).owner')==target
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('city_id,action',[(True,'keep'),(-1,'keep'),('65537','keep'),(65537,[]),(65537,None),(65537,'annex')])
def test_invalid_direct_city_decision_is_zero_submit(city_id,action):
    conn=DecisionConn();state=GameState(conn)
    assert 'INVALID_CITY_DECISION' in asyncio.run(state.resolve_city_capture(city_id,action))
    assert conn.lua.eval('#submissions')==0


def test_missing_original_city_collection_cannot_enter_irreversible_decision():
    conn=DecisionConn();state=GameState(conn)
    conn.lua.execute('Players[0].GetCities().FindID=nil')
    assert 'CITY_DECISION_REFUSED' in asyncio.run(state.resolve_city_capture(65537,'raze'))
    assert conn.lua.eval('#submissions')==0


def test_matching_captured_city_does_not_select_unrelated_rebelled_head():
    conn=DecisionConn();state=GameState(conn)
    conn.lua.execute('rebelled={second}')
    assert asyncio.run(state.resolve_city_capture(65537,'keep'))=='KEEP|city:65537|source:captured'
    assert conn.lua.eval('submissions[1].id')==65537
    assert conn.lua.eval('rebelled[1].id')==65538


def test_next_city_requires_a_new_explicit_call_after_original_confirmation():
    conn=DecisionConn();state=GameState(conn)
    assert asyncio.run(state.resolve_city_capture(65537,'keep')).startswith('KEEP|')
    assert 'CITY_DECISION_REFUSED' in asyncio.run(state.resolve_city_capture(65537,'keep'))
    assert conn.lua.eval('#submissions')==1
    assert asyncio.run(state.resolve_city_capture(65538,'keep')).startswith('KEEP|')
    assert conn.lua.eval('#submissions')==2


@pytest.mark.parametrize('setup',[
    'first.allowed=false', 'first.allowed=nil',
    'CityManager.CanStartCommand=nil',
    'CityManager.CanStartCommand=function() error("unavailable") end',
    'CityManager.RequestCommand=nil','Events.CityCommandStarted=nil',
    'first.owner=1','first.id=131073','captured={}',
    'Players[0].GetCities().GetNextCapturedCity=nil',
    'rebelled={first}','busy=true','busy=nil',
])
def test_unknown_or_refused_native_keep_qualification_is_zero_submit(setup):
    conn=DecisionConn();state=GameState(conn);conn.lua.execute(setup)
    assert 'CITY_DECISION_REFUSED' in asyncio.run(state.resolve_city_capture(65537,'keep'))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('action,setup',[
    ('reject',''),('raze','rebelled=captured;captured={}'),
    ('liberate_founder','rebelled=captured;captured={}'),
    ('liberate_previous','first.previous=nil'),
    ('liberate_founder','first.founder=nil'),
    ('liberate_founder','first.founder=0'),
    ('liberate_previous','Events.CityTransfered=nil'),
    ('raze','Events.CityRemovedFromMap=nil'),
    ('reject','rebelled=captured;captured={};PlayerManager.GetFreeCitiesPlayerID=nil'),
])
def test_source_or_target_owner_must_be_known_before_destructive_decision(action,setup):
    conn=DecisionConn();state=GameState(conn);conn.lua.execute(setup)
    assert 'CITY_DECISION_REFUSED' in asyncio.run(state.resolve_city_capture(65537,action))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('effect',[
    '', 'table.remove(captured,1)',
    'Events.CityCommandStarted.Fire(0,65537,0,0,71,1)',
    'table.remove(captured,1);Events.CityCommandStarted.Fire(0,65538,0,0,71,1)',
    'table.remove(captured,1);Events.CityCommandStarted.Fire(0,65537,0,0,99,1)',
    'applyDecision(first,1);first.id=131073',
    'applyDecision(first,1);first.owner=2',
    'applyDecision(first,1);first.x=11',
    'applyDecision(first,1);busy=true',
])
def test_keep_requires_original_city_command_and_resolved_source(effect):
    conn=DecisionConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'keep'))
    conn.lua.execute(effect)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'keep'))
    assert conn.lua.eval('#submissions')==1


def test_delayed_keep_completes_without_resubmission_when_original_receipt_arrives():
    conn=DecisionConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'keep'))
    conn.lua.execute('applyDecision(first,1)')
    assert asyncio.run(state.resolve_city_capture(65537,'keep')).startswith('KEEP|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('effect',[
    'first.removed=true',
    'Events.CityRemovedFromMap.Fire(0,65537)',
    'first.removed=true;Events.CityRemovedFromMap.Fire(0,65538)',
    'first.removed=true;Events.CityRemovedFromMap.Fire(2,65537)',
    'first.removed=true;Events.CityRemovedFromMap.Fire(0,65537);addCity(131073,0,10,10)',
])
def test_raze_needs_matching_removal_and_empty_original_city_plot(effect):
    conn=DecisionConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'raze'))
    conn.lua.execute('applyDecision(first,3);'+effect)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'raze'))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('effect',[
    'first.removed=true',
    'first.removed=true;addCity(196609,2,10,10)',
    'first.removed=true;Events.CityTransfered.Fire(2,196609)',
    'first.removed=true;addCity(196609,3,10,10);Events.CityTransfered.Fire(3,196609)',
    'first.removed=true;local new=addCity(196609,2,10,10);new.founder=3;Events.CityTransfered.Fire(2,196609)',
    'first.removed=true;addCity(196609,2,11,10);Events.CityTransfered.Fire(2,196609)',
    'first.removed=true;addCity(196609,2,10,10);Events.CityTransfered.Fire(2,196610)',
])
def test_liberation_requires_original_plot_owner_heritage_and_exact_transfer_event(effect):
    conn=DecisionConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'liberate_founder'))
    conn.lua.execute('applyDecision(first,4);'+effect)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'liberate_founder'))
    assert conn.lua.eval('#submissions')==1


def test_delayed_liberation_uses_original_owner_snapshot_even_after_old_entity_disappears():
    conn=DecisionConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'liberate_previous'))
    conn.lua.execute('applyDecision(first,5);first.removed=true;addCity(196609,3,10,10);first.previous=2')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'liberate_previous'))
    conn.lua.execute('Events.CityTransfered.Fire(3,196609)')
    assert asyncio.run(state.resolve_city_capture(65537,'liberate_previous')).startswith('LIBERATE_PREVIOUS|')
    assert conn.lua.eval('#submissions')==1


def test_pending_city_decision_blocks_other_city_unit_empire_and_turn():
    conn=DecisionConn();state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.city_action(ctx,65537,'keep'))
    for call in [state.resolve_city_capture(65538,'keep'),state.resolve_city_capture(65537,'raze'),
                 server.city_action(ctx,65538,'attack',7,8),state.sleep_unit(196608),
                 state.set_research('TECH_POTTERY'),state.end_turn()]:
        assert 'OUTCOME_UNKNOWN' in asyncio.run(call)
    assert conn.lua.eval('#submissions')==1


def test_native_throw_after_city_effect_never_replays():
    conn=DecisionConn();state=GameState(conn);conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'keep'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'keep'))
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('captured[1].id')==65538


def test_cancelled_city_command_is_retained_and_only_confirmed():
    class CancelConn(DecisionConn):
        async def execute_write(self,code):
            before=self.lua.eval('#submissions');result=await super().execute_write(code)
            if self.lua.eval('#submissions')>before:raise asyncio.CancelledError()
            return result
    conn=CancelConn();state=GameState(conn)
    with pytest.raises(asyncio.CancelledError):asyncio.run(state.resolve_city_capture(65537,'keep'))
    assert state.pending_action_summary is not None
    assert asyncio.run(state.resolve_city_capture(65537,'keep')).startswith('KEEP|')
    assert conn.lua.eval('#submissions')==1


def test_scope_change_before_city_command_proves_zero_submission():
    class ChangedConn(DecisionConn):
        async def execute_write(self,code):
            if '-- ACTION_SUBMIT_SCOPE|' in code:self.lua.execute('turn=42')
            return await super().execute_write(code)
    conn=ChangedConn();state=GameState(conn)
    assert 'ACTION_SCOPE_NO_SUBMIT' in asyncio.run(state.resolve_city_capture(65537,'keep'))
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('changed',['seed=9','turn=42','PlayerConfigurations[0].GetCivilizationTypeName=function() return "CIVILIZATION_CHINA" end'])
def test_changed_world_latches_original_city_command(changed):
    conn=DecisionConn();state=GameState(conn);conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'keep'))
    conn.lua.execute(changed)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'keep'))
    conn.lua.execute('seed=719665280;turn=41;PlayerConfigurations[0].GetCivilizationTypeName=function() return "CIVILIZATION_SUMERIA" end')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'keep'))
    assert conn.lua.eval('#submissions')==1


def test_failed_city_readback_preserves_pending_decision():
    class ReadErrorConn(DecisionConn):
        async def execute_write(self,code):
            if self.lua.eval('#submissions'):raise OSError('read unavailable')
            return await super().execute_write(code)
    conn=ReadErrorConn();state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'keep'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'keep'))
    assert conn.lua.eval('#submissions')==1


def test_later_transfer_event_does_not_erase_original_city_transfer_receipt():
    conn=DecisionConn();state=GameState(conn);conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.resolve_city_capture(65537,'liberate_founder'))
    conn.lua.execute('''
applyDecision(first,4);first.removed=true;addCity(196609,2,10,10)
Events.CityTransfered.Fire(2,196609);Events.CityTransfered.Fire(2,196610)
''')
    assert asyncio.run(state.resolve_city_capture(65537,'liberate_founder')).startswith('LIBERATE_FOUNDER|')
    assert conn.lua.eval('#submissions')==1
