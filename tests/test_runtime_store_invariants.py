"""Persistence invariants for Runtime Core Replacement task C2."""

from __future__ import annotations

import pytest

from civ_mcp.runtime.contracts import (
    BranchIdentity,
    Evidence,
    GameIdentity,
    OperationId,
    OperationIntent,
    OperationRecord,
    OutcomeState,
)
from civ_mcp.runtime.store import (
    HandoffNote,
    OperationBranchMismatchError,
    OperationStore,
    OperationStoreError,
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


def _unknown_operation(store: OperationStore) -> OperationRecord:
    record = _operation()
    prechecked = record.prechecked()
    sending = prechecked.sending()
    maybe_sent = sending.maybe_sent()
    unknown = maybe_sent.unknown()
    for state in (record, prechecked, sending, maybe_sent, unknown):
        store.save_operation(state)
    return unknown


def _evidence() -> Evidence:
    return Evidence(
        source="read_units",
        observed_turn=42,
        detail="unit 7 is at (11, 5)",
    )


def test_unknown_survives_process_restart_and_late_evidence_can_close_it(tmp_path) -> None:
    database = tmp_path / "runtime.sqlite3"
    with OperationStore(database) as store:
        unknown = _unknown_operation(store)

    with OperationStore(database) as reopened:
        loaded = reopened.get_operation(unknown.operation_id)
        assert loaded is not None
        assert loaded.outcome_state is OutcomeState.UNKNOWN

        reopened.save_operation(loaded.confirmed(_evidence()))
        assert reopened.get_operation(unknown.operation_id).outcome_state is OutcomeState.CONFIRMED


def test_old_branch_operation_cannot_be_reused_after_a_load(tmp_path) -> None:
    with OperationStore(tmp_path / "runtime.sqlite3") as store:
        unknown = _unknown_operation(store)
        new_branch = BranchIdentity(unknown.game_id, "branch-after-load")

        assert store.get_operation(unknown.operation_id) is not None
        with pytest.raises(OperationBranchMismatchError):
            store.get_operation_for_branch(unknown.operation_id, new_branch)


def test_sqlite_write_failure_does_not_fabricate_a_confirmed_result(tmp_path) -> None:
    with OperationStore(tmp_path / "runtime.sqlite3") as store:
        unknown = _unknown_operation(store)
        assert store._connection is not None
        store._connection.execute(
            """
            CREATE TRIGGER reject_operation_update BEFORE UPDATE ON operations
            BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END;
            """
        )

        with pytest.raises(OperationStoreError):
            store.save_operation(unknown.confirmed(_evidence()))

        persisted = store.get_operation(unknown.operation_id)
        assert persisted is not None
        assert persisted.outcome_state is OutcomeState.UNKNOWN
        assert persisted.evidence == ()


def test_handoff_note_is_separate_from_operation_facts(tmp_path) -> None:
    with OperationStore(tmp_path / "runtime.sqlite3") as store:
        unknown = _unknown_operation(store)
        note = HandoffNote(
            game_id=unknown.game_id,
            branch_id=unknown.branch_id,
            strategic_focus="secure the eastern frontier",
            existing_arrangements="warrior remains on the river approach",
            rationale="barbarian pressure is the immediate risk",
            change_conditions="switch after the camp is cleared or a rival declares war",
        )

        store.save_handoff_note(note)

        assert store.get_handoff_note(unknown.branch_id) == note
        persisted = store.get_operation(unknown.operation_id)
        assert persisted is not None
        assert persisted.outcome_state is OutcomeState.UNKNOWN
        assert persisted.evidence == ()
