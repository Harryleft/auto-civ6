"""Belief Engine and governance tools plus their adapter helpers.

This module is the graph_plan phase-4 deletion unit: once the graph path
replaces the legacy projection, it is removed wholesale.
"""

import json
import logging
import re
import time
from enum import Enum
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any

from mcp.server.fastmcp import Context

from civ6_belief_engine.belief_engine import (
    BeliefEngine,
    BeliefEngineError,
    action_args_hash,
    tool_result_reference,
)
from civ6_belief_engine.graph import (
    compare_shadow_projection,
    project_active_goals,
    project_world_state,
)
from civ_mcp.server import pipeline

log = logging.getLogger(__name__)
from civ_mcp.server.assembly import mcp

# ---------------------------------------------------------------------------
# Belief Engine
# ---------------------------------------------------------------------------


def _belief_json_object(raw: str | dict[str, Any], label: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise BeliefEngineError(f"{label} must be a JSON object")
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise BeliefEngineError(f"{label} must be valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise BeliefEngineError(f"{label} must be a JSON object")
    return value


def _belief_json_list(raw: str | list[Any], label: str) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str):
        raise BeliefEngineError(f"{label} must be a JSON array")
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise BeliefEngineError(f"{label} must be valid JSON: {exc.msg}") from exc
    if not isinstance(value, list):
        raise BeliefEngineError(f"{label} must be a JSON array")
    return value


def _governance_payload(value: Any) -> Any:
    """Serialize frozen governance contracts without losing typed boundaries."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _governance_payload(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _governance_payload(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_governance_payload(item) for item in value]
    return value


def _national_strategy_payload(value: Any, *, include_details: bool = False) -> dict[str, Any]:
    """Keep the six-module result visible before large governance history spills."""

    campaign = value.campaign
    payload: dict[str, Any] = {
        "snapshot_id": value.snapshot_id,
        "turn": value.turn,
        "departments": [
            {
                "department": assessment.department.value,
                "relevance": assessment.relevance,
                "degraded": assessment.degraded,
                "summary": assessment.summary,
                "support_request_count": len(assessment.support_requests),
                "workstream_ids": [
                    workstream.workstream_id for workstream in assessment.workstreams
                ],
                "proposal_ids": [
                    proposal.proposal_id for proposal in assessment.proposals
                ],
                "evidence_missing": list(assessment.evidence_missing),
            }
            for assessment in value.assessments
        ],
        "campaign": {
            "campaign_id": campaign.campaign_id,
            "objective": campaign.objective,
            "trigger": campaign.trigger,
            "ready_workstream_ids": list(campaign.ready_workstream_ids),
            "blocked_workstream_ids": list(campaign.blocked_workstream_ids),
            "coordination_link_count": len(campaign.coordination_links),
            "unresolved_support_request_count": len(
                campaign.unresolved_support_requests
            ),
            "blind_spots": list(campaign.blind_spots),
        },
    }
    if include_details:
        payload["details"] = _governance_payload(value)
    return payload


def _governance_proposal_from_dict(raw: dict[str, Any]):
    """Validate one ministerial proposal against the shared governance schema."""

    from civ6_belief_engine.governance import (
        ActionIntent,
        BudgetLock,
        EvidenceRequirement,
        ProbabilityConfidence,
        Proposal,
    )

    proposal_id = str(raw.get("proposal_id") or "")

    def evidence(item: dict[str, Any]) -> EvidenceRequirement:
        return EvidenceRequirement(
            requirement_id=str(item.get("requirement_id") or ""),
            tool=str(item.get("tool") or ""),
            target_entity_id=item.get("target_entity_id"),
            params=item.get("params") or {},
            min_observation_sequence=item.get("min_observation_sequence", 0),
            max_age_turns=item.get("max_age_turns"),
            required_facts=tuple(item.get("required_facts") or ()),
            required_metrics=tuple(item.get("required_metrics") or ()),
            expected_facts=item.get("expected_facts") or {},
            description=str(item.get("description") or ""),
        )

    intents = []
    for item in raw.get("action_intents") or []:
        if not isinstance(item, dict):
            raise BeliefEngineError("action_intents must contain JSON objects")
        requirements = item.get("evidence_requirements") or []
        if not all(isinstance(requirement, dict) for requirement in requirements):
            raise BeliefEngineError(
                "action intent evidence_requirements must contain JSON objects"
            )
        intent_tool = str(item.get("tool") or "")
        supplied_arguments = item.get("arguments")
        supplied_params = item.get("params")
        if (
            supplied_arguments is not None
            and supplied_params is not None
            and supplied_arguments != supplied_params
        ):
            raise BeliefEngineError(
                "action intent arguments and params must match when both are present"
            )
        intent_arguments = pipeline._canonical_action_params(
            intent_tool,
            supplied_arguments
            if supplied_arguments is not None
            else supplied_params
            if supplied_params is not None
            else {},
        )
        intents.append(
            ActionIntent(
                intent_id=str(item.get("intent_id") or ""),
                tool=intent_tool,
                arguments=intent_arguments,
                proposal_id=proposal_id,
                evidence_requirements=tuple(evidence(req) for req in requirements),
                allowed_turn=item.get("allowed_turn"),
                arguments_hash=str(item.get("arguments_hash") or ""),
            )
        )
    locks = []
    for item in raw.get("budget_locks") or []:
        if not isinstance(item, dict):
            raise BeliefEngineError("budget_locks must contain JSON objects")
        locks.append(
            BudgetLock(
                resource=str(item.get("resource") or ""),
                amount=item.get("amount", 1),
                scope=str(item.get("scope") or "global"),
                exclusive=item.get("exclusive", False),
                reason=str(item.get("reason") or ""),
            )
        )
    success = raw.get("success") or {}
    if not isinstance(success, dict):
        raise BeliefEngineError("proposal success must be a JSON object")
    return Proposal(
        proposal_id=proposal_id,
        department=str(raw.get("department") or ""),
        summary=str(raw.get("summary") or raw.get("statement") or ""),
        goal_ids=tuple(raw.get("goal_ids") or ()),
        success=ProbabilityConfidence(
            success.get("probability"), success.get("confidence")
        ),
        priority=raw.get("priority"),
        hard_constraints=raw.get("hard_constraints") or {},
        budget_locks=tuple(locks),
        benefits=raw.get("benefits") or {},
        costs=raw.get("costs") or {},
        opportunity_cost=raw.get("opportunity_cost", 0),
        failure_cost=raw.get("failure_cost", 0),
        action_intents=tuple(intents),
        belief_ids=tuple(raw.get("belief_ids") or ()),
        expires_turn=raw.get("expires_turn"),
    )


def _goal_priority(raw: Mapping[str, Any]) -> int:
    """Normalize legacy JSON numeric forms without accepting booleans/fractions."""

    value = raw.get("priority")
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise BeliefEngineError("goal priority must be a non-negative integer")


def _governance_goal_from_dict(raw: Mapping[str, Any]):
    """Restore one active goal into the immutable governance contract."""

    from civ6_belief_engine.governance import ProbabilityConfidence, StrategicGoal

    if not isinstance(raw, Mapping):
        raise BeliefEngineError("active goal must be a JSON object")
    success = raw.get("success")
    if success is None:
        # Pre-typed Goal records only guaranteed statement and priority.
        # Keep unknown likelihood explicit rather than rejecting old journals.
        probability = raw.get("probability", 0.5)
        confidence = raw.get("confidence", 0.0)
    elif isinstance(success, Mapping):
        probability = success.get("probability")
        confidence = success.get("confidence")
    else:
        raise BeliefEngineError("goal success must be a JSON object")
    return StrategicGoal(
        goal_id=str(raw.get("goal_id") or raw.get("id") or ""),
        statement=str(raw.get("statement") or ""),
        priority=_goal_priority(raw),
        success=ProbabilityConfidence(probability, confidence),
        hard_constraints=tuple(raw.get("hard_constraints") or ()),
        deadline_turn=raw.get("deadline_turn"),
        parent_goal_id=raw.get("parent_goal_id") or None,
        tags=tuple(raw.get("tags") or ()),
    )


def _goal_graph_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the fields consumed by the first Goal graph query."""

    return {
        "goal_id": str(raw.get("goal_id") or raw.get("id") or ""),
        "statement": str(raw.get("statement") or ""),
        "priority": _goal_priority(raw),
        "tags": list(raw.get("tags") or ()),
    }


def _release_stale_budget_locks(
    engine: BeliefEngine,
    *,
    turn: int,
) -> list[str]:
    """Release reservations after their last governed execution turn."""

    released: list[str] = []
    for lock in engine.list("budget_lock", status="active"):
        release_after_turn = int(
            lock.get("release_after_turn", lock.get("created_turn", turn))
        )
        if release_after_turn >= turn:
            continue
        engine.update(
            "budget_lock",
            lock["id"],
            {
                "status": "archived",
                "released_turn": turn,
                "release_reason": "turn_advanced",
            },
            turn=turn,
        )
        released.append(lock["id"])
    return released


def _typed_capabilities_for_turn(
    engine: BeliefEngine,
    *,
    turn: int,
) -> dict[str, Any]:
    snapshot = _typed_snapshot_observation_for_turn(engine, turn=turn)
    if snapshot is None:
        raise BeliefEngineError(
            "A current-turn typed snapshot is required; call get_governance_brief first"
        )
    capabilities = (snapshot.get("facts") or {}).get("capabilities") or {}
    if not isinstance(capabilities, dict) or not capabilities.get("ruleset"):
        raise BeliefEngineError("Typed snapshot is missing ruleset capabilities")
    return capabilities


def _typed_snapshot_observation_for_turn(
    engine: BeliefEngine,
    *,
    turn: int,
) -> dict[str, Any] | None:
    snapshots = [
        item
        for item in engine.list("observation", status="active")
        if item.get("source") == "game_state:typed_snapshot"
        and item.get("observed_turn") == turn
    ]
    return (
        max(
            snapshots,
            key=lambda item: (
                float(item.get("updated_at", 0)),
                float(item.get("created_at", 0)),
                str(item.get("id") or ""),
            ),
        )
        if snapshots
        else None
    )


def _reusable_typed_snapshot_for_turn(
    engine: BeliefEngine,
    *,
    turn: int,
) -> dict[str, Any] | None:
    """Reuse the typed snapshot only while no successful action has changed state."""

    snapshot = _typed_snapshot_observation_for_turn(engine, turn=turn)
    if snapshot is None:
        return None
    captured_at = float(snapshot.get("created_at", 0))
    has_later_mutation = any(
        action.get("selected_turn") == turn
        and action.get("success") is True
        and action.get("executed") is True
        and float(action.get("created_at", 0)) > captured_at
        for action in engine.list("action", status="active")
    )
    return None if has_later_mutation else snapshot


def _validate_ruleset_action_intent(
    *,
    tool: str,
    arguments: Mapping[str, Any],
    capabilities: Mapping[str, Any],
) -> None:
    requirements = {
        "appoint_governor": "governors",
        "assign_governor": "governors",
        "promote_governor": "governors",
        "choose_dedication": "dedications",
        "form_alliance": "alliances",
        "queue_wc_votes": "world_congress",
    }
    capability = requirements.get(tool)
    if capability and not capabilities.get(capability, False):
        raise BeliefEngineError(
            f"Action {tool} requires unavailable ruleset capability: {capability}"
        )
    if tool == "propose_trade" and (
        arguments.get("offer_favor") or arguments.get("request_favor")
    ) and not capabilities.get("diplomatic_favor", False):
        raise BeliefEngineError(
            "Diplomatic favor trade requires unavailable ruleset capability: "
            "diplomatic_favor"
        )

async def _capture_governance_snapshot(
    ctx: Context,
    engine: BeliefEngine,
) -> tuple[Any, dict[str, Any], dict[str, Any], list[str], list[dict[str, Any]]]:
    """Capture and ingest the authoritative typed state for one turn."""

    from civ6_belief_engine.governance.snapshot import snapshot_world_state

    snapshot = await pipeline._get_game(ctx).get_governance_snapshot()
    world = snapshot_world_state(snapshot)
    projection = engine.ingest_typed_snapshot(world, turn=snapshot.turn)
    try:
        graph_delta = project_world_state(
            world,
            previous=engine.graph_view,
            epoch=engine.epoch,
        )
        next_graph = engine.record_graph_delta(graph_delta)
        legacy_entities = tuple(
            entity
            for entity in engine.list("world_entity", status="active")
            if entity.get("snapshot_id") == snapshot.snapshot_id
        )
        mismatches = compare_shadow_projection(world, legacy_entities, next_graph)
        projection["graph_shadow"] = {
            "status": "matched" if not mismatches else "mismatch",
            "epoch": next_graph.epoch,
            "state_hash": next_graph.state_hash,
            "nodes": len(next_graph.nodes),
            "edges": len(next_graph.edges),
            "source_nodes": len(world.get("entities") or ()),
            "source_edges": len(world.get("relations") or ()),
            "mismatches": list(mismatches),
        }
        active_goals = engine.list("goal", status="active")
        goal_delta = project_active_goals(
            (_goal_graph_payload(goal) for goal in active_goals),
            previous=engine.graph_view,
            snapshot_id=snapshot.snapshot_id,
            turn=snapshot.turn,
            epoch=engine.epoch,
        )
        if (
            goal_delta.upsert_nodes
            or goal_delta.upsert_edges
            or goal_delta.remove_node_ids
            or goal_delta.remove_edge_keys
        ):
            goal_graph = engine.record_graph_delta(goal_delta, kind="goals")
        else:
            goal_graph = engine.graph_view
        projection["graph_goals"] = {
            "status": "projected",
            "active": len(goal_graph.active_goals()),
            "state_hash": goal_graph.state_hash,
        }
    except Exception as exc:
        # A derived-read-model failure is observable but must not take down the
        # legacy snapshot path. Preserve an already-proven world projection
        # when only the Goal projection failed.
        log.exception("Graph projection failed")
        error = f"{type(exc).__name__}: {exc}"
        projection.setdefault(
            "graph_shadow",
            {
                "status": "error",
                "epoch": engine.epoch,
                "error": error,
            },
        )
        projection["graph_goals"] = {
            "status": "error",
            "error": error,
        }
    released_locks = _release_stale_budget_locks(engine, turn=snapshot.turn)
    active_locks = engine.list("budget_lock", status="active")
    return snapshot, world, projection, released_locks, active_locks

@mcp.tool()
async def record_observation(
    ctx: Context,
    statement: str,
    facts: str = "{}",
    metrics: str = "{}",
    source: str = "agent",
    reliability: float = 1.0,
    tags: str = "[]",
) -> str:
    """Record a factual observation without interpreting what it means.

    ``facts`` and ``metrics`` are JSON objects.  Metrics use stable keys so
    predictions, belief expectations, and plan exit conditions can evaluate
    them later.  Important MCP query results are also captured automatically.
    """

    params = {
        "statement": statement,
        "facts": facts,
        "metrics": metrics,
        "source": source,
        "reliability": reliability,
        "tags": tags,
    }

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        return engine.create(
            "observation",
            {
                "statement": statement,
                "source": source,
                "facts": _belief_json_object(facts, "facts"),
                "metrics": _belief_json_object(metrics, "metrics"),
                "reliability": reliability,
                "tags": _belief_json_list(tags, "tags"),
                "observed_turn": turn,
            },
            turn=turn,
        )

    return await pipeline._belief_tool(ctx, "record_observation", params, _operation)


@mcp.tool()
async def upsert_belief(
    ctx: Context,
    belief_id: str,
    statement: str,
    category: str,
    probability: float,
    confidence: float,
    impact: str = "medium",
    urgency: str = "medium",
    evidence_ids: str = "[]",
    counter_evidence_ids: str = "[]",
    falsifiers: str = "[]",
    expectations: str = "[]",
    action_threshold: float = 0.65,
    replan_threshold: float = 0.5,
    gate_scope: str = "global",
) -> str:
    """Create or revise a belief while retaining its complete revision history."""

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        return engine.upsert(
            "belief",
            belief_id,
            {
                "statement": statement,
                "category": category,
                "probability": probability,
                "confidence": confidence,
                "impact": impact,
                "urgency": urgency,
                "evidence_ids": _belief_json_list(evidence_ids, "evidence_ids"),
                "counter_evidence_ids": _belief_json_list(
                    counter_evidence_ids, "counter_evidence_ids"
                ),
                "falsifiers": _belief_json_list(falsifiers, "falsifiers"),
                "expectations": _belief_json_list(expectations, "expectations"),
                "action_threshold": action_threshold,
                "replan_threshold": replan_threshold,
                "review_required": False,
                "gate_scope": gate_scope,
            },
            turn=turn,
        )

    return await pipeline._belief_tool(ctx, "upsert_belief", params, _operation)


@mcp.tool()
async def upsert_hypothesis(
    ctx: Context,
    hypothesis_id: str,
    topic_id: str,
    statement: str,
    probability: float,
    confidence: float,
    evidence_ids: str = "[]",
    counter_evidence_ids: str = "[]",
) -> str:
    """Create or revise one competing explanation in a hypothesis pool."""

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        return engine.upsert(
            "hypothesis",
            hypothesis_id,
            {
                "topic_id": topic_id,
                "statement": statement,
                "probability": probability,
                "confidence": confidence,
                "evidence_ids": _belief_json_list(evidence_ids, "evidence_ids"),
                "counter_evidence_ids": _belief_json_list(
                    counter_evidence_ids, "counter_evidence_ids"
                ),
            },
            turn=turn,
        )

    return await pipeline._belief_tool(ctx, "upsert_hypothesis", params, _operation)


@mcp.tool()
async def rebalance_hypothesis_pool(
    ctx: Context, topic_id: str, probabilities: str
) -> str:
    """Atomically redistribute a topic's hypothesis probabilities.

    ``probabilities`` is a JSON object mapping hypothesis IDs to probabilities;
    the values must sum to one (within 0.001).
    """

    params = {"topic_id": topic_id, "probabilities": probabilities}
    return await pipeline._belief_tool(
        ctx,
        "rebalance_hypothesis_pool",
        params,
        lambda engine, turn: engine.rebalance_hypotheses(
            topic_id,
            _belief_json_object(probabilities, "probabilities"),
            turn=turn,
        ),
    )


@mcp.tool()
async def upsert_prediction(
    ctx: Context,
    prediction_id: str,
    statement: str,
    probability: float,
    confidence: float,
    deadline_turn: int,
    evaluation: str = "{}",
    belief_ids: str = "[]",
) -> str:
    """Create or revise a falsifiable prediction with a deadline.

    ``evaluation`` may be a metric rule such as
    ``{"metric":"science","operator":">=","value":60}``.
    """

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        return engine.upsert(
            "prediction",
            prediction_id,
            {
                "statement": statement,
                "probability": probability,
                "confidence": confidence,
                "deadline_turn": deadline_turn,
                "evaluation": _belief_json_object(evaluation, "evaluation"),
                "belief_ids": _belief_json_list(belief_ids, "belief_ids"),
                "status": "active",
            },
            turn=turn,
        )

    return await pipeline._belief_tool(ctx, "upsert_prediction", params, _operation)


@mcp.tool()
async def resolve_prediction(
    ctx: Context,
    prediction_id: str,
    outcome: bool,
    actual: str,
) -> str:
    """Resolve a prediction manually when the outcome is not metric-evaluable."""

    params = {"prediction_id": prediction_id, "outcome": outcome, "actual": actual}
    return await pipeline._belief_tool(
        ctx,
        "resolve_prediction",
        params,
        lambda engine, turn: engine.resolve_prediction(
            prediction_id,
            outcome=outcome,
            actual=actual,
            turn=turn,
        ),
    )


@mcp.tool()
async def upsert_dynamic_plan(
    ctx: Context,
    plan_id: str,
    horizon: int,
    goal: str,
    probability_of_success: float,
    assumptions: str = "[]",
    assumption_thresholds: str = "{}",
    evidence_ids: str = "[]",
    success_conditions: str = "[]",
    exit_conditions: str = "[]",
    review_turn: int = 0,
    gate_scope: str = "global",
) -> str:
    """Create or revise a 5/10/20-turn plan with explicit invalidation rules."""

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        actual_review_turn = review_turn or turn + horizon
        return engine.upsert(
            "plan",
            plan_id,
            {
                "horizon": horizon,
                "goal": goal,
                "probability_of_success": probability_of_success,
                "assumptions": _belief_json_list(assumptions, "assumptions"),
                "assumption_thresholds": _belief_json_object(
                    assumption_thresholds, "assumption_thresholds"
                ),
                "evidence_ids": _belief_json_list(evidence_ids, "evidence_ids"),
                "success_conditions": _belief_json_list(
                    success_conditions, "success_conditions"
                ),
                "exit_conditions": _belief_json_list(exit_conditions, "exit_conditions"),
                "review_turn": actual_review_turn,
                "review_required": False,
                "status": "active",
                "gate_scope": gate_scope,
            },
            turn=turn,
        )

    return await pipeline._belief_tool(ctx, "upsert_dynamic_plan", params, _operation)


@mcp.tool()
async def set_plan_status(
    ctx: Context,
    plan_id: str,
    status: str,
    reason: str,
) -> str:
    """Mark a dynamic plan active, completed, abandoned, or needing replan."""

    allowed = {"active", "completed", "abandoned", "needs_replan"}
    params = {"plan_id": plan_id, "status": status, "reason": reason}

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        if status not in allowed:
            raise BeliefEngineError(f"status must be one of: {', '.join(sorted(allowed))}")
        patch: dict[str, Any] = {
            "status": status,
            "status_reason": reason,
            "review_required": status == "needs_replan",
        }
        if status == "active":
            patch.update(
                {
                    "broken_assumptions": [],
                    "triggered_exit_conditions": [],
                }
            )
        return engine.update(
            "plan",
            plan_id,
            patch,
            turn=turn,
        )

    return await pipeline._belief_tool(ctx, "set_plan_status", params, _operation)


@mcp.tool()
async def update_belief_entity(
    ctx: Context,
    entity_type: str,
    entity_id: str,
    patch: str,
) -> str:
    """Patch any current Belief Engine entity; history remains append-only."""

    params = {"entity_type": entity_type, "entity_id": entity_id, "patch": patch}
    return await pipeline._belief_tool(
        ctx,
        "update_belief_entity",
        params,
        lambda engine, turn: engine.update(
            entity_type,
            entity_id,
            _belief_json_object(patch, "patch"),
            turn=turn,
        ),
    )


@mcp.tool()
async def delete_belief_entity(
    ctx: Context,
    entity_type: str,
    entity_id: str,
    reason: str,
) -> str:
    """Delete current state via a tombstone while retaining audit history."""

    params = {"entity_type": entity_type, "entity_id": entity_id, "reason": reason}
    return await pipeline._belief_tool(
        ctx,
        "delete_belief_entity",
        params,
        lambda engine, turn: engine.delete(
            entity_type, entity_id, reason=reason, turn=turn
        ),
    )


@mcp.tool()
async def get_turn_brief(ctx: Context, limit: int = 12) -> str:
    """Return the reviewed Belief Engine state that must guide this turn.

    ``get_game_overview`` includes the same brief automatically.  This tool is
    provided for an explicit precheck or after a major action changes the
    evidence; it is intentionally the single compact entry point instead of
    requiring an agent to remember several separate belief calls.
    """

    params = {"limit": limit}
    return await pipeline._belief_tool(
        ctx,
        "get_turn_brief",
        params,
        lambda engine, turn: engine.turn_brief(turn=turn, limit=limit),
    )


@mcp.tool()
async def get_governance_brief(
    ctx: Context,
    limit: int = 12,
    confidence_floor: float = 0.6,
    include_department_details: bool = False,
) -> str:
    """Capture typed GameState facts and return the national governance agenda.

    This is the preferred start-of-turn control-plane input. It collects one
    same-turn typed snapshot, projects it into the existing event-sourced graph,
    then combines capabilities, scarce-resource budgets, confidence gaps, and
    current belief gates. No narrated game text is parsed for this snapshot.
    """

    mode = pipeline._get_belief_mode(ctx)
    if not mode.captures_governance_snapshot:
        return json.dumps(
            {
                "belief_mode": mode.value,
                "disabled": True,
                "message": (
                    "Live governance snapshots are disabled outside enforce mode; "
                    "use get_game_overview and targeted game queries."
                ),
            },
            ensure_ascii=False,
        )

    started = time.monotonic()
    params = {
        "limit": limit,
        "confidence_floor": confidence_floor,
        "include_department_details": include_department_details,
    }
    try:
        if not 0 <= confidence_floor <= 1:
            raise BeliefEngineError("confidence_floor must be between 0 and 1")
        engine, _turn = await pipeline._belief_context(ctx)
        snapshot, world, projection, released_locks, active_locks = (
            await _capture_governance_snapshot(ctx, engine)
        )
        belief_brief = engine.turn_brief(turn=snapshot.turn, limit=limit)
        active_goals = engine.list("goal", status="active")
        typed_goals = tuple(
            _governance_goal_from_dict(goal) for goal in active_goals
        )
        graph_ready = (
            projection.get("graph_shadow", {}).get("status") != "error"
            and projection.get("graph_goals", {}).get("status") == "projected"
        )
        from civ6_belief_engine.governance.departments import (
            NationalStrategyCoordinator,
            default_department_registry,
        )

        national_strategy = NationalStrategyCoordinator(
            default_department_registry()
        ).run(
            snapshot,
            agenda=tuple(goal.statement for goal in typed_goals),
            goals=typed_goals,
            graph=engine.graph_view if graph_ready else None,
        )
        await pipeline._flush_belief_events(ctx)
        low_confidence = [
            {
                "id": item["id"],
                "statement": item.get("statement"),
                "confidence": item.get("confidence"),
                "gate_scope": item.get("gate_scope", "global"),
            }
            for item in engine.list("belief", status="active")
            if float(item.get("confidence", 0)) < confidence_floor
        ][: max(1, min(limit, 50))]
        city_ids = [
            item["entity_id"]
            for item in world["entities"]
            if item.get("entity_type") == "city"
        ]
        unit_ids = [
            item["entity_id"]
            for item in world["entities"]
            if item.get("entity_type") == "unit"
        ]
        metrics = world.get("metrics") or {}
        gold_capacity = float(metrics.get("player.gold", 0))
        faith_capacity = float(metrics.get("player.faith", 0))

        def locked_amount(resource: str) -> float:
            return sum(
                float(lock.get("amount", 0))
                for lock in active_locks
                if lock.get("resource") == resource
            )

        def available_scopes(resource: str, scopes: list[str]) -> list[str]:
            held_scopes = {
                str(lock.get("scope") or "global")
                for lock in active_locks
                if lock.get("resource") == resource and lock.get("exclusive")
            }
            if "global" in held_scopes:
                return []
            return [scope for scope in scopes if scope not in held_scopes]

        result = {
            "game_id": engine.game_id,
            "turn": snapshot.turn,
            "snapshot": {
                "snapshot_id": snapshot.snapshot_id,
                "turn_before": snapshot.turn_before,
                "turn_after": snapshot.turn_after,
                "ruleset": world["ruleset"],
                "entity_count": len(world["entities"]),
                "relation_count": len(world["relations"]),
                **projection,
            },
            "capabilities": world["capabilities"],
            "national_strategy": _national_strategy_payload(
                national_strategy,
                include_details=include_department_details,
            ),
            "budget_capacity": {
                "gold": gold_capacity,
                "faith": faith_capacity,
                "research": 1,
                "civic": 1,
                "city_production": len(city_ids),
                "unit_action": len(unit_ids),
            },
            "available_budget": {
                "gold": max(0.0, gold_capacity - locked_amount("gold")),
                "faith": max(0.0, faith_capacity - locked_amount("faith")),
                "research_slots": available_scopes("research", ["current"]),
                "civic_slots": available_scopes("civic", ["current"]),
                "city_production_slots": available_scopes(
                    "city_production", city_ids
                ),
                "unit_action_slots": available_scopes("unit_action", unit_ids),
            },
            "confidence_gaps": low_confidence,
            "belief_brief": belief_brief,
            "governance": {
                "goals": active_goals[:limit],
                "proposals": engine.list("proposal", status="active")[:limit],
                "critic_reviews": engine.list("critic_review", status="active")[:limit],
                "council_decisions": engine.list("council_decision", status=None)[:limit],
                "budget_locks": active_locks[:limit],
                "released_budget_locks": released_locks,
            },
            "arbitration_order": [
                "hard_constraints",
                "budget_locks",
                "strategic_priority",
                "pareto_dominance",
                "opportunity_cost",
            ],
        }
        text = json.dumps(result, ensure_ascii=False, indent=2)
        await pipeline._get_logger(ctx).log_tool_call(
            "get_governance_brief",
            params,
            text,
            int((time.monotonic() - started) * 1000),
        )
        return pipeline._filter_downstream_result("get_governance_brief", params, text)
    except (BeliefEngineError, TypeError, ValueError, ConnectionError) as exc:
        message = f"Error: {exc}"
        await pipeline._get_logger(ctx).log_error("get_governance_brief", message)
        return message


@mcp.tool()
async def upsert_strategic_goal(
    ctx: Context,
    goal_id: str,
    statement: str,
    priority: int,
    probability: float,
    confidence: float,
    hard_constraints: str = "[]",
    deadline_turn: int = 0,
    parent_goal_id: str = "",
    tags: str = "[]",
) -> str:
    """Create or revise a national goal with separate likelihood/confidence."""

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        from civ6_belief_engine.governance import ProbabilityConfidence, StrategicGoal

        goal = StrategicGoal(
            goal_id=goal_id,
            statement=statement,
            priority=priority,
            success=ProbabilityConfidence(probability, confidence),
            hard_constraints=tuple(
                _belief_json_list(hard_constraints, "hard_constraints")
            ),
            deadline_turn=deadline_turn or None,
            parent_goal_id=parent_goal_id or None,
            tags=tuple(_belief_json_list(tags, "tags")),
        )
        payload = _governance_payload(goal)
        payload.update(
            {
                "statement": statement,
                "probability": probability,
                "confidence": confidence,
            }
        )
        return engine.upsert("goal", goal_id, payload, turn=turn)

    return await pipeline._belief_tool(ctx, "upsert_strategic_goal", params, _operation)


@mcp.tool()
async def submit_governance_proposal(ctx: Context, proposal: str) -> str:
    """Submit a structured ministerial proposal; departments cannot execute it.

    ``proposal`` is a JSON string. Its required canonical shape is:
    ``{"proposal_id":"p","department":"production","summary":"...",``
    ``"goal_ids":[],"success":{"probability":0.8,"confidence":0.8},``
    ``"priority":50,"hard_constraints":{},"budget_locks":[],``
    ``"benefits":{},"costs":{},"opportunity_cost":0.0,``
    ``"action_intents":[{"intent_id":"i","tool":"unit_action",``
    ``"arguments":{},"proposal_id":"p"}]}``.

    ``success`` must be an object (not top-level ``success_probability``),
    ``hard_constraints``/``benefits``/``costs`` are objects, and
    ``budget_locks`` is an array. Every action intent needs a non-empty
    ``intent_id`` and the same ``proposal_id`` as its enclosing proposal. It
    remains advisory until ``resolve_governance_council``.
    """

    params = {"proposal": proposal}

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        parsed = _belief_json_object(proposal, "proposal")
        typed = _governance_proposal_from_dict(parsed)
        capabilities = _typed_capabilities_for_turn(engine, turn=turn)
        for intent in typed.action_intents:
            _validate_ruleset_action_intent(
                tool=intent.tool,
                arguments=intent.arguments,
                capabilities=capabilities,
            )
        for intent in typed.action_intents:
            duplicate = engine.find_duplicate_pending_intent(
                tool=intent.tool,
                params=dict(intent.arguments or {}),
                exclude_proposal_id=typed.proposal_id,
            )
            if duplicate is not None:
                raise BeliefEngineError(
                    f"Duplicate pending council intent for {intent.tool}: "
                    f"already approved in proposal {duplicate['proposal_id']} "
                    "without a terminal decision. Complete or cancel that "
                    "authorization (or change the arguments) instead of "
                    "re-authorizing the same action."
                )
        for goal_id in typed.goal_ids:
            goal = engine.get("goal", goal_id)
            if not goal or goal.get("status") != "active":
                raise BeliefEngineError(f"Referenced active goal not found: {goal_id}")
        for belief_id in typed.belief_ids:
            belief = engine.get("belief", belief_id)
            if not belief or belief.get("status") != "active":
                raise BeliefEngineError(
                    f"Referenced active belief not found: {belief_id}"
                )
        payload = _governance_payload(typed)
        payload.update(
            {
                "status": "active",
                "statement": typed.summary,
                "action_intent": (
                    _governance_payload(typed.action_intents[0])
                    if typed.action_intents
                    else {}
                ),
                "submitted_turn": turn,
                "council_state": "proposed",
                "council_decision_id": None,
                "rejection_reasons": [],
            }
        )
        return engine.upsert(
            "proposal", typed.proposal_id, payload, turn=turn
        )

    return await pipeline._belief_tool(ctx, "submit_governance_proposal", params, _operation)


@mcp.tool()
async def review_governance_proposal(
    ctx: Context,
    review_id: str,
    proposal_id: str,
    verdict: str,
    rationale: str,
    probability: float,
    confidence: float,
    conditions: str = "[]",
    counterevidence: str = "[]",
    invalidated_assumptions: str = "[]",
    alternative: str = "",
) -> str:
    """Persist an evidence-grounded Devil's Advocate review.

    A review may agree, agree with concrete conditions, or object. Bare
    objections are rejected: they need counterevidence or an invalidated
    assumption plus a concrete alternative.
    """

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        from civ6_belief_engine.governance import (
            CounterEvidence,
            DevilsAdvocate,
            DevilsAdvocateVerdict,
            ProbabilityConfidence,
        )

        proposal_entity = engine.get("proposal", proposal_id)
        if not proposal_entity or proposal_entity.get("status") != "active":
            raise BeliefEngineError(f"Referenced active proposal not found: {proposal_id}")
        proposal_typed = _governance_proposal_from_dict(proposal_entity)
        evidence_items = _belief_json_list(counterevidence, "counterevidence")
        if not all(isinstance(item, dict) for item in evidence_items):
            raise BeliefEngineError(
                "counterevidence must contain traceable JSON observation objects"
            )
        grounded_evidence = []
        for item in evidence_items:
            observation_id = str(item.get("observation_id") or "")
            observation = engine.get("observation", observation_id)
            if not observation:
                raise BeliefEngineError(
                    f"Counterevidence observation not found: {observation_id}"
                )
            grounded_evidence.append(
                CounterEvidence(
                    observation_id=observation_id,
                    statement=str(item.get("statement") or observation.get("statement") or ""),
                    source_tool=str(
                        item.get("source_tool")
                        or str(observation.get("source") or "").removeprefix("mcp:")
                    ),
                    observed_turn=item.get(
                        "observed_turn", observation.get("observed_turn", turn)
                    ),
                )
            )
        review = DevilsAdvocate.review(
            proposal_typed,
            review_id=review_id,
            verdict=DevilsAdvocateVerdict(verdict),
            rationale=rationale,
            assessment=ProbabilityConfidence(probability, confidence),
            conditions=tuple(_belief_json_list(conditions, "conditions")),
            counterevidence=tuple(grounded_evidence),
            invalidated_assumptions=tuple(
                _belief_json_list(
                    invalidated_assumptions, "invalidated_assumptions"
                )
            ),
            alternative=alternative or None,
        )
        payload = _governance_payload(review)
        payload["proposal_version"] = proposal_entity["version"]
        return engine.upsert("critic_review", review_id, payload, turn=turn)

    return await pipeline._belief_tool(ctx, "review_governance_proposal", params, _operation)


@mcp.tool()
async def resolve_governance_council(
    ctx: Context,
    budget_limits: str,
    accepted_conditions: str = "{}",
) -> str:
    """Arbitrate proposals without a weighted national-strategy score.

    Order is fixed: hard constraints, grounded critic review, budget locks,
    strategic priority, Pareto dominance, then opportunity cost. Selected
    proposals become eligible for exact action routing; they do not execute.
    """

    params = {
        "budget_limits": budget_limits,
        "accepted_conditions": accepted_conditions,
    }

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        from civ6_belief_engine.governance import (
            BudgetLock,
            CouncilDecision,
            CouncilDecisionStatus,
            GovernanceCouncil,
        )

        _release_stale_budget_locks(engine, turn=turn)
        limits = _belief_json_object(budget_limits, "budget_limits")
        accepted = _belief_json_object(accepted_conditions, "accepted_conditions")
        typed_snapshot = _typed_snapshot_observation_for_turn(engine, turn=turn)
        if typed_snapshot is None:
            raise BeliefEngineError(
                "A current-turn typed snapshot is required; call get_governance_brief first"
            )
        snapshot_metrics = typed_snapshot.get("metrics") or {}
        authoritative_capacity = {
            "gold": max(0.0, float(snapshot_metrics.get("player.gold", 0))),
            "faith": max(0.0, float(snapshot_metrics.get("player.faith", 0))),
            "research": 1.0,
            "civic": 1.0,
            "city_production": max(
                0.0, float(snapshot_metrics.get("player.cities", 0))
            ),
            "unit_action": max(
                0.0, float(snapshot_metrics.get("player.units", 0))
            ),
        }
        for resource, capacity in authoritative_capacity.items():
            requested = limits.get(resource, capacity)
            if isinstance(requested, bool) or not isinstance(requested, (int, float)):
                raise BeliefEngineError(
                    f"budget limit {resource!r} must be numeric, not bool"
                )
            limits[resource] = min(float(requested), capacity)
            for key in tuple(limits):
                if key.startswith(resource + ":"):
                    scoped = limits[key]
                    if isinstance(scoped, bool) or not isinstance(scoped, (int, float)):
                        raise BeliefEngineError(
                            f"budget limit {key!r} must be numeric, not bool"
                        )
                    limits[key] = min(float(scoped), capacity)
        proposal_entities = engine.list("proposal", status="active")
        proposals = [
            _governance_proposal_from_dict(item) for item in proposal_entities
        ]
        proposal_versions = {
            str(item["id"]): int(item["version"]) for item in proposal_entities
        }
        blocked_by_critic: dict[str, tuple[str, ...]] = {}
        latest_reviews: dict[str, dict[str, Any]] = {}
        for review in engine.list("critic_review", status="active"):
            proposal_id = str(review.get("proposal_id") or "")
            if (
                proposal_id
                and review.get("proposal_version") == proposal_versions.get(proposal_id)
                and proposal_id not in latest_reviews
            ):
                latest_reviews[proposal_id] = review
        eligible = []
        for proposal_typed in proposals:
            review = latest_reviews.get(proposal_typed.proposal_id)
            if not review or review.get("verdict") == "agree":
                eligible.append(proposal_typed)
                continue
            if review.get("verdict") == "object":
                blocked_by_critic[proposal_typed.proposal_id] = (
                    f"grounded critic objection: {review.get('rationale', '')}",
                )
                continue
            required = set(review.get("conditions") or [])
            supplied = set(accepted.get(proposal_typed.proposal_id) or [])
            if required.issubset(supplied):
                eligible.append(proposal_typed)
            else:
                missing = sorted(required - supplied)
                blocked_by_critic[proposal_typed.proposal_id] = (
                    "critic conditions not accepted: " + ", ".join(missing),
                )

        active_locks = tuple(
            BudgetLock(
                resource=str(item.get("resource") or ""),
                amount=item.get("amount", 1),
                scope=str(item.get("scope") or "global"),
                exclusive=item.get("exclusive", False),
                reason=str(item.get("reason") or ""),
            )
            for item in engine.list("budget_lock", status="active")
        )
        proposal_identity = {
            "proposal_ids": sorted(item.proposal_id for item in proposals)
        }
        council_id = (
            f"council:{turn}:"
            f"{action_args_hash(proposal_identity)[:16]}"
        )
        base = GovernanceCouncil().decide(
            turn=turn,
            proposals=eligible,
            budget_limits=limits,
            held_locks=active_locks,
            decision_id=council_id,
        )
        rejected = dict(base.rejected_reasons)
        rejected.update(blocked_by_critic)
        selected = base.selected_proposal_ids
        if selected and rejected:
            status = CouncilDecisionStatus.PARTIAL
        elif selected:
            status = CouncilDecisionStatus.APPROVED
        else:
            status = CouncilDecisionStatus.REJECTED
        decision = CouncilDecision(
            decision_id=base.decision_id,
            turn=turn,
            status=status,
            selected_proposal_ids=selected,
            considered_proposal_ids=tuple(
                proposal_typed.proposal_id for proposal_typed in proposals
            ),
            rejected_reasons=rejected,
            explanation=(
                "critic: objections require evidence; conditions require explicit acceptance",
                *base.explanation,
            ),
            budget_usage=base.budget_usage,
        )
        payload = _governance_payload(decision)
        council_state = payload.pop("status")
        payload.update(
            {
                "status": "resolved",
                "council_state": council_state,
                "statement": f"Governance council resolved {len(proposals)} proposals",
                "selected_proposal_id": selected[0] if selected else "none",
                "typed_snapshot_id": (typed_snapshot.get("facts") or {}).get(
                    "snapshot_id"
                ),
                "effective_budget_limits": limits,
            }
        )
        persisted = engine.upsert(
            "council_decision",
            decision.decision_id,
            payload,
            turn=turn,
        )
        selected_set = set(selected)
        considered_set = {item.proposal_id for item in proposals}
        for proposal_typed in proposals:
            approved = proposal_typed.proposal_id in selected_set
            engine.update(
                "proposal",
                proposal_typed.proposal_id,
                {
                    "status": "resolved",
                    "council_state": "approved" if approved else "rejected",
                    "council_decision_id": decision.decision_id,
                    "rejection_reasons": list(
                        rejected.get(proposal_typed.proposal_id, ())
                    ),
                },
                turn=turn,
            )
            if approved:
                scheduled_turns = [
                    intent.allowed_turn
                    for intent in proposal_typed.action_intents
                    if intent.allowed_turn is not None
                ]
                release_after_turn = max(scheduled_turns, default=turn)
                for index, lock in enumerate(proposal_typed.budget_locks):
                    engine.upsert(
                        "budget_lock",
                        (
                            f"lock:{decision.decision_id}:"
                            f"{proposal_typed.proposal_id}:{index}"
                        ),
                        {
                            **_governance_payload(lock),
                            "proposal_id": proposal_typed.proposal_id,
                            "council_decision_id": decision.decision_id,
                            "release_after_turn": release_after_turn,
                        },
                        turn=turn,
                    )
        for review in engine.list("critic_review", status="active"):
            if str(review.get("proposal_id") or "") in considered_set:
                engine.update(
                    "critic_review",
                    review["id"],
                    {
                        "status": "resolved",
                        "council_decision_id": decision.decision_id,
                    },
                    turn=turn,
                )
        return persisted

    return await pipeline._belief_tool(ctx, "resolve_governance_council", params, _operation)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_belief_state(
    ctx: Context,
    entity_type: str = "",
    status: str = "active",
    last_n: int = 50,
) -> str:
    """Read the current world model without loading the entire raw trace.

    With no ``entity_type``, returns decision-relevant entities and current
    normalized metrics.  Use ``get_belief_trace`` for immutable history.
    """

    params = {"entity_type": entity_type, "status": status, "last_n": last_n}

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        selected_status = None if status in {"", "all"} else status
        limit = max(1, min(last_n, 200))
        if entity_type:
            return {
                "game_id": engine.game_id,
                "turn": turn,
                "entity_type": entity_type,
                "items": engine.list(entity_type, status=selected_status)[:limit],
            }
        visible_types = (
            "belief",
            "hypothesis",
            "prediction",
            "plan",
            "surprise",
            "contradiction",
            "attribution",
            "decision",
            "goal",
            "proposal",
            "critic_review",
            "council_decision",
            "budget_lock",
            "outcome",
            "world_entity",
        )
        return {
            "game_id": engine.game_id,
            "turn": turn,
            "current_metrics": engine.current_metrics(),
            "entities": {
                kind: engine.list(kind, status=selected_status)[:limit]
                for kind in visible_types
            },
            "research_metrics": engine.metrics(),
        }

    return await pipeline._belief_tool(ctx, "get_belief_state", params, _operation)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_belief_trace(
    ctx: Context,
    entity_type: str = "",
    entity_id: str = "",
    last_n: int = 100,
) -> str:
    """Read immutable create/update/delete history for audit and attribution."""

    params = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "last_n": last_n,
    }
    return await pipeline._belief_tool(
        ctx,
        "get_belief_trace",
        params,
        lambda engine, turn: {
            "game_id": engine.game_id,
            "turn": turn,
            "events": engine.history(
                entity_type=entity_type or None,
                entity_id=entity_id or None,
                last_n=last_n,
            ),
        },
    )


