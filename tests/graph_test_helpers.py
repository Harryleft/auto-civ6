"""Small explicit graph fixtures for department tests.

These helpers intentionally mirror the production graph contract instead of
teaching DepartmentContext to recover typed snapshot fields without a graph.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from civ6_belief_engine.graph import Edge, GraphView, Node
from civ6_belief_engine.governance import GraphSnapshotView


def _record(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return value


def graph_for_snapshot(
    snapshot: GraphSnapshotView,
    *,
    goals: tuple[Any, ...] = (),
) -> GraphView:
    """Project a department fixture into a complete same-turn GraphView."""

    turn = snapshot.turn
    nodes: list[Node] = []
    edges: list[Edge] = []

    def add_node(node_id: str, node_type: str, value: Any, **extra: Any) -> None:
        source = extra.pop("_source", "game_state:typed_snapshot")
        attributes = _record(value) or {}
        if not isinstance(attributes, dict):
            attributes = {"value": attributes}
        attributes.update(extra)
        nodes.append(Node(node_id, node_type, attributes, last_observed_turn=turn, source=source))

    def relate(relation: str, source: str, target: str, attributes: dict[str, Any] | None = None) -> None:
        edges.append(Edge(relation, source, target, attributes or {}, last_observed_turn=turn))

    add_node(f"player:{snapshot.player_id}", "player", snapshot.overview)
    player_id = f"player:{snapshot.player_id}"
    player = nodes[-1]
    player_attrs = dict(player.attributes)
    if snapshot.tech_civic is not None:
        player_attrs["tech_civic"] = _record(snapshot.tech_civic)
        player_attrs["tech_civic_available"] = True
    if snapshot.barbarians is not None:
        player_attrs["barbarian_overview_available"] = True
    if snapshot.great_people is not None:
        player_attrs["great_people_available"] = True
    player_attrs["threat_scan_available"] = True if snapshot.overview is not None else snapshot.threat_scan_available
    nodes[-1] = Node(player.node_id, player.node_type, player_attrs, last_observed_turn=turn)

    for index, city in enumerate(snapshot.cities):
        value = _record(city)
        city_id = f"city:{getattr(city, 'city_id', index)}"
        add_node(city_id, "city", value)
        relate("OWNS", player_id, city_id)
    for index, unit in enumerate(snapshot.units):
        value = _record(unit)
        unit_id = f"unit:{getattr(unit, 'unit_id', index)}"
        add_node(unit_id, "unit", value)
        relate("OWNS", player_id, unit_id)
    for index, resource in enumerate(snapshot.resources):
        resource_id = f"resource:{getattr(resource, 'name', index)}"
        add_node(resource_id, "resource_stockpile", resource)
        relate("OWNS", player_id, resource_id)
    for index, civ in enumerate(snapshot.diplomacy):
        civ_id = f"player:{getattr(civ, 'player_id', index)}"
        add_node(civ_id, "player", civ)
        relate("DIPLOMACY_WITH", player_id, civ_id)
    if snapshot.policies is not None:
        government_id = "government:current"
        add_node(government_id, "government", snapshot.policies)
        relate("USES_GOVERNMENT", player_id, government_id)
        for index, slot in enumerate(getattr(snapshot.policies, "slots", ()) or ()):
            slot_id = f"policy_slot:{index}"
            add_node(slot_id, "policy_slot", slot)
            relate("HAS_POLICY_SLOT", government_id, slot_id)
    if snapshot.barbarians is not None:
        for index, camp in enumerate(snapshot.barbarians.camps):
            add_node(f"barbarian_camp:{index}", "barbarian_camp", camp)
        for index, unit in enumerate(snapshot.barbarians.units):
            add_node(f"unit:barbarian:{getattr(unit, 'unit_id', index)}", "unit", unit)
    if snapshot.great_people is not None:
        for index, standing in enumerate(snapshot.great_people.standings):
            add_node(f"great_person_class:{index}", "great_person_class", standing)
    city_ids = {
        getattr(city, "city_id", index): f"city:{getattr(city, 'city_id', index)}"
        for index, city in enumerate(snapshot.cities)
    }
    for index, threat in enumerate(snapshot.threats):
        unit_id = getattr(threat, "unit_id", index)
        source_id = f"unit:threat:{unit_id}"
        add_node(source_id, "unit", threat)
        city_id = city_ids.get(getattr(threat, "nearest_city_id", -1))
        if city_id is not None:
            relate(
                "THREATENS",
                source_id,
                city_id,
                {
                    "distance": getattr(threat, "distance_to_city", 999),
                    "is_at_war": getattr(threat, "is_at_war", False),
                },
            )
    for index, goal in enumerate(goals):
        value = _record(goal)
        if not isinstance(value, dict):
            value = {"statement": str(value)}
        value.setdefault("goal_id", f"goal:{index}")
        value.setdefault("priority", 0)
        add_node(f"goal:{value['goal_id']}", "goal", value, _source="belief_engine:goal")

    return GraphView(snapshot.snapshot_id, turn, 1, {node.node_id: node for node in nodes}, {edge.key: edge for edge in edges})
