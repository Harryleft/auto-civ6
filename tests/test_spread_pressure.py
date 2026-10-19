"""Cumulative spread pressure through public MCP/GameState and native Lua."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from test_unit_effect_confirmation import EffectConn


class SpreadPressureConn(EffectConn):
    def __init__(self):
        super().__init__('spread')
        self.lua.execute('''
turn=128;seed=-1896272000
units[196608]=nil;u.id=2686987;units[u.id]=u
u.x=34;u.y=26;u.spreads=3;u.religion=12
city.id=327684;city.owner=0;city.x=34;city.y=26
incomingPressure=5.96875
religionRows={{Religion=-1,Followers=2,Pressure=50},{Religion=8,Followers=1,Pressure=26.078125},{Religion=12,Followers=0,Pressure=17.90625}}
city.GetReligion=function(self) return {
 GetTotalPressureOnCity=function(_,religion) assert(religion==12);return incomingPressure end,
 GetReligionsInCity=function() return religionRows end,
 GetMajorityReligion=function() return self.majority end,
} end
UnitManager.CanStartOperation=function(actor,operation,unused,params,check)
 assert(actor==u and operation==32 and unused==nil and params.x==34 and params.y==26 and check==true)
 return u.allowed
end
function applySpread()
 Events.UnitOperationStarted.Fire(0,2686987,32)
 u.spreads=u.spreads-1
 religionRows={{Religion=-1,Followers=1,Pressure=50},{Religion=8,Followers=0,Pressure=23.46875},{Religion=12,Followers=2,Pressure=217.90625}}
 if u.spreads==0 then
  units[2686987]=nil;Events.UnitRemovedFromMap.Fire(0,2686987)
 end
end
UnitManager.RequestOperation=function(actor,operation,params)
 table.insert(submissions,{id=actor.id,op=operation,params=params})
 if not deferred then applySpread() end
 if fail_request then error('native spread entered then failed') end
end
''')


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def test_t128_spread_uses_cumulative_religion_row_while_directional_reading_is_unchanged():
    conn = SpreadPressureConn(); state = GameState(conn)
    result = asyncio.run(server.unit_action(make_ctx(game=state), 2686987, 'spread_religion'))
    assert result.startswith('RELIGION_SPREAD|unit:2686987|religion:12|charges_remaining:2|')
    assert 'city:0:327684 pressure:17.90625->217.90625' in result
    assert conn.lua.eval('incomingPressure') == 5.96875
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('later_scope_change', [False, True])
def test_lost_spread_confirmation_retains_native_scope_witness_after_reply_loss(later_scope_change):
    class LostConfirmConn(SpreadPressureConn):
        lose_confirmation = True

        async def execute_write(self, code):
            rows = await super().execute_write(code)
            if self.lose_confirmation and any(row.startswith('UNIT_EFFECT_CONFIRMED|') for row in rows):
                self.lose_confirmation = False
                raise OSError('spread confirmation reply lost')
            return rows

    conn = LostConfirmConn(); state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    if later_scope_change:
        conn.lua.execute('seed=1;Events.UnitOperationStarted.Fire(0,2686987,32);seed=-1896272000')
        assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
        assert state.pending_action_kind == 'spread_religion'
    else:
        assert asyncio.run(state.spread_religion(2686987)).startswith('RELIGION_SPREAD|')
        assert state.pending_action_summary is None
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('change,restore', [
    ('u.religion=8', 'u.religion=12'),
    ('city.owner=1', 'city.owner=0'),
    ('city.id=393220', 'city.id=327684'),
    ('city.GetX=function() return 35 end', 'city.GetX=function() return 34 end'),
    ('u.kind=4', 'u.kind=3'),
    ('u.id=2752523', 'u.id=2686987'),
    ('u.GetOwner=function() return 1 end', 'u.GetOwner=function() return 0 end'),
    ('u.x=35', 'u.x=34'),
])
def test_observed_original_identity_change_cannot_confirm_after_restoring_it(change, restore):
    conn = SpreadPressureConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute(restore + ';applySpread()')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval('#submissions') == 1


BAD_RECORDS = [
    'nil', 'false', '17.90625', "'unknown'", '{false}', '{unknown={}}',
    '{{Religion=12,Pressure=17.90625},{Religion=12,Pressure=17.90625}}',
    '{{Religion=8,Pressure=2},{Religion=8,Pressure=2}}',
    '{{Religion=-2,Pressure=0}}', '{{Religion=12.5,Pressure=2}}',
    '{{Pressure=2}}', '{{Religion=12}}', '{{Religion=12,Pressure=-1}}',
    '{{Religion=12,Pressure=0/0}}', '{{Religion=12,Pressure=math.huge}}',
    "{{Religion=12,Pressure='217.90625'}}", '{{Religion=8,Pressure=nil}}',
]


@pytest.mark.parametrize('records', BAD_RECORDS)
def test_incomplete_or_malformed_native_collection_never_becomes_zero_baseline(records):
    conn = SpreadPressureConn(); state = GameState(conn)
    conn.lua.execute('religionRows=' + records)
    assert asyncio.run(state.spread_religion(2686987)).startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('records', BAD_RECORDS + [
    '{{Religion=12,Pressure=17.90625}}', '{{Religion=12,Pressure=1}}',
    '{{Religion=8,Pressure=500}}', '{}',
])
def test_wrong_or_unconfirmed_cumulative_pressure_cannot_borrow_a_higher_directional_reading(records):
    conn = SpreadPressureConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute('applySpread();incomingPressure=999;religionRows=' + records)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_kind == 'spread_religion'


@pytest.mark.parametrize('records', ['{}', '{{Religion=-1,Pressure=50},{Religion=8,Pressure=26.078125}}'])
def test_complete_valid_native_collection_without_original_religion_has_zero_baseline(records):
    conn = SpreadPressureConn(); state = GameState(conn)
    conn.lua.execute('religionRows=' + records)
    result = asyncio.run(state.spread_religion(2686987))
    assert 'pressure:0->217.90625' in result
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('setup', [
    'city.GetReligion=function() return {} end',
    "city.GetReligion=function() return {GetReligionsInCity=function() error('unavailable') end} end",
])
def test_unavailable_native_collection_is_zero_submit_before_entry(setup):
    conn = SpreadPressureConn(); state = GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(state.spread_religion(2686987)).startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions') == 0


@pytest.mark.parametrize('setup', [
    'u.spreads=3', 'u.spreads=1', 'u.religion=8',
    'Events.UnitOperationStarted.Fire=function() end',
])
def test_cumulative_pressure_increase_still_needs_original_start_religion_and_one_charge(setup):
    conn = SpreadPressureConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    if setup.startswith('Events.'):
        conn.lua.execute(setup + ';applySpread()')
    else:
        conn.lua.execute('applySpread();' + setup)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval('#submissions') == 1


def test_exact_final_charge_removal_and_cumulative_pressure_confirm_original_missionary():
    conn = SpreadPressureConn(); state = GameState(conn); conn.lua.execute('u.spreads=1')
    result = asyncio.run(state.spread_religion(2686987))
    assert result.startswith('RELIGION_SPREAD|unit:2686987|religion:12|charges_remaining:0|')
    assert 'pressure:17.90625->217.90625' in result
    assert conn.lua.eval('units[2686987]') is None
    assert conn.lua.eval('#submissions') == 1


def test_lost_spread_submission_reply_can_only_confirm_original_effect():
    conn = SpreadPressureConn(); state = GameState(conn); conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    assert asyncio.run(state.spread_religion(2686987)).startswith('RELIGION_SPREAD|')
    assert conn.lua.eval('#submissions') == 1


def test_pending_spread_blocks_other_mutations_save_and_turn():
    conn = SpreadPressureConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2752523))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.skip_unit(2686987))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.save_game('pending_spread'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.end_turn())
    conn.lua.execute('applySpread()')
    assert asyncio.run(state.spread_religion(2686987)).startswith('RELIGION_SPREAD|')
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('setup', ['u.kind=nil', 'city.owner=100'])
def test_unreadable_actor_type_or_invalid_city_owner_refuses_before_native_spread(setup):
    conn = SpreadPressureConn(); state = GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(state.spread_religion(2686987)).startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions') == 0


@pytest.mark.parametrize('setup', [
    'UnitManager.CanStartOperation=function() seed=1;return true end',
    'UnitManager.CanStartOperation=function() seed=nil;return true end',
])
def test_final_scope_change_or_unknown_during_native_spread_eligibility_is_zero_submit(setup):
    conn = SpreadPressureConn(); state = GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(state.spread_religion(2686987)).startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


def test_scope_change_inside_final_pressure_getter_is_sticky_on_the_original_spread():
    conn = SpreadPressureConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute('''
applySpread()
pressureReader=city.GetReligion
city.GetReligion=function(self)
 local religion=pressureReader(self)
 religion.GetReligionsInCity=function() seed=1;return religionRows end
 return religion
end
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute('seed=-1896272000;city.GetReligion=pressureReader')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval('#submissions') == 1


def test_unknown_cumulative_pressure_recovers_without_replaying_original_spread():
    conn = SpreadPressureConn(); state = GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute('applySpread();validPressureRows=religionRows;religionRows=nil')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute('religionRows=validPressureRows')
    assert asyncio.run(state.spread_religion(2686987)).startswith('RELIGION_SPREAD|')
    assert conn.lua.eval('#submissions') == 1


def test_actor_change_during_final_pressure_baseline_read_refuses_before_submission():
    conn = SpreadPressureConn(); state = GameState(conn)
    conn.lua.execute('''
local getReligion=city.GetReligion
city.GetReligion=function(self)
 local religion=getReligion(self)
 religion.GetReligionsInCity=function()
  if Events.UnitOperationStarted.Count()>0 then u.religion=8 end
  return religionRows
 end
 return religion
end
''')
    assert asyncio.run(state.spread_religion(2686987)).startswith('Error: UNIT_EFFECT_REFUSED|')
    assert conn.lua.eval('#submissions') == 0
