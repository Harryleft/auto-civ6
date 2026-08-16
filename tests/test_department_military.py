from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from civ6_belief_engine.governance import (
    GovernanceCouncil,
    ProbabilityConfidence,
    RulesetCapabilities,
    StrategicGoal,
    TypedTurnSnapshot,
)
from civ6_belief_engine.governance.departments.base import (
    Department,
    DepartmentContext,
    ReviewDisposition,
)
from civ6_belief_engine.governance.departments.military import MilitaryDepartment
from civ6_belief_engine.governance.graph_snapshot import GraphSnapshotView
from civ6_belief_engine.governance.models import Outcome, OutcomeStatus
from civ6_belief_engine.governance.snapshot import snapshot_world_state
from civ6_belief_engine.graph import (
    GraphView,
    project_active_goals,
    project_world_state,
)
from civ_mcp.lua.models import (
    BarbarianCamp,
    BarbarianOverview,
    BarbarianUnit,
    CityInfo,
    GameOverview,
    ThreatInfo,
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
    can_fortify: bool = True,
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
        can_fortify=can_fortify,
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
    threats: tuple[ThreatInfo, ...] = (),
    threat_scan_available: bool | None = None,
    overview_num_units: int | None = None,
) -> TypedTurnSnapshot:
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
    return TypedTurnSnapshot(
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
        threats=threats,
        threat_scan_available=(
            bool(threats)
            if threat_scan_available is None
            else threat_scan_available
        ),
    )


def _context(
    snapshot: TypedTurnSnapshot,
    *agenda: str,
    graph: GraphView | None = None,
    goals: tuple[StrategicGoal, ...] = (),
) -> DepartmentContext:
    department_snapshot = GraphSnapshotView(
        snapshot_id=snapshot.snapshot_id,
        turn=snapshot.turn,
        player_id=snapshot.player_id,
        ready=True,
        source="test",
        overview=snapshot.overview,
        cities=snapshot.cities,
        units=snapshot.units,
        diplomacy=snapshot.diplomacy,
        tech_civic=snapshot.tech_civic,
        resources=snapshot.resources,
        policies=snapshot.policies,
        barbarians=snapshot.barbarians,
        great_people=snapshot.great_people,
        threats=snapshot.threats,
        threat_scan_available=snapshot.threat_scan_available,
    )
    return DepartmentContext(
        snapshot=department_snapshot,
        agenda=agenda,
        goals=goals,
        graph=graph,
    )


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
    assert workstream.objective == "处理已知城市周边威胁，同时保留本土防御"
    assert "仅从满足本土防御余量的单位中筛选清剿编组" in workstream.candidate_actions
    assert workstream.resource_claims["home_defense_reserve"] == 1.0


