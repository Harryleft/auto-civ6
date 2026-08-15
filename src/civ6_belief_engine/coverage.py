"""Belief coverage metrics over BeliefEngine event journals.

Audit question: do recorded beliefs actually support recorded decisions?
The pipeline auto-records observations, actions, and a routed decision for
every gated tool call, but beliefs and predictions only exist when someone
explicitly wrote them. These metrics turn that asymmetry into a per-game
number instead of a marketing claim.

The reducer is deliberately independent of :class:`BeliefEngine`: journals
are replayed with last-write-wins on full entity snapshots (both
``entity.created`` and ``entity.updated`` carry the complete entity), so an
audit never mutates game state and works on archived journals.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

# Tombstones keep the entity row with a non-active status; predictions are
# resolved when their status flips to confirmed/disconfirmed.
_RESOLVED_PREDICTION_STATUSES = frozenset({"confirmed", "disconfirmed"})
_ACTIVE_STATUSES = frozenset({"active"})


def load_journal(path: str | Path) -> tuple[list[dict[str, Any]], int]:
    """Read a belief JSONL journal, tolerating a truncated final line.

    Returns the parsed events and the number of skipped (corrupt) lines —
    the same quarantine policy the engine applies at load time.
    """

    events: list[dict[str, Any]] = []
    skipped = 0
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if isinstance(event, dict):
                events.append(event)
            else:
                skipped += 1
    return events, skipped


def _reduce_entities(
    events: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Replay entity snapshots to their final state, last write wins."""

    entities: dict[str, dict[str, dict[str, Any]]] = {}
    for event in events:
        entity = event.get("entity")
        entity_type = event.get("entity_type")
        if entity_type is None or not isinstance(entity, dict):
            continue
        entity_id = entity.get("id") or event.get("entity_id")
        if not entity_id:
            continue
        entities.setdefault(entity_type, {})[entity_id] = entity
    return entities


def summarize_game(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce one game's events to belief-coverage metrics."""

    entities = _reduce_entities(events)
    counts = Counter({entity_type: len(ids) for entity_type, ids in entities.items()})

    decisions = entities.get("decision", {})
    beliefs = entities.get("belief", {})
    predictions = entities.get("prediction", {})
    actions = entities.get("action", {})

    decisions_total = len(decisions)
    decisions_referencing = sum(
        1 for d in decisions.values() if d.get("belief_ids")
    )
    # A reference only counts as support if it resolves to a recorded
    # belief row. Tombstones (archived/deleted) are historical rows that
    # did back the decision when it was made; a dangling id is not evidence.
    decisions_supported = sum(
        1
        for d in decisions.values()
        if d.get("belief_ids")
        and any(belief_id in beliefs for belief_id in d["belief_ids"])
    )

    def _derived(entity: dict[str, Any]) -> bool:
        return "derived" in (entity.get("tags") or [])

    # Derived beliefs are produced by the system's derivation rules; manual
    # ones are the agent's explicit self-reports. The split keeps the
    # "belief-driven" claim honest after derivation lands.
    decisions_supported_derived = sum(
        1
        for d in decisions.values()
        if d.get("belief_ids")
        and any(
            beliefs.get(belief_id) is not None and _derived(beliefs[belief_id])
            for belief_id in d["belief_ids"]
        )
    )

    resolved = [
        p
        for p in predictions.values()
        if p.get("status") in _RESOLVED_PREDICTION_STATUSES
    ]
    prediction_errors = [
        float(p["prediction_error"])
        for p in resolved
        if isinstance(p.get("prediction_error"), (int, float))
    ]

    turns = [
        int(event["turn"])
        for event in events
        if isinstance(event.get("turn"), int)
    ]

    def ratio(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    return {
        "game_id": next((e.get("game_id") for e in events if e.get("game_id")), None),
        "run_ids": sorted({e["run_id"] for e in events if e.get("run_id")}),
        "turns_played": max(turns) if turns else 0,
        "entity_counts": dict(counts),
        "decisions_total": decisions_total,
        "decisions_referencing_beliefs": decisions_referencing,
        "decisions_with_belief_support": decisions_supported,
        "belief_supported_decision_ratio": ratio(decisions_supported, decisions_total),
        "decisions_with_derived_support": decisions_supported_derived,
        "decisions_with_manual_support": decisions_supported - decisions_supported_derived,
        "decisions_with_proposal": sum(
            1
            for d in decisions.values()
            if (d.get("action_intent") or {}).get("proposal_id")
        ),
        "beliefs_total": len(beliefs),
        "beliefs_derived": sum(1 for b in beliefs.values() if _derived(b)),
        "beliefs_manual": sum(1 for b in beliefs.values() if not _derived(b)),
        "beliefs_active": sum(
            1 for b in beliefs.values() if b.get("status") in _ACTIVE_STATUSES
        ),
        "predictions_total": len(predictions),
        "predictions_derived": sum(1 for p in predictions.values() if _derived(p)),
        "predictions_manual": sum(1 for p in predictions.values() if not _derived(p)),
        "predictions_resolved": len(resolved),
        "prediction_resolution_ratio": ratio(len(resolved), len(predictions)),
        "prediction_error_mean": (
            round(sum(prediction_errors) / len(prediction_errors), 4)
            if prediction_errors
            else None
        ),
        "actions_total": len(actions),
        "beliefs_per_100_actions": (
            round(100 * len(beliefs) / len(actions), 2) if actions else None
        ),
    }


def summarize_fleet(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-game summaries into fleet totals.

    Ratios are recomputed from sums, never averaged across games, so one
    long game cannot be diluted by short ones.
    """

    def total(summary: dict[str, Any], key: str) -> int:
        return int(summary.get(key) or 0)

    decisions = sum(total(s, "decisions_total") for s in summaries)
    supported = sum(total(s, "decisions_with_belief_support") for s in summaries)
    beliefs = sum(total(s, "beliefs_total") for s in summaries)
    actions = sum(total(s, "actions_total") for s in summaries)
    predictions = sum(total(s, "predictions_total") for s in summaries)
    resolved = sum(total(s, "predictions_resolved") for s in summaries)

    def ratio(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    return {
        "games": len(summaries),
        "decisions_total": decisions,
        "decisions_with_belief_support": supported,
        "belief_supported_decision_ratio": ratio(supported, decisions),
        "decisions_with_derived_support": sum(
            total(s, "decisions_with_derived_support") for s in summaries
        ),
        "decisions_with_manual_support": sum(
            total(s, "decisions_with_manual_support") for s in summaries
        ),
        "beliefs_total": beliefs,
        "beliefs_derived": sum(total(s, "beliefs_derived") for s in summaries),
        "beliefs_manual": sum(total(s, "beliefs_manual") for s in summaries),
        "actions_total": actions,
        "beliefs_per_100_actions": (
            round(100 * beliefs / actions, 2) if actions else None
        ),
        "predictions_total": predictions,
        "predictions_resolved": resolved,
        "prediction_resolution_ratio": ratio(resolved, predictions),
        "games_with_any_belief": sum(1 for s in summaries if total(s, "beliefs_total")),
        "games_with_supported_decision": sum(
            1 for s in summaries if total(s, "decisions_with_belief_support")
        ),
    }
