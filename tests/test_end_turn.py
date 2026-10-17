"""Isolated checks for one-shot turn advancement and explicit uncertainty."""

import asyncio
from types import SimpleNamespace

import pytest

from civ_mcp import end_turn as et
from civ_mcp import lua as lq
from civ_mcp.connection import LuaError
from civ_mcp.game_state import GameState


class FakeConn:
    """Only returns scripted memory state; never connects to a game."""

    def __init__(self):
        self.turn = 50
        self.calls = []
        self.advance = True
        self.send_error = None
        self.read_error = None
        self.blocking = ["NONE"]
        self.handler = ["HANDLER_SET"]
        self.congress_phase = ["WC_PHASE|false|NONE|50|NONE"]
        self.game_over_status = ["GAME_ACTIVE"]
        self.ready = True
        self.readiness = None
        self.diplomacy_probe = ["DIPLOMACY_NONE"]
        self.ui_processing = False
        self.information_popups = ["INFORMATION_POPUPS|DONE"]

    async def execute_read(self, lua):
        self.calls.append(lua)
        if self.read_error:
            raise self.read_error
        if "Game.GetWinningTeam" in lua:
            return self.game_over_status(self) if callable(self.game_over_status) else self.game_over_status
        if "TURN_STATE|" in lua:
            ready = self.readiness(self) if self.readiness else self.ready
            return [f"TURN_STATE|{self.turn}|{"?" if ready is None else int(ready)}"]
        return [str(self.turn)]

    async def execute_write(self, lua):
        self.calls.append(lua)
        if "TURN_UI_READY|" in lua:
            processing = self.ui_processing(self) if callable(self.ui_processing) else self.ui_processing
            return [f"TURN_UI_READY|{self.turn}|{int(not processing)}"]
        if "INFORMATION_POPUPS" in lua:
            return self.information_popups
        if "DIPLOMACY_PROBE" in lua:
            return self.diplomacy_probe(self) if callable(self.diplomacy_probe) else self.diplomacy_probe
        if "WC_PHASE|" in lua:
            return self.congress_phase(self) if callable(self.congress_phase) else self.congress_phase
        if "ACTION_ENDTURN" in lua:
            if self.advance:
                self.turn += 1
            if self.send_error:
                raise self.send_error
            return ["OK:TURN_ENDED"]
        if "BLOCKING|" in lua:
            return self.blocking
        if "__civmcp_wc_handler" in lua:
            return self.handler
        if "NOTIF|" in lua:
            return ["NOTIF|NOTIFICATION_CHOOSE_TECH|Choose research|51|-1,-1", "TOTAL|1|1"]
        raise AssertionError(f"Unexpected write: {lua}")

    @property
    def requests(self):
        return [c for c in self.calls if "ACTION_ENDTURN" in c]


class FakeGameState:
    _pending_end_turn = GameState._pending_end_turn
    _pending_end_turn_from = GameState._pending_end_turn_from
    turn_pending = GameState.turn_pending
    capture_turn_snapshot = GameState.capture_turn_snapshot
    report_turn_advance = GameState.report_turn_advance
    end_turn = GameState.end_turn

    def __init__(self):
        self._turn_progress = et.TurnProgress()
        self.conn = FakeConn()
        self._pending_end_turn = False
        self._pending_end_turn_from = None
        self._last_snapshot = None
        self.sessions = []
        self.sessions_calls = 0
        self.sessions_error = None
        self.deals = []
        self.wc = SimpleNamespace(is_in_session=False, turns_until_next=5, resolutions=[])
        self.game_over = None
        self.snapshot_error = False
        self.diff_baselines = []
        self.pending_action_summary = None
        self.action_confirmed = False

    async def confirm_pending_action(self):
        if self.action_confirmed:
            self.pending_action_summary = None
            return "CONFIRMED|move"
        return "ERR:OUTCOME_UNKNOWN|move unit 123"

    async def get_diplomacy_sessions(self):
        self.sessions_calls += 1
        if self.sessions_error:
            raise self.sessions_error
        return self.sessions

    async def get_pending_deals(self):
        return self.deals

    async def get_world_congress(self):
        return self.wc

    async def check_game_over(self):
        return self.game_over

    async def _take_snapshot(self):
        if self.snapshot_error:
            raise LuaError("Snapshot unavailable")
        return lq.TurnSnapshot(self.conn.turn, {}, {}, "", "")

    def _diff_snapshots(self, before, after):
        self.diff_baselines.append((before.turn, after.turn))
        return [lq.TurnEvent(2, "city", "Rome population increased")]

    def _build_turn_report(self, before, after, events, notifications, stockpiles=None):
        lines = [f"Turn {before} -> {after}"]
        lines += [event.message for event in events]
        lines += [notification.message for notification in notifications]
        return "\n".join(lines)


