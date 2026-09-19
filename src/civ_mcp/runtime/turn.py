"""The new, narrow end-turn state machine.

It sends one hash-bound end-turn operation through SessionKernel.  Waiting,
popup guessing, automatic resubmission, and recovery remain outside this
normal path; F2/F3 add explicit decision and recovery boundaries later.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from civ_mcp.runtime.contracts import OperationRecord, OutcomeState
from civ_mcp.runtime.session import MutationExecution, SessionKernel


class TurnOutcome(StrEnum):
    ADVANCED = "ADVANCED"
    NEEDS_DECISION = "NEEDS_DECISION"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True, slots=True)
class TurnResult:
    outcome: TurnOutcome
    operation: OperationRecord
    reason: str = ""


class TurnLoop:
    """Turn progression owns no transport, game process, or strategic policy."""

    def __init__(self, session: SessionKernel) -> None:
        self._session = session

    async def end_turn(
        self, execution: MutationExecution, *, decision_turn: int
    ) -> TurnResult:
        record = await self._session.execute(execution, decision_turn=decision_turn)
        if record.outcome_state is OutcomeState.CONFIRMED:
            return TurnResult(TurnOutcome.ADVANCED, record)
        if record.outcome_state is OutcomeState.REJECTED:
            return TurnResult(TurnOutcome.NEEDS_DECISION, record, "游戏拒绝结束回合请求。")
        return TurnResult(
            TurnOutcome.RECOVERY_REQUIRED,
            record,
            "结束回合结果未确认；不得补发，需显式诊断或恢复。",
        )
