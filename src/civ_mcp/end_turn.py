"""End-turn execution: blockers, one request, bounded confirmation, and events."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol
from dataclasses import dataclass, field

import civ_mcp.narrate as nr
from civ_mcp import lua as lq
from civ_mcp.connection import GameConnection, LuaError
from civ_mcp.game_lifecycle import visible_information_popups
from civ_mcp.lua.congress import build_congress_phase_query, parse_congress_phase

class TurnContext(Protocol):
    """The deliberate collaboration used by turn advancement, not all GameState."""

    conn: GameConnection

    @property
    def pending_action_summary(self) -> str | None: ...
    async def confirm_pending_action(self) -> str: ...
    async def check_game_over(self) -> lq.GameOverStatus | None: ...
    async def get_diplomacy_sessions(self) -> lq.DiplomacyContext: ...
    async def get_pending_deals(self) -> lq.DiplomacyContext: ...
    async def capture_turn_snapshot(self) -> lq.TurnSnapshot: ...
    async def report_turn_advance(
        self, before: int, after: int, baseline: lq.TurnSnapshot | None,
    ) -> str: ...

log = logging.getLogger(__name__)

END_TURN_TIMEOUT_SECONDS = 180.0
POLL_INTERVAL_SECONDS = 2.0
DIPLOMACY_PROBE_SECONDS = 10.0
INFORMATION_PROBE_SECONDS = 5.0
CONGRESS_PROBE_SECONDS = 5.0


@dataclass
class TurnStatus:
    observed_turn: int | None = None
    local_turn_active: bool | None = None
    local_turn_complete: bool | None = None
    ui_processing: bool | None = None
    has_sent_turn_complete: bool | None = None
    can_end_turn: bool | None = None
    can_unready_turn: bool | None = None
    interface_mode: int | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool | None:
        facts = (self.local_turn_active, self.local_turn_complete)
        if False in facts or self.ui_processing is True:
            return False
        if all(fact is True for fact in facts) and self.ui_processing is False:
            return True
        return None

    @property
    def phase(self) -> str:
        if self.local_turn_active is False:
            return "WAITING_FOR_PLAYERS"
        if self.local_turn_active is True and self.local_turn_complete is False:
            return "LOCAL_TURN_STARTING"
        if self.local_turn_active is True and self.local_turn_complete is True:
            if self.ui_processing is True:
                return "UI_PROCESSING"
            if self.ui_processing is False:
                return "LOCAL_READY"
        return "UNKNOWN"

    @property
    def signature(self) -> tuple:
        return (self.observed_turn, self.local_turn_active, self.local_turn_complete,
                self.ui_processing, self.has_sent_turn_complete, self.can_end_turn,
                self.can_unready_turn, self.interface_mode)


async def read_turn_status(gs: TurnContext) -> TurnStatus:
    """Pure observations in native contexts; unavailable APIs stay unknown."""
    status = TurnStatus()
    try:
        lines = await gs.conn.execute_read(f"""
local p = Players[Game.GetLocalPlayer()]
local function fact(fn)
    local ok, value = pcall(fn)
    if ok and type(value) == "boolean" then return value and "1" or "0" end
    return "?"
end
print("TURN_STATE|" .. tostring(Game.GetCurrentGameTurn()) .. "|" ..
    fact(function() return p:IsTurnActive() end) .. "|" ..
    fact(function() return p:IsTurnActiveComplete() end))
print("{lq.SENTINEL}")
""")
        rows = [line.split("|") for line in lines if line.startswith("TURN_STATE|")]
        if len(rows) != 1 or len(rows[0]) != 4:
            raise ValueError("Malformed native turn state")
        _, turn, active, complete = rows[0]
        status.observed_turn = int(turn)
        if status.observed_turn < 0:
            raise ValueError("Invalid native turn")
        status.local_turn_active = {"1": True, "0": False}.get(active)
        status.local_turn_complete = {"1": True, "0": False}.get(complete)
    except (OSError, TimeoutError, LuaError, ValueError, IndexError) as exc:
        status.errors.append(f"GameCore: {exc}")
        return status
    try:
        lines = await gs.conn.execute_write(f"""
