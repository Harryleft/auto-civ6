"""Deterministic proposal arbitration for the national strategy council."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping

from .models import (
    BudgetLock,
    CouncilDecision,
    CouncilDecisionStatus,
    Proposal,
)


def _locks_overlap(left: BudgetLock, right: BudgetLock) -> bool:
    return left.resource == right.resource and (
        left.scope == right.scope or left.scope == "global" or right.scope == "global"
    )


def _compete(left: Proposal, right: Proposal) -> bool:
    return any(
        _locks_overlap(left_lock, right_lock)
        for left_lock in left.budget_locks
        for right_lock in right.budget_locks
    )


def pareto_dominates(left: Proposal, right: Proposal) -> bool:
    """Return whether ``left`` is no worse on every explicit objective.

    Missing dimensions use the neutral baseline zero.  Probability and evidence
    confidence are independent benefit dimensions; neither is multiplied into a
    weighted score.  Opportunity cost is intentionally excluded and considered
    only after Pareto comparison.
    """

    benefit_keys = set(left.benefits) | set(right.benefits)
    cost_keys = set(left.costs) | set(right.costs)
    left_benefits = {
        **{key: left.benefits.get(key, 0.0) for key in benefit_keys},
        "__success_probability": left.success.probability,
        "__evidence_confidence": left.success.confidence,
    }
    right_benefits = {
        **{key: right.benefits.get(key, 0.0) for key in benefit_keys},
        "__success_probability": right.success.probability,
        "__evidence_confidence": right.success.confidence,
    }
    left_costs = {key: left.costs.get(key, 0.0) for key in cost_keys}
    right_costs = {key: right.costs.get(key, 0.0) for key in cost_keys}
    no_worse = all(left_benefits[key] >= right_benefits[key] for key in left_benefits)
    no_worse = no_worse and all(left_costs[key] <= right_costs[key] for key in left_costs)
    strictly_better = any(
        left_benefits[key] > right_benefits[key] for key in left_benefits
    ) or any(left_costs[key] < right_costs[key] for key in left_costs)
    return no_worse and strictly_better


def _pareto_fronts(proposals: list[Proposal]) -> list[list[Proposal]]:
    remaining = list(proposals)
    fronts: list[list[Proposal]] = []
    while remaining:
        front = [
            candidate
            for candidate in remaining
            if not any(
                other.priority == candidate.priority
                and _compete(other, candidate)
                and pareto_dominates(other, candidate)
                for other in remaining
                if other is not candidate
            )
        ]
        fronts.append(front)
        front_ids = {proposal.proposal_id for proposal in front}
        remaining = [
            proposal for proposal in remaining if proposal.proposal_id not in front_ids
        ]
    return fronts


class GovernanceCouncil:
    """Apply hard constraints, locks, priorities, Pareto and opportunity cost."""

    def decide(
        self,
        *,
        turn: int,
        proposals: Iterable[Proposal],
        budget_limits: Mapping[str, float],
        held_locks: Iterable[BudgetLock] = (),
        decision_id: str | None = None,
    ) -> CouncilDecision:
        if type(turn) is not int or turn < 0:
            raise ValueError("turn must be a non-negative int")
        proposal_list = list(proposals)
        if not all(isinstance(proposal, Proposal) for proposal in proposal_list):
            raise TypeError("proposals must contain only Proposal values")
        ids = [proposal.proposal_id for proposal in proposal_list]
        if len(ids) != len(set(ids)):
            raise ValueError("proposal ids must be unique")
        limits = self._validate_limits(budget_limits)
        held_lock_list = list(held_locks)
        if not all(isinstance(lock, BudgetLock) for lock in held_lock_list):
            raise TypeError("held_locks must contain only BudgetLock values")
        rejected: dict[str, list[str]] = {}
        eligible: list[Proposal] = []

        # 1. Hard constraints are fail-closed and evaluated before preferences.
        for proposal in proposal_list:
            failures = [
                name for name, satisfied in proposal.hard_constraints.items() if not satisfied
            ]
            if proposal.expires_turn is not None and turn > proposal.expires_turn:
                failures.append(f"expired after turn {proposal.expires_turn}")
            if failures:
                rejected[proposal.proposal_id] = [
                    f"hard constraint failed: {failure}" for failure in failures
                ]
            else:
                eligible.append(proposal)

        # 2. A non-exclusive reservation without known capacity is not a lock.
        budget_eligible: list[Proposal] = []
        for proposal in eligible:
            lock_errors = self._individual_lock_errors(proposal, limits)
            if lock_errors:
                rejected[proposal.proposal_id] = lock_errors
            else:
                budget_eligible.append(proposal)

        # 3-5. Priority is lexicographic.  Within equal priority, nondominated
        # alternatives are considered first; opportunity cost resolves a Pareto tie.
        ordered: list[Proposal] = []
        for priority in sorted({item.priority for item in budget_eligible}, reverse=True):
            priority_group = [item for item in budget_eligible if item.priority == priority]
            for front in _pareto_fronts(priority_group):
                ordered.extend(
                    sorted(
                        front,
                        key=lambda item: (
                            item.opportunity_cost,
                            -item.success.probability,
                            -item.success.confidence,
                            item.proposal_id,
                        ),
                    )
                )

        selected: list[Proposal] = []
        usage: dict[str, float] = {}
        for proposal in ordered:
            conflict = self._allocation_error(
                proposal,
                selected,
                held_lock_list,
                limits,
            )
            if conflict:
                rejected[proposal.proposal_id] = [conflict]
                continue
            selected.append(proposal)
            for lock in proposal.budget_locks:
                usage[lock.key] = usage.get(lock.key, 0.0) + lock.amount

        selected_ids = tuple(proposal.proposal_id for proposal in selected)
        if selected_ids and rejected:
            status = CouncilDecisionStatus.PARTIAL
        elif selected_ids:
            status = CouncilDecisionStatus.APPROVED
        else:
            status = CouncilDecisionStatus.REJECTED
        if decision_id is None:
            digest_input = f"{turn}:" + ",".join(sorted(ids))
            digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]
            decision_id = f"council:{turn}:{digest}"

        explanation = (
            f"hard constraints: {len(eligible)}/{len(proposal_list)} proposals passed",
            f"budget locks: {len(budget_eligible)}/{len(eligible)} proposals had usable locks",
            "priority: higher integer priority considered first without weighted scoring",
            "pareto: probability, confidence, benefits and costs compared as separate dimensions",
            "opportunity cost: lower cost broke ties inside each Pareto front",
            f"resolution: selected {len(selected_ids)}, rejected {len(rejected)}",
        )
        return CouncilDecision(
            decision_id=decision_id,
            turn=turn,
            status=status,
            selected_proposal_ids=selected_ids,
            considered_proposal_ids=tuple(ids),
            rejected_reasons={key: tuple(value) for key, value in rejected.items()},
            explanation=explanation,
            budget_usage=usage,
        )

    @staticmethod
    def _validate_limits(budget_limits: Mapping[str, float]) -> dict[str, float]:
        if not isinstance(budget_limits, Mapping):
            raise TypeError("budget_limits must be a mapping")
        limits: dict[str, float] = {}
        for key, value in budget_limits.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("budget limit keys must be non-empty strings")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"budget limit {key!r} must be numeric, not bool")
            if value < 0:
                raise ValueError(f"budget limit {key!r} must be non-negative")
            limits[key] = float(value)
        return limits

    @staticmethod
    def _limit_for(lock: BudgetLock, limits: Mapping[str, float]) -> float | None:
        if lock.key in limits:
            return limits[lock.key]
        return limits.get(lock.resource)

    def _individual_lock_errors(
        self, proposal: Proposal, limits: Mapping[str, float]
    ) -> list[str]:
        errors: list[str] = []
        for lock in proposal.budget_locks:
            limit = self._limit_for(lock, limits)
            if limit is None and not lock.exclusive:
                errors.append(f"budget capacity missing for {lock.key}")
            elif limit is not None and lock.amount > limit:
                errors.append(
                    f"budget lock {lock.key} requests {lock.amount:g}, available {limit:g}"
                )
        return errors

    def _allocation_error(
        self,
        proposal: Proposal,
        selected: list[Proposal],
        held_locks: list[BudgetLock],
        limits: Mapping[str, float],
    ) -> str | None:
        for lock in proposal.budget_locks:
            for held in held_locks:
                if _locks_overlap(lock, held) and (lock.exclusive or held.exclusive):
                    return (
                        f"exclusive budget lock {lock.resource}:{lock.scope} conflicts "
                        "with an active reservation"
                    )
        for selected_proposal in selected:
            for lock in proposal.budget_locks:
                for held in selected_proposal.budget_locks:
                    if _locks_overlap(lock, held) and (lock.exclusive or held.exclusive):
                        return (
                            f"exclusive budget lock {lock.resource}:{lock.scope} conflicts "
                            f"with selected proposal {selected_proposal.proposal_id}"
                        )
        for lock in proposal.budget_locks:
            limit = self._limit_for(lock, limits)
            if limit is None:
                continue
            previously_locked = sum(
                held.amount for held in held_locks if _locks_overlap(lock, held)
            )
            newly_locked = sum(
                held.amount
                for chosen in selected
                for held in chosen.budget_locks
                if _locks_overlap(lock, held)
            )
            used = previously_locked + newly_locked
            if used + lock.amount > limit:
                holders = ", ".join(
                    chosen.proposal_id
                    for chosen in selected
                    if any(_locks_overlap(lock, held) for held in chosen.budget_locks)
                )
                return (
                    f"budget {lock.key} would use {used + lock.amount:g}/{limit:g}; "
                    f"already locked by {holders or 'an active reservation'}"
                )
        return None
