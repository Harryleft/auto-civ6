"""Unit tests for the FireTuner wire protocol (`civ_mcp.tuner_client`).

These run entirely against in-memory streams — no socket, no live game. They pin
the framing contract (what actually goes on the wire), the response-read
primitives, and the two behaviours that dominate per-command latency.
"""

from __future__ import annotations

import asyncio
import struct
import time

import pytest

from civ_mcp import tuner_client
from fake_tuner import FakeWriter, frame, make_reader, run

# ---------------------------------------------------------------------------
# Framing — send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    def test_frames_length_tag_and_null_terminator(self):
        """Header is <4-byte LE total length><4-byte LE tag>, then payload+NUL."""

        async def go():
            w = FakeWriter()
            await tuner_client.send_message(w, tuner_client.TAG_COMMAND, "CMD:2:print(1)")
            return w.sent

        sent = run(go())
        length, tag = struct.unpack(tuner_client.HEADER_FMT, sent[:8])
        assert tag == tuner_client.TAG_COMMAND
        assert sent[8:] == b"CMD:2:print(1)\x00"
        # The declared length covers the payload and its terminator, not the header.
        assert length == len(sent) - tuner_client.HEADER_SIZE

    def test_length_counts_encoded_bytes_not_characters(self):
        """Lua carrying non-ASCII (city names, leader names) must declare byte length."""

        async def go():
            w = FakeWriter()
            await tuner_client.send_message(w, tuner_client.TAG_COMMAND, "吉尔")
            return w.sent

        sent = run(go())
        length, _ = struct.unpack(tuner_client.HEADER_FMT, sent[:8])
        assert length == len("吉尔".encode("utf-8")) + 1 == 7

    def test_drains_the_writer(self):
        async def go():
            w = FakeWriter()
            await tuner_client.send_message(w, tuner_client.TAG_COMMAND, "x")
            return w.drain_calls

        assert run(go()) == 1


# ---------------------------------------------------------------------------
# Framing — recv_message
# ---------------------------------------------------------------------------


class TestRecvMessage:
    def test_round_trips_a_framed_message(self):
        async def go():
            r = make_reader(frame(tuner_client.TAG_HANDSHAKE, "APP:Civ6"))
            return await tuner_client.recv_message(r)

        msg = run(go())
        assert (msg.tag, msg.payload) == (tuner_client.TAG_HANDSHAKE, "APP:Civ6")

    def test_strips_trailing_null_padding(self):
        """The terminator is not always a single NUL; none of it is payload."""
        body = b"x" + b"\x00" * 3
        raw = struct.pack(tuner_client.HEADER_FMT, len(body), tuner_client.TAG_COMMAND) + body

        async def go():
            return await tuner_client.recv_message(make_reader(raw))

        assert run(go()).payload == "x"

    def test_preserves_embedded_null(self):
        """print() output is prefixed `O\\x00<context>: ` — that NUL is payload."""
        payload = "O\x00GameCore_Tuner: 42"

        async def go():
            r = make_reader(frame(tuner_client.TAG_COMMAND, payload))
            return await tuner_client.recv_message(r)

        assert run(go()).payload == payload

    def test_raises_on_truncated_header(self):
        async def go():
            return await tuner_client.recv_message(make_reader(b"\x05\x00", eof=True))

        with pytest.raises(asyncio.IncompleteReadError):
            run(go())

    def test_raises_on_truncated_body(self):
        """A body shorter than the declared length means the game died mid-write."""
        raw = struct.pack(tuner_client.HEADER_FMT, 100, tuner_client.TAG_COMMAND) + b"short"

        async def go():
            return await tuner_client.recv_message(make_reader(raw, eof=True))

        with pytest.raises(asyncio.IncompleteReadError):
            run(go())


# ---------------------------------------------------------------------------
# recv_message_timeout / drain_messages — the latency-critical primitives
# ---------------------------------------------------------------------------


