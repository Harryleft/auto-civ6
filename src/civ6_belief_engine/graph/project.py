"""Pure typed-world projection into the canonical graph contracts."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping

from .model import Coverage, Edge, EdgeKey, GraphDelta, Node, canonical_json
from .view import GraphView


class GraphProjectionError(ValueError):
    """Raised when a typed world payload cannot form a coherent graph."""


WORLD_SOURCE = "game_state:typed_snapshot"
GOAL_SOURCE = "belief_engine:goal"
GOVERNANCE_SOURCE = "belief_engine:governance"

_GOVERNANCE_ENTITY_TYPES = frozenset(
    {
        "observation",
        "belief",
        "goal",
        "proposal",
        "critic_review",
        "council_decision",
        "budget_lock",
        "decision",
        "action",
        "outcome",
        "hypothesis",
        "prediction",
        "plan",
        "surprise",
        "contradiction",
        "attribution",
        "simulation",
    }
)


_RELATION_NAMES = {
    "diplomacy": "DIPLOMACY_WITH",
    "progressing": "RESEARCHING",
    "stockpiles": "OWNS",
}


def city_node_id(x: int, y: int) -> str:
    """Owner-independent city identity for phase one (centre coordinates).

    Civ VI exposes a per-owner City.GetID(); the centre coordinate survives
    ownership changes. Raze-and-resettle still needs a future lineage signal
    from the adapter. Keep this the single construction point — departments
    must not re-format city IDs locally.
    """
    return f"city:{x}:{y}"


def _entity_identity(
    raw_type: str,
    raw_id: str,
    attributes: Mapping[str, Any],
) -> tuple[str, str, Coverage]:
    if raw_type == "city" and type(attributes.get("x")) is int and type(attributes.get("y")) is int:
        return (
            "city",
            city_node_id(attributes["x"], attributes["y"]),
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
            source=WORLD_SOURCE,
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
            source=WORLD_SOURCE,
            coverage=_relation_coverage(relation_type, source_node, target_node),
            observed=True,
        )

    # Content-deduplicated upserts (same rule as project_active_goals): a
    # node whose attributes, coverage, or observed flag are unchanged is not
    # re-written. Without this every turn persisted the full entity set,
    # doubling journal growth next to the legacy world-entity events and
    # making replay cost quadratic (one state-hash per full-view delta).
    upsert_nodes: list[Node] = []
    for node in current_nodes.values():
        prior = previous.node(node.node_id)
        if (
            prior is None
            or not prior.observed
            or prior.attributes != node.attributes
            or prior.coverage is not node.coverage
            or prior.node_type != node.node_type
        ):
            upsert_nodes.append(node)
    remove_node_ids: list[str] = []
    for node_id, prior in previous.nodes.items():
        if node_id in current_nodes:
            continue
        if prior.source != WORLD_SOURCE:
            continue
        if prior.coverage is Coverage.COMPLETE:
            remove_node_ids.append(node_id)
        elif prior.observed:
            upsert_nodes.append(replace(prior, observed=False))

    upsert_edges: list[Edge] = []
    for edge in current_edges.values():
        prior_edge = previous.edges.get(edge.key)
        if (
            prior_edge is None
            or not prior_edge.observed
            or prior_edge.attributes != edge.attributes
            or prior_edge.coverage is not edge.coverage
        ):
            upsert_edges.append(edge)
    remove_edge_keys: list[EdgeKey] = []
    removed_nodes = set(remove_node_ids)
    for key, prior in previous.edges.items():
        if key in current_edges:
            continue
        if prior.source != WORLD_SOURCE:
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


def project_active_goals(
    goals: Iterable[Mapping[str, Any]],
    *,
    previous: GraphView,
    snapshot_id: str,
    turn: int,
    epoch: int,
) -> GraphDelta:
    """Project the complete active-goal set into the existing graph namespace."""

    if not isinstance(previous, GraphView):
        raise TypeError("previous must be GraphView")
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise GraphProjectionError("active goals require snapshot_id")
    if type(turn) is not int or turn < 0:
        raise GraphProjectionError("active goals require a non-negative integer turn")
    if type(epoch) is not int or epoch < 1:
        raise GraphProjectionError("epoch must be a positive integer")
    if previous.epoch != epoch:
        raise GraphProjectionError("active goals must use the current graph epoch")

    current: dict[str, Node] = {}
    for raw in goals:
        if not isinstance(raw, Mapping):
            raise GraphProjectionError("active goals must be objects")
        goal_id = str(raw.get("goal_id") or "").strip()
        statement = str(raw.get("statement") or "").strip()
        priority = raw.get("priority")
        if (
            not goal_id
            or not statement
            or type(priority) is not int
            or priority < 0
        ):
            raise GraphProjectionError(
                "active goal requires goal_id, statement, and non-negative integer priority"
            )
        node_id = goal_id if goal_id.startswith("goal:") else f"goal:{goal_id}"
        if node_id in current:
            raise GraphProjectionError(f"duplicate active goal: {goal_id}")
        prior = previous.node(node_id)
        current[node_id] = Node(
            node_id=node_id,
            node_type="goal",
            attributes=raw,
            epoch=epoch,
            first_observed_turn=(
                prior.first_observed_turn if prior is not None else turn
            ),
            last_observed_turn=turn,
            source=GOAL_SOURCE,
            coverage=Coverage.COMPLETE,
            observed=True,
        )
    previous_goal_ids = {
        node.node_id
        for node in previous.nodes.values()
        if node.node_type == "goal" and node.source == GOAL_SOURCE
    }
    return GraphDelta(
        snapshot_id=snapshot_id,
        turn=turn,
        epoch=epoch,
        upsert_nodes=tuple(
            node
            for node_id, node in current.items()
            if (
                previous.node(node_id) is None
                or previous.node(node_id).attributes != node.attributes
                or not previous.node(node_id).observed
            )
        ),
        remove_node_ids=tuple(sorted(previous_goal_ids - set(current))),
    )


def _governance_node_id(entity_type: str, entity_id: str) -> str:
    prefix = f"{entity_type}:"
    return entity_id if entity_id.startswith(prefix) else f"{prefix}{entity_id}"


def _intent_node_id(intent_id: str) -> str:
    return _governance_node_id("action_intent", intent_id)


def _as_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list, set, frozenset)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _embedded_intent(entity: Mapping[str, Any]) -> Mapping[str, Any] | None:
    intent = entity.get("action_intent")
    return intent if isinstance(intent, Mapping) else None


def project_governance_state(
    entities: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    previous: GraphView | None,
    snapshot_id: str,
    turn: int,
    epoch: int = 1,
) -> GraphDelta:
    """Materialize governance lifecycle entities and their current relations.

    JSONL remains the append-only audit history. This projector makes the
    current Observation -> Goal -> Proposal -> Decision -> Action -> Outcome
    chain queryable in the same immutable GraphView as world facts.
    """

    if not snapshot_id:
        raise GraphProjectionError("governance state requires snapshot_id")
    if type(turn) is not int or turn < 0:
        raise GraphProjectionError("governance state requires a non-negative turn")
    if type(epoch) is not int or epoch < 1:
        raise GraphProjectionError("governance state requires a positive epoch")
    if previous is None or previous.epoch != epoch:
        previous = GraphView.empty(epoch=epoch, turn=turn)

    current_nodes: dict[str, Node] = {}
    raw_by_type: dict[str, tuple[Mapping[str, Any], ...]] = {}

    def add_intent_node(
        intent: Mapping[str, Any], *, observed: bool, turn: int
    ) -> None:
        intent_id = str(intent.get("intent_id") or "").strip()
        if not intent_id:
            return
        node_id = _intent_node_id(intent_id)
        prior = previous.node(node_id)
        current_nodes[node_id] = Node(
            node_id=node_id,
            node_type="action_intent",
            attributes=dict(intent),
            epoch=epoch,
            first_observed_turn=(
                prior.first_observed_turn if prior is not None else turn
            ),
            last_observed_turn=turn,
            source=GOVERNANCE_SOURCE,
            coverage=Coverage.COMPLETE,
            observed=observed,
        )

    for entity_type, raw_entities in entities.items():
        if entity_type not in _GOVERNANCE_ENTITY_TYPES:
            continue
        normalized = tuple(raw_entities)
        raw_by_type[entity_type] = normalized
        for entity in normalized:
            if not isinstance(entity, Mapping):
                raise GraphProjectionError("governance entities must be objects")
            entity_id = str(entity.get("id") or entity.get("goal_id") or "").strip()
            if not entity_id:
                raise GraphProjectionError(
                    f"governance {entity_type} requires a stable id"
                )
            if entity_type == "goal" and (
                type(entity.get("priority")) is not int
                or entity.get("priority", 0) < 0
                or not str(entity.get("statement") or "").strip()
            ):
                # A malformed legacy goal must not poison GraphView.active_goals.
                # The compatibility goal projector reports the validation error
                # to the caller on the same capture cycle.
                continue
            node_id = _governance_node_id(entity_type, entity_id)
            prior = previous.node(node_id)
            observed = entity.get("status") != "deleted"
            current_nodes[node_id] = Node(
                node_id=node_id,
                node_type=entity_type,
                attributes=dict(entity),
                epoch=epoch,
                first_observed_turn=(
                    prior.first_observed_turn if prior is not None else turn
                ),
                last_observed_turn=turn,
                source=GOAL_SOURCE if entity_type == "goal" else GOVERNANCE_SOURCE,
                coverage=Coverage.COMPLETE,
                observed=observed,
            )
            embedded = _embedded_intent(entity)
            if embedded is not None:
                add_intent_node(embedded, observed=observed, turn=turn)
            for intent in entity.get("action_intents") or ():
                if isinstance(intent, Mapping):
                    add_intent_node(intent, observed=observed, turn=turn)

    edge_specs: dict[EdgeKey, Edge] = {}

    def add_edge(
        relation_type: str,
        source_id: str,
        target_id: str,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        if source_id not in current_nodes or target_id not in current_nodes:
            return
        source = current_nodes[source_id]
        target = current_nodes[target_id]
        if not source.observed or not target.observed:
            return
        edge = Edge(
            relation_type=relation_type,
            source_id=source_id,
            target_id=target_id,
            attributes=attributes or {},
            epoch=epoch,
            valid_from_turn=turn,
            last_observed_turn=turn,
            source=GOVERNANCE_SOURCE,
            coverage=Coverage.COMPLETE,
            observed=True,
        )
        edge_specs[edge.key] = edge

    for entity_type, raw_entities in raw_by_type.items():
        for entity in raw_entities:
            if entity.get("status") == "deleted":
                continue
            entity_id = str(entity.get("id") or entity.get("goal_id") or "")
            source_id = _governance_node_id(entity_type, entity_id)
            for goal_id in _as_ids(entity.get("goal_ids")):
                add_edge("SERVES_GOAL", source_id, _governance_node_id("goal", goal_id))
            for belief_id in _as_ids(entity.get("belief_ids")):
                add_edge("GROUNDED_BY", source_id, _governance_node_id("belief", belief_id))
            for intent in entity.get("action_intents") or ():
                if isinstance(intent, Mapping) and intent.get("intent_id"):
                    add_edge(
                        "HAS_ACTION_INTENT",
                        source_id,
                        _intent_node_id(str(intent["intent_id"])),
                    )
            embedded = _embedded_intent(entity)
            if embedded and embedded.get("intent_id"):
                add_edge(
                    "USES_ACTION_INTENT",
                    source_id,
                    _intent_node_id(str(embedded["intent_id"])),
                )
            for proposal_id in _as_ids(entity.get("selected_proposal_ids")):
                add_edge(
                    "SELECTS",
                    source_id,
                    _governance_node_id("proposal", proposal_id),
                )
            for proposal_id in _as_ids(entity.get("considered_proposal_ids")):
                add_edge(
                    "CONSIDERS",
                    source_id,
                    _governance_node_id("proposal", proposal_id),
                )
            for field_name, relation in (
                ("proposal_id", "REFERENCES_PROPOSAL"),
                ("council_decision_id", "REFERENCES_COUNCIL"),
                ("decision_id", "REFERENCES_DECISION"),
                ("action_id", "REFERENCES_ACTION"),
                ("action_intent_id", "REFERENCES_ACTION_INTENT"),
            ):
                reference = entity.get(field_name)
                if reference:
                    target_type = field_name.removesuffix("_id")
                    target_id = (
                        _intent_node_id(str(reference))
                        if target_type == "action_intent"
                        else _governance_node_id(target_type, str(reference))
                    )
                    add_edge(relation, source_id, target_id)
            for observation_id in _as_ids(entity.get("verification_observation_ids")):
                add_edge(
                    "VERIFIED_BY",
                    source_id,
                    _governance_node_id("observation", observation_id),
                )

    upsert_nodes = tuple(
        node
        for node_id, node in current_nodes.items()
        if (
            previous.node(node_id) is None
            or previous.node(node_id).attributes != node.attributes
            or previous.node(node_id).observed != node.observed
            or previous.node(node_id).source != node.source
        )
    )
    current_edge_keys = set(edge_specs)
    remove_edge_keys = tuple(
        sorted(
            key
            for key, edge in previous.edges.items()
            if edge.source == GOVERNANCE_SOURCE and key not in current_edge_keys
        )
    )
    upsert_edges = tuple(
        edge
        for key, edge in edge_specs.items()
        if (
            previous.edges.get(key) is None
            or previous.edges[key].attributes != edge.attributes
            or not previous.edges[key].observed
        )
    )
    return GraphDelta(
        snapshot_id=snapshot_id,
        turn=turn,
        epoch=epoch,
        upsert_nodes=upsert_nodes,
        upsert_edges=upsert_edges,
        remove_edge_keys=remove_edge_keys,
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

    # The projection flips absent world entities to observed=False inside
    # every delta, so the view's observed world set equals the current
    # snapshot's entity set exactly — no turn-equality check needed (content
    # dedup means unchanged nodes keep an older last_observed_turn).
    observed_graph_ids = {
        node.node_id
        for node in graph.nodes.values()
        if node.observed and node.source == WORLD_SOURCE
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
        if edge.observed and edge.source == WORLD_SOURCE
    }
    if observed_edge_keys != expected_edge_keys:
        issues.append("graph_observed_edge_set")
    return tuple(sorted(set(issues)))
