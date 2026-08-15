"""World-model tools: calibration, forecasting, Bayesian rebalancing.

Adapters over the pure ``civ6_belief_engine.forecast`` functions.  Unlike
``tools/belief.py`` (the graph_plan phase-4 deletion unit), this module is a
long-lived surface: the pure functions it exposes survive projection
changes.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import Context

from civ6_belief_engine.belief_engine import BeliefEngine, BeliefEngineError
from civ6_belief_engine.forecast import (
    DEFAULT_FORECAST_METRICS,
    TrendExtrapolator,
    calibration_report,
    numeric_metric_series,
)
from civ_mcp.server import pipeline

from civ_mcp.server.assembly import mcp

_MAX_HORIZON = 40
_FORECASTER_TAG = "trend:v1"


@mcp.tool(annotations={"readOnlyHint": True})
async def get_calibration_report(ctx: Context) -> str:
    """Score resolved predictions: Brier, reliability buckets, observability.

    Use it before trusting any self-reported probability: a bucket whose
    observed frequency diverges from its mean claimed probability is a
    systematic bias to correct, and a low observability rate means
    predictions are being written against metrics that never get queried.
    """

    return await pipeline._belief_tool(
        ctx,
        "get_calibration_report",
        {},
        lambda engine, turn: calibration_report(engine.list("prediction", status=None)),
    )


@mcp.tool()
async def run_trend_forecast(
    ctx: Context,
    horizon_turns: int = 10,
    metrics: str = "[]",
) -> str:
    """Project metric futures under conservative/baseline/aggressive trends.

    Extrapolates least-squares trends from the observation history into
    ``simulation`` branch entities the council can cite as evidence.  Only
    metrics with at least three observed turns can project; pass a metric
    list to focus the forecast.  Prior simulations from the same forecaster
    are archived (tombstoned), never silently discarded.
    """

    if not 1 <= horizon_turns <= _MAX_HORIZON:
        return f"Error: horizon_turns must be between 1 and {_MAX_HORIZON}"
    params: dict[str, Any] = {"horizon_turns": horizon_turns, "metrics": metrics}

    try:
        metric_filter = json.loads(metrics) if metrics not in ("[]", "") else None
        if metric_filter is not None and (
            not isinstance(metric_filter, list)
            or not all(isinstance(item, str) for item in metric_filter)
        ):
            raise ValueError("metrics must be a JSON list of metric names")
    except json.JSONDecodeError as exc:
        return f"Error: metrics is not valid JSON: {exc}"

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        selected = (
            [name for name in metric_filter if name in DEFAULT_FORECAST_METRICS or name.startswith("diplomacy.")]
            if metric_filter
            else list(DEFAULT_FORECAST_METRICS)
        )
        series = numeric_metric_series(
            engine.list("observation", status="active"), metrics=selected
        )
        forecaster = TrendExtrapolator()
        branches = forecaster.forecast(
            series, current_turn=turn, horizon_turns=horizon_turns
        )
        usable = [branch for branch in branches if branch.projections]
        if not usable:
            return {
                "turn": turn,
                "forecaster": _FORECASTER_TAG,
                "projected_metrics": sorted(series),
                "branches": [],
                "message": (
                    "No metric has enough observed history to project. "
                    "Query get_game_overview for a few turns first."
                ),
            }
        for stale in engine.list("simulation", status="active"):
            if stale.get("forecaster") == _FORECASTER_TAG:
                engine.delete(
                    "simulation", stale["id"], turn=turn, reason="superseded forecast"
                )
        created: list[dict[str, Any]] = []
        for branch in usable:
            entity = engine.create(
                "simulation",
                {
                    "statement": f"{branch.label} over {branch.horizon_turns} turns",
                    "branch_label": branch.label,
                    "scenario": branch.scenario,
                    "horizon_turns": branch.horizon_turns,
                    "forecaster": _FORECASTER_TAG,
                    "assumptions": list(branch.assumptions),
                    "current_turn": turn,
                    "projections": {
                        metric: [[t, v] for t, v in points]
                        for metric, points in sorted(branch.projections.items())
                    },
                },
                turn=turn,
            )
            created.append(
                {
                    "simulation_id": entity["id"],
                    "scenario": branch.scenario,
                    "branch_label": branch.label,
                    "projections": {
                        metric: {"T" + str(t): v for t, v in points}
                        for metric, points in sorted(branch.projections.items())
                    },
                }
            )
        return {
            "turn": turn,
            "forecaster": _FORECASTER_TAG,
            "horizon_turns": horizon_turns,
            "projected_metrics": sorted(series),
            "assumptions": list(usable[0].assumptions),
            "branches": created,
        }

    return await pipeline._belief_tool(ctx, "run_trend_forecast", params, _operation)


@mcp.tool()
async def rebalance_hypotheses_bayesian(
    ctx: Context,
    topic_id: str,
    likelihood_ratios: str,
    evidence_id: str = "",
) -> str:
    """Update a hypothesis pool deterministically from evidence ratios.

    Supply one likelihood ratio per hypothesis as JSON
    (``{"hyp_1": 3.0, "hyp_2": 0.5}``); hypotheses the evidence does not
    discriminate are omitted (ratio 1.0).  The posterior arithmetic is
    computed server-side and recorded in the event log.
    """

    params: dict[str, Any] = {
        "topic_id": topic_id,
        "likelihood_ratios": likelihood_ratios,
        "evidence_id": evidence_id,
    }
    try:
        ratios = json.loads(likelihood_ratios)
        if not isinstance(ratios, dict) or not ratios:
            raise ValueError("likelihood_ratios must be a non-empty JSON object")
        for name, ratio in ratios.items():
            if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
                raise ValueError(f"ratio for {name} must be numeric")
    except json.JSONDecodeError as exc:
        return f"Error: likelihood_ratios is not valid JSON: {exc}"
    except ValueError as exc:
        return f"Error: {exc}"

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        return {
            "topic_id": topic_id,
            "hypotheses": engine.rebalance_hypotheses_bayesian(
                topic_id, ratios, turn=turn, evidence_id=evidence_id
            ),
        }

    return await pipeline._belief_tool(
        ctx, "rebalance_hypotheses_bayesian", params, _operation
    )
