"""FireTuner transport with explicit send-boundary facts.

The Runtime Core needs to distinguish a command that demonstrably never
entered the socket from one that may have reached Civ6.  This module owns only
framing and connection integrity; it does not parse game semantics, retry a
mutation, or decide whether an operation succeeded.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import struct
from typing import Protocol

from civ_mcp.runtime.contracts import SendState


HEADER_FORMAT = "<Ii"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
COMMAND_TAG = 3


class FrameReadInterrupted(ConnectionError):
    """A frame was consumed partly, so this connection cannot be reused."""


class StreamWriterLike(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...

    def is_closing(self) -> bool: ...

    async def wait_closed(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Frame:
    """One complete FireTuner wire frame."""

    tag: int
    payload: str


@dataclass(frozen=True, slots=True)
class TransportReceipt:
    """Transport evidence without any inferred gameplay outcome.

    ``MAYBE_SENT`` is deliberately retained even when a complete response was
    received: only an adapter's domain readback can confirm the operation.
    ``error`` reports a local transport fault and is never a game conclusion.
    """

    send_state: SendState
    complete: bool
    frames: tuple[Frame, ...]
    connection_usable: bool
    error: BaseException | None = None


Reconnect = Callable[[], Awaitable[tuple[asyncio.StreamReader, StreamWriterLike]]]
Completion = Callable[[Frame], bool]


class FireTunerTransport:
    """Serial FireTuner transport for the new Runtime Core.

    It has a single owner for one TCP stream.  Any receive failure after a
    command enters ``writer.write`` retires that stream, preventing a late
    frame from being attributed to a later command.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: StreamWriterLike,
        *,
        reconnect: Reconnect | None = None,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._reconnect = reconnect
        self._lock = asyncio.Lock()
        self._retired = False
        self._reading_body = False

    @classmethod
    async def connect(
        cls,
        host: str = "127.0.0.1",
        port: int = 4318,
        *,
        timeout: float = 5.0,
        reconnect: Reconnect | None = None,
    ) -> FireTunerTransport:
        """Open a raw FireTuner stream; handshake is intentionally separate."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        return cls(reader, writer, reconnect=reconnect)

    @property
    def is_usable(self) -> bool:
        return not self._retired and not self._writer.is_closing()

    async def close(self) -> None:
        """Retire the owned stream during Runtime shutdown."""
        await self._retire()

    async def submit_command(
        self,
        payload: str,
        *,
        tag: int = COMMAND_TAG,
        timeout: float = 5.0,
        is_complete: Completion,
    ) -> TransportReceipt:
        """Submit once and return the facts known at the transport boundary.

        A failed ``write`` is ``NOT_SENT``.  Once it returns normally, the
        operation is ``MAYBE_SENT`` forever at this layer, including when a
        later ``drain``/receive timeout makes the socket unusable.
        """
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0。")

        async with self._lock:
            if not self.is_usable:
                return TransportReceipt(
                    send_state=SendState.NOT_SENT,
                    complete=False,
                    frames=(),
                    connection_usable=False,
                    error=ConnectionError("FireTuner connection is not usable."),
                )

            data = _encode_frame(tag, payload)
            try:
                self._writer.write(data)
            except asyncio.CancelledError:
                await self._retire()
                raise
            except BaseException as exc:
                await self._retire()
                return TransportReceipt(
                    send_state=SendState.NOT_SENT,
                    complete=False,
                    frames=(),
                    connection_usable=False,
                    error=exc,
                )

            frames: list[Frame] = []
            try:
                async with asyncio.timeout(timeout):
                    await self._writer.drain()
                    while True:
                        frame = await self._recv_frame()
                        frames.append(frame)
                        if is_complete(frame):
                            return TransportReceipt(
                                send_state=SendState.MAYBE_SENT,
                                complete=True,
                                frames=tuple(frames),
                                connection_usable=True,
                            )
            except asyncio.CancelledError:
                await self._retire()
                raise
            except BaseException as exc:
                await self._retire()
                return TransportReceipt(
                    send_state=SendState.MAYBE_SENT,
                    complete=False,
                    frames=tuple(frames),
                    connection_usable=False,
                    error=exc,
                )

    async def execute_read(
        self,
        payload: str,
        *,
        tag: int = COMMAND_TAG,
        timeout: float = 5.0,
        is_complete: Completion,
    ) -> TransportReceipt:
        """Retry a read once, but only when the first command was NOT_SENT."""
        receipt = await self.submit_command(
            payload, tag=tag, timeout=timeout, is_complete=is_complete
        )
        if receipt.send_state is not SendState.NOT_SENT or self._reconnect is None:
            return receipt

        try:
            reader, writer = await self._reconnect()
        except asyncio.CancelledError:
            raise
        except BaseException:
            return receipt
        await self._replace_connection(reader, writer)
        return await self.submit_command(
            payload, tag=tag, timeout=timeout, is_complete=is_complete
        )

    async def execute_mutation(
        self,
        payload: str,
        *,
        tag: int = COMMAND_TAG,
        timeout: float = 5.0,
        is_complete: Completion,
    ) -> TransportReceipt:
        """Submit a mutation exactly once; callers must resolve MAYBE_SENT."""
        return await self.submit_command(
            payload, tag=tag, timeout=timeout, is_complete=is_complete
        )

    async def _recv_frame(self) -> Frame:
        if self._reading_body:
            raise FrameReadInterrupted("上一条 FireTuner frame 尚未读完。")
        header = await self._reader.readexactly(HEADER_SIZE)
        length, tag = struct.unpack(HEADER_FORMAT, header)
        self._reading_body = True
        try:
            body = await self._reader.readexactly(length)
        except asyncio.CancelledError:
            raise
        except BaseException:
            raise FrameReadInterrupted("FireTuner frame body 读取中断。") from None
        finally:
            # The stream is retired by submit_command if this read did not
            # finish.  Resetting the flag only records that the local await is
            # no longer active; it never makes the stream reusable again.
            self._reading_body = False
        return Frame(tag=tag, payload=body.rstrip(b"\x00").decode("utf-8", "replace"))

    async def _replace_connection(
        self, reader: asyncio.StreamReader, writer: StreamWriterLike
    ) -> None:
        async with self._lock:
            self._reader = reader
            self._writer = writer
            self._retired = False
            self._reading_body = False

    async def _retire(self) -> None:
        if self._retired:
            return
        self._retired = True
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, OSError, RuntimeError):
            pass


def _encode_frame(tag: int, payload: str) -> bytes:
    body = payload.encode("utf-8") + b"\x00"
    return struct.pack(HEADER_FORMAT, len(body), tag) + body
