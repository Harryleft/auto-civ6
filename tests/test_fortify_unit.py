"""Regression tests for asynchronous fortify confirmation."""

import asyncio

from civ_mcp.game_state import GameState


class _StubConnection:
    def __init__(self, mutation_lines, readback_lines):
        self.mutation_lines = list(mutation_lines)
        self.readback_lines = list(readback_lines)
        self.mutation_calls = 0
        self.readback_calls = 0

    async def execute_mutation(self, lua, **_kwargs):
        self.mutation_calls += 1
        return self.mutation_lines.pop(0)

    async def execute_write(self, lua, **_kwargs):
        self.readback_calls += 1
        return self.readback_lines.pop(0)


def _unit_line(*, fortify_turns: int, moves: str = "0/2") -> str:
    return (
        "1|7|Warrior|UNIT_WARRIOR|3,4|"
        f"{moves}|100/100|20|0|0||||0|0|||{fortify_turns}|1"
    )


def _gs(connection: _StubConnection) -> GameState:
    gs = GameState.__new__(GameState)
    gs.conn = connection
    return gs


def test_fortify_confirms_async_request_without_resubmitting() -> None:
    conn = _StubConnection(
        [["ERR:OUTCOME_UNKNOWN|Fortify request returned without an observable state change"]],
        [
            [_unit_line(fortify_turns=0)],
            [_unit_line(fortify_turns=1)],
        ],
    )

    result = asyncio.run(_gs(conn).fortify_unit(7))

    assert result == "FORTIFIED|readback_fortify_turns:1|readback_moves:0"
    assert conn.mutation_calls == 1
    assert conn.readback_calls == 2


def test_fortify_keeps_unknown_when_state_is_never_confirmed() -> None:
    conn = _StubConnection(
        [["ERR:OUTCOME_UNKNOWN|Fortify request returned without an observable state change"]],
        [[_unit_line(fortify_turns=0)] for _ in range(4)],
    )

    result = asyncio.run(_gs(conn).fortify_unit(7))

    assert result.startswith("Error: OUTCOME_UNKNOWN|")
    assert conn.mutation_calls == 1
    assert conn.readback_calls == 4
