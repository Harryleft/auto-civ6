from __future__ import annotations

from civ6_belief_engine.governance import RulesetCapabilities, TurnSnapshot
from civ6_belief_engine.governance.departments.base import (
    Department,
    DepartmentContext,
    ReviewDisposition,
)
from civ6_belief_engine.governance.departments.military import MilitaryDepartment
from civ6_belief_engine.governance.models import Outcome, OutcomeStatus
from civ_mcp.lua.models import (
    BarbarianCamp,
    BarbarianOverview,
    BarbarianUnit,
    CityInfo,
    GameOverview,
    UnitInfo,
)


def _unit(
    unit_id: int,
    unit_type: str,
    *,
    health: int = 100,
    moves_remaining: float = 1.0,
    combat_strength: int = 0,
    ranged_strength: int = 0,
) -> UnitInfo:
    return UnitInfo(
        unit_id=unit_id,
        unit_index=unit_id,
        name=f"Unit {unit_id}",
        unit_type=unit_type,
        x=unit_id,
        y=unit_id + 1,
        moves_remaining=moves_remaining,
        max_moves=2.0,
        health=health,
        max_health=100,
        combat_strength=combat_strength,
        ranged_strength=ranged_strength,
    )


def _city(city_id: int = 1) -> CityInfo:
    return CityInfo(
        city_id=city_id,
        name=f"City {city_id}",
        x=1,
        y=2,
        population=5,
        food=10.0,
        production=8.0,
        gold=5.0,
        science=4.0,
        culture=3.0,
        faith=2.0,
        housing=6.0,
        amenities=1,
        turns_to_grow=4,
        defense_strength=30,
        garrison_hp=100,
        garrison_max_hp=100,
    )


def _snapshot(
    *,
    units: tuple[UnitInfo, ...] = (),
    cities: tuple[CityInfo, ...] = (),
    barbarians: BarbarianOverview | None = None,
    overview_num_units: int | None = None,
) -> TurnSnapshot:
    overview = GameOverview(
        turn=12,
        player_id=0,
        civ_name="CIVILIZATION_TEST",
        leader_name="Leader Test",
        gold=100.0,
        gold_per_turn=5.0,
        science_yield=10.0,
        culture_yield=8.0,
        faith=3.0,
        current_research="TECH_MINING",
        current_civic="CIVIC_CODE_OF_LAWS",
        num_cities=len(cities),
        num_units=len(units) if overview_num_units is None else overview_num_units,
        ruleset="Standard",
    )
    return TurnSnapshot(
        snapshot_id="snapshot:military:12",
        turn=12,
        turn_before=12,
        turn_after=12,
        player_id=0,
        captured_at=12.0,
        capabilities=RulesetCapabilities.standard(),
        overview=overview,
        cities=cities,
        units=units,
        barbarians=barbarians,
    )


def _context(snapshot: TurnSnapshot, *agenda: str) -> DepartmentContext:
    return DepartmentContext(snapshot=snapshot, agenda=agenda)


def _outcome(status: OutcomeStatus, *, error: str | None = None) -> Outcome:
    return Outcome(
        outcome_id="outcome:military:1",
        intent_id="intent:military:1",
        proposal_id="proposal:military:1",
        decision_id="decision:military:1",
        status=status,
        turn=12,
        error=error,
    )


def test_normal_assessment_identifies_combat_damage_and_actionability() -> None:
    department = MilitaryDepartment()
    context = _context(
        _snapshot(
            units=(
                _unit(2, "UNIT_WARRIOR", health=70, moves_remaining=1.0, combat_strength=20),
                _unit(1, "UNIT_ARCHER", moves_remaining=0.0, ranged_strength=30),
                _unit(3, "UNIT_BUILDER", moves_remaining=2.0),
            ),
            cities=(_city(),),
            barbarians=BarbarianOverview(),
        )
    )

    assessment = department.assess(context)

    assert department.department is Department.MILITARY
    assert assessment.relevance == department.match(context)
    assert assessment.degraded is False
    assert "己方战斗单位: 2 个（1, 2）" in assessment.facts
    assert "受伤战斗单位: 1 个（2）" in assessment.facts
    assert "当前可行动战斗单位: 1 个（2）" in assessment.facts
    assert assessment.support_requests == ()
    assert "本土防御" in assessment.workstreams[0].objective
    assert all("调动全部单位" not in value for value in assessment.facts)


