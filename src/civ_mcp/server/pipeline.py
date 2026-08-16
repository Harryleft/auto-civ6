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
    lines.append(
        "执行约束: 附近敌对单位只触发验证，不自动降低路线信念；路线风险必须使用 get_combat_estimate 的真实 CS/HP/预期互伤后再更新。"
    )
    lines.append(
        "高影响或不可逆行动前，必须调用 route_belief_decision，并在行动后核对结果。"
    )
    return "\n".join(lines)


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
    if tool_name != "unit_action":
        return False
    return str(params.get("action", "")).lower() not in _ROUTINE_UNIT_ACTIONS


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
        # 双轨信封结果：信念上下文合并进 JSON 结构，而不是破坏可解析性的尾部追加。
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
        if not engine.bound:
            civ, seed = await _get_game(ctx).get_game_identity()
            engine.bind_game(civ, seed)
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

    await _await_auto_resume_ready(ctx)
    logger = _get_logger(ctx)
    turn = logger._turn or "?"
    start = time.monotonic()
    decision_context: dict[str, Any] = {
        "authorized": True,
        "decision_id": None,
        "route": "routine",
    }
    try:
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

    try:
        result = await fn()
    except (LuaError, ValueError) as e:
        result = f"Error: {e}"
        await _fail(
            result,
            _action_execution_status(tool_name, params, result, fallback="failed"),
        )
        return _return_result(result)
    except ConnectionError as e:
        result = str(e)
        await _fail(result, "unknown")

        # Connection-loss recovery: after consecutive failures,
        # the game has likely crashed. Auto-restart from autosave.
        _logged._conn_errors = getattr(_logged, "_conn_errors", 0) + 1
        if _logged._conn_errors >= 5:
            log.error(
                "CONNECTION RECOVERY: %d consecutive connection failures "
                "— triggering restart_and_load",
                _logged._conn_errors,
            )
            _logged._conn_errors = 0
            try:
                from civ_mcp.autosave import get_autosave_for_turn, get_latest_autosave

                turn_num = logger._turn
                save = (
                    get_autosave_for_turn(int(turn_num))
                    if turn_num
                    else get_latest_autosave()
                )
                restart_result = await game_launcher.restart_and_load(
                    save, conn=_get_game(ctx).conn
                )
                log.info("CONNECTION RECOVERY: %s", restart_result)
                # The game state rolled back to an older save; events recorded
                # after that point describe a future that no longer happened.
                # Mark a new epoch so the append-only stream stays interpretable.
                try:
                    engine = _get_beliefs(ctx)
                    if _get_belief_mode(ctx).records_events and engine.bound:
                        engine.record_game_reload(
                            reason="connection_recovery_restart_and_load",
                            turn=(
                                int(turn_num)
                                if isinstance(turn_num, int)
                                else None
                            ),
                            details={"save": str(save)},
                        )
                        await _flush_belief_events(ctx)
                except Exception:
                    log.error(
                        "CONNECTION RECOVERY: failed to record game reload epoch",
                        exc_info=True,
                    )
                gs = _get_game(ctx)
                for rc_attempt in range(30):
                    try:
                        await gs.conn.reconnect()
                        if gs.conn.gamecore_index is not None:
                            log.info("CONNECTION RECOVERY: reconnected")
                            break
                    except ConnectionError:
                        pass
                    await asyncio.sleep(1)
            except Exception:
                log.error("CONNECTION RECOVERY: restart failed", exc_info=True)

        return _return_result(result)
    except Exception as e:
        # An unexpected exception may happen after the game accepted a
        # mutation. Preserve that uncertainty and require read-back instead of
        # treating it as a safe-to-retry failure. CancelledError derives from
        # BaseException; load-time/turn-gate recovery covers that path.
        result = f"Error: {e}"
        await _fail(result, "unknown")
        return _return_result(result)
    # Success — reset connection error counter + refresh heartbeat
    _logged._conn_errors = 0
    heartbeat.write("playing", turn=turn or 0)
    # Keep the domain result separate from model-facing belief annotations.
    # Telemetry owns the rendered transcript; the Belief Engine observes only
    # the underlying game/tool result and never feeds its own context back in.
    domain_result = result
    if not domain_result.startswith(("Error", "ERR")):
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

async def _belief_context(ctx: Context) -> tuple[BeliefEngine, int]:
    """Bind the world model to the live game and return its current turn."""
    engine = _get_beliefs(ctx)
    logger = _get_logger(ctx)
    gs = _get_game(ctx)
    if not engine.bound:
        civ, seed = await gs.get_game_identity()
        engine.bind_game(civ, seed)
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

async def _narrate(
    query_fn: Callable[[], Awaitable[Any]], narrate_fn: Callable[..., str]
) -> str:
    """Helper: call a query function then narrate the result."""
    data = await query_fn()
    return narrate_fn(data)
