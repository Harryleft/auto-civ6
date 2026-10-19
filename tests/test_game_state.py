"""Unit tests for `civ_mcp.game_state.GameState`.

GameState is the high-level API layer: it wraps a GameConnection (the seam,
replaced here by a scripted `FakeConn`), drives Lua builders, and adds the
behaviours that make the tools trustworthy — position/combat follow-ups,
silent-failure verification (research, civic, production policies, governor
promotions, envoy tokens), game-identity cache invalidation, snapshot diffing,
turn reporting, and session reset after save loading.

The fake connection is scripted with (kind, lines) pairs consumed in call
order; "kind" ("read" = GameCore, "write" = InGame) is asserted on every call,
so tests also pin *which Lua state* each operation runs in. An entry whose
payload is an exception instance is raised instead of returned.
"""

from __future__ import annotations

import asyncio

import pytest

import civ_mcp.game_lifecycle as game_lifecycle
from action_scope_support import scope_rows, with_action_identity
from civ_mcp import lua as lq
from civ_mcp.game_state import (
    GameState,
    _action_result,
    _extract_city_defense,
    _extract_post_hp,
    _extract_pre_hp,
    _format_attack_followup,
)

run = asyncio.run


class FakeConn:
    """Scripted GameConnection stand-in.

    `script` entries are ("read"|"write", lines). The kind is asserted against
    the actual call (read = execute_read / GameCore, write = execute_write /
    InGame); a payload that is an exception instance is raised. Any call
    beyond the script raises AssertionError, so a test also proves no
    unexpected round-trip happened.
    """

    def __init__(self, script=()):
        self._script = list(script)
        self.calls: list[tuple[str, str]] = []

    async def execute_read(self, lua):
        return self._next("read", lua)

    async def execute_write(self, lua):
        return self._next("write", lua)

    def _next(self, kind, lua):
        if 'print("ACTION_SCOPE|' in lua:
            return scope_rows()
        self.calls.append((kind, lua))
        assert self._script, f"unexpected {kind} call: {lua[:100]}"
        expected, payload = self._script.pop(0)
        assert kind == expected, f"expected {expected}, got {kind}: {lua[:100]}"
        if isinstance(payload, Exception):
            raise payload
        return with_action_identity(lua, payload)

    @property
    def exhausted(self) -> bool:
        return not self._script


async def _instant_sleep(_seconds):
    """asyncio.sleep replacement: return immediately (does not yield)."""
    return None


def _gs(script=()) -> tuple[GameState, FakeConn]:
    conn = FakeConn(script)
    return GameState(conn), conn


async def _no_dismiss():
    return ""


# ---------------------------------------------------------------------------
# Construction defaults
# ---------------------------------------------------------------------------


class TestGameStateInit:
    """Fresh GameState carries no per-game or per-turn residue."""

    def test_default_fields(self):
        gs = GameState(FakeConn())
        assert gs._last_snapshot is None
        assert gs._game_identity is None
        assert gs._pending_end_turn is False
        assert gs._pending_end_turn_from is None
        assert gs._local_player_id == 0
        assert gs._revealed_tiles is None


# ---------------------------------------------------------------------------
# _action_result
# ---------------------------------------------------------------------------


class TestActionResult:
    """The OK:/ERR: scanner every action funnels through.

    LuaEvent callbacks can inject spurious output before the real result, so
    the contract is "first prefixed line wins", not "first line wins".
    """

    def test_empty_response_has_a_distinct_message(self):
        assert "OUTCOME_UNKNOWN" in _action_result([])

    def test_ok_prefix_is_stripped(self):
        assert _action_result(["OK:FORTIFIED|10,20"]) == "FORTIFIED|10,20"

    def test_err_prefix_becomes_error_string(self):
        assert _action_result(["ERR:NO_MOVES|unit"]) == "Error: NO_MOVES|unit"

    def test_scans_past_spurious_lines_to_the_first_prefixed_one(self):
        lines = ["BulkHide debug print", "OK:SKIPPED|2"]
        assert _action_result(lines) == "SKIPPED|2"

    def test_first_prefixed_line_wins_even_against_a_later_err(self):
        """Pins ordering semantics: an OK before an ERR is reported as success."""
        lines = ["OK:PRODUCING|X", "ERR:LATE_BAIL|ignored"]
        assert _action_result(lines) == "PRODUCING|X"

    def test_unprefixed_lines_are_joined_for_debugging(self):
        result = _action_result(["one", "two"])
        assert "OUTCOME_UNKNOWN" in result
        assert result.endswith("one\ntwo")


# ---------------------------------------------------------------------------
# Combat follow-up helpers
# ---------------------------------------------------------------------------


class TestAttackFollowupHelpers:
    """Pure helpers that turn the post-combat GameCore read into text.

    The load-bearing rule: units owned by the attacker must never be misread
    as the defender (after a melee kill the attacker stands on the target tile).
    """

    def test_format_labels_the_attackers_own_units(self):
        lines = [
            "UNIT|UNIT_WARRIOR|80/100|owner:0",
            "UNIT|UNIT_ARCHER|50/100|owner:1",
        ]
        out = _format_attack_followup(lines, attacker_owner=0)
        assert out == "(yours) UNIT_WARRIOR 80/100, UNIT_ARCHER 50/100"

    def test_format_tolerates_unit_lines_without_an_owner_field(self):
        assert _format_attack_followup(["UNIT|UNIT_WARRIOR|80/100"]) == (
            "UNIT_WARRIOR 80/100"
        )

    def test_format_includes_city_defenses(self):
        lines = ["CITY_DEF|wall:74/100|garrison:197/200"]
        out = _format_attack_followup(lines)
        assert "Walls 74/100" in out
        assert "City garrison 197/200" in out

    def test_empty_followup_leaves_elimination_unconfirmed(self):
        assert "elimination unconfirmed" in _format_attack_followup([])

    def test_extract_pre_hp_ranged_format(self):
        assert _extract_pre_hp("RANGE_ATTACK|t|pre_hp:80/100|your HP:100") == 80

    def test_extract_pre_hp_melee_format(self):
        assert _extract_pre_hp("MELEE_ATTACK|t|enemy HP:100 -> 80/100") == 100

    def test_extract_pre_hp_absent(self):
        assert _extract_pre_hp("MELEE_ATTACK|t|no hp info") is None

    def test_extract_post_hp_skips_the_attackers_units(self):
        lines = [
            "UNIT|UNIT_WARRIOR|90/100|owner:0",
            "UNIT|UNIT_ARCHER|45/100|owner:1",
        ]
        assert _extract_post_hp(lines, attacker_owner=0) == 45

    def test_extract_post_hp_returns_none_when_only_friendlies_remain(self):
        lines = ["UNIT|UNIT_WARRIOR|90/100|owner:0"]
        assert _extract_post_hp(lines, attacker_owner=0) is None

    def test_extract_city_defense_parses_wall_and_garrison(self):
        lines = ["CITY_DEF|wall:74/100|garrison:197/200"]
        assert _extract_city_defense(lines) == (74, 100, 197, 200)

    def test_extract_city_defense_absent(self):
        assert _extract_city_defense(["UNIT|X|1/1|owner:1"]) is None


# ---------------------------------------------------------------------------
# Game identity and cache invalidation
# ---------------------------------------------------------------------------