def test_graph_threat_drives_military_assessment_and_stale_threat_does_not() -> None:
    department = MilitaryDepartment()
    threat = ThreatInfo(
        unit_type="UNIT_ARCHER",
        x=3,
        y=2,
        hp=80,
        max_hp=100,
        combat_strength=15,
        ranged_strength=25,
        distance=1,
        owner_id=3,
        owner_name="Persia",
        unit_id=70,
        nearest_city_id=1,
        distance_to_city=2,
        is_at_war=True,
        city_distances=((1, 2),),
    )
    first_snapshot = _snapshot(
        units=(_unit(1, "UNIT_WARRIOR", combat_strength=20),),
        cities=(_city(),),
        barbarians=BarbarianOverview(),
        threats=(threat,),
    )
    first_graph = GraphView.empty().apply(
        project_world_state(snapshot_world_state(first_snapshot))
    )
    first_graph = first_graph.apply(
        project_active_goals(
            ({"goal_id": "survive", "statement": "保住首都", "priority": 100},),
            previous=first_graph,
            snapshot_id=first_snapshot.snapshot_id,
            turn=first_snapshot.turn,
            epoch=first_graph.epoch,
        )
    )
    first_context = _context(first_snapshot, graph=first_graph)

    assessment = department.assess(first_context)

    assert assessment.relevance == 1.0
    assert "图查询确认城市三格内当前可见敌军: 1 个" in assessment.facts
    assert assessment.workstreams[0].workstream_id == "military:threat-response"
    assert len(assessment.proposals) == 1
    proposal = assessment.proposals[0]
    assert proposal.goal_ids == ("survive",)
    assert proposal.action_intents[0].arguments == {
        "action": "fortify",
        "unit_id": 1,
    }
    assert proposal.action_intents[0].allowed_turn == 12
    assert proposal.action_intents[0].evidence_requirements[0].tool == "get_units"
    assert proposal.action_intents[0].evidence_requirements[0].expected_facts == {
        "unit_position:1": (1, 2)
    }
    decision = GovernanceCouncil().decide(
        turn=12,
        proposals=assessment.proposals,
        budget_limits={"unit_action": 1},
    )
    assert decision.selected_proposal_ids == (proposal.proposal_id,)
    assert department.review(
        first_context, _outcome(OutcomeStatus.SUCCEEDED)
    ) is ReviewDisposition.CONTINUE

    next_snapshot = _snapshot(
        units=(_unit(1, "UNIT_WARRIOR", combat_strength=20),),
        cities=(_city(),),
        barbarians=BarbarianOverview(),
        threat_scan_available=True,
    )
    next_graph = first_graph.apply(
        project_world_state(
            snapshot_world_state(next_snapshot),
            previous=first_graph,
        )
    )
    next_context = _context(next_snapshot, graph=next_graph)

    assert department.assess(next_context).support_requests == ()
    assert department.review(
        next_context, _outcome(OutcomeStatus.SUCCEEDED)
    ) is ReviewDisposition.EXIT


def test_peaceful_foreign_unit_does_not_become_threat_or_proposal() -> None:
    snapshot = _snapshot(
        units=(_unit(1, "UNIT_WARRIOR", combat_strength=20),),
        cities=(_city(),),
        barbarians=BarbarianOverview(),
        threats=(
            ThreatInfo(
                unit_type="UNIT_ARCHER",
                x=3,
                y=2,
                hp=80,
                max_hp=100,
                combat_strength=15,
                ranged_strength=25,
                distance=1,
                owner_id=3,
                owner_name="Persia",
                unit_id=70,
                nearest_city_id=1,
                distance_to_city=2,
                is_at_war=False,
                city_distances=((1, 2),),
            ),
        ),
    )
    graph = GraphView.empty().apply(project_world_state(snapshot_world_state(snapshot)))
    goal = StrategicGoal(
        goal_id="survive",
        statement="保住首都",
        priority=100,
        success=ProbabilityConfidence(0.8, 0.9),
    )

    assessment = MilitaryDepartment().assess(
        _context(snapshot, graph=graph, goals=(goal,))
    )

    assert graph.threats_near_city("city:1:2") == ()
    assert assessment.proposals == ()
    assert assessment.support_requests == ()


def test_graph_context_does_not_fall_back_to_direct_goal_values() -> None:
    threat = ThreatInfo(
        unit_type="UNIT_ARCHER",
        x=3,
        y=2,
        hp=80,
        max_hp=100,
        combat_strength=15,
        ranged_strength=25,
        distance=1,
        owner_id=3,
        owner_name="Persia",
        unit_id=70,
        nearest_city_id=1,
        distance_to_city=2,
        is_at_war=True,
        city_distances=((1, 2),),
    )
    snapshot = _snapshot(
        units=(_unit(1, "UNIT_WARRIOR", combat_strength=20),),
        cities=(_city(),),
        barbarians=BarbarianOverview(),
        threats=(threat,),
    )
    graph_without_goals = GraphView.empty().apply(
        project_world_state(snapshot_world_state(snapshot))
    )
    direct_goal = StrategicGoal(
        goal_id="legacy-only",
        statement="守住首都",
        priority=100,
        success=ProbabilityConfidence(0.8, 0.9),
    )

    assessment = MilitaryDepartment().assess(
        _context(snapshot, graph=graph_without_goals, goals=(direct_goal,))
    )

    assert assessment.proposals == ()


