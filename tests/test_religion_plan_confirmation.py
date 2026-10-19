"""Religion selection through public MCP/GameState with real Lua and native state."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import app_of, make_ctx
from test_unit_readiness import ReadyConn


class ReligionConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_after = None
        self.cancel_after = None
        self.lua.execute('''
pantheon,created,earned,faith=-1,-1,2,100
canPantheon=true
religions={}
takenPantheon,takenBeliefs,takenReligions,tooMany={},{},{},{}
deferredStages={};throwStage=nil
GameInfo.Beliefs={};GameInfo.Religions={}
for _,row in ipairs({
 {Index=1,Hash=101,BeliefType='BELIEF_SPARK',BeliefClassType='BELIEF_CLASS_PANTHEON'},
 {Index=2,Hash=102,BeliefType='BELIEF_FORGE',BeliefClassType='BELIEF_CLASS_PANTHEON'},
 {Index=10,Hash=110,BeliefType='BELIEF_WORK',BeliefClassType='BELIEF_CLASS_FOLLOWER'},
 {Index=11,Hash=111,BeliefType='BELIEF_CHORAL',BeliefClassType='BELIEF_CLASS_FOLLOWER'},
 {Index=20,Hash=120,BeliefType='BELIEF_PROPERTY',BeliefClassType='BELIEF_CLASS_FOUNDER'},
 {Index=21,Hash=121,BeliefType='BELIEF_STEWARDSHIP',BeliefClassType='BELIEF_CLASS_FOUNDER'},
}) do row.Name=row.BeliefType;GameInfo.Beliefs[row.Index]=row;GameInfo.Beliefs[row.Hash]=row;GameInfo.Beliefs[row.BeliefType]=row end
for _,row in ipairs({
 {Index=0,Hash=200,ReligionType='RELIGION_PANTHEON',Pantheon=true,RequiresCustomName=false},
 {Index=4,Hash=204,ReligionType='RELIGION_BUDDHISM',Pantheon=false,RequiresCustomName=false},
 {Index=5,Hash=205,ReligionType='RELIGION_HINDUISM',Pantheon=false,RequiresCustomName=false},
 {Index=6,Hash=206,ReligionType='RELIGION_CUSTOM',Pantheon=false,RequiresCustomName=true},
}) do row.Name=row.ReligionType;GameInfo.Religions[row.Index]=row;GameInfo.Religions[row.Hash]=row;GameInfo.Religions[row.ReligionType]=row end
playerReligion={GetPantheon=function() return pantheon end,GetReligionTypeCreated=function() return created end,
 GetNumBeliefsEarned=function() return earned end,CanCreatePantheon=function() return canPantheon end,
 GetFaithBalance=function() return faith end}
Players[0].GetReligion=function() return playerReligion end
religion={GetReligions=function() return religions end,
 IsInSomePantheon=function(_,id) return takenPantheon[id]==true or pantheon==id end,
 IsInSomeReligion=function(_,id)
  if takenBeliefs[id] then return true end
  for _,r in pairs(religions) do for _,b in pairs(r.Beliefs) do if b==id then return true end end end
  return false
 end,
 IsTooManyForReligion=function(_,belief,rel) return tooMany[belief]==true end,
 HasBeenFounded=function(_,id) return takenReligions[id]==true or created==id end}
Game.GetReligion=function() return religion end
PlayerOperations={FOUND_PANTHEON=10,FOUND_RELIGION=11,ADD_BELIEF=12,
 PARAM_BELIEF_TYPE='belief',PARAM_RELIGION_TYPE='religion',PARAM_INSERT_MODE='insert',VALUE_EXCLUSIVE=1}
Locale={Lookup=function(s) return s end}
function applyReligion(op,p)
 if op==10 then pantheon=GameInfo.Beliefs[p.belief].Index;faith=faith-25
 elseif op==11 then created=GameInfo.Religions[p.religion].Index;religions={{Religion=created,Founder=0,Beliefs={}}}
 else table.insert(religions[1].Beliefs,GameInfo.Beliefs[p.belief].Index) end
end
UI.RequestPlayerOperation=function(owner,op,p)
 assert(owner==0)
 table.insert(submissions,{op=op,params=p})
 if not deferredStages[#submissions] then applyReligion(op,p) end
 if throwStage==#submissions then error('native religion stage entered then threw') end
end
''')

    async def execute_write(self, code):
        before = self.lua.eval('#submissions')
        rows = await super().execute_write(code)
        stage = self.lua.eval('#submissions')
        if stage > before:
            if self.cancel_after == stage:
                self.cancel_after = None
                raise asyncio.CancelledError()
            if self.lose_after == stage:
                self.lose_after = None
                raise OSError(f'native religion stage {stage} applied; reply lost')
        return rows


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def test_lost_pantheon_reply_reentry_confirms_original_choice_without_second_cost():
    conn = ReligionConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lose_after = 1
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.choose_pantheon(ctx, 'BELIEF_SPARK'))
    assert asyncio.run(server.choose_pantheon(ctx, 'BELIEF_SPARK')).startswith('PANTHEON_FOUNDED|BELIEF_SPARK')
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('faith') == 75
    assert conn.lua.eval('submissions[1].params.insert') == 1
    assert state.pending_action_summary is None


RELIGION_ARGS = ('RELIGION_BUDDHISM', 'BELIEF_WORK', 'BELIEF_PROPERTY')


def test_lost_first_belief_reply_keeps_completed_phases_and_only_owner_continues_second():
    conn = ReligionConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute('pantheon=1')
    conn.lose_after = 2
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.found_religion(ctx, *RELIGION_ARGS))
    assert conn.lua.eval('#submissions') == 2
    assert conn.lua.eval('created') == 4
    assert conn.lua.eval('religions[1].Beliefs[1]') == 10
    result = asyncio.run(state.confirm_pending_action(wait=False))
    assert 'OUTCOME_UNKNOWN' in result
    assert 'confirmed:2/3' in result
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.choose_pantheon('BELIEF_FORGE'))
    assert conn.lua.eval('#submissions') == 2
    result = asyncio.run(server.found_religion(ctx, *RELIGION_ARGS))
    assert result.startswith('RELIGION_FOUNDED|RELIGION_BUDDHISM|BELIEF_WORK|BELIEF_PROPERTY')
    assert conn.lua.eval('#submissions') == 3
    assert conn.lua.eval('religions[1].Beliefs[2]') == 20
    assert state.pending_action_summary is None


@pytest.mark.parametrize('kind', ['pantheon', 'religion'])
def test_already_selected_exact_targets_are_noop_with_original_beliefs_preserved(kind):
    conn = ReligionConn(); state = GameState(conn)
    conn.lua.execute('pantheon=1;canPantheon=false')
    if kind == 'pantheon':
        result = asyncio.run(state.choose_pantheon('BELIEF_SPARK'))
        assert result.startswith('PANTHEON_UNCHANGED|BELIEF_SPARK')
    else:
        conn.lua.execute('created=4;earned=0;religions={{Religion=4,Founder=0,Beliefs={10,20,30}}}')
        result = asyncio.run(state.found_religion(*RELIGION_ARGS))
        assert result.startswith('RELIGION_UNCHANGED|RELIGION_BUDDHISM')
        assert conn.lua.eval('#religions[1].Beliefs') == 3
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


def test_normal_founding_uses_three_exact_exclusive_native_phases():
    conn = ReligionConn(); state = GameState(conn); conn.lua.execute('pantheon=1')
    assert asyncio.run(state.found_religion(*RELIGION_ARGS)).startswith('RELIGION_FOUNDED|')
    assert conn.lua.eval('#submissions') == 3
    assert [conn.lua.eval(f'submissions[{i}].op') for i in (1, 2, 3)] == [11, 12, 12]
    assert conn.lua.eval('submissions[1].params.religion') == 204
    assert conn.lua.eval('submissions[2].params.belief') == 110
    assert conn.lua.eval('submissions[3].params.belief') == 120
    assert [conn.lua.eval(f'submissions[{i}].params.insert') for i in (1, 2, 3)] == [1, 1, 1]
    assert conn.lua.eval('pantheon') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('stage', [1, 2, 3])
@pytest.mark.parametrize('interruption', ['lost', 'cancelled'])
def test_every_founding_phase_survives_interruption_without_replaying_prior_phases(stage, interruption):
    conn = ReligionConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute('pantheon=1')
    if interruption == 'lost':
        conn.lose_after = stage
        assert 'OUTCOME_UNKNOWN' in asyncio.run(server.found_religion(ctx, *RELIGION_ARGS))
    else:
        conn.cancel_after = stage
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(server.found_religion(ctx, *RELIGION_ARGS))
    assert conn.lua.eval('#submissions') == stage
    assert state.pending_action_kind == 'found_religion'
    assert asyncio.run(server.found_religion(ctx, *RELIGION_ARGS)).startswith('RELIGION_FOUNDED|')
    assert conn.lua.eval('#submissions') == 3
    assert conn.lua.eval('#religions[1].Beliefs') == 2
    assert state.pending_action_summary is None


@pytest.mark.parametrize('stage', [1, 2, 3])
def test_delayed_phase_is_only_observed_until_native_effect_appears(stage):
    conn = ReligionConn(); state = GameState(conn); conn.lua.execute(f'pantheon=1;deferredStages[{stage}]=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    assert conn.lua.eval('#submissions') == stage
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    assert conn.lua.eval('#submissions') == stage
    conn.lua.execute(f'applyReligion(submissions[{stage}].op,submissions[{stage}].params)')
    assert asyncio.run(state.found_religion(*RELIGION_ARGS)).startswith('RELIGION_FOUNDED|')
    assert conn.lua.eval('#submissions') == 3


@pytest.mark.parametrize('stage', [1, 2, 3])
def test_native_throw_after_entry_never_replays_or_advances_that_phase(stage):
    conn = ReligionConn(); state = GameState(conn); conn.lua.execute(f'pantheon=1;throwStage={stage}')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    assert conn.lua.eval('#submissions') == stage
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    assert conn.lua.eval('#submissions') == stage
    assert state.pending_action_kind == 'found_religion'


@pytest.mark.parametrize('setup', [
    'pantheon=-1', 'earned=1', 'earned=nil', 'created=5', 'takenReligions[4]=true',
    'takenBeliefs[10]=true', 'takenBeliefs[20]=true', 'takenPantheon[10]=true', 'tooMany[10]=true',
    'religion.HasBeenFounded=function() return nil end', 'religion.IsInSomeReligion=function() return nil end',
    'religion.IsTooManyForReligion=function() return nil end',
    'PlayerOperations.ADD_BELIEF=nil', 'PlayerOperations.VALUE_EXCLUSIVE=nil',
    'UI.RequestPlayerOperation=nil', 'busy=true', 'religions=nil',
    'religions={{Religion=5,Founder=0,Beliefs={}}}',
])
def test_founding_checks_all_targets_and_native_eligibility_before_first_submission(setup):
    conn = ReligionConn(); state = GameState(conn); conn.lua.execute('pantheon=1;' + setup)
    assert asyncio.run(state.found_religion(*RELIGION_ARGS)).startswith('Error: RELIGION_PLAN_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('args', [
    ('RELIGION_PANTHEON', 'BELIEF_WORK', 'BELIEF_PROPERTY'),
    ('RELIGION_CUSTOM', 'BELIEF_WORK', 'BELIEF_PROPERTY'),
    ('RELIGION_UNKNOWN', 'BELIEF_WORK', 'BELIEF_PROPERTY'),
    ('RELIGION_BUDDHISM', 'BELIEF_PROPERTY', 'BELIEF_WORK'),
    ('RELIGION_BUDDHISM', 'BELIEF_SPARK', 'BELIEF_PROPERTY'),
    ('RELIGION_BUDDHISM', 'BELIEF_WORK', 'BELIEF_WORK'),
    ('RELIGION_BUDDHISM', 'BELIEF_WORK', 'BELIEF_UNKNOWN'),
])
def test_wrong_religion_or_belief_class_never_submits(args):
    conn = ReligionConn(); state = GameState(conn); conn.lua.execute('pantheon=1')
    assert asyncio.run(state.found_religion(*args)).startswith('Error: RELIGION_PLAN_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup', [
    'created=5', 'pantheon=2', 'religions[1].Religion=5', 'religions[1].Founder=1',
    'religions[1].Beliefs={11}', 'religions[1].Beliefs={10,20}',
    'religions[1].Beliefs={10,10}', 'religions[1].Beliefs=nil',
])
def test_partial_plan_never_accepts_wrong_religion_or_unrequested_belief_set(setup):
    conn = ReligionConn(); state = GameState(conn); conn.lua.execute('pantheon=1')
    conn.lose_after = 2
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    conn.lua.execute(setup)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    assert conn.lua.eval('#submissions') == 2
    assert state.pending_action_kind == 'found_religion'


@pytest.mark.parametrize('setup', ['takenBeliefs[20]=true', 'tooMany[20]=true', 'earned=1', 'PlayerOperations.ADD_BELIEF=nil'])
def test_next_belief_eligibility_is_rechecked_without_replaying_completed_phases(setup):
    conn = ReligionConn(); state = GameState(conn); conn.lua.execute('pantheon=1')
    conn.lose_after = 2
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    conn.lua.execute(setup)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    assert conn.lua.eval('#submissions') == 2
    assert conn.lua.eval('religions[1].Beliefs[1]') == 10


@pytest.mark.parametrize('setup', [
    'canPantheon=false', 'canPantheon=nil', 'pantheon=2', 'takenPantheon[1]=true',
    'takenBeliefs[1]=true', 'tooMany[1]=true', 'PlayerOperations.FOUND_PANTHEON=nil',
    'PlayerOperations.VALUE_EXCLUSIVE=nil', 'UI.RequestPlayerOperation=nil', 'busy=true',
])
def test_pantheon_requires_native_eligibility_and_unclaimed_target(setup):
    conn = ReligionConn(); state = GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(state.choose_pantheon('BELIEF_SPARK')).startswith('Error: RELIGION_PLAN_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('belief', ['BELIEF_WORK', 'BELIEF_PROPERTY', 'BELIEF_UNKNOWN'])
def test_pantheon_rejects_other_belief_classes(belief):
    conn = ReligionConn(); state = GameState(conn)
    assert asyncio.run(state.choose_pantheon(belief)).startswith('Error: RELIGION_PLAN_REFUSED|')
    assert conn.lua.eval('#submissions') == 0


def test_pantheon_delay_does_not_accept_a_different_belief_or_repeat_the_cost():
    conn = ReligionConn(); state = GameState(conn); conn.lua.execute('deferredStages[1]=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.choose_pantheon('BELIEF_SPARK'))
    conn.lua.execute('pantheon=2')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.choose_pantheon('BELIEF_SPARK'))
    conn.lua.execute('applyReligion(submissions[1].op,submissions[1].params)')
    assert asyncio.run(state.choose_pantheon('BELIEF_SPARK')).startswith('PANTHEON_FOUNDED|')
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('faith') == 75


@pytest.mark.parametrize('stage', [1, 2])
def test_readonly_confirmation_and_different_intent_cannot_advance_any_ready_stage(stage):
    conn = ReligionConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute('pantheon=1'); conn.lose_after = stage
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.found_religion(ctx, *RELIGION_ARGS))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.confirm_pending_action(wait=False))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.found_religion(ctx, 'RELIGION_HINDUISM', 'BELIEF_WORK', 'BELIEF_PROPERTY'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion('RELIGION_BUDDHISM', 'BELIEF_CHORAL', 'BELIEF_PROPERTY'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion('RELIGION_BUDDHISM', 'BELIEF_WORK', 'BELIEF_STEWARDSHIP'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.recruit_great_person(42))
    assert conn.lua.eval('#submissions') == stage
    assert asyncio.run(server.found_religion(ctx, *RELIGION_ARGS)).startswith('RELIGION_FOUNDED|')
    assert conn.lua.eval('#submissions') == 3


@pytest.mark.parametrize('change,restore', [
    ('seed=123', 'seed=719665280'), ('turn=42', 'turn=41'),
    ('PlayerConfigurations[1]=PlayerConfigurations[0];Game.GetLocalPlayer=function() return 1 end', 'Game.GetLocalPlayer=function() return 0 end'),
])
def test_world_turn_and_owner_changes_prevent_continuation_even_after_return(change, restore):
    conn = ReligionConn(); state = GameState(conn); conn.lua.execute('pantheon=1'); conn.lose_after = 1
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    conn.lua.execute(restore)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    assert conn.lua.eval('#submissions') == 1


def test_world_change_between_ready_read_and_continuation_blocks_next_native_stage():
    class ChangeWorldConn(ReligionConn):
        switch_after_read = False

        async def execute_write(self, code):
            rows = await super().execute_write(code)
            if self.switch_after_read:
                self.switch_after_read = False
                self.lua.execute('seed=123')
            return rows
    conn = ChangeWorldConn(); state = GameState(conn); conn.lua.execute('pantheon=1'); conn.lose_after = 1
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    conn.switch_after_read = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    assert conn.lua.eval('#submissions') == 1
    conn.lua.execute('seed=719665280')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    assert conn.lua.eval('#submissions') == 1


def test_world_change_before_first_native_dispatch_is_zero_submission():
    class ChangeWorldConn(ReligionConn):
        calls = 0

        async def execute_write(self, code):
            self.calls += 1
            if self.calls == 2:
                self.lua.execute('seed=123')
            return await super().execute_write(code)
    conn = ChangeWorldConn(); state = GameState(conn); conn.lua.execute('pantheon=1')
    assert 'ACTION_SCOPE_NO_SUBMIT' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


def test_readback_error_does_not_allow_later_beliefs_until_successful_observation():
    class ReadErrorConn(ReligionConn):
        break_reads = True

        async def execute_write(self, code):
            if self.break_reads and self.lua.eval('#submissions') > 0:
                raise OSError('religion readback unavailable')
            return await super().execute_write(code)
    conn = ReadErrorConn(); state = GameState(conn); conn.lua.execute('pantheon=1')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    assert conn.lua.eval('#submissions') == 1
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.found_religion(*RELIGION_ARGS))
    conn.break_reads = False
    assert asyncio.run(state.found_religion(*RELIGION_ARGS)).startswith('RELIGION_FOUNDED|')
    assert conn.lua.eval('#submissions') == 3


@pytest.mark.parametrize('stage', [1, 2, 3])
def test_missing_acknowledgement_can_only_advance_after_real_phase_readback(stage):
    class EmptyReplyConn(ReligionConn):
        async def execute_write(self, code):
            before = self.lua.eval('#submissions')
            rows = await super().execute_write(code)
            if before < self.lua.eval('#submissions') == stage:
                return []
            return rows
    conn = EmptyReplyConn(); state = GameState(conn); conn.lua.execute('pantheon=1')
    assert asyncio.run(state.found_religion(*RELIGION_ARGS)).startswith('RELIGION_FOUNDED|')
    assert conn.lua.eval('#submissions') == 3


def test_pantheon_cancellation_retains_original_choice():
    conn = ReligionConn(); state = GameState(conn); conn.cancel_after = 1
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(state.choose_pantheon('BELIEF_SPARK'))
    assert state.pending_action_kind == 'choose_pantheon'
    assert asyncio.run(state.choose_pantheon('BELIEF_SPARK')).startswith('PANTHEON_FOUNDED|')
    assert conn.lua.eval('#submissions') == 1


def test_pantheon_native_throw_after_entry_keeps_gate_without_repeating_faith_cost():
    conn = ReligionConn(); state = GameState(conn); conn.lua.execute('throwStage=1')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.choose_pantheon('BELIEF_SPARK'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.choose_pantheon('BELIEF_SPARK'))
    assert conn.lua.eval('faith') == 75
    assert conn.lua.eval('#submissions') == 1


def test_existing_partial_religion_without_owned_receipt_is_not_silently_modified():
    conn = ReligionConn(); state = GameState(conn)
    conn.lua.execute('pantheon=1;created=4;religions={{Religion=4,Founder=0,Beliefs={10}}}')
    assert asyncio.run(state.found_religion(*RELIGION_ARGS)).startswith('Error: RELIGION_PLAN_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert conn.lua.eval('#religions[1].Beliefs') == 1


def test_stale_end_turn_notification_does_not_hide_exact_complete_religion():
    conn = ReligionConn(); state = GameState(conn); conn.lua.execute('''
pantheon=1
NotificationManager={GetFirstEndTurnBlocking=function() return 99 end}
EndTurnBlockingTypes={ENDTURN_BLOCKING_BELIEF=99}
''')
    assert asyncio.run(state.found_religion(*RELIGION_ARGS)).startswith('RELIGION_FOUNDED|')
    assert conn.lua.eval('#submissions') == 3
