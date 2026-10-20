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
    'city.owner=1', 'targets={plots={},modifiers={}}',
    'targets=nil', 'targets={plots={300,300},modifiers={1,1}}',
    'targets={plots={999},modifiers={1}}',
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
