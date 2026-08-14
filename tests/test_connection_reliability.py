"""Tests for connection-layer reliability: no mutation resend, loud timeouts.

Pins the two P0 behaviors of GameConnection:
- Dead-socket retry must never re-send a game-mutating command (the game
  may have executed the first send — a resend double-executes it), while
  queries keep the reconnect-and-resend recovery.
- A command that finishes without its completion sentinel must raise
  CommandTimeoutError instead of silently returning partial output that
  parsers misread.

Fully offline: all tuner_client I/O is faked via monkeypatch — no network,
no game.
"""

import asyncio

from civ_mcp import tuner_client
from civ_mcp.connection import (
    CommandTimeoutError,
    GameConnection,
    LuaError,
    MutationOutcomeUnknownError,
)
from civ_mcp.lua._helpers import SENTINEL


class _FakeWriter:
    """Just enough StreamWriter for is_connected()/disconnect()."""

    def __init__(self):
        self.closed = False

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


def _make_conn(monkeypatch, send_behaviors, recv_script):
    """Build an offline GameConnection backed by a scripted fake tuner.

    send_behaviors: one entry per CMD send attempt; None means the send
        succeeds, an exception instance is raised from send_message.
    recv_script: messages returned by recv_message_timeout, consumed in
        order; a None entry means "recv timed out" (quiet stream).

    Returns (conn, sent_payloads, reconnect_log).
    """
    conn = GameConnection()
    conn._reader = object()  # opaque — every tuner_client call is faked
    conn._writer = _FakeWriter()
    conn.gamecore_index = 0
    conn.ingame_index = 1
    conn.lua_states = {0: "GameCore_Tuner", 1: "InGame"}

    sent: list[str] = []
    behaviors = list(send_behaviors)

    async def fake_send(writer, tag, payload):
        sent.append(payload)
        behavior = behaviors.pop(0) if behaviors else None
        if isinstance(behavior, BaseException):
            raise behavior

    replies = list(recv_script)

    async def fake_recv(reader, timeout=2.0):
        return replies.pop(0) if replies else None

    async def fake_drain(reader, timeout=0.5):
        return []

    reconnect_log: list[bool] = []

    async def fake_reconnect(self):
        reconnect_log.append(True)

    monkeypatch.setattr(tuner_client, "send_message", fake_send)
    monkeypatch.setattr(tuner_client, "recv_message_timeout", fake_recv)
    monkeypatch.setattr(tuner_client, "drain_messages", fake_drain)
    monkeypatch.setattr(GameConnection, "reconnect", fake_reconnect)
    return conn, sent, reconnect_log


def _out(value: str) -> tuner_client.Message:
    """A print() output message carrying ``value``."""
    return tuner_client.Message(tag=2, payload=f"O\x00lua: {value}")


# ---------------------------------------------------------------------------
# Sentinel enforcement
# ---------------------------------------------------------------------------


class TestSentinelTimeout:
    def test_missing_sentinel_strict_raises_with_partial_lines(self, monkeypatch):
        """Timeout without sentinel + require_sentinel=True → the partial
        lines travel on the exception instead of masquerading as a result."""
        conn, _sent, _rc = _make_conn(
            monkeypatch,
            send_behaviors=[None],
            recv_script=[_out("PARTIAL1"), _out("PARTIAL2"), None],
        )
        try:
            asyncio.run(conn.execute_write("move...", timeout=0.2))
            raise AssertionError("expected CommandTimeoutError")
        except CommandTimeoutError as e:
            assert e.lines == ["PARTIAL1", "PARTIAL2"]
            assert e.timeout == 0.2
            assert "PARTIAL1" in str(e)  # diagnostic includes a preview
            assert "PARTIAL2" in str(e)

    def test_lenient_mode_returns_partial_lines(self, monkeypatch):
        """require_sentinel=False keeps the old lenient contract: partial
        output is returned, not raised."""
        conn, _sent, _rc = _make_conn(
            monkeypatch,
            send_behaviors=[None],
            recv_script=[_out("PARTIAL1"), None],
        )
        lines = asyncio.run(
            conn.execute_write("query...", timeout=0.2, require_sentinel=False)
        )
        assert lines == ["PARTIAL1"]

    def test_in_state_channel_is_lenient(self, monkeypatch):
        """execute_in_state (state probing / arbitrary code) stays lenient."""
        conn, _sent, _rc = _make_conn(
            monkeypatch,
            send_behaviors=[None],
            recv_script=[_out("StateName"), None],
        )
        lines = asyncio.run(conn.execute_in_state(50, "print(1)", timeout=0.2))
        assert lines == ["StateName"]

    def test_sentinel_present_returns_normally(self, monkeypatch):
        """A clean sentinel round-trip is unaffected by the strict default."""
        conn, _sent, _rc = _make_conn(
            monkeypatch,
            send_behaviors=[None],
            recv_script=[_out("OK:MOVED"), _out(SENTINEL)],
        )
        lines = asyncio.run(conn.execute_write("move..."))
        assert lines == ["OK:MOVED"]

    def test_timeout_error_is_lua_error_for_server_handling(self):
        """server.py's ``except (LuaError, ValueError)`` must keep catching
        timeouts, and ConnectionError must keep catching mutation failures."""
        assert issubclass(CommandTimeoutError, LuaError)
        assert issubclass(MutationOutcomeUnknownError, ConnectionError)


