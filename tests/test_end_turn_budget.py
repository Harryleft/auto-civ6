"""One ``end_turn`` call must fit inside the host tool-call deadline.

``integrations/deepseek-harness/civ6.cordis.yml`` allowed 900 s while
``civ_mcp.end_turn``'s own poll constants already summed to 1210 s on a World
Congress turn. A congress turn could therefore outlive the deadline that wraps
it and be killed mid-advance — an outcome the agent cannot tell apart from "the
turn never advanced", and the reason a bare 900→1300 bump would not have been a
fix.

These tests pin the two sides together:

* the wait loop is executed against a **virtual clock**, so the derived budget
  is verified against the code instead of asserted by hand;
* the host deadline in the DSH overlay is checked against that derived budget;
* the enforced ceiling returns a machine-readable "outcome unknown" receipt
  rather than letting the host kill the call;
* a hang-recovery restart is never *started* when the remaining budget cannot
  pay for it.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
from types import SimpleNamespace

import pytest

from civ_mcp import end_turn as et
from civ_mcp.server.tools import end_turn_flow
from civ_mcp.read_cache import ReadCache
from civ_mcp.server import pipeline
from civ_mcp.server.assembly import PlayProfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "integrations" / "deepseek-harness" / "civ6.cordis.yml"

_DSH_TIMEOUT = re.compile(r"^\s*toolCallTimeoutMs:\s*(\d+)\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# A virtual clock for the real wait loop
# ---------------------------------------------------------------------------


class _VirtualClock:
    """Replace ``end_turn._sleep`` so no test waits for real minutes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.elapsed = 0.0
        self.slept: list[float] = []

        async def sleep(seconds: float) -> None:
            self.slept.append(seconds)
            self.elapsed += seconds

        monkeypatch.setattr(et, "_sleep", sleep)
        monkeypatch.setattr(et, "_monotonic", lambda: self.elapsed)


class _FakeConnection:
    """Every Lua context answers with the same harmless line."""

    def __init__(self, *, wc_handler: bool) -> None:
        self._wc_handler = wc_handler
        self.sends = 0

    async def execute_read(self, code: str, **_kwargs) -> list[str]:
        if "GetCurrentGameTurn" in code:
            # A readable turn number is what makes the HANG branch reachable at
            # all; without it the loop cannot report "still on turn N".
            return ["57"]
        return ["NO_THREATS"]

    async def execute_write(self, _code: str, **_kwargs) -> list[str]:
        return ["HANDLER_SET"] if self._wc_handler else ["NO_THREATS"]

    async def execute_mutation(self, code: str, **_kwargs) -> list[str]:
        if "ACTION_ENDTURN" in code:
            self.sends += 1
        return ["OK"]


class _FakeGameState:
    """A game whose turn never advances and whose queries never block."""

    def __init__(self, *, wc_turn: bool) -> None:
        self.conn = _FakeConnection(wc_handler=wc_turn)
        self._cache_epoch = 0
        # A real cache object so GameState.invalidate_cached_state() runs for
        # real: the reload path is what resets the in-flight end-turn request.
        self._reads = ReadCache(lambda: None)
        self._run_aborted = False
        self._hang_retry_active = False
        self._high_water_turn = 0
        self._pending_end_turn = False
        self._pending_end_turn_from = None
        self._last_snapshot = None
        # Counted so the budget test can check its arithmetic against what the
        # loop really did instead of a hand-maintained total.
        self.drive_calls = 0
        self.dismiss_calls = 0

    async def check_game_over(self):
        return None

    async def get_diplomacy_sessions(self, **_kwargs) -> list:
        return []

    async def get_pending_deals(self) -> list:
        return []

    async def get_barbarian_overview(self):
        return SimpleNamespace(units=[], camps=[])

    async def get_units(self) -> list:
        return []

    async def dismiss_popup(self) -> str:
        self.dismiss_calls += 1
        # No stale screen in this fake: only the driver ever "acts", which keeps
        # the probe-sleep arithmetic in the budget test unambiguous.
        return "No popups to dismiss."

    async def drive_world_congress(self, **_kwargs) -> str:
        self.drive_calls += 1
        # A submitted congress round is the worst case: the probe actually did
        # something, so the caller also pays the follow-up re-check sleep.
        if self.conn._wc_handler:
            return "WC_DRIVE|submitted|spent:0|123:WC_RES_LUXURY:1:0:1:0:type"
        return "WC_DRIVE|no_session"

    async def get_world_congress(self):
        # turns_until_next <= 0 with resolutions opens the congress gate.
        return SimpleNamespace(
            turns_until_next=0 if self.conn._wc_handler else 5,
            is_in_session=False,
            resolutions=["RES"] if self.conn._wc_handler else [],
            favor=0,
        )

    async def _take_snapshot(self):
        # The pre-turn snapshot is optional in execute_end_turn; raising keeps
        # this fake to the polling path and leaves turn_before unknown.
        raise RuntimeError("no snapshot in the budget test")

    async def end_turn(self) -> str:  # pragma: no cover
        # Only referenced (never awaited) by the budget tests, which stub
        # pipeline._logged; kept so the attribute lookup cannot fail.
        return "end_turn stub"

    def _build_turn_report(self, turn_before, turn_after, *_a, **_k) -> str:
        # Reached only when a test makes the turn actually advance.
        return f"Turn {turn_before} -> {turn_after}"

    def _read_cache(self):
        # GameState.invalidate_cached_state() calls this; the fake carries a real
        # ReadCache so the reload path can be exercised for real.
        return self._reads

    async def get_notifications(self) -> list:
        return []

    async def get_empire_resources(self):
        return [], [], [], []


