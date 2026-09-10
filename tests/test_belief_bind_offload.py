"""``_bind_belief_engine`` must offload the journal replay *and* serialize it.

``bind_game`` replays the whole append-only journal (2.4s on a real 110-turn
game). Moving that off the event loop is only safe together with the lock: two
tool handlers can both observe ``not engine.bound``, and the thread hop is an
interleaving point the previous synchronous call did not have.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from civ_mcp.server import pipeline


def _context(lock):
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(belief_bind_lock=lock)
        )
    )


class _SlowEngine:
    """Records the binding thread and the call count."""

    def __init__(self, delay: float = 0.0) -> None:
        self.bound = False
        self.calls: list[tuple[str, int]] = []
        self.threads: list[int] = []
        self._delay = delay

    def bind_game(self, civ: str, seed: int) -> None:
        self.threads.append(threading.get_ident())
        if self._delay:
            import time

            time.sleep(self._delay)
        self.calls.append((civ, seed))
        self.bound = True


def test_bind_is_offloaded_off_the_event_loop_thread():
    engine = _SlowEngine()
    ctx = _context(asyncio.Lock())

    async def exercise():
        loop_thread = threading.get_ident()
        await pipeline._bind_belief_engine(ctx, engine, civ="ROME", seed=7)
        assert engine.threads[0] != loop_thread
        assert engine.calls == [("ROME", 7)]

    asyncio.run(exercise())


def test_concurrent_binds_replay_the_journal_only_once():
    engine = _SlowEngine(delay=0.05)
    ctx = _context(asyncio.Lock())

    async def exercise():
        await asyncio.gather(
            pipeline._bind_belief_engine(ctx, engine, civ="ROME", seed=7),
            pipeline._bind_belief_engine(ctx, engine, civ="ROME", seed=7),
            pipeline._bind_belief_engine(ctx, engine, civ="ROME", seed=7),
        )

    asyncio.run(exercise())
    assert engine.calls == [("ROME", 7)], "journal was replayed more than once"


def test_already_bound_engine_is_not_rebound():
    engine = _SlowEngine()
    engine.bound = True
    ctx = _context(asyncio.Lock())

    asyncio.run(pipeline._bind_belief_engine(ctx, engine, civ="ROME", seed=7))
    assert engine.calls == []


def test_context_without_a_lock_falls_back_to_the_inline_call():
    """Test-built contexts predate the lock; they must keep working."""

    engine = _SlowEngine()
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=SimpleNamespace())
    )

    async def exercise():
        loop_thread = threading.get_ident()
        await pipeline._bind_belief_engine(ctx, engine, civ="ROME", seed=7)
        assert engine.threads[0] == loop_thread

    asyncio.run(exercise())
    assert engine.calls == [("ROME", 7)]
