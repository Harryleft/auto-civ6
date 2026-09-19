"""End-turn state machine — snapshot, blocker resolution, turn advancement."""

from __future__ import annotations

import asyncio
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import civ_mcp.narrate as nr
from civ_mcp import lua as lq
from civ_mcp.connection import DEFAULT_TIMEOUT, CommandNotSentError, LuaError
from civ_mcp.game_lifecycle import cleanup_old_autosaves, save_game

if TYPE_CHECKING:
    from civ_mcp.game_state import GameState

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# World Congress: drive it, do not wait it out
# ---------------------------------------------------------------------------
# The congress opens *inside* ACTION_ENDTURN and parks on its screen. Waiting
# passively for it is what makes a congress turn look like a 10-20 minute hang:
# a human opens the congress, picks a side and submits, and the turn resolves in
# seconds. The driver does exactly that from Lua, so it is the fast path and it
# has to run *early* and keep trying across the whole session-opening window.
#
# An earlier revision drove at most three times, starting a full minute in. A
# session that opened after t+180s was therefore never driven at all, and the
# turn fell into the passive tail this schedule exists to avoid.
#
# The two operations carry very different risk and are bounded separately:
#
# * ``drive_world_congress`` is a **no-op while no session is open** — it reports
#   ``WC_DRIVE|no_session`` and changes nothing — so a speculative call cannot
#   close UI or disturb AI processing. It can therefore run on a real schedule.
# * Blind popup dismissal *does* close UI while the AI may still be processing,
#   which is the documented cause of wedged AI turns. It keeps a small bound and
#   starts late.
_WC_FIRST_DRIVE_AFTER = 5.0
# Burst: catch a session that opens promptly, which is the common case.
_WC_DRIVE_BURST_INTERVAL = 15.0
_WC_DRIVE_BURST_PROBES = 5
# Then sparse coverage. Measured against total time since ACTION_ENDTURN was sent,
# so it continues across calls and still reaches a session that opens late.
_WC_DRIVE_SPARSE_INTERVAL = 60.0
_WC_DRIVE_SPARSE_PROBES = 5

_WC_FIRST_DISMISS_AFTER = 60.0
_WC_DISMISS_INTERVAL = 120.0
_WC_MAX_DISMISS_PROBES = 2

# Poll cadence for the end_turn wait loop, sized from measured turns rather than
# from an assumption about how slow the AI is. Across 710 recorded end_turn calls
# on this machine the real distribution was median 15.5s, p90 26.3s, p97.6 90s —
# and every one of the 17 calls over 180s had burnt a much longer cadence to
# exhaustion, 8 of them on a blocker that was there from the start. The old
# ladder ran to 550s (and congress to 1210s), so it was almost never useful and
# turned a 15-second operation into a ten-minute wait.
_END_TURN_POLL_DELAYS = (
    2.0,
    2.0,
    3.0,
    3.0,
    5.0,
    5.0,
    8.0,
    8.0,
    8.0,  # ~44s
    10.0,
    10.0,
    10.0,
    12.0,  # ~86s: reach 90s with the quick phase
)

# One call polls for at most this long, then reports. Waiting out a genuinely
# slow turn is handled across calls instead: calling end_turn again while a
# request is already in flight does NOT re-send ACTION_ENDTURN (see
# ``_pending_end_turn``), it just keeps polling. That is what lets a single call
# stay inside a three-minute ceiling without giving up on the turn.
_END_TURN_POLL_WINDOW_SECONDS = 102.0

# How long the same pending turn may go without progress across all calls before
# this is called a wedged AI turn. 600s is ~7x the measured p90 and comfortably
# past the worst turn ever recorded (~590s), so it bounds the waiting without
# reclassifying a slow-but-working turn.
PENDING_TURN_HANG_AFTER_SECONDS = 600.0


def _end_turn_poll_delays(wc_turn: bool) -> tuple[float, ...]:
    """Return the poll cadence used while waiting for the turn to advance.

    Congress turns no longer get a longer *single-call* window: their extra
    coverage comes from the cumulative drive schedule, which is measured against
    total time since ACTION_ENDTURN was sent and therefore continues across
    calls. The parameter is kept because callers and tests still ask by kind.
    """

    return _END_TURN_POLL_DELAYS


# ---------------------------------------------------------------------------
# One end_turn call's time budget
# ---------------------------------------------------------------------------
# A single end_turn MCP call blocks inside the host's tool-call deadline while
# the AI civilizations play. That deadline used to be a hand-picked 900 s while
# the wait-loop constants above already summed to 1210 s on congress turns, so
# a congress turn could outlive its host deadline and be killed mid-advance —
# an outcome the agent cannot distinguish from "the turn never advanced".
#
# The budget below is *derived* from the constants the wait loop actually
# consumes, so editing a poll cadence re-checks the host-deadline invariant in
# tests/test_end_turn_budget.py instead of silently invalidating it. The host
# deadline is asserted against `end_turn_budget().total_seconds`, and the flow
# in server/tools/end_turn_flow.py enforces that ceiling from the inside.

# Phase 1 quick check: 8 polls at 0.5 s.
_PHASE1_SLEEP_SECONDS = 8 * 0.5
# Phase 3: popup-dismiss re-poll (up to 5 x 2 s) + final verification (2 s).
_POPUP_REPOLL_SLEEP_SECONDS = 5 * 2.0
_FINAL_VERIFY_SLEEP_SECONDS = 2.0
_PHASE3_SLEEP_SECONDS = _POPUP_REPOLL_SLEEP_SECONDS + _FINAL_VERIFY_SLEEP_SECONDS
# Extra sleep a congress turn performs: each drive/dismiss that actually acted
# is followed by a 2 s re-check. Bounded by the schedule above.
_WC_PROBE_SLEEP_SECONDS = (
    _WC_DRIVE_BURST_PROBES + _WC_DRIVE_SPARSE_PROBES + _WC_MAX_DISMISS_PROBES
) * 2.0
# `_check_mid_turn_diplomacy` may sleep once to let a just-opened session
# populate its dialogue, then up to 5 x 2 s waiting for a war declaration to
# clear. It runs at most twice per pass: the Phase 2 early probe and the Phase 3
# fallback.
_WAR_DECLARATION_POLL_ATTEMPTS = 5
_DIPLOMACY_PROBE_SLEEP_SECONDS = 2 * (2.0 + _WAR_DECLARATION_POLL_ATTEMPTS * 2.0)

# ---------------------------------------------------------------------------
# Recovery is a separate call, not part of this one
# ---------------------------------------------------------------------------
# A genuinely wedged AI turn needs a reload: the game's own background job stops
# making progress and Lua cannot wake it, so the only known escape is to relaunch
# and load an autosave. That repair used to run *inside* end_turn, up to three
# times. Two things were wrong with that:
#
# * it made one call's ceiling cover the wait *plus* three relaunches, so the
#   deadline had to be ~50 minutes for an operation whose normal case is seconds;
# * the caller could no longer tell "the turn was slow" from "the turn was wedged
#   and the game got restarted", because both came back as one result.
#
# Recovery is therefore an explicit step now, and this ceiling is what the host
# deadline has to cover for that step.
RESTART_AND_LOAD_CEILING_SECONDS = 300.0  # kill + relaunch + FrontEnd load
RESTART_RECONNECT_CEILING_SECONDS = 60.0  # restart_and_load's reconnect loop
RESTART_IDENTITY_CEILING_SECONDS = 15.0  # post-load identity re-check

# Per-query ceiling. A query that exceeds the connection timeout raises instead
# of returning, so no single query can cost more than this.
QUERY_CEILING_SECONDS = DEFAULT_TIMEOUT
# Allowance for the queries one end_turn performs outside the wait loop
# (overview, identity, diary snapshot, pre/post snapshot diff, blocker detail,
# game-over probes, next-turn brief). Declared rather than derived. It is small
# on purpose: the median recorded call was 15.5s *including* this work, because
# the poll almost always ends within seconds.
QUERY_RESERVE_SECONDS = 25.0

# The clock includes transport/query time and gaps between continuation calls.
# Tests replace both seams, so timing assertions do not require a live game.
_monotonic = time.monotonic


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _pending_elapsed(gs: GameState) -> float:
    started = getattr(gs, "_pending_end_turn_started", None)
    if started is None:
        # Compatibility for an in-memory request created before this field exists.
        started = _monotonic() - getattr(gs, "_pending_end_turn_wait", 0.0)
        gs._pending_end_turn_started = started
    elapsed = max(0.0, _monotonic() - started)
    gs._pending_end_turn_wait = elapsed
    return elapsed


@dataclass(frozen=True)
class EndTurnBudget:
    """Named parts of the worst-case wall time one end_turn call may consume.

    Recovery is deliberately absent: it is a separate call now, so folding it in
    here would once again size the deadline for restarts rather than for a turn.
    """

    poll_seconds: float
    query_seconds: float

    @property
    def total_seconds(self) -> float:
        return self.poll_seconds + self.query_seconds


def poll_sleep_budget_seconds(wc_turn: bool) -> float:
    """Return the sleep seconds one call's wait phase can consume.

    Bounded by the per-call window, plus the two conditional reserves that run
    inside an iteration (congress probes, the diplomacy probe).
    """

    return (
        _END_TURN_POLL_WINDOW_SECONDS
        + (_WC_PROBE_SLEEP_SECONDS if wc_turn else 0.0)
        + _DIPLOMACY_PROBE_SLEEP_SECONDS
    )


def restart_and_load_budget_seconds() -> float:
    """Return the ceiling for the explicit recovery call.

    ``restart_and_load`` is one kill/relaunch/load, not a retry loop: recovery
    became a deliberate step, so there is no longer a per-attempt × retries
    product to account for.
    """

    return (
        RESTART_AND_LOAD_CEILING_SECONDS
        + RESTART_RECONNECT_CEILING_SECONDS
        + RESTART_IDENTITY_CEILING_SECONDS
    )


def longest_agent_call_seconds() -> float:
    """Return the ceiling the host deadline must cover.

    This is the longest call the *turn loop* makes, which is ``end_turn``. It
    deliberately excludes the relaunch-shaped operations (``restart_and_load``,
    ``launch_game``, and ``load_game_save``'s FrontEnd path): a relaunch waits on
    the game itself — up to 60 s for the process and 180 s for FireTuner's port —
    so it cannot fit inside a three-minute loop, and the documented recovery
    procedure is an operator sequence rather than an in-loop call. Their ceiling
    is declared separately by :func:`restart_and_load_budget_seconds`.
    """

    return end_turn_budget(wc_turn=True).total_seconds


