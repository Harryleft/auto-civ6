"""Unit tests for `civ_mcp.end_turn` — the turn-advancement state machine.

Everything network-shaped is driven through `ScriptedConn`, a GameConnection-
style fake that routes each Lua snippet to a scripted response by matching a
marker substring in the code (the same way the real code identifies its own
queries). `FakeGameState` stubs the GameState surface that end_turn touches
(diplomacy, deals, world congress, snapshots, report building) so the real
blocker-resolution / polling / event-detection logic runs end to end.

`_check_save_scumming` is covered by tests/test_save_scumming.py and is only
exercised here where it integrates with execute_end_turn (run abort).
"""

from __future__ import annotations

import asyncio
import types

import pytest

from civ_mcp import lua as lq
from civ_mcp.autosave import get_autosave_for_turn
from civ_mcp.connection import LuaError
from civ_mcp.end_turn import (
    _check_empire_warnings,
    _check_mid_turn_diplomacy,
    _check_victory_proximity,
    _get_turn_number,
    execute_end_turn,
)

run = asyncio.run


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class Queue:
    """Callable response that pops scripted replies, then falls back."""

    def __init__(self, *responses, fallback=None):
        self._responses = [list(r) for r in responses]
        self._fallback = list(fallback) if fallback is not None else []

    def __call__(self, code, conn):
        if self._responses:
            return self._responses.pop(0)
        return list(self._fallback)


class ScriptedConn:
    """GameConnection-style fake routing Lua code to responses by content.

    Rules are (marker, response) pairs; the first rule whose marker is a
    substring of the Lua code wins. `add` prepends, so rules added later
    override earlier ones. A response is a list of output lines or a
    callable(code, conn) -> list[str] (which may mutate conn state).
    """

    def __init__(self):
        self.turn = 50
        self.calls: list[str] = []
        self.rules: list[tuple[str, object]] = []

    def add(self, marker, response):
        self.rules.insert(0, (marker, response))
        return self

    async def _dispatch(self, code):
        self.calls.append(code)
        for marker, resp in self.rules:
            if marker in code:
                if callable(resp):
                    return resp(code, self)
                return list(resp)
        return []

    async def execute_read(self, code, timeout=5.0):
        return await self._dispatch(code)

    async def execute_write(self, code, timeout=5.0):
        return await self._dispatch(code)


def overview_line(turn, gold="100.0", gpt="5.0", sci="10.0", cul="8.0", score="42"):
    """First line of the overview query response (>=14 pipe fields)."""
    return (
        f"{turn}|0|Rome|Trajan|{gold}|{gpt}|{sci}|{cul}|0.0"
        f"|Mining|Code of Laws|2|5|{score}"
    )


def _advance(code, conn):
    """Effect for the ACTION_ENDTURN write: the game turn ticks forward."""
    conn.turn += 1
    return ["OK:TURN_ENDED"]


def quiet_conn(turn=50, advance=True):
    """A conn wired for a peaceful empire: no blockers, no threats, no events."""
    conn = ScriptedConn()
    conn.turn = turn
    conn.add('print(Game.GetCurrentGameTurn())', lambda code, c: [str(c.turn)])
    conn.add("__civmcp_wc_handler", ["HANDLER_SET"])
    conn.add('k:find("WORLD_CONGRESS")', ["DISMISSED:0"])
    conn.add("WORLD_CONGRESS_LOOKED_AT_AVAILABLE", ["OK"])
    conn.add("GOVERNOR_IDLE", ["OK"])
    conn.add("SetGovernmentChangeConsidered", ["OK"])
    conn.add("AUTO_CLEARED", ["AUTO_CLEARED"])
    conn.add("NEEDS_PROMO", ["NO_PROMO_NEEDED|cleared=0"])
    conn.add("FinishMoves", ["AUTO_SKIPPED"])
    conn.add("CORRUPTED|", ["CLEAN"])
    conn.add("EMPTY|", ["CLEAN"])
    conn.add("GetTokensToGive", ["AUTO_RESOLVED"])
    conn.add("GetNextEscapingSpyID", ["OK:ESCAPE_ROUTE|DISTRICT_CITY_CENTER"])
    conn.add("UI.CanEndTurn()", ["CANNOT_END"])
    conn.add("BLOCKING|", ["NONE"])
    if advance:
        conn.add("ACTION_ENDTURN", _advance)
    conn.add("Network.SaveGame", ["OK|saved"])
    # Victory-proximity code embeds "DIPLO_THREAT|" which contains "THREAT|";
    # tests that care prepend their own rule, so ordering here is safe.
    conn.add("THREAT|", [])
    conn.add("NOTIF|", ["TOTAL|0|0"])
    conn.add("FOG|", [])
    conn.add("RIVAL|", [])
    conn.add("TRCAP|", [])
    conn.add("DIPLO_THREAT", [])
    conn.add("EXPLORE|", lambda code, c: [overview_line(c.turn)])
    return conn


class FakeGameState:
    """The slice of GameState that end_turn.py reads, stubbed in memory."""

    def __init__(self, conn, turn=50):
        self.conn = conn
        self._pending_end_turn = False
        self._pending_end_turn_from = None
        self._last_snapshot = None
        self._high_water_turn = 0
        self._advisor_calls_this_turn = 0
        self._run_aborted = False
        self._save_load_history = []
        self._last_game_over = None
        self.game_over = None
        self.game_over_after = None  # (call_number, status) — scripted escalation
        self._game_over_calls = 0
        self.sessions = []
        self.sessions_after = None  # (call_number, sessions) — appear mid-wait
        self.sessions_exc = None
        self.session_calls = 0
        self.deals = []
        self.deals_exc = None
        self.popup_result = "No popup"
        self.popup_exc = None
        self.capture_result = "Kept city"
        self.snapshot = lq.TurnSnapshot(
            turn=turn, units={}, cities={}, current_research="", current_civic=""
        )
        self.diff_events: list[lq.TurnEvent] = []
        self.wc = lq.WorldCongressStatus(
            is_in_session=False,
            turns_until_next=5,
            favor=0,
            max_votes=0,
            favor_costs=[],
            resolutions=[],
            proposals=[],
        )

    async def check_game_over(self):
        self._game_over_calls += 1
        if self.game_over_after and self._game_over_calls >= self.game_over_after[0]:
            return self.game_over_after[1]
        return self.game_over

    async def get_diplomacy_sessions(self):
        self.session_calls += 1
        if self.sessions_exc is not None:
            raise self.sessions_exc
        if self.sessions_after and self.session_calls >= self.sessions_after[0]:
            return list(self.sessions_after[1])
        return list(self.sessions)

    async def get_pending_deals(self):
        if self.deals_exc is not None:
            raise self.deals_exc
        return list(self.deals)

    async def dismiss_popup(self):
        if self.popup_exc is not None:
            raise self.popup_exc
        return self.popup_result

    async def get_world_congress(self):
        return self.wc

    async def _take_snapshot(self):
        return self.snapshot

    def _diff_snapshots(self, before, after):
        return list(self.diff_events)

    async def resolve_city_capture(self, action):
        return self.capture_result

    async def get_victory_progress(self):
        return object()  # narrate is monkeypatched where this is reached

    @staticmethod
    def _build_turn_report(
        turn_before, turn_after, events, notifications, stockpiles=None, score=None
    ):
        header = f"Turn {turn_before} -> {turn_after}"
        if score is not None:
            header += f" | Score: {score}"
        lines = [header]
        for e in events:
            lines.append(f"[p{e.priority}|{e.category}] {e.message}")
        for n in notifications:
            lines.append(f"NOTIF: {n.type_name}")
        return "\n".join(lines)


