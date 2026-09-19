"""M10: Runtime telemetry records facts but has no execution authority."""

from __future__ import annotations

from datetime import UTC, datetime
import json

from civ_mcp.runtime.contracts import BranchIdentity, GameIdentity, OperationId, OperationIntent, OperationRecord
from civ_mcp.runtime.telemetry import GameTelemetryEvent, RuntimeTelemetryLog


def _operation() -> OperationRecord:
    game = GameIdentity("game-a")
    return OperationRecord.create(
        game_id=game,
        branch_id=BranchIdentity.from_token(game, "save-0001"),
        decision_turn=10,
        operation_id=OperationId("move-1"),
        intent=OperationIntent.create("move_unit", {"unit_index": 7, "x": 1, "y": 2}),
    ).prechecked().sending().maybe_sent().unknown()


def test_telemetry_records_operation_fact_without_mutating_its_lifecycle(tmp_path) -> None:
    operation = _operation()
    log = RuntimeTelemetryLog(tmp_path / "runtime.jsonl")

    log.record_operation(operation)

    payload = json.loads(log.path.read_text(encoding="utf-8"))
    assert payload["kind"] == "operation"
    assert payload["operation_id"] == operation.operation_id.value
    assert payload["intent_hash"] == operation.intent.intent_hash
    assert "arguments_json" not in payload
    assert payload["outcome_state"] == "UNKNOWN"
    assert operation.outcome_state.value == "UNKNOWN"


def test_telemetry_records_a_supplied_game_observation_without_a_game_read(tmp_path) -> None:
    game = GameIdentity("game-a")
    event = GameTelemetryEvent(
        branch_id=BranchIdentity.from_token(game, "save-0001"),
        observed_turn=10,
        source="host:runtime-smoke",
        detail="context read completed",
        captured_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    log = RuntimeTelemetryLog(tmp_path / "runtime.jsonl")

    log.record_game_event(event)

    payload = json.loads(log.path.read_text(encoding="utf-8"))
    assert payload == {
        "branch_id": "game-a:save-0001",
        "captured_at": "2026-09-20T00:00:00+00:00",
        "detail": "context read completed",
        "game_id": "game-a",
        "kind": "game_observation",
        "observed_turn": 10,
        "recorded_at": payload["recorded_at"],
        "source": "host:runtime-smoke",
    }
