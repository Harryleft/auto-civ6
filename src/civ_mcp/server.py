"""MCP server for Civilization VI — lets LLM agents read game state and play.

Uses FastMCP with the lifespan pattern to maintain a persistent TCP connection
to the running game via FireTuner protocol.
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

import uvicorn
from mcp.server.fastmcp import Context, FastMCP

from civ_mcp import game_launcher, heartbeat
from civ_mcp.belief_engine import (
    BeliefEngine,
    BeliefEngineError,
    action_args_hash,
    tool_result_reference,
)
from civ_mcp.belief_mode import BeliefMode
from civ_mcp.game_over_watchdog import GameOverWatchdog
from civ_mcp import narrate as nr
from civ_mcp.connection import GameConnection, LuaError
from civ_mcp.diary import (
    diary_path as _diary_path,
    format_diary_entry as _format_diary_entry,
    merge_agent_reflections as _merge_agent_reflections,
    read_diary_entries as _read_diary_entries,
)
from civ_mcp.game_state import GameState
from civ_mcp.logger import GameLogger
from civ_mcp.map_capture import MapCapture
from civ_mcp.spatial import SpatialTracker
from civ_mcp.spectator import CameraController, PopupWatcher
from civ_mcp.telemetry import (
    EVENT_CITY_ROW,
    EVENT_DIARY_ROW,
    EVENT_BELIEF_EVENT,
    AlertSink,
    CloudSink,
    LocalSink,
    TelemetryEmitter,
)
from civ_mcp.web_api import create_app

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    game: GameState
    logger: GameLogger
    camera: CameraController
    popup_watcher: PopupWatcher
    spatial: SpatialTracker
    map_capture: MapCapture
    watchdog: GameOverWatchdog
    beliefs: BeliefEngine
    belief_mode: BeliefMode = BeliefMode.ENFORCE


async def _auto_boot(conn: GameConnection, save_name: str) -> None:
    """Launch game and load a save before MCP tools become available.

    Called during lifespan when CIV_MCP_SAVE_FILE is set (eval mode).
    Blocks until the game is loaded and ready for play.
    """
    import glob

    from civ_mcp.game_lifecycle import load_game_save

    # 0. Clear stale MCP autosaves. These are the saves that the main
    # menu's "Continue Game" button would load. If a previous run
    # crashed at T197, "Continue Game" resumes T197 instead of loading
    # the scenario save. Clearing them makes "Continue Game" harmless
    # (it would load the scenario save or nothing).
    # This does NOT break --resume-save which loads by name via Lua.
    stale = glob.glob(os.path.join(game_launcher.SINGLE_SAVE_DIR, "0_MCP_*.Civ6Save"))
    if stale:
        for f in stale:
            try:
                os.remove(f)
            except OSError:
                pass
        log.info("Auto-boot: cleared %d stale MCP autosave(s)", len(stale))

    # 1. Launch game (or reuse if already running).
    # The eval runner's ensure_game_ready() typically launches the game
    # before the MCP server starts. _launch_game_sync() detects an
    # already-running game and returns immediately, avoiding a wasteful
    # kill + relaunch cycle through the Aspyr launcher.
    # The step-5 verification below catches wrong-save scenarios as a
    # safety net (the Lua load path fails when mid-session, not from
    # main menu).
    heartbeat.write("launching")
    log.info("Auto-boot: launching game...")
    result = await asyncio.to_thread(game_launcher._launch_game_sync)
    log.info("Auto-boot: launch result: %s", result)

    # 2. Connect to FireTuner (retry — game takes time to start)
    for attempt in range(90):
        try:
            await conn.connect()
            log.info("Auto-boot: connected to FireTuner")
            heartbeat.write("connecting")
            break
        except ConnectionError:
            if attempt % 10 == 0:
                log.info("Auto-boot: waiting for FireTuner... (%ds)", attempt)
            await asyncio.sleep(1)
    else:
        log.error("Auto-boot: could not connect to FireTuner after 90s")
        heartbeat.write("error")
        return

    # 2b. Verify Lua states exist (port can open before game initialises).
    # A hung splash screen ("Loading, Please Wait...") has port open but
    # GameCore never appears. Skip this check — the main menu legitimately
    # has no GameCore on any platform; it only appears after a save is
    # loaded (step 3). The splash hang detection was causing false kills
    # when autosaves were cleaned (game stays at main menu, no GameCore).
    if conn.gamecore_index is None and False:  # disabled — see comment above
        log.warning(
            "Auto-boot: FireTuner connected but GameCore not found "
            "— game may be hung at splash screen"
        )
        for retry in range(30):
            await asyncio.sleep(2)
            try:
                await conn.reconnect()
                if conn.gamecore_index is not None:
                    log.info("Auto-boot: GameCore found after %ds", (retry + 1) * 2)
                    break
            except ConnectionError:
                pass
        else:
            log.error("Auto-boot: GameCore never appeared — killing hung game")
            heartbeat.write("error")
            await asyncio.to_thread(game_launcher._kill_game_sync)
            await asyncio.sleep(5)
            result = await asyncio.to_thread(game_launcher._launch_game_sync)
            log.info("Auto-boot: relaunched after hung splash: %s", result)
            for attempt in range(90):
                try:
                    await conn.connect()
                    if conn.gamecore_index is not None:
                        log.info("Auto-boot: GameCore found on relaunch")
                        break
                except ConnectionError:
                    pass
                await asyncio.sleep(1)
            if conn.gamecore_index is None:
                log.error("Auto-boot: relaunch also failed — giving up")
                heartbeat.write("error")
                return

    # 3. Load save (Lua on Windows/macOS, OCR menu nav on Linux)
    log.info("Auto-boot: loading save '%s'...", save_name)
    result = await load_game_save(conn, save_name)
    log.info("Auto-boot: load result: %s", result)
    heartbeat.write("loading")

    # 4. Wait for save to load, click through leader intro, then reconnect.
    # The CONTINUE GAME button on the leader screen has low-contrast
    # teal-on-teal text that OCR often misses — fall back to positional
    # click grid if OCR fails. Verify the click actually worked by
    # checking for Lua states (only available once in-game, not on leader
    # screen).
    log.info("Auto-boot: waiting 15s for save to load...")
    await asyncio.sleep(15)
    clicked = await asyncio.to_thread(
        lambda: game_launcher._click_text("CONTINUE", timeout=105, post_delay=1),
    )
    if clicked:
        log.info("Auto-boot: clicked CONTINUE GAME via OCR")
    else:
        log.warning("Auto-boot: OCR missed CONTINUE — using positional click grid")
        await asyncio.to_thread(game_launcher._click_continue_positional)

    # Verify the click worked — Lua states only appear once past the
    # leader screen into gameplay. Retry positional click if needed.
    await asyncio.sleep(3)
    game_ready = False
    for attempt in range(45):
        try:
            await conn.reconnect()
            if conn.gamecore_index is not None:
                log.info("Auto-boot: game ready (GameCore=%s)", conn.gamecore_index)
                heartbeat.write("playing")  # turn unknown until first end_turn
                game_ready = True
                break
        except ConnectionError:
            pass
        # Retry positional click every 10s in case the first click missed
        if attempt > 0 and attempt % 10 == 0:
            heartbeat.write("loading")  # keep heartbeat fresh during retry
            if not clicked:
                log.info("Auto-boot: retrying positional click (attempt %d)", attempt)
                await asyncio.to_thread(game_launcher._click_continue_positional)
        await asyncio.sleep(1)
    if not game_ready:
        log.warning("Auto-boot: save may not have loaded — GameCore not found")
        heartbeat.write("error")
        return

    # 5. Verify correct save loaded. If the wrong save loaded (e.g.
    # main-menu "Continue Game" loaded a stale autosave instead of the
    # scenario save), reload the correct one via Lua — no OCR needed.
    try:
        verify = await conn.execute_read(
            "local t = Game.GetCurrentGameTurn(); "
            'print("VERIFY|" .. t); '
            'print("---END---")'
        )
        for line in verify:
            if line.startswith("VERIFY|"):
                turn = int(line.split("|")[1])
                if turn > 5:
                    log.error(
                        "Auto-boot: loaded T%d but expected T1 — wrong save! "
                        "Reloading '%s' via Lua",
                        turn,
                        save_name,
                    )
                    # Retry via Lua (Network.LoadGame) — bypasses OCR entirely
                    result = await load_game_save(conn, save_name)
                    log.info("Auto-boot: Lua reload result: %s", result)
                    await asyncio.sleep(15)
                    # Click CONTINUE again for the leader screen
                    await asyncio.to_thread(game_launcher._click_continue_positional)
                    await asyncio.sleep(5)
                    for retry in range(30):
                        try:
                            await conn.reconnect()
                            if conn.gamecore_index is not None:
                                break
                        except ConnectionError:
                            pass
                        await asyncio.sleep(1)
                    # Verify again
                    try:
                        verify2 = await conn.execute_read(
                            "local t = Game.GetCurrentGameTurn(); "
                            'print("VERIFY|" .. t); '
                            'print("---END---")'
                        )
                        for line2 in verify2:
                            if line2.startswith("VERIFY|"):
                                t2 = int(line2.split("|")[1])
                                if t2 > 5:
                                    log.error(
                                        "Auto-boot: Lua reload also loaded T%d "
                                        "— falling back to kill + OCR",
                                        t2,
                                    )
                                    await game_launcher.kill_game()
                                    r = await asyncio.to_thread(
                                        game_launcher._launch_game_sync
                                    )
                                    log.info("Auto-boot: relaunch: %s", r)
                                    r = await asyncio.to_thread(
                                        game_launcher._navigate_to_save_sync,
                                        save_name,
                                        None,
                                    )
                                    log.info("Auto-boot: OCR nav: %s", r)
                                    for a in range(30):
                                        try:
                                            await conn.reconnect()
                                            if conn.gamecore_index is not None:
                                                return
                                        except ConnectionError:
                                            pass
                                        await asyncio.sleep(1)
                                    log.warning("Auto-boot: all fallbacks failed")
                                    return
                                log.info("Auto-boot: Lua reload verified at T%d", t2)
                                heartbeat.write("playing", turn=t2)
                    except Exception:
                        log.debug("Auto-boot: post-reload verify failed", exc_info=True)
                    return
                log.info("Auto-boot: verified save at T%d", turn)
                heartbeat.write("playing", turn=turn)
    except Exception:
        log.debug("Auto-boot: save verification failed", exc_info=True)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    conn = GameConnection()

    # Telemetry emitter — routes events to local JSONL + optional cloud sink
    emitter = TelemetryEmitter()
    emitter.add_sink(LocalSink())
    cloud_bucket = os.environ.get("CIV_MCP_TELEMETRY_BUCKET")
    if cloud_bucket:
        emitter.add_sink(CloudSink(cloud_bucket))
    alert_webhook = os.environ.get("CIV_MCP_ALERT_WEBHOOK")
    if alert_webhook:
        emitter.add_sink(AlertSink(alert_webhook))
    emitter.start()
    heartbeat.init(emitter.run_id)
    # Bind eval identity so the orchestrator can match running games to jobs
    eval_model = os.environ.get("CIV_MCP_AGENT_MODEL", "")
    eval_metadata = os.environ.get("CIV_MCP_METADATA", "")
    eval_scenario = ""
    if eval_metadata:
        try:
            eval_scenario = json.loads(eval_metadata).get("scenario_id", "")
        except Exception:
            pass
    heartbeat.bind_eval(eval_model, eval_scenario)
    heartbeat.write("starting")

    logger = GameLogger(emitter)
    spatial = SpatialTracker(emitter)
    map_capture = MapCapture(emitter)
    gs = GameState(conn)
    beliefs = BeliefEngine(run_id=emitter.run_id)
    belief_mode = BeliefMode.from_env()
    log.info("Game logger session: %s", logger.session_id)
    log.info("Belief Engine mode: %s", belief_mode.value)

    # Auto-boot: launch game + load save when running as eval
    save_file = os.environ.get("CIV_MCP_SAVE_FILE")
    if save_file:
        await _auto_boot(conn, save_file)

    # Spectator-mode background services (camera tracking + popup auto-dismiss)
    camera = CameraController(conn)
    popup_watcher = PopupWatcher(conn)
    watchdog = GameOverWatchdog(gs, logger)
    camera.start()
    popup_watcher.start()
    watchdog.start()

    # Start the web dashboard API as a background task (port 8000)
    web_app = create_app(gs)
    uvi_config = uvicorn.Config(web_app, host="0.0.0.0", port=8000, log_level="info")
    uvi_server = uvicorn.Server(uvi_config)
    api_task = asyncio.create_task(uvi_server.serve())
    log.info("Web API starting on http://0.0.0.0:8000")

    try:
        yield AppContext(
            game=gs,
            logger=logger,
            camera=camera,
            popup_watcher=popup_watcher,
            spatial=spatial,
            map_capture=map_capture,
            watchdog=watchdog,
            beliefs=beliefs,
            belief_mode=belief_mode,
        )
    finally:
        await emitter.close()
        await watchdog.stop()
        await camera.stop()
        await popup_watcher.stop()
        uvi_server.should_exit = True
        try:
            await api_task
        except asyncio.CancelledError:
            # Host shutdown cancels background tasks before lifespan cleanup.
            # Continue so the FireTuner socket is closed deliberately below.
            pass
        await conn.disconnect()


mcp = FastMCP(
    "Civilization VI",
    instructions=(
        "Read game state and issue commands to a running Civ 6 game. Start each "
        "turn with get_game_overview. When CIV_MCP_BELIEF_MODE=enforce, also use "
        "get_governance_brief and route selected exact action intents before "
        "governed actions. In observe/off mode the live turn loop stays lightweight "
        "and normal game rules plus end-turn blockers remain authoritative."
    ),
    lifespan=lifespan,
)


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


_COUNCIL_REQUIRED_TOOLS = {
    "set_research",
    "set_policies",
    "change_government",
    "choose_pantheon",
    "found_religion",
    "choose_dedication",
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
    "choose_dedication",
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
                "reason": (
                    "Governance turn gate is incomplete: "
                    + ", ".join(governance_gate["blockers"])
                    + ". Call get_governance_brief, resolve active proposals, "
                    "and complete routed council actions before ending the turn."
                ),
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
        review_events = []
        for key in ("predictions_resolved", "predictions_overdue", "plans_needing_replan", "contradictions_created"):
            values = review.get(key) or []
            if values:
                review_events.append(f"{key}={','.join(values[:6])}")
        suffix = [
            "\n\n=== BELIEF CONTEXT ===",
            f"turn={turn} default_route={gate.get('default_route', 'fast')}",
            "flags=" + ("; ".join(flags) if flags else "none"),
            "blocking_scopes="
            + (",".join(gate.get("blocking_scopes") or []) or "none"),
            "review=" + ("; ".join(review_events) if review_events else "none"),
            "Use get_turn_brief before a key action; nearby hostiles require quantified combat evidence.",
        ]
        return result + "\n" + "\n".join(suffix)
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
        )
        await _flush_belief_events(ctx)
    except Exception:
        log.warning("Belief Engine: failed to record tool result", exc_info=True)


async def _logged(
    ctx: Context,
    tool_name: str,
    params: dict[str, Any],
    fn: Callable[[], Awaitable[str]],
    *,
    tiles: set[tuple[int, int]] | None = None,
) -> str:
    """Run a tool function with timing, error handling, and logging."""
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
        )
        return result

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
        )
        return result

    try:
        result = await fn()
    except (LuaError, ValueError) as e:
        result = f"Error: {e}"
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
        )
        return result
    except ConnectionError as e:
        result = str(e)
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
        )

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
                restart_result = await game_launcher.restart_and_load(save)
                log.info("CONNECTION RECOVERY: %s", restart_result)
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

        return result
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
    reported_error = domain_result.startswith(("Error", "ERR"))
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
        success=not reported_error,
        decision_id=decision_id,
        decision_route=decision_route,
    )
    try:
        await _get_spatial(ctx).record(tool_name, params, result, ms, tiles=tiles)
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Query tools (read-only)
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_game_overview(ctx: Context) -> str:
    """Get a high-level summary of the current game state.

    Returns turn number, civilization, yields (gold/science/culture/faith),
    current research and civic, and counts of cities and units.
    Call this first to orient yourself.
    """
    gs = _get_game(ctx)

    async def _run():
        ov = await gs.get_game_overview()
        logger = _get_logger(ctx)
        logger.set_turn(ov.turn)
        spatial = _get_spatial(ctx)
        spatial.set_turn(ov.turn)
        try:
            civ, seed = await gs.get_game_identity()
            logger.bind_game(civ, seed)
            if _get_belief_mode(ctx).records_events:
                _get_beliefs(ctx).bind_game(civ, seed)
            spatial.bind_game(civ, seed)
            heartbeat.bind_game(civ, seed)
            gs.spatial = spatial
        except Exception:
            pass
        # Seed revealed tiles for visibility diff (once per session)
        if not spatial._revealed_seeded:
            try:
                seed_lines = await gs.conn.execute_read(
                    lq.build_revealed_tiles_seed_query()
                )
                seed_tiles = lq.parse_revealed_tiles_seed(seed_lines)
                spatial.seed_revealed(seed_tiles)
                log.info(
                    "Seeded spatial tracker with %d revealed tiles", len(seed_tiles)
                )
            except Exception:
                log.debug("Failed to seed revealed tiles", exc_info=True)
        text = nr.narrate_overview(ov)
        # Check for game-over state
        gameover = await gs.check_game_over()
        if gameover is not None:
            vtype = (
                gameover.victory_type.replace("VICTORY_", "").replace("_", " ").title()
            )
            if gameover.is_defeat:
                text += (
                    f"\n\n*** GAME OVER — DEFEAT ***\n"
                    f"{gameover.winner_leader} of {gameover.winner_name} won a {vtype} victory.\n"
                    f"No further actions are possible."
                )
            else:
                text += f"\n\n*** GAME OVER — VICTORY ***\nYou won a {vtype} victory!"
            try:
                await logger.log_game_over(
                    is_defeat=gameover.is_defeat,
                    winner_civ=gameover.winner_name,
                    winner_leader=gameover.winner_leader,
                    victory_type=vtype,
                    player_alive=gameover.player_alive,
                )
            except Exception:
                log.warning("Failed to log game-over in overview", exc_info=True)
        # Governance is deliberately absent from the lightweight live modes.
        # Enforcement retains the legacy snapshot and action-gate behavior.
        if _get_belief_mode(ctx).captures_governance_snapshot:
            try:
                engine = _get_beliefs(ctx)
                cached = _reusable_typed_snapshot_for_turn(engine, turn=ov.turn)
                if cached is None:
                    snapshot, world, projection, released, locks = (
                        await _capture_governance_snapshot(ctx, engine)
                    )
                    snapshot_id = snapshot.snapshot_id
                    ruleset = world["ruleset"]
                    changed_count = len(projection["world_entities_changed"])
                    archived_count = len(projection["world_entities_archived"])
                    lock_count = len(locks)
                    released_count = len(released)
                    snapshot_source = "captured"
                else:
                    facts = cached.get("facts") or {}
                    capabilities = facts.get("capabilities") or {}
                    snapshot_id = str(facts.get("snapshot_id") or "unknown")
                    ruleset = str(capabilities.get("ruleset") or "unknown")
                    current_entities = [
                        item
                        for item in engine.list("world_entity", status="active")
                        if item.get("snapshot_id") == snapshot_id
                    ]
                    changed_count = 0
                    archived_count = 0
                    lock_count = len(engine.list("budget_lock", status="active"))
                    released_count = 0
                    snapshot_source = "reused"
                belief_brief = engine.turn_brief(turn=ov.turn)
                await _flush_belief_events(ctx)
                text += (
                    "\n\n=== GOVERNANCE SNAPSHOT ===\n"
                    f"snapshot={snapshot_id} ruleset={ruleset} source={snapshot_source} "
                    f"entities_changed={changed_count} "
                    f"entities_archived={archived_count} "
                    f"active_budget_locks={lock_count} released_locks={released_count}"
                )
                if cached is not None:
                    text += f" active_world_entities={len(current_entities)}"
                text += _format_belief_turn_brief(belief_brief)
            except Exception as exc:
                log.warning("Governance: failed to capture typed turn state", exc_info=True)
                text += (
                    "\n\n=== GOVERNANCE SNAPSHOT ERROR ===\n"
                    f"{exc}\nKey actions and end_turn remain blocked until "
                    "get_governance_brief succeeds."
                )
        return text

    return await _logged(ctx, "get_game_overview", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_units(ctx: Context) -> str:
    """List all your units with position, type, movement, and health.

    Each unit shows its id and idx (needed for action commands).
    Consumed units (e.g. settlers that founded cities) are excluded.
    """
    gs = _get_game(ctx)
    unit_tiles: set[tuple[int, int]] = set()

    async def _run():
        units = await gs.get_units()
        unit_tiles.update((u.x, u.y) for u in units if u.x >= 0)
        try:
            threats = await gs.get_threat_scan()
        except Exception:
            threats = None
        trade_status = None
        try:
            trade_status = await gs.get_trade_routes()
        except Exception:
            pass
        return nr.narrate_units(units, threats, trade_status)

    return await _logged(ctx, "get_units", {}, _run, tiles=unit_tiles)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_spies(ctx: Context) -> str:
    """List all your spy units with position, rank, city, and available missions.

    Shows each spy's composite id (needed for spy_action), current location,
    rank (Recruit/Agent/Special Agent/Senior Agent), XP, and which operations
    are available at their current position.

    Note: offensive missions only become available once the spy has physically
    arrived in the target city. Use spy_action with action='travel' first.
    """
    gs = _get_game(ctx)

    async def _run():
        spies = await gs.get_spies()
        return nr.narrate_spies(spies)

    return await _logged(ctx, "get_spies", {}, _run)


@mcp.tool()
async def spy_action(
    ctx: Context,
    unit_id: int,
    action: str,
    target_x: int,
    target_y: int,
) -> str:
    """Send a spy to a city or launch a spy mission.

    Args:
        unit_id: The spy's composite ID (from get_spies output)
        action: 'travel' to move spy to a city, or a mission type to launch a mission.
            Mission types: COUNTERSPY, GAIN_SOURCES, SIPHON_FUNDS, STEAL_TECH_BOOST,
            SABOTAGE_PRODUCTION, GREAT_WORK_HEIST, RECRUIT_PARTISANS,
            NEUTRALIZE_GOVERNOR, FABRICATE_SCANDAL
        target_x: X coordinate of the target city tile
        target_y: Y coordinate of the target city tile

    Travel notes:
        - Valid targets: your own cities and city-states only.
        - Allied civ cities are NOT valid travel targets.
        - Travel is queued end-of-turn; spy position updates after turn ends.

    Mission notes:
        - Spy must be physically IN the target city to launch any offensive mission.
        - Use 'travel' first, then end the turn, then launch the mission.
        - COUNTERSPY defends your own city (spy must be in your city).
        - get_spies shows which ops are available at the spy's current location.
    """
    gs = _get_game(ctx)
    unit_index = unit_id % 65536
    params = {
        "unit_id": unit_id,
        "action": action,
        "target_x": target_x,
        "target_y": target_y,
    }

    async def _run():
        if action.lower() == "travel":
            return await gs.spy_travel(unit_index, target_x, target_y)
        return await gs.spy_mission(unit_index, action.upper(), target_x, target_y)

    result = await _logged(ctx, "spy_action", params, _run)
    _get_camera(ctx).push(target_x, target_y, f"spy {action}")
    return result


@mcp.tool(annotations={"readOnlyHint": True})
async def get_cities(ctx: Context) -> str:
    """List all your cities with yields, population, production, growth, and loyalty.

    Each city shows its id (needed for production commands).
    Cities losing loyalty show warnings with flip timers.
    """
    gs = _get_game(ctx)

    async def _run():
        cities, distances = await gs.get_cities()
        return nr.narrate_cities(cities, distances)

    return await _logged(ctx, "get_cities", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_city_production(ctx: Context, city_id: int) -> str:
    """List what a city can produce right now.

    Args:
        city_id: City ID (from get_cities output)

    Returns available units, buildings, and districts with production costs.
    Call this when a city finishes building or to decide what to produce next.
    """
    gs = _get_game(ctx)

    async def _run():
        options = await gs.list_city_production(city_id)
        return nr.narrate_city_production(options)

    return await _logged(ctx, "get_city_production", {"city_id": city_id}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_map_area(
    ctx: Context, center_x: int, center_y: int, radius: int = 2
) -> str:
    """Get terrain info for tiles around a point.

    Args:
        center_x: X coordinate of center tile
        center_y: Y coordinate of center tile
        radius: How many tiles out from center (default 2, max 4)
    """
    radius = min(radius, 4)
    gs = _get_game(ctx)
    tile_coords: set[tuple[int, int]] = set()

    async def _run():
        tiles = await gs.get_map_area(center_x, center_y, radius)
        tile_coords.update((t.x, t.y) for t in tiles)
        return nr.narrate_map(tiles)

    result = await _logged(
        ctx,
        "get_map_area",
        {"center_x": center_x, "center_y": center_y, "radius": radius},
        _run,
        tiles=tile_coords,
    )
    _get_camera(ctx).push(center_x, center_y, f"map_area ({center_x},{center_y})")
    return result


@mcp.tool(annotations={"readOnlyHint": True})
async def get_settle_advisor(ctx: Context, unit_id: int) -> str:
    """List best settle locations near a settler unit.

    Args:
        unit_id: The settler's composite ID (from get_units output)

    Scores locations by yields, water, defense, and resource value.
    Returns top 5 candidates sorted by score.
    """
    gs = _get_game(ctx)
    unit_index = unit_id % 65536
    return await _logged(
        ctx,
        "get_settle_advisor",
        {"unit_id": unit_id},
        lambda: gs.get_settle_advisor(unit_index),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_pathing_estimate(
    ctx: Context, unit_id: int, target_x: int, target_y: int
) -> str:
    """Estimate how many turns a unit needs to reach a destination.

    Args:
        unit_id: The unit's composite ID (from get_units output)
        target_x: Destination X coordinate
        target_y: Destination Y coordinate

    Returns estimated turns, path length, and reachable tiles this turn.
    """
    gs = _get_game(ctx)
    unit_index = unit_id % 65536

    async def _run():
        est = await gs.get_pathing_estimate(unit_index, target_x, target_y)
        return nr.narrate_pathing_estimate(est)

    return await _logged(
        ctx,
        "get_pathing_estimate",
        {"unit_id": unit_id, "target_x": target_x, "target_y": target_y},
        _run,
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_combat_estimate(
    ctx: Context, unit_id: int, target_x: int, target_y: int
) -> str:
    """Quantify a unit matchup without executing an attack.

    Returns effective combat strengths, current HP, terrain/fortification/
    promotion/flanking/support modifiers, and estimated damage to both sides.
    Use this after a proximity scan and before revising a route-safety belief;
    merely seeing a hostile unit is not evidence that the route is unsafe.

    Args:
        unit_id: Attacking or escort unit composite ID from get_units
        target_x: Hostile unit X coordinate
        target_y: Hostile unit Y coordinate
    """
    gs = _get_game(ctx)
    unit_index = unit_id % 65536

    async def _run():
        estimate = await gs.get_combat_estimate(unit_index, target_x, target_y)
        if estimate is None:
            return "No quantified combat estimate is available for this matchup."
        return nr.narrate_combat_estimate(estimate)

    return await _logged(
        ctx,
        "get_combat_estimate",
        {"unit_id": unit_id, "target_x": target_x, "target_y": target_y},
        _run,
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_global_settle_advisor(ctx: Context) -> str:
    """Find the best settle locations across the entire revealed map.

    Unlike get_settle_advisor (which searches near a specific settler),
    this scans all revealed land for the top 10 settle candidates.
    Use this when deciding WHERE to send a settler, not just where to settle.
    """
    gs = _get_game(ctx)

    async def _run():
        candidates = await gs.get_global_settle_scan()
        if not candidates:
            return "No valid settle locations found on revealed map."
        return nr.narrate_settle_candidates(candidates)

    return await _logged(ctx, "get_global_settle_advisor", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_builder_tasks(ctx: Context) -> str:
    """Get a prioritized task board for all your builders.

    Scans your territory for tiles needing improvements and matches them
    with idle builders. Like the builder lens in the UI — shows what to
    build where and which builder is closest.

    Priority tiers:
    - URGENT: Pillaged improvements (yield loss), unimproved strategic resources
    - HIGH: Unimproved luxury/bonus resources
    - NORMAL: Empty tiles that could benefit from farms/mines/lumber mills

    Call this before issuing builder orders each turn.
    """
    gs = _get_game(ctx)

    async def _run():
        tasks, builders = await gs.get_builder_tasks()
        return nr.narrate_builder_tasks(tasks, builders)

    return await _logged(ctx, "get_builder_tasks", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_empire_resources(ctx: Context) -> str:
    """Get a summary of all resources in and near your empire.

    Shows owned resources (improved/unimproved) grouped by type,
    and unclaimed resources near your cities.
    """
    gs = _get_game(ctx)

    async def _run():
        stockpiles, owned, nearby, luxuries = await gs.get_empire_resources()
        return nr.narrate_empire_resources(stockpiles, owned, nearby, luxuries)

    return await _logged(ctx, "get_empire_resources", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_strategic_map(ctx: Context) -> str:
    """Get fog-of-war boundaries and unclaimed resources across the map.

    Shows how far explored territory extends from each city (in 6 directions),
    highlighting directions that need exploration. Also lists unclaimed luxury
    and strategic resources on revealed but unowned land.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_strategic_map",
        {},
        lambda: _narrate(gs.get_strategic_map, nr.narrate_strategic_map),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_diplomacy(ctx: Context) -> str:
    """Get diplomatic status with all known civilizations.

    Shows diplomatic state (Friendly/Neutral/Unfriendly), relationship modifiers
    with scores and reasons, grievances, delegations/embassies, and available
    diplomatic actions you can take. Also shows visible enemy city details
    (name, population, loyalty, walls).
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_diplomacy",
        {},
        lambda: _narrate(gs.get_diplomacy, nr.narrate_diplomacy),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_tech_civics(ctx: Context) -> str:
    """Get technology and civic research status.

    Shows current research, current civic, turns remaining,
    and lists of available technologies and civics to choose from.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_tech_civics",
        {},
        lambda: _narrate(gs.get_tech_civics, nr.narrate_tech_civics),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_pending_trades(ctx: Context) -> str:
    """Check for pending trade deal offers from other civilizations.

    Shows what each civ is offering and what they want in return.
    Use respond_to_trade to accept or reject.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_pending_trades",
        {},
        lambda: _narrate(gs.get_pending_deals, nr.narrate_pending_deals),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_policies(ctx: Context) -> str:
    """Get current government, policy slots, and available policies.

    Shows current government type, each policy slot with its type and current
    policy (if any), and all unlocked policies grouped by compatible slot type.
    Wildcard slots accept any policy type.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx, "get_policies", {}, lambda: _narrate(gs.get_policies, nr.narrate_policies)
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_notifications(ctx: Context) -> str:
    """Get all active game notifications.

    Shows action-required items (need your decision) and informational
    notifications. Action-required items include which MCP tool to use
    to resolve them. Call this to check what needs attention without
    ending the turn.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_notifications",
        {},
        lambda: _narrate(gs.get_notifications, nr.narrate_notifications),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_pending_diplomacy(ctx: Context) -> str:
    """Check for pending diplomacy encounters (e.g. first meeting with a civ).

    Diplomacy encounters block turn progression. Call this if end_turn
    reports the turn didn't advance. Returns any open sessions with their
    dialogue text, visible buttons, and response guidance.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_pending_diplomacy",
        {},
        lambda: _narrate(gs.get_diplomacy_sessions, nr.narrate_diplomacy_sessions),
    )