def end_turn_budget(*, wc_turn: bool = True) -> EndTurnBudget:
    """Return the worst-case budget for one end_turn call.

    ``wc_turn=True`` is the conservative default: the caller cannot know in
    advance whether the turn will open a World Congress, and a deadline that
    only covers plain turns is exactly the bug this model fixes.
    """

    return EndTurnBudget(
        poll_seconds=poll_sleep_budget_seconds(wc_turn),
        query_seconds=QUERY_RESERVE_SECONDS,
    )


def _barbarian_attack_opportunities(
    units: list[lq.UnitInfo], overview: lq.BarbarianOverview
) -> list[tuple[int, int, int]]:
    """Return own unit/target pairs that can attack a visible barbarian now."""

    barbarian_positions = {(unit.x, unit.y) for unit in overview.units}
    opportunities: list[tuple[int, int, int]] = []
    for unit in units:
        for target in unit.targets:
            try:
                target_x, target_y = (int(value) for value in target.split(",", 1))
            except (TypeError, ValueError):
                continue
            if (target_x, target_y) in barbarian_positions:
                opportunities.append((unit.unit_id, target_x, target_y))
    return opportunities


def _can_override_end_turn_blockers(
    hard_blockers: list[tuple[str, str]],
) -> bool:
    """Return whether the UI's ``CanEndTurn`` result may override blockers.

    Unit-order blockers are backed by live GameCore unit state.  Treating a
    contradictory ``UI.CanEndTurn() == true`` as authoritative can start the
    next-turn job while a unit task is still incomplete, which is unsafe for
    the async game-core worker.  Other blocker types retain the existing
    compatibility behavior for now.
    """
    return not any(
        blocker_type == "ENDTURN_BLOCKING_UNITS"
        for blocker_type, _ in hard_blockers
    )


def _wc_drive_due(
    *,
    wc_turn: bool,
    cumulative_wait: float,
    drives: int,
) -> bool:
    """Return whether another congress *drive* attempt should run.

    Gated on the earlier ``get_world_congress`` result rather than on the
    end-turn blocker query, because while the congress segment runs that query
    reports nothing — the game is not waiting on our side.

    The schedule is burst-then-sparse: a session that opens promptly is driven
    within seconds, and one that opens late is still driven instead of being
    waited out. It is bounded in total, so a session that never opens cannot
    turn this into a dense loop of InGame calls during AI processing.
    """

    if not wc_turn:
        return False
    if drives < _WC_DRIVE_BURST_PROBES:
        return cumulative_wait >= _WC_FIRST_DRIVE_AFTER + drives * _WC_DRIVE_BURST_INTERVAL
    sparse = drives - _WC_DRIVE_BURST_PROBES
    if sparse >= _WC_DRIVE_SPARSE_PROBES:
        return False
    burst_span = _WC_DRIVE_BURST_PROBES * _WC_DRIVE_BURST_INTERVAL
    return cumulative_wait >= (
        _WC_FIRST_DRIVE_AFTER + burst_span + sparse * _WC_DRIVE_SPARSE_INTERVAL
    )


def _wc_dismiss_due(
    *,
    wc_turn: bool,
    cumulative_wait: float,
    dismissals: int,
) -> bool:
    """Return whether another blind congress popup dismissal should run.

    Separate from the drive on purpose. Closing UI while the AI may still be
    processing is the documented cause of wedged turns, so this stays late and
    tightly bounded; the driver does its own screen cleanup when it submits.
    """

    return (
        wc_turn
        and dismissals < _WC_MAX_DISMISS_PROBES
        and cumulative_wait
        >= _WC_FIRST_DISMISS_AFTER + dismissals * _WC_DISMISS_INTERVAL
    )


async def _drive_congress(gs: GameState, *, expected_world_epoch: int | None = None) -> bool:
    """Vote and submit an *open* congress session. No-op without one.

    Reads the live resolution list, applies the registered policy (defaulting to
    one free vote per resolution, which costs no favor), votes, submits the turn
    and clears the congress screens — the program-side equivalent of a human
    opening the congress, choosing a side and clicking vote.

    Returns True only when votes were actually submitted, so the caller can
    re-check the turn immediately instead of continuing to poll.
    """

    gs._wc_drive_uncertain = True
    try:
        result = await gs.drive_world_congress(**({"expected_world_epoch": expected_world_epoch} if expected_world_epoch is not None else {}))
        if expected_world_epoch is not None and _world_changed_while_waiting(gs, expected_world_epoch):
            return False
    except CommandNotSentError:
        gs._wc_drive_uncertain = False
        return False
    except Exception:
        log.debug("World Congress drive failed", exc_info=True)
        return False
    gs._wc_drive_uncertain = False
    if "submitted" in result:
        log.info("World Congress driven from Lua: %s", result[:240])
        return True
    log.debug("World Congress drive had no session: %s", result[:120] or "<no result>")
    return False


async def _dismiss_congress_popup(gs: GameState) -> bool:
    """Clear a stale congress screen when no session can be driven."""

    try:
        dismissed = await gs.dismiss_popup()
    except Exception:
        log.debug("Congress popup dismissal failed", exc_info=True)
        return False
    log.info("World Congress popup dismissal: %s", dismissed[:80])
    return "Dismissed" in dismissed


async def _check_mid_turn_diplomacy(
    gs: GameState,
    lua: str,
    turn_before: int | None,
    *, expected_world_epoch: int | None = None,
) -> tuple[str | None, bool]:
    """Probe for AI diplomatic proposals during end_turn polling.

    Returns (message, advanced) where message is a string to return to the
    agent if diplomacy was found, or None if no diplomacy detected.
    ``advanced`` is True if the turn advanced during war-declaration handling.

    Handles war auto-dismiss, deal formatting, and session info.  Extracted
    from the Phase 3 inline logic so both the early probe (Phase 2, ~45s)
    and the full-timeout fallback (Phase 3) can share the same code.
    """
    epoch = getattr(gs, "_world_epoch", 0) if expected_world_epoch is None else expected_world_epoch
    try:
        if _world_changed_while_waiting(gs, epoch):
            return _RELOAD_INTERRUPTED, False
        mid_sessions = await gs.get_diplomacy_sessions(expected_world_epoch=epoch)
        if _world_changed_while_waiting(gs, epoch):
            return _RELOAD_INTERRUPTED, False
        if not mid_sessions:
            return None, False

        # DiplomacyActionView text can take 1-2s to populate after session
        # opens during AI processing. If text is empty, retry once.
        if any(not s.dialogue_text for s in mid_sessions):
            await _sleep(2.0)
            if _world_changed_while_waiting(gs, epoch):
                return _RELOAD_INTERRUPTED, False
            mid_sessions = await gs.get_diplomacy_sessions(expected_world_epoch=epoch)
            if _world_changed_while_waiting(gs, epoch):
                return _RELOAD_INTERRUPTED, False

        # Auto-dismiss war declarations — these are informational only
        # (you can't decline a war). Dismiss and report to the agent.
        war_sessions = [s for s in mid_sessions if s.is_at_war]
        if war_sessions:
            war_names = []
            for ws in war_sessions:
                close_lua = lq.build_diplomacy_respond(ws.other_player_id, "EXIT")
                if _world_changed_while_waiting(gs, epoch):
                    return _RELOAD_INTERRUPTED, False
                await gs.conn.execute_mutation(close_lua, turn_action="diplomacy", expected_world_epoch=epoch)
                if _world_changed_while_waiting(gs, epoch):
                    return _RELOAD_INTERRUPTED, False
                war_names.append(f"{ws.other_civ_name} ({ws.other_leader_name})")
                log.info(
                    "Auto-dismissed war declaration from %s",
                    ws.other_civ_name,
                )
            # Remove war sessions from the list
            mid_sessions = [s for s in mid_sessions if not s.is_at_war]
            # If only war sessions, resume polling (original ACTION_ENDTURN
            # is still in flight — do NOT re-send or turns will skip)
            if not mid_sessions:
                war_msg = ", ".join(war_names)
                advanced = False
                for _ in range(_WAR_DECLARATION_POLL_ATTEMPTS):
                    await _sleep(2.0)
                    if _world_changed_while_waiting(gs, epoch):
                        return _RELOAD_INTERRUPTED, False
                    turn_after = await _get_turn_number(gs)
                    if _world_changed_while_waiting(gs, epoch):
                        return _RELOAD_INTERRUPTED, False
                    if (
                        turn_after is not None
                        and turn_before is not None
                        and turn_after > turn_before
                    ):
                        advanced = True
                        break
                if advanced:
                    return None, True  # turn advanced, caller handles snapshot
                # Closing the input does not cancel the original request.
                return (
                    f"WAR DECLARED by {war_msg}! Session dismissed.\n"
                    f"回合尚未确认推进；再次调用 end_turn 只继续观察，不重发。\n"
                    f"Reassess: check unit positions, city defenses, and military strength."
                ), False

        if not mid_sessions:
            # All sessions were war declarations and turn advanced
            return None, True if war_sessions else False

        # Non-war sessions: format for agent
        session_info = []
        for s in mid_sessions:
            phase = (
                "deal"
                if s.deal_summary
                else ("goodbye" if s.buttons == "GOODBYE" else "active")
            )
            session_info.append(f"{s.other_civ_name} ({s.other_leader_name}) [{phase}]")
        has_deal = any(s.deal_summary for s in mid_sessions)
        lines: list[str] = []
        if war_sessions:
            war_names_str = ", ".join(f"{ws.other_civ_name}" for ws in war_sessions)
            lines.append(f"WAR DECLARED by {war_names_str}! (auto-dismissed)")
        lines.append(
            f"Turn paused — AI diplomatic proposal from {', '.join(session_info)}.",
        )
        for s in mid_sessions:
            if s.dialogue_text:
                lines.append(f'{s.other_civ_name} says: "{s.dialogue_text}"')
            if s.reason_text:
                lines.append(f"Reason: {s.reason_text}")
            if s.deal_summary:
                lines.append(f"Deal from {s.other_civ_name}: {s.deal_summary}")
        if has_deal:
            lines.append(
                "Use respond_to_trade(other_player_id=X, accept=True/False) to handle it, then end_turn again."
            )
        else:
            lines.append("Use respond_to_diplomacy to handle it, then end_turn again.")
        return "\n".join(lines), False
    except Exception:
        if _world_changed_while_waiting(gs, epoch):
            return _RELOAD_INTERRUPTED, False
        log.debug("Mid-turn diplomacy check failed", exc_info=True)
        return None, False


