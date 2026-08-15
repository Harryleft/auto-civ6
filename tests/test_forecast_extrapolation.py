"""Trend extrapolation and metric-series extraction tests."""

from __future__ import annotations

import pytest

from civ6_belief_engine.forecast import TrendExtrapolator, numeric_metric_series


class TestNumericMetricSeries:
    def test_builds_series_and_later_observation_wins(self):
        observations = [
            {"observed_turn": 1, "metrics": {"gold": 100}},
            {"observed_turn": 2, "metrics": {"gold": 150}},
            {"observed_turn": 2, "metrics": {"gold": 175}, "updated_at": 99.0},
            {"observed_turn": 3, "metrics": {"gold": 200}},
        ]
        assert numeric_metric_series(observations) == {
            "gold": [(1, 100.0), (2, 175.0), (3, 200.0)]
        }

    def test_skips_non_numeric_and_single_point_metrics(self):
        observations = [
            {"observed_turn": 1, "metrics": {"gold": 100, "era": "ancient"}},
            {"observed_turn": 2, "metrics": {"gold": 150, "era": "classical", "flag": True}},
            {"observed_turn": 1, "metrics": {"cities": 4}},
        ]
        series = numeric_metric_series(observations)
        assert "era" not in series
        assert "flag" not in series
        assert "cities" not in series  # only one observation
        assert series["gold"] == [(1, 100.0), (2, 150.0)]

    def test_metric_filter_restricts_output(self):
        observations = [
            {"observed_turn": 1, "metrics": {"gold": 100, "science": 5}},
            {"observed_turn": 2, "metrics": {"gold": 150, "science": 12}},
        ]
        series = numeric_metric_series(observations, metrics=["gold"])
        assert list(series) == ["gold"]


class TestTrendExtrapolator:
    def test_exact_linear_projection_with_three_scenarios(self):
        series = {"gold": [(1, 100.0), (2, 110.0), (3, 120.0)]}  # slope 10/turn
        branches = TrendExtrapolator().forecast(
            series, current_turn=3, horizon_turns=4
        )
        assert [branch.scenario for branch in branches] == [
            "conservative",
            "baseline",
            "aggressive",
        ]
        baseline = branches[1]
        # offsets {2, 4} -> turns 5 and 7; last point (3, 120), slope 10
        assert baseline.projections["gold"] == ((5, 140.0), (7, 160.0))
        assert branches[0].projections["gold"] == ((5, 130.0), (7, 140.0))
        assert branches[2].projections["gold"] == ((5, 150.0), (7, 180.0))

    def test_clamps_non_negative_when_history_is_non_negative(self):
        series = {"gold": [(1, 50.0), (2, 30.0), (3, 10.0)]}  # slope -20
        branches = TrendExtrapolator().forecast(
            series, current_turn=3, horizon_turns=2
        )
        conservative = branches[0]  # slope x0.5 = -10/turn
        # T4: 10 - 10 = 0; T5: 10 - 20 = -10 -> clamped to 0
        assert conservative.projections["gold"] == ((4, 0.0), (5, 0.0))

    def test_skips_metrics_below_min_points(self):
        series = {
            "gold": [(1, 100.0), (2, 110.0), (3, 120.0)],
            "cities": [(1, 2.0), (2, 3.0)],
        }
        branches = TrendExtrapolator().forecast(
            series, current_turn=3, horizon_turns=2
        )
        assert "cities" not in branches[0].projections
        assert "gold" in branches[0].projections

    def test_no_series_yields_empty_branch_projections(self):
        branches = TrendExtrapolator().forecast({}, current_turn=1, horizon_turns=5)
        assert branches
        assert all(not branch.projections for branch in branches)

    def test_invalid_horizon_and_configuration_rejected(self):
        extrapolator = TrendExtrapolator()
        with pytest.raises(ValueError):
            extrapolator.forecast({"gold": [(1, 1.0)] * 3}, current_turn=1, horizon_turns=0)
        with pytest.raises(ValueError):
            TrendExtrapolator(window=1)