@pytest.fixture
def clock(monkeypatch):
    """Advance a local clock so timeout paths run without real waiting."""
    timer = SimpleNamespace(now=0.0, sleeps=[])

    async def sleep(seconds):
        timer.sleeps.append(seconds)
        timer.now += seconds

    monkeypatch.setattr(et.time, "monotonic", lambda: timer.now)
    monkeypatch.setattr(et.asyncio, "sleep", sleep)
    return timer


def play(gs):
    return asyncio.run(gs.end_turn())


def session(**changes):
    defaults = dict(other_civ_name="Rome", other_leader_name="Trajan", other_player_id=2,
                    dialogue_text="Proposal", reason_text="", deal_summary="", is_at_war=False)
    return SimpleNamespace(**(defaults | changes))


def test_advancement_uses_one_request_and_actual_snapshot_difference(clock):
    gs = FakeGameState()
    result = play(gs)
    assert "Turn 50 -> 51" in result
    assert "Rome population increased" in result
    assert "Choose research" in result
    assert gs.diff_baselines == [(50, 51)]
    assert len(gs.conn.requests) == 1
    assert gs._pending_end_turn is False
    assert gs._pending_end_turn_from is None
    assert not any("Network.SaveGame" in c for c in gs.conn.calls)


@pytest.mark.parametrize("blocker", [
    "CONSIDER_RAZE_CITY", "CONSIDER_DISLOYAL_CITY", "UNIT_PROMOTION",
    "WORLD_CONGRESS_SESSION", "GIVE_INFLUENCE_TOKEN", "PRODUCTION", "UNKNOWN",
])
def test_strategy_blockers_do_not_trigger_automatic_choices(clock, blocker):
    gs = FakeGameState()
    gs.conn.blocking = [f"BLOCKING|ENDTURN_BLOCKING_{blocker}|Player choice required"]
    assert "Cannot end turn" in play(gs)
    assert not gs.conn.requests
    assert not gs._pending_end_turn
    assert len([c for c in gs.conn.calls if "BLOCKING|" not in c and "GetCurrentGameTurn" not in c]) == 0


@pytest.mark.parametrize("response", [[], ["UNKNOWN"], ["unrelated output"]])
def test_unknown_blocker_query_does_not_mean_no_blockers(clock, response):
    gs = FakeGameState()
    gs.conn.blocking = response
    assert "Cannot confirm" in play(gs)
    assert not gs.conn.requests


def test_diplomacy_requires_explicit_response(clock):
    gs = FakeGameState()
    gs.sessions = [session()]
    assert "diplomacy encounter" in play(gs)
    assert not gs.conn.requests


def test_world_congress_requires_registered_player_votes(clock):
    gs = FakeGameState()
    gs.wc.is_in_session = True
    gs.conn.handler = ["NO_HANDLER"]
    gs.conn.congress_phase = ["WC_PHASE|true|STAGE1|50|NONE"]
    assert "queue_wc_votes" in play(gs)
    assert not gs.conn.requests


@pytest.mark.parametrize("error", [ConnectionError("offline"), LuaError("unknown"), TimeoutError("timeout")])
def test_failed_prerequisites_do_not_send_request(clock, error):
    gs = FakeGameState()
    gs.sessions_error = error
    assert "Cannot confirm" in play(gs)
    assert not gs.conn.requests


