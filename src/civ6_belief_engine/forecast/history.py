"""Compact views of observed turns; no I/O, forward filling, or state writes."""

from __future__ import annotations

from math import isfinite
from typing import Any, Iterable, Mapping, Sequence

from .protocol import finite_number, numeric_metric_series, observation_turn

DEFAULT_HISTORY_WINDOWS: tuple[int, ...] = (5, 10, 20)
DEFAULT_HISTORY_METRICS: tuple[str, ...] = (
    "science",
    "culture",
    "gold",
    "gold_per_turn",
    "cities",
    "population",
    "units",
    "exploration_pct",
)


def recent_metric_points(
    points: Sequence[tuple[int, float]],
    *,
    current_turn: int,
    window_turns: int,
) -> list[tuple[int, float]]:
    """Select actual turns [current-window+1, current], latest valid per turn.

    Missing turns stay missing; even numerous old samples cannot become a
    recent window. Callers supply points in observation order when repeated.
    """
    if current_turn < 0 or window_turns < 1:
        raise ValueError("current_turn must be non-negative and window_turns positive")
    start_turn = max(0, current_turn - window_turns + 1)
    by_turn: dict[int, float] = {}
    for raw_turn, value in points:
        turn = observation_turn({"observed_turn": raw_turn})
        number = finite_number(value)
        if (
            turn is not None
            and start_turn <= turn <= current_turn
            and number is not None
        ):
            by_turn[turn] = number
    return sorted(by_turn.items())


def summarize_history(
    observations: Iterable[Mapping[str, Any]],
    *,
    current_turn: int,
    windows: Sequence[int] = DEFAULT_HISTORY_WINDOWS,
    metrics: Sequence[str] = DEFAULT_HISTORY_METRICS,
    max_metrics: int = 8,
) -> dict[str, Any]:
    """Summarize observed history separately from current facts and forecasts.

    The caller supplies observations from the active game/reload epoch. This
    view never removes accumulated facts or ledger history. Coverage means
    distinct sampled turns / possible turns, including turn zero. Single
    observations retain their evidence but cannot imply a change or trend.
    """
    if current_turn < 0 or max_metrics < 1:
        raise ValueError("current_turn must be non-negative and max_metrics positive")
    selected_windows = tuple(dict.fromkeys(windows))
    if not selected_windows or any(window < 1 for window in selected_windows):
        raise ValueError("windows must contain positive turn counts")
    # Keep the model-facing result bounded even if a caller passes all metrics.
    selected_metrics = tuple(dict.fromkeys(metrics))[: min(max_metrics, 16)]
    selected_windows = selected_windows[:3]
    earliest = max(0, current_turn - max(selected_windows) + 1)
    recent_observations = (
        item
        for item in observations
        if (turn := observation_turn(item)) is not None
        and earliest <= turn <= current_turn
    )
    series = numeric_metric_series(
        recent_observations,
        metrics=selected_metrics,
        current_turn=current_turn,
        min_points=1,
    )
    summaries: list[dict[str, Any]] = []
    for window in selected_windows:
        start_turn = max(0, current_turn - window + 1)
        available_turns = current_turn - start_turn + 1
        metric_summaries: dict[str, dict[str, Any]] = {}
        for metric in selected_metrics:
            points = recent_metric_points(
                series.get(metric, ()),
                current_turn=current_turn,
                window_turns=window,
            )
            if not points:
                continue
            first_turn, first_value = points[0]
            last_turn, last_value = points[-1]
            change = last_value - first_value if len(points) >= 2 else None
            metric_summaries[metric] = {
                "sample_count": len(points),
                "coverage": round(len(points) / available_turns, 3),
                "first": {"turn": first_turn, "value": first_value},
                "last": {"turn": last_turn, "value": last_value},
                "change": change if change is None or isfinite(change) else None,
                "latest_age_turns": current_turn - last_turn,
            }
        summaries.append(
            {
                "window_turns": window,
                "start_turn": start_turn,
                "end_turn": current_turn,
                "available_turns": available_turns,
                "metrics": metric_summaries,
            }
        )
    return {
        "kind": "observed_history",
        "as_of_turn": current_turn,
        "note": "历史观测摘要；缺测未补值，末次观测不代表当前事实，变化不代表未来预测。",
        "windows": summaries,
    }
