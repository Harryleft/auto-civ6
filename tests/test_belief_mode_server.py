"""Live-loop contracts for the progressive Belief Engine modes."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from civ_mcp.belief_mode import BeliefMode
from civ_mcp.server import (
    get_governance_brief,
    get_turn_brief,
    route_belief_decision,
)
from civ_mcp.server import pipeline as server_module
from civ_mcp.server.assembly import PlayProfile
from civ_mcp.server.pipeline import (
    _append_belief_context,
    _belief_action_preflight,
    _format_runtime_policy,
    _logged,
    _record_belief_tool_result,
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


@pytest.mark.parametrize("mode", list(BeliefMode))
def test_runtime_policy_output_is_derived_from_belief_mode(mode):
    heading, payload = _format_runtime_policy(mode).split("\n", 1)

    assert heading == "=== RUNTIME POLICY ==="
    assert json.loads(payload) == mode.runtime_policy()


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
        "message": "off 模式下，信念引擎持久化已禁用。",
        "中文说明": {
            "工具": "执行工具 get_turn_brief",
            "状态": "已禁用",
            "说明": "以下原有字段为机器契约和游戏证据，字段名、ID、枚举及原始值保持不变。",
        },
    }


def test_lean_play_profile_lands_on_the_off_mode_guarantees():
    """The lean path is defined by off-mode behavior, not by the prompt alone."""

    profile_mode = PlayProfile.LEAN.effective_belief_mode({})

    assert profile_mode is BeliefMode.OFF
    assert profile_mode.records_events is False
    assert profile_mode.enforces_actions is False
    assert profile_mode.appends_context is False
    assert profile_mode.captures_governance_snapshot is False
    policy = profile_mode.runtime_policy()
    assert policy["governance"] == "disabled"
    assert policy["action_routing"] == "bypassed"
    assert policy["belief_context"] == "disabled"


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


def test_logged_keeps_model_annotations_out_of_belief_observations(monkeypatch):
    captured: dict[str, str] = {}

    class Logger:
        _turn = 9

        async def log_tool_call(self, _tool, _params, result, _duration_ms):
            captured["telemetry"] = result

    async def preflight(*_args, **_kwargs):
        return {"authorized": True, "decision_id": None, "route": "routine"}

    async def append_context(_ctx, _tool, result):
        return result + "\n\n=== BELIEF CONTEXT ===\nmode=enforce"

    async def record(_ctx, _tool, _params, result, *_args, **_kwargs):
        captured["belief"] = result

    async def operation():
        return "GAME RESULT"

    monkeypatch.setattr(server_module, "_belief_action_preflight", preflight)
    monkeypatch.setattr(server_module, "_append_belief_context", append_context)
    monkeypatch.setattr(server_module, "_record_belief_tool_result", record)
    monkeypatch.setattr(server_module.heartbeat, "write", lambda *_args, **_kwargs: None)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(logger=Logger())
        )
    )

    result = asyncio.run(_logged(ctx, "get_units", {}, operation))

    assert "信念上下文" in result
    # Telemetry keeps the unmodified result as audit evidence; only the
    # model/caller-facing return is localized.
    assert captured["telemetry"] == "GAME RESULT\n\n=== BELIEF CONTEXT ===\nmode=enforce"
    assert captured["belief"] == "GAME RESULT"