def test_missing_evidence_degrades_and_does_not_infer_clean_map() -> None:
    department = MilitaryDepartment()
    context = _context(_snapshot(overview_num_units=1))

    assessment = department.assess(context)

    assert assessment.relevance == department.match(context) == 0.5
    assert assessment.degraded is True
    assert "己方单位明细" in assessment.evidence_missing
    assert "蛮族情报（已知营地与可见单位）" in assessment.evidence_missing
    assert any("不能据此认定迷雾区没有蛮族" in value for value in assessment.facts)
    assert assessment.support_requests == ()


def test_barbarian_assessment_requests_cross_department_support_and_reserve() -> None:
    department = MilitaryDepartment()
    context = _context(
        _snapshot(
            units=(_unit(1, "UNIT_WARRIOR", combat_strength=20),),
            cities=(_city(),),
            barbarians=BarbarianOverview(
                camps=[BarbarianCamp(x=8, y=9, distance_to_city=4)],
                units=[
                    BarbarianUnit(
                        unit_id=20,
                        unit_type="UNIT_BARBARIAN_WARRIOR",
                        x=7,
                        y=8,
                        hp=60,
                        max_hp=100,
                        combat_strength=20,
                        ranged_strength=0,
                    )
                ],
            ),
        ),
        "清剿蛮族",
    )

    assessment = department.assess(context)

    assert assessment.relevance == department.match(context) == 1.0
    assert assessment.degraded is False
    assert {request.target for request in assessment.support_requests} == {
        Department.PRODUCTION,
        Department.ECONOMY,
        Department.CIVICS,
    }
    assert all(
        request.requester is Department.MILITARY
        for request in assessment.support_requests
    )
    workstream = assessment.workstreams[0]
    assert workstream.objective == "清除已知蛮族威胁，同时保留本土防御"
    assert "仅从满足本土防御余量的单位中筛选清剿编组" in workstream.candidate_actions
    assert workstream.resource_claims["home_defense_reserve"] == 1.0


def test_assessment_is_deterministic_and_review_is_conservative() -> None:
    barbarians = BarbarianOverview(
        camps=[BarbarianCamp(x=8, y=9)],
        units=[],
    )
    first_context = _context(
        _snapshot(
            units=(_unit(2, "UNIT_WARRIOR", combat_strength=20), _unit(1, "UNIT_ARCHER", ranged_strength=30)),
            cities=(_city(),),
            barbarians=barbarians,
        )
    )
    second_context = _context(
        _snapshot(
            units=(_unit(1, "UNIT_ARCHER", ranged_strength=30), _unit(2, "UNIT_WARRIOR", combat_strength=20)),
            cities=(_city(),),
            barbarians=BarbarianOverview(camps=[BarbarianCamp(x=8, y=9)], units=[]),
        )
    )
    department = MilitaryDepartment()

    assert department.assess(first_context) == department.assess(second_context)
    assert department.review(first_context, _outcome(OutcomeStatus.SUCCEEDED)) is ReviewDisposition.CONTINUE
    assert department.review(
        _context(
            _snapshot(
                units=(_unit(1, "UNIT_WARRIOR", combat_strength=20),),
                cities=(_city(),),
                barbarians=BarbarianOverview(),
            )
        ),
        _outcome(OutcomeStatus.SUCCEEDED),
    ) is ReviewDisposition.EXIT
    assert department.review(
        first_context,
        _outcome(OutcomeStatus.RETRYABLE, error="temporary failure"),
    ) is ReviewDisposition.REPLAN
