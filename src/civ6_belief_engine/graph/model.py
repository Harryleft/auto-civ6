"""Small immutable contracts for the derived decision graph."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias


JsonMapping: TypeAlias = Mapping[str, Any]
EdgeKey: TypeAlias = tuple[str, str, str]


class Coverage(StrEnum):
    """What absence from the next typed snapshot means."""

    COMPLETE = "complete"
    CURRENTLY_VISIBLE = "currently_visible"
    KNOWN_HISTORY = "known_history"
    SUMMARY = "summary"


def _nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def freeze_json(value: Any) -> Any:
    """Detach and recursively freeze a JSON-compatible value."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): freeze_json(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        )
    if isinstance(value, (tuple, list)):
        return tuple(freeze_json(item) for item in value)
    if isinstance(value, (set, frozenset)):
        frozen = (freeze_json(item) for item in value)
        return tuple(sorted(frozen, key=lambda item: canonical_json(item)))
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("graph values must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported graph value type: {type(value).__name__}")


def thaw_json(value: Any) -> Any:
    """Return a normal JSON-serializable copy of a frozen value."""

    if isinstance(value, Mapping):
        return {str(key): thaw_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw_json(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported frozen graph value type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        thaw_json(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _edge_key(value: Any) -> EdgeKey:
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 3
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError("edge key must contain three non-empty strings")
    return (value[0].upper(), value[1].strip(), value[2].strip())


@dataclass(frozen=True, slots=True)
class Node:
    node_id: str
    node_type: str
    attributes: JsonMapping = field(default_factory=dict)
    epoch: int = 1
    first_observed_turn: int = 0
    last_observed_turn: int = 0
    source: str = "game_state:typed_snapshot"
    coverage: Coverage = Coverage.COMPLETE
    observed: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", _nonempty(self.node_id, "node_id"))
        object.__setattr__(self, "node_type", _nonempty(self.node_type, "node_type"))
        object.__setattr__(self, "source", _nonempty(self.source, "source"))
        if type(self.epoch) is not int or self.epoch < 1:
            raise ValueError("epoch must be a positive integer")
        if type(self.first_observed_turn) is not int or self.first_observed_turn < 0:
            raise ValueError("first_observed_turn must be a non-negative integer")
        if type(self.last_observed_turn) is not int or self.last_observed_turn < 0:
            raise ValueError("last_observed_turn must be a non-negative integer")
        if self.last_observed_turn < self.first_observed_turn:
            raise ValueError("last_observed_turn cannot precede first_observed_turn")
        if type(self.observed) is not bool:
            raise TypeError("observed must be a bool")
        object.__setattr__(self, "coverage", Coverage(self.coverage))
        object.__setattr__(self, "attributes", freeze_json(self.attributes))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "attributes": thaw_json(self.attributes),
            "epoch": self.epoch,
            "first_observed_turn": self.first_observed_turn,
            "last_observed_turn": self.last_observed_turn,
            "source": self.source,
            "coverage": self.coverage.value,
            "observed": self.observed,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Node:
        return cls(
            node_id=str(payload["node_id"]),
            node_type=str(payload["node_type"]),
            attributes=payload.get("attributes") or {},
            epoch=payload.get("epoch", 1),
            first_observed_turn=payload.get("first_observed_turn", 0),
            last_observed_turn=payload.get("last_observed_turn", 0),
            source=str(payload.get("source") or "game_state:typed_snapshot"),
            coverage=Coverage(payload.get("coverage", Coverage.COMPLETE)),
            observed=payload.get("observed", True),
        )


@dataclass(frozen=True, slots=True)
class Edge:
    relation_type: str
    source_id: str
    target_id: str
    attributes: JsonMapping = field(default_factory=dict)
    epoch: int = 1
    valid_from_turn: int = 0
    last_observed_turn: int = 0
    source: str = "game_state:typed_snapshot"
    coverage: Coverage = Coverage.COMPLETE
    observed: bool = True

    def __post_init__(self) -> None:
        relation_type = _nonempty(self.relation_type, "relation_type").upper()
        object.__setattr__(self, "relation_type", relation_type)
        object.__setattr__(self, "source_id", _nonempty(self.source_id, "source_id"))
        object.__setattr__(self, "target_id", _nonempty(self.target_id, "target_id"))
        object.__setattr__(self, "source", _nonempty(self.source, "source"))
        if type(self.epoch) is not int or self.epoch < 1:
            raise ValueError("epoch must be a positive integer")
        if type(self.valid_from_turn) is not int or self.valid_from_turn < 0:
            raise ValueError("valid_from_turn must be a non-negative integer")
        if type(self.last_observed_turn) is not int or self.last_observed_turn < 0:
            raise ValueError("last_observed_turn must be a non-negative integer")
        if self.last_observed_turn < self.valid_from_turn:
            raise ValueError("last_observed_turn cannot precede valid_from_turn")
        if type(self.observed) is not bool:
            raise TypeError("observed must be a bool")
        object.__setattr__(self, "coverage", Coverage(self.coverage))
        object.__setattr__(self, "attributes", freeze_json(self.attributes))

    @property
    def key(self) -> EdgeKey:
        return (self.relation_type, self.source_id, self.target_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_type": self.relation_type,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "attributes": thaw_json(self.attributes),
            "epoch": self.epoch,
            "valid_from_turn": self.valid_from_turn,
            "last_observed_turn": self.last_observed_turn,
            "source": self.source,
            "coverage": self.coverage.value,
            "observed": self.observed,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Edge:
        return cls(
            relation_type=str(payload["relation_type"]),
            source_id=str(payload["source_id"]),
            target_id=str(payload["target_id"]),
            attributes=payload.get("attributes") or {},
            epoch=payload.get("epoch", 1),
            valid_from_turn=payload.get("valid_from_turn", 0),
            last_observed_turn=payload.get("last_observed_turn", 0),
            source=str(payload.get("source") or "game_state:typed_snapshot"),
            coverage=Coverage(payload.get("coverage", Coverage.COMPLETE)),
            observed=payload.get("observed", True),
        )


@dataclass(frozen=True, slots=True)
class GraphDelta:
    snapshot_id: str
    turn: int
    epoch: int
    upsert_nodes: tuple[Node, ...] = ()
    upsert_edges: tuple[Edge, ...] = ()
    remove_node_ids: tuple[str, ...] = ()
    remove_edge_keys: tuple[EdgeKey, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", _nonempty(self.snapshot_id, "snapshot_id"))
        if type(self.turn) is not int or self.turn < 0:
            raise ValueError("turn must be a non-negative integer")
        if type(self.epoch) is not int or self.epoch < 1:
            raise ValueError("epoch must be a positive integer")
        nodes = tuple(sorted(self.upsert_nodes, key=lambda node: node.node_id))
        edges = tuple(sorted(self.upsert_edges, key=lambda edge: edge.key))
        remove_nodes = tuple(
            sorted(_nonempty(item, "remove_node_id") for item in self.remove_node_ids)
        )
        remove_edges = tuple(sorted(_edge_key(item) for item in self.remove_edge_keys))
        if len({node.node_id for node in nodes}) != len(nodes):
            raise ValueError("upsert_nodes contains duplicate node IDs")
        if len({edge.key for edge in edges}) != len(edges):
            raise ValueError("upsert_edges contains duplicate edge keys")
        if len(set(remove_nodes)) != len(remove_nodes):
            raise ValueError("remove_node_ids contains duplicates")
        if len(set(remove_edges)) != len(remove_edges):
            raise ValueError("remove_edge_keys contains duplicates")
        if {node.node_id for node in nodes} & set(remove_nodes):
            raise ValueError("a node cannot be upserted and removed in one delta")
        if {edge.key for edge in edges} & set(remove_edges):
            raise ValueError("an edge cannot be upserted and removed in one delta")
        if any(node.epoch != self.epoch for node in nodes):
            raise ValueError("all nodes must belong to the delta epoch")
        if any(edge.epoch != self.epoch for edge in edges):
            raise ValueError("all edges must belong to the delta epoch")
        object.__setattr__(self, "upsert_nodes", nodes)
        object.__setattr__(self, "upsert_edges", edges)
        object.__setattr__(self, "remove_node_ids", remove_nodes)
        object.__setattr__(self, "remove_edge_keys", remove_edges)

    @property
    def digest(self) -> str:
        encoded = canonical_json(self.to_dict()).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "turn": self.turn,
            "epoch": self.epoch,
            "upsert_nodes": [node.to_dict() for node in self.upsert_nodes],
            "upsert_edges": [edge.to_dict() for edge in self.upsert_edges],
            "remove_node_ids": list(self.remove_node_ids),
            "remove_edge_keys": [list(key) for key in self.remove_edge_keys],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> GraphDelta:
        return cls(
            snapshot_id=str(payload["snapshot_id"]),
            turn=payload["turn"],
            epoch=payload["epoch"],
            upsert_nodes=tuple(Node.from_dict(item) for item in payload.get("upsert_nodes", ())),
            upsert_edges=tuple(Edge.from_dict(item) for item in payload.get("upsert_edges", ())),
            remove_node_ids=tuple(payload.get("remove_node_ids", ())),
            remove_edge_keys=tuple(_edge_key(item) for item in payload.get("remove_edge_keys", ())),
        )
