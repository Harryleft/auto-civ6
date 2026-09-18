"""Contracts for the switchable lean play profile.

The refactor's central claim is that a player who is not asked to maintain
governance artifacts is more reliable, not merely differently prompted. That
claim is only testable if the lean path really is a different *deployment*:
governance off, control-plane tools absent from the model's list, and the role
prompt free of the proposal/probability protocol.

Three things are kept deliberately separate here, because they are separate
guarantees:

* **configuration** — one profile, one belief mode, and a loud failure when an
  explicit lean contradicts an explicit observe/enforce;
* **visibility** — what ``tools/list`` answers;
* **execution permission** — whether a hand-written tool name can still reach
  the control plane in-process.

DSH has no MCP tool allowlist (its client registers every advertised tool), so
both the surface and the guard have to live inside the MCP server.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
from types import SimpleNamespace

import pytest

from civ6_belief_engine.belief_mode import BELIEF_MODE_ENV, BeliefMode
from civ_mcp.server import mcp
from civ_mcp.server import pipeline
from civ_mcp.server.assembly import (
    LEAN_HIDDEN_CONTROL_TOOLS,
    PlayProfile,
    PlayProfileConflictError,
    apply_play_profile,
    hidden_tool_names,
    play_profile_report,
    registered_control_plane_tool_names,
    resolve_play_profile,
)
from civ_mcp.server.tools import end_turn_flow

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEAN_OVERLAY = ROOT / "integrations" / "deepseek-harness" / "civ6-lean.cordis.yml"
AGENT_OVERLAY = ROOT / "integrations" / "deepseek-harness" / "civ6-agent.cordis.yml"
WRAPPER = ROOT / "scripts" / "civ6_agent"
HARNESS = ROOT / "scripts" / "deepseek_harness"

# Game-domain tools that must survive the lean profile: the plan forbids
# deleting game capability to hit a tool-count target.
_REQUIRED_GAME_TOOLS = frozenset(
    {
        "get_game_overview",
        "get_units",
        "get_cities",
        "get_city_production",
        "set_city_production",
        "unit_action",
        "city_action",
        "end_turn",
        "skip_remaining_units",
        "get_diplomacy",
        "get_religion_overview",
        "get_world_congress",
        "queue_wc_votes",
        "get_governors",
        "get_trade_routes",
        "get_victory_progress",
        "get_tech_civics",
        "get_policies",
        "get_notifications",
        "propose_trade",
        "get_spies",
        "get_era_progress",
    }
)


BEFORE_LEAN = dict(mcp._tool_manager._tools)


@pytest.fixture
def lean_surface():
    """Apply the lean profile and always put the legacy surface back."""

    change = apply_play_profile(PlayProfile.LEAN)
    try:
        yield change
    finally:
        change.restore()
    assert frozenset(mcp._tool_manager._tools) == frozenset(BEFORE_LEAN), (
        "lean profile fixture must restore the exact legacy tool surface"
    )


def _context(profile: PlayProfile, mode: BeliefMode = BeliefMode.OFF):
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(play_profile=profile, belief_mode=mode)
        )
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_default_profile_is_legacy() -> None:
    assert PlayProfile.from_env({}) is PlayProfile.LEGACY


@pytest.mark.parametrize(
    ("value", "expected"),
    [("legacy", PlayProfile.LEGACY), ("lean", PlayProfile.LEAN), (" LEAN ", PlayProfile.LEAN)],
)
def test_profile_parsing_normalizes(value: str, expected: PlayProfile) -> None:
    assert PlayProfile.from_env({"CIV_MCP_PLAY_PROFILE": value}) is expected


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="legacy, lean"):
        PlayProfile.from_env({"CIV_MCP_PLAY_PROFILE": "turbo"})


def test_legacy_keeps_the_configured_belief_mode() -> None:
    assert (
        PlayProfile.LEGACY.effective_belief_mode({BELIEF_MODE_ENV: "observe"})
        is BeliefMode.OBSERVE
    )
    assert PlayProfile.LEGACY.effective_belief_mode({}) is BeliefMode.ENFORCE


def test_lean_pins_governance_off() -> None:
    assert PlayProfile.LEAN.effective_belief_mode({}) is BeliefMode.OFF
    assert PlayProfile.LEAN.effective_belief_mode({BELIEF_MODE_ENV: "off"}) is BeliefMode.OFF


@pytest.mark.parametrize("mode", ["observe", "enforce", "ENFORCE"])
def test_lean_contradicting_an_explicit_belief_mode_fails_loudly(mode: str) -> None:
    """The contradiction must not be resolved by quietly overriding either side."""

    with pytest.raises(PlayProfileConflictError, match="CIV_MCP_PLAY_PROFILE=lean"):
        resolve_play_profile(
            {"CIV_MCP_PLAY_PROFILE": "lean", BELIEF_MODE_ENV: mode}
        )


def test_blank_belief_mode_counts_as_unconfigured() -> None:
    """A launcher forwarding an empty variable must not break startup."""

    assert BeliefMode.from_env({BELIEF_MODE_ENV: ""}) is BeliefMode.ENFORCE
    assert BeliefMode.from_env({BELIEF_MODE_ENV: "   "}) is BeliefMode.ENFORCE
    assert PlayProfile.LEAN.effective_belief_mode({BELIEF_MODE_ENV: ""}) is BeliefMode.OFF


def test_report_matches_what_the_process_resolves() -> None:
    import json

    report = json.loads(play_profile_report())

    assert report["play_profile"] == resolve_play_profile().value
    assert report["belief_mode"] == resolve_play_profile().effective_belief_mode().value
    assert report["tool_count"] == len(mcp._tool_manager._tools)
    # The RUNTIME POLICY block and the dry-run preview read the same object.
    assert report["runtime_policy"]["belief_mode"] == report["belief_mode"]


# ---------------------------------------------------------------------------
# Visibility: the served tool surface
# ---------------------------------------------------------------------------


def test_the_hidden_list_matches_the_control_plane_modules() -> None:
    """Adding a control-plane tool must fail here, not leak into lean."""

    assert LEAN_HIDDEN_CONTROL_TOOLS == registered_control_plane_tool_names()
    assert len(LEAN_HIDDEN_CONTROL_TOOLS) == 29


def test_legacy_withholds_nothing() -> None:
    assert hidden_tool_names(PlayProfile.LEGACY) == frozenset()


def test_lean_removes_exactly_the_control_plane(lean_surface) -> None:
    served = {tool.name for tool in asyncio.run(mcp.list_tools())}

    assert set(lean_surface.removed) == LEAN_HIDDEN_CONTROL_TOOLS
    assert served.isdisjoint(LEAN_HIDDEN_CONTROL_TOOLS), (
        "精简模式只靠提示词要求“不使用”是不够的：控制面工具必须离开工具列表"
    )
    assert BEFORE_LEAN.keys() - LEAN_HIDDEN_CONTROL_TOOLS == served


def test_lean_does_not_delete_game_capability_or_the_lua_boundary(lean_surface) -> None:
    served = {tool.name for tool in asyncio.run(mcp.list_tools())}

    assert _REQUIRED_GAME_TOOLS <= served, (
        f"精简模式丢失了游戏能力: {sorted(_REQUIRED_GAME_TOOLS - served)}"
    )
    # CIV_MCP_DISABLE_LUA, not the play profile, owns the Lua boundary.
    assert "run_lua" in served
    for process_tool in ("kill_game", "launch_game", "restart_and_load", "load_game_save"):
        assert process_tool in served


def test_removed_tools_are_not_reachable_by_a_hand_written_name(lean_surface) -> None:
    """The MCP router cannot reach a tool that is no longer registered."""

    for name in sorted(LEAN_HIDDEN_CONTROL_TOOLS):
        assert name not in mcp._tool_manager._tools


# ---------------------------------------------------------------------------
# Execution permission: separate from visibility
# ---------------------------------------------------------------------------


def test_the_guard_refuses_control_plane_tools_under_lean() -> None:
    lean = _context(PlayProfile.LEAN)

    for name in ("route_belief_decision", "submit_governance_proposal", "get_belief_state"):
        refusal = pipeline._hidden_tool_refusal(lean, name)

        assert refusal is not None
        assert "TOOL_NOT_AVAILABLE_IN_PLAY_PROFILE" in refusal
        assert name in refusal
        assert "--play-profile legacy" in refusal


def test_the_guard_allows_everything_under_legacy() -> None:
    legacy = _context(PlayProfile.LEGACY, mode=BeliefMode.ENFORCE)

    assert pipeline._hidden_tool_refusal(legacy, "route_belief_decision") is None
    assert pipeline._hidden_tool_refusal(legacy, "get_units") is None


def test_the_guard_does_not_touch_game_tools_under_lean() -> None:
    lean = _context(PlayProfile.LEAN)

    for name in ("get_units", "end_turn", "set_city_production", "run_lua"):
        assert pipeline._hidden_tool_refusal(lean, name) is None


def test_logged_refuses_before_any_game_access() -> None:
    """A hand-written control-plane name must not reach the game or the journal."""

    called: list[str] = []

    async def operation() -> str:
        called.append("ran")
        return "should not happen"

    result = asyncio.run(
        pipeline._logged(
            _context(PlayProfile.LEAN),
            "submit_governance_proposal",
            {},
            operation,
        )
    )

    assert called == []
    assert "TOOL_NOT_AVAILABLE_IN_PLAY_PROFILE" in result


def test_belief_tool_refuses_before_the_off_mode_short_circuit() -> None:
    """The refusal must say why, not report an unrelated 'belief disabled' state."""

    result = asyncio.run(
        pipeline._belief_tool(
            _context(PlayProfile.LEAN),
            "route_belief_decision",
            {},
            lambda _engine, _turn: {"ran": True},
        )
    )

    assert "TOOL_NOT_AVAILABLE_IN_PLAY_PROFILE" in result
    assert "disabled" not in result


def test_off_mode_still_short_circuits_non_hidden_work() -> None:
    """Legacy ``off`` semantics are unchanged by the profile work."""

    result = asyncio.run(
        pipeline._belief_tool(
            _context(PlayProfile.LEGACY),
            "get_turn_brief",
            {},
            lambda _engine, _turn: {"ran": True},
        )
    )

    assert "disabled" in result


def test_lean_never_routes_a_council_action_and_never_replays_the_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No governance snapshot, no belief replay, no council approval under lean."""

    touched: list[str] = []

    async def belief_context(_ctx):
        touched.append("belief_context")  # would replay the journal
        return object(), 11

    monkeypatch.setattr(pipeline, "_belief_context", belief_context)

    result = asyncio.run(
        pipeline._belief_action_preflight(
            _context(PlayProfile.LEAN),
            "set_research",
            {"tech_name": "TECH_POTTERY"},
        )
    )

    assert touched == [], "lean must not bind or replay the belief journal"
    assert result["authorized"] is True
    assert result["route"] == "routine"
    assert result["decision_id"] is None


