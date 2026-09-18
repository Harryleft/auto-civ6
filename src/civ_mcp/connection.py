"""Persistent, reconnectable FireTuner connection to Civilization VI.

Wraps tuner_client.py into a stateful connection manager with:
- Lua state index discovery (GameCore_Tuner, InGame)
- asyncio lock for serializing commands
- Sentinel-based multi-line response collection
- Output prefix parsing (O\x00<context>: <value>)
- Reconnection on connection loss (queries only — mutations are never resent)
"""

from __future__ import annotations

import asyncio
import logging
import os

from civ_mcp import tuner_client
from civ_mcp.lua._helpers import SENTINEL

log = logging.getLogger(__name__)

# Default per-command ceiling. It is sized for reads and light commands.
DEFAULT_TIMEOUT = 5.0

# Some mutations make the game do real work inline — fortifying or skipping the
# whole army, swapping policy cards (yields are recomputed), resolving a World
# Congress round. Those legitimately exceed the read timeout, and a timeout is
# reported as MutationOutcomeUnknownError, which tells the caller the command
# may already have run. That is a false positive for a command that is merely
# slow: it costs the agent a verification round and invites duplicate actions.
SLOW_MUTATION_TIMEOUT = 30.0

# FireTuner pushes unsolicited output (LuaEvent callbacks such as ShowIngameUI
# debug prints) alongside command responses, so `_locked_execute` drains the
# socket before sending and again after the sentinel — otherwise that output
# would be attributed to the wrong command. Both drains run to their timeout
# when nothing is pending, so the previous values (0.1s + 0.2s) cost 300ms of
# pure waiting per Lua command: ~25 minutes across a 110-turn game, and a
# measured floor of 302ms that no latency figure in the docs could beat.
#
# What the drains actually collect is data already buffered by the time they
# run: the pre-send drain clears whatever accumulated while the agent was
# thinking (seconds to minutes), and the post-command drain catches output
# emitted alongside the response it just read. A fresh reader consumes every
# buffered message within 0.5ms, so the multi-hundred-ms windows were far more
# conservative than the mechanism needs. This value keeps a 20ms grace window;
# raise CIV_MCP_DRAIN_TIMEOUT if a real game shows output leaking between
# commands (the symptom is stray lines prepended to a tool result).
DRAIN_TIMEOUT = float(os.environ.get("CIV_MCP_DRAIN_TIMEOUT", "0.02"))


class LuaError(Exception):
    """Raised when Lua code execution returns an error."""


class CommandTimeoutError(LuaError):
    """Raised when a command times out before its completion sentinel arrives.

    Subclassing LuaError keeps server.py's ``except (LuaError, ValueError)``
    handler in play, so the error reaches the agent instead of the old
    behavior of silently returning partial output that parsers then
    misread. Carries the configured timeout and the lines received so far.
    """

    def __init__(self, timeout: float, lines: list[str]):
        self.timeout = timeout
        self.lines = list(lines)
        super().__init__(timeout, lines)

    def __str__(self) -> str:
        preview = " | ".join(self.lines[:3])
        if len(preview) > 200:
            preview = preview[:200] + "..."
        return (
            f"Command timed out after {self.timeout:.1f}s without receiving "
            f'the "{SENTINEL}" completion sentinel — output is incomplete '
            f"and must not be parsed as a result. Received "
            f"{len(self.lines)} partial line(s)"
            + (f": [{preview}]" if preview else "")
        )


class MutationOutcomeUnknownError(ConnectionError):
    """A game-mutating command was sent but its outcome is unknown.

    The connection died around the send, so the game may already have
    executed the command and merely lost its output. Resending would risk
    double-executing a move/attack/purchase, so mutations are sent exactly
    once and never retried here. Subclassing ConnectionError keeps
    server.py's connection-loss recovery path in play.
    """

    def __init__(self, lua_code: str, original: BaseException | None = None):
        self.lua_code = lua_code
        self.original = original
        super().__init__(lua_code, original)

    def __str__(self) -> str:
        first_line = self.lua_code.splitlines()[0][:120] if self.lua_code else ""
        cause = f" ({self.original!r})" if self.original else ""
        return (
            "Command may have already been executed by the game — result "
            f"unknown; connection lost mid-command{cause}. It was NOT "
            "resent (resending a mutation can double-execute it). Verify "
            "the current game state with a read query before retrying. "
            f"Command started with: {first_line!r}"
        )


