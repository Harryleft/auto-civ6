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
from typing import Literal

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


TurnAction = Literal["end_turn", "diplomacy", "congress", "load"]
_TURN_ACTIONS = {"end_turn", "diplomacy", "congress", "load"}


class CommandNotSentError(LuaError):
    """The execution phase disallowed this command before it was sent."""


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
    double-executing a move/attack/purchase, so mutations are sent at most
    once and never retried here. Subclassing ConnectionError preserves the
    server's error classification; it does not authorize game recovery.
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
        self.load_revision = 0
        self.world_epoch = 0
        # Keep ordinary InGame work off the wire while the turn request is
        # in flight. Poller checks are only an optimization; enforce the phase
        # again while holding the send lock, immediately before writer.write.
        self.turn_in_progress = False
        self.reload_pending = False

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def _connect(self) -> None:
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
        try:
            app_identity, raw_states = await tuner_client.handshake(
                self._reader, self._writer
            )
        except BaseException:
            self._discard_connection()
            raise
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

    def _discard_connection(self) -> asyncio.StreamWriter | None:
        """Detach synchronously, including while this task is being cancelled."""
        writer = self._writer
        self._writer = None
        self._reader = None
        self.generation += 1
        if writer is not None and not writer.is_closing():
            writer.close()
        return writer

    async def _disconnect(self) -> None:
        writer = self._discard_connection()
        if writer is not None:
            await writer.wait_closed()

    async def _ensure_connected(self) -> None:
        """Connect (or reconnect) if not connected. Raises ConnectionError on failure."""
        if self.is_connected:
            return
        log.info("Connecting to Civ 6...")
        await self._connect()

    async def _reconnect(self) -> None:
        """Force a fresh connection and re-discover Lua states."""
        await self._disconnect()
        await self._connect()

    async def connect(self) -> None:
        async with self._lock:
            await self._ensure_connected()

    async def disconnect(self) -> None:
        async with self._lock:
            await self._disconnect()

    async def ensure_connected(self) -> None:
        async with self._lock:
            await self._ensure_connected()

    async def reconnect(self) -> None:
        async with self._lock:
            await self._reconnect()

    async def _ensure_game_states(self) -> None:
        """Ensure we have GameCore and InGame state indexes.

        If connected but missing game states (e.g. connected at main menu),
        reconnect to re-discover states now that a game may be loaded.
        """
        await self._ensure_connected()
        if self.gamecore_index is None or self.ingame_index is None:
            log.info("Game states not found, reconnecting to re-discover...")
            await self._reconnect()
        if self.gamecore_index is None or self.ingame_index is None:
            raise ConnectionError(
                "GameCore_Tuner/InGame states not found. "
                "Make sure a game is in progress (not at the main menu)."
            )

    async def execute_read(
        self, lua_code: str, timeout: float = DEFAULT_TIMEOUT, require_sentinel: bool = True,
        *, expected_world_epoch: int | None = None,
    ) -> list[str]:
        """Execute Lua in GameCore context (read state). Returns parsed output lines."""
        return await self._execute_and_collect(
            None, lua_code, timeout, require_sentinel=require_sentinel,
            context="gamecore", expected_world_epoch=expected_world_epoch,
        )

    async def execute_write(
        self,
        lua_code: str,
        timeout: float = DEFAULT_TIMEOUT,
        require_sentinel: bool = True,
        mutation: bool = False,
        *,
        turn_action: TurnAction | None = None,
        expected_world_epoch: int | None = None,
    ) -> list[str]:
        """Execute InGame Lua; turn_action names a specific turn/reload operation.

        Ordinary InGame work is rejected at the send boundary during AI turn
        processing. Only the end-turn submitter and explicit diplomacy,
        congress, or load handlers may opt in to their corresponding exception.
        Mutation-capable arbitrary Lua must pass mutation=True.
        """
        return await self._execute_and_collect(
            None, lua_code, timeout, mutation=mutation,
            require_sentinel=require_sentinel, context="ingame",
            turn_action=turn_action, expected_world_epoch=expected_world_epoch,
        )

    async def execute_mutation(
        self, lua_code: str, timeout: float = DEFAULT_TIMEOUT, context: str = "ingame",
        *, turn_action: TurnAction | None = None,
        expected_world_epoch: int | None = None,
    ) -> list[str]:
        """Send a mutation at most once; a missing receipt has unknown outcome."""
        if context not in {"ingame", "gamecore"}:
            raise ValueError(
                f"Unknown mutation context {context!r} (expected 'ingame' or 'gamecore')"
            )
        return await self._execute_and_collect(
            None, lua_code, timeout, mutation=True, require_sentinel=True,
            context=context, turn_action=turn_action,
            expected_world_epoch=expected_world_epoch,
        )

    async def execute_in_state(
        self,
        state_index: int,
        lua_code: str,
        timeout: float = DEFAULT_TIMEOUT,
        mutation: bool = False,
        require_sentinel: bool = False,
        *,
        turn_action: TurnAction | None = None,
        expected_world_epoch: int | None = None,
    ) -> list[str]:
        """Execute state-local Lua, applying the same mutation and phase guards.

        Lenient probes may return partial output, but their socket is discarded
        when no sentinel arrives; a later request must get a fresh handshake.
        """
        return await self._execute_and_collect(
            state_index, lua_code, timeout, mutation=mutation,
            require_sentinel=require_sentinel, turn_action=turn_action,
            expected_world_epoch=expected_world_epoch,
        )

    def _check_send_allowed(
        self, state_index: int, *, mutation: bool, turn_action: TurnAction | None,
        expected_world_epoch: int | None = None,
    ) -> None:
        if expected_world_epoch is not None and self.world_epoch != expected_world_epoch:
            raise CommandNotSentError("对局已切换，旧请求不能用于新局面；本次命令未发送。")
        if turn_action is not None and turn_action not in _TURN_ACTIONS:
            raise ValueError(f"Unknown turn action {turn_action!r}")
        if mutation and self.reload_pending and turn_action != "load":
            raise CommandNotSentError("读档结果尚未确认，暂停游戏写入；本次命令未发送。")
        if self.turn_in_progress and state_index == self.ingame_index and turn_action is None:
            raise CommandNotSentError("回合仍在处理中，暂缓普通 InGame 请求；本次命令未发送。")

    async def _execute_and_collect(
        self,
        state_index: int | None,
        lua_code: str,
        timeout: float,
        mutation: bool = False,
        require_sentinel: bool = True,
        *,
        context: str | None = None,
        turn_action: TurnAction | None = None,
        expected_world_epoch: int | None = None,
    ) -> list[str]:
        """Serialize connection setup, state selection, phase checks and sends.

        Read queries may reconnect and retry once. Mutations are never resent,
        including after a missing sentinel or an interrupted response frame.
        """
        # Bind every write to the world at invocation, before queueing for the
        # lock. A completed load can clear reload_pending while an old move or
        # purchase is still waiting. Explicit load commands follow the same
        # rule: a queued second load must not silently act on a newer world.
        if mutation and expected_world_epoch is None:
            expected_world_epoch = self.world_epoch
        async with self._lock:
            # Capture the old name before setup can replace the state table.
            state_name = self.lua_states.get(state_index) if context is None else None
            if context is not None:
                await self._ensure_game_states()
            else:
                await self._ensure_connected()

            def resolve_state() -> int:
                if context == "ingame":
                    return self.ingame_index
                if context == "gamecore":
                    return self.gamecore_index
                if state_name is not None:
                    for index, name in self.lua_states.items():
                        if name == state_name:
                            return index
                    raise CommandNotSentError("重新连接后目标 Lua 状态已消失；本次命令未发送。")
                return state_index

            deadline = asyncio.get_running_loop().time() + timeout
            for attempt in range(2):
                selected = resolve_state()
                self._check_send_allowed(
                    selected, mutation=mutation, turn_action=turn_action,
                    expected_world_epoch=expected_world_epoch,
                )
                if mutation:
                    self.mutation_revision += 1
                try:
                    return await self._locked_execute(
                        selected, lua_code, timeout,
                        require_sentinel=require_sentinel,
                        mutation=mutation, turn_action=turn_action, deadline=deadline,
                        expected_world_epoch=expected_world_epoch,
                    )
                except CommandTimeoutError as exc:
                    if mutation:
                        raise MutationOutcomeUnknownError(lua_code, exc) from exc
                    raise
                except (ConnectionError, OSError, asyncio.IncompleteReadError) as exc:
                    self._discard_connection()
                    if mutation:
                        raise MutationOutcomeUnknownError(lua_code, exc) from exc
                    if attempt:
                        raise
                    log.info("Connection lost, reconnecting...")
                    try:
                        async with asyncio.timeout_at(deadline):
                            await self._reconnect()
                    except TimeoutError as exc:
                        raise CommandTimeoutError(timeout, []) from exc
            raise AssertionError("unreachable")

    async def _locked_execute(
        self,
        state_index: int,
        lua_code: str,
        timeout: float,
        require_sentinel: bool = True,
        *,
        mutation: bool = False,
        turn_action: TurnAction | None = None,
        deadline: float | None = None,
        expected_world_epoch: int | None = None,
    ) -> list[str]:
        """Collect frames against one deadline; an unfinished reply retires TCP."""
        assert self._reader is not None
        assert self._writer is not None
        lines: list[str] = []
        saw_sentinel = False
        loop = asyncio.get_running_loop()
        if deadline is None:
            deadline = loop.time() + timeout
        try:
            async with asyncio.timeout_at(deadline):
                await tuner_client.drain_messages(self._reader, timeout=DRAIN_TIMEOUT)
                # The pre-send drain is an await too: phase may have changed
                # after taking the lock. Recheck immediately before any write.
                self._check_send_allowed(
                    state_index, mutation=mutation, turn_action=turn_action,
                    expected_world_epoch=expected_world_epoch,
                )
                await tuner_client.send_message(
                    self._writer, tuner_client.TAG_COMMAND, f"CMD:{state_index}:{lua_code}"
                )
                while (remaining := deadline - loop.time()) > 0:
                    msg = await tuner_client.recv_message_timeout(
                        self._reader, timeout=remaining
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
        except TimeoutError:
            # asyncio.timeout also bounds blocked sends, not just receiving.
            pass
        except CommandNotSentError:
            raise
        except BaseException:
            self._discard_connection()
            raise

        if not saw_sentinel:
            # Even a clean, idle timeout can leave a late sentinel queued. A
            # fresh connection is the only safe next command boundary because
            # the protocol does not identify individual requests.
            self._discard_connection()
            if require_sentinel or mutation:
                raise CommandTimeoutError(timeout, lines)
            return lines

        try:
            async with asyncio.timeout_at(deadline):
                await tuner_client.drain_messages(self._reader, timeout=DRAIN_TIMEOUT)
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            # The sentinel already confirmed this command. Retire a broken
            # trailing frame without changing the confirmed execution result.
            self._discard_connection()
        except BaseException:
            self._discard_connection()
            raise
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