class TestGetGameIdentity:
    """get_game_identity re-queries every call and resets all per-game caches
    when the (civ, seed) pair changes — loading a different save must not
    leak the previous game's snapshots, pending requests or previous turn counters."""

    def test_parses_civ_and_seed(self):
        gs, conn = _gs([("write", ["GAMESEED|CIVILIZATION_ROME|12345"])])

        async def go():
            return await gs.get_game_identity()

        assert run(go()) == ("rome", 12345)
        assert conn.exhausted

    def test_same_identity_keeps_cached_state(self):
        gs, _ = _gs([("write", ["GAMESEED|CIVILIZATION_ROME|1"])])
        gs._game_identity = ("rome", 1)
        snapshot = object()
        gs._last_snapshot = snapshot
        gs._pending_end_turn = True
        gs._pending_end_turn_from = 7
        assert run(gs.get_game_identity()) == ("rome", 1)
        assert gs._last_snapshot is snapshot
        assert gs._pending_end_turn_from == 7

    @pytest.mark.parametrize("pending", [False, True])
    def test_changed_identity_clears_observations_without_retiring_pending_turn(self, pending):
        gs, _ = _gs([("write", ["GAMESEED|CIVILIZATION_EGYPT|2"])])
        gs._game_identity = ("rome", 1)
        gs._last_snapshot = object()
        gs._pending_end_turn = pending
        gs._pending_end_turn_from = 99 if pending else None
        gs._revealed_tiles = {(2, 3)}
        assert run(gs.get_game_identity()) == ("egypt", 2)
        assert gs._last_snapshot is None
        assert gs.turn_pending is pending
        assert gs._pending_end_turn_from == (99 if pending else None)
        assert gs._revealed_tiles is None

    def test_unparseable_response_returns_unknown_without_caching(self):
        """Pins current behaviour: a response with no GAMESEED line yields
        ("unknown", 0) and leaves _game_identity unset — so the next call
        re-queries instead of caching a bogus identity."""
        gs, _ = _gs([("write", ["garbage", "lines"])])

        async def go():
            return await gs.get_game_identity()

        assert run(go()) == ("unknown", 0)
        assert gs._game_identity is None


# ---------------------------------------------------------------------------
# check_game_over fallback
# ---------------------------------------------------------------------------


class TestCheckGameOver:
    """Victory/defeat screens can freeze the InGame Lua state, so the check
    falls back to a read-only GameCore probe (`build_gameover_check_gamecore`,
    exported from the lua package) when the InGame call fails."""

    def test_ingame_answer_short_circuits_the_fallback(self):
        gs, conn = _gs([("write", ["GAME_ACTIVE"])])

        async def go():
            return await gs.check_game_over()

        assert run(go()) is None
        assert [kind for kind, _ in conn.calls] == ["write"]

    def test_gamecore_fallback_detects_game_over_when_ingame_fails(self):
        """A frozen InGame state must not blind the check: the GameCore
        fallback issues a read and parses the GAME_OVER line it returns."""
        gs, conn = _gs(
            [
                ("write", ConnectionError("InGame frozen")),
                ("read", ["GAME_OVER|DEFEAT|Unknown|Unknown|dead|Unknown|-1"]),
            ]
        )

        async def go():
            return await gs.check_game_over()

        status = run(go())
        assert status is not None
        assert status.is_game_over
        assert status.is_defeat
        assert [kind for kind, _ in conn.calls] == ["write", "read"]

    def test_double_failure_returns_none(self):
        gs, _ = _gs(
            [
                ("write", ConnectionError("InGame frozen")),
                ("read", ConnectionError("GameCore dead too")),
            ]
        )

        async def go():
            return await gs.check_game_over()

        assert run(go()) is None


# ---------------------------------------------------------------------------
# get_game_overview snapshot bootstrap
# ---------------------------------------------------------------------------


_OVERVIEW_LINE = (
    "12|0|Rome|Trajan|100.0|5.0|3.0|2.0|10.0|Pottery|Code of Laws|1|1|45"
)
_UNIT_LINE = "65536|1|Warrior|UNIT_WARRIOR|10,20|2.0/2.0|100/100"
_CITY_LINE = "123|Capital|10,20|2|4.0|3.0|2.0|1.0|1.0|0.5|6.0|1|5|BUILDING_MONUMENT|3"
_STOCKPILE_LINE = "STOCKPILE|Iron|10|50|2|0|0"


class TestGetGameOverviewBootstrap:
    """The first overview call captures the baseline TurnSnapshot that the
    next end_turn diffs against. A failed bootstrap must never lose the
    overview itself."""

    def test_overview_then_units_and_cities_share_working_ingame_context(self):
        gs, conn = _gs([
            ("write", [_OVERVIEW_LINE]),
            ("write", [_UNIT_LINE]),
            ("write", [_CITY_LINE]),
            ("write", [_STOCKPILE_LINE]),
            ("write", [_UNIT_LINE]),
            ("write", [_CITY_LINE]),
        ])

        async def go():
            overview = await gs.get_game_overview()
            units = await gs.get_units()
            cities, _ = await gs.get_cities()
            return overview, units, cities

        overview, units, cities = run(go())
        assert overview.turn == 12
        assert [unit.unit_id for unit in units] == [65536]
        assert [city.city_id for city in cities] == [123]
        assert gs._last_snapshot is not None
        assert conn.exhausted

    def test_empty_overview_cannot_confirm_a_loaded_game(self):
        import pytest

        gs, _ = _gs([("write", [])])
        with pytest.raises(ValueError, match="Empty overview"):
            run(gs.get_game_overview())

    def test_first_call_captures_a_snapshot(self):
        gs, conn = _gs(
            [
                ("write", [_OVERVIEW_LINE]),
                ("write", [_UNIT_LINE]),
                ("write", [_CITY_LINE]),
                ("write", [_STOCKPILE_LINE]),
            ]
        )

        async def go():
            return await gs.get_game_overview()

        ov = run(go())
        assert ov.turn == 12
        snap = gs._last_snapshot
        assert snap is not None
        assert snap.turn == 12
        assert 65536 in snap.units
        assert 123 in snap.cities
        assert snap.cities[123].currently_building == "BUILDING_MONUMENT"
        assert [s.name for s in snap.stockpiles] == ["Iron"]
        assert conn.exhausted

    def test_second_call_does_not_re_snapshot(self):
        gs, conn = _gs(
            [
                ("write", [_OVERVIEW_LINE]),
                ("write", [_UNIT_LINE]),
                ("write", [_CITY_LINE]),
                ("write", [_STOCKPILE_LINE]),
                ("write", [_OVERVIEW_LINE]),
            ]
        )

        async def go():
            await gs.get_game_overview()
            return await gs.get_game_overview()

        run(go())
        assert gs._last_snapshot is not None
        # FakeConn raises on any unscripted call: reaching here proves the
        # second overview only paid for the single overview query.
        assert conn.exhausted

    def test_overview_sets_actual_local_player_for_combat_followup(self):
        gs, _ = _gs([("write", [_OVERVIEW_LINE.replace("12|0|", "12|3|")])])
        gs._last_snapshot = object()
        ov = run(gs.get_game_overview())
        assert ov.player_id == 3
        assert gs._local_player_id == 3

    def test_snapshot_failure_is_swallowed(self):
        gs, _ = _gs(
            [
                ("write", [_OVERVIEW_LINE]),
                ("write", RuntimeError("units query blew up")),
            ]
        )

        async def go():
            return await gs.get_game_overview()

        ov = run(go())
        assert ov.turn == 12
        assert gs._last_snapshot is None

    @pytest.mark.parametrize("failed_query", ["units", "cities", "stockpiles"])
    def test_required_snapshot_never_uses_cached_or_partial_load_evidence(self, failed_query):
        script = [("write", [_OVERVIEW_LINE])]
        for query, lines in [("units", [_UNIT_LINE]), ("cities", [_CITY_LINE]),
                             ("stockpiles", [_STOCKPILE_LINE])]:
            script.append(("write", ValueError(f"{query} unavailable") if query == failed_query else lines))
            if query == failed_query:
                break
        gs, conn = _gs(script)
        gs._last_snapshot = object()

        with pytest.raises(ValueError, match=f"{failed_query} unavailable"):
            run(gs.get_game_overview(require_snapshot=True))
        assert conn.exhausted


