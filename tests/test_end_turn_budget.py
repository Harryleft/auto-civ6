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
from civ_mcp.server import pipeline

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


class _FakeConnection:
    """Every Lua context answers with the same harmless line."""

    def __init__(self, *, wc_handler: bool) -> None:
        self._wc_handler = wc_handler

    async def execute_read(self, code: str, **_kwargs) -> list[str]:
        if "GetCurrentGameTurn" in code:
            # A readable turn number is what makes the HANG branch reachable at
            # all; without it the loop cannot report "still on turn N".
            return ["57"]
        return ["NO_THREATS"]

    async def execute_write(self, _code: str, **_kwargs) -> list[str]:
        return ["HANDLER_SET"] if self._wc_handler else ["NO_THREATS"]

    async def execute_mutation(self, _code: str, **_kwargs) -> list[str]:
        return ["OK"]


class _FakeGameState:
    """A game whose turn never advances and whose queries never block."""

    def __init__(self, *, wc_turn: bool) -> None:
        self.conn = _FakeConnection(wc_handler=wc_turn)
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

    async def get_diplomacy_sessions(self) -> list:
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

    async def drive_world_congress(self) -> str:
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


def test_a_congress_turn_drives_the_session_instead_of_waiting_it_out() -> None:
    """The old schedule started at t+60s with 3 tries; a late session escaped it."""

    recorded: list[float] = []
    elapsed = {"t": 0.0}

    async def sleep(seconds: float) -> None:
        recorded.append(seconds)
        elapsed["t"] += seconds

    original_sleep = et._sleep
    et._sleep = sleep
    try:
        gs = _FakeGameState(wc_turn=True)
        asyncio.run(et.execute_end_turn(gs))
    finally:
        et._sleep = original_sleep

    total = et._WC_DRIVE_BURST_PROBES + et._WC_DRIVE_SPARSE_PROBES
    assert gs.drive_calls >= 12, "议会回合必须持续尝试驱动，而不是只试三次"
    assert gs.drive_calls <= total
    # The first attempt happens within seconds of the turn being requested.
    first_attempt_at = et._PHASE1_SLEEP_SECONDS + min(recorded) if recorded else None
    assert gs.drive_calls > 0 and first_attempt_at is not None


def test_the_drive_schedule_fits_inside_the_congress_poll_window() -> None:
    """A schedule that outlives its own window would leave attempts unreachable."""

    window = et.poll_sleep_budget_seconds(True) - et._PHASE3_SLEEP_SECONDS
    last_threshold = (
        et._WC_FIRST_DRIVE_AFTER
        + et._WC_DRIVE_BURST_PROBES * et._WC_DRIVE_BURST_INTERVAL
        + (et._WC_DRIVE_SPARSE_PROBES - 1) * et._WC_DRIVE_SPARSE_INTERVAL
    )

    assert last_threshold < window


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
    longest = et.longest_mcp_call_seconds()
    budget = et.end_turn_budget(wc_turn=True)

    assert host_ms > longest * 1000, (
        f"DSH 允许 {host_ms / 1000:.0f}s，但最长单次调用为 {longest:.0f}s"
        f"（end_turn 轮询 {budget.poll_seconds:.0f}s + 查询 "
        f"{budget.query_seconds:.0f}s；恢复单独一步 "
        f"{et.restart_and_load_budget_seconds():.0f}s）；"
        "宿主期限必须大于最长单次调用，否则会在推进中途被杀死"
    )
    # 20 minutes, not 50: the ceiling covers a slow AI turn, not three relaunches.
    assert host_ms <= 20 * 60 * 1000


def test_budget_covers_a_congress_turn_and_not_only_a_plain_one() -> None:
    plain = et.end_turn_budget(wc_turn=False)
    congress = et.end_turn_budget(wc_turn=True)

    assert congress.poll_seconds > plain.poll_seconds
    assert et.poll_sleep_budget_seconds(True) == et.poll_sleep_budget_seconds(False) + 220.0


