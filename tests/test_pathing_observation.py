"""Missing or partial native path data cannot imply current reachability."""
import asyncio

import pytest

from civ_mcp.game_state import GameState
from civ_mcp.lua.models import PathingEstimate
from civ_mcp.narrate import narrate_pathing_estimate


@pytest.mark.parametrize('rows',[[],['ERR:UNIT_NOT_FOUND'],['WAYPOINTS|'],['PATH|0|3|2']])
def test_missing_or_inconsistent_path_is_unknown(rows):
    class Conn:
        async def execute_write(self, _source):return rows
    result=asyncio.run(GameState(Conn()).get_pathing_estimate(9,1,1))
    assert result.turns is None
    assert 'unknown' in narrate_pathing_estimate(result).lower()


def test_future_turn_count_is_explicitly_a_heuristic_not_native_timing():
    class Conn:
        async def execute_write(self, _source):return ['PATH|3|8|2','WAYPOINTS|(1,1);(2,1);(3,1)']
    result=asyncio.run(GameState(Conn()).get_pathing_estimate(9,3,1))
    assert 'heuristic' in narrate_pathing_estimate(result)


@pytest.mark.parametrize("path_row", ["PATH|0|4|4", "PATH|2|4|2"])
def test_legacy_path_preserves_waypoints_without_promising_execution(path_row):
    class Conn:
        async def execute_write(self, _source):
            return [path_row, "WAYPOINTS|(14,18);(13,19);(12,19);(12,20)"]

    result = asyncio.run(GameState(Conn()).get_pathing_estimate(9, 12, 20))
    text = narrate_pathing_estimate(result)
    assert "Legacy heuristic preview" in text
    assert "(14,18) -> (13,19) -> (12,19) -> (12,20)" in text
    assert "Reachable this turn" not in text
    assert "does not guarantee displacement or route" in text


@pytest.mark.parametrize(
    ("turns", "summary"),
    [
        (None, "Path/reachability unknown: native observation missing or inconsistent."),
        (-2, "Unit has no moves remaining this turn."),
        (
            -1,
            "No native path to the destination returned. It may be in fog, "
            "behind foreign borders, or blocked by impassable terrain.",
        ),
    ],
)
def test_unavailable_path_does_not_present_waypoints_as_valid(turns, summary):
    result = PathingEstimate(turns, 3, 0, ["(14,18)", "(13,19)", "(12,20)"])

    assert narrate_pathing_estimate(result) == summary


class NativePathConn:
    """Execute the real query in the existing isolated movement engine fixture."""

    def __init__(self, setup=""):
        from test_action_lua_execution import MovementConn

        engine = MovementConn()
        self.lua = engine.lua
        self.rows = engine.rows
        self.lua.execute('''
unit.GetMovementMovesRemaining=function() return 1 end
unit.HasMovedIntoZOC=function() return true end
-- Deliberately disagree with the legacy geometric/reachable preview.
UnitManager.GetMoveToPath=function() return {17019,18018} end
UnitManager.GetReachableMovement=function() return {17019,18018} end
UnitManager.GetMoveToPathEx=function() return {plots={17019,18018},turns={1,2}} end
''')
        self.lua.execute(setup)

    async def execute_write(self, source):
        self.rows.clear()
        self.lua.execute(source)
        return [row for row in self.rows if row != "---END---"]


def test_native_path_timing_replaces_geometric_reachability_with_zoc_facts():
    conn = NativePathConn()
    result = asyncio.run(GameState(conn).get_pathing_estimate(6, 18, 18))
    text = narrate_pathing_estimate(result)
    assert result.turns == 1
    assert 'Native path preview' in text
    assert 'ZOC=true' in text
    assert 'Reachable this turn' not in text
    assert conn.lua.globals().submissions == 0


@pytest.mark.parametrize("setup", [
    "UnitManager.GetMoveToPathEx=nil",
    "Map.GetPlot=function() error('unavailable') end",
    "Map.GetPlotByIndex=function() error('unavailable') end",
    "Map.GetPlotByIndex=function() return {GetX=function() error('unavailable') end} end",
    "unit.GetX=function() error('unavailable') end",
    "UnitManager.GetMoveToPathEx=function() error('unavailable') end",
    "UnitManager.GetMoveToPathEx=function() return nil end",
    "UnitManager.GetMoveToPathEx=function() return {plots={17019,18018},turns={1}} end",
    "UnitManager.GetMoveToPathEx=function() return {plots={[1]=17019,[3]=18018},turns={1,2}} end",
    "UnitManager.GetMoveToPathEx=function() return {plots={17019,18018},turns={1,0/0}} end",
    "UnitManager.GetMoveToPathEx=function() return {plots={17019,18018},turns={1,math.huge}} end",
    "UnitManager.GetMoveToPathEx=function() return {plots={17019,18018},turns={2,1}} end",
    "UnitManager.GetMoveToPathEx=function() return {plots={17018,18018},turns={1,2}} end",
    "UnitManager.GetMoveToPathEx=function() return {plots={17019,18019},turns={1,2}} end",
    "UnitManager.GetMoveToPathEx=function() return {plots={17019,17019,18018},turns={1,1,2}} end",
    "UI.IsGameCoreBusy=function() return true end",
    "UI.IsGameCoreBusy=nil",
    "UI.IsGameCoreBusy=function() error('unavailable') end",
])
def test_native_path_unavailable_or_malformed_never_guesses_turns(setup):
    conn = NativePathConn(setup)
    result = asyncio.run(GameState(conn).get_pathing_estimate(6, 18, 18))
    text = narrate_pathing_estimate(result)
    assert result.turns is None
    assert result.moved_into_zoc is True
    assert result.movement_moves_remaining == 1
    assert 'unknown' in text
    assert 'Path preview:' not in text
    assert conn.lua.globals().submissions == 0


