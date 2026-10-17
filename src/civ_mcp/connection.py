"""Persistent, reconnectable FireTuner connection to Civilization VI.

Wraps tuner_client.py into a stateful connection manager with:
- Lua state index discovery (GameCore_Tuner, InGame)
- asyncio lock for serializing commands
- Request-correlated multi-line response collection
- Output prefix parsing (O\x00<context>: <value>)
- Explicit reconnection and no automatic command replay
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from civ_mcp import tuner_client
from civ_mcp.lua._helpers import SENTINEL

log = logging.getLogger(__name__)


class LuaError(Exception):
    """Raised when Lua code execution returns an error."""


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

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Connect and discover context indexes; retire unsuccessful handshakes."""
        try:
            self._reader, self._writer = await tuner_client.connect(self.host, self.port)
            app_identity, raw_states = await tuner_client.handshake(self._reader, self._writer)
            self.lua_states = {}
            self.gamecore_index = None
            self.ingame_index = None
            i = 0
            while i + 1 < len(raw_states):
                try:
                    index = int(raw_states[i])
                except ValueError:
                    i += 1
                    continue
                name = raw_states[i + 1]
                i += 2
                self.lua_states[index] = name
                if name == "GameCore_Tuner" and self.gamecore_index is None:
                    self.gamecore_index = index
                if name == "InGame" and self.ingame_index is None:
                    self.ingame_index = index
            log.info("Connected: %s, GameCore=%s, InGame=%s", app_identity, self.gamecore_index, self.ingame_index)
        except (OSError, asyncio.IncompleteReadError) as exc:
            await self.disconnect()
            raise ConnectionError(
                f"Cannot connect to Civ 6 at {self.host}:{self.port}: {exc}. "
                "Make sure Civ 6 is running with EnableTuner enabled."
            ) from exc

    async def disconnect(self) -> None:
        writer = self._writer
        self._writer = None
        self._reader = None
        self.lua_states = {}
        self.gamecore_index = None
        self.ingame_index = None
        if writer and not writer.is_closing():
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

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

    async def execute_read(self, lua_code: str, timeout: float = 5.0) -> list[str]:
        """Execute in GameCore; context indexes are resolved under the command lock."""
        return await self._execute_and_collect("GameCore_Tuner", lua_code, timeout)

    async def execute_write(self, lua_code: str, timeout: float = 5.0) -> list[str]:
        """Execute in InGame once. A lost response never causes automatic replay."""
        return await self._execute_and_collect("InGame", lua_code, timeout)

    async def execute_in_state(self, state_index: int, lua_code: str, timeout: float = 5.0) -> list[str]:
        """Execute once in a known Lua context, including MainMenu."""
        return await self._execute_and_collect(state_index, lua_code, timeout)

    async def _execute_and_collect(self, state_index: int | str, lua_code: str, timeout: float) -> list[str]:
        """Serialize connection discovery and execution; retire uncertain streams.

        GameCore is also used for mutations by some game tools, so context name
        alone cannot establish idempotence. No Lua chunk is automatically replayed.
        """
        async with self._lock:
            try:
                await self.ensure_connected()
                if isinstance(state_index, str):
                    await self._ensure_game_states()
                    index = self.gamecore_index if state_index == "GameCore_Tuner" else self.ingame_index
                else:
                    index = state_index
                return await self._locked_execute(index, lua_code, timeout)
            except (OSError, LuaError, asyncio.IncompleteReadError, asyncio.CancelledError):
                await self.disconnect()
                raise

    async def _locked_execute(
        self, state_index: int, lua_code: str, timeout: float
    ) -> list[str]:
        """Inner execute — must be called while holding self._lock."""
        assert self._reader is not None
        assert self._writer is not None

        # Keep print local to this request. Late event callbacks retain their old
        # prefix, so they cannot become a later command's output or completion.
        # Legacy builder sentinels remain harmless data inside this boundary.
        prefix = f"CIVMCP|{uuid.uuid4().hex}|"
        output_prefix = prefix + "OUT|"
        completion = prefix + "END"
        wrapped = f"""do
local native_print = print
local function print(...)
    local values = {{}}
    for i = 1, select("#", ...) do values[i] = tostring(select(i, ...)) end
    native_print("{output_prefix}" .. table.concat(values, "\\t"))
end
(function()
{lua_code}
end)()
native_print("{completion}")
end"""

        lines: list[str] = []
        try:
            # Sending and reading share one deadline. A cancelled partial frame
            # is retired by _execute_and_collect, never drained or replayed.
            async with asyncio.timeout(timeout):
                await tuner_client.send_message(
                    self._writer, tuner_client.TAG_COMMAND, f"CMD:{state_index}:{wrapped}"
                )
                while True:
                    msg = await tuner_client.recv_message(self._reader)
                    if msg.payload.startswith("ERR:"):
                        raise LuaError(msg.payload)
                    text = _parse_output(msg.payload)
                    if text == completion:
                        return lines
                    if text is not None and text.startswith(output_prefix):
                        value = text[len(output_prefix):]
                        if value.strip() != SENTINEL:
                            lines.append(value)
                    # Ignore acks, unsolicited prints and other requests' output.
        except TimeoutError as exc:
            raise TimeoutError(
                f"Incomplete response from Civ 6 within {timeout:.1f}s "
                f"(Lua state {state_index}); command outcome is unknown."
            ) from exc


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