async def _get_turn_number(gs: GameState) -> int | None:
    """Read the current game turn number."""
    try:
        lines = await gs.conn.execute_read(
            'print(Game.GetCurrentGameTurn()); print("---END---")'
        )
        if lines:
            return int(lines[0])
    except (LuaError, ValueError, IndexError):
        pass
    return None


def _game_over_message(gs: GameState, gameover: lq.GameOverStatus) -> str:
    """Record a finished game and return the GAME OVER result message."""
    gs._pending_end_turn = False
    gs._pending_end_turn_from = None
    gs.conn.turn_in_progress = False
    gs._pending_end_turn_started = None
    gs._last_game_over = gameover
    vtype = gameover.victory_type.replace("VICTORY_", "").replace("_", " ").title()
    if gameover.is_defeat:
        return (
            f"GAME OVER — DEFEAT. {gameover.winner_leader} of {gameover.winner_name} "
            f"won a {vtype} victory. The game has ended. No further actions are possible."
        )
    return f"GAME OVER — VICTORY! You won a {vtype} victory! The game has ended."


async def _check_victory_proximity(gs: GameState) -> list[lq.TurnEvent]:
    """Lightweight per-turn check for foreign victory threats."""
    events: list[lq.TurnEvent] = []
    lines = await gs.conn.execute_write(lq.build_victory_proximity_query())
    enabled: set[str] = set()
    for line in lines:
        if line.startswith("VENABLED|"):
            enabled.add(line.split("|", 1)[1])
    for line in lines:
        if line.startswith("REL_THREAT|"):
            if enabled and "VICTORY_RELIGIOUS" not in enabled:
                continue
            parts = line.split("|")
            if len(parts) >= 4:
                civ_name, rel_name = parts[1], parts[2]
                count, total = int(parts[3]), int(parts[4])
                if count >= total:
                    events.append(
                        lq.TurnEvent(
                            priority=1,
                            category="victory",
                            message=f"!!! RELIGIOUS VICTORY IMMINENT: {civ_name}'s {rel_name} is majority in ALL {total} civilizations!",
                        )
                    )
                elif count >= total - 1:
                    events.append(
                        lq.TurnEvent(
                            priority=1,
                            category="victory",
                            message=f"!! RELIGIOUS VICTORY THREAT: {civ_name}'s {rel_name} is majority in {count}/{total} civilizations!",
                        )
                    )
        elif line.startswith("DIPLO_THREAT|"):
            if enabled and "VICTORY_DIPLOMATIC" not in enabled:
                continue
            parts = line.split("|")
            if len(parts) >= 3:
                dvp = int(parts[2])
                if dvp >= 20:
                    events.append(
                        lq.TurnEvent(
                            priority=1,
                            category="victory",
                            message=f"!!! DIPLOMATIC VICTORY IMMINENT: {parts[1]} has {dvp}/20 DVP — wins immediately, does NOT wait for World Congress!",
                        )
                    )
                elif dvp >= 18:
                    events.append(
                        lq.TurnEvent(
                            priority=1,
                            category="victory",
                            message=f"!! DIPLOMATIC VICTORY THREAT: {parts[1]} has {dvp}/20 DVP — wins IMMEDIATELY at 20, does not wait for WC. Must strip DVP at next World Congress BEFORE they reach 20.",
                        )
                    )
                elif dvp >= 15:
                    events.append(
                        lq.TurnEvent(
                            priority=1,
                            category="victory",
                            message=f"!! DIPLOMATIC VICTORY THREAT: {parts[1]} has {dvp}/20 DVP!",
                        )
                    )
                else:
                    events.append(
                        lq.TurnEvent(
                            priority=2,
                            category="victory",
                            message=f"Diplomatic race: {parts[1]} has {dvp}/20 DVP.",
                        )
                    )
        elif line.startswith("SCI_THREAT|"):
            if enabled and "VICTORY_TECHNOLOGY" not in enabled:
                continue
            parts = line.split("|")
            if len(parts) >= 4:
                vp, needed = int(parts[2]), int(parts[3])
                if vp >= needed - 1:
                    events.append(
                        lq.TurnEvent(
                            priority=1,
                            category="victory",
                            message=f"!! SCIENCE VICTORY IMMINENT: {parts[1]} has {vp}/{needed} space race projects!",
                        )
                    )
                elif vp >= 1:
                    events.append(
                        lq.TurnEvent(
                            priority=2,
                            category="victory",
                            message=f"Science race: {parts[1]} has {vp}/{needed} space race projects.",
                        )
                    )
    return events


async def _check_empire_warnings(
    gs: GameState,
    snap: lq.TurnSnapshot | None,
) -> tuple[list[lq.TurnEvent], int | None]:
    """Lightweight alerts that compensate for the Sensorium Effect.

    Surfaces information a human player would notice via passive visual cues:
    scoreboard position, idle trade routes, resource caps, loyalty crises,
    military imbalance, and gold deficits.

    Returns (events, score) where score is the current game score if available.
    """
    events: list[lq.TurnEvent] = []
    game_score: int | None = None

    # --- Loyalty crisis (from snapshot cities) ---
    if snap:
        for cs in snap.cities.values():
            if cs.loyalty_per_turn < -5:
                turns_left = (
                    int(cs.loyalty / abs(cs.loyalty_per_turn))
                    if cs.loyalty_per_turn < 0
                    else 99
                )
                events.append(
                    lq.TurnEvent(
                        priority=1,
                        category="city",
                        message=(
                            f"LOYALTY CRISIS: {cs.name} losing {cs.loyalty_per_turn:+.1f}/t "
                            f"(loyalty {cs.loyalty:.0f}) — will rebel in ~{turns_left} turns!"
                        ),
                    )
                )
            elif cs.loyalty < 30 and cs.loyalty_per_turn < 0:
                events.append(
                    lq.TurnEvent(
                        priority=2,
                        category="city",
                        message=f"LOYALTY WARNING: {cs.name} at {cs.loyalty:.0f} loyalty ({cs.loyalty_per_turn:+.1f}/t)",
                    )
                )

    # --- Resource cap (from snapshot stockpiles) ---
    if snap:
        for s in snap.stockpiles:
            net = s.per_turn - s.demand + s.imported
            if s.cap > 0 and s.amount >= s.cap and net > 0:
                events.append(
                    lq.TurnEvent(
                        priority=3,
                        category="economy",
                        message=(
                            f"RESOURCE CAP: {s.name} {s.amount}/{s.cap} ({net:+d}/t) "
                            f"— excess is wasted. Trade surplus or spend it."
                        ),
                    )
                )

    # --- Gold deficit (quick overview query) ---
    try:
        ov_lines = await gs.conn.execute_write(lq.build_overview_query())
        overview = lq.parse_overview_response(ov_lines)
    except Exception:
        log.debug("Overview query for warnings failed", exc_info=True)
        overview = None

    if overview:
        game_score = overview.score
        if (
            overview.gold_per_turn < 0
            and overview.gold < abs(overview.gold_per_turn) * 20
        ):
            turns_to_zero = (
                int(overview.gold / abs(overview.gold_per_turn))
                if overview.gold_per_turn < 0
                else 99
            )
            events.append(
                lq.TurnEvent(
                    priority=2,
                    category="economy",
                    message=(
                        f"DEFICIT: Gold {overview.gold_per_turn:+.0f}/t with {overview.gold:.0f} in treasury "
                        f"— bankrupt in ~{turns_to_zero} turns."
                    ),
                )
            )

    # --- Idle trade routes (lightweight Lua query) ---
    try:
        tr_lines = await gs.conn.execute_write(lq.build_trade_capacity_check())
        for line in tr_lines:
            if line.startswith("TRCAP|"):
                parts = line.split("|")
                cap, active = int(parts[1]), int(parts[2])
                idle = cap - active
                if idle > 0:
                    events.append(
                        lq.TurnEvent(
                            priority=2,
                            category="economy",
                            message=(
                                f"IDLE TRADE ROUTE: {idle} unused route "
                                f"{'capacity' if idle == 1 else 'capacities'} "
                                f"({active}/{cap} active). Build a Trader or assign an idle one."
                            ),
                        )
                    )
                break
    except Exception:
        log.debug("Trade capacity check failed", exc_info=True)

    # --- Scoreboard + military disparity (rival snapshot, every 5 turns) ---
    turn = snap.turn if snap else 0
    if turn > 0 and turn % 5 == 0:
        try:
            rival_lines = await gs.conn.execute_write(lq.build_rival_snapshot_query())
            rivals = lq.parse_rival_snapshot_response(rival_lines)
            if rivals and overview:
                our_sci = overview.science_yield
                # Compute science rankings
                all_sci = [(r.name, r.sci) for r in rivals] + [("You", our_sci)]
                all_sci.sort(key=lambda x: x[1], reverse=True)
                our_rank = next(i + 1 for i, (n, _) in enumerate(all_sci) if n == "You")
                leader_name, leader_sci = all_sci[0]
                if our_rank > 1 and len(all_sci) > 2:
                    events.append(
                        lq.TurnEvent(
                            priority=2,
                            category="scoreboard",
                            message=(
                                f"SCOREBOARD: Your science ({our_sci:.1f}/t) ranks "
                                f"{our_rank} of {len(all_sci)}. "
                                f"Leader: {leader_name} at {leader_sci:.1f}/t."
                            ),
                        )
                    )

                # Military disparity
                our_mil_lines = await gs.conn.execute_read(
                    "local me = Game.GetLocalPlayer(); "
                    "print(Players[me]:GetStats():GetMilitaryStrength()); "
                    'print("---END---")'
                )
                our_mil = 0
                if our_mil_lines:
                    try:
                        our_mil = int(float(our_mil_lines[0]))
                    except (ValueError, IndexError):
                        pass
                if our_mil > 0:
                    for r in rivals:
                        if r.mil >= our_mil * 2:
                            events.append(
                                lq.TurnEvent(
                                    priority=2,
                                    category="military",
                                    message=(
                                        f"MILITARY WARNING: {r.name} has {r.mil} military "
                                        f"({r.mil / our_mil:.1f}x ours at {our_mil})."
                                    ),
                                )
                            )
        except Exception:
            log.debug("Rival snapshot for warnings failed", exc_info=True)

    return events, game_score