@pytest.mark.parametrize("setup, field", [
    ("unit.GetMovementMovesRemaining=nil", "movement_moves_remaining"),
    ("unit.GetMovementMovesRemaining=function() error('unavailable') end", "movement_moves_remaining"),
    ("unit.GetMovementMovesRemaining=function() return 0/0 end", "movement_moves_remaining"),
    ("unit.GetMovesRemaining=nil", "moves_remaining"),
    ("unit.HasMovedIntoZOC=nil", "moved_into_zoc"),
    ("unit.HasMovedIntoZOC=function() error('unavailable') end", "moved_into_zoc"),
    ("UnitManager.CanStartOperation=nil", "can_start_target"),
    ("UnitOperationMoveModifiers=nil", "can_start_target"),
])
def test_optional_movement_facts_remain_unknown_without_discarding_native_preview(setup, field):
    conn = NativePathConn(setup)
    result = asyncio.run(GameState(conn).get_pathing_estimate(6, 18, 18))
    assert result.turns == 1
    assert getattr(result, field) is None
    assert 'unknown' in narrate_pathing_estimate(result)
    assert conn.lua.globals().submissions == 0


@pytest.mark.parametrize("effective,zoc,can_start", [(0, True, True), (1, True, True), (1, False, False)])
def test_current_turn_preview_does_not_override_separate_native_movement_facts(effective, zoc, can_start):
    conn = NativePathConn(f"""
unit.GetMovementMovesRemaining=function() return {effective} end
unit.HasMovedIntoZOC=function() return {str(zoc).lower()} end
UnitManager.GetMoveToPathEx=function() return {{plots={{17019,18018}},turns={{1,1}}}} end
UnitManager.CanStartOperation=function() return {str(can_start).lower()} end
""")
    result = asyncio.run(GameState(conn).get_pathing_estimate(6, 18, 18))
    assert result.turns == 0
    assert result.movement_moves_remaining == effective
    assert result.moved_into_zoc is zoc
    assert result.can_start_target is can_start
    assert result.path_turns == [1, 1]
    text = narrate_pathing_estimate(result)
    assert 'destination labeled current turn' in text
    assert 'does not guarantee displacement or route' in text
    assert 'Reachable this turn' not in text
    assert conn.lua.globals().submissions == 0


@pytest.mark.parametrize("at_war,flags", [(False, 16384), (True, 16400)])
def test_target_eligibility_uses_move_flags_without_submitting(at_war, flags):
    conn = NativePathConn(f"""
Players[0].GetDiplomacy=function() return {{IsAtWarWith=function() return {str(at_war).lower()} end}} end
Map.GetUnitsAt=function() return {{Units=function()
    local done=false
    return function() if not done then done=true; return {{GetOwner=function() return 1 end}} end end
end}} end
UnitManager.CanStartOperation=function(u,operation,testVisible,params)
    assert(u==unit and operation==UnitOperationTypes.MOVE_TO and testVisible==nil)
    assert(params[UnitOperationTypes.PARAM_X]==18 and params[UnitOperationTypes.PARAM_Y]==18)
    assert(params[UnitOperationTypes.PARAM_MODIFIERS]=={flags})
    eligibility_checked=true
    return true
end
""")
    result = asyncio.run(GameState(conn).get_pathing_estimate(6, 18, 18))
    assert result.can_start_target is True
    assert result.move_flags == flags
    assert conn.lua.globals().eligibility_checked is True
    assert conn.lua.globals().submissions == 0


@pytest.mark.parametrize("visible,expected", [(True, -1), (False, None)])
def test_empty_native_path_only_means_no_path_when_destination_visible(visible, expected):
    conn = NativePathConn(f"""
UnitManager.GetMoveToPathEx=function() return {{plots={{}},turns={{}}}} end
PlayersVisibility[0].IsVisible=function() return {str(visible).lower()} end
""")
    result = asyncio.run(GameState(conn).get_pathing_estimate(6, 18, 18))
    assert result.turns == expected
    assert result.moved_into_zoc is True
    assert conn.lua.globals().submissions == 0
