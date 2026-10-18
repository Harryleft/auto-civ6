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
local p = {IsTurnActive=function() return active end, IsTurnActiveComplete=function() return complete end,
           CanUnreadyTurn=function() return unready end}
Game = {GetLocalPlayer=function() return 0 end, GetCurrentGameTurn=function() return turn end}
Players = {[0]=p}
UI = {IsProcessingMessages=function() return processing end, HasSentTurnComplete=function() return sent end,
      CanEndTurn=function() return allowed end, GetInterfaceMode=function() return mode end,
      SetInterfaceMode=function(value) mode=value; mode_changes=mode_changes+1 end,
      RequestAction=function() requests=requests+1 end}
NotificationManager={GetFirstEndTurnBlocking=function() return blocker end}
EndTurnBlockingTypes={NO_ENDTURN_BLOCKING=0}
InterfaceModeTypes={SELECTION=1}
ActionTypes={ACTION_ENDTURN=12}
''')

    def run(self):
        self.lua.execute(build_end_turn(78))
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
