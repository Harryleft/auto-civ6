"""Small, immutable contracts for pluggable national-strategy departments."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable

from ...graph import GraphView
from ..models import Outcome, Proposal, StrategicGoal, TurnSnapshot


class Department(StrEnum):
    MILITARY = "military"
    SCIENCE = "science"
    CIVICS = "civics"
    PRODUCTION = "production"
    ECONOMY = "economy"
    DIPLOMACY = "diplomacy"


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _texts(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    normalized = tuple(_text(item, name) for item in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} must not contain duplicates")
    return normalized


@dataclass(frozen=True, slots=True)
class DepartmentContext:
    snapshot: TurnSnapshot
    agenda: tuple[str, ...] = ()
    goals: tuple[StrategicGoal, ...] = ()
    graph: GraphView | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, TurnSnapshot):
            raise TypeError("snapshot must be TurnSnapshot")
        object.__setattr__(self, "agenda", _texts(tuple(self.agenda), "agenda"))
        goals = tuple(self.goals)
        if not all(isinstance(goal, StrategicGoal) for goal in goals):
            raise TypeError("goals must contain StrategicGoal values")
        object.__setattr__(self, "goals", goals)
        if self.graph is not None and not isinstance(self.graph, GraphView):
            raise TypeError("graph must be GraphView or None")


@dataclass(frozen=True, slots=True)
class SupportRequest:
    requester: Department
    target: Department
    objective: str
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.requester, Department) or not isinstance(
            self.target, Department
        ):
            raise TypeError("requester and target must be Department values")
        if self.requester == self.target:
            raise ValueError("a department cannot request support from itself")
        object.__setattr__(self, "objective", _text(self.objective, "objective"))
        object.__setattr__(self, "reason", _text(self.reason, "reason"))


@dataclass(frozen=True, slots=True)
class Workstream:
    workstream_id: str
    department: Department
    objective: str
    priority: int
    dependencies: tuple[str, ...] = ()
    resource_claims: Mapping[str, float] = field(default_factory=dict)
    candidate_actions: tuple[str, ...] = ()
    exit_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "workstream_id", _text(self.workstream_id, "workstream_id"))
        if not isinstance(self.department, Department):
            raise TypeError("department must be Department")
        object.__setattr__(self, "objective", _text(self.objective, "objective"))
        if type(self.priority) is not int or not 0 <= self.priority <= 100:
            raise ValueError("priority must be an integer between 0 and 100")
        object.__setattr__(
            self, "dependencies", _texts(tuple(self.dependencies), "dependencies")
        )
        claims: dict[str, float] = {}
        for key, value in self.resource_claims.items():
            name = _text(key, "resource claim")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"resource claim {name!r} must be non-negative")
            claims[name] = float(value)
        object.__setattr__(self, "resource_claims", MappingProxyType(claims))
        object.__setattr__(
            self, "candidate_actions", _texts(tuple(self.candidate_actions), "candidate_actions")
        )
        object.__setattr__(
            self, "exit_conditions", _texts(tuple(self.exit_conditions), "exit_conditions")
        )


@dataclass(frozen=True, slots=True)
class DepartmentAssessment:
    department: Department
    snapshot_id: str
    relevance: float
    summary: str
    facts: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    opportunities: tuple[str, ...] = ()
    capability_gaps: tuple[str, ...] = ()
    evidence_missing: tuple[str, ...] = ()
    support_requests: tuple[SupportRequest, ...] = ()
    workstreams: tuple[Workstream, ...] = ()
    proposals: tuple[Proposal, ...] = ()
    degraded: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.department, Department):
            raise TypeError("department must be Department")
        object.__setattr__(self, "snapshot_id", _text(self.snapshot_id, "snapshot_id"))
        if (
            isinstance(self.relevance, bool)
            or not isinstance(self.relevance, (int, float))
            or not 0 <= float(self.relevance) <= 1
        ):
            raise ValueError("relevance must be between 0 and 1")
        object.__setattr__(self, "relevance", float(self.relevance))
        object.__setattr__(self, "summary", _text(self.summary, "summary"))
        for name in (
            "facts",
            "risks",
            "opportunities",
            "capability_gaps",
            "evidence_missing",
        ):
            object.__setattr__(self, name, _texts(tuple(getattr(self, name)), name))
        requests = tuple(self.support_requests)
        if not all(isinstance(item, SupportRequest) for item in requests):
            raise TypeError("support_requests must contain SupportRequest values")
        if any(item.requester != self.department for item in requests):
            raise ValueError("support request requester must match assessment department")
        object.__setattr__(self, "support_requests", requests)
        workstreams = tuple(self.workstreams)
        if not all(isinstance(item, Workstream) for item in workstreams):
            raise TypeError("workstreams must contain Workstream values")
        if any(item.department != self.department for item in workstreams):
            raise ValueError("workstream department must match assessment department")
        object.__setattr__(self, "workstreams", workstreams)
        proposals = tuple(self.proposals)
        if not all(isinstance(item, Proposal) for item in proposals):
            raise TypeError("proposals must contain Proposal values")
        if any(item.department != self.department.value for item in proposals):
            raise ValueError("proposal department must match assessment department")
        object.__setattr__(self, "proposals", proposals)
        if type(self.degraded) is not bool:
            raise TypeError("degraded must be a bool")


class ReviewDisposition(StrEnum):
    CONTINUE = "continue"
    REPLAN = "replan"
    EXIT = "exit"


@runtime_checkable
class DepartmentPlugin(Protocol):
    department: Department

    def match(self, context: DepartmentContext) -> float: ...

    def assess(self, context: DepartmentContext) -> DepartmentAssessment: ...

    def review(self, context: DepartmentContext, outcome: Outcome) -> ReviewDisposition: ...
