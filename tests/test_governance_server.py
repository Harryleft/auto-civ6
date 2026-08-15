"""MCP-boundary tests for the governance contracts."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from civ6_belief_engine.belief_engine import (
    BeliefEngine,
    BeliefEngineError,
    action_args_hash,
    tool_result_reference,
)
from civ_mcp.server import (
    _belief_action_preflight,
    _canonical_action_params,
    _governance_goal_from_dict,
    _governance_payload,
    _national_strategy_payload,
    _normalize_impact_urgency,
    _normalize_trade_mode,
    _governance_proposal_from_dict,
    _release_stale_budget_locks,
    _reusable_typed_snapshot_for_turn,
    _typed_snapshot_observation_for_turn,
    mcp,
    propose_trade,
    record_action_verification,
    resolve_governance_council,
    route_belief_decision,
    submit_governance_proposal,
)
from civ6_belief_engine.governance import RulesetCapabilities, TurnSnapshot
from civ6_belief_engine.governance.departments import (
    Department,
    NationalStrategyCoordinator,
    default_department_registry,
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


def test_proposal_parser_coerces_llm_boolean_and_string_scores():
    payload = _proposal_payload()
    payload["benefits"] = {"city_defense": True, "unit_synergy": "0.6"}
    payload["costs"] = {"expansion_delay_turns": False}
    payload["opportunity_cost"] = True
    payload["priority"] = 90.0

    proposal = _governance_proposal_from_dict(payload)

    assert proposal.benefits == {"city_defense": 1.0, "unit_synergy": 0.6}
    assert proposal.costs == {"expansion_delay_turns": 0.0}
    assert proposal.opportunity_cost == 1.0
    assert proposal.priority == 90
    assert type(proposal.priority) is int


def test_impact_urgency_normalizer_coerces_llm_enum_drift():
    assert _normalize_impact_urgency("now", "urgency") == "critical"
    assert _normalize_impact_urgency("紧急", "urgency") == "critical"
    assert _normalize_impact_urgency("HIGH ", "impact") == "high"
    assert _normalize_impact_urgency("中", "impact") == "medium"
    assert _normalize_impact_urgency("Critical", "impact") == "critical"

    with pytest.raises(
        BeliefEngineError,
        match=r"impact must be low, medium, high, or critical; got 'banana'",
    ):
        _normalize_impact_urgency("banana", "impact")

    with pytest.raises(
        BeliefEngineError, match=r"urgency must be low, medium, high, or critical; got 3"
    ):
        _normalize_impact_urgency(3, "urgency")


def test_route_decision_accepts_normalized_impact_urgency_drift(tmp_path):
    ctx, _engine = _bare_loop_context(tmp_path)
    _engine.ingest_typed_snapshot(
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
            statement="Clear the barbarian camp near the capital",
            probability=0.9,
            confidence=0.8,
            impact="紧急",
            urgency="now",
            irreversibility=0.2,
        )
    )

    routed = json.loads(result)
    assert routed["impact"] == "critical"
    assert routed["urgency"] == "critical"


def test_goal_parser_restores_nested_and_legacy_probability_contracts():
    nested = _governance_goal_from_dict(
        {
            "goal_id": "survive",
            "statement": "守住首都",
            "priority": 100,
            "success": {"probability": 0.8, "confidence": 0.9},
            "tags": ["military"],
        }
    )
    legacy = _governance_goal_from_dict(
        {
            "id": "goal:legacy",
            "statement": "保留旧记录兼容",
            "priority": "50",
        }
    )

    assert nested.goal_id == "survive"
    assert nested.success.probability == 0.8
    assert nested.success.confidence == 0.9
    assert legacy.goal_id == "goal:legacy"
    assert legacy.priority == 50
    assert legacy.success.probability == 0.5
    assert legacy.success.confidence == 0.0


def test_action_arguments_must_be_a_json_object():
    with pytest.raises(ValueError, match="must be a JSON object"):
        _canonical_action_params("set_research", [])

    for field in ("arguments", "params"):
        payload = _proposal_payload()
        payload["action_intents"][0].pop("arguments")
        payload["action_intents"][0][field] = []
        with pytest.raises(ValueError, match="must be a JSON object"):
            _governance_proposal_from_dict(payload)


def test_trade_mode_is_case_normalized_and_rejects_unknown_values():
    assert _normalize_trade_mode("TEST") == "test"
    assert _normalize_trade_mode(" Send ") == "send"
    with pytest.raises(ValueError, match="must be test or send"):
        _normalize_trade_mode("preview")


def test_uppercase_trade_test_mode_never_sends_a_deal(monkeypatch):
    calls: list[str] = []

    class _Game:
        async def test_trade(self, *_args):
            calls.append("test")
            return "TESTED"

        async def propose_trade(self, *_args):
            calls.append("send")
            return "SENT"

    async def direct_logged(_ctx, _tool, _params, fn, **_kwargs):
        return await fn()

    monkeypatch.setattr("civ_mcp.server.pipeline._logged", direct_logged)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(game=_Game())
        )
    )

    result = asyncio.run(
        propose_trade(ctx, other_player_id=2, offer_gold=1, mode="TEST")
    )

    assert result == "TESTED"
    assert calls == ["test"]


@pytest.mark.parametrize("field", ["params", "arguments"])
def test_route_rejects_non_object_action_params_without_crashing(tmp_path, field):
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
            statement="Malformed route",
            probability=0.9,
            confidence=0.9,
            impact="low",
            urgency="low",
            irreversibility=0.1,
            action_intent=json.dumps({"tool": "unit_action", field: []}),
        )
    )

    assert result.startswith("【中文运行信息】")
    assert "错误： action arguments/params must be a JSON object" in result


def test_route_accepts_native_json_values_from_mcp_tool_calls(tmp_path):
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
            statement="Record a routine scouting decision",
            probability=0.8,
            confidence=0.7,
            impact="low",
            urgency="low",
            irreversibility=0.1,
            belief_ids=[],
            considered_actions=["move scout"],
            action_intent={},
            evidence_requirements=[],
        )
    )

    routed = json.loads(result)
    assert routed["belief_ids"] == []
    assert routed["considered_actions"] == ["move scout"]


def test_current_turn_snapshot_lookup_reuses_latest_capture(tmp_path):
    engine = BeliefEngine("snapshot-reuse", tmp_path)
    engine.bind_game("CIVILIZATION_ROME", 123)
    for snapshot_id in ("snapshot:old", "snapshot:new"):
        engine.create(
            "observation",
            {
                "statement": snapshot_id,
                "source": "game_state:typed_snapshot",
                "facts": {
                    "snapshot_id": snapshot_id,
                    "capabilities": {"ruleset": "RULESET_STANDARD"},
                },
                "observed_turn": 42,
            },
            turn=42,
        )

    cached = _typed_snapshot_observation_for_turn(engine, turn=42)

    assert cached is not None
    assert cached["facts"]["snapshot_id"] == "snapshot:new"
    assert _reusable_typed_snapshot_for_turn(engine, turn=42) == cached

    engine.create(
        "action",
        {
            "statement": "A real game mutation happened after the snapshot",
            "tool": "unit_action",
            "params": {"unit_id": 7, "action": "move"},
            "success": True,
            "executed": True,
            "selected_turn": 42,
        },
        turn=42,
    )

    assert _reusable_typed_snapshot_for_turn(engine, turn=42) is None


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


def test_scheduled_action_lock_survives_through_its_allowed_turn(tmp_path):
    engine = BeliefEngine("test-future-lock", tmp_path)
    engine.bind_game("CIVILIZATION_ROME", 123)
    engine.create(
        "budget_lock",
        {
            "resource": "gold",
            "amount": 50,
            "proposal_id": "proposal:future",
            "release_after_turn": 9,
        },
        turn=7,
        entity_id="lock:future",
    )

    assert _release_stale_budget_locks(engine, turn=8) == []
    assert _release_stale_budget_locks(engine, turn=9) == []
    assert _release_stale_budget_locks(engine, turn=10) == ["lock:future"]


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
        "get_climate_overview",
    }.issubset(names)


def test_national_strategy_payload_runs_and_serializes_all_six_departments():
    snapshot = TurnSnapshot(
        snapshot_id="snapshot:modules",
        turn=12,
        turn_before=12,
        turn_after=12,
        player_id=0,
        captured_at=1.0,
        capabilities=RulesetCapabilities.standard(),
    )

    brief = NationalStrategyCoordinator(default_department_registry()).run(snapshot)
    payload = _national_strategy_payload(brief)

    assert [item["department"] for item in payload["departments"]] == [
        department.value for department in Department
    ]
    assert len(payload["departments"]) == 6
    assert "details" not in payload
    assert json.loads(json.dumps(payload))["campaign"]["campaign_id"].startswith(
        "campaign:12:"
    )

    detailed = _national_strategy_payload(brief, include_details=True)
    assert len(detailed["details"]["assessments"]) == 6


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
    assert routed["route"] == "verify_then_fast"
    assert routed["action_intent"]["params"] == approved_intent["arguments"]
    assert routed["evidence_requirements"][0]["required_metrics"] == [
        "observed_city_count"
    ]
    assert engine.get("decision", routed["id"])["decision_state"] == "authorized"

    authorization = engine.authorize_action(
        tool=approved_intent["tool"],
        params=approved_intent["arguments"],
        turn=42,
        required=True,
    )
    assert authorization["authorized"] is False
    assert authorization["route"] == "verify_then_fast"
    assert authorization["missing_evidence"][0]["tool"] == "get_cities"


def test_council_route_without_evidence_contract_is_immediately_executable(tmp_path):
    ctx, engine, approved_intent = _governed_context(tmp_path)
    proposal = engine.get("proposal", "production:east:walls")
    proposal["action_intents"][0]["evidence_requirements"] = []
    engine.update(
        "proposal",
        proposal["id"],
        {"action_intents": proposal["action_intents"]},
        turn=42,
    )

    result = asyncio.run(
        route_belief_decision(
            ctx,
            statement="Build the council-approved walls without an evidence contract",
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
    assert routed["route"] == "fast"
    # A restarted agent may read an older persisted record whose route was
    # computed before council-aware classification existed.
    engine.update("decision", routed["id"], {"route": "slow"}, turn=42)
    authorization = engine.authorize_action(
        tool=approved_intent["tool"],
        params=approved_intent["arguments"],
        turn=42,
        required=True,
    )
    assert authorization["authorized"] is True


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

    assert result.startswith("【中文运行信息】")
    assert "错误： evidence_requirements do not match" in result


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


def test_council_extends_budget_lock_through_scheduled_action_turn(tmp_path):
    ctx, engine, _approved_intent = _governed_context(tmp_path)
    proposal = _proposal_payload()
    proposal["action_intents"][0]["allowed_turn"] = 44
    asyncio.run(submit_governance_proposal(ctx, json.dumps(proposal)))

    result = asyncio.run(resolve_governance_council(ctx, budget_limits="{}"))

    assert json.loads(result)["council_state"] == "approved"
    lock = engine.list("budget_lock", status="active")[0]
    assert lock["release_after_turn"] == 44


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

    assert result.startswith("【中文运行信息】")
    assert "错误： Action form_alliance requires unavailable" in result


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

    assert result.startswith("【中文运行信息】")
    assert "错误： selected_action is audit-only" in result


def test_national_action_cannot_route_without_council(tmp_path):
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
            statement="Research Writing without national arbitration",
            probability=0.8,
            confidence=0.8,
            impact="low",
            urgency="low",
            irreversibility=0.1,
            action_intent=json.dumps(
                {
                    "tool": "set_research",
                    "params": {"tech_or_civic": "TECH_WRITING"},
                }
            ),
        )
    )

    assert result.startswith("【中文运行信息】")
    assert "错误： This national, scarce-resource" in result
    assert engine.list("decision", status=None) == []


def test_action_verification_recovers_interrupted_executing_decision(tmp_path):
    ctx, engine = _bare_loop_context(tmp_path)
    params = {"unit_id": 7, "action": "attack", "target_x": 8, "target_y": 9}
    decision = engine.route_decision(
        statement="Attack the quantified target",
        probability=0.99,
        confidence=0.99,
        impact="low",
        urgency="low",
        irreversibility=0.1,
        action_intent={
            "tool": "unit_action",
            "params": params,
            "args_hash": action_args_hash(params),
        },
        turn=42,
    )
    authorization = engine.authorize_action(
        tool="unit_action", params=params, turn=42, required=True
    )
    assert authorization["authorized"] is True
    assert engine.get("decision", decision["id"])["decision_state"] == "executing"

    recovered = asyncio.run(
        record_action_verification(
            ctx,
            decision_id=decision["id"],
            tool="unit_action",
            expected="target defeated",
            actual="OK:TARGET_DEFEATED",
            success=True,
        )
    )

    recovered_action = json.loads(recovered)
    assert recovered_action["verification"]["source"] == "agent_recovery"
    assert "actual" not in recovered_action
    assert recovered_action["actual_ref"] == tool_result_reference(
        "OK:TARGET_DEFEATED"
    )
    assert engine.get("decision", decision["id"])["decision_state"] == "succeeded"
    outcomes = engine.list("outcome", status="active")
    assert outcomes[-1]["decision_id"] == decision["id"]
