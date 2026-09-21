"""In-memory FireTuner transport for unit tests.

The tuner layer talks to the game over an asyncio TCP stream. These helpers let
tests drive it with in-memory streams, so the code under test — framing,
response collection, reconnect, state discovery — runs for real instead of being
mocked out. Only the five module-level entry points in `tuner_client` (connect,
handshake, send_message, recv_message_timeout, drain_messages) ever get replaced,
and only when a test needs a scripted game.

Everything here must be used from *inside* a running loop: an `asyncio.StreamReader`
binds to the loop that created it, so a test that connects in one `asyncio.run()`
and reads in another would be reading from a closed loop.
"""

from __future__ import annotations

import asyncio
import struct

from civ_mcp import tuner_client
from civ_mcp.connection import GameConnection

run = asyncio.run


class FakeWriter:
    """Captures everything written so tests can assert on the wire bytes."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.closed = False
        self.closing = False
        self.drain_calls = 0

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        self.drain_calls += 1

    def close(self) -> None:
        self.closed = True
        self.closing = True

    async def wait_closed(self) -> None:
        pass

    def is_closing(self) -> bool:
        return self.closing

    @property
    def sent(self) -> bytes:
        return b"".join(self.chunks)

    def messages(self) -> list[tuple[int, str]]:
        """Decode everything written back into (tag, payload) pairs."""
        out: list[tuple[int, str]] = []
        buf, off = self.sent, 0
        while off < len(buf):
            length, tag = struct.unpack(
                tuner_client.HEADER_FMT, buf[off : off + tuner_client.HEADER_SIZE]
            )
            off += tuner_client.HEADER_SIZE
            data = buf[off : off + length]
            off += length
            out.append((tag, data.rstrip(b"\x00").decode("utf-8")))
        return out


def frame(tag: int, payload: str) -> bytes:
    """Build one wire message the way the game would send it."""
    data = payload.encode("utf-8") + b"\x00"
    return struct.pack(tuner_client.HEADER_FMT, len(data), tag) + data


def make_reader(*chunks: bytes, eof: bool = False) -> asyncio.StreamReader:
    """A reader pre-loaded with raw bytes. Must be called inside a running loop."""
    reader = asyncio.StreamReader()
    for chunk in chunks:
        reader.feed_data(chunk)
    if eof:
        reader.feed_eof()
    return reader


async def _no_drain(reader, timeout=0.5):
    return []


async def make_connection(
    monkeypatch,
    incoming: tuple[bytes, ...] = (),
    states: tuple[str, ...] = ("0", "GameCore_Tuner", "1", "InGame"),
    patch_drain: bool = True,
) -> tuple[GameConnection, FakeWriter]:
    """Connect a `GameConnection` to an in-memory stream instead of a live game.

    `states` is the raw state list as the wire carries it: alternating
    index/name pairs, e.g. ``("0", "GameCore_Tuner", "1", "InGame")``.

    `incoming` is re-queued on every (re)connect, so reconnect paths see a fresh
    socket carrying the same scripted bytes. `patch_drain` silences the two fixed
    drains so protocol-loop tests don't each pay ~300ms; the tests that are *about*
    that cost pass `patch_drain=False`.
    """
    writer = FakeWriter()

    async def fake_connect(host, port, timeout=5.0):
        return make_reader(*incoming), writer

    async def fake_handshake(reader, writer_):
        return "APP:Civ6", list(states)

    monkeypatch.setattr(tuner_client, "connect", fake_connect)
    monkeypatch.setattr(tuner_client, "handshake", fake_handshake)
    if patch_drain:
        monkeypatch.setattr(tuner_client, "drain_messages", _no_drain)

    conn = GameConnection()
    await conn.connect()
    return conn, writer