@pytest.fixture
def fast(monkeypatch):
    """Remove all real waiting and filesystem side effects from the flow.

    end_turn polls with asyncio.sleep on every path (up to ~9 minutes of AI
    processing time in production); here every sleep is a no-op. The per-turn
    autosave (Network.SaveGame + save-dir cleanup) is replaced by recorders.
    """

    async def _sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    rec = types.SimpleNamespace(saves=[], cleanups=[])

    async def fake_save(conn, name):
        rec.saves.append(name)
        return f"Saved: {name}"

    monkeypatch.setattr("civ_mcp.end_turn.save_game", fake_save)
    monkeypatch.setattr(
        "civ_mcp.end_turn.cleanup_old_autosaves",
        lambda keep=5: rec.cleanups.append(keep),
    )
    return rec


def make_session(**kw):
    base = dict(
        session_id=1,
        other_player_id=2,
        other_civ_name="Egypt",
        other_leader_name="Cleopatra",
        choices=[],
    )
    base.update(kw)
    return lq.DiplomacySession(**base)


def make_game_over(is_defeat, victory_type="VICTORY_SCIENCE"):
    return lq.GameOverStatus(
        is_game_over=True,
        is_defeat=is_defeat,
        winner_name="Egypt",
        winner_leader="Cleopatra",
        victory_type=victory_type,
        player_alive=not is_defeat,
    )


def make_wc(turns_until_next=0, n_resolutions=1, favor=10):
    resolutions = [
        lq.CongressResolution(
            resolution_type="WC_RES_X",
            resolution_hash=1,
            name="X",
            target_kind="YIELD",
            effect_a="a",
            effect_b="b",
            possible_targets=[],
        )
        for _ in range(n_resolutions)
    ]
    return lq.WorldCongressStatus(
        is_in_session=False,
        turns_until_next=turns_until_next,
        favor=favor,
        max_votes=1,
        favor_costs=[0],
        resolutions=resolutions,
        proposals=[],
    )


def make_city_snap(**kw):
    base = dict(city_id=1, name="Rome", population=5, currently_building="Granary")
    base.update(kw)
    return lq.CitySnapshot(**base)


# ---------------------------------------------------------------------------
# _get_turn_number
# ---------------------------------------------------------------------------


class TestGetTurnNumber:
    """The turn number drives every advancement check; a bad read must
    degrade to None (treated as "no information") rather than crash polling."""

    def test_reads_the_first_output_line_as_an_int(self):
        async def go():
            conn = ScriptedConn()
            conn.add("GetCurrentGameTurn", ["42"])
            return await _get_turn_number(FakeGameState(conn))

        assert run(go()) == 42

    def test_returns_none_when_the_game_says_nothing(self):
        async def go():
            return await _get_turn_number(FakeGameState(ScriptedConn()))

        assert run(go()) is None

    def test_returns_none_on_unparseable_output(self):
        async def go():
            conn = ScriptedConn()
            conn.add("GetCurrentGameTurn", ["not-a-number"])
            return await _get_turn_number(FakeGameState(conn))

        assert run(go()) is None

    def test_returns_none_on_lua_error(self):
        async def go():
            conn = ScriptedConn()

            def explode(code, c):
                raise LuaError("ERR:attempt to index nil")

            conn.add("GetCurrentGameTurn", explode)
            return await _get_turn_number(FakeGameState(conn))

        assert run(go()) is None


# ---------------------------------------------------------------------------
# _check_victory_proximity
# ---------------------------------------------------------------------------


class TestVictoryProximity:
    """These warnings are the only signal for invisible victories (religious,
    diplomatic, science). Tier boundaries and the enabled-victory filter
    decide whether the agent ever hears about a rival about to win."""

    async def _run_proximity(self, lines):
        conn = ScriptedConn()
        conn.add("DIPLO_THREAT", lines)
        return await _check_victory_proximity(FakeGameState(conn))

    def test_no_output_means_no_events(self):
        assert run(self._run_proximity([])) == []

    def test_diplo_twenty_is_imminent(self):
        events = run(self._run_proximity(["DIPLO_THREAT|Rome|20"]))
        assert len(events) == 1
        assert "DIPLOMATIC VICTORY IMMINENT" in events[0].message
        assert "20/20" in events[0].message

    def test_diplo_eighteen_warns_about_immediate_win_at_twenty(self):
        events = run(self._run_proximity(["DIPLO_THREAT|Rome|18"]))
        assert "DIPLOMATIC VICTORY THREAT" in events[0].message
        assert "wins IMMEDIATELY at 20" in events[0].message

    def test_diplo_fifteen_is_a_plain_threat(self):
        events = run(self._run_proximity(["DIPLO_THREAT|Rome|15"]))
        assert events[0].message.endswith("has 15/20 DVP!")

    def test_diplo_below_fifteen_is_priority_two_race_info(self):
        events = run(self._run_proximity(["DIPLO_THREAT|Rome|10"]))
        assert events[0].priority == 2
        assert "Diplomatic race" in events[0].message

    def test_science_one_below_needed_is_imminent(self):
        events = run(self._run_proximity(["SCI_THREAT|Egypt|49|50"]))
        assert "SCIENCE VICTORY IMMINENT" in events[0].message
        assert "49/50" in events[0].message

    def test_science_any_progress_is_priority_two(self):
        events = run(self._run_proximity(["SCI_THREAT|Egypt|2|50"]))
        assert events[0].priority == 2
        assert "Science race" in events[0].message

    def test_religion_majority_in_all_civs_is_imminent(self):
        events = run(self._run_proximity(["REL_THREAT|Mali|Islam|6|6"]))
        assert "RELIGIOUS VICTORY IMMINENT" in events[0].message
        assert "ALL 6" in events[0].message

    def test_religion_one_away_is_a_threat(self):
        events = run(self._run_proximity(["REL_THREAT|Mali|Islam|5|6"]))
        assert "RELIGIOUS VICTORY THREAT" in events[0].message
        assert "5/6" in events[0].message

    def test_disabled_victory_types_are_filtered_out(self):
        """When VENABLED lists active victory types, threats for disabled
        types are noise and must be dropped."""
        lines = ["VENABLED|VICTORY_TECHNOLOGY", "DIPLO_THREAT|Rome|20"]
        assert run(self._run_proximity(lines)) == []

    def test_enabled_victory_types_pass_the_filter(self):
        lines = ["VENABLED|VICTORY_DIPLOMATIC", "DIPLO_THREAT|Rome|20"]
        events = run(self._run_proximity(lines))
        assert len(events) == 1

    def test_no_venabled_lines_means_everything_is_reported(self):
        events = run(self._run_proximity(["DIPLO_THREAT|Rome|20"]))
        assert len(events) == 1

    def test_short_rel_threat_line_is_skipped_without_losing_other_events(self):
        """A REL_THREAT line with fewer than 5 fields (civ, rel, count, total)
        cannot be parsed and must be skipped — it must not crash the scan and
        discard every other threat found that turn."""
        lines = ["REL_THREAT|Mali|Islam|3", "DIPLO_THREAT|Rome|20"]
        events = run(self._run_proximity(lines))
        assert len(events) == 1
        assert "DIPLOMATIC VICTORY IMMINENT" in events[0].message


