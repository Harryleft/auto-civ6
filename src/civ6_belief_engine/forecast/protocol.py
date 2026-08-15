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
) -> dict[str, list[tuple[int, float]]]:
    """Build numeric metric time series from observation entities.

    Later observations win when the same (metric, turn) pair repeats; turns
    are kept in ascending order.  Non-numeric values are skipped rather
    than coerced — a forecast must not silently invent a numeric trend from
    a boolean or string metric.
    """
    allowed = set(metrics) if metrics is not None else None
    points_by_metric: dict[str, dict[int, float]] = {}
    for observation in sorted(
        observations,
        key=lambda item: (
            int(item.get("observed_turn", -1) or -1),
            float(item.get("updated_at", 0) or 0),
        ),
    ):
        turn = int(observation.get("observed_turn", -1) or -1)
        if turn < 0:
            continue
        for key, value in (observation.get("metrics") or {}).items():
            if allowed is not None and key not in allowed:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            points_by_metric.setdefault(key, {})[turn] = float(value)
    return {
        key: sorted(points.items())
        for key, points in points_by_metric.items()
        if len(points) >= 2
    }
