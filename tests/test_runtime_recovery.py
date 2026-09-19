"""J1/J2: recovery never changes operation facts or guesses a loaded save."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from civ_mcp.runtime.contracts import (
    BranchIdentity,
    ContractViolation,
    GameIdentity,
    OperationId,
    OperationIntent,
    OperationRecord,
    OutcomeState,
)
from civ_mcp.runtime.recovery import (
    RecoveredBinding,
    RecoveryCheckpoint,
    RecoveryInput,
    RecoveryOutcome,
    RecoverySupervisor,
    VerifiedRecoveryDriver,
)


def _unknown() -> OperationRecord:
    game = GameIdentity("game-a")
    return OperationRecord.create(
        game_id=game,
        branch_id=BranchIdentity.from_token(game, "main"),
        decision_turn=10,
        operation_id=OperationId("move-1"),
        intent=OperationIntent.create("move_unit", {"unit": 1}),
    ).prechecked().sending().maybe_sent().unknown()


def _checkpoint(
    game: GameIdentity,
    checkpoint_id: str = "auto-10",
    expected_turn: int = 10,
) -> RecoveryCheckpoint:
    return RecoveryCheckpoint(checkpoint_id, game, expected_turn)


def _request(
    game: GameIdentity,
    *,
    branch_token: str = "main",
    operation: OperationRecord | None = None,
    checkpoints: tuple[RecoveryCheckpoint, ...] | None = None,
) -> RecoveryInput:
    return RecoveryInput(
        game,
        BranchIdentity.from_token(game, branch_token),
        operation,
        process_running=True,
        connection_available=False,
        checkpoints=checkpoints if checkpoints is not None else (_checkpoint(game),),
    )


def test_recovery_creates_a_new_branch_without_reclassifying_unknown() -> None:
    operation = _unknown()
    calls = 0

    async def driver(_request):
        nonlocal calls
        calls += 1
        return RecoveredBinding(operation.game_id, "auto-10", 10, "after-recovery")

    result = asyncio.run(
        RecoverySupervisor(driver).recover(
            _request(operation.game_id, operation=operation)
        )
    )

    assert result.outcome is RecoveryOutcome.RECOVERED
    assert result.new_branch == BranchIdentity.from_token(operation.game_id, "after-recovery")
    assert result.checkpoint == _checkpoint(operation.game_id)
    assert operation.outcome_state is OutcomeState.UNKNOWN
    assert calls == 1


def test_missing_checkpoint_requires_operator_without_starting_recovery() -> None:
    async def driver(_request):
        raise AssertionError("must not run")

    game = GameIdentity("game-a")
    result = asyncio.run(
        RecoverySupervisor(driver).recover(_request(game, checkpoints=()))
    )

    assert result.outcome is RecoveryOutcome.NEEDS_OPERATOR


def test_recovery_identity_mismatch_fails_safe_without_creating_branch() -> None:
    game = GameIdentity("game-a")

    async def driver(_request):
        return RecoveredBinding(GameIdentity("game-b"), "auto-10", 10, "wrong-game")

    result = asyncio.run(RecoverySupervisor(driver).recover(_request(game)))

    assert result.outcome is RecoveryOutcome.FAILED_SAFE
    assert result.new_branch is None


def test_recovery_cannot_reuse_the_original_branch() -> None:
    game = GameIdentity("game-a")

    async def driver(_request):
        return RecoveredBinding(game, "auto-10", 10, "main")

    result = asyncio.run(
        RecoverySupervisor(driver).recover(_request(game, operation=_unknown()))
    )

    assert result.outcome is RecoveryOutcome.FAILED_SAFE
    assert result.new_branch is None
    assert "branch token" in result.reason


def test_port_busy_or_bad_save_requires_operator_without_declaring_crash() -> None:
    game = GameIdentity("game-a")

    async def driver(_request):
        return None  # host could not acquire FireTuner or load a stable checkpoint

    result = asyncio.run(
        RecoverySupervisor(driver).recover(_request(game, operation=_unknown()))
    )

    assert result.outcome is RecoveryOutcome.NEEDS_OPERATOR
    assert "未确认" in result.reason


def test_recovery_rejects_a_full_branch_id_from_the_host_driver() -> None:
    game = GameIdentity("game-a")

    async def driver(_request):
        return RecoveredBinding(game, "auto-10", 10, "game-a:after-recovery")

    result = asyncio.run(
        RecoverySupervisor(driver).recover(_request(game, operation=_unknown()))
    )

    assert result.outcome is RecoveryOutcome.FAILED_SAFE
    assert result.new_branch is None


def test_recovery_rejects_a_checkpoint_outside_the_captured_inventory() -> None:
    game = GameIdentity("game-a")

    async def driver(_request):
        return RecoveredBinding(game, "unrelated-save", 10, "after-recovery")

    result = asyncio.run(
        RecoverySupervisor(driver).recover(_request(game, operation=_unknown()))
    )

    assert result.outcome is RecoveryOutcome.FAILED_SAFE
    assert result.new_branch is None
    assert result.checkpoint is None
    assert "inventory" in result.reason


def test_recovery_rejects_the_wrong_turn_after_loading_a_checkpoint() -> None:
    game = GameIdentity("game-a")

    async def driver(_request):
        return RecoveredBinding(game, "auto-10", 11, "after-recovery")

    result = asyncio.run(
        RecoverySupervisor(driver).recover(_request(game, operation=_unknown()))
    )

    assert result.outcome is RecoveryOutcome.FAILED_SAFE
    assert result.new_branch is None
    assert "turn" in result.reason


def test_recovery_input_rejects_an_operation_from_another_branch() -> None:
    game = GameIdentity("game-a")

    with pytest.raises(ContractViolation, match="operation"):
        _request(
            game,
            branch_token="another-branch",
            operation=_unknown(),
        )


def test_recovery_input_rejects_a_checkpoint_from_another_game() -> None:
    game = GameIdentity("game-a")

    with pytest.raises(ContractViolation, match="checkpoint"):
        _request(game, checkpoints=(_checkpoint(GameIdentity("game-b")),))


class _ProbeAdapter:
    def __init__(self, identities: list[GameIdentity], turns: list[int]) -> None:
        self._identities = iter(identities)
        self._turns = iter(turns)

    async def read_game_identity(self):
        return SimpleNamespace(value=next(self._identities))

    async def read_overview(self):
        return SimpleNamespace(value=SimpleNamespace(turn=next(self._turns)))


def test_verified_driver_probes_a_stable_live_binding_after_host_recovery() -> None:
    game = GameIdentity("game-a")
    adapter = _ProbeAdapter([game, game], [10, 10])
    driver = VerifiedRecoveryDriver(
        adapter,  # type: ignore[arg-type]
        checkpoint_id="auto-10",
        branch_token="after-recovery",
    )

    result = asyncio.run(RecoverySupervisor(driver).recover(_request(game)))

    assert result.outcome is RecoveryOutcome.RECOVERED
    assert result.new_branch == BranchIdentity.from_token(game, "after-recovery")
    assert result.checkpoint == _checkpoint(game)


def test_verified_driver_treats_a_changing_live_turn_as_inconclusive() -> None:
    game = GameIdentity("game-a")
    adapter = _ProbeAdapter([game, game], [10, 11])
    driver = VerifiedRecoveryDriver(
        adapter,  # type: ignore[arg-type]
        checkpoint_id="auto-10",
        branch_token="after-recovery",
    )

    result = asyncio.run(RecoverySupervisor(driver).recover(_request(game)))

    assert result.outcome is RecoveryOutcome.NEEDS_OPERATOR
    assert result.new_branch is None