# ---------------------------------------------------------------------------
# Action tools (mutating)
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_governors(ctx: Context) -> str:
    """Get governor status, appointed governors, and available types.

    Shows governor points, currently appointed governors with assignments,
    and governors available to appoint. Use appoint_governor to appoint one.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_governors",
        {},
        lambda: _narrate(gs.get_governors, nr.narrate_governors),
    )


@mcp.tool()
async def appoint_governor(ctx: Context, governor_type: str) -> str:
    """Appoint a new governor.

    Args:
        governor_type: e.g. GOVERNOR_THE_EDUCATOR (Pingala), GOVERNOR_THE_DEFENDER (Victor)

    Requires available governor points. Use get_governors to see options.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "appoint_governor",
        {"governor_type": governor_type},
        lambda: gs.appoint_governor(governor_type),
    )


@mcp.tool()
async def assign_governor(ctx: Context, governor_type: str, city_id: int) -> str:
    """Assign an appointed governor to a city.

    Args:
        governor_type: The governor type (from get_governors output)
        city_id: The city ID (from get_cities output)

    Governor must already be appointed. Takes several turns to establish.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "assign_governor",
        {"governor_type": governor_type, "city_id": city_id},
        lambda: gs.assign_governor(governor_type, city_id),
    )


@mcp.tool()
async def promote_governor(
    ctx: Context, governor_type: str, promotion_type: str
) -> str:
    """Promote a governor with a new ability.

    Args:
        governor_type: The governor type (from get_governors output)
        promotion_type: The promotion type (from get_governors output, shown under each governor)

    Requires available governor points. Use get_governors to see available promotions.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "promote_governor",
        {"governor_type": governor_type, "promotion_type": promotion_type},
        lambda: gs.promote_governor(governor_type, promotion_type),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_unit_promotions(ctx: Context, unit_id: int) -> str:
    """List available promotions for a unit.

    Args:
        unit_id: The unit's composite ID (from get_units output)

    Shows promotions filtered by the unit's promotion class.
    Only units with enough XP will have promotions available.
    """
    gs = _get_game(ctx)

    async def _run():
        status = await gs.get_unit_promotions(unit_id)
        return nr.narrate_unit_promotions(status)

    return await _logged(ctx, "get_unit_promotions", {"unit_id": unit_id}, _run)


@mcp.tool()
async def promote_unit(ctx: Context, unit_id: int, promotion_type: str) -> str:
    """Apply a promotion to a unit.

    Args:
        unit_id: The unit's composite ID (from get_units output)
        promotion_type: e.g. PROMOTION_BATTLECRY, PROMOTION_TORTOISE

    Use get_unit_promotions first to see available options.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "promote_unit",
        {"unit_id": unit_id, "promotion_type": promotion_type},
        lambda: gs.promote_unit(unit_id, promotion_type),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_city_states(ctx: Context) -> str:
    """List known city-states with envoy counts and types.

    Shows envoy tokens available, each city-state's type (Scientific,
    Industrial, etc.), how many envoys you've sent, and who is suzerain.
    Use send_envoy to send an envoy.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_city_states",
        {},
        lambda: _narrate(gs.get_city_states, nr.narrate_city_states),
    )


@mcp.tool()
async def send_envoy(ctx: Context, player_id: int) -> str:
    """Send an envoy to a city-state.

    Args:
        player_id: The city-state's player ID (from get_city_states)

    Requires available envoy tokens. Use get_city_states to see options.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx, "send_envoy", {"player_id": player_id}, lambda: gs.send_envoy(player_id)
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_pantheon_beliefs(ctx: Context) -> str:
    """Get pantheon status and available beliefs for selection.

    Shows current pantheon (if any), faith balance, and all available
    pantheon beliefs with their bonuses. Use choose_pantheon to found one.
    """
    gs = _get_game(ctx)

    async def _run():
        status = await gs.get_pantheon_status()
        return nr.narrate_pantheon_status(status)

    return await _logged(ctx, "get_pantheon_beliefs", {}, _run)


@mcp.tool()
async def choose_pantheon(ctx: Context, belief_type: str) -> str:
    """Found a pantheon with the specified belief.

    Args:
        belief_type: e.g. BELIEF_GOD_OF_THE_FORGE, BELIEF_DIVINE_SPARK

    Use get_pantheon_beliefs first to see options. Requires enough faith
    and no existing pantheon.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "choose_pantheon",
        {"belief_type": belief_type},
        lambda: gs.choose_pantheon(belief_type),
    )


@mcp.tool()
async def get_religion_beliefs(ctx: Context) -> str:
    """Get religion founding status, available religions, and available beliefs.

    Shows whether you've founded a religion, available religion types to choose,
    and beliefs grouped by class (Follower, Founder, Enhancer, Worship).
    Use found_religion to found a religion after your Great Prophet activates.
    """
    gs = _get_game(ctx)

    async def _run():
        status = await gs.get_religion_founding_status()
        return nr.narrate_religion_founding_status(status)

    return await _logged(ctx, "get_religion_beliefs", {}, _run)


