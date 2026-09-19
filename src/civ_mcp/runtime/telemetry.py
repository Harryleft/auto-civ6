"""Append-only, non-controlling telemetry for Runtime operation facts.

This module deliberately receives immutable records from an outer host.  It
does not import SessionKernel, MCP routing, CivAdapter, or the operation store,
so logging can never decide a game action or become part of execution success.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path

from civ_mcp.runtime.contracts import BranchIdentity, ContractViolation, OperationRecord


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class GameTelemetryEvent:
    """A host-observed game fact; it has no mutation or verification authority."""

    branch_id: BranchIdentity
    observed_turn: int
    source: str
    detail: str
    captured_at: datetime

    def __post_init__(self) -> None:
        if self.observed_turn < 0:
            raise ContractViolation("telemetry observed_turn 不能为负数。")
        if not self.source.strip() or not self.detail.strip():
            raise ContractViolation("telemetry source 和 detail 不能为空。")


class RuntimeTelemetryLog:
    """A local JSONL sink that only records already-known Runtime facts."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def record_operation(self, operation: OperationRecord) -> None:
        """Append an immutable lifecycle observation without changing the operation."""
        self._append(
            {
                "kind": "operation",
                "operation_id": operation.operation_id.value,
                "game_id": operation.game_id.value,
                "branch_id": operation.branch_id.value,
                "decision_turn": operation.decision_turn,
                "tool": operation.intent.tool,
                "intent_hash": operation.intent.intent_hash,
                "send_state": operation.send_state.value,
                "outcome_state": operation.outcome_state.value,
                "evidence": [
                    {
                        "source": evidence.source,
                        "observed_turn": evidence.observed_turn,
                        "detail": evidence.detail,
                        "captured_at": evidence.captured_at.isoformat(),
                    }
                    for evidence in operation.evidence
                ],
                "recorded_at": _utc_now().isoformat(),
            }
        )

    def record_game_event(self, event: GameTelemetryEvent) -> None:
        """Append a supplied game observation without reading or controlling Civ6."""
        self._append(
            {
                "kind": "game_observation",
                "game_id": event.branch_id.game_id.value,
                "branch_id": event.branch_id.value,
                "observed_turn": event.observed_turn,
                "source": event.source,
                "detail": event.detail,
                "captured_at": event.captured_at.isoformat(),
                "recorded_at": _utc_now().isoformat(),
            }
        )

    def _append(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            stream.write("\n")
