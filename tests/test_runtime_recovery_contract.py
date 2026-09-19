"""故障注入：通用管道只记录异常，读档证据决定是否结束旧回合。"""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from civ6_belief_engine.belief_mode import BeliefMode
from civ_mcp import game_lifecycle
from civ_mcp.connection import MutationOutcomeUnknownError
from civ_mcp.game_state import GameState
from civ_mcp.server import pipeline
from civ_mcp.server.tools import system


@pytest.fixture
def runtime(monkeypatch):
    conn = SimpleNamespace(
        mutation_revision=0, generation=1, turn_in_progress=True, reload_pending=False
    )
    gs = GameState(conn)
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 57
    gs._game_identity = ("test", 42)
    logger = SimpleNamespace(_turn=57, log_error=AsyncMock(), log_tool_call=AsyncMock())
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context=SimpleNamespace(
        game=gs, logger=logger, belief_mode=BeliefMode.OFF
    )))
    monkeypatch.setattr(pipeline.heartbeat, "write", lambda *a, **kw: None)
    return ctx, gs, conn


def test_five_unknown_mutations_never_restart(runtime, monkeypatch):
    ctx, gs, _conn = runtime
    restart = AsyncMock()
    monkeypatch.setattr(pipeline.game_launcher, "restart_and_load", restart)

    async def unknown():
        raise MutationOutcomeUnknownError("ACTION_ENDTURN")

    async def run():
        for _ in range(6):
            await pipeline._logged(ctx, "move_unit", {}, unknown)

    asyncio.run(run())
    restart.assert_not_called()
    assert gs._pending_end_turn is True


@pytest.mark.parametrize("reply,submitted", [
    ("Error: Save not found", False),
    ("Error: Index out of range. LOAD_NOT_SUBMITTED", True),
])
def test_rejected_load_preserves_original_turn(runtime, monkeypatch, reply, submitted):
    ctx, gs, conn = runtime

    async def load(_conn, _index):
        conn.mutation_revision += int(submitted)
        return reply

    monkeypatch.setattr(game_lifecycle, "load_save", load)
    asyncio.run(system.load_save(ctx, save_index=999))
    assert gs._pending_end_turn is True
    assert gs._pending_end_turn_from == 57
    assert conn.turn_in_progress is True
    assert conn.reload_pending is False


def test_unknown_load_blocks_later_actions_without_forgetting_turn(runtime, monkeypatch):
    ctx, gs, conn = runtime

    async def load(_conn, _index):
        conn.mutation_revision += 1
        raise MutationOutcomeUnknownError("Network.LoadGame")

    monkeypatch.setattr(game_lifecycle, "load_save", load)
    action = AsyncMock(return_value="unexpected send")

    async def run():
        await system.load_save(ctx, save_index=1)
        return await pipeline._logged(ctx, "end_turn", {}, action)

    result = asyncio.run(run())
    assert "GATE:RELOAD_UNCONFIRMED" in result
    assert conn.reload_pending is True
    assert gs._pending_end_turn is True
    action.assert_not_called()


def test_confirmed_load_resets_original_request(runtime, monkeypatch):
    ctx, gs, conn = runtime

    async def load(_conn, _index):
        conn.mutation_revision += 1
        return "Loaded checkpoint (VERIFIED)"

    monkeypatch.setattr(game_lifecycle, "load_save", load)
    asyncio.run(system.load_save(ctx, save_index=1))
    assert gs._pending_end_turn is False
    assert conn.turn_in_progress is False
    assert conn.reload_pending is False


def test_pending_observation_skips_entry_material_and_governance(runtime, monkeypatch):
    ctx, gs, _conn = runtime
    entry = AsyncMock(side_effect=AssertionError("must not collect InGame briefing"))
    preflight = AsyncMock(side_effect=AssertionError("must not repeat submission gate"))
    monkeypatch.setattr(pipeline, "_turn_context_gate", entry)
    monkeypatch.setattr(pipeline, "_belief_action_preflight", preflight)
    result = asyncio.run(pipeline._logged(
        ctx, "end_turn", {}, AsyncMock(return_value="TURN_PENDING"), localize=False
    ))
    assert result == "TURN_PENDING"
    entry.assert_not_called()
    preflight.assert_not_called()
    assert gs._pending_end_turn is True