def test_unknown_current_turn_prevents_send(clock):
    gs = FakeGameState()
    gs.conn.read_error = LuaError("read unavailable")
    assert "current turn is unknown" in play(gs)
    assert not gs.conn.requests


def test_send_exception_keeps_pending_before_confirmation(clock):
    gs = FakeGameState()
    gs.conn.send_error = ConnectionError("reply lost")
    first = play(gs)
    assert "result unknown" in first
    assert gs._pending_end_turn
    assert gs._pending_end_turn_from == 50
    assert gs._last_snapshot.turn == 50
    second = play(gs)
    assert "Turn 50 -> 51" in second
    assert len(gs.conn.requests) == 1


def test_timeout_budget_is_three_minutes_and_pending_is_never_resent(clock):
    gs = FakeGameState()
    gs.conn.advance = False
    first = play(gs)
    assert "HANG:" in first
    assert "after 180s" in first
    assert clock.now == 180
    assert gs._pending_end_turn
    assert gs._pending_end_turn_from == 50
    assert len(gs.conn.requests) == 1
    assert gs.sessions_calls == 1  # Full UI dialogue only during preflight.
    second = play(gs)
    assert "HANG:" in second
    assert clock.now == 360
    assert len(gs.conn.requests) == 1


def test_pending_request_can_confirm_without_querying_in_game_blockers(clock):
    gs = FakeGameState()
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 49
    gs._turn_progress.baseline = lq.TurnSnapshot(49, {}, {}, "", "")
    gs.sessions_error = AssertionError("Must not re-enter preflight while AI is processing")
    assert "Turn 49 -> 50" in play(gs)
    assert not gs.conn.requests
    assert gs.diff_baselines == [(49, 50)]


def test_poll_query_failure_is_reported_and_preserves_pending(clock):
    gs = FakeGameState()
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 50
    gs.conn.read_error = LuaError("GameCore unavailable")
    assert "cannot be reliably confirmed" in play(gs)
    assert gs._pending_end_turn
    assert not gs.conn.requests


def test_mid_turn_diplomacy_never_replays_or_makes_war_choice(clock):
    gs = FakeGameState()
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 50
    gs.conn.diplomacy_probe = ["DIPLOMACY|2"]
    result = play(gs)
    assert "Turn paused" in result
    assert "player_id=2" in result
    assert clock.now == 10
    assert gs._pending_end_turn
    assert not gs.conn.requests
    assert not any("AddResponse" in c or "CloseSession" in c for c in gs.conn.calls)
    assert all("ContextPtr" not in c or "INFORMATION_POPUPS" in c for c in gs.conn.calls)


def test_turn_regression_does_not_clear_pending(clock):
    gs = FakeGameState()
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 51
    assert "turn changed from 51 to 50" in play(gs)
    assert gs._pending_end_turn
    assert not gs.conn.requests


def test_game_over_returns_without_sending_request(clock):
    gs = FakeGameState()
    gs.game_over = SimpleNamespace(victory_type="VICTORY_CULTURE", is_defeat=False)
    assert "GAME OVER — VICTORY" in play(gs)
    assert not gs.conn.requests


def test_missing_snapshots_do_not_obscure_confirmed_advance(clock):
    gs = FakeGameState()
    gs.snapshot_error = True
    result = play(gs)
    assert "Turn 50 -> 51" in result
    assert "snapshot unavailable" in result
    assert gs._pending_end_turn is False


def test_polling_hard_timeout_includes_slow_read(monkeypatch):
    # Use real asyncio here: a read that never returns must consume the same
    # deadline as polling, rather than adding its own timeout afterward.
    gs = FakeGameState()
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 50

    async def stuck_read(lua):
        await asyncio.Event().wait()

    gs.conn.execute_read = stuck_read
    monkeypatch.setattr(et, "END_TURN_TIMEOUT_SECONDS", 0.01)
    assert "HANG:" in play(gs)
    assert gs._pending_end_turn
    assert not gs.conn.requests


