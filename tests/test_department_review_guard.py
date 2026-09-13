"""Every department must reject an outcome that belongs to a previous turn.

``BaseDepartment._review_precheck`` exists to centralise exactly this guard.
Four departments do not inherit the base class, so only some of them apply it —
and two of them currently treat a stale outcome as current evidence, returning
CONTINUE ("keep working on this plan") for a result whose motivation is gone.

These tests are written against the *intended* semantics, so they fail until the
guard is shared rather than re-implemented per department.
"""

from __future__ import annotations

import pytest

from civ6_belief_engine.governance import GraphSnapshotView
from civ6_belief_engine.governance.departments.base import (
    BaseDepartment,
    DepartmentContext,
    ReviewDisposition,
)
from civ6_belief_engine.governance.departments.civics import CivicsDepartment
from civ6_belief_engine.governance.departments.diplomacy import DiplomacyDepartment
from civ6_belief_engine.governance.departments.economy import EconomyDepartment
from civ6_belief_engine.governance.departments.great_people import (
    GreatPeopleDepartment,
)
from civ6_belief_engine.governance.departments.military import MilitaryDepartment
from civ6_belief_engine.governance.departments.production import ProductionDepartment
from civ6_belief_engine.governance.departments.science import ScienceDepartment
from civ6_belief_engine.governance.models import Outcome, OutcomeStatus
from graph_test_helpers import graph_for_snapshot
from test_department_science import _overview, _tech_status

DEPARTMENTS = {
    "civics": CivicsDepartment,
    "diplomacy": DiplomacyDepartment,
    "economy": EconomyDepartment,
    "great_people": GreatPeopleDepartment,
    "military": MilitaryDepartment,
    "production": ProductionDepartment,
    "science": ScienceDepartment,
}


def _context(turn: int = 42) -> DepartmentContext:
    snapshot = GraphSnapshotView(
        snapshot_id="snapshot:review-guard",
        turn=turn,
        player_id=0,
        ready=True,
        source="test",
        overview=_overview(turn=turn),
        tech_civic=_tech_status(),
        threat_scan_available=False,
    )
    return DepartmentContext(
        snapshot=snapshot,
        graph=graph_for_snapshot(snapshot),
        agenda=(),
    )


def _outcome(*, turn: int, status: OutcomeStatus = OutcomeStatus.SUCCEEDED) -> Outcome:
    return Outcome(
        outcome_id="outcome:review-guard",
        intent_id="intent:review-guard",
        proposal_id="proposal:review-guard",
        decision_id="decision:review-guard",
        status=status,
        turn=turn,
        result={},
        # Failed/retryable outcomes must carry an error by contract.
        error=None if status is OutcomeStatus.SUCCEEDED else "command failed",
    )


@pytest.mark.parametrize("name", sorted(DEPARTMENTS))
def test_stale_outcome_is_always_replanned(name):
    """An outcome from turn N-1 is not evidence for turn N."""

    department = DEPARTMENTS[name]()
    disposition = department.review(_context(turn=42), _outcome(turn=41))

    assert disposition is ReviewDisposition.REPLAN, (
        f"{name} treated a turn-41 outcome as current evidence at turn 42 "
        f"and returned {disposition.value}"
    )


@pytest.mark.parametrize("name", sorted(DEPARTMENTS))
def test_non_succeeded_outcome_is_always_replanned(name):
    """Only a succeeded current-turn outcome may advance the workstream."""

    department = DEPARTMENTS[name]()
    for status in (OutcomeStatus.FAILED, OutcomeStatus.RETRYABLE):
        disposition = department.review(_context(turn=42), _outcome(turn=42, status=status))
        assert disposition is ReviewDisposition.REPLAN, (name, status)


@pytest.mark.parametrize("name", sorted(DEPARTMENTS))
def test_guard_lets_a_current_turn_success_through(name):
    """Guard against 'fix' by making the precheck always replan.

    A department may still replan for its own reasons (a degraded assessment),
    which is why this asserts on the shared guard rather than on ``review()``.
    """

    department = DEPARTMENTS[name]()
    assert (
        department._review_precheck(_context(turn=42), _outcome(turn=42)) is None
    ), f"{name}'s guard rejected a current-turn successful outcome"


@pytest.mark.parametrize("name", sorted(DEPARTMENTS))
def test_every_department_applies_the_shared_precheck(name):
    """The guard must come from one place, not be re-implemented per department."""

    department = DEPARTMENTS[name]()
    assert isinstance(department, BaseDepartment), (
        f"{name} does not inherit BaseDepartment, so it cannot share the guard"
    )
    assert type(department)._review_precheck is BaseDepartment._review_precheck
