from __future__ import annotations

from civ6_belief_engine.governance import GraphSnapshotView
from civ6_belief_engine.governance.departments.base import (
    Department,
    DepartmentContext,
    ReviewDisposition,
)
from civ6_belief_engine.governance.departments.production import ProductionDepartment
from civ6_belief_engine.governance.models import Outcome, OutcomeStatus
from civ_mcp.lua.models import (
    BarbarianCamp,
    BarbarianOverview,
    BarbarianUnit,
    CityInfo,
    UnitInfo,
)


def _city(
    city_id: int,
    name: str,
    *,
    production: float,
    current: str,
    turns_left: int,
    defense: int,
) -> CityInfo:
    return CityInfo(
        city_id=city_id,
        name=name,
        x=city_id,
        y=city_id + 1,
        population=5,
        food=8.0,
        production=production,
        gold=4.0,
        science=3.0,
        culture=2.0,
        faith=1.0,
        housing=6.0,
        amenities=1,
        turns_to_grow=4,
        currently_building=current,
        production_turns_left=turns_left,
        defense_strength=defense,
    )


def _unit(unit_id: int, strength: int) -> UnitInfo:
    return UnitInfo(
        unit_id=unit_id,
        unit_index=unit_id,
        name=f"Unit {unit_id}",
        unit_type="UNIT_WARRIOR",
        x=unit_id,
        y=unit_id,
        moves_remaining=1.0,
        max_moves=2.0,
        health=100,
        max_health=100,
        combat_strength=strength,
    )


def _snapshot(
    *,
    cities: tuple[CityInfo, ...] = (),
    units: tuple[UnitInfo, ...] = (),
    barbarians: BarbarianOverview | None = None,
) -> GraphSnapshotView:
    return GraphSnapshotView(
        snapshot_id="snapshot:production:42",
        turn=42,
        player_id=0,
        ready=True,
        source="test",
        cities=cities,
        units=units,
        barbarians=barbarians,
    )


def _context(snapshot: GraphSnapshotView, *, agenda: tuple[str, ...] = ()) -> DepartmentContext:
    return DepartmentContext(snapshot=snapshot, agenda=agenda)


def _outcome(status: OutcomeStatus, *, result: dict | None = None) -> Outcome:
    error = None if status is OutcomeStatus.SUCCEEDED else "production action failed"
    return Outcome(
        outcome_id="outcome:production:1",
        intent_id="intent:production:1",
        proposal_id="proposal:production:1",
        decision_id="decision:production:1",
        status=status,
        turn=42,
        result=result or {},
        error=error,
    )


def test_normal_assessment_reads_city_production_and_matches_relevance() -> None:
    snapshot = _snapshot(
        cities=(
            _city(
                2,
                "Frontier",
                production=5.0,
                current="NONE",
                turns_left=0,
                defense=25,
            ),
            _city(
                1,
                "Capital",
                production=12.5,
                current="BUILDING_LIBRARY",
                turns_left=6,
                defense=55,
            ),
        ),
        barbarians=BarbarianOverview(),
    )
    context = _context(snapshot)
    department = ProductionDepartment()

    assessment = department.assess(context)

    assert department.department is Department.PRODUCTION
    assert assessment.relevance == department.match(context)
    assert assessment.degraded is False
    assert assessment.evidence_missing == ()
    assert any("production=12.50" in fact for fact in assessment.facts)
    assert any("currently_building=BUILDING_LIBRARY" in fact for fact in assessment.facts)
    assert any("production_turns_left=6" in fact for fact in assessment.facts)
    assert any("defense_strength=55" in fact for fact in assessment.facts)
    assert [item.workstream_id for item in assessment.workstreams] == [
        "production:protect-key-cities"
    ]
    assert assessment.support_requests == ()


def test_missing_city_evidence_returns_degraded_read_only_assessment() -> None:
    context = _context(_snapshot())
    department = ProductionDepartment()

    assessment = department.assess(context)

    assert department.match(context) == 0.0
    assert assessment.relevance == 0.0
    assert assessment.degraded is True
    assert "cities" in assessment.evidence_missing
    assert "city.production" in assessment.evidence_missing
    assert assessment.workstreams == ()
    assert assessment.support_requests == ()


def test_missing_barbarian_evidence_is_explicitly_degraded() -> None:
    context = _context(
        _snapshot(
            cities=(
                _city(
                    1,
                    "Capital",
                    production=10.0,
                    current="BUILDING_MONUMENT",
                    turns_left=3,
                    defense=40,
                ),
            )
        )
    )

    assessment = ProductionDepartment().assess(context)

    assert assessment.degraded is True
    assert assessment.evidence_missing == ("barbarian_overview",)
    assert "蛮族态势证据" in assessment.risks[0]


def test_barbarian_shortfall_requests_minimum_reinforcement_and_preserves_key_city() -> None:
    snapshot = _snapshot(
        cities=(
            _city(
                2,
                "Frontier",
                production=4.0,
                current="NONE",
                turns_left=0,
                defense=30,
            ),
            _city(
                1,
                "Capital",
                production=14.0,
                current="WONDER",
                turns_left=8,
                defense=60,
            ),
        ),
        units=(),
        barbarians=BarbarianOverview(
            camps=[BarbarianCamp(x=8, y=8, distance_to_city=4)],
            units=[
                BarbarianUnit(
                    unit_id=99,
                    unit_type="UNIT_BARBARIAN_WARRIOR",
                    x=8,
                    y=9,
                    hp=100,
                    max_hp=100,
                    combat_strength=25,
                    ranged_strength=0,
                )
            ],
        ),
    )
    context = _context(snapshot)
    department = ProductionDepartment()

    first = department.assess(context)
    second = department.assess(context)
    workstreams = {item.workstream_id: item for item in first.workstreams}

    assert first == second
    assert first.relevance == department.match(context)
    assert first.degraded is False
    assert first.support_requests[0].target is Department.MILITARY
    assert "barbarian_minimum_reinforcement" in first.capability_gaps
    assert set(workstreams) == {
        "production:protect-key-cities",
        "production:minimum-barbarian-reinforcement",
    }
    protection = workstreams["production:protect-key-cities"]
    reinforcement = workstreams["production:minimum-barbarian-reinforcement"]
    assert protection.resource_claims == {"city_production:city:1": 1.0}
    assert any("city:2" in action for action in reinforcement.candidate_actions)
    assert any("最低补充数量：3 个单位" in action for action in reinforcement.candidate_actions)
    assert any("所有城市造兵" in action for action in reinforcement.candidate_actions)


def test_review_is_deterministic_and_only_returns_disposition() -> None:
    context = _context(
        _snapshot(
            cities=(
                _city(
                    1,
                    "Capital",
                    production=10.0,
                    current="BUILDING_MONUMENT",
                    turns_left=3,
                    defense=40,
                ),
            ),
            barbarians=BarbarianOverview(),
        )
    )
    department = ProductionDepartment()

    assert department.review(context, _outcome(OutcomeStatus.RETRYABLE)) is ReviewDisposition.REPLAN
    assert department.review(
        context, _outcome(OutcomeStatus.SUCCEEDED)
    ) is ReviewDisposition.CONTINUE
    assert department.review(
        context,
        _outcome(OutcomeStatus.SUCCEEDED, result={"workflow_complete": True}),
    ) is ReviewDisposition.EXIT
