"""Typed contracts for the Civilization governance control plane.

The governance layer deliberately contains no model SDK or persistence code.  It
accepts typed ``GameState`` results, records the evidence required by an action,
and produces immutable values that can be appended to the existing event log.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import StrEnum
from numbers import Real
from types import MappingProxyType
from typing import Any, Mapping

from .inputs import (
    BarbarianOverviewInput,
    CityInput,
    DiplomacyInput,
    GameOverviewInput,
    GovernmentInput,
    GreatPeopleInput,
    NotificationInput,
    ResourceStockpileInput,
    TechCivicInput,
    ThreatInput,
    UnitInput,
    VictoryInput,
)

from ..validation import require_text, require_texts



def _strict_bool(value: bool, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a bool")
    return value


def _strict_int(value: int, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _finite_number(
    value: float,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real number, not bool")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and numeric < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and numeric > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return numeric



def _numeric_mapping(
    values: Mapping[str, float], name: str, *, minimum: float | None = None
) -> Mapping[str, float]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{name} must be a mapping")
    normalized: dict[str, float] = {}
    for key, value in values.items():
        normalized[require_text(key, f"{name} key")] = _finite_number(
            value, f"{name}[{key!r}]", minimum=minimum
        )
    return MappingProxyType(normalized)


def _bool_mapping(values: Mapping[str, bool], name: str) -> Mapping[str, bool]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{name} must be a mapping")
    normalized: dict[str, bool] = {}
    for key, value in values.items():
        normalized[require_text(key, f"{name} key")] = _strict_bool(
            value, f"{name}[{key!r}]"
        )
    return MappingProxyType(normalized)


def _json_value(value: Any) -> Any:
    """Convert supported typed values to a stable JSON-compatible structure."""

    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_value(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported action argument type: {type(value).__name__}")


def _freeze_value(value: Any) -> Any:
    """Recursively detach and freeze JSON-compatible governance payloads."""

    if is_dataclass(value):
        return _freeze_value(asdict(value))
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(
            _freeze_value(item)
            for item in sorted(
                value,
                key=lambda item: json.dumps(_json_value(item), sort_keys=True),
            )
        )
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported governance payload type: {type(value).__name__}")


def _arguments_hash(arguments: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _json_value(arguments), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProbabilityConfidence:
    """An event likelihood and the quality of evidence supporting that estimate.

    These values are intentionally separate.  A 10% event can be known with high
    confidence, while a 90% estimate can be based on poor evidence.
    """

    probability: float
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "probability",
            _finite_number(self.probability, "probability", minimum=0, maximum=1),
        )
        object.__setattr__(
            self,
            "confidence",
            _finite_number(self.confidence, "confidence", minimum=0, maximum=1),
        )


@dataclass(frozen=True, slots=True)
class RulesetCapabilities:
    """Explicit feature flags shared by agenda, council and action routing."""

    ruleset: str = "RULESET_STANDARD"
    governors: bool = False
    ages: bool = False
    dedications: bool = False
    alliances: bool = False
    diplomatic_favor: bool = False
    world_congress: bool = False
    resource_stockpiles: bool = False
    climate: bool = False
    basic_diplomacy: bool = True
    trade: bool = True
    city_states: bool = True
    religion: bool = True
    combat_estimate: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "ruleset", require_text(self.ruleset, "ruleset"))
        for name in (
            "governors",
            "ages",
            "dedications",
            "alliances",
            "diplomatic_favor",
            "world_congress",
            "resource_stockpiles",
            "climate",
            "basic_diplomacy",
            "trade",
            "city_states",
            "religion",
            "combat_estimate",
        ):
            _strict_bool(getattr(self, name), name)

    @classmethod
    def standard(cls) -> RulesetCapabilities:
        return cls()


@dataclass(frozen=True, slots=True)
class TypedTurnSnapshot:
    """Immutable typed input used only at the GameState-to-Graph adapter boundary."""

    snapshot_id: str
    turn: int
    turn_before: int
    turn_after: int
    player_id: int
    captured_at: float
    capabilities: RulesetCapabilities
    overview: GameOverviewInput | None = None
    cities: tuple[CityInput, ...] = ()
    units: tuple[UnitInput, ...] = ()
    diplomacy: tuple[DiplomacyInput, ...] = ()
    tech_civic: TechCivicInput | None = None
    resources: tuple[ResourceStockpileInput, ...] = ()
    victory: VictoryInput | None = None
    notifications: tuple[NotificationInput, ...] = ()
    policies: GovernmentInput | None = None
    barbarians: BarbarianOverviewInput | None = None
    great_people: GreatPeopleInput | None = None
    threats: tuple[ThreatInput, ...] = ()
    threat_scan_available: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", require_text(self.snapshot_id, "snapshot_id"))
        turn = _strict_int(self.turn, "turn")
        before = _strict_int(self.turn_before, "turn_before")
        after = _strict_int(self.turn_after, "turn_after")
        if not (turn == before == after):
            raise ValueError(
                "cross-turn snapshot rejected: turn, turn_before and turn_after must match"
            )
        _strict_int(self.player_id, "player_id")
        object.__setattr__(
            self, "captured_at", _finite_number(self.captured_at, "captured_at", minimum=0)
        )
        if not isinstance(self.capabilities, RulesetCapabilities):
            raise TypeError("capabilities must be RulesetCapabilities")
        self._typed_or_none(self.overview, GameOverviewInput, "overview")
        self._typed_or_none(self.tech_civic, TechCivicInput, "tech_civic")
        self._typed_or_none(self.victory, VictoryInput, "victory")
        self._typed_or_none(self.policies, GovernmentInput, "policies")
        self._typed_or_none(self.barbarians, BarbarianOverviewInput, "barbarians")
        self._typed_or_none(self.great_people, GreatPeopleInput, "great_people")
        for name, item_type in (
            ("cities", CityInput),
            ("units", UnitInput),
            ("diplomacy", DiplomacyInput),
            ("resources", ResourceStockpileInput),
            ("notifications", NotificationInput),
            ("threats", ThreatInput),
        ):
            values = tuple(getattr(self, name))
            if not all(isinstance(value, item_type) for value in values):
                raise TypeError(f"{name} must contain only {item_type.__name__} values")
            object.__setattr__(self, name, values)
        if type(self.threat_scan_available) is not bool:
            raise TypeError("threat_scan_available must be a bool")
        if self.threats and not self.threat_scan_available:
            raise ValueError("threat rows require an available threat scan")
        if not isinstance(self.extra, Mapping):
            raise TypeError("extra must be a mapping")
        if not all(isinstance(key, str) and key for key in self.extra):
            raise TypeError("extra keys must be non-empty strings")
        object.__setattr__(self, "extra", _freeze_value(self.extra))

    @staticmethod
    def _typed_or_none(value: Any, expected: type[Any], name: str) -> None:
        if value is not None and not isinstance(value, expected):
            raise TypeError(f"{name} must be {expected.__name__} or None")


@dataclass(frozen=True, slots=True)
class StrategicGoal:
    goal_id: str
    statement: str
    priority: int
    success: ProbabilityConfidence
    hard_constraints: tuple[str, ...] = ()
    deadline_turn: int | None = None
    parent_goal_id: str | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal_id", require_text(self.goal_id, "goal_id"))
        object.__setattr__(self, "statement", require_text(self.statement, "statement"))
        _strict_int(self.priority, "priority")
        if not isinstance(self.success, ProbabilityConfidence):
            raise TypeError("success must be ProbabilityConfidence")
        object.__setattr__(
            self,
            "hard_constraints",
            require_texts(self.hard_constraints, "hard_constraints"),
        )
        object.__setattr__(self, "tags", require_texts(self.tags, "tags"))
        if self.deadline_turn is not None:
            _strict_int(self.deadline_turn, "deadline_turn")
        if self.parent_goal_id is not None:
            object.__setattr__(
                self, "parent_goal_id", require_text(self.parent_goal_id, "parent_goal_id")
            )


@dataclass(frozen=True, slots=True)
class BudgetLock:
    """A proposed reservation of a scarce resource or an exclusive slot."""

    resource: str
    amount: float = 1.0
    scope: str = "global"
    exclusive: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource", require_text(self.resource, "resource"))
        object.__setattr__(self, "scope", require_text(self.scope, "scope"))
        object.__setattr__(
            self, "amount", _finite_number(self.amount, "amount", minimum=0.0000001)
        )
        _strict_bool(self.exclusive, "exclusive")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")
        object.__setattr__(self, "reason", self.reason.strip())

    @property
    def key(self) -> str:
        return f"{self.resource}:{self.scope}"


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    requirement_id: str
    tool: str
    target_entity_id: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    min_observation_sequence: int = 0
    max_age_turns: int | None = None
    required_facts: tuple[str, ...] = ()
    required_metrics: tuple[str, ...] = ()
    description: str = ""
    expected_facts: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "requirement_id", require_text(self.requirement_id, "requirement_id")
        )
        object.__setattr__(self, "tool", require_text(self.tool, "tool"))
        if not isinstance(self.params, Mapping):
            raise TypeError("params must be a mapping")
        if not all(isinstance(key, str) and key for key in self.params):
            raise TypeError("params keys must be non-empty strings")
        object.__setattr__(self, "params", _freeze_value(self.params))
        _strict_int(self.min_observation_sequence, "min_observation_sequence")
        if self.max_age_turns is not None:
            _strict_int(self.max_age_turns, "max_age_turns")
        if self.target_entity_id is not None:
            object.__setattr__(
                self,
                "target_entity_id",
                require_text(self.target_entity_id, "target_entity_id"),
            )
        object.__setattr__(
            self, "required_facts", require_texts(self.required_facts, "required_facts")
        )
        object.__setattr__(
            self,
            "required_metrics",
            require_texts(self.required_metrics, "required_metrics"),
        )
        if not isinstance(self.expected_facts, Mapping) or not all(
            isinstance(key, str) and key for key in self.expected_facts
        ):
            raise TypeError("expected_facts must be a mapping with non-empty string keys")
        object.__setattr__(
            self,
            "expected_facts",
            _freeze_value(self.expected_facts),
        )
        if not isinstance(self.description, str):
            raise TypeError("description must be a string")
        object.__setattr__(self, "description", self.description.strip())


@dataclass(frozen=True, slots=True)
class ActionIntent:
    """A structured, hash-bound request for the single game writer."""

    intent_id: str
    tool: str
    arguments: Mapping[str, Any]
    proposal_id: str
    evidence_requirements: tuple[EvidenceRequirement, ...] = ()
    allowed_turn: int | None = None
    arguments_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", require_text(self.intent_id, "intent_id"))
        object.__setattr__(self, "tool", require_text(self.tool, "tool"))
        object.__setattr__(
            self, "proposal_id", require_text(self.proposal_id, "proposal_id")
        )
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        if not all(isinstance(key, str) and key for key in self.arguments):
            raise TypeError("argument keys must be non-empty strings")
        frozen_arguments = _freeze_value(self.arguments)
        computed_hash = _arguments_hash(frozen_arguments)
        if self.arguments_hash and self.arguments_hash != computed_hash:
            raise ValueError("arguments_hash does not match arguments")
        object.__setattr__(self, "arguments", frozen_arguments)
        object.__setattr__(self, "arguments_hash", computed_hash)
        requirements = tuple(self.evidence_requirements)
        if not all(isinstance(item, EvidenceRequirement) for item in requirements):
            raise TypeError("evidence_requirements must contain EvidenceRequirement values")
        object.__setattr__(self, "evidence_requirements", requirements)
        if self.allowed_turn is not None:
            _strict_int(self.allowed_turn, "allowed_turn")


@dataclass(frozen=True, slots=True)
class Proposal:
    proposal_id: str
    department: str
    summary: str
    goal_ids: tuple[str, ...]
    success: ProbabilityConfidence
    priority: int
    hard_constraints: Mapping[str, bool] = field(default_factory=dict)
    budget_locks: tuple[BudgetLock, ...] = ()
    benefits: Mapping[str, float] = field(default_factory=dict)
    costs: Mapping[str, float] = field(default_factory=dict)
    opportunity_cost: float = 0.0
    failure_cost: float = 0.0
    action_intents: tuple[ActionIntent, ...] = ()
    belief_ids: tuple[str, ...] = ()
    expires_turn: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "proposal_id", require_text(self.proposal_id, "proposal_id")
        )
        object.__setattr__(self, "department", require_text(self.department, "department"))
        object.__setattr__(self, "summary", require_text(self.summary, "summary"))
        object.__setattr__(self, "goal_ids", require_texts(self.goal_ids, "goal_ids"))
        if not self.goal_ids:
            raise ValueError("goal_ids must contain at least one goal")
        if not isinstance(self.success, ProbabilityConfidence):
            raise TypeError("success must be ProbabilityConfidence")
        _strict_int(self.priority, "priority")
        object.__setattr__(
            self,
            "hard_constraints",
            _bool_mapping(self.hard_constraints, "hard_constraints"),
        )
        locks = tuple(self.budget_locks)
        if not all(isinstance(lock, BudgetLock) for lock in locks):
            raise TypeError("budget_locks must contain BudgetLock values")
        object.__setattr__(self, "budget_locks", locks)
        object.__setattr__(self, "benefits", _numeric_mapping(self.benefits, "benefits"))
        object.__setattr__(
            self, "costs", _numeric_mapping(self.costs, "costs", minimum=0)
        )
        object.__setattr__(
            self,
            "opportunity_cost",
            _finite_number(self.opportunity_cost, "opportunity_cost", minimum=0),
        )
        object.__setattr__(
            self,
            "failure_cost",
            _finite_number(self.failure_cost, "failure_cost", minimum=0),
        )
        intents = tuple(self.action_intents)
        if not all(isinstance(intent, ActionIntent) for intent in intents):
            raise TypeError("action_intents must contain ActionIntent values")
        if any(intent.proposal_id != self.proposal_id for intent in intents):
            raise ValueError("every action intent must reference this proposal")
        object.__setattr__(self, "action_intents", intents)
        object.__setattr__(self, "belief_ids", require_texts(self.belief_ids, "belief_ids"))
        if self.expires_turn is not None:
            _strict_int(self.expires_turn, "expires_turn")


class CouncilDecisionStatus(StrEnum):
    APPROVED = "approved"
    PARTIAL = "partial"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class CouncilDecision:
    decision_id: str
    turn: int
    status: CouncilDecisionStatus
    selected_proposal_ids: tuple[str, ...]
    considered_proposal_ids: tuple[str, ...]
    rejected_reasons: Mapping[str, tuple[str, ...]]
    explanation: tuple[str, ...]
    budget_usage: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "decision_id", require_text(self.decision_id, "decision_id")
        )
        _strict_int(self.turn, "turn")
        if not isinstance(self.status, CouncilDecisionStatus):
            raise TypeError("status must be CouncilDecisionStatus")
        object.__setattr__(
            self,
            "selected_proposal_ids",
            require_texts(self.selected_proposal_ids, "selected_proposal_ids"),
        )
        object.__setattr__(
            self,
            "considered_proposal_ids",
            require_texts(self.considered_proposal_ids, "considered_proposal_ids"),
        )
        if not set(self.selected_proposal_ids).issubset(self.considered_proposal_ids):
            raise ValueError("selected proposals must be present in considered proposals")
        if not isinstance(self.rejected_reasons, Mapping):
            raise TypeError("rejected_reasons must be a mapping")
        reasons = {
            require_text(key, "rejected proposal id"): require_texts(tuple(value), "rejection reason")
            for key, value in self.rejected_reasons.items()
        }
        object.__setattr__(self, "rejected_reasons", MappingProxyType(reasons))
        object.__setattr__(
            self, "explanation", require_texts(self.explanation, "explanation")
        )
        object.__setattr__(
            self,
            "budget_usage",
            _numeric_mapping(self.budget_usage, "budget_usage", minimum=0),
        )


class OutcomeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYABLE = "retryable"


@dataclass(frozen=True, slots=True)
class Outcome:
    outcome_id: str
    intent_id: str
    proposal_id: str
    decision_id: str
    status: OutcomeStatus
    turn: int
    observation_ids: tuple[str, ...] = ()
    result: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        for name in ("outcome_id", "intent_id", "proposal_id", "decision_id"):
            object.__setattr__(self, name, require_text(getattr(self, name), name))
        if not isinstance(self.status, OutcomeStatus):
            raise TypeError("status must be OutcomeStatus")
        _strict_int(self.turn, "turn")
        object.__setattr__(
            self,
            "observation_ids",
            require_texts(self.observation_ids, "observation_ids"),
        )
        if not isinstance(self.result, Mapping):
            raise TypeError("result must be a mapping")
        object.__setattr__(self, "result", _freeze_value(self.result))
        if self.error is not None:
            object.__setattr__(self, "error", require_text(self.error, "error"))
        if self.status is OutcomeStatus.SUCCEEDED and self.error is not None:
            raise ValueError("a succeeded outcome cannot contain an error")
        if self.status is not OutcomeStatus.SUCCEEDED and self.error is None:
            raise ValueError("failed and retryable outcomes must contain an error")
