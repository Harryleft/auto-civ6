"""Runtime pipeline: the _logged wrapper, belief gating, and context accessors.

Every game tool flows through pipeline._logged (authorize precheck -> execute
-> belief recording -> result filtering). This is the future ActionPipeline
boundary described in graph_plan. Tests monkeypatch this module's attributes.
"""

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import Context

from civ6_belief_engine.belief_engine import (
    BeliefEngine,
    BeliefEngineError,
    action_args_hash,
)
from civ6_belief_engine.belief_mode import BeliefMode
from civ_mcp import game_launcher, heartbeat
from civ_mcp.facts import parse_envelope as _parse_fact_envelope
from civ_mcp.server.assembly import PlayProfile, hidden_tool_names
from civ_mcp.telemetry import EVENT_BELIEF_EVENT

log = logging.getLogger(__name__)
from civ_mcp.connection import LuaError
from civ_mcp.game_over_watchdog import GameOverWatchdog
from civ_mcp.game_state import GameState
from civ_mcp.logger import GameLogger
from civ_mcp.map_capture import MapCapture
from civ_mcp.presentation import action_receipt_status, localize_model_result
from civ_mcp.result_filter import filter_tool_result
from civ_mcp.spatial import SpatialTracker
from civ_mcp.spectator import CameraController, PopupWatcher
from civ_mcp.turn_context import (
    GATE_EXEMPT_TOOLS,
    TurnContext,
    TurnContextState,
    build_turn_context,
    turn_context_from_identity,
)

_SAVE_LOADING_TOOLS = frozenset({"load_save", "load_game_save", "load_save_from_menu"})


def _get_game(ctx: Context) -> GameState:
    return ctx.request_context.lifespan_context.game


def _get_logger(ctx: Context) -> GameLogger:
    return ctx.request_context.lifespan_context.logger


def _get_camera(ctx: Context) -> CameraController:
    return ctx.request_context.lifespan_context.camera


def _get_spatial(ctx: Context) -> SpatialTracker:
    return ctx.request_context.lifespan_context.spatial


def _get_map_capture(ctx: Context) -> MapCapture:
    return ctx.request_context.lifespan_context.map_capture


def _get_watchdog(ctx: Context) -> GameOverWatchdog:
    return ctx.request_context.lifespan_context.watchdog


def _get_beliefs(ctx: Context) -> BeliefEngine:
    return ctx.request_context.lifespan_context.beliefs


def _get_belief_mode(ctx: Context) -> BeliefMode:
    """Return the process mode, defaulting to legacy enforcement in tests."""

    return getattr(
        ctx.request_context.lifespan_context,
        "belief_mode",
        BeliefMode.ENFORCE,
    )


def _get_play_profile(ctx: Context) -> PlayProfile:
    """Return the process play profile, defaulting to legacy in tests."""

    return getattr(
        ctx.request_context.lifespan_context,
        "play_profile",
        PlayProfile.LEGACY,
    )


_HIDDEN_TOOL_REFUSAL = (
    "工具不可用 — {tool} 属于信念/治理控制面，已从精简游玩模式"
    f"（{PlayProfile.LEAN.value}）的工具面移走。该模式只保留游戏领域工具。\n"
    "这不是参数错误：重试、改写参数或换用其它治理工具都不会改变结果，"
    "也不要尝试绕过治理边界。\n"
    "如果需要治理/信念能力，请让启动方改用 --play-profile legacy 重新开始会话。\n"
    "TOOL_NOT_AVAILABLE_IN_PLAY_PROFILE"
)


def _hidden_tool_refusal(ctx: Context, tool_name: str) -> str | None:
    """Refuse a control-plane tool that the lean profile withheld.

    Removing a tool from the served surface stops the MCP router from reaching
    it, but a direct in-process call — or a client that hand-writes the tool
    name — would still invoke the function. Visibility and execution permission
    are separate guarantees, so this is checked independently of the surface.
    """

    if tool_name not in hidden_tool_names(_get_play_profile(ctx)):
        return None
    return _HIDDEN_TOOL_REFUSAL.format(tool=tool_name)


def _get_turn_context_state(ctx: Context) -> TurnContextState:
    """Return the per-session briefing bookkeeping, creating it if absent."""

    lifespan = ctx.request_context.lifespan_context
    state = getattr(lifespan, "turn_context_state", None)
    if not isinstance(state, TurnContextState):
        state = TurnContextState()
        try:
            lifespan.turn_context_state = state
        except (AttributeError, TypeError):  # pragma: no cover - defensive
            pass
    return state


def _turn_context_enabled(ctx: Context) -> bool:
    """Whether this process attaches the standing situation brief."""

    from civ_mcp.server.assembly import turn_context_enabled

    return turn_context_enabled(_get_play_profile(ctx))


def _tool_requires_briefing(tool_name: str) -> bool:
    """Return whether executing this tool needs the session to be oriented first.

    Derived from the MCP annotations the tools already carry, so a new tool is
    classified by its own declaration instead of a second hand-kept list.

    * registered and declared ``readOnlyHint`` -> no briefing needed;
    * registered and not declared read-only -> treated as a write;
    * not registered at all -> not gated. It is not part of the served surface,
      so the router (or the caller) owns that error, and gating an unknown name
      would only hide it behind a briefing.
    """

    from civ_mcp.server.assembly import mcp

    tool = mcp._tool_manager._tools.get(tool_name)
    if tool is None:
        return False
    annotations = getattr(tool, "annotations", None)
    return not bool(annotations and getattr(annotations, "readOnlyHint", False))


async def build_and_record_turn_context(ctx: Context) -> TurnContext | None:
    """Collect the briefing, record it as delivered, and return it.

    Returns ``None`` when collection fails. Callers decide what to do about it:
    ``get_game_overview`` degrades to a visible note, while the write gate
    refuses the write. Never raises, because a briefing failure must not turn a
    game tool into a protocol-level error.
    """

    gs = _get_game(ctx)
    try:
        context = await build_turn_context(gs)
    except Exception:
        log.warning("Failed to build the turn context briefing", exc_info=True)
        return None
    _get_turn_context_state(ctx).record(context, cache_epoch=_cache_epoch(gs))
    log.info(
        "Turn context: turn=%s consistency=%s write_allowed=%s calls=%d elapsed_ms=%d",
        context.turn,
        context.consistency,
        context.write_allowed,
        context.query_calls,
        context.elapsed_ms,
    )
    return context


