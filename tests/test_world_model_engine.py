"""World-model engine integration: overdue lifecycle, simulation entities,
Bayesian rebalancing."""

from __future__ import annotations

import pytest

from civ6_belief_engine.belief_engine import BeliefEngine, BeliefEngineError


def _prediction(**overrides):
    payload = {
        "statement": "Science output will reach 60.",
        "probability": 0.8,
        "confidence": 0.7,
        "deadline_turn": 10,
    }
    payload.update(overrides)
    return payload


def _hypothesis(**overrides):
    payload = {
        "statement": "The neighbour is preparing a war.",
        "topic_id": "neighbour-intent",
        "probability": 0.5,
        "confidence": 0.6,
    }
    payload.update(overrides)
    return payload


def _observe(engine, turn, metrics):
    return engine.create(
        "observation",
        {"statement": f"fixture T{turn}", "source": "get_game_overview", "metrics": metrics},
        turn=turn,
    )


class TestOverdueLifecycle:
    def test_unobservable_metric_records_reason(self, engine):
        prediction = engine.create(
            "prediction",
            _prediction(evaluation={"metric": "science", "operator": ">=", "value": 60}),
            turn=1,
        )
        _observe(engine, 11, {"gold": 300})  # science never observed
        engine.review(turn=11)
        assert prediction["status"] == "active"  # pre-review snapshot
        stored = engine.get("prediction", prediction["id"])
        assert stored["status"] == "overdue"
        assert stored["overdue_reason"] == "metric_unavailable"

    def test_missing_evaluation_rule_records_reason(self, engine):
        prediction = engine.create("prediction", _prediction(), turn=1)
        _observe(engine, 11, {"science": 80})
        engine.review(turn=11)
        stored = engine.get("prediction", prediction["id"])
        assert stored["status"] == "overdue"
        assert stored["overdue_reason"] == "no_evaluation_rule"

    def test_late_disconfirming_metric_resolves_overdue(self, engine):
        prediction = engine.create(
            "prediction",
            _prediction(evaluation={"metric": "science", "operator": ">=", "value": 60}),
            turn=1,
        )
        _observe(engine, 11, {"gold": 300})
        engine.review(turn=11)
        assert engine.get("prediction", prediction["id"])["status"] == "overdue"
        # The metric finally arrives at T15 and the rule is still False.
        _observe(engine, 15, {"science": 40})
        review = engine.review(turn=15)
        stored = engine.get("prediction", prediction["id"])
        assert stored["status"] == "disconfirmed"
        assert stored["resolution_source"] == "automatic_late"
        assert stored["resolved_turn"] == 15
        assert prediction["id"] in review["predictions_resolved"]

    def test_late_confirming_metric_keeps_overdue_open(self, engine):
        prediction = engine.create(
            "prediction",
            _prediction(evaluation={"metric": "science", "operator": ">=", "value": 60}),
            turn=1,
        )
        _observe(engine, 11, {"gold": 300})
        engine.review(turn=11)
        _observe(engine, 15, {"science": 80})
        engine.review(turn=15)
        stored = engine.get("prediction", prediction["id"])
        # True after the deadline cannot prove the claim held *by* T10.
        assert stored["status"] == "overdue"


class TestSimulationEntities:
    def test_create_and_tombstone_simulation(self, engine):
        simulation = engine.create(
            "simulation",
            {
                "statement": "baseline over 10 turns",
                "branch_label": "baseline: trend x1",
                "scenario": "baseline",
                "projections": {"gold": [[15, 450.0]]},
            },
            turn=5,
        )
        assert simulation["id"].startswith("simulation_")
        assert engine.list("simulation", status="active")[0]["id"] == simulation["id"]
        deleted = engine.delete(
            "simulation", simulation["id"], turn=6, reason="superseded forecast"
        )
        assert deleted["status"] == "deleted"
        assert engine.list("simulation", status="active") == []
        # The audit event survives the tombstone.
        history = engine.history(entity_type="simulation", entity_id=simulation["id"])
        assert any(event["event_type"] == "entity.deleted" for event in history)

    def test_simulation_requires_branch_fields(self, engine):
        with pytest.raises(BeliefEngineError):
            engine.create("simulation", {"statement": "incomplete"}, turn=5)


class TestBayesianRebalance:
    def test_posterior_shifts_toward_discriminated_hypothesis(self, engine):
        first = engine.create("hypothesis", _hypothesis(probability=0.5), turn=1)
        second = engine.create(
            "hypothesis",
            _hypothesis(statement="The neighbour wants trade.", probability=0.5),
            turn=1,
        )
        updated = engine.rebalance_hypotheses_bayesian(
            "neighbour-intent",
            {first["id"]: 3.0},
            turn=2,
            evidence_id="observation_1",
        )
        by_id = {item["id"]: item for item in updated}
        assert by_id[first["id"]]["probability"] == pytest.approx(0.75, abs=1e-3)
        assert by_id[second["id"]]["probability"] == pytest.approx(0.25, abs=1e-3)
        assert by_id[first["id"]]["last_likelihood_ratio"] == 3.0
        assert by_id[second["id"]]["last_likelihood_ratio"] == 1.0
        assert by_id[first["id"]]["last_evidence_id"] == "observation_1"

    def test_unknown_ratio_target_rejected_before_any_write(self, engine):
        engine.create("hypothesis", _hypothesis(), turn=1)
        with pytest.raises(BeliefEngineError):
            engine.rebalance_hypotheses_bayesian(
                "neighbour-intent", {"hypothesis_missing": 2.0}, turn=2
            )

    def test_zero_prior_rejected(self, engine):
        engine.create("hypothesis", _hypothesis(probability=0.0), turn=1)
        with pytest.raises(BeliefEngineError, match="zero prior"):
            engine.rebalance_hypotheses_bayesian(
                "neighbour-intent", {"hyp_a": 2.0}, turn=2
            )

    def test_missing_topic_rejected(self, engine):
        with pytest.raises(BeliefEngineError, match="No active hypotheses"):
            engine.rebalance_hypotheses_bayesian("unknown-topic", {"hyp_a": 2.0}, turn=2)