# ---------------------------------------------------------------------------
# Dead-socket retry policy
# ---------------------------------------------------------------------------


class TestMutationNoResend:
    def test_mutation_dead_socket_raises_and_sends_exactly_once(self, monkeypatch):
        """Mutation + OSError on send → MutationOutcomeUnknownError, one send
        total, and no reconnect-and-resend."""
        conn, sent, reconnect_log = _make_conn(
            monkeypatch,
            send_behaviors=[OSError("socket dead")],
            recv_script=[],
        )
        try:
            asyncio.run(conn.execute_mutation("attack..."))
            raise AssertionError("expected MutationOutcomeUnknownError")
        except MutationOutcomeUnknownError as e:
            assert isinstance(e, ConnectionError)
            assert isinstance(e.__cause__, OSError)
            assert "unknown" in str(e)
            assert "Verify the current game state" in str(e)
        assert sent == ["CMD:1:attack..."]  # sent once, never resent
        assert reconnect_log == []

    def test_query_dead_socket_reconnects_and_resends_once(self, monkeypatch):
        """Query + OSError on first send → reconnect, resend, succeed
        (pre-existing recovery behavior preserved)."""
        conn, sent, reconnect_log = _make_conn(
            monkeypatch,
            send_behaviors=[OSError("socket dead"), None],
            recv_script=[_out("TURN|42"), _out(SENTINEL)],
        )
        lines = asyncio.run(conn.execute_read("print(Game.GetCurrentGameTurn())"))
        assert lines == ["TURN|42"]
        assert len(sent) == 2  # original + the single retry
        assert reconnect_log == [True]

    def test_state_local_mutation_dead_socket_never_resent(self, monkeypatch):
        """execute_in_state(mutation=True) (popup Close() in arbitrary Lua
        states) must inherit the mutation contract: one send, no resend."""
        conn, sent, reconnect_log = _make_conn(
            monkeypatch,
            send_behaviors=[OSError("socket dead")],
            recv_script=[],
        )
        try:
            asyncio.run(
                conn.execute_in_state(57, 'pcall(Close); print("---END---")', mutation=True)
            )
            raise AssertionError("expected MutationOutcomeUnknownError")
        except MutationOutcomeUnknownError:
            pass
        assert sent == ['CMD:57:pcall(Close); print("---END---")']  # once only
        assert reconnect_log == []

    def test_state_probe_without_mutation_flag_still_resends(self, monkeypatch):
        """Plain execute_in_state (state probing) keeps query-like recovery."""
        conn, sent, reconnect_log = _make_conn(
            monkeypatch,
            send_behaviors=[OSError("socket dead"), None],
            recv_script=[_out("SomePopupState"), _out(SENTINEL)],
        )
        lines = asyncio.run(conn.execute_in_state(57, "print(1)"))
        assert lines == ["SomePopupState"]
        assert len(sent) == 2
        assert reconnect_log == [True]


# ---------------------------------------------------------------------------
# execute_mutation routing
# ---------------------------------------------------------------------------


class TestExecuteMutationRouting:
    def test_routes_ingame_and_gamecore_state_indexes(self, monkeypatch):
        conn, sent, _rc = _make_conn(
            monkeypatch,
            send_behaviors=[None, None],
            recv_script=[_out(SENTINEL), _out(SENTINEL)],
        )
        code = 'UI.RequestAction(ActionTypes.ACTION_ENDTURN)'
        assert asyncio.run(conn.execute_mutation(code)) == []
        assert asyncio.run(conn.execute_mutation(code, context="gamecore")) == []
        # ingame_index=1, gamecore_index=0 (as wired by _make_conn)
        assert sent == [f"CMD:1:{code}", f"CMD:0:{code}"]

    def test_unknown_context_rejected(self, monkeypatch):
        conn, _sent, _rc = _make_conn(monkeypatch, send_behaviors=[], recv_script=[])
        try:
            asyncio.run(conn.execute_mutation("x", context="mainmenu"))
            raise AssertionError("expected ValueError")
        except ValueError:
            pass