def _allowed_probe_sleeps(wc_turn: bool) -> float:
    """Probe re-check sleeps the budget reserves for a congress turn."""

    return et._WC_PROBE_SLEEP_SECONDS if wc_turn else 0.0


@pytest.mark.parametrize("wc_turn", [False, True])
def test_wait_loop_stays_inside_the_derived_poll_budget(
    monkeypatch: pytest.MonkeyPatch, wc_turn: bool
) -> None:
    """Run the real loop on a virtual clock; nothing may be unaccounted for."""

    clock = _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=wc_turn)

    asyncio.run(et.execute_end_turn(gs))

    # Every congress drive that submitted paid a 2 s re-check.
    acted_sleeps = gs.drive_calls * 2.0
    expected = (
        et._PHASE1_SLEEP_SECONDS
        + sum(et._end_turn_poll_delays(wc_turn))
        + acted_sleeps
        + et._FINAL_VERIFY_SLEEP_SECONDS
    )
    assert clock.elapsed == expected
    # The loop can never outrun the budget it is derived from.
    assert clock.elapsed <= et.poll_sleep_budget_seconds(wc_turn)
    # The headroom is exactly the bounded branches this pass did not take: the
    # diplomacy probe's dialogue/war wait, the popup-dismiss re-poll, and the
    # congress probes the schedule did not reach.
    assert et.poll_sleep_budget_seconds(wc_turn) - clock.elapsed == (
        et._DIPLOMACY_PROBE_SLEEP_SECONDS
        + et._POPUP_REPOLL_SLEEP_SECONDS
        + (_allowed_probe_sleeps(wc_turn) - acted_sleeps)
    )


def test_a_congress_turn_drives_the_session_instead_of_waiting_it_out(monkeypatch) -> None:
    """The old schedule started at t+60s with 3 tries; a late session escaped it."""

    recorded: list[float] = []
    elapsed = {"t": 0.0}

    async def sleep(seconds: float) -> None:
        recorded.append(seconds)
        elapsed["t"] += seconds

    monkeypatch.setattr(et, "_monotonic", lambda: elapsed["t"])
    original_sleep = et._sleep
    et._sleep = sleep
    try:
        class LateCongress(_FakeGameState):
            async def drive_world_congress(self, **_kwargs):
                self.drive_calls += 1
                return "WC_DRIVE|submitted" if self.drive_calls >= 5 else "WC_DRIVE|no_session"

        gs = LateCongress(wc_turn=True)
        asyncio.run(et.execute_end_turn(gs))
    finally:
        et._sleep = original_sleep

    total = et._WC_DRIVE_BURST_PROBES + et._WC_DRIVE_SPARSE_PROBES
    assert gs.drive_calls >= 5, "议会回合必须持续尝试驱动，而不是只试三次"
    assert gs.drive_calls <= total
    # The first attempt happens within seconds of the turn being requested.
    first_attempt_at = et._PHASE1_SLEEP_SECONDS + min(recorded) if recorded else None
    assert gs.drive_calls > 0 and first_attempt_at is not None


