"""J1: recovery neither changes unknown operations nor retries mutations."""

from __future__ import annotations

import asyncio

from civ_mcp.runtime.contracts import BranchIdentity, GameIdentity, OperationId, OperationIntent, OperationRecord, OutcomeState
from civ_mcp.runtime.recovery import RecoveryInput, RecoveryOutcome, RecoverySupervisor


def _unknown() -> OperationRecord:
    game = GameIdentity("game-a")
    return OperationRecord.create(
        game_id=game, branch_id=BranchIdentity(game, "main"), decision_turn=10,
        operation_id=OperationId("move-1"), intent=OperationIntent.create("move_unit", {"unit": 1}),
    ).prechecked().sending().maybe_sent().unknown()


def test_recovery_creates_a_new_branch_without_reclassifying_unknown() -> None:
    operation = _unknown()
    calls = 0

    async def driver(_request):
        nonlocal calls
        calls += 1
        return "after-recovery"

    result = asyncio.run(RecoverySupervisor(driver).recover(
        RecoveryInput(operation.game_id, operation.branch_id, operation, False, False, ("auto-10",))
    ))
    assert result.outcome is RecoveryOutcome.RECOVERED
    assert result.new_branch.value == "after-recovery"
    assert operation.outcome_state is OutcomeState.UNKNOWN
    assert calls == 1


def test_missing_checkpoint_requires_operator_without_starting_recovery() -> None:
    async def driver(_request):
        raise AssertionError("must not run")

    game = GameIdentity("game-a")
    result = asyncio.run(RecoverySupervisor(driver).recover(
        RecoveryInput(game, BranchIdentity(game, "main"), None, True, False, ())
    ))
    assert result.outcome is RecoveryOutcome.NEEDS_OPERATOR
