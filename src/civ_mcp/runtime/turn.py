"""The new, narrow end-turn state machine.

It sends one hash-bound end-turn operation through SessionKernel.  Waiting,
popup guessing, automatic resubmission, and recovery remain outside this
normal path; F2/F3 add explicit decision and recovery boundaries later.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from civ_mcp.runtime.contracts import OperationId, OperationRecord, OutcomeState
from civ_mcp.runtime.session import MutationExecution, SessionKernel


class TurnOutcome(StrEnum):
    ADVANCED = "ADVANCED"
    NEEDS_DECISION = "NEEDS_DECISION"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True, slots=True)
class DecisionInterrupt:
    """A game blocker whose choice must be made by the model."""

    decision_type: str
    facts: dict[str, object]
    allowed_choices: tuple[str, ...]
    continuation_operation_id: OperationId


@dataclass(frozen=True, slots=True)
class TurnResult:
    outcome: TurnOutcome
    operation: OperationRecord
    reason: str = ""
    decision: DecisionInterrupt | None = None


Continuation = Callable[[str], Awaitable[TurnResult]]


class TurnLoop:
    """Turn progression owns no transport, game process, or strategic policy."""

    def __init__(self, session: SessionKernel) -> None:
        self._session = session
        self._continuations: dict[OperationId, tuple[DecisionInterrupt, Continuation]] = {}

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

    def needs_decision(
        self,
        operation: OperationRecord,
        *,
        decision_type: str,
        facts: dict[str, object],
        allowed_choices: tuple[str, ...],
        continuation: Continuation,
    ) -> TurnResult:
        """Register an interrupt without submitting another end-turn request."""
        if not allowed_choices:
            raise ValueError("decision interrupt 必须提供 allowed_choices。")
        interrupt = DecisionInterrupt(
            decision_type=decision_type,
            facts=facts,
            allowed_choices=allowed_choices,
            continuation_operation_id=operation.operation_id,
        )
        self._continuations[operation.operation_id] = (interrupt, continuation)
        return TurnResult(TurnOutcome.NEEDS_DECISION, operation, decision=interrupt)

    async def resume(self, operation_id: OperationId, choice: str) -> TurnResult:
        """Apply one allowed choice to the original turn continuation only."""
        try:
            interrupt, continuation = self._continuations.pop(operation_id)
        except KeyError as exc:
            raise ValueError("没有可恢复的 turn decision interrupt。") from exc
        if choice not in interrupt.allowed_choices:
            self._continuations[operation_id] = (interrupt, continuation)
            raise ValueError("choice 不属于该 interrupt 的 allowed_choices。")
        return await continuation(choice)