def test_lean_governance_snapshot_is_explicitly_disabled() -> None:
    """The overview must lose the governance block, not fail or half-render it."""

    mode = PlayProfile.LEAN.effective_belief_mode({})

    assert mode.captures_governance_snapshot is False
    assert mode.records_events is False
    assert mode.enforces_actions is False
    assert mode.runtime_policy()["governance"] == "disabled"
    assert mode.runtime_policy()["action_routing"] == "bypassed"


# ---------------------------------------------------------------------------
# end_turn reflections
# ---------------------------------------------------------------------------


def _stub_game_state() -> SimpleNamespace:
    """Minimal game object: ``_run_end_turn_impl`` reads ``gs.end_turn`` before
    the patched pipeline runs, even though it never awaits it here."""

    async def end_turn(*, poll_deadline=None) -> str:
        return "stub"

    return SimpleNamespace(end_turn=end_turn)


def _stub_pipeline_io(monkeypatch: pytest.MonkeyPatch, profile: PlayProfile) -> None:
    """Patch every pipeline accessor a clean end_turn advance would touch."""

    async def fake_logged(*_args, **_kwargs) -> str:
        return "Turn 10 -> 11"

    noop = lambda *_: None  # noqa: E731 - tiny stub, used inline below
    monkeypatch.setattr(pipeline, "_get_play_profile", lambda _ctx: profile)
    monkeypatch.setattr(pipeline, "_logged", fake_logged)
    monkeypatch.setattr(
        pipeline,
        "_get_logger",
        lambda _ctx: SimpleNamespace(
            session_id="t", set_agent_model=noop, set_turn=noop
        ),
    )
    monkeypatch.setattr(
        pipeline, "_get_spatial", lambda _ctx: SimpleNamespace(set_turn=noop)
    )
    monkeypatch.setattr(
        pipeline, "_get_camera", lambda _ctx: SimpleNamespace(clear=noop)
    )
    monkeypatch.setattr(
        pipeline, "_get_watchdog", lambda _ctx: SimpleNamespace(arm=noop)
    )