def _cache_epoch(gs: Any) -> int | None:
    """Return the game's cache epoch, which bumps on every load/new game."""

    epoch = getattr(gs, "_cache_epoch", None)
    return epoch if type(epoch) is int else None


_TURN_CONTEXT_GATE_MARKER = "GATE:TURN_CONTEXT_REQUIRED"


async def _turn_context_gate(ctx: Context, tool_name: str) -> str | None:
    """Return a read-only briefing instead of executing a gated call.

    Two distinct reasons can hold a write back in the lean loop:

    * this session has not yet shown the model the current game's situation, so
      the first change would be made blind;
    * the last briefing could not confirm the game identity or the live state,
      so writing would act on an unconfirmed world.

    Reads are never gated, and neither are the tools needed to get *into* a
    game. A gate that cannot be satisfied is a permanent lockout, which the plan
    forbids: a single optional query failure may leave a gap, but it must not
    freeze every operation.

    The steady-state cost is O(1): the game's cache epoch bumps on every save
    load or new game, so a matching epoch plus a write-allowing briefing means
    the session is already oriented and no extra query is needed.
    """

    if not _get_play_profile(ctx).requires_entry_material:
        return None
    if tool_name in GATE_EXEMPT_TOOLS or not _tool_requires_briefing(tool_name):
        return None

    gs = _get_game(ctx)
    state = _get_turn_context_state(ctx)
    epoch = _cache_epoch(gs)
    if state.write_allowed and state.delivered_epoch == epoch and state.brief:
        return None

    reason = (
        "本会话尚未取得当前对局的入口材料，先只读返回局面，不执行原变更。"
        if not state.brief
        else "上一次局面简报未能确认对局身份或本国实时状态，写操作已停止；先只读核验。"
    )
    context = await build_and_record_turn_context(ctx)
    if context is None:
        log.warning("Turn context gate: briefing failed for %s", tool_name)
        return turn_context_from_identity(None)
    log.warning(
        "Turn context gate held back %s (write_allowed=%s)", tool_name, context.write_allowed
    )
    return (
        f"{reason}\n"
        f"请依据下面的局面重新决定，确认后再重新调用 {tool_name}；"
        "不要重复提交可能已经生效的改动。\n"
        f"{_TURN_CONTEXT_GATE_MARKER}\n\n{context.brief}"
    )


async def _await_auto_resume_ready(ctx: Context) -> None:
    """Keep game tools off the shared connection during DSH GUI recovery."""

    ready = getattr(
        ctx.request_context.lifespan_context,
        "auto_resume_ready",
        None,
    )
    if ready is not None and not ready.is_set():
        log.info("Waiting for DSH auto-resume before executing a game tool")
        await ready.wait()


