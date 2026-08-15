"""Behavioural tests for belief coverage metrics.

The coverage reducer audits BeliefEngine journals without a live game, so
its contract is: replay any real journal (including one produced by the
engine itself) and report support ratios that match hand counts.
"""

from __future__ import annotations

import json

from civ6_belief_engine.belief_engine import BeliefEngine
from civ6_belief_engine.coverage import (
    load_journal,
    summarize_fleet,
    summarize_game,
)


def created(entity_type: str, entity_id: str, payload=None, *, turn=1, **fields):
    """Build a minimal ``entity.created`` event like the engine emits."""

    entity = {"id": entity_id, "entity_type": entity_type, "status": "active"}
    entity.update(fields)
    entity.update(payload or {})
    return {
        "v": 1,
        "event_type": "entity.created",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "turn": turn,
        "game_id": "test_game",
        "run_id": "test-run",
        "entity": entity,
    }


def updated(entity_type: str, entity_id: str, **fields):
    """Build a minimal ``entity.updated`` event carrying a full snapshot."""

    entity = {"id": entity_id, "entity_type": entity_type, "status": "active"}
    entity.update(fields)
    return {
        "v": 1,
        "event_type": "entity.updated",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "turn": 2,
        "game_id": "test_game",
        "run_id": "test-run",
        "entity": entity,
    }


def test_support_ratio_requires_resolvable_belief_references():
    events = [
        created("belief", "b1"),
        # Tombstoned belief: it existed, so references to it still count.
        created("belief", "b_dead", status="deleted"),
        created("decision", "d1", belief_ids=["b1"]),
        created("decision", "d2", belief_ids=["b_dead"]),
        # Dangling reference: no belief row ever existed for this id.
        created("decision", "d3", belief_ids=["b_ghost"]),
        created("decision", "d4"),
        created("action", "a1"),
        created("action", "a2"),
        created("action", "a3"),
        created("action", "a4"),
    ]
    summary = summarize_game(events)
    assert summary["decisions_total"] == 4
    assert summary["decisions_referencing_beliefs"] == 3
    assert summary["decisions_with_belief_support"] == 2
    assert summary["belief_supported_decision_ratio"] == 0.5
    assert summary["beliefs_total"] == 2
    assert summary["beliefs_active"] == 1
    assert summary["beliefs_per_100_actions"] == 50.0


def test_prediction_resolution_and_calibration_error():
    events = [
        created("prediction", "p1"),
        created("prediction", "p2"),
        updated(
            "prediction",
            "p1",
            status="confirmed",
            prediction_error=0.2,
        ),
        updated(
            "prediction",
            "p2",
            status="disconfirmed",
            prediction_error=0.4,
        ),
    ]
    summary = summarize_game(events)
    assert summary["predictions_total"] == 2
    assert summary["predictions_resolved"] == 2
    assert summary["prediction_resolution_ratio"] == 1.0
    assert summary["prediction_error_mean"] == 0.3


def test_updated_snapshot_wins_and_proposal_linkage_counts():
    events = [
        created("decision", "d1", belief_ids=["b1"]),
        updated("decision", "d1", belief_ids=[]),
        created(
            "decision",
            "d2",
            action_intent={"tool": "unit_action", "proposal_id": "p_x"},
        ),
    ]
    summary = summarize_game(events)
    assert summary["decisions_referencing_beliefs"] == 0
    assert summary["belief_supported_decision_ratio"] == 0.0
    assert summary["decisions_with_proposal"] == 1


def test_empty_and_zero_denominator_summaries_stay_valid():
    summary = summarize_game([created("observation", "o1")])
    assert summary["decisions_total"] == 0
    assert summary["belief_supported_decision_ratio"] is None
    assert summary["beliefs_per_100_actions"] is None
    assert summary["prediction_resolution_ratio"] is None


def test_load_journal_skips_corrupt_lines(tmp_path):
    path = tmp_path / "belief_test_1.jsonl"
    good = created("belief", "b1")
    path.write_text(
        json.dumps(good) + "\n{truncated...\n\n" + json.dumps(good) + "\n",
        encoding="utf-8",
    )
    events, skipped = load_journal(path)
    assert len(events) == 2
    assert skipped == 1


def test_coverage_splits_derived_and_manual_beliefs():
    events = [
        created("belief", "b_derived", tags=["automatic", "derived", "military"]),
        created("belief", "b_manual"),
        created("decision", "d1", belief_ids=["b_derived"]),
        created("decision", "d2", belief_ids=["b_manual"]),
        created(
            "prediction",
            "p_derived",
            tags=["automatic", "derived"],
            status="confirmed",
            prediction_error=0.1,
        ),
    ]
    summary = summarize_game(events)
    assert summary["beliefs_derived"] == 1
    assert summary["beliefs_manual"] == 1
    assert summary["predictions_derived"] == 1
    assert summary["predictions_manual"] == 0
    assert summary["decisions_with_derived_support"] == 1
    assert summary["decisions_with_manual_support"] == 1


def test_fleet_aggregate_recomputes_ratios_from_sums():
    game_a = summarize_game(
        [
            created("belief", "b1"),
            created("decision", "d1", belief_ids=["b1"]),
        ]
    )
    game_b = summarize_game([created("decision", "d2")])
    fleet = summarize_fleet([game_a, game_b])
    assert fleet["games"] == 2
    assert fleet["decisions_total"] == 2
    assert fleet["decisions_with_belief_support"] == 1
    assert fleet["belief_supported_decision_ratio"] == 0.5
    assert fleet["games_with_any_belief"] == 1
    assert fleet["games_with_supported_decision"] == 1


def test_real_engine_journal_round_trip(tmp_path):
    """The reducer must agree with a journal written by the engine itself."""

    engine = BeliefEngine(run_id="coverage-test", directory=tmp_path)
    engine.bind_game("CIVILIZATION_TEST", 42)
    engine.create(
        "belief",
        {
            "statement": "Second city site stays safe until T20.",
            "category": "military",
            "probability": 0.8,
            "confidence": 0.7,
        },
        turn=1,
        entity_id="b1",
    )
    routed = engine.route_decision(
        statement="Settle second city",
        probability=0.8,
        confidence=0.7,
        impact="high",
        urgency="medium",
        irreversibility=0.9,
        turn=2,
        belief_ids=["b1"],
    )
    engine.route_decision(
        statement="Move warrior",
        probability=0.6,
        confidence=0.6,
        impact="low",
        urgency="low",
        irreversibility=0.1,
        turn=2,
    )
    engine.record_tool_result(
        tool="get_game_overview",
        params={},
        result="Turn 2 | Test",
        turn=2,
        category="query",
        success=True,
        duration_ms=5,
    )
    engine.record_tool_result(
        tool="unit_action",
        params={"unit_id": 1, "action": "move"},
        result="OK",
        turn=2,
        category="action",
        success=True,
        duration_ms=10,
    )

    journal = tmp_path / "belief_CIVILIZATION_TEST_42.jsonl"
    events, skipped = load_journal(journal)
    assert skipped == 0
    summary = summarize_game(events)
    assert summary["game_id"] == "CIVILIZATION_TEST_42"
    assert summary["beliefs_total"] == 1
    assert summary["decisions_total"] == 2
    assert summary["decisions_with_belief_support"] == 1
    assert summary["belief_supported_decision_ratio"] == 0.5
    assert summary["entity_counts"]["observation"] >= 1
    assert summary["entity_counts"]["action"] == 1
    # The routed decision must actually carry the belief linkage on disk.
    assert routed["belief_ids"] == ["b1"]
