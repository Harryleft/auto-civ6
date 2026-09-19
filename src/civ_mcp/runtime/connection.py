"""FireTuner bootstrap owned by the new Runtime Core.

The historical ``GameConnection`` mixes transport, process recovery, turn
policy, and state tracking.  This module owns only one narrow concern: open a
socket, complete the FireTuner handshake, and expose the two discovered Lua
state indexes to ``CivAdapter``.  It has no game-policy or recovery behavior.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from civ_mcp.runtime.transport import FireTunerTransport, Frame, StreamWriterLike


HANDSHAKE_TAG = 4
OpenStream = Callable[
    [str, int, float], Awaitable[tuple[asyncio.StreamReader, StreamWriterLike]]
]


class RuntimeConnectionError(ConnectionError):
    """The Runtime Core could not establish a usable FireTuner boundary."""


class LuaStateChangedError(RuntimeConnectionError):
    """A reconnect changed the Lua indexes that an already-built read used."""


@dataclass(frozen=True, slots=True)
class LuaStateIndexes:
    """The two Lua contexts the Civ adapter is allowed to address."""

    gamecore: int
    ingame: int

    def for_context(self, context: str) -> int:
        if context == "gamecore":
            return self.gamecore
        if context == "ingame":
            return self.ingame
        raise ValueError("Civ Lua context 必须是 gamecore 或 ingame。")


class RuntimeConnection:
    """A handshaken FireTuner stream with state-safe read reconnection."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        timeout: float,
        open_stream: OpenStream,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._open_stream = open_stream
        self._states: LuaStateIndexes | None = None
        self._transport: FireTunerTransport | None = None
        self.app_identity = ""

    @classmethod
    async def connect(
        cls,
        host: str = "127.0.0.1",
        port: int = 4318,
        *,
        timeout: float = 5.0,
        open_stream: OpenStream | None = None,
    ) -> "RuntimeConnection":
        """Connect and discover GameCore/InGame before exposing a transport."""
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0。")
        connection = cls(
            host=host,
            port=port,
            timeout=timeout,
            open_stream=open_stream or _open_stream,
        )
        reader, writer, app_identity, states = await connection._open_and_discover()
        connection.app_identity = app_identity
        connection._states = states
        connection._transport = FireTunerTransport(
            reader,
            writer,
            reconnect=connection._reconnect,
        )
        return connection

    @property
    def transport(self) -> FireTunerTransport:
        if self._transport is None:
            raise RuntimeConnectionError("RuntimeConnection 尚未完成 FireTuner 握手。")
        return self._transport

    @property
    def states(self) -> LuaStateIndexes:
        if self._states is None:
            raise RuntimeConnectionError("RuntimeConnection 尚未发现 Lua states。")
        return self._states

    def state_for(self, context: str) -> int:
        """Resolve the current discovered Lua index for one approved context."""
        return self.states.for_context(context)

    async def close(self) -> None:
        """Close only this Runtime-owned socket; never restart or load Civ6."""
        if self._transport is not None:
            await self._transport.close()

    async def _reconnect(self) -> tuple[asyncio.StreamReader, StreamWriterLike]:
        """Reconnect reads only when the same named Lua states retain their IDs."""
        reader, writer, app_identity, discovered = await self._open_and_discover()
        previous = self.states
        self.app_identity = app_identity
        self._states = discovered
        if discovered != previous:
            await _close_writer(writer)
            raise LuaStateChangedError(
                "重连后 FireTuner Lua state 编号已变化；本次读请求未重试。"
            )
        return reader, writer

    async def _open_and_discover(
        self,
    ) -> tuple[asyncio.StreamReader, StreamWriterLike, str, LuaStateIndexes]:
        try:
            reader, writer = await self._open_stream(self._host, self._port, self._timeout)
        except (TimeoutError, OSError) as exc:
            raise RuntimeConnectionError(
                f"无法连接 Civ6 FireTuner {self._host}:{self._port}。"
            ) from exc
        handshake = FireTunerTransport(reader, writer)
        try:
            app_identity = await _handshake_value(handshake, "APP:")
            raw_states = await _handshake_value(handshake, "LSQ:")
            return reader, writer, app_identity, _parse_lua_states(raw_states)
        except BaseException:
            await handshake.close()
            raise


async def _open_stream(
    host: str, port: int, timeout: float
) -> tuple[asyncio.StreamReader, StreamWriterLike]:
    return await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)


async def _handshake_value(transport: FireTunerTransport, payload: str) -> str:
    receipt = await transport.submit_command(
        payload,
        tag=HANDSHAKE_TAG,
        is_complete=lambda _frame: True,
    )
    if not receipt.complete or not receipt.frames:
        raise RuntimeConnectionError(f"FireTuner {payload} 握手未得到完整响应。")
    return receipt.frames[-1].payload


def _parse_lua_states(raw_states: str) -> LuaStateIndexes:
    entries = [item.strip() for item in raw_states.split("\x00") if item.strip()]
    if len(entries) <= 1 and "\n" in raw_states:
        entries = [item.strip() for item in raw_states.splitlines() if item.strip()]
    discovered: dict[str, int] = {}
    for index, name in zip(entries[::2], entries[1::2], strict=False):
        try:
            numeric_index = int(index)
        except ValueError:
            continue
        discovered.setdefault(name, numeric_index)
    try:
        return LuaStateIndexes(
            gamecore=discovered["GameCore_Tuner"],
            ingame=discovered["InGame"],
        )
    except KeyError as exc:
        raise RuntimeConnectionError(
            "FireTuner 未暴露 GameCore_Tuner/InGame；请先进入已加载的对局。"
        ) from exc


async def _close_writer(writer: StreamWriterLike) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError, RuntimeError):
        pass
