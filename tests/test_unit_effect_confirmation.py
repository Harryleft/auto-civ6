"""Public unit effects execute real Lua; only native engine and transport are simulated."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import app_of, make_ctx
from test_unit_readiness import ReadyConn


class EffectConn(ReadyConn):
    def __init__(self, kind='great_person'):
        super().__init__()
        self.lose_reply = False
        self.lua.execute('''
function event()
 local handlers={}
 return {Add=function(f) handlers[f]=true end,Remove=function(f) handlers[f]=nil end,
  Fire=function(...) for f in pairs(handlers) do f(...) end end,
  Count=function() local n=0;for _ in pairs(handlers) do n=n+1 end;return n end}
end
Events={UnitGreatPersonActivated=event(),UnitRemovedFromMap=event(),UnitOperationStarted=event()}
u=units[196608];u.kind=1;u.individual=42;u.class=3;u.charges=2;u.spreads=2;u.religion=4;u.owner=0
u.x=19;u.y=17
u.GetX=function(self) return self.x end
u.GetY=function(self) return self.y end
u.GetOwner=function(self) return self.owner end
u.GetType=function(self) return self.kind end
u.GetName=function() return 'Test unit' end
u.GetSpreadCharges=function(self) return self.spreads end
u.GetReligionType=function(self) return self.religion end
u.GetGreatPerson=function(self) return {GetIndividual=function() return self.individual end,
 GetClass=function() return self.class end,GetActionCharges=function() return self.charges end} end
Players[0].GetUnits=function() return {Members=function() return pairs(units) end,
 FindID=function(_,id) return units[id] end} end
GameInfo.Units={[1]={UnitType='UNIT_GREAT_SCIENTIST'},[2]={UnitType='UNIT_GREAT_PROPHET'},[3]={UnitType='UNIT_MISSIONARY'}}
GameInfo.UnitCommands={UNITCOMMAND_ACTIVATE_GREAT_PERSON={Hash=30}}
GameInfo.UnitOperations.UNITOPERATION_FOUND_RELIGION={Hash=31}
GameInfo.UnitOperations.UNITOPERATION_SPREAD_RELIGION={Hash=32}
UnitOperationTypes={PARAM_X='x',PARAM_Y='y'}
UnitOperationResults={PLOTS='plots',MODIFIERS='modifiers',MODIFIER_IS_TARGET=1}
Locale={Lookup=function(s) return s end}
city={id=65539,owner=1,x=20,y=17,pressure=100,majority=2}
city.GetID=function(self) return self.id end
city.GetOwner=function(self) return self.owner end
city.GetX=function(self) return self.x end
city.GetY=function(self) return self.y end
city.GetReligion=function(self) return {GetTotalPressureOnCity=function(_,religion) assert(religion==4);return self.pressure end,
 GetMajorityReligion=function() return self.majority end} end
CityManager={GetCityAt=function(x,y) if city and x==city.x and y==city.y then return city end end}
Map={GetPlotByIndex=function(id) if id==300 then return {GetX=function() return 20 end,GetY=function() return 17 end} end end}
targets={plots={300},modifiers={1}}
UnitManager.GetOperationTargets=function(unit,op) assert(unit.id==196608 and op==32);return targets end
beliefs,createdReligion,hasProphet=0,-1,true
Players[0].GetReligion=function() return {GetNumBeliefsEarned=function() return beliefs end,
 GetReligionTypeCreated=function() return createdReligion end,HasReligiousFoundingUnit=function() return hasProphet end} end
UnitManager.CanStartCommand=function(unit,op,visible,results) assert(visible==nil or visible==false);return unit.allowed end
UnitManager.CanStartOperation=function(unit,op,params,targets,results) return unit.allowed end
function removeUnit()
 units[196608]=nil
 Events.UnitRemovedFromMap.Fire(0,196608)
 -- Native consumed userdata is invalid; callbacks/readback must use numeric IDs.
 for k,v in pairs(u) do if type(v)=='function' then u[k]=function() error('destroyed unit userdata accessed') end end end
end
function applyEffect(op)
 if op==30 then
  u.charges=u.charges-1
  Events.UnitGreatPersonActivated.Fire(0,196608,u.class,u.individual)
  if u.charges==0 then removeUnit() end
 elseif op==31 then
  Events.UnitOperationStarted.Fire(0,196608,31)
  beliefs=2;hasProphet=false;removeUnit()
 else
  Events.UnitOperationStarted.Fire(0,196608,32)
  u.spreads=u.spreads-1;city.pressure=300
  if u.spreads==0 then removeUnit() end
 end
end
function request(unit,op,p)
 table.insert(submissions,{id=unit.id,op=op,params=p})
 if not deferred then applyEffect(op) end
 if fail_request then error('native operation entered then failed') end
end
UnitManager.RequestCommand=request
UnitManager.RequestOperation=request
''')
        if kind == 'prophet':
            self.lua.execute('u.kind=2;u.class=7;u.charges=0')
        elif kind == 'spread':
            self.lua.execute('u.kind=3')

    async def execute_write(self, code):
        before = self.lua.eval('#submissions')
        rows = await super().execute_write(code)
        if self.lose_reply and self.lua.eval('#submissions') > before:
            self.lose_reply = False
            raise OSError('native effect applied; reply lost')
        return rows


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def test_lost_activation_reply_repeated_public_call_only_confirms_original_charge():
    conn = EffectConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    result = asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    assert result.startswith('GP_ACTIVATED|unit:196608|individual:42|')
    assert 'charges_remaining:1' in result
    assert 'rewards not measured' in result
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('u.charges') == 1
    assert state.pending_action_summary is None


def test_prophet_with_zero_action_charges_confirms_readiness_without_claiming_religion_founded():
    conn = EffectConn('prophet'); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    assert conn.lua.eval('submissions[1].op') == 31
    conn.lua.execute('applyEffect(31)')
    result = asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    assert result.startswith('RELIGION_FOUNDING_READY|unit:196608|individual:42|')
    assert 'beliefs_earned:0->2' in result
    assert conn.lua.eval('createdReligion') == -1
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


def test_spread_confirms_exact_city_pressure_without_a_majority_change():
    conn = EffectConn('spread'); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'spread_religion'))
    assert conn.lua.eval('submissions[1].params.x') == 20
    assert conn.lua.eval('submissions[1].params.y') == 17
    conn.lua.execute('applyEffect(32)')
    result = asyncio.run(server.unit_action(ctx, 196608, 'spread_religion'))
    assert result.startswith('RELIGION_SPREAD|unit:196608|religion:4|')
    assert 'charges_remaining:1' in result
    assert 'city:1:65539' in result
    assert 'pressure:100->300' in result
    assert conn.lua.eval('city.majority') == 2
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


CASES = [
    ('great_person', 'activate', 'activate_great_person', 30, 'GP_ACTIVATED|'),
    ('prophet', 'activate', 'activate_great_person', 31, 'RELIGION_FOUNDING_READY|'),
    ('spread', 'spread_religion', 'spread_religion', 32, 'RELIGION_SPREAD|'),
]


@pytest.mark.parametrize('kind,action,method,op,prefix', CASES)
def test_normal_public_effect_has_exact_native_submission_and_completion(kind, action, method, op, prefix):
    conn = EffectConn(kind); state = GameState(conn)
    result = asyncio.run(getattr(state, method)(196608))
    assert result.startswith(prefix)
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('submissions[1].id') == 196608
    assert conn.lua.eval('submissions[1].op') == op
    assert state.pending_action_summary is None
    assert conn.lua.eval('Events.UnitRemovedFromMap.Count()') == 0
    assert conn.lua.eval('Events.UnitGreatPersonActivated.Count()') == 0
    assert conn.lua.eval('Events.UnitOperationStarted.Count()') == 0


@pytest.mark.parametrize('kind,action,method,op,prefix', CASES)
def test_delayed_effect_and_same_intent_mcp_reentry_never_replays(kind, action, method, op, prefix):
    conn = EffectConn(kind); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, action))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, action))
    conn.lua.execute(f'applyEffect({op})')
    assert asyncio.run(server.unit_action(ctx, 196608, action)).startswith(prefix)
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('kind,action,method,op,prefix', CASES)
def test_lost_and_cancelled_effect_preserve_original_request(kind, action, method, op, prefix):
    class CancelConn(EffectConn):
        async def execute_write(self, code):
            before = self.lua.eval('#submissions')
            rows = await super().execute_write(code)
            if self.lua.eval('#submissions') > before:
                raise asyncio.CancelledError()
            return rows
    for conn in (EffectConn(kind), CancelConn(kind)):
        state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
        if type(conn) is EffectConn:
            conn.lose_reply = True
            assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, action))
        else:
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(server.unit_action(ctx, 196608, action))
        assert state.pending_action_kind == method
        assert asyncio.run(server.unit_action(ctx, 196608, action)).startswith(prefix)
        assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('kind,method,charges', [
    ('great_person', 'activate_great_person', 'u.charges=1'),
    ('spread', 'spread_religion', 'u.spreads=1'),
])
def test_last_charge_confirms_exact_removal_without_accessing_destroyed_userdata(kind, method, charges):
    conn = EffectConn(kind); state = GameState(conn); conn.lua.execute(charges)
    result = asyncio.run(getattr(state, method)(196608))
    assert 'charges_remaining:0' in result
    assert conn.lua.eval('units[196608]') is None
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('event', [
    'Events.UnitGreatPersonActivated.Fire(0,196609,3,42)',
    'Events.UnitGreatPersonActivated.Fire(1,196608,3,42)',
    'Events.UnitGreatPersonActivated.Fire(0,196608,4,42)',
    'Events.UnitGreatPersonActivated.Fire(0,196608,3,43)',
])
def test_other_great_person_activation_event_does_not_confirm_original(event):
    conn = EffectConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    conn.lua.execute('u.charges=1;' + event)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('setup', [
    'u.charges=2', 'u.charges=0', 'u.individual=43', 'u.class=4', 'u.owner=1',
    'u.id=262144', 'u.x=20', 'u.kind=2',
])
def test_activation_event_requires_original_identity_position_and_actual_charge_delta(setup):
    conn = EffectConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    conn.lua.execute('applyEffect(30);' + setup)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('kind,action,method,op,prefix', CASES)
@pytest.mark.parametrize('setup', [
    'u.allowed=false', 'u.allowed=nil', 'busy=true', 'u.id=262144', 'u.owner=1',
    'Events.UnitRemovedFromMap=nil',
])
def test_native_eligibility_and_identity_refusal_mean_zero_submission(kind, action, method, op, prefix, setup):
    conn = EffectConn(kind); state = GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(getattr(state, method)(196608)).startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('kind,action,method,op,prefix', CASES)
def test_mcp_keeps_composite_unit_identity(kind, action, method, op, prefix):
    conn = EffectConn(kind); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    assert 'UNIT_ID_CHANGED' in asyncio.run(server.unit_action(ctx, 262144, action))
    assert conn.lua.eval('#submissions') == 0


@pytest.mark.parametrize('kind,method,setup', [
    ('great_person', 'activate_great_person', 'u.charges=0'),
    ('great_person', 'activate_great_person', 'u.individual=nil'),
    ('great_person', 'activate_great_person', 'Events.UnitGreatPersonActivated=nil'),
    ('great_person', 'activate_great_person', 'UnitManager.RequestCommand=nil'),
    ('prophet', 'activate_great_person', 'createdReligion=4'),
    ('prophet', 'activate_great_person', 'hasProphet=false'),
    ('prophet', 'activate_great_person', 'beliefs=nil'),
    ('prophet', 'activate_great_person', 'u.moves=0'),
    ('spread', 'spread_religion', 'u.spreads=0'),
    ('spread', 'spread_religion', 'u.religion=0'),
    ('spread', 'spread_religion', 'city.pressure=nil'),
    ('spread', 'spread_religion', 'u.moves=0'),
    ('spread', 'spread_religion', 'UnitManager.RequestOperation=nil'),
    ('spread', 'spread_religion', 'Events.UnitOperationStarted=nil'),
])
def test_missing_required_native_resource_or_observer_refuses_before_submission(kind, method, setup):
    conn = EffectConn(kind); state = GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(getattr(state, method)(196608)).startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup', [
    'city.pressure=100;city.majority=4', 'u.spreads=2', 'u.religion=5',
    'city.id=131075', 'city.owner=2', 'city.x=21', 'city=nil',
])
def test_spread_requires_original_religion_city_pressure_and_charge_result(setup):
    conn = EffectConn('spread'); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(196608))
    conn.lua.execute('applyEffect(32);' + setup)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(196608))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('setup', [
    'targets=nil', 'targets={}', 'targets.modifiers={0}', 'targets.plots={999}',
    'UnitManager.GetOperationTargets=nil', 'city=nil',
    'targets.plots={300,300};targets.modifiers={1,1}',
])
def test_spread_never_guesses_an_unknown_or_ambiguous_adjacent_city(setup):
    conn = EffectConn('spread'); state = GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(state.spread_religion(196608)).startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


def test_spread_on_actual_city_plot_does_not_require_guessing_adjacent_targets():
    conn = EffectConn('spread'); state = GameState(conn)
    conn.lua.execute('u.x=20;UnitManager.GetOperationTargets=nil')
    result = asyncio.run(state.spread_religion(196608))
    assert result.startswith('RELIGION_SPREAD|')
    assert 'city:1:65539 pressure:100->300' in result
    assert conn.lua.eval('submissions[1].params.x') == 20


@pytest.mark.parametrize('setup', ['beliefs=0', 'hasProphet=true', 'createdReligion=4'])
def test_prophet_operation_without_original_founding_readiness_remains_unknown(setup):
    conn = EffectConn('prophet'); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    conn.lua.execute('applyEffect(31);' + setup)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    assert conn.lua.eval('#submissions') == 1


def test_prophet_readiness_is_measured_even_if_native_unit_remains_until_religion_selection():
    conn = EffectConn('prophet'); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    conn.lua.execute('Events.UnitOperationStarted.Fire(0,196608,31);beliefs=2;hasProphet=false')
    assert asyncio.run(state.activate_great_person(196608)).startswith('RELIGION_FOUNDING_READY|')
    assert conn.lua.eval('u.charges') == 0
    assert conn.lua.eval('units[196608] ~= nil') is True
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('kind,method,setup', [
    ('great_person', 'activate_great_person', 'u.charges=1'),
    ('spread', 'spread_religion', 'u.spreads=1;city.pressure=300'),
    ('prophet', 'activate_great_person', 'beliefs=2;hasProphet=false'),
])
def test_charge_or_ready_state_without_exact_native_event_does_not_confirm(kind, method, setup):
    conn = EffectConn(kind); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state, method)(196608))
    conn.lua.execute(setup)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state, method)(196608))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('setup', [
    'units[196608]=nil;Events.UnitGreatPersonActivated.Fire(0,196608,3,42)',
    'units[196608]=nil;Events.UnitRemovedFromMap.Fire(0,196608)',
    'units[196608]=nil;Events.UnitRemovedFromMap.Fire(0,262144);Events.UnitGreatPersonActivated.Fire(0,196608,3,42)',
])
def test_last_charge_absence_needs_both_exact_activation_and_exact_removal_evidence(setup):
    conn = EffectConn(); state = GameState(conn); conn.lua.execute('u.charges=1;deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    conn.lua.execute(setup)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('kind,action,method,op,prefix', CASES)
def test_native_throw_after_effect_is_unknown_and_never_replayed(kind, action, method, op, prefix):
    conn = EffectConn(kind); state = GameState(conn); conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state, method)(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state, method)(196608))
    assert state.pending_action_kind == method
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('change,restore', [
    ('seed=123', 'seed=719665280'), ('turn=42', 'turn=41'),
    ('PlayerConfigurations[1]=PlayerConfigurations[0];Game.GetLocalPlayer=function() return 1 end', 'Game.GetLocalPlayer=function() return 0 end'),
])
def test_changed_world_turn_or_owner_cannot_confirm_even_after_returning(change, restore):
    conn = EffectConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    conn.lua.execute('applyEffect(30);' + change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    conn.lua.execute(restore)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    assert conn.lua.eval('#submissions') == 1


def test_world_change_in_native_callback_is_sticky_even_before_confirmation_read():
    conn = EffectConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    conn.lua.execute('seed=123;applyEffect(30);seed=719665280')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    assert conn.lua.eval('#submissions') == 1


def test_pending_effect_blocks_other_public_mutations_without_losing_original_intent():
    conn = EffectConn(); state = GameState(conn); ctx = make_ctx(); app_of(ctx).game = state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'activate'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'spread_religion'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196609))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.recruit_great_person(42))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    conn.lua.execute('applyEffect(30)')
    assert asyncio.run(server.unit_action(ctx, 196608, 'activate')).startswith('GP_ACTIVATED|')
    assert conn.lua.eval('#submissions') == 1


def test_malformed_native_receipt_storage_refuses_before_submission():
    conn = EffectConn(); state = GameState(conn); conn.lua.execute('__civmcpUnitEffect=5')
    assert asyncio.run(state.activate_great_person(196608)).startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


def test_observer_installation_failure_is_zero_submission_and_cleans_partial_listeners():
    conn = EffectConn(); state = GameState(conn)
    conn.lua.execute("Events.UnitGreatPersonActivated.Add=function() error('cannot subscribe') end")
    assert asyncio.run(state.activate_great_person(196608)).startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert conn.lua.eval('Events.UnitRemovedFromMap.Count()') == 0
    assert state.pending_action_summary is None


def test_new_independent_activation_after_confirmation_consumes_only_the_next_charge():
    conn = EffectConn(); state = GameState(conn)
    assert 'charges_remaining:1' in asyncio.run(state.activate_great_person(196608))
    assert 'charges_remaining:0' in asyncio.run(state.activate_great_person(196608))
    assert conn.lua.eval('#submissions') == 2
    assert conn.lua.eval('Events.UnitGreatPersonActivated.Count()') == 0
    assert state.pending_action_summary is None


def test_readback_transport_failure_keeps_observer_and_later_confirms_consumed_unit():
    class ReadErrorConn(EffectConn):
        def __init__(self):
            super().__init__()
            self.break_reads = True

        async def execute_write(self, code):
            if self.break_reads and self.lua.eval('#submissions') > 0:
                raise OSError('readback transport unavailable')
            return await super().execute_write(code)
    conn = ReadErrorConn(); state = GameState(conn); conn.lua.execute('u.charges=1')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.activate_great_person(196608))
    assert conn.lua.eval('units[196608]') is None
    assert conn.lua.eval('Events.UnitGreatPersonActivated.Count()') == 1
    conn.break_reads = False
    assert 'charges_remaining:0' in asyncio.run(state.activate_great_person(196608))
    assert conn.lua.eval('Events.UnitGreatPersonActivated.Count()') == 0
    assert conn.lua.eval('#submissions') == 1
