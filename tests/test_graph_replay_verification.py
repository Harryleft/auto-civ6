"""Replay verification modes and their integrity trade-off.

``GraphView.state_hash`` serializes the entire node/edge set, so checking it
after every delta costs O(deltas x view size). On the real 110-turn journal
that was 148 comparisons costing 46.7s of a 48.4s load. The default now checks
only the newest recorded checkpoint; these tests pin both the equivalence on a
valid stream and the exact difference in detection power.
"""

from __future__ import annotations

import pytest

from civ6_belief_engine.graph import (
    GRAPH_DELTA_EVENT,
    GraphDelta,
    GraphReplayError,
    GraphView,
    Node,
    replay_deltas,
    replay_graph_events,
)


def _delta(snapshot_id: str, turn: int, node_id: str) -> GraphDelta:
    return GraphDelta(
        snapshot_id=snapshot_id,
        turn=turn,
        epoch=1,
        upsert_nodes=(
            Node(node_id, "player", {"turn": turn}, last_observed_turn=turn),
        ),
    )


def _persisted_events(deltas: list[GraphDelta]) -> list[dict]:
    """Mirror belief_engine.record_graph_delta: each event pins its own view hash."""

    events: list[dict] = []
    view = GraphView.empty()
    for delta in deltas:
        view = view.apply(delta)
        events.append(
            {
                "event_type": GRAPH_DELTA_EVENT,
                "epoch": delta.epoch,
                "entity": {"delta": delta.to_dict(), "state_hash": view.state_hash},
            }
        )
    return events


@pytest.fixture
def deltas() -> list[GraphDelta]:
    return [
        _delta("snapshot:1", 1, "player:0"),
        _delta("snapshot:2", 2, "player:1"),
        _delta("snapshot:3", 3, "player:2"),
    ]


def test_every_verification_mode_rebuilds_the_same_view(deltas):
    events = _persisted_events(deltas)
    expected = replay_deltas(deltas)

    for mode in ("final", "all", "none"):
        replayed = replay_graph_events(events, verify=mode)
        assert replayed.state_hash == expected.state_hash, mode
        assert set(replayed.nodes) == {"player:0", "player:1", "player:2"}


def test_final_mode_detects_a_corrupted_last_checkpoint(deltas):
    events = _persisted_events(deltas)
    events[-1]["entity"]["state_hash"] = "0" * 64

    with pytest.raises(GraphReplayError, match="state hash mismatch"):
        replay_graph_events(events)
    with pytest.raises(GraphReplayError, match="state hash mismatch"):
        replay_graph_events(events, verify="all")


def test_final_mode_skips_earlier_checkpoints_that_all_mode_checks(deltas):
    """Documents the deliberate reduction: only the newest checkpoint is pinned.

    A mid-stream hash mismatch that the following deltas overwrite is invisible
    to ``final``. That is the cost of one hash computation instead of one per
    delta; ``all`` remains available for diagnosis.
    """

    events = _persisted_events(deltas)
    events[0]["entity"]["state_hash"] = "0" * 64

    assert replay_graph_events(events, verify="final") is not None
    with pytest.raises(GraphReplayError, match="snapshot:1"):
        replay_graph_events(events, verify="all")


def test_none_mode_skips_hash_checks_but_still_rejects_bad_payloads(deltas):
    events = _persisted_events(deltas)
    events[-1]["entity"]["state_hash"] = "0" * 64

    replayed = replay_graph_events(events, verify="none")
    assert set(replayed.nodes) == {"player:0", "player:1", "player:2"}

    events[-1]["entity"] = {"state_hash": "deadbeef"}
    with pytest.raises(GraphReplayError, match="missing its delta payload"):
        replay_graph_events(events, verify="none")


def test_events_without_a_recorded_hash_are_tolerated(deltas):
    events = _persisted_events(deltas)
    for event in events:
        event["entity"]["state_hash"] = None

    replayed = replay_graph_events(events)
    assert set(replayed.nodes) == {"player:0", "player:1", "player:2"}


def test_epoch_filter_still_applies_with_final_verification(deltas):
    events = _persisted_events(deltas)
    for event in events:
        event["epoch"] = 1
    later = _delta("snapshot:9", 9, "player:9")
    events.append(
        {
            "event_type": GRAPH_DELTA_EVENT,
            "epoch": 2,
            "entity": {"delta": later.to_dict(), "state_hash": None},
        }
    )

    assert set(replay_graph_events(events, epoch=1).nodes) == {
        "player:0",
        "player:1",
        "player:2",
    }
    assert set(replay_graph_events(events, epoch=2).nodes) == {"player:9"}
