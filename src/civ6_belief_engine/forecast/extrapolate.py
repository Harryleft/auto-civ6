"""Deterministic trend extrapolation forecaster.

First implementation of the Slow Path ``Forecaster`` contract: least-squares
slope over the recent window per metric, projected under three scenario
assumptions (conservative / baseline / aggressive).  It makes no claim about
Civ VI mechanics — it extends observed trends and says so in each branch's
assumptions.  Higher-fidelity forecasters (e.g. save-state replay through
the game's own rules engine) implement the same protocol and replace this
one without touching callers.
"""

from __future__ import annotations

from math import isfinite
from typing import Sequence

from .protocol import Branch, Forecaster, MetricSeries
from .history import recent_metric_points

#: scenario name -> share of the fitted slope carried into the future
SCENARIO_SLOPE_FACTORS: tuple[tuple[str, float], ...] = (
    ("conservative", 0.5),
    ("baseline", 1.0),
    ("aggressive", 1.5),
)


def _linear_slope(points: Sequence[tuple[int, float]]) -> float | None:
    """Least-squares slope, or None when the trend is undefined."""
    count = len(points)
    if count < 2:
        return None
    mean_x = sum(turn for turn, _ in points) / count
    mean_y = sum(value for _, value in points) / count
    var_x = sum((turn - mean_x) ** 2 for turn, _ in points)
    if var_x == 0:
        return None
    cov_xy = sum(
        (turn - mean_x) * (value - mean_y) for turn, value in points
    )
    slope = cov_xy / var_x
    return slope if isfinite(slope) else None


class TrendExtrapolator:
    """Project metrics forward under scaled-trend scenarios."""

    def __init__(
        self,
        *,
        window: int = 20,
        min_points: int = 3,
        slope_factors: Sequence[tuple[str, float]] = SCENARIO_SLOPE_FACTORS,
    ) -> None:
        if window < 2 or min_points < 2:
            raise ValueError("window and min_points must both be at least 2")
        if not slope_factors:
            raise ValueError("slope_factors cannot be empty")
        self._window = window
        self._min_points = min_points
        self._slope_factors = tuple(
            (str(name), float(factor)) for name, factor in slope_factors
        )
        if any(not isfinite(factor) for _, factor in self._slope_factors):
            raise ValueError("slope factors must be finite")

    def forecast(
        self,
        series: MetricSeries,
        *,
        current_turn: int,
        horizon_turns: int,
    ) -> tuple[Branch, ...]:
        if horizon_turns < 1:
            raise ValueError("horizon_turns must be at least 1")
        horizons = sorted(
            {offset for offset in (horizon_turns // 2, horizon_turns) if offset > 0}
        )
        trends: dict[str, tuple[tuple[int, float], float, bool]] = {}
        for metric, points in sorted(series.items()):
            recent = recent_metric_points(
                points, current_turn=current_turn, window_turns=self._window,
            )
            if len(recent) < self._min_points:
                continue
            slope = _linear_slope(recent)
            if slope is None:
                continue
            last_turn, last_value = recent[-1]
            trends[metric] = (
                (last_turn, last_value), slope,
                all(value >= 0 for _, value in recent),
            )

        branches: list[Branch] = []
        for scenario, factor in self._slope_factors:
            projections: dict[str, tuple[tuple[int, float], ...]] = {}
            for metric, ((last_turn, last_value), slope, non_negative) in trends.items():
                projected: list[tuple[int, float]] = []
                for offset in horizons:
                    future_turn = current_turn + offset
                    value = last_value + slope * factor * (future_turn - last_turn)
                    if not isfinite(value):
                        projected = []
                        break
                    if non_negative:
                        value = max(0.0, value)
                    projected.append(
                        (future_turn, round(value, 2))
                    )
                if projected:
                    projections[metric] = tuple(projected)
            branches.append(
                Branch(
                    label=f"{scenario}: 趋势系数 {factor:g}",
                    scenario=scenario,
                    horizon_turns=horizon_turns,
                    assumptions=(
                        f"按最近 {self._window} 个实际回合的有效观测外推；缺测未补值",
                        f"假设观测趋势持续，{scenario} 情景采用 {factor:g} 倍斜率；不代表当前事实",
                    ),
                    projections=projections,
                )
            )
        return tuple(branches)
