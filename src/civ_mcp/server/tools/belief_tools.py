"""MCP governance and belief tools.

Tool registration lives here; contract normalization and typed snapshot
capture are explicit neighboring boundaries.
"""

import json
import logging
import re
import time
from typing import Any

from mcp.server.fastmcp import Context

from civ6_belief_engine.graph import GOVERNANCE_ENTITY_TYPES
from civ6_belief_engine.belief_engine import (
    BeliefEngine,
    BeliefEngineError,
    action_args_hash,
    tool_result_reference,
)
from civ_mcp.server import pipeline
from civ_mcp.server.assembly import mcp
from civ_mcp.server.governance_snapshot import (
    _capture_governance_snapshot,
    _release_stale_budget_locks,
    _typed_capabilities_for_turn,
    _typed_snapshot_observation_for_turn,
    _validate_ruleset_action_intent,
)
from civ_mcp.server.tools.governance_adapters import (
    _belief_json_list,
    _belief_json_object,
    _governance_payload,
    _governance_proposal_from_dict,
    _national_strategy_payload,
    _normalize_impact_urgency,
)

log = logging.getLogger(__name__)


def _intents_fingerprint(intents: Any) -> str:
    """Content identity of an intent set the council approved.

    Routing compares the proposal's *current* ``action_intents`` against what
    was approved. Without this, rewriting the proposal after the vote (for
    example through ``update_belief_entity``) silently redirects an approval
    from intent A to intent B — the budget lock and the council's priority
    ordering were both computed for A.
    """

    canonical = sorted(
        json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for item in (intents or [])
        if isinstance(item, dict)
    )
    return action_args_hash({"intents": canonical})


