"""Explicit citizen yield targets through GameState and a Lua 5.1 native seam."""
import asyncio
import pytest
from test_unit_readiness import ReadyConn
from civ_mcp.game_state import GameState

run = asyncio.run


class FocusConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
favored,ignored,commands,focus_queue={},{},{},{}
focus_deferred=false;city_missing=false
local names={"YIELD_FOOD","YIELD_PRODUCTION","YIELD_GOLD","YIELD_SCIENCE","YIELD_CULTURE","YIELD_FAITH"}
local yieldRows={}
GameInfo.Yields=setmetatable({}, {__call=function()
 local i=0;return function() i=i+1;return yieldRows[i] end
end})
for i,name in ipairs(names) do
 local row={Index=i-1,YieldType=name};yieldRows[i]=row;GameInfo.Yields[name]=row
 favored[i-1]=false;ignored[i-1]=false
end
citizens={IsFavoredYield=function(_,i) return favored[i] end,
 IsDisfavoredYield=function(_,i) return ignored[i] end}
city={id=262147,owner=0,x=26,y=17,GetID=function(self) return self.id end,
 GetOwner=function(self) return self.owner end,GetX=function(self) return self.x end,
 GetY=function(self) return self.y end,GetCitizens=function() return citizens end}
CityCommandTypes={SET_FOCUS=12,PARAM_FLAGS=1,PARAM_YIELD_TYPE=2,PARAM_DATA0=3}
function applyFocus(command)
 if command.flag==0 then favored[command.yield]=command.value==1
 elseif command.flag==1 then ignored[command.yield]=command.value==1 end
end
CityManager={GetCity=function(owner,id) if not city_missing and owner==city.owner and id==city.id%65536 then return city end end,
 RequestCommand=function(c,kind,params)
  if kind~=12 or (params[3]~=0 and params[3]~=1) then error("official SET_FOCUS requires PARAM_DATA0") end
  local command={flag=params[1],yield=params[2],value=params[3]}
  table.insert(commands,command)
  if focus_deferred then table.insert(focus_queue,command) else applyFocus(command) end
 end}