def test_invalid_index_through_real_loader_keeps_pending(runtime):
    ctx, gs, conn = runtime
    sent = []

    async def execute(code, **kwargs):
        assert kwargs["turn_action"] == "load"
        conn.mutation_revision += 1
        sent.append(code)
        marker = re.search(r'print\("RELOAD_PROBE\|([a-f0-9]+)"\)', code)
        if marker:
            return [f"RELOAD_PROBE|{marker.group(1)}"]
        return ["ERR:INDEX_OUT_OF_RANGE|2"]

    conn.execute_mutation = execute
    result = asyncio.run(system.load_save(ctx, save_index=999))
    assert "LOAD_NOT_SUBMITTED" in result
    assert len(sent) == 2
    assert gs._pending_end_turn is True
    assert conn.reload_pending is False


@pytest.mark.parametrize("observed,expected", [("old-token", False), ("nil", True)])
def test_only_replaced_lua_world_confirms_same_turn_load(runtime, observed, expected):
    ctx, gs, conn = runtime
    gs.mark_reload_uncertain()
    conn._load_probe_token = "old-token"
    conn._load_probe_from_menu = False
    conn.gamecore_index, conn.ingame_index = 0, 1
    conn.reconnect = AsyncMock()
    conn.execute_write = AsyncMock(return_value=[f"RELOAD_WORLD|57|{observed}"])
    overview = AsyncMock(return_value="Turn 57")
    result = asyncio.run(pipeline._logged(ctx, "get_game_overview", {}, overview))
    assert gs._pending_end_turn is not expected
    assert conn.reload_pending is not expected
    assert overview.await_count == int(expected)
    if not expected:
        assert "GATE:RELOAD_UNCONFIRMED" in result
    conn.execute_write.assert_awaited_once()
    assert conn.execute_write.call_args.kwargs["turn_action"] == "load"


def test_missing_load_probe_cannot_confirm_old_world(runtime):
    ctx, gs, conn = runtime
    gs.mark_reload_uncertain()
    conn.reconnect = AsyncMock()
    assert asyncio.run(game_lifecycle.verify_loaded_world(conn)) is False
    conn.reconnect.assert_not_called()


def test_load_rejected_at_send_boundary_restores_pending(runtime):
    from civ_mcp.connection import CommandNotSentError

    ctx, gs, conn = runtime
    conn.load_revision = 0

    async def execute(code, **kwargs):
        conn.mutation_revision += 1
        marker = re.search(r'print\("RELOAD_PROBE\|([a-f0-9]+)"\)', code)
        if marker:
            return [f"RELOAD_PROBE|{marker.group(1)}"]
        raise CommandNotSentError("目标状态已消失")

    conn.execute_mutation = execute
    result = asyncio.run(system.load_save(ctx, save_index=1))
    assert "LOAD_NOT_SUBMITTED" in result
    assert gs._pending_end_turn is True
    assert conn.reload_pending is False


@pytest.mark.parametrize("change", ["other_token", "revision_during_read"])
def test_unrelated_marker_or_concurrent_load_is_not_reload_proof(runtime, change):
    _ctx, gs, conn = runtime
    gs.mark_reload_uncertain()
    conn._load_probe_token = "old-token"
    conn._load_probe_from_menu = False
    conn.load_revision = 1
    conn.gamecore_index, conn.ingame_index = 0, 1
    conn.reconnect = AsyncMock()

    async def read(*args, **kwargs):
        if change == "revision_during_read":
            conn.load_revision += 1
            return ["RELOAD_WORLD|57|nil"]
        return ["RELOAD_WORLD|57|other-token"]

    conn.execute_write = read
    assert asyncio.run(game_lifecycle.verify_loaded_world(conn)) is False
    assert gs._pending_end_turn is True