@mcp.tool()
async def found_religion(
    ctx: Context, religion_type: str, follower_belief: str, founder_belief: str
) -> str:
    """Found a religion with a chosen name, follower belief, and founder belief.

    Args:
        religion_type: e.g. RELIGION_HINDUISM, RELIGION_BUDDHISM, RELIGION_ISLAM
        follower_belief: e.g. BELIEF_WORK_ETHIC, BELIEF_CHORAL_MUSIC
        founder_belief: e.g. BELIEF_STEWARDSHIP, BELIEF_CHURCH_PROPERTY

    Requires your Great Prophet to have already activated on a Holy Site
    (via UNITOPERATION_FOUND_RELIGION). Use get_religion_beliefs
    first to see available options.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "found_religion",
        {
            "religion_type": religion_type,
            "follower_belief": follower_belief,
            "founder_belief": founder_belief,
        },
        lambda: gs.found_religion(religion_type, follower_belief, founder_belief),
    )


@mcp.tool()
async def upgrade_unit(ctx: Context, unit_id: int) -> str:
    """Upgrade a unit to its next type (e.g. Slinger -> Archer).

    Args:
        unit_id: The unit's composite ID (from get_units output)

    Requires the right technology, enough gold, and the unit must have
    moves remaining. The unit's movement is consumed by upgrading.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx, "upgrade_unit", {"unit_id": unit_id}, lambda: gs.upgrade_unit(unit_id)
    )


@mcp.tool()
async def get_dedications(ctx: Context) -> str:
    """Get current era age, available dedications, and active ones.

    Shows era score thresholds, whether you're in a Golden/Dark/Normal age,
    and lists available dedication choices with their bonuses.
    Use choose_dedication to select one when required.
    """
    gs = _get_game(ctx)

    async def _run():
        status = await gs.get_dedications()
        return nr.narrate_dedications(status)

    return await _logged(ctx, "get_dedications", {}, _run)


@mcp.tool()
async def choose_dedication(ctx: Context, dedication_index: int) -> str:
    """Choose a dedication/commemoration for the current era.

    Args:
        dedication_index: The index of the dedication (from get_dedications output)

    Use get_dedications first to see available options and their bonuses.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "choose_dedication",
        {"dedication_index": dedication_index},
        lambda: gs.choose_dedication(dedication_index),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_trade_options(ctx: Context, other_player_id: int) -> str:
    """See what both sides can trade — like opening the trade screen.

    Args:
        other_player_id: The player ID (from get_diplomacy output)

    Shows gold, resources, favor, open borders status, and alliance eligibility
    for both you and the other civilization. Use before propose_trade to see
    what's available.
    """
    gs = _get_game(ctx)

    async def _run():
        opts = await gs.get_deal_options(other_player_id)
        return nr.narrate_deal_options(opts)

    return await _logged(
        ctx, "get_trade_options", {"other_player_id": other_player_id}, _run
    )


@mcp.tool()
async def respond_to_trade(ctx: Context, other_player_id: int, accept: bool) -> str:
    """Accept or reject a pending trade deal.

    Args:
        other_player_id: The player ID of the civilization (from get_pending_trades)
        accept: True to accept the deal, False to reject it

    Use get_pending_trades first to see what's being offered.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "respond_to_trade",
        {"other_player_id": other_player_id, "accept": accept},
        lambda: gs.respond_to_deal(other_player_id, accept),
    )


@mcp.tool()
async def propose_trade(
    ctx: Context,
    other_player_id: int,
    offer_gold: int = 0,
    offer_gold_per_turn: int = 0,
    offer_resources: str = "",
    offer_favor: int = 0,
    offer_open_borders: bool = False,
    request_gold: int = 0,
    request_gold_per_turn: int = 0,
    request_resources: str = "",
    request_favor: int = 0,
    request_open_borders: bool = False,
    joint_war_target: int = 0,
    mode: str = "send",
) -> str:
    """Propose a trade deal to another civilization.

    Args:
        other_player_id: The player ID (from get_diplomacy output)
        offer_gold: Lump sum gold to give them
        offer_gold_per_turn: Gold per turn to give them (30-turn duration)
        offer_resources: Comma-separated resource types to offer, e.g. "RESOURCE_SILK,RESOURCE_TEA"
        offer_favor: Diplomatic favor to offer
        offer_open_borders: True to offer our open borders
        request_gold: Lump sum gold to request from them
        request_gold_per_turn: Gold per turn to request (30-turn duration)
        request_resources: Comma-separated resource types to request
        request_favor: Diplomatic favor to request from them
        request_open_borders: True to request their open borders
        joint_war_target: Player ID of a third civ to declare joint war against
        mode: "send" to commit the deal, "test" to preview AI's counter-offer without committing

    Examples: Gift 100 gold: offer_gold=100. Trade silk for 3 gpt: offer_resources="RESOURCE_SILK", request_gold_per_turn=3.
    Mutual open borders: offer_open_borders=True, request_open_borders=True.
    Test a deal first: mode="test" to see what the AI thinks is fair, then mode="send" to commit.
    """
    gs = _get_game(ctx)
    try:
        mode = _normalize_trade_mode(mode)
    except BeliefEngineError as exc:
        return f"Error: {exc}"

    offer_items: list[dict] = []
    request_items: list[dict] = []
    if offer_gold > 0:
        offer_items.append({"type": "GOLD", "amount": offer_gold, "duration": 0})
    if offer_gold_per_turn > 0:
        offer_items.append(
            {"type": "GOLD", "amount": offer_gold_per_turn, "duration": 30}
        )
    for res in (r.strip() for r in offer_resources.split(",") if r.strip()):
        offer_items.append(
            {"type": "RESOURCE", "name": res, "amount": 1, "duration": 30}
        )
    if offer_favor > 0:
        offer_items.append({"type": "FAVOR", "amount": offer_favor})
    if offer_open_borders:
        offer_items.append({"type": "AGREEMENT", "subtype": "OPEN_BORDERS"})
    if request_gold > 0:
        request_items.append({"type": "GOLD", "amount": request_gold, "duration": 0})
    if request_gold_per_turn > 0:
        request_items.append(
            {"type": "GOLD", "amount": request_gold_per_turn, "duration": 30}
        )
    for res in (r.strip() for r in request_resources.split(",") if r.strip()):
        request_items.append(
            {"type": "RESOURCE", "name": res, "amount": 1, "duration": 30}
        )
    if request_favor > 0:
        request_items.append({"type": "FAVOR", "amount": request_favor})
    if request_open_borders:
        request_items.append({"type": "AGREEMENT", "subtype": "OPEN_BORDERS"})
    if joint_war_target > 0:
        # Joint war is mutual — both sides commit
        offer_items.append({"type": "AGREEMENT", "subtype": "JOINT_WAR"})
        request_items.append({"type": "AGREEMENT", "subtype": "JOINT_WAR"})

    if not offer_items and not request_items:
        return "Error: must specify at least one offer or request item"

    public_params = {
        "other_player_id": other_player_id,
        "offer_gold": offer_gold,
        "offer_gold_per_turn": offer_gold_per_turn,
        "offer_resources": offer_resources,
        "offer_favor": offer_favor,
        "offer_open_borders": offer_open_borders,
        "request_gold": request_gold,
        "request_gold_per_turn": request_gold_per_turn,
        "request_resources": request_resources,
        "request_favor": request_favor,
        "request_open_borders": request_open_borders,
        "joint_war_target": joint_war_target,
        "mode": mode,
    }
    if mode == "test":
        return await _logged(
            ctx,
            "propose_trade",
            public_params,
            lambda: gs.test_trade(other_player_id, offer_items, request_items),
        )

    return await _logged(
        ctx,
        "propose_trade",
        public_params,
        lambda: gs.propose_trade(other_player_id, offer_items, request_items),
    )


@mcp.tool()
async def propose_peace(ctx: Context, other_player_id: int) -> str:
    """Propose white peace to a civilization you're at war with.

    Args:
        other_player_id: The player ID (from get_diplomacy output)

    Requires being at war and past the 10-turn war cooldown.
    The AI may accept or reject based on war score and relationship.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "propose_peace",
        {"other_player_id": other_player_id},
        lambda: gs.propose_peace(other_player_id),
    )


@mcp.tool()
async def set_policies(ctx: Context, assignments: str) -> str:
    """Set policy cards in government slots.

    Args:
        assignments: Comma-separated slot assignments, e.g.
            "0=POLICY_AGOGE,1=POLICY_URBAN_PLANNING"
            Slots not listed keep their current policy. Use NONE to
            explicitly clear a slot (e.g. "2=NONE"). Use get_policies to
            see available policies and slot indices.

    Wildcard slots can accept any policy type. Military slots accept
    military policies, economic slots accept economic policies, etc.
    """
    gs = _get_game(ctx)

    async def _run():
        parsed: dict[int, str] = {}
        for pair in assignments.split(","):
            pair = pair.strip()
            if "=" not in pair:
                continue
            idx_str, policy = pair.split("=", 1)
            parsed[int(idx_str.strip())] = policy.strip()
        if not parsed:
            return "Error: no valid assignments. Format: '0=POLICY_AGOGE,1=POLICY_URBAN_PLANNING'"
        return await gs.set_policies(parsed)

    return await _logged(ctx, "set_policies", {"assignments": assignments}, _run)


@mcp.tool()
async def respond_to_diplomacy(
    ctx: Context, other_player_id: int, response: str
) -> str:
    """Respond to a pending diplomacy encounter.

    Args:
        other_player_id: The player ID of the other civilization (from get_pending_diplomacy)
        response: "POSITIVE" (friendly) or "NEGATIVE" (dismissive)

    First meetings typically have 2-3 rounds. The tool automatically detects
    and closes goodbye-phase sessions (where dialogue text stops changing).
    If SESSION_CONTINUES is returned, send another response for the next round.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "respond_to_diplomacy",
        {"other_player_id": other_player_id, "response": response},
        lambda: gs.diplomacy_respond(other_player_id, response),
    )


@mcp.tool()
async def send_diplomatic_action(
    ctx: Context, other_player_id: int, action: str
) -> str:
    """Send a proactive diplomatic action to another civilization.

    Args:
        other_player_id: The player ID (from get_diplomacy output)
        action: One of: DIPLOMATIC_DELEGATION, DECLARE_FRIENDSHIP, DENOUNCE,
                RESIDENT_EMBASSY, OPEN_BORDERS,
                DECLARE_SURPRISE_WAR, DECLARE_FORMAL_WAR, DECLARE_HOLY_WAR,
                DECLARE_LIBERATION_WAR, DECLARE_RECONQUEST_WAR,
                DECLARE_PROTECTORATE_WAR, DECLARE_COLONIAL_WAR,
                DECLARE_TERRITORIAL_WAR

    Delegations cost 25 gold and can be rejected if the civ dislikes you.
    Embassies require Writing tech. Use get_diplomacy to see available actions.
    Surprise war is always available if not allied/friends. Other war types
    (casus belli) require specific civics and conditions.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "send_diplomatic_action",
        {"other_player_id": other_player_id, "action": action},
        lambda: gs.send_diplomatic_action(other_player_id, action),
    )


@mcp.tool()
async def form_alliance(
    ctx: Context, other_player_id: int, alliance_type: str = "MILITARY"
) -> str:
    """Form an alliance with another civilization.

    Args:
        other_player_id: The player ID (from get_diplomacy output)
        alliance_type: One of: MILITARY, RESEARCH, CULTURAL, ECONOMIC, RELIGIOUS

    Requires declared friendship and Diplomatic Service civic.
    Use get_trade_options to check alliance eligibility first.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "form_alliance",
        {"other_player_id": other_player_id, "alliance_type": alliance_type},
        lambda: gs.form_alliance(other_player_id, alliance_type.upper()),
    )


@mcp.tool()
async def city_action(
    ctx: Context,
    city_id: int,
    action: str,
    target_x: Optional[int] = None,
    target_y: Optional[int] = None,
) -> str:
    """Issue a command to a city.

    Args:
        city_id: City ID (from get_cities output)
        action: Currently supported: 'attack' (city ranged attack)
        target_x: Target X coordinate (required for attack)
        target_y: Target Y coordinate (required for attack)

    For attack: city must have walls and not have fired this turn.
    Range is 2 tiles from city center.

    For captured/disloyal city decisions (city_id is ignored, uses pending city):
    - 'keep': Keep the city (works for both captured and loyalty-flipped cities)
    - 'reject': Reject/free a disloyal city (loyalty flip only)
    - 'raze': Raze a captured city (military conquest only)
    - 'liberate_founder': Liberate to original founder
    - 'liberate_previous': Liberate to previous owner
    """
    gs = _get_game(ctx)
    match action:
        case "attack":
            if target_x is None or target_y is None:
                return "Error: attack requires target_x and target_y"
            result = await _logged(
                ctx,
                "city_action",
                {
                    "city_id": city_id,
                    "action": action,
                    "target_x": target_x,
                    "target_y": target_y,
                },
                lambda: gs.city_attack(city_id, target_x, target_y),
            )
            _get_camera(ctx).push(target_x, target_y, "city attack")
            return result
        case "keep" | "reject" | "raze" | "liberate_founder" | "liberate_previous":
            return await _logged(
                ctx,
                "city_action",
                {"city_id": city_id, "action": action},
                lambda: gs.resolve_city_capture(action),
            )
        case _:
            return f"Error: Unknown city action '{action}'. Available: attack, keep, reject, raze, liberate_founder, liberate_previous"


@mcp.tool()
async def unit_action(
    ctx: Context,
    unit_id: int,
    action: str,
    target_x: Optional[int] = None,
    target_y: Optional[int] = None,
    improvement: Optional[str] = None,
) -> str:
    """Issue a command to a unit.

    Args:
        unit_id: The unit's composite ID (from get_units output)
        action: One of: move, attack, fortify, skip, found_city, improve, repair, remove_improvement, remove_feature, build_route, automate, heal, alert, sleep, delete, trade_route, activate, sacrifice_charges, teleport, spread_religion
        target_x: Target X coordinate (required for move/attack/trade_route/teleport)
        target_y: Target Y coordinate (required for move/attack/trade_route/teleport)
        improvement: Improvement type for builders (required for improve), e.g.
            IMPROVEMENT_FARM, IMPROVEMENT_MINE, IMPROVEMENT_QUARRY,
            IMPROVEMENT_PLANTATION, IMPROVEMENT_CAMP, IMPROVEMENT_PASTURE,
            IMPROVEMENT_FISHING_BOATS, IMPROVEMENT_LUMBER_MILL

    For move/attack: provide target_x and target_y.
    For trade_route: provide target_x and target_y of destination city.
    For teleport: provide target_x and target_y of destination city. Traders only, must be idle (not on active route).
    For improve: provide improvement name. Builder must be on the tile.
    For repair: repairs a pillaged improvement on the builder's current tile. No improvement name needed.
    For remove_improvement: demolishes an intact improvement on the builder's current tile (e.g. to replace a farm with a mine). Costs one charge.
    For activate: activates a Great Person on their matching district.
    For sacrifice_charges: Royal Society builder sacrifice — spends ALL builder charges to boost a district project (2% of cost per charge). Builder must be on the district tile.
    For spread_religion: spreads religion at current tile. Missionaries/Apostles only.
    For build_route: builds road/railroad on current tile. Military Engineers only. No charges used; costs 1 Iron + 1 Coal per railroad tile.
    For fortify/skip/found_city/automate/heal/alert/sleep/delete: no target needed.
    heal = fortify until healed (auto-wake at full HP).
    alert = sleep but auto-wake when enemy enters sight range.
    delete = permanently disband the unit.
    """
    gs = _get_game(ctx)
    unit_index = unit_id % 65536
    params: dict[str, Any] = {"unit_id": unit_id, "action": action}
    if target_x is not None:
        params["target_x"] = target_x
    if target_y is not None:
        params["target_y"] = target_y
    if improvement:
        params["improvement"] = improvement

    async def _run():
        match action.lower():
            case "move":
                if target_x is None or target_y is None:
                    return "Error: move requires target_x and target_y"
                return await gs.move_unit(unit_index, target_x, target_y)
            case "attack":
                if target_x is None or target_y is None:
                    return "Error: attack requires target_x and target_y"
                return await gs.attack_unit(unit_index, target_x, target_y)
            case "fortify":
                return await gs.fortify_unit(unit_index)
            case "skip":
                return await gs.skip_unit(unit_index)
            case "found_city":
                return await gs.found_city(unit_index)
            case "improve":
                if not improvement:
                    return "Error: improve requires improvement name (e.g. IMPROVEMENT_FARM). To repair a pillaged improvement, use action='repair' instead."
                return await gs.improve_tile(unit_index, improvement)
            case "repair":
                return await gs.repair_improvement(unit_index)
            case "remove_improvement":
                return await gs.remove_improvement(unit_index)
            case "remove_feature":
                return await gs.remove_feature(unit_index)
            case "build_route":
                return await gs.build_route(unit_index)
            case "automate":
                return await gs.automate_explore(unit_index)
            case "heal":
                return await gs.heal_unit(unit_index)
            case "alert":
                return await gs.alert_unit(unit_index)
            case "sleep":
                return await gs.sleep_unit(unit_index)
            case "delete":
                return await gs.delete_unit(unit_index)
            case "trade_route":
                if target_x is None or target_y is None:
                    return "Error: trade_route requires target_x and target_y of destination city"
                return await gs.make_trade_route(unit_index, target_x, target_y)
            case "activate":
                return await gs.activate_great_person(unit_index)
            case "sacrifice_charges":
                return await gs.sacrifice_builder_charges(unit_index)
            case "spread_religion":
                return await gs.spread_religion(unit_index)
            case "teleport":
                if target_x is None or target_y is None:
                    return "Error: teleport requires target_x and target_y of the destination city"
                return await gs.teleport_to_city(unit_index, target_x, target_y)
            case _:
                return f"Error: Unknown action '{action}'. Valid: move, attack, fortify, skip, found_city, improve, repair, remove_improvement, remove_feature, build_route, automate, heal, alert, sleep, delete, trade_route, activate, sacrifice_charges, teleport, spread_religion"

    result = await _logged(ctx, "unit_action", params, _run)
    if (
        action.lower() in ("move", "attack", "trade_route", "teleport")
        and target_x is not None
        and target_y is not None
    ):
        _get_camera(ctx).push(target_x, target_y, f"{action}→({target_x},{target_y})")
    return result


