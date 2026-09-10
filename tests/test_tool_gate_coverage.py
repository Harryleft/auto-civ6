"""Every registered MCP tool must be explicitly classified by the action gate.

The gate used to decide by absence: a tool in none of the sets simply executed
without a route. Adding a tool therefore granted it an ungoverned path, with no
error anywhere — the worst possible default for a system whose selling point is
that irreversible actions are governed. These tests turn that silent fail-open
into a failing build.
"""

from __future__ import annotations

import pytest

from civ_mcp.server import mcp, pipeline

REGISTERED = frozenset(mcp._tool_manager._tools)


def _classified() -> frozenset[str]:
    return (
        pipeline._BELIEF_GATED_TOOLS
        | pipeline._ROUTINE_TOOLS
        | pipeline._CONDITIONAL_GATE_TOOLS
    )


def test_every_registered_tool_is_classified():
    unclassified = sorted(REGISTERED - _classified())
    assert not unclassified, (
        "以下工具未出现在任何门禁分类中；新增工具必须显式归类到 "
        "_BELIEF_GATED_TOOLS、_ROUTINE_TOOLS 或 _CONDITIONAL_GATE_TOOLS：\n  "
        + "\n  ".join(unclassified)
    )


def test_no_classification_entry_is_stale():
    stale = sorted(_classified() - REGISTERED)
    assert not stale, (
        "以下门禁分类项已不存在于注册工具中（可能是改名或删除后未清理）：\n  "
        + "\n  ".join(stale)
    )


def test_gate_sets_do_not_overlap():
    overlap = sorted(pipeline._BELIEF_GATED_TOOLS & pipeline._ROUTINE_TOOLS)
    assert not overlap, f"工具同时被归类为需要路由与例行动作：{overlap}"


def test_council_required_tools_are_a_subset_of_routed_tools():
    """A council-gated tool must also require a route, or it could never reach one."""

    extra = sorted(pipeline._COUNCIL_REQUIRED_TOOLS - pipeline._BELIEF_GATED_TOOLS)
    assert not extra, f"议会门禁工具未同时要求信念路由：{extra}"


def test_unknown_tool_fails_closed():
    assert pipeline._belief_route_required("some_future_tool", {}) is True


@pytest.mark.parametrize(
    ("tool", "params", "expected"),
    [
        ("get_units", {}, False),
        ("get_game_overview", {}, False),
        ("set_research", {}, True),
        ("purchase_item", {}, True),
        ("skip_remaining_units", {}, False),
        ("dismiss_popup", {}, False),
        ("unit_action", {"action": "fortify"}, False),
        ("unit_action", {"action": "FOUND_CITY"}, True),
        ("run_lua", {"context": "gamecore"}, False),
        ("run_lua", {"context": "ingame"}, True),
        ("propose_trade", {"mode": "test"}, False),
        ("propose_trade", {"mode": "send"}, True),
    ],
)
def test_representative_routing_decisions_are_unchanged(tool, params, expected):
    assert pipeline._belief_route_required(tool, params) is expected