def _check_save_scumming(gs: GameState) -> tuple[list[lq.TurnEvent], bool]:
    """Detect save-scumming patterns from recent save load history.

    Benchmark runs should play forward — save loads are only legitimate for
    recovering from engine hangs or loading the initial scenario. Repeated
    loads across different turns indicate the agent is rolling back to retry
    unfavorable outcomes.

    Thresholds (tuned against Opus T326 legitimate deadlock debugging and
    Gemini's 19-load scumming run):
      - MINOR warn: 3+ loads across 3+ distinct turns (span >= 10)
      - STRONG warn: 5+ loads across 5+ distinct turns (span >= 20)
      - HARD STOP: 8+ loads across 8+ distinct turns (span >= 30)

    The "distinct turns" signal is critical — 25 loads all at T326 is a
    deadlock, 25 loads spread across T100-T300 is scumming.

    Returns (events, hard_stop).
    """
    events: list[lq.TurnEvent] = []
    history = gs._save_load_history

    if len(history) < 3:
        return events, False

    # Only consider in-play loads (high water turn > 0)
    play_loads = [(ts, turn, name) for ts, turn, name in history if turn > 0]
    if len(play_loads) < 3:
        return events, False

    n_loads = len(play_loads)
    distinct_turns = sorted({turn for _, turn, _ in play_loads})
    n_distinct = len(distinct_turns)
    span = distinct_turns[-1] - distinct_turns[0] if distinct_turns else 0

    # Hard stop — abort the run
    if n_loads >= 8 and n_distinct >= 8 and span >= 30:
        events.append(
            lq.TurnEvent(
                priority=1,
                category="abuse",
                message=(
                    f"!!! RUN ABORTED — save scumming threshold exceeded. "
                    f"{n_loads} save loads across {n_distinct} distinct turns "
                    f"(span {span}). Benchmark runs must play forward from a "
                    f"single starting save. Repeated save loads to retry "
                    f"turns are considered cheating and invalidate the run. "
                    f"No further actions will be processed."
                ),
            )
        )
        return events, True

    # Strong warning
    if n_loads >= 5 and n_distinct >= 5 and span >= 20:
        events.append(
            lq.TurnEvent(
                priority=1,
                category="abuse",
                message=(
                    f"SAVE SCUMMING CRITICAL: {n_loads} save loads across "
                    f"{n_distinct} distinct turns (span {span}). STOP loading "
                    f"saves — this is a benchmark run. Play forward from the "
                    f"current state. The next load will abort the run."
                ),
            )
        )
        return events, False

    # Soft warning
    if n_loads >= 3 and n_distinct >= 3 and span >= 10:
        events.append(
            lq.TurnEvent(
                priority=2,
                category="abuse",
                message=(
                    f"SAVE SCUMMING WARNING: {n_loads} save loads across "
                    f"{n_distinct} different turns. Benchmark runs must play "
                    f"forward — save loads are only for recovering from engine "
                    f"hangs. Continuing to reload will result in disqualification."
                ),
            )
        )

    return events, False


end_turn_confirmation: ContextVar[Callable[[str], None] | None] = ContextVar(
    "end_turn_confirmation", default=None
)


@dataclass
class _TurnReceipt:
    confirmed: str | None = None
    subscribers: list[Callable[[str], None]] = field(default_factory=list)

    def confirm(self, result: str) -> None:
        self.confirmed = result
        for callback in list(self.subscribers):
            callback(result)


async def execute_end_turn(gs: GameState) -> str:
    """Coalesce overlapping calls into one logical operation and submission.

    The owner may be cancelled, but once sending is possible its pending state
    survives. A cancelled follower must not cancel the owner's operation.
    """
    active = getattr(gs, "_end_turn_task", None)
    follower = active is not None and not active.done()
    if follower:
        receipt = gs._end_turn_receipt
    else:
        receipt = _TurnReceipt()
        gs._end_turn_receipt = receipt
        active = asyncio.create_task(_execute_end_turn(gs, receipt))
        gs._end_turn_task = active
    callback = end_turn_confirmation.get()
    if callback is not None:
        receipt.subscribers.append(callback)
        if receipt.confirmed is not None:
            callback(receipt.confirmed)
    try:
        return await asyncio.shield(active) if follower else await active
    finally:
        if callback is not None:
            receipt.subscribers.remove(callback)
        if gs._pending_end_turn:
            _pending_elapsed(gs)
        if not follower and getattr(gs, "_end_turn_task", None) is active:
            gs._end_turn_task = None


