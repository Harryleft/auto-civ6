"""End-turn orchestration: diary merge, recovery, and game-over handling.

MCP registration is kept in ``tools.end_turn``; this module owns the
multi-step runtime flow and remains independently testable.
"""

import asyncio
import logging
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone

from mcp.server.fastmcp import Context

from civ_mcp import game_launcher, heartbeat, narrate as nr
from civ_mcp.connection import LuaError
from civ_mcp.diary import (
    diary_path as _diary_path,
    merge_agent_reflections as _merge_agent_reflections,
)
from civ_mcp.end_turn import end_turn_budget
from civ_mcp.server import pipeline
from civ_mcp.telemetry import EVENT_CITY_ROW, EVENT_DIARY_ROW

log = logging.getLogger(__name__)


def _budget_seconds() -> float:
    """Return the enforced wall-clock ceiling for one end_turn call.

    Read through this function (not a captured constant) so a test can shrink
    the ceiling and exercise the expiry path without waiting for real minutes.
    """

    return end_turn_budget(wc_turn=True).total_seconds


# Returned when the call runs out of its own time budget. The turn may still
# advance inside the game after the cancellation, so the only safe next step is
# a read-only confirmation: never another end_turn.
_BUDGET_EXHAUSTED_RECEIPT = (
    "结果未知 — end_turn 已超过本次调用的时间预算（{budget:.0f} 秒）。"
    "结束回合请求很可能已经送达游戏，因此不要再次调用 end_turn，也不要重复任何"
    "可能已发出的购买/移动/生产改动。\n"
    "下一步只读核验：调用 get_game_overview 读取当前回合号，与本回合开始前记录的"
    "回合号比较。回合号已推进说明本回合已经完成；回合号未变才需要检查阻塞项"
    "（get_pending_diplomacy / get_notifications）并另行决定。\n"
    "UNKNOWN:END_TURN_BUDGET_EXHAUSTED"
)


async def run_end_turn(
    ctx: Context,
    tactical: str = "",
    strategic: str = "",
    tooling: str = "",
    planning: str = "",
    hypothesis: str = "",
) -> str:
    """End the current turn inside an enforced time budget.

    One end_turn blocks inside the DSH tool-call deadline while the AI civs
    play. The budget is derived in ``civ_mcp.end_turn`` and asserted against the
    host deadline by ``tests/test_end_turn_budget.py``; enforcing it here as
    well means an unexpectedly slow game returns a machine-readable "outcome
    unknown" receipt instead of being killed by the host, which the agent could
    not tell apart from "the turn never advanced".
    """

    budget_seconds = _budget_seconds()
    try:
        async with asyncio.timeout(budget_seconds):
            return await _run_end_turn_impl(
                ctx,
                tactical=tactical,
                strategic=strategic,
                tooling=tooling,
                planning=planning,
                hypothesis=hypothesis,
            )
    except TimeoutError:
        log.error(
            "end_turn exceeded its %.0fs budget; reporting an unknown outcome",
            budget_seconds,
        )
        return pipeline._filter_downstream_result(
            "end_turn",
            {},
            _BUDGET_EXHAUSTED_RECEIPT.format(budget=budget_seconds),
        )


