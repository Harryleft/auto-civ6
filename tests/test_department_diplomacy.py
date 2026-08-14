from __future__ import annotations

from civ6_belief_engine.governance import RulesetCapabilities, TurnSnapshot
from civ6_belief_engine.governance.departments.base import (
    Department,
    DepartmentContext,
    ReviewDisposition,
)
from civ6_belief_engine.governance.departments.diplomacy import DiplomacyDepartment
from civ_mcp.lua.models import BarbarianCamp, BarbarianOverview, BarbarianUnit, CivInfo
from civ6_belief_engine.governance.models import Outcome, OutcomeStatus


def _snapshot(
    *,
    diplomacy: tuple[CivInfo, ...] = (),
    barbarians: BarbarianOverview | None = None,
    units: tuple = (),
) -> TurnSnapshot:
    return TurnSnapshot(
        snapshot_id="snapshot:diplomacy:9",
        turn=9,
        turn_before=9,
        turn_after=9,
        player_id=0,
        captured_at=1.0,
        capabilities=RulesetCapabilities.standard(),
        diplomacy=diplomacy,
        barbarians=barbarians,
        units=units,
    )


def _outcome(*, status: OutcomeStatus = OutcomeStatus.SUCCEEDED, turn: int = 9) -> Outcome:
    return Outcome(
        outcome_id="outcome:1",
        intent_id="intent:1",
        proposal_id="proposal:1",
        decision_id="decision:1",
        status=status,
        turn=turn,
        error=None if status is OutcomeStatus.SUCCEEDED else "test failure",
    )


def test_normal_assessment_reads_contact_war_relationship_and_military() -> None:
    civ = CivInfo(
        player_id=2,
        civ_name="罗马",
        leader_name="图拉真",
        has_met=True,
        is_at_war=False,
        diplomatic_state="FRIENDLY",
        relationship_score=42,
        military_strength=120,
    )
    context = DepartmentContext(snapshot=_snapshot(diplomacy=(civ,)))
    department = DiplomacyDepartment()

    assessment = department.assess(context)

    assert department.department is Department.DIPLOMACY
    assert assessment.department is Department.DIPLOMACY
    assert assessment.relevance == department.match(context)
    assert assessment.degraded is False
    assert assessment.evidence_missing == ()
    fact = assessment.facts[0]
    assert "已接触文明 罗马" in fact
    assert "war=no" in fact
    assert "relationship=42" in fact
    assert "military_strength=120" in fact
    assert assessment.workstreams[0].department is Department.DIPLOMACY


def test_missing_or_uncontacted_evidence_degrades_and_never_claims_safety() -> None:
    unknown = CivInfo(
        player_id=3,
        civ_name="未知文明",
        leader_name="未知领袖",
        has_met=False,
        is_at_war=False,
        military_strength=999,
    )
    context = DepartmentContext(snapshot=_snapshot(diplomacy=(unknown,)))
    department = DiplomacyDepartment()

    assessment = department.assess(context)

    assert assessment.degraded is True
    assert assessment.relevance == department.match(context)
    assert any("未接触" in item and "未知" in item for item in assessment.facts)
    assert any("不等于安全" in item for item in assessment.risks)
    assert any("关系、战争状态和军力未知" in item for item in assessment.evidence_missing)
    assert all("已确认安全" not in item for item in assessment.risks)


def test_empty_diplomacy_evidence_is_degraded_instead_of_safe() -> None:
    context = DepartmentContext(snapshot=_snapshot())

    assessment = DiplomacyDepartment().assess(context)

    assert assessment.degraded is True
    assert assessment.relevance == 0.0
    assert "证据不完整" in assessment.summary
    assert any("空结果不能解释为安全" in item for item in assessment.evidence_missing)


def test_barbarian_activity_emits_military_coordination_signal() -> None:
    barbarian_overview = BarbarianOverview(
        camps=[BarbarianCamp(x=4, y=5, distance_to_city=3)],
        units=[
            BarbarianUnit(
                unit_id=77,
                unit_type="BARBARIAN_WARRIOR",
                x=5,
                y=5,
                hp=20,
                max_hp=20,
                combat_strength=20,
                ranged_strength=0,
            )
        ],
    )
    context = DepartmentContext(
        snapshot=_snapshot(barbarians=barbarian_overview),
        agenda=("外交评估", "处理蛮族边境"),
    )
    department = DiplomacyDepartment()

    assessment = department.assess(context)

    assert assessment.relevance == department.match(context) == 1.0
    assert any("多线战争" in item for item in assessment.risks)
    assert len(assessment.support_requests) == 1
    request = assessment.support_requests[0]
    assert request.requester is Department.DIPLOMACY
    assert request.target is Department.MILITARY
    assert "并发战线" in request.objective
    assert assessment.workstreams[0].objective == "评估蛮族与文明战争的多线外交及边境风险"


def test_review_replans_failure_or_stale_outcome_and_exits_when_irrelevant() -> None:
    department = DiplomacyDepartment()
    context = DepartmentContext(snapshot=_snapshot())

    assert department.review(context, _outcome(status=OutcomeStatus.FAILED)) is ReviewDisposition.REPLAN
    assert department.review(context, _outcome(turn=8)) is ReviewDisposition.REPLAN
    assert department.review(context, _outcome()) is ReviewDisposition.REPLAN