def _format_runtime_policy(mode: BeliefMode) -> str:
    return "=== RUNTIME POLICY ===\n" + json.dumps(
        mode.runtime_policy(),
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def _flush_belief_events(ctx: Context) -> None:
    """Mirror locally persisted belief events into configured telemetry sinks."""
    if not _get_belief_mode(ctx).records_events:
        return
    for event in _get_beliefs(ctx).drain_events():
        await _get_logger(ctx)._emitter.emit(EVENT_BELIEF_EVENT, event)


async def _record_game_reload_epoch(
    ctx: Context,
    *,
    reason: str,
    turn: int | None = None,
    details: dict[str, Any] | None = None,
    confirmed: bool = False,
) -> None:
    """Mark a world rollback so the abandoned branch stops authorizing actions.

    Every path that loads a save must call this: the append-only journal still
    describes a future the game no longer has, and the authorizations recorded
    in it would otherwise stay consumable. Never raises — a bookkeeping failure
    must not break the recovery the caller is performing.
    """

    try:
        game = _get_game(ctx)
        transition = getattr(
            game, "confirm_world_changed" if confirmed else "mark_reload_uncertain", None
        )
        if callable(transition):
            transition()
        else:
            invalidate = getattr(game, "invalidate_cached_state", None)
            if callable(invalidate):
                invalidate()
    except Exception:
        log.error("Failed to clear game read caches (%s)", reason, exc_info=True)
    try:
        engine = _get_beliefs(ctx)
        if not _get_belief_mode(ctx).records_events or not engine.bound:
            return
        engine.record_game_reload(reason=reason, turn=turn, details=details or {})
        await _flush_belief_events(ctx)
    except Exception:
        log.error("Failed to record game reload epoch (%s)", reason, exc_info=True)


def _format_belief_turn_brief(brief: dict[str, Any]) -> str:
    """Render the machine-generated belief state into the turn-loop context."""
    gate = brief.get("decision_gate") or {}
    review = brief.get("review") or {}
    lines = [
        "\n\n=== BELIEF ENGINE TURN BRIEF (决策输入) ===",
        f"默认决策路由: {gate.get('default_route', 'fast')}",
    ]
    if review.get("predictions_resolved"):
        lines.append(
            "预测已验证: " + ", ".join(review["predictions_resolved"])
        )
    if review.get("predictions_overdue"):
        lines.append(
            "!! 预测逾期，必须复核: " + ", ".join(review["predictions_overdue"])
        )
    if review.get("plans_needing_replan"):
        lines.append(
            "!! 计划触发重规划: " + ", ".join(review["plans_needing_replan"])
        )
    if review.get("contradictions_created"):
        lines.append(
            "!! 新矛盾，禁止直接沿用旧假设: "
            + ", ".join(review["contradictions_created"])
        )
    if review.get("knowledge_stale"):
        lines.append(
            "!! 知识过期（N 回合未刷新，先补查再决策）: "
            + "; ".join(
                f"{item.get('id')} 未刷新 {item.get('unseen_turns')} 回合"
                + f"（建议 {item.get('refresh_tool')}）"
                for item in review["knowledge_stale"]
            )
        )

    beliefs = brief.get("beliefs") or []
    if beliefs:
        lines.append("当前信念:")
        for item in beliefs:
            flags = []
            if item.get("review_required"):
                flags.append("REVIEW")
            impact = item.get("impact", "medium")
            urgency = item.get("urgency", "medium")
            lines.append(
                "  - {id}: p={p:.2f}, conf={c:.2f}, {impact}/{urgency}{flags} — {statement}".format(
                    id=item.get("id", "?"),
                    p=float(item.get("probability", 0)),
                    c=float(item.get("confidence", 0)),
                    impact=impact,
                    urgency=urgency,
                    flags=f" [{','.join(flags)}]" if flags else "",
                    statement=item.get("statement", ""),
                )
            )
    else:
        lines.append("当前没有活动信念；对关键判断先建立可证伪信念再行动。")

    predictions = brief.get("predictions") or []
    if predictions:
        lines.append("活动预测: " + "; ".join(
            f"{item.get('id', '?')}@T{item.get('deadline_turn', '?')}: {item.get('statement', '')}"
            for item in predictions
        ))
    plans = brief.get("plans") or []
    if plans:
        lines.append("活动计划: " + "; ".join(
            f"{item.get('id', '?')}[{item.get('status', 'active')}]: {item.get('goal', '')}"
            for item in plans
        ))
    if gate.get("active_surprises"):
        lines.append("!! 活动 Surprise: " + ", ".join(gate["active_surprises"]))
    if gate.get("active_contradictions"):
        lines.append("!! 活动 Contradiction: " + ", ".join(gate["active_contradictions"]))
    history = brief.get("history_summary") or {}
    if any(window.get("metrics") for window in history.get("windows", [])):
        lines.append("历史窗口只汇总已观测回合，缺测不补值，也不代表当前事实或未来预测。")
    for window in history.get("windows", []):
        changes = []
        for metric, item in window.get("metrics", {}).items():
            change = item.get("change")
            if change is not None:
                changes.append(
                    f"{metric} {change:+g}（{item['sample_count']}/{window['available_turns']} 回合有观测，"
                    f"末次 T{item['last']['turn']}）"
                )
        if changes:
            lines.append(f"近 {window['window_turns']} 回合历史变化: " + "; ".join(changes))
    lines.append(
        "执行约束: 附近敌对单位只触发验证，不自动降低路线信念；路线风险必须使用 get_combat_estimate 的真实 CS/HP/预期互伤后再更新。"
    )
    lines.append(
        "高影响或不可逆行动前，必须调用 route_belief_decision，并在行动后核对结果。"
    )
    return "\n".join(lines)


def _format_world_changes(changes: Mapping[str, Any]) -> str:
    if changes.get("mode") == "baseline":
        return "世界变化：本次建立观测基线，后续采集再比较变化。"
    counts = changes.get("counts") or {}
    domains = changes.get("affected_domains") or []
    labels = {
        "science": "科研", "culture": "文化", "production": "生产",
        "economy": "经济", "military": "军事", "diplomacy": "外交", "map": "地图",
    }
    text = (
        f"世界变化：{counts.get('node_changes', 0)} 个对象、"
        f"{counts.get('edge_changes', 0)} 条关系；建议重评："
        + ("、".join(labels.get(domain, domain) for domain in domains) or "无新增领域")
        + "。范围限于本次观测，未观测对象需另行核实。"
    )
    for check in changes.get("checks_required", []):
        text += "\n待核实：" + str(check.get("reason", ""))
    return text


def _param_summary(params: dict[str, Any]) -> str:
    """Compact one-line summary of tool params for console logging."""
    if not params:
        return ""
    parts = []
    for k, v in params.items():
        s = str(v)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s}")
    return " ".join(parts)


def _result_summary(result: str) -> str:
    """First meaningful line of a result, truncated."""
    line = result.split("\n", 1)[0].strip()
    return line[:120] + "..." if len(line) > 120 else line


def _filter_downstream_result(
    tool_name: str,
    params: Mapping[str, Any],
    result: str,
) -> str:
    """Filter only the model-facing copy; fail open on local filter errors."""
    try:
        filtered = filter_tool_result(tool_name, result, params=params)
    except Exception:
        log.warning("Local result filter failed for %s", tool_name, exc_info=True)
        filtered = result
    return localize_model_result(tool_name, filtered, params=dict(params))


def _action_execution_status(
    tool_name: str,
    params: Mapping[str, Any],
    result: str,
    *,
    fallback: str | None = None,
) -> str | None:
    """Map a model-facing action receipt to the existing audit status.

    ``submitted`` is deliberately represented as ``unknown`` in the belief
    engine.  The engine has a fail-closed four-state contract and must not
    close a routed decision until a later read-back proves the postcondition;
    the presentation layer still exposes the more useful Chinese distinction
    between submitted and truly unknown (for example, a dead connection).
    """

    receipt = action_receipt_status(tool_name, result, params=dict(params))
    if receipt is None:
        return fallback
    status = receipt[0]
    return "unknown" if status == "submitted" else status


# Key actions are routed centrally here instead of relying on every tool
# implementation to remember the Belief Engine.  Routine maintenance actions
# (fortify/skip/heal/etc.) remain executable without an explicit route, while
# strategic, irreversible, or combat actions require one.
_ACTION_PARAM_DEFAULTS: dict[str, dict[str, Any]] = {
    "set_research": {"category": "tech"},
    "purchase_item": {"yield_type": "YIELD_GOLD"},
    "patronize_great_person": {"yield_type": "YIELD_GOLD"},
    "form_alliance": {"alliance_type": "MILITARY"},
    "run_lua": {"context": "gamecore"},
    "propose_trade": {
        "offer_gold": 0,
        "offer_gold_per_turn": 0,
        "offer_resources": "",
        "offer_favor": 0,
        "offer_open_borders": False,
        "request_gold": 0,
        "request_gold_per_turn": 0,
        "request_resources": "",
        "request_favor": 0,
        "request_open_borders": False,
        "joint_war_target": 0,
        "mode": "send",
    },
}