local function fact(fn)
    local ok, value = pcall(fn)
    if ok and type(value) == "boolean" then return value and "1" or "0" end
    return "?"
end
local mode = "?"
local ok, value = pcall(UI.GetInterfaceMode)
if ok and type(value) == "number" then mode = tostring(value) end
print("TURN_UI_READY|" .. tostring(Game.GetCurrentGameTurn()) .. "|" ..
    fact(function() return not UI.IsProcessingMessages() end) .. "|" ..
    fact(UI.HasSentTurnComplete) .. "|" .. fact(UI.CanEndTurn) .. "|" ..
    fact(function() return Players[Game.GetLocalPlayer()]:CanUnreadyTurn() end) .. "|" .. mode)
print("{lq.SENTINEL}")
""")
        rows = [line.split("|") for line in lines if line.startswith("TURN_UI_READY|")]
        if len(rows) != 1 or len(rows[0]) != 7:
            raise ValueError("Malformed native UI turn state")
        _, turn, ready, sent, can_end, can_unready, mode = rows[0]
        if int(turn) != status.observed_turn:
            raise ValueError("Native contexts observed different turns")
        status.ui_processing = {"1": False, "0": True}.get(ready)
        status.has_sent_turn_complete = {"1": True, "0": False}.get(sent)
        status.can_end_turn = {"1": True, "0": False}.get(can_end)
        status.can_unready_turn = {"1": True, "0": False}.get(can_unready)
        status.interface_mode = None if mode == "?" else int(mode)
    except (OSError, TimeoutError, LuaError, ValueError, IndexError) as exc:
        status.errors.append(f"InGame: {exc}")
    return status


async def read_turn_state(gs: TurnContext) -> tuple[int | None, bool | None]:
    """Compatibility view used by loading; detailed facts remain available above."""
    status = await read_turn_status(gs)
    return status.observed_turn, status.ready


def _game_over_result(gameover: lq.GameOverStatus) -> str:
    victory = gameover.victory_type.replace("VICTORY_", "").replace("_", " ").title()
    if gameover.is_defeat:
        return (
            f"GAME OVER — DEFEAT. {gameover.winner_leader} of "
            f"{gameover.winner_name} won a {victory} victory."
        )
    return f"GAME OVER — VICTORY! You won a {victory} victory!"


async def _check_mid_turn_diplomacy(
    gs: TurnContext, lua: str, turn_before: int | None,
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


async def _check_congress_phase(gs: TurnContext, *, pending: bool) -> str | None:
    session, stage, turn, receipt = parse_congress_phase(
        await gs.conn.execute_write(build_congress_phase_query())
    )
    if not session and stage == "NONE":
        return None
    prefix = "Turn paused —" if pending else "Cannot end turn:"
    if stage == "UNKNOWN" or (not session and stage != "NONE") or receipt in ("VOTES_REQUESTED", "SUBMIT_REQUESTED", "FAILED", "STALE"):
        return (f"{prefix} World Congress phase/result unknown (turn {turn}, {stage}, {receipt}). "
                "Use get_world_congress; do not repeat votes or the original end-turn request.")
    if stage in ("STAGE1", "STAGE2") and receipt == "NONE":
        return (f"{prefix} World Congress voting stage active (turn {turn}, {stage}); no local tool request recorded. "
                "This is not proof that votes are absent. Use open_world_congress for a verified nonempty Intro, "
                "then get_world_congress and queue_wc_votes "
                "only for new explicit choices. Call end_turn afterward only to confirm "
                "the existing request; it will not be sent again.")
    if not pending:
        return (f"{prefix} World Congress {stage}, local request {receipt}. "
                "The congress stage is already processing; no ordinary end-turn request was sent.")
    return None


async def _check_blockers(gs: TurnContext) -> str | None:
    """Check actual game prerequisites; never make strategy choices for the player."""
    sessions = await gs.get_diplomacy_sessions()
    if sessions.status != "NONE":
        return "Cannot end turn: diplomacy encounter pending.\n" + nr.narrate_diplomacy_sessions(sessions)
    deals = await gs.get_pending_deals()
    if deals.status != "NONE":
        return "Cannot end turn: " + nr.narrate_pending_deals(deals)

    congress = await _check_congress_phase(gs, pending=False)
    if congress:
        return congress

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


class TurnProgress:
    """Own one submitted turn and its immutable reporting baseline.

    Cache refreshes belong to observations; only this module may retire a
    submitted request after readiness or a terminal-game witness confirms it.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.pending = False
        self.from_turn: int | None = None
        self.baseline: lq.TurnSnapshot | None = None
        self._scope_invalid = False
        self.submitted_at: float | None = None
        self.last_observed_at: float | None = None
        self.phase_changed_at: float | None = None
        self.last_status: TurnStatus | None = None
        self.read_failures = 0

    def observe(self, status: TurnStatus) -> None:
        now = time.monotonic()
        if status.observed_turn is not None:
            if self.last_status is None or status.signature != self.last_status.signature:
                self.phase_changed_at = now
            self.last_observed_at = now
        self.last_status = status
        self.read_failures += len(status.errors)

    def describe(self, status: TurnStatus) -> str:
        now = time.monotonic()
        def value(fact):
            if fact is None:
                return "unknown"
            if isinstance(fact, bool):
                return str(fact).lower()
            return str(fact)
        facts = [f"{name}={value(getattr(status, name))}" for name in (
            "observed_turn", "local_turn_active", "local_turn_complete", "ui_processing",
            "has_sent_turn_complete", "can_end_turn", "can_unready_turn", "interface_mode")]
        elapsed = now - self.submitted_at if self.submitted_at is not None else None
        facts += [f"pending={str(self.pending).lower()}", f"from_turn={value(self.from_turn)}",
                  f"elapsed={elapsed:g}s" if elapsed is not None else "elapsed=unknown",
                  f"phase_unchanged={now-self.phase_changed_at:g}s" if self.phase_changed_at is not None else "phase_unchanged=unknown",
                  f"last_observed_age={now-self.last_observed_at:g}s" if self.last_observed_at is not None else "last_observed_age=unknown",
                  f"read_failures={self.read_failures}"]
        if status.errors:
            facts.append("read_errors=" + "; ".join(status.errors))
        return status.phase + " | " + " | ".join(facts)

    async def get_status(self, gs: TurnContext) -> str:
        status = await read_turn_status(gs)
        self.observe(status)
        interactions = []
        for name, probe in (
            ("diplomacy", lambda: _check_mid_turn_diplomacy(gs, "", self.from_turn)),
            ("congress", lambda: _check_congress_phase(gs, pending=self.pending)),
            ("information", lambda: visible_information_popups(gs.conn)),
        ):
            try:
                value = await probe()
                if name == "diplomacy":
                    value = value[0]
                if name == "information":
                    value = "visibility unknown" if value is None else ", ".join(value)
                interactions.append(name + ": " + (value or "none"))
            except (OSError, TimeoutError, LuaError, ValueError) as exc:
                self.read_failures += 1
                interactions.append(name + ": read error: " + str(exc))
        return self.describe(status) + "\n" + "\n".join(interactions)

    def invalidate_scope(self) -> None:
        """An observed world change cannot retire or confirm an older request."""
        if self.pending:
            self._scope_invalid = True

    def _scope_error(self) -> str:
        return ("ERR:OUTCOME_UNKNOWN|Pending end-turn belongs to a changed game. "
                "New-world readiness cannot confirm the original request; "
                "use explicit recovery. No request was resent.")

    async def _report_advance(self, gs: TurnContext, before: int, after: int) -> str:
        if self._scope_invalid:
            return self._scope_error()
        # Retire the request before presentation. No report failure may restore
        # a confirmed mutation or cause it to be sent again.
        previous = self.baseline
        self.reset()
        try:
            gameover = await gs.check_game_over()
            if gameover is not None:
                return f"Turn {before} -> {after}\n" + _game_over_result(gameover)
        except (OSError, TimeoutError, LuaError):
            log.debug("Post-turn game-over check failed", exc_info=True)
        try:
            return await gs.report_turn_advance(before, after, previous)
        except (OSError, TimeoutError, LuaError, ValueError):
            return f"Turn {before} -> {after}\nPost-turn report unavailable; read game state to confirm changes."

    async def execute(self, gs: TurnContext) -> str:
        """Send at most one request and preserve uncertainty until advancement is seen."""
        if self._scope_invalid:
            return self._scope_error()
        if gs.pending_action_summary:
            confirmation = await gs.confirm_pending_action()
            if gs.pending_action_summary:
                return (
                    f"ERR:OUTCOME_UNKNOWN|Cannot end turn while {gs.pending_action_summary}. "
                    f"{confirmation} Read the affected unit/city before continuing."
                )
        if not self.pending:
            try:
                gameover = await gs.check_game_over()
                if gameover is not None:
                    self.reset()
                    return _game_over_result(gameover)
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
                    self.baseline = await gs.capture_turn_snapshot()
                except (OSError, TimeoutError, LuaError, ValueError):
                    self.baseline = None
                # Queries can race an external UI action. Never send from an old
                # turn after taking its snapshot and inspecting its notifications.
                current, ready = await read_turn_state(gs)
                if current is None:
                    return "Cannot end turn: current turn cannot be confirmed. Read game state before retrying."
                if current > turn_before:
                    # An external UI request advanced the game during inspection.
                    # Confirm its readiness below without sending another request.
                    self.pending = True
                    self.from_turn = turn_before
                    self.submitted_at = None  # External submit time was not observed.
                elif current != turn_before:
                    return "Cannot end turn: game turn changed during inspection. Read game state before retrying."
                elif ready is not True:
                    return "Cannot end turn: local player is not ready or readiness is unknown. Read game state before retrying."
            except (OSError, TimeoutError, LuaError, ValueError) as exc:
                return f"Cannot confirm end-turn prerequisites: {exc}. Read game state before retrying."

            if not self.pending:
                self.pending = True
                self.from_turn = turn_before
                self.submitted_at = time.monotonic()
                try:
                    receipt = await gs.conn.execute_write(lq.build_end_turn(turn_before))
                    refused = [line for line in receipt if line.startswith("END_TURN|NOT_SUBMITTED|")]
                    if len(refused) == 1 and "END_TURN|SUBMITTED" not in receipt:
                        reason = refused[0].split("|", 2)[2]
                        if reason != "ALREADY_SENT":
                            self.reset()
                            return f"Cannot end turn: NOT_SUBMITTED ({reason}); no end-turn request was sent. Read get_turn_status."
                        # The native UI has already sent complete. Preserve a
                        # monitor; its original submission time is unknown.
                        self.submitted_at = None
                    elif receipt != ["END_TURN|SUBMITTED"]:
                        return (f"End-turn result unknown (from turn {turn_before}): no authoritative submission receipt. "
                                "The pending request is preserved; call end_turn only to confirm it.")
                except (OSError, TimeoutError, LuaError) as exc:
                    return (
                        f"End-turn result unknown (from turn {turn_before}): {exc}. "
                        "The request may have executed. Read game state or call end_turn "
                        "to confirm; it will not be sent again. Use explicit recovery if needed."
                    )
        else:
            turn_before = self.from_turn
            if turn_before is None:
                return "End-turn result unknown: request has no baseline. Read game state or use explicit recovery; no request was resent."

        started = time.monotonic()
        deadline = started + END_TURN_TIMEOUT_SECONDS
        next_diplomacy_probe = started + DIPLOMACY_PROBE_SECONDS
        next_information_probe = started
        next_congress_probe = started
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
                            if self._scope_invalid:
                                return self._scope_error()
                            if not any(line == "GAME_ACTIVE" or line.startswith("GAME_OVER|") for line in status):
                                reads_failed = True
                                self.read_failures += 1
                            else:
                                gameover = lq.parse_gameover_response(status)
                                if gameover is not None:
                                    self.reset()
                                    return _game_over_result(gameover)
                        except (OSError, TimeoutError, LuaError, ValueError):
                            reads_failed = True
                            self.read_failures += 1
                    status = await read_turn_status(gs)
                    self.observe(status)
                    turn_after, ready = status.observed_turn, status.ready
                    if status.errors:
                        reads_failed = True
                    if self._scope_invalid:
                        return self._scope_error()
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
                    if time.monotonic() >= next_congress_probe:
                        next_congress_probe = time.monotonic() + CONGRESS_PROBE_SECONDS
                        try:
                            congress = await _check_congress_phase(gs, pending=True)
                            if congress:
                                return congress
                        except ValueError:
                            return ("End-turn result unknown: World Congress phase unavailable. "
                                    "Use get_world_congress; the original request is preserved and was not resent.")
                        except (OSError, TimeoutError, LuaError):
                            reads_failed = True
                            self.read_failures += 1
                    diplomacy_checked = False
                    if time.monotonic() >= next_diplomacy_probe:
                        diplomacy_checked = True
                        next_diplomacy_probe = time.monotonic() + DIPLOMACY_PROBE_SECONDS
                        try:
                            message, _ = await _check_mid_turn_diplomacy(gs, "", turn_before)
                            if message:
                                return message
                        except (OSError, TimeoutError, LuaError, ValueError):
                            reads_failed = True
                            self.read_failures += 1
                    if time.monotonic() >= next_information_probe:
                        next_information_probe = time.monotonic() + INFORMATION_PROBE_SECONDS
                        try:
                            popups = await visible_information_popups(gs.conn)
                            if popups:
                                # A simultaneous information overlay must not hide
                                # a pending encounter or choose its response.
                                if not diplomacy_checked:
                                    message, _ = await _check_mid_turn_diplomacy(gs, "", turn_before)
                                    if message:
                                        return message
                                return (
                                    f"Turn paused — information popup visible: {', '.join(popups)} "
                                    f"(requested from turn {turn_before}, last observed turn {turn_after}). "
                                    "Use dismiss_popup to close it explicitly, then call end_turn "
                                    "to confirm the existing request; it will not be sent again."
                                )
                            if popups is None:
                                reads_failed = True
                                self.read_failures += 1
                        except (OSError, TimeoutError, LuaError, ValueError):
                            reads_failed = True
                            self.read_failures += 1
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        await asyncio.sleep(min(POLL_INTERVAL_SECONDS, remaining))
        except TimeoutError:
            pass  # Observation budget expiry is not an actual failed native read.
        if turn_after is not None and turn_after > turn_before and ready is True:
            return await self._report_advance(gs, turn_before, turn_after)
        detail = " Some game queries failed; the current turn cannot be reliably confirmed." if reads_failed else ""
        return (
            f"WAITING: end-turn result unconfirmed after {END_TURN_TIMEOUT_SECONDS:g}s "
            f"(requested from turn {turn_before}, last observed turn {turn_after})."
            f"{detail} {self.describe(self.last_status or TurnStatus())}. Read get_turn_status, inspect diplomacy/popups, or use explicit recovery. "
            "The pending request is preserved; another end_turn call will only confirm it."
        )
