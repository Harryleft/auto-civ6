"""End-turn execution: blockers, one request, bounded confirmation, and events."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import civ_mcp.narrate as nr
from civ_mcp import lua as lq
from civ_mcp.connection import LuaError

if TYPE_CHECKING:
    from civ_mcp.game_state import GameState

log = logging.getLogger(__name__)

END_TURN_TIMEOUT_SECONDS = 180.0
POLL_INTERVAL_SECONDS = 2.0
DIPLOMACY_PROBE_SECONDS = 10.0


async def read_turn_state(gs: GameState) -> tuple[int | None, bool | None]:
    """Read engine turn readiness without switching UI while AI is processing.

    The installed PiratesScenario gameplay uses IsTurnActive; ActionPanel uses
    IsTurnActiveComplete before enabling the next-turn controls. Missing APIs
    stay unknown, so platform differences cannot silently authorize actions.
    """
    try:
        lines = await gs.conn.execute_read(f"""
local me = Game.GetLocalPlayer()
local p = Players[me]
local turn = Game.GetCurrentGameTurn()
local ok, ready = pcall(function()
    return p ~= nil and p:IsTurnActive() and p:IsTurnActiveComplete()
end)
print("TURN_STATE|" .. tostring(turn) .. "|" .. (ok and (ready and "1" or "0") or "?"))
print("{lq.SENTINEL}")
""")
        for line in lines:
            fields = line.split("|")
            if len(fields) == 3 and fields[0] == "TURN_STATE":
                turn = int(fields[1])
                ready = {"1": True, "0": False}.get(fields[2])
                if turn < 0:
                    return None, None
                if ready is not True:
                    return turn, ready
                # ActionPanel also waits for queued UI/game messages to drain.
                # Only enter InGame after the engine reports the local turn
                # active and complete, never on each AI-processing poll.
                ui_lines = await gs.conn.execute_write(f"""
local ok, processing = pcall(UI.IsProcessingMessages)
print("TURN_UI_READY|" .. tostring(Game.GetCurrentGameTurn()) .. "|" .. (ok and (not processing and "1" or "0") or "?"))
print("{lq.SENTINEL}")
""")
                for ui_line in ui_lines:
                    ui_fields = ui_line.split("|")
                    if len(ui_fields) == 3 and ui_fields[0] == "TURN_UI_READY":
                        if int(ui_fields[1]) != turn:
                            return turn, None
                        return turn, {"1": True, "0": False}.get(ui_fields[2])
                return turn, None
    except (ConnectionError, TimeoutError, LuaError, ValueError, IndexError):
        pass
    return None, None


def _game_over_result(gs: GameState, gameover: lq.GameOverStatus) -> str:
    gs._pending_end_turn = False
    gs._pending_end_turn_from = None
    victory = gameover.victory_type.replace("VICTORY_", "").replace("_", " ").title()
    if gameover.is_defeat:
        return (
            f"GAME OVER — DEFEAT. {gameover.winner_leader} of "
            f"{gameover.winner_name} won a {victory} victory."
        )
    return f"GAME OVER — VICTORY! You won a {victory} victory!"


async def _check_mid_turn_diplomacy(
    gs: GameState, lua: str, turn_before: int | None,
) -> tuple[str | None, bool]:
    """Report a paused encounter; leave its response and original request intact.

    The signature is retained for callers that previously shared this probe.
    Nothing here reissues ACTION_ENDTURN or chooses a diplomatic response.
    """
    # Civ6Common.lua uses these pure session lookups. Avoid the full dialogue
    # query's ContextPtr/UI control traversal while other players are active.
    # InGame context availability still needs platform-specific live validation.
    observed = await gs.conn.execute_write(f"""
-- DIPLOMACY_PROBE: read-only, no session response or UI control traversal
local me = Game.GetLocalPlayer()
local found = false
for i = 0, 62 do
    if i ~= me and Players[i] and Players[i]:IsAlive() then
        local sid = DiplomacyManager.FindOpenSessionID(me, i)
        if sid and sid >= 0 then
            print("DIPLOMACY|" .. i)
            found = true
        end
    end
end
if not found then
    print(DiplomacyManager.HasQueuedSession(me) and "DIPLOMACY_QUEUED" or "DIPLOMACY_NONE")
