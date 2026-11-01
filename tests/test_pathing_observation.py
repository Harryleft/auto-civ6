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


@pytest.mark.parametrize(
    ("path_row", "summary"),
    [
        ("PATH|0|4|4", "Reachable this turn (4 tiles in path, all within movement range)."),
        (
            "PATH|2|4|2",
            "~2 turns (heuristic assuming current reachable tiles per turn; terrain and "
            "future costs may differ; 4 tiles total, 2 reachable this turn)",
        ),
    ],
)
def test_path_query_preserves_every_native_waypoint(path_row, summary):
    class Conn:
        async def execute_write(self, _source):
            return [path_row, "WAYPOINTS|(14,18);(13,19);(12,19);(12,20)"]

    result = asyncio.run(GameState(Conn()).get_pathing_estimate(9, 12, 20))

    assert narrate_pathing_estimate(result) == (
        summary + "\n  Path: (14,18) -> (13,19) -> (12,19) -> (12,20)"
    )


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
