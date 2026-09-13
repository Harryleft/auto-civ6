"""Offline integration of read collections, typed snapshots, and overview tools."""

from __future__ import annotations

import asyncio
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from civ_mcp import heartbeat, lua as lq, research_cache
from civ_mcp.game_state import GameState
from civ_mcp.lua.models import GreatPeopleOverview
from civ_mcp.server import pipeline
from civ_mcp.server.governance_snapshot import (
    _capture_governance_snapshot,
    _reusable_typed_snapshot_for_turn,
)
from civ_mcp.server.tools import queries


@pytest.fixture
def collected_game(monkeypatch):
    """Keep real GameState methods/decorators; replace only the Lua boundary."""

    class Connection:
        generation = 1
        mutation_revision = 0
        turn = 42
        gold = 10.0
        science = 5.0

        def __init__(self):
            self.calls = Counter()

        async def _execute(self, code):
            if code.startswith("print(Game.GetCurrentGameTurn())"):
                self.calls["turn"] += 1
                return [str(self.turn)]
            self.calls[code] += 1
            if code == "research":
                return [
                    "RESEARCH_RULES|fixture",
                    "CURRENT|None|-1|None|-1",
                    "COMPLETED|0|0",
                ]
            assert code in {
                "overview",
                "cities",
                "units",
                "diplomacy",
                "policies",
                "barbarians",
                "great_people",
                "threats",
                "notifications",
            }, f"unexpected offline query: {code}"
            return [code]

        execute_read = _execute
        execute_write = _execute

    conn = Connection()
    game = GameState(conn)
    # Normal play already has this end-turn diff baseline. Its first-session
    # bootstrap reads are a separate path from governance collection reuse.
    game._last_snapshot = lq.TurnSnapshot(
        turn=conn.turn,
        units={},
        cities={},
        current_research="None",
        current_civic="None",
    )
    builders = {
        "build_overview_query": "overview",
        "build_cities_query": "cities",
        "build_units_query": "units",
        "build_diplomacy_query": "diplomacy",
        "build_policies_query": "policies",
        "build_barbarian_overview_query": "barbarians",
        "build_great_people_overview_query": "great_people",
        "build_threat_scan_query": "threats",
        "build_notifications_query": "notifications",
    }
    for name, token in builders.items():
        monkeypatch.setattr(lq, name, lambda token=token: token)
    monkeypatch.setattr(
        research_cache, "build_tech_civics_query", lambda **kwargs: "research"
    )
    monkeypatch.setattr(
        lq,
        "parse_overview_response",
        lambda lines: lq.GameOverview(
            turn=conn.turn,
            player_id=0,
            civ_name="CIVILIZATION_TEST",
            leader_name="Test",
            gold=conn.gold,
            gold_per_turn=1.0,
            science_yield=conn.science,
            culture_yield=2.0,
            faith=0.0,
            current_research="None",
            current_civic="None",
            num_cities=0,
            num_units=0,
            ruleset="RULESET_STANDARD",
        ),
    )
    responses = {
        "parse_cities_response": ([], []),
        "parse_units_response": [],
        "parse_diplomacy_response": [],
        "parse_policies_response": lq.GovernmentStatus(
            "Chiefdom", "GOVERNMENT_CHIEFDOM"
        ),
        "parse_barbarian_overview_response": lq.BarbarianOverview(),
        "parse_great_people_overview_response": GreatPeopleOverview(standings=[]),
        "parse_threat_scan_response": [],
        "parse_notifications_response": [],
    }
    for name, value in responses.items():
        monkeypatch.setattr(lq, name, lambda lines, value=value: value)
    return game, conn


def _context(game, engine):
    logger = SimpleNamespace(
        _turn=42,
        set_turn=Mock(),
        bind_game=Mock(),
        log_tool_call=AsyncMock(),
        log_error=AsyncMock(),
        _emitter=SimpleNamespace(emit=AsyncMock()),
    )
    spatial = SimpleNamespace(
        _revealed_seeded=True, set_turn=Mock(), bind_game=Mock(), record=AsyncMock()
    )
    lifespan = SimpleNamespace(
        game=game, beliefs=engine, logger=logger, spatial=spatial
    )
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=lifespan))


