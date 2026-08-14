"""Pure typed-world projection into the canonical graph contracts."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .model import Coverage, Edge, EdgeKey, GraphDelta, Node, canonical_json
from .view import GraphView


class GraphProjectionError(ValueError):
    """Raised when a typed world payload cannot form a coherent graph."""


_RELATION_NAMES = {
    "diplomacy": "DIPLOMACY_WITH",
    "progressing": "RESEARCHING",
    "stockpiles": "OWNS",
}


def _entity_identity(
    raw_type: str,
    raw_id: str,
    attributes: Mapping[str, Any],
) -> tuple[str, str, Coverage]:
    if raw_type == "city" and type(attributes.get("x")) is int and type(attributes.get("y")) is int:
        # Civ VI exposes a per-owner City.GetID(), not a proven global city ID.
        # The city-centre coordinate survives ownership changes and is the best
        # available phase-one identity. Raze-and-resettle still requires a
        # future lineage signal from the adapter.
        return (
            "city",
            f"city:{attributes['x']}:{attributes['y']}",
            Coverage.KNOWN_HISTORY,
        )
    if raw_type == "city":
        raise GraphProjectionError(
            f"city {raw_id} requires integer x/y for owner-independent identity"
        )
    if raw_type == "civilization":
        suffix = raw_id.rsplit(":", 1)[-1]
        return "player", f"player:{suffix}", Coverage.KNOWN_HISTORY
    if raw_type == "barbarian_unit":
        suffix = raw_id.rsplit(":", 1)[-1]
        return "unit", f"unit:barbarian:{suffix}", Coverage.CURRENTLY_VISIBLE
    if raw_type in {"foreign_unit", "hostile_unit"}:
        prefix = "foreign_unit:" if raw_type == "foreign_unit" else "hostile_unit:"
        suffix = raw_id.removeprefix(prefix)
        return "unit", f"unit:{suffix}", Coverage.CURRENTLY_VISIBLE
    if raw_type in {"tile", "barbarian_camp"}:
        return raw_type, raw_id, Coverage.CURRENTLY_VISIBLE
    if raw_type == "player":
        return raw_type, raw_id, Coverage.KNOWN_HISTORY
    return raw_type, raw_id, Coverage.COMPLETE


def _relation_coverage(
    relation_type: str,
    source: Node,
    target: Node,
) -> Coverage:
    if relation_type == "DIPLOMACY_WITH":
        return Coverage.KNOWN_HISTORY
    if (
        source.coverage is Coverage.CURRENTLY_VISIBLE
        or target.coverage is Coverage.CURRENTLY_VISIBLE
    ):
        return Coverage.CURRENTLY_VISIBLE
    return Coverage.COMPLETE


def project_world_state(
    world: Mapping[str, Any],
    *,
    previous: GraphView | None = None,
    epoch: int = 1,
) -> GraphDelta:
    """Return the minimal delta from a typed world snapshot.

    The function performs no I/O. Absence deletes only data whose declared
    coverage is complete; visible-only or historical facts become unknown.
    """

    snapshot_id = str(world.get("snapshot_id") or "")
    if not snapshot_id:
        raise GraphProjectionError("typed world requires snapshot_id")
    turn = world.get("turn")
    if type(turn) is not int or turn < 0:
        raise GraphProjectionError("typed world requires a non-negative integer turn")
    if type(epoch) is not int or epoch < 1:
        raise GraphProjectionError("epoch must be a positive integer")
    if previous is None or previous.epoch != epoch:
        previous = GraphView.empty(epoch=epoch, turn=turn)

    current_nodes: dict[str, Node] = {}
    source_to_graph_id: dict[str, str] = {}
    for raw_node in world.get("entities") or ():
        if not isinstance(raw_node, Mapping):
            raise GraphProjectionError("typed world entities must be objects")
        raw_id = str(raw_node.get("entity_id") or raw_node.get("id") or "")
        raw_type = str(raw_node.get("entity_type") or raw_node.get("node_type") or "")
        attributes = raw_node.get("attributes") or {}
        if not raw_id or not raw_type or not isinstance(attributes, Mapping):
            raise GraphProjectionError("typed world entity requires type, ID, and attributes")
        node_type, node_id, coverage = _entity_identity(raw_type, raw_id, attributes)
        if node_id in current_nodes:
            raise GraphProjectionError(f"duplicate normalized node ID: {node_id}")
        source_to_graph_id[raw_id] = node_id
        prior = previous.node(node_id)
        first_turn = prior.first_observed_turn if prior is not None else turn
        current_nodes[node_id] = Node(
            node_id=node_id,
            node_type=node_type,
            attributes=attributes,
            epoch=epoch,
            first_observed_turn=first_turn,
            last_observed_turn=turn,
            coverage=coverage,
            observed=True,
        )

    current_edges: dict[EdgeKey, Edge] = {}
    for raw_edge in world.get("relations") or ():
        if not isinstance(raw_edge, Mapping):
            raise GraphProjectionError("typed world relations must be objects")
        raw_relation = str(raw_edge.get("relation_type") or "")
        raw_source = str(raw_edge.get("source_id") or "")
        raw_target = str(raw_edge.get("target_id") or "")
        if not raw_relation or not raw_source or not raw_target:
            raise GraphProjectionError("typed world relation requires type, source, and target")
        source_id = source_to_graph_id.get(raw_source)
        target_id = source_to_graph_id.get(raw_target)
        if source_id is None or target_id is None:
            raise GraphProjectionError(
                f"dangling source relation {raw_relation}: {raw_source} -> {raw_target}"
            )
        relation_type = _RELATION_NAMES.get(raw_relation.lower(), raw_relation.upper())
        source_node = current_nodes[source_id]
        target_node = current_nodes[target_id]
        key = (relation_type, source_id, target_id)
        if key in current_edges:
            raise GraphProjectionError(f"duplicate normalized edge: {key}")
        prior = previous.edges.get(key)
        current_edges[key] = Edge(
            relation_type=relation_type,
            source_id=source_id,
            target_id=target_id,
            attributes=raw_edge.get("attributes") or {},
            epoch=epoch,
            valid_from_turn=prior.valid_from_turn if prior is not None else turn,
            last_observed_turn=turn,
            coverage=_relation_coverage(relation_type, source_node, target_node),
            observed=True,
        )

    upsert_nodes = list(current_nodes.values())
    remove_node_ids: list[str] = []
    for node_id, prior in previous.nodes.items():
        if node_id in current_nodes:
            continue
        if prior.coverage is Coverage.COMPLETE:
            remove_node_ids.append(node_id)
        elif prior.observed:
            upsert_nodes.append(replace(prior, observed=False))

    upsert_edges = list(current_edges.values())
    remove_edge_keys: list[EdgeKey] = []
    removed_nodes = set(remove_node_ids)
    for key, prior in previous.edges.items():
        if key in current_edges:
            continue
        if prior.source_id in removed_nodes or prior.target_id in removed_nodes:
            remove_edge_keys.append(key)
            continue
        source_is_current = prior.source_id in current_nodes
        if prior.coverage is Coverage.COMPLETE or (
            source_is_current and prior.coverage is Coverage.CURRENTLY_VISIBLE
        ):
            remove_edge_keys.append(key)
        elif prior.observed:
            upsert_edges.append(replace(prior, observed=False))

    return GraphDelta(
        snapshot_id=snapshot_id,
        turn=turn,
        epoch=epoch,
        upsert_nodes=tuple(upsert_nodes),
        upsert_edges=tuple(upsert_edges),
        remove_node_ids=tuple(remove_node_ids),
        remove_edge_keys=tuple(remove_edge_keys),
    )


def compare_shadow_projection(
    world: Mapping[str, Any],
    legacy_entities: tuple[Mapping[str, Any], ...],
    graph: GraphView,
) -> tuple[str, ...]:
    """Compare the legacy world-entity projection with the shadow graph."""

    issues: list[str] = []
    legacy_by_id = {
        str(entity.get("id") or ""): entity
        for entity in legacy_entities
        if entity.get("id")
    }
    graph_ids: dict[str, str] = {}
    expected_graph_ids: set[str] = set()
    raw_entities = world.get("entities") or ()
    for raw_node in raw_entities:
        if not isinstance(raw_node, Mapping):
            issues.append("source_entity_not_object")
            continue
        raw_id = str(raw_node.get("entity_id") or raw_node.get("id") or "")
        raw_type = str(raw_node.get("entity_type") or raw_node.get("node_type") or "")
        attributes = raw_node.get("attributes") or {}
        legacy = legacy_by_id.get(raw_id)
        if legacy is None:
            issues.append(f"legacy_missing_node:{raw_id}")
        else:
            if str(legacy.get("node_type") or "") != raw_type:
                issues.append(f"legacy_node_type:{raw_id}")
            if canonical_json(legacy.get("attributes") or {}) != canonical_json(attributes):
                issues.append(f"legacy_attributes:{raw_id}")
        try:
            _, graph_id, _ = _entity_identity(raw_type, raw_id, attributes)
        except (TypeError, ValueError) as exc:
            issues.append(f"graph_identity:{raw_id}:{exc}")
            continue
        graph_ids[raw_id] = graph_id
        expected_graph_ids.add(graph_id)
        graph_node = graph.node(graph_id)
        if graph_node is None or not graph_node.observed:
            issues.append(f"graph_missing_node:{graph_id}")
        elif canonical_json(graph_node.attributes) != canonical_json(attributes):
            issues.append(f"graph_attributes:{graph_id}")

    observed_graph_ids = {
        node.node_id
        for node in graph.nodes.values()
        if node.observed and node.last_observed_turn == world.get("turn")
    }
    if observed_graph_ids != expected_graph_ids:
        issues.append("graph_observed_node_set")

    expected_edge_keys: set[EdgeKey] = set()
    for raw_edge in world.get("relations") or ():
        if not isinstance(raw_edge, Mapping):
            issues.append("source_edge_not_object")
            continue
        relation = str(raw_edge.get("relation_type") or "")
        source_id = str(raw_edge.get("source_id") or "")
        target_id = str(raw_edge.get("target_id") or "")
        legacy_source = legacy_by_id.get(source_id)
        legacy_target = legacy_by_id.get(target_id)
        outgoing = {
            (
                str(link.get("relation") or ""),
                str(link.get("direction") or ""),
                str(link.get("entity_id") or ""),
                canonical_json(link.get("attributes") or {}),
            )
            for link in (legacy_source or {}).get("links", ())
        }
        incoming = {
            (
                str(link.get("relation") or ""),
                str(link.get("direction") or ""),
                str(link.get("entity_id") or ""),
                canonical_json(link.get("attributes") or {}),
            )
            for link in (legacy_target or {}).get("links", ())
        }
        attributes_json = canonical_json(raw_edge.get("attributes") or {})
        if (relation, "outgoing", target_id, attributes_json) not in outgoing:
            issues.append(f"legacy_outgoing_edge:{relation}:{source_id}:{target_id}")
        if (relation, "incoming", source_id, attributes_json) not in incoming:
            issues.append(f"legacy_incoming_edge:{relation}:{source_id}:{target_id}")
        graph_source = graph_ids.get(source_id)
        graph_target = graph_ids.get(target_id)
        if graph_source is None or graph_target is None:
            continue
        graph_relation = _RELATION_NAMES.get(relation.lower(), relation.upper())
        key = (graph_relation, graph_source, graph_target)
        expected_edge_keys.add(key)
        graph_edge = graph.edges.get(key)
        if graph_edge is None or not graph_edge.observed:
            issues.append(f"graph_missing_edge:{graph_relation}:{graph_source}:{graph_target}")
        elif canonical_json(graph_edge.attributes) != attributes_json:
            issues.append(f"graph_edge_attributes:{graph_relation}:{graph_source}:{graph_target}")

    observed_edge_keys = {
        edge.key
        for edge in graph.edges.values()
        if edge.observed and edge.last_observed_turn == world.get("turn")
    }
    if observed_edge_keys != expected_edge_keys:
        issues.append("graph_observed_edge_set")
    return tuple(sorted(set(issues)))