async def _execute_end_turn(gs: GameState, receipt: _TurnReceipt) -> str:
    entry_epoch = getattr(gs, "_world_epoch", 0)
    if getattr(gs, "_reload_uncertain", False) or getattr(gs.conn, "reload_pending", False):
        return "UNKNOWN:RELOAD_PENDING|读档结果尚未确认；暂停游戏操作，先确认新局面。"
    if gs._pending_end_turn:
        gs.conn.turn_in_progress = True
        return await _observe_pending_end_turn(gs, receipt)
    # 0a. Run aborted due to save scumming — refuse to advance
    if gs._run_aborted:
        return (
            "RUN ABORTED — save scumming threshold exceeded. "
            "This benchmark run has been invalidated because the agent "
            "loaded saves across too many distinct turns. Benchmark runs "
            "must play forward from a single starting save. No further "
            "actions will be processed."
        )

    # 0. Game-over check — don't try to advance a finished game
    gameover = await gs.check_game_over()
    if gameover is not None:
        return _game_over_message(gs, gameover)

    # Record turn number at entry so we can detect external advancement
    # (e.g. game auto-ends turn when skip_remaining_units finishes all moves)
    turn_at_entry = await _get_turn_number(gs)

    # 1. Diplomacy sessions block turn advancement
    sessions = await gs.get_diplomacy_sessions()
    if sessions:
        session_info = []
        for s in sessions:
            phase = "goodbye" if s.buttons == "GOODBYE" else "active"
            session_info.append(f"{s.other_civ_name} ({s.other_leader_name}) [{phase}]")
        return (
            f"Cannot end turn: diplomacy encounter pending with {', '.join(session_info)}. "
            f"Use respond_to_diplomacy to handle it."
        )

    # 1b. Check for incoming trade deal offers (e.g. delegations from other civs)
    try:
        deals = await gs.get_pending_deals()
        if deals:
            return (
                "Cannot end turn: incoming trade deal pending.\n"
                + nr.narrate_pending_deals(deals)
            )
    except Exception:
        log.debug("Pending deal check failed", exc_info=True)

    # Do not silently skip an immediately attackable barbarian. This is a
    # narrow guard: it only blocks when the current unit scan says one of our
    # units can attack a currently visible barbarian unit. It does not block a
    # turn merely because a camp was revealed or because no combat unit is
    # available; those cases remain advisory and recoverable.
    try:
        barbarian_now = await gs.get_barbarian_overview()
        if barbarian_now.units:
            own_units = await gs.get_units()
            opportunities = _barbarian_attack_opportunities(own_units, barbarian_now)
            if opportunities:
                unit_id, target_x, target_y = opportunities[0]
                return (
                    "Cannot end turn: a visible barbarian can be attacked now. "
                    f"Assess it with get_combat_estimate(unit_id={unit_id}, "
                    f"target_x={target_x}, target_y={target_y}), then attack or "
                    "move the unit before retrying end_turn."
                )
    except Exception:
        log.debug("Immediate barbarian attack preflight failed", exc_info=True)

    # 2. Pre-dismiss any ExclusivePopupManager popups (wonder, disaster, era)
    # that may hold engine locks blocking turn advancement.
    try:
        pre_dismiss = await gs.dismiss_popup()
        if "Dismissed" in pre_dismiss:
            log.info("Pre-turn popup dismissed: %s", pre_dismiss)
    except Exception:
        log.debug("Pre-turn dismiss failed", exc_info=True)

    # 2b. World Congress gate — if WC fires this turn and no handler is
    #     registered, block end_turn and tell the agent to vote first.
    #     The WC session opens+closes within ACTION_ENDTURN synchronously,
    #     so we MUST register a handler BEFORE sending ACTION_ENDTURN.
    wc_turn = False
    try:
        wc_status = await gs.get_world_congress()
        if wc_status.turns_until_next <= 0 or wc_status.is_in_session:
            n_res = len(wc_status.resolutions) if wc_status.resolutions else 0
            # Skip gate when WC fires with 0 resolutions — nothing to vote on
            if n_res == 0 and not wc_status.is_in_session:
                log.info("WC fires this turn with 0 resolutions — auto-proceeding")
            else:
                wc_turn = True
                handler_lines = await gs.conn.execute_write(
                    f'print(__civmcp_wc_handler and "HANDLER_SET" or "NO_HANDLER"); '
                    f'print("{lq.SENTINEL}")'
                )
                handler_set = any("HANDLER_SET" in l for l in handler_lines)
                if not handler_set:
                    return (
                        f"World Congress fires this turn ({n_res} resolution(s), {wc_status.favor} favor). "
                        f"Use get_world_congress() to review resolutions and targets, "
                        f"then queue_wc_votes() to register your votes, "
                        f"then call end_turn() again."
                    )
    except Exception:
        log.debug("WC imminence check failed", exc_info=True)

    # 3. Check ALL EndTurnBlocking notifications at once, auto-resolve soft
    #    blockers, and report remaining hard blockers in a single message.
    for _round in range(3):
        try:
            blocking_lines = await gs.conn.execute_write(
                lq.build_end_turn_blocking_query()
            )
            blockers = lq.parse_end_turn_blocking(blocking_lines)
            if not blockers:
                break  # nothing blocking

            resolved_any = False
            hard_blockers: list[tuple[str, str]] = []

            for blocking_type, blocking_msg in blockers:
                # --- Auto-resolvable soft blockers ---

                if blocking_type == "ENDTURN_BLOCKING_GOVERNOR_IDLE":
                    await gs.conn.execute_mutation(
                        f"local me = Game.GetLocalPlayer(); "
                        f"local list = NotificationManager.GetList(me); "
                        f"if list then "
                        f"  for _, nid in ipairs(list) do "
                        f"    local e = NotificationManager.Find(me, nid); "
                        f"    if e and not e:IsDismissed() then "
                        f"      local bt = e:GetEndTurnBlocking(); "
                        f"      if bt and bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_GOVERNOR_IDLE then "
                        f"        pcall(function() NotificationManager.SendActivated(me, nid) end); "
                        f"        pcall(function() NotificationManager.Dismiss(me, nid) end) "
                        f"      end "
                        f"    end "
                        f"  end "
                        f"end; "
                        f'print("OK"); print("{lq.SENTINEL}")'
                    )
                    resolved_any = True
                    continue

                if blocking_type == "ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE":
                    await gs.conn.execute_mutation(
                        f"local me = Game.GetLocalPlayer(); "
                        f"Players[me]:GetCulture():SetGovernmentChangeConsidered(true); "
                        f'print("OK"); print("{lq.SENTINEL}")'
                    )
                    resolved_any = True
                    continue

                if blocking_type == "ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK":
                    await gs.conn.execute_mutation(
                        f"local me = Game.GetLocalPlayer(); "
                        f"UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_LOOKED_AT_AVAILABLE, {{}}); "
                        f"local list = NotificationManager.GetList(me); "
                        f"if list then "
                        f"  for _, nid in ipairs(list) do "
                        f"    pcall(function() "
                        f"      local e = NotificationManager.Find(me, nid); "
                        f"      if e and not e:IsDismissed() then "
                        f"        local bt = e:GetEndTurnBlocking(); "
                        f"        if bt and bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK then "
                        f"          NotificationManager.Dismiss(me, nid) "
                        f"        end "
                        f"      end "
                        f"    end) "
                        f"  end "
                        f"end; "
                        f'local i = ContextPtr:LookUpControl("/InGame/WorldCongressIntro"); '
                        f"if i then i:SetHide(true) end; "
                        f'local p = ContextPtr:LookUpControl("/InGame/WorldCongressPopup"); '
                        f"if p then p:SetHide(true) end; "
                        f'print("OK"); print("{lq.SENTINEL}")'
                    )
                    resolved_any = True
                    continue

                if blocking_type == "ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION":
                    # NEVER auto-resolve session blockers — the agent must
                    # call get_world_congress() and queue_wc_votes()
                    # to deploy diplomatic favor strategically.
                    hard_blockers.append((blocking_type, blocking_msg))
                    continue

                # Catch-all for any other World Congress blocking types
                # (e.g. special session proposals, emergency discussions)
                # Replicates the game UI's "Pass" button: LOOKED_AT_AVAILABLE
                # + dismiss all WC-related blocking notifications.
                if "WORLD_CONGRESS" in blocking_type:
                    try:
                        wc_dismiss_lines = await gs.conn.execute_mutation(
                            f"local me = Game.GetLocalPlayer(); "
                            f"UI.RequestPlayerOperation(me, PlayerOperations.WORLD_CONGRESS_LOOKED_AT_AVAILABLE, {{}}); "
                            f"local dismissed = 0; "
                            f"local list = NotificationManager.GetList(me); "
                            f"if list then "
                            f"  for _, nid in ipairs(list) do "
                            f"    pcall(function() "
                            f"      local e = NotificationManager.Find(me, nid); "
                            f"      if e and not e:IsDismissed() then "
                            f"        local bt = e:GetEndTurnBlocking(); "
                            f"        if bt and bt ~= 0 then "
                            f"          for k, v in pairs(EndTurnBlockingTypes) do "
                            f'            if v == bt and k:find("WORLD_CONGRESS") then '
                            f"              NotificationManager.Dismiss(me, nid); "
                            f"              dismissed = dismissed + 1; "
                            f"              break "
                            f"            end "
                            f"          end "
                            f"        end "
                            f"      end "
                            f"    end) "
                            f"  end "
                            f"end; "
                            f'local i = ContextPtr:LookUpControl("/InGame/WorldCongressIntro"); '
                            f"if i then i:SetHide(true) end; "
                            f'local p = ContextPtr:LookUpControl("/InGame/WorldCongressPopup"); '
                            f"if p then p:SetHide(true) end; "
                            f'print("DISMISSED:" .. dismissed); print("{lq.SENTINEL}")'
                        )
                        if any(
                            "DISMISSED:" in l and not l.endswith(":0")
                            for l in wc_dismiss_lines
                        ):
                            resolved_any = True
                            log.info("Auto-dismissed WC blocker: %s", blocking_type)
                            continue
                    except Exception:
                        log.debug("WC catch-all auto-resolve failed", exc_info=True)
                    hard_blockers.append((blocking_type, blocking_msg))
                    continue

                if blocking_type == "ENDTURN_BLOCKING_CONSIDER_DISLOYAL_CITY":
                    try:
                        result = await gs.resolve_city_capture("keep")
                        if "Error" not in result:
                            log.info("Auto-kept disloyal city: %s", result)
                            resolved_any = True
                            continue
                    except Exception:
                        log.debug("Disloyal city auto-resolve failed", exc_info=True)
                    hard_blockers.append((blocking_type, blocking_msg))
                    continue

                if blocking_type == "ENDTURN_BLOCKING_CONSIDER_RAZE_CITY":
                    try:
                        result = await gs.resolve_city_capture("keep")
                        if "Error" not in result:
                            log.info("Auto-kept captured city: %s", result)
                            resolved_any = True
                            continue
                    except Exception:
                        log.debug("Captured city auto-resolve failed", exc_info=True)
                    hard_blockers.append((blocking_type, blocking_msg))
                    continue

                if blocking_type == "ENDTURN_BLOCKING_GIVE_INFLUENCE_TOKEN":
                    try:
                        envoy_lines = await gs.conn.execute_mutation(
                            f"local me = Game.GetLocalPlayer(); "
                            f"local inf = Players[me]:GetInfluence(); "
                            f"local tokens = inf:GetTokensToGive(); "
                            f"if tokens == 0 then "
                            f"  inf:SetGivingTokensConsidered(true); "
                            f'  print("AUTO_RESOLVED"); '
                            f'else print("HAS_TOKENS|" .. tokens); end; '
                            f'print("{lq.SENTINEL}")'
                        )
                        if any("AUTO_RESOLVED" in l for l in envoy_lines):
                            resolved_any = True
                            continue
                    except Exception:
                        log.debug("Envoy auto-resolve failed", exc_info=True)
                    hard_blockers.append((blocking_type, blocking_msg))
                    continue

                if blocking_type == "ENDTURN_BLOCKING_PRODUCTION":
                    try:
                        corruption_lines = await gs.conn.execute_write(
                            f"local me = Game.GetLocalPlayer(); "
                            f"local corrupted = {{}}; "
                            f"for i, c in Players[me]:GetCities():Members() do "
                            f"  local bq = c:GetBuildQueue(); "
                            f"  if bq:GetSize() > 0 and bq:GetCurrentProductionTypeHash() == 0 then "
                            f'    table.insert(corrupted, Locale.Lookup(c:GetName()) .. " (id:" .. c:GetID() .. ")") '
                            f"  end "
                            f"end; "
                            f"if #corrupted > 0 then "
                            f'  print("CORRUPTED|" .. table.concat(corrupted, ",")) '
                            f'else print("CLEAN") end; '
                            f'print("{lq.SENTINEL}")'
                        )
                        is_corrupted = any(
                            cl.startswith("CORRUPTED|") for cl in corruption_lines
                        )
                        if is_corrupted:
                            city_names = next(
                                cl.split("|", 1)[1]
                                for cl in corruption_lines
                                if cl.startswith("CORRUPTED|")
                            )
                            dismiss_lines = await gs.conn.execute_mutation(
                                f"local me = Game.GetLocalPlayer(); "
                                f"local dismissed = 0; "
                                f"local list = NotificationManager.GetList(me); "
                                f"if list then "
                                f"  for _, nid in ipairs(list) do "
                                f"    local e = NotificationManager.Find(me, nid); "
                                f"    if e and not e:IsDismissed() then "
                                f"      local bt = e:GetEndTurnBlocking(); "
                                f"      if bt and bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_PRODUCTION then "
                                f"        NotificationManager.Dismiss(me, nid); dismissed = dismissed + 1 "
                                f"      end "
                                f"    end "
                                f"  end "
                                f"end; "
                                f'print("DISMISSED|" .. dismissed); '
                                f'print("{lq.SENTINEL}")'
                            )
                            if any(
                                "DISMISSED|" in l and not l.endswith("|0")
                                for l in dismiss_lines
                            ):
                                log.info(
                                    "Auto-dismissed corrupted production for: %s",
                                    city_names,
                                )
                                resolved_any = True
                                continue
                    except Exception:
                        log.debug("Corruption check failed", exc_info=True)

                    # Empty-queue detection: RequestOperation can silently
                    # no-op, leaving cities with size==0 queues that block
                    # turn advancement. Name them in the blocker so the
                    # agent doesn't have to round-trip get_cities.
                    try:
                        empty_lines = await gs.conn.execute_write(
                            f"local me = Game.GetLocalPlayer(); "
                            f"local empty = {{}}; "
                            f"for i, c in Players[me]:GetCities():Members() do "
                            f"  local bq = c:GetBuildQueue(); "
                            f"  if bq:GetSize() == 0 then "
                            f'    table.insert(empty, Locale.Lookup(c:GetName()) .. " (id:" .. c:GetID() .. ")") '
                            f"  end "
                            f"end; "
                            f"if #empty > 0 then "
                            f'  print("EMPTY|" .. table.concat(empty, ", ")) '
                            f'else print("CLEAN") end; '
                            f'print("{lq.SENTINEL}")'
                        )
                        empty_cities = next(
                            (
                                el.split("|", 1)[1]
                                for el in empty_lines
                                if el.startswith("EMPTY|")
                            ),
                            None,
                        )
                        if empty_cities:
                            blocking_msg = (
                                f"Production — empty queue in {empty_cities}. "
                                f"Set production with set_city_production then "
                                f"retry end_turn."
                            )
                    except Exception:
                        log.debug("Empty-queue check failed", exc_info=True)

                    hard_blockers.append((blocking_type, blocking_msg))
                    continue

                # --- Stale research/civic notifications ---
                # If tech/civic is already set but the notification persists,
                # force-dismiss it (set_research may have been called but
                # the notification wasn't cleared — e.g. before MCP restart).
                if blocking_type in (
                    "ENDTURN_BLOCKING_RESEARCH",
                    "ENDTURN_BLOCKING_CIVIC",
                ):
                    try:
                        dismiss_lua = (
                            f"local me = Game.GetLocalPlayer() "
                            f"local pTechs = Players[me]:GetTechs() "
                            f"local pCulture = Players[me]:GetCulture() "
                            f"local researching = pTechs:GetResearchingTech() "
                            f"local civicing = pCulture:GetProgressingCivic() "
                            f"local isSet = false "
                            f'if "{blocking_type}" == "ENDTURN_BLOCKING_RESEARCH" and researching >= 0 then isSet = true end '
                            f'if "{blocking_type}" == "ENDTURN_BLOCKING_CIVIC" and civicing >= 0 then isSet = true end '
                            f"if isSet then "
                            f"  local list = NotificationManager.GetList(me) "
                            f"  if list then "
                            f"    for _, nid in ipairs(list) do "
                            f"      local e = NotificationManager.Find(me, nid) "
                            f"      if e and not e:IsDismissed() then "
                            f"        local bt = e:GetEndTurnBlocking() "
                            f"        if bt and bt == EndTurnBlockingTypes.{blocking_type} then "
                            f"          pcall(function() NotificationManager.SendActivated(me, nid) end) "
                            f"          pcall(function() NotificationManager.Dismiss(me, nid) end) "
                            f"        end "
                            f"      end "
                            f"    end "
                            f"  end "
                            f'  print("AUTO_CLEARED") '
                            f'else print("NOT_SET") end '
                            f'print("{lq.SENTINEL}")'
                        )
                        result_lines = await gs.conn.execute_mutation(dismiss_lua)
                        if any("AUTO_CLEARED" in l for l in result_lines):
                            resolved_any = True
                            continue
                    except Exception:
                        log.debug(
                            "Research/civic notification auto-clear failed",
                            exc_info=True,
                        )
                    # Research/civic was unset — add diagnostic hint
                    kind = "tech" if "RESEARCH" in blocking_type else "civic"
                    enhanced_msg = (
                        (
                            f"{blocking_msg} (no {kind} selected — "
                            f"this can happen after diplomacy events or tech completion)"
                        )
                        if blocking_msg
                        else (
                            f"No {kind} selected — "
                            f"this can happen after diplomacy events or tech completion"
                        )
                    )
                    hard_blockers.append((blocking_type, enhanced_msg))
                    continue

                # --- Stale promotion notifications ---
                # GameCore SetPromotion doesn't consume XP or advance level,
                # so CanPromote() perpetually returns TRUE. Use XP-threshold
                # formula (matching promote_unit's post-promote dismiss) to
                # determine if any unit genuinely has enough XP for another
                # promotion: needed = T1 * (promoCount+1) * (promoCount+2) / 2
                if blocking_type == "ENDTURN_BLOCKING_UNIT_PROMOTION":
                    try:
                        # Step 1 (GameCore): Check XP formula AND zero out stored
                        # promotions on units that don't genuinely need one.
                        # ChangeStoredPromotions zeroes the engine counter that
                        # causes the blocker to regenerate after Dismiss().
                        check_lines = await gs.conn.execute_mutation(
                            f"local me = Game.GetLocalPlayer(); "
                            f"local anyNeed = false; "
                            f"local cleared = 0; "
                            f"for i, u in Players[me]:GetUnits():Members() do "
                            f"  if u:GetX() ~= -9999 then "
                            f"    local ok, exp = pcall(function() return u:GetExperience() end); "
                            f"    if ok and exp then "
                            f"      local ui = GameInfo.Units[u:GetType()]; "
                            f'      local promClass = ui and ui.PromotionClass or ""; '
                            f'      if promClass ~= "" then '
                            f"        local promoCount = 0; "
                            f"        for p in GameInfo.UnitPromotions() do "
                            f"          if p.PromotionClass == promClass and exp:HasPromotion(p.Index) then "
                            f"            promoCount = promoCount + 1 "
                            f"          end "
                            f"        end; "
                            f"        local t1 = exp:GetExperienceForNextLevel(); "
                            f"        local xp = exp:GetExperiencePoints(); "
                            f"        local needed = t1 * (promoCount + 1) * (promoCount + 2) / 2; "
                            f"        if xp >= needed then "
                            f"          anyNeed = true "
                            f"        else "
                            f"          local stored = 0; "
                            f"          pcall(function() stored = exp:GetStoredPromotions() end); "
                            f"          if stored > 0 then "
                            f"            pcall(function() exp:ChangeStoredPromotions(-stored) end); "
                            f"            cleared = cleared + 1 "
                            f"          end "
                            f"        end "
                            f"      end "
                            f"    end "
                            f"  end "
                            f"end; "
                            f'print(anyNeed and "NEEDS_PROMO" or ("NO_PROMO_NEEDED|cleared=" .. cleared)); '
                            f'print("{lq.SENTINEL}")',
                            context="gamecore",
                        )
                        needs_promo = any(
                            "NEEDS_PROMO" == l.strip() for l in check_lines
                        )
                        log.debug(
                            "Promotion blocker: needs_promo=%s (check=%s)",
                            needs_promo,
                            [l for l in check_lines if "PROMO" in l or "cleared" in l],
                        )
                        if not needs_promo:
                            # Step 2: InGame dismiss — NotificationManager is InGame-only.
                            # Dismiss BOTH the end-turn blocker AND the regular notification
                            # (NOTIFICATION_UNIT_PROMOTION_AVAILABLE) which is a separate
                            # object that regenerates every turn due to stale CanPromote().
                            await gs.conn.execute_mutation(
                                f"local me = Game.GetLocalPlayer(); "
                                f"local list = NotificationManager.GetList(me); "
                                f"if list then "
                                f"  for _, nid in ipairs(list) do "
                                f"    local e = NotificationManager.Find(me, nid); "
                                f"    if e and not e:IsDismissed() then "
                                f"      local bt = e:GetEndTurnBlocking(); "
                                f"      if bt and bt == EndTurnBlockingTypes.ENDTURN_BLOCKING_UNIT_PROMOTION then "
                                f"        pcall(function() NotificationManager.SendActivated(me, nid) end); "
                                f"        pcall(function() NotificationManager.Dismiss(me, nid) end) "
                                f"      else "
                                f"        local tn = ''; "
                                f"        pcall(function() tn = e:GetTypeName() end); "
                                f"        if tn == 'NOTIFICATION_UNIT_PROMOTION_AVAILABLE' then "
                                f"          pcall(function() NotificationManager.Dismiss(me, nid) end) "
                                f"        end "
                                f"      end "
                                f"    end "
                                f"  end "
                                f"end; "
                                f'print("{lq.SENTINEL}")'
                            )
                            resolved_any = True
                            continue
                    except Exception:
                        log.debug(
                            "Promotion notification auto-clear failed", exc_info=True
                        )
                    hard_blockers.append((blocking_type, blocking_msg))
                    continue

                # --- Units blocking: auto-skip if all have 0 moves ---
                if blocking_type == "ENDTURN_BLOCKING_UNITS":
                    try:
                        check_lua = (
                            f"local me = Game.GetLocalPlayer(); "
                            f"local anyMoves = false; "
                            f"for _, u in Players[me]:GetUnits():Members() do "
                            f"  if u:GetX() ~= -9999 and u:GetMovesRemaining() > 0 then "
                            f"    anyMoves = true; break end end; "
                            f"if not anyMoves then "
                            f"  for _, u in Players[me]:GetUnits():Members() do "
                            f"    if u:GetX() ~= -9999 then UnitManager.FinishMoves(u) end "
                            f'  end; print("AUTO_SKIPPED") '
                            f'else print("UNITS_NEED_ORDERS") end; '
                            f'print("{lq.SENTINEL}")'
                        )
                        skip_lines = await gs.conn.execute_mutation(
                            check_lua, context="gamecore"
                        )
                        if any("AUTO_SKIPPED" in l for l in skip_lines):
                            resolved_any = True
                            continue
                    except Exception:
                        log.debug("Auto-skip 0-move units failed", exc_info=True)
                    hard_blockers.append((blocking_type, blocking_msg))
                    continue

                # --- Spy escape route: auto-pick fastest district ---
                if blocking_type == "ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE":
                    try:
                        escape_lines = await gs.conn.execute_mutation(
                            lq.build_spy_escape_route()
                        )
                        if any("OK:ESCAPE_ROUTE" in l for l in escape_lines):
                            log.info(
                                "Auto-resolved spy escape: %s",
                                next(
                                    (l for l in escape_lines if "OK:" in l),
                                    "",
                                ),
                            )
                            resolved_any = True
                            continue
                    except Exception:
                        log.debug("Spy escape auto-resolve failed", exc_info=True)
                    hard_blockers.append((blocking_type, blocking_msg))
                    continue

                # --- Unrecognized blocker → always hard ---
                hard_blockers.append((blocking_type, blocking_msg))

            # If we have hard blockers, check if turn advanced externally
            # (e.g. game auto-end-turn after skip_remaining_units)
            if hard_blockers:
                turn_now = await _get_turn_number(gs)
                if (
                    turn_now is not None
                    and turn_at_entry is not None
                    and turn_now > turn_at_entry
                ):
                    log.info(
                        "Turn advanced externally (%s -> %s), skipping blocker report",
                        turn_at_entry,
                        turn_now,
                    )
                    break  # fall through to snapshot/diff flow

                # Ask the game if turn can actually end despite our blockers.
                # Safe here — we haven't started AI processing yet (pre-end-turn phase).
                if _can_override_end_turn_blockers(hard_blockers):
                    try:
                        can_end_lines = await gs.conn.execute_write(
                            f"local can = UI.CanEndTurn(); "
                            f'print(can and "CAN_END" or "CANNOT_END"); '
                            f'print("{lq.SENTINEL}")'
                        )
                        if any(l == "CAN_END" for l in can_end_lines):
                            log.info(
                                "UI.CanEndTurn()=true despite blockers %s — proceeding",
                                [bt for bt, _ in hard_blockers],
                            )
                            break  # fall through to end_turn request
                    except Exception:
                        log.debug("UI.CanEndTurn check failed", exc_info=True)
                else:
                    log.info(
                        "Refusing UI.CanEndTurn() override because unit blockers remain: %s",
                        [bt for bt, _ in hard_blockers],
                    )

                lines_out: list[str] = ["Cannot end turn — resolve these blockers:"]
                for bt, bm in hard_blockers:
                    hint = lq.BLOCKING_TOOL_MAP.get(
                        bt, "Resolve the blocking notification"
                    )
                    display = (
                        bt.replace("ENDTURN_BLOCKING_", "").replace("_", " ").title()
                    )
                    line = f"  - {display}"
                    if bm:
                        line += f" ({bm})"
                    line += f"  ->  {hint}"
                    lines_out.append(line)
                return "\n".join(lines_out)

            # All blockers were soft-resolved — loop to re-check
            if resolved_any:
                continue
            break  # no blockers left
        except Exception:
            log.debug("Blocking check failed, proceeding anyway", exc_info=True)
            break

    # Take pre-turn snapshot.
    # When re-entering after mid-turn diplomacy (_pending_end_turn=True),
    # the turn may have already advanced. Use the previous call's snapshot
    # as the baseline so the diff captures what changed across the turn.
    if gs._pending_end_turn and gs._last_snapshot is not None:
        snap_before = gs._last_snapshot
        log.debug(
            "Using previous snapshot (turn %s) as baseline for pending end-turn",
            snap_before.turn,
        )
    else:
        try:
            snap_before = await gs._take_snapshot()
        except Exception:
            log.debug("Pre-turn snapshot failed", exc_info=True)
            snap_before = gs._last_snapshot

    # Pre-turn threat scan (for fog-of-war direction tracking)
    threats_before: list[lq.ThreatInfo] = []
    try:
        pre_threat_lines = await gs.conn.execute_read(lq.build_threat_scan_query())
        threats_before = lq.parse_threat_scan_response(pre_threat_lines)
    except Exception:
        log.debug("Pre-turn threat scan failed", exc_info=True)

    turn_before = snap_before.turn if snap_before else await _get_turn_number(gs)

    if _world_changed_while_waiting(gs, entry_epoch):
        return _RELOAD_INTERRUPTED

    # Reserve the logical operation before the first await that may send it.
    # Missing acknowledgements and cancellation never prove it was not accepted.
    gs._pending_end_turn = True
    gs._pending_end_turn_from = turn_before
    gs._pending_end_turn_started = _monotonic()
    gs._pending_end_turn_wait = 0.0
    gs._pending_wc_turn = wc_turn
    gs._pending_world_epoch = entry_epoch
    gs._pending_snap_before = snap_before
    gs._pending_threats_before = threats_before
    gs._wc_driven = False
    gs._wc_drive_uncertain = False
    gs._wc_drives = 0
    gs._wc_dismissals = 0
    gs.conn.turn_in_progress = True
    try:
        await gs.conn.execute_mutation(lq.build_end_turn(), turn_action="end_turn", expected_world_epoch=gs._pending_world_epoch)
    except CommandNotSentError:
        # This transport classification is emitted only before a command write.
        gs._pending_end_turn = False
        gs._pending_end_turn_from = None
        gs._pending_end_turn_started = None
        gs.conn.turn_in_progress = False
        raise
    return await _observe_pending_end_turn(gs, receipt)


