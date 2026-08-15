"""Slow Path forecasting: calibration, extrapolation, Bayesian updates.

Every module here is a pure function over engine state — no I/O, no clock,
no entity writes.  Persistence and MCP exposure live in ``civ_mcp`` tool
adapters, so implementations stay replaceable (the ``Forecaster`` protocol
is the seam for higher-fidelity simulators, e.g. save-state replay through
the game's own rules engine).
"""

from .bayes import posterior, trace
from .calibration import calibration_report
from .extrapolate import TrendExtrapolator
from .protocol import (
    Branch,
    DEFAULT_FORECAST_METRICS,
    Forecaster,
    MetricSeries,
    numeric_metric_series,
)

__all__ = [
    "Branch",
    "DEFAULT_FORECAST_METRICS",
    "Forecaster",
    "MetricSeries",
    "TrendExtrapolator",
    "calibration_report",
    "numeric_metric_series",
    "posterior",
    "trace",
]
