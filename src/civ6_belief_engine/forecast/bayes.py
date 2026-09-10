"""Server-side Bayesian posterior update for hypothesis pools.

``rebalance_hypotheses`` currently accepts a full probability distribution
supplied by the caller, so the arithmetic lives in the model's head and
cannot be audited.  This module keeps judgment where it belongs (the caller
supplies one likelihood ratio per hypothesis) and makes the update itself
deterministic: posterior ∝ prior × likelihood, normalized.  A hypothesis
the evidence does not discriminate keeps its prior implicitly (ratio 1.0).
"""

from __future__ import annotations

from math import isfinite
from typing import Any, Mapping

_FLOOR = 1e-9


def posterior(
    prior: Mapping[str, float],
    likelihood_ratios: Mapping[str, float],
) -> dict[str, float]:
    """Multiply priors by likelihood ratios and normalize to 1.

    Raises ``ValueError`` when the prior is empty or contains a
    non-positive entry (a zero prior can never be revived by evidence —
    fix the prior explicitly instead), or when a ratio is not a positive
    finite number.  Unmentioned hypotheses default to ratio 1.0.
    """
    if not prior:
        raise ValueError("prior cannot be empty")
    for name, probability in prior.items():
        if not isinstance(probability, (int, float)) or isinstance(probability, bool):
            raise ValueError(f"prior for {name} must be numeric")
        if probability <= 0:
            raise ValueError(
                f"prior for {name} is {probability}; a zero prior cannot be "
                "updated by evidence — revise the prior explicitly first"
            )
    unknown = set(likelihood_ratios) - set(prior)
    if unknown:
        raise ValueError(f"likelihood ratios for unknown hypotheses: {sorted(unknown)}")
    for name, ratio in likelihood_ratios.items():
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
            raise ValueError(f"likelihood ratio for {name} must be numeric")
        if not isfinite(ratio) or ratio <= 0:
            raise ValueError(f"likelihood ratio for {name} must be positive and finite")

    weighted = {
        name: max(float(prior[name]) * float(likelihood_ratios.get(name, 1.0)), _FLOOR)
        for name in prior
    }
    total = sum(weighted.values())
    # Each factor is finite, but their product is not bounded. On overflow the
    # division below would return all-zero posteriors while claiming to have
    # normalized to 1 — and 0.0 passes the probability validator, so the zeros
    # could be persisted and silently kill every hypothesis.
    if not isfinite(total) or total <= 0:
        raise ValueError(
            f"posterior weights summed to {total}, which cannot be normalized; "
            "the likelihood ratios span too many orders of magnitude"
        )
    return {name: value / total for name, value in weighted.items()}


def trace(
    prior: Mapping[str, float],
    likelihood_ratios: Mapping[str, float],
) -> dict[str, Any]:
    """Return prior/ratio/posterior rows suitable for direct persistence."""
    posterior_values = posterior(prior, likelihood_ratios)
    return {
        "rows": [
            {
                "hypothesis_id": name,
                "prior": round(float(prior[name]), 6),
                "likelihood_ratio": float(likelihood_ratios.get(name, 1.0)),
                "posterior": round(posterior_values[name], 6),
            }
            for name in sorted(prior)
        ]
    }