def _world_changed_while_waiting(gs: GameState, epoch: int) -> bool:
    return (
        getattr(gs, "_reload_uncertain", False)
        or getattr(gs.conn, "reload_pending", False)
        or getattr(gs, "_world_epoch", 0) != epoch
    )


_RELOAD_INTERRUPTED = "UNKNOWN:RELOAD_PENDING|等待期间发生读档；原回合推进结果不可用于新局面。"


async def _observe_pending_end_turn(gs: GameState, receipt: _TurnReceipt) -> str:
    """Continue one request without preflight, generic popups, or another send."""
    epoch = getattr(gs, "_pending_world_epoch", getattr(gs, "_world_epoch", 0))
    turn_before = gs._pending_end_turn_from
    snap_before = getattr(gs, "_pending_snap_before", gs._last_snapshot)
    threats_before = getattr(gs, "_pending_threats_before", [])
    wc_turn = bool(getattr(gs, "_pending_wc_turn", False))
    lua = lq.build_end_turn()
    # Poll for turn advancement using GameCore-only queries.
    # CRITICAL: Do NOT send InGame queries while AI civs are processing
    # their turns.  InGame queries (diplomacy sessions, UI.CanEndTurn,
    # popup dismissal) force context switches that can stall the AI
    # diplomacy subsystem, causing infinite hangs (seen in Games 1-5).
    turn_after = None
    advanced = False

    # Waiting spans calls, not just this one: a call polls for a bounded window
    # and then reports, and calling end_turn again while a request is in flight
    # keeps polling without re-sending ACTION_ENDTURN. So elapsed waiting and
    # congress drive progress are carried on the game state, not on the stack.
    pass_start_wait = _pending_elapsed(gs)
    wc_driven = bool(getattr(gs, "_wc_driven", False))
    cumulative_wait = pass_start_wait
    wc_drives = int(getattr(gs, "_wc_drives", 0))

    # Phase 1: Quick check (4s) — turn sometimes advances within 1-2s
    for _ in range(8):
        await _sleep(0.5)
        cumulative_wait = _pending_elapsed(gs)
        turn_after = await _get_turn_number(gs)
        if _world_changed_while_waiting(gs, epoch):
            return _RELOAD_INTERRUPTED
        if (
            turn_after is not None
            and turn_before is not None
            and turn_after > turn_before
        ):
            advanced = True
            break

    # Phase 2: bounded polling. GameCore-only queries.
    if not advanced:
        diplomacy_probed = False
        for delay in _end_turn_poll_delays(wc_turn):
            cumulative_wait = _pending_elapsed(gs)
            if cumulative_wait - pass_start_wait >= _END_TURN_POLL_WINDOW_SECONDS:
                # This call has spent its window. Report rather than keep the
                # host call open; the caller can continue the wait cheaply.
                log.info(
                    "end_turn window reached (t+%.0fs this call, %.0fs total on T%s)",
                    cumulative_wait - pass_start_wait,
                    cumulative_wait,
                    turn_before,
                )
                break
            await _sleep(delay)
            cumulative_wait = _pending_elapsed(gs)
            turn_after = await _get_turn_number(gs)
            if _world_changed_while_waiting(gs, epoch):
                return _RELOAD_INTERRUPTED
            if (
                turn_after is not None
                and turn_before is not None
                and turn_after > turn_before
            ):
                advanced = True
                break
            cumulative_wait = _pending_elapsed(gs)
            # Check for game-over during longer polling intervals.
            # An opponent victory (Science, Culture, etc.) fires during
            # their turn — without this we'd wait the full 9-min timeout.
            if delay >= 10.0:
                gameover = await gs.check_game_over()
                if _world_changed_while_waiting(gs, epoch):
                    return _RELOAD_INTERRUPTED
                if gameover is not None:
                    return _game_over_message(gs, gameover)
            # Bounded named diplomacy probe. Silence alone does not prove the
            # engine is idle; only a confirmed session permits a response.
            if not diplomacy_probed and cumulative_wait >= 45:
                diplomacy_probed = True
                diplo_msg, diplo_advanced = await _check_mid_turn_diplomacy(
                    gs, lua, turn_before, expected_world_epoch=epoch
                )
                if diplo_msg is not None:
                    return diplo_msg
                if diplo_advanced:
                    turn_after = await _get_turn_number(gs)
                    if _world_changed_while_waiting(gs, epoch):
                        return _RELOAD_INTERRUPTED
                    advanced = turn_after is not None and turn_before is not None and turn_after > turn_before
                    if advanced:
                        break
            # World Congress turns park on a congress screen. The session opens
            # inside ACTION_ENDTURN, so nobody else can vote it: we drive it
            # here — vote the live resolutions and submit — which is what a
            # human would do and is orders of magnitude faster than waiting the
            # screen out. Tried from t+5s and kept up across the whole opening
            # window, because only driving makes the turn advance.
            if not wc_driven and not getattr(gs, "_wc_drive_uncertain", False) and _wc_drive_due(
                wc_turn=wc_turn,
                cumulative_wait=cumulative_wait,
                drives=wc_drives,
            ):
                wc_drives += 1
                gs._wc_drives = wc_drives
                if await _drive_congress(gs, expected_world_epoch=epoch):
                    wc_driven = True
                    gs._wc_driven = True
                    log.info(
                        "World Congress drive %d submitted (t+%.0fs)",
                        wc_drives,
                        cumulative_wait,
                    )
                    await _sleep(2.0)
                    turn_after = await _get_turn_number(gs)
                    if _world_changed_while_waiting(gs, epoch):
                        return _RELOAD_INTERRUPTED
                    if (
                        turn_after is not None
                        and turn_before is not None
                        and turn_after > turn_before
                    ):
                        advanced = True
                        break
    # Carry this pass's waiting forward so the next call continues the schedule
    # instead of restarting it (and, on a congress turn, re-driving from zero).
    cumulative_wait = _pending_elapsed(gs)

    # A deadline is not proof that the AI is idle. Only the bounded, named
    # diplomacy input probe is allowed; generic UI reads/dismissals stay off.
    if not advanced:
        # Check for AI diplomatic proposals (reuses the same helper
        # as the early Phase 2 probe — Phase 3 is the fallback if the
        # probe didn't fire or missed the diplomacy window).
        diplo_msg, diplo_advanced = await _check_mid_turn_diplomacy(
            gs, lua, turn_before, expected_world_epoch=epoch
        )
        if diplo_msg is not None:
            return diplo_msg
        if diplo_advanced:
            turn_after = await _get_turn_number(gs)
            if _world_changed_while_waiting(gs, epoch):
                return _RELOAD_INTERRUPTED
            advanced = turn_after is not None and turn_before is not None and turn_after > turn_before

    if not advanced:
        # Final verification — turn may have slipped through
        await _sleep(2.0)
        turn_after = await _get_turn_number(gs)
        if _world_changed_while_waiting(gs, epoch):
            return _RELOAD_INTERRUPTED
        if (
            turn_after is not None
            and turn_before is not None
            and turn_after > turn_before
        ):
            advanced = True

    if not advanced:
        # Check if game ended during turn transition (victory/defeat)
        gameover = await gs.check_game_over()
        if _world_changed_while_waiting(gs, epoch):
            return _RELOAD_INTERRUPTED
        if gameover is not None:
            return _game_over_message(gs, gameover)

        # Expected congress timing is not evidence of a currently open session.
        # no_session/failed probes exhaust their finite schedule and eventually
        # yield the same diagnostic deadline as any other unconfirmed request.
        turn_num = turn_after or turn_before
        cumulative_wait = _pending_elapsed(gs)
        # No blockers, no diplomacy, no game over. Whether this is a hang or
        # merely a turn that is still being played out is decided by how long the
        # *same* pending turn has been waited on in total, not by how long one
        # call has been open: measured turns are median 15s / p90 26s, so a
        # single call's window says nothing about whether the game is stuck.
        if cumulative_wait < PENDING_TURN_HANG_AFTER_SECONDS:
            log.info(
                "Turn T%s still processing after %.0fs total; reporting pending",
                turn_num,
                cumulative_wait,
            )
            # The request is still parked in the game: keep the flag and the
            # accumulated wait so the next call continues instead of restarting.
            return (
                f"TURN_PENDING:{turn_num}|"
                f"尚未确认回合推进（距本次逻辑请求提交已过 {cumulative_wait:.0f} 秒）。\n"
                "再次调用 end_turn 即可继续等待，"
                "重复调用不会重发结束回合请求。\n"
                "若同时有需要决策的通知或阻塞项，先处理它们。\n"
                "TURN_PENDING_KEEP_WAITING"
            )
        # The diagnostic threshold is not proof of a dead engine.
        # Return structured HANG: prefix so the caller can start the explicit
        # recovery step. Recovery is deliberately NOT attempted inside this call.
        if turn_num is not None:
            from .autosave import get_autosave_for_turn

            hang_save = get_autosave_for_turn(turn_num)
            return (
                f"HANG:{turn_num}:{hang_save}|"
                f"End turn requested (turn is still {turn_num}) after "
                f"{cumulative_wait:.0f}s. 等待达到诊断阈值，尚未确认推进。"
                + (" 预计议会到期，但尚未确认会话或投票提交。" if wc_turn and not wc_driven else "")
                + " 原请求仍可能在途；不得重新提交或据此自动重启。"
            )
        return "UNKNOWN:END_TURN_UNCONFIRMED|无法确认回合号；保留原请求，暂停提交并检查连接。"

    receipt.confirm(f"Turn {turn_before} -> {turn_after}")

    # Turn advanced — clear the pending flag and the carried waiting state, so
    # the next turn starts its own window and its own drive schedule.
    gs._pending_end_turn = False
    gs._pending_end_turn_from = None
    gs._pending_end_turn_wait = 0.0
    gs._pending_end_turn_started = None
    gs._pending_wc_turn = False
    gs._wc_driven = False
    gs._wc_drive_uncertain = False
    gs._wc_drives = 0
    gs._wc_dismissals = 0
    gs.conn.turn_in_progress = False

    # Turn regression detection — catch accidental wrong-save loads
    if turn_after is not None and gs._high_water_turn > 0:
        if turn_after < gs._high_water_turn - 1:
            from .autosave import get_autosave_for_turn

            latest_autosave = get_autosave_for_turn(gs._high_water_turn)
            log.warning(
                "Turn regressed from %d to %d — possible wrong save loaded",
                gs._high_water_turn,
                turn_after,
            )
            return (
                f"CRITICAL: Turn regressed from {gs._high_water_turn} to {turn_after}. "
                f"You may have loaded the wrong save file. "
                f"Your most recent MCP autosave is {latest_autosave}. "
                f'Use load_game_save("{latest_autosave}") to recover.'
            )
    if turn_after is not None:
        # Reset per-turn counters only on TRUE advance. Blocker turns have
        # turn_after == turn_before, so the counter must NOT reset — this
        # prevents the agent from advisor-spamming between blocker retries
        # within a single game turn.
        if turn_after > gs._high_water_turn:
            gs._advisor_calls_this_turn = 0
        gs._high_water_turn = max(gs._high_water_turn, turn_after)

    # Post-advance game-over check — victory can trigger during the turn
    # transition (e.g. science vessel arriving, diplo VP threshold).
    # Must check here so "GAME OVER" appears in result for log_game_over.
    gameover = await gs.check_game_over()
    if _world_changed_while_waiting(gs, epoch):
        return f"Turn {turn_before} -> {turn_after}\nBRIEF_PENDING|推进已确认，后处理期间局面改变。"
    if gameover is not None:
        gs._last_game_over = gameover
        vtype = gameover.victory_type.replace("VICTORY_", "").replace("_", " ").title()
        if gameover.is_defeat:
            return (
                f"Turn {turn_before} -> {turn_after}\n"
                f"GAME OVER — DEFEAT. {gameover.winner_leader} of {gameover.winner_name} won a {vtype} victory. "
                f"The game has ended. No further actions are possible."
            )
        else:
            return (
                f"Turn {turn_before} -> {turn_after}\n"
                f"GAME OVER — VICTORY! You won a {vtype} victory! The game has ended."
            )

    # Take post-turn snapshot and diff
    snap_after = None
    try:
        snap_after = await gs._take_snapshot()
        if _world_changed_while_waiting(gs, epoch):
            return f"Turn {turn_before} -> {turn_after}\nBRIEF_PENDING|推进已确认，快照期间局面改变。"
        gs._last_snapshot = snap_after
    except Exception:
        log.warning("Post-turn snapshot failed — events will be limited", exc_info=True)

    # MCP per-turn autosave — fire-and-forget after successful turn advance.
    # On Linux (Aspyr port), Network.SaveGame silently fails for custom names.
    # We rely on the game's own AutoSave_NNNN instead.
    from .autosave import saves_work_on_this_platform

    if turn_after is not None and saves_work_on_this_platform():
        try:
            await save_game(gs.conn, f"0_MCP_{turn_after:04d}")
            cleanup_old_autosaves(keep=8)
        except Exception:
            log.debug("MCP autosave failed for T%s", turn_after, exc_info=True)

    events: list[lq.TurnEvent] = []
    if snap_before and snap_after:
        events = gs._diff_snapshots(snap_before, snap_after)

    # Query active notifications
    notifications: list[lq.GameNotification] = []
    try:
        notif_lines = await gs.conn.execute_write(lq.build_notifications_query())
        notifications = gs._arbitrate_notifications(
            lq.parse_notifications_response(notif_lines)
        )
    except Exception:
        log.debug("Notification query failed", exc_info=True)

    # Check for pending trade deals (AI may propose during their turn)
    try:
        deals = await gs.get_pending_deals()
        if deals:
            events.append(
                lq.TurnEvent(
                    priority=2,
                    category="diplomacy",
                    message=nr.narrate_pending_deals(deals),
                )
            )
    except Exception:
        log.debug("Trade deal check failed", exc_info=True)

    # Threat scan — check for hostile units near cities
    threats: list[lq.ThreatInfo] = []
    try:
        threat_lines = await gs.conn.execute_read(lq.build_threat_scan_query())
        threats = lq.parse_threat_scan_response(threat_lines)
        for t in threats:
            rs_str = f" RS:{t.ranged_strength}" if t.ranged_strength > 0 else ""
            events.append(
                lq.TurnEvent(
                    priority=2,
                    category="unit",
                    message=f"THREAT: {t.owner_name} {t.unit_type} CS:{t.combat_strength}{rs_str} HP:{t.hp}/{t.max_hp} spotted {t.distance} tiles away at ({t.x},{t.y})",
                )
            )
    except Exception:
        log.debug("Threat scan failed", exc_info=True)

    # Barbarian camps are the source of repeated spawns and are not covered by
    # the generic hostile-unit scan. Keep this as a post-advance alert rather
    # than a hard blocker: a camp may be revealed without a reachable or
    # healthy combat unit, and end_turn must remain recoverable in that case.
    try:
        barbarian_overview = await gs.get_barbarian_overview()
        if barbarian_overview.camps or barbarian_overview.units:
            camp_priority = (
                1
                if any(camp.distance_to_city <= 10 for camp in barbarian_overview.camps)
                else 2
            )
            events.append(
                lq.TurnEvent(
                    priority=camp_priority,
                    category="barbarian",
                    message=nr.narrate_barbarian_overview(
                        barbarian_overview, compact=True
                    ),
                )
            )
    except Exception:
        log.debug("Barbarian camp scan failed after turn advance", exc_info=True)

    # Fog-of-war direction tracking — diff pre/post threats
    if threats_before:
        try:
            disappeared, _, _ = lq.diff_threats(threats_before, threats)
            if disappeared:
                positions = [(t.x, t.y) for t in disappeared]
                fog_lines = await gs.conn.execute_read(
                    lq.build_fog_neighbor_query(positions)
                )
                fog_dirs = lq.parse_fog_neighbor_response(fog_lines)
                for t in disappeared:
                    dirs = fog_dirs.get((t.x, t.y), [])
                    if dirs:
                        dir_str = "/".join(dirs)
                        msg = (
                            f"LOST CONTACT: {t.owner_name} {t.unit_type} "
                            f"HP:{t.hp}/{t.max_hp} last seen at ({t.x},{t.y}) "
                            f"— likely moved {dir_str} into fog"
                        )
                    else:
                        msg = (
                            f"VANISHED: {t.owner_name} {t.unit_type} "
                            f"HP:{t.hp}/{t.max_hp} last at ({t.x},{t.y}) "
                            f"— no adjacent fog (killed or garrisoned?)"
                        )
                    events.append(
                        lq.TurnEvent(priority=1, category="unit", message=msg)
                    )
        except Exception:
            log.debug("Fog direction tracking failed", exc_info=True)

    events.sort(key=lambda e: e.priority)

    # Victory proximity check (every turn — lightweight)
    try:
        victory_events = await _check_victory_proximity(gs)
        events.extend(victory_events)
    except Exception:
        log.warning("Victory proximity check failed", exc_info=True)

    # Every 10 turns: full victory progress snapshot
    if turn_after is not None and turn_after % 10 == 0:
        try:
            vp = await gs.get_victory_progress()
            summary = nr.narrate_victory_progress(vp)
            events.append(
                lq.TurnEvent(
                    priority=3,
                    category="victory",
                    message=f"10-TURN VICTORY SNAPSHOT (T{turn_after}):\n{summary}",
                )
            )
        except Exception:
            log.debug("10-turn victory check failed", exc_info=True)

    # Growth alerts from post-turn city state
    if snap_after:
        for cs in snap_after.cities.values():
            if cs.food_surplus < 0:
                events.append(
                    lq.TurnEvent(
                        priority=1,
                        category="city",
                        message=f"STARVING: {cs.name} ({cs.food_surplus:+.1f} food/t) — will lose population!",
                    )
                )
            elif cs.food_surplus == 0 and cs.turns_to_grow <= 0:
                events.append(
                    lq.TurnEvent(
                        priority=2,
                        category="city",
                        message=f"STAGNANT: {cs.name} (0 food surplus) — needs farm, granary, or trade route",
                    )
                )
            elif cs.turns_to_grow > 15:
                events.append(
                    lq.TurnEvent(
                        priority=3,
                        category="city",
                        message=f"SLOW GROWTH: {cs.name} ({cs.turns_to_grow}t to next pop, {cs.food_surplus:+.1f}/t)",
                    )
                )

    # Empire-wide warnings (scoreboard, idle trade, loyalty, military, gold)
    game_score = None
    try:
        warning_events, game_score = await _check_empire_warnings(gs, snap_after)
        events.extend(warning_events)
    except Exception:
        log.debug("Empire warnings failed", exc_info=True)

    # Save scumming detection
    try:
        scum_events, hard_stop = _check_save_scumming(gs)
        events.extend(scum_events)
        if hard_stop:
            gs._run_aborted = True
    except Exception:
        log.debug("Save scumming check failed", exc_info=True)

    events.sort(key=lambda e: e.priority)
    return gs._build_turn_report(
        turn_before,
        turn_after,
        events,
        notifications,
        stockpiles=snap_after.stockpiles if snap_after else None,
        score=game_score,
    )
