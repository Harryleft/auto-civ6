"""F1 regressions: a turn is submitted once and never self-recovers."""

from __future__ import annotations

import asyncio

from civ_mcp.runtime.contracts import BranchIdentity, GameIdentity, OperationId, OperationIntent, OperationRecord, OutcomeState
from civ_mcp.runtime.session import MutationExecution
from civ_mcp.runtime.turn import TurnLoop, TurnOutcome


def _record(outcome: OutcomeState) -> OperationRecord:
    game = GameIdentity("game-a")
    record = OperationRecord.create(
        game_id=game,
        branch_id=BranchIdentity(game, "main"),
        decision_turn=10,
        operation_id=OperationId("end-turn-1"),
        intent=OperationIntent.create("end_turn", {}),
    ).prechecked().sending().maybe_sent()
    if outcome is OutcomeState.UNKNOWN:
        return record.unknown()
    if outcome is OutcomeState.CONFIRMED:
        from civ_mcp.runtime.contracts import Evidence

        return record.confirmed(Evidence("read_overview", 11, "turn advanced"))
    return record


def test_confirmed_end_turn_is_advanced() -> None:
    class Session:
        async def execute(self, *_args, **_kwargs):
            return _record(OutcomeState.CONFIRMED)

    async def verify():
        return None

    result = asyncio.run(TurnLoop(Session()).end_turn(
        MutationExecution(OperationIntent.create("end_turn", {}), None, verify, OperationId("end-turn-1")),
        decision_turn=10,
    ))
    assert result.outcome is TurnOutcome.ADVANCED


def test_unknown_end_turn_requires_recovery_without_resubmission() -> None:
    class Session:
        calls = 0

        async def execute(self, *_args, **_kwargs):
            self.calls += 1
            return _record(OutcomeState.UNKNOWN)

    session = Session()

    async def verify():
        return None

    result = asyncio.run(TurnLoop(session).end_turn(
        MutationExecution(OperationIntent.create("end_turn", {}), None, verify, OperationId("end-turn-1")),
        decision_turn=10,
    ))
    assert result.outcome is TurnOutcome.RECOVERY_REQUIRED
    assert session.calls == 1
