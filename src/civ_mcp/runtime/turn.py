"""The new, narrow end-turn state machine.

It sends one hash-bound end-turn operation through SessionKernel, then waits
with read-only observations that can supply fresh Evidence. Popup guessing,
automatic resubmission, and recovery remain outside this normal path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from civ_mcp.runtime.contracts import Evidence, OperationId, OperationRecord, OutcomeState, SendState
from civ_mcp.runtime.session import MutationExecution, SessionKernel, StaleIntentError


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


@dataclass(frozen=True, slots=True)
class TurnObservation:
    """A phase-safe read during AI processing; it has no write capability."""

    evidence: Evidence | None = None
    interrupt: DecisionInterrupt | None = None
    continuation: Continuation | None = None
    identity_changed: bool = False


TurnObserver = Callable[[OperationRecord], Awaitable[TurnObservation]]
Sleep = Callable[[float], Awaitable[None]]


class TurnLoop:
    """Turn progression owns no transport, game process, or strategic policy."""

    def __init__(
        self,
        session: SessionKernel,
        *,
        observer: TurnObserver | None = None,
        sleep: Sleep | None = None,
    ) -> None:
        self._session = session
        self._continuations: dict[OperationId, tuple[DecisionInterrupt, Continuation]] = {}
        self._observer = observer
        self._sleep = sleep or _sleep

    async def end_turn(
        self, execution: MutationExecution, *, decision_turn: int
    ) -> TurnResult:
        record = await self._session.execute(execution, decision_turn=decision_turn)
        if record.outcome_state is OutcomeState.CONFIRMED:
            return TurnResult(TurnOutcome.ADVANCED, record)
        if record.outcome_state is OutcomeState.REJECTED:
            return TurnResult(TurnOutcome.NEEDS_DECISION, record, "游戏拒绝结束回合请求。")
        if record.send_state is SendState.NOT_SENT:
            return TurnResult(
                TurnOutcome.NEEDS_DECISION,
                record,
                "结束回合命令未越过发送边界；必须以新的 operation 重新决策。",
            )
        if self._observer is not None:
            return await self.wait_for_turn(record)
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
        try:
            return await continuation(choice)
        except BaseException:
            self._continuations[operation_id] = (interrupt, continuation)
            raise

    async def wait_for_turn(
        self,
        operation: OperationRecord,
        *,
        poll_interval: float = 1.0,
        diagnostic_polls: int = 10,
    ) -> TurnResult:
        """Wait with reads only; a long wait is never proof of a game crash."""
        if self._observer is None:
            raise RuntimeError("TurnLoop 没有配置只读 turn observer。")
        if diagnostic_polls < 1:
            raise ValueError("diagnostic_polls 必须大于 0。")
        for attempt in range(diagnostic_polls):
            observed = await self._observer(operation)
            if observed.identity_changed:
                return TurnResult(
                    TurnOutcome.RECOVERY_REQUIRED,
                    operation,
                    "等待期间 Civ6 对局 identity 已变化，拒绝关闭原 operation。",
                )
            if observed.evidence is not None:
                try:
                    confirmed = await self._session.confirm_observed(operation, observed.evidence)
                except StaleIntentError:
                    return TurnResult(
                        TurnOutcome.RECOVERY_REQUIRED,
                        operation,
                        "观察证据归属的 Civ6 对局已变化，拒绝关闭原 operation。",
                    )
                return TurnResult(TurnOutcome.ADVANCED, confirmed)
            if observed.interrupt is not None:
                if observed.interrupt.continuation_operation_id != operation.operation_id:
                    return TurnResult(
                        TurnOutcome.RECOVERY_REQUIRED,
                        operation,
                        "turn interrupt 归属另一条 operation，拒绝接管。",
                    )
                if observed.continuation is not None:
                    self._continuations[operation.operation_id] = (
                        observed.interrupt,
                        observed.continuation,
                    )
                return TurnResult(
                    TurnOutcome.NEEDS_DECISION, operation, decision=observed.interrupt
                )
            if attempt + 1 < diagnostic_polls:
                await self._sleep(poll_interval)
        return TurnResult(
            TurnOutcome.RECOVERY_REQUIRED,
            operation,
            "AI 回合在诊断阈值内未出现可验证进展；需显式恢复，不代表已崩溃。",
        )


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