# ---------------------------------------------------------------------------
# _check_empire_warnings
# ---------------------------------------------------------------------------


def warnings_conn(**rules):
    conn = ScriptedConn()
    for marker, resp in rules.items():
        conn.add(marker, resp)
    return conn


class TestEmpireWarnings:
    """Empire warnings compensate for the Sensorium Effect — the agent cannot
    see the score ticker or city banners, so loyalty collapses, bankruptcies
    and idle trade routes must be spelled out or they are silently missed."""

    def _snap(self, turn=51, cities=None, stockpiles=None):
        return lq.TurnSnapshot(
            turn=turn,
            units={},
            cities=cities or {},
            current_research="",
            current_civic="",
            stockpiles=stockpiles or [],
        )

    def test_loyalty_crisis_reports_turns_until_rebellion(self):
        async def go():
            snap = self._snap(
                cities={1: make_city_snap(loyalty=40.0, loyalty_per_turn=-10.0)}
            )
            conn = warnings_conn(**{"EXPLORE|": [overview_line(51)]})
            return await _check_empire_warnings(FakeGameState(conn), snap)

        events, _ = run(go())
        assert len(events) == 1
        assert events[0].priority == 1
        assert "LOYALTY CRISIS: Rome losing -10.0/t" in events[0].message
        assert "~4 turns" in events[0].message

    def test_low_loyalty_without_freefall_is_a_warning(self):
        async def go():
            snap = self._snap(
                cities={1: make_city_snap(loyalty=25.0, loyalty_per_turn=-2.0)}
            )
            conn = warnings_conn(**{"EXPLORE|": [overview_line(51)]})
            return await _check_empire_warnings(FakeGameState(conn), snap)

        events, _ = run(go())
        assert events[0].priority == 2
        assert "LOYALTY WARNING" in events[0].message

    def test_stable_loyalty_produces_no_city_events(self):
        async def go():
            snap = self._snap(cities={1: make_city_snap()})
            conn = warnings_conn(**{"EXPLORE|": [overview_line(51)]})
            return await _check_empire_warnings(FakeGameState(conn), snap)

        events, _ = run(go())
        assert events == []

    def test_full_stockpile_with_positive_net_flags_waste(self):
        async def go():
            snap = self._snap(
                stockpiles=[
                    lq.ResourceStockpile(
                        name="IRON", amount=40, cap=40, per_turn=5, demand=0, imported=0
                    )
                ]
            )
            conn = warnings_conn(**{"EXPLORE|": [overview_line(51)]})
            return await _check_empire_warnings(FakeGameState(conn), snap)

        events, _ = run(go())
        assert any("RESOURCE CAP: IRON 40/40 (+5/t)" in e.message for e in events)

    def test_stockpile_below_cap_or_draining_is_quiet(self):
        async def go():
            snap = self._snap(
                stockpiles=[
                    lq.ResourceStockpile(
                        name="IRON", amount=30, cap=40, per_turn=5, demand=0, imported=0
                    ),
                    lq.ResourceStockpile(
                        name="COAL", amount=20, cap=20, per_turn=1, demand=3, imported=0
                    ),
                ]
            )
            conn = warnings_conn(**{"EXPLORE|": [overview_line(51)]})
            return await _check_empire_warnings(FakeGameState(conn), snap)

        events, _ = run(go())
        assert events == []

    def test_gold_deficit_estimates_turns_to_bankruptcy_and_returns_score(self):
        async def go():
            conn = warnings_conn(
                **{"EXPLORE|": [overview_line(51, gold="50.0", gpt="-10.0", score="77")]}
            )
            return await _check_empire_warnings(FakeGameState(conn), self._snap())

        events, score = run(go())
        assert score == 77
        assert any("DEFICIT" in e.message and "~5 turns" in e.message for e in events)

    def test_large_treasury_cushions_a_deficit(self):
        async def go():
            conn = warnings_conn(
                **{"EXPLORE|": [overview_line(51, gold="1000.0", gpt="-10.0")]}
            )
            return await _check_empire_warnings(FakeGameState(conn), self._snap())

        events, _ = run(go())
        assert not any("DEFICIT" in e.message for e in events)

    def test_overview_failure_degrades_to_no_economy_events_and_no_score(self):
        """The overview query is best-effort: a failure must not kill the
        whole warnings pass (loyalty/resource checks already ran)."""
        async def go():
            conn = warnings_conn(**{"EXPLORE|": []})
            return await _check_empire_warnings(FakeGameState(conn), self._snap())

        events, score = run(go())
        assert events == []
        assert score is None

    def test_idle_trade_routes_singular_and_plural(self):
        async def go():
            conn = warnings_conn(
                **{
                    "EXPLORE|": [overview_line(51)],
                    "TRCAP|": Queue(["TRCAP|2|1"], ["TRCAP|4|1"]),
                }
            )
            gs = FakeGameState(conn)
            one, _ = await _check_empire_warnings(gs, self._snap())
            two, _ = await _check_empire_warnings(gs, self._snap())
            return one, two

        one, two = run(go())
        assert "1 unused route capacity (1/2 active)" in one[0].message
        assert "3 unused route capacities (1/4 active)" in two[0].message

    def test_full_trade_capacity_is_quiet(self):
        async def go():
            conn = warnings_conn(
                **{"EXPLORE|": [overview_line(51)], "TRCAP|": ["TRCAP|2|2"]}
            )
            return await _check_empire_warnings(FakeGameState(conn), self._snap())

        events, _ = run(go())
        assert events == []

    def test_scoreboard_and_military_run_only_every_five_turns(self):
        """The rival snapshot is the most expensive warning query; it must
        not fire on off turns."""
        async def go():
            conn = warnings_conn(
                **{
                    "EXPLORE|": [overview_line(51)],
                    "RIVAL|": ["RIVAL|1|Egypt|100|3|12|50.0|20.0|5.0|300|10|8|0.0|0|0|"],
                }
            )
            return await _check_empire_warnings(FakeGameState(conn), self._snap(turn=51))

        events, _ = run(go())
        assert events == []

    def test_scoreboard_reports_rank_and_leader(self):
        async def go():
            conn = warnings_conn()
            conn.add("GetMilitaryStrength", ["50"])
            conn.add("EXPLORE|", [overview_line(50)])
            conn.add(
                "RIVAL|",
                [
                    "RIVAL|1|Egypt|100|3|12|50.0|20.0|5.0|300|10|8|0.0|0|0|",
                    "RIVAL|2|China|90|2|10|5.0|15.0|3.0|100|9|7|0.0|0|0|",
                ],
            )
            return await _check_empire_warnings(FakeGameState(conn), self._snap(turn=50))

        events, _ = run(go())
        messages = [e.message for e in events]
        assert any(
            "ranks 2 of 3" in m and "Leader: Egypt at 50.0/t" in m for m in messages
        )

    def test_military_warning_flags_rivals_at_double_our_strength(self):
        async def go():
            conn = warnings_conn()
            conn.add("GetMilitaryStrength", ["50"])
            conn.add("EXPLORE|", [overview_line(50)])
            conn.add(
                "RIVAL|",
                ["RIVAL|1|Egypt|100|3|12|50.0|20.0|5.0|300|10|8|0.0|0|0|"],
            )
            return await _check_empire_warnings(FakeGameState(conn), self._snap(turn=50))

        events, _ = run(go())
        assert any(
            "MILITARY WARNING: Egypt has 300 military (6.0x ours at 50)" in e.message
            for e in events
        )

    def test_leading_science_rank_produces_no_scoreboard_event(self):
        async def go():
            conn = warnings_conn()
            conn.add("GetMilitaryStrength", ["500"])
            conn.add("EXPLORE|", [overview_line(50, sci="100.0")])
            conn.add(
                "RIVAL|",
                ["RIVAL|1|Egypt|100|3|12|50.0|20.0|5.0|300|10|8|0.0|0|0|"],
            )
            return await _check_empire_warnings(FakeGameState(conn), self._snap(turn=50))

        events, _ = run(go())
        assert not any("SCOREBOARD" in e.message for e in events)
        assert not any("MILITARY WARNING" in e.message for e in events)


