"""Every save-loading path must mark the world-rollback branch boundary.

The journal is append-only, so after a load it still describes a future the
game no longer has — including the authorizations recorded for it. Only the
connection-recovery path used to call ``record_game_reload``; the
``restart_and_load`` tool and the end-turn hang recovery loaded saves without
marking anything.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from civ_mcp.server import pipeline
from civ_mcp.server.tools import system


class _Engine:
    def __init__(self, bound: bool = True) -> None:
        self.bound = bound
        self.reloads: list[dict] = []

    def record_game_reload(self, *, reason, turn=None, details=None):
        self.reloads.append({"reason": reason, "turn": turn, "details": details})
        return {"id": "epoch"}


def _context(engine, *, records_events: bool = True, turn: int | None = 57):
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(beliefs=engine, game=object())
        )
    )


def _install(monkeypatch, *, records_events: bool, turn: int | None = 57):
    engine = _Engine(bound=records_events or True)
    flushed: list[int] = []

    async def flush(_ctx):
        flushed.append(1)

    monkeypatch.setattr(
        pipeline,
        "_get_belief_mode",
        lambda _ctx: SimpleNamespace(records_events=records_events),
    )
    monkeypatch.setattr(
        pipeline,
        "_get_logger",
        lambda _ctx: SimpleNamespace(_turn=turn),
    )
    monkeypatch.setattr(pipeline, "_flush_belief_events", flush)
    return engine, flushed


def test_records_and_flushes_when_bound(monkeypatch):
    engine, flushed = _install(monkeypatch, records_events=True)
    asyncio.run(
        pipeline._record_game_reload_epoch(
            _context(engine), reason="unit_test", turn=12, details={"save": "s"}
        )
    )
    assert engine.reloads == [
        {"reason": "unit_test", "turn": 12, "details": {"save": "s"}}
    ]
    assert flushed == [1]


def test_noop_when_persistence_is_disabled(monkeypatch):
    engine, flushed = _install(monkeypatch, records_events=False)
    asyncio.run(
        pipeline._record_game_reload_epoch(_context(engine), reason="unit_test")
    )
    assert engine.reloads == []
    assert flushed == []


def test_noop_when_the_engine_is_not_bound(monkeypatch):
    engine, flushed = _install(monkeypatch, records_events=True)
    engine.bound = False
    asyncio.run(
        pipeline._record_game_reload_epoch(_context(engine), reason="unit_test")
    )
    assert engine.reloads == []
    assert flushed == []


def test_never_raises_so_recovery_is_not_broken(monkeypatch):
    """Bookkeeping must not break the recovery the caller is performing."""

    engine, _ = _install(monkeypatch, records_events=True)

    def boom(**_kwargs):
        raise RuntimeError("journal is unwritable")

    monkeypatch.setattr(engine, "record_game_reload", boom)
    asyncio.run(
        pipeline._record_game_reload_epoch(_context(engine), reason="unit_test")
    )


def test_restart_and_load_tool_records_the_epoch(monkeypatch):
    recorded: list[dict] = []

    async def fake_restart(save_name, conn=None):
        return f"restarted {save_name}"

    async def fake_record(_ctx, *, reason, turn=None, details=None):
        recorded.append({"reason": reason, "turn": turn, "details": details})

    class _Connection:
        gamecore_index = None

        async def reconnect(self):
            self.gamecore_index = 1

    game_state = SimpleNamespace(_game_identity=None, conn=_Connection())
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(game=game_state, beliefs=_Engine())
        )
    )

    monkeypatch.setattr(system.game_launcher, "restart_and_load", fake_restart)
    monkeypatch.setattr(pipeline, "_record_game_reload_epoch", fake_record)
    monkeypatch.setattr(
        pipeline, "_get_logger", lambda _ctx: SimpleNamespace(_turn=57)
    )

    result = asyncio.run(system.restart_and_load(ctx, save_name="0_MCP_0057"))

    assert "0_MCP_0057" in result
    assert recorded == [
        {
            "reason": "restart_and_load_tool",
            "turn": 57,
            "details": {"save": "0_MCP_0057"},
        }
    ]


@pytest.mark.parametrize(
    "path",
    ["src/civ_mcp/server/tools/system.py", "src/civ_mcp/server/tools/end_turn_flow.py"],
)
def test_every_save_loading_path_marks_the_epoch(path):
    """Guard the invariant at the source: no restart_and_load without a marker."""

    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[1] / path).read_text(
        encoding="utf-8"
    )
    restarts = source.count("await game_launcher.restart_and_load(")
    markers = source.count("_record_game_reload_epoch(")
    assert restarts > 0, f"{path} no longer calls restart_and_load"
    assert markers >= restarts, (
        f"{path}: {restarts} 处 restart_and_load 但只有 {markers} 处 epoch 标记；"
        "加载存档必须同时记录 epoch，否则被放弃分支上的授权仍可被消费"
    )