def test_graph_context_ignores_legacy_agenda_and_stale_graph_goals() -> None:
    snapshot = _snapshot(barbarians=BarbarianOverview())
    graph = GraphView.empty(turn=snapshot.turn).apply(
        project_active_goals(
            ({"goal_id": "survive", "statement": "守住首都", "priority": 100},),
            previous=GraphView.empty(turn=snapshot.turn),
            snapshot_id="snapshot:stale",
            turn=snapshot.turn,
            epoch=1,
        )
    )

    context = _context(snapshot, "军事防御", graph=graph)

    assert MilitaryDepartment().match(context) == 0.0


def test_stale_graph_threats_degrade_without_affecting_current_assessment() -> None:
    threat = ThreatInfo(
        unit_type="UNIT_ARCHER",
        x=3,
        y=2,
        hp=80,
        max_hp=100,
        combat_strength=15,
        ranged_strength=25,
        distance=1,
        owner_id=3,
        owner_name="Persia",
        unit_id=70,
        nearest_city_id=1,
        distance_to_city=2,
        is_at_war=True,
        city_distances=((1, 2),),
    )
    stale_snapshot = _snapshot(
        units=(_unit(1, "UNIT_WARRIOR", combat_strength=20),),
        cities=(_city(),),
        barbarians=BarbarianOverview(),
        threats=(threat,),
    )
    stale_graph = GraphView.empty().apply(
        project_world_state(snapshot_world_state(stale_snapshot))
    )
    current_snapshot = replace(
        stale_snapshot,
        snapshot_id="snapshot:current",
        threats=(),
    )
    context = _context(current_snapshot, graph=stale_graph)

    assessment = MilitaryDepartment().assess(context)

    assert "图视图不是当前快照" in assessment.evidence_missing
    assert "图视图不是当前快照；不能据此判断当前城市威胁" in assessment.facts
    assert assessment.support_requests == ()
    assert assessment.proposals == ()
    assert MilitaryDepartment().review(
        context, _outcome(OutcomeStatus.SUCCEEDED)
    ) is ReviewDisposition.REPLAN


def test_defense_proposal_requires_a_relevant_graph_goal() -> None:
    threat = ThreatInfo(
        unit_type="UNIT_ARCHER",
        x=3,
        y=2,
        hp=80,
        max_hp=100,
        combat_strength=15,
        ranged_strength=25,
        distance=1,
        owner_id=3,
        owner_name="Persia",
        unit_id=70,
        nearest_city_id=1,
        distance_to_city=2,
        is_at_war=True,
        city_distances=((1, 2),),
    )
    snapshot = _snapshot(
        units=(_unit(1, "UNIT_WARRIOR", combat_strength=20),),
        cities=(_city(),),
        barbarians=BarbarianOverview(),
        threats=(threat,),
    )
    graph = GraphView.empty().apply(
        project_world_state(snapshot_world_state(snapshot))
    )
    graph = graph.apply(
        project_active_goals(
            (
                {
                    "goal_id": "research",
                    "statement": "完成当前科技",
                    "priority": 100,
                    "tags": ["science"],
                },
            ),
            previous=graph,
            snapshot_id=snapshot.snapshot_id,
            turn=snapshot.turn,
            epoch=graph.epoch,
        )
    )

    assessment = MilitaryDepartment().assess(_context(snapshot, graph=graph))

    assert assessment.proposals == ()


def test_unavailable_threat_scan_is_reported_as_missing_evidence() -> None:
    snapshot = _snapshot(
        units=(_unit(1, "UNIT_WARRIOR", combat_strength=20),),
        cities=(_city(),),
        barbarians=BarbarianOverview(),
        threat_scan_available=False,
    )
    graph = GraphView.empty().apply(project_world_state(snapshot_world_state(snapshot)))

    assessment = MilitaryDepartment().assess(_context(snapshot, graph=graph))

    assert "城市周边敌军扫描" in assessment.evidence_missing
    assert "不能据此认定城市安全" in assessment.facts[-1]


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


def test_military_department_does_not_import_the_mcp_adapter() -> None:
    path = (
        Path(__file__).parents[1]
        / "src"
        / "civ6_belief_engine"
        / "governance"
        / "departments"
        / "military.py"
    )
    tree = ast.parse(path.read_text(), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not any(name == "civ_mcp" or name.startswith("civ_mcp.") for name in imported)
