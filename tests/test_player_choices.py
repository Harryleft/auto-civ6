"""Government and dedication choices through MCP/GameState and real Lua."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from test_unit_readiness import ReadyConn

GOV = 'GOVERNMENT_OLIGARCHY'


class ChoiceConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply = False
        self.cancel_reply = False
        self.lua.execute('''
government,anarchy,endTurn,anarchyTurns=0,false,-1,0
considered,considerationCalls=false,0
unlocked,eligible,made,civicCompleted,accepted=true,true,false,true,true
local row={Index=2,Hash=402,GovernmentType='GOVERNMENT_OLIGARCHY',Name='Oligarchy'}
GameInfo.Governments={[2]=row,[402]=row,GOVERNMENT_OLIGARCHY=row}
Players[0].IsTurnActive=function() return true end
Game.IsAllowStrategicCommands=function() return true end
function applyGovernment()
 made=true
 if anarchyTurns>0 then anarchy=true;government=-1;endTurn=turn+anarchyTurns
 else government=2 end
end
culture={
 GovernmentChangeConsidered=function() return considered end,
 SetGovernmentChangeConsidered=function(_,value)
  assert(value==true);considerationCalls=considerationCalls+1;considered=true
 end,
 GetCurrentGovernment=function() return government end,
 IsGovernmentUnlocked=function(_,hash) assert(hash==402,'unlocked needs Hash');return unlocked end,
 CanChangeGovernmentAtAll=function() return eligible end,
 GovernmentChangeMade=function() return made end,
 CivicCompletedThisTurn=function() return civicCompleted end,
 IsInAnarchy=function() return anarchy end,
 GetAnarchyEndTurn=function() return endTurn end,
 GetAnarchyTurns=function(_,index) assert(index==2,'anarchy duration needs Index');return anarchyTurns end,
 RequestChangeGovernment=function(_,hash)
  assert(hash==402,'government request needs Hash')
  table.insert(submissions,{kind='government',target=hash})
  if not deferred then applyGovernment() end
  if fail_request then error('native government request threw after entry') end
  return accepted
 end,
}
Players[0].GetCulture=function() return culture end
era,allowed,active,choices=2,3,{}, {7,8}
GameInfo.Eras={[2]={EraType='ERA_CLASSICAL'},[3]={EraType='ERA_MEDIEVAL'}}
GameInfo.CommemorationTypes={[7]={Index=7,CommemorationType='COMMEMORATION_SCIENCE'},[8]={Index=8,CommemorationType='COMMEMORATION_CULTURE'},[9]={Index=9,CommemorationType='COMMEMORATION_LOCKED'}}
eras={
 GetCurrentEra=function() return era end,
 GetPlayerNumAllowedCommemorations=function() return allowed end,
 GetPlayerActiveCommemorations=function() return active end,
 GetPlayerCommemorateChoices=function() return choices end,
}
Game.GetEras=function() return eras end
PlayerOperations={COMMEMORATE=5,PARAM_COMMEMORATION_TYPE='choice'}
function applyDedication(index) table.insert(active,index);allowed=allowed-1 end
UI.RequestPlayerOperation=function(owner,operation,params)
 assert(owner==0 and operation==5)
 table.insert(submissions,{kind='dedication',target=params.choice})
 if not deferred then applyDedication(params.choice) end
 if fail_request then error('native dedication operation threw after entry') end
end
''')

    async def execute_write(self, code):
        before = self.lua.eval('#submissions')
        ack_before = self.lua.eval('considerationCalls')
        rows = await super().execute_write(code)
        if self.lua.eval('#submissions') > before or self.lua.eval('considerationCalls') > ack_before:
            if self.cancel_reply:
                raise asyncio.CancelledError()
            if self.lose_reply:
                self.lose_reply = False
                raise OSError('native player choice ran; response lost')
        return rows


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


@pytest.mark.parametrize('already_selected',[False,True])
def test_explicit_government_choice_acknowledges_native_consideration(already_selected):
    conn=ChoiceConn();state=GameState(conn);ctx=make_ctx(game=state)
    if already_selected:
        # T42: the adopted government is deliberate, but considered remains false.
        conn.lua.execute('government=2;made=true')
    result=asyncio.run(server.change_government(ctx,GOV))
    assert result.startswith('GOVERNMENT_UNCHANGED|' if already_selected else 'GOVERNMENT_CHANGED|')
    assert conn.lua.eval('considered') is True
    assert conn.lua.eval('considerationCalls')==1
    assert conn.lua.eval('#submissions')==(0 if already_selected else 1)


@pytest.mark.parametrize('patch',[
    'culture.SetGovernmentChangeConsidered=nil',
    'culture.GovernmentChangeConsidered=nil',
    'culture.GovernmentChangeConsidered=function() error("consideration unreadable") end',
    'culture.GovernmentChangeConsidered=function() return "false" end',
])
@pytest.mark.parametrize('already_selected',[False,True])
def test_unavailable_consideration_api_refuses_before_government_change(patch,already_selected):
    conn=ChoiceConn();state=GameState(conn)
    if already_selected: conn.lua.execute('government=2')
    conn.lua.execute(patch)
    assert asyncio.run(state.change_government(GOV)).startswith('Error: CHOICE_REFUSED|')
    assert conn.lua.eval('#submissions')==0
    assert conn.lua.eval('considerationCalls')==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('acknowledge',[
    'considerationCalls=considerationCalls+1',
    'considerationCalls=considerationCalls+1;error("acknowledgment failed")',
    'considerationCalls=considerationCalls+1;considered=true;error("failed after acknowledgment")',
])
@pytest.mark.parametrize('already_selected',[False,True])
def test_failed_consideration_keeps_government_facts_but_never_replays(acknowledge,already_selected):
    conn=ChoiceConn();state=GameState(conn)
    if already_selected: conn.lua.execute('government=2')
    conn.lua.execute('culture.SetGovernmentChangeConsidered=function() '+acknowledge+' end')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.change_government(GOV))
    again=asyncio.run(state.change_government(GOV))
    assert 'OUTCOME_UNKNOWN' in again and 'CONSIDERATION_' in again
    assert conn.lua.eval('government')==2
    assert conn.lua.eval('considerationCalls')==1
    assert conn.lua.eval('#submissions')==(0 if already_selected else 1)


@pytest.mark.parametrize('read_failure',[
    'return nil', 'return "true"', 'error("consideration read failed")',
])
def test_consideration_read_failure_after_acceptance_remains_pending_until_readable(read_failure):
    conn=ChoiceConn();state=GameState(conn)
    conn.lua.execute('''
culture.SetGovernmentChangeConsidered=function()
 considerationCalls=considerationCalls+1;considered=true
 culture.GovernmentChangeConsidered=function() '''+read_failure+''' end
end
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.change_government(GOV))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.change_government(GOV))
    conn.lua.execute('culture.GovernmentChangeConsidered=function() return considered end')
    assert asyncio.run(state.change_government(GOV)).startswith('GOVERNMENT_CHANGED|')
    assert conn.lua.eval('considerationCalls')==1
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('patch',[
    'Players[0].IsTurnActive=function() return false end',
    'Players[0].IsTurnActive=function() return nil end',
    'Game.IsAllowStrategicCommands=function() return false end',
    'Game.IsAllowStrategicCommands=function() error("strategic commands unknown") end',
])
def test_same_government_acknowledgment_requires_active_allowed_turn(patch):
    conn=ChoiceConn();state=GameState(conn)
    conn.lua.execute('government=2;'+patch)
    assert asyncio.run(state.change_government(GOV)).startswith('Error: CHOICE_REFUSED|')
    assert conn.lua.eval('considerationCalls')==0
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('boundary',['lost','cancelled'])
def test_current_government_acknowledgment_lost_reply_only_confirms_original(boundary):
    conn=ChoiceConn();state=GameState(conn);ctx=make_ctx(game=state)
    conn.lua.execute('government=2;made=true')
    if boundary=='lost': conn.lose_reply=True
    else: conn.cancel_reply=True
    if boundary=='cancelled':
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(server.change_government(ctx,GOV))
    else:
        assert 'OUTCOME_UNKNOWN' in asyncio.run(server.change_government(ctx,GOV))
    assert asyncio.run(server.change_government(ctx,GOV)).startswith('GOVERNMENT_UNCHANGED|')
    assert conn.lua.eval('considered') is True
    assert conn.lua.eval('considerationCalls')==1
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


def test_delayed_government_change_confirms_target_without_resending():
    conn = ChoiceConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.change_government(ctx,GOV))
    assert conn.lua.eval('considered') is True
    conn.lua.execute('applyGovernment()')
    assert asyncio.run(server.change_government(ctx,GOV)).startswith('GOVERNMENT_CHANGED|')
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


def test_anarchy_start_is_confirmed_without_claiming_final_government_or_blocking_turns():
    conn = ChoiceConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('anarchyTurns=3')
    result = asyncio.run(server.change_government(ctx,GOV))
    assert result.startswith('GOVERNMENT_CHANGE_STARTED|'), result
    assert 'target:GOVERNMENT_OLIGARCHY' in result and 'anarchy_until:44' in result
    assert conn.lua.eval('government') == -1
    assert state.pending_action_summary is None
    assert not state.pending_action_blocks_mutations
    assert conn.lua.eval('#submissions') == 1
    assert asyncio.run(state.skip_unit(196608)).startswith('SKIPPED')
    assert conn.lua.eval('#submissions') == 2


def test_delayed_dedication_confirms_only_the_requested_active_choice():
    conn = ChoiceConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.choose_dedication(ctx,7))
    conn.lua.execute('applyDedication(8)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.choose_dedication(ctx,7))
    conn.lua.execute('applyDedication(7)')
    assert asyncio.run(server.choose_dedication(ctx,7)).startswith('DEDICATION_CHOSEN|')
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('considerationCalls') == 0
    assert state.pending_action_summary is None


def test_immediate_government_change_does_not_require_an_old_anarchy_interval():
    conn = ChoiceConn(); state = GameState(conn)
    conn.lua.execute('culture.GetAnarchyEndTurn=nil')
    assert asyncio.run(state.change_government(GOV)).startswith('GOVERNMENT_CHANGED|')
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('patch', [
    'accepted=false', 'accepted=nil',
    'culture.GovernmentChangeMade=function() return false end',
    'applyGovernment=function() made=true;anarchy=true;government=-1;endTurn=99 end',
    'endTurn=44',
])
def test_anarchy_needs_acceptance_and_a_new_matching_interval(patch):
    conn = ChoiceConn(); state = GameState(conn)
    conn.lua.execute('anarchyTurns=3;' + patch)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.change_government(GOV))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.change_government(GOV))
    assert state.pending_action_blocks_mutations
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('patch,reason', [
    ('unlocked=false', 'GOVERNMENT_LOCKED'),
    ('eligible=false', 'CANNOT_CHANGE_GOVERNMENT'),
    ('made=true', 'CANNOT_CHANGE_GOVERNMENT'),
    ('civicCompleted=false', 'CANNOT_CHANGE_GOVERNMENT'),
    ('anarchy=true', 'ANARCHY_ALREADY_ACTIVE'),
    ('unlocked=nil', 'ELIGIBILITY_UNKNOWN'),
    ('culture.RequestChangeGovernment=nil', 'OPERATION_UNAVAILABLE'),
])
def test_government_refusal_proves_no_native_submission(patch, reason):
    conn = ChoiceConn(); state = GameState(conn)
    conn.lua.execute(patch)
    assert reason in asyncio.run(state.change_government(GOV))
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


def test_existing_government_is_noop_even_if_a_new_change_would_be_denied():
    conn = ChoiceConn(); state = GameState(conn)
    conn.lua.execute('government=2;eligible=false;unlocked=false;made=true')
    assert asyncio.run(state.change_government(GOV)).startswith('GOVERNMENT_UNCHANGED|')
    assert conn.lua.eval('#submissions') == 0


@pytest.mark.parametrize('operation,target,label', [
    ('change_government', GOV, 'GOVERNMENT_CHANGED'),
    ('choose_dedication', 7, 'DEDICATION_CHOSEN'),
])
@pytest.mark.parametrize('boundary', ['lost', 'cancelled', 'throw'])
def test_player_choice_uncertain_transport_never_replays_native_request(operation, target, label, boundary):
    conn = ChoiceConn(); state = GameState(conn); ctx = make_ctx(game=state)
    if boundary == 'lost': conn.lose_reply = True
    if boundary == 'cancelled': conn.cancel_reply = True
    if boundary == 'throw': conn.lua.execute('fail_request=true')
    call = getattr(server, operation)
    if boundary == 'cancelled':
        with pytest.raises(asyncio.CancelledError): asyncio.run(call(ctx,target))
    else:
        result = asyncio.run(call(ctx,target))
        assert 'OUTCOME_UNKNOWN' in result
    result = asyncio.run(call(ctx,target))
    assert (label if boundary != 'throw' else 'OUTCOME_UNKNOWN') in result
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_blocks_mutations == (boundary == 'throw')


@pytest.mark.parametrize('patch,reason', [
    ('allowed=0', 'NO_DEDICATION_NEEDED'),
    ('choices={8}', 'DEDICATION_NOT_AVAILABLE'),
    ('active=nil', 'CHOICES_UNAVAILABLE'),
    ('choices=nil', 'CHOICES_UNAVAILABLE'),
    ('era=-1', 'ERA_UNAVAILABLE'),
    ('allowed=nil', 'ELIGIBILITY_UNKNOWN'),
])
def test_dedication_requires_current_native_availability(patch, reason):
    conn = ChoiceConn(); state = GameState(conn)
    conn.lua.execute(patch)
    assert reason in asyncio.run(state.choose_dedication(7))
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


def test_heroic_choices_consume_one_choice_each_and_repeating_active_choice_is_noop():
    conn = ChoiceConn(); state = GameState(conn); ctx = make_ctx(game=state)
    assert asyncio.run(server.choose_dedication(ctx,7)).startswith('DEDICATION_CHOSEN|')
    assert asyncio.run(server.choose_dedication(ctx,7)).startswith('DEDICATION_UNCHANGED|')
    assert asyncio.run(server.choose_dedication(ctx,8)).startswith('DEDICATION_CHOSEN|')
    assert conn.lua.eval('#submissions') == 2
    assert conn.lua.eval('allowed') == 1
    conn.lua.execute('allowed=0;choices=nil')
    assert asyncio.run(server.choose_dedication(ctx,7)).startswith('DEDICATION_UNCHANGED|')
    assert conn.lua.eval('#submissions') == 2


def test_dedication_era_mismatch_is_latched_even_when_old_era_is_seen_again():
    conn = ChoiceConn(); state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.choose_dedication(7))
    conn.lua.execute('era=3;applyDedication(7)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.choose_dedication(7))
    conn.lua.execute('era=2')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.choose_dedication(7))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('operation,target,other', [
    ('change_government', GOV, 'GOVERNMENT_CHIEFDOM'),
    ('choose_dedication', 7, 8),
])
def test_pending_choice_blocks_changed_intent_other_mcp_and_direct_writes(operation,target,other):
    conn = ChoiceConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('deferred=true')
    call = getattr(server, operation)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(call(ctx,target))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(call(ctx,other))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.set_research(ctx,'TECH_POTTERY'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.skip_unit(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('operation,target,read_patch,restore,label', [
    ('change_government', GOV,
     'culture.GetCurrentGovernment=function() error("read unavailable") end',
     'culture.GetCurrentGovernment=function() return government end;applyGovernment()', 'GOVERNMENT_CHANGED'),
    ('choose_dedication', 7,
     'eras.GetPlayerActiveCommemorations=function() error("read unavailable") end',
     'eras.GetPlayerActiveCommemorations=function() return active end;applyDedication(7)', 'DEDICATION_CHOSEN'),
])
def test_choice_readback_error_preserves_original_intent_until_readable(operation,target,read_patch,restore,label):
    conn = ChoiceConn(); state = GameState(conn)
    conn.lua.execute('deferred=true')
    call = getattr(state, operation)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(call(target))
    conn.lua.execute(read_patch)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(call(target))
    conn.lua.execute(restore)
    assert asyncio.run(call(target)).startswith(label + '|')
    assert conn.lua.eval('#submissions') == 1
