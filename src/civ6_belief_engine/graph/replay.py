"""Replay graph deltas from memory or the existing JSONL event envelope."""

from __future__ import annotations

from typing import Any, Iterable, Literal, Mapping

from .model import GraphDelta
from .view import GraphInvariantError, GraphView


GRAPH_DELTA_EVENT = "graph.delta"

# ``GraphView.state_hash`` serializes the whole node/edge set, so comparing it
# after every delta costs O(deltas x view size). On a real 110-turn journal
# (420 deltas, 4,269 nodes / 5,854 edges) that was 148 comparisons taking
# 46.7s of a 48.4s load. Checking only the final recorded checkpoint verifies
# the same end state for one comparison; ``all`` restores the stricter mode
# for callers that need to locate the exact diverging delta.
ReplayVerification = Literal["final", "all", "none"]


class GraphReplayError(ValueError):
    """Raised when persisted graph events do not reproduce their recorded state."""


def replay_deltas(deltas: Iterable[GraphDelta]) -> GraphView:
    view: GraphView | None = None
    for delta in deltas:
        if view is None:
            view = GraphView.empty(epoch=delta.epoch, turn=delta.turn)
        view = view.apply(delta)
    return view or GraphView.empty()


def _verified_indices(
    expected_hashes: list[str | None],
    verify: ReplayVerification,
) -> set[int]:
    if verify == "none":
        return set()
    checkpoints = [
        index for index, expected in enumerate(expected_hashes) if expected is not None
    ]
    if verify == "all":
        return set(checkpoints)
    # "final": the last delta that recorded a hash pins the end state.
    return {checkpoints[-1]} if checkpoints else set()


def replay_graph_events(
    events: Iterable[Mapping[str, Any]],
    *,
    epoch: int | None = None,
    verify: ReplayVerification = "final",
) -> GraphView:
    """Rebuild the graph view from persisted ``graph.delta`` events.

    ``verify`` selects how much of the recorded ``state_hash`` chain is
    re-checked while replaying:

    ``final`` (default)
        Re-check the newest recorded checkpoint only. Detects a replay that
        diverges from what was persisted, at one hash computation.
    ``all``
        Re-check every recorded checkpoint. Locates the exact diverging delta
        at O(deltas x view size); use it for diagnosis, not on the load path.
    ``none``
        Skip hash verification entirely. Structural errors
        (:class:`GraphInvariantError`) are still raised.
    """

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

    check_at = _verified_indices(expected_hashes, verify)
    view: GraphView | None = None
    for index, delta in enumerate(deltas):
        if view is None:
            view = GraphView.empty(epoch=delta.epoch, turn=delta.turn)
        try:
            view = view.apply(delta)
        except GraphInvariantError as exc:
            raise GraphReplayError(str(exc)) from exc
        if index in check_at and view.state_hash != expected_hashes[index]:
            raise GraphReplayError(
                f"graph state hash mismatch after snapshot {delta.snapshot_id}"
            )
    if view is not None:
        return view
    return GraphView.empty(epoch=epoch or 1)
