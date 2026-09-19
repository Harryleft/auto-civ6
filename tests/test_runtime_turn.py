"""F1 regressions: a turn is submitted once and never self-recovers."""

from __future__ import annotations

import asyncio

import pytest

from civ_mcp.runtime.contracts import BranchIdentity, Evidence, GameIdentity, OperationId, OperationIntent, OperationRecord, OutcomeState, SendState
from civ_mcp.runtime.session import MutationExecution
from civ_mcp.runtime.turn import DecisionInterrupt, TurnLoop, TurnObservation, TurnOutcome, TurnResult


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


def _not_sent_record() -> OperationRecord:
    game = GameIdentity("game-a")
    return OperationRecord.create(
        game_id=game,
        branch_id=BranchIdentity(game, "main"),
        decision_turn=10,
        operation_id=OperationId("end-turn-not-sent"),
        intent=OperationIntent.create("end_turn", {}),
    ).prechecked().sending().not_sent()


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


def test_unknown_end_turn_waits_for_fresh_evidence_without_resubmission() -> None:
    class Session:
        calls = 0
        confirmations = 0

        async def execute(self, *_args, **_kwargs):
            self.calls += 1
            return _record(OutcomeState.UNKNOWN)

        async def confirm_observed(self, operation, evidence):
            self.confirmations += 1
            return operation.confirmed(evidence)

    async def observe(_operation):
        return TurnObservation(Evidence("read_overview", 11, "turn 10 -> 11"))

    async def verify():
        return None

    session = Session()
    result = asyncio.run(TurnLoop(session, observer=observe).end_turn(
        MutationExecution(OperationIntent.create("end_turn", {}), None, verify, OperationId("end-turn-1")),
        decision_turn=10,
    ))
    assert result.outcome is TurnOutcome.ADVANCED
    assert result.operation.outcome_state is OutcomeState.CONFIRMED
    assert session.calls == 1
    assert session.confirmations == 1


def test_not_sent_end_turn_requires_a_new_decision_without_waiting() -> None:
    class Session:
        async def execute(self, *_args, **_kwargs):
            return _not_sent_record()

    async def observe(_operation):
        raise AssertionError("a NOT_SENT operation must not enter turn waiting")

    async def verify():
        return None

    result = asyncio.run(TurnLoop(Session(), observer=observe).end_turn(
        MutationExecution(OperationIntent.create("end_turn", {}), None, verify, OperationId("end-turn-not-sent")),
        decision_turn=10,
    ))
    assert result.outcome is TurnOutcome.NEEDS_DECISION
    assert result.operation.send_state is SendState.NOT_SENT


def test_interrupt_resumes_the_original_operation_without_another_end_turn() -> None:
    class Session:
        calls = 0

        async def execute(self, *_args, **_kwargs):
            self.calls += 1
            return _record(OutcomeState.CONFIRMED)

    session = Session()
    loop = TurnLoop(session)
    operation = _record(OutcomeState.CONFIRMED)
    choices: list[str] = []

    async def continuation(choice: str):
        choices.append(choice)
        return await _advanced(operation)

    result = loop.needs_decision(
        operation,
        decision_type="WORLD_CONGRESS",
        facts={"turn": 10},
        allowed_choices=("vote_a", "vote_b"),
        continuation=continuation,
    )
    assert result.decision.continuation_operation_id == operation.operation_id
    resumed = asyncio.run(loop.resume(operation.operation_id, "vote_a"))
    assert resumed.outcome is TurnOutcome.ADVANCED
    assert choices == ["vote_a"]
    assert session.calls == 0


def test_pending_decisions_expose_facts_but_not_continuations() -> None:
    class Session:
        pass

    loop = TurnLoop(Session())
    operation = _record(OutcomeState.UNKNOWN)

    async def continuation(_choice: str):
        raise AssertionError("context inspection must not invoke a continuation")

    loop.needs_decision(
        operation,
        decision_type="DIPLOMACY",
        facts={"leader": "Catherine"},
        allowed_choices=("POSITIVE",),
        continuation=continuation,
    )

    assert loop.pending_decisions() == (
        DecisionInterrupt(
            "DIPLOMACY", {"leader": "Catherine"}, ("POSITIVE",), operation.operation_id
        ),
    )