@mcp.tool()
async def review_belief_engine(ctx: Context) -> str:
    """Evaluate due predictions, contradictions, and plan invalidation triggers."""

    return await pipeline._belief_tool(
        ctx,
        "review_belief_engine",
        {},
        lambda engine, turn: engine.review(turn=turn),
    )


@mcp.tool()
async def route_belief_decision(
    ctx: Context,
    statement: str,
    probability: float,
    confidence: float,
    impact: str,
    urgency: str,
    irreversibility: float,
    belief_ids: str | list[str] = "[]",
    considered_actions: str | list[str] = "[]",
    selected_action: str = "",
    reason: str = "",
    action_intent: str | dict[str, Any] = "{}",
    evidence_requirements: str | list[dict[str, Any]] = "[]",
    gate_scope: str = "global",
    council_decision_id: str = "",
) -> str:
    """Route a decision and bind it to an exact action/evidence contract.

    ``action_intent`` is the authorization contract; the legacy
    ``selected_action`` field is audit-only and cannot authorize execution:
    ``{"tool":"set_research","params":{"tech_or_civic":"TECH_WRITING",...}}``.
    For ``verify_then_fast``, ``evidence_requirements`` names the exact fresh
    queries that must follow the decision. The harness compares tool parameters,
    not merely the presence of any query.
    """

    mode = pipeline._get_belief_mode(ctx)
    if not mode.enforces_actions:
        return json.dumps(
            {
                "belief_mode": mode.value,
                "enforced": False,
                "authorized": True,
                "message": "Action routing is bypassed outside enforce mode.",
            },
            ensure_ascii=False,
        )

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        governance_gate = engine.governance_turn_gate(turn=turn)
        if governance_gate["typed_snapshot_id"] is None:
            raise BeliefEngineError(
                "A current-turn typed governance snapshot is required; "
                "call get_governance_brief first"
            )
        parsed_intent = _belief_json_object(action_intent, "action_intent")
        if not parsed_intent and str(selected_action or "").strip():
            raise BeliefEngineError(
                "selected_action is audit-only; provide a structured action_intent"
            )
        if parsed_intent:
            tool = str(parsed_intent.get("tool") or "").strip()
            supplied_params = parsed_intent.get("params")
            supplied_arguments = parsed_intent.get("arguments")
            if (
                supplied_params is not None
                and supplied_arguments is not None
                and supplied_params != supplied_arguments
            ):
                raise BeliefEngineError(
                    "action_intent params and arguments must match when both are present"
                )
            intent_params = pipeline._canonical_action_params(
                tool,
                supplied_params
                if supplied_params is not None
                else supplied_arguments
                if supplied_arguments is not None
                else {},
            )
            if not tool or not isinstance(intent_params, dict):
                raise BeliefEngineError(
                    "action_intent requires a tool and JSON object arguments/params"
                )
            computed_hash = action_args_hash(intent_params)
            supplied_hash = str(
                parsed_intent.get("args_hash")
                or parsed_intent.get("arguments_hash")
                or ""
            )
            if supplied_hash and supplied_hash != computed_hash:
                raise BeliefEngineError(
                    "action_intent argument hash does not match its arguments"
                )
            parsed_intent["params"] = intent_params
            parsed_intent["args_hash"] = computed_hash
            _validate_ruleset_action_intent(
                tool=tool,
                arguments=intent_params,
                capabilities=_typed_capabilities_for_turn(engine, turn=turn),
            )
        else:
            parsed_intent = None
        parsed_requirements = _belief_json_list(
            evidence_requirements, "evidence_requirements"
        )

        def normalize_requirements(items: list[Any]) -> list[dict[str, Any]]:
            normalized: list[dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict) or not item.get("tool"):
                    raise BeliefEngineError(
                        "evidence_requirements must contain JSON objects with a tool"
                    )
                requirement_params = item.get("params") or {}
                required_facts = item.get("required_facts") or []
                required_metrics = (
                    item.get("required_metrics") or item.get("metric_keys") or []
                )
                expected_facts = item.get("expected_facts") or {}
                minimum_sequence = item.get("min_observation_sequence", 0)
                max_age_turns = item.get("max_age_turns")
                if not isinstance(requirement_params, dict):
                    raise BeliefEngineError(
                        "evidence requirement params must be a JSON object"
                    )
                if not isinstance(required_facts, list) or not all(
                    isinstance(value, str) and value for value in required_facts
                ):
                    raise BeliefEngineError(
                        "evidence required_facts must be a list of non-empty strings"
                    )
                if not isinstance(required_metrics, list) or not all(
                    isinstance(value, str) and value for value in required_metrics
                ):
                    raise BeliefEngineError(
                        "evidence required_metrics must be a list of non-empty strings"
                    )
                if not isinstance(expected_facts, dict) or not all(
                    isinstance(key, str) and key for key in expected_facts
                ):
                    raise BeliefEngineError(
                        "evidence expected_facts must be a JSON object with non-empty keys"
                    )
                if type(minimum_sequence) is not int or minimum_sequence < 0:
                    raise BeliefEngineError(
                        "evidence min_observation_sequence must be a non-negative integer"
                    )
                if max_age_turns is not None and (
                    type(max_age_turns) is not int or max_age_turns < 0
                ):
                    raise BeliefEngineError(
                        "evidence max_age_turns must be a non-negative integer or null"
                    )
                normalized.append(
                    {
                        "requirement_id": str(item.get("requirement_id") or ""),
                        "tool": str(item["tool"]),
                        "target_entity_id": item.get("target_entity_id"),
                        "params": requirement_params,
                        "min_observation_sequence": minimum_sequence,
                        "max_age_turns": max_age_turns,
                        "required_facts": list(required_facts),
                        "required_metrics": list(required_metrics),
                        "expected_facts": expected_facts,
                    }
                )
            return normalized

        parsed_requirements = normalize_requirements(parsed_requirements)
        council_required = bool(
            parsed_intent
            and pipeline._governance_council_required(
                tool=tool,
                params=intent_params,
                impact=impact,
                irreversibility=irreversibility,
            )
        )
        if council_decision_id:
            council = engine.get("council_decision", council_decision_id)
            if not council:
                raise BeliefEngineError(
                    f"Unknown council decision: {council_decision_id}"
                )
            if not parsed_intent:
                raise BeliefEngineError(
                    "a council-routed decision requires a structured action_intent"
                )
            proposal_id = str(parsed_intent.get("proposal_id") or "")
            selected_ids = set(council.get("selected_proposal_ids") or [])
            if proposal_id not in selected_ids:
                raise BeliefEngineError(
                    "action_intent proposal_id was not selected by the council"
                )
            proposal = engine.get("proposal", proposal_id)
            if not proposal or proposal.get("council_decision_id") != council_decision_id:
                raise BeliefEngineError(
                    f"Council-selected proposal not found: {proposal_id}"
                )
            candidate_hash = action_args_hash(intent_params)
            approved_intent = next(
                (
                    item
                    for item in proposal.get("action_intents") or []
                    if isinstance(item, dict)
                    and item.get("tool") == tool
                    and item.get("arguments_hash") == candidate_hash
                ),
                None,
            )
            if approved_intent is None:
                raise BeliefEngineError(
                    "action_intent does not match an action selected by the council"
                )
            allowed_turn = approved_intent.get("allowed_turn")
            if allowed_turn is not None and allowed_turn != turn:
                raise BeliefEngineError(
                    f"action_intent is approved only for turn {allowed_turn}"
                )
            approved_requirements = normalize_requirements(
                list(approved_intent.get("evidence_requirements") or [])
            )
            if parsed_requirements and parsed_requirements != approved_requirements:
                raise BeliefEngineError(
                    "evidence_requirements do not match the council-approved contract"
                )
            parsed_requirements = approved_requirements
            parsed_intent = {
                **parsed_intent,
                "proposal_version": int(proposal.get("version", 1)),
                "council_decision_id": council_decision_id,
            }
        elif council_required:
            raise BeliefEngineError(
                "This national, scarce-resource, high-impact, or irreversible "
                "action requires a governance proposal and council_decision_id"
            )
        elif governance_gate["active_proposal_ids"]:
            raise BeliefEngineError(
                "Active governance proposals must be resolved before direct routing; "
                "supply the selected council_decision_id"
            )
        decision = engine.route_decision(
            statement=statement,
            probability=probability,
            confidence=confidence,
            impact=impact,
            urgency=urgency,
            irreversibility=irreversibility,
            belief_ids=_belief_json_list(belief_ids, "belief_ids"),
            action_intent=parsed_intent,
            evidence_requirements=parsed_requirements,
            gate_scope=gate_scope,
            council_decision_id=council_decision_id or None,
            turn=turn,
        )
        extras = {
            "considered_actions": _belief_json_list(
                considered_actions, "considered_actions"
            ),
            "selected_action": selected_action,
            "reason": reason,
            "governance_snapshot_id": governance_gate["typed_snapshot_id"],
            # The common action wrapper consumes structured authorization once.
            # Human-readable selected_action remains audit-only.
            "decision_state": "authorized" if parsed_intent else "unbound",
        }
        return engine.update("decision", decision["id"], extras, turn=turn)

    return await pipeline._belief_tool(ctx, "route_belief_decision", params, _operation)