class TestReadOnlyCityFacts:
    def test_snapshot_preserves_observed_city_completion_facts(self):
        gs, _ = _gs([
            ("write", [_OVERVIEW_LINE]), ("write", [_UNIT_LINE]),
            ("write", [_CITY_LINE, "CITYBLDG|123|PALACE,MONUMENT",
                       "CITYFACTS|123|0|CIVILIZATION_ROME|42|DISTRICT_CAMPUS@11,20"]),
            ("write", []),
        ])
        run(gs.get_game_overview())
        city = gs._last_snapshot.cities[123]
        assert (city.x, city.y) == (10, 20)
        assert city.owner_id == 0
        assert city.game_identity == ("rome", 42)
        assert city.buildings == ["BUILDING_PALACE", "BUILDING_MONUMENT"]
        assert city.completed_districts == ["DISTRICT_CAMPUS@11,20"]

    def test_partial_city_facts_do_not_turn_into_completion_evidence(self):
        gs, _ = _gs([
            ("write", [_OVERVIEW_LINE]), ("write", [_UNIT_LINE]),
            ("write", [_CITY_LINE, "CITYFACTS|123|0|CIVILIZATION_ROME|nil|",
                       "CITYFACTS|123|0|CIVILIZATION_ROME"]),
            ("write", []),
        ])
        run(gs.get_game_overview())
        city = gs._last_snapshot.cities[123]
        assert city.game_identity is None
        assert city.buildings is None
        assert city.completed_districts is None

    def test_invalid_queue_is_reported_without_repair(self):
        line = _CITY_LINE.replace("BUILDING_MONUMENT", "CORRUPTED_QUEUE")
        gs, conn = _gs([("write", [line])])
        cities, _ = run(gs.get_cities())
        assert cities[0].currently_building == "CORRUPTED_QUEUE"
        # This existing boundary carries generated Lua to the real engine.
        # Reading must never submit a queue edit, even for a zero hash.
        assert not any("RemoveAt" in lua or "RequestOperation" in lua for _, lua in conn.calls)


# ---------------------------------------------------------------------------
# _diff_snapshots
# ---------------------------------------------------------------------------


def _unit(uid, hp=100, name="Warrior", x=1, y=2):
    return lq.UnitInfo(
        unit_id=uid,
        unit_index=uid % 65536,
        name=name,
        unit_type="UNIT_WARRIOR",
        x=x,
        y=y,
        moves_remaining=2.0,
        max_moves=2.0,
        health=hp,
        max_health=100,
    )


def _city(cid, pop=2, building="NONE", name="Capital", **facts):
    return lq.CitySnapshot(
        city_id=cid, name=name, population=pop, currently_building=building, **facts
    )


def _snap(turn=1, units=None, cities=None, research="None", civic="None", stock=None):
    return lq.TurnSnapshot(
        turn=turn,
        units=units or {},
        cities=cities or {},
        current_research=research,
        current_civic=civic,
        stockpiles=stock or [],
    )


def _stock(name="Iron", amount=0, per_turn=0, demand=0, imported=0):
    return lq.ResourceStockpile(
        name=name, amount=amount, cap=50, per_turn=per_turn,
        demand=demand, imported=imported,
    )


class TestDiffSnapshots:
    """The end_turn event stream is only as good as this diff: missing units and
    city losses are critical (1), damage/finished builds/research important
    (2), heals/growth/new units informational (3), and the report is sorted
    by that priority."""

    diff = staticmethod(GameState._diff_snapshots)

    def test_missing_unit_is_critical_without_claiming_death(self):
        before = _snap(units={1: _unit(1, x=3, y=4)})
        events = self.diff(before, _snap())
        assert len(events) == 1
        assert events[0].priority == 1
        assert "no longer appears" in events[0].message
        assert "cause unknown" in events[0].message
        assert "(3,4)" in events[0].message

    def test_damaged_and_healed_units(self):
        before = _snap(units={1: _unit(1, hp=100), 2: _unit(2, hp=50)})
        after = _snap(units={1: _unit(1, hp=70), 2: _unit(2, hp=80)})
        events = self.diff(before, after)
        by_priority = {e.priority: e.message for e in events}
        assert "took 30 damage" in by_priority[2]
        assert "healed 30 HP" in by_priority[3]

    def test_new_unit_is_informational(self):
        after = _snap(units={9: _unit(9, name="Settler")})
        events = self.diff(_snap(), after)
        assert events[0].priority == 3
        assert "New unit: Settler" in events[0].message

    def test_lost_city_is_critical(self):
        events = self.diff(_snap(cities={5: _city(5)}), _snap())
        assert events[0].priority == 1
        assert "City Capital was lost!" in events[0].message

    def test_city_growth_and_new_city(self):
        before = _snap(cities={5: _city(5, pop=2)})
        after = _snap(cities={5: _city(5, pop=4), 6: _city(6, name="Colony")})
        events = self.diff(before, after)
        messages = [e.message for e in events]
        assert any("grew to population 4" in m for m in messages)
        assert any("New city founded: Colony!" in m for m in messages)

    def test_cleared_queue_does_not_claim_completion_without_product(self):
        before = _snap(cities={5: _city(5, building="BUILDING_MONUMENT")})
        after = _snap(cities={5: _city(5, building="NONE")})
        (event,) = self.diff(before, after)
        assert event.priority == 2
        assert "completion unconfirmed" in event.message
        assert "finished building" not in event.message
        assert "Now: nothing." in event.message

    def test_new_building_reports_finished_while_old_building_does_not(self):
        facts = dict(
            x=10, y=20, owner_id=0, game_identity=("rome", 42), completed_districts=[]
        )
        after = _snap(cities={5: _city(5, buildings=["BUILDING_MONUMENT"], **facts)})
        for existing, expected in (([], True), (["BUILDING_MONUMENT"], False), (None, False)):
            before = _snap(cities={5: _city(
                5, building="BUILDING_MONUMENT", buildings=existing, **facts
            )})
            (event,) = self.diff(before, after)
            assert ("finished building" in event.message) is expected

    def test_unit_completion_requires_new_matching_unit_in_this_city(self):
        facts = dict(x=10, y=20, owner_id=0, game_identity=("rome", 42))
        old = _unit(1, x=10, y=20)
        before = _snap(
            units={1: old}, cities={5: _city(5, building="UNIT_WARRIOR", **facts)}
        )
        cases = (
            (_unit(2, x=10, y=20), True),
            (_unit(2, x=30, y=40), False),
            (old, False),
        )
        for unit, expected in cases:
            after = _snap(units={unit.unit_id: unit}, cities={5: _city(5, **facts)})
            events = self.diff(before, after)
            messages = [e.message for e in events if e.category == "city"]
            assert any("finished building UNIT_WARRIOR" in m for m in messages) is expected

    def test_native_district_completion_reports_finished(self):
        facts = dict(x=10, y=20, owner_id=0, game_identity=("rome", 42), buildings=[])
        before = _snap(cities={5: _city(
            5, building="DISTRICT_CAMPUS", completed_districts=[], **facts
        )})
        after = _snap(cities={5: _city(
            5, completed_districts=["DISTRICT_CAMPUS@11,20"], **facts
        )})
        (event,) = self.diff(before, after)
        assert "finished building DISTRICT_CAMPUS" in event.message

    def test_district_completion_rejects_old_products_and_mismatched_identity(self):
        from dataclasses import replace

        city = _city(5, building="DISTRICT_CAMPUS", x=10, y=20, owner_id=0,
                     game_identity=("rome", 42), completed_districts=[])
        completed = replace(
            city, currently_building="NONE", completed_districts=["DISTRICT_CAMPUS@11,20"]
        )
        cases = [
            (replace(city, completed_districts=["DISTRICT_CAMPUS@11,20"]), completed),
            (replace(city, completed_districts=None), completed),
            (city, replace(completed, completed_districts=[])),
            (city, replace(completed, completed_districts=["DISTRICT_HOLY_SITE@11,20"])),
            (city, replace(completed, game_identity=("rome", 43))),
            (city, replace(completed, game_identity=None)),
            (city, replace(completed, owner_id=1)),
            (city, replace(completed, owner_id=None)),
            (city, replace(completed, x=30)),
        ]
        for before, after in cases:
            (event,) = self.diff(_snap(cities={5: before}), _snap(cities={5: after}))
            assert "completion unconfirmed" in event.message
            assert "finished building" not in event.message

    def test_switch_cancel_and_unknown_queue_never_prove_completion(self):
        for queued in ("BUILDING_GRANARY", "NONE", "UNKNOWN", "CORRUPTED_QUEUE"):
            before = _snap(cities={5: _city(5, building="DISTRICT_CAMPUS")})
            after = _snap(cities={5: _city(5, building=queued)})
            assert all("finished building" not in e.message for e in self.diff(before, after))
        # Unknown/idle queue entries are never production projects.
        for old in ("NONE", "nothing", "UNKNOWN", "CORRUPTED_QUEUE"):
            assert self.diff(_snap(cities={5: _city(5, building=old)}),
                             _snap(cities={5: _city(5)})) == []

    def test_corrupted_queue_is_reported_as_anomaly_not_idle(self):
        before = _snap(cities={5: _city(5, building="UNIT_TRADER")})
        after = _snap(cities={5: _city(5, building="CORRUPTED_QUEUE")})
        (event,) = self.diff(before, after)
        assert "invalid queue" in event.message
        assert "nothing" not in event.message

    def test_research_and_civic_completion(self):
        before = _snap(research="Pottery", civic="Code of Laws")
        after = _snap(research="Mining", civic="Craftsmanship")
        events = self.diff(before, after)
        messages = [e.message for e in events]
        assert "Research complete: Pottery! Now: Mining." in messages
        assert "Civic complete: Code of Laws! Now: Craftsmanship." in messages

    def test_idle_research_slot_produces_no_event(self):
        """Pins the guard: 'None' -> anything is not a completion."""
        events = self.diff(_snap(research="None"), _snap(research="Pottery"))
        assert events == []

    def test_depleted_stockpile_warns_with_net_rate(self):
        before = _snap(stock=[_stock("Iron", amount=5, per_turn=1, demand=3)])
        after = _snap(stock=[_stock("Iron", amount=0, per_turn=1, demand=3)])
        (event,) = self.diff(before, after)
        assert event.priority == 2
        assert "DEPLETED: Iron" in event.message
        assert "-2/t" in event.message

    def test_stockpile_already_at_zero_is_not_a_depletion_event(self):
        before = _snap(stock=[_stock("Iron", amount=0)])
        after = _snap(stock=[_stock("Iron", amount=0)])
        assert self.diff(before, after) == []

    def test_events_are_sorted_by_priority(self):
        before = _snap(
            units={1: _unit(1)}, cities={5: _city(5)}, research="Pottery"
        )
        after = _snap(research="Mining")
        priorities = [e.priority for e in self.diff(before, after)]
        assert priorities == sorted(priorities)
        assert priorities[0] == 1  # deaths/losses surface first