@pytest.mark.parametrize(
    ("profile", "expect_refusal"),
    [(PlayProfile.LEGACY, True), (PlayProfile.LEAN, False)],
)
def test_reflection_requirement_follows_the_profile(
    monkeypatch: pytest.MonkeyPatch, profile: PlayProfile, expect_refusal: bool
) -> None:
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(game=_stub_game_state())
        )
    )
    _stub_pipeline_io(monkeypatch, profile)

    result = asyncio.run(end_turn_flow._run_end_turn_impl(ctx, deadline=1e12))

    if expect_refusal:
        assert "Empty reflections" in result
        for field in ("tactical", "strategic", "tooling", "planning", "hypothesis"):
            assert field in result
    else:
        assert "Empty reflections" not in result
        # The turn really advanced: an empty diary must not block the loop.
        # ``_render_result`` localizes the model-facing copy, so assert on the
        # normalized advance marker rather than on the raw English payload.
        assert "10 → 11" in result


def test_lean_end_turn_does_not_invent_reflection_text() -> None:
    """Empty stays empty: the diary must not claim observations nobody made."""

    source = (ROOT / "src" / "civ_mcp" / "server" / "tools" / "end_turn_flow.py").read_text(
        encoding="utf-8"
    )

    assert "requires_reflections" in source
    # No placeholder is written on the model's behalf.
    for invented in ('"No issues"', '"已无问题"', '"No issues found"'):
        assert invented not in source