# ---------------------------------------------------------------------------
# The recovery reserve must match the retries the flow really performs
# ---------------------------------------------------------------------------


def test_recovery_is_not_part_of_the_end_turn_budget() -> None:
    """Restarts used to live inside this call, which is what forced ~50 minutes."""

    budget = et.end_turn_budget(wc_turn=True)

    assert budget.total_seconds == budget.poll_seconds + budget.query_seconds
    assert not hasattr(budget, "recovery_seconds")


def test_the_host_deadline_covers_the_longest_single_call() -> None:
    """The timeout is per call, so end_turn and recovery must not be summed."""

    longest = et.longest_mcp_call_seconds()
    separately_summed = (
        et.end_turn_budget(wc_turn=True).total_seconds
        + et.restart_and_load_budget_seconds()
    )

    assert longest == et.end_turn_budget(wc_turn=True).total_seconds
    assert longest < separately_summed
    # A vote is a seconds-long operation; the ceiling exists for a slow AI turn.
    assert longest < 20 * 60, (
        "单次调用期限必须回到“等一轮慢回合”的量级，而不是被重启次数撑高"
    )


def test_the_recovery_call_ceiling_is_itself_bounded() -> None:
    ceiling = et.restart_and_load_budget_seconds()

    assert ceiling == (
        et.RESTART_AND_LOAD_CEILING_SECONDS
        + et.RESTART_RECONNECT_CEILING_SECONDS
        + et.RESTART_IDENTITY_CEILING_SECONDS
    )
    assert ceiling < et.end_turn_budget(wc_turn=True).total_seconds


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
            lifespan_context=SimpleNamespace(game=game or object())
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
    assert "不要再次调用 end_turn" in result
    assert "get_game_overview" in result
    # It must not read as an advance, and it must not invite a retry of the
    # action whose outcome is unknown.
    assert "->" not in result
    assert "Turn " not in result


def _hang_verdict(monkeypatch: pytest.MonkeyPatch, *, wc_turn: bool, driven: bool):
    """Run one exhausted pass and return (verdict, game, clock)."""

    class _GS(_FakeGameState):
        async def drive_world_congress(self) -> str:
            self.drive_calls += 1
            if driven:
                return "WC_DRIVE|submitted|spent:0|1:WC_RES_LUXURY:1:0:1:0:type"
            return "WC_DRIVE|no_session"

    gs = _GS(wc_turn=wc_turn)
    clock = _VirtualClock(monkeypatch)
    return asyncio.run(et.execute_end_turn(gs)), gs, clock


def test_an_undriven_congress_is_never_reported_as_a_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blocker query reports nothing during congress; that is not a wedge.

    Misreading it as a wedge used to kill and reload a perfectly healthy game up
    to three times — over a missing vote.
    """

    result, gs, _ = _hang_verdict(monkeypatch, wc_turn=True, driven=False)

    assert result.startswith("CONGRESS_NOT_DRIVEN:")
    assert not result.startswith("HANG:")
    assert "CONGRESS_NOT_DRIVEN_IS_NOT_A_HANG" in result
    assert "禁止重启游戏" in result
    assert gs.drive_calls > 0


def test_a_plain_turn_that_never_advances_is_still_a_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _, _ = _hang_verdict(monkeypatch, wc_turn=False, driven=False)

    assert result.startswith("HANG:")


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
    assert "restart_and_load" in result
    assert "get_game_overview" in result
    assert "重复发送 end_turn" in result


def test_end_turn_flow_no_longer_restarts_the_game() -> None:
    """Guard the invariant at the source: a reload inside end_turn was the bug."""

    source = (
        ROOT / "src" / "civ_mcp" / "server" / "tools" / "end_turn_flow.py"
    ).read_text(encoding="utf-8")

    assert "await game_launcher.restart_and_load(" not in source
    assert "HANG_RECOVERY_IS_A_SEPARATE_STEP" in source
