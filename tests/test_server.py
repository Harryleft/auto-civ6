"""Unit tests for `civ_mcp.server` — the MCP tool dispatch layer.

Tools are plain async functions registered on a FastMCP instance, so the tests
call them directly with a fake `Context` whose lifespan context carries scripted
stand-ins for GameState / GameLogger / SpatialTracker / CameraController /
MapCapture / GameOverWatchdog. Only process-level side effects are patched:
`server.heartbeat` (writes ~/.civ6-mcp/heartbeat.json), `server.game_launcher`
for the lifecycle tools (kills/launches real processes), and the diary path
helpers (home-directory I/O). Everything inside server.py itself — parameter
parsing, dispatch tables, error mapping, camera pushes, watchdog arming — is the
real code under test.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from civ_mcp import server
from civ_mcp.connection import LuaError
from civ_mcp.lua import GameOverview, GameOverStatus, PathingEstimate, TileInfo, UnitInfo

run = asyncio.run


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeConn:
    """Stands in for GameConnection on the fake GameState."""

    def __init__(self, gamecore_index: int | None = 0) -> None:
        self.gamecore_index = gamecore_index
        self.reconnect_calls = 0
        self.read_queries: list[str] = []

    async def execute_read(self, query, timeout=None):
        self.read_queries.append(query)
        return []

    async def execute_write(self, code, timeout=None):
        return []

    async def reconnect(self):
        self.reconnect_calls += 1


class FakeGameState:
    """Scripted stand-in for GameState.

    Every public async method is auto-stubbed: the call is recorded and the
    scripted value returned (or raised, if the scripted value is an exception).
    Unscripted methods return "<name>" so `_logged`'s result summarising works.
    `check_game_over` defaults to None (game running), the common case.
    """

    def __init__(self) -> None:
        self.conn = FakeConn()
        self._game_identity = None
        self._diary_written_turn = None
        self._end_turn_blocked = False
        self._pending_end_turn = False
        self._pending_end_turn_from = None
        self._hang_retry_active = False
        self._last_game_over = None
        self._advisor_budget_warning = None
        self.spatial = None
        self.calls: list[tuple[str, tuple, dict]] = []
        self.script: dict = {"check_game_over": None}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        async def stub(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            value = self.script.get(name, f"<{name}>")
            if isinstance(value, Exception):
                raise value
            return value

        return stub

    def called(self, name: str) -> list[tuple[str, tuple, dict]]:
        return [c for c in self.calls if c[0] == name]

    def last_args(self, name: str) -> tuple:
        calls = self.called(name)
        assert calls, f"expected {name} to be called"
        return calls[-1][1]


class FakeLogger:
    def __init__(self) -> None:
        self._turn = None
        self.session_id = "test-run"
        self.turns: list[int] = []
        self.bound_games: list[tuple] = []
        self.agent_models: list[str] = []
        self.errors: list[tuple[str, str]] = []
        self.tool_calls: list[tuple] = []
        self.game_overs: list[dict] = []
        self.emitted: list[tuple] = []
        self._emitter = SimpleNamespace(metadata={}, emit=self._emit)

    def set_turn(self, turn):
        self._turn = turn
        self.turns.append(turn)

    def set_agent_model(self, model):
        self.agent_models.append(model)

    def bind_game(self, civ, seed):
        self.bound_games.append((civ, seed))

    async def log_tool_call(self, tool, params, result, ms):
        self.tool_calls.append((tool, params, result, ms))

    async def log_error(self, tool, error):
        self.errors.append((tool, error))

    async def log_game_over(self, **kwargs):
        self.game_overs.append(kwargs)

    async def _emit(self, event, row):
        self.emitted.append((event, row))


class FakeSpatial:
    def __init__(self) -> None:
        self._revealed_seeded = False
        self.turns: list[int] = []
        self.bound_games: list[tuple] = []
        self.seeded: list[set] = []
        self.records: list[tuple] = []
        self.record_error: Exception | None = None

    def set_turn(self, turn):
        self.turns.append(turn)

    def bind_game(self, civ, seed):
        self.bound_games.append((civ, seed))

    def seed_revealed(self, tiles):
        self.seeded.append(tiles)
        self._revealed_seeded = True

    async def record(self, tool_name, params, result, ms, tiles=None):
        if self.record_error is not None:
            raise self.record_error
        self.records.append((tool_name, tiles))


class FakeCamera:
    def __init__(self) -> None:
        self.pushes: list[tuple] = []
        self.clears = 0

    def push(self, x, y, label=""):
        self.pushes.append((x, y, label))

    def clear(self):
        self.clears += 1


class FakeWatchdog:
    def __init__(self) -> None:
        self.armed = 0

    def arm(self):
        self.armed += 1


class FakeMapCapture:
    def __init__(self) -> None:
        self.bound_games: list[tuple] = []
        self.captures: list[tuple] = []

    def bind_game(self, civ, seed):
        self.bound_games.append((civ, seed))

    async def capture(self, conn, turn):
        self.captures.append((conn, turn))


def make_ctx(**overrides) -> SimpleNamespace:
    """Build a Context stand-in: request_context.lifespan_context.<service>."""
    app = SimpleNamespace(
        game=FakeGameState(),
        logger=FakeLogger(),
        camera=FakeCamera(),
        spatial=FakeSpatial(),
        map_capture=FakeMapCapture(),
        watchdog=FakeWatchdog(),
    )
    for key, value in overrides.items():
        setattr(app, key, value)
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))


def app_of(ctx) -> SimpleNamespace:
    return ctx.request_context.lifespan_context


@pytest.fixture(autouse=True)
def _isolate_server_state(monkeypatch):
    """Keep process-level side effects and cross-test counters out of tests."""
    monkeypatch.setattr(server.heartbeat, "write", lambda *a, **k: None)
    monkeypatch.setattr(server.heartbeat, "bind_game", lambda *a, **k: None)
    monkeypatch.delenv("CIV_MCP_AGENT_MODEL", raising=False)
    server._logged._conn_errors = 0
    yield
    server._logged._conn_errors = 0


def make_overview(turn: int = 12, player_id: int = 0) -> GameOverview:
    return GameOverview(
        turn=turn,
        player_id=player_id,
        civ_name="Rome",
        leader_name="Trajan",
        gold=100.0,
        gold_per_turn=5.0,
        science_yield=10.0,
        culture_yield=8.0,
        faith=3.0,
        current_research="TECH_POTTERY",
        current_civic="CIVIC_CRAFTSMANSHIP",
        num_cities=2,
        num_units=5,
    )


def make_gameover(defeat: bool = True) -> GameOverStatus:
    return GameOverStatus(
        is_game_over=True,
        is_defeat=defeat,
        winner_name="Egypt",
        winner_leader="Cleopatra",
        victory_type="VICTORY_RELIGIOUS",
        player_alive=not defeat,
    )


REFLECTIONS = {
    "tactical": "moved warriors",
    "strategic": "ahead on score",
    "tooling": "No issues",
    "planning": "build settler next",
    "hypothesis": "barb camp spawns T20",
}


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """The FastMCP instance exposes the expected tool surface."""

    def test_core_tools_are_registered(self):
        tools = server.mcp._tool_manager.list_tools()
        names = {t.name for t in tools}
        expected = {
            "get_game_overview",
            "get_units",
            "get_cities",
            "unit_action",
            "city_action",
            "end_turn",
            "set_city_production",
            "set_research",
            "propose_trade",
            "queue_wc_votes",
            "get_diary",
            "load_game_save",
            "restart_and_load",
            "run_lua",
            "spy_action",
        }
        assert expected <= names

    def test_every_tool_has_a_description(self):
        """The LLM only sees name + docstring — none may be blank."""
        tools = server.mcp._tool_manager.list_tools()
        assert tools, "no tools registered at all"
        for t in tools:
            assert t.description, f"tool {t.name} has no description"


# ---------------------------------------------------------------------------
# Log/result summaries (pure helpers)
# ---------------------------------------------------------------------------


class TestParamSummary:
    def test_empty_params_produce_empty_string(self):
        assert server._param_summary({}) == ""

    def test_long_values_are_truncated(self):
        out = server._param_summary({"code": "x" * 100})
        assert out == f"code={'x' * 37}..."


class TestResultSummary:
    def test_takes_the_first_line_only(self):
        assert server._result_summary("one\ntwo\nthree") == "one"

    def test_truncates_past_120_chars(self):
        line = "y" * 200
        out = server._result_summary(line)
        assert out == "y" * 120 + "..."

    def test_blank_first_line_stays_blank(self):
        assert server._result_summary("\nsecond") == ""


# ---------------------------------------------------------------------------
# _logged — the wrapper every tool result flows through
# ---------------------------------------------------------------------------


class TestLogged:
    """The error-mapping contract: what an agent sees when things go wrong."""

    def test_success_returns_result_and_logs_the_call(self):
        ctx = make_ctx()

        async def go():
            return await server._logged(ctx, "my_tool", {"a": 1}, lambda: _ret("ok"))

        async def _ret(v):
            return v

        assert run(go()) == "ok"
        logger = app_of(ctx).logger
        assert len(logger.tool_calls) == 1
        tool, params, result, ms = logger.tool_calls[0]
        assert (tool, params, result) == ("my_tool", {"a": 1}, "ok")
        assert ms >= 0
        assert logger.errors == []

    def test_lua_error_becomes_an_error_string(self):
        """The game answered with a Lua failure — the agent sees 'Error: ...'."""
        ctx = make_ctx()

        async def go():
            async def boom():
                raise LuaError("ERR:attempt to index nil")

            return await server._logged(ctx, "my_tool", {}, boom)

        assert run(go()) == "Error: ERR:attempt to index nil"
        assert app_of(ctx).logger.errors == [("my_tool", "Error: ERR:attempt to index nil")]

    def test_value_error_becomes_an_error_string(self):
        ctx = make_ctx()

        async def go():
            async def boom():
                raise ValueError("bad int")

            return await server._logged(ctx, "my_tool", {}, boom)

        assert run(go()) == "Error: bad int"

    def test_connection_error_is_returned_with_the_error_prefix(self):
        """Connection loss is mapped to "Error: ..." like every other failure.

        Lua/validation failures come back as "Error: ..." and transport
        failures now do too, so an agent keying on the "Error:" prefix
        catches both.
        """
        ctx = make_ctx()

        async def go():
            async def boom():
                raise ConnectionError("socket closed by peer")

            return await server._logged(ctx, "my_tool", {}, boom)

        assert run(go()) == "Error: socket closed by peer"
        assert server._logged._conn_errors == 1

    def test_success_resets_the_connection_error_counter(self):
        ctx = make_ctx()

        async def go():
            async def boom():
                raise ConnectionError("dead")

            async def fine():
                return "ok"

            await server._logged(ctx, "t", {}, boom)
            await server._logged(ctx, "t", {}, boom)
            mid = server._logged._conn_errors
            await server._logged(ctx, "t", {}, fine)
            return mid

        assert run(go()) == 2
        assert server._logged._conn_errors == 0

    def test_lua_error_resets_the_connection_error_counter(self):
        """A Lua error means the game answered — the connection is alive.

        After 4 connection failures, a Lua error resets the counter to 0,
        so one more dead socket does not trigger the auto-restart: the
        last five calls were not all transport failures.
        """
        ctx = make_ctx()

        async def go():
            async def dead():
                raise ConnectionError("dead")

            async def lua():
                raise LuaError("ERR:bad lua")

            for _ in range(4):
                await server._logged(ctx, "t", {}, dead)
            await server._logged(ctx, "t", {}, lua)
            return server._logged._conn_errors

        assert run(go()) == 0

    def test_five_consecutive_connection_errors_trigger_auto_restart(self, monkeypatch):
        """The recovery path: after 5 dead sockets, restart_and_load fires."""
        ctx = make_ctx()
        restarts: list = []

        async def fake_restart(save):
            restarts.append(save)
            return "restarted"

        monkeypatch.setattr(server.game_launcher, "restart_and_load", fake_restart)

        async def go():
            async def dead():
                raise ConnectionError("dead")

            results = [await server._logged(ctx, "t", {}, dead) for _ in range(5)]
            return results

        results = run(go())
        assert results == ["Error: dead"] * 5
        assert len(restarts) == 1
        # Counter reset after triggering, and the conn was reconnected.
        assert server._logged._conn_errors == 0
        assert app_of(ctx).game.conn.reconnect_calls >= 1

    def test_a_spatial_record_failure_is_swallowed(self):
        """Telemetry must never break a tool call."""
        ctx = make_ctx()
        app_of(ctx).spatial.record_error = RuntimeError("telemetry exploded")

        async def go():
            async def fine():
                return "ok"

            return await server._logged(ctx, "t", {}, fine)

        assert run(go()) == "ok"


# ---------------------------------------------------------------------------
# get_game_overview
# ---------------------------------------------------------------------------


class TestGetGameOverview:
    def test_happy_path_narrates_and_binds_turn_and_game(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_game_overview"] = make_overview(turn=12)
        gs.script["get_game_identity"] = ("CIVILIZATION_ROME", 4242)

        async def go():
            return await server.get_game_overview(ctx)

        text = run(go())
        assert "Turn 12" in text
        assert "Rome" in text
        logger = app_of(ctx).logger
        spatial = app_of(ctx).spatial
        assert logger._turn == 12
        assert 12 in spatial.turns
        assert ("CIVILIZATION_ROME", 4242) in logger.bound_games
        assert ("CIVILIZATION_ROME", 4242) in spatial.bound_games

    def test_revealed_tile_seeding_happens(self):
        """The seeding block runs: spatial tracker is seeded on first overview.

        server.py imports the lua module as `lq`, so the seed query is built,
        the read is issued, and the spatial tracker is seeded for the
        visibility diff. The overview itself still succeeds.
        """
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_game_overview"] = make_overview()
        gs.script["get_game_identity"] = ("CIVILIZATION_ROME", 1)
        spatial = app_of(ctx).spatial

        async def go():
            return await server.get_game_overview(ctx)

        text = run(go())
        assert "Turn 12" in text  # tool itself succeeds
        assert len(spatial.seeded) == 1
        assert spatial._revealed_seeded is True
        # The seed query was built and the read was issued.
        assert len(gs.conn.read_queries) == 1

    def test_identity_failure_is_swallowed(self):
        """A failure in get_game_identity must not break the overview."""
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_game_overview"] = make_overview()
        gs.script["get_game_identity"] = LuaError("ERR:no identity")

        async def go():
            return await server.get_game_overview(ctx)

        assert "Turn 12" in run(go())

    def test_game_over_defeat_is_appended(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_game_overview"] = make_overview()
        gs.script["get_game_identity"] = ("CIVILIZATION_ROME", 1)
        gs.script["check_game_over"] = make_gameover(defeat=True)

        async def go():
            return await server.get_game_overview(ctx)

        text = run(go())
        assert "*** GAME OVER — DEFEAT ***" in text
        assert "Cleopatra of Egypt won a Religious victory." in text
        over = app_of(ctx).logger.game_overs[0]
        assert over["is_defeat"] is True
        assert over["victory_type"] == "Religious"

    def test_game_over_victory_is_appended(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_game_overview"] = make_overview()
        gs.script["get_game_identity"] = ("CIVILIZATION_ROME", 1)
        gs.script["check_game_over"] = make_gameover(defeat=False)

        async def go():
            return await server.get_game_overview(ctx)

        text = run(go())
        assert "*** GAME OVER — VICTORY ***" in text

    def test_lua_error_from_the_query_is_mapped(self):
        ctx = make_ctx()
        app_of(ctx).game.script["get_game_overview"] = LuaError("ERR:gamecore gone")

        async def go():
            return await server.get_game_overview(ctx)

        assert run(go()) == "Error: ERR:gamecore gone"


# ---------------------------------------------------------------------------
# get_units / get_cities / get_spies (query + narrate wiring)
# ---------------------------------------------------------------------------


class TestGetUnits:
    def test_happy_path_narrates_real_units(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_units"] = [
            UnitInfo(
                unit_id=196615,
                unit_index=7,
                name="Warrior",
                unit_type="UNIT_WARRIOR",
                x=3,
                y=4,
                moves_remaining=2,
                max_moves=2,
                health=100,
                max_health=100,
            )
        ]
        gs.script["get_threat_scan"] = []
        gs.script["get_trade_routes"] = None

        async def go():
            return await server.get_units(ctx)

        text = run(go())
        assert "Warrior" in text

    def test_unit_tiles_are_recorded_for_the_spatial_tracker(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_units"] = [
            UnitInfo(1, 1, "Warrior", "UNIT_WARRIOR", 3, 4, 2, 2, 100, 100),
            # x=-1 marks a unit with no map position (e.g. consumed/garrisoned)
            UnitInfo(2, 2, "Spy", "UNIT_SPY", -1, -1, 2, 2, 100, 100),
        ]
        gs.script["get_threat_scan"] = LuaError("ERR:no scan")
        gs.script["get_trade_routes"] = LuaError("ERR:no routes")

        async def go():
            return await server.get_units(ctx)

        run(go())
        spatial = app_of(ctx).spatial
        assert spatial.records[-1] == ("get_units", {(3, 4)})

    def test_threat_and_trade_failures_are_tolerated(self):
        """Threat scan and trade routes are best-effort enrichments."""
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_units"] = []
        gs.script["get_threat_scan"] = ConnectionError("dead")
        gs.script["get_trade_routes"] = LuaError("ERR:nope")

        async def go():
            return await server.get_units(ctx)

        assert run(go()) == "No units."


class TestGetCities:
    def test_empty_empire(self):
        ctx = make_ctx()
        app_of(ctx).game.script["get_cities"] = ([], [])

        async def go():
            return await server.get_cities(ctx)

        assert run(go()) == "No cities."

    def test_connection_loss_returns_error_prefixed_message(self):
        """Transport failure is mapped to "Error: ..." like other failures."""
        ctx = make_ctx()
        app_of(ctx).game.script["get_cities"] = ConnectionError("socket died")

        async def go():
            return await server.get_cities(ctx)

        assert run(go()) == "Error: socket died"


class TestGetSpies:
    def test_no_spies_yet(self):
        ctx = make_ctx()
        app_of(ctx).game.script["get_spies"] = []

        async def go():
            return await server.get_spies(ctx)

        assert "No spies" in run(go())


# ---------------------------------------------------------------------------
# unit_action — the dispatch table
# ---------------------------------------------------------------------------


class TestUnitActionDispatch:
    @pytest.mark.parametrize(
        ("action", "method"),
        [
            ("fortify", "fortify_unit"),
            ("skip", "skip_unit"),
            ("found_city", "found_city"),
            ("repair", "repair_improvement"),
            ("remove_improvement", "remove_improvement"),
            ("remove_feature", "remove_feature"),
            ("build_route", "build_route"),
            ("automate", "automate_explore"),
            ("heal", "heal_unit"),
            ("alert", "alert_unit"),
            ("sleep", "sleep_unit"),
            ("delete", "delete_unit"),
            ("activate", "activate_great_person"),
            ("sacrifice_charges", "sacrifice_builder_charges"),
            ("spread_religion", "spread_religion"),
        ],
    )
    def test_parameterless_actions_dispatch(self, action, method):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.unit_action(ctx, 42, action)

        assert run(go()) == f"<{method}>"
        assert gs.last_args(method) == (42,)

    def test_composite_unit_id_is_reduced_to_an_index(self):
        """get_units shows composite ids; the Lua layer wants id % 65536."""
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.unit_action(ctx, 3 * 65536 + 7, "move", 10, 11)

        run(go())
        assert gs.last_args("move_unit") == (7, 10, 11)

    def test_move_pushes_the_camera(self):
        ctx = make_ctx()

        async def go():
            return await server.unit_action(ctx, 1, "move", 10, 11)

        run(go())
        assert app_of(ctx).camera.pushes == [(10, 11, "move→(10,11)")]

    def test_fortify_does_not_push_the_camera(self):
        ctx = make_ctx()

        async def go():
            return await server.unit_action(ctx, 1, "fortify")

        run(go())
        assert app_of(ctx).camera.pushes == []

    def test_camera_is_pushed_even_when_the_move_fails(self):
        """Pins current behaviour: the camera moves on error results too.

        The push happens after `_logged` without inspecting the result, so a
        move rejected by the game still yanks the spectator camera.
        """
        ctx = make_ctx()
        app_of(ctx).game.script["move_unit"] = LuaError("ERR:unit cannot move")

        async def go():
            return await server.unit_action(ctx, 1, "move", 5, 6)

        assert run(go()) == "Error: ERR:unit cannot move"
        assert app_of(ctx).camera.pushes == [(5, 6, "move→(5,6)")]


class TestUnitActionValidation:
    """Parameter errors must come back as text, not exceptions."""

    @pytest.mark.parametrize(
        ("action", "kwargs", "fragment"),
        [
            ("move", {}, "move requires target_x and target_y"),
            ("move", {"target_x": 3}, "move requires target_x and target_y"),
            ("attack", {}, "attack requires target_x and target_y"),
            (
                "trade_route",
                {},
                "trade_route requires target_x and target_y of destination city",
            ),
            (
                "teleport",
                {},
                "teleport requires target_x and target_y of the destination city",
            ),
            ("improve", {}, "improve requires improvement name"),
        ],
    )
    def test_missing_parameters_produce_errors(self, action, kwargs, fragment):
        ctx = make_ctx()

        async def go():
            return await server.unit_action(ctx, 1, action, **kwargs)

        text = run(go())
        assert text.startswith("Error:")
        assert fragment in text

    def test_unknown_action_lists_the_valid_set(self):
        ctx = make_ctx()

        async def go():
            return await server.unit_action(ctx, 1, "dance")

        text = run(go())
        assert text.startswith("Error: Unknown action 'dance'")
        assert "move" in text and "fortify" in text

    def test_improve_dispatches_with_the_improvement_name(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.unit_action(
                ctx, 9, "improve", improvement="IMPROVEMENT_FARM"
            )

        run(go())
        assert gs.last_args("improve_tile") == (9, "IMPROVEMENT_FARM")

    def test_action_matching_is_case_insensitive(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.unit_action(ctx, 9, "MOVE", 1, 2)

        run(go())
        assert gs.last_args("move_unit") == (9, 1, 2)


# ---------------------------------------------------------------------------
# city_action
# ---------------------------------------------------------------------------


class TestCityAction:
    def test_attack_requires_coordinates(self):
        ctx = make_ctx()

        async def go():
            return await server.city_action(ctx, 5, "attack")

        assert run(go()) == "Error: attack requires target_x and target_y"

    def test_attack_dispatches_and_pushes_the_camera(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.city_action(ctx, 5, "attack", 7, 8)

        run(go())
        assert gs.last_args("city_attack") == (5, 7, 8)
        assert app_of(ctx).camera.pushes == [(7, 8, "city attack")]

    @pytest.mark.parametrize(
        "action", ["keep", "reject", "raze", "liberate_founder", "liberate_previous"]
    )
    def test_capture_decisions_dispatch_to_resolve(self, action):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.city_action(ctx, 5, action)

        run(go())
        assert gs.last_args("resolve_city_capture") == (action,)

    def test_unknown_action_lists_available(self):
        ctx = make_ctx()

        async def go():
            return await server.city_action(ctx, 5, "annex")

        text = run(go())
        assert "Unknown city action 'annex'" in text
        assert "keep" in text and "raze" in text


# ---------------------------------------------------------------------------
# Production / research / purchasing
# ---------------------------------------------------------------------------


class TestSetCityProduction:
    def test_passes_all_arguments_through(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.set_city_production(
                ctx, 3, "DISTRICT", "DISTRICT_CAMPUS", 10, 11
            )

        run(go())
        assert gs.last_args("set_city_production") == (
            3,
            "DISTRICT",
            "DISTRICT_CAMPUS",
            10,
            11,
        )

    def test_coordinates_default_to_none(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.set_city_production(ctx, 3, "UNIT", "UNIT_WARRIOR")

        run(go())
        assert gs.last_args("set_city_production") == (3, "UNIT", "UNIT_WARRIOR", None, None)


class TestPurchaseItem:
    def test_defaults_to_gold(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.purchase_item(ctx, 3, "UNIT", "UNIT_WARRIOR")

        run(go())
        assert gs.last_args("purchase_item") == (3, "UNIT", "UNIT_WARRIOR", "YIELD_GOLD")

    def test_faith_purchase(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.purchase_item(
                ctx, 3, "UNIT", "UNIT_MISSIONARY", "YIELD_FAITH"
            )

        run(go())
        assert gs.last_args("purchase_item") == (
            3,
            "UNIT",
            "UNIT_MISSIONARY",
            "YIELD_FAITH",
        )


class TestSetResearch:
    def test_tech_is_the_default(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.set_research(ctx, "TECH_POTTERY")

        run(go())
        assert gs.last_args("set_research") == ("TECH_POTTERY",)
        assert gs.called("set_civic") == []

    def test_civic_category_routes_to_set_civic(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.set_research(ctx, "CIVIC_CRAFTSMANSHIP", "Civic")

        run(go())
        assert gs.last_args("set_civic") == ("CIVIC_CRAFTSMANSHIP",)


# ---------------------------------------------------------------------------
# propose_trade — item list construction
# ---------------------------------------------------------------------------


class TestProposeTrade:
    def test_empty_deal_is_rejected_before_touching_the_game(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.propose_trade(ctx, 2)

        assert run(go()) == "Error: must specify at least one offer or request item"
        assert gs.calls == []

    def test_gold_and_gpt_become_distinct_durations(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.propose_trade(
                ctx, 2, offer_gold=100, request_gold_per_turn=3
            )

        run(go())
        _pid, offer, request = gs.last_args("propose_trade")
        assert offer == [{"type": "GOLD", "amount": 100, "duration": 0}]
        assert request == [{"type": "GOLD", "amount": 3, "duration": 30}]

    def test_resources_split_on_commas_and_trim_whitespace(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.propose_trade(
                ctx, 2, offer_resources="RESOURCE_SILK, RESOURCE_TEA ,,"
            )

        run(go())
        _pid, offer, _request = gs.last_args("propose_trade")
        assert offer == [
            {"type": "RESOURCE", "name": "RESOURCE_SILK", "amount": 1, "duration": 30},
            {"type": "RESOURCE", "name": "RESOURCE_TEA", "amount": 1, "duration": 30},
        ]

    def test_joint_war_is_added_to_both_sides(self):
        """Joint war is mutual: both offer and request carry the agreement."""
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.propose_trade(ctx, 2, joint_war_target=5)

        run(go())
        _pid, offer, request = gs.last_args("propose_trade")
        assert offer == [{"type": "AGREEMENT", "subtype": "JOINT_WAR"}]
        assert request == [{"type": "AGREEMENT", "subtype": "JOINT_WAR"}]

    def test_open_borders_and_favor(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.propose_trade(
                ctx, 2, offer_open_borders=True, request_favor=10
            )

        run(go())
        _pid, offer, request = gs.last_args("propose_trade")
        assert offer == [{"type": "AGREEMENT", "subtype": "OPEN_BORDERS"}]
        assert request == [{"type": "FAVOR", "amount": 10}]

    def test_test_mode_calls_test_trade_instead(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.propose_trade(ctx, 2, offer_gold=50, mode="test")

        run(go())
        assert gs.called("propose_trade") == []
        _pid, offer, _request = gs.last_args("test_trade")
        assert offer == [{"type": "GOLD", "amount": 50, "duration": 0}]


# ---------------------------------------------------------------------------
# set_policies — string parsing
# ---------------------------------------------------------------------------


class TestSetPolicies:
    def test_parses_slot_assignments(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.set_policies(ctx, "0=POLICY_AGOGE, 1=NONE")

        run(go())
        assert gs.last_args("set_policies") == ({0: "POLICY_AGOGE", 1: "NONE"},)

    def test_pairs_without_an_equals_sign_are_skipped(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.set_policies(ctx, "junk,2=POLICY_SURVEY")

        run(go())
        assert gs.last_args("set_policies") == ({2: "POLICY_SURVEY"},)

    def test_no_valid_assignments_is_an_error(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.set_policies(ctx, "no equals signs here")

        text = run(go())
        assert text.startswith("Error: no valid assignments")
        assert gs.calls == []

    def test_a_non_numeric_slot_is_mapped_to_an_error_string(self):
        """int() raises ValueError inside _run; _logged maps it to 'Error: ...'."""
        ctx = make_ctx()

        async def go():
            return await server.set_policies(ctx, "abc=POLICY_AGOGE")

        text = run(go())
        assert text.startswith("Error:")


# ---------------------------------------------------------------------------
# queue_wc_votes
# ---------------------------------------------------------------------------


class TestQueueWcVotes:
    def test_valid_json_is_parsed_and_forwarded(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        votes = [{"hash": -513644209, "option": 1, "target": 2, "votes": 5}]

        async def go():
            return await server.queue_wc_votes(ctx, json.dumps(votes))

        run(go())
        assert gs.last_args("queue_wc_votes") == (votes,)

    def test_invalid_json_returns_an_error_string(self):
        """Malformed votes JSON comes back as "Error: ...".

        json.loads runs inside the `_logged` wrapper, so a malformed payload
        is mapped to the same "Error: ..." string every other validation
        failure produces instead of raising to the MCP framework.
        """
        ctx = make_ctx()

        async def go():
            return await server.queue_wc_votes(ctx, "not json {")

        assert run(go()).startswith("Error:")


# ---------------------------------------------------------------------------
# spy_action
# ---------------------------------------------------------------------------


class TestSpyAction:
    def test_travel_uses_the_unit_index(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.spy_action(ctx, 65536 + 4, "travel", 10, 11)

        run(go())
        assert gs.last_args("spy_travel") == (4, 10, 11)
        assert app_of(ctx).camera.pushes == [(10, 11, "spy travel")]

    def test_mission_names_are_uppercased(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.spy_action(ctx, 4, "siphon_funds", 10, 11)

        run(go())
        assert gs.last_args("spy_mission") == (4, "SIPHON_FUNDS", 10, 11)


# ---------------------------------------------------------------------------
# get_map_area / get_settle_advisor / get_pathing_estimate
# ---------------------------------------------------------------------------


class TestGetMapArea:
    def test_radius_is_capped_at_four(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_map_area"] = []

        async def go():
            return await server.get_map_area(ctx, 10, 10, radius=99)

        assert run(go()) == "No tiles."
        assert gs.last_args("get_map_area") == (10, 10, 4)

    def test_negative_radius_is_clamped_to_one(self):
        """Both bounds are clamped: radius is at least 1, at most 4."""
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_map_area"] = []

        async def go():
            return await server.get_map_area(ctx, 10, 10, radius=-3)

        run(go())
        assert gs.last_args("get_map_area") == (10, 10, 1)

    def test_tiles_are_narrated_and_camera_pushed(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_map_area"] = [
            TileInfo(
                x=10,
                y=11,
                terrain="TERRAIN_GRASS",
                feature=None,
                resource=None,
                is_hills=True,
                is_river=False,
                is_coastal=False,
                improvement=None,
                owner_id=0,
            )
        ]

        async def go():
            return await server.get_map_area(ctx, 10, 11, radius=1)

        text = run(go())
        assert "GRASS" in text
        assert app_of(ctx).camera.pushes == [(10, 11, "map_area (10,11)")]


class TestGetSettleAdvisor:
    def test_composite_id_is_reduced(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_settle_advisor"] = "Settle at (3,4)"

        async def go():
            return await server.get_settle_advisor(ctx, 2 * 65536 + 5)

        assert run(go()) == "Settle at (3,4)"
        assert gs.last_args("get_settle_advisor") == (5,)


class TestGetPathingEstimate:
    def test_composite_id_is_reduced(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_pathing_estimate"] = PathingEstimate(
            turns=2, total_tiles=9, reachable_this_turn=3
        )

        async def go():
            return await server.get_pathing_estimate(ctx, 65536 + 8, 1, 2)

        assert "~2 turns" in run(go())
        assert gs.last_args("get_pathing_estimate") == (8, 1, 2)


# ---------------------------------------------------------------------------
# Advisors (district / wonder) — error strings and budget warnings
# ---------------------------------------------------------------------------


class TestGetDistrictAdvisor:
    def test_a_string_result_becomes_an_error(self):
        """GameState signals placement failures by returning a plain string."""
        ctx = make_ctx()
        app_of(ctx).game.script["get_district_advisor"] = "city not found"

        async def go():
            return await server.get_district_advisor(ctx, 99, "DISTRICT_CAMPUS")

        assert run(go()) == "Error: city not found"

    def test_budget_warning_is_prepended_once_and_cleared(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_district_advisor"] = []
        gs._advisor_budget_warning = "budget exceeded"

        async def go():
            return await server.get_district_advisor(ctx, 1, "DISTRICT_CAMPUS")

        text = run(go())
        assert text.startswith("!! budget exceeded")
        assert gs._advisor_budget_warning is None

    def test_no_warning_means_plain_narration(self):
        ctx = make_ctx()
        app_of(ctx).game.script["get_district_advisor"] = []

        async def go():
            return await server.get_district_advisor(ctx, 1, "DISTRICT_CAMPUS")

        text = run(go())
        assert text == "No valid placement tiles for DISTRICT_CAMPUS."


class TestGetWonderAdvisor:
    def test_a_string_result_becomes_an_error(self):
        ctx = make_ctx()
        app_of(ctx).game.script["get_wonder_advisor"] = "wonder already built"

        async def go():
            return await server.get_wonder_advisor(ctx, 1, "BUILDING_PYRAMIDS")

        assert run(go()) == "Error: wonder already built"


class TestGetGpAdvisor:
    def test_none_result_produces_a_guidance_message(self):
        ctx = make_ctx()
        app_of(ctx).game.script["get_gp_advisor"] = None

        async def go():
            return await server.get_gp_advisor(ctx, 7)

        assert run(go()) == "Could not get GP advisor info. Is this a Great Person unit?"


# ---------------------------------------------------------------------------
# end_turn
# ---------------------------------------------------------------------------


def _prime_end_turn(gs, turn=5, result="Turn 5 -> 6"):
    """Script the GameState calls end_turn makes before/after advancing."""
    gs.script["get_game_overview"] = make_overview(turn=turn)
    gs.script["get_game_identity"] = ("CIVILIZATION_ROME", 77)
    gs.script["end_turn"] = result
    gs._diary_written_turn = turn  # skip the diary-snapshot write path


class TestEndTurnReflections:
    def test_missing_reflections_are_rejected_before_advancing(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.end_turn(ctx, tactical="did stuff")

        text = run(go())
        assert "Empty reflections: strategic, tooling, planning, hypothesis" in text
        assert gs.called("end_turn") == []

    def test_all_reflections_required_even_whitespace(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.end_turn(
                ctx,
                tactical="ok",
                strategic="   ",
                tooling="ok",
                planning="ok",
                hypothesis="ok",
            )

        text = run(go())
        assert "Empty reflections: strategic" in text
        assert gs.called("end_turn") == []


class TestEndTurnAdvance:
    def test_successful_advance_updates_turn_clears_camera_arms_watchdog(self):
        ctx = make_ctx()
        app = app_of(ctx)
        _prime_end_turn(app.game, turn=5, result="Turn 5 -> 6")

        async def go():
            return await server.end_turn(ctx, **REFLECTIONS)

        assert run(go()) == "Turn 5 -> 6"
        assert app.logger._turn == 6
        assert 6 in app.spatial.turns
        assert app.camera.clears == 1
        assert app.watchdog.armed == 1
        assert app.game._end_turn_blocked is False
        # Map capture ran for the new turn.
        assert app.map_capture.captures[-1][1] == 6

    def test_agent_model_env_var_is_forwarded_to_the_logger(self, monkeypatch):
        monkeypatch.setenv("CIV_MCP_AGENT_MODEL", "test-model-1")
        ctx = make_ctx()
        app = app_of(ctx)
        _prime_end_turn(app.game)

        async def go():
            return await server.end_turn(ctx, **REFLECTIONS)

        run(go())
        assert app.logger.agent_models == ["test-model-1"]

    def test_a_blocked_turn_sets_the_blocked_flag(self):
        ctx = make_ctx()
        app = app_of(ctx)
        _prime_end_turn(app.game, result="Turn paused: diplomacy encounter open")

        async def go():
            return await server.end_turn(ctx, **REFLECTIONS)

        assert run(go()) == "Turn paused: diplomacy encounter open"
        assert app.game._end_turn_blocked is True
        # A blocked turn is not an advance: camera stays, logger turn unchanged.
        assert app.camera.clears == 0
        assert app.logger._turn == 5  # from the pre-turn overview sync

    def test_retry_after_blocker_merges_reflections(self, monkeypatch, tmp_path):
        """Second end_turn after a blocker merges into the existing diary row."""
        ctx = make_ctx()
        app = app_of(ctx)
        _prime_end_turn(app.game, turn=9, result="Turn 9 -> 10")
        app.game._end_turn_blocked = True  # previous call hit a blocker
        app.game._diary_written_turn = 9

        monkeypatch.setattr(server, "_diary_path", lambda *a: tmp_path / "d.jsonl")
        merges: list[tuple] = []

        def fake_merge(path, turn, reflections):
            merges.append((path, turn, reflections))
            return {"turn": turn, "merged": True}

        monkeypatch.setattr(server, "_merge_agent_reflections", fake_merge)

        async def go():
            return await server.end_turn(ctx, **REFLECTIONS)

        assert run(go()) == "Turn 9 -> 10"
        assert len(merges) == 1
        assert merges[0][1] == 9
        assert merges[0][2] == REFLECTIONS
        # The merged row was re-emitted for the telemetry sinks.
        assert any(evt == server.EVENT_DIARY_ROW for evt, _ in app.logger.emitted)

    def test_world_congress_blocker_auto_submits_on_the_third_repeat(self):
        """Same-turn WC blocker three times in a row → submit_congress fires."""
        ctx = make_ctx()
        app = app_of(ctx)
        _prime_end_turn(app.game, turn=20, result="World Congress fires this turn")
        app.game.script["submit_congress"] = "submitted"

        async def go():
            results = []
            for _ in range(3):
                results.append(await server.end_turn(ctx, **REFLECTIONS))
            return results

        run(go())
        assert app.game._end_turn_blocked is True
        assert len(app.game.called("submit_congress")) == 1


class TestEndTurnHang:
    def test_hang_with_no_save_file_returns_the_hang_message(self, monkeypatch):
        """Recovery needs the autosave on disk; without it the agent sees HANG."""
        ctx = make_ctx()
        app = app_of(ctx)
        _prime_end_turn(
            app.game, turn=57, result="HANG:57:0_MCP_999997_UNITTEST|AI stuck"
        )
        restarts: list = []

        async def fake_restart(save):
            restarts.append(save)
            return "restarted"

        monkeypatch.setattr(server.game_launcher, "restart_and_load", fake_restart)

        async def go():
            return await server.end_turn(ctx, **REFLECTIONS)

        text = run(go())
        assert text.startswith("HANG:57:")
        assert restarts == []  # no save on disk → no recovery attempt
        assert app.watchdog.armed == 1

    def test_hang_during_game_over_is_rewritten_to_defeat(self):
        """The game can end while InGame Lua is frozen: end_turn returns HANG
        but the GameCore fallback in check_game_over still sees the outcome."""
        ctx = make_ctx()
        app = app_of(ctx)
        _prime_end_turn(
            app.game, turn=57, result="HANG:57:0_MCP_999998_UNITTEST|AI stuck"
        )
        app.game.script["check_game_over"] = make_gameover(defeat=True)

        async def go():
            return await server.end_turn(ctx, **REFLECTIONS)

        text = run(go())
        assert text.startswith("GAME OVER — DEFEAT.")
        assert "Cleopatra of Egypt won a Religious victory." in text
        assert app.logger.game_overs[0]["is_defeat"] is True
        # Game over → watchdog is not armed for further polling.
        assert app.watchdog.armed == 0


# ---------------------------------------------------------------------------
# get_diary
# ---------------------------------------------------------------------------


@pytest.fixture
def diary_file(tmp_path, monkeypatch):
    """Point the server's diary path at a tmp file; returns the path."""
    path = tmp_path / "diary.jsonl"
    monkeypatch.setattr(server, "_diary_path", lambda *a: path)
    return path


