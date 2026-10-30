"""Missing or partial native path data cannot imply current reachability."""
import asyncio

import pytest

from civ_mcp.game_state import GameState
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