end
print("{lq.SENTINEL}")
""")
    players = []
    for line in observed:
        if line.startswith("DIPLOMACY|"):
            players.append(int(line.split("|", 1)[1]))
    queued = "DIPLOMACY_QUEUED" in observed
    if not players and not queued:
        if "DIPLOMACY_NONE" not in observed:
            raise LuaError("Diplomacy probe returned no valid status")
        return None, False
    lines = ["Turn paused — diplomacy response required:"]
    lines.extend(f"  - player_id={player}" for player in players)
    if queued:
        lines.append("  - A diplomacy session is queued; inspect pending diplomacy.")
    lines.append(
        "Use get_pending_diplomacy/respond_to_diplomacy or "
        "get_pending_trades/respond_to_trade. Then call end_turn to confirm "
        "the existing request; it will not be sent again."
    )
    return "\n".join(lines), False


async def _check_blockers(gs: GameState) -> str | None:
    """Check actual game prerequisites; never make strategy choices for the player."""
    sessions = await gs.get_diplomacy_sessions()
    if sessions:
        names = ", ".join(s.other_civ_name for s in sessions)
        return (
            f"Cannot end turn: diplomacy encounter pending with {names}. "
            "Use get_pending_diplomacy then respond_to_diplomacy."
        )
    deals = await gs.get_pending_deals()
    if deals:
        return "Cannot end turn: incoming trade deal pending.\n" + nr.narrate_pending_deals(deals)

    wc = await gs.get_world_congress()
    if wc.is_in_session or (wc.turns_until_next == 0 and wc.resolutions):
        handler = await gs.conn.execute_write(
            f'print(__civmcp_wc_handler and "HANDLER_SET" or "NO_HANDLER"); '
            f'print("{lq.SENTINEL}")'
        )
        if "HANDLER_SET" not in handler:
            return (
                "Cannot end turn: World Congress requires votes. "
                "Use get_world_congress then queue_wc_votes before end_turn."
            )

    lines = await gs.conn.execute_write(lq.build_end_turn_blocking_query())
    # The parser's empty list is also used for malformed responses. Only an
    # explicit NONE is evidence that no notifications block the turn.
    if not any(line == "NONE" or line.startswith("BLOCKING|") for line in lines):
        return "Cannot confirm end-turn blockers: game query returned no valid status. Read game state before retrying."
    blockers = lq.parse_end_turn_blocking(lines)
    if not blockers:
        return None
    result = ["Cannot end turn — resolve these blockers:"]
    for blocking_type, message in blockers:
        display = blocking_type.removeprefix("ENDTURN_BLOCKING_").replace("_", " ").title()
        hint = lq.BLOCKING_TOOL_MAP.get(blocking_type, "Inspect the blocking notification")
        result.append(f"  - {display}: {message} -> {hint}")
    return "\n".join(result)


async def _report_advance(gs: GameState, before: int, after: int) -> str:
    """Capture factual changes without evaluations, automatic saves, or advice."""
    gs._pending_end_turn = False
    gs._pending_end_turn_from = None
    try:
        gameover = await gs.check_game_over()
        if gameover is not None:
            return f"Turn {before} -> {after}\n" + _game_over_result(gs, gameover)
    except (ConnectionError, TimeoutError, LuaError):
        log.debug("Post-turn game-over check failed", exc_info=True)

    previous = gs._last_snapshot
    current = None
    limited: list[str] = []
    try:
        current = await gs._take_snapshot()
        gs._last_snapshot = current
    except (ConnectionError, TimeoutError, LuaError, ValueError):
        limited.append("Post-turn snapshot unavailable; read game state to confirm changes.")
    events = gs._diff_snapshots(previous, current) if previous and current else []
    notifications: list[lq.GameNotification] = []
    try:
        lines = await gs.conn.execute_write(lq.build_notifications_query())
        if not any(line.startswith("TOTAL|") for line in lines):
            limited.append("Notifications unavailable.")
        else:
            notifications = lq.parse_notifications_response(lines)
    except (ConnectionError, TimeoutError, LuaError, ValueError):
        limited.append("Notifications unavailable.")
    report = gs._build_turn_report(
        before, after, events, notifications,
        stockpiles=current.stockpiles if current else None,
    )
    return "\n".join([report, *limited])


async def execute_end_turn(gs: GameState) -> str:
    """Send at most one request and preserve uncertainty until advancement is seen."""
    if gs.pending_action_summary:
        confirmation = await gs.confirm_pending_action()
        if gs.pending_action_summary:
            return (
                f"ERR:OUTCOME_UNKNOWN|Cannot end turn while {gs.pending_action_summary}. "
                f"{confirmation} Read the affected unit/city before continuing."
            )
    if not gs._pending_end_turn:
        try:
            gameover = await gs.check_game_over()
            if gameover is not None:
                return _game_over_result(gs, gameover)
            turn_before, ready = await read_turn_state(gs)
            if turn_before is None:
                return "Cannot end turn: current turn is unknown. Read game state before retrying."
            if ready is not True:
                return "Cannot end turn: local player is not ready or readiness is unknown. Read game state before retrying."
            blocked = await _check_blockers(gs)
            if blocked:
                return blocked
            # A missing snapshot limits the report, but must not hide whether
            # the request was sent. Store the baseline before writing it.
            try:
                gs._last_snapshot = await gs._take_snapshot()
            except (ConnectionError, TimeoutError, LuaError, ValueError):
                gs._last_snapshot = None
            # Queries can race an external UI action. Never send from an old
            # turn after taking its snapshot and inspecting its notifications.
            current, ready = await read_turn_state(gs)
            if current is None:
                return "Cannot end turn: current turn cannot be confirmed. Read game state before retrying."
            if current > turn_before:
                # An external UI request advanced the game during inspection.
                # Confirm its readiness below without sending another request.
                gs._pending_end_turn = True
                gs._pending_end_turn_from = turn_before
            elif current != turn_before:
                return "Cannot end turn: game turn changed during inspection. Read game state before retrying."
            elif ready is not True:
                return "Cannot end turn: local player is not ready or readiness is unknown. Read game state before retrying."
        except (ConnectionError, TimeoutError, LuaError, ValueError) as exc:
            return f"Cannot confirm end-turn prerequisites: {exc}. Read game state before retrying."

        if not gs._pending_end_turn:
            gs._pending_end_turn = True
            gs._pending_end_turn_from = turn_before
            try:
                await gs.conn.execute_write(lq.build_end_turn())
            except (ConnectionError, TimeoutError, LuaError) as exc:
                return (
                    f"End-turn result unknown (from turn {turn_before}): {exc}. "
                    "The request may have executed. Read game state or call end_turn "
                    "to confirm; it will not be sent again. Use explicit recovery if needed."
                )
    else:
        turn_before = gs._pending_end_turn_from
        if turn_before is None:
            return "End-turn result unknown: request has no baseline. Read game state or use explicit recovery; no request was resent."

    started = time.monotonic()
    deadline = started + END_TURN_TIMEOUT_SECONDS
    next_diplomacy_probe = started + DIPLOMACY_PROBE_SECONDS
    turn_after = None
    ready = None
    reads_failed = False
    next_game_over_check = started
    try:
        # Deadline covers both polling and bounded read-only session probes. Query
        # duration counts toward the budget, not only the sleep intervals.
        async with asyncio.timeout(END_TURN_TIMEOUT_SECONDS):
            while time.monotonic() < deadline:
                # A victory/elimination can finish AI processing without
                # advancing the counter. Keep this probe in GameCore so a
                # pending request never switches into the InGame UI to check.
                if time.monotonic() >= next_game_over_check:
                    next_game_over_check = time.monotonic() + 10.0
                    try:
                        status = await gs.conn.execute_read(lq.build_gameover_check_gamecore())
                        if not any(line == "GAME_ACTIVE" or line.startswith("GAME_OVER|") for line in status):
                            reads_failed = True
                        else:
                            gameover = lq.parse_gameover_response(status)
                            if gameover is not None:
                                return _game_over_result(gs, gameover)
                    except (ConnectionError, TimeoutError, LuaError, ValueError):
                        reads_failed = True
                turn_after, ready = await read_turn_state(gs)
                if turn_after is None:
                    reads_failed = True
                elif turn_after > turn_before and ready is True:
                    break
                elif turn_after < turn_before:
                    return (
                        f"End-turn result unknown: turn changed from {turn_before} "
                        f"to {turn_after}. Read game state or use explicit recovery; "
                        "the pending request was not resent."
                    )
                if time.monotonic() >= next_diplomacy_probe:
                    next_diplomacy_probe = time.monotonic() + DIPLOMACY_PROBE_SECONDS
                    try:
                        message, _ = await _check_mid_turn_diplomacy(gs, "", turn_before)
                        if message:
                            return message
                    except (ConnectionError, TimeoutError, LuaError, ValueError):
                        reads_failed = True
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(min(POLL_INTERVAL_SECONDS, remaining))
    except TimeoutError:
        reads_failed = True
    if turn_after is not None and turn_after > turn_before and ready is True:
        return await _report_advance(gs, turn_before, turn_after)
    detail = " Some game queries failed; the current turn cannot be reliably confirmed." if reads_failed else ""
    return (
        f"HANG: end-turn result unknown after {END_TURN_TIMEOUT_SECONDS:g}s "
        f"(requested from turn {turn_before}, last observed turn {turn_after})."
        f"{detail} Read game state, inspect diplomacy/popups, or use explicit recovery. "
        "The pending request is preserved; another end_turn call will only confirm it."
    )