def test_pending_terminal_game_is_confirmed_without_turn_advance(clock):
    gs = FakeGameState()
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 50
    gs.conn.game_over_status = ["GAME_OVER|DEFEAT|Rome|Unknown|alive|Trajan|2"]
    result = play(gs)
    assert "GAME OVER — DEFEAT" in result
    assert "Trajan of Rome" in result
    assert gs._pending_end_turn is False
    assert gs._pending_end_turn_from is None
    assert not gs.conn.requests
    assert gs.sessions_calls == 0
    assert not any("ContextPtr" in code for code in gs.conn.calls)


def test_terminal_game_during_polling_does_not_wait_for_timeout(clock):
    gs = FakeGameState()
    gs.conn.advance = False
    gs.conn.game_over_status = lambda conn: (
        ["GAME_OVER|VICTORY|Rome|Unknown|alive|Trajan|0"]
        if clock.now >= 10 else ["GAME_ACTIVE"]
    )
    result = play(gs)
    assert "GAME OVER — VICTORY" in result
    assert clock.now == 10
    assert len(gs.conn.requests) == 1
    assert gs._pending_end_turn is False


def test_unknown_gamecore_terminal_status_does_not_invent_game_over(clock):
    gs = FakeGameState()
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 50
    gs.conn.game_over_status = ["UNKNOWN"]
    result = play(gs)
    assert "HANG:" in result
    assert "cannot be reliably confirmed" in result
    assert gs._pending_end_turn
    assert not gs.conn.requests


def test_turn_counter_advances_before_local_player_is_ready(clock):
    gs = FakeGameState()
    gs.conn.readiness = lambda conn: not conn.requests or clock.now >= 6
    result = play(gs)
    assert "Turn 50 -> 51" in result
    assert clock.now >= 6
    assert len(gs.conn.requests) == 1


def test_inactive_local_turn_does_not_submit_new_request(clock):
    gs = FakeGameState()
    gs.conn.ready = False
    assert "not ready" in play(gs)
    assert not gs.conn.requests


def test_unconfirmed_action_prevents_turn_submission(clock):
    gs = FakeGameState()
    gs.pending_action_summary = "move unit 123"
    result = play(gs)
    assert "OUTCOME_UNKNOWN" in result
    assert "unit 123" in result
    assert not gs.conn.requests


def test_confirmed_pending_action_allows_turn_submission(clock):
    gs = FakeGameState()
    gs.pending_action_summary = "move unit 123"
    gs.action_confirmed = True
    assert "Turn 50 -> 51" in play(gs)
    assert gs.pending_action_summary is None
    assert len(gs.conn.requests) == 1


def test_diplomacy_appearing_after_first_probe_is_found_without_replay(clock):
    gs = FakeGameState()
    gs.conn.advance = False
    gs.conn.diplomacy_probe = lambda conn: (
        ["DIPLOMACY|2"] if clock.now >= 20 else ["DIPLOMACY_NONE"]
    )
    result = play(gs)
    assert "Turn paused" in result
    assert "player_id=2" in result
    assert clock.now < 45
    assert gs._pending_end_turn
    assert len(gs.conn.requests) == 1


def test_message_processing_must_finish_after_turn_activation(clock):
    gs = FakeGameState()
    gs.conn.ui_processing = lambda conn: bool(conn.requests) and clock.now < 4
    assert "Turn 50 -> 51" in play(gs)
    assert clock.now >= 4
    assert len(gs.conn.requests) == 1


def test_readiness_unknown_keeps_request_pending_until_later_confirmation(clock):
    gs = FakeGameState()
    gs.conn.readiness = lambda conn: True if not conn.requests else None
    first = play(gs)
    assert "HANG:" in first
    assert gs._pending_end_turn
    assert len(gs.conn.requests) == 1
    gs.conn.readiness = None
    second = play(gs)
    assert "Turn 50 -> 51" in second
    assert len(gs.conn.requests) == 1


