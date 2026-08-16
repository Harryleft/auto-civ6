"""Tests for the deterministic, read-only science department plugin."""

from __future__ import annotations

from civ6_belief_engine.governance.departments.base import (
    Department,
    DepartmentContext,
    ReviewDisposition,
)
from civ6_belief_engine.governance.departments.science import ScienceDepartment
from civ6_belief_engine.governance import GraphSnapshotView
from civ6_belief_engine.governance.models import (
    Outcome,
    OutcomeStatus,
)
from graph_test_helpers import graph_for_snapshot
from civ_mcp.lua.models import (
    BarbarianCamp,
    BarbarianOverview,
    BarbarianUnit,
    GameOverview,
    TechCivicStatus,
    TechOption,
)


def _overview(*, turn: int = 42, current_research: str = "TECH_EDUCATION") -> GameOverview:
    return GameOverview(
        turn=turn,
        player_id=0,
        civ_name="CIVILIZATION_TEST",
        leader_name="Leader Test",
        gold=100.0,
        gold_per_turn=5.0,
        science_yield=30.0,
        culture_yield=20.0,
        faith=10.0,
        current_research=current_research,
        current_civic="CIVIC_CODE_OF_LAWS",
        num_cities=1,
        num_units=2,
        ruleset="Standard",
    )


def _tech_status(*, with_unlock: bool = True) -> TechCivicStatus:
    unlocks = "UNIT_SWORDSMAN; BUILDING_ARSENAL" if with_unlock else ""
    return TechCivicStatus(
        current_research="TECH_EDUCATION",
        current_research_turns=4,
        current_civic="CIVIC_CODE_OF_LAWS",
        current_civic_turns=2,
        available_techs=[
            TechOption(
                name="Iron Working",
                tech_type="TECH_IRON_WORKING",
                cost=120,
                progress_pct=25,
                turns=6,
                boosted=False,
                boost_desc="",
                unlocks=unlocks,
                prereqs="TECH_MINING",
                era="ERA_CLASSICAL",
            ),
            TechOption(
                name="Mathematics",
                tech_type="TECH_MATHEMATICS",
                cost=100,
                progress_pct=10,
                turns=5,
                boosted=True,
                boost_desc="Build a water mill",
                unlocks="BUILDING_WATER_MILL",
                prereqs="TECH_WRITING",
                era="ERA_CLASSICAL",
            ),
        ],
        available_civics=[],
        completed_tech_count=8,
        completed_civic_count=5,
    )


def _snapshot(
    *,
    overview: GameOverview | None = None,
    tech_civic: TechCivicStatus | None = None,
    barbarians: BarbarianOverview | None = None,
    turn: int = 42,
) -> GraphSnapshotView:
    return GraphSnapshotView(
        snapshot_id=f"snapshot_test_{turn}",
        turn=turn,
        player_id=0,
        ready=True,
        source="test",
        overview=overview,
        tech_civic=tech_civic,
        barbarians=barbarians,
    )


def _context(snapshot: GraphSnapshotView, *agenda: str) -> DepartmentContext:
    return DepartmentContext(snapshot=snapshot, agenda=agenda, graph=graph_for_snapshot(snapshot))


def _outcome(status: OutcomeStatus, *, error: str | None = None) -> Outcome:
    return Outcome(
        outcome_id="outcome:test",
        intent_id="intent:test",
        proposal_id="proposal:test",
        decision_id="decision:test",
        status=status,
        turn=42,
        error=error,
    )


def test_science_assessment_reads_current_options_turn_and_raw_unlock_text():
    department = ScienceDepartment()
    context = _context(
        _snapshot(
            overview=_overview(),
            tech_civic=_tech_status(),
            barbarians=BarbarianOverview(),
        ),
        "science planning",
    )

    assessment = department.assess(context)

    assert department.department is Department.SCIENCE
    assert assessment.department is Department.SCIENCE
    assert assessment.relevance == department.match(context)
    assert 0.0 < assessment.relevance <= 1.0
    assert assessment.degraded is False
    assert assessment.evidence_missing == ()
    assert "turn: 42" in assessment.facts
    assert "current research: TECH_EDUCATION" in assessment.facts
    assert any("TECH_IRON_WORKING" in fact for fact in assessment.facts)
    assert any("raw, unverified" in fact and "UNIT_SWORDSMAN" in fact for fact in assessment.facts)


def test_science_assessment_is_explicitly_degraded_when_core_evidence_is_missing():
    department = ScienceDepartment()
    context = _context(_snapshot())

    assessment = department.assess(context)

    assert assessment.relevance == department.match(context) == 0.0
    assert assessment.degraded is True
    assert any("current research" in item for item in assessment.evidence_missing)
    assert any("available technologies" in item for item in assessment.evidence_missing)
    assert any("unlock text" in item for item in assessment.evidence_missing)
    assert any("barbarian overview" in item for item in assessment.evidence_missing)


def test_barbarian_presence_creates_military_collaboration_without_unit_assumptions():
    department = ScienceDepartment()
    barbarians = BarbarianOverview(
        camps=[BarbarianCamp(x=8, y=9, visibility="revealed")],
        units=[
            BarbarianUnit(
                unit_id=7,
                unit_type="UNIT_BARBARIAN_RAIDER",
                x=7,
                y=9,
                hp=35,
                max_hp=40,
                combat_strength=20,
                ranged_strength=0,
            )
        ],
    )
    context = _context(
        _snapshot(
            overview=_overview(),
            tech_civic=_tech_status(with_unlock=True),
            barbarians=barbarians,
        )
    )

    assessment = department.assess(context)

    assert len(assessment.support_requests) == 1
    request = assessment.support_requests[0]
    assert request.target is Department.MILITARY
    assert "军事解锁机会" in request.objective
    assert "不推断具体兵种" in request.reason
    assert any("军事部门" in item for item in assessment.opportunities)
    assert any("raw, unverified" in item for item in assessment.facts)


def test_science_review_replans_failed_or_degraded_outcomes_and_continues_valid_work():
    department = ScienceDepartment()
    complete_context = _context(
        _snapshot(
            overview=_overview(),
            tech_civic=_tech_status(),
            barbarians=BarbarianOverview(),
        )
    )
    missing_context = _context(_snapshot())

    assert department.review(
        complete_context,
        _outcome(OutcomeStatus.SUCCEEDED),
    ) is ReviewDisposition.CONTINUE
    assert department.review(
        complete_context,
        _outcome(OutcomeStatus.RETRYABLE, error="temporary failure"),
    ) is ReviewDisposition.REPLAN
    assert department.review(
        missing_context,
        _outcome(OutcomeStatus.SUCCEEDED),
    ) is ReviewDisposition.REPLAN