class GameConnection:
    """Persistent FireTuner TCP connection to Civ 6."""

    def __init__(self, host: str = "127.0.0.1", port: int = 4318):
        self.host = host
        self.port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self.lua_states: dict[int, str] = {}  # index -> name
        self.gamecore_index: int | None = None
        self.ingame_index: int | None = None
        self.generation = 0
        self.mutation_revision = 0
        # True while an ACTION_ENDTURN request is in flight and the AI civs are
        # processing. Background pollers read it to stay off the InGame context
        # during that window: InGame queries force context switches that can
        # stall the AI's diplomacy job and wedge the turn (the documented cause
        # of the Games 1-5 hangs). The wait loop itself already sticks to
        # GameCore-only queries for the same reason; this puts the 2 Hz popup
        # poller and the camera on the same rule.
        self.turn_in_progress = False

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Connect to Civ 6 and discover Lua state indexes."""
        self.generation += 1
        log.info("Connecting to Civ 6 at %s:%d", self.host, self.port)
        try:
            self._reader, self._writer = await tuner_client.connect(
                self.host, self.port
            )
        except (asyncio.TimeoutError, OSError) as e:
            raise ConnectionError(
                f"Cannot connect to Civ 6 at {self.host}:{self.port}. "
                "Is the game running with EnableTuner=1?"
            ) from e
        app_identity, raw_states = await tuner_client.handshake(
            self._reader, self._writer
        )
        log.info("Connected: %s", app_identity)

        # Parse state list: alternating [index_number, state_name] pairs
        self.lua_states = {}
        self.gamecore_index = None
        self.ingame_index = None
        i = 0
        while i + 1 < len(raw_states):
            try:
                idx = int(raw_states[i])
                name = raw_states[i + 1]
                self.lua_states[idx] = name
                if name == "GameCore_Tuner" and self.gamecore_index is None:
                    self.gamecore_index = idx
                if name == "InGame" and self.ingame_index is None:
                    self.ingame_index = idx
                i += 2
            except ValueError:
                i += 1

        log.info(
            "Discovered %d Lua states (GameCore=%s, InGame=%s)",
            len(self.lua_states),
            self.gamecore_index,
            self.ingame_index,
        )

    async def disconnect(self) -> None:
        self.generation += 1
        if self._writer and not self._writer.is_closing():
            self._writer.close()
            await self._writer.wait_closed()
        self._writer = None
        self._reader = None

    async def ensure_connected(self) -> None:
        """Connect (or reconnect) if not connected. Raises ConnectionError on failure."""
        if self.is_connected:
            return
        log.info("Connecting to Civ 6...")
        await self.connect()

    async def reconnect(self) -> None:
        """Force a fresh connection and re-discover Lua states."""
        await self.disconnect()
        await self.connect()

    async def _ensure_game_states(self) -> None:
        """Ensure we have GameCore and InGame state indexes.

        If connected but missing game states (e.g. connected at main menu),
        reconnect to re-discover states now that a game may be loaded.
        """
        await self.ensure_connected()
        if self.gamecore_index is None or self.ingame_index is None:
            log.info("Game states not found, reconnecting to re-discover...")
            await self.reconnect()
        if self.gamecore_index is None or self.ingame_index is None:
            raise ConnectionError(
                "GameCore_Tuner/InGame states not found. "
                "Make sure a game is in progress (not at the main menu)."
            )

    async def execute_read(
        self, lua_code: str, timeout: float = DEFAULT_TIMEOUT, require_sentinel: bool = True
    ) -> list[str]:
        """Execute Lua in GameCore context (read state). Returns parsed output lines."""
        await self._ensure_game_states()
        return await self._execute_and_collect(
            self.gamecore_index, lua_code, timeout, require_sentinel=require_sentinel
        )

    async def execute_write(
        self,
        lua_code: str,
        timeout: float = DEFAULT_TIMEOUT,
        require_sentinel: bool = True,
        mutation: bool = False,
    ) -> list[str]:
        """Execute Lua in InGame context (issue commands). Returns parsed output lines.

        ``mutation=True`` is for arbitrary InGame code that can change game
        state (run_lua's escape hatch): it inherits the send-exactly-once
        contract while keeping a caller-chosen sentinel requirement.
        """
        await self._ensure_game_states()
        return await self._execute_and_collect(
            self.ingame_index,
            lua_code,
            timeout,
            mutation=mutation,
            require_sentinel=require_sentinel,
        )

    async def execute_mutation(
        self, lua_code: str, timeout: float = DEFAULT_TIMEOUT, context: str = "ingame"
    ) -> list[str]:
        """Execute a game-mutating command (moves, attacks, purchases, end_turn...).

        Guarantees the command is sent at most once: on a dead socket the
        query path may safely reconnect and resend, but a mutation may
        already have run in the game — resending would double-execute it,
        so we raise MutationOutcomeUnknownError instead. The completion
        sentinel is required, and a mutation that times out without it is
        reported as MutationOutcomeUnknownError too: the command may have
        executed, so the caller must verify game state before retrying
        rather than treating it as a plain parse failure.
        """
        await self._ensure_game_states()
        if context == "ingame":
            state_index = self.ingame_index
        elif context == "gamecore":
            state_index = self.gamecore_index
        else:
            raise ValueError(
                f"Unknown mutation context {context!r} (expected 'ingame' or 'gamecore')"
            )
        try:
            return await self._execute_and_collect(
                state_index, lua_code, timeout, mutation=True, require_sentinel=True
            )
        except CommandTimeoutError as exc:
            raise MutationOutcomeUnknownError(lua_code, exc) from exc

    async def execute_in_state(
        self,
        state_index: int,
        lua_code: str,
        timeout: float = DEFAULT_TIMEOUT,
        mutation: bool = False,
        require_sentinel: bool = False,
    ) -> list[str]:
        """Execute Lua in an arbitrary state index. Returns parsed output lines.

        Lenient by default: state probing (game_lifecycle popup scans) and
        arbitrary code must not depend on the sentinel convention. Pass
        ``mutation=True`` for state-local commands that change game/UI state
        (popup Close()) — they are then never resent on a dead socket.
        """
        return await self._execute_and_collect(
            state_index,
            lua_code,
            timeout,
            mutation=mutation,
            require_sentinel=require_sentinel,
        )

    async def _execute_and_collect(
        self,
        state_index: int,
        lua_code: str,
        timeout: float,
        mutation: bool = False,
        require_sentinel: bool = True,
    ) -> list[str]:
        """Send Lua code and collect output lines until sentinel or timeout.

        Auto-reconnects once on dead socket (e.g. after game crash/reload)
        for queries only — mutations are never resent because the game may
        have already executed the original send (MutationOutcomeUnknownError).
        """
        await self.ensure_connected()
        async with self._lock:
            if mutation:
                # Invalidate before the one send, including unknown outcomes.
                self.mutation_revision += 1
            try:
                return await self._locked_execute(
                    state_index, lua_code, timeout, require_sentinel=require_sentinel
                )
            except (ConnectionError, OSError, asyncio.IncompleteReadError) as e:
                if mutation:
                    raise MutationOutcomeUnknownError(lua_code, e) from e
                # Dead socket — reconnect once and retry (still holding lock)
                log.info("Connection lost, reconnecting...")
                await self.reconnect()
                return await self._locked_execute(
                    state_index, lua_code, timeout, require_sentinel=require_sentinel
                )

    async def _locked_execute(
        self,
        state_index: int,
        lua_code: str,
        timeout: float,
        require_sentinel: bool = True,
    ) -> list[str]:
        """Inner execute — must be called while holding self._lock."""
        assert self._reader is not None
        assert self._writer is not None

        # Drain any stale messages
        await tuner_client.drain_messages(self._reader, timeout=DRAIN_TIMEOUT)

        await tuner_client.send_message(
            self._writer, tuner_client.TAG_COMMAND, f"CMD:{state_index}:{lua_code}"
        )

        lines: list[str] = []
        saw_sentinel = False
        deadline = asyncio.get_running_loop().time() + timeout

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break

            msg = await tuner_client.recv_message_timeout(
                self._reader, timeout=min(remaining, 2.0)
            )
            if msg is None:
                break

            if msg.payload.startswith("ERR:"):
                raise LuaError(msg.payload)

            text = _parse_output(msg.payload)
            if text is not None:
                if text.strip() == SENTINEL:
                    saw_sentinel = True
                    break
                lines.append(text)
            # Ignore non-output messages (e.g. tag=3 empty ack)

        if require_sentinel and not saw_sentinel:
            # Timed out (or the stream went quiet) before the Lua sentinel
            # arrived — the collected lines are partial/unreliable, so fail
            # loudly instead of handing the caller a truncated result.
            raise CommandTimeoutError(timeout, lines)

        # Drain any trailing unsolicited output
        await tuner_client.drain_messages(self._reader, timeout=DRAIN_TIMEOUT)
        return lines


def _parse_output(payload: str) -> str | None:
    """Extract value from a print() output message.

    Format: O\\x00<context_name>: <value>
    Returns the value part, or None if not an output message.
    """
    if not payload.startswith("O"):
        return None

    # Find the ': ' separator after the context name
    sep = payload.find(": ", 2)
    if sep >= 0:
        return payload[sep + 2 :]

    # Fallback: strip the O and null byte prefix
    return payload.lstrip("O").lstrip("\x00").strip()