def test_external_turn_advance_during_inspection_waits_without_new_request(clock):
    gs = FakeGameState()
    async def snapshot():
        gs.conn.turn = 51
        return lq.TurnSnapshot(50, {}, {}, "", "")
    gs._take_snapshot = snapshot
    gs.conn.readiness = lambda conn: conn.turn == 50 or clock.now >= 4
    assert "Turn 50 -> 51" in play(gs)
    assert clock.now >= 4
    assert not gs.conn.requests


def test_diplomacy_resume_confirms_original_request_only(clock):
    gs = FakeGameState()
    gs.conn.advance = False
    gs.conn.diplomacy_probe = ["DIPLOMACY|2"]
    assert "Turn paused" in play(gs)
    gs.conn.diplomacy_probe = ["DIPLOMACY_NONE"]
    gs.conn.turn = 51
    assert "Turn 50 -> 51" in play(gs)
    assert len(gs.conn.requests) == 1
    assert not any("AddResponse" in code or "CloseSession" in code for code in gs.conn.calls)


def test_probe_queries_count_against_total_deadline(clock):
    gs = FakeGameState()
    gs.conn.advance = False
    def slow_probe(conn):
        clock.now += 25
        return ["DIPLOMACY_NONE"]
    gs.conn.diplomacy_probe = slow_probe
    assert "HANG:" in play(gs)
    assert 180 <= clock.now <= 205
    assert len(gs.conn.requests) == 1


@pytest.mark.parametrize("ready", [True, False, None])
def test_visible_disaster_pauses_promptly_without_replay_or_dismissal(clock, ready):
    gs = FakeGameState()
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 49
    gs.conn.ready = ready
    gs.conn.ui_processing = True
    gs.conn.information_popups = ["INFORMATION_POPUPS|VISIBLE|NaturalDisasterPopup", "INFORMATION_POPUPS|DONE"]
    result = play(gs)
    assert "NaturalDisasterPopup" in result
    assert "dismiss_popup" in result
    assert "requested from turn 49" in result
    assert clock.now <= 15
    assert gs._pending_end_turn
    assert gs._pending_end_turn_from == 49
    assert not gs.conn.requests
    assert not any("pcall(Close)" in c or "SetHide" in c for c in gs.conn.calls)


def test_diplomacy_takes_priority_when_information_popup_is_also_visible(clock):
    gs = FakeGameState()
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 49
    gs.conn.ui_processing = True
    gs.conn.diplomacy_probe = ["DIPLOMACY|2"]
    gs.conn.information_popups = ["INFORMATION_POPUPS|VISIBLE|NaturalDisasterPopup", "INFORMATION_POPUPS|DONE"]
    result = play(gs)
    assert "diplomacy response required" in result
    assert gs._pending_end_turn
    assert not gs.conn.requests


def test_popup_closure_confirms_original_request_without_another_submission(clock):
    gs = FakeGameState()
    gs.conn.ui_processing = lambda conn: bool(conn.requests)
    gs.conn.information_popups = [
        "INFORMATION_POPUPS|VISIBLE|NaturalDisasterPopup",
        "INFORMATION_POPUPS|VISIBLE|HistoricMoments", "INFORMATION_POPUPS|DONE",
    ]
    first = play(gs)
    assert "NaturalDisasterPopup, HistoricMoments" in first
    assert gs._pending_end_turn and len(gs.conn.requests) == 1
    gs.conn.information_popups = ["INFORMATION_POPUPS|DONE"]
    gs.conn.ui_processing = False
    assert "Turn 50 -> 51" in play(gs)
    assert not gs._pending_end_turn and len(gs.conn.requests) == 1


@pytest.mark.parametrize("visibility", [[], ["INFORMATION_POPUPS|UNKNOWN"],
    ["INFORMATION_POPUPS|VISIBLE|UnrecognizedPopup", "INFORMATION_POPUPS|DONE"]])
def test_unknown_popup_visibility_never_authorizes_advancement(clock, visibility):
    gs = FakeGameState()
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 49
    gs.conn.ui_processing = True
    gs.conn.information_popups = visibility
    result = play(gs)
    assert "HANG:" in result and "queries failed" in result
    assert gs._pending_end_turn and not gs.conn.requests