@mcp.tool()
async def skip_remaining_units(ctx: Context) -> str:
    """Skip all units that still have moves remaining.

    Useful after diplomacy encounters invalidate all standing orders.
    Uses GameCore FinishMoves on each unit — fast, reliable, no async issues.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx, "skip_remaining_units", {}, lambda: gs.skip_remaining_units()
    )


@mcp.tool()
async def set_city_production(
    ctx: Context,
    city_id: int,
    item_type: str,
    item_name: str,
    target_x: int | None = None,
    target_y: int | None = None,
) -> str:
    """Set what a city should produce.

    Args:
        city_id: City ID (from get_cities output)
        item_type: UNIT, BUILDING, DISTRICT, or PROJECT
        item_name: e.g. UNIT_WARRIOR, BUILDING_MONUMENT, DISTRICT_CAMPUS, PROJECT_LAUNCH_EARTH_SATELLITE
        target_x: X coordinate for district/wonder placement (required for districts — use get_district_advisor to find best tile)
        target_y: Y coordinate for district/wonder placement

    Tip: call get_cities first to see your cities and their IDs.
    """
    gs = _get_game(ctx)
    params: dict = {"city_id": city_id, "item_type": item_type, "item_name": item_name}
    if target_x is not None:
        params["target_x"] = target_x
        params["target_y"] = target_y
    return await _logged(
        ctx,
        "set_city_production",
        params,
        lambda: gs.set_city_production(
            city_id, item_type, item_name, target_x, target_y
        ),
    )


@mcp.tool()
async def purchase_item(
    ctx: Context,
    city_id: int,
    item_type: str,
    item_name: str,
    yield_type: str = "YIELD_GOLD",
) -> str:
    """Purchase a unit or building instantly with gold or faith.

    Args:
        city_id: City ID (from get_cities output)
        item_type: UNIT or BUILDING
        item_name: e.g. UNIT_WARRIOR, BUILDING_MONUMENT
        yield_type: YIELD_GOLD (default) or YIELD_FAITH

    Costs gold/faith immediately. Use get_city_production to see what's available.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "purchase_item",
        {
            "city_id": city_id,
            "item_type": item_type,
            "item_name": item_name,
            "yield_type": yield_type,
        },
        lambda: gs.purchase_item(city_id, item_type, item_name, yield_type),
    )


@mcp.tool()
async def set_research(ctx: Context, tech_or_civic: str, category: str = "tech") -> str:
    """Choose a technology or civic to research.

    Args:
        tech_or_civic: The type name, e.g. TECH_POTTERY or CIVIC_CRAFTSMANSHIP
        category: "tech" or "civic" (default: tech)

    Tip: call get_tech_civics first to see available options.
    """
    gs = _get_game(ctx)

    async def _run():
        if category.lower() == "civic":
            return await gs.set_civic(tech_or_civic)
        return await gs.set_research(tech_or_civic)

    return await _logged(
        ctx,
        "set_research",
        {"tech_or_civic": tech_or_civic, "category": category},
        _run,
    )


@mcp.tool(annotations={"destructiveHint": True})
async def end_turn(
    ctx: Context,
    tactical: str = "",
    strategic: str = "",
    tooling: str = "",
    planning: str = "",
    hypothesis: str = "",
) -> str:
    """End the current turn.

    Make sure you've moved all units, set production, and chosen research
    before ending the turn.

    All 5 reflection parameters are required and must be non-empty.
    These form the per-turn diary — your persistent memory across sessions:
        tactical: What happened this turn — combat, movements, improvements.
        strategic: Current standing vs rivals — yields, city count, victory path.
        tooling: Tool issues or observations. Write "No issues" if none.
        planning: Concrete actions for the next 5-10 turns.
        hypothesis: Predictions — enemy behavior, resource needs, timelines.

    IMPORTANT: Reflections are recorded BEFORE the AI processes its turn.
    Anything that surfaces after end_turn (diplomacy proposals, AI movements,
    events reported in the turn result) belongs in the NEXT turn's diary.
    If end_turn is blocked and you call it again after resolving the blocker,
    the diary entry from the first call is kept — do not repeat reflections.
    """
    gs = _get_game(ctx)

    reflections = {
        "tactical": tactical,
        "strategic": strategic,
        "tooling": tooling,
        "planning": planning,
        "hypothesis": hypothesis,
    }
    missing = [k for k, v in reflections.items() if not v.strip()]
    if missing:
        return (
            f"Empty reflections: {', '.join(missing)}. "
            "Provide non-empty entries for all 5 fields: "
            "tactical, strategic, tooling, planning, hypothesis."
        )

    # Model ID comes from CIV_MCP_AGENT_MODEL env var (set by eval runner)
    env_model = os.environ.get("CIV_MCP_AGENT_MODEL", "")
    if env_model:
        _get_logger(ctx).set_agent_model(env_model)

    # Capture diary state and write BEFORE advancing the turn.
    # This ensures the entry is saved even if the session is interrupted
    # during AI turn processing.
    #
    # If the last end_turn hit a blocker (diplomacy, WC), the turn may have
    # advanced during processing. On retry, we merge reflections into the
    # previous entry rather than writing a duplicate with terse reflections.
    _diary_turn = 0
    _diary_player_id = -1
    _diary_civ_type = None
    _diary_seed = None
    _diary_run_id = _get_logger(ctx).session_id
    _diary_snapshot = None
    _is_retry = getattr(gs, "_end_turn_blocked", False)
    try:
        ov = await gs.get_game_overview()
        _diary_player_id = ov.player_id
        _diary_turn = ov.turn
        # Keep logger/spatial turn in sync (agent may not call get_game_overview every turn)
        _get_logger(ctx).set_turn(ov.turn)
        _get_spatial(ctx).set_turn(ov.turn)
    except Exception:
        log.warning("Diary: failed to capture overview", exc_info=True)
    try:
        _diary_civ_type, _diary_seed = await gs.get_game_identity()
    except Exception:
        log.warning("Diary: failed to get game identity", exc_info=True)

    if _is_retry and _diary_civ_type is not None:
        # Merge reflections into the most recent agent row (from the
        # previous end_turn call that wrote before hitting a blocker).
        # Merges into whichever turn that row belongs to — handles both
        # same-turn retries and turn-advanced-during-blocker cases.
        try:
            path = _diary_path(_diary_civ_type, _diary_seed, _diary_run_id)
            merged_row = _merge_agent_reflections(
                path, gs._diary_written_turn, reflections
            )
            if merged_row:
                log.info(
                    "Diary: merged retry reflections into turn %s",
                    gs._diary_written_turn,
                )
                # Re-emit merged row so CloudSink gets the updated reflections
                await _get_logger(ctx)._emitter.emit(EVENT_DIARY_ROW, merged_row)
        except Exception:
            log.warning("Diary: failed to merge reflections", exc_info=True)
    elif (
        _diary_civ_type is not None
        and _diary_turn > 0
        and gs._diary_written_turn != _diary_turn
    ):
        try:
            _diary_snapshot = await gs.get_diary_snapshot()
        except Exception:
            log.warning("Diary: failed to capture snapshot", exc_info=True)
        if _diary_snapshot:
            game_id = f"{_diary_civ_type}_{_diary_seed}"
            ts = datetime.now(timezone.utc).isoformat()
            # MCP client metadata (from handshake)
            agent_client = ""
            agent_client_ver = ""
            try:
                ci = ctx.session.client_params.clientInfo
                agent_client = ci.name or ""
                agent_client_ver = ci.version or ""
            except Exception:
                pass
            try:
                _emitter = _get_logger(ctx)._emitter
                # Write one row per player (emitter routes to sinks)
                for pr in _diary_snapshot.players:
                    row = asdict(pr)
                    row["v"] = 1
                    row["turn"] = _diary_turn
                    row["game"] = game_id
                    row["timestamp"] = ts
                    if pr.pid == _diary_player_id:
                        row["is_agent"] = True
                        # Merge agent extras
                        ag = _diary_snapshot.agent
                        row["diplo_states"] = ag.diplo_states
                        row["suzerainties"] = ag.suzerainties
                        row["envoys_available"] = ag.envoys_available
                        row["envoys_sent"] = ag.envoys_sent
                        row["gp_points"] = ag.gp_points
                        row["governors"] = ag.governors
                        row["trade_routes"] = {
                            "capacity": ag.trade_capacity,
                            "active": ag.trade_active,
                            "domestic": ag.trade_domestic,
                            "international": ag.trade_international,
                        }
                        row["reflections"] = reflections
                        row["agent_client"] = agent_client
                        row["agent_client_ver"] = agent_client_ver
                        row["agent_model"] = env_model
                        # Eval metadata from emitter (only non-empty)
                        for _mk, _mv in _emitter.metadata.items():
                            if _mv:
                                row[_mk] = _mv
                    await _emitter.emit(EVENT_DIARY_ROW, row)
                # Write one row per city
                for cr in _diary_snapshot.cities:
                    row = asdict(cr)
                    row["v"] = 1
                    row["turn"] = _diary_turn
                    row["game"] = game_id
                    await _emitter.emit(EVENT_CITY_ROW, row)
                gs._diary_written_turn = _diary_turn
            except Exception:
                log.warning("Diary: failed to write entry", exc_info=True)

    # Advance the turn
    result = await _logged(ctx, "end_turn", {}, gs.end_turn)

    # ---------------------------------------------------------------
    # Auto-recover from AI turn hangs (transparent to agent).
    # end_turn returns "HANG:{turn}:{save}|..." when AI processing is
    # stuck after ~39s of polling with no blockers found.
    # Recovery: restart_and_load the MCP autosave, reconnect, retry
    # up to _MAX_HANG_RETRIES times with escalating waits.
    # ---------------------------------------------------------------
    _MAX_HANG_RETRIES = 3
    _HANG_EXTRA_WAIT = [0, 15, 30]  # extra seconds before retry per attempt

    if result.startswith("HANG:") and not gs._hang_retry_active:
        parts = result.split("|", 1)
        hang_info = parts[
            0
        ]  # "HANG:57:AutoSave_0057" (Linux) or "HANG:57:0_MCP_0057" (Windows)
        _, hang_turn, hang_save = hang_info.split(":")
        hang_turn_int = int(hang_turn)

        # Check save file exists before attempting recovery.
        # MCP saves (0_MCP_*) are in SINGLE_SAVE_DIR; game autosaves
        # (AutoSave_*) are in SAVE_DIR (auto/ subdir).
        save_path = os.path.join(game_launcher.SINGLE_SAVE_DIR, f"{hang_save}.Civ6Save")
        if not os.path.exists(save_path):
            save_path = os.path.join(game_launcher.SAVE_DIR, f"{hang_save}.Civ6Save")
        if not os.path.exists(save_path):
            log.error(
                "HANG RECOVERY: Save file %s not found, cannot auto-recover",
                save_path,
            )
            # Fall through — return the hang message to agent
        else:
            identity_before = gs._game_identity
            gs._hang_retry_active = True
            try:
                for attempt in range(1, _MAX_HANG_RETRIES + 1):
                    extra_wait = _HANG_EXTRA_WAIT[
                        min(attempt - 1, len(_HANG_EXTRA_WAIT) - 1)
                    ]
                    log.warning(
                        "HANG RECOVERY: attempt %d/%d for T%s "
                        "(extra wait: %ds, save: %s)",
                        attempt,
                        _MAX_HANG_RETRIES,
                        hang_turn,
                        extra_wait,
                        hang_save,
                    )

                    # Step 1: Kill + relaunch + OCR load
                    restart_result = await game_launcher.restart_and_load(hang_save)
                    log.info("HANG RECOVERY: restart_and_load: %s", restart_result)

                    # Step 2: Reconnect
                    conn = gs.conn
                    reconnected = False
                    for rc_attempt in range(30):
                        try:
                            await conn.reconnect()
                            if conn.gamecore_index is not None:
                                reconnected = True
                                break
                        except ConnectionError:
                            pass
                        await asyncio.sleep(1)

                    if not reconnected:
                        log.error(
                            "HANG RECOVERY: could not reconnect (attempt %d)",
                            attempt,
                        )
                        continue  # try the whole cycle again

                    # Step 2b: Verify correct game loaded (with retries).
                    # The game may still be on the leader screen after
                    # restart — Lua states exist but game APIs aren't
                    # fully initialized. Retry the check rather than
                    # restarting the entire recovery cycle.
                    if identity_before is not None:
                        identity_ok = False
                        for id_check in range(3):
                            try:
                                actual = await gs.get_game_identity()
                                if actual == identity_before:
                                    identity_ok = True
                                    break
                                log.warning(
                                    "HANG RECOVERY: wrong identity %s vs %s "
                                    "(check %d/3)",
                                    actual,
                                    identity_before,
                                    id_check + 1,
                                )
                            except Exception:
                                log.debug(
                                    "HANG RECOVERY: identity check failed "
                                    "(check %d/3), waiting...",
                                    id_check + 1,
                                )
                            await asyncio.sleep(5)
                        if not identity_ok:
                            log.warning(
                                "HANG RECOVERY: identity check inconclusive "
                                "— proceeding anyway (attempt %d)",
                                attempt,
                            )

                    # Step 3: Reset state flags
                    gs._pending_end_turn = False
                    gs._pending_end_turn_from = None
                    gs._end_turn_blocked = False

                    # Step 4: Extra wait to give AI more processing time
                    if extra_wait > 0:
                        log.info(
                            "HANG RECOVERY: waiting %ds before retry...",
                            extra_wait,
                        )
                        await asyncio.sleep(extra_wait)

                    # Step 5: Retry end_turn
                    log.info(
                        "HANG RECOVERY: retrying end_turn for T%s...",
                        hang_turn,
                    )
                    result = await gs.end_turn()
                    log.info("HANG RECOVERY: retry result: %s", result[:200])

                    if not result.startswith("HANG:"):
                        log.info(
                            "HANG RECOVERY: T%s resolved on attempt %d",
                            hang_turn,
                            attempt,
                        )
                        break  # success — fall through to normal processing
                else:
                    # All retries exhausted
                    earlier = max(1, hang_turn_int - 3)
                    log.error(
                        "HANG RECOVERY: all %d attempts failed for T%s",
                        _MAX_HANG_RETRIES,
                        hang_turn,
                    )
                    return (
                        f"AI turn hung at T{hang_turn} after "
                        f"{_MAX_HANG_RETRIES} automatic restart attempts "
                        f"with escalating waits. The hang may be "
                        f"probabilistic — another attempt could work. "
                        f"Try restart_and_load('{hang_save.replace(hang_turn, str(earlier))}') "
                        f"to skip back a few turns."
                    )
            except Exception:
                log.error("HANG RECOVERY: failed", exc_info=True)
                return (
                    f"HANG RECOVERY FAILED at T{hang_turn}: "
                    f"restart_and_load threw an exception. "
                    f"Try restart_and_load('{hang_save}') manually."
                )
            finally:
                gs._hang_retry_active = False

    # Clear stale camera events on successful turn advance
    turn_advanced = (
        "->" in result and "Cannot end turn" not in result and "Error" not in result
    )
    if turn_advanced:
        _get_camera(ctx).clear()
        gs._end_turn_blocked = False
        # Update logger/spatial turn from result ("Turn X -> Y")
        m = re.search(r"Turn \d+ -> (\d+)", result)
        if m:
            new_turn = int(m.group(1))
            _get_logger(ctx).set_turn(new_turn)
            _get_spatial(ctx).set_turn(new_turn)
            heartbeat.write("playing", turn=new_turn)
        # Map capture — record terrain (first turn) + ownership delta
        if _diary_civ_type and _diary_seed:
            try:
                mc = _get_map_capture(ctx)
                mc.bind_game(_diary_civ_type, _diary_seed)
                capture_turn = new_turn if m else _diary_turn
                await mc.capture(gs.conn, capture_turn)
            except Exception:
                log.debug("Map capture failed", exc_info=True)
    elif "Turn paused" in result or "World Congress fires" in result:
        gs._end_turn_blocked = True
        # Safety net: if WC blocker fires repeatedly on the same turn,
        # auto-submit to break infinite loops (agent used wrong voting tool)
        if "World Congress fires" in result:
            wc_turn = getattr(gs, "_wc_blocker_turn", -1)
            wc_count = getattr(gs, "_wc_blocker_count", 0)
            current = _diary_turn or 0
            if wc_turn == current:
                gs._wc_blocker_count = wc_count + 1
                if gs._wc_blocker_count >= 3:
                    log.warning(
                        "WC blocker repeated %d times on T%d — auto-submitting",
                        gs._wc_blocker_count,
                        current,
                    )
                    try:
                        await gs.submit_congress()
                    except Exception:
                        log.debug("WC auto-submit failed", exc_info=True)
            else:
                gs._wc_blocker_turn = current
                gs._wc_blocker_count = 1

    # Log structured game-over entry.
    # Also check on HANG — the game may have ended during AI processing but
    # InGame Lua froze, so end_turn returned HANG instead of GAME OVER.
    # The GameCore fallback in check_game_over can detect this.
    if "HANG:" in result and "GAME OVER" not in result:
        try:
            hang_check = await gs.check_game_over()
            if hang_check is not None:
                gs._last_game_over = hang_check
                vtype = (
                    hang_check.victory_type.replace("VICTORY_", "")
                    .replace("_", " ")
                    .title()
                )
                if hang_check.is_defeat:
                    result = (
                        f"GAME OVER — DEFEAT. {hang_check.winner_leader} "
                        f"of {hang_check.winner_name} won a {vtype} victory. "
                        f"The game has ended. No further actions are possible."
                    )
                else:
                    result = (
                        f"GAME OVER — VICTORY! You won a {vtype} victory! "
                        f"The game has ended."
                    )
        except Exception:
            log.debug("HANG game-over recheck failed", exc_info=True)

    if "GAME OVER" in result:
        heartbeat.write("finished", turn=_diary_turn or 0)
        try:
            gameover = gs._last_game_over
            if gameover is None:
                gameover = await gs.check_game_over()
            if gameover is not None:
                gs._last_game_over = None
                vtype = (
                    gameover.victory_type.replace("VICTORY_", "")
                    .replace("_", " ")
                    .title()
                )
                await _get_logger(ctx).log_game_over(
                    is_defeat=gameover.is_defeat,
                    winner_civ=gameover.winner_name,
                    winner_leader=gameover.winner_leader,
                    victory_type=vtype,
                    player_alive=gameover.player_alive,
                )
            else:
                log.error(
                    "GAME OVER detected but no GameOverStatus available "
                    "— outcome will be missing from log"
                )
        except Exception:
            log.warning("Failed to log game-over entry", exc_info=True)

    # Arm the watchdog after first successful end_turn so it starts
    # polling for game-over independently of future tool calls.
    if "GAME OVER" not in result:
        _get_watchdog(ctx).arm()

    return result


