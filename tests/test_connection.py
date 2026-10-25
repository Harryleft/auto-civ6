"""Unit tests for `civ_mcp.connection.GameConnection`.

The class talks to the game through five module-level functions in
`tuner_client`; those are the only things replaced here. Everything else — Lua
state discovery, output parsing, the send/collect loop, reconnect-on-dead-socket
— is the real code running against an in-memory stream.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from civ_mcp import tuner_client
from civ_mcp.connection import GameConnection, LuaError, _parse_output
from fake_tuner import frame, make_connection, run

TAG = tuner_client.TAG_COMMAND


@pytest.fixture(autouse=True)
def request_tokens(monkeypatch):
    tokens = iter(f"request{i}" for i in range(1, 100))
    monkeypatch.setattr(uuid, "uuid4", lambda: SimpleNamespace(hex=next(tokens)))


# ---------------------------------------------------------------------------
# Lua state discovery
# ---------------------------------------------------------------------------


class TestStateDiscovery:
    def test_finds_gamecore_and_ingame_indexes(self, monkeypatch):
        """The state list arrives as alternating index/name pairs."""

        async def go():
            return await make_connection(
                monkeypatch, states=("0", "GameCore_Tuner", "1", "InGame")
            )

        conn, _ = run(go())
        assert conn.lua_states == {0: "GameCore_Tuner", 1: "InGame"}
        assert (conn.gamecore_index, conn.ingame_index) == (0, 1)

    def test_finds_states_that_are_not_at_the_start_of_the_list(self, monkeypatch):
        """FrontEnd states usually come first; the game states follow."""

        async def go():
            return await make_connection(
                monkeypatch, states=("0", "FrontEnd", "1", "GameCore_Tuner", "2", "InGame")
            )

        conn, _ = run(go())
        assert (conn.gamecore_index, conn.ingame_index) == (1, 2)

    def test_takes_the_first_match_when_a_name_repeats(self, monkeypatch):
        """Reloading can surface the same state twice; the first index is the live one."""

        async def go():
            return await make_connection(monkeypatch, states=("0", "InGame", "1", "InGame"))

        conn, _ = run(go())
        assert conn.ingame_index == 0

    def test_resyncs_past_an_entry_that_is_not_an_index(self, monkeypatch):
        """A malformed entry must not shift every following index by one."""

        async def go():
            return await make_connection(
                monkeypatch, states=("0", "GameCore_Tuner", "bogus", "1", "InGame")
            )

        conn, _ = run(go())
        assert conn.lua_states == {0: "GameCore_Tuner", 1: "InGame"}
        assert (conn.gamecore_index, conn.ingame_index) == (0, 1)

    def test_drops_a_trailing_entry_with_no_name(self, monkeypatch):
        async def go():
            return await make_connection(monkeypatch, states=("0", "GameCore_Tuner", "7"))

        conn, _ = run(go())
        assert conn.lua_states == {0: "GameCore_Tuner"}


class TestConnectFailure:
    def test_raises_an_actionable_error_when_nothing_is_listening(self, monkeypatch):
        async def refuse(host, port, timeout=5.0):
            raise OSError("Connection refused")

        monkeypatch.setattr(tuner_client, "connect", refuse)

        with pytest.raises(ConnectionError, match="EnableTuner"):
            run(GameConnection().connect())

    def test_refusal_mentions_the_single_firetuner_client_slot(self, monkeypatch):
        """A running game can still refuse: one client owns the tuner connection."""

        async def refuse(host, port, timeout=5.0):
            raise ConnectionRefusedError(61, "Connection refused")

        monkeypatch.setattr(tuner_client, "connect", refuse)

        with pytest.raises(ConnectionError) as caught:
            run(GameConnection().connect())

        message = str(caught.value)
        assert "Only one FireTuner client can hold the connection" in message
        assert "leftover civ-mcp process" in message


class TestConnectionLifecycle:
    def test_is_connected_is_false_before_connecting(self):
        assert GameConnection().is_connected is False

    def test_is_connected_follows_connect_and_disconnect(self, monkeypatch):
        async def go():
            conn, _ = await make_connection(monkeypatch)
            connected = conn.is_connected
            await conn.disconnect()
            return conn, connected

        conn, connected = run(go())
        assert connected is True
        assert conn.is_connected is False
        assert conn._reader is None and conn._writer is None

    def test_ensure_game_states_raises_when_sitting_at_the_main_menu(self, monkeypatch):
        """Connected but with no game loaded — reconnecting cannot invent the states."""

        async def go():
            conn, _ = await make_connection(
                monkeypatch, states=("0", "FrontEnd", "1", "GameCore")
            )
            await conn._ensure_game_states()

        with pytest.raises(ConnectionError, match="main menu"):
            run(go())


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


class TestParseOutput:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            # print() output: "O\x00<context>: <value>"
            ("O\x00GameCore_Tuner: 42", "42"),
            # Only the first ": " separates context from value.
            ("O\x00ctx: a: b", "a: b"),
            # An empty value is still an output message, not a non-output one.
            ("O\x00ctx: ", ""),
            # No separator at all — falls back to stripping the prefix.
            ("O\x00no separator", "no separator"),
            # The fallback must not eat an 'O' that belongs to the value.
            ("O\x00Oasis", "Oasis"),
            # Empty ack written with tag=3 — not output, must be ignored.
            ("", None),
            # Any other message on the wire is not output either.
            ("hello", None),
        ],
    )
    def test_extracts_the_value_or_reports_non_output(self, payload, expected):
        assert _parse_output(payload) == expected


# ---------------------------------------------------------------------------
# The send/collect loop
# ---------------------------------------------------------------------------


class TestFramedResponses:
    def test_wraps_code_in_the_explicit_state_and_preserves_a_trailing_return(self, monkeypatch):
        async def go():
            conn, writer = await make_connection(
                monkeypatch, incoming=(frame(TAG, "O\x00c: CIVMCP|request1|END"),)
            )
            await conn.execute_in_state(2, "return 1 -- trailing comment", timeout=1.0)
            return writer

        messages = run(go()).messages()
        assert len(messages) == 1
        assert messages[0][1].startswith("CMD:2:do\n")
        assert "(function()\nreturn 1 -- trailing comment\nend)()" in messages[0][1]

    def test_collects_output_lines_until_its_request_completion(self, monkeypatch):
        async def go():
            conn, _ = await make_connection(
                monkeypatch,
                incoming=(
                    frame(TAG, "O\x00ctx: CIVMCP|request1|OUT|one"),
                    frame(TAG, "O\x00ctx: CIVMCP|request1|OUT|two"),
                    frame(TAG, "O\x00ctx: CIVMCP|request1|END"),
                    frame(TAG, "O\x00ctx: after sentinel"),
                ),
            )
            return await conn.execute_read("print()", timeout=1.0)

        assert run(go()) == ["one", "two"]

    def test_ignores_messages_that_are_not_output(self, monkeypatch):
        async def go():
            conn, _ = await make_connection(
                monkeypatch,
                incoming=(frame(TAG, ""), frame(TAG, "O\x00c: CIVMCP|request1|END")),
            )
            return await conn.execute_read("print()", timeout=1.0)

        assert run(go()) == []

    def test_raises_lua_error_on_an_err_response(self, monkeypatch):
        async def go():
            conn, _ = await make_connection(
                monkeypatch, incoming=(frame(TAG, "ERR:attempt to index nil"),)
            )
            return await conn.execute_read("bad()", timeout=1.0)

        with pytest.raises(LuaError, match="attempt to index nil"):
            run(go())

    def test_a_silent_socket_raises_instead_of_returning_empty_lines(self, monkeypatch):
        """A silent socket is a dead connection, not an empty result.

        A response without the request completion marker is incomplete. The
        caller gets a TimeoutError, and the uncertain socket is retired without
        replaying a potentially executed command.
        """

        async def go():
            conn, _ = await make_connection(monkeypatch, incoming=())
            return await conn.execute_read("print()", timeout=0.01)

        with pytest.raises(TimeoutError, match="Incomplete response from Civ 6"):
            run(go())

    def test_does_not_cancel_and_drain_frames_between_commands(self, monkeypatch):
        async def forbidden(*args, **kwargs):
            raise AssertionError("No frame drains in the command path")
        monkeypatch.setattr(tuner_client, "drain_messages", forbidden)
        async def go():
            conn, _ = await make_connection(monkeypatch, incoming=(frame(TAG, "O\x00c: CIVMCP|request1|END"),), patch_drain=False)
            assert await conn.execute_read("print()", 1.0) == []
        run(go())


# ---------------------------------------------------------------------------
# Reconnect and routing
# ---------------------------------------------------------------------------


class TestExecuteAndCollect:
    def test_lost_response_retires_socket_without_replaying(self, monkeypatch):
        async def go():
            conn, writer = await make_connection(monkeypatch)
            calls = []
            async def failed(index, code, timeout):
                calls.append(index)
                raise ConnectionError("accepted command but response lost")
            monkeypatch.setattr(conn, "_locked_execute", failed)
            with pytest.raises(ConnectionError):
                await conn._execute_and_collect(0, "MUTATION", 1.0)
            assert calls == [0]
            assert writer.closed
            assert not conn.is_connected
        run(go())

    def test_silent_response_is_unknown_and_never_resent(self, monkeypatch):
        async def go():
            conn, writer = await make_connection(monkeypatch)
            calls = []
            async def failed(index, code, timeout):
                calls.append(code)
                raise TimeoutError("No response")
            monkeypatch.setattr(conn, "_locked_execute", failed)
            with pytest.raises(TimeoutError):
                await conn._execute_and_collect(0, "MUTATION", 1.0)
            assert calls == ["MUTATION"]
            assert writer.closed
        run(go())


    def test_does_not_retry_a_lua_error(self, monkeypatch):
        """A Lua error is the game answering, not the socket dying — a retry would re-run it."""

        async def go():
            conn, _ = await make_connection(monkeypatch)
            attempts: list[int] = []

            async def explode(state_index, lua_code, timeout):
                attempts.append(1)
                raise LuaError("ERR:bad lua")

            monkeypatch.setattr(conn, "_locked_execute", explode)
            with pytest.raises(LuaError):
                await conn._execute_and_collect(0, "bad()", 5.0)
            return attempts

        assert run(go()) == [1]

    def test_reads_go_to_gamecore_and_writes_go_to_ingame(self, monkeypatch):
        async def go():
            conn, writer = await make_connection(
                monkeypatch, incoming=(frame(TAG, "O\x00c: CIVMCP|request1|END"), frame(TAG, "O\x00c: CIVMCP|request2|END"))
            )
            await conn.execute_read("READ_ME")
            await conn.execute_write("WRITE_ME")
            return writer

        messages = run(go()).messages()
        assert [payload.split(":", 2)[:2] for _, payload in messages] == [
            ["CMD", "0"], ["CMD", "1"]
        ]
        assert "\nREAD_ME\n" in messages[0][1]
        assert "\nWRITE_ME\n" in messages[1][1]


class TestUncertainStream:
    def test_partial_output_without_sentinel_is_not_success(self, monkeypatch):
        async def go():
            conn, writer = await make_connection(monkeypatch, incoming=(frame(TAG, "O\x00c: OK:SUBMITTED"),))
            with pytest.raises(TimeoutError, match="outcome is unknown"):
                await conn.execute_write("MUTATION", timeout=0.01)
            assert len(writer.messages()) == 1
            assert writer.closed
        run(go())

    def test_cancelled_partial_frame_cannot_poison_the_next_connection(self, monkeypatch):
        async def go():
            raw = frame(TAG, "O\x00c: reply")
            conn, writer = await make_connection(monkeypatch, incoming=(raw[:10],))
            with pytest.raises(TimeoutError):
                await conn.execute_write("MUTATION", timeout=0.01)
            assert writer.closed
            assert conn._reader is None
        run(go())

    def test_context_index_is_resolved_after_reconnection(self, monkeypatch):
        async def go():
            conn, writer = await make_connection(monkeypatch, incoming=(frame(TAG, "O\x00c: CIVMCP|request1|END"),), states=("8", "GameCore_Tuner", "160", "InGame"))
            await conn.execute_write("CURRENT_CONTEXT")
            assert len(writer.messages()) == 1
            assert writer.messages()[0][1].startswith("CMD:160:do\n")
        run(go())


class TestRequestOwnership:
    def test_legacy_end_and_late_previous_output_cannot_shift_the_next_reply(self, monkeypatch):
        async def go():
            from civ_mcp.game_lifecycle import execute_lua
            conn, writer = await make_connection(monkeypatch, incoming=tuple(
                frame(TAG, "O\x00ctx: " + value) for value in [
                    "CIVMCP|request1|OUT|FIRST",
                    "CIVMCP|request1|OUT|---END---",
                    "CIVMCP|request1|END",
                    "---END---",
                    "CIVMCP|request1|OUT|STALE_CALLBACK",
                    "CIVMCP|request1|END",
                    "CIVMCP|request2|OUT|SECOND",
                    "CIVMCP|request2|END",
                ]
            ))
            first = await execute_lua(conn, 'print("FIRST"); print("---END---")', "ingame")
            second = await conn.execute_read('print("SECOND"); print("---END---")')
            return first, second, writer

        first, second, writer = run(go())
        assert (first, second) == ("FIRST", ["SECOND"])
        assert len(writer.messages()) == 2

    def test_another_request_completion_cannot_turn_an_incomplete_reply_into_success(self, monkeypatch):
        async def go():
            conn, writer = await make_connection(monkeypatch, incoming=tuple(
                frame(TAG, "O\x00ctx: " + value) for value in [
                    "---END---", "CIVMCP|old-request|OUT|STALE",
                    "CIVMCP|old-request|END", "CIVMCP|request1|OUT|SUBMITTED",
                ]
            ))
            with pytest.raises(TimeoutError, match="outcome is unknown"):
                await conn.execute_write("submit_once()", timeout=0.01)
            assert len(writer.messages()) == 1
            assert writer.closed
            assert not conn.is_connected
        run(go())

    def test_mainmenu_no_output_and_return_need_no_caller_sentinel(self, monkeypatch):
        async def go():
            conn, writer = await make_connection(monkeypatch,
                states=("12", "MainMenu"),
                incoming=(frame(TAG, "O\x00MainMenu: CIVMCP|request1|END"),))
            result = await conn.execute_in_state(12, "return -- no value")
            return result, writer.messages()
        result, messages = run(go())
        assert result == []
        assert len(messages) == 1
        assert messages[0][1].startswith("CMD:12:do\n")
        assert "\nreturn -- no value\nend)()" in messages[0][1]

    def test_cancellation_during_a_partial_response_retires_the_stream(self, monkeypatch):
        async def go():
            raw = frame(TAG, "O\x00ctx: CIVMCP|request1|OUT|PARTIAL")
            conn, writer = await make_connection(monkeypatch, incoming=(raw[:10],))
            task = asyncio.create_task(conn.execute_write("submit_once()"))
            # Schedule cancellation after execution has had a chance to submit;
            # this yields to the loop, without a clock-based synchronization guess.
            await asyncio.sleep(0)
            assert len(writer.messages()) == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert writer.closed
            assert not conn.is_connected
        run(go())

    def test_send_and_response_share_the_same_deadline_without_replay(self, monkeypatch):
        async def go():
            conn, writer = await make_connection(monkeypatch)
            async def blocked_drain():
                await asyncio.Event().wait()
            monkeypatch.setattr(writer, "drain", blocked_drain)
            with pytest.raises(TimeoutError, match="outcome is unknown"):
                await conn.execute_write("submit_once()", timeout=0.01)
            assert len(writer.messages()) == 1
            assert writer.closed
        run(go())


class TestBoundedStateRediscovery:
    def test_refresh_waits_for_current_request_then_replaces_directory(self, monkeypatch):
        from fake_tuner import FakeWriter, make_reader
        async def go():
            conn, old_writer = await make_connection(monkeypatch)
            old_reader = conn._reader
            new_writer = FakeWriter()
            async def fresh_connect(*args):
                return make_reader(), new_writer
            async def fresh_handshake(*args):
                return "APP:Civ6", ["8", "InGame", "9", "GameCore_Tuner", "211", "NaturalDisasterPopup"]
            monkeypatch.setattr(tuner_client, "connect", fresh_connect)
            monkeypatch.setattr(tuner_client, "handshake", fresh_handshake)
            command = asyncio.create_task(conn.execute_in_state(0, "return 1"))
            await asyncio.sleep(0)
            refresh = asyncio.create_task(conn.rediscover_states(timeout=0.1))
            await asyncio.sleep(0)
            assert not old_writer.closed
            old_reader.feed_data(frame(TAG, "O\x00ctx: CIVMCP|request1|END"))
            assert await command == []
            await refresh
            assert old_writer.closed and conn.is_connected
            assert conn.lua_states == {8: "InGame", 9: "GameCore_Tuner", 211: "NaturalDisasterPopup"}
            assert len(old_writer.messages()) == 1 and new_writer.messages() == []
        run(go())

    def test_timeout_retires_partial_discovery_without_gameplay_submission(self, monkeypatch):
        from fake_tuner import FakeWriter, make_reader
        async def go():
            conn, old_writer = await make_connection(monkeypatch)
            new_writer = FakeWriter()
            async def fresh_connect(*args):
                return make_reader(), new_writer
            async def stuck_handshake(*args):
                await asyncio.Event().wait()
            monkeypatch.setattr(tuner_client, "connect", fresh_connect)
            monkeypatch.setattr(tuner_client, "handshake", stuck_handshake)
            with pytest.raises(TimeoutError):
                await conn.rediscover_states(timeout=0.01)
            assert old_writer.closed and new_writer.closed
            assert not conn.is_connected and conn.lua_states == {}
            assert old_writer.messages() == [] and new_writer.messages() == []
        run(go())

    def test_refresh_queue_wait_has_a_deadline_without_retiring_active_command(self, monkeypatch):
        async def go():
            conn, writer = await make_connection(monkeypatch)
            command = asyncio.create_task(conn.execute_in_state(0, "return 1"))
            await asyncio.sleep(0)
            refresh = asyncio.create_task(conn.rediscover_states(timeout=0.005))
            try:
                # 等待有界完成，避免依赖 Windows 无法保证的 15 毫秒调度精度。
                await asyncio.wait({refresh}, timeout=0.2)
                assert refresh.done(), "state discovery waited beyond its budget on another command"
                with pytest.raises(TimeoutError):
                    await refresh
                assert not writer.closed
                conn._reader.feed_data(frame(TAG, "O\x00ctx: CIVMCP|request1|END"))
                assert await command == []
            finally:
                command.cancel()
                refresh.cancel()
                await asyncio.gather(command, refresh, return_exceptions=True)
        run(go())


def test_named_context_resolves_exact_discovered_index(monkeypatch):
    async def go():
        conn, writer = await make_connection(monkeypatch,
            states=("0", "GameCore_Tuner", "1", "InGame", "47", "DiplomacyActionView"),
            incoming=(frame(TAG, "O\x00c: CIVMCP|request1|END"),))
        assert await conn.execute_in_named_state("DiplomacyActionView", "print('native close')") == []
        return writer.messages()
    messages = run(go())
    assert len(messages) == 1 and messages[0][1].startswith("CMD:47:")


@pytest.mark.parametrize("states", [
    ("0", "GameCore_Tuner", "1", "InGame"),
    ("0", "GameCore_Tuner", "1", "InGame", "47", "DiplomacyActionView", "48", "DiplomacyActionView"),
])
def test_unknown_or_ambiguous_named_context_sends_no_command(monkeypatch, states):
    from civ_mcp.connection import LuaStateUnavailable
    async def go():
        conn, writer = await make_connection(monkeypatch, states=states)
        with pytest.raises(LuaStateUnavailable):
            await conn.execute_in_named_state("DiplomacyActionView", "Close()")
        return writer.messages()
    assert run(go()) == []
