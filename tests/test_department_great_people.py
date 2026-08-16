"""Behavioural tests for the great people strategy department."""

from __future__ import annotations

from civ6_belief_engine.governance import RulesetCapabilities, TurnSnapshot
from civ6_belief_engine.governance.departments.base import (
    Department,
    DepartmentContext,
    ReviewDisposition,
)
from civ6_belief_engine.governance.departments.great_people import GreatPeopleDepartment
from civ6_belief_engine.governance.models import Outcome, OutcomeStatus
from civ_mcp.lua.models import (
    GameOverview,
    GPClassStanding,
    GPPlayerPoints,
    GreatPeopleOverview,
)


def _overview(*, faith: float = 100.0) -> GameOverview:
    return GameOverview(
        turn=42,
        player_id=0,
        civ_name="CIVILIZATION_TEST",
        leader_name="Leader",
        gold=300.0,
        gold_per_turn=20.0,
        science_yield=30.0,
        culture_yield=18.0,
        faith=faith,
        current_research="TECH_EDUCATION",
        current_civic="CIVIC_FEUDALISM",
        num_cities=2,
        num_units=4,
        total_maintenance=5.0,
        ruleset="RULESET_EXPANSION_2",
    )


def _standing(
    class_name: str, ours: int, *rivals: tuple[str, int]
) -> GPClassStanding:
    entries = [GPPlayerPoints(player_id=0, player_name="Us", points_total=ours)]
    for index, (name, points) in enumerate(rivals, start=1):
        entries.append(
            GPPlayerPoints(player_id=index, player_name=name, points_total=points)
        )
    return GPClassStanding(class_name=class_name, entries=entries)


def _gp(*standings: GPClassStanding) -> GreatPeopleOverview:
    return GreatPeopleOverview(standings=list(standings))


def _snapshot(
    *,
    overview: GameOverview | None = None,
    great_people: GreatPeopleOverview | None = None,
) -> TurnSnapshot:
    return TurnSnapshot(
        snapshot_id="snapshot:great_people:42",
        turn=42,
        turn_before=42,
        turn_after=42,
        player_id=0,
        captured_at=42.0,
        capabilities=RulesetCapabilities.standard(),
        overview=overview,
        great_people=great_people,
    )


def _context(snapshot: TurnSnapshot, *, agenda: tuple[str, ...] = ()) -> DepartmentContext:
    return DepartmentContext(snapshot=snapshot, agenda=agenda)


def _outcome(status: OutcomeStatus, *, result: dict | None = None) -> Outcome:
    return Outcome(
        outcome_id="outcome:great_people:1",
        intent_id="intent:great_people:1",
        proposal_id="proposal:great_people:1",
        decision_id="decision:great_people:1",
        status=status,
        turn=42,
        result=result or {},
        error=None if status is OutcomeStatus.SUCCEEDED else "great people action failed",
    )


def test_missing_evidence_degrades_assessment():
    department = GreatPeopleDepartment()
    context = _context(_snapshot(overview=_overview()))
    assessment = department.assess(context)
    assert assessment.degraded is True
    assert assessment.evidence_missing == ("great_people",)
    assert assessment.workstreams == ()
    assert assessment.relevance == 0.0


def test_leading_every_class_produces_no_race_workstream():
    department = GreatPeopleDepartment()
    gp = _gp(
        _standing("Scientist", 60, ("Babylon", 48)),
        _standing("Writer", 25),
    )
    assessment = department.assess(_context(_snapshot(overview=_overview(), great_people=gp)))
    assert assessment.degraded is False
    assert assessment.workstreams == ()
    assert any("我们领先" in fact for fact in assessment.facts)


def test_rival_lead_creates_race_workstream_with_faith_claim():
    department = GreatPeopleDepartment()
    # Babylon leads Scientist by 8/48 (gap ratio ~0.17) -> pressure, priority 75.
    gp = _gp(_standing("Scientist", 40, ("Babylon", 48), ("Rome", 25)))
    assessment = department.assess(
        _context(_snapshot(overview=_overview(faith=100.0), great_people=gp))
    )
    assert assessment.degraded is False
    assert len(assessment.workstreams) == 1
    workstream = assessment.workstreams[0]
    assert workstream.department == Department.GREAT_PEOPLE
    assert workstream.priority == 75
    # Half of the 100 faith treasury is claimed as a conservative ceiling.
    assert workstream.resource_claims["faith"] == 50.0
    assert any("Babylon" in fact for fact in assessment.facts)
    assert any("竞争" in opportunity for opportunity in assessment.opportunities)


def test_large_gap_is_not_a_race():
    department = GreatPeopleDepartment()
    # 60-point gap on a 90-point leader: > 50% -> abandoned, no workstream.
    gp = _gp(_standing("Scientist", 30, ("Babylon", 90)))
    assessment = department.assess(_context(_snapshot(overview=_overview(), great_people=gp)))
    assert assessment.workstreams == ()
    assert any("不划算" in risk for risk in assessment.risks)


def test_review_dispositions():
    department = GreatPeopleDepartment()
    gp = _gp(_standing("Scientist", 40, ("Babylon", 48)))
    context = _context(_snapshot(overview=_overview(), great_people=gp))
    assert department.review(context, _outcome(OutcomeStatus.SUCCEEDED)) == ReviewDisposition.CONTINUE
    assert department.review(context, _outcome(OutcomeStatus.FAILED)) == ReviewDisposition.REPLAN
    assert (
        department.review(
            context, _outcome(OutcomeStatus.SUCCEEDED, result={"campaign_complete": True})
        )
        == ReviewDisposition.EXIT
    )
    stale = _outcome(OutcomeStatus.SUCCEEDED)
    stale = Outcome(
        outcome_id="outcome:great_people:1",
        intent_id="intent:great_people:1",
        proposal_id="proposal:great_people:1",
        decision_id="decision:great_people:1",
        status=OutcomeStatus.SUCCEEDED,
        turn=41,
        result={},
        error=None,
    )
    assert department.review(context, stale) == ReviewDisposition.REPLAN
