from __future__ import annotations

from civ6_belief_engine.governance import GraphSnapshotView
from civ6_belief_engine.governance.departments.base import (
    Department,
    DepartmentContext,
    ReviewDisposition,
)
from civ6_belief_engine.governance.departments.civics import CivicsDepartment
from civ_mcp.lua.models import (
    BarbarianCamp,
    BarbarianOverview,
    BarbarianUnit,
    CivicOption,
    GovernmentStatus,
    PolicyInfo,
    PolicySlot,
    TechCivicStatus,
)
from civ6_belief_engine.governance.models import Outcome, OutcomeStatus


def _snapshot(
    *,
    tech_civic: TechCivicStatus | None = None,
    policies: GovernmentStatus | None = None,
    barbarians: BarbarianOverview | None = None,
) -> GraphSnapshotView:
    return GraphSnapshotView(
        snapshot_id="snapshot:civics:12",
        turn=12,
        player_id=0,
        ready=True,
        source="test",
        tech_civic=tech_civic,
        policies=policies,
        barbarians=barbarians,
    )


def _tech_civic() -> TechCivicStatus:
    return TechCivicStatus(
        current_research="TECH_MINING",
        current_research_turns=4,
        current_civic="CIVIC_CODE_OF_LAWS",
        current_civic_turns=2,
        available_techs=[],
        available_civics=[
            CivicOption(
                name="Foreign Trade",
                civic_type="CIVIC_FOREIGN_TRADE",
                cost=40,
                progress_pct=25,
                turns=3,
                boosted=True,
                boost_desc="meet a foreign civilization",
            ),
            CivicOption(
                name="Craftsmanship",
                civic_type="CIVIC_CRAFTSMANSHIP",
                cost=40,
                progress_pct=0,
                turns=5,
                boosted=False,
                boost_desc="improve a resource",
            ),
        ],
    )


def _policies() -> GovernmentStatus:
    return GovernmentStatus(
        government_name="Chiefdom",
        government_type="GOVERNMENT_CHIEFDOM",
        slots=[
            PolicySlot(1, "SLOT_WILDCARD", None, None),
            PolicySlot(0, "SLOT_MILITARY", None, None),
            PolicySlot(2, "SLOT_ECONOMIC", "POLICY_GOD_KING", "God King"),
        ],
        available_policies=[
            PolicyInfo("POLICY_AGOGE", "Agoge", "Unit production", "SLOT_MILITARY"),
            PolicyInfo(
                "POLICY_GOD_KING",
                "God King",
                "Faith and gold",
                "SLOT_ECONOMIC",
            ),
            PolicyInfo(
                "POLICY_SURVEY",
                "Survey",
                "Scout production",
                "SLOT_MILITARY",
            ),
        ],
    )


def _outcome(status: OutcomeStatus) -> Outcome:
    return Outcome(
        outcome_id="outcome:1",
        intent_id="intent:1",
        proposal_id="proposal:1",
        decision_id="decision:1",
        status=status,
        turn=12,
        error=None if status is OutcomeStatus.SUCCEEDED else "test outcome",
    )


def test_normal_assessment_is_deterministic_and_wildcard_is_candidate_only() -> None:
    context = DepartmentContext(
        snapshot=_snapshot(tech_civic=_tech_civic(), policies=_policies()),
        agenda=("review civics and policy options",),
    )
    department = CivicsDepartment()

    first = department.assess(context)
    second = department.assess(context)

    assert department.department is Department.CIVICS
    assert first == second
    assert first.relevance == department.match(context)
    assert "current_civic:CIVIC_CODE_OF_LAWS" in first.facts
    assert "empty_policy_slot:1:SLOT_WILDCARD" in first.opportunities
    assert "candidate_policy:1:POLICY_AGOGE" in first.opportunities
    assert "candidate_policy:1:POLICY_GOD_KING" in first.opportunities
    assert "candidate_policy:1:POLICY_SURVEY" in first.opportunities
    assert all("set_policies" not in item for item in first.opportunities)
    policy_workstream = next(
        item for item in first.workstreams if item.workstream_id == "civics:policy-slots"
    )
    assert all("set_policies" not in item for item in policy_workstream.candidate_actions)


def test_missing_evidence_is_degraded_and_does_not_invent_policy_state() -> None:
    department = CivicsDepartment()
    context = DepartmentContext(snapshot=_snapshot())

    assessment = department.assess(context)

    assert assessment.relevance == department.match(context) == 0.0
    assert assessment.degraded is True
    assert assessment.evidence_missing == ("tech_civic", "policies")
    assert assessment.facts == ()
    assert assessment.opportunities == ()
    assert assessment.support_requests == ()
    assert assessment.workstreams == ()


def test_barbarians_emit_military_support_signal_without_military_action() -> None:
    context = DepartmentContext(
        snapshot=_snapshot(
            tech_civic=_tech_civic(),
            policies=_policies(),
            barbarians=BarbarianOverview(
                camps=[BarbarianCamp(8, 9, distance_to_city=4, distance_to_military=2)],
                units=[
                    BarbarianUnit(
                        63,
                        "UNIT_WARRIOR",
                        8,
                        8,
                        100,
                        100,
                        20,
                        0,
                        3,
                        1,
                    )
                ],
            ),
        )
    )

    assessment = CivicsDepartment().assess(context)

    assert "barbarian_military_policy_support" in assessment.opportunities
    assert len(assessment.support_requests) == 1
    request = assessment.support_requests[0]
    assert request.requester is Department.CIVICS
    assert request.target is Department.MILITARY
    assert all("ActionIntent" not in item for item in assessment.opportunities)


def test_review_is_a_fixed_outcome_mapping() -> None:
    department = CivicsDepartment()
    context = DepartmentContext(snapshot=_snapshot())

    assert department.review(context, _outcome(OutcomeStatus.SUCCEEDED)) is ReviewDisposition.CONTINUE
    assert department.review(context, _outcome(OutcomeStatus.FAILED)) is ReviewDisposition.REPLAN
    assert department.review(context, _outcome(OutcomeStatus.RETRYABLE)) is ReviewDisposition.REPLAN
