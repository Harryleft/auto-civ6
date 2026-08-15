"""Prediction calibration report — a pure read model over resolved predictions.

The engine already stores ``prediction_error`` per resolved prediction, but a
mean hides the two failures that matter: which claimed-probability buckets
are systematically wrong (reliability), and how many predictions could never
be checked at all (observability).  This module quantifies both without
writing any state.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

RELIABILITY_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.2),
    (0.2, 0.4),
    (0.4, 0.6),
    (0.6, 0.8),
    (0.8, 1.0),
)


def _resolved(prediction: Mapping[str, Any]) -> bool:
    return prediction.get("status") in {"confirmed", "disconfirmed"}


def _outcome(prediction: Mapping[str, Any]) -> float:
    return 1.0 if prediction.get("status") == "confirmed" else 0.0


def _resolved_late(prediction: Mapping[str, Any]) -> bool:
    resolved_turn = prediction.get("resolved_turn")
    deadline = prediction.get("deadline_turn")
    if isinstance(resolved_turn, bool) or isinstance(deadline, bool):
        return False
    if not isinstance(resolved_turn, int) or not isinstance(deadline, int):
        return False
    return resolved_turn > deadline


def _bucket_index(probability: float) -> int | None:
    for index, (low, high) in enumerate(RELIABILITY_BUCKETS):
        if low <= probability < high:
            return index
    if probability == 1.0:
        return len(RELIABILITY_BUCKETS) - 1
    return None


def _brier(predictions: Sequence[Mapping[str, Any]]) -> float | None:
    if not predictions:
        return None
    errors = [
        (float(item["probability"]) - _outcome(item)) ** 2
        for item in predictions
        if isinstance(item.get("probability"), (int, float))
    ]
    if not errors:
        return None
    return round(sum(errors) / len(errors), 4)


def calibration_report(predictions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate calibration, reliability, and observability over predictions.

    ``predictions`` are current prediction entities (any status), as returned
    by ``BeliefEngine.list("prediction", status=None)``.
    """
    resolved = [item for item in predictions if _resolved(item)]
    overdue = [item for item in predictions if item.get("status") == "overdue"]
    active = [item for item in predictions if item.get("status") == "active"]
    # A prediction resolved after its deadline was NOT checkable when it
    # mattered; its outcome still scores calibration, but observability
    # counts the deadline-time miss. Source-agnostic: judged by turns.
    late_resolved = [item for item in resolved if _resolved_late(item)]
    on_time_resolved = [item for item in resolved if not _resolved_late(item)]

    buckets: list[dict[str, Any]] = []
    for index, (low, high) in enumerate(RELIABILITY_BUCKETS):
        members = [
            item
            for item in resolved
            if isinstance(item.get("probability"), (int, float))
            and _bucket_index(float(item["probability"])) == index
        ]
        if not members:
            buckets.append(
                {"range": f"[{low:.1f},{high:.1f})", "count": 0}
            )
            continue
        claimed = sum(float(item["probability"]) for item in members) / len(members)
        observed = sum(_outcome(item) for item in members) / len(members)
        buckets.append(
            {
                "range": f"[{low:.1f},{high:.1f})",
                "count": len(members),
                "mean_claimed_probability": round(claimed, 4),
                "observed_frequency": round(observed, 4),
                "bias": round(observed - claimed, 4),
            }
        )

    by_source: dict[str, dict[str, Any]] = {}
    for item in resolved:
        source = str(item.get("resolution_source") or "unknown")
        by_source.setdefault(source, {"count": 0, "outcomes": 0.0})
        by_source[source]["count"] += 1
        by_source[source]["outcomes"] += _outcome(item)
    for source, stats in by_source.items():
        count = stats["count"]
        stats["observed_frequency"] = round(stats.pop("outcomes") / count, 4)
        stats["brier"] = _brier(
            [item for item in resolved if str(item.get("resolution_source") or "unknown") == source]
        )

    overall_bias = None
    if resolved:
        claimed_total = sum(
            float(item["probability"])
            for item in resolved
            if isinstance(item.get("probability"), (int, float))
        )
        overall_bias = round(
            (sum(_outcome(item) for item in resolved) / len(resolved))
            - (claimed_total / len(resolved)),
            4,
        )

    return {
        "total_predictions": len(predictions),
        "resolved": len(resolved),
        "late_resolved": len(late_resolved),
        "overdue": len(overdue),
        "active": len(active),
        # Of the predictions that reached their check window, how many could
        # be verified against evidence by the deadline — the Sensorium
        # Effect, measured.  Late resolutions count in the denominator (the
        # deadline-time miss) but not the numerator.
        "observability_rate": (
            round(len(on_time_resolved) / (len(on_time_resolved) + len(late_resolved) + len(overdue)), 4)
            if (on_time_resolved or late_resolved or overdue)
            else None
        ),
        "brier_score": _brier(resolved),
        "mean_absolute_error": (
            round(
                sum(
                    float(item.get("prediction_error", 0)) for item in resolved
                )
                / len(resolved),
                4,
            )
            if resolved
            else None
        ),
        "overall_bias": overall_bias,
        # positive bias = events happen more often than claimed (underconfidence);
        # negative = claimed more certainty than reality (overconfidence)
        "calibration_hint": (
            "insufficient data"
            if len(resolved) < 5
            else (
                "overconfident"
                if (overall_bias is not None and overall_bias <= -0.1)
                else "underconfident"
                if (overall_bias is not None and overall_bias >= 0.1)
                else "roughly calibrated"
            )
        ),
        "reliability_buckets": buckets,
        "by_resolution_source": by_source,
    }
