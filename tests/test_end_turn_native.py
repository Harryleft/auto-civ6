"""Execute native preconditions atomically in an isolated Lua 5.1 context."""
import pytest
from lupa.lua51 import LuaRuntime
from civ_mcp.lua.notifications import build_end_turn


class NativeTurn:
    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.execute('''
turn, active, complete, processing, sent, allowed, unready, blocker, mode = 78, true, true, false, false, true, false, 0, 4
requests, mode_changes = 0, 0
seed,civ,local_player=1,'CIVILIZATION_SUMERIA',0
PlayerConfigurations=setmetatable({}, {__index=function() return {GetCivilizationTypeName=function() return civ end} end})
GameConfiguration={GetValue=function() return seed end}
auto_end, range_block, tactical, first_ready, range_notice, core_busy = false, false, true, nil, nil, false
units, cities, queued_targets, paths = {}, {}, {}, {}
local p = {IsTurnActive=function() return active end, IsTurnActiveComplete=function() return complete end,
           CanUnreadyTurn=function() return unready end,
 GetUnits=function() return {GetFirstReadyUnit=function() return first_ready end,Members=function() return ipairs(units) end} end,
 GetCities=function() return {Members=function() return ipairs(cities) end} end}
function makeUnit(id,x,y,moves)
 return {GetID=function() return id end, GetX=function() return x end,GetY=function() return y end,
 GetMovesRemaining=function() return moves end, IsReadyToMove=function() return false end}
end
function makePlot(x,y) return {GetX=function() return x end,GetY=function() return y end} end
plots={[1]=makePlot(21,18),[2]=makePlot(20,18),[3]=makePlot(19,18)}
Map={GetPlotByIndex=function(index) return plots[index] end}
UnitManager={GetQueuedDestination=function(u) return queued_targets[u:GetID()] end,
 GetMoveToPathEx=function(u) return paths[u:GetID()] end, GetActivityType=function() return 1225574625 end}
ActivityTypes={ACTIVITY_OPERATION=1225574625}
UserConfiguration={IsAutoEndTurn=function() return auto_end end,IsCityRangeAttackTurnBlocking=function() return range_block end}
Game = {GetLocalPlayer=function() return local_player end, GetCurrentGameTurn=function() return turn end,IsAllowTacticalCommands=function() return tactical end}
Players = {[0]=p}
UI = {IsProcessingMessages=function() return processing end, HasSentTurnComplete=function() return sent end,
      CanEndTurn=function() return allowed end, GetInterfaceMode=function() return mode end,IsGameCoreBusy=function() return core_busy end,
      SetInterfaceMode=function(value) mode=value; mode_changes=mode_changes+1 end,
      RequestAction=function() requests=requests+1 end}
NotificationManager={GetFirstEndTurnBlocking=function() return blocker end,FindType=function() return range_notice end}
NotificationTypes={CITY_RANGE_ATTACK=15}
EndTurnBlockingTypes={NO_ENDTURN_BLOCKING=0}
InterfaceModeTypes={SELECTION=1}
ActionTypes={ACTION_ENDTURN=12}
''')

    def run(self):
        self.lua.execute(build_end_turn(78, ("CIVILIZATION_SUMERIA", 1, 0)))
        return self.rows


@pytest.mark.parametrize("setup,reason", [
    ("processing=true", "UI_PROCESSING"), ("sent=true", "ALREADY_SENT"),
    ("allowed=false", "CANNOT_END_TURN"), ("unready=true", "ALREADY_SENT"),
    ("active=false", "NOT_READY"), ("complete=false", "NOT_READY"),
    ("turn=79", "TURN_CHANGED"), ("blocker=3", "BLOCKED"),
    ("UI.CanEndTurn=nil", "UNKNOWN"), ("UI.HasSentTurnComplete=nil", "UNKNOWN"),
])
def test_changed_final_guard_submits_nothing(setup, reason):
    game = NativeTurn()
    game.lua.execute(setup)
    assert f"END_TURN|NOT_SUBMITTED|{reason}" in game.run()
    assert game.lua.globals().requests == 0
    assert game.lua.globals().mode_changes == 0


def test_native_selection_recovery_then_one_submission():
    game = NativeTurn()
    assert "END_TURN|SUBMITTED" in game.run()
    assert game.lua.globals().requests == 1
    assert game.lua.globals().mode == 1
    assert game.lua.globals().mode_changes == 1


