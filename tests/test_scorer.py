"""Regression tests for CivBench governance scoring."""

import importlib.util
import json
import sys
from types import ModuleType


if importlib.util.find_spec("inspect_ai") is None:
    # inspect-ai is an optional eval dependency.  The helper tests do not need
    # its runtime, so provide the tiny import surface scorer.py decorates with.
    inspect_ai = ModuleType("inspect_ai")
    inspect_scorer = ModuleType("inspect_ai.scorer")
    inspect_solver = ModuleType("inspect_ai.solver")

    class _InspectPlaceholder:
        pass

    def _metric():
        return object()

    def _scorer(**_kwargs):
        return lambda function: function

    inspect_scorer.Score = _InspectPlaceholder
    inspect_scorer.Target = _InspectPlaceholder
    inspect_scorer.mean = _metric
    inspect_scorer.stderr = _metric
    inspect_scorer.scorer = _scorer
    inspect_solver.TaskState = _InspectPlaceholder
    inspect_ai.scorer = inspect_scorer
    inspect_ai.solver = inspect_solver
    sys.modules.update(
        {
            "inspect_ai": inspect_ai,
            "inspect_ai.scorer": inspect_scorer,
            "inspect_ai.solver": inspect_solver,
        }
    )

from evals.scorer import ToolCall, _governance_metrics


def _call(
    name: str,
    result: str,
    *,
    arguments: dict | None = None,
    inspect_error: bool = False,
) -> ToolCall:
    return ToolCall(
        name=name,
        arguments=arguments or {},
        result=result,
        inspect_error=inspect_error,
    )


def _brief(turn: int) -> str:
    return json.dumps(
        {
            "turn": turn,
            "snapshot": {
                "snapshot_id": f"snapshot:{turn}",
                "turn_before": turn,
                "turn_after": turn,
            },
        }
    )


def test_governance_coverage_uses_unique_completed_turns_and_unique_brief_turns():
    calls = [
        _call("get_governance_brief", _brief(10)),
        _call("get_governance_brief", _brief(10)),  # duplicate must not inflate
        _call("end_turn", "Turn 10 -> 11 | Score: 50"),
        _call("end_turn", "Turn 10 -> 11 | Score: 50"),  # retry/replay duplicate
        _call("end_turn", "Cannot end turn: production required"),
        _call("end_turn", "Turn paused by diplomacy"),
        _call("get_governance_brief", _brief(11)),
        _call("end_turn", "Turn 11 -> 12\n== Events =="),
    ]

    result = _governance_metrics(calls)

    assert result["governance_coverage"] == 1.0
    assert result["governance_briefs"] == 3.0


def test_governance_coverage_is_intersection_over_real_completed_turns():
    calls = [
        _call("get_governance_brief", _brief(20)),
        _call("get_governance_brief", _brief(99)),  # real JSON, wrong turn
        _call("get_governance_brief", "not JSON"),  # raw count only
        _call("get_governance_brief", json.dumps({"turn": True})),
        _call("get_governance_brief", "Error: snapshot failed"),
        _call("end_turn", "Turn 20 -> 21"),
        _call("end_turn", "Turn 21 -> 22"),
        _call("end_turn", "Turn 22 -> 23"),
        _call("end_turn", "Turn 23 -> 23"),  # no actual advancement
        _call("end_turn", "Turn 24 -> 25", inspect_error=True),
    ]

    result = _governance_metrics(calls)

    assert result["governance_coverage"] == 1 / 3
    # Preserve the original successful-call count independently of whether a
    # response is useful for the stricter coverage metric.
    assert result["governance_briefs"] == 4.0


def test_governance_coverage_is_zero_without_a_real_completed_turn():
    calls = [
        _call("get_governance_brief", _brief(30)),
        _call("get_governance_brief", _brief(30)),
        _call("end_turn", "Cannot end turn: units need orders"),
        _call("end_turn", "Turn paused — waiting for AI"),
    ]

    result = _governance_metrics(calls)

    assert result["governance_coverage"] == 0.0
    assert result["governance_briefs"] == 2.0


def test_governance_raw_route_and_council_counts_are_unchanged():
    calls = [
        _call(
            "route_belief_decision",
            "{}",
            arguments={"action_intent": {"tool": "set_research"}},
        ),
        _call("route_belief_decision", "{}", arguments={"action_intent": {}}),
        _call(
            "route_belief_decision",
            "Error: rejected",
            arguments={"action_intent": {"tool": "set_research"}},
        ),
        _call("resolve_governance_council", "{}"),
        _call("resolve_governance_council", "Error: no proposals"),
    ]

    result = _governance_metrics(calls)

    assert result["structured_routes"] == 1.0
    assert result["council_resolutions"] == 1.0