async def _advanced(operation: OperationRecord) -> TurnResult:
    return TurnResult(TurnOutcome.ADVANCED, operation)


def test_wait_advances_without_a_second_session_execute() -> None:
    class Session:
        calls = 0

        async def confirm_observed(self, operation, evidence):
            return operation.confirmed(evidence)

    observations = iter((TurnObservation(), TurnObservation(Evidence("read_overview", 11, "turn advanced"))))

    async def observe(_operation):
        return next(observations)

    async def no_sleep(_seconds: float):
        return None

    session = Session()
    result = asyncio.run(
        TurnLoop(session, observer=observe, sleep=no_sleep).wait_for_turn(
            _record(OutcomeState.UNKNOWN), diagnostic_polls=3
        )
    )
    assert result.outcome is TurnOutcome.ADVANCED
    assert session.calls == 0


def test_wait_threshold_requires_recovery_without_claiming_a_crash() -> None:
    class Session:
        pass

    async def observe(_operation):
        return TurnObservation()

    async def no_sleep(_seconds: float):
        return None

    result = asyncio.run(
        TurnLoop(Session(), observer=observe, sleep=no_sleep).wait_for_turn(
            _record(OutcomeState.UNKNOWN), diagnostic_polls=2
        )
    )
    assert result.outcome is TurnOutcome.RECOVERY_REQUIRED
    assert "不代表已崩溃" in result.reason


def test_wait_returns_only_an_interrupt_bound_to_the_original_operation() -> None:
    class Session:
        pass

    operation = _record(OutcomeState.UNKNOWN)
    interrupt = DecisionInterrupt("DIPLOMACY", {"leader": "Catherine"}, ("accept",), operation.operation_id)

    async def observe(_operation):
        return TurnObservation(interrupt=interrupt)

    result = asyncio.run(TurnLoop(Session(), observer=observe).wait_for_turn(operation))
    assert result.outcome is TurnOutcome.NEEDS_DECISION
    assert result.decision is interrupt


def test_observed_interrupt_registers_its_continuation_for_resume() -> None:
    class Session:
        pass

    operation = _record(OutcomeState.UNKNOWN)
    interrupt = DecisionInterrupt("DIPLOMACY", {"leader": "Catherine"}, ("POSITIVE",), operation.operation_id)
    choices: list[str] = []

    async def continuation(choice: str):
        choices.append(choice)
        return TurnResult(TurnOutcome.ADVANCED, operation)

    async def observe(_operation):
        return TurnObservation(interrupt=interrupt, continuation=continuation)

    loop = TurnLoop(Session(), observer=observe)
    observed = asyncio.run(loop.wait_for_turn(operation))
    resumed = asyncio.run(loop.resume(operation.operation_id, "POSITIVE"))

    assert observed.outcome is TurnOutcome.NEEDS_DECISION
    assert resumed.outcome is TurnOutcome.ADVANCED
    assert choices == ["POSITIVE"]


def test_failed_continuation_remains_resumable() -> None:
    class Session:
        pass

    loop = TurnLoop(Session())
    operation = _record(OutcomeState.UNKNOWN)
    attempts = 0

    async def continuation(_choice: str):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary precheck failure")
        return TurnResult(TurnOutcome.ADVANCED, operation)

    loop.needs_decision(
        operation,
        decision_type="DIPLOMACY",
        facts={},
        allowed_choices=("POSITIVE",),
        continuation=continuation,
    )
    with pytest.raises(RuntimeError, match="temporary"):
        asyncio.run(loop.resume(operation.operation_id, "POSITIVE"))
    assert asyncio.run(loop.resume(operation.operation_id, "POSITIVE")).outcome is TurnOutcome.ADVANCED
    assert attempts == 2


def test_wait_rejects_an_identity_change_without_closing_the_operation() -> None:
    class Session:
        async def confirm_observed(self, *_args):
            raise AssertionError("identity change must not close an operation")

    async def observe(_operation):
        return TurnObservation(identity_changed=True)

    result = asyncio.run(TurnLoop(Session(), observer=observe).wait_for_turn(_record(OutcomeState.UNKNOWN)))
    assert result.outcome is TurnOutcome.RECOVERY_REQUIRED
    assert "identity" in result.reason