async def _run_end_turn_impl(
    ctx: Context,
    *,
    tactical: str = "",
    strategic: str = "",
    tooling: str = "",
    planning: str = "",
    hypothesis: str = "",
) -> str:
    """Run the end-turn flow."""
    gs = pipeline._get_game(ctx)

    def _render_result(raw_result: str) -> str:
        """Render only after end-turn control flow has consumed raw markers."""

        return pipeline._filter_downstream_result("end_turn", {}, raw_result)

    reflections = {
        "tactical": tactical,
        "strategic": strategic,
        "tooling": tooling,
        "planning": planning,
        "hypothesis": hypothesis,
    }
    missing = [k for k, v in reflections.items() if not v.strip()]
    # The legacy profile keeps the five-field diary requirement. The lean
    # profile drops it: five mandatory essays per turn were part of the
    # governance ceremony this refactor removes. Empty fields are recorded as
    # empty rather than filled with invented "no issues" / "done" text, so the
    # diary never claims the model observed something it did not.
    if missing and pipeline._get_play_profile(ctx).requires_reflections:
        return _render_result(
            f"Empty reflections: {', '.join(missing)}. "
            "Provide non-empty entries for all 5 fields: "
            "tactical, strategic, tooling, planning, hypothesis."
        )

    # Model ID comes from CIV_MCP_AGENT_MODEL env var (set by eval runner)
    env_model = os.environ.get("CIV_MCP_AGENT_MODEL", "")
    if env_model:
        pipeline._get_logger(ctx).set_agent_model(env_model)

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
    _diary_run_id = pipeline._get_logger(ctx).session_id
    _diary_snapshot = None
    _is_retry = getattr(gs, "_end_turn_blocked", False)
    try:
        ov = await gs.get_game_overview()
        _diary_player_id = ov.player_id
        _diary_turn = ov.turn
        # Keep logger/spatial turn in sync (agent may not call get_game_overview every turn)
        pipeline._get_logger(ctx).set_turn(ov.turn)
        pipeline._get_spatial(ctx).set_turn(ov.turn)
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
                await pipeline._get_logger(ctx)._emitter.emit(EVENT_DIARY_ROW, merged_row)
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
                _emitter = pipeline._get_logger(ctx)._emitter
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
    # Keep the machine result raw while this wrapper handles HANG, turn
    # advancement, blockers, watchdogs, and game-over logging.  Localize once
    # at the final return so those branches do not lose their prefixes.
    result = await pipeline._logged(
        ctx, "end_turn", {}, gs.end_turn, localize=False
    )

    # ---------------------------------------------------------------
    # A wedged AI turn is *reported*, not repaired here.
    #
    # Recovery (kill, relaunch, reload an autosave) used to run inside this call
    # up to three times. That made one call's ceiling cover the wait plus three
    # relaunches — which is why the deadline had to be ~50 minutes for an
    # operation whose normal case is seconds — and it also hid from the caller
    # whether the turn had merely been slow or had been wedged and restarted.
    # Recovery is now a deliberate separate step, so the deadline only has to
    # cover a turn.
    # ---------------------------------------------------------------
    if result.startswith("HANG:") and not gs._hang_retry_active:
        hang_info = result.split("|", 1)[0]
        _, hang_turn, hang_save = hang_info.split(":")
        log.error(
            "AI turn hang reported at T%s (save %s); recovery is a separate step",
            hang_turn,
            hang_save,
        )
        return _render_result(
            f"HANG:{hang_turn}:{hang_save}|"
            "结果未知 — 本回合疑似卡在 AI 处理阶段，本次调用没有重启游戏。\n"
            f"待核验存档: {hang_save}（读取前先用 get_game_overview 确认当前回合号）。\n"
            "下一步：停止本回合循环并原样报告。重启游戏是宿主机/操作者的动作，"
            "按 kill_game → launch_game → load_game_save 分开执行（每次一个独立调用），"
            "不要在本回合循环里尝试重启，也不要等待重启结果。\n"
            "禁止在未核对结果前重复发送 end_turn 或重复已发出的改动。\n"
            "HANG_RECOVERY_IS_A_SEPARATE_STEP"
        )


    # Clear stale camera events on successful turn advance
    turn_advanced = (
        "->" in result and "Cannot end turn" not in result and "Error" not in result
    )
    if turn_advanced:
        pipeline._get_camera(ctx).clear()
        gs._end_turn_blocked = False
        # Update logger/spatial turn from result ("Turn X -> Y")
        m = re.search(r"Turn \d+ -> (\d+)", result)
        if m:
            new_turn = int(m.group(1))
            pipeline._get_logger(ctx).set_turn(new_turn)
            pipeline._get_spatial(ctx).set_turn(new_turn)
            heartbeat.write("playing", turn=new_turn)
        # Map capture — record terrain (first turn) + ownership delta
        if _diary_civ_type and _diary_seed:
            try:
                mc = pipeline._get_map_capture(ctx)
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
                await pipeline._get_logger(ctx).log_game_over(
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
        pipeline._get_watchdog(ctx).arm()

    # A confirmed advance means the game is back on our turn, so the returned
    # brief *is* the next turn's entry material: the model should decide from it
    # instead of mechanically re-querying. Blocked turns, unknown outcomes and
    # finished games must not fabricate a next turn, so they never get a brief.
    if turn_advanced and "GAME OVER" not in result and pipeline._turn_context_enabled(ctx):
        confirmed = re.search(r"Turn \d+ -> (\d+)", result)
        expected_turn = int(confirmed.group(1)) if confirmed else None
        brief_context = await pipeline.build_and_record_turn_context(ctx)
        if brief_context is None:
            # The action succeeded and only the briefing failed. Reporting this
            # as a plain failure would invite a resend of an end_turn that
            # already took effect, so say exactly which half succeeded.
            result += (
                "\n\n=== 推进已确认，简报待重取 ===\n"
                f"回合推进已经确认（T{_diary_turn or '?'} → T{expected_turn}），"
                "但下一回合局面简报采集失败。\n"
                "只允许重新读取局面：调用 get_game_overview。"
                "不要再次调用 end_turn，也不要重复本回合已经发出的任何改动。\n"
                "BRIEF_PENDING:RE_READ_OVERVIEW_ONLY"
            )
        else:
            result += "\n\n" + brief_context.brief
            if expected_turn is not None and brief_context.turn != expected_turn:
                result += (
                    f"\n\n注意: end_turn 确认推进到 T{expected_turn}，"
                    f"但简报读到 T{brief_context.turn}。以只读复核为准，不要重发 end_turn。"
                )

    return _render_result(result)