# ---------------------------------------------------------------------------
# _build_turn_report
# ---------------------------------------------------------------------------


def _notif(message, turn, action=False, hint=None):
    return lq.GameNotification(
        type_name="NOTIFICATION_X",
        message=message,
        turn=turn,
        x=0,
        y=0,
        is_action_required=action,
        resolution_hint=hint,
    )


class TestBuildTurnReport:
    """The turn report is the agent's main event feed; the contract is a
    scannable header, priority-iconed events, action-required blockers with
    resolution hints, and a recency cutoff that drops stale info lines."""

    report = staticmethod(GameState._build_turn_report)

    def test_header_includes_score_when_given(self):
        out = self.report(3, 4, [], [], score=120)
        assert out.startswith("Turn 3 -> 4 | Score: 120")

    def test_header_omits_score_when_none(self):
        assert self.report(3, 4, [], []) == "Turn 3 -> 4"

    def test_only_visible_stockpiles_are_listed_with_net_rate(self):
        stock = [
            _stock("Iron", amount=10, per_turn=2, demand=0),
            _stock("Coal", amount=0, per_turn=0, demand=0),  # invisible
        ]
        out = self.report(1, 2, [], [], stockpiles=stock)
        assert "Resources: Iron 10/50 (+2/t)" in out
        assert "Coal" not in out

    def test_events_carry_priority_icons(self):
        events = [
            lq.TurnEvent(1, "unit", "critical thing"),
            lq.TurnEvent(2, "city", "important thing"),
            lq.TurnEvent(3, "unit", "info thing"),
        ]
        out = self.report(1, 2, events, [])
        assert "  !!! critical thing" in out
        assert "  >> important thing" in out
        assert "  -- info thing" in out

    def test_action_required_includes_resolution_hint(self):
        notifs = [_notif("Choose research", 2, action=True, hint="set_research")]
        out = self.report(1, 2, [], notifs)
        assert "== Action Required ==" in out
        assert "* Choose research  -> Use: set_research" in out

    def test_stale_info_notifications_are_dropped_but_blockers_survive(self):
        """turn_after - 2 is the cutoff: a 3-turn-old wonder notice is clutter;
        a 3-turn-old *blocker* still demands action regardless of age."""
        notifs = [
            _notif("Old wonder completed", turn=7),
            _notif("Recent event", turn=8),
            _notif("Ancient blocker", turn=1, action=True),
        ]
        out = self.report(9, 10, [], notifs)
        assert "Old wonder completed" not in out
        assert "Recent event" in out
        assert "Ancient blocker" in out


# ---------------------------------------------------------------------------
# move_unit position follow-up
# ---------------------------------------------------------------------------


class TestMoveUnit:
    """RequestOperation is async: the OK line only says the move was
    requested. The GameCore position readback is what tells the agent whether
    the unit actually moved, stopped mid-path, or never left — and why."""

    def test_successful_move_reports_delta_to_target(self):
        gs, conn = _gs(
            [
                ("write", ["OK:MOVING_TO|12,20|from:10,20"]),
                ("write", ["POS|12|20|busy:false"]),
            ]
        )
        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.move_unit(1, 12, 20)

        result = run(go())
        assert result == (
            "MOVING_TO|12,20|from:10,20|now_at:12,20|(moved dx:+2 dy:+0)"
        )
        assert conn.exhausted

    def test_mid_path_stop_is_flagged_as_moves_exhausted(self):
        gs, _ = _gs(
            [
                ("write", ["OK:MOVING_TO|12,20|from:10,20"]),
                ("write", ["POS|11|20|moves:0|busy:false"]),
            ]
        )
        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.move_unit(1, 12, 20)

        result = run(go())
        assert "|now_at:11,20|(moved dx:+1 dy:+0)" in result
        assert "|STOPPED_MID_PATH (moves exhausted)" in result

    def test_unit_that_never_moved_gets_a_blocked_diagnosis(self):
        gs, _ = _gs(
            [
                ("write", ["OK:MOVING_TO|12,20|from:10,20"]),
                ("write", ["POS|10|20|moves:2|busy:false", "DIAG|MOUNTAIN|impassable mountain"]),
            ]
        )
        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.move_unit(1, 12, 20)

        result = run(go())
        assert "|BLOCKED (impassable mountain)" in result

    def test_gone_unit_leaves_outcome_unknown(self):
        gs, _ = _gs(
            [
                ("write", ["OK:CAPTURE_MOVE|12,20|from:10,20"]),
                ("write", ["POS|GONE"]),
                ("write", ConnectionError("no further observation")),
            ]
        )
        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.move_unit(1, 12, 20)

        assert "OUTCOME_UNKNOWN" in run(go())

    def test_error_result_skips_the_position_readback(self):
        gs, conn = _gs([("write", ["ERR:NO_MOVES|unit has no moves"])])
        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.move_unit(1, 12, 20)

        assert run(go()) == "Error: NO_MOVES|unit has no moves"
        assert conn.exhausted

    def test_popup_dismiss_failure_does_not_block_the_move(self):
        gs, _ = _gs(
            [
                ("write", ["OK:MOVING_TO|12,20|from:10,20"]),
                ("write", ["POS|12|20|busy:false"]),
            ]
        )

        async def boom():
            raise ConnectionError("popup dismiss failed")

        gs.dismiss_popup = boom

        async def go():
            return await gs.move_unit(1, 12, 20)

        assert "MOVING_TO" in run(go())


