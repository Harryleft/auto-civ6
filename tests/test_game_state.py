"""Unit tests for `civ_mcp.game_state.GameState`.

GameState is the high-level API layer: it wraps a GameConnection (the seam,
replaced here by a scripted `FakeConn`), drives Lua builders, and adds the
behaviours that make the tools trustworthy — position/combat follow-ups,
silent-failure verification (research, civic, production policies, governor
promotions, envoy tokens), game-identity cache invalidation, snapshot diffing,
turn reporting, and save-load bookkeeping for scumming detection.

The fake connection is scripted with (kind, lines) pairs consumed in call
order; "kind" ("read" = GameCore, "write" = InGame) is asserted on every call,
so tests also pin *which Lua state* each operation runs in. An entry whose
payload is an exception instance is raised instead of returned.
"""

from __future__ import annotations

import asyncio

import civ_mcp.game_lifecycle as game_lifecycle
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
        self.calls.append((kind, lua))
        assert self._script, f"unexpected {kind} call: {lua[:100]}"
        expected, payload = self._script.pop(0)
        assert kind == expected, f"expected {expected}, got {kind}: {lua[:100]}"
        if isinstance(payload, Exception):
            raise payload
        return payload

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
        assert gs._diary_written_turn is None
        assert gs._end_turn_blocked is False
        assert gs._pending_end_turn is False
        assert gs._pending_end_turn_from is None
        assert gs._high_water_turn == 0
        assert gs._local_player_id == 0
        assert gs._last_game_over is None
        assert gs._save_load_history == []
        assert gs._run_aborted is False
        assert gs._advisor_calls_this_turn == 0
        assert gs._advisor_budget_warning is None
        assert gs.spatial is None


# ---------------------------------------------------------------------------
# _action_result
# ---------------------------------------------------------------------------


