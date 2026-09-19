"""Read-only game/branch ownership for the Runtime Core."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from civ_mcp.civ.adapter import CivAdapter, CivMutationRequest, CivReadRequest, CivReadResult
from civ_mcp.runtime.contracts import BranchIdentity, Evidence, GameIdentity, OperationId, OperationIntent, OperationRecord, SendState
from civ_mcp.runtime.store import HandoffNote, OperationStore


T = TypeVar("T")
IdentityProbe = Callable[[], Awaitable[GameIdentity]]
TurnProbe = Callable[[], Awaitable[int]]
EvidenceProbe = Callable[[], Awaitable[Evidence | None]]
MutationPrecheck = Callable[[], Awaitable[None]]


async def _no_precheck() -> None:
    """Default for mutations whose domain has no additional baseline contract."""


class SessionIdentityMismatchError(RuntimeError):
    """The requested binding does not match the game observed from Civ6."""


class StaleSessionRequestError(RuntimeError):
    """A delayed read returned after its game/branch binding changed."""


class StaleIntentError(RuntimeError):
    """The model's decision turn or game identity is no longer current."""


class MutationPreconditionError(RuntimeError):
    """A fresh domain baseline makes the requested mutation inapplicable."""


@dataclass(frozen=True, slots=True)
class SessionBinding:
    game_id: GameIdentity
    branch_id: BranchIdentity
    generation: int


@dataclass(frozen=True, slots=True)
class MutationExecution:
    """One model intent and the domain readback that can prove its outcome."""

    intent: OperationIntent
    request: CivMutationRequest
    verify: EvidenceProbe
    operation_id: OperationId
    precheck: MutationPrecheck = _no_precheck


class SessionKernel:
    """Single in-process owner of the current game and branch, read-only for E1."""

    def __init__(
        self,
        adapter: CivAdapter,
        store: OperationStore,
        *,
        identity_probe: IdentityProbe,
        turn_probe: TurnProbe,
    ) -> None:
        self._adapter = adapter
        self._store = store
        self._identity_probe = identity_probe
        self._turn_probe = turn_probe
        self._binding: SessionBinding | None = None
        self._lock = asyncio.Lock()
        self._mutation_lock = asyncio.Lock()

    @property
    def binding(self) -> SessionBinding | None:
        return self._binding

    def current_operations(self) -> list[OperationRecord]:
        return self._store.list_operations_for_branch(self._require_binding().branch_id)

    def handoff_note(self) -> HandoffNote | None:
        return self._store.get_handoff_note(self._require_binding().branch_id)

    def save_handoff(
        self,
        *,
        strategic_focus: str,
        existing_arrangements: str,
        rationale: str,
        change_conditions: str,
    ) -> HandoffNote:
        """Save only strategic continuity; this cannot modify operation facts."""
        binding = self._require_binding()
        note = HandoffNote(
            game_id=binding.game_id,
            branch_id=binding.branch_id,
            strategic_focus=strategic_focus,
            existing_arrangements=existing_arrangements,
            rationale=rationale,
            change_conditions=change_conditions,
        )
        self._store.save_handoff_note(note)
        return note

    async def bind(self, game_id: GameIdentity, branch_id: BranchIdentity) -> SessionBinding:
        if branch_id.game_id != game_id:
            raise SessionIdentityMismatchError("branch_id 不属于待绑定的 game_id。")
        observed = await self._identity_probe()
        if observed != game_id:
            raise SessionIdentityMismatchError("Civ6 当前对局与请求绑定的 game_id 不一致。")
        async with self._lock:
            generation = 1 if self._binding is None else self._binding.generation + 1
            self._binding = SessionBinding(game_id, branch_id, generation)
            return self._binding

    async def read(
        self, request: CivReadRequest[T], *, observed_turn: int
    ) -> CivReadResult[T]:
        binding = self._require_binding()
        result = await self._adapter.read(request, observed_turn=observed_turn)
        if self._binding != binding:
            raise StaleSessionRequestError("读取结果属于已切换的 game/branch，已拒绝使用。")
        return result

    async def execute(self, execution: MutationExecution, *, decision_turn: int) -> OperationRecord:
        """Submit a mutation exactly once and close it only with domain Evidence."""
        if execution.intent.tool != execution.request.tool:
            raise ValueError("OperationIntent 与 CivMutationRequest 的 tool 必须一致。")
        async with self._mutation_lock:
            binding = self._require_binding()
            if await self._identity_probe() != binding.game_id or await self._turn_probe() != decision_turn:
                raise StaleIntentError("对局或回合已变化，模型意图未发送。")
            existing = self._store.get_operation(execution.operation_id)
            if existing is not None:
                existing.assert_same_intent(execution.intent)
                if existing.game_id != binding.game_id or existing.branch_id != binding.branch_id:
                    raise StaleIntentError("operation 属于另一条 game/branch，未发送。")
                return existing
            await execution.precheck()
            if (
                self._binding != binding
                or await self._identity_probe() != binding.game_id
                or await self._turn_probe() != decision_turn
            ):
                raise StaleIntentError("采集领域 baseline 后对局或回合已变化，模型意图未发送。")
            record = OperationRecord.create(
                game_id=binding.game_id,
                branch_id=binding.branch_id,
                decision_turn=decision_turn,
                intent=execution.intent,
                operation_id=execution.operation_id,
            )
            self._store.save_operation(record)
            record = record.prechecked()
            self._store.save_operation(record)
            record = record.sending()
            self._store.save_operation(record)
            try:
                receipt = await self._adapter.submit(execution.request)
            except asyncio.CancelledError:
                self._persist_unknown_after_submit_interruption(record)
                raise
            except Exception:
                return self._persist_unknown_after_submit_interruption(record)
            if receipt.send_state is SendState.NOT_SENT:
                record = record.not_sent()
                self._store.save_operation(record)
                return record
            record = record.maybe_sent()
            self._store.save_operation(record)
            if not receipt.complete:
                record = record.unknown()
                self._store.save_operation(record)
                return record
            try:
                evidence = await execution.verify()
            except asyncio.CancelledError:
                record = record.unknown()
                self._store.save_operation(record)
                raise
            except Exception:
                evidence = None
            if evidence is not None:
                try:
                    same_binding = self._binding == binding
                    same_game = await self._identity_probe() == binding.game_id
                except Exception:
                    same_binding = False
                    same_game = False
                if not same_binding or not same_game:
                    evidence = None
            record = record.confirmed(evidence) if evidence is not None else record.unknown()
            self._store.save_operation(record)
            return record

    def _persist_unknown_after_submit_interruption(
        self, record: OperationRecord
    ) -> OperationRecord:
        """Close a post-submit interruption conservatively before returning."""
        record = record.maybe_sent()
        self._store.save_operation(record)
        record = record.unknown()
        self._store.save_operation(record)
        return record

    def _require_binding(self) -> SessionBinding:
        if self._binding is None:
            raise SessionIdentityMismatchError("SessionKernel 尚未绑定当前 Civ6 对局。")
        return self._binding
