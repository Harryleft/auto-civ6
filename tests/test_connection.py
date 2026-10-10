"""Unit tests for `civ_mcp.connection.GameConnection`.

The class talks to the game through five module-level functions in
`tuner_client`; those are the only things replaced here. Everything else — Lua
state discovery, output parsing, the send/collect loop, reconnect-on-dead-socket
— is the real code running against an in-memory stream.
"""

from __future__ import annotations

import time

import pytest

from civ_mcp import tuner_client
from civ_mcp.connection import GameConnection, LuaError, _parse_output
from civ_mcp.lua._helpers import SENTINEL
from fake_tuner import frame, make_connection, run

TAG = tuner_client.TAG_COMMAND


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


class TestLockedExecute:
    def test_sends_the_state_index_and_code_verbatim(self, monkeypatch):
        async def go():
            conn, writer = await make_connection(
                monkeypatch, incoming=(frame(TAG, f"O\x00c: {SENTINEL}"),)
            )
            await conn._locked_execute(2, "print('hi')", timeout=1.0)
            return writer

        assert run(go()).messages() == [(TAG, "CMD:2:print('hi')")]

    def test_collects_output_lines_until_the_sentinel(self, monkeypatch):
        async def go():
            conn, _ = await make_connection(
                monkeypatch,
                incoming=(
                    frame(TAG, "O\x00ctx: one"),
                    frame(TAG, "O\x00ctx: two"),
                    frame(TAG, f"O\x00ctx: {SENTINEL}"),
                    frame(TAG, "O\x00ctx: after sentinel"),
                ),
            )
            return await conn._locked_execute(0, "print()", timeout=1.0)

        assert run(go()) == ["one", "two"]

    def test_ignores_messages_that_are_not_output(self, monkeypatch):
        async def go():
            conn, _ = await make_connection(
                monkeypatch,
                incoming=(frame(TAG, ""), frame(TAG, f"O\x00c: {SENTINEL}")),
            )
            return await conn._locked_execute(0, "print()", timeout=1.0)

        assert run(go()) == []

    def test_raises_lua_error_on_an_err_response(self, monkeypatch):
        async def go():
            conn, _ = await make_connection(
                monkeypatch, incoming=(frame(TAG, "ERR:attempt to index nil"),)
            )
            return await conn._locked_execute(0, "bad()", timeout=1.0)

        with pytest.raises(LuaError, match="attempt to index nil"):
            run(go())

    def test_a_silent_socket_raises_instead_of_returning_empty_lines(self, monkeypatch):
        """A silent socket is a dead connection, not an empty result.

        Builders always end their Lua with the sentinel, so "no sentinel and no
        output" means the game never answered. The caller gets a TimeoutError
        (which _execute_and_collect treats like a dead socket: reconnect + retry)
        instead of a [] it cannot tell apart from "no data".
        """

        async def go():
            conn, _ = await make_connection(monkeypatch, incoming=())
            return await conn._locked_execute(0, "print()", timeout=0.01)

        with pytest.raises(TimeoutError, match="Incomplete response from Civ 6"):
            run(go())

    def test_does_not_cancel_and_drain_frames_between_commands(self, monkeypatch):
        async def forbidden(*args, **kwargs):
            raise AssertionError("No frame drains in the command path")
        monkeypatch.setattr(tuner_client, "drain_messages", forbidden)
        async def go():
            conn, _ = await make_connection(monkeypatch, incoming=(frame(TAG, f"O\x00c: {SENTINEL}"),), patch_drain=False)
            assert await conn._locked_execute(0, "print()", 1.0) == []
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
                monkeypatch, incoming=(frame(TAG, f"O\x00c: {SENTINEL}"),) * 2
            )
            await conn.execute_read("READ_ME")
            await conn.execute_write("WRITE_ME")
            return writer

        assert run(go()).messages() == [
            (TAG, "CMD:0:READ_ME"),
            (TAG, "CMD:1:WRITE_ME"),
        ]


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
            conn, writer = await make_connection(monkeypatch, incoming=(frame(TAG, f"O\x00c: {SENTINEL}"),), states=("8", "GameCore_Tuner", "160", "InGame"))
            await conn.execute_write("CURRENT_CONTEXT")
            assert writer.messages() == [(TAG, "CMD:160:CURRENT_CONTEXT")]
        run(go())
