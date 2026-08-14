from __future__ import annotations

from civ6_belief_engine.governance import RulesetCapabilities, TurnSnapshot
from civ6_belief_engine.governance.departments.base import (
    Department,
    DepartmentContext,
    ReviewDisposition,
)
from civ6_belief_engine.governance.departments.economy import EconomyDepartment
from civ6_belief_engine.governance.models import Outcome, OutcomeStatus
from civ_mcp.lua.models import (
    BarbarianCamp,
    BarbarianOverview,
    GameOverview,
    ResourceStockpile,
    UnitInfo,
)


def _overview(
    *,
    gold: float = 400.0,
    gold_per_turn: float = 12.0,
    faith: float = 90.0,
    maintenance: float = 30.0,
    num_units: int = 2,
) -> GameOverview:
    return GameOverview(
        turn=42,
        player_id=0,
        civ_name="CIVILIZATION_TEST",
        leader_name="Leader",
        gold=gold,
        gold_per_turn=gold_per_turn,
        science_yield=30.0,
        culture_yield=18.0,
        faith=faith,
        current_research="TECH_EDUCATION",
        current_civic="CIVIC_FEUDALISM",
        num_cities=2,
        num_units=num_units,
        total_maintenance=maintenance,
        ruleset="RULESET_EXPANSION_2",
    )


def _unit(unit_id: int, *, can_upgrade: bool = False, cost: int = 0) -> UnitInfo:
    return UnitInfo(
        unit_id=unit_id,
        unit_index=unit_id,
        name=f"Unit {unit_id}",
        unit_type="UNIT_WARRIOR",
        x=unit_id,
        y=1,
        moves_remaining=1.0,
        max_moves=2.0,
        health=100,
        max_health=100,
        combat_strength=20,
        can_upgrade=can_upgrade,
        upgrade_target="UNIT_MUSKETMAN" if can_upgrade else "",
        upgrade_cost=cost,
    )


def _snapshot(
    *,
    overview: GameOverview | None = None,
    units: tuple[UnitInfo, ...] = (),
    resources: tuple[ResourceStockpile, ...] = (),
    barbarians: BarbarianOverview | None = None,
) -> TurnSnapshot:
    return TurnSnapshot(
        snapshot_id="snapshot:economy:42",
        turn=42,
        turn_before=42,
        turn_after=42,
        player_id=0,
        captured_at=1.0,
        capabilities=RulesetCapabilities(resource_stockpiles=True),
        overview=overview,
        units=units,
        resources=resources,
        barbarians=barbarians,
    )


def _normal_context(*, barbarians: BarbarianOverview | None = None) -> DepartmentContext:
    return DepartmentContext(
        snapshot=_snapshot(
            overview=_overview(),
            units=(_unit(2, can_upgrade=True, cost=80), _unit(1)),
            resources=(
                ResourceStockpile(
                    name="Iron",
                    amount=20,
                    cap=50,
                    per_turn=2,
                    demand=1,
                    imported=0,
                ),
            ),
            barbarians=barbarians,
        ),
        agenda=("maintain economic stability",),
    )


def test_normal_assessment_is_read_only_and_conservative() -> None:
    department = EconomyDepartment()
    context = _normal_context()

    before = context.snapshot
    assessment = department.assess(context)
    repeated = department.assess(context)

    assert department.department is Department.ECONOMY
    assert assessment.department is Department.ECONOMY
    assert assessment == repeated
    assert assessment.relevance == department.match(context)
    assert assessment.degraded is False
    assert "gold=400" in assessment.facts
    assert "gold_per_turn=12" in assessment.facts
    assert "total_maintenance=30" in assessment.facts
    assert "faith=90" in assessment.facts
    assert "resource=Iron:amount=20,cap=50,per_turn=2,demand=1,imported=0" in assessment.facts
    assert "upgrade_cost:2=80" in assessment.facts
    assert len(assessment.workstreams) == 1
    assert assessment.workstreams[0].resource_claims["gold"] == 140.0
    assert assessment.workstreams[0].resource_claims["gold"] < context.snapshot.overview.gold
    assert context.snapshot is before


def test_missing_evidence_fails_closed_and_reports_degradation() -> None:
    department = EconomyDepartment()
    context = DepartmentContext(snapshot=_snapshot(overview=None))

    assessment = department.assess(context)

    assert department.match(context) == 0.0
    assert assessment.relevance == 0.0
    assert assessment.degraded is True
    assert "overview.gold" in assessment.evidence_missing
    assert "overview.gold_per_turn" in assessment.evidence_missing
    assert "resource_stockpiles" in assessment.evidence_missing
    assert assessment.workstreams == ()
    assert assessment.support_requests == ()
    assert department.review(
        context,
        Outcome(
            outcome_id="outcome:missing",
            intent_id="intent:missing",
            proposal_id="proposal:missing",
            decision_id="decision:missing",
            status=OutcomeStatus.SUCCEEDED,
            turn=42,
        ),
    ) is ReviewDisposition.REPLAN


def test_invalid_upgrade_evidence_does_not_claim_affordability() -> None:
    department = EconomyDepartment()
    context = DepartmentContext(
        snapshot=_snapshot(
            overview=_overview(num_units=1),
            units=(_unit(7, can_upgrade=True, cost=0),),
            resources=(
                ResourceStockpile(
                    name="Iron", amount=10, cap=20, per_turn=1, demand=0, imported=0
                ),
            ),
        )
    )

    assessment = department.assess(context)

    assert assessment.degraded is True
    assert "unit_upgrade_cost:7" in assessment.evidence_missing
    assert assessment.workstreams == ()
    assert any("不宣称其可负担" in risk for risk in assessment.risks)


def test_barbarian_context_emits_military_budget_coordination_signal() -> None:
    department = EconomyDepartment()
    context = _normal_context(
        barbarians=BarbarianOverview(
            camps=[BarbarianCamp(x=8, y=9, distance_to_city=5, distance_to_military=3)],
            units=[],
        )
    )

    assessment = department.assess(context)

    assert len(assessment.support_requests) == 1
    request = assessment.support_requests[0]
    assert request.requester is Department.ECONOMY
    assert request.target is Department.MILITARY
    assert "蛮族" in request.objective
    assert "升级或购买预算" in request.objective
    assert assessment.workstreams[0].priority == 80
    assert assessment.relevance == department.match(context)


def test_review_replans_stale_or_unsuccessful_outcomes() -> None:
    department = EconomyDepartment()
    context = _normal_context()

    def outcome(status: OutcomeStatus, *, turn: int = 42, result: dict | None = None) -> Outcome:
        return Outcome(
            outcome_id="outcome:1",
            intent_id="intent:1",
            proposal_id="proposal:1",
            decision_id="decision:1",
            status=status,
            turn=turn,
            result=result or {},
            error="failed" if status is not OutcomeStatus.SUCCEEDED else None,
        )

    assert department.review(context, outcome(OutcomeStatus.SUCCEEDED)) is ReviewDisposition.CONTINUE
    assert department.review(
        context, outcome(OutcomeStatus.SUCCEEDED, result={"campaign_complete": True})
    ) is ReviewDisposition.EXIT
    assert department.review(context, outcome(OutcomeStatus.RETRYABLE)) is ReviewDisposition.REPLAN
    assert department.review(context, outcome(OutcomeStatus.FAILED)) is ReviewDisposition.REPLAN
    assert department.review(context, outcome(OutcomeStatus.SUCCEEDED, turn=41)) is ReviewDisposition.REPLAN
