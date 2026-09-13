"""读档经真实工具管道作废同回合证据；所有游戏与遥测边界均离线。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from civ6_belief_engine.belief_engine import BeliefEngine
from civ6_belief_engine.belief_mode import BeliefMode
from civ_mcp import game_lifecycle
from civ_mcp.connection import MutationOutcomeUnknownError
from civ_mcp.game_state import GameState
from civ_mcp.server import pipeline
from civ_mcp.server.tools import system


_LOAD_TOOLS = ("load_save", "load_game_save", "load_save_from_menu")


class _Connection:
    # 读档可能保持连接、回合号不变，刻意让这两个连接版本保持不变。
    generation = 1
    mutation_revision = 0

    def __init__(self):
        self.hp = 80
        self.unit_reads = 0

    async def execute_write(self, _lua, **_kwargs):
        self.unit_reads += 1
        return [f"1|7|Warrior|UNIT_WARRIOR|3,4|0/2|{self.hp}/100|20|0|0||||0|0|||0|1"]


class _Logger:
    _turn = 57

    def __init__(self):
        self._emitter = self
        self.events = []

    async def emit(self, event_type, data):
        self.events.append((event_type, data))

    async def log_tool_call(self, tool, params, result, duration_ms):
        self.events.append(("tool_call", {"tool": tool, "result": result}))

    async def log_error(self, tool, result):
        self.events.append(("error", {"tool": tool, "result": result}))


def _context(tmp_path, monkeypatch, mode):
    connection = _Connection()
    game = GameState(connection)
    game._high_water_turn = 57
    engine = BeliefEngine(run_id="load-cache-test", directory=tmp_path)
    engine.bind_game("CIVILIZATION_TEST", 42)
    logger = _Logger()
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(
                game=game, beliefs=engine, logger=logger, belief_mode=mode
            )
        )
    )
    monkeypatch.setattr(pipeline.heartbeat, "write", lambda *_args, **_kwargs: None)
    # 避免独立参数用例之间累积连接失败而触发真实恢复路径。
    monkeypatch.setattr(pipeline._logged, "_conn_errors", 0, raising=False)
    return ctx, game, connection, engine, logger


def _install_load(monkeypatch, connection, outcome, *, changes_world, submitted=False):
    calls = []

    async def load(conn, save):
        assert conn is connection
        calls.append(save)
        if submitted:
            connection.mutation_revision += 1
        if changes_world:
            connection.hp = 40
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(game_lifecycle, "load_save", load)
    monkeypatch.setattr(game_lifecycle, "load_game_save", load)
    return calls


async def _call_load(ctx, tool):
    if tool == "load_save":
        return await system.load_save(ctx, save_index=1)
    return await getattr(system, tool)(ctx, save_name="checkpoint")


async def _load_inside_same_turn_collection(ctx, game, connection, tool):
    with game.read_collection():
        game._read_cache().set_turn(57)
        before = await game.get_units()
        assert await game.get_units() == before
        assert connection.unit_reads == 1, "先证明本用例确实建立了可复用缓存"
        result = await _call_load(ctx, tool)
        after = await game.get_units()
        assert connection.unit_reads == 2, "读档后相同回合的单位必须重新读取"
        return result, before, after


@pytest.mark.parametrize("tool", _LOAD_TOOLS)
@pytest.mark.parametrize("mode", (BeliefMode.ENFORCE, BeliefMode.OFF))
@pytest.mark.parametrize(
    "outcome",
    (
        "Loading save checkpoint via FrontEnd API",
        "ERR:OUTCOME_UNKNOWN|Load request sent; verify the game state",
        MutationOutcomeUnknownError("Network.LoadGame(checkpoint)"),
        RuntimeError("transport response lost after load submission"),
    ),
    ids=("submitted", "unknown_receipt", "unknown_connection", "unknown_exception"),
)
def test_load_replaces_same_turn_cache_and_marks_epoch(
    tmp_path, monkeypatch, tool, mode, outcome
):
    ctx, game, connection, engine, logger = _context(tmp_path, monkeypatch, mode)
    calls = _install_load(monkeypatch, connection, outcome, changes_world=True)
    starting_epoch = engine.epoch
    game._ruleset_caps = object()
    game._last_snapshot = object()

    _result, before, after = asyncio.run(
        _load_inside_same_turn_collection(ctx, game, connection, tool)
    )

    assert before != after, "加载后的真实观测必须替代旧单位状态"
    assert len(calls) == 1, "结果未知也不能自动重发加载请求"
    assert game._ruleset_caps is None
    assert game._last_snapshot is None
    assert logger._turn == 57
    assert engine.epoch == starting_epoch + int(mode.records_events)
    actions = engine.list("action", status="active")
    if mode.records_events:
        assert len(actions) == 1
        assert actions[0]["success"] is False
        assert actions[0]["outcome_status"] == "unknown"
    else:
        assert actions == []


@pytest.mark.parametrize("tool", _LOAD_TOOLS)
@pytest.mark.parametrize(
    "outcome",
    (
        "Error: save was not found",
        "ERR:save was not found",
        "FAILED|save was not found",
        ValueError("invalid save name"),
    ),
    ids=("error_text", "err_text", "failed_marker", "validation_error"),
)
def test_known_load_failure_creates_neither_epoch_nor_success(
    tmp_path, monkeypatch, tool, outcome
):
    ctx, game, connection, engine, _logger = _context(
        tmp_path, monkeypatch, BeliefMode.ENFORCE
    )
    calls = _install_load(monkeypatch, connection, outcome, changes_world=False)
    starting_epoch = engine.epoch

    _result, before, after = asyncio.run(
        _load_inside_same_turn_collection(ctx, game, connection, tool)
    )

    assert before == after
    assert len(calls) == 1
    assert engine.epoch == starting_epoch
    actions = engine.list("action", status="active")
    assert len(actions) == 1
    assert actions[0]["success"] is False
    assert actions[0]["outcome_status"] == "failed"


@pytest.mark.parametrize("tool", _LOAD_TOOLS)
@pytest.mark.parametrize("mode", (BeliefMode.ENFORCE, BeliefMode.OFF))
def test_load_error_after_submission_still_invalidates_epoch(
    tmp_path, monkeypatch, tool, mode
):
    ctx, game, connection, engine, _logger = _context(tmp_path, monkeypatch, mode)
    calls = _install_load(
        monkeypatch,
        connection,
        "Error: save loading began but the ready state could not be confirmed",
        changes_world=True,
        submitted=True,
    )
    starting_epoch = engine.epoch

    _result, before, after = asyncio.run(
        _load_inside_same_turn_collection(ctx, game, connection, tool)
    )

    assert before != after
    assert len(calls) == 1
    assert engine.epoch == starting_epoch + int(mode.records_events)
    actions = engine.list("action", status="active")
    assert not any(action["success"] for action in actions)


@pytest.mark.parametrize("tool", _LOAD_TOOLS)
@pytest.mark.parametrize("mode", (BeliefMode.ENFORCE, BeliefMode.OFF))
def test_cancelled_load_after_submission_marks_epoch_and_propagates(
    tmp_path, monkeypatch, tool, mode
):
    ctx, game, connection, engine, _logger = _context(tmp_path, monkeypatch, mode)
    calls = _install_load(
        monkeypatch,
        connection,
        asyncio.CancelledError(),
        changes_world=True,
        submitted=True,
    )
    starting_epoch = engine.epoch

    async def run():
        with game.read_collection():
            game._read_cache().set_turn(57)
            before = await game.get_units()
            assert await game.get_units() == before
            assert connection.unit_reads == 1
            with pytest.raises(asyncio.CancelledError):
                await _call_load(ctx, tool)
            after = await game.get_units()
            assert connection.unit_reads == 2
            assert before != after

    asyncio.run(run())

    assert len(calls) == 1
    assert engine.epoch == starting_epoch + int(mode.records_events)
    assert not any(
        action["success"] for action in engine.list("action", status="active")
    )
