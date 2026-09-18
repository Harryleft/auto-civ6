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
from civ_mcp.end_turn import (
    HANG_EXTRA_WAITS,
    MAX_HANG_RETRIES,
    _now,
    end_turn_budget,
    hang_attempt_ceiling_seconds,
)
from civ_mcp.server import pipeline
from civ_mcp.telemetry import EVENT_CITY_ROW, EVENT_DIARY_ROW

log = logging.getLogger(__name__)

# Sourced from civ_mcp.end_turn so the retry count, the escalating waits, and
# the declared end_turn budget can never drift apart.
_MAX_HANG_RETRIES = MAX_HANG_RETRIES
_HANG_EXTRA_WAIT = list(HANG_EXTRA_WAITS)


def _budget_seconds() -> float:
    """Return the enforced wall-clock ceiling for one end_turn call.

    Read through this function (not a captured constant) so a test can shrink
    the ceiling and exercise the expiry path without waiting for real minutes.
    """

    return end_turn_budget(wc_turn=True).total_seconds


def _clock() -> float:
    """Return the monotonic clock used for deadline arithmetic."""

    return _now()


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

# Returned when the remaining budget cannot pay for another hang-recovery
# attempt. Distinct from the message above so a reader can tell "we ran out of
# time waiting" from "we chose not to start a restart we could not finish".
_HANG_RECOVERY_UNAFFORDABLE_RECEIPT = (
    "结果未知 — 检测到 AI 回合疑似挂起，但本次调用剩余预算（{remaining:.0f} 秒）"
    "不足以完成一次自动恢复（每次约需 {needed:.0f} 秒），因此没有重启游戏。"
    "游戏可能仍在处理，也可能已经结束回合。\n"
    "下一步只读核验：调用 get_game_overview 确认当前回合号；不要再次调用 end_turn，"
    "也不要重复可能已发出的改动。如确认回合确实未推进，再在独立调用中处理挂起。\n"
    "UNKNOWN:HANG_RECOVERY_UNAFFORDABLE"
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
    deadline = _clock() + budget_seconds
    try:
        async with asyncio.timeout(budget_seconds):
            return await _run_end_turn_impl(
                ctx,
                deadline=deadline,
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
    deadline: float,
    tactical: str = "",
    strategic: str = "",
    tooling: str = "",
    planning: str = "",
    hypothesis: str = "",
) -> str:
    """Run the end-turn flow. ``deadline`` bounds polling and recovery."""
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
    if missing:
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
    # Auto-recover from AI turn hangs (transparent to agent).
    # end_turn returns "HANG:{turn}:{save}|..." when AI processing is
    # stuck with no blockers found.
    # Recovery: restart_and_load the MCP autosave, reconnect, retry
    # up to _MAX_HANG_RETRIES times with escalating waits.
    # A restart is never *started* unless the remaining budget can pay for
    # it: beginning a kill/relaunch we cannot finish is worse than reporting
    # the hang, because the host would then have to kill the call mid-reload.
    # ---------------------------------------------------------------
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
        elif deadline - _clock() < hang_attempt_ceiling_seconds():
            remaining = deadline - _clock()
            log.error(
                "HANG RECOVERY: not started at T%s — %.0fs left of the end_turn "
                "budget cannot pay for an attempt (needs %.0fs)",
                hang_turn,
                remaining,
                hang_attempt_ceiling_seconds(),
            )
            return _render_result(
                _HANG_RECOVERY_UNAFFORDABLE_RECEIPT.format(
                    remaining=max(remaining, 0.0),
                    needed=hang_attempt_ceiling_seconds(),
                )
            )
        else:
            identity_before = gs._game_identity
            gs._hang_retry_active = True
            try:
                for attempt in range(1, _MAX_HANG_RETRIES + 1):
                    remaining = deadline - _clock()
                    if remaining < hang_attempt_ceiling_seconds():
                        log.error(
                            "HANG RECOVERY: stopping before attempt %d/%d — "
                            "%.0fs left cannot pay for an attempt (needs %.0fs)",
                            attempt,
                            _MAX_HANG_RETRIES,
                            remaining,
                            hang_attempt_ceiling_seconds(),
                        )
                        return _render_result(
                            _HANG_RECOVERY_UNAFFORDABLE_RECEIPT.format(
                                remaining=max(remaining, 0.0),
                                needed=hang_attempt_ceiling_seconds(),
                            )
                        )
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

                    # Step 1: Kill + relaunch + FrontEnd API load
                    restart_result = await game_launcher.restart_and_load(
                        hang_save, conn=gs.conn
                    )
                    log.info("HANG RECOVERY: restart_and_load: %s", restart_result)
                    # The save loads an earlier world; the journal still holds
                    # the abandoned branch's authorizations until marked.
                    await pipeline._record_game_reload_epoch(
                        ctx,
                        reason="hang_recovery_restart_and_load",
                        turn=hang_turn_int,
                        details={"save": hang_save, "attempt": attempt},
                    )

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

                    # Step 5: Retry end_turn. Bound the retry's own poll so a
                    # second hung attempt cannot consume the whole budget.
                    log.info(
                        "HANG RECOVERY: retrying end_turn for T%s...",
                        hang_turn,
                    )
                    result = await gs.end_turn(
                        poll_deadline=min(
                            deadline, _clock() + hang_attempt_ceiling_seconds()
                        )
                    )
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
                    return _render_result(
                        f"AI turn hung at T{hang_turn} after "
                        f"{_MAX_HANG_RETRIES} automatic restart attempts "
                        f"with escalating waits. The hang may be "
                        f"probabilistic — another attempt could work. "
                        f"Try restart_and_load('{hang_save.replace(hang_turn, str(earlier))}') "
                        f"to skip back a few turns."
                    )
            except Exception:
                log.error("HANG RECOVERY: failed", exc_info=True)
                return _render_result(
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

    return _render_result(result)
