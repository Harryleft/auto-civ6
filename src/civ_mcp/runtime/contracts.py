"""Immutable contracts for the Civ6 Runtime Core.

This module defines *execution facts*, not gameplay strategy.  It deliberately
has no I/O or dependency on FastMCP, GameState, FireTuner, or the legacy belief
engine, so every new Runtime module can share one unambiguous operation model.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import json
from typing import Mapping, Self
from uuid import uuid4


class ContractViolation(ValueError):
    """Raised when data cannot represent a valid Runtime contract."""


class OperationStateError(ContractViolation):
    """Raised when an operation lifecycle transition is not permitted."""


class IntentMismatchError(ContractViolation):
    """Raised when an existing operation ID is reused for a new intent."""


class EvidenceRequiredError(OperationStateError):
    """Raised when code tries to close an operation without game evidence."""


class SendState(StrEnum):
    """Whether a mutation crossed the FireTuner send boundary."""

    CREATED = "CREATED"
    PRECHECKED = "PRECHECKED"
    SENDING = "SENDING"
    NOT_SENT = "NOT_SENT"
    MAYBE_SENT = "MAYBE_SENT"


class OutcomeState(StrEnum):
    """What the Runtime knows about a possibly-sent game operation."""

    NOT_STARTED = "NOT_STARTED"
    OBSERVING = "OBSERVING"
    UNKNOWN = "UNKNOWN"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    STALE_INTENT = "STALE_INTENT"


def _required_text(value: str, name: str) -> str:
    if not value or not value.strip():
        raise ContractViolation(f"{name} 不能为空。")
    return value


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class GameIdentity:
    """Stable identity for one concrete Civ6 game."""

    value: str

    def __post_init__(self) -> None:
        _required_text(self.value, "game_id")


@dataclass(frozen=True, slots=True)
class BranchIdentity:
    """A game timeline created at game start or after loading a save."""

    game_id: GameIdentity
    value: str

    def __post_init__(self) -> None:
        _required_text(self.value, "branch_id")

    @classmethod
    def from_token(cls, game_id: GameIdentity, branch_token: str) -> Self:
        """Construct the only Runtime-owned branch-ID encoding.

        Hosts name a save timeline with a token.  The Runtime owns the full
        game-scoped identity so bootstrap and recovery cannot produce
        incompatible branch values for the same token.
        """
        _required_text(branch_token, "branch_token")
        if ":" in branch_token:
            raise ContractViolation("branch_token 不能包含 ':'；请只传入 host token。")
        return cls(game_id, f"{game_id.value}:{branch_token}")


@dataclass(frozen=True, slots=True)
class OperationId:
    """Opaque id for one exact player intent."""

    value: str

    def __post_init__(self) -> None:
        _required_text(self.value, "operation_id")

    @classmethod
    def new(cls) -> Self:
        return cls(str(uuid4()))


@dataclass(frozen=True, slots=True)
class OperationIntent:
    """Canonical, hash-bound tool request a SessionKernel may execute."""

    tool: str
    arguments_json: str
    intent_hash: str

    def __post_init__(self) -> None:
        _required_text(self.tool, "tool")
        _required_text(self.arguments_json, "arguments_json")
        _required_text(self.intent_hash, "intent_hash")
        try:
            arguments = json.loads(self.arguments_json)
        except json.JSONDecodeError as exc:
            raise ContractViolation("arguments_json 必须是合法 JSON。") from exc
        expected_hash = _intent_hash(self.tool, arguments)
        if self.intent_hash != expected_hash:
            raise ContractViolation("intent_hash 与 tool/arguments_json 不一致。")

    @classmethod
    def create(cls, tool: str, arguments: Mapping[str, object]) -> Self:
        """Make an intent whose JSON and hash are stable across processes."""
        _required_text(tool, "tool")
        try:
            arguments_json = json.dumps(
                arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        except (TypeError, ValueError) as exc:
            raise ContractViolation("operation 参数必须可以序列化为 JSON。") from exc
        parsed_arguments = json.loads(arguments_json)
        return cls(
            tool=tool,
            arguments_json=arguments_json,
            intent_hash=_intent_hash(tool, parsed_arguments),
        )


def _intent_hash(tool: str, arguments: object) -> str:
    canonical = json.dumps(
        {"tool": tool, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Evidence:
    """A domain readback or explicit game receipt that can close an operation."""

    source: str
    observed_turn: int | None
    detail: str
    captured_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        _required_text(self.source, "evidence.source")
        _required_text(self.detail, "evidence.detail")
        if self.observed_turn is not None and self.observed_turn < 0:
            raise ContractViolation("evidence.observed_turn 不能为负数。")


@dataclass(frozen=True, slots=True)
class OperationRecord:
    """The append-only semantic view of one mutation lifecycle.

    Instances are immutable.  Store implementations persist each returned
    record transactionally; callers may not treat logs, timeouts, or exception
    classes as evidence that changes ``outcome_state``.
    """

    operation_id: OperationId
    game_id: GameIdentity
    branch_id: BranchIdentity
    decision_turn: int
    intent: OperationIntent
    send_state: SendState = SendState.CREATED
    outcome_state: OutcomeState = OutcomeState.NOT_STARTED
    evidence: tuple[Evidence, ...] = ()
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if self.branch_id.game_id != self.game_id:
            raise ContractViolation("branch_id 必须属于同一个 game_id。")
        if self.decision_turn < 0:
            raise ContractViolation("decision_turn 不能为负数。")
        if self.outcome_state in {OutcomeState.CONFIRMED, OutcomeState.REJECTED}:
            if not self.evidence:
                raise EvidenceRequiredError("关闭 operation 必须提供领域 Evidence。")
        if self.send_state == SendState.NOT_SENT and self.outcome_state == OutcomeState.OBSERVING:
            raise ContractViolation("NOT_SENT 的 operation 不能进入 OBSERVING。")

    @classmethod
    def create(
        cls,
        *,
        game_id: GameIdentity,
        branch_id: BranchIdentity,
        decision_turn: int,
        intent: OperationIntent,
        operation_id: OperationId | None = None,
    ) -> Self:
        return cls(
            operation_id=operation_id or OperationId.new(),
            game_id=game_id,
            branch_id=branch_id,
            decision_turn=decision_turn,
            intent=intent,
        )

    def assert_same_intent(self, intent: OperationIntent) -> None:
        """Reject an attempt to reuse this operation ID with new parameters."""
        if self.intent.intent_hash != intent.intent_hash:
            raise IntentMismatchError(
                "同一个 operation_id 不能绑定不同的 intent_hash。"
            )

    def prechecked(self) -> Self:
        return self._transition(
            allowed_send={SendState.CREATED}, send_state=SendState.PRECHECKED
        )

    def stale_intent(self) -> Self:
        """Record a pre-send turn/identity mismatch without sending anything."""
        return self._transition(
            allowed_send={SendState.CREATED, SendState.PRECHECKED},
            send_state=SendState.NOT_SENT,
            outcome_state=OutcomeState.STALE_INTENT,
        )

    def sending(self) -> Self:
        return self._transition(
            allowed_send={SendState.PRECHECKED}, send_state=SendState.SENDING
        )

    def not_sent(self) -> Self:
        return self._transition(
            allowed_send={SendState.SENDING}, send_state=SendState.NOT_SENT
        )

    def maybe_sent(self) -> Self:
        """Mark the send boundary as crossed; this operation may never resend."""
        return self._transition(
            allowed_send={SendState.SENDING},
            send_state=SendState.MAYBE_SENT,
            outcome_state=OutcomeState.OBSERVING,
        )

    def observing(self) -> Self:
        return self._transition(
            allowed_send={SendState.MAYBE_SENT},
            allowed_outcome={OutcomeState.OBSERVING, OutcomeState.UNKNOWN},
            outcome_state=OutcomeState.OBSERVING,
        )

    def unknown(self) -> Self:
        return self._transition(
            allowed_send={SendState.MAYBE_SENT},
            allowed_outcome={OutcomeState.OBSERVING},
            outcome_state=OutcomeState.UNKNOWN,
        )

    def confirmed(self, evidence: Evidence) -> Self:
        return self._close(OutcomeState.CONFIRMED, evidence)

    def rejected(self, evidence: Evidence) -> Self:
        return self._close(OutcomeState.REJECTED, evidence)

    def _close(self, outcome_state: OutcomeState, evidence: Evidence | None) -> Self:
        if evidence is None:
            raise EvidenceRequiredError("关闭 operation 必须提供领域 Evidence。")
        return self._transition(
            allowed_send={SendState.MAYBE_SENT},
            allowed_outcome={OutcomeState.OBSERVING, OutcomeState.UNKNOWN},
            outcome_state=outcome_state,
            evidence=self.evidence + (evidence,),
        )

    def _transition(
        self,
        *,
        allowed_send: set[SendState],
        send_state: SendState | None = None,
        allowed_outcome: set[OutcomeState] | None = None,
        outcome_state: OutcomeState | None = None,
        evidence: tuple[Evidence, ...] | None = None,
    ) -> Self:
        if self.send_state not in allowed_send:
            raise OperationStateError(
                f"send_state={self.send_state} 不能执行这一步 operation transition。"
            )
        if allowed_outcome is not None and self.outcome_state not in allowed_outcome:
            raise OperationStateError(
                f"outcome_state={self.outcome_state} 不能执行这一步 operation transition。"
            )
        return replace(
            self,
            send_state=send_state or self.send_state,
            outcome_state=outcome_state or self.outcome_state,
            evidence=evidence if evidence is not None else self.evidence,
            updated_at=_utc_now(),
        )