def test_snapshot_reuses_preloaded_fields_only_within_the_current_collection(
    collected_game,
):
    game, conn = collected_game

    async def collect():
        with game.read_collection():
            await game.get_game_overview()
            await game.get_policies()
            await game.get_barbarian_overview()
            return await game.get_governance_snapshot()

    async def run():
        first = await collect()
        expected = {
            name: 1
            for name in (
                "overview",
                "cities",
                "units",
                "diplomacy",
                "research",
                "policies",
                "barbarians",
                "great_people",
                "threats",
                "notifications",
            )
        }
        expected["turn"] = 2
        assert dict(conn.calls) == expected
        conn.gold = 50.0  # same turn and transport revision, outside the collection
        second = await collect()
        assert first.turn == second.turn == 42
        assert first.overview.gold == 10.0 and second.overview.gold == 50.0
        assert dict(conn.calls) == {name: count * 2 for name, count in expected.items()}

    asyncio.run(run())


def test_real_overview_requests_refresh_snapshot_after_delayed_same_turn_change(
    collected_game,
    engine,
    monkeypatch,
):
    game, conn = collected_game
    ctx = _context(game, engine)
    monkeypatch.setattr(
        game, "get_game_identity", AsyncMock(return_value=("CIVILIZATION_TEST", 42))
    )
    monkeypatch.setattr(game, "check_game_over", AsyncMock(return_value=None))
    monkeypatch.setattr(heartbeat, "bind_game", Mock())
    monkeypatch.setattr(heartbeat, "write", Mock())
    monkeypatch.setattr(pipeline, "_bind_belief_engine", AsyncMock())
    monkeypatch.setattr(
        pipeline,
        "_belief_action_preflight",
        AsyncMock(
            return_value={
                "authorized": True,
                "decision_id": None,
                "route": "routine",
            }
        ),
    )
    monkeypatch.setattr(pipeline, "_record_belief_tool_result", AsyncMock())
    monkeypatch.setattr(
        pipeline,
        "_append_belief_context",
        AsyncMock(side_effect=lambda ctx, tool, text: text),
    )
    monkeypatch.setattr(
        pipeline, "_filter_downstream_result", lambda tool, params, text: text
    )
    monkeypatch.setattr(
        queries.nr, "narrate_overview", lambda overview: f"金币={overview.gold}"
    )
    monkeypatch.setattr(
        queries.nr, "narrate_barbarian_overview", lambda overview, **kwargs: "蛮族观测"
    )

    async def run():
        first = await queries.get_game_overview(ctx)
        assert engine.graph_view.node("player:0").attributes["gold"] == 10.0
        # This is precisely the journal-only evidence the old request path
        # considered reusable: no later submitted action exists in the log.
        assert _reusable_typed_snapshot_for_turn(engine, turn=42) is not None
        conn.gold = 95.0  # an already submitted operation becomes visible later
        second = await queries.get_game_overview(ctx)
        assert "金币=10.0" in first and "金币=95.0" in second
        assert engine.graph_view.turn == 42
        assert engine.graph_view.node("player:0").attributes["gold"] == 95.0
        assert conn.calls["overview"] == conn.calls["units"] == 2
        assert conn.calls["turn"] == 4  # both requests bracket a real collection
        assert ctx.request_context.lifespan_context.logger.log_error.await_count == 0

    asyncio.run(run())


def test_capture_delivers_world_change_context_after_the_first_baseline(
    collected_game, engine
):
    game, conn = collected_game
    ctx = _context(game, engine)

    async def run():
        first_snapshot, _, first_projection, _, _ = await _capture_governance_snapshot(
            ctx, engine
        )
        assert first_projection["world_changes"]["mode"] == "baseline"
        assert first_projection["graph_shadow"]["status"] == "matched"
        conn.science = 8.0
        second_snapshot, _, projection, _, _ = await _capture_governance_snapshot(
            ctx, engine
        )
        changes = projection["world_changes"]
        assert changes["mode"] == "changes"
        assert changes["snapshot_id"] == second_snapshot.snapshot_id
        assert first_snapshot.snapshot_id != second_snapshot.snapshot_id
        assert set(changes["affected_domains"]) == {"science", "production"}
        assert changes["node_changes"][0]["changed_fields"] == ["science_yield"]
        assert changes["counts"]["node_changes"] == 1
        assert projection["graph_shadow"]["status"] == "matched"

    asyncio.run(run())
