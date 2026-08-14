"""Immutable materialized graph view with derived adjacency indexes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .model import Edge, EdgeKey, GraphDelta, Node, canonical_json


class GraphInvariantError(ValueError):
    """Raised when a delta would create an invalid current graph."""


@dataclass(frozen=True, slots=True)
class GraphView:
    snapshot_id: str
    turn: int
    epoch: int
    nodes: Mapping[str, Node] = field(default_factory=dict)
    edges: Mapping[EdgeKey, Edge] = field(default_factory=dict)
    _outgoing: Mapping[str, tuple[EdgeKey, ...]] = field(init=False, repr=False)
    _incoming: Mapping[str, tuple[EdgeKey, ...]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self.turn) is not int or self.turn < 0:
            raise ValueError("turn must be a non-negative integer")
        if type(self.epoch) is not int or self.epoch < 1:
            raise ValueError("epoch must be a positive integer")
        nodes = dict(sorted(self.nodes.items()))
        edges = dict(sorted(self.edges.items()))
        for node_id, node in nodes.items():
            if node_id != node.node_id:
                raise GraphInvariantError(f"node key does not match node_id: {node_id}")
            if node.epoch != self.epoch:
                raise GraphInvariantError(f"node belongs to another epoch: {node_id}")
        outgoing: dict[str, list[EdgeKey]] = {}
        incoming: dict[str, list[EdgeKey]] = {}
        for key, edge in edges.items():
            if key != edge.key:
                raise GraphInvariantError(f"edge key does not match edge: {key}")
            if edge.epoch != self.epoch:
                raise GraphInvariantError(f"edge belongs to another epoch: {key}")
            if edge.source_id not in nodes or edge.target_id not in nodes:
                raise GraphInvariantError(
                    f"dangling edge {edge.relation_type}: "
                    f"{edge.source_id} -> {edge.target_id}"
                )
            outgoing.setdefault(edge.source_id, []).append(key)
            incoming.setdefault(edge.target_id, []).append(key)
        object.__setattr__(self, "nodes", MappingProxyType(nodes))
        object.__setattr__(self, "edges", MappingProxyType(edges))
        object.__setattr__(
            self,
            "_outgoing",
            MappingProxyType({node_id: tuple(sorted(keys)) for node_id, keys in outgoing.items()}),
        )
        object.__setattr__(
            self,
            "_incoming",
            MappingProxyType({node_id: tuple(sorted(keys)) for node_id, keys in incoming.items()}),
        )

    @classmethod
    def empty(cls, *, epoch: int = 1, turn: int = 0) -> GraphView:
        return cls(snapshot_id="", turn=turn, epoch=epoch)

    def apply(self, delta: GraphDelta) -> GraphView:
        if delta.epoch < self.epoch:
            raise GraphInvariantError(
                f"cannot apply epoch {delta.epoch} to current epoch {self.epoch}"
            )
        if delta.epoch == self.epoch and self.snapshot_id and delta.turn < self.turn:
            raise GraphInvariantError(
                f"cannot apply turn {delta.turn} after current turn {self.turn}"
            )
        if delta.epoch > self.epoch:
            nodes: dict[str, Node] = {}
            edges: dict[EdgeKey, Edge] = {}
        else:
            nodes = dict(self.nodes)
            edges = dict(self.edges)
        for node_id in delta.remove_node_ids:
            nodes.pop(node_id, None)
            edges = {
                key: edge
                for key, edge in edges.items()
                if edge.source_id != node_id and edge.target_id != node_id
            }
        for key in delta.remove_edge_keys:
            edges.pop(key, None)
        for node in delta.upsert_nodes:
            nodes[node.node_id] = node
        for edge in delta.upsert_edges:
            edges[edge.key] = edge
        return GraphView(
            snapshot_id=delta.snapshot_id,
            turn=delta.turn,
            epoch=delta.epoch,
            nodes=nodes,
            edges=edges,
        )

    def node(self, node_id: str) -> Node | None:
        return self.nodes.get(node_id)

    def nodes_of_type(self, node_type: str, *, observed_only: bool = False) -> tuple[Node, ...]:
        return tuple(
            node
            for node in self.nodes.values()
            if node.node_type == node_type and (node.observed or not observed_only)
        )

    def edges_from(self, node_id: str, relation_type: str | None = None) -> tuple[Edge, ...]:
        relation = relation_type.upper() if relation_type else None
        return tuple(
            self.edges[key]
            for key in self._outgoing.get(node_id, ())
            if relation is None or key[0] == relation
        )

    def edges_to(self, node_id: str, relation_type: str | None = None) -> tuple[Edge, ...]:
        relation = relation_type.upper() if relation_type else None
        return tuple(
            self.edges[key]
            for key in self._incoming.get(node_id, ())
            if relation is None or key[0] == relation
        )

    def threats_near_city(
        self,
        city_id: str,
        *,
        max_distance: int = 3,
        include_stale: bool = False,
    ) -> tuple[Edge, ...]:
        """Return visible hostile-unit edges near one city."""

        if type(max_distance) is not int or max_distance < 0:
            raise ValueError("max_distance must be a non-negative integer")
        city = self.node(city_id)
        if city is None or city.node_type != "city":
            return ()
        threats: list[Edge] = []
        for edge in self.edges_to(city_id, "THREATENS"):
            source = self.node(edge.source_id)
            distance = edge.attributes.get("distance")
            if type(distance) is not int or distance > max_distance:
                continue
            if not include_stale and (
                not edge.observed or source is None or not source.observed
            ):
                continue
            threats.append(edge)
        return tuple(
            sorted(
                threats,
                key=lambda edge: (
                    edge.attributes["distance"],
                    edge.source_id,
                ),
            )
        )

    @property
    def state_hash(self) -> str:
        payload = {
            "snapshot_id": self.snapshot_id,
            "turn": self.turn,
            "epoch": self.epoch,
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges.values()],
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