def test_the_congress_extra_no_longer_pays_for_a_passive_wait() -> None:
    """+660s only existed to sit out a congress screen nobody was operating."""

    plain = sum(et._end_turn_poll_delays(False))
    congress = sum(et._end_turn_poll_delays(True))

    assert congress - plain <= 240.0, (
        "议会回合的额外被动等待必须只是少量余量；驱动才是快路径"
    )




# ---------------------------------------------------------------------------
# The host deadline must cover the derived budget
# ---------------------------------------------------------------------------


def test_host_deadline_covers_the_whole_end_turn_budget() -> None:
    match = _DSH_TIMEOUT.search(OVERLAY.read_text(encoding="utf-8"))

    assert match, "civ6.cordis.yml no longer sets an explicit toolCallTimeoutMs"
    host_ms = int(match.group(1))
    longest = et.longest_agent_call_seconds()
    budget = et.end_turn_budget(wc_turn=True)

    assert host_ms > longest * 1000, (
        f"DSH 允许 {host_ms / 1000:.0f}s，但最长单次调用为 {longest:.0f}s"
        f"（end_turn 轮询 {budget.poll_seconds:.0f}s + 查询 "
        f"{budget.query_seconds:.0f}s）；"
        "宿主期限必须大于最长单次回合调用，否则会在推进中途被杀死"
    )
    # Three minutes, not fifty: measured turns are median 15.5s / p97.6 90s.
    assert host_ms <= 3 * 60 * 1000


def test_budget_stays_inside_the_three_minute_window() -> None:
    plain = et.end_turn_budget(wc_turn=False)
    congress = et.end_turn_budget(wc_turn=True)

    # Congress costs a little more (the bounded drive/dismiss re-checks) but no
    # longer buys a longer single call.
    assert congress.poll_seconds > plain.poll_seconds
    assert congress.total_seconds <= 3 * 60
    assert et.longest_agent_call_seconds() == congress.total_seconds


def test_the_drive_schedule_fits_inside_the_cumulative_hang_threshold() -> None:
    """The schedule spans calls, so it is bounded by total waiting, not a window."""

    window = et.PENDING_TURN_HANG_AFTER_SECONDS
    last_threshold = (
        et._WC_FIRST_DRIVE_AFTER
        + et._WC_DRIVE_BURST_PROBES * et._WC_DRIVE_BURST_INTERVAL
        + (et._WC_DRIVE_SPARSE_PROBES - 1) * et._WC_DRIVE_SPARSE_INTERVAL
    )

    assert last_threshold < window


# ---------------------------------------------------------------------------
# The recovery reserve must match the retries the flow really performs
# ---------------------------------------------------------------------------


def test_recovery_is_not_part_of_the_end_turn_budget() -> None:
    """Restarts used to live inside this call, which is what forced ~50 minutes."""

    budget = et.end_turn_budget(wc_turn=True)

    assert budget.total_seconds == budget.poll_seconds + budget.query_seconds
    assert not hasattr(budget, "recovery_seconds")


def test_the_host_deadline_covers_the_longest_loop_call() -> None:
    """The budget covers the turn loop; a game relaunch is an out-of-loop step."""

    longest = et.longest_agent_call_seconds()

    assert longest == et.end_turn_budget(wc_turn=True).total_seconds
    # Measured turns are median 15.5s / p90 26.3s / p97.6 90s, so a three-minute
    # ceiling is generous for the loop. Anything larger is a relaunch, not a turn.
    assert longest <= 3 * 60, (
        "单次回合调用期限必须落在三分钟以内；更长的只可能是重启游戏"
    )


