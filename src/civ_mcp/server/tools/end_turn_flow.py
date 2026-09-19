"""End-turn orchestration: diary merge, recovery, and game-over handling.

MCP registration is kept in ``tools.end_turn``; this module owns the
multi-step runtime flow and remains independently testable.
"""

import asyncio
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from mcp.server.fastmcp import Context

from civ_mcp import game_launcher, heartbeat, narrate as nr
from civ_mcp.connection import LuaError
from civ_mcp.diary import (
    diary_path as _diary_path,
    merge_agent_reflections as _merge_agent_reflections,
)
from civ_mcp.end_turn import end_turn_budget, end_turn_confirmation
from civ_mcp.server import pipeline
from civ_mcp.telemetry import EVENT_CITY_ROW, EVENT_DIARY_ROW

log = logging.getLogger(__name__)


def _budget_seconds() -> float:
    """Return the enforced wall-clock ceiling for one end_turn call.

    Read through this function (not a captured constant) so a test can shrink
    the ceiling and exercise the expiry path without waiting for real minutes.
    """

    return end_turn_budget(wc_turn=True).total_seconds


# An expired waiter cannot cancel a request inside Civ VI. Re-enter the
# pending-aware end_turn path; ordinary InGame overview queries may be blocked
# until the game returns control, and must not be prescribed as the only exit.
_BUDGET_EXHAUSTED_RECEIPT = (
    "结果未知 — end_turn 已超过本次调用的时间预算（{budget:.0f} 秒）。"
    "游戏可能仍在处理本次结束回合请求，不要重复任何可能已发出的购买/移动/生产改动。\n"
    "下一步可调用 end_turn 做只读续等：已在途请求不会重发 ACTION_ENDTURN；"
    "只有明确尚未提交的请求才允许首次提交。"
    "回合推进得到确认后，再调用 get_game_overview 补取局面。"
    "等待到期不代表游戏已经停止，也不授权自动重启。\n"
    "UNKNOWN:END_TURN_BUDGET_EXHAUSTED"
)


def _confirmed_turns(result: str) -> tuple[int, int] | None:
    match = re.search(r"(?:^|\n)Turn (\d+) -> (\d+)", result)
    if match and int(match.group(2)) > int(match.group(1)):
        return int(match.group(1)), int(match.group(2))
    return None


@dataclass
class _EndTurnReceipt:
    # Per-call state: overlapping callers must never inherit one another's
    # receipt. Save it before telemetry/briefing can suspend or be cancelled.
    confirmed_result: str | None = None

    def record(self, result: str) -> None:
        if _confirmed_turns(result) is not None:
            self.confirmed_result = result


def _brief_pending_result(result: str) -> str:
    turns = _confirmed_turns(result)
    assert turns is not None
    return result + (
        "\n\n=== 推进已确认，简报待重取 ===\n"
        f"回合推进已经确认（T{turns[0]} → T{turns[1]}），"
        "但后处理或下一回合局面简报尚未完成。\n"
        "只允许重新读取局面：调用 get_game_overview。"
        "不要再次调用 end_turn，也不要重复本回合已经发出的任何改动。\n"
        "CONFIRMED:END_TURN_ADVANCED\n"
        "BRIEF_PENDING:RE_READ_OVERVIEW_ONLY"
    )


@dataclass
class _SharedEndTurnFlow:
    task: asyncio.Task[str]
    waiters: int = 0


async def run_end_turn(
    ctx: Context,
    tactical: str = "",
    strategic: str = "",
    tooling: str = "",
    planning: str = "",
    hypothesis: str = "",
) -> str:
    """Coalesce the whole call, including diary preparation and post-processing.

    Coalescing only the core action is too late: a second caller can still be
    gathering its diary when the first advances, then accidentally end the new
    turn. All overlapping callers therefore share one bounded operation. A
    cancelled waiter cannot cancel other waiters' request; when the final
    waiter leaves, cancellation still reaches the core and releases I/O.
    """
    gs = pipeline._get_game(ctx)
    operation = getattr(gs, "_end_turn_flow_operation", None)
    if operation is None or operation.task.done():
        operation = _SharedEndTurnFlow(
            asyncio.create_task(
                _run_end_turn_bounded(
                    ctx,
                    tactical=tactical,
                    strategic=strategic,
                    tooling=tooling,
                    planning=planning,
                    hypothesis=hypothesis,
                )
            )
        )
        gs._end_turn_flow_operation = operation
    operation.waiters += 1
    try:
        # This task owns its own deadline; shielding it never creates an
        # unbounded background operation.
        return await asyncio.shield(operation.task)
    finally:
        operation.waiters -= 1
        if operation.waiters == 0 and not operation.task.done():
            operation.task.cancel()
            try:
                await operation.task
            except (asyncio.CancelledError, Exception):
                pass
        if getattr(gs, "_end_turn_flow_operation", None) is operation and operation.task.done():
            gs._end_turn_flow_operation = None