# ---------------------------------------------------------------------------
# The lean role prompt and the overlay wiring
# ---------------------------------------------------------------------------


def test_lean_persona_drops_the_governance_protocol() -> None:
    text = LEAN_OVERLAY.read_text(encoding="utf-8")

    for removed in (
        "action_intents",
        "proposal_id",
        "success_probability",
        "council_decision_id",
        "budget_locks",
        "opportunity_cost",
        "hard_constraints",
        "参数/验证错误，立刻停止当前会话",
        "阅读源码/文档来反推格式",
        "mcp__civ6__* 工具观察和操作游戏；本次是纯游戏回合循环",
    ):
        assert removed not in text, f"精简角色不应再包含治理/开发协议内容: {removed}"


def test_lean_persona_keeps_the_safety_distinctions() -> None:
    text = LEAN_OVERLAY.read_text(encoding="utf-8")

    # Pre-execution rejection -> bounded correction is allowed.
    assert "有界修正后重试同一动作" in text
    # Outcome unknown -> read-only verification, never a resend.
    assert "绝不要重发" in text
    assert "只读核验" in text
    # Permission refusal -> stop, never route around it.
    assert "不要换一条" in text
    assert "绕过治理或安全边界" in text


def test_lean_overlay_is_applied_after_the_agent_overlay() -> None:
    """A `config` patch replaces wholesale, so order decides the persona."""

    text = HARNESS.read_text(encoding="utf-8")
    # Anchor on the runtime block: the check block above it uses a differently
    # named array, and its own ordering is asserted by `deepseek_harness check`.
    runtime = text.split('patch_args=(--patch "$patch_file")', 1)[1]
    agent_index = runtime.index('patch_args+=(--patch "$civ_agent_patch_file")')
    lean_index = runtime.index('patch_args+=(--patch "$civ_lean_patch_file")')
    assert agent_index < lean_index


def test_the_play_profile_reaches_the_mcp_child() -> None:
    overlay = (
        ROOT / "integrations" / "deepseek-harness" / "civ6.cordis.yml"
    ).read_text(encoding="utf-8")

    assert "CIV_MCP_PLAY_PROFILE" in overlay
    assert "CIV_MCP_BELIEF_MODE" in overlay
    # The child is where the surface is decided; the wrapper must export it.
    assert "CIV_MCP_PLAY_PROFILE=" in WRAPPER.read_text(encoding="utf-8")


def test_agents_md_is_not_assumed_to_be_read_by_the_agent_overlay() -> None:
    """AGENTS.md arrives as a user message via agent-instructions, which is off."""

    text = AGENT_OVERLAY.read_text(encoding="utf-8")

    assert "- id: agent-instructions\n  disabled: true" in text


def test_agent_overlay_governance_text_is_left_for_legacy() -> None:
    """Legacy behavior must not change: its role text is still there."""

    text = AGENT_OVERLAY.read_text(encoding="utf-8")

    assert "每个 `action_intents` 项必须有非空" in text
    assert re.search(r"success_probability", text)
