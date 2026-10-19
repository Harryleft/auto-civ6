"""Public GP tools and GameState execute real Lua against the native engine seam."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import app_of, make_ctx
from test_unit_readiness import ReadyConn


class GreatPeopleConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply = False
        self.lua.execute('''
units={}
points,gold,faith=150,1000,700
allowed=true
recruitCost,rejectCost,goldCost,faithCost=100,20,400,250
current={{Individual=42,Class=3,Cost=100}}
passed={}
YieldTypes={GOLD=2,FAITH=5}
PlayerOperations={PARAM_GREAT_PERSON_INDIVIDUAL_TYPE='individual',PARAM_YIELD_TYPE='yield',
 RECRUIT_GREAT_PERSON=10,PATRONIZE_GREAT_PERSON=11,REJECT_GREAT_PERSON=12}
GameInfo.GreatPersonIndividuals={[42]={Name='Einstein'},[43]={Name='Next scientist'}}
Locale={Lookup=function(s) return s end}
function addGP(id,individual)
 local u={id=id,individual=individual,owner=0}
 u.GetID=function(self) return self.id end
 u.GetOwner=function(self) return self.owner end
 u.GetGreatPerson=function(self) return {GetIndividual=function() return self.individual end} end
 units[id]=u
end
function available(individual)
 for _,e in ipairs(current) do
  if e.Individual==individual then
   return not passed[individual] and (e.Claimant==nil or e.Claimant==-1 or e.Claimant==0)
  end
 end
 return false
end
gp={
 GetTimeline=function() return current end,
 CanRecruitPerson=function(_,owner,id) return allowed and available(id) and points>=recruitCost end,
 CanRejectPerson=function(_,owner,id) return allowed and available(id) and points>=recruitCost end,
 GetRejectCost=function(_,owner,id) return rejectCost end,
 CanPatronizePerson=function(_,owner,id,y) return allowed and available(id) and ((y==2 and gold>=goldCost) or (y==5 and faith>=faithCost)) end,
 GetPatronizeCost=function(_,owner,id,y) return y==2 and goldCost or faithCost end,
}
Game.GetGreatPeople=function() return gp end
Players[0].GetGreatPeoplePoints=function() return {GetPointsTotal=function(_,class) assert(class==3);return points end} end
Players[0].GetTreasury=function() return {GetGoldBalance=function() return gold end} end
Players[0].GetReligion=function() return {GetFaithBalance=function() return faith end} end
function applyPool(op,p)
 assert(p.individual==42)
 if op==12 then
  passed[p.individual]=true;points=points-rejectCost
 else
  if op==10 then points=points-recruitCost
  elseif p.yield==2 then gold=gold-goldCost
  else faith=faith-faithCost end
  addGP(262144,p.individual)
  current={{Individual=43,Class=3,Cost=200}}
 end
end
UI.RequestPlayerOperation=function(owner,op,p)
 assert(owner==0)
 table.insert(submissions,{op=op,params=p})
 if not deferred then applyPool(op,p) end
 if fail_request then error('native operation entered then failed') end
end
''')

    async def execute_write(self, code):
        before = self.lua.eval('#submissions')
        rows = await super().execute_write(code)
        if self.lose_reply and self.lua.eval('#submissions') > before:
            self.lose_reply = False
            raise OSError('native GP operation ran; response lost')
        return rows


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def test_t73_local_timeline_candidate_can_recruit_with_native_eligibility():
    conn = GreatPeopleConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute('''
points=82.296875
current={{Individual=133,Class=5,Cost=80,Claimant=0}}
GameInfo.GreatPersonIndividuals[133]={Name='Omar Khayyam'}
gp.CanRecruitPerson=function(_,owner,id) return owner==0 and id==133 end
Players[0].GetGreatPeoplePoints=function()
 return {GetPointsTotal=function(_,class) assert(class==5);return points end}
end
function applyPool(op,p)
 assert(op==10 and p.individual==133)
 points=points-80
 addGP(262144,133)
 current={{Individual=134,Class=5,Cost=120}}
end
''')
    result = asyncio.run(server.recruit_great_person(ctx, 133))
    assert result.startswith('RECRUITED|Omar Khayyam|individual:133|gp_points_cost:80|unit:262144')
    assert conn.lua.eval('points') == 2.296875
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


def test_lost_patronage_reply_repeated_public_call_only_confirms_original_person():
    conn = GreatPeopleConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.patronize_great_person(ctx, 42))
    assert state.pending_action_kind == 'patronize_great_person'
    result = asyncio.run(server.patronize_great_person(ctx, 42))
    assert result.startswith('PATRONIZED|Einstein|')
    assert 'cost:400 gold' in result
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('gold') == 600
    assert state.pending_action_summary is None


def test_delayed_recruitment_needs_points_and_new_unit_of_exact_individual():
    conn = GreatPeopleConn(); state = GameState(conn)
    conn.lua.execute('addGP(196608,42);deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.recruit_great_person(42))
    conn.lua.execute('points=50;addGP(262144,43)')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.recruit_great_person(42))
    conn.lua.execute('units[262144].individual=42')
    result = asyncio.run(state.recruit_great_person(42))
    assert result.startswith('RECRUITED|Einstein|')
    assert 'gp_points_cost:100' in result
    assert 'unit:262144' in result
    assert conn.lua.eval('#submissions') == 1


def test_delayed_rejection_spends_class_points_without_advancing_or_rejecting_next_candidate():
    conn = GreatPeopleConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.reject_great_person(ctx, 42))
    conn.lua.execute('applyPool(submissions[1].op,submissions[1].params)')
    result = asyncio.run(server.reject_great_person(ctx, 42))
    assert result.startswith('REJECTED|Einstein|')
    assert 'gp_points_cost:20' in result
    assert conn.lua.eval('points') == 130
    assert conn.lua.eval('faith') == 700
    assert conn.lua.eval('current[1].Individual') == 42
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('method,currency,prefix,resource,remaining', [
    ('recruit_great_person', None, 'RECRUITED|', 'points', 50),
    ('patronize_great_person', 'YIELD_GOLD', 'PATRONIZED|', 'gold', 600),
    ('patronize_great_person', 'YIELD_FAITH', 'PATRONIZED|', 'faith', 450),
    ('reject_great_person', None, 'REJECTED|', 'points', 130),
])
@pytest.mark.parametrize('claimant', [None, -1, 0])
def test_normal_public_actions_confirm_native_resource_and_original_individual(method, currency, prefix, resource, remaining, claimant):
    conn = GreatPeopleConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    if claimant is not None:
        conn.lua.execute(f'current[1].Claimant={claimant}')
    args = (42,) if currency is None else (42, currency)
    result = asyncio.run(getattr(server, method)(ctx, *args))
    assert result.startswith(prefix)
    assert 'individual:42' in result
    assert conn.lua.eval(resource) == remaining
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('submissions[1].params.individual') == 42
    assert state.pending_action_summary is None


@pytest.mark.parametrize('method', ['recruit_great_person', 'patronize_great_person', 'reject_great_person'])
def test_lost_and_cancelled_request_keeps_original_pool_intent(method):
    class CancelConn(GreatPeopleConn):
        async def execute_write(self, code):
            before = self.lua.eval('#submissions')
            rows = await super().execute_write(code)
            if self.lua.eval('#submissions') > before:
                raise asyncio.CancelledError()
            return rows
    for conn in (GreatPeopleConn(), CancelConn()):
        state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
        conn.lua.execute('current[1].Claimant=0')
        if type(conn) is GreatPeopleConn:
            conn.lose_reply = True
            assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(server, method)(ctx, 42))
        else:
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(getattr(server, method)(ctx, 42))
        assert state.pending_action_kind == method
        assert 'OUTCOME_UNKNOWN' not in asyncio.run(getattr(server, method)(ctx, 42))
        assert conn.lua.eval('#submissions') == 1
        assert state.pending_action_summary is None


@pytest.mark.parametrize('currency,cost', [('YIELD_GOLD', '400 gold'), ('YIELD_FAITH', '250 faith')])
def test_patronage_uses_captured_cost_when_next_candidate_has_a_different_price(currency, cost):
    conn = GreatPeopleConn(); state = GameState(conn)
    conn.lua.execute('''
local original=UI.RequestPlayerOperation
UI.RequestPlayerOperation=function(owner,op,p)
 original(owner,op,p)
 goldCost=999;faithCost=999
end
''')
    result = asyncio.run(state.patronize_great_person(42, currency))
    assert result.startswith('PATRONIZED|')
    assert 'cost:' + cost in result
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('method', ['recruit_great_person', 'patronize_great_person', 'reject_great_person'])
def test_deferred_request_repeated_before_completion_never_resubmits(method):
    conn = GreatPeopleConn(); state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state, method)(42))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state, method)(42))
    conn.lua.execute('applyPool(submissions[1].op,submissions[1].params)')
    assert 'OUTCOME_UNKNOWN' not in asyncio.run(getattr(state, method)(42))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('setup', [
    'allowed=false', 'allowed=nil', 'busy=true', 'current={}',
    'current[1].Claimant=1', 'current[1].Class=nil',
    'current[2]=current[1]', 'UI.RequestPlayerOperation=nil',
])
@pytest.mark.parametrize('method', ['recruit_great_person', 'patronize_great_person', 'reject_great_person'])
def test_native_eligibility_or_identity_refusal_proves_zero_submission(method, setup):
    conn = GreatPeopleConn(); state = GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(getattr(state, method)(42)).startswith('Error: GP_POOL_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('method,native_method', [
    ('recruit_great_person', 'CanRecruitPerson'),
    ('reject_great_person', 'CanRejectPerson'),
    ('patronize_great_person', 'CanPatronizePerson'),
])
@pytest.mark.parametrize('result', ['false', 'nil', '1', '"true"'])
def test_only_requested_native_operation_can_authorize_local_candidate(method, native_method, result):
    conn = GreatPeopleConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute(f'''
current[1].Claimant=0
gp.CanRecruitPerson=function() return true end
gp.CanRejectPerson=function() return true end
gp.CanPatronizePerson=function() return true end
gp.{native_method}=function() return {result} end
''')
    assert asyncio.run(getattr(server, method)(ctx, 42)).startswith('Error: GP_POOL_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('currency,denied_yield', [('YIELD_GOLD', 2), ('YIELD_FAITH', 5)])
def test_patronage_requires_native_permission_for_requested_currency(currency, denied_yield):
    conn = GreatPeopleConn(); state = GameState(conn)
    conn.lua.execute(f'''
current[1].Claimant=0
gp.CanPatronizePerson=function(_,owner,id,yield) return yield~={denied_yield} end
''')
    assert asyncio.run(state.patronize_great_person(42, currency)).startswith('Error: GP_POOL_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('method,setup', [
    ('recruit_great_person', 'current[1].Cost=nil'),
    ('recruit_great_person', 'points=99'),
    ('recruit_great_person', 'current[1].Cost=200'),
    ('patronize_great_person', 'gold=399'),
    ('patronize_great_person', 'goldCost=nil'),
    ('patronize_great_person', 'YieldTypes.GOLD=nil'),
    ('reject_great_person', 'rejectCost=nil'),
    ('reject_great_person', 'rejectCost=200'),
])
def test_unknown_or_insufficient_native_cost_does_not_submit(method, setup):
    conn = GreatPeopleConn(); state = GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(getattr(state, method)(42)).startswith('Error: GP_POOL_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('method,change', [
    ('recruit_great_person', 'points=150'),
    ('recruit_great_person', 'points=49'),
    ('patronize_great_person', 'gold=1000'),
    ('patronize_great_person', 'gold=599'),
    ('reject_great_person', 'points=150;faith=680'),
    ('reject_great_person', 'passed[42]=nil'),
    ('reject_great_person', 'gp.CanRejectPerson=function() return nil end'),
    ('reject_great_person', 'addGP(262144,42)'),
])
def test_resource_or_original_result_mismatch_keeps_pending(method, change):
    conn = GreatPeopleConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state, method)(42))
    conn.lua.execute('applyPool(submissions[1].op,submissions[1].params);' + change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state, method)(42))
    assert state.pending_action_kind == method
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('change', [
    'units[262144]=nil', 'units[262144].individual=43', 'units[262144].owner=1',
    'units[262144].id=196608', 'addGP(327680,42)',
])
def test_patronage_requires_one_new_owned_unit_with_original_full_identity(change):
    conn = GreatPeopleConn(); state = GameState(conn)
    conn.lua.execute('addGP(196608,42);deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.patronize_great_person(42))
    conn.lua.execute('applyPool(submissions[1].op,submissions[1].params);' + change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.patronize_great_person(42))
    assert conn.lua.eval('#submissions') == 1


def test_reject_reentry_after_pool_changes_never_processes_new_candidate():
    conn = GreatPeopleConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.reject_great_person(ctx, 42))
    conn.lua.execute('current={{Individual=43,Class=3,Cost=100}}')
    assert asyncio.run(server.reject_great_person(ctx, 42)).startswith('REJECTED|Einstein|')
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('submissions[1].params.individual') == 42
    assert conn.lua.eval('passed[43]') is None


@pytest.mark.parametrize('method', ['recruit_great_person', 'patronize_great_person', 'reject_great_person'])
def test_native_throw_after_effect_is_unknown_and_never_replayed(method):
    conn = GreatPeopleConn(); state = GameState(conn); conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state, method)(42))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state, method)(42))
    assert state.pending_action_kind == method
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('change,restore', [
    ('seed=123', 'seed=719665280'), ('turn=42', 'turn=41'),
    ('PlayerConfigurations[1]=PlayerConfigurations[0];Game.GetLocalPlayer=function() return 1 end', 'Game.GetLocalPlayer=function() return 0 end'),
])
def test_world_turn_and_owner_change_permanently_prevent_old_request_confirmation(change, restore):
    conn = GreatPeopleConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.patronize_great_person(42))
    conn.lua.execute('applyPool(submissions[1].op,submissions[1].params);' + change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.patronize_great_person(42))
    conn.lua.execute(restore)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.patronize_great_person(42))
    assert conn.lua.eval('#submissions') == 1


def test_world_changes_between_scope_read_and_dispatch_prove_zero_submission():
    class ChangeWorldConn(GreatPeopleConn):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def execute_write(self, code):
            self.calls += 1
            if self.calls == 2:
                self.lua.execute('seed=123')
            return await super().execute_write(code)
    conn = ChangeWorldConn(); state = GameState(conn)
    assert 'ACTION_SCOPE_NO_SUBMIT' in asyncio.run(state.recruit_great_person(42))
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


def test_confirmation_read_error_retains_pending_and_later_read_can_confirm():
    class ReadErrorConn(GreatPeopleConn):
        def __init__(self):
            super().__init__()
            self.break_read = True

        async def execute_write(self, code):
            if self.break_read and self.lua.eval('#submissions') > 0:
                raise OSError('read unavailable')
            return await super().execute_write(code)
    conn = ReadErrorConn(); state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.patronize_great_person(42))
    conn.break_read = False
    assert asyncio.run(state.patronize_great_person(42)).startswith('PATRONIZED|')
    assert conn.lua.eval('#submissions') == 1


def test_other_individual_currency_and_mutation_wait_for_original_request():
    conn = GreatPeopleConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.patronize_great_person(ctx, 42))
    for method, args in [
        (state.patronize_great_person, (43,)),
        (state.patronize_great_person, (42, 'YIELD_FAITH')),
        (state.recruit_great_person, (43,)),
        (state.reject_great_person, (42,)),
        (state.purchase_tile, (65537, 10, 10)),
        (state.end_turn, ()),
    ]:
        assert 'OUTCOME_UNKNOWN' in asyncio.run(method(*args))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.reject_great_person(ctx, 42))
    assert state.pending_action_kind == 'patronize_great_person'
    assert conn.lua.eval('#submissions') == 1
    conn.lua.execute('applyPool(submissions[1].op,submissions[1].params)')
    assert asyncio.run(server.patronize_great_person(ctx, 42)).startswith('PATRONIZED|')
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('id,currency', [(-1, 'YIELD_GOLD'), (True, 'YIELD_GOLD'), (42, 'YIELD_SCIENCE')])
def test_invalid_public_intent_never_submits(id, currency):
    conn = GreatPeopleConn(); state = GameState(conn)
    assert asyncio.run(state.patronize_great_person(id, currency)).startswith('Error: INVALID_')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


def test_empty_native_reply_retains_request_until_real_result_is_read():
    class EmptyReplyConn(GreatPeopleConn):
        async def execute_write(self, code):
            before = self.lua.eval('#submissions')
            rows = await super().execute_write(code)
            if self.lua.eval('#submissions') > before:
                return []
            return rows
    conn = EmptyReplyConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.patronize_great_person(42))
    conn.lua.execute('applyPool(submissions[1].op,submissions[1].params)')
    assert asyncio.run(state.patronize_great_person(42)).startswith('PATRONIZED|')
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('method,expected', [
    ('recruit_great_person', 'gp_points_cost:100'),
    ('reject_great_person', 'gp_points_cost:20'),
])
def test_point_cost_is_the_original_quote_not_the_next_pool_quote(method, expected):
    conn = GreatPeopleConn(); state = GameState(conn)
    conn.lua.execute('''
local original=UI.RequestPlayerOperation
UI.RequestPlayerOperation=function(owner,op,p)
 original(owner,op,p)
 current[1].Cost=999;rejectCost=999
end
''')
    result = asyncio.run(getattr(state, method)(42))
    assert expected in result
    assert 'OUTCOME_UNKNOWN' not in result
    assert conn.lua.eval('#submissions') == 1