async def _run_end_turn_bounded(
    ctx: Context,
    tactical: str = "",
    strategic: str = "",
    tooling: str = "",
    planning: str = "",
    hypothesis: str = "",
) -> str:
    """Enforce one operation deadline without downgrading confirmed execution."""

    budget_seconds = _budget_seconds()
    receipt = _EndTurnReceipt()
    token = end_turn_confirmation.set(receipt.record)
    try:
        async with asyncio.timeout(budget_seconds):
            return await _run_end_turn_impl(
                ctx,
                tactical=tactical,
                strategic=strategic,
                tooling=tooling,
                planning=planning,
                hypothesis=hypothesis,
                _receipt=receipt,
            )
    except TimeoutError:
        if receipt.confirmed_result is not None:
            log.warning("end_turn confirmed; post-processing exceeded the call budget")
            return pipeline._filter_downstream_result(
                "end_turn", {}, _brief_pending_result(receipt.confirmed_result)
            )
        log.error(
            "end_turn exceeded its %.0fs budget; reporting an unknown outcome",
            budget_seconds,
        )
        return pipeline._filter_downstream_result(
            "end_turn",
            {},
            _BUDGET_EXHAUSTED_RECEIPT.format(budget=budget_seconds),
        )
    except Exception:
        if receipt.confirmed_result is None:
            raise
        log.warning("end_turn confirmed; post-processing failed", exc_info=True)
        return pipeline._filter_downstream_result(
            "end_turn", {}, _brief_pending_result(receipt.confirmed_result)
        )
    finally:
        end_turn_confirmation.reset(token)


async def _run_end_turn_impl(
    ctx: Context,
    *,
    tactical: str = "",
    strategic: str = "",
    tooling: str = "",
    planning: str = "",
    hypothesis: str = "",
    _receipt: _EndTurnReceipt | None = None,
) -> str:
    """Run the end-turn flow."""
    gs = pipeline._get_game(ctx)

    def _render_result(raw_result: str) -> str:
        """Render only after end-turn control flow has consumed raw markers."""

        return pipeline._filter_downstream_result("end_turn", {}, raw_result)

    # Continuation is observation of the existing request. Avoid the diary's
    # InGame queries before reaching the pending-aware state machine.
    if getattr(gs, "_pending_end_turn", False):
        identity = getattr(gs, "_game_identity", None)
        return await _complete_end_turn(
            ctx,
            _diary_turn=getattr(gs, "_pending_end_turn_from", None) or 0,
            _diary_civ_type=identity[0] if identity else None,
            _diary_seed=identity[1] if identity else None,
            _receipt=_receipt,
        )

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
    #
    # Continuations already branched above and never re-run this preparation.
    requires_reflections = pipeline._get_play_profile(ctx).requires_reflections
    if missing and requires_reflections:
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

    return await _complete_end_turn(
        ctx,
        _diary_turn=_diary_turn,
        _diary_civ_type=_diary_civ_type,
        _diary_seed=_diary_seed,
        _receipt=_receipt,
    )


async def _complete_end_turn(
    ctx: Context,
    *,
    _diary_turn: int = 0,
    _diary_civ_type: str | None = None,
    _diary_seed: int | None = None,
    _receipt: _EndTurnReceipt | None = None,
) -> str:
    """Execute/observe once, then use the remaining call budget for extras."""
    gs = pipeline._get_game(ctx)
    receipt = _receipt if _receipt is not None else _EndTurnReceipt()

    def _render_result(raw_result: str) -> str:
        return pipeline._filter_downstream_result("end_turn", {}, raw_result)

    async def execute_and_record() -> str:
        raw = await gs.end_turn()
        receipt.record(raw)
        return raw

    # Advance the turn
    # Keep the machine result raw while this wrapper handles HANG, turn
    # advancement, blockers, watchdogs, and game-over logging.  Localize once
    # at the final return so those branches do not lose their prefixes.
    result = await pipeline._logged(
        ctx, "end_turn", {}, execute_and_record, localize=False
    )
    receipt.record(result)
    if receipt.confirmed_result is not None and _confirmed_turns(result) is None:
        # The pipeline can turn a failure after the core confirmation into an
        # error receipt. Preserve the stronger evidence already recorded.
        return _render_result(_brief_pending_result(receipt.confirmed_result))

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
            result + "\n"
            "等待已达到诊断阈值，推进结果尚未确认，本次调用没有重启游戏。\n"
            f"待核验存档: {hang_save}。在途期间诊断使用 GameCore 或明确的外交/议会查询。\n"
            "下一步：停止本回合循环并原样报告。重启游戏是宿主机/操作者的动作，"
            "按 kill_game → launch_game → load_game_save 分开执行（每次一个独立调用），"
            "不要在本回合循环里尝试重启，也不要等待重启结果。\n"
            "禁止在未核对结果前重复发送 end_turn 或重复已发出的改动。\n"
            "HANG_RECOVERY_IS_A_SEPARATE_STEP"
        )


    # Clear stale camera events on successful turn advance
    turn_advanced = _confirmed_turns(result) is not None
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
        # Repeated observations do not authorize submitting a congress vote.
        # Leave the blocker visible for the explicit resolution path.

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
            result = _brief_pending_result(result)
        else:
            result += "\n\n" + brief_context.brief
            if expected_turn is not None and brief_context.turn != expected_turn:
                result += (
                    f"\n\n注意: end_turn 确认推进到 T{expected_turn}，"
                    f"但简报读到 T{brief_context.turn}。以只读复核为准，不要重发 end_turn。"
                )

    return _render_result(result)
