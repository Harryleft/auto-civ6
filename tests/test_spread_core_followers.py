"""复现压力未变化时的信徒增加和末次传播消耗现场。"""

import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from test_spread_pressure import SpreadPressureConn
from test_unit_readiness import ReadyConn


class CoreFollowersConn(SpreadPressureConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
core=false;coreFollowers=3;afterFollowers=4;coreRetainsActor=false;delayedDeath=false
u.spreads=1
u.GetReligion=function(self) return {
 GetReligionType=function() return self.religion end,
 GetSpreadCharges=function() if not missingCoreCharges then return self.spreads end end} end
u.IsDelayedDeath=function() return delayedDeath end
local ownedUnits=Players[0].GetUnits
Players[0].GetUnits=function(self)
 if core and coreRetainsActor then return {FindID=function() return u end} end
 return ownedUnits(self)
end
Players[0].GetCities=function() return {
 Members=function() return ipairs({city}) end,
 FindID=function(_,id) if id==327684 then return city end end} end
local uiReligion=city.GetReligion
city.GetReligion=function(self)
 if core then return {GetNumFollowers=function(_,religion)
  assert(religion==12);return coreFollowers
 end} end
 return uiReligion(self)
end
function applySpread()
 Events.UnitOperationStarted.Fire(0,2686987,32)
 u.spreads=u.spreads-1;coreFollowers=afterFollowers
 if u.spreads==0 then units[u.id]=nil;Events.UnitRemovedFromMap.Fire(0,u.id) end
end
''')

    async def execute_read(self, code):
        self.lua.execute("core=true;savedReligionGetter=u.GetReligionType;u.GetReligionType=nil")
        try:
            return await ReadyConn.execute_write(self, code)
        finally:
            self.lua.execute("core=false;u.GetReligionType=savedReligionGetter")


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT", .01)
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL", .001)


def test_native_t67_final_charge_with_stale_pressure_confirms_core_followers_only():
    conn = CoreFollowersConn()
    state = GameState(conn)
    result = asyncio.run(server.unit_action(make_ctx(game=state), 2686987, "spread_religion"))
    assert result.startswith("RELIGION_SPREAD|")
    assert "charges_remaining:0" in result
    assert "GameCore followers:3->4; UI pressure unchanged." in result
    assert "Pressure increase confirmed" not in result
    assert conn.lua.eval("religionRows[3].Pressure") == 17.90625
    assert conn.lua.eval("#submissions") == 1
    assert state.pending_action_summary is None


def test_t195_consumed_last_spread_confirms_execution_without_claiming_follower_gain():
    conn = CoreFollowersConn(); conn.lua.execute('afterFollowers=3')
    state = GameState(conn)
    result = asyncio.run(server.unit_action(make_ctx(game=state), 2686987, 'spread_religion'))
    assert result.startswith('RELIGION_SPREAD_EXECUTED|')
    assert 'GameCore followers:3->3' in result
    assert 'last charge and unit consumption confirmed' in result
    assert 'Pressure increase confirmed' not in result
    assert 'pressure effect unmeasured' in result
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


def test_t196_live_spread_charge_decrement_confirms_execution_without_follower_gain():
    conn = CoreFollowersConn(); conn.lua.execute('u.spreads=2;afterFollowers=3')
    state = GameState(conn)
    result = asyncio.run(server.unit_action(make_ctx(game=state), 2686987, 'spread_religion'))
    assert result.startswith('RELIGION_SPREAD_EXECUTED|')
    assert 'spread charge:2->1' in result
    assert 'GameCore charges_remaining:1' in result
    assert 'GameCore followers:3->3' in result
    assert 'pressure effect unmeasured' in result
    assert 'unit consumption confirmed' not in result
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('core_charges', ['2', '0', '"1"', 'nil'])
def test_live_spread_requires_core_charge_to_match_original_decrement(core_charges):
    conn = CoreFollowersConn()
    conn.lua.execute('''u.spreads=2;afterFollowers=3
u.GetReligion=function(self) return {
 GetReligionType=function() return self.religion end,
 GetSpreadCharges=function() return ''' + core_charges + ''' end} end
''')
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is not None


def test_core_charge_getter_cannot_hide_actor_identity_change():
    conn = CoreFollowersConn(); conn.lua.execute('u.spreads=2;afterFollowers=3;deferred=true')
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute('''applySpread()
u.GetReligion=function(self) return {
 GetReligionType=function() return self.religion end,
 GetSpreadCharges=function() self.owner=1;return self.spreads end} end
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute('''u.owner=0
u.GetReligion=function(self) return {
 GetReligionType=function() return self.religion end,
 GetSpreadCharges=function() return self.spreads end} end
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval('#submissions') == 1


def test_unknown_core_id_does_not_mask_known_owner_change():
    conn = CoreFollowersConn(); conn.lua.execute('u.spreads=2;afterFollowers=3;deferred=true')
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute('''applySpread();coreWrong=true
local id,owner=u.GetID,u.GetOwner
u.GetID=function(self) if core and coreWrong then return nil end;return id(self) end
u.GetOwner=function(self) if core and coreWrong then return 1 end;return owner(self) end
''')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute('coreWrong=false')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval('#submissions') == 1


def test_last_spread_rejects_live_core_actor_despite_ingame_removal():
    conn = CoreFollowersConn()
    conn.lua.execute('afterFollowers=3;coreRetainsActor=true')
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is not None


def test_t195_delayed_death_core_object_confirms_consumption_without_claiming_absence():
    conn = CoreFollowersConn()
    conn.lua.execute('afterFollowers=3;coreRetainsActor=true;delayedDeath=true')
    state = GameState(conn)
    result = asyncio.run(state.spread_religion(2686987))
    assert result.startswith('RELIGION_SPREAD_EXECUTED|')
    assert 'unit_state:delayed_death' in result
    assert 'unit_state:absent' not in result
    assert 'GameCore followers:3->3' in result
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('change,restore', [
    ('u.id=123', 'u.id=2686987'), ('u.owner=1', 'u.owner=0'),
    ('u.religion=13', 'u.religion=12'),
])
def test_delayed_death_actor_identity_mismatch_stays_invalid_after_restore(change, restore):
    conn = CoreFollowersConn()
    conn.lua.execute('afterFollowers=3;coreRetainsActor=true;delayedDeath=true;deferred=true')
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute('applySpread();' + change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute(restore)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval('#submissions') == 1


def test_lost_last_spread_reply_confirms_only_original_consumption_without_follower_gain():
    conn = CoreFollowersConn(); conn.lua.execute('afterFollowers=3'); conn.lose_reply = True
    state = GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.spread_religion(2686987))
    result = asyncio.run(state.spread_religion(2686987))
    assert result.startswith('RELIGION_SPREAD_EXECUTED|')
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize("setup", [
    "u.spreads=2;afterFollowers=3;missingCoreCharges=true", "afterFollowers=2", "afterFollowers=nil", "afterFollowers='4'",
    "Events.UnitOperationStarted.Fire=function() end",
    "Events.UnitRemovedFromMap.Fire=function() end", "fail_request=true",
])
def test_no_follower_gain_and_missing_consumption_witness_stays_pending(setup):
    conn = CoreFollowersConn()
    state = GameState(conn)
    conn.lua.execute(setup)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.spread_religion(2686987))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval("#submissions") == 1
    assert state.pending_action_summary


def test_lost_reply_confirms_original_core_change_without_second_spread():
    conn = CoreFollowersConn()
    state = GameState(conn)
    conn.lose_reply = True
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.spread_religion(2686987))
    assert "GameCore followers:3->4" in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval("#submissions") == 1


@pytest.mark.parametrize("change,restore", [
    ("city.owner=1", "city.owner=0"), ("city.id=10", "city.id=327684"),
    ("city.x=35", "city.x=34"), ("seed=1", "seed=-1896272000"),
    ("turn=129", "turn=128"),
])
def test_changed_core_city_or_scope_cannot_recover_by_restoring_old_values(change, restore):
    conn = CoreFollowersConn()
    state = GameState(conn)
    conn.lua.execute("deferred=true")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute("applySpread();" + change)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute(restore)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval("#submissions") == 1


def test_unchanged_core_follower_case_recovers_only_after_real_effect_arrives():
    conn = CoreFollowersConn()
    state = GameState(conn)
    conn.lua.execute("u.spreads=2;afterFollowers=3;missingCoreCharges=true")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute("coreFollowers=4")
    assert "GameCore followers:3->4" in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval("#submissions") == 1


def test_core_city_disappearance_is_sticky_even_if_it_returns():
    conn = CoreFollowersConn()
    state = GameState(conn)
    conn.lua.execute("deferred=true")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute('applySpread();citiesReader=Players[0].GetCities;Players[0].GetCities=function() return {FindID=function() return nil end} end')
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute("Players[0].GetCities=citiesReader")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval("#submissions") == 1


def test_core_identity_change_inside_follower_getter_is_not_hidden_by_ui_cache():
    conn = CoreFollowersConn()
    state = GameState(conn)
    conn.lua.execute("deferred=true")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute('''
applySpread();religionReader=city.GetReligion
city.GetReligion=function(self)
 if core then return {GetNumFollowers=function() city.owner=1;return 4 end} end
 return religionReader(self)
end
''')
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.spread_religion(2686987))
    conn.lua.execute("city.owner=0;city.GetReligion=religionReader")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.spread_religion(2686987))
    assert conn.lua.eval("#submissions") == 1
