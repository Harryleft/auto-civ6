"""Calibration report tests — pure read model over prediction entities."""

from __future__ import annotations

import pytest

from civ6_belief_engine.belief_engine import BeliefEngine
from civ6_belief_engine.forecast import calibration_report


def _prediction(**overrides):
    payload = {
        "statement": "Gold will exceed 100.",
        "probability": 0.8,
        "confidence": 0.7,
        "deadline_turn": 12,
    }
    payload.update(overrides)
    return payload


def _resolved(engine: BeliefEngine, *, probability: float, outcome: bool, turn: int = 5):
    prediction = engine.create(
        "prediction", _prediction(probability=probability), turn=turn
    )
    return engine.resolve_prediction(
        prediction["id"],
        outcome=outcome,
        actual=100,
        turn=turn + 1,
        source="automatic",
    )


def _report(engine: BeliefEngine):
    return calibration_report(engine.list("prediction", status=None))


class TestCalibrationReport:
    def test_empty_report_has_no_scores(self, engine):
        report = _report(engine)
        assert report["total_predictions"] == 0
        assert report["brier_score"] is None
        assert report["observability_rate"] is None
        assert report["calibration_hint"] == "insufficient data"

    def test_brier_score_matches_hand_computation(self, engine):
        # (0.9 - 0)^2 = 0.81; (0.6 - 1)^2 = 0.16; mean = 0.485
        _resolved(engine, probability=0.9, outcome=False)
        _resolved(engine, probability=0.6, outcome=True)
        report = _report(engine)
        assert report["brier_score"] == 0.485
        assert report["resolved"] == 2

    def test_reliability_bucket_groups_by_claimed_probability(self, engine):
        # Two high-confidence claims: one true, one false.
        _resolved(engine, probability=0.9, outcome=True)
        _resolved(engine, probability=0.85, outcome=False)
        _resolved(engine, probability=0.3, outcome=False)
        buckets = {
            bucket["range"]: bucket for bucket in _report(engine)["reliability_buckets"]
        }
        assert buckets["[0.8,1.0)"]["count"] == 2
        assert buckets["[0.8,1.0)"]["observed_frequency"] == 0.5
        assert buckets["[0.8,1.0)"]["mean_claimed_probability"] == 0.875
        assert buckets["[0.8,1.0)"]["bias"] == round(0.5 - 0.875, 4)
        assert buckets["[0.2,0.4)"]["count"] == 1
        assert buckets["[0.2,0.4)"]["observed_frequency"] == 0.0

    def test_observability_rate_counts_overdue_as_unobservable(self, engine):
        _resolved(engine, probability=0.9, outcome=True)
        engine.create(
            "prediction",
            _prediction(statement="Fog metric will double.", deadline_turn=2),
            turn=1,
        )
        engine.update(
            "prediction",
            engine.list("prediction", status="active")[0]["id"],
            {"status": "overdue"},
            turn=3,
        )
        report = _report(engine)
        assert report["resolved"] == 1
        assert report["overdue"] == 1
        assert report["observability_rate"] == 0.5

    def test_overall_bias_flags_overconfidence(self, engine):
        # Claims ~0.9 confidence, always wrong: bias = 0 - 0.9 = -0.9
        for _ in range(5):
            _resolved(engine, probability=0.9, outcome=False)
        report = _report(engine)
        assert report["overall_bias"] == -0.9
        assert report["calibration_hint"] == "overconfident"

    def test_single_sided_samples_report_full_or_zero(self, engine):
        # All deadline-reached predictions resolved -> perfectly observable.
        _resolved(engine, probability=0.8, outcome=True)
        report = _report(engine)
        assert report["observability_rate"] == 1.0
        # All deadline-reached predictions overdue -> nothing observable.
        engine.create(
            "prediction",
            _prediction(statement="Fog metric will double.", deadline_turn=2),
            turn=1,
        )
        engine.update(
            "prediction",
            engine.list("prediction", status="active")[0]["id"],
            {"status": "overdue"},
            turn=3,
        )
        report = _report(engine)
        assert report["observability_rate"] == 0.5  # 1 on-time / (1 + 0 + 1)

    def test_late_resolution_counts_as_deadline_time_miss(self, engine):
        late = engine.create(
            "prediction", _prediction(deadline_turn=10), turn=1
        )
        engine.resolve_prediction(
            late["id"], outcome=False, actual=10, turn=15, source="automatic_late"
        )
        report = _report(engine)
        assert report["resolved"] == 1
        assert report["late_resolved"] == 1
        # The outcome still scores calibration...
        assert report["brier_score"] == pytest.approx((0.8 - 0.0) ** 2, abs=1e-4)
        # ...but the deadline-time checkability was a miss.
        assert report["observability_rate"] == 0.0

    def test_by_resolution_source_splits_scores(self, engine):
        manual = engine.create("prediction", _prediction(probability=0.8), turn=1)
        engine.resolve_prediction(
            manual["id"], outcome=True, actual=1, turn=2, source="manual"
        )
        _resolved(engine, probability=0.8, outcome=True)
        by_source = _report(engine)["by_resolution_source"]
        assert set(by_source) == {"manual", "automatic"}
        assert by_source["manual"]["count"] == 1
        assert by_source["manual"]["observed_frequency"] == 1.0