# ---------------------------------------------------------------------------
# Belief Engine
# ---------------------------------------------------------------------------


def _belief_json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise BeliefEngineError(f"{label} must be valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise BeliefEngineError(f"{label} must be a JSON object")
    return value


def _belief_json_list(raw: str, label: str) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise BeliefEngineError(f"{label} must be valid JSON: {exc.msg}") from exc
    if not isinstance(value, list):
        raise BeliefEngineError(f"{label} must be a JSON array")
    return value


def _governance_payload(value: Any) -> Any:
    """Serialize frozen governance contracts without losing typed boundaries."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _governance_payload(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _governance_payload(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_governance_payload(item) for item in value]
    return value


def _governance_proposal_from_dict(raw: dict[str, Any]):
    """Validate one ministerial proposal against the shared governance schema."""

    from civ_mcp.governance import (
        ActionIntent,
        BudgetLock,
        EvidenceRequirement,
        ProbabilityConfidence,
        Proposal,
    )

    proposal_id = str(raw.get("proposal_id") or "")

    def evidence(item: dict[str, Any]) -> EvidenceRequirement:
        return EvidenceRequirement(
            requirement_id=str(item.get("requirement_id") or ""),
            tool=str(item.get("tool") or ""),
            target_entity_id=item.get("target_entity_id"),
            params=item.get("params") or {},
            min_observation_sequence=item.get("min_observation_sequence", 0),
            max_age_turns=item.get("max_age_turns"),
            required_facts=tuple(item.get("required_facts") or ()),
            required_metrics=tuple(item.get("required_metrics") or ()),
            description=str(item.get("description") or ""),
        )

    intents = []
    for item in raw.get("action_intents") or []:
        if not isinstance(item, dict):
            raise BeliefEngineError("action_intents must contain JSON objects")
        requirements = item.get("evidence_requirements") or []
        if not all(isinstance(requirement, dict) for requirement in requirements):
            raise BeliefEngineError(
                "action intent evidence_requirements must contain JSON objects"
            )
        intent_tool = str(item.get("tool") or "")
        supplied_arguments = item.get("arguments")
        supplied_params = item.get("params")
        if (
            supplied_arguments is not None
            and supplied_params is not None
            and supplied_arguments != supplied_params
        ):
            raise BeliefEngineError(
                "action intent arguments and params must match when both are present"
            )
        intent_arguments = _canonical_action_params(
            intent_tool,
            supplied_arguments
            if supplied_arguments is not None
            else supplied_params
            if supplied_params is not None
            else {},
        )
        intents.append(
            ActionIntent(
                intent_id=str(item.get("intent_id") or ""),
                tool=intent_tool,
                arguments=intent_arguments,
                proposal_id=proposal_id,
                evidence_requirements=tuple(evidence(req) for req in requirements),
                allowed_turn=item.get("allowed_turn"),
                arguments_hash=str(item.get("arguments_hash") or ""),
            )
        )
    locks = []
    for item in raw.get("budget_locks") or []:
        if not isinstance(item, dict):
            raise BeliefEngineError("budget_locks must contain JSON objects")
        locks.append(
            BudgetLock(
                resource=str(item.get("resource") or ""),
                amount=item.get("amount", 1),
                scope=str(item.get("scope") or "global"),
                exclusive=item.get("exclusive", False),
                reason=str(item.get("reason") or ""),
            )
        )
    success = raw.get("success") or {}
    if not isinstance(success, dict):
        raise BeliefEngineError("proposal success must be a JSON object")
    return Proposal(
        proposal_id=proposal_id,
        department=str(raw.get("department") or ""),
        summary=str(raw.get("summary") or raw.get("statement") or ""),
        goal_ids=tuple(raw.get("goal_ids") or ()),
        success=ProbabilityConfidence(
            success.get("probability"), success.get("confidence")
        ),
        priority=raw.get("priority"),
        hard_constraints=raw.get("hard_constraints") or {},
        budget_locks=tuple(locks),
        benefits=raw.get("benefits") or {},
        costs=raw.get("costs") or {},
        opportunity_cost=raw.get("opportunity_cost", 0),
        failure_cost=raw.get("failure_cost", 0),
        action_intents=tuple(intents),
        belief_ids=tuple(raw.get("belief_ids") or ()),
        expires_turn=raw.get("expires_turn"),
    )


def _release_stale_budget_locks(
    engine: BeliefEngine,
    *,
    turn: int,
) -> list[str]:
    """Release reservations after their last governed execution turn."""

    released: list[str] = []
    for lock in engine.list("budget_lock", status="active"):
        release_after_turn = int(
            lock.get("release_after_turn", lock.get("created_turn", turn))
        )
        if release_after_turn >= turn:
            continue
        engine.update(
            "budget_lock",
            lock["id"],
            {
                "status": "archived",
                "released_turn": turn,
                "release_reason": "turn_advanced",
            },
            turn=turn,
        )
        released.append(lock["id"])
    return released


def _typed_capabilities_for_turn(
    engine: BeliefEngine,
    *,
    turn: int,
) -> dict[str, Any]:
    snapshot = _typed_snapshot_observation_for_turn(engine, turn=turn)
    if snapshot is None:
        raise BeliefEngineError(
            "A current-turn typed snapshot is required; call get_governance_brief first"
        )
    capabilities = (snapshot.get("facts") or {}).get("capabilities") or {}
    if not isinstance(capabilities, dict) or not capabilities.get("ruleset"):
        raise BeliefEngineError("Typed snapshot is missing ruleset capabilities")
    return capabilities


def _typed_snapshot_observation_for_turn(
    engine: BeliefEngine,
    *,
    turn: int,
) -> dict[str, Any] | None:
    snapshots = [
        item
        for item in engine.list("observation", status="active")
        if item.get("source") == "game_state:typed_snapshot"
        and item.get("observed_turn") == turn
    ]
    return (
        max(
            snapshots,
            key=lambda item: (
                float(item.get("updated_at", 0)),
                float(item.get("created_at", 0)),
                str(item.get("id") or ""),
            ),
        )
        if snapshots
        else None
    )


def _reusable_typed_snapshot_for_turn(
    engine: BeliefEngine,
    *,
    turn: int,
) -> dict[str, Any] | None:
    """Reuse the typed snapshot only while no successful action has changed state."""

    snapshot = _typed_snapshot_observation_for_turn(engine, turn=turn)
    if snapshot is None:
        return None
    captured_at = float(snapshot.get("created_at", 0))
    has_later_mutation = any(
        action.get("selected_turn") == turn
        and action.get("success") is True
        and action.get("executed") is True
        and float(action.get("created_at", 0)) > captured_at
        for action in engine.list("action", status="active")
    )
    return None if has_later_mutation else snapshot


def _validate_ruleset_action_intent(
    *,
    tool: str,
    arguments: Mapping[str, Any],
    capabilities: Mapping[str, Any],
) -> None:
    requirements = {
        "appoint_governor": "governors",
        "assign_governor": "governors",
        "promote_governor": "governors",
        "choose_dedication": "dedications",
        "form_alliance": "alliances",
        "queue_wc_votes": "world_congress",
    }
    capability = requirements.get(tool)
    if capability and not capabilities.get(capability, False):
        raise BeliefEngineError(
            f"Action {tool} requires unavailable ruleset capability: {capability}"
        )
    if tool == "propose_trade" and (
        arguments.get("offer_favor") or arguments.get("request_favor")
    ) and not capabilities.get("diplomatic_favor", False):
        raise BeliefEngineError(
            "Diplomatic favor trade requires unavailable ruleset capability: "
            "diplomatic_favor"
        )


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


async def _capture_governance_snapshot(
    ctx: Context,
    engine: BeliefEngine,
) -> tuple[Any, dict[str, Any], dict[str, Any], list[str], list[dict[str, Any]]]:
    """Capture and ingest the authoritative typed state for one turn."""

    from civ_mcp.governance.snapshot import snapshot_world_state

    snapshot = await _get_game(ctx).get_governance_snapshot()
    world = snapshot_world_state(snapshot)
    projection = engine.ingest_typed_snapshot(world, turn=snapshot.turn)
    released_locks = _release_stale_budget_locks(engine, turn=snapshot.turn)
    active_locks = engine.list("budget_lock", status="active")
    return snapshot, world, projection, released_locks, active_locks


async def _belief_tool(
    ctx: Context,
    tool_name: str,
    params: dict[str, Any],
    operation: Callable[[BeliefEngine, int], Any],
) -> str:
    """Run a belief operation with normal MCP logging and telemetry mirroring."""
    mode = _get_belief_mode(ctx)
    if not mode.records_events:
        return json.dumps(
            {
                "belief_mode": mode.value,
                "disabled": True,
                "tool": tool_name,
                "message": "Belief Engine persistence is disabled in off mode.",
            },
            ensure_ascii=False,
        )
    started = time.monotonic()
    try:
        engine, turn = await _belief_context(ctx)
        result = operation(engine, turn)
        await _flush_belief_events(ctx)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        await _get_logger(ctx).log_tool_call(
            tool_name,
            params,
            text,
            int((time.monotonic() - started) * 1000),
        )
        return text
    except (BeliefEngineError, json.JSONDecodeError, TypeError, ValueError) as exc:
        message = f"Error: {exc}"
        await _get_logger(ctx).log_error(tool_name, message)
        return message
    except ConnectionError as exc:
        message = f"Error: {exc}"
        await _get_logger(ctx).log_error(tool_name, message)
        return message


@mcp.tool()
async def record_observation(
    ctx: Context,
    statement: str,
    facts: str = "{}",
    metrics: str = "{}",
    source: str = "agent",
    reliability: float = 1.0,
    tags: str = "[]",
) -> str:
    """Record a factual observation without interpreting what it means.

    ``facts`` and ``metrics`` are JSON objects.  Metrics use stable keys so
    predictions, belief expectations, and plan exit conditions can evaluate
    them later.  Important MCP query results are also captured automatically.
    """

    params = {
        "statement": statement,
        "facts": facts,
        "metrics": metrics,
        "source": source,
        "reliability": reliability,
        "tags": tags,
    }

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        return engine.create(
            "observation",
            {
                "statement": statement,
                "source": source,
                "facts": _belief_json_object(facts, "facts"),
                "metrics": _belief_json_object(metrics, "metrics"),
                "reliability": reliability,
                "tags": _belief_json_list(tags, "tags"),
                "observed_turn": turn,
            },
            turn=turn,
        )

    return await _belief_tool(ctx, "record_observation", params, _operation)


@mcp.tool()
async def upsert_belief(
    ctx: Context,
    belief_id: str,
    statement: str,
    category: str,
    probability: float,
    confidence: float,
    impact: str = "medium",
    urgency: str = "medium",
    evidence_ids: str = "[]",
    counter_evidence_ids: str = "[]",
    falsifiers: str = "[]",
    expectations: str = "[]",
    action_threshold: float = 0.65,
    replan_threshold: float = 0.5,
    gate_scope: str = "global",
) -> str:
    """Create or revise a belief while retaining its complete revision history."""

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        return engine.upsert(
            "belief",
            belief_id,
            {
                "statement": statement,
                "category": category,
                "probability": probability,
                "confidence": confidence,
                "impact": impact,
                "urgency": urgency,
                "evidence_ids": _belief_json_list(evidence_ids, "evidence_ids"),
                "counter_evidence_ids": _belief_json_list(
                    counter_evidence_ids, "counter_evidence_ids"
                ),
                "falsifiers": _belief_json_list(falsifiers, "falsifiers"),
                "expectations": _belief_json_list(expectations, "expectations"),
                "action_threshold": action_threshold,
                "replan_threshold": replan_threshold,
                "review_required": False,
                "gate_scope": gate_scope,
            },
            turn=turn,
        )

    return await _belief_tool(ctx, "upsert_belief", params, _operation)


@mcp.tool()
async def upsert_hypothesis(
    ctx: Context,
    hypothesis_id: str,
    topic_id: str,
    statement: str,
    probability: float,
    confidence: float,
    evidence_ids: str = "[]",
    counter_evidence_ids: str = "[]",
) -> str:
    """Create or revise one competing explanation in a hypothesis pool."""

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        return engine.upsert(
            "hypothesis",
            hypothesis_id,
            {
                "topic_id": topic_id,
                "statement": statement,
                "probability": probability,
                "confidence": confidence,
                "evidence_ids": _belief_json_list(evidence_ids, "evidence_ids"),
                "counter_evidence_ids": _belief_json_list(
                    counter_evidence_ids, "counter_evidence_ids"
                ),
            },
            turn=turn,
        )

    return await _belief_tool(ctx, "upsert_hypothesis", params, _operation)


@mcp.tool()
async def rebalance_hypothesis_pool(
    ctx: Context, topic_id: str, probabilities: str
) -> str:
    """Atomically redistribute a topic's hypothesis probabilities.

    ``probabilities`` is a JSON object mapping hypothesis IDs to probabilities;
    the values must sum to one (within 0.001).
    """

    params = {"topic_id": topic_id, "probabilities": probabilities}
    return await _belief_tool(
        ctx,
        "rebalance_hypothesis_pool",
        params,
        lambda engine, turn: engine.rebalance_hypotheses(
            topic_id,
            _belief_json_object(probabilities, "probabilities"),
            turn=turn,
        ),
    )


@mcp.tool()
async def upsert_prediction(
    ctx: Context,
    prediction_id: str,
    statement: str,
    probability: float,
    confidence: float,
    deadline_turn: int,
    evaluation: str = "{}",
    belief_ids: str = "[]",
) -> str:
    """Create or revise a falsifiable prediction with a deadline.

    ``evaluation`` may be a metric rule such as
    ``{"metric":"science","operator":">=","value":60}``.
    """

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        return engine.upsert(
            "prediction",
            prediction_id,
            {
                "statement": statement,
                "probability": probability,
                "confidence": confidence,
                "deadline_turn": deadline_turn,
                "evaluation": _belief_json_object(evaluation, "evaluation"),
                "belief_ids": _belief_json_list(belief_ids, "belief_ids"),
                "status": "active",
            },
            turn=turn,
        )

    return await _belief_tool(ctx, "upsert_prediction", params, _operation)


@mcp.tool()
async def resolve_prediction(
    ctx: Context,
    prediction_id: str,
    outcome: bool,
    actual: str,
) -> str:
    """Resolve a prediction manually when the outcome is not metric-evaluable."""

    params = {"prediction_id": prediction_id, "outcome": outcome, "actual": actual}
    return await _belief_tool(
        ctx,
        "resolve_prediction",
        params,
        lambda engine, turn: engine.resolve_prediction(
            prediction_id,
            outcome=outcome,
            actual=actual,
            turn=turn,
        ),
    )


@mcp.tool()
async def upsert_dynamic_plan(
    ctx: Context,
    plan_id: str,
    horizon: int,
    goal: str,
    probability_of_success: float,
    assumptions: str = "[]",
    assumption_thresholds: str = "{}",
    evidence_ids: str = "[]",
    success_conditions: str = "[]",
    exit_conditions: str = "[]",
    review_turn: int = 0,
    gate_scope: str = "global",
) -> str:
    """Create or revise a 5/10/20-turn plan with explicit invalidation rules."""

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        actual_review_turn = review_turn or turn + horizon
        return engine.upsert(
            "plan",
            plan_id,
            {
                "horizon": horizon,
                "goal": goal,
                "probability_of_success": probability_of_success,
                "assumptions": _belief_json_list(assumptions, "assumptions"),
                "assumption_thresholds": _belief_json_object(
                    assumption_thresholds, "assumption_thresholds"
                ),
                "evidence_ids": _belief_json_list(evidence_ids, "evidence_ids"),
                "success_conditions": _belief_json_list(
                    success_conditions, "success_conditions"
                ),
                "exit_conditions": _belief_json_list(exit_conditions, "exit_conditions"),
                "review_turn": actual_review_turn,
                "review_required": False,
                "status": "active",
                "gate_scope": gate_scope,
            },
            turn=turn,
        )

    return await _belief_tool(ctx, "upsert_dynamic_plan", params, _operation)


@mcp.tool()
async def set_plan_status(
    ctx: Context,
    plan_id: str,
    status: str,
    reason: str,
) -> str:
    """Mark a dynamic plan active, completed, abandoned, or needing replan."""

    allowed = {"active", "completed", "abandoned", "needs_replan"}
    params = {"plan_id": plan_id, "status": status, "reason": reason}

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        if status not in allowed:
            raise BeliefEngineError(f"status must be one of: {', '.join(sorted(allowed))}")
        patch: dict[str, Any] = {
            "status": status,
            "status_reason": reason,
            "review_required": status == "needs_replan",
        }
        if status == "active":
            patch.update(
                {
                    "broken_assumptions": [],
                    "triggered_exit_conditions": [],
                }
            )
        return engine.update(
            "plan",
            plan_id,
            patch,
            turn=turn,
        )

    return await _belief_tool(ctx, "set_plan_status", params, _operation)


@mcp.tool()
async def update_belief_entity(
    ctx: Context,
    entity_type: str,
    entity_id: str,
    patch: str,
) -> str:
    """Patch any current Belief Engine entity; history remains append-only."""

    params = {"entity_type": entity_type, "entity_id": entity_id, "patch": patch}
    return await _belief_tool(
        ctx,
        "update_belief_entity",
        params,
        lambda engine, turn: engine.update(
            entity_type,
            entity_id,
            _belief_json_object(patch, "patch"),
            turn=turn,
        ),
    )


@mcp.tool()
async def delete_belief_entity(
    ctx: Context,
    entity_type: str,
    entity_id: str,
    reason: str,
) -> str:
    """Delete current state via a tombstone while retaining audit history."""

    params = {"entity_type": entity_type, "entity_id": entity_id, "reason": reason}
    return await _belief_tool(
        ctx,
        "delete_belief_entity",
        params,
        lambda engine, turn: engine.delete(
            entity_type, entity_id, reason=reason, turn=turn
        ),
    )


@mcp.tool()
async def get_turn_brief(ctx: Context, limit: int = 12) -> str:
    """Return the reviewed Belief Engine state that must guide this turn.

    ``get_game_overview`` includes the same brief automatically.  This tool is
    provided for an explicit precheck or after a major action changes the
    evidence; it is intentionally the single compact entry point instead of
    requiring an agent to remember several separate belief calls.
    """

    params = {"limit": limit}
    return await _belief_tool(
        ctx,
        "get_turn_brief",
        params,
        lambda engine, turn: engine.turn_brief(turn=turn, limit=limit),
    )


@mcp.tool()
async def get_governance_brief(
    ctx: Context,
    limit: int = 12,
    confidence_floor: float = 0.6,
) -> str:
    """Capture typed GameState facts and return the national governance agenda.

    This is the preferred start-of-turn control-plane input. It collects one
    same-turn typed snapshot, projects it into the existing event-sourced graph,
    then combines capabilities, scarce-resource budgets, confidence gaps, and
    current belief gates. No narrated game text is parsed for this snapshot.
    """

    mode = _get_belief_mode(ctx)
    if not mode.captures_governance_snapshot:
        return json.dumps(
            {
                "belief_mode": mode.value,
                "disabled": True,
                "message": (
                    "Live governance snapshots are disabled outside enforce mode; "
                    "use get_game_overview and targeted game queries."
                ),
            },
            ensure_ascii=False,
        )

    started = time.monotonic()
    params = {"limit": limit, "confidence_floor": confidence_floor}
    try:
        if not 0 <= confidence_floor <= 1:
            raise BeliefEngineError("confidence_floor must be between 0 and 1")
        engine, _turn = await _belief_context(ctx)
        snapshot, world, projection, released_locks, active_locks = (
            await _capture_governance_snapshot(ctx, engine)
        )
        belief_brief = engine.turn_brief(turn=snapshot.turn, limit=limit)
        await _flush_belief_events(ctx)
        low_confidence = [
            {
                "id": item["id"],
                "statement": item.get("statement"),
                "confidence": item.get("confidence"),
                "gate_scope": item.get("gate_scope", "global"),
            }
            for item in engine.list("belief", status="active")
            if float(item.get("confidence", 0)) < confidence_floor
        ][: max(1, min(limit, 50))]
        city_ids = [
            item["entity_id"]
            for item in world["entities"]
            if item.get("entity_type") == "city"
        ]
        unit_ids = [
            item["entity_id"]
            for item in world["entities"]
            if item.get("entity_type") == "unit"
        ]
        metrics = world.get("metrics") or {}
        gold_capacity = float(metrics.get("player.gold", 0))
        faith_capacity = float(metrics.get("player.faith", 0))

        def locked_amount(resource: str) -> float:
            return sum(
                float(lock.get("amount", 0))
                for lock in active_locks
                if lock.get("resource") == resource
            )

        def available_scopes(resource: str, scopes: list[str]) -> list[str]:
            held_scopes = {
                str(lock.get("scope") or "global")
                for lock in active_locks
                if lock.get("resource") == resource and lock.get("exclusive")
            }
            if "global" in held_scopes:
                return []
            return [scope for scope in scopes if scope not in held_scopes]

        result = {
            "game_id": engine.game_id,
            "turn": snapshot.turn,
            "snapshot": {
                "snapshot_id": snapshot.snapshot_id,
                "turn_before": snapshot.turn_before,
                "turn_after": snapshot.turn_after,
                "ruleset": world["ruleset"],
                "entity_count": len(world["entities"]),
                "relation_count": len(world["relations"]),
                **projection,
            },
            "capabilities": world["capabilities"],
            "budget_capacity": {
                "gold": gold_capacity,
                "faith": faith_capacity,
                "research": 1,
                "civic": 1,
                "city_production": len(city_ids),
                "unit_action": len(unit_ids),
            },
            "available_budget": {
                "gold": max(0.0, gold_capacity - locked_amount("gold")),
                "faith": max(0.0, faith_capacity - locked_amount("faith")),
                "research_slots": available_scopes("research", ["current"]),
                "civic_slots": available_scopes("civic", ["current"]),
                "city_production_slots": available_scopes(
                    "city_production", city_ids
                ),
                "unit_action_slots": available_scopes("unit_action", unit_ids),
            },
            "confidence_gaps": low_confidence,
            "belief_brief": belief_brief,
            "governance": {
                "goals": engine.list("goal", status="active")[:limit],
                "proposals": engine.list("proposal", status="active")[:limit],
                "critic_reviews": engine.list("critic_review", status="active")[:limit],
                "council_decisions": engine.list("council_decision", status=None)[:limit],
                "budget_locks": active_locks[:limit],
                "released_budget_locks": released_locks,
            },
            "arbitration_order": [
                "hard_constraints",
                "budget_locks",
                "strategic_priority",
                "pareto_dominance",
                "opportunity_cost",
            ],
        }
        text = json.dumps(result, ensure_ascii=False, indent=2)
        await _get_logger(ctx).log_tool_call(
            "get_governance_brief",
            params,
            text,
            int((time.monotonic() - started) * 1000),
        )
        return text
    except (BeliefEngineError, TypeError, ValueError, ConnectionError) as exc:
        message = f"Error: {exc}"
        await _get_logger(ctx).log_error("get_governance_brief", message)
        return message


@mcp.tool()
async def upsert_strategic_goal(
    ctx: Context,
    goal_id: str,
    statement: str,
    priority: int,
    probability: float,
    confidence: float,
    hard_constraints: str = "[]",
    deadline_turn: int = 0,
    parent_goal_id: str = "",
    tags: str = "[]",
) -> str:
    """Create or revise a national goal with separate likelihood/confidence."""

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        from civ_mcp.governance import ProbabilityConfidence, StrategicGoal

        goal = StrategicGoal(
            goal_id=goal_id,
            statement=statement,
            priority=priority,
            success=ProbabilityConfidence(probability, confidence),
            hard_constraints=tuple(
                _belief_json_list(hard_constraints, "hard_constraints")
            ),
            deadline_turn=deadline_turn or None,
            parent_goal_id=parent_goal_id or None,
            tags=tuple(_belief_json_list(tags, "tags")),
        )
        payload = _governance_payload(goal)
        payload.update(
            {
                "statement": statement,
                "probability": probability,
                "confidence": confidence,
            }
        )
        return engine.upsert("goal", goal_id, payload, turn=turn)

    return await _belief_tool(ctx, "upsert_strategic_goal", params, _operation)


@mcp.tool()
async def submit_governance_proposal(ctx: Context, proposal: str) -> str:
    """Submit a structured ministerial proposal; departments cannot execute it.

    The JSON proposal names goals, separate success probability/confidence,
    hard constraints, budget locks, benefits, costs, opportunity cost and exact
    action intents. It remains advisory until ``resolve_governance_council``.
    """

    params = {"proposal": proposal}

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        parsed = _belief_json_object(proposal, "proposal")
        typed = _governance_proposal_from_dict(parsed)
        capabilities = _typed_capabilities_for_turn(engine, turn=turn)
        for intent in typed.action_intents:
            _validate_ruleset_action_intent(
                tool=intent.tool,
                arguments=intent.arguments,
                capabilities=capabilities,
            )
        for goal_id in typed.goal_ids:
            goal = engine.get("goal", goal_id)
            if not goal or goal.get("status") != "active":
                raise BeliefEngineError(f"Referenced active goal not found: {goal_id}")
        for belief_id in typed.belief_ids:
            belief = engine.get("belief", belief_id)
            if not belief or belief.get("status") != "active":
                raise BeliefEngineError(
                    f"Referenced active belief not found: {belief_id}"
                )
        payload = _governance_payload(typed)
        payload.update(
            {
                "status": "active",
                "statement": typed.summary,
                "action_intent": (
                    _governance_payload(typed.action_intents[0])
                    if typed.action_intents
                    else {}
                ),
                "submitted_turn": turn,
                "council_state": "proposed",
                "council_decision_id": None,
                "rejection_reasons": [],
            }
        )
        return engine.upsert(
            "proposal", typed.proposal_id, payload, turn=turn
        )

    return await _belief_tool(ctx, "submit_governance_proposal", params, _operation)


@mcp.tool()
async def review_governance_proposal(
    ctx: Context,
    review_id: str,
    proposal_id: str,
    verdict: str,
    rationale: str,
    probability: float,
    confidence: float,
    conditions: str = "[]",
    counterevidence: str = "[]",
    invalidated_assumptions: str = "[]",
    alternative: str = "",
) -> str:
    """Persist an evidence-grounded Devil's Advocate review.

    A review may agree, agree with concrete conditions, or object. Bare
    objections are rejected: they need counterevidence or an invalidated
    assumption plus a concrete alternative.
    """

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        from civ_mcp.governance import (
            CounterEvidence,
            DevilsAdvocate,
            DevilsAdvocateVerdict,
            ProbabilityConfidence,
        )

        proposal_entity = engine.get("proposal", proposal_id)
        if not proposal_entity or proposal_entity.get("status") != "active":
            raise BeliefEngineError(f"Referenced active proposal not found: {proposal_id}")
        proposal_typed = _governance_proposal_from_dict(proposal_entity)
        evidence_items = _belief_json_list(counterevidence, "counterevidence")
        if not all(isinstance(item, dict) for item in evidence_items):
            raise BeliefEngineError(
                "counterevidence must contain traceable JSON observation objects"
            )
        grounded_evidence = []
        for item in evidence_items:
            observation_id = str(item.get("observation_id") or "")
            observation = engine.get("observation", observation_id)
            if not observation:
                raise BeliefEngineError(
                    f"Counterevidence observation not found: {observation_id}"
                )
            grounded_evidence.append(
                CounterEvidence(
                    observation_id=observation_id,
                    statement=str(item.get("statement") or observation.get("statement") or ""),
                    source_tool=str(
                        item.get("source_tool")
                        or str(observation.get("source") or "").removeprefix("mcp:")
                    ),
                    observed_turn=item.get(
                        "observed_turn", observation.get("observed_turn", turn)
                    ),
                )
            )
        review = DevilsAdvocate.review(
            proposal_typed,
            review_id=review_id,
            verdict=DevilsAdvocateVerdict(verdict),
            rationale=rationale,
            assessment=ProbabilityConfidence(probability, confidence),
            conditions=tuple(_belief_json_list(conditions, "conditions")),
            counterevidence=tuple(grounded_evidence),
            invalidated_assumptions=tuple(
                _belief_json_list(
                    invalidated_assumptions, "invalidated_assumptions"
                )
            ),
            alternative=alternative or None,
        )
        payload = _governance_payload(review)
        payload["proposal_version"] = proposal_entity["version"]
        return engine.upsert("critic_review", review_id, payload, turn=turn)

    return await _belief_tool(ctx, "review_governance_proposal", params, _operation)


@mcp.tool()
async def resolve_governance_council(
    ctx: Context,
    budget_limits: str,
    accepted_conditions: str = "{}",
) -> str:
    """Arbitrate proposals without a weighted national-strategy score.

    Order is fixed: hard constraints, grounded critic review, budget locks,
    strategic priority, Pareto dominance, then opportunity cost. Selected
    proposals become eligible for exact action routing; they do not execute.
    """

    params = {
        "budget_limits": budget_limits,
        "accepted_conditions": accepted_conditions,
    }

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        from civ_mcp.governance import (
            BudgetLock,
            CouncilDecision,
            CouncilDecisionStatus,
            GovernanceCouncil,
        )

        _release_stale_budget_locks(engine, turn=turn)
        limits = _belief_json_object(budget_limits, "budget_limits")
        accepted = _belief_json_object(accepted_conditions, "accepted_conditions")
        typed_snapshot = _typed_snapshot_observation_for_turn(engine, turn=turn)
        if typed_snapshot is None:
            raise BeliefEngineError(
                "A current-turn typed snapshot is required; call get_governance_brief first"
            )
        snapshot_metrics = typed_snapshot.get("metrics") or {}
        authoritative_capacity = {
            "gold": max(0.0, float(snapshot_metrics.get("player.gold", 0))),
            "faith": max(0.0, float(snapshot_metrics.get("player.faith", 0))),
            "research": 1.0,
            "civic": 1.0,
            "city_production": max(
                0.0, float(snapshot_metrics.get("player.cities", 0))
            ),
            "unit_action": max(
                0.0, float(snapshot_metrics.get("player.units", 0))
            ),
        }
        for resource, capacity in authoritative_capacity.items():
            requested = limits.get(resource, capacity)
            if isinstance(requested, bool) or not isinstance(requested, (int, float)):
                raise BeliefEngineError(
                    f"budget limit {resource!r} must be numeric, not bool"
                )
            limits[resource] = min(float(requested), capacity)
            for key in tuple(limits):
                if key.startswith(resource + ":"):
                    scoped = limits[key]
                    if isinstance(scoped, bool) or not isinstance(scoped, (int, float)):
                        raise BeliefEngineError(
                            f"budget limit {key!r} must be numeric, not bool"
                        )
                    limits[key] = min(float(scoped), capacity)
        proposal_entities = engine.list("proposal", status="active")
        proposals = [
            _governance_proposal_from_dict(item) for item in proposal_entities
        ]
        proposal_versions = {
            str(item["id"]): int(item["version"]) for item in proposal_entities
        }
        blocked_by_critic: dict[str, tuple[str, ...]] = {}
        latest_reviews: dict[str, dict[str, Any]] = {}
        for review in engine.list("critic_review", status="active"):
            proposal_id = str(review.get("proposal_id") or "")
            if (
                proposal_id
                and review.get("proposal_version") == proposal_versions.get(proposal_id)
                and proposal_id not in latest_reviews
            ):
                latest_reviews[proposal_id] = review
        eligible = []
        for proposal_typed in proposals:
            review = latest_reviews.get(proposal_typed.proposal_id)
            if not review or review.get("verdict") == "agree":
                eligible.append(proposal_typed)
                continue
            if review.get("verdict") == "object":
                blocked_by_critic[proposal_typed.proposal_id] = (
                    f"grounded critic objection: {review.get('rationale', '')}",
                )
                continue
            required = set(review.get("conditions") or [])
            supplied = set(accepted.get(proposal_typed.proposal_id) or [])
            if required.issubset(supplied):
                eligible.append(proposal_typed)
            else:
                missing = sorted(required - supplied)
                blocked_by_critic[proposal_typed.proposal_id] = (
                    "critic conditions not accepted: " + ", ".join(missing),
                )

        active_locks = tuple(
            BudgetLock(
                resource=str(item.get("resource") or ""),
                amount=item.get("amount", 1),
                scope=str(item.get("scope") or "global"),
                exclusive=item.get("exclusive", False),
                reason=str(item.get("reason") or ""),
            )
            for item in engine.list("budget_lock", status="active")
        )
        proposal_identity = {
            "proposal_ids": sorted(item.proposal_id for item in proposals)
        }
        council_id = (
            f"council:{turn}:"
            f"{action_args_hash(proposal_identity)[:16]}"
        )
        base = GovernanceCouncil().decide(
            turn=turn,
            proposals=eligible,
            budget_limits=limits,
            held_locks=active_locks,
            decision_id=council_id,
        )
        rejected = dict(base.rejected_reasons)
        rejected.update(blocked_by_critic)
        selected = base.selected_proposal_ids
        if selected and rejected:
            status = CouncilDecisionStatus.PARTIAL
        elif selected:
            status = CouncilDecisionStatus.APPROVED
        else:
            status = CouncilDecisionStatus.REJECTED
        decision = CouncilDecision(
            decision_id=base.decision_id,
            turn=turn,
            status=status,
            selected_proposal_ids=selected,
            considered_proposal_ids=tuple(
                proposal_typed.proposal_id for proposal_typed in proposals
            ),
            rejected_reasons=rejected,
            explanation=(
                "critic: objections require evidence; conditions require explicit acceptance",
                *base.explanation,
            ),
            budget_usage=base.budget_usage,
        )
        payload = _governance_payload(decision)
        council_state = payload.pop("status")
        payload.update(
            {
                "status": "resolved",
                "council_state": council_state,
                "statement": f"Governance council resolved {len(proposals)} proposals",
                "selected_proposal_id": selected[0] if selected else "none",
                "typed_snapshot_id": (typed_snapshot.get("facts") or {}).get(
                    "snapshot_id"
                ),
                "effective_budget_limits": limits,
            }
        )
        persisted = engine.upsert(
            "council_decision",
            decision.decision_id,
            payload,
            turn=turn,
        )
        selected_set = set(selected)
        considered_set = {item.proposal_id for item in proposals}
        for proposal_typed in proposals:
            approved = proposal_typed.proposal_id in selected_set
            engine.update(
                "proposal",
                proposal_typed.proposal_id,
                {
                    "status": "resolved",
                    "council_state": "approved" if approved else "rejected",
                    "council_decision_id": decision.decision_id,
                    "rejection_reasons": list(
                        rejected.get(proposal_typed.proposal_id, ())
                    ),
                },
                turn=turn,
            )
            if approved:
                scheduled_turns = [
                    intent.allowed_turn
                    for intent in proposal_typed.action_intents
                    if intent.allowed_turn is not None
                ]
                release_after_turn = max(scheduled_turns, default=turn)
                for index, lock in enumerate(proposal_typed.budget_locks):
                    engine.upsert(
                        "budget_lock",
                        (
                            f"lock:{decision.decision_id}:"
                            f"{proposal_typed.proposal_id}:{index}"
                        ),
                        {
                            **_governance_payload(lock),
                            "proposal_id": proposal_typed.proposal_id,
                            "council_decision_id": decision.decision_id,
                            "release_after_turn": release_after_turn,
                        },
                        turn=turn,
                    )
        for review in engine.list("critic_review", status="active"):
            if str(review.get("proposal_id") or "") in considered_set:
                engine.update(
                    "critic_review",
                    review["id"],
                    {
                        "status": "resolved",
                        "council_decision_id": decision.decision_id,
                    },
                    turn=turn,
                )
        return persisted

    return await _belief_tool(ctx, "resolve_governance_council", params, _operation)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_belief_state(
    ctx: Context,
    entity_type: str = "",
    status: str = "active",
    last_n: int = 50,
) -> str:
    """Read the current world model without loading the entire raw trace.

    With no ``entity_type``, returns decision-relevant entities and current
    normalized metrics.  Use ``get_belief_trace`` for immutable history.
    """

    params = {"entity_type": entity_type, "status": status, "last_n": last_n}

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        selected_status = None if status in {"", "all"} else status
        limit = max(1, min(last_n, 200))
        if entity_type:
            return {
                "game_id": engine.game_id,
                "turn": turn,
                "entity_type": entity_type,
                "items": engine.list(entity_type, status=selected_status)[:limit],
            }
        visible_types = (
            "belief",
            "hypothesis",
            "prediction",
            "plan",
            "surprise",
            "contradiction",
            "attribution",
            "decision",
            "goal",
            "proposal",
            "critic_review",
            "council_decision",
            "budget_lock",
            "outcome",
            "world_entity",
        )
        return {
            "game_id": engine.game_id,
            "turn": turn,
            "current_metrics": engine.current_metrics(),
            "entities": {
                kind: engine.list(kind, status=selected_status)[:limit]
                for kind in visible_types
            },
            "research_metrics": engine.metrics(),
        }

    return await _belief_tool(ctx, "get_belief_state", params, _operation)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_belief_trace(
    ctx: Context,
    entity_type: str = "",
    entity_id: str = "",
    last_n: int = 100,
) -> str:
    """Read immutable create/update/delete history for audit and attribution."""

    params = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "last_n": last_n,
    }
    return await _belief_tool(
        ctx,
        "get_belief_trace",
        params,
        lambda engine, turn: {
            "game_id": engine.game_id,
            "turn": turn,
            "events": engine.history(
                entity_type=entity_type or None,
                entity_id=entity_id or None,
                last_n=last_n,
            ),
        },
    )


@mcp.tool()
async def review_belief_engine(ctx: Context) -> str:
    """Evaluate due predictions, contradictions, and plan invalidation triggers."""

    return await _belief_tool(
        ctx,
        "review_belief_engine",
        {},
        lambda engine, turn: engine.review(turn=turn),
    )


@mcp.tool()
async def route_belief_decision(
    ctx: Context,
    statement: str,
    probability: float,
    confidence: float,
    impact: str,
    urgency: str,
    irreversibility: float,
    belief_ids: str = "[]",
    considered_actions: str = "[]",
    selected_action: str = "",
    reason: str = "",
    action_intent: str = "{}",
    evidence_requirements: str = "[]",
    gate_scope: str = "global",
    council_decision_id: str = "",
) -> str:
    """Route a decision and bind it to an exact action/evidence contract.

    ``action_intent`` is the authorization contract; the legacy
    ``selected_action`` field is audit-only and cannot authorize execution:
    ``{"tool":"set_research","params":{"tech_or_civic":"TECH_WRITING",...}}``.
    For ``verify_then_fast``, ``evidence_requirements`` names the exact fresh
    queries that must follow the decision. The harness compares tool parameters,
    not merely the presence of any query.
    """

    mode = _get_belief_mode(ctx)
    if not mode.enforces_actions:
        return json.dumps(
            {
                "belief_mode": mode.value,
                "enforced": False,
                "authorized": True,
                "message": "Action routing is bypassed outside enforce mode.",
            },
            ensure_ascii=False,
        )

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        governance_gate = engine.governance_turn_gate(turn=turn)
        if governance_gate["typed_snapshot_id"] is None:
            raise BeliefEngineError(
                "A current-turn typed governance snapshot is required; "
                "call get_governance_brief first"
            )
        parsed_intent = _belief_json_object(action_intent, "action_intent")
        if not parsed_intent and str(selected_action or "").strip():
            raise BeliefEngineError(
                "selected_action is audit-only; provide a structured action_intent"
            )
        if parsed_intent:
            tool = str(parsed_intent.get("tool") or "").strip()
            supplied_params = parsed_intent.get("params")
            supplied_arguments = parsed_intent.get("arguments")
            if (
                supplied_params is not None
                and supplied_arguments is not None
                and supplied_params != supplied_arguments
            ):
                raise BeliefEngineError(
                    "action_intent params and arguments must match when both are present"
                )
            intent_params = _canonical_action_params(
                tool,
                supplied_params
                if supplied_params is not None
                else supplied_arguments
                if supplied_arguments is not None
                else {},
            )
            if not tool or not isinstance(intent_params, dict):
                raise BeliefEngineError(
                    "action_intent requires a tool and JSON object arguments/params"
                )
            computed_hash = action_args_hash(intent_params)
            supplied_hash = str(
                parsed_intent.get("args_hash")
                or parsed_intent.get("arguments_hash")
                or ""
            )
            if supplied_hash and supplied_hash != computed_hash:
                raise BeliefEngineError(
                    "action_intent argument hash does not match its arguments"
                )
            parsed_intent["params"] = intent_params
            parsed_intent["args_hash"] = computed_hash
            _validate_ruleset_action_intent(
                tool=tool,
                arguments=intent_params,
                capabilities=_typed_capabilities_for_turn(engine, turn=turn),
            )
        else:
            parsed_intent = None
        parsed_requirements = _belief_json_list(
            evidence_requirements, "evidence_requirements"
        )

        def normalize_requirements(items: list[Any]) -> list[dict[str, Any]]:
            normalized: list[dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict) or not item.get("tool"):
                    raise BeliefEngineError(
                        "evidence_requirements must contain JSON objects with a tool"
                    )
                requirement_params = item.get("params") or {}
                required_facts = item.get("required_facts") or []
                required_metrics = (
                    item.get("required_metrics") or item.get("metric_keys") or []
                )
                minimum_sequence = item.get("min_observation_sequence", 0)
                max_age_turns = item.get("max_age_turns")
                if not isinstance(requirement_params, dict):
                    raise BeliefEngineError(
                        "evidence requirement params must be a JSON object"
                    )
                if not isinstance(required_facts, list) or not all(
                    isinstance(value, str) and value for value in required_facts
                ):
                    raise BeliefEngineError(
                        "evidence required_facts must be a list of non-empty strings"
                    )
                if not isinstance(required_metrics, list) or not all(
                    isinstance(value, str) and value for value in required_metrics
                ):
                    raise BeliefEngineError(
                        "evidence required_metrics must be a list of non-empty strings"
                    )
                if type(minimum_sequence) is not int or minimum_sequence < 0:
                    raise BeliefEngineError(
                        "evidence min_observation_sequence must be a non-negative integer"
                    )
                if max_age_turns is not None and (
                    type(max_age_turns) is not int or max_age_turns < 0
                ):
                    raise BeliefEngineError(
                        "evidence max_age_turns must be a non-negative integer or null"
                    )
                normalized.append(
                    {
                        "requirement_id": str(item.get("requirement_id") or ""),
                        "tool": str(item["tool"]),
                        "target_entity_id": item.get("target_entity_id"),
                        "params": requirement_params,
                        "min_observation_sequence": minimum_sequence,
                        "max_age_turns": max_age_turns,
                        "required_facts": list(required_facts),
                        "required_metrics": list(required_metrics),
                    }
                )
            return normalized

        parsed_requirements = normalize_requirements(parsed_requirements)
        council_required = bool(
            parsed_intent
            and _governance_council_required(
                tool=tool,
                params=intent_params,
                impact=impact,
                irreversibility=irreversibility,
            )
        )
        if council_decision_id:
            council = engine.get("council_decision", council_decision_id)
            if not council:
                raise BeliefEngineError(
                    f"Unknown council decision: {council_decision_id}"
                )
            if not parsed_intent:
                raise BeliefEngineError(
                    "a council-routed decision requires a structured action_intent"
                )
            proposal_id = str(parsed_intent.get("proposal_id") or "")
            selected_ids = set(council.get("selected_proposal_ids") or [])
            if proposal_id not in selected_ids:
                raise BeliefEngineError(
                    "action_intent proposal_id was not selected by the council"
                )
            proposal = engine.get("proposal", proposal_id)
            if not proposal or proposal.get("council_decision_id") != council_decision_id:
                raise BeliefEngineError(
                    f"Council-selected proposal not found: {proposal_id}"
                )
            candidate_hash = action_args_hash(intent_params)
            approved_intent = next(
                (
                    item
                    for item in proposal.get("action_intents") or []
                    if isinstance(item, dict)
                    and item.get("tool") == tool
                    and item.get("arguments_hash") == candidate_hash
                ),
                None,
            )
            if approved_intent is None:
                raise BeliefEngineError(
                    "action_intent does not match an action selected by the council"
                )
            allowed_turn = approved_intent.get("allowed_turn")
            if allowed_turn is not None and allowed_turn != turn:
                raise BeliefEngineError(
                    f"action_intent is approved only for turn {allowed_turn}"
                )
            approved_requirements = normalize_requirements(
                list(approved_intent.get("evidence_requirements") or [])
            )
            if parsed_requirements and parsed_requirements != approved_requirements:
                raise BeliefEngineError(
                    "evidence_requirements do not match the council-approved contract"
                )
            parsed_requirements = approved_requirements
        elif council_required:
            raise BeliefEngineError(
                "This national, scarce-resource, high-impact, or irreversible "
                "action requires a governance proposal and council_decision_id"
            )
        elif governance_gate["active_proposal_ids"]:
            raise BeliefEngineError(
                "Active governance proposals must be resolved before direct routing; "
                "supply the selected council_decision_id"
            )
        decision = engine.route_decision(
            statement=statement,
            probability=probability,
            confidence=confidence,
            impact=impact,
            urgency=urgency,
            irreversibility=irreversibility,
            belief_ids=_belief_json_list(belief_ids, "belief_ids"),
            action_intent=parsed_intent,
            evidence_requirements=parsed_requirements,
            gate_scope=gate_scope,
            council_decision_id=council_decision_id or None,
            turn=turn,
        )
        extras = {
            "considered_actions": _belief_json_list(
                considered_actions, "considered_actions"
            ),
            "selected_action": selected_action,
            "reason": reason,
            "governance_snapshot_id": governance_gate["typed_snapshot_id"],
            # The common action wrapper consumes structured authorization once.
            # Human-readable selected_action remains audit-only.
            "decision_state": "authorized" if parsed_intent else "unbound",
        }
        return engine.update("decision", decision["id"], extras, turn=turn)

    return await _belief_tool(ctx, "route_belief_decision", params, _operation)


@mcp.tool()
async def cancel_routed_action(
    ctx: Context,
    decision_id: str,
    reason: str,
) -> str:
    """Close an unexecuted/retryable routed action with an explicit Outcome.

    Use this after a slow review changes the plan or when a retry is no longer
    rational. It cannot cancel an executing or already successful action.
    """

    params = {"decision_id": decision_id, "reason": reason}
    return await _belief_tool(
        ctx,
        "cancel_routed_action",
        params,
        lambda engine, turn: engine.cancel_action_authorization(
            decision_id,
            reason=reason,
            turn=turn,
        ),
    )


@mcp.tool()
async def assess_route_combat_risk(
    ctx: Context,
    belief_id: str,
    nearby_hostiles: str = "[]",
    assessment: str = "",
) -> str:
    """仅依据量化战斗预估来修订路线信念。

    - 若 nearby_hostiles 非空但未提供 assessment: 不修改信念, 返回 verify_then_fast。
    - 若要修订信念: 先调用 get_combat_estimate(unit_id, target_x, target_y),
      再将其结果作为 assessment 传入, 字段: source("combat_estimate"),
      revised_probability, attacker_cs, defender_cs, attacker_hp, defender_hp,
      expected_damage_to_attacker, expected_damage_to_defender。
    - 下调概率必须被数值支持: defender_cs > attacker_cs 且 攻击方损失比例更大。

    示例:
      get_combat_estimate -> {source: "combat_estimate", attacker_cs: 20, ...}
      assess_route_combat_risk(belief_id=...,
        nearby_hostiles='[{"type":"UNIT_SCOUT","cs":10,"hp":100,"dist":2}]',
        assessment='{"source":"combat_estimate","revised_probability":0.55,...}')
    """

    params = {
        "belief_id": belief_id,
        "nearby_hostiles": nearby_hostiles,
        "assessment": assessment,
    }

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        parsed_assessment = None
        if assessment and assessment.strip() not in {"null", "None"}:
            parsed_assessment = _belief_json_object(assessment, "assessment")
        hostiles = _belief_json_list(nearby_hostiles, "nearby_hostiles")
        if not all(isinstance(item, dict) for item in hostiles):
            raise BeliefEngineError("nearby_hostiles must contain JSON objects")
        return engine.assess_route_combat_risk(
            belief_id,
            nearby_hostiles=hostiles,
            assessment=parsed_assessment,
            turn=turn,
        )

    return await _belief_tool(ctx, "assess_route_combat_risk", params, _operation)


@mcp.tool()
async def record_action_verification(
    ctx: Context,
    decision_id: str,
    tool: str,
    expected: str,
    actual: str,
    success: bool,
    belief_changes: str = "{}",
) -> str:
    """Link a decision to evidence, including recovery after an interrupted call.

    When an authorization is persisted as ``executing`` but the normal MCP
    response is lost, this is the explicit recovery path. The tool must match
    the hash-bound intent; success closes it, while failure makes it retryable.
    """

    params = locals().copy()
    params.pop("ctx")

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        decision = engine.get("decision", decision_id)
        if not decision:
            raise BeliefEngineError(f"Unknown decision: {decision_id}")
        if decision.get("decision_state") == "executing":
            intent = decision.get("action_intent") or {}
            expected_tool = str(intent.get("tool") or "")
            if expected_tool != tool:
                raise BeliefEngineError(
                    f"Verification tool {tool} does not match executing intent "
                    f"{expected_tool}"
                )
            intent_params = intent.get("params") or intent.get("arguments") or {}
            canonical_params = _canonical_action_params(tool, intent_params)
            action = engine.record_tool_result(
                tool=tool,
                params=canonical_params,
                result=actual,
                turn=turn,
                category="action",
                success=success,
                duration_ms=0,
                decision_id=decision_id,
                decision_route=str(decision.get("route") or "recovery"),
            )
            if action is None:
                raise BeliefEngineError("Unable to persist recovered action outcome")
            return engine.update(
                "action",
                action["id"],
                {
                    "expected": expected,
                    "actual_ref": tool_result_reference(actual),
                    "belief_changes": _belief_json_object(
                        belief_changes, "belief_changes"
                    ),
                    "verification": {
                        "verified": True,
                        "source": "agent_recovery",
                    },
                },
                turn=turn,
            )
        return engine.create(
            "action",
            {
                "statement": f"Verification for decision {decision_id}",
                "decision_id": decision_id,
                "tool": tool,
                "expected": expected,
                "actual_ref": tool_result_reference(actual),
                "success": success,
                "belief_changes": _belief_json_object(
                    belief_changes, "belief_changes"
                ),
                "verification": {"verified": True, "source": "agent"},
            },
            turn=turn,
        )

    return await _belief_tool(ctx, "record_action_verification", params, _operation)


@mcp.tool()
async def upsert_failure_attribution(
    ctx: Context,
    attribution_id: str,
    failure: str,
    candidates: str,
) -> str:
    """Create or revise candidate causes for a failure.

    Candidates are JSON objects with ``id``, ``statement``, ``prior``, and
    optional weighted ``evidence_for`` / ``evidence_against`` arrays.
    """

    params = {
        "attribution_id": attribution_id,
        "failure": failure,
        "candidates": candidates,
    }

    def _operation(engine: BeliefEngine, turn: int) -> dict[str, Any]:
        entity = engine.upsert(
            "attribution",
            attribution_id,
            {
                "failure": failure,
                "candidates": _belief_json_list(candidates, "candidates"),
            },
            turn=turn,
        )
        return engine.update_attribution_posteriors(entity["id"], turn=turn)

    return await _belief_tool(ctx, "upsert_failure_attribution", params, _operation)


@mcp.tool()
async def recompute_failure_attribution(ctx: Context, attribution_id: str) -> str:
    """Recompute posterior candidate-cause weights after evidence changes."""

    return await _belief_tool(
        ctx,
        "recompute_failure_attribution",
        {"attribution_id": attribution_id},
        lambda engine, turn: engine.update_attribution_posteriors(
            attribution_id, turn=turn
        ),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_belief_metrics(ctx: Context) -> str:
    """Return belief, prediction, plan, routing, and calibration metrics."""

    return await _belief_tool(
        ctx,
        "get_belief_metrics",
        {},
        lambda engine, turn: {"turn": turn, **engine.metrics()},
    )


# ---------------------------------------------------------------------------
# Diary
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_diary(
    ctx: Context,
    last_n: int = 5,
    turn: Optional[int] = None,
    from_turn: Optional[int] = None,
    to_turn: Optional[int] = None,
) -> str:
    """Read diary entries for game memory.

    Args:
        last_n: Number of most recent entries to return (default 5, max 50).
                Used when turn/from_turn/to_turn are not specified.
        turn: Return the single entry for this turn number.
        from_turn: Return entries from this turn onward (inclusive).
        to_turn: Return entries up to this turn (inclusive).

    Auto-detects the current game from the live connection. Each game has
    its own diary file (keyed by civ + random seed).

    Call this at the start of a session or after context compaction to
    restore strategic memory from previous turns.
    """
    gs = _get_game(ctx)
    try:
        civ_type, seed = await gs.get_game_identity()
    except Exception:
        return "Could not detect current game. Is the game running?"

    run_id = _get_logger(ctx).session_id
    path = _diary_path(civ_type, seed, run_id)
    if not path.exists():
        return f"No diary entries yet for this game ({civ_type}, seed {seed})."

    entries = _read_diary_entries(path)
    if not entries:
        return f"No diary entries yet for this game ({civ_type}, seed {seed})."

    # New format (v2) has N rows per turn — filter to agent rows only.
    # Old format entries (no "v" key) pass through unchanged.
    entries = [e for e in entries if "v" not in e or e.get("is_agent")]

    # Filter by query mode
    if turn is not None:
        entries = [e for e in entries if e.get("turn") == turn]
    elif from_turn is not None or to_turn is not None:
        lo = from_turn if from_turn is not None else 0
        hi = to_turn if to_turn is not None else 999999
        entries = [e for e in entries if lo <= e.get("turn", 0) <= hi]
    else:
        last_n = min(max(last_n, 1), 50)
        entries = entries[-last_n:]

    if not entries:
        return "No diary entries match the query."

    return "\n\n".join(_format_diary_entry(e) for e in entries)


# ---------------------------------------------------------------------------
# Trade routes
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_trade_routes(ctx: Context) -> str:
    """Get trade route capacity, active routes, and trader status.

    Shows how many routes are active vs capacity, and lists all trader
    units with their positions and whether they're idle or on a route.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_trade_routes",
        {},
        lambda: _narrate(gs.get_trade_routes, nr.narrate_trade_routes),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_trade_destinations(ctx: Context, unit_id: int) -> str:
    """List valid trade route destinations for a trader unit.

    Args:
        unit_id: The trader's composite ID (from get_units output)

    Shows domestic and international destinations. Use unit_action
    with action='trade_route' and target_x/target_y to start a route.
    """
    gs = _get_game(ctx)
    unit_index = unit_id % 65536

    async def _run():
        dests = await gs.get_trade_destinations(unit_index)
        return nr.narrate_trade_destinations(dests)

    return await _logged(ctx, "get_trade_destinations", {"unit_id": unit_id}, _run)


# ---------------------------------------------------------------------------
# District advisor
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_district_advisor(ctx: Context, city_id: int, district_type: str) -> str:
    """Show best tiles to place a district with adjacency bonuses.

    Args:
        city_id: City ID (from get_cities)
        district_type: e.g. DISTRICT_CAMPUS, DISTRICT_HOLY_SITE, DISTRICT_INDUSTRIAL_ZONE

    Returns valid placement tiles ranked by adjacency bonus.
    Use set_city_production with target_x/target_y to build the district.
    """
    gs = _get_game(ctx)

    async def _run():
        result = await gs.get_district_advisor(city_id, district_type)
        if isinstance(result, str):
            return f"Error: {result}"  # propagate specific error reason
        narrated = nr.narrate_district_advisor(result, district_type)
        if gs._advisor_budget_warning:
            warn = gs._advisor_budget_warning
            gs._advisor_budget_warning = None
            return f"!! {warn}\n\n{narrated}"
        return narrated

    return await _logged(
        ctx,
        "get_district_advisor",
        {"city_id": city_id, "district_type": district_type},
        _run,
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_wonder_advisor(ctx: Context, city_id: int, wonder_name: str) -> str:
    """Show best tiles to place a wonder with displacement cost analysis.

    Args:
        city_id: City ID (from get_cities output)
        wonder_name: Wonder building type, e.g. BUILDING_CHICHEN_ITZA, BUILDING_ORSZAGHAZ

    Returns valid placement tiles ranked by displacement cost (lowest = best):
    tiles with no improvements or resources are preferred over productive tiles.
    Also shows terrain, feature, river/coastal status, and any resources/improvements
    that would be removed by placing the wonder there.
    Use set_city_production with target_x/target_y to build the wonder.
    """
    gs = _get_game(ctx)

    async def _run():
        placements = await gs.get_wonder_advisor(city_id, wonder_name)
        if isinstance(placements, str):
            return f"Error: {placements}"  # propagate budget/error string
        narrated = nr.narrate_wonder_advisor(placements, wonder_name)
        if gs._advisor_budget_warning:
            warn = gs._advisor_budget_warning
            gs._advisor_budget_warning = None
            return f"!! {warn}\n\n{narrated}"
        return narrated

    return await _logged(
        ctx,
        "get_wonder_advisor",
        {"city_id": city_id, "wonder_name": wonder_name},
        _run,
    )


# ---------------------------------------------------------------------------
# Tile purchase tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_purchasable_tiles(ctx: Context, city_id: int) -> str:
    """List tiles a city can purchase with gold.

    Args:
        city_id: City ID (from get_cities)

    Shows cost, terrain, and resources for each purchasable tile.
    Tiles with luxury/strategic resources are listed first.
    """
    gs = _get_game(ctx)

    async def _run():
        tiles = await gs.get_purchasable_tiles(city_id)
        return nr.narrate_purchasable_tiles(tiles)

    return await _logged(ctx, "get_purchasable_tiles", {"city_id": city_id}, _run)


@mcp.tool()
async def purchase_tile(ctx: Context, city_id: int, x: int, y: int) -> str:
    """Buy a tile for a city with gold.

    Args:
        city_id: City ID
        x: Tile X coordinate
        y: Tile Y coordinate

    Use get_purchasable_tiles first to see costs and options.
    """
    gs = _get_game(ctx)
    result = await _logged(
        ctx,
        "purchase_tile",
        {"city_id": city_id, "x": x, "y": y},
        lambda: gs.purchase_tile(city_id, x, y),
    )
    _get_camera(ctx).push(x, y, f"purchase tile ({x},{y})")
    return result


# ---------------------------------------------------------------------------
# Government change
# ---------------------------------------------------------------------------


@mcp.tool()
async def change_government(ctx: Context, government_type: str) -> str:
    """Switch to a different government type.

    Args:
        government_type: e.g. GOVERNMENT_CLASSICAL_REPUBLIC, GOVERNMENT_OLIGARCHY

    Use get_policies to see current government. First switch after
    unlocking a new tier is free (no anarchy).
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "change_government",
        {"government_type": government_type},
        lambda: gs.change_government(government_type),
    )


# ---------------------------------------------------------------------------
# Great People
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_great_people(ctx: Context) -> str:
    """See available Great People and recruitment progress.

    Shows which Great People are available, their recruitment cost,
    and which civilization (if any) is recruiting them.
    """
    gs = _get_game(ctx)

    async def _run():
        gp = await gs.get_great_people()
        return nr.narrate_great_people(gp)

    return await _logged(ctx, "get_great_people", {}, _run)


@mcp.tool()
async def get_gp_advisor(ctx: Context, unit_index: int) -> str:
    """Show best cities to activate a Great Person, ranked by suitability.

    Args:
        unit_index: The Great Person unit's index (from get_units output).

    Lists all cities with the matching district (e.g., campuses for Great Scientists),
    showing which ones the GP can activate on, distance, city yield, and great work
    slot availability for cultural GPs.
    """
    gs = _get_game(ctx)

    async def _run():
        result = await gs.get_gp_advisor(unit_index)
        if result is None:
            return "Could not get GP advisor info. Is this a Great Person unit?"
        return nr.narrate_gp_advisor(result)

    return await _logged(ctx, "get_gp_advisor", {"unit": unit_index}, _run)


@mcp.tool()
async def recruit_great_person(ctx: Context, individual_id: int) -> str:
    """Recruit a Great Person using accumulated GP points.

    Args:
        individual_id: The individual's ID (from get_great_people output, shown after ability)

    Requires enough Great Person points for that class.
    The GP spawns in your capital. Use get_great_people to check [CAN RECRUIT] status.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "recruit_great_person",
        {"individual_id": individual_id},
        lambda: gs.recruit_great_person(individual_id),
    )


@mcp.tool()
async def patronize_great_person(
    ctx: Context, individual_id: int, yield_type: str = "YIELD_GOLD"
) -> str:
    """Buy a Great Person instantly with gold or faith.

    Args:
        individual_id: The individual's ID (from get_great_people output)
        yield_type: YIELD_GOLD (default) or YIELD_FAITH

    Costs shown in get_great_people output under "Patronize:".
    Requires enough gold/faith to cover the cost.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "patronize_great_person",
        {"individual_id": individual_id, "yield_type": yield_type},
        lambda: gs.patronize_great_person(individual_id, yield_type),
    )


@mcp.tool()
async def reject_great_person(ctx: Context, individual_id: int) -> str:
    """Pass on a Great Person (skip to the next one in that class).

    Args:
        individual_id: The individual's ID (from get_great_people output)

    Costs faith. The next Great Person in that class becomes available.
    Use when you don't want the current GP and want to save points for a better one.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "reject_great_person",
        {"individual_id": individual_id},
        lambda: gs.reject_great_person(individual_id),
    )


