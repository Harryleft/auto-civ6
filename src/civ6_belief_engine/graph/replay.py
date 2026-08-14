"""Replay graph deltas from memory or the existing JSONL event envelope."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .model import GraphDelta
from .view import GraphInvariantError, GraphView


GRAPH_DELTA_EVENT = "graph.delta"


class GraphReplayError(ValueError):
    """Raised when persisted graph events do not reproduce their recorded state."""


def replay_deltas(deltas: Iterable[GraphDelta]) -> GraphView:
    view: GraphView | None = None
    for delta in deltas:
        if view is None:
            view = GraphView.empty(epoch=delta.epoch, turn=delta.turn)
        view = view.apply(delta)
    return view or GraphView.empty()


def replay_graph_events(
    events: Iterable[Mapping[str, Any]],
    *,
    epoch: int | None = None,
) -> GraphView:
    deltas: list[GraphDelta] = []
    expected_hashes: list[str | None] = []
    for event in events:
        if event.get("event_type") != GRAPH_DELTA_EVENT:
            continue
        event_epoch = event.get("epoch", 1)
        if epoch is not None and event_epoch != epoch:
            continue
        entity = event.get("entity")
        if not isinstance(entity, Mapping) or not isinstance(entity.get("delta"), Mapping):
            raise GraphReplayError("graph.delta event is missing its delta payload")
        try:
            deltas.append(GraphDelta.from_dict(entity["delta"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise GraphReplayError(f"invalid graph.delta payload: {exc}") from exc
        state_hash = entity.get("state_hash")
        expected_hashes.append(str(state_hash) if state_hash else None)

    view: GraphView | None = None
    for delta, expected_hash in zip(deltas, expected_hashes, strict=True):
        if view is None:
            view = GraphView.empty(epoch=delta.epoch, turn=delta.turn)
        try:
            view = view.apply(delta)
        except GraphInvariantError as exc:
            raise GraphReplayError(str(exc)) from exc
        if expected_hash is not None and view.state_hash != expected_hash:
            raise GraphReplayError(
                f"graph state hash mismatch after snapshot {delta.snapshot_id}"
            )
    if view is not None:
        return view
    return GraphView.empty(epoch=epoch or 1)
