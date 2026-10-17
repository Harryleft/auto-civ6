"""MCP tool dispatch and protocol tests with isolated game transport and logs."""

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
    def __init__(self, gamecore_index=0):
        self.gamecore_index = gamecore_index
        self.ingame_index = 1
        self.lua_states = {24: "MainMenu"}
        self.reconnect_calls = 0
        self.disconnect_calls = 0
        self.read_queries = []
        self.state_calls = []
        self.reconnect_states = []
        self.load_response = ["MCP_FRONTEND_AUTOSTART|true", "MCP_FRONTEND_LOAD|true"]

    @property
    def load_calls(self):
        return [(idx, code) for idx, code in self.state_calls if "Network.LoadGame" in code]

    async def execute_read(self, query, timeout=None):
        self.read_queries.append(query)
        if "TURN_STATE|" in query:
            return ["TURN_STATE|12|1"]
        return []

    async def execute_write(self, query, timeout=None):
        if "TURN_UI_READY|" in query:
            return ["TURN_UI_READY|12|1"]
        return []

    async def execute_in_state(self, index, code, timeout=None):
        self.state_calls.append((index, code))
        if "MCP_SAVE_QUERY_START" in code:
            return ["MCP_SAVE_QUERY_STARTED"]
        if "MCP_SAVE_QUERY_STATUS" in code:
            return ["MCP_SAVE_QUERY|" + getattr(self, "query_status", "READY")]
        if isinstance(self.load_response, Exception):
            raise self.load_response
        return self.load_response

    async def reconnect(self):
        self.reconnect_calls += 1
        if self.reconnect_states:
            self.gamecore_index = self.reconnect_states.pop(0)

    async def disconnect(self):
        self.disconnect_calls += 1


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
        self._revealed_tiles = set()
        self.reset_calls = 0
        self._pending_end_turn = False
        self._pending_end_turn_from = None
        self.calls: list[tuple[str, tuple, dict]] = []
        self.script: dict = {"check_game_over": None}

    def reset_session(self):
        self.reset_calls += 1
        self._revealed_tiles = None

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
    def __init__(self):
        self.tool_calls = []

    async def log_tool_call(self, tool, params, result, ms):
        self.tool_calls.append((tool, params, result, ms))


def make_ctx(**overrides) -> SimpleNamespace:
    """Build a Context stand-in: request_context.lifespan_context.<service>."""
    app = server.AppContext(game=FakeGameState(), logger=FakeLogger())
    for key, value in overrides.items():
        setattr(app, key, value)
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=app))