def test_visible_disaster_is_reported_when_ui_turn_temporarily_lags(clock):
    gs = FakeGameState()
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 49
    original = gs.conn.execute_write
    async def delayed_ui(lua):
        if "TURN_UI_READY|" in lua:
            return ["TURN_UI_READY|49|1"]
        return await original(lua)
    gs.conn.execute_write = delayed_ui
    gs.conn.information_popups = ["INFORMATION_POPUPS|VISIBLE|NaturalDisasterPopup", "INFORMATION_POPUPS|DONE"]
    assert "NaturalDisasterPopup" in play(gs)
    assert clock.now <= 15 and gs._pending_end_turn
    assert not gs.conn.requests


def test_pending_turn_reports_midturn_world_congress_without_resending(clock):
    gs = FakeGameState()
    gs.conn.turn = 67
    gs.conn.ready = False
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 67
    gs.wc.is_in_session = True
    gs.conn.congress_phase = ["WC_PHASE|true|STAGE1|67|NONE"]
    result = play(gs)
    assert "World Congress" in result and "queue_wc_votes" in result
    assert not gs.conn.requests
    assert clock.now < 10


@pytest.mark.parametrize("phase", ["WC_PHASE|true|UNKNOWN|67|NONE", "WC_PHASE|true|STAGE1|67|SUBMIT_REQUESTED", "WC_PHASE|true|STAGE1|67|FAILED"])
def test_uncertain_congress_phase_is_reported_without_replaying_or_guessing(phase, clock):
    gs = FakeGameState()
    gs.conn.ready = False
    gs.conn.turn = 67
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 67
    gs.conn.congress_phase = [phase]
    assert "World Congress phase/result unknown" in play(gs)
    assert clock.now < 10 and not gs.conn.requests


def test_requested_congress_continuation_only_observes_original_turn_until_ready(clock):
    gs = FakeGameState()
    gs.conn.turn = 67
    gs.conn.ready = False
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 67

    def phase(conn):
        conn.turn, conn.ready = 68, True
        return ["WC_PHASE|true|STAGE1|67|CONTINUE_REQUESTED"]

    gs.conn.congress_phase = phase
    assert "Turn 67 -> 68" in play(gs)
    assert not gs.conn.requests
    assert not gs._pending_end_turn


def test_malformed_congress_read_reports_unknown_instead_of_claiming_no_session(clock):
    gs = FakeGameState()
    gs.conn.ready = False
    gs._pending_end_turn = True
    gs._pending_end_turn_from = 50
    gs.conn.congress_phase = ["WC_PHASE|truncated"]
    result = play(gs)
    assert "World Congress phase unavailable" in result
    assert clock.now < 10 and not gs.conn.requests


class GameStateConn(FakeConn):
    """The native protocol, exercised through the real GameState interface."""

    def __init__(self):
        super().__init__()
        self.snapshot_turn = None
        self.snapshot_error = False
        self.notification_error = False

    async def execute_write(self, lua):
        if lua == lq.build_overview_query():
            self.calls.append(lua)
            if self.snapshot_error:
                raise LuaError("Snapshot unavailable")
            turn = self.turn if self.snapshot_turn is None else self.snapshot_turn
            research = "Writing" if turn == 50 else "Currency"
            return [f"{turn}|0|Babylon|Hammurabi|100|5|10|5|0|{research}|Craftsmanship|0|0|20"]
        responses = {
            lq.build_gameover_check(): ["GAME_ACTIVE"],
            lq.build_diplomacy_session_query(): ["NONE"],
            lq.build_pending_deals_query(): ["DEALS_COMPLETE"],
            lq.build_units_query(): [],
            lq.build_cities_query(): [],
            lq.build_stockpile_query(): [],
        }
        if lua in responses:
            self.calls.append(lua)
            return responses[lua]
        if lua == lq.build_notifications_query() and self.notification_error:
            self.calls.append(lua)
            raise LuaError("Notifications unavailable")
        return await super().execute_write(lua)