# ---------------------------------------------------------------------------
# World Congress
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_world_congress(ctx: Context) -> str:
    """Get World Congress status, active resolutions, and voting options.

    Shows whether congress is in session, resolutions to vote on (with options A/B
    and possible targets), turns until next session, and your diplomatic favor.
    When in session, use queue_wc_votes to register votes before end_turn.
    """
    gs = _get_game(ctx)

    async def _run():
        status = await gs.get_world_congress()
        return nr.narrate_world_congress(status)

    return await _logged(ctx, "get_world_congress", {}, _run)


@mcp.tool()
async def queue_wc_votes(ctx: Context, votes: str) -> str:
    """Pre-configure World Congress votes for the upcoming session.

    Args:
        votes: JSON array of vote objects, e.g.
            '[{"hash": -513644209, "option": 1, "target": 2, "votes": 5}]'
            hash = resolution type hash (from get_world_congress)
            option = 1 for A, 2 for B
            target = player ID for PlayerType resolutions (from get_world_congress
                     target list, e.g. [target=2] Portugal), or target value for
                     non-player resolutions. The handler resolves to the correct
                     0-based index at runtime.
            votes = max votes to allocate (will use as many as favor allows)

    Call this BEFORE end_turn when get_world_congress shows 0 turns until next
    session. Registers an event handler that fires during WC processing and
    casts your votes with the specified preferences.

    If you don't call this, end_turn will pause at the World Congress session
    and return control to you for interactive voting.
    """
    gs = _get_game(ctx)
    vote_list = json.loads(votes)

    async def _run():
        return await gs.queue_wc_votes(vote_list)

    return await _logged(ctx, "queue_wc_votes", {"votes": votes}, _run)


