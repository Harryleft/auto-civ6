"""Typed governance snapshot lifecycle at the MCP boundary.

This module owns capture, graph projection, and same-turn snapshot reuse. It
does not register MCP tools; tool modules consume these functions explicitly.
"""

import logging
from collections.abc import Mapping
from typing import Any

from mcp.server.fastmcp import Context

from civ6_belief_engine.belief_engine import BeliefEngine, BeliefEngineError
from civ6_belief_engine.graph import (
    compare_shadow_projection,
    project_active_goals,
    project_world_state,
)
from civ6_belief_engine.graph.context import summarize_world_changes
from civ_mcp.server import pipeline
from civ_mcp.server.tools.governance_adapters import _goal_graph_payload

log = logging.getLogger(__name__)


def _release_stale_budget_locks(
    engine: BeliefEngine,
    *,
    turn: int,
) -> list[str]:
    """Release reservations after their last governed execution turn."""

    released: list[str] = []
    for lock in engine.current_governance_entities("budget_lock", status="active"):
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
        for item in engine.current_governance_entities("observation", status="active")
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
    """Historical candidate without a known later submission, not fresh evidence.

    Kept for internal compatibility. An earlier async action can still take
    effect after capture, so authoritative overview requests always recapture.
    """

    snapshot = _typed_snapshot_observation_for_turn(engine, turn=turn)
    if snapshot is None:
        return None
    captured_at = float(snapshot.get("created_at", 0))
    has_later_mutation = any(
        action.get("selected_turn") == turn
        and action.get("executed") is True
        and float(action.get("created_at", 0)) > captured_at
        for action in engine.current_governance_entities("action", status="active")
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
        world_changes = summarize_world_changes(engine.graph_view, graph_delta)
        next_graph = engine.record_graph_delta(graph_delta)
        projection["world_changes"] = world_changes
        governance_graph = engine.sync_governance_graph(turn=snapshot.turn)
        event_source_entities = tuple(
            entity
            for entity in engine.list("world_entity", status="active")
            if entity.get("snapshot_id") == snapshot.snapshot_id
        )
        mismatches = compare_shadow_projection(
            world, event_source_entities, next_graph
        )
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
        projection["graph_governance"] = {
            "status": "projected",
            "nodes": len(
                tuple(
                    node
                    for node in governance_graph.nodes.values()
                    if node.source in {
                        "belief_engine:goal",
                        "belief_engine:governance",
                    }
                )
            ),
            "edges": len(
                tuple(
                    edge
                    for edge in governance_graph.edges.values()
                    if edge.source == "belief_engine:governance"
                )
            ),
        }
        # This is the validation input for the dedicated Goal projection, not
        # a Department read. Keep the event-source records here so malformed
        # journal goals remain observable as ``graph_goals: error`` instead of
        # being silently filtered by the governance GraphView materializer.
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
        # event-source snapshot path. Preserve an already-proven world projection
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
    engine.sync_governance_graph(turn=snapshot.turn)
    active_locks = engine.graph_entities("budget_lock", status="active")
    return snapshot, world, projection, released_locks, active_locks
