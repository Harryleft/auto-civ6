"""Offline wire-level regressions: real StreamReaders, no game or sockets."""

import asyncio
import struct

import pytest

from civ_mcp import tuner_client
from civ_mcp.connection import (
    CommandNotSentError,
    CommandTimeoutError,
    GameConnection,
    MutationOutcomeUnknownError,
)
from civ_mcp.lua._helpers import SENTINEL


def _frame(payload):
    body = payload.encode() + b"\x00"
    return struct.pack(tuner_client.HEADER_FMT, len(body), 2) + body


def _output(text):
    return _frame(f"O\x00InGame: {text}")


class _Writer:
    def __init__(self, on_send=None):
        self.closed = False
        self.sent = []
        self.on_send = on_send

    def write(self, data):
        self.sent.append(data[8:-1].decode())
        if self.on_send:
            self.on_send()

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    def is_closing(self):
        return self.closed

    async def wait_closed(self):
        pass


def _connection(on_send=None):
    conn = GameConnection()
    reader = asyncio.StreamReader()
    writer = _Writer(on_send)
    conn._reader, conn._writer = reader, writer
    conn.gamecore_index, conn.ingame_index = 0, 1
    conn.lua_states = {0: "GameCore_Tuner", 1: "InGame"}
    return conn, reader, writer


def test_response_after_three_seconds_silence_respects_thirty_second_budget():
    async def run():
        conn, reader, writer = _connection()
        loop = asyncio.get_running_loop()
        writer.on_send = lambda: loop.call_later(
            3.0, reader.feed_data, _output("OK") + _output(SENTINEL)
        )
        started = loop.time()
        assert await conn.execute_mutation("act()", timeout=30) == ["OK"]
        assert loop.time() - started >= 3.0
        assert len(writer.sent) == 1

    asyncio.run(run())


@pytest.mark.parametrize("body_prefix", [b"", b"ABC"])
def test_cancelled_frame_body_cannot_be_reinterpreted_as_a_header(body_prefix):
    async def run():
        reader = asyncio.StreamReader()
        frame = _frame("ABCDEFGH")
        reader.feed_data(frame[:8] + body_prefix)
        task = asyncio.create_task(tuner_client.recv_message(reader))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        reader.feed_data(frame[8 + len(body_prefix):] + _frame("next"))
        with pytest.raises(tuner_client.FrameReadInterrupted):
            await tuner_client.recv_message(reader)

    asyncio.run(run())


def test_timeout_during_partial_header_can_resume_without_losing_bytes():
    async def run():
        reader = asyncio.StreamReader()
        frame = _frame("message")
        reader.feed_data(frame[:4])
        assert await tuner_client.recv_message_timeout(reader, timeout=0.01) is None
        reader.feed_data(frame[4:] + _frame("next"))
        assert (await tuner_client.recv_message(reader)).payload == "message"
        assert (await tuner_client.recv_message(reader)).payload == "next"

    asyncio.run(run())


def test_drain_timeout_on_half_frame_requires_a_new_connection():
    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(_frame("ABCDEFGH")[:11])
        with pytest.raises(tuner_client.FrameReadInterrupted):
            await tuner_client.drain_messages(reader, timeout=0.01)
        with pytest.raises(tuner_client.FrameReadInterrupted):
            await tuner_client.recv_message(reader)

    asyncio.run(run())


def test_real_deadline_retires_old_socket_and_late_receipt_cannot_reach_next_query(monkeypatch):
    async def run():
        conn, old_reader, old_writer = _connection()
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(MutationOutcomeUnknownError) as caught:
            await conn.execute_mutation("ACTION_ENDTURN", timeout=0.08)
        assert isinstance(caught.value.__cause__, CommandTimeoutError)
        assert 0.07 <= loop.time() - started < 0.5
        assert old_writer.closed
        assert len(old_writer.sent) == 1
        assert not conn.is_connected
        old_reader.feed_data(_output("OLD") + _output(SENTINEL))

        new_reader = asyncio.StreamReader()
        new_writer = _Writer(lambda: new_reader.feed_data(_output("NEW") + _output(SENTINEL)))
        handshakes = []

        async def connect(*args):
            return new_reader, new_writer

        async def handshake(reader, writer):
            handshakes.append(True)
            return "Civ6", ["4", "GameCore_Tuner", "9", "InGame"]

        monkeypatch.setattr(tuner_client, "connect", connect)
        monkeypatch.setattr(tuner_client, "handshake", handshake)
        assert await conn.execute_read("read_new_turn()") == ["NEW"]
        assert new_writer.sent == ["CMD:4:read_new_turn()"]
        assert len(old_writer.sent) == 1
        assert handshakes == [True]

    asyncio.run(run())


def test_cancelled_end_turn_response_retires_stream_without_clearing_pending():
    async def run():
        conn, reader, writer = _connection()
        conn.turn_in_progress = True
        sent = asyncio.Event()

        def begin_reply():
            reader.feed_data(_output("OK")[:8])
            sent.set()

        writer.on_send = begin_reply
        task = asyncio.create_task(conn.execute_mutation(
            "ACTION_ENDTURN", timeout=30, turn_action="end_turn"
        ))
        await sent.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert writer.closed
        assert not conn.is_connected
        assert conn.turn_in_progress
        assert len(writer.sent) == 1

    asyncio.run(run())