# ---------------------------------------------------------------------------
# Victory progress
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_victory_progress(ctx: Context) -> str:
    """Get victory condition progress for all civilizations.

    Shows progress toward Science, Domination, Culture, Religious,
    Diplomatic, and Score victories. Includes space race VP, diplomatic VP,
    tourism vs domestic tourists, religion spread, capital ownership,
    and military strength. Call every 20-30 turns to track the race.
    """
    gs = _get_game(ctx)

    async def _run():
        vp = await gs.get_victory_progress()
        return nr.narrate_victory_progress(vp)

    return await _logged(ctx, "get_victory_progress", {}, _run)


# ---------------------------------------------------------------------------
# Religion status
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_religion_spread(ctx: Context) -> str:
    """Get per-city religion breakdown across all visible cities.

    Shows which religion is majority in each city, follower counts,
    and which religions are closest to religious victory.
    """
    gs = _get_game(ctx)

    async def _run():
        rs = await gs.get_religion_status()
        return nr.narrate_religion_status(rs)

    return await _logged(ctx, "get_religion_spread", {}, _run)


# ---------------------------------------------------------------------------
# City yield focus
# ---------------------------------------------------------------------------


@mcp.tool()
async def set_city_focus(ctx: Context, city_id: int, focus: str) -> str:
    """Set a city's citizen yield priority.

    Args:
        city_id: City ID
        focus: One of: food, production, gold, science, culture, faith, default
               'default' clears all focus settings.

    Cities automatically assign citizens to tiles. This biases the AI
    toward the chosen yield type when assigning new citizens.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "set_city_focus",
        {"city_id": city_id, "focus": focus},
        lambda: gs.set_city_focus(city_id, focus),
    )


# ---------------------------------------------------------------------------
# Utility tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def dismiss_popup(ctx: Context) -> str:
    """Dismiss any blocking popup in the game UI.

    Call this if you suspect a popup (e.g. historic moment, boost notification)
    is blocking interaction.
    """
    gs = _get_game(ctx)
    return await _logged(ctx, "dismiss_popup", {}, gs.dismiss_popup)


@mcp.tool(annotations={"destructiveHint": True})
async def run_lua(ctx: Context, code: str, context: str = "gamecore") -> str:
    """Run arbitrary Lua code in the game. Advanced escape hatch — prefer built-in tools.

    Args:
        code: Lua code to execute. Use print() for output, end with print("---END---").
        context: "gamecore" (default) for read-only state queries.
                 "ingame" for commands and UI-dependent queries.

    Context differences:
      gamecore: Players[], GameInfo.*, Map.*, Game.* — safe read-only access.
                CANNOT use: UI.*, UnitManager.*, CityManager.*, notifications.
      ingame:   All APIs including UI.*, UnitManager.*, CityManager.*.
                Use for: moving units, setting research, diplomacy actions.

    Always use print() for output (not return).
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "run_lua",
        {"code": code, "context": context},
        lambda: gs.execute_lua(code, context),
    )