class TestActionResult:
    """The OK:/ERR: scanner every action funnels through.

    LuaEvent callbacks can inject spurious output before the real result, so
    the contract is "first prefixed line wins", not "first line wins".
    """

    def test_empty_response_has_a_distinct_message(self):
        assert _action_result([]) == "Action completed (no response)."

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
        assert _action_result(["one", "two"]) == "one\ntwo"


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

    def test_format_reports_elimination_when_nothing_is_left(self):
        assert _format_attack_followup([]) == "Target eliminated"

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
    leak the previous game's snapshots, diary guards, or scumming history."""

    def test_parses_civ_and_seed(self):
        gs, conn = _gs([("write", ["GAMESEED|CIVILIZATION_ROME|12345"])])

        async def go():
            return await gs.get_game_identity()

        assert run(go()) == ("rome", 12345)
        assert conn.exhausted

    def test_same_identity_keeps_cached_state(self):
        gs, _ = _gs([("write", ["GAMESEED|CIVILIZATION_ROME|1"])])
        gs._game_identity = ("rome", 1)
        gs._last_snapshot = object()
        gs._diary_written_turn = 7
        snapshot_before = gs._last_snapshot

        async def go():
            return await gs.get_game_identity()

        assert run(go()) == ("rome", 1)
        assert gs._last_snapshot is snapshot_before
        assert gs._diary_written_turn == 7

    def test_changed_identity_resets_all_per_game_state(self):
        gs, _ = _gs([("write", ["GAMESEED|CIVILIZATION_EGYPT|2"])])
        gs._game_identity = ("rome", 1)
        gs._last_snapshot = object()
        gs._diary_written_turn = 9
        gs._last_game_over = object()
        gs._save_load_history = [(1.0, 10, "AutoSave_0010")]
        gs._run_aborted = True
        gs._advisor_calls_this_turn = 15
        gs._advisor_budget_warning = "warn"

        async def go():
            return await gs.get_game_identity()

        assert run(go()) == ("egypt", 2)
        assert gs._last_snapshot is None
        assert gs._diary_written_turn is None
        assert gs._last_game_over is None
        assert gs._save_load_history == []
        assert gs._run_aborted is False
        assert gs._advisor_calls_this_turn == 0
        assert gs._advisor_budget_warning is None

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

    def test_first_call_captures_a_snapshot(self):
        gs, conn = _gs(
            [
                ("write", [_OVERVIEW_LINE]),
                ("read", [_UNIT_LINE]),
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
                ("read", [_UNIT_LINE]),
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

    def test_snapshot_failure_is_swallowed(self):
        gs, _ = _gs(
            [
                ("write", [_OVERVIEW_LINE]),
                ("read", RuntimeError("units query blew up")),
            ]
        )

        async def go():
            return await gs.get_game_overview()

        ov = run(go())
        assert ov.turn == 12
        assert gs._last_snapshot is None


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


def _city(cid, pop=2, building="NONE", name="Capital"):
    return lq.CitySnapshot(
        city_id=cid, name=name, population=pop, currently_building=building
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
    """The end_turn event stream is only as good as this diff: deaths and
    city losses are critical (1), damage/finished builds/research important
    (2), heals/growth/new units informational (3), and the report is sorted
    by that priority."""

    diff = staticmethod(GameState._diff_snapshots)

    def test_killed_unit_is_critical(self):
        before = _snap(units={1: _unit(1, x=3, y=4)})
        events = self.diff(before, _snap())
        assert len(events) == 1
        assert events[0].priority == 1
        assert "was killed" in events[0].message
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

    def test_finished_building_reports_what_is_now_queued(self):
        before = _snap(cities={5: _city(5, building="BUILDING_MONUMENT")})
        after = _snap(cities={5: _city(5, building="NONE")})
        (event,) = self.diff(before, after)
        assert event.priority == 2
        assert "finished building BUILDING_MONUMENT" in event.message
        assert "Now: nothing." in event.message

    def test_corrupted_queue_is_called_out_as_invalidated(self):
        before = _snap(cities={5: _city(5, building="UNIT_TRADER")})
        after = _snap(cities={5: _city(5, building="CORRUPTED_QUEUE")})
        (event,) = self.diff(before, after)
        assert "queue invalidated" in event.message

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
                ("read", ["POS|12|20"]),
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
                ("read", ["POS|11|20"]),
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
                ("read", ["POS|10|20", "DIAG|MOUNTAIN|impassable mountain"]),
            ]
        )
        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.move_unit(1, 12, 20)

        result = run(go())
        assert "|BLOCKED (impassable mountain)" in result

    def test_gone_unit_leaves_the_result_unannotated(self):
        gs, _ = _gs(
            [
                ("write", ["OK:CAPTURE_MOVE|12,20|from:10,20"]),
                ("read", ["POS|GONE"]),
            ]
        )
        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.move_unit(1, 12, 20)

        assert run(go()) == "CAPTURE_MOVE|12,20|from:10,20"

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
                ("read", ["POS|12|20"]),
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


_ESTIMATE_LINE = "ESTIMATE|UNIT_ARCHER|UNIT_WARRIOR|25|20|1||100|80"
_RANGE_HIT = "OK:RANGE_ATTACK|target:Warrior at (12,20)|pre_hp:80/100|your HP:100|range:2 dist:2"


class TestAttackUnit:
    """Combat HP is not readable same-frame, so the contract is: follow-up
    read wins when it reflects a change, the pre-attack estimate is the
    fallback when the follow-up looks stale, and elimination is reported
    when no enemy remains on the tile."""

    def test_followup_with_lower_hp_reports_real_damage(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, conn = _gs(
            [
                ("write", [_ESTIMATE_LINE]),
                ("write", [_RANGE_HIT]),
                ("write", ["UNIT|UNIT_WARRIOR|50/100|owner:1"]),
            ]
        )

        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.attack_unit(1, 12, 20)

        result = run(go())
        assert "|damage dealt:30" in result
        assert "Post-combat: UNIT_WARRIOR 50/100" in result
        assert conn.exhausted

    def test_stale_followup_falls_back_to_the_estimate(self, monkeypatch):
        """The follow-up often reflects pre-combat HP (engine hasn't applied
        damage within the same frame); the estimate is then authoritative."""
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, _ = _gs(
            [
                ("write", [_ESTIMATE_LINE]),
                ("write", [_RANGE_HIT]),
                ("write", ["UNIT|UNIT_WARRIOR|80/100|owner:1"]),
            ]
        )

        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.attack_unit(1, 12, 20)

        result = run(go())
        # estimate: 24 * 10^((25-20)/30) ≈ 35 damage → ~45/80
        assert "|est damage dealt:~35" in result
        assert "~45/80 (estimate — verify with get_units)" in result

    def test_empty_followup_is_not_reported_as_a_kill(self, monkeypatch):
        """A follow-up read that returns nothing — indistinguishable from a
        silently failed read — must not be treated as target elimination.
        With no follow-up data, the report falls back to the estimate."""
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, _ = _gs(
            [
                ("write", [_ESTIMATE_LINE]),
                ("write", [_RANGE_HIT]),
                ("write", []),  # game said nothing — not proof of a kill
            ]
        )

        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.attack_unit(1, 12, 20)

        result = run(go())
        assert "(killed)" not in result
        assert "Target eliminated" not in result
        assert "|est damage dealt:~35" in result
        assert "~45/80 (estimate — verify with get_units)" in result

    def test_failed_estimate_does_not_block_the_attack(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, _ = _gs(
            [
                ("write", ConnectionError("estimate query died")),
                ("write", [_RANGE_HIT]),
                ("write", ["UNIT|UNIT_WARRIOR|50/100|owner:1"]),
            ]
        )

        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.attack_unit(1, 12, 20)

        assert "|damage dealt:30" in run(go())

    def test_melee_result_uses_enemy_hp_format(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, _ = _gs(
            [
                ("write", [_ESTIMATE_LINE]),
                ("write", ["OK:MELEE_ATTACK|target:Warrior at (12,20)|enemy HP:100 -> 80/100"]),
                ("write", ["UNIT|UNIT_WARRIOR|80/100|owner:1"]),
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
                ("write", ["UNIT|UNIT_WARRIOR|60/100|owner:1"]),
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
            ]
        )

        async def go():
            return await gs.city_attack(3, 12, 20)

        result = run(go())
        assert "(killed)" not in result
        assert "Target eliminated" not in result
        assert "Post-combat: no follow-up data — outcome unconfirmed" in result

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

    def test_fortify_translates_sleeping_into_an_explanation(self):
        gs, _ = _gs([("write", ["OK:SLEEPING"])])

        async def go():
            return await gs.fortify_unit(1)

        assert run(go()) == "Unit is sleeping (this unit type cannot fortify)"

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
    """Two-phase contract: fortify/heal what can fight (InGame), then skip
    whatever still has moves (GameCore). A failed fortify phase must not
    eat the skip phase."""

    def test_fortify_and_skip_results_are_combined(self):
        gs, _ = _gs(
            [
                ("write", ["OK:FORTIFIED|3 units fortified"]),
                ("read", ["OK:SKIPPED|2 units"]),
            ]
        )

        async def go():
            return await gs.skip_remaining_units()

        assert run(go()) == "FORTIFIED|3 units fortified\nSKIPPED|2 units"

    def test_fortify_error_is_dropped_from_the_output(self):
        gs, _ = _gs(
            [
                ("write", ["ERR:NO_UNITS|nothing to fortify"]),
                ("read", ["OK:SKIPPED|2 units"]),
            ]
        )

        async def go():
            return await gs.skip_remaining_units()

        assert run(go()) == "SKIPPED|2 units"

    def test_fortify_exception_still_runs_the_skip_phase(self):
        gs, conn = _gs(
            [
                ("write", ConnectionError("InGame dead")),
                ("read", ["OK:SKIPPED|1 unit"]),
            ]
        )

        async def go():
            return await gs.skip_remaining_units()

        assert run(go()) == "SKIPPED|1 unit"
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
                ("read", ["CITY_EXISTS|Rome"]),
            ]
        )
        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.found_city(1)

        assert run(go()) == "FOUNDED|10,20"
        assert conn.exhausted

    def test_unverified_founding_retries_once_after_popup_dismiss(self):
        gs, conn = _gs(
            [
                ("write", ["OK:FOUNDED|10,20"]),
                ("read", ["NO_CITY_HERE"]),
                ("write", ["OK:FOUNDED|10,20"]),  # retry
                ("read", ["CITY_EXISTS|Rome"]),
            ]
        )
        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.found_city(1)

        assert run(go()) == "FOUNDED|10,20"
        assert conn.exhausted

    def test_double_failure_becomes_found_failed_with_advisor(self):
        gs, conn = _gs(
            [
                ("write", ["OK:FOUNDED|10,20"]),
                ("read", ["NO_CITY_HERE"]),
                ("write", ["OK:FOUNDED|10,20"]),
                ("read", ["NO_CITY_HERE"]),
                ("read", ["SETTLE|11,21|42.5|4|2|river|1|"]),  # advisor
            ]
        )
        gs.dismiss_popup = _no_dismiss

        async def go():
            return await gs.found_city(1)

        result = run(go())
        assert result.startswith("Error: FOUND_FAILED|Founding at 10,20")
        assert "\n\n" in result  # advisor narration appended
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
# Silent-failure verification (research / civic / policies / governor / envoy)
# ---------------------------------------------------------------------------


class TestSetResearch:
    """RequestPlayerOperation can silently no-op while GetResearchingTech
    still returns the OLD tech. The index-comparison readback is the source
    of truth; mismatch falls back to the GameCore path."""

    def test_matching_index_returns_the_original_result(self):
        gs, conn = _gs(
            [
                ("write", ["OK:RESEARCHING|TECH_POTTERY"]),
                ("read", ["MATCH"]),
            ]
        )

        async def go():
            return await gs.set_research("TECH_POTTERY")

        assert run(go()) == "RESEARCHING|TECH_POTTERY"
        assert conn.exhausted

    def test_mismatched_index_falls_back_to_gamecore(self):
        gs, conn = _gs(
            [
                ("write", ["OK:RESEARCHING|TECH_POTTERY"]),
                ("read", ["MISMATCH:5~=3"]),
                ("read", ["OK:RESEARCHING_GAMECORE|TECH_POTTERY"]),
            ]
        )

        async def go():
            return await gs.set_research("TECH_POTTERY")

        assert run(go()) == "RESEARCHING_GAMECORE|TECH_POTTERY"
        assert conn.exhausted

    def test_silent_verify_is_treated_as_mismatch(self):
        """PINS CURRENT BEHAVIOUR: `verify and verify[0] == "MATCH"` makes an
        empty verify response (silent socket) fall through to the GameCore
        fallback — arguably right, but 'no answer' and 'wrong tech' are
        indistinguishable here."""
        gs, _ = _gs(
            [
                ("write", ["OK:RESEARCHING|TECH_POTTERY"]),
                ("read", []),  # game never answered the verify
                ("read", ["OK:RESEARCHING_GAMECORE|TECH_POTTERY"]),
            ]
        )

        async def go():
            return await gs.set_research("TECH_POTTERY")

        assert run(go()) == "RESEARCHING_GAMECORE|TECH_POTTERY"

    def test_non_researching_result_skips_verification(self):
        gs, conn = _gs([("write", ["ERR:TECH_NOT_FOUND|TECH_BOGUS"])])

        async def go():
            return await gs.set_research("TECH_BOGUS")

        assert run(go()) == "Error: TECH_NOT_FOUND|TECH_BOGUS"
        assert conn.exhausted


class TestSetCivic:
    """Same silent-no-op guard as research, for the culture track."""

    def test_matching_index_returns_original_result(self):
        gs, _ = _gs(
            [
                ("write", ["OK:PROGRESSING|CIVIC_CRAFTSMANSHIP"]),
                ("read", ["MATCH"]),
            ]
        )

        async def go():
            return await gs.set_civic("CIVIC_CRAFTSMANSHIP")

        assert run(go()) == "PROGRESSING|CIVIC_CRAFTSMANSHIP"

    def test_mismatch_falls_back_to_gamecore(self):
        gs, _ = _gs(
            [
                ("write", ["OK:PROGRESSING|CIVIC_CRAFTSMANSHIP"]),
                ("read", ["MISMATCH:2~=7"]),
                ("read", ["OK:PROGRESSING_GC|CIVIC_CRAFTSMANSHIP"]),
            ]
        )

        async def go():
            return await gs.set_civic("CIVIC_CRAFTSMANSHIP")

        assert run(go()) == "PROGRESSING_GC|CIVIC_CRAFTSMANSHIP"


class TestSetPolicies:
    """RequestPolicyChanges can silently no-op (e.g. mid era-transition), so
    every non-error result is re-read and per-slot mismatches are surfaced
    as a WARN instead of being reported as success."""

    _GOV_LINES = [
        "GOV|GOVERNMENT_CHIEFDOM|Chiefdom|x",
        "SLOT|0|SLOT_MILITARY|POLICY_DISCIPLINE|Discipline",
        "SLOT|1|SLOT_ECONOMIC|NONE|Empty",
    ]

    def test_matching_slots_return_a_clean_result(self):
        gs, _ = _gs(
            [
                ("write", ["OK:POLICIES_SET"]),
                ("write", self._GOV_LINES),
            ]
        )

        async def go():
            return await gs.set_policies({0: "POLICY_DISCIPLINE", 1: "NONE"})

        result = run(go())
        assert result == "POLICIES_SET"
        assert "WARN" not in result

    def test_mismatched_slot_appends_silent_failure_warning(self):
        lines = [
            "GOV|GOVERNMENT_CHIEFDOM|Chiefdom|x",
            "SLOT|0|SLOT_MILITARY|NONE|Empty",
        ]
        gs, _ = _gs([("write", ["OK:POLICIES_SET"]), ("write", lines)])

        async def go():
            return await gs.set_policies({0: "POLICY_DISCIPLINE"})

        result = run(go())
        assert "WARN:SILENT_FAILURE" in result
        assert "slot 0 (wanted POLICY_DISCIPLINE, got EMPTY)" in result

    def test_error_result_skips_the_verification_read(self):
        gs, conn = _gs([("write", ["ERR:CANNOT_SLOT|wrong slot type"])])

        async def go():
            return await gs.set_policies({0: "POLICY_DISCIPLINE"})

        assert run(go()) == "Error: CANNOT_SLOT|wrong slot type"
        assert conn.exhausted


class TestPromoteGovernor:
    """Governor promotions are verified by re-reading HasPromotion — an
    applied-looking PROMOTED line is not proof the promotion stuck."""

    def test_verified_promotion_returns_the_original_result(self):
        gs, conn = _gs(
            [
                ("write", ["OK:PROMOTED|Pingala with Librarian"]),
                ("write", ["VERIFIED"]),
            ]
        )

        async def go():
            return await gs.promote_governor(
                "GOVERNOR_THE_EDUCATOR", "GOVERNOR_EDUCATOR_LIBRARIAN"
            )

        assert run(go()) == "PROMOTED|Pingala with Librarian"
        assert conn.exhausted

    def test_failed_verification_replaces_the_result_with_the_error(self):
        gs, _ = _gs(
            [
                ("write", ["OK:PROMOTED|Pingala with Librarian"]),
                ("write", ["ERR:PROMOTION_FAILED|LIBRARIAN was not applied"]),
            ]
        )

        async def go():
            return await gs.promote_governor(
                "GOVERNOR_THE_EDUCATOR", "GOVERNOR_EDUCATOR_LIBRARIAN"
            )

        assert run(go()) == "Error: PROMOTION_FAILED|LIBRARIAN was not applied"

    def test_non_promoted_result_skips_verification(self):
        gs, conn = _gs([("write", ["ERR:NO_GOVERNOR|not appointed"])])

        async def go():
            return await gs.promote_governor("GOVERNOR_THE_EDUCATOR", "X")

        assert run(go()) == "Error: NO_GOVERNOR|not appointed"
        assert conn.exhausted


class TestSendEnvoy:
    """The envoy send is meant to race-proof the influence-token decrement
    by re-reading GetTokensToGive and annotating the result."""

    def test_successful_send_verifies_remaining_tokens(self, monkeypatch):
        """On ENVOY_SENT the token count is re-read and annotated, to
        race-proof the async influence-token decrement."""
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, conn = _gs(
            [
                ("write", ["OK:ENVOY_SENT|Kabul"]),
                ("write", ["2"]),  # GetTokensToGive verify readback
            ]
        )

        async def go():
            return await gs.send_envoy(3)

        assert run(go()) == "ENVOY_SENT|Kabul (verified remaining: 2)"
        assert conn.exhausted

    def test_error_result_passes_through(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, _ = _gs([("write", ["ERR:NO_TOKENS|no envoys available"])])

        async def go():
            return await gs.send_envoy(3)

        assert run(go()) == "Error: NO_TOKENS|no envoys available"


# ---------------------------------------------------------------------------
# Diplomacy actions
# ---------------------------------------------------------------------------


class TestProposePeace:
    """Peace is async on the engine side: the PROPOSED line is followed by a
    war-state readback that decides ACCEPTED vs REJECTED."""

    def test_at_peace_readback_reports_acceptance(self):
        gs, _ = _gs(
            [
                ("write", ["OK:PROPOSED|Gilgamesh"]),
                ("write", ["AT_PEACE|1"]),
            ]
        )

        async def go():
            return await gs.propose_peace(1)

        assert run(go()) == "ACCEPTED|Peace established with Gilgamesh"

    def test_still_at_war_reports_rejection(self):
        gs, _ = _gs(
            [
                ("write", ["OK:PROPOSED|Gilgamesh"]),
                ("write", ["AT_WAR|1"]),
            ]
        )

        async def go():
            return await gs.propose_peace(1)

        assert run(go()) == "REJECTED|Gilgamesh rejected your peace offer"

    def test_error_result_skips_the_war_state_readback(self):
        gs, conn = _gs([("write", ["ERR:COOLDOWN|peace on cooldown"])])

        async def go():
            return await gs.propose_peace(1)

        assert run(go()) == "Error: COOLDOWN|peace on cooldown"
        assert conn.exhausted


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
        gs, conn = _gs([("write", ["OK:SENT|delegation"])])

        async def go():
            return await gs.send_diplomatic_action(1, "DIPLOMATIC_DELEGATION")

        assert run(go()) == "SENT|delegation"
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
    """A session that closes on its own (or errors) short-circuits before
    the frame-delay state check — no extra round-trips."""

    def test_session_closed_returns_immediately(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
        gs, conn = _gs(
            [
                ("write", []),  # get_diplomacy_sessions (pre-text capture)
                ("write", ["OK:SESSION_CLOSED|goodbye"]),
            ]
        )

        async def go():
            return await gs.diplomacy_respond(1, "POSITIVE")

        assert run(go()) == "SESSION_CLOSED|goodbye"
        assert conn.exhausted


# ---------------------------------------------------------------------------
# District advisor (budget + error propagation)
# ---------------------------------------------------------------------------


class TestDistrictAdvisor:
    """Beyond the call budget (tested in test_advisor_budget.py), the
    contract here: Lua bail lines (ERR:*) reach the agent verbatim instead
    of being swallowed by the DPLOT-only parser, and the soft warning only
    attaches to successful calls so an error can't strand a stale warning."""

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

    def test_soft_warning_attaches_to_successful_calls(self):
        gs, _ = _gs([("write", ["DPLOT|12,20|2|1|0|0|0|3|freshwater"])])
        gs._advisor_calls_this_turn = 9  # this call is the 10th → soft zone

        async def go():
            return await gs.get_district_advisor(1, "DISTRICT_CAMPUS")

        run(go())
        assert gs._advisor_budget_warning is not None
        assert "10/20" in gs._advisor_budget_warning

    def test_error_path_does_not_attach_a_warning(self):
        gs, _ = _gs([("write", ["ERR:NO_CITY|gone"])])
        gs._advisor_calls_this_turn = 9

        async def go():
            return await gs.get_district_advisor(99, "DISTRICT_CAMPUS")

        run(go())
        assert gs._advisor_budget_warning is None

    def test_hard_cap_short_circuits_without_touching_the_game(self):
        gs, conn = _gs()  # no script: any conn call fails the test
        gs._advisor_calls_this_turn = 20

        async def go():
            return await gs.get_district_advisor(1, "DISTRICT_CAMPUS")

        result = run(go())
        assert result.startswith("ERR:ADVISOR_BUDGET_EXCEEDED")
        assert conn.calls == []


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

    def test_empty_response_has_a_fallback_message(self):
        gs, _ = _gs([("write", [])])

        async def go():
            return await gs.patronize_great_person(42)

        assert run(go()) == "Action completed (no response)."


# ---------------------------------------------------------------------------
# Save-load bookkeeping
# ---------------------------------------------------------------------------


class TestSaveLoadRecording:
    """Every successful load feeds the scumming detector (_save_load_history);
    failures must not. The history is bounded so long sessions don't grow it
    without limit."""

    def test_successful_load_by_index_is_recorded(self, monkeypatch):
        async def fake_load(conn, save_index):
            return "OK:LOADED"

        monkeypatch.setattr(game_lifecycle, "load_save", fake_load)
        gs, _ = _gs()

        async def go():
            return await gs.load_save(3)

        assert run(go()) == "OK:LOADED"
        assert [name for _, _, name in gs._save_load_history] == ["index:3"]

    def test_failed_load_is_not_recorded(self, monkeypatch):
        async def fake_load(conn, save_index):
            return "Error: INDEX_OUT_OF_RANGE|5"

        monkeypatch.setattr(game_lifecycle, "load_save", fake_load)
        gs, _ = _gs()

        async def go():
            return await gs.load_save(9)

        assert run(go()).startswith("Error:")
        assert gs._save_load_history == []

    def test_successful_load_by_name_is_recorded(self, monkeypatch):
        async def fake_load(conn, save_name):
            return "OK:LOADED"

        monkeypatch.setattr(game_lifecycle, "load_game_save", fake_load)
        gs, _ = _gs()

        async def go():
            return await gs.load_game_save("0_MCP_0079")

        assert run(go()) == "OK:LOADED"
        assert [name for _, _, name in gs._save_load_history] == ["0_MCP_0079"]

    def test_history_is_bounded_to_the_last_50_entries(self):
        gs, _ = _gs()
        gs._save_load_history = [(float(i), i, f"save_{i}") for i in range(50)]
        gs._record_save_load("save_new")
        assert len(gs._save_load_history) == 50
        assert gs._save_load_history[-1][2] == "save_new"
        assert gs._save_load_history[0][2] == "save_1"  # oldest dropped

    def test_recorded_turn_comes_from_the_high_water_mark(self):
        gs, _ = _gs()
        gs._high_water_turn = 123
        gs._record_save_load("AutoSave_0123")
        _, turn, _ = gs._save_load_history[-1]
        assert turn == 123
