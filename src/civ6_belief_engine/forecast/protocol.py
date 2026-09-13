"""Forecaster contracts and metric-series extraction.

The Slow Path world simulation reads the same observation history the rest
of the engine already persists.  A ``Forecaster`` is a pure function from
metric time series to future branches; it performs no I/O, keeps no state,
and never writes entities itself.  Persistence is decided by the caller
(the MCP tool layer), which keeps every forecaster implementation
replaceable and testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping, Protocol, Sequence, TypeAlias

# metric name -> ordered (turn, value) points
MetricSeries: TypeAlias = Mapping[str, Sequence[tuple[int, float]]]

#: Metrics the overview normalizes; forecasts default to this set so a
#: branch stays readable instead of projecting every incidental key.
DEFAULT_FORECAST_METRICS: tuple[str, ...] = (
    "turn",
    "score",
    "gold",
    "gold_per_turn",
    "science",
    "culture",
    "faith",
    "favor",
    "cities",
    "population",
    "units",
    "exploration_pct",
    "era_score",
)


@dataclass(frozen=True, slots=True)
class Branch:
    """One projected future of the tracked metrics."""

    label: str
    scenario: str
    horizon_turns: int
    assumptions: tuple[str, ...]
    #: metric name -> ((turn, projected_value), ...)
    projections: Mapping[str, tuple[tuple[int, float], ...]]


class Forecaster(Protocol):
    """Project metric futures from observed history.  Pure and deterministic."""

    def forecast(
        self,
        series: MetricSeries,
        *,
        current_turn: int,
        horizon_turns: int,
    ) -> tuple[Branch, ...]: ...


def numeric_metric_series(
    observations: Iterable[Mapping[str, Any]],
    *,
    metrics: Sequence[str] | None = None,
    current_turn: int | None = None,
    min_points: int = 2,
) -> dict[str, list[tuple[int, float]]]:
    """Build numeric metric time series from observation entities.

    The last valid observation wins per (metric, turn), ordered by
    ``updated_at`` and then input order. Turns are kept in ascending order.
    ``current_turn`` excludes future observations; ``min_points=1`` also
    exposes isolated observations for history summaries, never a trend.
    Non-numeric and non-finite values are skipped rather
    than coerced — a forecast must not silently invent a numeric trend from
    a boolean or string metric.
    """
    if min_points < 1:
        raise ValueError("min_points must be at least 1")
    allowed = set(metrics) if metrics is not None else None
    points_by_metric: dict[str, dict[int, tuple[float, int, float]]] = {}
    for index, observation in enumerate(observations):
        turn = observation_turn(observation)
        if turn is None or (current_turn is not None and turn > current_turn):
            continue
        stamp = finite_number(observation.get("updated_at")) or 0.0
        metric_values = observation.get("metrics")
        if not isinstance(metric_values, Mapping):
            continue
        for key, value in metric_values.items():
            if allowed is not None and key not in allowed:
                continue
            number = finite_number(value)
            if number is None:
                continue
            points = points_by_metric.setdefault(key, {})
            candidate = (stamp, index, number)
            if turn not in points or candidate[:2] > points[turn][:2]:
                points[turn] = candidate
    return {
        key: [(turn, ranked[2]) for turn, ranked in sorted(points.items())]
        for key, points in points_by_metric.items()
        if len(points) >= min_points
    }


def finite_number(value: Any) -> float | None:
    """Accept actual finite numbers, excluding booleans and numeric strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if isfinite(number) else None


def observation_turn(observation: Mapping[str, Any]) -> int | None:
    """Read a valid non-negative turn without treating turn zero as absent."""
    value = observation.get("observed_turn")
    if isinstance(value, bool) or value is None:
        return None
    try:
        turn = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if turn < 0 or (not isinstance(value, str) and turn != value):
        return None
    return turn