def test_the_relaunch_ceiling_is_declared_but_out_of_the_loop_budget() -> None:
    """The relaunch waits on the game, so it is its own out-of-loop step."""

    relaunch = et.restart_and_load_budget_seconds()

    assert relaunch == (
        et.RESTART_AND_LOAD_CEILING_SECONDS
        + et.RESTART_RECONNECT_CEILING_SECONDS
        + et.RESTART_IDENTITY_CEILING_SECONDS
    )
    assert relaunch > et.longest_agent_call_seconds(), (
        "重启游戏比一轮回合慢，正因如此它必须留在回合循环之外"
    )
    # And the hang receipt must not send the model into that call.
    receipt = (
        ROOT / "src" / "civ_mcp" / "server" / "tools" / "end_turn_flow.py"
    ).read_text(encoding="utf-8")
    assert "restart_and_load" not in receipt.split("HANG_RECOVERY_IS_A_SEPARATE_STEP")[0].split("HANG:")[-1], (
        "挂起回执不得指示模型在本回合循环内调用 restart_and_load"
    )


def test_query_reserve_is_expressed_in_per_query_ceilings() -> None:
    from civ_mcp.connection import DEFAULT_TIMEOUT

    assert et.QUERY_CEILING_SECONDS == DEFAULT_TIMEOUT
    assert et.QUERY_RESERVE_SECONDS > 0
    # A congress pass issues on the order of a hundred queries; the reserve
    # must at least cover the poll loop's own turn-number reads.
    assert et.QUERY_RESERVE_SECONDS >= len(et._end_turn_poll_delays(True)) * (
        et.QUERY_CEILING_SECONDS
    ) / 10


# ---------------------------------------------------------------------------
# Exhaustion reports an unknown outcome; it never looks like success
# ---------------------------------------------------------------------------


def _context(game=None) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(game=game or SimpleNamespace())
        )
    )


def test_exhausted_budget_returns_unknown_and_forbids_a_resend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_finishes(*_args, **_kwargs) -> str:
        await asyncio.sleep(30)
        return "unreachable"  # pragma: no cover

    monkeypatch.setattr(end_turn_flow, "_budget_seconds", lambda: 0.01)
    monkeypatch.setattr(end_turn_flow, "_run_end_turn_impl", never_finishes)

    result = asyncio.run(end_turn_flow.run_end_turn(_context()))

    assert "UNKNOWN:END_TURN_BUDGET_EXHAUSTED" in result
    assert "结果未知" in result
    assert "只读续等" in result
    assert "不会重发" in result
    assert "get_game_overview" in result
    # It must not read as an advance, and it must not invite a retry of the
    # action whose outcome is unknown.
    assert "->" not in result
    assert "Turn " not in result


def _hang_verdict(
    monkeypatch: pytest.MonkeyPatch, *, wc_turn: bool, driven: bool
) -> tuple[str, "_FakeGameState", "_VirtualClock"]:
    """Keep calling end_turn until it stops saying "still pending".

    One call now polls for a bounded window and reports; the verdict that says
    "stuck" only appears once the *same* pending turn has been waited on past the
    cumulative threshold, which is exactly the behaviour under test.
    """

    class _GS(_FakeGameState):
        async def drive_world_congress(self, **_kwargs) -> str:
            self.drive_calls += 1
            if driven:
                return "WC_DRIVE|submitted|spent:0|1:WC_RES_LUXURY:1:0:1:0:type"
            return "WC_DRIVE|no_session"

    gs = _GS(wc_turn=wc_turn)
    clock = _VirtualClock(monkeypatch)
    result = ""
    for _ in range(12):
        result = asyncio.run(et.execute_end_turn(gs))
        if not result.startswith("TURN_PENDING:"):
            break
    return result, gs, clock


def test_expected_congress_without_a_session_reaches_bounded_diagnosis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, gs, _ = _hang_verdict(monkeypatch, wc_turn=True, driven=False)

    assert result.startswith("HANG:")
    assert gs._pending_end_turn is True
    assert gs.conn.sends == 1
    assert gs.drive_calls <= et._WC_DRIVE_BURST_PROBES + et._WC_DRIVE_SPARSE_PROBES
    assert gs._pending_end_turn_wait >= et.PENDING_TURN_HANG_AFTER_SECONDS


