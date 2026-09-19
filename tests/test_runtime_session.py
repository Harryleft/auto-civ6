"""Task E1: SessionKernel owns one current game/branch for reads."""

from __future__ import annotations

import asyncio

import pytest

from civ_mcp.civ.adapter import CivMutationRequest, CivReadRequest, CivReadResult
from civ_mcp.runtime.contracts import BranchIdentity, Evidence, GameIdentity, OperationId, OperationIntent, OutcomeState, SendState
from civ_mcp.runtime.session import (
    MutationExecution,
    SessionIdentityMismatchError,
    SessionKernel,
    StaleIntentError,
    StaleSessionRequestError,
)
from civ_mcp.runtime.store import OperationStore
from civ_mcp.runtime.transport import TransportReceipt


class _Adapter:
    def __init__(self) -> None:
        self.gate: asyncio.Event | None = None

    async def read(self, request, *, observed_turn):
        if self.gate is not None:
            await self.gate.wait()
        return CivReadResult(
            value=request.decode(("answer",)),
            source="test",
            observed_turn=observed_turn,
            coverage="test",
        )


def _request() -> CivReadRequest[str]:
    return CivReadRequest("test", "return 1", lambda lines: lines[0], "test")


def test_can_switch_a_to_b_then_back_to_a() -> None:
    async def run() -> None:
        current = GameIdentity("game-a")

        async def probe() -> GameIdentity:
            return current

        kernel = SessionKernel(
            _Adapter(), OperationStore(":memory:"), identity_probe=probe, turn_probe=lambda: _turn(1)
        )
        a = BranchIdentity(GameIdentity("game-a"), "a-main")
        b_game = GameIdentity("game-b")
        b = BranchIdentity(b_game, "b-main")
        assert (await kernel.bind(a.game_id, a)).generation == 1
        current = b_game
        assert (await kernel.bind(b_game, b)).generation == 2
        current = a.game_id
        assert (await kernel.bind(a.game_id, a)).generation == 3

    asyncio.run(run())


def test_stale_precheck_creates_no_operation_and_sends_nothing(tmp_path) -> None:
    async def run() -> None:
        game = GameIdentity("game-a")

        async def probe() -> GameIdentity:
            return game

        class MutationAdapter(_Adapter):
            calls = 0

            async def submit(self, _request):
                self.calls += 1

        store = OperationStore(tmp_path / "operations.sqlite3")
        kernel = SessionKernel(MutationAdapter(), store, identity_probe=probe, turn_probe=lambda: _turn(11))
        await kernel.bind(game, BranchIdentity(game, "main"))
        operation_id = OperationId("stale-move")

        async def verify() -> None:
            return None

        with pytest.raises(StaleIntentError):
            await kernel.execute(
                MutationExecution(
                    OperationIntent.create("move_unit", {"unit_id": 1}),
                    CivMutationRequest("move_unit", "move()"), verify, operation_id,
                ),
                decision_turn=10,
            )
        assert store.get_operation(operation_id) is None

    asyncio.run(run())


def test_same_operation_id_never_submits_twice_after_unknown_transport_failure(tmp_path) -> None:
    async def run() -> None:
        game = GameIdentity("game-a")

        async def probe() -> GameIdentity:
            return game

        class MutationAdapter(_Adapter):
            calls = 0

            async def submit(self, _request):
                self.calls += 1
                raise ConnectionError("post-send connection lost")

        adapter = MutationAdapter()
        kernel = SessionKernel(adapter, OperationStore(tmp_path / "operations.sqlite3"), identity_probe=probe, turn_probe=lambda: _turn(10))
        await kernel.bind(game, BranchIdentity(game, "main"))
        operation_id = OperationId("maybe-sent-move")

        async def verify() -> None:
            return None

        execution = MutationExecution(
            OperationIntent.create("move_unit", {"unit_id": 1}),
            CivMutationRequest("move_unit", "move()"), verify, operation_id,
        )
        first = await kernel.execute(execution, decision_turn=10)
        second = await kernel.execute(execution, decision_turn=10)
        assert first.outcome_state is OutcomeState.UNKNOWN
        assert second.operation_id == operation_id
        assert adapter.calls == 1

    asyncio.run(run())


def test_cancelled_submit_is_persisted_as_unknown_and_never_resent(tmp_path) -> None:
    async def run() -> None:
        game = GameIdentity("game-a")

        async def probe() -> GameIdentity:
            return game

        class MutationAdapter(_Adapter):
            calls = 0

            async def submit(self, _request):
                self.calls += 1
                raise asyncio.CancelledError()

        adapter = MutationAdapter()
        store = OperationStore(tmp_path / "operations.sqlite3")
        kernel = SessionKernel(adapter, store, identity_probe=probe, turn_probe=lambda: _turn(10))
        await kernel.bind(game, BranchIdentity(game, "main"))
        operation_id = OperationId("cancelled-move")

        async def verify() -> None:
            return None

        execution = MutationExecution(
            OperationIntent.create("move_unit", {"unit_id": 1}),
            CivMutationRequest("move_unit", "move()"), verify, operation_id,
        )
        with pytest.raises(asyncio.CancelledError):
            await kernel.execute(execution, decision_turn=10)
        persisted = store.get_operation(operation_id)
        assert persisted is not None
        assert persisted.outcome_state is OutcomeState.UNKNOWN
        assert (await kernel.execute(execution, decision_turn=10)).operation_id == operation_id
        assert adapter.calls == 1

    asyncio.run(run())