# ---------------------------------------------------------------------------
# Belief Engine
# ---------------------------------------------------------------------------


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
    unknown_basis: bool = False,
) -> str:
    """Create or revise a belief while retaining its complete revision history.

    ``impact`` and ``urgency`` must be low, medium, high, or critical.
    A belief must cite ``evidence_ids`` (observation references); if it has
    no observable basis at all, set ``unknown_basis=true`` to explicitly
    declare an unverified basis. Evidence-less beliefs without that
    declaration are rejected.
    """

    impact = _normalize_impact_urgency(impact, "impact")
    urgency = _normalize_impact_urgency(urgency, "urgency")
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
                "unknown_basis": bool(unknown_basis),
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
        active_goals = engine.graph_entities("goal", status="active")
        from civ6_belief_engine.governance import GraphSnapshotView
        from civ6_belief_engine.governance.departments import (
            NationalStrategyCoordinator,
            default_department_registry,
        )
        department_snapshot = GraphSnapshotView.from_graph(
            engine.graph_view,
            snapshot_id=snapshot.snapshot_id,
            turn=snapshot.turn,
            player_id=snapshot.player_id,
        )

        national_strategy = NationalStrategyCoordinator(
            default_department_registry()
        ).run(
            department_snapshot,
            # Departments always receive the materialized graph.  A stale or
            # failed projection remains visible to their conservative gates;
            # silently switching the whole coordinator back to the legacy
            # snapshot would hide a broken new-track read path.
            graph=engine.graph_view,
        )
        await pipeline._flush_belief_events(ctx)
        low_confidence = [
            {
                "id": item["id"],
                "statement": item.get("statement"),
                "confidence": item.get("confidence"),
                "gate_scope": item.get("gate_scope", "global"),
            }
            for item in engine.graph_entities("belief", status="active")
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
                "proposals": engine.graph_entities("proposal", status="active")[:limit],
                "critic_reviews": engine.graph_entities("critic_review", status="active")[:limit],
                "council_decisions": engine.graph_entities("council_decision", status=None)[:limit],
                "budget_locks": engine.graph_entities("budget_lock", status="active")[:limit],
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
    ``benefits``/``costs`` values must be real numbers (e.g. ``0.8``);
    booleans coerce to ``1.0``/``0.0`` and numeric strings are parsed.
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
            goal = engine.current_governance_entity("goal", goal_id)
            if not goal or goal.get("status") != "active":
                raise BeliefEngineError(f"Referenced active goal not found: {goal_id}")
        for belief_id in typed.belief_ids:
            belief = engine.current_governance_entity("belief", belief_id)
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

        proposal_entity = engine.current_governance_entity("proposal", proposal_id)
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
            observation = engine.current_governance_entity("observation", observation_id)
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
        proposal_entities = engine.current_governance_entities("proposal", status="active")
        proposals = [
            _governance_proposal_from_dict(item) for item in proposal_entities
        ]
        proposal_versions = {
            str(item["id"]): int(item["version"]) for item in proposal_entities
        }
        blocked_by_critic: dict[str, tuple[str, ...]] = {}
        latest_reviews: dict[str, dict[str, Any]] = {}
        for review in engine.current_governance_entities("critic_review", status="active"):
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
            for item in engine.current_governance_entities("budget_lock", status="active")
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
        selected_set = set(selected)
        # Pin the approved intent content. Routing must reject a proposal whose
        # action_intents were rewritten after the vote, otherwise a council
        # approval of A can be redirected to execute B while the budget lock and
        # priority ordering were both computed for A.
        approved_intents = {
            item.proposal_id: _intents_fingerprint(
                _governance_payload(item.action_intents)
            )
            for item in proposals
            if item.proposal_id in selected_set
        }
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
                "approved_intents": approved_intents,
            }
        )
        persisted = engine.upsert(
            "council_decision",
            decision.decision_id,
            payload,
            turn=turn,
        )
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
        for review in engine.current_governance_entities("critic_review", status="active"):
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
            graph_items = engine.graph_entities(entity_type, status=selected_status)
            return {
                "game_id": engine.game_id,
                "turn": turn,
                "entity_type": entity_type,
                "items": (
                    graph_items
                    if entity_type in GOVERNANCE_ENTITY_TYPES
                    else engine.list(entity_type, status=selected_status)
                )[:limit],
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
            "simulation",
            "world_entity",
        )
        return {
            "game_id": engine.game_id,
            "turn": turn,
            "current_metrics": engine.current_metrics(),
            "entities": {
                kind: (
                    engine.graph_entities(kind, status=selected_status)
                    if kind in GOVERNANCE_ENTITY_TYPES
                    else engine.list(kind, status=selected_status)
                )[:limit]
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

    ``impact`` and ``urgency`` must be low, medium, high, or critical.
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

    impact = _normalize_impact_urgency(impact, "impact")
    urgency = _normalize_impact_urgency(urgency, "urgency")
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
            council = engine.current_governance_entity(
                "council_decision", council_decision_id
            )
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
            proposal = engine.current_governance_entity("proposal", proposal_id)
            if not proposal or proposal.get("council_decision_id") != council_decision_id:
                raise BeliefEngineError(
                    f"Council-selected proposal not found: {proposal_id}"
                )
            # The council approved a specific intent set; a later rewrite of the
            # proposal must invalidate the approval rather than redirect it.
            pinned = (council.get("approved_intents") or {}).get(proposal_id)
            if pinned is None:
                raise BeliefEngineError(
                    "council decision did not pin approved intents for this "
                    "proposal; re-run resolve_governance_council"
                )
            if pinned != _intents_fingerprint(proposal.get("action_intents")):
                raise BeliefEngineError(
                    "proposal action_intents changed after the council approved "
                    "them; re-run resolve_governance_council"
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

    ``outcome_unknown`` decisions are settled here too: cancellation requires
    prior verification, so without this branch a conservative ``submitted``
    receipt leaves an authorization with no official exit and deadlocks
    ``end_turn`` for the rest of the game.
    """

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        decision = engine.current_governance_entity("decision", decision_id)
        if not decision:
            raise BeliefEngineError(f"Unknown decision: {decision_id}")
        verification_patch = {
            "expected": expected,
            "actual_ref": tool_result_reference(actual),
            "belief_changes": _belief_json_object(
                belief_changes, "belief_changes"
            ),
            "verification": {
                "verified": True,
                "source": "agent_recovery",
            },
        }
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
                verification_patch,
                turn=turn,
            )
        if decision.get("decision_state") == "outcome_unknown":
            # The mutation was already recorded with an unknown receipt; this
            # verification is the read-back that settles it. Complete the
            # authorization directly instead of appending a second action
            # event for the same attempt.
            intent = decision.get("action_intent") or {}
            expected_tool = str(intent.get("tool") or "")
            if expected_tool != tool:
                raise BeliefEngineError(
                    f"Verification tool {tool} does not match unverified intent "
                    f"{expected_tool}"
                )
            engine.complete_action_authorization(
                decision_id,
                tool=tool,
                success=success,
                outcome_status="succeeded" if success else "failed",
                result=actual,
                turn=turn,
            )
            linked_actions = [
                item
                for item in engine.current_governance_entities("action", status=None)
                if item.get("decision_id") == decision_id
            ]
            latest = max(
                linked_actions,
                key=lambda item: (
                    item.get("version", 0),
                    item.get("last_updated_turn", 0),
                ),
                default=None,
            )
            if latest is not None:
                return engine.update(
                    "action",
                    latest["id"],
                    verification_patch,
                    turn=turn,
                )
            return engine.current_governance_entity("decision", decision_id) or {}
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