def test_a_single_call_reports_pending_not_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One window is ~100s; measured turns reach 90s, so one call proves nothing."""

    clock = _VirtualClock(monkeypatch)
    result = asyncio.run(et.execute_end_turn(_FakeGameState(wc_turn=False)))

    assert result.startswith("TURN_PENDING:")
    assert "TURN_PENDING_KEEP_WAITING" in result
    assert "重复调用不会重发结束回合请求" in result
    assert not result.startswith("HANG:")
    # And the window really bounded the call.
    assert clock.elapsed <= et._END_TURN_POLL_WINDOW_SECONDS + max(
        et._END_TURN_POLL_DELAYS
    )


def test_waiting_continues_across_calls_without_resending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: short calls, cumulative waiting, no duplicate request."""

    clock = _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=False)

    first = asyncio.run(et.execute_end_turn(gs))
    sends_after_first = gs.conn.sends
    second = asyncio.run(et.execute_end_turn(gs))

    assert first.startswith("TURN_PENDING:") and second.startswith("TURN_PENDING:")
    assert gs.conn.sends == sends_after_first, (
        "第二次调用不得重发 ACTION_ENDTURN，否则会跳过回合"
    )
    assert gs._pending_end_turn_wait > et._END_TURN_POLL_WINDOW_SECONDS


def test_a_plain_turn_that_never_advances_is_still_a_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, gs, _ = _hang_verdict(monkeypatch, wc_turn=False, driven=False)

    assert result.startswith("HANG:")
    assert gs._pending_end_turn_wait >= et.PENDING_TURN_HANG_AFTER_SECONDS


def test_a_congress_turn_that_was_driven_can_still_be_a_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driving removes the *false* hang; a real wedge after submitting is real."""

    result, gs, _ = _hang_verdict(monkeypatch, wc_turn=True, driven=True)

    assert gs.drive_calls > 0
    assert result.startswith("HANG:")


def test_a_hang_result_never_invites_a_resend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery moved out, so the receipt must say exactly what to do next."""

    async def fake_logged(*_args, **_kwargs) -> str:
        return "HANG:57:AutoSave_0057|AI turn processing appears stuck."

    noop = lambda *_: None  # noqa: E731
    monkeypatch.setattr(pipeline, "_logged", fake_logged)
    monkeypatch.setattr(pipeline, "_turn_context_enabled", lambda _ctx: False)
    monkeypatch.setattr(
        pipeline,
        "_get_logger",
        lambda _ctx: SimpleNamespace(
            session_id="t", set_agent_model=noop, set_turn=noop
        ),
    )
    monkeypatch.setattr(
        pipeline, "_get_spatial", lambda _ctx: SimpleNamespace(set_turn=noop)
    )

    gs = _FakeGameState(wc_turn=False)
    gs._game_identity = ("test", 1)
    result = asyncio.run(
        end_turn_flow._run_end_turn_impl(
            _context(gs), tactical="t", strategic="s", tooling="o",
            planning="p", hypothesis="h",
        )
    )

    assert "HANG_RECOVERY_IS_A_SEPARATE_STEP" in result
    assert "宿主机/操作者" in result
    # The relaunch is an out-of-loop operator sequence, never an in-loop call:
    # it waits on the game's own launch and cannot fit the loop's deadline.
    assert "kill_game → launch_game → load_game_save" in result
    assert "不要在本回合循环里尝试重启" in result
    assert "重复发送 end_turn" in result


def test_end_turn_flow_no_longer_restarts_the_game() -> None:
    """Guard the invariant at the source: a reload inside end_turn was the bug."""

    source = (
        ROOT / "src" / "civ_mcp" / "server" / "tools" / "end_turn_flow.py"
    ).read_text(encoding="utf-8")

    assert "await game_launcher.restart_and_load(" not in source
    assert "HANG_RECOVERY_IS_A_SEPARATE_STEP" in source