# ---------------------------------------------------------------------------
# _check_mid_turn_diplomacy
# ---------------------------------------------------------------------------


class TestMidTurnDiplomacy:
    """During AI processing a diplomacy session can pause the turn. War
    declarations are informational and must be auto-dismissed (the original
    ACTION_ENDTURN is still in flight); trade proposals must be handed back
    to the agent with the right follow-up tool."""

    def test_no_sessions_is_a_clean_pass(self, fast):
        async def go():
            return await _check_mid_turn_diplomacy(FakeGameState(ScriptedConn()), "lua", 50)

        assert run(go()) == (None, False)

    def test_war_declaration_is_dismissed_and_turn_advance_detected(self, fast):
        async def go():
            conn = ScriptedConn()
            conn.add("GetCurrentGameTurn", ["51"])
            gs = FakeGameState(conn)
            gs.sessions = [make_session(is_at_war=True)]
            result = await _check_mid_turn_diplomacy(gs, "lua", 50)
            return result, conn, gs

        (msg, advanced), conn, gs = run(go())
        assert (msg, advanced) == (None, True)
        assert any("EXIT" in c for c in conn.calls)  # CloseSession sent
        # Turn advanced on its own — pending flags are the caller's business.
        assert gs._pending_end_turn is False

    def test_war_declaration_without_advance_tells_agent_to_retry(self, fast):
        async def go():
            conn = ScriptedConn()
            conn.add("GetCurrentGameTurn", ["50"])
            gs = FakeGameState(conn)
            gs._pending_end_turn = True
            gs._pending_end_turn_from = 50
            gs.sessions = [make_session(is_at_war=True)]
            result = await _check_mid_turn_diplomacy(gs, "lua", 50)
            return result, gs

        (msg, advanced), gs = run(go())
        assert advanced is False
        assert "WAR DECLARED by Egypt (Cleopatra)!" in msg
        assert "call end_turn again" in msg
        # The original ACTION_ENDTURN was consumed — next call must re-send.
        assert gs._pending_end_turn is False
        assert gs._pending_end_turn_from is None

    def test_deal_session_is_formatted_with_trade_instructions(self, fast):
        async def go():
            gs = FakeGameState(ScriptedConn())
            gs.sessions = [
                make_session(
                    deal_summary="They offer: 100 Gold",
                    dialogue_text="A fair exchange.",
                    reason_text="Agenda: likes traders",
                )
            ]
            return await _check_mid_turn_diplomacy(gs, "lua", 50)

        msg, advanced = run(go())
        assert advanced is False
        assert "Egypt (Cleopatra) [deal]" in msg
        assert 'Egypt says: "A fair exchange."' in msg
        assert "Reason: Agenda: likes traders" in msg
        assert "Deal from Egypt: They offer: 100 Gold" in msg
        assert "respond_to_trade" in msg

    def test_plain_session_points_at_respond_to_diplomacy(self, fast):
        async def go():
            gs = FakeGameState(ScriptedConn())
            gs.sessions = [make_session(dialogue_text="Greetings.", buttons="GOODBYE")]
            return await _check_mid_turn_diplomacy(gs, "lua", 50)

        msg, _ = run(go())
        assert "Egypt (Cleopatra) [goodbye]" in msg
        assert "Use respond_to_diplomacy" in msg

    def test_mixed_war_and_deal_reports_both(self, fast):
        async def go():
            conn = ScriptedConn()
            conn.add("GetCurrentGameTurn", ["50"])
            gs = FakeGameState(conn)
            gs.sessions = [
                make_session(is_at_war=True),
                make_session(
                    session_id=2, deal_summary="They offer: Open Borders"
                ),
            ]
            return await _check_mid_turn_diplomacy(gs, "lua", 50)

        msg, advanced = run(go())
        assert advanced is False
        assert "WAR DECLARED by Egypt! (auto-dismissed)" in msg
        assert "Deal from Egypt: They offer: Open Borders" in msg

    def test_empty_dialogue_is_refetched_once(self, fast):
        """DiplomacyActionView text takes 1-2s to populate; exactly one retry."""
        async def go():
            gs = FakeGameState(ScriptedConn())
            gs.sessions = [make_session(dialogue_text="", buttons="GOODBYE")]
            result = await _check_mid_turn_diplomacy(gs, "lua", 50)
            return result, gs.session_calls

        (msg, _), calls = run(go())
        assert calls == 2
        assert "Egypt (Cleopatra) [goodbye]" in msg

    def test_session_query_failure_is_swallowed_as_no_diplomacy(self, fast):
        """A failed InGame probe during AI processing must look like 'no
        diplomacy' so polling continues instead of crashing end_turn."""
        async def go():
            gs = FakeGameState(ScriptedConn())
            gs.sessions_exc = LuaError("ERR:context busy")
            return await _check_mid_turn_diplomacy(gs, "lua", 50)

        assert run(go()) == (None, False)


# ---------------------------------------------------------------------------
# execute_end_turn — entry gates
# ---------------------------------------------------------------------------