def _canonical_action_params(
    tool_name: str,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Use one public MCP action identity for proposal, route and execution."""

    if not isinstance(params, Mapping):
        raise BeliefEngineError("action arguments/params must be a JSON object")
    if not all(isinstance(key, str) and key for key in params):
        raise BeliefEngineError(
            "action arguments/params keys must be non-empty strings"
        )
    canonical = dict(_ACTION_PARAM_DEFAULTS.get(tool_name, {}))
    canonical.update({key: value for key, value in params.items() if value is not None})
    return canonical


def _normalize_trade_mode(mode: Any) -> str:
    normalized = str(mode or "send").strip().lower()
    if normalized not in {"test", "send"}:
        raise BeliefEngineError("trade mode must be test or send")
    return normalized


# ``choose_dedication`` is deliberately absent: it is a forced, current-turn
# Civ VI UI selection. Its Lua builder validates the offered index and reads
# back the result, while an extra council/action route would deadlock a turn.
_COUNCIL_REQUIRED_TOOLS = {
    "set_research",
    "set_policies",
    "change_government",
    "choose_pantheon",
    "found_religion",
    "appoint_governor",
    "assign_governor",
    "promote_governor",
    "send_envoy",
    "purchase_item",
    "recruit_great_person",
    "patronize_great_person",
    "reject_great_person",
    "propose_trade",
    "propose_peace",
    "form_alliance",
    "queue_wc_votes",
}


def _governance_council_required(
    *,
    tool: str,
    params: Mapping[str, Any],
    impact: str,
    irreversibility: float,
) -> bool:
    """Classify national/scarce-resource actions without trusting prose alone."""

    if tool == "propose_trade" and _normalize_trade_mode(
        params.get("mode", "send")
    ) == "test":
        return False
    if tool in _COUNCIL_REQUIRED_TOOLS:
        return True
    if str(impact).strip().lower() in {"high", "critical"}:
        return True
    if (
        not isinstance(irreversibility, bool)
        and isinstance(irreversibility, (int, float))
        and float(irreversibility) >= 0.7
    ):
        return True
    action = str(params.get("action") or "").strip().upper()
    if tool == "unit_action" and action in {
        "FOUND_CITY",
        "DELETE",
        "SACRIFICE_CHARGES",
    }:
        return True
    if tool == "city_action" and action in {
        "KEEP",
        "REJECT",
        "RAZE",
        "LIBERATE_FOUNDER",
        "LIBERATE_PREVIOUS",
    }:
        return True
    return tool == "send_diplomatic_action" and action == "DECLARE_WAR"


# Dedications remain absent here for the same current-turn blocker reason;
# all strategic and resource-changing actions below still require routing.
_BELIEF_GATED_TOOLS = {
    "spy_action",
    "set_city_production",
    "purchase_item",
    "set_research",
    "set_policies",
    "purchase_tile",
    "promote_unit",
    "upgrade_unit",
    "choose_pantheon",
    "found_religion",
    "appoint_governor",
    "assign_governor",
    "promote_governor",
    "send_envoy",
    "propose_trade",
    "respond_to_trade",
    "propose_peace",
    "respond_to_diplomacy",
    "send_diplomatic_action",
    "form_alliance",
    "city_action",
    "queue_wc_votes",
    "change_government",
    "recruit_great_person",
    "patronize_great_person",
    "reject_great_person",
    "set_city_focus",
    "run_lua",
}
_ROUTINE_UNIT_ACTIONS = {
    "fortify",
    "skip",
    "heal",
    "alert",
    "sleep",
    "automate",
}

# Tools that execute without a belief route. Every registered MCP tool must be
# classified exactly once: in _BELIEF_GATED_TOOLS, in _ROUTINE_TOOLS, or by one
# of the parameter-dependent branches below (unit_action / run_lua /
# propose_trade). tests/test_tool_gate_coverage.py fails when a newly
# registered tool is in none of them, and _belief_route_required fails closed
# in the meantime, so a forgotten classification cannot silently grant an
# ungoverned execution path.
_ROUTINE_TOOLS = frozenset({
    # Read-only queries: no route needed.
    "get_barbarian_overview", "get_belief_metrics", "get_belief_state",
    "get_belief_trace", "get_builder_tasks", "get_calibration_report",
    "get_cities", "get_city_production", "get_city_states",
    "get_climate_overview", "get_combat_estimate", "get_dedications",
    "get_diary", "get_diplomacy", "get_district_advisor",
    "get_empire_resources", "get_era_progress", "get_game_overview",
    "get_global_settle_advisor", "get_governance_brief",
    "get_governors", "get_gp_advisor", "get_great_people",
    "get_great_people_overview", "get_map_area", "get_notifications",
    "get_pantheon_beliefs", "get_pathing_estimate",
    "get_pending_diplomacy", "get_pending_trades", "get_policies",
    "get_purchasable_tiles", "get_religion_beliefs",
    "get_religion_overview", "get_religion_spread",
    "get_settle_advisor", "get_spies", "get_strategic_map",
    "get_tech_civics", "get_trade_destinations", "get_trade_options",
    "get_trade_routes", "get_turn_brief", "get_unit_promotions",
    "get_units", "get_victory_progress", "get_village_overview",
    "get_wonder_advisor", "get_world_congress",

    # Belief/governance control plane: mutates the journal, never the game.
    "cancel_routed_action", "delete_belief_entity",
    "rebalance_hypotheses_bayesian", "rebalance_hypothesis_pool",
    "recompute_failure_attribution", "record_action_verification",
    "record_observation", "resolve_governance_council",
    "resolve_prediction", "review_belief_engine",
    "review_governance_proposal", "route_belief_decision",
    "submit_governance_proposal", "update_belief_entity",
    "upsert_belief", "upsert_dynamic_plan",
    "upsert_failure_attribution", "upsert_hypothesis",
    "upsert_prediction", "upsert_strategic_goal",

    # Process and save-file lifecycle: outside the game write path.
    "kill_game", "launch_game", "list_saves", "load_game_save",
    "load_save", "load_save_from_menu", "restart_and_load",

    # Routine actions, plus tools the preflight handles separately.
    "assess_route_combat_risk", "choose_dedication", "dismiss_popup",
    "end_turn", "run_trend_forecast", "set_plan_status",
    "skip_remaining_units",
})

# Tools whose need for a route depends on their arguments, so they are decided
# by _belief_route_required rather than by a static set. run_lua and
# propose_trade are the mirror case: listed as gated, with a parameter branch
# that can exempt a read-only variant.
_CONDITIONAL_GATE_TOOLS = frozenset({"unit_action"})


def _belief_route_required(tool_name: str, params: dict[str, Any]) -> bool:
    if tool_name == "run_lua":
        # GameCore Lua is the documented read-only escape hatch; InGame Lua
        # can mutate the world and therefore needs an explicit route.
        return str(params.get("context", "gamecore")).lower() == "ingame"
    if tool_name == "propose_trade" and _normalize_trade_mode(
        params.get("mode", "send")
    ) == "test":
        # Testing an offer only asks the game for acceptance; it does not send
        # a deal and is evidence for the eventual governed action.
        return False
    if tool_name in _BELIEF_GATED_TOOLS:
        return True
    if tool_name == "unit_action":
        return str(params.get("action", "")).lower() not in _ROUTINE_UNIT_ACTIONS
    if tool_name in _ROUTINE_TOOLS:
        return False
    # Unclassified tool: fail closed rather than granting an ungoverned path.
    return True


_NEXT_CALL_FOR_STATE = {
    "authorized": "执行该决策绑定的动作（授权会被消费），或 cancel_routed_action 显式放弃",
    "executing": "动作执行中；若结果已返回则 record_action_verification 结算，否则等待",
    "retryable": "重试同一动作（重新调用即可再次消费授权），或 cancel_routed_action 显式放弃",
    "outcome_unknown": "必须先 record_action_verification 用读回证据结算，此前不可取消/重试",
    "not_routed": "route_belief_decision 传入 council_decision_id 与该已批准 action_intent",
}


_BLOCKER_HINTS = {
    "current_turn_typed_snapshot_missing": (
        "本回合缺少 typed snapshot：调用 get_governance_brief（或 get_game_overview）"
        "捕获后重试 end_turn"
    ),
    "governance_proposals_not_arbitrated": (
        "存在未仲裁提案：resolve_governance_council 完成仲裁"
    ),
}


def _format_governance_gate_reason(gate: dict[str, Any]) -> str:
    """Turn the gate payload into one actionable line per blocker.

    The old message named blocker categories only, which forced the caller to
    jump out of its turn loop into get_governance_brief bookkeeping — the
    measured pathology (40 no-op retries, end_turn 56% of blocks). Each line
    now names the stuck decision and the exact next tool call.
    """

    lines = ["治理回合门禁未完成: " + ", ".join(gate.get("blockers") or []) + "。"]
    for blocker in gate.get("blockers") or []:
        hint = _BLOCKER_HINTS.get(blocker)
        if hint:
            lines.append(f"- {blocker}: {hint}")
    pending = gate.get("pending_authorizations") or []
    if pending:
        lines.append("待清算授权（逐项处理后 end_turn 即可通过）:")
        for item in pending[:4]:
            state = str(item.get("decision_state") or "")
            lines.append(
                f"- {item.get('decision_id')} [{state}] → "
                + _NEXT_CALL_FOR_STATE.get(state, "用 get_governance_brief 查看详情")
            )
        if len(pending) > 4:
            lines.append(f"- …另有 {len(pending) - 4} 项，见 get_governance_brief")
    intents = gate.get("pending_council_intents") or []
    if intents:
        lines.append("议会意图未闭环:")
        for item in intents[:3]:
            state = str(item.get("decision_state") or "")
            lines.append(
                f"- {item.get('proposal_id')}/{item.get('intent_id') or 'intent'} "
                f"[{state}] → "
                + _NEXT_CALL_FOR_STATE.get(state, _NEXT_CALL_FOR_STATE["not_routed"])
            )
        if len(intents) > 3:
            lines.append(f"- …另有 {len(intents) - 3} 项，见 get_governance_brief")
    proposals = gate.get("active_proposal_ids") or []
    if proposals:
        lines.append(
            "未仲裁提案: " + ", ".join(proposals[:4]) + " → resolve_governance_council 仲裁"
        )
    lines.append("处理前不要重复调用 end_turn。")
    return "\n".join(lines)


async def _belief_action_preflight(
    ctx: Context, tool_name: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Run the common pre-action decision gate before touching the game."""
    if not _get_belief_mode(ctx).enforces_actions:
        return {"authorized": True, "decision_id": None, "route": "routine"}
    required = _belief_route_required(tool_name, params)
    if not required and tool_name not in {"unit_action", "end_turn"}:
        return {"authorized": True, "decision_id": None, "route": "routine"}
    engine, turn = await _belief_context(ctx)
    if tool_name == "end_turn":
        governance_gate = engine.governance_turn_gate(turn=turn)
        if not governance_gate["ready"]:
            return {
                "authorized": False,
                "decision_id": None,
                "route": "slow",
                "governance_gate": governance_gate,
                "reason": _format_governance_gate_reason(governance_gate),
            }
        brief = engine.turn_brief(turn=turn)
        gate = brief["decision_gate"]
        if gate.get("default_route") == "slow":
            return {
                "authorized": False,
                "decision_id": None,
                "route": "slow",
                "reason": (
                    "The turn brief has an unresolved slow gate. Review active "
                    "contradictions/surprises or replan before ending the turn."
                ),
            }
        return {
            "authorized": True,
            "decision_id": None,
            "route": gate.get("default_route", "fast"),
        }

    return engine.authorize_action(
        tool=tool_name,
        params=_canonical_action_params(tool_name, params),
        turn=turn,
        required=required,
    )


async def _append_belief_context(
    ctx: Context, tool_name: str, result: str
) -> str:
    """Make the gate visible alongside every normal game query."""
    if not _get_belief_mode(ctx).appends_context:
        return result
    if not tool_name.startswith("get_") or tool_name == "get_game_overview":
        return result
    try:
        engine, turn = await _belief_context(ctx)
        brief = engine.turn_brief(turn=turn)
        await _flush_belief_events(ctx)
        gate = brief["decision_gate"]
        review = brief["review"]
        flags = []
        for key, label in (
            ("beliefs_requiring_review", "beliefs"),
            ("plans_requiring_review", "plans"),
            ("active_surprises", "surprises"),
            ("active_contradictions", "contradictions"),
        ):
            values = gate.get(key) or []
            if values:
                flags.append(f"{label}={','.join(values[:6])}")
        stale = gate.get("knowledge_stale") or []
        if stale:
            flags.append(
                "stale=" + ",".join(str(item.get("id")) for item in stale[:6])
            )
        review_events = []
        for key in ("predictions_resolved", "predictions_overdue", "plans_needing_replan", "contradictions_created"):
            values = review.get(key) or []
            if values:
                review_events.append(f"{key}={','.join(values[:6])}")
        context = {
            "turn": turn,
            "default_route": gate.get("default_route", "fast"),
            "flags": flags,
            "blocking_scopes": gate.get("blocking_scopes") or [],
            "review": review_events,
            "note": (
                "Use get_turn_brief before a key action; nearby hostiles "
                "require quantified combat evidence."
            ),
        }
        # 信封结果：信念上下文合并进 JSON 结构，而不是破坏可解析性的尾部追加。
        parsed = _parse_fact_envelope(result)
        if parsed is not None:
            parsed["belief_context"] = context
            return json.dumps(parsed, ensure_ascii=False)
        return result + "\n" + "\n".join(
            [
                "\n\n=== BELIEF CONTEXT ===",
                f"turn={turn} default_route={gate.get('default_route', 'fast')}",
                "flags=" + ("; ".join(flags) if flags else "none"),
                "blocking_scopes="
                + (",".join(gate.get("blocking_scopes") or []) or "none"),
                "review=" + ("; ".join(review_events) if review_events else "none"),
                "Use get_turn_brief before a key action; nearby hostiles require quantified combat evidence.",
            ]
        )
    except Exception:
        log.debug("Belief context append failed for %s", tool_name, exc_info=True)
        return result


async def _record_belief_tool_result(
    ctx: Context,
    tool_name: str,
    params: dict[str, Any],
    result: str,
    turn: int | str,
    duration_ms: int,
    *,
    success: bool,
    decision_id: str | None = None,
    decision_route: str | None = None,
    execution_status: str | None = None,
) -> None:
    """Capture query facts and action verification without breaking gameplay."""
    if not _get_belief_mode(ctx).records_events:
        return
    try:
        engine = _get_beliefs(ctx)
        logger = _get_logger(ctx)
        if (
            getattr(_get_game(ctx), "_pending_end_turn", False) is True
            and (not engine.bound or logger._turn is None)
        ):
            return
        if not engine.bound:
            civ, seed = await _get_game(ctx).get_game_identity()
            await _bind_belief_engine(ctx, engine, civ=civ, seed=seed)
            logger.bind_game(civ, seed)
        if logger._turn is None:
            overview = await _get_game(ctx).get_game_overview()
            logger.set_turn(overview.turn)
        # get_game_overview learns and binds the turn inside its operation, so
        # the value captured by _logged before the call can still be unknown.
        current_turn = logger._turn
        observed_turn = current_turn if current_turn is not None else turn
        is_read_only_variant = (
            tool_name == "propose_trade"
            and _normalize_trade_mode(params.get("mode", "send")) == "test"
        ) or (
            tool_name == "run_lua"
            and str(params.get("context", "gamecore")).lower() == "gamecore"
        )
        category = (
            "turn"
            if tool_name == "end_turn"
            else "query"
            if tool_name.startswith("get_")
            or tool_name == "screenshot"
            or is_read_only_variant
            else "action"
        )
        engine.record_tool_result(
            tool=tool_name,
            params=(
                _canonical_action_params(tool_name, params)
                if category in {"action", "turn"}
                else params
            ),
            result=result,
            turn=int(observed_turn) if observed_turn != "?" else 0,
            category=category,
            success=success,
            duration_ms=duration_ms,
            decision_id=decision_id,
            decision_route=decision_route,
            execution_status=execution_status,
        )
        _sync_governance_graph(
            engine, turn=int(observed_turn) if observed_turn != "?" else 0
        )
        await _flush_belief_events(ctx)
    except Exception:
        log.warning("Belief Engine: failed to record tool result", exc_info=True)


def _sync_governance_graph(engine: Any, *, turn: int) -> None:
    """Synchronize the optional graph read model when the engine supports it."""

    sync = getattr(engine, "sync_governance_graph", None)
    if callable(sync):
        sync(turn=turn)


async def _logged(
    ctx: Context,
    tool_name: str,
    params: dict[str, Any],
    fn: Callable[[], Awaitable[str]],
    *,
    tiles: set[tuple[int, int]] | None = None,
    localize: bool = True,
) -> str:
    """Serialize load preparation and its receipt, including the state recheck."""
    if tool_name in _SAVE_LOADING_TOOLS:
        game = _get_game(ctx)
        lock = getattr(game, "_load_operation_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            game._load_operation_lock = lock
        async with lock:
            return await _logged_impl(ctx, tool_name, params, fn, tiles=tiles, localize=localize)
    return await _logged_impl(ctx, tool_name, params, fn, tiles=tiles, localize=localize)


async def _logged_impl(
    ctx: Context,
    tool_name: str,
    params: dict[str, Any],
    fn: Callable[[], Awaitable[str]],
    *,
    tiles: set[tuple[int, int]] | None = None,
    localize: bool = True,
) -> str:
    """Run a tool function with timing, error handling, and logging."""

    def _return_result(raw_result: str) -> str:
        """Keep internal callers on the raw contract when requested."""

        return (
            _filter_downstream_result(tool_name, params, raw_result)
            if localize
            else raw_result
        )

    async def _fail(result: str, execution_status: str) -> None:
        """Shared error tail: timing, log, belief record (caller returns)."""

        if tool_name in _SAVE_LOADING_TOOLS and (
            _load_was_submitted()
            or (execution_status == "unknown" and load_counter == "mutation_revision")
        ):
            await _record_game_reload_epoch(ctx, reason=f"{tool_name}_unknown")
        ms = int((time.monotonic() - start) * 1000)
        log.info(
            "[T%s] %s(%s) ERR %dms: %s",
            turn,
            tool_name,
            _param_summary(params),
            ms,
            _result_summary(result),
        )
        await logger.log_error(tool_name, result)
        await _record_belief_tool_result(
            ctx,
            tool_name,
            params,
            result,
            turn,
            ms,
            success=False,
            decision_id=decision_id,
            decision_route=decision_route,
            execution_status=execution_status,
        )

    refusal = _hidden_tool_refusal(ctx, tool_name)
    if refusal is not None:
        log.warning("Refused control-plane tool %s under the lean profile", tool_name)
        return _return_result(refusal)

    await _await_auto_resume_ready(ctx)
    game = getattr(ctx.request_context.lifespan_context, "game", None)
    pending_observation = (
        tool_name == "end_turn" and getattr(game, "_pending_end_turn", False) is True
    )
    if getattr(getattr(game, "conn", None), "reload_pending", False) is True:
        if tool_name == "get_game_overview":
            from civ_mcp.game_lifecycle import verify_loaded_world

            if await verify_loaded_world(game.conn):
                game.confirm_world_changed()
            else:
                return _return_result(
                    "读档结果尚未确认，旧回合请求仍保留；本次只读核验未确认新局面。"
                    "可稍后重读 get_game_overview，或由操作者选择独立恢复。"
                    " GATE:RELOAD_UNCONFIRMED"
                )
        elif _tool_requires_briefing(tool_name):
            return _return_result(
                "读档结果尚未确认，暂停游戏写入；先调用 get_game_overview 核验。"
                " GATE:RELOAD_UNCONFIRMED"
            )
    pending_input = (
        getattr(game, "_pending_end_turn", False) is True
        and tool_name in {"respond_to_diplomacy", "respond_to_trade", "queue_wc_votes"}
    )
    if not pending_observation and not pending_input:
        gate = await _turn_context_gate(ctx, tool_name)
        if gate is not None:
            return _return_result(gate)

    logger = _get_logger(ctx)
    turn = logger._turn or "?"
    start = time.monotonic()
    decision_context: dict[str, Any] = {
        "authorized": True,
        "decision_id": None,
        "route": "routine",
    }
    try:
        if not pending_observation:
            decision_context = await _belief_action_preflight(ctx, tool_name, params)
    except Exception as exc:
        result = f"Error: Belief preflight failed: {exc}"
        ms = int((time.monotonic() - start) * 1000)
        await logger.log_error(tool_name, result)
        await _record_belief_tool_result(
            ctx,
            tool_name,
            params,
            result,
            turn,
            ms,
            success=False,
            execution_status="blocked",
        )
        return _return_result(result)

    decision_id = decision_context.get("decision_id")
    decision_route = decision_context.get("route")
    if not decision_context.get("authorized", True):
        result = "BELIEF_GATE_REQUIRED: " + str(
            decision_context.get("reason") or "Resolve the Belief Engine gate before retrying."
        )
        ms = int((time.monotonic() - start) * 1000)
        log.info(
            "[T%s] %s(%s) BLOCKED %dms: %s",
            turn,
            tool_name,
            _param_summary(params),
            ms,
            _result_summary(result),
        )
        await logger.log_error(tool_name, result)
        await _record_belief_tool_result(
            ctx,
            tool_name,
            params,
            result,
            turn,
            ms,
            success=False,
            decision_id=decision_id,
            decision_route=decision_route,
            execution_status="blocked",
        )
        return _return_result(result)

    game = None
    load_revision = None
    load_counter = "mutation_revision"  # compatibility for older connection adapters
    load_known_rejected = False

    def _load_was_submitted() -> bool:
        revision = getattr(getattr(game, "conn", None), load_counter, None)
        return (
            not load_known_rejected
            and type(load_revision) is int and type(revision) is int
            and revision != load_revision
        )

    try:
        try:
            game = _get_game(ctx)
        except AttributeError:
            game = None
        collection = getattr(game, "read_collection", None)
        if tool_name in _SAVE_LOADING_TOOLS:
            connection = getattr(game, "conn", None)
            if type(getattr(connection, "load_revision", None)) is int:
                load_counter = "load_revision"
            load_revision = getattr(connection, load_counter, None)
            invalidate = getattr(game, "invalidate_cached_state", None)
            if callable(invalidate):
                invalidate()
        # Collection reuse ends before belief recording; a later evidence
        # request always reads the game, even on the same turn.
        read_scope = (
            collection()
            if tool_name.startswith("get_") and callable(collection)
            else nullcontext()
        )
        with read_scope:
            result = await fn()
        if tool_name in _SAVE_LOADING_TOOLS:
            load_known_rejected = "LOAD_NOT_SUBMITTED" in result
            receipt = action_receipt_status(tool_name, result, params=params)
            status = receipt[0] if receipt else "unknown"
            if status in {"succeeded", "submitted", "unknown"} or _load_was_submitted():
                # FrontEnd can start loading successfully then return Error
                # when its auto-continue step fails. Submission still abandons
                # the old world branch, even if the final receipt is a failure.
                reason = "submitted" if status in {"succeeded", "submitted"} else "unknown"
                await _record_game_reload_epoch(
                    ctx, reason=f"{tool_name}_{reason}", confirmed=status == "succeeded"
                )
                if status == "failed":
                    result = "ERR:OUTCOME_UNKNOWN|加载可能已提交，但未确认完成。" + result
    except asyncio.CancelledError:
        if tool_name in _SAVE_LOADING_TOOLS and _load_was_submitted():
            await _record_game_reload_epoch(ctx, reason=f"{tool_name}_unknown")
        raise
    except (LuaError, ValueError) as e:
        result = f"Error: {e}"
        if tool_name in _SAVE_LOADING_TOOLS and _load_was_submitted():
            result = "ERR:OUTCOME_UNKNOWN|加载可能已提交，但未确认完成。" + result
        await _fail(
            result,
            _action_execution_status(tool_name, params, result, fallback="failed"),
        )
        return _return_result(result)
    except ConnectionError as e:
        result = str(e)
        await _fail(result, "unknown")

        # A lost response is not proof of process death. Recovery belongs to
        # the explicit lifecycle tools; this wrapper only records uncertainty.
        log.warning("连接异常，保留当前对局；请只读诊断：%s", result)

        return _return_result(result)
    except Exception as e:
        # An unexpected exception may happen after the game accepted a
        # mutation. Preserve that uncertainty and require read-back instead of
        # treating it as a safe-to-retry failure. CancelledError derives from
        # BaseException; load-time/turn-gate recovery covers that path.
        result = f"Error: {e}"
        await _fail(result, "unknown")
        return _return_result(result)
    # Refresh the heartbeat only; success does not authorize recovery.
    heartbeat.write("playing", turn=turn or 0)
    # Keep the domain result separate from model-facing belief annotations.
    # Telemetry owns the rendered transcript; the Belief Engine observes only
    # the underlying game/tool result and never feeds its own context back in.
    domain_result = result
    if not domain_result.startswith(("Error", "ERR")):
        if not pending_observation and not pending_input:
            result = await _append_belief_context(ctx, tool_name, result)
        if decision_id:
            result += (
                f"\n\n[Belief decision consumed: {decision_id}; "
                f"route={decision_route}]"
            )
    ms = int((time.monotonic() - start) * 1000)
    receipt = action_receipt_status(tool_name, domain_result, params=params)
    if receipt is None:
        reported_error = domain_result.startswith(("Error", "ERR"))
        record_success = not reported_error
        execution_status = None
    else:
        receipt_status = receipt[0]
        # A submitted request has no postcondition yet. Keep it open in the
        # event-sourced audit as ``unknown`` while showing
        # ``已提交待验证`` to the model; only a later read-back can close it.
        execution_status = (
            "unknown" if receipt_status == "submitted" else receipt_status
        )
        record_success = receipt_status == "succeeded"
        reported_error = receipt_status in {"failed", "blocked", "unknown"}
    log.info(
        "[T%s] %s(%s) %s %dms: %s",
        turn,
        tool_name,
        _param_summary(params),
        "ERR" if reported_error else "OK",
        ms,
        _result_summary(result),
    )
    if reported_error:
        await logger.log_error(tool_name, result)
    else:
        await logger.log_tool_call(tool_name, params, result, ms)
    await _record_belief_tool_result(
        ctx,
        tool_name,
        params,
        domain_result,
        turn,
        ms,
        success=record_success,
        decision_id=decision_id,
        decision_route=decision_route,
        execution_status=execution_status,
    )
    try:
        await _get_spatial(ctx).record(tool_name, params, result, ms, tiles=tiles)
    except Exception:
        pass
    return _return_result(result)


async def _bind_belief_engine(
    ctx: Context,
    engine: BeliefEngine,
    *,
    civ: str,
    seed: int,
) -> None:
    """Bind the world model without stalling the event loop.

    ``bind_game`` replays the whole append-only journal. On a real 110-turn
    game that measured 2.4s after the replay-verification fix (48s before it),
    so running it inline stalls every other concurrent MCP request.

    Two tool handlers can both observe ``not engine.bound``, and the thread hop
    is a real interleaving point that the previous synchronous call did not
    have: without the lock they would replay the same journal at once and
    double-append the orphaned-decision recovery events. Contexts built by
    tests may omit the lock; fall back to the inline call there.
    """

    if engine.bound:
        return
    lock = getattr(
        ctx.request_context.lifespan_context, "belief_bind_lock", None
    )
    if lock is None:
        engine.bind_game(civ, seed)
        return
    async with lock:
        if engine.bound:
            return
        await asyncio.to_thread(engine.bind_game, civ, seed)


async def _belief_context(ctx: Context) -> tuple[BeliefEngine, int]:
    """Bind the world model to the live game and return its current turn."""
    engine = _get_beliefs(ctx)
    logger = _get_logger(ctx)
    gs = _get_game(ctx)
    if not engine.bound:
        civ, seed = await gs.get_game_identity()
        await _bind_belief_engine(ctx, engine, civ=civ, seed=seed)
        logger.bind_game(civ, seed)
    turn = logger._turn
    if turn is None:
        overview = await gs.get_game_overview()
        turn = overview.turn
        logger.set_turn(turn)
    return engine, int(turn)

async def _belief_tool(
    ctx: Context,
    tool_name: str,
    params: dict[str, Any],
    operation: Callable[[BeliefEngine, int], Any],
) -> str:
    """Run a belief operation with normal MCP logging and telemetry mirroring."""
    refusal = _hidden_tool_refusal(ctx, tool_name)
    if refusal is not None:
        log.warning("Refused control-plane tool %s under the lean profile", tool_name)
        return _filter_downstream_result(tool_name, params, refusal)
    mode = _get_belief_mode(ctx)
    if not mode.records_events:
        text = json.dumps(
            {
                "belief_mode": mode.value,
                "disabled": True,
                "tool": tool_name,
                "message": "Belief Engine persistence is disabled in off mode.",
            },
            ensure_ascii=False,
        )
        return _filter_downstream_result(tool_name, params, text)
    started = time.monotonic()
    try:
        # Mirror _logged: during DSH auto-resume the GUI owns the connection,
        # and a belief tool that reads the game would race the recovery.
        await _await_auto_resume_ready(ctx)
        engine, turn = await _belief_context(ctx)
        _sync_governance_graph(engine, turn=turn)
        result = operation(engine, turn)
        _sync_governance_graph(engine, turn=turn)
        await _flush_belief_events(ctx)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        await _get_logger(ctx).log_tool_call(
            tool_name,
            params,
            text,
            int((time.monotonic() - started) * 1000),
        )
        return _filter_downstream_result(tool_name, params, text)
    except (BeliefEngineError, json.JSONDecodeError, TypeError, ValueError) as exc:
        message = f"Error: {exc}"
        await _get_logger(ctx).log_error(tool_name, message)
        return _filter_downstream_result(tool_name, params, message)
    except ConnectionError as exc:
        message = f"Error: {exc}"
        await _get_logger(ctx).log_error(tool_name, message)
        return _filter_downstream_result(tool_name, params, message)
    except Exception as exc:
        # Without this, an unexpected error escapes as a protocol-level failure
        # while the same class of error through _logged becomes an "Error: ..."
        # text result — one server, two error shapes for the caller.
        log.warning("Belief tool %s failed", tool_name, exc_info=True)
        message = f"Error: {exc}"
        await _get_logger(ctx).log_error(tool_name, message)
        return _filter_downstream_result(tool_name, params, message)

async def _narrate(
    query_fn: Callable[[], Awaitable[Any]], narrate_fn: Callable[..., str]
) -> str:
    """Helper: call a query function then narrate the result."""
    data = await query_fn()
    return narrate_fn(data)