def test_real_game_state_keeps_original_baseline_when_overview_observes_pending_turn(clock):
    conn = GameStateConn()
    gs = GameState(conn)
    conn.send_error = ConnectionError("reply lost")
    assert "result unknown" in asyncio.run(gs.end_turn())
    # An ordinary observation can refresh its cache but cannot rewrite the
    # submitted request's turn-50 facts.
    asyncio.run(gs.get_game_overview(require_snapshot=True))
    report = asyncio.run(gs.end_turn())
    assert "Turn 50 -> 51" in report
    assert "Research complete: Writing! Now: Currency." in report
    assert len(conn.requests) == 1


@pytest.mark.parametrize("snapshot_turn", [50, 52])
def test_real_game_state_does_not_describe_stale_or_future_snapshot_as_confirmed_turn(clock, snapshot_turn):
    conn = GameStateConn()
    gs = GameState(conn)
    conn.send_error = ConnectionError("reply lost")
    assert "result unknown" in asyncio.run(gs.end_turn())
    conn.snapshot_turn = snapshot_turn
    report = asyncio.run(gs.end_turn())
    assert "Turn 50 -> 51" in report
    assert "snapshot unavailable" in report
    assert "Research complete:" not in report
    assert not gs.turn_pending
    assert len(conn.requests) == 1


@pytest.mark.parametrize("failure", ["snapshot", "notifications", "both"])
def test_real_game_state_report_failure_keeps_confirmed_request_retired(clock, failure):
    conn = GameStateConn()
    gs = GameState(conn)
    conn.send_error = ConnectionError("reply lost")
    assert "result unknown" in asyncio.run(gs.end_turn())
    conn.snapshot_error = failure in {"snapshot", "both"}
    conn.notification_error = failure in {"notifications", "both"}
    report = asyncio.run(gs.end_turn())
    assert "Turn 50 -> 51" in report
    assert "unavailable" in report
    assert not gs.turn_pending
    conn.ready = False
    assert "not ready" in asyncio.run(gs.end_turn())
    assert len(conn.requests) == 1


def test_real_game_state_unknown_request_blocks_actions_and_saves_until_ready(clock):
    conn = GameStateConn()
    gs = GameState(conn)
    conn.readiness = lambda conn: not conn.requests
    assert "after 180s" in asyncio.run(gs.end_turn())
    assert gs.turn_pending
    calls = len(conn.calls)
    assert "End-turn outcome is pending" in asyncio.run(gs.move_unit(123, 4, 5))
    assert "End-turn outcome is pending" in asyncio.run(gs.save_game("offline_turn_test"))
    assert len(conn.calls) == calls
    conn.readiness = None
    assert "Turn 50 -> 51" in asyncio.run(gs.end_turn())
    assert not gs.turn_pending
    assert len(conn.requests) == 1


@pytest.mark.parametrize("pause", ["diplomacy", "congress", "disaster"])
def test_real_game_state_resumes_paused_request_without_resubmitting(clock, pause):
    conn = GameStateConn()
    gs = GameState(conn)
    conn.readiness = lambda conn: not conn.requests
    if pause == "diplomacy":
        conn.diplomacy_probe = ["DIPLOMACY|2"]
    elif pause == "congress":
        conn.congress_phase = lambda conn: [
            f"WC_PHASE|true|STAGE1|{conn.turn}|NONE" if conn.requests
            else "WC_PHASE|false|NONE|50|NONE"
        ]
    else:
        conn.information_popups = ["INFORMATION_POPUPS|VISIBLE|NaturalDisasterPopup", "INFORMATION_POPUPS|DONE"]
    assert "Turn paused" in asyncio.run(gs.end_turn())
    assert gs.turn_pending
    conn.readiness = None
    conn.diplomacy_probe = ["DIPLOMACY_NONE"]
    conn.congress_phase = ["WC_PHASE|false|NONE|51|NONE"]
    conn.information_popups = ["INFORMATION_POPUPS|DONE"]
    report = asyncio.run(gs.end_turn())
    assert "Turn 50 -> 51" in report
    assert "Research complete: Writing! Now: Currency." in report
    assert not gs.turn_pending
    assert len(conn.requests) == 1