def app_of(ctx) -> SimpleNamespace:
    return ctx.request_context.lifespan_context


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
            "save_game",
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

    def test_attack_dispatches_with_coordinates(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.city_action(ctx, 5, "attack", 7, 8)

        run(go())
        assert gs.last_args("city_attack") == (5, 7, 8)

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
    def test_explicit_escape_route_dispatches_to_the_requested_spy(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        result = run(server.spy_action(ctx, 65536 + 4, "escape:district_harbor", 0, 0))
        assert result == "<spy_escape>"
        assert gs.last_args("spy_escape") == (4, "DISTRICT_HARBOR")
        assert gs.called("spy_mission") == []

    def test_travel_uses_the_unit_index(self):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def go():
            return await server.spy_action(ctx, 65536 + 4, "travel", 10, 11)

        run(go())
        assert gs.last_args("spy_travel") == (4, 10, 11)

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

    def test_tiles_are_narrated(self):
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
# Advisors (district / wonder) — placement results
# ---------------------------------------------------------------------------


class TestGetDistrictAdvisor:
    def test_a_string_result_becomes_an_error(self):
        """GameState signals placement failures by returning a plain string."""
        ctx = make_ctx()
        app_of(ctx).game.script["get_district_advisor"] = "city not found"

        async def go():
            return await server.get_district_advisor(ctx, 99, "DISTRICT_CAMPUS")

        assert run(go()) == "Error: city not found"


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


@pytest.fixture(autouse=True)
def isolated_server(monkeypatch, tmp_path):
    """Tools must not reach real process controls, sockets or save paths."""
    import socket
    import subprocess

    def external_call(*args, **kwargs):
        raise AssertionError("Test attempted an external process or socket call")

    real_socket = socket.socket

    def local_socket(family=socket.AF_INET, *args, **kwargs):
        if family == socket.AF_UNIX:
            return real_socket(family, *args, **kwargs)
        return external_call(family, *args, **kwargs)

    async def external_process():
        raise AssertionError("Test attempted a real game lifecycle action")

    monkeypatch.setattr(socket, "socket", local_socket)
    monkeypatch.setattr(subprocess, "run", external_call)
    monkeypatch.setattr(subprocess, "Popen", external_call)
    monkeypatch.setattr(server.game_launcher, "kill_game", external_process)
    monkeypatch.setattr(server.game_launcher, "launch_game", external_process)
    monkeypatch.setattr(server.game_launcher, "get_latest_autosave", lambda: None)
    monkeypatch.setattr(server.game_launcher, "SAVE_DIR", str(tmp_path / "auto"))
    monkeypatch.setattr(server.game_launcher, "SINGLE_SAVE_DIR", str(tmp_path))

    from civ_mcp import checkpoints
    from pathlib import Path
    monkeypatch.setattr(checkpoints, "CHECKPOINT_DIR", tmp_path / "records")
    assert checkpoints.begin_baseline_run() is None
    class SaveBoundary:
        async def execute_write(self, code, timeout=None):
            if "Network.SaveGame" in code:
                (tmp_path / "MySave.Civ6Save").write_bytes(b"known completed checkpoint")
                return ["MCP_SAVE|true"]
            return ["MCP_SAVE_COMPLETE|true"]
    assert run(server.game_lifecycle.save_game(SaveBoundary(), "MySave")).startswith("Saved:")
    assert checkpoints.record_checkpoint("MySave", ("rome", 4242), 12) is None


class TestLogged:
    @pytest.mark.parametrize("failure", [LuaError, ValueError, ConnectionError, TimeoutError])
    def test_errors_are_logged_without_replaying(self, failure):
        ctx = make_ctx()
        calls = []

        async def operation():
            calls.append(1)
            raise failure("failed")

        assert run(server._logged(ctx, "action", {}, operation)) == "Error: failed"
        assert calls == [1]
        assert app_of(ctx).game.conn.reconnect_calls == 0
        assert app_of(ctx).logger.tool_calls[0][:3] == ("action", {}, "Error: failed")

    def test_whole_tools_are_serialized_across_awaits(self):
        async def go():
            ctx = make_ctx()
            started = asyncio.Event()
            release = asyncio.Event()
            events = []

            async def first():
                events.append("check")
                started.set()
                await release.wait()
                events.append("verify")
                return "first"

            async def second():
                events.append("second")
                return "second"

            task = asyncio.create_task(server._logged(ctx, "first", {}, first))
            await started.wait()
            next_task = asyncio.create_task(server._logged(ctx, "second", {}, second))
            await asyncio.sleep(0)
            assert events == ["check"]
            release.set()
            assert await asyncio.gather(task, next_task) == ["first", "second"]
            return events

        assert run(go()) == ["check", "verify", "second"]

    def test_local_log_failure_does_not_replay_action(self):
        ctx = make_ctx()
        calls = []

        async def action():
            calls.append(1)
            return "confirmed"

        async def full_disk(*args):
            raise OSError("disk full")

        app_of(ctx).logger.log_tool_call = full_disk
        assert run(server._logged(ctx, "action", {}, action)) == "confirmed"
        assert calls == [1]


class TestPendingEndTurnDispatch:
    @pytest.mark.parametrize("tool,args", [
        ("set_research", ("TECH_MINING",)),
        ("promote_unit", (7, "PROMOTION_BATTLECRY")),
        ("unit_action", (7, "automate")),
    ])
    def test_unconfirmed_turn_blocks_all_gameplay_mutations(self, tool, args):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs._pending_end_turn = True
        result = run(getattr(server, tool)(ctx, *args))
        assert "OUTCOME_UNKNOWN" in result
        assert gs.calls == []

    @pytest.mark.parametrize("tool", ["get_units", "list_saves", "end_turn", "respond_to_diplomacy", "respond_to_trade", "queue_wc_votes", "dismiss_popup", "restart_and_load"])
    def test_unconfirmed_turn_still_allows_confirmation_and_blocker_resolution(self, tool):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs._pending_end_turn = True
        observations = []
        async def operation():
            observations.append(tool)
            return "observed"
        assert run(server._logged(ctx, tool, {}, operation)) == "observed"
        assert observations == [tool]


class TestGetGameOverview:
    def test_identity_is_checked_before_overview(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_game_identity"] = ("rome", 4242)
        gs.script["get_game_overview"] = make_overview()
        text = run(server.get_game_overview(ctx))
        assert "Turn 12" in text and "Rome" in text
        assert [call[0] for call in gs.calls] == [
            "get_game_identity", "get_game_overview", "check_game_over",
        ]

    def test_identity_failure_stops_the_query(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_game_identity"] = LuaError("identity unavailable")
        assert run(server.get_game_overview(ctx)) == "Error: identity unavailable"
        assert gs.called("get_game_overview") == []

    def test_revealed_baseline_is_seeded_only_once(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs._revealed_tiles = None
        gs.script["get_game_overview"] = make_overview()

        async def go():
            await server.get_game_overview(ctx)
            await server.get_game_overview(ctx)

        run(go())
        assert gs._revealed_tiles == set()
        assert len(gs.conn.read_queries) == 1

    @pytest.mark.parametrize("defeat, label", [(True, "DEFEAT"), (False, "VICTORY")])
    def test_game_outcome_is_visible(self, defeat, label):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["get_game_overview"] = make_overview()
        gs.script["check_game_over"] = make_gameover(defeat)
        assert f"GAME OVER: {label}" in run(server.get_game_overview(ctx))


class TestEndTurn:
    @pytest.mark.parametrize("result", [
        "Turn 5 -> 6", "BLOCKED: research", "ERR:OUTCOME_UNKNOWN|request pending",
    ])
    def test_result_is_returned_without_reflection_or_recovery(self, result):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["end_turn"] = result
        assert run(server.end_turn(ctx)) == result
        assert gs.called("end_turn") == [("end_turn", (), {})]
        assert gs.conn.reconnect_calls == 0


class TestSaveTools:
    def test_save_dispatch_and_log_preserve_confirmed_result(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.script["save_game"] = "Saved: checkpoint; file confirmed, turn 12."
        result = run(server.save_game(ctx, "checkpoint"))
        assert result == gs.script["save_game"]
        assert gs.last_args("save_game") == ("checkpoint",)
        logged = app_of(ctx).logger.tool_calls[0]
        assert logged[:3] == ("save_game", {"save_name": "checkpoint"}, result)

    def test_index_load_uses_the_same_confirmed_checkpoint_loader(self):
        ctx, gs = self.prepare_menu_load()
        server.game_lifecycle._filesystem_save_names[:] = ["MySave"]
        result = run(server.load_save(ctx, 1))
        assert result.startswith("Loaded MySave:")
        assert len(gs.conn.load_calls) == 1
        assert not gs.called("load_save")

    def test_public_native_fallback_listing_routes_displayed_index(self, monkeypatch, tmp_path):
        ctx, gs = self.prepare_menu_load()
        # The platform's native list can work when the filesystem scanner has
        # no files in its configured autosave-parent path.
        monkeypatch.setattr(server.game_launcher, "SAVE_DIR", str(tmp_path / "empty" / "auto"))
        original_write = gs.conn.execute_write
        async def enumerate_native(code, timeout=None):
            if "MCPSaveQueryDone = false" in code:
                return ["QUERY_SENT"]
            if "MCPSaveQueryDone then" in code:
                return ["COUNT|1", "SAVE|1|MySave"]
            return await original_write(code, timeout)
        gs.conn.execute_write = enumerate_native
        async def list_native():
            return await server.game_lifecycle.list_saves(gs.conn)
        gs.list_saves = list_native
        listing = run(server.list_saves(ctx))
        assert "1. MySave" in listing
        assert run(server.load_save(ctx, 1)).startswith("Loaded MySave:")
        assert len(gs.conn.load_calls) == 1

    def test_previous_run_checkpoint_is_rejected_before_restart_or_load(self):
        from civ_mcp import checkpoints
        ctx, gs = self.prepare_menu_load()
        assert checkpoints.begin_baseline_run() is None
        assert "no restart performed" in run(server.restart_and_load(ctx, "MySave"))
        assert "no load sent" in run(server.load_game_save(ctx, "MySave"))
        assert gs.conn.load_calls == []
        assert gs.conn.disconnect_calls == 0

    def test_verified_checkpoint_recovery_preserves_run_ownership(self):
        from civ_mcp import checkpoints
        ctx, gs = self.prepare_menu_load()
        assert run(server.load_game_save(ctx, "MySave")).startswith("Loaded MySave:")
        assert checkpoints.expected_checkpoint("MySave") == ("rome", 4242, 12)

    @pytest.mark.parametrize("matches", [True, False])
    def test_only_verified_baseline_load_rotates_current_run(self, monkeypatch, tmp_path, matches):
        from civ_mcp import checkpoints
        from pathlib import Path
        ctx, gs = self.prepare_menu_load()
        original = Path(__file__).resolve().parents[1] / "fixtures" / "saves" / "benchmark_turn_1.Civ6Save"
        (tmp_path / original.name).write_bytes(original.read_bytes())
        gs.script["get_game_identity"] = ("sumeria", 719665280) if matches else ("rome", 4242)
        gs.script["get_game_overview"] = make_overview(turn=1)
        original_read = gs.conn.execute_read
        original_write = gs.conn.execute_write
        async def turn_one_read(code, timeout=None):
            if "TURN_STATE|" in code:
                return ["TURN_STATE|1|1"]
            return await original_read(code, timeout)
        async def turn_one_write(code, timeout=None):
            if "TURN_UI_READY|" in code:
                return ["TURN_UI_READY|1|1"]
            return await original_write(code, timeout)
        gs.conn.execute_read = turn_one_read
        gs.conn.execute_write = turn_one_write
        result = run(server.load_game_save(ctx, "benchmark_turn_1"))
        assert result.startswith("Loaded" if matches else "ERR:OUTCOME_UNKNOWN")
        assert (checkpoints.expected_checkpoint("MySave") is None) == matches
        assert len(gs.conn.load_calls) == 1

    def test_invalid_native_listing_clears_previous_displayed_indices(self, monkeypatch, tmp_path):
        ctx, gs = self.prepare_menu_load()
        async def list_real():
            return await server.game_lifecycle.list_saves(gs.conn)
        gs.list_saves = list_real
        assert "1. MySave" in run(server.list_saves(ctx))
        monkeypatch.setattr(server.game_launcher, "SAVE_DIR", str(tmp_path / "empty" / "auto"))
        async def invalid_native(code, timeout=None):
            if "MCPSaveQueryDone then" in code:
                return ["COUNT|1", "SAVE|3|MySave"]
            return ["QUERY_SENT"]
        gs.conn.execute_write = invalid_native
        assert "No saves found" in run(server.list_saves(ctx))
        assert "No matching cached save index" in run(server.load_save(ctx, 1))
        assert gs.conn.load_calls == []

    def test_missing_index_does_not_submit_a_load(self):
        ctx = make_ctx()
        assert run(server.load_save(ctx, 999)).startswith("Error:")
        assert app_of(ctx).game.conn.load_calls == []

    def prepare_menu_load(self):
        ctx = make_ctx()
        gs = app_of(ctx).game
        gs.conn.gamecore_index = None
        gs.conn.reconnect_states = [None, 0]
        gs.script["get_game_identity"] = ("rome", 4242)
        gs.script["get_game_overview"] = make_overview()
        return ctx, gs

    @pytest.mark.parametrize("tool", ["load_game_save", "load_save_from_menu"])
    def test_named_loader_confirms_on_the_same_connection(self, tool):
        ctx, gs = self.prepare_menu_load()
        assert run(getattr(server, tool)(ctx, "MySave")) == "Loaded MySave: rome, seed 4242, turn 12."
        assert len(gs.conn.load_calls) == 1
        assert gs.conn.load_calls[0][0] == 24
        assert gs.reset_calls == 1

    def test_native_load_started_without_autostart_still_requires_confirmation(self):
        ctx, gs = self.prepare_menu_load()
        gs.conn.load_response = ["MCP_FRONTEND_AUTOSTART|false", "MCP_FRONTEND_LOAD|true"]
        result = run(server.load_game_save(ctx, "MySave"))
        assert result.startswith("Loaded MySave:")
        assert len(gs.conn.load_calls) == 1
        assert gs._pending_load is False

    def test_unknown_submission_is_confirmed_without_resending(self):
        ctx, gs = self.prepare_menu_load()
        gs.conn.load_response = ConnectionError("load closed socket")
        result = run(server.load_game_save(ctx, "MySave"))
        assert result.startswith("Loaded MySave:")
        assert len(gs.conn.load_calls) == 1

    @pytest.mark.parametrize("identity,turn", [(('egypt', 4242), 12), (('rome', 999), 12), (('rome', 4242), 11), (('rome', 4242), 0)])
    def test_load_must_match_checkpoint_identity_and_turn(self, identity, turn):
        ctx, gs = self.prepare_menu_load()
        gs.script["get_game_identity"] = identity
        gs.script["get_game_overview"] = make_overview(turn=turn)
        result = run(server.load_game_save(ctx, "MySave"))
        assert result.startswith("ERR:OUTCOME_UNKNOWN")
        assert len(gs.conn.load_calls) == 1
        assert gs._pending_load

    def test_loading_while_in_game_is_refused(self):
        ctx = make_ctx()
        result = run(server.load_game_save(ctx, "MySave"))
        assert result.startswith("Error: A game is loaded")
        assert app_of(ctx).game.conn.state_calls == []

    def test_unconfirmed_load_is_bounded_and_never_resent(self, monkeypatch):
        ctx, gs = self.prepare_menu_load()
        gs.conn.reconnect_states = []
        gs.conn.load_response = []
        monkeypatch.setattr(server, "LOAD_TIMEOUT", 0.01)
        result = run(server.load_game_save(ctx, "MySave"))
        assert result.startswith("ERR:OUTCOME_UNKNOWN")
        assert len(gs.conn.load_calls) == 1
        assert gs.conn.disconnect_calls == 1

    def test_invalid_name_is_rejected_before_reconnecting(self):
        ctx = make_ctx()
        assert run(server.load_game_save(ctx, "a/b")).startswith("Error:")
        assert app_of(ctx).game.conn.reconnect_calls == 0

    def test_startup_connection_reset_retries_discovery_not_loading(self, monkeypatch):
        ctx, gs = self.prepare_menu_load()
        reconnect = gs.conn.reconnect
        attempts = []

        async def startup():
            attempts.append(1)
            if len(attempts) == 1:
                raise ConnectionResetError("FireTuner startup reset")
            await reconnect()

        async def immediate_sleep(seconds):
            return None

        gs.conn.reconnect = startup
        monkeypatch.setattr(server.asyncio, "sleep", immediate_sleep)
        assert run(server.load_game_save(ctx, "MySave")).startswith("Loaded MySave:")
        assert len(attempts) == 3
        assert len(gs.conn.load_calls) == 1

    def test_missing_main_menu_times_out_without_sending_load(self, monkeypatch):
        ctx, gs = self.prepare_menu_load()
        gs.conn.lua_states = {}
        gs.conn.reconnect_states = []
        monkeypatch.setattr(server, "LOAD_TIMEOUT", 0.01)
        result = run(server.load_game_save(ctx, "MySave"))
        assert "no load command was sent" in result
        assert gs.conn.state_calls == []


class TestLifecycleTools:
    def test_kill_disconnects_and_resets_session(self, monkeypatch):
        ctx = make_ctx()
        gs = app_of(ctx).game

        async def kill():
            assert gs.conn.disconnect_calls == 1
            return "killed"

        monkeypatch.setattr(server.game_launcher, "kill_game", kill)
        assert run(server.kill_game(ctx)) == "killed"
        assert gs.reset_calls == 1

    def test_restart_uses_one_connection_and_native_load(self, monkeypatch):
        ctx, gs = TestSaveTools().prepare_menu_load()
        events = []

        async def kill():
            assert gs.conn.disconnect_calls == 1
            events.append("kill")
            return "killed"

        async def launch():
            assert gs.reset_calls == 1
            events.append("launch")
            return "launched"

        monkeypatch.setattr(server.game_launcher, "kill_game", kill)
        monkeypatch.setattr(server.game_launcher, "launch_game", launch)
        result = run(server.restart_and_load(ctx, "MySave"))
        assert result.startswith("Loaded MySave:")
        assert events == ["kill", "launch"]
        assert len(gs.conn.load_calls) == 1
        assert gs.reset_calls == 2

    def test_failed_launch_does_not_load(self, monkeypatch):
        ctx = make_ctx()

        async def kill():
            return "killed"

        async def launch():
            return "FAILED: process not detected"

        monkeypatch.setattr(server.game_launcher, "kill_game", kill)
        monkeypatch.setattr(server.game_launcher, "launch_game", launch)
        assert run(server.restart_and_load(ctx, "MySave")).startswith("Error:")
        assert app_of(ctx).game.conn.state_calls == []

    def test_failed_shutdown_does_not_launch_or_load(self, monkeypatch):
        ctx = make_ctx()

        async def kill():
            return "WARNING: Game process is still running."

        async def launch():
            raise AssertionError("Shutdown failure must not launch another process")

        monkeypatch.setattr(server.game_launcher, "kill_game", kill)
        monkeypatch.setattr(server.game_launcher, "launch_game", launch)
        assert run(server.restart_and_load(ctx, "MySave")).startswith("Error:")
        assert app_of(ctx).game.conn.state_calls == []


class TestMcpProtocol:
    def test_session_initializes_calls_game_tools_and_closes_lifespan(self, monkeypatch, tmp_path):
        """Use real MCP client/server messages over memory streams, with fake FireTuner.

        This exercises initialization, schemas, tool dispatch, GameState, action
        logging and lifespan cleanup without opening a game or HTTP listener.
        """
        import anyio
        from mcp import ClientSession
        from civ_mcp.logger import GameLogger

        conn = FakeConn()

        async def read(query, timeout=None):
            conn.read_queries.append(query)
            return ["42"]

        conn.execute_read = read
        log_path = tmp_path / "actions.jsonl"
        monkeypatch.setattr(server, "GameConnection", lambda: conn)
        monkeypatch.setattr(server, "GameLogger", lambda: GameLogger(log_path))

        async def go():
            client_write, server_read = anyio.create_memory_object_stream(10)
            server_write, client_read = anyio.create_memory_object_stream(10)
            lowlevel = server.mcp._mcp_server
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(
                    lowlevel.run, server_read, server_write,
                    lowlevel.create_initialization_options(),
                )
                async with ClientSession(client_read, client_write) as session:
                    initialization = await session.initialize()
                    assert initialization.serverInfo.name == "Civilization VI"
                    tools = (await session.list_tools()).tools
                    names = {tool.name for tool in tools}
                    assert len(names) == 76
                    assert "get_diary" not in names
                    save_schema = next(tool.inputSchema for tool in tools if tool.name == "save_game")
                    assert save_schema["required"] == ["save_name"]
                    rejected = await session.call_tool("save_game", {"save_name": "a/b"})
                    assert rejected.content[0].text.startswith("Error:")
                    schema = next(tool.inputSchema for tool in tools if tool.name == "end_turn")
                    assert schema.get("properties", {}) == {}
                    result = await session.call_tool("run_lua", {"code": "print(42)"})
                    assert not result.isError
                    assert result.content[0].text == "42"
                    invalid = await session.call_tool("unit_action", {"unit_id": 7, "action": "dance"})
                    assert "Error: Unknown action" in invalid.content[0].text
                tasks.cancel_scope.cancel()

        anyio.run(go)
        assert conn.disconnect_calls == 1
        assert conn.read_queries == ['(function() print(42) end)(); print("---END---")']
        entries = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert [entry["tool"] for entry in entries] == ["save_game", "run_lua", "unit_action"]
        assert all("success" not in entry for entry in entries)