class TestEntryGates:
    """Checks that run before any blocker handling: an aborted run, a
    finished game, an open diplomacy session, a pending trade deal, and an
    un-voted World Congress all refuse to advance the turn."""

    def test_aborted_run_refuses_without_touching_the_game(self, fast):
        async def go():
            conn = quiet_conn()
            gs = FakeGameState(conn)
            gs._run_aborted = True
            result = await execute_end_turn(gs)
            return result, conn

        result, conn = run(go())
        assert "RUN ABORTED" in result
        assert conn.calls == []

    def test_defeat_at_entry_is_terminal(self, fast):
        async def go():
            gs = FakeGameState(quiet_conn())
            gs.game_over = make_game_over(is_defeat=True, victory_type="VICTORY_RELIGIOUS")
            result = await execute_end_turn(gs)
            return result, gs

        result, gs = run(go())
        assert "GAME OVER — DEFEAT. Cleopatra of Egypt won a Religious victory." in result
        assert gs._last_game_over is not None
        assert gs._pending_end_turn is False

    def test_victory_at_entry_is_terminal(self, fast):
        async def go():
            gs = FakeGameState(quiet_conn())
            gs.game_over = make_game_over(is_defeat=False)
            return await execute_end_turn(gs)

        result = run(go())
        assert result == "GAME OVER — VICTORY! You won a Science victory! The game has ended."

    def test_open_diplomacy_session_blocks(self, fast):
        async def go():
            gs = FakeGameState(quiet_conn())
            gs.sessions = [make_session()]
            return await execute_end_turn(gs)

        result = run(go())
        assert result == (
            "Cannot end turn: diplomacy encounter pending with "
            "Egypt (Cleopatra) [active]. Use respond_to_diplomacy to handle it."
        )

    def test_pending_trade_deal_blocks(self, fast):
        async def go():
            gs = FakeGameState(quiet_conn())
            gs.deals = [
                lq.PendingDeal(
                    other_player_id=1,
                    other_player_name="Egypt",
                    other_leader_name="Cleopatra",
                )
            ]
            return await execute_end_turn(gs)

        result = run(go())
        assert "Cannot end turn: incoming trade deal pending." in result
        assert "From: Egypt (Cleopatra) [player 1]" in result

    def test_world_congress_without_vote_handler_blocks(self, fast):
        """WC fires synchronously inside ACTION_ENDTURN; without a registered
        voter the favor would be wasted, so end_turn must refuse first."""
        async def go():
            conn = quiet_conn()
            conn.add("__civmcp_wc_handler", ["NO_HANDLER"])
            gs = FakeGameState(conn)
            gs.wc = make_wc(turns_until_next=0, n_resolutions=2, favor=30)
            return await execute_end_turn(gs)

        result = run(go())
        assert "World Congress fires this turn (2 resolution(s), 30 favor)." in result
        assert "queue_wc_votes()" in result

    def test_world_congress_with_handler_proceeds(self, fast):
        async def go():
            gs = FakeGameState(quiet_conn())
            gs.wc = make_wc(turns_until_next=0, n_resolutions=1)
            return await execute_end_turn(gs)

        assert run(go()).startswith("Turn 50 -> 51")

    def test_world_congress_with_zero_resolutions_auto_proceeds(self, fast):
        async def go():
            conn = quiet_conn()
            gs = FakeGameState(conn)
            gs.wc = make_wc(turns_until_next=0, n_resolutions=0)
            result = await execute_end_turn(gs)
            return result, conn

        result, conn = run(go())
        assert result.startswith("Turn 50 -> 51")
        # Nothing to vote on — the handler probe is skipped entirely.
        assert not any("__civmcp_wc_handler" in c for c in conn.calls)

    def test_popup_and_deal_probe_failures_do_not_block(self, fast):
        """Both pre-turn probes are best-effort; a failure must not stop the turn."""
        async def go():
            gs = FakeGameState(quiet_conn())
            gs.popup_exc = LuaError("ERR:no UI")
            gs.deals_exc = LuaError("ERR:busy")
            return await execute_end_turn(gs)

        assert run(go()).startswith("Turn 50 -> 51")


# ---------------------------------------------------------------------------
# execute_end_turn — blocker resolution
# ---------------------------------------------------------------------------


def blocker_conn(*blocking_rounds, turn=50):
    """Quiet conn whose blocking query returns the given rounds in order."""
    conn = quiet_conn(turn=turn)
    conn.add("BLOCKING|", Queue(*blocking_rounds, fallback=["NONE"]))
    return conn


