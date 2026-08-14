"""Live-loop contracts for the progressive Belief Engine modes."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from civ_mcp.belief_mode import BeliefMode
from civ_mcp.server import (
    _append_belief_context,
    _belief_action_preflight,
    _record_belief_tool_result,
    get_governance_brief,
    get_turn_brief,
    route_belief_decision,
)


def _context(mode: BeliefMode):
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(belief_mode=mode)
        )
    )


@pytest.mark.parametrize("mode", [BeliefMode.OFF, BeliefMode.OBSERVE])
def test_lightweight_modes_bypass_action_preflight_without_belief_state(mode):
    result = asyncio.run(
        _belief_action_preflight(
            _context(mode),
            "unit_action",
            {"unit_id": 1, "action": "move", "target_x": 2, "target_y": 3},
        )
    )

    assert result == {
        "authorized": True,
        "decision_id": None,
        "route": "routine",
    }


@pytest.mark.parametrize("mode", [BeliefMode.OFF, BeliefMode.OBSERVE])
def test_lightweight_modes_do_not_append_belief_context(mode):
    result = asyncio.run(
        _append_belief_context(_context(mode), "get_units", "one unit")
    )

    assert result == "one unit"


def test_off_mode_does_not_record_tool_results():
    result = asyncio.run(
        _record_belief_tool_result(
            _context(BeliefMode.OFF),
            "get_units",
            {},
            "one unit",
            1,
            10,
            success=True,
        )
    )

    assert result is None


@pytest.mark.parametrize("mode", [BeliefMode.OFF, BeliefMode.OBSERVE])
def test_lightweight_modes_do_not_capture_governance_snapshot(mode):
    result = json.loads(asyncio.run(get_governance_brief(_context(mode))))

    assert result["belief_mode"] == mode.value
    assert result["disabled"] is True


def test_off_mode_disables_belief_persistence_tools_without_game_access():
    result = json.loads(asyncio.run(get_turn_brief(_context(BeliefMode.OFF))))

    assert result == {
        "belief_mode": "off",
        "disabled": True,
        "tool": "get_turn_brief",
        "message": "Belief Engine persistence is disabled in off mode.",
    }


@pytest.mark.parametrize("mode", [BeliefMode.OFF, BeliefMode.OBSERVE])
def test_lightweight_modes_make_route_tool_an_explicit_noop(mode):
    result = json.loads(
        asyncio.run(
            route_belief_decision(
                _context(mode),
                statement="move scout",
                probability=0.8,
                confidence=0.8,
                impact="low",
                urgency="low",
                irreversibility=0.1,
            )
        )
    )

    assert result["belief_mode"] == mode.value
    assert result["enforced"] is False
    assert result["authorized"] is True