# ---------------------------------------------------------------------------
# attack_unit combat follow-up
# ---------------------------------------------------------------------------


_ESTIMATE_LINE = "NATIVE_ESTIMATE|UNIT_ARCHER|UNIT_WARRIOR|25|20|1|100|80|0|35"
_RANGE_HIT = "OK:RANGE_ATTACK|target:Warrior at (12,20)|pre_hp:80/100|your HP:100|range:2 dist:2"


class TestAttackUnit:
    """Combat HP is not readable same-frame, so the contract is: follow-up
    read confirms actual changes; estimates never replace actual damage
    and absence from a tile is never proof of elimination."""

    def test_native_preview_preserves_engine_strength_and_damage_for_wounded_target(self):
        # Native preview already applied the attack/defense requirements. In this
        # melee attack the attacker has Barding, whose ranged-defense +7 does not apply.
        gs, conn = _gs([
            ("write", ["NATIVE_ESTIMATE|UNIT_HORSEMAN|UNIT_WARRIOR|36|20|0|100|40|8|39"]),
            ("write", ["OK:MELEE_ATTACK|pre_hp:40/100|target_owner:63|target_id:123|attacks_before:1"]),
            ("write", ["UNIT|UNIT_WARRIOR|11/100|owner:63|id:123", "CORE_BUSY|false"]),
        ])
        gs.dismiss_popup = _no_dismiss
        result = run(gs.attack_unit(1, 12, 20))
        assert "Native combat preview (Melee)" in result
        assert "UNIT_HORSEMAN (CS:36, HP:100)" in result
        assert "Est damage to defender: ~39" in result
        assert "Est damage to attacker: ~8" in result
        assert "damage dealt:29" in result
        assert "BARDING" not in result
        assert conn.exhausted

    def test_unverified_legacy_estimate_is_unknown_and_does_not_block_attack(self):
        gs, conn = _gs([
            ("write", ["ESTIMATE|UNIT_HORSEMAN|UNIT_WARRIOR|43|20|0|att BARDING +7|100|40"]),
            ("write", [_RANGE_HIT]),
            ("write", ["UNIT|UNIT_WARRIOR|50/100|owner:1", "CORE_BUSY|false"]),
        ])
        gs.dismiss_popup = _no_dismiss
        result = run(gs.attack_unit(1, 12, 20))
        assert "Combat estimate unknown" in result
        assert "conditional modifiers uncomputed" in result
        assert "CS:43" not in result and "BARDING +7" not in result
        assert "damage dealt:30" in result
        assert sum("RequestOperation" in code for _, code in conn.calls) == 1
        assert conn.exhausted

    def test_preview_uses_native_rules_for_exact_unit_without_gameplay_mutation(self):
        gs, conn = _gs([
            ("write", ["NATIVE_ESTIMATE|UNIT_HORSEMAN|UNIT_WARRIOR|36|20|0|100|40|8|39"]),
            ("write", ["OK:MELEE_ATTACK|pre_hp:40/100"]),
            ("write", ["UNIT|UNIT_WARRIOR|11/100|owner:63", "CORE_BUSY|false"]),
        ])
        gs.dismiss_popup = _no_dismiss
        result = run(gs.attack_unit(1, 12, 20))
        preview = conn.calls[0][1]
        # FakeConn cannot execute Civ6's native API. These source checks supplement
        # the public result contract and pin the documented native boundary.
        assert "GameInfo.UnitPromotionModifiers" not in preview
        assert "GameInfo.ModifierArguments" not in preview
        assert "RequestOperation" not in preview
        assert "CombatManager.SimulateAttackVersus(unit:GetComponentID(), enemy:GetComponentID(), combatType)" in preview
        assert "CombatManager.SimulateAttackInto(unit:GetComponentID(), combatType, 12, 20)" in preview
        assert "side[CombatResultParameters.COMBAT_STRENGTH] + side[CombatResultParameters.STRENGTH_MODIFIER]" in preview
        assert "attacker[CombatResultParameters.DAMAGE_TO]" in preview
        assert "defender[CombatResultParameters.DAMAGE_TO]" in preview
        assert "sameComponent(defender[CombatResultParameters.ID], defenderID)" in preview
        assert "Est damage to defender: ~39" in result
        assert "damage dealt:29" in result
        assert conn.exhausted

    @pytest.mark.parametrize("line", [
        "NATIVE_ESTIMATE|UNIT_ARCHER|UNIT_WARRIOR|25|20|invalid|100|80|0|30",
        "NATIVE_ESTIMATE|UNIT_ARCHER|UNIT_WARRIOR|25|20|1|100|80|0|-30",
        "NATIVE_ESTIMATE|UNIT_ARCHER|UNIT_WARRIOR|25|20|1|100|0|0|30",
        "NATIVE_ESTIMATE|UNIT_ARCHER|UNIT_WARRIOR|NaN|20|1|100|80|0|30",
        "NATIVE_ESTIMATE|UNIT_ARCHER|UNIT_WARRIOR|25|20|1|100|80|0",
        "ESTIMATE_UNKNOWN|core busy or state unavailable",
        "ESTIMATE_UNKNOWN|native preview target mismatch or malformed",
        "ESTIMATE_UNKNOWN|air preview mode unverified",
    ])
    def test_unreliable_native_preview_is_unknown_without_blocking_real_attack(self, line):
        gs, conn = _gs([
            ("write", [line]), ("write", [_RANGE_HIT]),
            ("write", ["UNIT|UNIT_WARRIOR|50/100|owner:1", "CORE_BUSY|false"]),
        ])
        gs.dismiss_popup = _no_dismiss
        result = run(gs.attack_unit(1, 12, 20))
        assert "Combat estimate unknown" in result
        assert "Est damage" not in result
        assert "damage dealt:30" in result
        assert conn.exhausted

    @pytest.mark.parametrize("preview_mode, operation", [("0", "RANGE_ATTACK"), ("1", "MELEE_ATTACK")])
    def test_mismatched_preview_mode_is_discarded_after_real_attack(self, preview_mode, operation):
        gs, conn = _gs([
            ("write", [f"NATIVE_ESTIMATE|UNIT_ARCHER|UNIT_WARRIOR|25|20|{preview_mode}|100|80|0|33"]),
            ("write", [f"OK:{operation}|pre_hp:80/100"]),
            ("write", ["UNIT|UNIT_WARRIOR|50/100|owner:63", "CORE_BUSY|false"]),
        ])
        gs.dismiss_popup = _no_dismiss
        result = run(gs.attack_unit(1, 12, 20))
        assert "Combat estimate unknown" in result
        assert "Est damage" not in result
        assert "damage dealt:30" in result
        assert conn.exhausted

    def test_followup_with_lower_hp_reports_real_damage(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, conn = _gs(
            [
                ("write", [_ESTIMATE_LINE]),
                ("write", [_RANGE_HIT]),
                ("write", ["UNIT|UNIT_WARRIOR|50/100|owner:1", "CORE_BUSY|false"]),
            ]
        )

        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.attack_unit(1, 12, 20)

        result = run(go())
        assert "|damage dealt:30" in result
        assert "Post-combat: UNIT_WARRIOR 50/100" in result
        assert conn.exhausted

    def test_stale_followup_remains_unknown(self, monkeypatch):
        """An old HP observation cannot confirm asynchronous combat."""
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, _ = _gs(
            [
                ("write", [_ESTIMATE_LINE]),
                ("write", [_RANGE_HIT]),
                ("write", ["UNIT|UNIT_WARRIOR|80/100|owner:1", "CORE_BUSY|false"]),
                ("write", ConnectionError("confirmation lost")),
            ]
        )

        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.attack_unit(1, 12, 20)

        result = run(go())
        # The native preview (35) still cannot replace an unchanged HP observation.
        assert "OUTCOME_UNKNOWN" in result
        assert "damage dealt:" not in result

    def test_empty_followup_is_not_reported_as_a_kill(self, monkeypatch):
        """A follow-up read that returns nothing — indistinguishable from a
        silently failed read — must not be treated as target elimination.
        With no follow-up data, the attack outcome stays unknown."""
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, _ = _gs(
            [
                ("write", [_ESTIMATE_LINE]),
                ("write", [_RANGE_HIT]),
                ("write", []),  # game said nothing — not proof of a kill
                ("write", ConnectionError("confirmation lost")),
            ]
        )

        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.attack_unit(1, 12, 20)

        result = run(go())
        assert "(killed)" not in result
        assert "Target eliminated" not in result
        assert "OUTCOME_UNKNOWN" in result
        assert "damage dealt:" not in result

    def test_failed_estimate_does_not_block_the_attack(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, _ = _gs(
            [
                ("write", ConnectionError("estimate query died")),
                ("write", [_RANGE_HIT]),
                ("write", ["UNIT|UNIT_WARRIOR|50/100|owner:1", "CORE_BUSY|false"]),
            ]
        )

        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.attack_unit(1, 12, 20)

        result = run(go())
        assert "Combat estimate unknown" in result
        assert "|damage dealt:30" in result

    def test_melee_result_uses_enemy_hp_format(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, _ = _gs(
            [
                ("write", [_ESTIMATE_LINE]),
                ("write", ["OK:MELEE_ATTACK|target:Warrior at (12,20)|enemy HP:100 -> 80/100"]),
                ("write", ["UNIT|UNIT_WARRIOR|80/100|owner:1", "CORE_BUSY|false"]),
            ]
        )

        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.attack_unit(1, 12, 20)

        assert "|damage dealt:20" in run(go())


class TestCityAttack:
    """City ranged attacks get the same follow-up treatment as unit attacks,
    minus the estimate."""

    def test_followup_damage_is_reported(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, conn = _gs(
            [
                ("write", ["OK:CITY_RANGE_ATTACK|Capital -> Warrior@12,20|pre_hp:80/100"]),
                ("write", ["UNIT|UNIT_WARRIOR|60/100|owner:1", "CORE_BUSY|false"]),
            ]
        )

        async def go():
            return await gs.city_attack(3, 12, 20)

        result = run(go())
        assert "|damage dealt:20" in result
        assert conn.exhausted

    def test_empty_followup_is_not_reported_as_a_kill(self, monkeypatch):
        """Same as attack_unit — silence from the follow-up read must not
        read as 'Target eliminated'; the outcome is reported as unconfirmed."""
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, _ = _gs(
            [
                ("write", ["OK:CITY_RANGE_ATTACK|Capital -> Warrior@12,20|pre_hp:80/100"]),
                ("write", []),
                ("write", ConnectionError("confirmation lost")),
            ]
        )

        async def go():
            return await gs.city_attack(3, 12, 20)

        result = run(go())
        assert "(killed)" not in result
        assert "Target eliminated" not in result
        assert "OUTCOME_UNKNOWN" in result

    def test_non_attack_result_skips_the_followup(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, conn = _gs([("write", ["ERR:NO_TARGET|nothing at tile"])])

        async def go():
            return await gs.city_attack(3, 12, 20)

        assert run(go()) == "Error: NO_TARGET|nothing at tile"
        assert conn.exhausted


# ---------------------------------------------------------------------------
# Simple unit actions
# ---------------------------------------------------------------------------


class TestSimpleUnitActions:
    """Thin action wrappers: the contract is which Lua state they run in and
    how the raw lines are reduced to a result string."""

    def test_fortify_passes_sleeping_status_through(self):
        gs, _ = _gs([("write", ["OK:SLEEPING"])])

        async def go():
            return await gs.fortify_unit(1)

        assert run(go()) == "SLEEPING"

    def test_fortify_passes_other_results_through(self):
        gs, _ = _gs([("write", ["OK:FORTIFIED|10,20"])])

        async def go():
            return await gs.fortify_unit(1)

        assert run(go()) == "FORTIFIED|10,20"

    def test_skip_unit_runs_in_ingame(self):
        """skip_unit is a state-changing action, so like its sibling unit
        actions it runs in the InGame context (execute_write)."""
        gs, conn = _gs([("write", ["OK:SKIPPED"])])

        async def go():
            return await gs.skip_unit(1)

        assert run(go()) == "SKIPPED"
        assert [kind for kind, _ in conn.calls] == ["write"]


class TestSkipRemainingUnits:
    """Batch candidate queries are read-only InGame calls; failures cannot
    trigger an alternative mutation or hide behind a successful second phase.
    Native per-unit behavior is exercised in test_unit_readiness.py.
    """

    def test_empty_ready_list_does_not_submit(self):
        gs, conn = _gs([("write", ["OK:READY_UNITS"])])
        assert run(gs.skip_remaining_units()) == "SKIPPED|0 candidates checked"
        assert conn.exhausted

    def test_candidate_query_error_is_preserved_without_fallback(self):
        gs, conn = _gs([("write", ["ERR:READINESS_UNKNOWN|native state unavailable"])])
        assert "READINESS_UNKNOWN" in run(gs.skip_remaining_units())
        assert conn.exhausted

    def test_candidate_query_disconnect_does_not_submit(self):
        gs, conn = _gs([("write", ConnectionError("InGame dead"))])
        assert "no units submitted" in run(gs.skip_remaining_units())
        assert conn.exhausted


# ---------------------------------------------------------------------------
# found_city verification
# ---------------------------------------------------------------------------


class TestFoundCity:
    """Settling is fire-and-forget: the FOUNDED line only means the request
    was accepted. The GameCore readback proves the city exists; a failed
    readback gets exactly one popup-dismiss retry, and every failure path
    appends the settle advisor so the agent has alternatives."""

    def test_founded_and_verified(self):
        gs, conn = _gs(
            [
                ("write", ["OK:FOUNDED|10,20"]),
                ("read", ["CITY_EXISTS|owner:0|city:65536"]),
            ]
        )
        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.found_city(1)

        assert run(go()) == "FOUNDED|10,20"
        assert conn.exhausted

    def test_unverified_founding_waits_without_resubmitting(self):
        gs, conn = _gs([
            ("write", ["OK:FOUNDED|10,20"]),
            ("read", ["NO_CITY_HERE"]),
            ("read", ["CITY_EXISTS|owner:0|city:65536"]),
        ])
        gs.dismiss_popup = _no_dismiss
        assert run(gs.found_city(1)) == "FOUNDED|10,20"
        assert sum("RequestOperation" in code for _, code in conn.calls) == 1
        assert conn.exhausted

    def test_missing_city_and_lost_observation_leaves_unknown(self):
        gs, conn = _gs([
            ("write", ["OK:FOUNDED|10,20"]),
            ("read", ["NO_CITY_HERE"]),
            ("read", ConnectionError("confirmation lost")),
        ])
        gs.dismiss_popup = _no_dismiss
        assert "OUTCOME_UNKNOWN" in run(gs.found_city(1))
        assert sum("RequestOperation" in code for _, code in conn.calls) == 1
        assert gs.pending_action_summary
        assert conn.exhausted

    def test_cannot_found_appends_settle_advisor(self):
        gs, _ = _gs(
            [
                ("write", ["ERR:CANNOT_FOUND|too close to another city"]),
                ("read", ["SETTLE|11,21|42.5|4|2|river|1|"]),
            ]
        )
        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.found_city(1)

        result = run(go())
        assert result.startswith("Error: CANNOT_FOUND|too close to another city")
        assert "\n\n" in result

    def test_advisor_failure_leaves_the_plain_error(self):
        gs, conn = _gs(
            [
                ("write", ["ERR:CANNOT_FOUND|blocked"]),
                ("read", ConnectionError("advisor died")),
            ]
        )
        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.found_city(1)

        assert run(go()) == "Error: CANNOT_FOUND|blocked"
        assert conn.exhausted


# ---------------------------------------------------------------------------
# Diplomacy actions
# ---------------------------------------------------------------------------


class TestProposePeace:
    """One proposal followed by bounded read-only confirmation of exact peace."""

    @staticmethod
    def _repeat_readback(monkeypatch, reply, *, hang=False):
        monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT", 0.01)
        monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL", 0.001)
        gs, conn = _gs([("write", ["OK:PROPOSED|Gilgamesh"])])
        original = conn.execute_write

        async def execute(lua):
            if not conn.calls:
                return await original(lua)
            conn.calls.append(("write", lua))
            if hang:
                await asyncio.Future()
            if isinstance(reply, Exception):
                raise reply
            return reply

        conn.execute_write = execute
        return gs, conn

    @staticmethod
    def _assert_one_proposal_then_queries(conn):
        assert conn.calls[0] == ("write", lq.build_propose_peace(1))
        assert all(call == ("write", lq.build_check_war_state(1)) for call in conn.calls[1:])

    def test_at_peace_readback_reports_acceptance(self):
        gs, conn = _gs([
            ("write", ["OK:PROPOSED|Gilgamesh"]),
            ("write", ["AT_PEACE"]),
        ])
        assert run(gs.propose_peace(1)) == "ACCEPTED|Peace established with Gilgamesh"
        assert conn.exhausted and len(conn.calls) == 2
        self._assert_one_proposal_then_queries(conn)

    @pytest.mark.parametrize("intermediate", [
        [["AT_WAR"], ["AT_WAR"]],
        [[], ["AT_PEACE|1"]],
        [["AT_PEACE", "AT_WAR"], ["AT_PEACE", "AT_PEACE"]],
    ])
    def test_delayed_peace_is_confirmed_without_sending_another_proposal(self, monkeypatch, intermediate):
        monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL", 0)
        gs, conn = _gs([
            ("write", ["OK:PROPOSED|Gilgamesh"]),
            *[("write", rows) for rows in intermediate],
            ("write", ["AT_PEACE"]),
        ])
        assert run(gs.propose_peace(1)) == "ACCEPTED|Peace established with Gilgamesh"
        assert conn.exhausted and len(conn.calls) == 4
        self._assert_one_proposal_then_queries(conn)

    @pytest.mark.parametrize("reply", [
        ["AT_WAR"], [], ["AT_PEACE|1"], ["NOT_AT_PEACE"], ["AT_PEACE_EXTRA"],
        ["AT_PEACE", "AT_WAR"], ["AT_PEACE", "AT_PEACE"], ["AT_PEACE", ""],
        None, {"AT_PEACE": True},
    ])
    def test_unconfirmed_or_malformed_readback_is_not_rejection_or_acceptance(self, monkeypatch, reply):
        gs, conn = self._repeat_readback(monkeypatch, reply)

        async def go():
            return await asyncio.wait_for(gs.propose_peace(1), 0.5)

        result = run(go())
        assert result.startswith("PROPOSED_UNCONFIRMED|"), result
        assert "ACCEPTED" not in result and "REJECTED" not in result
        assert "get_diplomacy" in result and "do not automatically resend" in result
        assert len(conn.calls) >= 2
        self._assert_one_proposal_then_queries(conn)

    @pytest.mark.parametrize("exception", [OSError("read lost"), TimeoutError("read timeout"), ValueError("bad reply")])
    def test_read_exception_keeps_the_proposal_unconfirmed(self, monkeypatch, exception):
        gs, conn = self._repeat_readback(monkeypatch, exception)
        result = run(gs.propose_peace(1))
        assert result.startswith("PROPOSED_UNCONFIRMED|")
        assert "get_diplomacy" in result and "REJECTED" not in result
        assert len(conn.calls) == 2
        self._assert_one_proposal_then_queries(conn)

    def test_native_read_failure_keeps_the_proposal_unconfirmed(self, monkeypatch):
        from civ_mcp.connection import LuaError

        gs, conn = self._repeat_readback(monkeypatch, LuaError("native read failed"))
        assert run(gs.propose_peace(1)).startswith("PROPOSED_UNCONFIRMED|")
        assert len(conn.calls) == 2
        self._assert_one_proposal_then_queries(conn)

    def test_confirmation_deadline_includes_a_read_that_never_completes(self, monkeypatch):
        gs, conn = self._repeat_readback(monkeypatch, [], hang=True)

        async def go():
            return await asyncio.wait_for(gs.propose_peace(1), 0.5)

        assert run(go()).startswith("PROPOSED_UNCONFIRMED|")
        assert len(conn.calls) == 2
        self._assert_one_proposal_then_queries(conn)

    @pytest.mark.parametrize(("native_error", "expected"), [
        ("ERR:COOLDOWN|peace on cooldown", "Error: COOLDOWN|peace on cooldown"),
        ("ERR:NOT_AT_WAR|Not at war", "Error: NOT_AT_WAR|Not at war"),
        ("ERR:CANNOT_MAKE_PEACE|war cooldown", "Error: CANNOT_MAKE_PEACE|war cooldown"),
    ])
    def test_error_result_skips_the_war_state_readback(self, native_error, expected):
        gs, conn = _gs([("write", [native_error])])
        assert run(gs.propose_peace(1)) == expected
        assert conn.exhausted and len(conn.calls) == 1


class TestSendDiplomaticAction:
    """OPEN_BORDERS is deliberately rerouted through the trade-deal API
    (session-based borders hang the AI turn); war declarations schedule a
    background UI cleanup."""

    def test_open_borders_routes_through_propose_trade(self):
        gs, _ = _gs()
        seen = {}

        async def fake_trade(other, offer_items, request_items):
            seen["other"] = other
            seen["offer"] = offer_items
            seen["request"] = request_items
            return "TRADE_SENT"

        gs.propose_trade = fake_trade

        async def go():
            return await gs.send_diplomatic_action(2, "OPEN_BORDERS")

        assert run(go()) == "TRADE_SENT"
        assert seen["other"] == 2
        agreement = {"type": "AGREEMENT", "subtype": "OPEN_BORDERS"}
        assert seen["offer"] == [agreement]
        assert seen["request"] == [agreement]

    def test_non_war_action_passes_the_result_through(self):
        gs, conn = _gs([("write", ["OK:SENT|Denounced Sweden"])])

        async def go():
            return await gs.send_diplomatic_action(1, "DENOUNCE")

        assert run(go()) == "SENT|Denounced Sweden"
        assert conn.exhausted

    def test_war_declaration_schedules_background_cleanup(self):
        async def go():
            gs, _ = _gs([("write", ["OK:WAR_DECLARED|Gilgamesh"])])
            done = asyncio.Event()
            cleaned = []

            async def fake_cleanup(other_player_id):
                cleaned.append(other_player_id)
                done.set()

            gs._cleanup_war_diplomacy = fake_cleanup
            result = await gs.send_diplomatic_action(1, "DECLARE_WAR")
            await asyncio.wait_for(done.wait(), 1.0)
            return result, cleaned

        result, cleaned = run(go())
        assert result == "WAR_DECLARED|Gilgamesh"
        assert cleaned == [1]

    def test_failed_war_declaration_does_not_schedule_cleanup(self):
        """The guard matches the post-_action_result form ("Error: "): a
        refused declaration must not queue the 8-second background UI
        cleanup — there is no war session to clean up."""

        async def go():
            gs, _ = _gs([("write", ["ERR:NOT_MET|no delegation"])])
            cleaned = []

            async def fake_cleanup(other_player_id):
                cleaned.append(other_player_id)

            gs._cleanup_war_diplomacy = fake_cleanup
            result = await gs.send_diplomatic_action(1, "DECLARE_WAR")
            await asyncio.sleep(0)  # let any (wrongly) scheduled task run
            return result, cleaned

        result, cleaned = run(go())
        assert result == "Error: NOT_MET|no delegation"
        assert cleaned == []


class TestDiplomacyRespond:
    def test_absent_session_does_not_submit_generic_response(self):
        gs, conn = _gs([("write", ["CONTEXT|NONE|NO_OPEN_SESSION", "CONTEXT_COMPLETE"])])
        result = run(gs.diplomacy_respond(1, "POSITIVE"))
        assert "OUTCOME_UNKNOWN" in result
        assert conn.exhausted


# ---------------------------------------------------------------------------
# District advisor (placement facts + error propagation)
# ---------------------------------------------------------------------------


class TestDistrictAdvisor:
    """Placement facts and Lua errors remain visible to the player."""

    def test_err_lines_are_propagated_verbatim(self):
        gs, conn = _gs([("write", ["ERR:NO_CITY|city 99 not found"])])

        async def go():
            return await gs.get_district_advisor(99, "DISTRICT_CAMPUS")

        assert run(go()) == "ERR:NO_CITY|city 99 not found"
        assert conn.exhausted

    def test_success_returns_placements(self):
        gs, _ = _gs([("write", ["DPLOT|12,20|2|1|0|0|0|3|freshwater"])])

        async def go():
            return await gs.get_district_advisor(1, "DISTRICT_CAMPUS")

        result = run(go())
        assert isinstance(result, list)
        assert result[0].x == 12 and result[0].y == 20
        assert result[0].adjacency == {"science": 2, "production": 1}





# ---------------------------------------------------------------------------
# Great Person raw passthrough
# ---------------------------------------------------------------------------


class TestGreatPersonResults:
    """recruit/patronize/reject go through _action_result like every other
    action: OK:/ERR: prefixes are normalised, not passed through raw."""

    def test_recruit_strips_the_ok_prefix(self):
        gs, _ = _gs([("write", ["OK:RECRUITED|Einstein", "trailing noise"])])

        async def go():
            return await gs.recruit_great_person(42)

        assert run(go()) == "RECRUITED|Einstein"

    def test_reject_rewrites_the_err_prefix(self):
        gs, _ = _gs([("write", ["ERR:NOT_ENOUGH_FAITH|need 100"])])

        async def go():
            return await gs.reject_great_person(42)

        assert run(go()) == "Error: NOT_ENOUGH_FAITH|need 100"

    def test_empty_response_reports_an_unknown_outcome(self):
        gs, _ = _gs([("write", [])])

        async def go():
            return await gs.patronize_great_person(42)

        assert "OUTCOME_UNKNOWN" in run(go())


# ---------------------------------------------------------------------------
# Save-load bookkeeping
# ---------------------------------------------------------------------------




class TestSaveCheckpoint:
    def prepare(self, monkeypatch, tmp_path, *, turns=(12, 12), identities=None):
        from types import SimpleNamespace
        from civ_mcp import checkpoints, game_launcher
        monkeypatch.setattr(game_launcher, "SINGLE_SAVE_DIR", str(tmp_path))
        monkeypatch.setattr(checkpoints, "CHECKPOINT_DIR", tmp_path / "metadata")
        assert checkpoints.begin_baseline_run() is None
        gs, conn = _gs([("write", ["GAME_ACTIVE"])])
        snapshots = iter(turns)
        game_ids = iter(identities or [("babylon", 42), ("babylon", 42)])
        saves = []
        async def identity():
            value = next(game_ids)
            if isinstance(value, Exception):
                raise value
            return value
        async def overview():
            return SimpleNamespace(turn=next(snapshots), score=0, score_categories=None)
        async def save(connection, name):
            assert connection is conn
            saves.append(name)
            (tmp_path / (name + ".Civ6Save")).write_bytes(b"completed-save")
            checkpoints.mark_complete(name)
            return game_lifecycle.NativeTransition("confirmed", "Saved: checkpoint; file confirmed (100 bytes).")
        monkeypatch.setattr(gs, "get_game_identity", identity)
        monkeypatch.setattr(gs, "get_game_overview", overview)
        monkeypatch.setattr(game_lifecycle, "save_game_result", save)
        return gs, conn, saves

    def test_confirmed_checkpoint_includes_identity_and_turn(self, monkeypatch, tmp_path):
        gs, conn, saves = self.prepare(monkeypatch, tmp_path)
        result = run(gs.save_game("checkpoint"))
        assert result.endswith("Civilization babylon, seed 42, turn 12.")
        assert saves == ["checkpoint"]
        assert conn.exhausted

    def test_pending_end_turn_cannot_create_recovery_checkpoint(self, monkeypatch, tmp_path):
        gs, _, saves = self.prepare(monkeypatch, tmp_path)
        gs._pending_end_turn = True
        assert "pending" in run(gs.save_game("checkpoint"))
        assert saves == []
        assert gs._pending_end_turn

    def test_changed_turn_is_not_a_confirmed_checkpoint(self, monkeypatch, tmp_path):
        gs, _, saves = self.prepare(monkeypatch, tmp_path, turns=(12, 13))
        assert run(gs.save_game("checkpoint")).startswith("ERR:OUTCOME_UNKNOWN")
        assert saves == ["checkpoint"]

    def test_changed_world_is_not_a_confirmed_checkpoint(self, monkeypatch, tmp_path):
        gs, _, _ = self.prepare(monkeypatch, tmp_path, identities=[("babylon", 42), ("rome", 42)])
        assert run(gs.save_game("checkpoint")).startswith("ERR:OUTCOME_UNKNOWN")

    def test_lost_verification_does_not_replay_or_hide_the_saved_file(self, monkeypatch, tmp_path):
        gs, _, saves = self.prepare(monkeypatch, tmp_path, identities=[("babylon", 42), ConnectionError("lost")])
        result = run(gs.save_game("checkpoint"))
        assert result.startswith("ERR:OUTCOME_UNKNOWN|Saved: checkpoint;")
        assert "verification failed" in result
        assert saves == ["checkpoint"]

    def test_unknown_identity_refuses_save(self, monkeypatch, tmp_path):
        gs, _, saves = self.prepare(monkeypatch, tmp_path, identities=[("unknown", 0)])
        assert run(gs.save_game("checkpoint")).startswith("Error:")
        assert saves == []

    def test_invalid_name_refuses_save_before_queries(self):
        gs, conn = _gs()
        assert run(gs.save_game("a/b")).startswith("Error:")
        assert conn.calls == []


class TestSessionReset:
    def test_unknown_diplomacy_status_is_not_an_empty_session(self):
        gs, _ = _gs([("write", [])])
        from civ_mcp.connection import LuaError
        import pytest
        with pytest.raises(LuaError, match="unknown"):
            run(gs.get_diplomacy_sessions())

    def test_empty_pending_deals_requires_completion_marker(self):
        gs, _ = _gs([("write", ["CONTEXT|NONE|NO_OPEN_SESSION", "CONTEXT_COMPLETE"])])
        assert run(gs.get_pending_deals()).status == "NONE"
