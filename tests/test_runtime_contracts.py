"""Operation protocol tests for Runtime Core Replacement task A1."""

from __future__ import annotations

import pytest

from civ_mcp.runtime.contracts import (
    BranchIdentity,
    Evidence,
    EvidenceRequiredError,
    GameIdentity,
    IntentMismatchError,
    OperationId,
    OperationIntent,
    OperationRecord,
    OperationStateError,
    OutcomeState,
    SendState,
)


def _operation() -> OperationRecord:
    game_id = GameIdentity("game-france-001")
    return OperationRecord.create(
        game_id=game_id,
        branch_id=BranchIdentity(game_id, "branch-main"),
        decision_turn=42,
        intent=OperationIntent.create("move_unit", {"unit_id": 7, "x": 11, "y": 5}),
        operation_id=OperationId("operation-move-001"),
    )


def _evidence() -> Evidence:
    return Evidence(
        source="read_units",
        observed_turn=42,
        detail="unit 7 is at (11, 5)",
    )


def test_contracts_import_without_the_old_runtime() -> None:
    operation = _operation()

    assert operation.send_state is SendState.CREATED
    assert operation.outcome_state is OutcomeState.NOT_STARTED
    assert operation.intent.intent_hash


def test_invalid_lifecycle_transition_is_rejected() -> None:
    with pytest.raises(OperationStateError):
        _operation().sending()


def test_unknown_cannot_be_confirmed_without_new_evidence() -> None:
    unknown = _operation().prechecked().sending().maybe_sent().unknown()

    with pytest.raises(EvidenceRequiredError):
        unknown.confirmed(None)  # type: ignore[arg-type]

    assert unknown.confirmed(_evidence()).outcome_state is OutcomeState.CONFIRMED


def test_confirmed_operation_cannot_return_to_created() -> None:
    confirmed = _operation().prechecked().sending().maybe_sent().confirmed(_evidence())

    with pytest.raises(OperationStateError):
        confirmed.prechecked()


def test_operation_id_cannot_be_reused_for_a_different_intent() -> None:
    operation = _operation()
    changed = OperationIntent.create("move_unit", {"unit_id": 7, "x": 12, "y": 5})

    with pytest.raises(IntentMismatchError):
        operation.assert_same_intent(changed)


def test_stale_intent_never_crosses_the_send_boundary() -> None:
    stale = _operation().prechecked().stale_intent()

    assert stale.send_state is SendState.NOT_SENT
    assert stale.outcome_state is OutcomeState.STALE_INTENT


def test_branch_must_belong_to_the_operation_game() -> None:
    foreign_game = GameIdentity("game-england-002")

    with pytest.raises(ValueError, match="branch_id"):
        OperationRecord.create(
            game_id=GameIdentity("game-france-001"),
            branch_id=BranchIdentity(foreign_game, "branch-main"),
            decision_turn=42,
            intent=OperationIntent.create("move_unit", {"unit_id": 7}),
        )


def test_runtime_owned_branch_identity_is_scoped_and_rejects_full_ids() -> None:
    game = GameIdentity("game-france-001")

    assert BranchIdentity.from_token(game, "save-0001") == BranchIdentity(
        game, "game-france-001:save-0001"
    )

    with pytest.raises(ValueError, match="branch_token"):
        BranchIdentity.from_token(game, "game-france-001:save-0001")