# ---------------------------------------------------------------------------
# Save / Load
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def list_saves(ctx: Context) -> str:
    """List available save files (normal, autosave).

    Returns indexed list of saves. Use load_save(save_index=N) to load one.
    Call this before load_save to see what's available.
    """
    gs = _get_game(ctx)
    return await _logged(ctx, "list_saves", {}, gs.list_saves)


@mcp.tool(annotations={"destructiveHint": True})
async def load_save(ctx: Context, save_index: int) -> str:
    """Load a save file by index from the most recent list_saves() result.

    Args:
        save_index: Index number from list_saves output (1-based)

    The game will reload entirely. Wait ~10 seconds after calling this,
    then use get_game_overview to verify the loaded state.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx, "load_save", {"save_index": save_index}, lambda: gs.load_save(save_index)
    )


@mcp.tool(annotations={"destructiveHint": True})
async def load_game_save(ctx: Context, save_name: str) -> str:
    """Load a save file by name. No need to call list_saves first.

    Args:
        save_name: Save name without extension (e.g. "0_MCP_0079",
                   "0A_GROUND_CONTROL", "AutoSave_0221", "quicksave").

    Tries Lua-based loading first (fast, ~5s). If the save isn't found
    via Lua (common for autosaves/quicksaves), falls back to OCR menu
    navigation (~90s) after verifying the file exists on disk.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "load_game_save",
        {"save_name": save_name},
        lambda: gs.load_game_save(save_name),
    )


# ---------------------------------------------------------------------------
# Game Lifecycle (kill / launch / load from menu)
# ---------------------------------------------------------------------------
# These tools do NOT require a FireTuner connection — they manage the game
# process itself. Hardcoded to Civ 6 only (no arbitrary system commands).


@mcp.tool(annotations={"destructiveHint": True})
async def kill_game(ctx: Context) -> str:
    """Kill the Civ 6 game process and wait for Steam to deregister.

    Only kills Civ 6 processes. Waits ~10 seconds for Steam to deregister
    so the game can be relaunched cleanly.
    """
    return await game_launcher.kill_game()


@mcp.tool(annotations={"destructiveHint": True})
async def launch_game(ctx: Context) -> str:
    """Launch Civ 6 via Steam.

    Starts the game and waits for the process to appear (~15-30 seconds).
    The game will be at the main menu after launch — use load_save or
    restart_and_load to load a specific save.

    NOTE: FireTuner connection is NOT available at the main menu.
    Only in-game MCP tools work after a save is loaded.
    """
    return await game_launcher.launch_game()


@mcp.tool(annotations={"destructiveHint": True})
async def load_save_from_menu(ctx: Context, save_name: str | None = None) -> str:
    """Navigate the main menu to load a save via OCR-guided clicking.

    Args:
        save_name: Autosave name (e.g. "AutoSave_0221"). If not provided,
                   loads the most recent autosave.

    Requires the game to be running and at the main menu. Uses macOS Vision
    OCR to find and click menu elements. Takes 30-90 seconds.

    After loading, wait ~10 seconds then call get_game_overview to verify.

    Requires pyobjc: uv pip install 'civ6-mcp[launcher]'
    """
    return await game_launcher.load_save_from_menu(save_name)


@mcp.tool(annotations={"destructiveHint": True})
async def restart_and_load(ctx: Context, save_name: str | None = None) -> str:
    """Full game recovery: kill, relaunch, and load a save.

    Args:
        save_name: Autosave name (e.g. "AutoSave_0221"). If not provided,
                   loads the most recent autosave.

    This is the recommended tool for recovering from game hangs (e.g. AI turn
    processing stuck in infinite loop). Takes 60-120 seconds total:
    1. Kills the game process
    2. Waits for Steam to deregister (~10s)
    3. Relaunches via Steam (~15-30s for process start + main menu)
    4. Navigates menus via OCR to load the save (~30-60s)

    After completion, wait ~10 seconds then call get_game_overview to verify.
    """
    gs = _get_game(ctx)
    identity_before = gs._game_identity

    result = await game_launcher.restart_and_load(save_name)

    # Reconnect and verify correct game loaded
    conn = gs.conn
    for attempt in range(30):
        try:
            await conn.reconnect()
            if conn.gamecore_index is not None:
                break
        except ConnectionError:
            pass
        await asyncio.sleep(1)

    if conn.gamecore_index is not None and identity_before is not None:
        try:
            actual = await gs.get_game_identity()
            if actual != identity_before:
                log.warning(
                    "restart_and_load: wrong game loaded "
                    "(expected %s, got %s) — retrying",
                    identity_before,
                    actual,
                )
                result2 = await game_launcher.restart_and_load(save_name)
                for attempt in range(30):
                    try:
                        await conn.reconnect()
                        if conn.gamecore_index is not None:
                            break
                    except ConnectionError:
                        pass
                    await asyncio.sleep(1)
                try:
                    actual2 = await gs.get_game_identity()
                    if actual2 != identity_before:
                        return (
                            f"{result2} | WARNING: Wrong game loaded "
                            f"(expected {identity_before[0]}, "
                            f"got {actual2[0]}). Manual recovery needed."
                        )
                except Exception:
                    pass
                return f"{result2} | Reloaded after wrong-game detection."
        except Exception:
            log.debug("Post-load identity check failed", exc_info=True)

    return result


async def _narrate(
    query_fn: Callable[[], Awaitable[Any]], narrate_fn: Callable[..., str]
) -> str:
    """Helper: call a query function then narrate the result."""
    data = await query_fn()
    return narrate_fn(data)


def main():
    """Entry point for the MCP server."""
    import signal

    logging.basicConfig(level=logging.INFO)

    # Remap SIGTERM → SIGINT so asyncio's existing SIGINT handler triggers a
    # graceful shutdown (cancels all tasks → lifespan finally block runs →
    # conn.disconnect() closes the FireTuner TCP connection cleanly).
    # Without this, SIGTERM kills the process immediately, leaving the game
    # with an abrupt TCP RST which can cause it to crash.
    # SIGTERM is not available on Windows, so skip the remap there.
    if hasattr(signal, "SIGTERM"):
        signal.signal(
            signal.SIGTERM, lambda sig, frame: os.kill(os.getpid(), signal.SIGINT)
        )

    if os.environ.get("CIV_MCP_DISABLE_LUA"):
        mcp._tool_manager.remove_tool("run_lua")

    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        # SIGTERM is intentionally remapped to SIGINT above so FastMCP runs
        # lifespan cleanup. Treat the resulting interrupt as a normal stop.
        pass