@pytest.mark.parametrize("setup,reason,detail", [
    ('auto_end=true;first_ready=makeUnit(2228231,21,18,1)', 'UNITS_HAVE_MOVES', '2228231'),
    ('range_block=true;range_notice={IsDismissed=function() return false end}', 'CITY_RANGE_ATTACK', 'city_range'),
    ('units={makeUnit(2228231,21,18,2)};queued_targets[2228231]=2;paths[2228231]={plots={1,2},turns={1,1}}', 'QUEUED_MOVE_CURRENT', 'goal=20,18'),
])
def test_native_local_actions_refuse_before_mode_or_end_request(setup,reason,detail):
    game=NativeTurn(); game.lua.execute(setup)
    rows=game.run()
    assert f'END_TURN|NOT_SUBMITTED|{reason}' in rows
    assert any(detail in row for row in rows)
    assert game.lua.globals().requests==0 and game.lua.globals().mode_changes==0
    assert game.lua.eval('queued_targets[2228231]') in (None,2)


@pytest.mark.parametrize('setup', [
    'first_ready=makeUnit(2228231,21,18,1)',  # auto_end=false: native UI does not force movement.
    'units={makeUnit(2228231,21,18,2)};queued_targets[2228231]=2;paths[2228231]={plots={1,2},turns={1,2}}',
    'units={makeUnit(917505,21,18,2)};queued_targets[917505]=2;cities={{GetTrade=function() return {GetOutgoingRoutes=function() return {{TraderUnitID=917505}} end} end}}',
    'units={makeUnit(2228231,21,18,0)};queued_targets[2228231]=2',
    'units={makeUnit(2228231,21,18,2)};queued_targets[2228231]=1',
])
def test_native_future_route_exhausted_or_disabled_soft_action_does_not_force_unit_action(setup):
    game=NativeTurn();game.lua.execute(setup)
    assert 'END_TURN|SUBMITTED' in game.run()
    assert game.lua.globals().requests==1


@pytest.mark.parametrize('setup', [
    'UserConfiguration.IsAutoEndTurn=nil;first_ready=makeUnit(2228231,21,18,1)',
    'auto_end=true;tactical=nil;first_ready=makeUnit(2228231,21,18,1)',
    'range_block=true;range_notice={IsDismissed=function() return nil end}',
    'units={makeUnit(2228231,21,18,2)};queued_targets[2228231]=2',
    'units={makeUnit(2228231,21,18,2)};queued_targets[2228231]=2;paths[2228231]={plots={1,3},turns={1,1}}',
    'units={makeUnit(2228231,21,18,2)};queued_targets[2228231]=2;paths[2228231]={plots={[1]=1,[3]=2},turns={1,1}}',
    'units={makeUnit(2228231,21,18,2)};queued_targets[2228231]=2;Players[0].GetCities=nil',
    'units={makeUnit(2228231,21,18,2)};queued_targets[2228231]=2;paths[2228231]={plots={1,2},turns={1,1}};core_busy=true',
])
def test_native_unknown_relevant_local_facts_never_submit_or_replace_queue(setup):
    game=NativeTurn();game.lua.execute(setup)
    assert 'END_TURN|NOT_SUBMITTED|READINESS_UNKNOWN' in game.run()
    assert game.lua.globals().requests==0 and game.lua.globals().mode_changes==0
    assert game.lua.eval('queued_targets[2228231]') in (None,2)


@pytest.mark.parametrize("change", ["seed=2", "civ='CIVILIZATION_BABYLON'", "local_player=1"])
def test_world_change_at_final_native_boundary_never_submits(change):
    game = NativeTurn()
    game.lua.execute("seed=1;civ='CIVILIZATION_SUMERIA';local_player=0;" + change)
    assert "END_TURN|NOT_SUBMITTED|WORLD_CHANGED" in game.run()
    assert game.lua.globals().requests == 0
    assert game.lua.globals().mode_changes == 0


@pytest.mark.parametrize("setup", ["seed=nil", "civ='unknown'", "local_player=nil", "seed=0/0"])
def test_unknown_world_at_final_native_boundary_never_submits(setup):
    game = NativeTurn()
    game.lua.execute(setup)
    assert "END_TURN|NOT_SUBMITTED|WORLD_UNKNOWN" in game.run()
    assert game.lua.globals().requests == 0 and game.lua.globals().mode_changes == 0


@pytest.mark.parametrize("mutation", ["seed=2", "turn=79"])
def test_world_and_turn_are_rechecked_after_native_precondition_callbacks(mutation):
    game = NativeTurn()
    game.lua.execute('UI.CanEndTurn=function() ' + mutation + ';return true end')
    assert any(row.startswith("END_TURN|NOT_SUBMITTED|") for row in game.run())
    assert game.lua.globals().requests == 0 and game.lua.globals().mode_changes == 0


def test_world_is_rechecked_between_mode_recovery_and_actual_end_request():
    game = NativeTurn()
    game.lua.execute('UI.SetInterfaceMode=function(value) mode=value;mode_changes=mode_changes+1;seed=2 end')
    assert "END_TURN|NOT_SUBMITTED|WORLD_CHANGED" in game.run()
    assert game.lua.globals().requests == 0