@mcp.tool()
async def cancel_routed_action(
    ctx: Context,
    decision_id: str,
    reason: str,
) -> str:
    """Close an unexecuted/retryable routed action with an explicit Outcome.

    Use this after a slow review changes the plan or when a retry is no longer
    rational. It cannot cancel an executing or already successful action.
    """

    params = {"decision_id": decision_id, "reason": reason}
    return await pipeline._belief_tool(
        ctx,
        "cancel_routed_action",
        params,
        lambda engine, turn: engine.cancel_action_authorization(
            decision_id,
            reason=reason,
            turn=turn,
        ),
    )


@mcp.tool()
async def assess_route_combat_risk(
    ctx: Context,
    belief_id: str,
    nearby_hostiles: str = "[]",
    assessment: str = "",
) -> str:
    """仅依据量化战斗预估来修订路线信念。

    - 若 nearby_hostiles 非空但未提供 assessment: 不修改信念, 返回 verify_then_fast。
    - 若要修订信念: 先调用 get_combat_estimate(unit_id, target_x, target_y),
      再将其结果作为 assessment 传入, 字段: source("combat_estimate"),
      revised_probability, attacker_cs, defender_cs, attacker_hp, defender_hp,
      expected_damage_to_attacker, expected_damage_to_defender。
    - 下调概率必须被数值支持: defender_cs > attacker_cs 且 攻击方损失比例更大。

    示例:
      get_combat_estimate -> {source: "combat_estimate", attacker_cs: 20, ...}
      assess_route_combat_risk(belief_id=...,
        nearby_hostiles='[{"type":"UNIT_SCOUT","cs":10,"hp":100,"dist":2}]',
        assessment='{"source":"combat_estimate","revised_probability":0.55,...}')
    """

    params = {
        "belief_id": belief_id,
        "nearby_hostiles": nearby_hostiles,
        "assessment": assessment,
    }

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        parsed_assessment = None
        if assessment and assessment.strip() not in {"null", "None"}:
            parsed_assessment = _belief_json_object(assessment, "assessment")
        hostiles = _belief_json_list(nearby_hostiles, "nearby_hostiles")
        if not all(isinstance(item, dict) for item in hostiles):
            raise BeliefEngineError("nearby_hostiles must contain JSON objects")
        return engine.assess_route_combat_risk(
            belief_id,
            nearby_hostiles=hostiles,
            assessment=parsed_assessment,
            turn=turn,
        )

    return await pipeline._belief_tool(ctx, "assess_route_combat_risk", params, _operation)