def test_cancelled_evidence_readback_is_persisted_as_unknown(tmp_path) -> None:
    async def run() -> None:
        game = GameIdentity("game-a")

        async def probe() -> GameIdentity:
            return game

        class MutationAdapter(_Adapter):
            async def submit(self, _request):
                return TransportReceipt(SendState.MAYBE_SENT, True, (), True)

        store = OperationStore(tmp_path / "operations.sqlite3")
        kernel = SessionKernel(
            MutationAdapter(), store, identity_probe=probe, turn_probe=lambda: _turn(10)
        )
        await kernel.bind(game, BranchIdentity(game, "main"))
        operation_id = OperationId("cancelled-readback")

        async def verify() -> None:
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await kernel.execute(
                MutationExecution(
                    OperationIntent.create("move_unit", {"unit_id": 1}),
                    CivMutationRequest("move_unit", "move()"), verify, operation_id,
                ),
                decision_turn=10,
            )
        persisted = store.get_operation(operation_id)
        assert persisted is not None
        assert persisted.outcome_state is OutcomeState.UNKNOWN
        assert persisted.evidence == ()

    asyncio.run(run())


def test_identity_mismatch_is_rejected_before_binding() -> None:
    async def probe() -> GameIdentity:
        return GameIdentity("game-live")

    requested = GameIdentity("game-stale")
    kernel = SessionKernel(
        _Adapter(), OperationStore(":memory:"), identity_probe=probe, turn_probe=lambda: _turn(1)
    )
    with pytest.raises(SessionIdentityMismatchError):
        asyncio.run(kernel.bind(requested, BranchIdentity(requested, "old")))


def test_delayed_old_read_cannot_cross_into_a_new_session() -> None:
    async def run() -> None:
        current = GameIdentity("game-a")

        async def probe() -> GameIdentity:
            return current

        adapter = _Adapter()
        adapter.gate = asyncio.Event()
        kernel = SessionKernel(
            adapter, OperationStore(":memory:"), identity_probe=probe, turn_probe=lambda: _turn(10)
        )
        branch_a = BranchIdentity(current, "a-main")
        await kernel.bind(current, branch_a)
        delayed = asyncio.create_task(kernel.read(_request(), observed_turn=10))
        await asyncio.sleep(0)
        current = GameIdentity("game-b")
        await kernel.bind(current, BranchIdentity(current, "b-main"))
        adapter.gate.set()
        with pytest.raises(StaleSessionRequestError):
            await delayed

    asyncio.run(run())


async def _turn(value: int) -> int:
    return value


def test_mutation_is_confirmed_only_by_domain_readback(tmp_path) -> None:
    async def run() -> None:
        game = GameIdentity("game-a")

        async def probe() -> GameIdentity:
            return game

        class MutationAdapter(_Adapter):
            calls = 0

            async def submit(self, _request):
                self.calls += 1
                return TransportReceipt(SendState.MAYBE_SENT, True, (), True)

        adapter = MutationAdapter()
        store = OperationStore(tmp_path / "operations.sqlite3")
        kernel = SessionKernel(adapter, store, identity_probe=probe, turn_probe=lambda: _turn(10))
        branch = BranchIdentity(game, "main")
        await kernel.bind(game, branch)

        async def verify() -> Evidence:
            return Evidence("read_units", 10, "unit moved to (3,4)")

        record = await kernel.execute(
            MutationExecution(
                OperationIntent.create("move_unit", {"unit_id": 1, "x": 3, "y": 4}),
                CivMutationRequest("move_unit", "move()"),
                verify,
                OperationId("move-1"),
            ),
            decision_turn=10,
        )
        assert record.outcome_state is OutcomeState.CONFIRMED
        assert adapter.calls == 1
        assert store.get_operation(record.operation_id).evidence == record.evidence

    asyncio.run(run())


def test_handoff_can_change_strategy_without_changing_operation_facts(tmp_path) -> None:
    async def run() -> None:
        game = GameIdentity("game-a")

        async def probe() -> GameIdentity:
            return game

        store = OperationStore(tmp_path / "operations.sqlite3")
        kernel = SessionKernel(_Adapter(), store, identity_probe=probe, turn_probe=lambda: _turn(10))
        await kernel.bind(game, BranchIdentity(game, "main"))
        note = kernel.save_handoff(
            strategic_focus="secure the frontier",
            existing_arrangements="warrior watches the pass",
            rationale="barbarian camp nearby",
            change_conditions="camp removed",
        )
        assert kernel.handoff_note() == note
        assert kernel.current_operations() == []

    asyncio.run(run())
