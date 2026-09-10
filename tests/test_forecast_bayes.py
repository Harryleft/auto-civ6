"""Unit tests for the Bayesian posterior helper.

These are pure functions over the caller's priors and likelihood ratios, so
they are tested directly rather than through the MCP tool surface.
"""

from __future__ import annotations

import math

import pytest

from civ6_belief_engine.forecast import bayes


def test_posterior_normalizes_to_one():
    result = bayes.posterior({"a": 0.5, "b": 0.5}, {"a": 3.0})
    assert sum(result.values()) == pytest.approx(1.0)
    assert result["a"] > result["b"]


def test_unmentioned_hypotheses_keep_ratio_one():
    result = bayes.posterior({"a": 0.5, "b": 0.5}, {})
    assert result == pytest.approx({"a": 0.5, "b": 0.5})


def test_empty_prior_is_rejected():
    with pytest.raises(ValueError, match="prior cannot be empty"):
        bayes.posterior({}, {})


@pytest.mark.parametrize("bad", [0, -1.0])
def test_non_positive_prior_is_rejected(bad):
    with pytest.raises(ValueError, match="zero prior cannot be"):
        bayes.posterior({"a": bad, "b": 0.5}, {})


@pytest.mark.parametrize("bad", [0, -2.0, float("inf"), float("nan")])
def test_non_positive_or_non_finite_ratio_is_rejected(bad):
    with pytest.raises(ValueError, match="positive and finite"):
        bayes.posterior({"a": 0.5, "b": 0.5}, {"a": bad})


def test_unknown_hypothesis_ratio_is_rejected():
    with pytest.raises(ValueError, match="unknown hypotheses"):
        bayes.posterior({"a": 0.5}, {"typo": 2.0})


def test_overflow_raises_instead_of_returning_zeros():
    """Regression: an overflowing product used to return {a: 0.0, b: 0.0}.

    The function promises normalization to 1, and 0.0 passes the probability
    validator, so a silent all-zero result could be persisted and would erase
    every hypothesis in the pool.
    """

    with pytest.raises(ValueError, match="cannot be normalized"):
        bayes.posterior({"a": 1.0, "b": 1.0}, {"a": 1e308, "b": 1e308})


def test_large_but_representable_ratios_still_normalize():
    result = bayes.posterior({"a": 0.5, "b": 0.5}, {"a": 1e150, "b": 1e-150})
    assert sum(result.values()) == pytest.approx(1.0)
    assert math.isfinite(result["a"])
    assert result["a"] > 0.999


def test_underflowed_ratios_keep_the_floor_and_stay_normalizable():
    result = bayes.posterior({"a": 0.5, "b": 0.5}, {"a": 0.0 + 1e-300})
    assert sum(result.values()) == pytest.approx(1.0)
    assert all(value > 0 for value in result.values())


def test_trace_returns_one_row_per_hypothesis():
    rows = bayes.trace({"b": 0.4, "a": 0.6}, {"a": 2.0})["rows"]
    assert [row["hypothesis_id"] for row in rows] == ["a", "b"]
    assert rows[0]["likelihood_ratio"] == 2.0
    assert rows[1]["likelihood_ratio"] == 1.0


def test_trace_propagates_the_overflow_error():
    with pytest.raises(ValueError, match="cannot be normalized"):
        bayes.trace({"a": 1.0, "b": 1.0}, {"a": 1e308, "b": 1e308})
