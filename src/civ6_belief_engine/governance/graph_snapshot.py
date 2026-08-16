"""Canonical current-state views assembled from ``GraphView``.

Departments consume this module instead of reaching into the adapter-shaped
``TurnSnapshot`` fields.  The legacy constructor exists only for old unit
callers that do not provide a graph; the production coordinator supplies the
same-turn materialized graph.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any, Mapping

from ..graph import GraphView, Node


def _record(value: Any) -> Any:
    """Convert frozen graph mappings into attribute-readable domain records."""

    if isinstance(value, Mapping):
        return SimpleNamespace(
            **{str(key): _record(item) for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_record(item) for item in value)
    return value


def _merge_record(*values: Mapping[str, Any]) -> Any:
    merged: dict[str, Any] = {}
    for value in values:
        merged.update(value)
    return _record(merged)


def _node_record(node: Node) -> Any:
    return _merge_record(node.attributes)


def _observed_node(graph: GraphView, node_id: str) -> Node | None:
    node = graph.node(node_id)
    return node if node is not None and node.observed else None


@dataclass(frozen=True, slots=True)
class GraphSnapshotView:
    """Stable domain records required by the strategy departments."""

    snapshot_id: str
    turn: int
    player_id: int
    ready: bool
    source: str
    overview: Any | None = None
    cities: tuple[Any, ...] = ()
    units: tuple[Any, ...] = ()
    diplomacy: tuple[Any, ...] = ()
    tech_civic: Any | None = None
    resources: tuple[Any, ...] = ()
    policies: Any | None = None
    barbarians: Any | None = None
    great_people: Any | None = None
    threats: tuple[Any, ...] = ()
    threat_scan_available: bool = False

    @classmethod
    def from_context(cls, context: Any) -> GraphSnapshotView:
        graph = getattr(context, "graph", None)
        snapshot = context.snapshot
        if graph is None:
            return cls.from_legacy(snapshot)
        view = cls.from_graph(
            graph,
            snapshot_id=snapshot.snapshot_id,
            turn=snapshot.turn,
            player_id=snapshot.player_id,
        )
        if view.ready:
            return view
        # The typed snapshot is still the current adapter input when a
        # derived graph is stale. Keep current non-graph facts available for
        # conservative assessment, while ``ready=False`` prevents graph-only
        # threats/goals from influencing decisions.
        return replace(cls.from_legacy(snapshot), ready=False, source=view.source)

    @classmethod
    def from_legacy(cls, snapshot: Any) -> GraphSnapshotView:
        """Compatibility-only view for tests and callers before graph wiring."""

        return cls(
            snapshot_id=snapshot.snapshot_id,
            turn=snapshot.turn,
            player_id=snapshot.player_id,
            ready=True,
            source="legacy_compat",
            overview=snapshot.overview,
            cities=tuple(snapshot.cities),
            units=tuple(snapshot.units),
            diplomacy=tuple(snapshot.diplomacy),
            tech_civic=snapshot.tech_civic,
            resources=tuple(snapshot.resources),
            policies=snapshot.policies,
            barbarians=snapshot.barbarians,
            great_people=snapshot.great_people,
            threats=tuple(snapshot.threats),
            threat_scan_available=snapshot.threat_scan_available,
        )

    @classmethod
    def from_graph(
        cls,
        graph: GraphView,
        *,
        snapshot_id: str,
        turn: int,
        player_id: int,
    ) -> GraphSnapshotView:
        if graph.snapshot_id != snapshot_id or graph.turn != turn:
            return cls(
                snapshot_id=snapshot_id,
                turn=turn,
                player_id=player_id,
                ready=False,
                source="graph_stale",
            )

        player_node_id = f"player:{player_id}"
        player = _observed_node(graph, player_node_id)
        if player is None:
            return cls(
                snapshot_id=snapshot_id,
                turn=turn,
                player_id=player_id,
                ready=False,
                source="graph_missing_player",
            )
        player_attributes = dict(player.attributes)

        def related_nodes(relation: str, node_type: str) -> tuple[Node, ...]:
            nodes: list[Node] = []
            for edge in graph.edges_from(player_node_id, relation):
                if not edge.observed:
                    continue
                node = _observed_node(graph, edge.target_id)
                if node is not None and node.node_type == node_type:
                    nodes.append(node)
            return tuple(sorted(nodes, key=lambda node: node.node_id))

        cities = tuple(_node_record(node) for node in related_nodes("OWNS", "city"))
        units = tuple(_node_record(node) for node in related_nodes("OWNS", "unit"))
        resources = tuple(
            _node_record(node)
            for node in related_nodes("OWNS", "resource_stockpile")
        )

        diplomacy: list[Any] = []
        for edge in graph.edges_from(player_node_id, "DIPLOMACY_WITH"):
            if not edge.observed:
                continue
            rival = _observed_node(graph, edge.target_id)
            if rival is None:
                continue
            attributes = {**dict(rival.attributes), **dict(edge.attributes)}
            rival_id = attributes.get("player_id")
            if type(rival_id) is not int:
                suffix = rival.node_id.rsplit(":", 1)[-1]
                if not suffix.isdigit():
                    continue
                attributes["player_id"] = int(suffix)
            complete = (
                type(attributes.get("has_met")) is bool
                and type(attributes.get("is_at_war")) is bool
                and isinstance(attributes.get("diplomatic_state"), str)
                and type(attributes.get("relationship_score")) is int
                and type(attributes.get("military_strength")) is int
            )
            if not complete:
                attributes.update(
                    {
                        "has_met": False,
                        "is_at_war": False,
                        "diplomatic_state": "UNKNOWN",
                        "relationship_score": 0,
                        "military_strength": 0,
                    }
                )
            diplomacy.append(_record(attributes))
        diplomacy.sort(
            key=lambda civ: (
                getattr(civ, "player_id", -1),
                getattr(civ, "civ_name", ""),
                getattr(civ, "leader_name", ""),
            )
        )

        tech_civic = _record(player_attributes.get("tech_civic"))
        government_nodes = related_nodes("USES_GOVERNMENT", "government")
        policies = None
        if government_nodes:
            government = government_nodes[0]
            slots = tuple(
                _node_record(node)
                for edge in graph.edges_from(government.node_id, "HAS_POLICY_SLOT")
                if edge.observed
                for node in (_observed_node(graph, edge.target_id),)
                if node is not None and node.node_type == "policy_slot"
            )
            government_attributes = dict(government.attributes)
            government_attributes["slots"] = slots
            policies = _record(government_attributes)

        barbarian_available = player_attributes.get(
            "barbarian_overview_available", False
        )
        barbarians = None
        if type(barbarian_available) is bool and barbarian_available:
            camps = tuple(
                _node_record(node)
                for node in graph.nodes_of_type("barbarian_camp", observed_only=True)
            )
            barbarian_units = tuple(
                _node_record(node)
                for node in graph.nodes_of_type("unit", observed_only=True)
                if node.node_id.startswith("unit:barbarian:")
            )
            barbarians = SimpleNamespace(camps=camps, units=barbarian_units)

        great_people_available = player_attributes.get(
            "great_people_available", False
        )
        great_people = None
        if type(great_people_available) is bool and great_people_available:
            standings = tuple(
                _node_record(node)
                for node in graph.nodes_of_type("great_person_class", observed_only=True)
            )
            great_people = SimpleNamespace(standings=standings)

        threats: list[Any] = []
        for edge in graph.edges.values():
            if edge.relation_type != "THREATENS" or not edge.observed:
                continue
            source = _observed_node(graph, edge.source_id)
            target = _observed_node(graph, edge.target_id)
            if source is None or target is None:
                continue
            threats.append(_merge_record(source.attributes, edge.attributes))

        return cls(
            snapshot_id=snapshot_id,
            turn=turn,
            player_id=player_id,
            ready=True,
            source="graph",
            overview=_node_record(player),
            cities=cities,
            units=units,
            diplomacy=tuple(diplomacy),
            tech_civic=tech_civic,
            resources=resources,
            policies=policies,
            barbarians=barbarians,
            great_people=great_people,
            threats=tuple(threats),
            threat_scan_available=(
                player_attributes.get("threat_scan_available") is True
            ),
        )


def graph_snapshot(context: Any) -> GraphSnapshotView:
    """Build one same-turn view for a department invocation."""

    return GraphSnapshotView.from_context(context)


def graph_goals(context: Any) -> tuple[Any, ...]:
    """Return active goals from the same-turn graph, or legacy goals without a graph."""

    graph = getattr(context, "graph", None)
    if graph is None:
        return tuple(getattr(context, "goals", ()) or ())
    snapshot = context.snapshot
    if graph.snapshot_id != snapshot.snapshot_id or graph.turn != snapshot.turn:
        return ()
    goals: list[Any] = []
    for node in graph.active_goals():
        attributes = dict(node.attributes)
        attributes.setdefault("goal_id", node.node_id.removeprefix("goal:"))
        attributes.setdefault("tags", ())
        goals.append(_record(attributes))
    return tuple(goals)


__all__ = ["GraphSnapshotView", "graph_goals", "graph_snapshot"]