def test_send_backpressure_is_bounded_by_command_deadline():
    async def run():
        conn, _, writer = _connection()

        async def blocked_drain():
            await asyncio.Event().wait()

        writer.drain = blocked_drain
        with pytest.raises(MutationOutcomeUnknownError):
            await conn.execute_mutation("act()", timeout=0.05)
        assert writer.closed
        assert len(writer.sent) == 1

    asyncio.run(run())


def test_queued_ingame_query_is_rechecked_after_lock_is_acquired():
    async def run():
        conn, _, writer = _connection()
        await conn._lock.acquire()
        task = asyncio.create_task(conn.execute_write("watch_popup()"))
        await asyncio.sleep(0)
        conn.turn_in_progress = True
        conn._lock.release()
        with pytest.raises(CommandNotSentError):
            await task
        assert writer.sent == []
        assert conn.is_connected

    asyncio.run(run())


def test_phase_change_during_presend_drain_is_rechecked(monkeypatch):
    async def run():
        conn, _, writer = _connection()

        async def drain(*args, **kwargs):
            conn.turn_in_progress = True
            return []

        monkeypatch.setattr(tuner_client, "drain_messages", drain)
        with pytest.raises(CommandNotSentError):
            await conn.execute_write("watch_popup()")
        assert writer.sent == []

    asyncio.run(run())


@pytest.mark.parametrize("turn_action", ["end_turn", "diplomacy", "congress", "load"])
def test_only_named_turn_operations_bypass_ingame_phase_guard(turn_action):
    async def run():
        conn, reader, writer = _connection()
        writer.on_send = lambda: reader.feed_data(_output(SENTINEL))
        conn.turn_in_progress = True
        with pytest.raises(CommandNotSentError):
            await conn.execute_in_state(1, "unrelated()", mutation=True)
        with pytest.raises(ValueError):
            await conn.execute_write("unrelated()", turn_action="anything")
        assert await conn.execute_mutation("handle_input()", turn_action=turn_action) == []
        assert await conn.execute_read("observe_turn()") == []
        assert len(writer.sent) == 2

    asyncio.run(run())


def test_unknown_reload_blocks_mutations_in_both_contexts():
    async def run():
        conn, reader, writer = _connection()
        writer.on_send = lambda: reader.feed_data(_output(SENTINEL))
        conn.reload_pending = True
        for context in ("ingame", "gamecore"):
            with pytest.raises(CommandNotSentError):
                await conn.execute_mutation("act()", context=context)
        assert conn.mutation_revision == 0
        assert await conn.execute_read("observe_world()") == []
        assert await conn.execute_mutation("load()", turn_action="load") == []
        assert len(writer.sent) == 2

    asyncio.run(run())


def test_concurrent_first_queries_share_one_handshake(monkeypatch):
    async def run():
        conn = GameConnection()
        reader = asyncio.StreamReader()
        writer = _Writer(lambda: reader.feed_data(_output(SENTINEL)))
        handshakes = []

        async def connect(*args):
            await asyncio.sleep(0)
            return reader, writer

        async def handshake(*args):
            handshakes.append(True)
            await asyncio.sleep(0)
            return "Civ6", ["0", "GameCore_Tuner", "1", "InGame"]

        monkeypatch.setattr(tuner_client, "connect", connect)
        monkeypatch.setattr(tuner_client, "handshake", handshake)
        assert await asyncio.gather(conn.execute_read("a()"), conn.execute_read("b()")) == [[], []]
        assert handshakes == [True]
        assert len(writer.sent) == 2

    asyncio.run(run())


def test_query_retry_resolves_new_state_index(monkeypatch):
    async def run():
        conn, _, writer = _connection()

        def broken_send():
            raise OSError("old socket")

        writer.on_send = broken_send
        fresh = asyncio.StreamReader()
        fresh_writer = _Writer(lambda: fresh.feed_data(_output(SENTINEL)))

        async def connect(*args):
            return fresh, fresh_writer

        async def handshake(*args):
            return "Civ6", ["7", "GameCore_Tuner", "8", "InGame"]

        monkeypatch.setattr(tuner_client, "connect", connect)
        monkeypatch.setattr(tuner_client, "handshake", handshake)
        assert await conn.execute_read("read()") == []
        assert writer.sent == ["CMD:0:read()"]
        assert fresh_writer.sent == ["CMD:7:read()"]

    asyncio.run(run())


def test_trailing_partial_frame_retires_socket_but_keeps_confirmed_result():
    async def run():
        conn, reader, writer = _connection()
        writer.on_send = lambda: reader.feed_data(
            _output("CONFIRMED") + _output(SENTINEL) + _output("late")[:9]
        )
        assert await conn.execute_mutation("act()") == ["CONFIRMED"]
        assert writer.closed
        assert not conn.is_connected
        assert len(writer.sent) == 1

    asyncio.run(run())


