"""Fault-injection contract for Runtime Core Replacement task B1.

These tests intentionally name the new transport boundary before its B2
implementation exists.  They document facts the legacy exception-oriented
connection API cannot expose to SessionKernel.
"""

from __future__ import annotations

import asyncio
import struct

import pytest

from civ_mcp.runtime.contracts import SendState
from civ_mcp.runtime.transport import FireTunerTransport, Frame


def _frame(payload: str, *, tag: int = 2) -> bytes:
    body = payload.encode("utf-8") + b"\x00"
    return struct.pack("<Ii", len(body), tag) + body


class _Writer:
    def __init__(self, *, write_error: BaseException | None = None, drain_error: BaseException | None = None):
        self.closed = False
        self.sent: list[bytes] = []
        self.write_error = write_error
        self.drain_error = drain_error
        self.on_write = None

    def write(self, data: bytes) -> None:
        if self.write_error is not None:
            raise self.write_error
        self.sent.append(data)
        if self.on_write is not None:
            self.on_write()

    async def drain(self) -> None:
        if self.drain_error is not None:
            raise self.drain_error

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed

    async def wait_closed(self) -> None:
        return None


def _completion(frame: Frame) -> bool:
    return frame.payload == "DONE"


def test_complete_response_after_three_seconds_uses_the_whole_budget() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        writer = _Writer()
        transport = FireTunerTransport(reader, writer)
        loop = asyncio.get_running_loop()
        writer.on_write = lambda: loop.call_later(3.0, reader.feed_data, _frame("DONE"))

        receipt = await transport.submit_command(
            "CMD:1:read()", timeout=30.0, is_complete=_completion
        )

        assert receipt.send_state is SendState.MAYBE_SENT
        assert receipt.complete
        assert receipt.frames == (Frame(tag=2, payload="DONE"),)
        assert transport.is_usable

    asyncio.run(run())


def test_cancelled_half_frame_retires_the_connection() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        writer = _Writer()
        transport = FireTunerTransport(reader, writer)
        started = asyncio.Event()

        def send_half_frame() -> None:
            reader.feed_data(_frame("DONE")[:8])
            started.set()

        writer.on_write = send_half_frame
        task = asyncio.create_task(
            transport.submit_command("CMD:1:read()", timeout=30.0, is_complete=_completion)
        )
        await started.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert not transport.is_usable
        assert writer.closed

    asyncio.run(run())


def test_presend_failure_is_explicitly_not_sent() -> None:
    async def run() -> None:
        writer = _Writer(write_error=ConnectionResetError("not connected"))
        transport = FireTunerTransport(asyncio.StreamReader(), writer)

        receipt = await transport.submit_command("CMD:1:move()", is_complete=_completion)

        assert receipt.send_state is SendState.NOT_SENT
        assert not receipt.complete
        assert writer.sent == []

    asyncio.run(run())


def test_postsend_disconnect_is_explicitly_maybe_sent() -> None:
    async def run() -> None:
        writer = _Writer(drain_error=ConnectionResetError("connection lost"))
        transport = FireTunerTransport(asyncio.StreamReader(), writer)

        receipt = await transport.submit_command("CMD:1:move()", is_complete=_completion)

        assert receipt.send_state is SendState.MAYBE_SENT
        assert not receipt.complete
        assert len(writer.sent) == 1
        assert not transport.is_usable

    asyncio.run(run())


def test_read_reconnects_only_after_a_confirmed_presend_failure() -> None:
    async def run() -> None:
        first = _Writer(write_error=ConnectionResetError("not connected"))
        second_reader = asyncio.StreamReader()
        second = _Writer()
        second.on_write = lambda: second_reader.feed_data(_frame("DONE"))
        reconnects = 0

        async def reconnect() -> tuple[asyncio.StreamReader, _Writer]:
            nonlocal reconnects
            reconnects += 1
            return second_reader, second

        transport = FireTunerTransport(asyncio.StreamReader(), first, reconnect=reconnect)
        receipt = await transport.execute_read("CMD:0:overview()", is_complete=_completion)

        assert receipt.complete
        assert reconnects == 1
        assert first.sent == []
        assert len(second.sent) == 1

    asyncio.run(run())


def test_mutation_that_might_be_sent_is_never_retried() -> None:
    async def run() -> None:
        writer = _Writer(drain_error=ConnectionResetError("connection lost"))
        reconnects = 0

        async def reconnect() -> tuple[asyncio.StreamReader, _Writer]:
            nonlocal reconnects
            reconnects += 1
            return asyncio.StreamReader(), _Writer()

        transport = FireTunerTransport(asyncio.StreamReader(), writer, reconnect=reconnect)
        receipt = await transport.execute_mutation("CMD:1:attack()", is_complete=_completion)

        assert receipt.send_state is SendState.MAYBE_SENT
        assert not receipt.complete
        assert len(writer.sent) == 1
        assert reconnects == 0

    asyncio.run(run())