def _write_diary(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


LEGACY_T1 = {"turn": 1, "score": {"total": 10}, "reflections": {"tactical": "founded capital"}}
V2_AGENT_T2 = {
    "v": 1,
    "turn": 2,
    "is_agent": True,
    "score": 25,
    "cities": 2,
    "reflections": {"tactical": "scouted east"},
}
V2_RIVAL_T2 = {"v": 1, "turn": 2, "score": 22, "cities": 2}  # no is_agent
LEGACY_T3 = {"turn": 3, "score": {"total": 40}, "reflections": {"tactical": "built monument"}}


class TestGetDiary:
    def test_game_not_detected(self):
        ctx = make_ctx()
        app_of(ctx).game.script["get_game_identity"] = LuaError("ERR:no game")

        async def go():
            return await server.get_diary(ctx)

        assert run(go()) == "Could not detect current game. Is the game running?"

    def test_no_diary_file(self, diary_file):
        ctx = make_ctx()
        app_of(ctx).game.script["get_game_identity"] = ("CIVILIZATION_ROME", 7)

        async def go():
            return await server.get_diary(ctx)

        text = run(go())
        assert "No diary entries yet for this game" in text
        assert "CIVILIZATION_ROME" in text

    def test_reads_entries_and_drops_non_agent_v2_rows(self, diary_file):
        """v2 diaries have one row per player per turn; only agent rows shown."""
        ctx = make_ctx()
        app_of(ctx).game.script["get_game_identity"] = ("CIVILIZATION_ROME", 7)
        _write_diary(diary_file, [LEGACY_T1, V2_AGENT_T2, V2_RIVAL_T2, LEGACY_T3])

        async def go():
            return await server.get_diary(ctx)

        text = run(go())
        assert "=== Turn 1 ===" in text
        assert "=== Turn 2 ===" in text
        assert "=== Turn 3 ===" in text
        # The rival row for turn 2 must not appear (only one T2 block).
        assert text.count("=== Turn 2 ===") == 1

    def test_last_n_limits_the_output(self, diary_file):
        ctx = make_ctx()
        app_of(ctx).game.script["get_game_identity"] = ("CIVILIZATION_ROME", 7)
        _write_diary(diary_file, [LEGACY_T1, V2_AGENT_T2, LEGACY_T3])

        async def go():
            return await server.get_diary(ctx, last_n=1)

        text = run(go())
        assert "=== Turn 3 ===" in text
        assert "=== Turn 1 ===" not in text

    def test_turn_filter_selects_a_single_turn(self, diary_file):
        ctx = make_ctx()
        app_of(ctx).game.script["get_game_identity"] = ("CIVILIZATION_ROME", 7)
        _write_diary(diary_file, [LEGACY_T1, V2_AGENT_T2, LEGACY_T3])

        async def go():
            return await server.get_diary(ctx, turn=2)

        text = run(go())
        assert "=== Turn 2 ===" in text
        assert "=== Turn 1 ===" not in text

    def test_range_filter(self, diary_file):
        ctx = make_ctx()
        app_of(ctx).game.script["get_game_identity"] = ("CIVILIZATION_ROME", 7)
        _write_diary(diary_file, [LEGACY_T1, V2_AGENT_T2, LEGACY_T3])

        async def go():
            return await server.get_diary(ctx, from_turn=2, to_turn=2)

        text = run(go())
        assert "=== Turn 2 ===" in text
        assert "=== Turn 3 ===" not in text

    def test_no_match(self, diary_file):
        ctx = make_ctx()
        app_of(ctx).game.script["get_game_identity"] = ("CIVILIZATION_ROME", 7)
        _write_diary(diary_file, [LEGACY_T1])

        async def go():
            return await server.get_diary(ctx, turn=99)

        assert run(go()) == "No diary entries match the query."


# ---------------------------------------------------------------------------
# run_lua / saves / lifecycle
# ---------------------------------------------------------------------------


class TestRunLua:
    def test_code_and_context_are_forwarded(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.run_lua(ctx, "print(1)", "ingame")

        run(go())
        assert gs.last_args("execute_lua") == ("print(1)", "ingame")

    def test_default_context_is_gamecore(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.run_lua(ctx, "print(1)")

        run(go())
        assert gs.last_args("execute_lua") == ("print(1)", "gamecore")


class TestSaveTools:
    def test_load_save_by_index(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.load_save(ctx, 3)

        run(go())
        assert gs.last_args("load_save") == (3,)

    def test_load_game_save_by_name(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.load_game_save(ctx, "0_MCP_0079")

        run(go())
        assert gs.last_args("load_game_save") == ("0_MCP_0079",)

    def test_load_game_save_lua_error_is_mapped(self):
        ctx = make_ctx()
        app_of(ctx).game.script["load_game_save"] = LuaError("ERR:save not found")

        async def go():
            return await server.load_game_save(ctx, "nope")

        assert run(go()) == "Error: ERR:save not found"


class TestLifecycleTools:
    """These drive the game process, not FireTuner — game_launcher is patched."""

    def test_lifecycle_tools_forward_to_the_game_layer(self, monkeypatch):
        """Wiring smoke test: process-level tools reach game_launcher, and
        list_saves reaches the game state."""
        calls: list = []

        async def fake_kill():
            calls.append("kill")
            return "killed"

        async def fake_launch():
            calls.append("launch")
            return "launched"

        monkeypatch.setattr(server.game_launcher, "kill_game", fake_kill)
        monkeypatch.setattr(server.game_launcher, "launch_game", fake_launch)
        ctx = make_ctx()
        app_of(ctx).game.script["list_saves"] = "1. quicksave"

        async def go():
            return (
                await server.kill_game(ctx),
                await server.launch_game(ctx),
                await server.list_saves(ctx),
            )

        assert run(go()) == ("killed", "launched", "1. quicksave")
        assert calls == ["kill", "launch"]

    def test_load_save_from_menu_passes_the_name(self, monkeypatch):
        seen: list = []

        async def fake(name):
            seen.append(name)
            return "loading"

        monkeypatch.setattr(server.game_launcher, "load_save_from_menu", fake)

        async def go():
            return await server.load_save_from_menu(make_ctx(), "AutoSave_0001")

        assert run(go()) == "loading"
        assert seen == ["AutoSave_0001"]

    def test_restart_and_load_reconnects_and_returns(self, monkeypatch):
        ctx = make_ctx()
        app = app_of(ctx)

        async def fake(name):
            return f"restarted {name}"

        monkeypatch.setattr(server.game_launcher, "restart_and_load", fake)

        async def go():
            return await server.restart_and_load(ctx, "0_MCP_0050")

        assert run(go()) == "restarted 0_MCP_0050"
        # Identity was unknown before the restart → no wrong-game retry, and
        # the reconnect loop exits as soon as GameCore appears.
        assert app.game.conn.reconnect_calls == 1


# ---------------------------------------------------------------------------
# The _narrate helper
# ---------------------------------------------------------------------------


class TestNarrateHelper:
    def test_query_result_flows_into_the_narrator(self):
        async def go():
            async def query():
                return {"x": 1}

            return await server._narrate(query, lambda d: f"got {d['x']}")

        assert run(go()) == "got 1"