def test_post_sentinel_drain_cannot_extend_deadline_or_lose_confirmation(monkeypatch):
    async def run():
        conn, reader, writer = _connection()
        writer.on_send = lambda: reader.feed_data(_output("CONFIRMED") + _output(SENTINEL))
        drains = 0

        async def drain(*args, **kwargs):
            nonlocal drains
            drains += 1
            if drains == 2:
                await asyncio.Event().wait()
            return []

        monkeypatch.setattr(tuner_client, "drain_messages", drain)
        started = asyncio.get_running_loop().time()
        assert await conn.execute_mutation("act()", timeout=0.05) == ["CONFIRMED"]
        assert asyncio.get_running_loop().time() - started < 0.5
        assert writer.closed

    asyncio.run(run())


def test_external_reconnect_waits_for_existing_command(monkeypatch):
    async def run():
        conn, reader, writer = _connection()
        sent = asyncio.Event()
        writer.on_send = sent.set
        fresh = asyncio.StreamReader()
        fresh_writer = _Writer()
        reconnects = []

        async def connect(*args):
            reconnects.append(True)
            return fresh, fresh_writer

        async def handshake(*args):
            return "Civ6", ["0", "GameCore_Tuner", "1", "InGame"]

        monkeypatch.setattr(tuner_client, "connect", connect)
        monkeypatch.setattr(tuner_client, "handshake", handshake)
        command = asyncio.create_task(conn.execute_mutation("act()"))
        await sent.wait()
        reconnect = asyncio.create_task(conn.reconnect())
        await asyncio.sleep(0)
        assert reconnects == []
        assert not writer.closed
        reader.feed_data(_output("CONFIRMED") + _output(SENTINEL))
        assert await command == ["CONFIRMED"]
        await reconnect
        assert reconnects == [True]
        assert writer.closed
        assert conn._writer is fresh_writer

    asyncio.run(run())


def test_query_reconnect_does_not_start_a_second_full_budget(monkeypatch):
    async def run():
        conn, _, writer = _connection()

        async def failed_send():
            await asyncio.sleep(0.04)
            raise OSError("lost socket")

        writer.drain = failed_send
        fresh = asyncio.StreamReader()
        fresh_writer = _Writer()
        loop = asyncio.get_running_loop()
        fresh_writer.on_send = lambda: loop.call_later(
            0.09, fresh.feed_data, _output(SENTINEL)
        )

        async def connect(*args):
            return fresh, fresh_writer

        async def handshake(*args):
            return "Civ6", ["0", "GameCore_Tuner", "1", "InGame"]

        monkeypatch.setattr(tuner_client, "connect", connect)
        monkeypatch.setattr(tuner_client, "handshake", handshake)
        with pytest.raises(CommandTimeoutError):
            await conn.execute_read("read()", timeout=0.14)
        assert len(writer.sent) == 1
        assert len(fresh_writer.sent) == 1
        assert fresh_writer.closed

    asyncio.run(run())


def test_queued_old_world_diplomacy_is_not_sent_after_reload_finishes():
    async def run():
        conn, _, writer = _connection()
        await conn._lock.acquire()
        task = asyncio.create_task(conn.execute_mutation(
            "close_old_war_session()", turn_action="diplomacy", expected_world_epoch=0
        ))
        await asyncio.sleep(0)
        conn.world_epoch = 1
        conn.reload_pending = False
        conn.turn_in_progress = False
        conn._lock.release()
        with pytest.raises(CommandNotSentError):
            await task
        assert writer.sent == []
        assert conn.mutation_revision == 0

    asyncio.run(run())


def test_world_change_during_presend_drain_rejects_old_request(monkeypatch):
    async def run():
        conn, _, writer = _connection()

        async def drain(*args, **kwargs):
            conn.world_epoch = 1
            return []

        monkeypatch.setattr(tuner_client, "drain_messages", drain)
        with pytest.raises(CommandNotSentError):
            await conn.execute_mutation(
                "old_vote()", turn_action="congress", expected_world_epoch=0
            )
        assert writer.sent == []

    asyncio.run(run())


@pytest.mark.parametrize("channel", ["mutation", "write", "state"])
def test_ordinary_mutation_captures_world_before_queueing_without_explicit_epoch(channel):
    async def run():
        conn, _, writer = _connection()
        await conn._lock.acquire()
        if channel == "mutation":
            operation = conn.execute_mutation("purchase()")
        elif channel == "write":
            operation = conn.execute_write("purchase()", mutation=True)
        else:
            operation = conn.execute_in_state(1, "purchase()", mutation=True)
        task = asyncio.create_task(operation)
        await asyncio.sleep(0)
        conn.world_epoch += 1
        conn.reload_pending = False
        conn._lock.release()
        with pytest.raises(CommandNotSentError):
            await task
        assert writer.sent == []
        assert conn.mutation_revision == 0

    asyncio.run(run())