''')


def test_food_focus_sets_native_favored_with_official_flag_and_explicit_boolean():
    conn = FocusConn()
    state = GameState(conn)
    result = run(state.set_city_focus(262147, "food"))
    assert result.startswith("FOCUS_SET|")
    assert conn.lua.eval('favored[0]') is True
    assert conn.lua.eval('ignored[0]') is False
    assert conn.lua.eval('#commands') == 1
    assert conn.lua.eval('commands[1].flag') == 0
    assert conn.lua.eval('commands[1].value') == 1
    assert state.pending_action_summary is None


@pytest.fixture
def quick_focus(monkeypatch):
    import civ_mcp.game_state as module
    monkeypatch.setattr(module, "ACTION_CONFIRM_TIMEOUT", .015)
    monkeypatch.setattr(module, "ACTION_CONFIRM_INTERVAL", .001)


def test_already_matching_focus_is_idempotent_without_native_toggle():
    conn = FocusConn();state = GameState(conn)
    conn.lua.execute('favored[0]=true')
    assert run(state.set_city_focus(262147, "YIELD_FOOD")).startswith("NO_CHANGE|")
    assert conn.lua.eval('#commands') == 0
    assert conn.lua.eval('favored[0]') is True


def test_ignored_target_is_cleared_before_favoring_and_other_favorites_are_removed():
    conn = FocusConn();state = GameState(conn)
    conn.lua.execute('ignored[0]=true;favored[1]=true;ignored[5]=true')
    assert run(state.set_city_focus(262147, "food")).startswith("FOCUS_SET|")
    assert conn.lua.eval('favored[0] and not ignored[0] and not favored[1] and ignored[5]') is True
    assert conn.lua.eval('#commands') == 3
    assert conn.lua.eval('commands[1].flag==1 and commands[1].value==0') is True
    assert run(state.set_city_focus(262147, "food")).startswith("NO_CHANGE|")
    assert conn.lua.eval('#commands') == 3


def test_default_clears_all_favored_and_ignored_then_is_noop():
    conn = FocusConn();state = GameState(conn)
    conn.lua.execute('favored[0]=true;favored[1]=true;ignored[5]=true')
    assert run(state.set_city_focus(262147, "default")).startswith("FOCUS_CLEARED|")
    assert conn.lua.eval('not favored[0] and not favored[1] and not ignored[5]') is True
    assert conn.lua.eval('#commands') == 3
    assert all(command.value == 0 for command in conn.lua.globals().commands.values())
    assert run(state.set_city_focus(262147, "default")).startswith("NO_CHANGE|")
    assert conn.lua.eval('#commands') == 3


def test_delayed_native_commands_only_confirm_on_fresh_citizens_read(quick_focus):
    conn = FocusConn();state = GameState(conn)
    conn.lua.execute('focus_deferred=true')
    assert "OUTCOME_UNKNOWN" in run(state.set_city_focus(262147, "food"))
    assert state.pending_action_entity == ("city", 262147)
    assert "OUTCOME_UNKNOWN" in run(state.set_city_focus(262147, "production"))
    assert conn.lua.eval('#commands') == 1
    conn.lua.execute('for _,command in ipairs(focus_queue) do applyFocus(command) end')
    assert run(state.set_city_focus(262147, "food")).startswith("FOCUS_SET|")
    assert conn.lua.eval('#commands') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('exception', [OSError, asyncio.CancelledError])
def test_lost_reply_or_cancellation_keeps_one_city_intent(exception, quick_focus):
    class LostConn(FocusConn):
        lost = False
        async def execute_write(self, source):
            rows = await super().execute_write(source)
            if 'local function setFlag' in source and not self.lost:
                self.lost = True
                raise exception('native callback reply lost')
            return rows
    conn = LostConn();state = GameState(conn)
    if exception is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            run(state.set_city_focus(262147, "food"))
    else:
        assert "OUTCOME_UNKNOWN" in run(state.set_city_focus(262147, "food"))
    assert state.pending_action_summary
    assert run(state.set_city_focus(262147, "food")).startswith("FOCUS_SET|")
    assert conn.lua.eval('#commands') == 1


@pytest.mark.parametrize('change', [
    'seed=99', 'turn=42', 'city.id=327683', 'city.owner=1', 'city.x=27', 'city.y=18',
    'favored[1]=true', 'ignored[0]=true', 'city_missing=true',
    'CityCommandTypes.PARAM_DATA0=nil', 'CityManager.RequestCommand=nil',
    'favored[0]=nil', 'city.y=nil',
])
def test_atomic_world_city_and_preference_guard_proves_zero_submission(change):
    class ChangedConn(FocusConn):
        async def execute_write(self, source):
            if 'local function setFlag' in source:
                self.lua.execute(change)
            return await super().execute_write(source)
    conn = ChangedConn();state = GameState(conn)
    assert run(state.set_city_focus(262147, "food")).startswith("Error:")
    assert conn.lua.eval('#commands') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('change', [
    'seed=99', 'turn=42', 'city.id=327683', 'city.owner=1', 'city.x=27',
    'city_missing=true', 'favored[0]=nil', '__civmcpCityFocus[3].token="other"',
])
def test_unknown_confirmation_preserves_gate_after_world_city_or_receipt_changes(change, quick_focus):
    conn = FocusConn();state = GameState(conn)
    conn.lua.execute('focus_deferred=true')
    assert "OUTCOME_UNKNOWN" in run(state.set_city_focus(262147, "food"))
    conn.lua.execute(change)
    assert "OUTCOME_UNKNOWN" in run(state.set_city_focus(262147, "food"))
    assert state.pending_action_summary
    assert conn.lua.eval('#commands') == 1


def test_unconfirmed_focus_blocks_saving_and_turn_progression(quick_focus):
    conn = FocusConn();state = GameState(conn)
    conn.lua.execute('focus_deferred=true')
    assert "OUTCOME_UNKNOWN" in run(state.set_city_focus(262147, "food"))
    assert "OUTCOME_UNKNOWN" in run(state.save_game("focus_pending"))
    assert "OUTCOME_UNKNOWN" in run(state.end_turn())
    assert state.pending_action_summary
    assert conn.lua.eval('#commands') == 1


def test_observed_changed_world_cannot_be_hidden_by_fresh_identity_cache(quick_focus):
    conn = FocusConn();state = GameState(conn)
    conn.lua.execute('focus_deferred=true')
    assert "OUTCOME_UNKNOWN" in run(state.set_city_focus(262147, "food"))
    conn.lua.execute('seed=99')
    assert run(state.get_game_identity()) == ('sumeria', 99)
    conn.lua.execute('seed=719665280;for _,command in ipairs(focus_queue) do applyFocus(command) end')
    assert run(state.get_game_identity()) == ('sumeria', 719665280)
    assert "OUTCOME_UNKNOWN" in run(state.set_city_focus(262147, "food"))
    assert state.pending_action_summary and conn.lua.eval('#commands') == 1


def test_partial_native_failure_never_replays_or_claims_completed_command_block(quick_focus):
    conn = FocusConn();state = GameState(conn)
    conn.lua.execute('''ignored[0]=true;
local native=CityManager.RequestCommand
CityManager.RequestCommand=function(c,kind,params) native(c,kind,params);error("failure after first native command") end''')
    assert "OUTCOME_UNKNOWN" in run(state.set_city_focus(262147, "food"))
    assert conn.lua.eval('#commands') == 1
    conn.lua.execute('favored[0]=true')
    assert "OUTCOME_UNKNOWN" in run(state.set_city_focus(262147, "food"))
    assert state.pending_action_summary and conn.lua.eval('#commands') == 1
