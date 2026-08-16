from __future__ import annotations

from civ6_belief_engine.belief_engine import BeliefEngine
from civ6_belief_engine.graph import GraphView, project_governance_state


def _lifecycle_entities() -> dict[str, tuple[dict, ...]]:
    return {
        "observation": (
            {
                "id": "observation:1",
                "status": "active",
                "statement": "敌军出现在首都附近",
            },
        ),
        "belief": (
            {
                "id": "belief:threat",
                "status": "active",
                "statement": "首都需要防御",
            },
        ),
        "goal": (
            {
                "id": "goal:survive",
                "status": "active",
                "goal_id": "survive",
                "statement": "守住首都",
                "priority": 100,
            },
        ),
        "proposal": (
            {
                "id": "proposal:defend",
                "status": "active",
                "proposal_id": "proposal:defend",
                "goal_ids": ("goal:survive",),
                "belief_ids": ("belief:threat",),
                "action_intents": (
                    {
                        "intent_id": "intent:fortify",
                        "proposal_id": "proposal:defend",
                        "tool": "unit_action",
                    },
                ),
            },
        ),
        "council_decision": (
            {
                "id": "council:1",
                "status": "resolved",
                "selected_proposal_ids": ("proposal:defend",),
                "considered_proposal_ids": ("proposal:defend",),
            },
        ),
        "decision": (
            {
                "id": "decision:1",
                "status": "active",
                "proposal_id": "proposal:defend",
                "council_decision_id": "council:1",
                "action_intent_id": "intent:fortify",
                "action_intent": {
                    "intent_id": "intent:fortify",
                    "proposal_id": "proposal:defend",
                    "tool": "unit_action",
                },
            },
        ),
        "action": (
            {
                "id": "action:1",
                "status": "active",
                "decision_id": "decision:1",
                "proposal_id": "proposal:defend",
                "action_intent_id": "intent:fortify",
            },
        ),
        "outcome": (
            {
                "id": "outcome:1",
                "status": "active",
                "decision_id": "decision:1",
                "action_id": "action:1",
                "proposal_id": "proposal:defend",
                "action_intent_id": "intent:fortify",
                "verification_observation_ids": ("observation:1",),
            },
        ),
    }


def test_governance_lifecycle_is_materialized_without_dangling_edges() -> None:
    delta = project_governance_state(
        _lifecycle_entities(),
        previous=GraphView.empty(turn=10),
        snapshot_id="snapshot:10",
        turn=10,
    )
    view = GraphView.empty(turn=10).apply(delta)

    assert view.node("goal:survive") is not None
    assert view.node("proposal:defend") is not None
    assert view.node("decision:1") is not None
    assert view.node("action:1") is not None
    assert view.node("outcome:1") is not None
    assert view.node("action_intent:intent:fortify") is not None
    assert view.edges_from("proposal:defend", "SERVES_GOAL")
    assert view.edges_from("decision:1", "REFERENCES_ACTION_INTENT")
    assert view.edges_from("outcome:1", "VERIFIED_BY")


def test_deleted_governance_entity_is_not_currently_observed() -> None:
    entities = _lifecycle_entities()
    entities["proposal"] = ({**entities["proposal"][0], "status": "deleted"},)
    delta = project_governance_state(
        entities,
        previous=GraphView.empty(turn=10),
        snapshot_id="snapshot:10",
        turn=10,
    )
    view = GraphView.empty(turn=10).apply(delta)

    assert view.node("proposal:defend") is not None
    assert view.node("proposal:defend").observed is False
    assert view.edges_from("proposal:defend") == ()


def test_belief_engine_governance_graph_replays_and_reads_current_state(tmp_path) -> None:
    engine = BeliefEngine(run_id="governance-graph-replay", directory=tmp_path)
    engine.bind_game("CIVILIZATION_INDIA", 123)
    payloads = {
        "observation": {
            "statement": "敌军出现在首都附近",
            "source": "mcp:get_barbarian_overview",
        },
        "belief": {
            "statement": "首都需要防御",
            "category": "military",
            "probability": 0.8,
            "confidence": 0.7,
            "unknown_basis": True,
        },
        "goal": {
            "statement": "守住首都",
            "priority": 100,
            "goal_id": "goal:survive",
        },
        "proposal": {
            "statement": "调派部队防守首都",
            "department": "military",
            "proposal_id": "proposal:defend",
            "goal_ids": ["goal:survive"],
            "action_intent": {"tool": "unit_action"},
            "action_intents": (
                {
                    "intent_id": "intent:fortify",
                    "proposal_id": "proposal:defend",
                    "tool": "unit_action",
                },
            ),
        },
        "council_decision": {
            "statement": "批准防守提案",
            "selected_proposal_id": "proposal:defend",
            "selected_proposal_ids": ["proposal:defend"],
        },
        "decision": {
            "statement": "执行防守意图",
            "route": "fast",
            "decision_state": "authorized",
            "action_intent": {
                "intent_id": "intent:fortify",
                "proposal_id": "proposal:defend",
                "tool": "unit_action",
            },
            "council_decision_id": "council:1",
        },
        "action": {
            "statement": "已执行防守行动",
            "tool": "unit_action",
            "decision_id": "decision:1",
            "action_intent_id": "intent:fortify",
        },
        "outcome": {
            "statement": "防守行动成功",
            "action_intent": {"tool": "unit_action"},
            "success": True,
            "decision_id": "decision:1",
            "action_id": "action:1",
            "action_intent_id": "intent:fortify",
            "verification_observation_ids": ["observation:1"],
        },
    }
    for entity_type, payload in payloads.items():
        entity_id = {
            "observation": "observation:1",
            "belief": "belief:threat",
            "goal": "goal:survive",
            "proposal": "proposal:defend",
            "council_decision": "council:1",
            "decision": "decision:1",
            "action": "action:1",
            "outcome": "outcome:1",
        }[entity_type]
        engine.create(entity_type, payload, turn=10, entity_id=entity_id)

    view = engine.sync_governance_graph(turn=10)
    assert engine.governance_graph_current() is True
    assert view.edges_from("proposal:defend", "SERVES_GOAL")
    assert view.edges_from("outcome:1", "VERIFIED_BY")
    assert engine.current_governance_entity("proposal", "proposal:defend")["status"] == "active"

    reloaded = BeliefEngine(run_id="governance-graph-replay-reloaded", directory=tmp_path)
    reloaded.bind_game("CIVILIZATION_INDIA", 123)
    assert reloaded.graph_replay_error is None
    assert reloaded.graph_view.state_hash == view.state_hash
    assert reloaded.current_governance_entities("decision", status="active")[0]["id"] == "decision:1"