class TestRecvMessageTimeout:
    def test_returns_none_and_waits_out_the_full_timeout(self):
        """An idle socket is not detected early — the timeout is a floor, not a ceiling."""

        async def go():
            r = make_reader()  # never fed, never closed
            started = time.perf_counter()
            msg = await tuner_client.recv_message_timeout(r, timeout=0.05)
            return msg, time.perf_counter() - started

        msg, elapsed = run(go())
        assert msg is None
        assert elapsed >= 0.05

    def test_returns_promptly_when_a_message_is_waiting(self):
        async def go():
            r = make_reader(frame(tuner_client.TAG_COMMAND, "O\x00ctx: hi"))
            started = time.perf_counter()
            msg = await tuner_client.recv_message_timeout(r, timeout=1.0)
            return msg, time.perf_counter() - started

        msg, elapsed = run(go())
        assert msg.payload == "O\x00ctx: hi"
        assert elapsed < 0.5


class TestDrainMessages:
    def test_returns_everything_queued_then_stops(self):
        async def go():
            r = make_reader(
                frame(tuner_client.TAG_COMMAND, "a"),
                frame(tuner_client.TAG_COMMAND, "b"),
            )
            return await tuner_client.drain_messages(r, timeout=0.02)

        assert [m.payload for m in run(go())] == ["a", "b"]

    def test_always_burns_the_full_timeout_on_an_empty_stream(self):
        """Pins the fixed cost measured in the field.

        `GameConnection._locked_execute` drains before *and* after every command
        (0.1s then 0.2s). When the stream is quiet — the common case — both drains
        run to their full timeout, so every command pays ~300ms of pure sleep.
        This test exists so that cost cannot change silently.
        """

        async def go():
            r = make_reader()
            started = time.perf_counter()
            msgs = await tuner_client.drain_messages(r, timeout=0.1)
            return msgs, time.perf_counter() - started

        msgs, elapsed = run(go())
        assert msgs == []
        assert elapsed >= 0.1


# ---------------------------------------------------------------------------
# handshake
# ---------------------------------------------------------------------------


async def _noop(reader, timeout=0.5):
    return []


class TestHandshake:
    """Handshake's own parsing, with the transport primitives scripted.

    The leading drain would otherwise eat the replies this test pre-queues, so the
    drain is silenced and `recv_message_timeout` is fed in order.
    """

    def _script(self, monkeypatch, replies):
        sent: list[tuple[int, str]] = []

        async def fake_send(writer, tag, payload):
            sent.append((tag, payload))

        queue = list(replies)

        async def fake_recv(reader, timeout=2.0):
            if not queue:
                return None
            payload = queue.pop(0)
            if payload is None:
                return None
            return tuner_client.Message(tag=tuner_client.TAG_HANDSHAKE, payload=payload)

        monkeypatch.setattr(tuner_client, "send_message", fake_send)
        monkeypatch.setattr(tuner_client, "recv_message_timeout", fake_recv)
        monkeypatch.setattr(tuner_client, "drain_messages", _noop)
        return sent

    def test_asks_for_identity_then_state_list_in_that_order(self, monkeypatch):
        sent = self._script(monkeypatch, ["APP:Civ6", "0\x00GameCore_Tuner\x001\x00InGame"])

        async def go():
            await tuner_client.handshake(make_reader(), FakeWriter())

        run(go())
        assert sent == [
            (tuner_client.TAG_HANDSHAKE, "APP:"),
            (tuner_client.TAG_HANDSHAKE, "LSQ:"),
        ]

    def test_parses_null_separated_state_list(self, monkeypatch):
        """The wire carries the list as alternating index/name pairs."""
        self._script(monkeypatch, ["APP:Civ6", "0\x00GameCore_Tuner\x001\x00InGame"])

        async def go():
            return await tuner_client.handshake(make_reader(), FakeWriter())

        identity, states = run(go())
        assert identity == "APP:Civ6"
        assert states == ["0", "GameCore_Tuner", "1", "InGame"]

    def test_falls_back_to_newline_separated_state_list(self, monkeypatch):
        """Some builds return the list newline-separated; a single entry signals that."""
        self._script(monkeypatch, ["APP:Civ6", "0\nGameCore_Tuner\n1\nInGame"])

        async def go():
            return await tuner_client.handshake(make_reader(), FakeWriter())

        _, states = run(go())
        assert states == ["0", "GameCore_Tuner", "1", "InGame"]

    def test_reports_missing_identity_instead_of_raising(self, monkeypatch):
        """At the main menu the game may not answer APP: — the caller needs a value."""
        self._script(monkeypatch, [None])

        async def go():
            return await tuner_client.handshake(make_reader(), FakeWriter())

        identity, states = run(go())
        assert identity == "<no response>"
        assert states == []
