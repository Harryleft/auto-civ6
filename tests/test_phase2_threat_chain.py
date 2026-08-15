"""Offline end-to-end logical acceptance of the phase-two threat slice.

Real-game acceptance needs an actual enemy next to a city, which an early
peaceful game may never produce. This drives the identical chain with
production-format inputs instead:

    Lua threat/units text -> parsers -> typed snapshot -> world projection
    -> shadow graph -> threats_near_city -> Military defense proposal
    -> council selection -> proposal JSON round-trip -> routed ActionIntent
    -> evidence verification (real narrated get_units) -> fortify outcome.

Every step uses the real production function; only the game itself is
stubbed by the Lua text fixtures.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from civ6_belief_engine.belief_engine import BeliefEngine, action_args_hash
from civ6_belief_engine.governance.council import GovernanceCouncil
from civ6_belief_engine.governance.departments.base import DepartmentContext
from civ6_belief_engine.governance.departments.military import MilitaryDepartment
from civ6_belief_engine.governance.models import ProbabilityConfidence, StrategicGoal
from civ6_belief_engine.governance.snapshot import (
    build_turn_snapshot,
    snapshot_world_state,
)
from civ6_belief_engine.graph import city_node_id, project_world_state, project_active_goals
from civ6_belief_engine.graph.view import GraphView
from civ_mcp import narrate as nr
from civ_mcp.lua import models as lq
from civ_mcp.lua.units import parse_threat_scan_response, parse_units_response

TURN = 40
GAME = ("CIVILIZATION_TEST", 7)


def _overview() -> lq.GameOverview:
    return lq.GameOverview(
        turn=TURN,
        player_id=0,
        civ_name="CIVILIZATION_FRANCE",
        leader_name="Eleanor",
        gold=80.0,
        gold_per_turn=4.0,
        science_yield=9.0,
        culture_yield=6.0,
        faith=12.0,
        current_research="TECH_POTTERY",
        current_civic="CIVIC_CODE_OF_LAWS",
        num_cities=1,
        num_units=1,
        score=40,
        explored_land=20,
        total_land=100,
        total_population=3,
        difficulty="DEITY",
        game_speed="GAMESPEED_STANDARD",
        game_speed_name="Standard",
        enabled_victories={"VICTORY_SCIENCE", "VICTORY_DOMINATION"},
        ruleset="RULESET_EXPANSION_2",
    )


def _city() -> lq.CityInfo:
    return lq.CityInfo(
        city_id=1,
        name="Paris",
        x=10,
        y=24,
        population=3,
        food=8.0,
        production=6.0,
        gold=2.0,
        science=3.0,
        culture=2.0,
        faith=1.0,
        housing=6.0,
        amenities=1,
        turns_to_grow=6,
        currently_building="BUILDING_MONUMENT",
        districts=["DISTRICT_CITY_CENTER"],
        buildings=["PALACE"],
    )


def test_phase2_threat_chain_from_lua_text_to_verified_outcome(tmp_path):
    # --- 1. Production parsers consume realistic Lua text -------------------
    units = parse_units_response(
        ["0|0|Warrior|UNIT_WARRIOR|10,24|2.0/2.0|100/100|20|0|0||0|0|||||0|1"]
    )
    assert len(units) == 1 and units[0].unit_id == 0
    assert units[0].fortify_turns == 0 and units[0].can_fortify is True

    threats = parse_threat_scan_response(
        [
            "THREAT|2|Rome|UNIT_WARRIOR|10,27|100/100|CS:20|RS:0|dist:3|cs:0"
            "|uid:42|city:1|citydist:3|war:1|cities:1=3"
        ]
    )
    assert len(threats) == 1 and threats[0].is_at_war is True

    # --- 2. Typed snapshot -> world projection -> shadow graph --------------
    snapshot = build_turn_snapshot(
        turn_before=TURN,
        turn_after=TURN,
        captured_at=1234.5,
        overview=_overview(),
        cities=[_city()],
        units=units,
        barbarians=lq.BarbarianOverview(camps=[], units=[]),
        threats=threats,
    )
    assert snapshot.threat_scan_available is True

    world = snapshot_world_state(snapshot)
    delta = project_world_state(world)
    view = GraphView.empty().apply(delta)

    threat_edges = view.threats_near_city(city_node_id(10, 24), max_distance=3)
    assert len(threat_edges) == 1
    assert threat_edges[0].attributes["distance"] == 3

    # --- 3. Goal projection feeds the military department -------------------
    goal_payload = {
        "goal_id": "goal:defend-homeland",
        "statement": "守住边境城市的军事安全",
        "priority": 60,
        "tags": ["military", "defense"],
    }
    goal_delta = project_active_goals(
        [goal_payload],
        previous=view,
        snapshot_id=world["snapshot_id"],
        turn=TURN,
        epoch=1,
    )
    view = view.apply(goal_delta)

    typed_goal = StrategicGoal(
        goal_id="goal:defend-homeland",
        statement="守住边境城市的军事安全",
        priority=60,
        success=ProbabilityConfidence(0.8, 0.7),
        tags=("military", "defense"),
    )
    assessment = MilitaryDepartment().assess(
        DepartmentContext(snapshot=snapshot, goals=(typed_goal,), graph=view)
    )

    assert assessment.proposals, assessment.summary
    proposal = assessment.proposals[0]
    assert proposal.action_intents[0].tool == "unit_action"
    assert proposal.action_intents[0].arguments == {"unit_id": 0, "action": "fortify"}

    # --- 4. Council selects the proposal ------------------------------------
    decision = GovernanceCouncil().decide(
        turn=TURN,
        proposals=[proposal],
        budget_limits={"unit_action": 2},
    )
    assert proposal.proposal_id in decision.selected_proposal_ids

    # --- 5. Proposal JSON round-trip preserves the intent contract ----------
    # Production seam: the department's typed proposals are surfaced in the
    # governance brief; the AGENT then submits proposal JSON (there is no
    # typed->asdict serializer on purpose). Mirror that submission here.
    from civ_mcp.server.tools import belief as belief_tools

    def _submission_json(typed) -> dict:
        return {
            "proposal_id": typed.proposal_id,
            "department": typed.department,
            "summary": typed.summary,
            "goal_ids": list(typed.goal_ids),
            "success": {"probability": typed.success.probability,
                        "confidence": typed.success.confidence},
            "priority": typed.priority,
            "hard_constraints": dict(typed.hard_constraints),
            "budget_locks": [
                {
                    "resource": lock.resource,
                    "amount": lock.amount,
                    "scope": lock.scope,
                    "exclusive": lock.exclusive,
                    "reason": lock.reason,
                }
                for lock in typed.budget_locks
            ],
            "benefits": dict(typed.benefits),
            "costs": dict(typed.costs),
            "opportunity_cost": typed.opportunity_cost,
            "failure_cost": typed.failure_cost,
            "expires_turn": typed.expires_turn,
            "action_intents": [
                {
                    "intent_id": item.intent_id,
                    "tool": item.tool,
                    "params": dict(item.arguments),
                    "proposal_id": item.proposal_id,
                    "arguments_hash": item.arguments_hash,
                    "allowed_turn": item.allowed_turn,
                    "evidence_requirements": [
                        {
                            "requirement_id": req.requirement_id,
                            "tool": req.tool,
                            "params": dict(req.params),
                            "max_age_turns": req.max_age_turns,
                            "required_facts": list(req.required_facts),
                            "required_metrics": list(req.required_metrics),
                            "expected_facts": dict(req.expected_facts),
                            "description": req.description,
                        }
                        for req in item.evidence_requirements
                    ],
                }
                for item in typed.action_intents
            ],
        }

    raw = json.loads(json.dumps(_submission_json(proposal)))
    restored = belief_tools._governance_proposal_from_dict(raw)
    intent = restored.action_intents[0]
    assert intent.arguments_hash == proposal.action_intents[0].arguments_hash

    # --- 6. Route -> evidence -> authorize -> execute -> verified outcome ---
    engine = BeliefEngine(run_id="phase2-acceptance", directory=tmp_path)
    engine.bind_game(*GAME)

    evidence = {
        "tool": "get_units",
        "params": {},
        "max_age_turns": 0,
        "required_facts": ["unit_position:0"],
        "required_metrics": ["observed_unit_count"],
        "expected_facts": {"unit_position:0": [10, 24]},
    }
    routed = engine.route_decision(
        statement="让守军原地设防",
        probability=0.9,
        confidence=0.8,
        impact="medium",
        urgency="high",
        irreversibility=0,
        action_intent={
            "tool": "unit_action",
            "params": dict(intent.arguments),
            "args_hash": intent.arguments_hash,
            "proposal_id": restored.proposal_id,
            "intent_id": intent.intent_id,
        },
        evidence_requirements=[evidence],
        council_decision_id=decision.decision_id,
        turn=TURN,
    )
    assert routed["route"] == "verify_then_fast"

    # Evidence step: the agent re-runs get_units; the automatic normalizer
    # must extract the defender position from the REAL narration format.
    narration = nr.narrate_units(units)
    assert "at (10,24)" in narration and "[id:0" in narration
    engine.record_tool_result(
        tool="get_units",
        params={},
        result=narration,
        turn=TURN,
        category="query",
        success=True,
        duration_ms=30,
    )

    authorized = engine.authorize_action(
        tool="unit_action",
        params={"unit_id": 0, "action": "fortify"},
        turn=TURN,
        required=True,
    )
    assert authorized["authorized"] is True, authorized

    engine.record_tool_result(
        tool="unit_action",
        params={"unit_id": 0, "action": "fortify"},
        result="OK:FORTIFIED|readback_fortify_turns:1|readback_moves:1",
        turn=TURN,
        category="action",
        success=True,
        duration_ms=45,
        decision_id=routed["id"],
        decision_route="fast",
    )

    final = engine.get("decision", routed["id"])
    assert final["decision_state"] == "succeeded"
    outcomes = [
        e
        for e in engine.list("outcome", status="active")
        if e.get("decision_id") == routed["id"]
    ]
    assert outcomes and outcomes[0]["success"] is True
