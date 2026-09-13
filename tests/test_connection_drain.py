"""The per-command socket drain must stay correct while getting cheap.

``_locked_execute`` drains the FireTuner socket before sending and again after
the sentinel, because the game pushes unsolicited LuaEvent output alongside
command responses. Both drains run to their timeout when nothing is pending, so
the old 0.1s + 0.2s windows cost a fixed 300ms per Lua command — measured, and
the floor that made every documented latency figure unachievable.

What the drains need to collect is data already buffered when they run, so the
tests below pin the property that matters: a message sitting in the buffer is
consumed at the current timeout. If that ever stops holding, the drains are
silently doing nothing and stray output will leak between commands.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time

from civ_mcp import connection, tuner_client


def _message(payload: str, tag: int = 1) -> bytes:
    body = payload.encode() + b"\x00"
    return len(body).to_bytes(4, "little") + tag.to_bytes(4, "little") + body


async def _drain_buffered(*payloads: str) -> int:
    """Create the reader inside the loop (3.12 binds it to a running loop)."""

    reader = asyncio.StreamReader()
    reader.feed_data(b"".join(_message(payload) for payload in payloads))
    drained = await tuner_client.drain_messages(
        reader, timeout=connection.DRAIN_TIMEOUT
    )
    return len(drained)


async def _time_idle_drain() -> float:
    start = time.perf_counter()
    await tuner_client.drain_messages(
        asyncio.StreamReader(), timeout=connection.DRAIN_TIMEOUT
    )
    return time.perf_counter() - start


def test_buffered_messages_are_consumed_at_the_current_timeout():
    drained = asyncio.run(
        _drain_buffered("---END---", "O\x00InGame: first", "O\x00InGame: second")
    )
    assert drained == 3


def test_drain_timeout_is_far_below_the_old_fixed_windows():
    """Guard the regression: 0.1 + 0.2 per command is ~25 minutes a game."""

    assert connection.DRAIN_TIMEOUT <= 0.05


def test_idle_drain_returns_promptly_at_both_call_sites():
    """Every Lua command pays this twice, so bound the real cost."""

    pair = asyncio.run(_time_idle_drain()) + asyncio.run(_time_idle_drain())
    # The old fixed windows cost 300ms for the pair.
    assert pair < 0.15


def test_drain_timeout_is_overridable_for_a_real_game():
    """A live game may need a wider grace window; that must not need a patch."""

    script = (
        "import civ_mcp.connection as c; print(c.DRAIN_TIMEOUT)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert float(completed.stdout.strip()) == connection.DRAIN_TIMEOUT

    import os

    overridden = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "CIV_MCP_DRAIN_TIMEOUT": "0.5"},
    )
    assert overridden.returncode == 0, overridden.stderr
    assert float(overridden.stdout.strip()) == 0.5