@mcp.tool()
async def record_action_verification(
    ctx: Context,
    decision_id: str,
    tool: str,
    expected: str,
    actual: str,
    success: bool,
    belief_changes: str = "{}",
) -> str:
    """Link a decision to evidence, including recovery after an interrupted call.

    When an authorization is persisted as ``executing`` but the normal MCP
    response is lost, this is the explicit recovery path. The tool must match
    the hash-bound intent; success closes it, while failure makes it retryable.
    """

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        decision = engine.get("decision", decision_id)
        if not decision:
            raise BeliefEngineError(f"Unknown decision: {decision_id}")
        if decision.get("decision_state") == "executing":
            intent = decision.get("action_intent") or {}
            expected_tool = str(intent.get("tool") or "")
            if expected_tool != tool:
                raise BeliefEngineError(
                    f"Verification tool {tool} does not match executing intent "
                    f"{expected_tool}"
                )
            intent_params = intent.get("params") or intent.get("arguments") or {}
            canonical_params = pipeline._canonical_action_params(tool, intent_params)
            action = engine.record_tool_result(
                tool=tool,
                params=canonical_params,
                result=actual,
                turn=turn,
                category="action",
                success=success,
                duration_ms=0,
                decision_id=decision_id,
                decision_route=str(decision.get("route") or "recovery"),
            )
            if action is None:
                raise BeliefEngineError("Unable to persist recovered action outcome")
            return engine.update(
                "action",
                action["id"],
                {
                    "expected": expected,
                    "actual_ref": tool_result_reference(actual),
                    "belief_changes": _belief_json_object(
                        belief_changes, "belief_changes"
                    ),
                    "verification": {
                        "verified": True,
                        "source": "agent_recovery",
                    },
                },
                turn=turn,
            )
        return engine.create(
            "action",
            {
                "statement": f"Verification for decision {decision_id}",
                "decision_id": decision_id,
                "tool": tool,
                "expected": expected,
                "actual_ref": tool_result_reference(actual),
                "success": success,
                "belief_changes": _belief_json_object(
                    belief_changes, "belief_changes"
                ),
                "verification": {"verified": True, "source": "agent"},
            },
            turn=turn,
        )

    return await pipeline._belief_tool(ctx, "record_action_verification", params, _operation)


