"""Offline cache leases: reuse a collection, never manufacture fresh evidence."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from civ_mcp import lua as lq
from civ_mcp.connection import GameConnection, MutationOutcomeUnknownError
from civ_mcp.game_state import GameState
from civ_mcp.read_cache import ReadCache
from civ_mcp.server.governance_snapshot import _reusable_typed_snapshot_for_turn


def test_collection_reuses_reads_and_returns_detached_values():
    async def run():
        cache = ReadCache(lambda: (1, 0))
        load = AsyncMock(return_value=[{"health": 100}])
        with cache.collection():
            first = await cache.read("units", load)
            first[0]["health"] = 0
            with cache.collection():
                assert await cache.read("units", load) == [{"health": 100}]
        assert load.await_count == 1
        # A subsequent evidence request must refresh, even on the same turn.
        with cache.collection():
            await cache.read("units", load)
        assert load.await_count == 2

    asyncio.run(run())


def test_mutation_reconnect_turn_and_unknown_generation_end_reuse():
    async def run():
        stamp = [1, 0]
        cache = ReadCache(lambda: tuple(stamp))
        load = AsyncMock(side_effect=range(20))
        with cache.collection():
            cache.set_turn(10)
            assert await cache.read("units", load) == 0
            stamp[1] += 1
            assert await cache.read("units", load) == 1
            stamp[0] += 1
            assert await cache.read("units", load) == 2
            cache.set_turn(11)
            assert await cache.read("units", load) == 3
        untrusted = ReadCache(lambda: None)
        with untrusted.collection():
            assert await untrusted.read("units", load) != await untrusted.read(
                "units", load
            )

    asyncio.run(run())


def test_failure_and_change_during_query_are_not_cached():
    async def run():
        revision = [0]
        cache = ReadCache(lambda: revision[0])
        load = AsyncMock(side_effect=[ValueError("scan failed"), [], [1]])
        with cache.collection():
            with pytest.raises(ValueError):
                await cache.read("threats", load)
            assert await cache.read("threats", load) == []
            assert load.await_count == 2

            async def changed():
                revision[0] += 1
                return [42]

            await cache.read("units", changed)
            assert await cache.read("units", load) == [1]

    asyncio.run(run())


def test_child_tasks_do_not_inherit_partial_collection():
    async def run():
        cache = ReadCache(lambda: 1)
        load = AsyncMock(side_effect=[1, 2])
        with cache.collection():
            assert await cache.read("units", load) == 1
            assert await asyncio.create_task(cache.read("units", load)) == 2

    asyncio.run(run())


def test_game_state_collection_saves_reads_and_reload_discards_them(monkeypatch):
    async def run():
        conn = SimpleNamespace(
            generation=1,
            mutation_revision=0,
            execute_write=AsyncMock(return_value=["cities"]),
        )
        gs = GameState(conn)
        monkeypatch.setattr(
            lq, "parse_cities_response", lambda lines: (list(lines), [])
        )
        with gs.read_collection():
            await gs.get_cities()
            await gs.get_cities()
            assert conn.execute_write.await_count == 1
            gs.invalidate_cached_state()
            await gs.get_cities()
            assert conn.execute_write.await_count == 2
        await gs.get_cities()
        assert conn.execute_write.await_count == 3

    asyncio.run(run())


def test_mutation_revision_changes_before_unknown_send(monkeypatch):
    async def run():
        conn = GameConnection()
        monkeypatch.setattr(conn, "ensure_connected", AsyncMock())
        observed = []

        async def send(*args, **kwargs):
            observed.append(conn.mutation_revision)
            raise OSError("lost after send")

        monkeypatch.setattr(conn, "_locked_execute", send)
        with pytest.raises(MutationOutcomeUnknownError):
            await conn._execute_and_collect(1, "move", 1, mutation=True)
        assert observed == [1]
        assert conn.mutation_revision == 1
        before = conn.generation
        await conn.disconnect()
        assert conn.generation > before

    asyncio.run(run())


@pytest.mark.parametrize("executed,expect_reuse", [(True, False), (False, True)])
def test_unknown_executed_action_invalidates_snapshot(engine, executed, expect_reuse):
    engine.create(
        "observation",
        {
            "statement": "snapshot",
            "source": "game_state:typed_snapshot",
            "facts": {"snapshot_id": "s"},
            "observed_turn": 42,
        },
        turn=42,
    )
    engine.create(
        "action",
        {
            "statement": "unknown or blocked action",
            "tool": "unit_action",
            "params": {"unit_id": 7, "action": "move"},
            "selected_turn": 42,
            "executed": executed,
            "success": False,
        },
        turn=42,
    )
    assert (
        _reusable_typed_snapshot_for_turn(engine, turn=42) is not None
    ) == expect_reuse


def test_history_reaches_turn_brief_and_is_cleared_by_reload(engine):
    for turn, value in [(0, 1), (25, 3), (29, 9), (35, 100)]:
        engine.create(
            "observation",
            {
                "statement": f"observed {turn}",
                "source": "test",
                "metrics": {"science": value},
                "observed_turn": turn,
            },
            turn=turn,
        )
    brief = engine.turn_brief(turn=29)
    recent = brief["history_summary"]["windows"][0]["metrics"]["science"]
    assert recent["sample_count"] == 2
    assert recent["change"] == 6
    assert recent["last"] == {"turn": 29, "value": 9.0}
    engine.record_game_reload(reason="same turn load", turn=29)
    assert all(
        not w["metrics"]
        for w in engine.turn_brief(turn=29)["history_summary"]["windows"]
    )