def test_overlapping_loads_do_not_replace_first_probe(runtime, monkeypatch):
    ctx, gs, conn = runtime
    calls = 0

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def load(_conn, _index):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            conn.mutation_revision += 1
            return "Loading save checkpoint"

        monkeypatch.setattr(game_lifecycle, "load_save", load)
        first = asyncio.create_task(system.load_save(ctx, save_index=1))
        await entered.wait()
        second = asyncio.create_task(system.load_save(ctx, save_index=1))
        await asyncio.sleep(0)
        release.set()
        await first
        return await second

    result = asyncio.run(run())
    assert "GATE:RELOAD_UNCONFIRMED" in result
    assert calls == 1
    assert gs._pending_end_turn is True


def test_frontend_submitted_error_never_falls_back_to_second_load(monkeypatch):
    conn = SimpleNamespace(gamecore_index=None, ensure_connected=AsyncMock())
    monkeypatch.setattr(game_lifecycle, "load_save_from_frontend", AsyncMock(
        return_value="Error: save loading began but auto-start unavailable"
    ))
    monkeypatch.setattr(pipeline.game_launcher, "ocr_recovery_enabled", lambda: True)
    fallback = AsyncMock()
    monkeypatch.setattr(pipeline.game_launcher, "load_save_from_menu", fallback)
    result = asyncio.run(game_lifecycle.load_game_save(conn, "checkpoint"))
    assert result.startswith("Error:")
    fallback.assert_not_called()


def test_missing_recovery_save_does_not_enter_reload_state(runtime, monkeypatch, tmp_path):
    ctx, gs, conn = runtime
    monkeypatch.setattr(pipeline.game_launcher, "SAVE_DIR", str(tmp_path))
    monkeypatch.setattr(pipeline.game_launcher, "SINGLE_SAVE_DIR", str(tmp_path))
    restart = AsyncMock()
    monkeypatch.setattr(pipeline.game_launcher, "restart_and_load", restart)
    result = asyncio.run(system.restart_and_load(ctx, save_name="absent"))
    assert "LOAD_NOT_SUBMITTED" in result
    restart.assert_not_called()
    assert gs._pending_end_turn is True
    assert conn.reload_pending is False


def test_explicit_recovery_verifies_world_before_unblocking(runtime, monkeypatch, tmp_path):
    ctx, gs, conn = runtime
    (tmp_path / "checkpoint.Civ6Save").touch()
    monkeypatch.setattr(pipeline.game_launcher, "SAVE_DIR", str(tmp_path))
    monkeypatch.setattr(pipeline.game_launcher, "SINGLE_SAVE_DIR", str(tmp_path))
    restart = AsyncMock(return_value="Load: GameCore/InGame ready")
    monkeypatch.setattr(pipeline.game_launcher, "restart_and_load", restart)

    async def verify(_conn):
        assert _conn.reload_pending is True
        assert gs._pending_end_turn is True
        return True

    monkeypatch.setattr(game_lifecycle, "verify_loaded_world", verify)
    result = asyncio.run(system.restart_and_load(ctx, save_name="checkpoint"))
    assert "CONFIRMED:" in result
    assert gs._pending_end_turn is False
    assert conn.reload_pending is False
    restart.assert_awaited_once()


def test_pending_diplomacy_input_does_not_require_ordinary_ingame_brief(runtime, monkeypatch):
    ctx, _gs, _conn = runtime
    entry = AsyncMock(side_effect=AssertionError("ordinary InGame briefing forbidden"))
    monkeypatch.setattr(pipeline, "_turn_context_gate", entry)
    response = AsyncMock(return_value="OK:RESPONDED")
    asyncio.run(pipeline._logged(ctx, "respond_to_diplomacy", {}, response))
    response.assert_awaited_once()
    entry.assert_not_called()