def test_a_continuation_call_does_not_demand_the_essays_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Waiting now spans calls; re-writing five reflections each time is ceremony.

    The first call already wrote this turn's diary entry, so a legacy-profile
    "keep waiting" retry must not be refused for empty reflections.
    """

    async def fake_logged(*_args, **_kwargs) -> str:
        return "TURN_PENDING:57|still processing"

    noop = lambda *_: None  # noqa: E731
    monkeypatch.setattr(pipeline, "_get_play_profile", lambda _ctx: PlayProfile.LEGACY)
    monkeypatch.setattr(pipeline, "_turn_context_enabled", lambda _ctx: False)
    monkeypatch.setattr(pipeline, "_logged", fake_logged)
    monkeypatch.setattr(
        pipeline, "_get_logger",
        lambda _ctx: SimpleNamespace(session_id="t", set_agent_model=noop, set_turn=noop),
    )
    monkeypatch.setattr(
        pipeline, "_get_spatial", lambda _ctx: SimpleNamespace(set_turn=noop)
    )
    monkeypatch.setattr(
        pipeline, "_get_camera", lambda _ctx: SimpleNamespace(clear=noop)
    )
    monkeypatch.setattr(
        pipeline, "_get_watchdog", lambda _ctx: SimpleNamespace(arm=noop)
    )

    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context=SimpleNamespace(
                game=_FakeGameState(wc_turn=False), play_profile=PlayProfile.LEGACY
            )
        )
    )

    # First call with no reflections and nothing in flight: still refused.
    fresh = _FakeGameState(wc_turn=False)
    ctx.request_context.lifespan_context.game = fresh
    first = asyncio.run(end_turn_flow._run_end_turn_impl(ctx))
    assert "Empty reflections" in first

    # A continuation call (request already in flight) is not.
    continuing = _FakeGameState(wc_turn=False)
    continuing._pending_end_turn = True
    ctx.request_context.lifespan_context.game = continuing
    second = asyncio.run(end_turn_flow._run_end_turn_impl(ctx))
    assert "Empty reflections" not in second


def test_lost_end_turn_ack_keeps_submission_pending(monkeypatch):
    from civ_mcp.connection import MutationOutcomeUnknownError

    _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=False)
    send = gs.conn.execute_mutation

    async def lose_ack(code, **kwargs):
        await send(code, **kwargs)
        raise MutationOutcomeUnknownError(code, TimeoutError())

    gs.conn.execute_mutation = lose_ack
    with pytest.raises(MutationOutcomeUnknownError):
        asyncio.run(et.execute_end_turn(gs))
    assert gs._pending_end_turn and gs.conn.turn_in_progress
    gs.conn.execute_mutation = send
    asyncio.run(et.execute_end_turn(gs))
    assert gs.conn.sends == 1


def test_cancelled_send_stays_pending_and_is_not_resent(monkeypatch):
    _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=False)
    original = gs.conn.execute_mutation

    async def scenario():
        sending = asyncio.Event()

        async def waiting_ack(code, **kwargs):
            await original(code, **kwargs)
            sending.set()
            await asyncio.Event().wait()

        gs.conn.execute_mutation = waiting_ack
        task = asyncio.create_task(et.execute_end_turn(gs))
        await sending.wait()
        assert gs._pending_end_turn and gs.conn.turn_in_progress
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        gs.conn.execute_mutation = original
        await et.execute_end_turn(gs)

    asyncio.run(scenario())
    assert gs.conn.sends == 1
    assert gs._pending_end_turn


def test_concurrent_end_turn_calls_share_one_submission(monkeypatch):
    _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=False)
    original = gs.conn.execute_mutation

    async def scenario():
        sending = asyncio.Event()
        release = asyncio.Event()

        async def waiting_ack(code, **kwargs):
            await original(code, **kwargs)
            sending.set()
            await release.wait()
            return ["OK"]

        gs.conn.execute_mutation = waiting_ack
        first = asyncio.create_task(et.execute_end_turn(gs))
        await sending.wait()
        second = asyncio.create_task(et.execute_end_turn(gs))
        await asyncio.sleep(0)
        release.set()
        assert await first == await second

    asyncio.run(scenario())
    assert gs.conn.sends == 1


def test_pending_popup_and_hang_never_enable_another_submission(monkeypatch):
    clock = _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=False)

    async def dismiss():
        gs.dismiss_calls += 1
        return "Dismissed popup"

    gs.dismiss_popup = dismiss
    asyncio.run(et.execute_end_turn(gs))
    clock.elapsed += et.PENDING_TURN_HANG_AFTER_SECONDS
    result = asyncio.run(et.execute_end_turn(gs))
    asyncio.run(et.execute_end_turn(gs))
    assert result.startswith("HANG:")
    assert gs._pending_end_turn and gs.conn.turn_in_progress
    assert gs.conn.sends == 1
    assert gs.dismiss_calls == 1  # Only the original preflight may close a popup.


def test_wait_time_includes_queries_and_gaps_between_calls(monkeypatch):
    clock = _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=False)
    original = gs.conn.execute_read

    async def slow_read(code, **kwargs):
        if gs._pending_end_turn:
            clock.elapsed += 9.0
        return await original(code, **kwargs)

    gs.conn.execute_read = slow_read
    asyncio.run(et.execute_end_turn(gs))
    first_wait = gs._pending_end_turn_wait
    assert first_wait > sum(clock.slept)
    clock.elapsed += 700.0
    result = asyncio.run(et.execute_end_turn(gs))
    assert gs._pending_end_turn_wait >= first_wait + 700.0
    assert result.startswith("HANG:")
    assert gs.conn.sends == 1


def test_pending_continuation_skips_preflight_and_ordinary_ingame(monkeypatch):
    _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=False)
    asyncio.run(et.execute_end_turn(gs))

    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Pending path attempted normal InGame/preflight work")

    gs.conn.execute_write = forbidden
    gs.get_world_congress = forbidden
    gs._take_snapshot = forbidden
    gs.get_barbarian_overview = forbidden
    gs.dismiss_popup = forbidden
    result = asyncio.run(et.execute_end_turn(gs))
    assert result.startswith("TURN_PENDING:")
    assert gs.conn.sends == 1
    assert calls == []


def test_diplomacy_early_return_keeps_request_clock_and_submission(monkeypatch):
    clock = _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=False)

    async def sessions(**_kwargs):
        if not gs._pending_end_turn:
            return []
        clock.elapsed += 11.0
        return [SimpleNamespace(dialogue_text="hello", is_at_war=False,
                                deal_summary="", buttons="", other_civ_name="test",
                                other_leader_name="leader", reason_text="")]

    gs.get_diplomacy_sessions = sessions
    asyncio.run(et.execute_end_turn(gs))
    assert gs._pending_end_turn_wait == clock.elapsed
    clock.elapsed += 20.0
    asyncio.run(et.execute_end_turn(gs))
    assert gs._pending_end_turn_wait == clock.elapsed
    assert gs.conn.sends == 1


def test_explicit_not_sent_failure_releases_submission(monkeypatch):
    from civ_mcp.connection import CommandNotSentError

    _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=False)
    original = gs.conn.execute_mutation

    async def reject_before_send(*args, **kwargs):
        raise CommandNotSentError("blocked before write")

    gs.conn.execute_mutation = reject_before_send
    with pytest.raises(CommandNotSentError):
        asyncio.run(et.execute_end_turn(gs))
    assert not gs._pending_end_turn and not gs.conn.turn_in_progress
    assert gs.conn.sends == 0
    gs.conn.execute_mutation = original
    asyncio.run(et.execute_end_turn(gs))
    assert gs.conn.sends == 1


def test_confirmed_advance_notifies_all_waiters_before_snapshot_finishes(monkeypatch):
    _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=False)
    original = gs.conn.execute_mutation
    receipts = [[], []]

    async def scenario():
        sending = asyncio.Event()
        release_send = asyncio.Event()
        snapshot_started = asyncio.Event()
        release_snapshot = asyncio.Event()

        async def waiting_ack(code, **kwargs):
            await original(code, **kwargs)
            sending.set()
            await release_send.wait()
            return ["OK"]

        async def read_turn(_gs):
            return 58 if gs.conn.sends else 57

        async def snapshot():
            if not gs.conn.sends:
                raise RuntimeError("preflight has no snapshot")
            snapshot_started.set()
            await release_snapshot.wait()
            return None

        async def request(index):
            token = et.end_turn_confirmation.set(receipts[index].append)
            try:
                return await et.execute_end_turn(gs)
            finally:
                et.end_turn_confirmation.reset(token)

        gs.conn.execute_mutation = waiting_ack
        gs._take_snapshot = snapshot
        monkeypatch.setattr(et, "_get_turn_number", read_turn)
        first = asyncio.create_task(request(0))
        await sending.wait()
        second = asyncio.create_task(request(1))
        await asyncio.sleep(0)
        release_send.set()
        await snapshot_started.wait()
        assert receipts == [["Turn 57 -> 58"], ["Turn 57 -> 58"]]
        assert not first.done() and not second.done()
        assert gs.conn.sends == 1
        release_snapshot.set()
        assert await first == await second

    asyncio.run(scenario())


def test_reload_during_observation_cannot_confirm_old_request(monkeypatch):
    clock = _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=False)
    asyncio.run(et.execute_end_turn(gs))
    receipts = []

    async def loaded_world(_gs):
        gs._world_epoch = 1
        return 58

    monkeypatch.setattr(et, "_get_turn_number", loaded_world)
    token = et.end_turn_confirmation.set(receipts.append)
    try:
        result = asyncio.run(et.execute_end_turn(gs))
    finally:
        et.end_turn_confirmation.reset(token)
    assert result.startswith("UNKNOWN:RELOAD_PENDING")
    assert receipts == []
    assert gs.conn.sends == 1
    assert clock.elapsed > 0


def test_confirmed_congress_submission_is_not_driven_again(monkeypatch):
    _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=True)
    asyncio.run(et.execute_end_turn(gs))
    asyncio.run(et.execute_end_turn(gs))
    assert gs.drive_calls == 1
    assert gs._wc_driven
    assert gs.conn.sends == 1


def test_unknown_congress_submission_is_not_driven_again(monkeypatch):
    from civ_mcp.connection import MutationOutcomeUnknownError

    _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=True)

    async def lost_ack(**_kwargs):
        gs.drive_calls += 1
        raise MutationOutcomeUnknownError("congress input", TimeoutError())

    gs.drive_world_congress = lost_ack
    asyncio.run(et.execute_end_turn(gs))
    asyncio.run(et.execute_end_turn(gs))
    assert gs.drive_calls == 1
    assert gs._wc_drive_uncertain
    assert gs.conn.sends == 1


@pytest.mark.parametrize("crossing", ["game_over", "diplomacy_query", "diplomacy_sleep"])
def test_world_change_across_await_rejects_old_evidence_and_war_response(monkeypatch, crossing):
    clock = _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=False)
    asyncio.run(et.execute_end_turn(gs))
    calls = []
    original_send = gs.conn.execute_mutation

    async def record_send(code, **kwargs):
        calls.append(code)
        return await original_send(code, **kwargs)

    async def gameover():
        gs._world_epoch = 1
        return SimpleNamespace(victory_type="VICTORY_SCIENCE", is_defeat=True,
                               winner_name="new world", winner_leader="new leader")

    async def sessions(**_kwargs):
        if crossing == "diplomacy_query":
            gs._world_epoch = 1
        return [SimpleNamespace(dialogue_text="" if crossing == "diplomacy_sleep" else "war",
                                is_at_war=True, other_player_id=1,
                                other_civ_name="old world", other_leader_name="old leader")]

    async def cross_during_sleep(seconds):
        clock.elapsed += seconds
        gs._world_epoch = 1

    gs.conn.execute_mutation = record_send
    if crossing == "game_over":
        gs.check_game_over = gameover
        result = asyncio.run(et.execute_end_turn(gs))
    else:
        gs.get_diplomacy_sessions = sessions
        if crossing == "diplomacy_sleep":
            monkeypatch.setattr(et, "_sleep", cross_during_sleep)
        result, advanced = asyncio.run(et._check_mid_turn_diplomacy(
            gs, "unused", 57, expected_world_epoch=0))
        assert advanced is False
    assert result.startswith("UNKNOWN:RELOAD_PENDING")
    assert calls == []
    assert gs._pending_end_turn
    assert gs.conn.sends == 1


def test_world_change_during_preflight_does_not_submit_a_new_end_turn(monkeypatch):
    _VirtualClock(monkeypatch)
    gs = _FakeGameState(wc_turn=False)

    async def snapshot():
        gs._world_epoch = 1
        return None

    gs._take_snapshot = snapshot
    result = asyncio.run(et.execute_end_turn(gs))
    assert result.startswith("UNKNOWN:RELOAD_PENDING")
    assert gs.conn.sends == 0
