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
    """Replace ``end_turn._sleep``/``_now`` so no test waits for real minutes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.elapsed = 0.0
        self.slept: list[float] = []

        async def sleep(seconds: float) -> None:
            self.slept.append(seconds)
            self.elapsed += seconds

        monkeypatch.setattr(et, "_sleep", sleep)
        monkeypatch.setattr(et, "_now", lambda: self.elapsed)


class _FakeConnection:
    """Every Lua context answers with the same harmless line."""

    def __init__(self, *, wc_handler: bool) -> None:
        self._wc_handler = wc_handler

    async def execute_read(self, _code: str, **_kwargs) -> list[str]:
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
        return "No popups to dismiss."

    async def drive_world_congress(self) -> str:
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

    async def end_turn(self, *, poll_deadline=None) -> str:  # pragma: no cover
        # Only referenced (never awaited) by the budget tests, which stub
        # pipeline._logged; kept so the attribute lookup cannot fail.
        return f"end_turn stub poll_deadline={poll_deadline}"


def _expected_uncontested_sleeps(wc_turn: bool) -> float:
    """Sleeps a no-diplomacy, no-advance, no-popup pass really performs."""

    probes = et._WC_MAX_DRIVE_PROBES if wc_turn else 0
    return (
        et._PHASE1_SLEEP_SECONDS
        + sum(et._end_turn_poll_delays(wc_turn))
        + probes * 2.0
        + et._FINAL_VERIFY_SLEEP_SECONDS
    )


@pytest.mark.parametrize("wc_turn", [False, True])
def test_wait_loop_stays_inside_the_derived_poll_budget(
    monkeypatch: pytest.MonkeyPatch, wc_turn: bool
) -> None:
    """Run the real loop on a virtual clock; nothing may be unaccounted for."""

    clock = _VirtualClock(monkeypatch)

    asyncio.run(et.execute_end_turn(_FakeGameState(wc_turn=wc_turn)))

    assert clock.elapsed == _expected_uncontested_sleeps(wc_turn)
    # The headroom is exactly the two bounded branches this pass did not take:
    # the diplomacy probe's dialogue/war wait and the popup-dismiss re-poll.
    assert (
        et.poll_sleep_budget_seconds(wc_turn) - clock.elapsed
        == et._DIPLOMACY_PROBE_SLEEP_SECONDS + et._POPUP_REPOLL_SLEEP_SECONDS
    )


def test_poll_deadline_stops_the_loop_early() -> None:
    """A bounded retry must not be able to run the whole cadence again."""

    recorded: list[float] = []

    async def sleep(seconds: float) -> None:
        recorded.append(seconds)

    async def now() -> float:  # pragma: no cover - replaced below
        return 0.0

    original_sleep, original_now = et._sleep, et._now
    et._sleep, et._now = sleep, lambda: sum(recorded)
    try:
        asyncio.run(
            et.execute_end_turn(_FakeGameState(wc_turn=False), poll_deadline=100.0)
        )
    finally:
        et._sleep, et._now = original_sleep, original_now

    assert sum(recorded) < et.poll_sleep_budget_seconds(False)


# ---------------------------------------------------------------------------
# The host deadline must cover the derived budget
# ---------------------------------------------------------------------------


def test_host_deadline_covers_the_whole_end_turn_budget() -> None:
    match = _DSH_TIMEOUT.search(OVERLAY.read_text(encoding="utf-8"))

    assert match, "civ6.cordis.yml no longer sets an explicit toolCallTimeoutMs"
    host_ms = int(match.group(1))
    total = et.end_turn_budget(wc_turn=True)

    assert host_ms > total.total_seconds * 1000, (
        f"DSH 允许 {host_ms / 1000:.0f}s，但最坏单次 end_turn 预算为 "
        f"{total.total_seconds:.0f}s（轮询 {total.poll_seconds:.0f}s + "
        f"查询 {total.query_seconds:.0f}s + 恢复 {total.recovery_seconds:.0f}s）；"
        "宿主期限必须大于总预算，否则议会回合会在推进中途被杀死"
    )


def test_budget_covers_a_congress_turn_and_not_only_a_plain_one() -> None:
    plain = et.end_turn_budget(wc_turn=False)
    congress = et.end_turn_budget(wc_turn=True)

    assert congress.poll_seconds > plain.poll_seconds
    assert et.poll_sleep_budget_seconds(True) == 1210.0 + 4.0 + 6.0 + 12.0 + 44.0


# ---------------------------------------------------------------------------
# The recovery reserve must match the retries the flow really performs
# ---------------------------------------------------------------------------


def test_recovery_reserve_covers_every_declared_retry() -> None:
    assert et.hang_recovery_budget_seconds() == (
        et.hang_attempt_ceiling_seconds() * et.MAX_HANG_RETRIES
    )
    assert end_turn_flow._MAX_HANG_RETRIES == et.MAX_HANG_RETRIES
    assert end_turn_flow._HANG_EXTRA_WAIT == list(et.HANG_EXTRA_WAITS)
    # The escalating waits are part of the per-attempt ceiling, so a larger
    # wait cannot silently outgrow the reserve.
    assert et.HANG_EXTRA_WAITS[-1] <= et.hang_attempt_ceiling_seconds()


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


def test_hang_recovery_is_not_started_when_the_budget_cannot_pay_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restarts: list[str] = []

    async def fake_restart(save_name, conn=None):  # pragma: no cover
        restarts.append(save_name)
        return "restarted"

    monkeypatch.setattr(
        end_turn_flow.game_launcher, "restart_and_load", fake_restart
    )
    # _run_end_turn_impl must reach the recovery branch.
    async def fake_logged(*_args, **_kwargs) -> str:
        return "HANG:57:AutoSave_0057|AI turn processing appears stuck."

    monkeypatch.setattr(pipeline, "_logged", fake_logged)
    monkeypatch.setattr(
        pipeline,
        "_get_logger",
        lambda _ctx: SimpleNamespace(
            session_id="budget-test",
            set_agent_model=lambda *_: None,
            set_turn=lambda *_: None,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "_get_spatial",
        lambda _ctx: SimpleNamespace(set_turn=lambda *_: None),
    )
    monkeypatch.setattr(
        end_turn_flow, "_clock", lambda: 10_000.0
    )
    monkeypatch.setattr(
        end_turn_flow.os.path,
        "exists",
        lambda _path: True,
    )

    gs = _FakeGameState(wc_turn=False)
    gs._game_identity = ("test", 1)

    result = asyncio.run(
        end_turn_flow._run_end_turn_impl(
            _context(gs),
            deadline=10_000.0 + end_turn_flow.hang_attempt_ceiling_seconds() - 1.0,
            tactical="t",
            strategic="s",
            tooling="o",
            planning="p",
            hypothesis="h",
        )
    )

    assert restarts == [], "a restart must not start when it cannot finish"
    assert "UNKNOWN:HANG_RECOVERY_UNAFFORDABLE" in result
    assert "不要再次调用 end_turn" in result


def test_recovery_budget_is_not_consumed_by_an_unaffordable_retry_loop() -> None:
    """The refusal is the same at the first attempt and mid-loop."""

    needed = et.hang_attempt_ceiling_seconds()
    assert needed == (
        et.HANG_RESTART_CEILING_SECONDS
        + et.HANG_RECONNECT_CEILING_SECONDS
        + et.HANG_IDENTITY_CEILING_SECONDS
        + max(et.HANG_EXTRA_WAITS)
        + et.HANG_RETRY_POLL_CEILING_SECONDS
    )
    # A retried turn polls less than a first pass, so the ceiling is a real bound.
    assert et.HANG_RETRY_POLL_CEILING_SECONDS < et.poll_sleep_budget_seconds(True)
