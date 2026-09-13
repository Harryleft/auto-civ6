"""Actual-turn history windows preserve missing data and evidence age."""

from __future__ import annotations

import json

import pytest

from civ6_belief_engine.forecast.history import summarize_history


def test_default_windows_use_turn_boundaries_not_observation_count():
    observations = [
        {"observed_turn": 0, "metrics": {"gold": 900}},
        {"observed_turn": 1, "metrics": {"gold": 10}},
        {"observed_turn": 10, "metrics": {"gold": 100}},
        {"observed_turn": 15, "metrics": {"gold": 150}},
        {"observed_turn": 16, "metrics": {"gold": 160}},
        {"observed_turn": 20, "metrics": {"gold": 200}},
        {"observed_turn": 21, "metrics": {"gold": 999}},
    ]
    summary = summarize_history(observations, current_turn=20)
    windows = summary["windows"]
    assert summary["kind"] == "observed_history"
    assert summary["as_of_turn"] == 20
    assert [item["window_turns"] for item in windows] == [5, 10, 20]
    assert [item["start_turn"] for item in windows] == [16, 11, 1]
    assert [item["metrics"]["gold"]["sample_count"] for item in windows] == [2, 3, 5]
    assert [item["metrics"]["gold"]["change"] for item in windows] == [40, 50, 190]
    assert windows[0]["metrics"]["gold"]["coverage"] == 0.4


def test_sparse_window_keeps_observation_age_and_missing_data():
    summary = summarize_history(
        [
            {"observed_turn": 16, "metrics": {"science": 25}},
            {"observed_turn": 16, "updated_at": 1, "metrics": {"science": 30}},
            {"observed_turn": 16, "updated_at": 2, "metrics": {"science": None}},
        ],
        current_turn=20,
    )
    metric = summary["windows"][0]["metrics"]["science"]
    assert metric["sample_count"] == 1
    assert metric["coverage"] == 0.2
    assert metric["first"] == metric["last"] == {"turn": 16, "value": 30.0}
    assert metric["latest_age_turns"] == 4
    assert metric["change"] is None
    assert "gold" not in summary["windows"][0]["metrics"]


def test_turn_zero_is_valid_and_early_game_coverage_has_no_pre_game_turns():
    summary = summarize_history(
        [
            {"observed_turn": 0, "metrics": {"gold": 0}},
        ],
        current_turn=0,
    )
    for window in summary["windows"]:
        assert window["start_turn"] == window["end_turn"] == 0
        assert window["available_turns"] == 1
        assert window["metrics"]["gold"]["coverage"] == 1.0


def test_history_discards_non_finite_values_without_overwriting_valid_observation():
    summary = summarize_history(
        [
            {"observed_turn": 18, "metrics": {"gold": 180, "science": True}},
            {"observed_turn": 18, "updated_at": 1, "metrics": {"gold": float("nan")}},
            {"observed_turn": 19, "metrics": {"gold": float("inf"), "culture": "10"}},
            {"observed_turn": 20, "metrics": {"gold": 200, "faith": 10**1000}},
        ],
        current_turn=20,
    )
    metric = summary["windows"][0]["metrics"]["gold"]
    assert metric["sample_count"] == 2
    assert metric["change"] == 20
    assert set(summary["windows"][0]["metrics"]) == {"gold"}
    json.dumps(summary, allow_nan=False)


def test_summary_is_bounded_and_does_not_change_input_history():
    observations = [
        {"observed_turn": turn, "metrics": {f"metric_{i}": i for i in range(30)}}
        for turn in range(100)
    ]
    before = json.dumps(observations)
    summary = summarize_history(
        observations,
        current_turn=99,
        metrics=[f"metric_{i}" for i in range(30)],
        max_metrics=2,
        windows=(5, 10, 20, 50),
    )
    assert len(summary["windows"]) == 3
    assert all(len(window["metrics"]) == 2 for window in summary["windows"])
    assert "points" not in json.dumps(summary)
    assert json.dumps(observations) == before


def test_no_recent_evidence_returns_empty_metrics_without_old_values():
    summary = summarize_history(
        [
            {"observed_turn": 1, "metrics": {"gold": 100}},
        ],
        current_turn=100,
    )
    assert all(not window["metrics"] for window in summary["windows"])


@pytest.mark.parametrize(
    "options", [{"windows": ()}, {"windows": (0,)}, {"max_metrics": 0}]
)
def test_invalid_configuration_rejected(options):
    with pytest.raises(ValueError):
        summarize_history([], current_turn=1, **options)
