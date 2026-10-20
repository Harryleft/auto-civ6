"""Remove heresy through public MCP/GameState and executed native Lua adapters."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from test_unit_effect_confirmation import EffectConn


class PurgeConn(EffectConn):
    def __init__(self):
        super().__init__('spread')
        self.lua.execute('''
u.kind=5;u.religion=12;u.spreads=3;u.x=35;u.y=22
GameInfo.Units[5]={UnitType='UNIT_INQUISITOR'}
GameInfo.UnitOperations.UNITOPERATION_REMOVE_HERESY={OperationType='UNITOPERATION_REMOVE_HERESY',Hash=77}
OperationResultsTypes={NO_TARGETS=0}
-- First-party constants observed by native T142 request #101.
UnitOperationResults={PLOTS=1933683541,MODIFIERS=628984671,MODIFIER_IS_TARGET=889110605,
 ADDITIONAL_DESCRIPTION=-1554772595,FAILURE_REASONS=-2028665063}
targets={[1933683541]={300},[628984671]={889110605}}
city.owner=0;city.x=35;city.y=22
religionRows={{Religion=-1,Pressure=40},{Religion=8,Pressure=400},{Religion=9,Pressure=200},{Religion=12,Pressure=100}}
city.GetReligion=function() return {GetReligionsInCity=function() return religionRows end} end
Map.GetPlotByIndex=function(id)
 if id==300 then return {GetX=function() return 35 end,GetY=function() return 22 end} end
end
UnitManager.GetOperationTargets=function(actor,op) assert(actor==u and op==77);return targets end
UnitManager.CanStartOperation=function(actor,op,unused,params,resultType)
 assert(actor==u and op==77 and unused==nil and params==false and resultType==0)
 return u.allowed
end
function applyPurge()
 Events.UnitOperationStarted.Fire(0,196608,77)
 u.spreads=u.spreads-1
 religionRows={{Religion=-1,Pressure=40},{Religion=8,Pressure=120},{Religion=9,Pressure=60},{Religion=12,Pressure=100}}
 if u.spreads==0 then removeUnit() end
end
UnitManager.RequestOperation=function(actor,op,...)
 assert(actor==u and op==77 and select('#',...)==0)
 table.insert(submissions,{id=actor.id,op=op})
 if not deferred then applyPurge() end
 if fail_request then error('native purge entered then failed') end
end
''')


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def test_public_remove_heresy_binds_native_owned_city_and_all_foreign_pressure_stocks():
    conn = PurgeConn(); state = GameState(conn)
    result = asyncio.run(server.unit_action(make_ctx(game=state), 196608, 'remove_heresy'))
    assert result.startswith('HERESY_REMOVED|unit:196608|religion:12|charges_remaining:2|city:0:65539|')
    assert '8:400->120' in result and '9:200->60' in result
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('change,restore', [
    ('u.religion=8', 'u.religion=12'),
    ('u.kind=3', 'u.kind=5'),
    ('u.id=262144', 'u.id=196608'),
    ('u.owner=1', 'u.owner=0'),
    ('u.x=36', 'u.x=35'),
    ('city.owner=1', 'city.owner=0'),
    ('city.id=131075', 'city.id=65539'),
    ('city.GetX=function() return 36 end', 'city.GetX=function() return 35 end'),
    ('seed=9', 'seed=719665280'),
    ('turn=42', 'turn=41'),
])
def test_observed_actor_city_or_world_change_stays_invalid_after_restoration(change, restore):
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    conn.lua.execute(restore + ';applyPurge()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    assert conn.lua.eval('#submissions') == 1


def test_known_city_disappearance_cannot_be_restored_to_confirm_original_purge():
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    conn.lua.execute('savedCity=city;city=nil')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    conn.lua.execute('city=savedCity;applyPurge()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    assert conn.lua.eval('#submissions') == 1


def test_unknown_pressure_cannot_hide_city_change_during_the_same_probe():
    class RestoreBetweenProbes(PurgeConn):
        async def execute_write(self, code):
            rows = await super().execute_write(code)
            if self.lua.eval('savedReligion ~= nil'):
                self.lua.execute('city.owner=0;city.GetReligion=savedReligion')
            return rows

    conn = RestoreBetweenProbes(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    conn.lua.execute('''
applyPurge()
savedReligion=city.GetReligion
city.GetReligion=function() city.owner=1;return {GetReligionsInCity=function() return nil end} end
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    conn.lua.execute('city.owner=0;city.GetReligion=savedReligion')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('setup', [
    'u.allowed=false', 'u.allowed=nil',
    "UnitManager.CanStartOperation=function() error('unknown eligibility') end",
    'u.kind=3', 'u.religion=0', 'u.spreads=0',
    'city.owner=1', 'targets={[1933683541]={},[628984671]={}}',
    'targets=nil', 'targets={[1933683541]={300,300},[628984671]={889110605,889110605}}',
    'targets={[1933683541]={999},[628984671]={889110605}}',
    'UnitManager.GetOperationTargets=function() return nil end',
    'religionRows=nil', 'religionRows=false',
    'religionRows={{Pressure=400}}',
    'religionRows={{Religion=8,Pressure=400},{Religion=8,Pressure=200}}',
    'religionRows={{Religion=8,Pressure=-1}}',
    'religionRows={{Religion=8,Pressure=math.huge}}',
    'religionRows={{Religion=8,Pressure=0/0}}',
    "religionRows={{Religion=8,Pressure='400'}}",
    'religionRows={{Religion=8.5,Pressure=400}}',
    'religionRows={{Religion=12,Pressure=100}}',
])
def test_unavailable_eligibility_target_identity_or_pressure_is_zero_submission(setup):
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute(setup)
    result = asyncio.run(state.remove_heresy(196608))
    assert result.startswith('Error: REMOVE_HERESY_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup', [
    'UnitManager.CanStartOperation=function() seed=9;return true end',
    'UnitManager.CanStartOperation=function() seed=nil;return true end',
    'UnitManager.CanStartOperation=function() u.religion=8;return true end',
    'UnitManager.CanStartOperation=function() city.owner=1;return true end',
    'UnitManager.CanStartOperation=function() targets=nil;return true end',
])
def test_final_native_qualification_cannot_change_bound_facts_before_submission(setup):
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(state.remove_heresy(196608)).startswith('Error: REMOVE_HERESY_REFUSED|')
    assert conn.lua.eval('#submissions') == 0


@pytest.mark.parametrize('setup', [
    'u.spreads=3', 'u.spreads=1',
    'religionRows={{Religion=8,Pressure=400},{Religion=9,Pressure=200},{Religion=12,Pressure=999}}',
    'religionRows={{Religion=8,Pressure=401},{Religion=9,Pressure=60}}',
    'religionRows={{Religion=8,Pressure=120},{Religion=9,Pressure=60},{Religion=10,Pressure=10}}',
    'religionRows=nil',
    'religionRows={{Religion=8,Pressure=120},{Religion=8,Pressure=60}}',
])
def test_charge_and_bound_foreign_pressure_effects_are_required(setup):
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    conn.lua.execute('applyPurge();' + setup)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('setup', [
    'Events.UnitOperationStarted.Fire=function() end',
    'Events.UnitOperationStarted.Fire=function() Events.UnitOperationStarted=event() end',
])
def test_pressure_and_charge_without_exact_original_started_event_remains_pending(setup):
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    conn.lua.execute(setup + ';applyPurge()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('owner,unit_id,operation', [(1,196608,77),(0,262144,77),(0,196608,78)])
def test_wrong_native_started_witness_cannot_confirm(owner, unit_id, operation):
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    conn.lua.execute(f'''Events.UnitOperationStarted.Fire({owner},{unit_id},{operation})
u.spreads=2;religionRows={{{{Religion=8,Pressure=100}}}}''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('reverse_events', [False, True])
def test_final_charge_requires_original_consuming_event_and_both_unit_views_absent(reverse_events):
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute('u.spreads=1;deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    if reverse_events:
        conn.lua.execute('''
units[196608]=nil;Events.UnitRemovedFromMap.Fire(0,196608)
Events.UnitOperationStarted.Fire(0,196608,77)
religionRows={{Religion=8,Pressure=100},{Religion=9,Pressure=50}}
for k,v in pairs(u) do if type(v)=='function' then u[k]=function() error('consumed userdata') end end end
''')
    else:
        conn.lua.execute('applyPurge()')
    result = asyncio.run(state.remove_heresy(196608))
    assert result.startswith('HERESY_REMOVED|unit:196608|religion:12|charges_remaining:0|')
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup', [
    'u.spreads=2',
    'Events.UnitRemovedFromMap.Fire=function() end',
    'Players[0].GetUnits=function() return {FindID=function() return u end} end',
])
def test_disappearance_alone_or_nonfinal_charge_cannot_prove_consumption(setup):
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute('u.spreads=1;deferred=true')
    if setup == 'u.spreads=2':
        conn.lua.execute(setup)
        setup = ''
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    # The retained view deliberately remains readable to exercise dual lookup,
    # rather than interpreting a missing map unit as destruction.
    conn.lua.execute(setup + '''
Events.UnitOperationStarted.Fire(0,196608,77)
units[196608]=nil;Events.UnitRemovedFromMap.Fire(0,196608)
religionRows={{Religion=8,Pressure=100},{Religion=9,Pressure=50}}
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    assert conn.lua.eval('#submissions') == 1


def test_lost_submission_reply_confirms_original_without_spending_second_charge():
    conn = PurgeConn(); state = GameState(conn); conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    assert asyncio.run(state.remove_heresy(196608)).startswith('HERESY_REMOVED|')
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('u.spreads') == 2


@pytest.mark.parametrize('later_change', [False, True])
def test_lost_confirmation_retains_original_witness_and_sticky_scope(later_change):
    class LoseConfirmation(PurgeConn):
        lose_confirmation = True

        async def execute_write(self, code):
            rows = await super().execute_write(code)
            if self.lose_confirmation and any(row.startswith('REMOVE_HERESY_CONFIRMED|') for row in rows):
                self.lose_confirmation = False
                raise OSError('confirmation response lost')
            return rows

    conn = LoseConfirmation(); state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    if later_change:
        conn.lua.execute('seed=9;Events.UnitOperationStarted.Fire(0,196608,77);seed=719665280')
        assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
        assert state.pending_action_kind == 'remove_heresy'
    else:
        assert asyncio.run(state.remove_heresy(196608)).startswith('HERESY_REMOVED|')
    assert conn.lua.eval('#submissions') == 1


def test_native_throw_after_entry_keeps_original_pending_without_replay():
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    assert state.pending_action_kind == 'remove_heresy'
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('phase', ['submission', 'confirmation'])
def test_cancellation_retains_owner_and_can_only_confirm_original_request(phase):
    class CancelOnce(PurgeConn):
        cancel = True

        async def execute_write(self, code):
            before = self.lua.eval('#submissions')
            rows = await super().execute_write(code)
            if self.cancel and (
                phase == 'submission' and self.lua.eval('#submissions') > before
                or phase == 'confirmation' and any(row.startswith('REMOVE_HERESY_CONFIRMED|') for row in rows)
            ):
                self.cancel = False
                raise asyncio.CancelledError()
            return rows

    conn = CancelOnce(); state = GameState(conn)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(state.remove_heresy(196608))
    assert state.pending_action_kind == 'remove_heresy'
    assert asyncio.run(state.remove_heresy(196608)).startswith('HERESY_REMOVED|')
    assert conn.lua.eval('#submissions') == 1


def test_pending_purge_blocks_other_actor_action_save_and_end_turn():
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    ctx = make_ctx(game=state)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'remove_heresy'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(262144))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.unit_action(ctx, 196608, 'spread_religion'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.skip_unit(196608))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.save_game('pending_purge'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    assert conn.lua.eval('#submissions') == 1
    conn.lua.execute('applyPurge()')
    assert asyncio.run(server.unit_action(ctx, 196608, 'remove_heresy')).startswith('HERESY_REMOVED|')
    assert conn.lua.eval('#submissions') == 1


def test_unknown_pressure_can_recover_without_replaying_operation():
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    conn.lua.execute('applyPurge();savedRows=religionRows;religionRows=nil')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    conn.lua.execute('religionRows=savedRows')
    assert asyncio.run(state.remove_heresy(196608)).startswith('HERESY_REMOVED|')
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('change,restore', [
    ('u.GetReligionType=function() return nil end;city.owner=1',
     'u.GetReligionType=function(self) return self.religion end;city.owner=0'),
    ('u.GetType=function() return nil end;u.religion=8',
     'u.GetType=function(self) return self.kind end;u.religion=12'),
    ('turn=nil;seed=9', 'turn=41;seed=719665280'),
])
def test_readable_identity_mismatch_beside_unknown_field_remains_sticky(change, restore):
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    conn.lua.execute(restore + ';applyPurge()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    assert conn.lua.eval('#submissions') == 1


def test_complete_pressure_collection_can_confirm_total_foreign_removal_without_assuming_own_growth():
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.remove_heresy(196608))
    conn.lua.execute('applyPurge();religionRows={{Religion=12,Pressure=100}}')
    result = asyncio.run(state.remove_heresy(196608))
    assert '8:400->0' in result and '9:200->0' in result
    assert 'conversion not inferred' in result


def test_final_charge_getter_cannot_change_actor_religion_before_native_entry():
    conn = PurgeConn(); state = GameState(conn)
    conn.lua.execute('''
u.GetSpreadCharges=function(self)
 if Events.UnitOperationStarted.Count()>0 then self.religion=8 end
 return self.spreads
end
''')
    assert asyncio.run(state.remove_heresy(196608)).startswith('Error: REMOVE_HERESY_REFUSED|')
    assert conn.lua.eval('#submissions') == 0


def test_native_description_only_self_city_purge_confirms_through_public_tool():
    conn = PurgeConn(); state = GameState(conn)
    conn.lua.execute("targets={[-1554772595]={'10 followers'}}")
    result = asyncio.run(server.unit_action(make_ctx(game=state), 196608, 'remove_heresy'))
    assert result.startswith('HERESY_REMOVED|unit:196608|religion:12|charges_remaining:2|city:0:65539|')
    assert '8:400->120' in result and '9:200->60' in result
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup', [
    'u.x=34',
    'city.owner=1',
    'city=nil',
    'city.id=nil',
    "city.GetOwner=function() error('unknown owner') end",
    'CityManager.GetCityAt=function() return city end;city.x=36',
    'u.allowed=false', 'u.allowed=nil',
    "UnitManager.CanStartOperation=function() error('unknown eligibility') end",
    'UnitManager.CanStartOperation=function() u.x=34;return true end',
    'UnitManager.CanStartOperation=function() city.id=131075;return true end',
    'UnitManager.CanStartOperation=function() city.GetID=function() return nil end;return true end',
    'UnitManager.CanStartOperation=function() city=nil;return true end',
    'UnitManager.CanStartOperation=function() city.owner=1;return true end',
    'UnitManager.CanStartOperation=function() targets={[1933683541]={300}};return true end',
])
def test_description_only_purge_requires_exact_stable_owned_center_and_native_permission(setup):
    conn = PurgeConn(); state = GameState(conn)
    conn.lua.execute("targets={[-1554772595]={'10 followers'}};" + setup)
    result = asyncio.run(server.unit_action(make_ctx(game=state), 196608, 'remove_heresy'))
    assert result.startswith('Error: REMOVE_HERESY_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('targets', [
    '{[1933683541]={300}}', '{[628984671]={889110605}}',
    '{[1933683541]=false,[628984671]={889110605}}', '{[1933683541]={300},[628984671]=false}',
    '{[1933683541]={300,999},[628984671]={889110605}}', '{[1933683541]={300},[628984671]={889110605,889110605}}',
    '{[1933683541]={300},[628984671]={false}}', '{[1933683541]={false},[628984671]={889110605}}',
    '{[1933683541]={300},[628984671]={}}', '{[1933683541]={},[628984671]={889110605}}',
])
def test_exact_center_cannot_override_partial_or_malformed_native_coordinate_targets(targets):
    conn = PurgeConn(); state = GameState(conn)
    conn.lua.execute('targets=' + targets)
    result = asyncio.run(server.unit_action(make_ctx(game=state), 196608, 'remove_heresy'))
    assert result.startswith('Error: REMOVE_HERESY_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('other_owner,targets', [
    (0, '{[1933683541]={301},[628984671]={889110605}}'),
    (1, '{[1933683541]={301},[628984671]={889110605}}'),
    (0, '{[1933683541]={300,301},[628984671]={889110605,889110605}}'),
])
def test_exact_center_cannot_override_conflicting_or_ambiguous_actual_native_city(other_owner, targets):
    conn = PurgeConn(); state = GameState(conn)
    conn.lua.execute('''
other={id=131075,owner=''' + str(other_owner) + ''',x=36,y=22}
for _,name in ipairs({'GetID','GetOwner','GetX','GetY'}) do other[name]=city[name] end
CityManager.GetCityAt=function(x,y)
 if x==35 and y==22 then return city elseif x==36 and y==22 then return other end
end
Map.GetPlotByIndex=function(id)
 if id==300 or id==301 then return {GetX=function() return id==300 and 35 or 36 end,GetY=function() return 22 end} end
end
targets=''' + targets)
    result = asyncio.run(server.unit_action(make_ctx(game=state), 196608, 'remove_heresy'))
    assert result.startswith('Error: REMOVE_HERESY_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


def test_off_center_purge_still_uses_exact_unique_actual_native_city_target():
    conn = PurgeConn(); state = GameState(conn); conn.lua.execute('u.x=34')
    result = asyncio.run(server.unit_action(make_ctx(game=state), 196608, 'remove_heresy'))
    assert result.startswith('HERESY_REMOVED|unit:196608|religion:12|charges_remaining:2|city:0:65539|')
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('charges,lose_reply', [(3, True), (1, False), (1, True)])
def test_description_only_self_city_preserves_final_consumption_and_lost_reply_ownership(charges, lose_reply):
    conn = PurgeConn(); state = GameState(conn)
    conn.lua.execute("targets={[-1554772595]={'10 followers'}};u.spreads=" + str(charges))
    conn.lose_reply = lose_reply
    ctx = make_ctx(game=state)
    result = asyncio.run(server.unit_action(ctx, 196608, 'remove_heresy'))
    if lose_reply:
        assert 'OUTCOME_UNKNOWN' in result
        assert state.pending_action_kind == 'remove_heresy'
        result = asyncio.run(server.unit_action(ctx, 196608, 'remove_heresy'))
    assert f'charges_remaining:{charges-1}' in result
    assert result.startswith('HERESY_REMOVED|')
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


def test_native_target_discovery_cannot_change_the_exact_center_before_binding():
    conn = PurgeConn(); state = GameState(conn)
    conn.lua.execute('''
UnitManager.GetOperationTargets=function()
 city.id=131075
 return {[-1554772595]={'10 followers'}}
end
''')
    result = asyncio.run(server.unit_action(make_ctx(game=state), 196608, 'remove_heresy'))
    assert result.startswith('Error: REMOVE_HERESY_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('setup', [
    'UnitOperationResults.PLOTS=nil;UnitOperationResults.MODIFIERS=nil',
    'UnitOperationResults.PLOTS=nil',
    "UnitOperationResults.PLOTS='unknown'",
    "UnitOperationResults.MODIFIERS='unknown'",
    'UnitOperationResults.PLOTS=1.5',
    'UnitOperationResults.MODIFIERS=UnitOperationResults.PLOTS',
    'UnitOperationResults.MODIFIER_IS_TARGET=UnitOperationResults.PLOTS',
    'UnitOperationResults.MODIFIER_IS_TARGET=UnitOperationResults.MODIFIERS',
    'UnitOperationResults.MODIFIERS=nil',
    "UnitOperationResults.PLOTS='unknown';UnitOperationResults.MODIFIERS='unknown'",
    'UnitOperationResults.PLOTS=false;UnitOperationResults.MODIFIERS={}',
    'UnitOperationResults.PLOTS=0/0',
    "UnitOperationResults.PLOTS=''",
    'UnitOperationResults.MODIFIER_IS_TARGET=nil',
    'UnitOperationResults.MODIFIER_IS_TARGET=false',
    'UnitOperationResults.MODIFIER_IS_TARGET=math.huge',
    'UnitOperationResults.ADDITIONAL_DESCRIPTION=UnitOperationResults.PLOTS',
    'UnitOperationResults.ADDITIONAL_DESCRIPTION=UnitOperationResults.MODIFIERS',
])
@pytest.mark.parametrize('target_rows', [
    '{[1933683541]={999},[628984671]={889110605}}',
    "{[-1554772595]={'10 followers'}}",
])
def test_unknown_or_conflicting_native_target_keys_cannot_hide_coordinate_targets(setup, target_rows):
    conn = PurgeConn(); state = GameState(conn)
    conn.lua.execute('targets=' + target_rows + ';' + setup)
    result = asyncio.run(server.unit_action(make_ctx(game=state), 196608, 'remove_heresy'))
    assert result.startswith('Error: REMOVE_HERESY_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None
