"""Runtime-owned FireTuner handshake and safe state-reconnect tests."""

from __future__ import annotations

import asyncio
import struct

import pytest

from civ_mcp.civ.adapter import CivAdapter, CivReadError, CivReadRequest
from civ_mcp.runtime.connection import LuaStateChangedError, RuntimeConnection, RuntimeConnectionError


def _frame(payload: str, *, tag: int = 2) -> bytes:
    body = payload.encode("utf-8") + b"\x00"
    return struct.pack("<Ii", len(body), tag) + body


class _Writer:
    def __init__(self, reader: asyncio.StreamReader, *, states: str, fail_command: bool = False) -> None:
        self._reader = reader
        self._states = states
        self._fail_command = fail_command
        self.closed = False
        self.sent: list[str] = []

    def write(self, data: bytes) -> None:
        _length, tag = struct.unpack("<Ii", data[:8])
        payload = data[8:].rstrip(b"\x00").decode("utf-8")
        self.sent.append(payload)
        if tag == 4 and payload == "APP:":
            self._reader.feed_data(_frame("APP:civ6"))
        elif tag == 4 and payload == "LSQ:":
            self._reader.feed_data(_frame(self._states))
        elif tag == 3 and self._fail_command:
            raise ConnectionResetError("not sent")
        elif tag == 3:
            self._reader.feed_data(_frame("O\x00GameCore: answer"))
            self._reader.feed_data(_frame("O\x00GameCore: ---END---"))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed

    async def wait_closed(self) -> None:
        return None


def _stream(*, states: str, fail_command: bool = False) -> tuple[asyncio.StreamReader, _Writer]:
    reader = asyncio.StreamReader()
    return reader, _Writer(reader, states=states, fail_command=fail_command)


def test_runtime_connection_discovers_named_lua_states_without_legacy_connection() -> None:
    async def run() -> None:
        reader, writer = _stream(states="8\x00GameCore_Tuner\x00153\x00InGame")

        async def open_stream(_host: str, _port: int, _timeout: float):
            return reader, writer

        connection = await RuntimeConnection.connect(open_stream=open_stream)
        assert connection.app_identity == "APP:civ6"
        assert connection.states.gamecore == 8
        assert connection.states.ingame == 153
        await connection.close()
        assert writer.closed

    asyncio.run(run())


def test_reconnect_with_changed_lua_states_refuses_stale_read_then_uses_new_state() -> None:
    async def run() -> None:
        first = _stream(
            states="8\x00GameCore_Tuner\x00153\x00InGame", fail_command=True
        )
        second = _stream(states="9\x00GameCore_Tuner\x00154\x00InGame")
        third = _stream(states="9\x00GameCore_Tuner\x00154\x00InGame")
        streams = iter((first, second, third))

        async def open_stream(_host: str, _port: int, _timeout: float):
            return next(streams)

        connection = await RuntimeConnection.connect(open_stream=open_stream)
        adapter = CivAdapter(connection.transport, state_resolver=connection.state_for)
        request = CivReadRequest(
            tool="test_read",
            lua_code="return 1",
            decode=lambda lines: lines[0],
            coverage="test",
        )
        with pytest.raises(CivReadError):
            await adapter.read(request, observed_turn=1)
        assert connection.states.gamecore == 9
        assert second[1].closed

        result = await adapter.read(request, observed_turn=2)
        assert result.value == "answer"
        assert any(command.startswith("CMD:9:") for command in third[1].sent)

    asyncio.run(run())


def test_runtime_connection_rejects_handshake_without_both_game_states() -> None:
    async def run() -> None:
        reader, writer = _stream(states="8\x00GameCore_Tuner")

        async def open_stream(_host: str, _port: int, _timeout: float):
            return reader, writer

        with pytest.raises(RuntimeConnectionError, match="GameCore_Tuner/InGame"):
            await RuntimeConnection.connect(open_stream=open_stream)
        assert writer.closed

    asyncio.run(run())


def test_changed_state_error_has_the_runtime_boundary_type() -> None:
    assert issubclass(LuaStateChangedError, RuntimeConnectionError)