@mcp.tool()
async def upsert_failure_attribution(
    ctx: Context,
    attribution_id: str,
    failure: str,
    candidates: str,
) -> str:
    """Create or revise candidate causes for a failure.

    Candidates are JSON objects with ``id``, ``statement``, ``prior``, and
    optional weighted ``evidence_for`` / ``evidence_against`` arrays.
    """

    params = {
        "attribution_id": attribution_id,
        "failure": failure,
        "candidates": candidates,
    }

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        entity = engine.upsert(
            "attribution",
            attribution_id,
            {
                "failure": failure,
                "candidates": _belief_json_list(candidates, "candidates"),
            },
            turn=turn,
        )
        return engine.update_attribution_posteriors(entity["id"], turn=turn)

    return await pipeline._belief_tool(ctx, "upsert_failure_attribution", params, _operation)


@mcp.tool()
async def recompute_failure_attribution(ctx: Context, attribution_id: str) -> str:
    """Recompute posterior candidate-cause weights after evidence changes."""

    return await pipeline._belief_tool(
        ctx,
        "recompute_failure_attribution",
        {"attribution_id": attribution_id},
        lambda engine, turn: engine.update_attribution_posteriors(
            attribution_id, turn=turn
        ),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_belief_metrics(ctx: Context) -> str:
    """Return belief, prediction, plan, routing, and calibration metrics."""

    return await pipeline._belief_tool(
        ctx,
        "get_belief_metrics",
        {},
        lambda engine, turn: {"turn": turn, **engine.metrics()},
    )
