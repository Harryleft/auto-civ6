"""MCP-boundary tests for the governance contracts."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from civ_mcp.belief_engine import BeliefEngine, action_args_hash
from civ_mcp.server import (
    _belief_action_preflight,
    _governance_payload,
    _governance_proposal_from_dict,
    _release_stale_budget_locks,
    mcp,
    resolve_governance_council,
    route_belief_decision,
    submit_governance_proposal,
)


def _proposal_payload() -> dict:
    arguments = {
        "city_id": 4,
        "item_type": "BUILDING",
        "item_name": "BUILDING_WALLS",
    }
    return {
        "proposal_id": "production:east:walls",
        "department": "production",
        "summary": "Build walls",
        "goal_ids": ["goal:survival"],
        "success": {"probability": 0.8, "confidence": 0.7},
        "priority": 90,
        "hard_constraints": {"city_can_build_walls": True},
        "budget_locks": [
            {
                "resource": "city_production",
                "scope": "city:0:4",
                "exclusive": True,
            }
        ],
        "benefits": {"capital_survival": 0.8},
        "costs": {"expansion_delay_turns": 1},
        "opportunity_cost": 0.25,
        "action_intents": [
            {
                "intent_id": "intent:walls:4",
                "tool": "set_city_production",
                "arguments": arguments,
                "evidence_requirements": [
                    {
                        "requirement_id": "evidence:city:4",
                        "tool": "get_cities",
                        "params": {},
                        "required_metrics": ["observed_city_count"],
                        "max_age_turns": 0,
                    }
                ],
            }
        ],
    }


def test_proposal_parser_preserves_exact_action_and_evidence_contract():
    typed = _governance_proposal_from_dict(_proposal_payload())
    payload = _governance_payload(typed)

    intent = payload["action_intents"][0]
    assert intent["arguments_hash"] == action_args_hash(intent["arguments"])
    assert intent["evidence_requirements"][0]["tool"] == "get_cities"
    assert intent["evidence_requirements"][0]["required_metrics"] == [
        "observed_city_count"
    ]
    json.dumps(payload)


def test_proposal_parser_keeps_probability_and_confidence_strictly_separate():
    payload = _proposal_payload()
    payload["success"]["confidence"] = True

    with pytest.raises(TypeError, match="confidence must be a real number, not bool"):
        _governance_proposal_from_dict(payload)


def test_prior_turn_budget_locks_are_archived(tmp_path):
    engine = BeliefEngine("test-governance", tmp_path)
    engine.bind_game("CIVILIZATION_ROME", 123)
    lock = engine.create(
        "budget_lock",
        {
            "resource": "gold",
            "amount": 50,
            "proposal_id": "proposal:old",
        },
        turn=7,
        entity_id="lock:old",
    )

    released = _release_stale_budget_locks(engine, turn=8)

    assert released == [lock["id"]]
    archived = engine.get("budget_lock", lock["id"])
    assert archived["status"] == "archived"
    assert archived["released_turn"] == 8
    assert _release_stale_budget_locks(engine, turn=8) == []


def test_governance_mcp_tools_are_registered():
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}

    assert {
        "get_governance_brief",
        "upsert_strategic_goal",
        "submit_governance_proposal",
        "review_governance_proposal",
        "resolve_governance_council",
        "cancel_routed_action",
    }.issubset(names)


class _FakeEmitter:
    async def emit(self, _event_type, _payload):
        return None


class _FakeLogger:
    def __init__(self, turn: int):
        self._turn = turn
        self._emitter = _FakeEmitter()

    async def log_tool_call(self, *_args):
        return None

    async def log_error(self, *_args):
        return None


def _governed_context(tmp_path, *, turn: int = 42):
    engine = BeliefEngine("test-council-route", tmp_path)
    engine.bind_game("CIVILIZATION_ROME", 456)
    engine.create(
        "goal",
        {
            "statement": "Survive and defend the capital",
            "priority": 100,
            "probability": 0.8,
            "confidence": 0.8,
        },
        turn=turn,
        entity_id="goal:survival",
    )
    engine.create(
        "observation",
        {
            "statement": "Typed snapshot for the current turn",
            "source": "game_state:typed_snapshot",
            "facts": {
                "snapshot_id": "snapshot:test:42",
                "capabilities": {
                    "ruleset": "RULESET_STANDARD",
                    "governors": False,
                    "dedications": False,
                    "alliances": False,
                    "diplomatic_favor": False,
                    "world_congress": False,
                },
            },
            "metrics": {
                "player.gold": 100,
                "player.faith": 20,
                "player.cities": 1,
                "player.units": 2,
            },
            "observed_turn": turn,
        },
        turn=turn,
        entity_id="observation:snapshot:42",
    )
    raw = _proposal_payload()
    raw["action_intents"][0]["allowed_turn"] = turn
    typed = _governance_proposal_from_dict(raw)
    payload = _governance_payload(typed)
    payload.update(
        {
            "status": "resolved",
            "statement": typed.summary,
            "action_intent": payload["action_intents"][0],
            "council_state": "approved",
            "council_decision_id": "council:42:test",
        }
    )
    engine.create(
        "proposal",
        payload,
        turn=turn,
        entity_id=typed.proposal_id,
    )
    engine.create(
        "council_decision",
        {
            "status": "resolved",
            "council_state": "approved",
            "statement": "Council approved one proposal",
            "selected_proposal_id": typed.proposal_id,
            "selected_proposal_ids": [typed.proposal_id],
        },
        turn=turn,
        entity_id="council:42:test",
    )
    lifespan = SimpleNamespace(
        beliefs=engine,
        logger=_FakeLogger(turn),
        game=SimpleNamespace(),
    )
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=lifespan)
    )
    return ctx, engine, payload["action_intents"][0]


def test_council_action_intent_routes_verbatim_and_keeps_approved_evidence(tmp_path):
    ctx, engine, approved_intent = _governed_context(tmp_path)

    result = asyncio.run(
        route_belief_decision(
            ctx,
            statement="Build the approved walls",
            probability=0.8,
            confidence=0.7,
            impact="high",
            urgency="high",
            irreversibility=0.5,
            action_intent=json.dumps(approved_intent),
            council_decision_id="council:42:test",
            gate_scope="city:0:4",
        )
    )

    routed = json.loads(result)
    assert routed["council_decision_id"] == "council:42:test"
    assert routed["action_intent"]["params"] == approved_intent["arguments"]
    assert routed["evidence_requirements"][0]["required_metrics"] == [
        "observed_city_count"
    ]
    assert engine.get("decision", routed["id"])["decision_state"] == "authorized"


def test_council_route_rejects_weakened_evidence_contract(tmp_path):
    ctx, _engine, approved_intent = _governed_context(tmp_path)

    result = asyncio.run(
        route_belief_decision(
            ctx,
            statement="Try to bypass the approved evidence",
            probability=0.8,
            confidence=0.7,
            impact="high",
            urgency="high",
            irreversibility=0.5,
            action_intent=json.dumps(approved_intent),
            evidence_requirements=json.dumps(
                [{"tool": "get_game_overview", "params": {}}]
            ),
            council_decision_id="council:42:test",
        )
    )

    assert result.startswith("Error: evidence_requirements do not match")


def test_resubmitting_a_resolved_proposal_reactivates_it(tmp_path):
    ctx, engine, _approved_intent = _governed_context(tmp_path)
    previous = engine.get("proposal", "production:east:walls")

    result = asyncio.run(
        submit_governance_proposal(ctx, json.dumps(_proposal_payload()))
    )

    updated = json.loads(result)
    assert updated["status"] == "active"
    assert updated["council_state"] == "proposed"
    assert updated["council_decision_id"] is None
    assert updated["version"] == previous["version"] + 1


def test_council_clamps_declared_budget_to_typed_live_capacity(tmp_path):
    ctx, engine, _approved_intent = _governed_context(tmp_path)
    asyncio.run(submit_governance_proposal(ctx, json.dumps(_proposal_payload())))

    result = asyncio.run(
        resolve_governance_council(ctx, budget_limits=json.dumps({"gold": 999}))
    )

    decision = json.loads(result)
    assert decision["status"] == "resolved"
    assert decision["council_state"] == "approved"
    assert decision["effective_budget_limits"]["gold"] == 100
    assert decision["typed_snapshot_id"] == "snapshot:test:42"
    proposal = engine.get("proposal", "production:east:walls")
    assert proposal["status"] == "resolved"
    assert proposal["council_state"] == "approved"


def test_standard_ruleset_rejects_expansion_only_action_intent(tmp_path):
    ctx, _engine, _approved_intent = _governed_context(tmp_path)
    proposal = _proposal_payload()
    proposal["proposal_id"] = "diplomacy:alliance"
    proposal["action_intents"] = [
        {
            "intent_id": "intent:alliance:3",
            "tool": "form_alliance",
            "arguments": {"other_player_id": 3, "alliance_type": "MILITARY"},
        }
    ]

    result = asyncio.run(submit_governance_proposal(ctx, json.dumps(proposal)))

    assert result.startswith("Error: Action form_alliance requires unavailable")


def _bare_loop_context(tmp_path, *, turn: int = 42):
    engine = BeliefEngine("test-loop-gate", tmp_path)
    engine.bind_game("CIVILIZATION_ROME", 789)
    lifespan = SimpleNamespace(
        beliefs=engine,
        logger=_FakeLogger(turn),
        game=SimpleNamespace(),
    )
    return (
        SimpleNamespace(
            request_context=SimpleNamespace(lifespan_context=lifespan)
        ),
        engine,
    )


def test_end_turn_preflight_requires_current_typed_governance_snapshot(tmp_path):
    ctx, _engine = _bare_loop_context(tmp_path)

    gate = asyncio.run(_belief_action_preflight(ctx, "end_turn", {}))

    assert gate["authorized"] is False
    assert gate["governance_gate"]["blockers"] == [
        "current_turn_typed_snapshot_missing"
    ]


def test_legacy_selected_action_cannot_authorize_execution(tmp_path):
    ctx, engine = _bare_loop_context(tmp_path)
    engine.ingest_typed_snapshot(
        {
            "snapshot_id": "snapshot:42",
            "turn_before": 42,
            "turn_after": 42,
            "capabilities": {"ruleset": "RULESET_STANDARD"},
            "entities": [],
            "relations": [],
            "metrics": {},
        },
        turn=42,
    )

    result = asyncio.run(
        route_belief_decision(
            ctx,
            statement="Legacy fuzzy research authorization",
            probability=0.8,
            confidence=0.8,
            impact="low",
            urgency="low",
            irreversibility=0.1,
            selected_action="set_research",
        )
    )

    assert result.startswith("Error: selected_action is audit-only")