class TestSoftBlockerAutoResolution:
    """Soft blockers (governor idle, stale notifications, 0-move units, ...)
    are resolved by end_turn itself so the agent is never bothered with
    mechanical cleanup. Each test proves the resolver Lua ran and the turn
    then advanced."""

    def test_governor_idle_is_dismissed(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_GOVERNOR_IDLE|Gov point"])
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        result, conn = run(go())
        assert result.startswith("Turn 50 -> 51")
        assert any("GOVERNOR_IDLE" in c and "SendActivated" in c for c in conn.calls)

    def test_government_change_consideration_is_marked_considered(self, fast):
        async def go():
            conn = blocker_conn(
                ["BLOCKING|ENDTURN_BLOCKING_CONSIDER_GOVERNMENT_CHANGE|"]
            )
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        result, conn = run(go())
        assert result.startswith("Turn 50 -> 51")
        assert any("SetGovernmentChangeConsidered(true)" in c for c in conn.calls)

    def test_world_congress_look_is_dismissed(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK|"])
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        result, conn = run(go())
        assert result.startswith("Turn 50 -> 51")
        assert any("WORLD_CONGRESS_LOOKED_AT_AVAILABLE" in c for c in conn.calls)

    def test_world_congress_catch_all_resolves_when_something_was_dismissed(self, fast):
        async def go():
            conn = blocker_conn(
                ["BLOCKING|ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION|"]
            )
            conn.add('k:find("WORLD_CONGRESS")', ["DISMISSED:1"])
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        result, _ = run(go())
        assert result.startswith("Turn 50 -> 51")

    def test_world_congress_catch_all_is_hard_when_nothing_was_dismissed(self, fast):
        async def go():
            conn = blocker_conn(
                ["BLOCKING|ENDTURN_BLOCKING_WORLD_CONGRESS_SPECIAL_SESSION|"]
            )
            conn.add('k:find("WORLD_CONGRESS")', ["DISMISSED:0"])
            return await execute_end_turn(FakeGameState(conn))

        result = run(go())
        assert "Cannot end turn" in result
        assert "World Congress Special Session" in result

    def test_disloyal_city_is_auto_kept(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_CONSIDER_DISLOYAL_CITY|"])
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        assert run(go())[0].startswith("Turn 50 -> 51")

    def test_captured_city_is_auto_kept(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_CONSIDER_RAZE_CITY|"])
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        assert run(go())[0].startswith("Turn 50 -> 51")

    def test_city_capture_error_makes_it_a_hard_blocker(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_CONSIDER_RAZE_CITY|"])
            gs = FakeGameState(conn)
            gs.capture_result = "Error: city action failed"
            return await execute_end_turn(gs)

        result = run(go())
        assert "Cannot end turn" in result
        assert "Consider Raze City" in result
        assert "city_action" in result

    def test_envoy_with_zero_tokens_is_auto_resolved(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_GIVE_INFLUENCE_TOKEN|"])
            conn.add("GetTokensToGive", ["AUTO_RESOLVED"])
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        assert run(go())[0].startswith("Turn 50 -> 51")

    def test_envoy_with_tokens_is_a_hard_blocker(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_GIVE_INFLUENCE_TOKEN|"])
            conn.add("GetTokensToGive", ["HAS_TOKENS|2"])
            return await execute_end_turn(FakeGameState(conn))

        result = run(go())
        assert "Cannot end turn" in result
        assert "send_envoy" in result

    def test_stale_research_notification_is_force_dismissed(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_RESEARCH|Pick a tech"])
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        assert run(go())[0].startswith("Turn 50 -> 51")

    def test_unset_research_is_hard_with_a_diagnostic_hint(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_RESEARCH|"])
            conn.add("AUTO_CLEARED", ["NOT_SET"])
            return await execute_end_turn(FakeGameState(conn))

        result = run(go())
        assert "Research" in result
        assert "No tech selected" in result
        assert "set_research" in result

    def test_unset_civic_is_hard_with_a_civic_hint(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_CIVIC|"])
            conn.add("AUTO_CLEARED", ["NOT_SET"])
            return await execute_end_turn(FakeGameState(conn))

        result = run(go())
        assert "No civic selected" in result

    def test_promotion_blocker_with_no_xp_qualified_unit_is_cleared(self, fast):
        """GameCore promotions don't consume XP, so CanPromote() lies; the
        XP-threshold check is the real gate, and both notification objects
        must be dismissed or the blocker regenerates."""
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_UNIT_PROMOTION|"])
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        result, conn = run(go())
        assert result.startswith("Turn 50 -> 51")
        assert any("NOTIFICATION_UNIT_PROMOTION_AVAILABLE" in c for c in conn.calls)

    def test_promotion_blocker_with_real_xp_is_hard(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_UNIT_PROMOTION|"])
            conn.add("NEEDS_PROMO", ["NEEDS_PROMO"])
            return await execute_end_turn(FakeGameState(conn))

        result = run(go())
        assert "Cannot end turn" in result
        assert "promote_unit" in result

    def test_units_with_zero_moves_are_auto_finished(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_UNITS|2 units"])
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        assert run(go())[0].startswith("Turn 50 -> 51")

    def test_units_with_moves_are_a_hard_blocker(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_UNITS|2 units"])
            conn.add("FinishMoves", ["UNITS_NEED_ORDERS"])
            return await execute_end_turn(FakeGameState(conn))

        result = run(go())
        assert "Cannot end turn" in result
        assert "Move or skip remaining units" in result

    def test_corrupted_production_queue_is_dismissed(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_PRODUCTION|"])
            conn.add("CORRUPTED|", ["CORRUPTED|Rome (id:3)"])
            conn.add("DISMISSED|", ["DISMISSED|1"])
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        assert run(go())[0].startswith("Turn 50 -> 51")

    def test_empty_production_queue_names_the_city_in_the_blocker(self, fast):
        """RequestOperation can silently no-op; naming the empty-queue city
        saves the agent a get_cities round-trip."""
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_PRODUCTION|"])
            conn.add("EMPTY|", ["EMPTY|Rome (id:3)"])
            return await execute_end_turn(FakeGameState(conn))

        result = run(go())
        assert "empty queue in Rome (id:3)" in result
        assert "set_city_production" in result

    def test_production_blocker_without_diagnostics_keeps_the_raw_message(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_PRODUCTION|Choose production"])
            return await execute_end_turn(FakeGameState(conn))

        result = run(go())
        assert "Production (Choose production)" in result

    def test_spy_escape_route_is_auto_picked(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE|"])
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        assert run(go())[0].startswith("Turn 50 -> 51")

    def test_spy_escape_failure_is_hard(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_SPY_CHOOSE_ESCAPE_ROUTE|"])
            conn.add("GetNextEscapingSpyID", ["NO_ESCAPING_SPY"])
            return await execute_end_turn(FakeGameState(conn))

        result = run(go())
        assert "Cannot end turn" in result
        assert "Spy Choose Escape Route" in result

    def test_world_congress_session_is_never_auto_resolved(self, fast):
        """Voting deploys diplomatic favor strategically — the one blocker
        that must always reach the agent."""
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION|"])
            return await execute_end_turn(FakeGameState(conn))

        result = run(go())
        assert "Cannot end turn" in result
        assert "MUST VOTE" in result

    def test_unrecognized_blocker_is_always_hard_with_generic_hint(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_SOMETHING_NEW|mystery"])
            return await execute_end_turn(FakeGameState(conn))

        result = run(go())
        assert "  - Something New (mystery)  ->  Resolve the blocking notification" in result

    def test_multiple_blockers_resolve_in_one_pass(self, fast):
        """The loop re-queries after soft resolutions; a second round with
        NONE ends it — blockers resolved together, not one end_turn call each."""
        async def go():
            conn = blocker_conn(
                [
                    "BLOCKING|ENDTURN_BLOCKING_GOVERNOR_IDLE|",
                    "BLOCKING|ENDTURN_BLOCKING_UNITS|",
                ],
                ["NONE"],
            )
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        result, conn = run(go())
        assert result.startswith("Turn 50 -> 51")
        assert any("GOVERNOR_IDLE" in c for c in conn.calls)
        assert any("FinishMoves" in c for c in conn.calls)


# ---------------------------------------------------------------------------
# execute_end_turn — hard-blocker flow decisions
# ---------------------------------------------------------------------------


class TestHardBlockerFlow:
    """With hard blockers present, end_turn can still legitimately proceed:
    the game may have auto-advanced (external end-turn) or UI.CanEndTurn may
    disagree with the notification list. Both must skip the blocker report."""

    def test_can_end_turn_true_overrides_hard_blockers(self, fast):
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_UNITS|"])
            conn.add("FinishMoves", ["UNITS_NEED_ORDERS"])
            conn.add("UI.CanEndTurn()", ["CAN_END"])
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        result, conn = run(go())
        assert result.startswith("Turn 50 -> 51")
        assert any("ACTION_ENDTURN" in c for c in conn.calls)

    def test_external_advance_skips_the_blocker_report(self, fast):
        """skip_remaining_units can finish the last move, making the game
        auto-end the turn while our blocker list is already stale."""

        def external_advance(code, conn):
            conn.turn = 51
            return ["UNITS_NEED_ORDERS"]

        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_UNITS|"])
            conn.add("FinishMoves", external_advance)
            result = await execute_end_turn(FakeGameState(conn))
            return result, conn

        result, conn = run(go())
        # Entry turn was 50, external advance took it to 51, and the fresh
        # ACTION_ENDTURN (still sent, no pending request) moved it to 52.
        assert result.startswith("Turn 50 -> 52")

    def test_hard_blocker_report_does_not_set_pending_flags(self, fast):
        """No ACTION_ENDTURN was sent, so nothing may be left in flight."""
        async def go():
            conn = blocker_conn(["BLOCKING|ENDTURN_BLOCKING_UNITS|"])
            conn.add("FinishMoves", ["UNITS_NEED_ORDERS"])
            gs = FakeGameState(conn)
            result = await execute_end_turn(gs)
            return result, gs

        result, gs = run(go())
        assert "Cannot end turn" in result
        assert gs._pending_end_turn is False
        assert gs._pending_end_turn_from is None

    def test_blocking_query_exception_proceeds_anyway(self, fast):
        """A failed blocker query must not trap the player — the turn is
        attempted and the game itself decides."""

        def explode(code, conn):
            raise LuaError("ERR:InGame busy")

        async def go():
            conn = quiet_conn()
            conn.add("BLOCKING|", explode)
            return await execute_end_turn(FakeGameState(conn))

        assert run(go()).startswith("Turn 50 -> 51")


# ---------------------------------------------------------------------------
# execute_end_turn — advancement bookkeeping
# ---------------------------------------------------------------------------


class TestAdvancementBookkeeping:
    """The pending flag, high-water mark, advisor counter and autosave are
    the run's safety rails: they detect duplicate end-turn requests, wrong
    save loads and advisor spam between turns."""

    def test_clean_advance_clears_pending_and_raises_high_water(self, fast):
        async def go():
            gs = FakeGameState(quiet_conn())
            gs._high_water_turn = 50
            result = await execute_end_turn(gs)
            return result, gs

        result, gs = run(go())
        assert result.startswith("Turn 50 -> 51")
        assert gs._pending_end_turn is False
        assert gs._pending_end_turn_from is None
        assert gs._high_water_turn == 51

    def test_advisor_counter_resets_only_on_true_advance(self, fast):
        """Resetting on a blocker retry would let the agent advisor-spam
        between retries within one game turn."""
        async def go():
            gs = FakeGameState(quiet_conn())
            gs._high_water_turn = 50
            gs._advisor_calls_this_turn = 3
            await execute_end_turn(gs)
            return gs

        assert run(go())._advisor_calls_this_turn == 0

    def test_advisor_counter_kept_when_turn_does_not_exceed_high_water(self, fast):
        async def go():
            gs = FakeGameState(quiet_conn())
            gs._high_water_turn = 51  # turn advances 50 -> 51, not beyond
            gs._advisor_calls_this_turn = 3
            await execute_end_turn(gs)
            return gs

        gs = run(go())
        assert gs._advisor_calls_this_turn == 3
        assert gs._high_water_turn == 51

    def test_pending_request_is_not_resent(self, fast):
        """After mid-turn diplomacy the original ACTION_ENDTURN is still in
        flight; re-sending skips turns (412 -> 415 in the field)."""
        async def go():
            conn = quiet_conn(turn=51)
            gs = FakeGameState(conn)
            gs._pending_end_turn = True
            gs._pending_end_turn_from = 49
            gs._last_snapshot = lq.TurnSnapshot(
                turn=50, units={}, cities={}, current_research="", current_civic=""
            )
            result = await execute_end_turn(gs)
            return result, conn

        result, conn = run(go())
        # Baseline is the original request's turn, and nothing was re-sent.
        assert result.startswith("Turn 49 -> 51")
        assert not any("ACTION_ENDTURN" in c for c in conn.calls)

    def test_turn_regression_reports_the_recovery_autosave(self, fast):
        """Landing two or more turns below the high-water mark means the
        wrong save was loaded; the agent gets the exact save to recover with."""
        async def go():
            conn = quiet_conn(turn=40)
            gs = FakeGameState(conn, turn=40)
            gs._high_water_turn = 100
            result = await execute_end_turn(gs)
            return result, gs

        result, gs = run(go())
        assert "CRITICAL: Turn regressed from 100 to 41." in result
        assert get_autosave_for_turn(100) in result
        assert gs._pending_end_turn is False

    def test_one_turn_below_high_water_is_not_a_regression(self, fast):
        async def go():
            conn = quiet_conn(turn=40)
            gs = FakeGameState(conn, turn=40)
            gs._high_water_turn = 42  # 41 >= 42 - 1 — within tolerance
            result = await execute_end_turn(gs)
            return result, gs

        result, _ = run(go())
        assert "CRITICAL" not in result
        assert result.startswith("Turn 40 -> 41")

    def test_autosave_fires_after_a_successful_advance(self, fast):
        async def go():
            result = await execute_end_turn(FakeGameState(quiet_conn()))
            return result

        run(go())
        assert fast.saves == ["0_MCP_0051"]
        assert fast.cleanups == [8]

    def test_autosave_is_skipped_on_platforms_where_saves_do_not_work(self, fast, monkeypatch):
        """Linux (Aspyr) silently drops Network.SaveGame for custom names —
        attempting it would be wasted latency every turn."""
        monkeypatch.setattr(
            "civ_mcp.autosave.saves_work_on_this_platform", lambda: False
        )

        async def go():
            return await execute_end_turn(FakeGameState(quiet_conn()))

        run(go())
        assert fast.saves == []


# ---------------------------------------------------------------------------
# execute_end_turn — hang, timeout, and game-over during the wait
# ---------------------------------------------------------------------------


class TestHangAndTimeout:
    """When the turn never advances, the message must distinguish a real AI
    hang (HANG: prefix with the recovery autosave, which server.py parses for
    auto-recovery) from leftover blockers, and a game that ends mid-wait must
    surface immediately instead of burning the full timeout."""

    def test_true_hang_emits_structured_prefix_and_recovery_save(self, fast):
        async def go():
            conn = quiet_conn(advance=False)
            gs = FakeGameState(conn)
            result = await execute_end_turn(gs)
            return result, gs

        result, gs = run(go())
        expected_save = get_autosave_for_turn(50)
        assert result.startswith(f"HANG:50:{expected_save}|")
        assert "AI turn processing appears stuck" in result
        # Next call must re-send ACTION_ENDTURN.
        assert gs._pending_end_turn is False
        assert gs._pending_end_turn_from is None

    def test_lingering_blocker_is_reported_instead_of_a_hang(self, fast):
        async def go():
            conn = quiet_conn(advance=False)
            conn.add(
                "BLOCKING|",
                Queue(["NONE"], ["BLOCKING|ENDTURN_BLOCKING_UNITS|"]),
            )
            result = await execute_end_turn(FakeGameState(conn))
            return result

        result = run(go())
        assert result == "End turn blocked (turn 50): Blocker: Units"

    def test_defeat_during_ai_processing_short_circuits_the_wait(self, fast):
        async def go():
            conn = quiet_conn(advance=False)
            gs = FakeGameState(conn)
            gs.game_over_after = (2, make_game_over(is_defeat=True, victory_type="VICTORY_CULTURE"))
            result = await execute_end_turn(gs)
            return result, gs

        result, gs = run(go())
        assert "GAME OVER — DEFEAT. Cleopatra of Egypt won a Culture victory." in result
        assert gs._last_game_over is not None
        assert gs._pending_end_turn is False

    def test_victory_detected_right_after_advance(self, fast):
        """Victories fire during the turn transition; the post-advance check
        puts GAME OVER in the result so log_game_over sees it."""
        async def go():
            conn = quiet_conn()
            gs = FakeGameState(conn)
            gs.game_over_after = (2, make_game_over(is_defeat=False))
            result = await execute_end_turn(gs)
            return result, gs

        result, gs = run(go())
        assert result.startswith("Turn 50 -> 51\n")
        assert "GAME OVER — VICTORY! You won a Science victory!" in result
        assert gs._last_game_over is not None

    def test_mid_turn_diplomacy_message_replaces_the_hang(self, fast):
        """A trade proposal surfacing during the wait must end the call with
        instructions, not a HANG."""
        async def go():
            conn = quiet_conn(advance=False)
            gs = FakeGameState(conn)
            # Entry gate (call 1) sees nothing; the session opens during the wait.
            gs.sessions_after = (2, [make_session(deal_summary="They offer: 5 GPT")])
            result = await execute_end_turn(gs)
            return result

        result = run(go())
        assert "Turn paused — AI diplomatic proposal" in result
        assert "respond_to_trade" in result
        assert not result.startswith("HANG:")


# ---------------------------------------------------------------------------
# execute_end_turn — post-advance event assembly
# ---------------------------------------------------------------------------


class TestPostTurnEvents:
    """After a successful advance the report is the agent's only window into
    what changed: diff events, threats, fog-of-war tracking, growth alerts,
    victory scans and empire warnings all land in one string."""

    def test_snapshot_diff_events_are_included(self, fast):
        async def go():
            gs = FakeGameState(quiet_conn())
            gs.diff_events = [
                lq.TurnEvent(priority=2, category="unit", message="UNIT LOST: Warrior")
            ]
            return await execute_end_turn(gs)

        assert "UNIT LOST: Warrior" in run(go())

    def test_notifications_are_included(self, fast):
        async def go():
            conn = quiet_conn()
            conn.add(
                "NOTIF|",
                ["NOTIF|NOTIFICATION_CHOOSE_TECH|Choose research|50|-1,-1", "TOTAL|1|1"],
            )
            return await execute_end_turn(FakeGameState(conn))

        assert "NOTIF: NOTIFICATION_CHOOSE_TECH" in run(go())

    def test_threat_scan_lines_become_threat_events(self, fast):
        async def go():
            conn = quiet_conn()
            conn.add(
                "THREAT|",
                ["THREAT|63|Barbarian|WARRIOR|10,20|80/100|CS:20|RS:0|dist:3|cs:0|uid:42"],
            )
            return await execute_end_turn(FakeGameState(conn))

        result = run(go())
        assert "THREAT: Barbarian WARRIOR CS:20 HP:80/100 spotted 3 tiles away at (10,20)" in result

    def test_disappeared_threat_gets_fog_directions(self, fast):
        """A visible enemy that vanishes between turns moved into fog; the
        adjacent-fog directions tell the agent which way it went."""
        async def go():
            conn = quiet_conn()
            conn.add(
                "THREAT|",
                Queue(
                    ["THREAT|63|Barbarian|WARRIOR|10,20|80/100|CS:20|RS:0|dist:3|cs:0|uid:42"],
                    [],
                ),
            )
            conn.add("FOG|", ["FOG|10,20|NE,E"])
            return await execute_end_turn(FakeGameState(conn))

        result = run(go())
        assert "LOST CONTACT: Barbarian WARRIOR" in result
        assert "likely moved NE/E into fog" in result

    def test_disappeared_threat_without_adjacent_fog_vanished(self, fast):
        async def go():
            conn = quiet_conn()
            conn.add(
                "THREAT|",
                Queue(
                    ["THREAT|63|Barbarian|WARRIOR|10,20|80/100|CS:20|RS:0|dist:3|cs:0|uid:42"],
                    [],
                ),
            )
            conn.add("FOG|", [])
            return await execute_end_turn(FakeGameState(conn))

        result = run(go())
        assert "VANISHED: Barbarian WARRIOR" in result
        assert "no adjacent fog" in result

    def test_growth_alerts_come_from_the_post_turn_snapshot(self, fast):
        async def go():
            gs = FakeGameState(quiet_conn())
            gs.snapshot = lq.TurnSnapshot(
                turn=50,
                units={},
                cities={
                    1: make_city_snap(city_id=1, name="Rome", food_surplus=-2.0),
                    2: make_city_snap(
                        city_id=2, name="Antium", food_surplus=0.0, turns_to_grow=0
                    ),
                    3: make_city_snap(
                        city_id=3, name="Ostia", food_surplus=1.0, turns_to_grow=20
                    ),
                },
                current_research="",
                current_civic="",
            )
            return await execute_end_turn(gs)

        result = run(go())
        assert "STARVING: Rome (-2.0 food/t)" in result
        assert "STAGNANT: Antium (0 food surplus)" in result
        assert "SLOW GROWTH: Ostia (20t to next pop" in result

    def test_victory_proximity_events_are_included(self, fast):
        async def go():
            conn = quiet_conn()
            conn.add("DIPLO_THREAT", ["DIPLO_THREAT|Egypt|19"])
            return await execute_end_turn(FakeGameState(conn))

        assert "DIPLOMATIC VICTORY THREAT: Egypt has 19/20 DVP" in run(go())

    def test_ten_turn_victory_snapshot_fires_on_multiples_of_ten(self, fast, monkeypatch):
        monkeypatch.setattr(
            "civ_mcp.narrate.narrate_victory_progress", lambda vp: "VP SUMMARY"
        )

        async def go():
            conn = quiet_conn(turn=59)
            return await execute_end_turn(FakeGameState(conn, turn=59))

        result = run(go())
        assert result.startswith("Turn 59 -> 60")
        assert "10-TURN VICTORY SNAPSHOT (T60):" in result
        assert "VP SUMMARY" in result

    def test_no_victory_snapshot_off_the_decade(self, fast):
        async def go():
            return await execute_end_turn(FakeGameState(quiet_conn()))

        assert "10-TURN VICTORY SNAPSHOT" not in run(go())

    def test_empire_warnings_reach_the_report(self, fast):
        async def go():
            conn = quiet_conn()
            conn.add("TRCAP|", ["TRCAP|2|0"])
            return await execute_end_turn(FakeGameState(conn))

        assert "IDLE TRADE ROUTE: 2 unused route capacities" in run(go())

    def test_save_scumming_hard_stop_aborts_the_run(self, fast):
        """Integration point: the detector's hard stop must flip
        _run_aborted so every subsequent end_turn refuses."""
        async def go():
            gs = FakeGameState(quiet_conn())
            gs._save_load_history = [
                (1000.0 + i, t, f"AutoSave_{t:04d}")
                for i, t in enumerate([30, 40, 50, 60, 70, 80, 90, 100])
            ]
            first = await execute_end_turn(gs)
            second = await execute_end_turn(gs)
            return first, second, gs

        first, second, gs = run(go())
        assert "RUN ABORTED" in first
        assert gs._run_aborted is True
        assert "RUN ABORTED" in second
        assert "No further actions will be processed" in second
