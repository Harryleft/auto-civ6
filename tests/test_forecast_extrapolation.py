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

    def test_turn_zero_and_last_valid_observation_are_retained(self):
        observations = [
            {"observed_turn": 0, "metrics": {"gold": 0}},
            {"observed_turn": 1, "updated_at": 20, "metrics": {"gold": 20}},
            {"observed_turn": 1, "updated_at": 10, "metrics": {"gold": 10}},
            {"observed_turn": 1, "updated_at": 30, "metrics": {"gold": float("nan")}},
            {"observed_turn": 2, "metrics": {"gold": float("inf")}},
            {"observed_turn": 3, "metrics": {"gold": 100}},
        ]
        assert numeric_metric_series(observations, current_turn=2) == {
            "gold": [(0, 0.0), (1, 20.0)],
        }

    def test_summary_can_request_single_samples_without_weakening_forecast_default(self):
        observations = [{"observed_turn": 0, "metrics": {"cities": 1}}]
        assert numeric_metric_series(observations) == {}
        assert numeric_metric_series(observations, min_points=1) == {"cities": [(0, 1.0)]}

    @pytest.mark.parametrize("turn", [None, -1, float("nan"), float("inf"), True, 1.5, "bad"])
    def test_invalid_turns_do_not_crash_or_create_samples(self, turn):
        assert numeric_metric_series([
            {"observed_turn": turn, "metrics": {"gold": 1}},
        ], min_points=1) == {}


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

    def test_window_counts_actual_turns_and_ignores_future_observations(self):
        series = {"gold": [(1, 999.0), (6, 60.0), (8, 80.0), (10, 100.0), (11, 999.0)]}
        branches = TrendExtrapolator(window=5).forecast(
            series, current_turn=10, horizon_turns=20,
        )
        assert branches[1].projections["gold"] == ((20, 200.0), (30, 300.0))

    def test_many_old_samples_do_not_create_recent_forecast(self):
        series = {"gold": [(turn, float(turn * 10)) for turn in range(1, 21)]}
        branches = TrendExtrapolator(window=20).forecast(
            series, current_turn=100, horizon_turns=5,
        )
        assert all(not branch.projections for branch in branches)

    def test_duplicate_samples_in_one_turn_do_not_satisfy_minimum(self):
        branches = TrendExtrapolator().forecast(
            {"gold": [(10, 100.0), (10, 200.0), (11, 250.0)]},
            current_turn=11, horizon_turns=5,
        )
        assert all(not branch.projections for branch in branches)

    def test_latest_valid_values_and_sorting_used_for_direct_series(self):
        branches = TrendExtrapolator().forecast(
            {"gold": [(3, 30.0), (1, 10.0), (2, 999.0), (2, 20.0), (2, float("inf"))]},
            current_turn=3, horizon_turns=2,
        )
        assert branches[1].projections["gold"] == ((4, 40.0), (5, 50.0))

    def test_non_finite_factors_rejected_and_overflowing_projection_omitted(self):
        with pytest.raises(ValueError):
            TrendExtrapolator(slope_factors=[("invalid", float("nan"))])
        branches = TrendExtrapolator(slope_factors=[("large", 1e308)]).forecast(
            {"gold": [(1, 0.0), (2, 10.0), (3, 20.0)]},
            current_turn=3, horizon_turns=10,
        )
        assert not branches[0].projections
