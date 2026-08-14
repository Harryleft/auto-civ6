"""Deterministic, read-only civics and policy department."""

from __future__ import annotations

from typing import Iterable

from ..models import Outcome, OutcomeStatus
from .base import (
    Department,
    DepartmentAssessment,
    DepartmentContext,
    ReviewDisposition,
    SupportRequest,
    Workstream,
)


_EMPTY_MARKERS = frozenset({"", "NONE", "NULL"})
_CIVIC_TERMS = (
    "civic",
    "civics",
    "policy",
    "policies",
    "government",
    "市政",
    "政策",
    "政府",
)


def _clean(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _present(value: object) -> bool:
    return bool(_clean(value)) and _clean(value).upper() not in _EMPTY_MARKERS


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _integer(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _slot_index(slot: object) -> int:
    return _integer(getattr(slot, "slot_index", 0))


def _slot_type(slot: object) -> str:
    return _clean(getattr(slot, "slot_type", "")) or "UNKNOWN"


def _is_empty_slot(slot: object) -> bool:
    return not _present(getattr(slot, "current_policy", None))


def _is_wildcard(slot_type: str) -> bool:
    return "WILDCARD" in slot_type.upper()


def _category(slot_type: str) -> str:
    normalized = slot_type.upper()
    for category in ("MILITARY", "ECONOMIC", "DIPLOMATIC", "WILDCARD"):
        if category in normalized:
            return category
    return normalized


def _policy_type(policy: object) -> str:
    return _clean(getattr(policy, "policy_type", ""))


def _policy_name(policy: object) -> str:
    return _clean(getattr(policy, "name", ""))


def _civic_type(civic: object) -> str:
    return _clean(getattr(civic, "civic_type", ""))


def _civic_name(civic: object) -> str:
    return _clean(getattr(civic, "name", ""))


def _ordered_slots(government: object) -> tuple[object, ...]:
    slots = tuple(getattr(government, "slots", ()) or ())
    return tuple(
        sorted(
            slots,
            key=lambda slot: (
                _slot_index(slot),
                _slot_type(slot),
                _clean(getattr(slot, "current_policy", "")),
            ),
        )
    )


def _ordered_policies(government: object) -> tuple[object, ...]:
    policies = tuple(getattr(government, "available_policies", ()) or ())
    return tuple(
        sorted(
            policies,
            key=lambda policy: (
                _policy_type(policy),
                _policy_name(policy),
                _clean(getattr(policy, "slot_type", "")),
                _clean(getattr(policy, "description", "")),
            ),
        )
    )


def _ordered_civics(tech_civic: object) -> tuple[object, ...]:
    civics = tuple(getattr(tech_civic, "available_civics", ()) or ())
    return tuple(
        sorted(
            civics,
            key=lambda civic: (
                _integer(getattr(civic, "turns", 0)),
                _civic_type(civic),
                _civic_name(civic),
            ),
        )
    )


def _policy_matches(slot_type: str, policy: object) -> bool:
    if _is_wildcard(slot_type):
        return True
    policy_type = _clean(getattr(policy, "slot_type", ""))
    return bool(policy_type) and _category(slot_type) == _category(policy_type)


def _barbarian_pressure(snapshot: object) -> bool:
    barbarians = getattr(snapshot, "barbarians", None)
    if barbarians is None:
        return False
    return bool(
        tuple(getattr(barbarians, "camps", ()) or ())
        or tuple(getattr(barbarians, "units", ()) or ())
    )


def _civic_agenda(context: DepartmentContext) -> bool:
    texts = list(context.agenda)
    texts.extend(getattr(goal, "statement", "") for goal in context.goals)
    normalized = tuple(_clean(text).casefold() for text in texts)
    return any(term.casefold() in text for text in normalized for term in _CIVIC_TERMS)


class CivicsDepartment:
    """Evaluate civic progression and policy support without game-side effects."""

    department = Department.CIVICS

    def match(self, context: DepartmentContext) -> float:
        """Return a fixed relevance score derived only from the shared snapshot."""

        snapshot = context.snapshot
        relevance = 0.0
        if snapshot.tech_civic is not None:
            relevance += 0.45
        if snapshot.policies is not None:
            relevance += 0.45
        if snapshot.policies is not None and any(
            _is_empty_slot(slot) for slot in _ordered_slots(snapshot.policies)
        ):
            relevance += 0.05
        if _barbarian_pressure(snapshot):
            relevance += 0.05
        if _civic_agenda(context):
            relevance += 0.10
        return round(min(1.0, relevance), 2)

    def assess(self, context: DepartmentContext) -> DepartmentAssessment:
        """Build a stable assessment from typed evidence; never mutate or execute."""

        snapshot = context.snapshot
        relevance = self.match(context)
        tech_civic = snapshot.tech_civic
        government = snapshot.policies

        facts: list[str] = []
        risks: list[str] = []
        opportunities: list[str] = []
        capability_gaps: list[str] = []
        evidence_missing: list[str] = []
        workstreams: list[Workstream] = []
        support_requests: list[SupportRequest] = []

        if tech_civic is None:
            evidence_missing.append("tech_civic")
            capability_gaps.append("missing civic evidence")
        else:
            current_civic = _clean(getattr(tech_civic, "current_civic", ""))
            facts.append(f"current_civic:{current_civic or 'NONE'}")
            civics = _ordered_civics(tech_civic)
            facts.append(f"available_civics:{len(civics)}")
            for civic in civics:
                civic_id = _civic_type(civic) or _civic_name(civic)
                if civic_id:
                    opportunities.append(f"civic_candidate:{civic_id}")

            if civics:
                workstreams.append(
                    Workstream(
                        workstream_id="civics:civic-progression",
                        department=self.department,
                        objective="review civic progression options",
                        priority=50,
                        candidate_actions=tuple(
                            f"consider_civic:{_civic_type(civic) or _civic_name(civic)}"
                            for civic in civics
                            if _civic_type(civic) or _civic_name(civic)
                        ),
                        exit_conditions=("civic progression evidence refreshed",),
                    )
                )

        empty_slots: list[object] = []
        if government is None:
            evidence_missing.append("policies")
            capability_gaps.append("missing policy evidence")
        else:
            government_type = _clean(getattr(government, "government_type", ""))
            government_name = _clean(getattr(government, "government_name", ""))
            facts.append(f"government:{government_type or government_name or 'UNKNOWN'}")
            slots = _ordered_slots(government)
            policies = _ordered_policies(government)
            facts.append(f"policy_slots:{len(slots)}")
            facts.append(f"available_policies:{len(policies)}")

            for slot in slots:
                index = _slot_index(slot)
                slot_type = _slot_type(slot)
                current_policy = _clean(getattr(slot, "current_policy", ""))
                if _present(current_policy):
                    facts.append(f"current_policy:{index}:{current_policy}")
                else:
                    facts.append(f"empty_policy_slot:{index}:{slot_type}")
                    empty_slots.append(slot)

            candidate_actions: list[str] = []
            for slot in empty_slots:
                index = _slot_index(slot)
                slot_type = _slot_type(slot)
                opportunities.append(f"empty_policy_slot:{index}:{slot_type}")
                matching = tuple(
                    policy
                    for policy in policies
                    if _policy_matches(slot_type, policy) and _policy_type(policy)
                )
                if not matching:
                    risks.append(f"no_policy_candidate_for_slot:{index}")
                for policy in matching:
                    policy_id = _policy_type(policy)
                    opportunities.append(f"candidate_policy:{index}:{policy_id}")
                    candidate_actions.append(f"recommend_policy:{index}:{policy_id}")

            if empty_slots:
                risks.append(f"empty_policy_slots:{len(empty_slots)}")
                capability_gaps.extend(
                    f"policy slot {_slot_index(slot)} requires review" for slot in empty_slots
                )
                workstreams.append(
                    Workstream(
                        workstream_id="civics:policy-slots",
                        department=self.department,
                        objective="review empty policy slots",
                        priority=80,
                        candidate_actions=_unique(candidate_actions),
                        exit_conditions=("policy slot evidence refreshed",),
                    )
                )

        if _barbarian_pressure(snapshot):
            opportunities.append("barbarian_military_policy_support")
            support_requests.append(
                SupportRequest(
                    requester=Department.CIVICS,
                    target=Department.MILITARY,
                    objective="evaluate civic policy support for barbarian clearance",
                    reason="barbarian camp or visible unit present in snapshot",
                )
            )
            risks.append("barbarian_pressure")

        evidence_degraded = bool(evidence_missing)
        if tech_civic is None and government is None:
            summary = "civics evidence unavailable"
        elif evidence_degraded:
            summary = "civics assessment degraded"
        elif empty_slots:
            summary = f"civics policy review required: {len(empty_slots)} empty slot(s)"
        else:
            summary = "civics and policy assessment ready"

        return DepartmentAssessment(
            department=self.department,
            snapshot_id=snapshot.snapshot_id,
            relevance=relevance,
            summary=summary,
            facts=_unique(facts),
            risks=_unique(risks),
            opportunities=_unique(opportunities),
            capability_gaps=_unique(capability_gaps),
            evidence_missing=_unique(evidence_missing),
            support_requests=tuple(support_requests),
            workstreams=tuple(workstreams),
            degraded=evidence_degraded,
        )

    def review(self, context: DepartmentContext, outcome: Outcome) -> ReviewDisposition:
        """Map a typed outcome to a fixed disposition without interpreting free text."""

        if not isinstance(context, DepartmentContext):
            raise TypeError("context must be DepartmentContext")
        if outcome.status is OutcomeStatus.SUCCEEDED:
            return ReviewDisposition.CONTINUE
        return ReviewDisposition.REPLAN
