"""Regression tests for end-turn safety gates."""

import asyncio

from civ_mcp.end_turn import (
    _WC_DRIVE_BURST_PROBES,
    _WC_DRIVE_SPARSE_PROBES,
    _WC_MAX_DISMISS_PROBES,
    _can_override_end_turn_blockers,
    _dismiss_congress_popup,
    _drive_congress,
    _end_turn_poll_delays,
    _wc_dismiss_due,
    _wc_drive_due,
)


def test_unit_blocker_cannot_be_overridden_by_ui_can_end_turn() -> None:
    assert (
        _can_override_end_turn_blockers(
            [("ENDTURN_BLOCKING_UNITS", "Scout needs orders")]
        )
        is False
    )


def test_other_blockers_retain_ui_override_compatibility() -> None:
    assert (
        _can_override_end_turn_blockers(
            [("ENDTURN_BLOCKING_PRODUCTION", "London needs production")]
        )
        is True
    )


def test_unit_blocker_wins_when_mixed_with_other_blockers() -> None:
    assert (
        _can_override_end_turn_blockers(
            [
                ("ENDTURN_BLOCKING_PRODUCTION", "London needs production"),
                ("ENDTURN_BLOCKING_UNITS", "Scout needs orders"),
            ]
        )
        is False
    )


class _FakeGameState:
    def __init__(self, dismissed: str, drive: str = "WC_DRIVE|no_session") -> None:
        self.dismiss_calls = 0
        self.drive_calls = 0
        self._dismissed = dismissed
        self._drive = drive

    async def dismiss_popup(self) -> str:
        self.dismiss_calls += 1
        return self._dismissed

    async def drive_world_congress(self) -> str:
        self.drive_calls += 1
        return self._drive


def test_early_dismiss_only_on_congress_turns() -> None:
    assert (
        _wc_drive_due(wc_turn=False, cumulative_wait=300.0, drives=0) is False
    )
    assert (
        _wc_dismiss_due(wc_turn=False, cumulative_wait=300.0, dismissals=0) is False
    )


def test_the_driver_starts_almost_immediately() -> None:
    """A human would click vote at once; waiting a full minute first is the bug."""

    assert _wc_drive_due(wc_turn=True, cumulative_wait=5.0, drives=0) is True
    assert _wc_drive_due(wc_turn=True, cumulative_wait=4.9, drives=0) is False


def test_the_driver_bursts_then_sparsens_out() -> None:
    """Fast where it matters, sparse afterwards, so a late session is still driven."""

    # Burst: the first few attempts are 15s apart.
    assert _wc_drive_due(wc_turn=True, cumulative_wait=20.0, drives=1) is True
    assert _wc_drive_due(wc_turn=True, cumulative_wait=19.9, drives=1) is False
    # Sparse: after the burst the gap widens to a minute.
    after_burst = _WC_DRIVE_BURST_PROBES
    assert (
        _wc_drive_due(wc_turn=True, cumulative_wait=200.0, drives=after_burst) is True
    )
    assert (
        _wc_drive_due(wc_turn=True, cumulative_wait=260.0, drives=after_burst + 1)
        is True
    )


def test_the_driver_keeps_trying_across_the_whole_opening_window() -> None:
    """Three attempts starting at t+60s missed any session that opened later."""

    total = _WC_DRIVE_BURST_PROBES + _WC_DRIVE_SPARSE_PROBES

    assert total >= 12, "驱动次数太少会让议会回合重新退回被动等待"
    # The schedule must still be a bounded number of InGame calls.
    assert total <= 30, "密集 InGame 调用是历史卡死的成因，必须保持有界"


def test_the_driver_attempts_are_bounded_per_turn() -> None:
    total = _WC_DRIVE_BURST_PROBES + _WC_DRIVE_SPARSE_PROBES

    assert (
        _wc_drive_due(wc_turn=True, cumulative_wait=10_000.0, drives=total) is False
    )


def test_blind_popup_dismissal_stays_late_and_tight() -> None:
    """Closing UI during AI processing is the documented cause of wedged turns."""

    assert _wc_dismiss_due(wc_turn=True, cumulative_wait=30.0, dismissals=0) is False
    assert _wc_dismiss_due(wc_turn=True, cumulative_wait=60.0, dismissals=0) is True
    assert _wc_dismiss_due(wc_turn=True, cumulative_wait=180.0, dismissals=1) is True
    assert _wc_dismiss_due(wc_turn=True, cumulative_wait=10_000.0, dismissals=2) is False
    assert _WC_MAX_DISMISS_PROBES <= 2, "盲关弹窗必须极小，驱动才是快路径"


class _FakeGameState:
    def __init__(self, dismissed: str, drive: str = "WC_DRIVE|no_session") -> None:
        self.dismiss_calls = 0
        self.drive_calls = 0
        self._dismissed = dismissed
        self._drive = drive

    async def dismiss_popup(self) -> str:
        self.dismiss_calls += 1
        return self._dismissed

    async def drive_world_congress(self) -> str:
        self.drive_calls += 1
        return self._drive


def test_wc_probe_drives_the_open_session_and_submits() -> None:
    """议会已开会时：程序自己按策略投票并提交，不需要人点界面。"""

    gs = _FakeGameState(
        "No popups to dismiss.",
        drive="WC_DRIVE|submitted|spent:0|123:WC_RES_LUXURY:1:0:1:0:type",
    )
    assert asyncio.run(_drive_congress(gs)) is True
    assert gs.drive_calls == 1
    assert gs.dismiss_calls == 0, "驱动成功时不应再去盲关界面"


def test_the_driver_reports_no_session_without_side_effects() -> None:
    """No open session -> nothing happened, and nothing was touched."""

    gs = _FakeGameState("Dismissed: WorldCongressIntro")
    assert asyncio.run(_drive_congress(gs)) is False
    assert gs.drive_calls == 1
    assert gs.dismiss_calls == 0, "驱动本身不得顺手关界面"


def test_a_driver_error_is_not_a_submission() -> None:
    class _Broken(_FakeGameState):
        async def drive_world_congress(self) -> str:
            self.drive_calls += 1
            raise RuntimeError("lua blew up")

    gs = _Broken("No popups to dismiss.")
    assert asyncio.run(_drive_congress(gs)) is False
    assert gs.drive_calls == 1


def test_popup_dismissal_is_a_separate_fallback() -> None:
    gs = _FakeGameState("Dismissed: WorldCongressIntro")
    assert asyncio.run(_dismiss_congress_popup(gs)) is True
    assert gs.dismiss_calls == 1

    gs2 = _FakeGameState("No popups to dismiss.")
    assert asyncio.run(_dismiss_congress_popup(gs2)) is False


def test_plain_turn_poll_budget_is_unchanged() -> None:
    delays = _end_turn_poll_delays(wc_turn=False)
    assert sum(delays) == 550.0
    assert all(delay > 0 for delay in delays)


def test_congress_turn_gets_a_bounded_extra_poll_budget() -> None:
    plain = _end_turn_poll_delays(wc_turn=False)
    congress = _end_turn_poll_delays(wc_turn=True)

    # 550s plain + 180s congress slack. The slack used to be +660s, which only
    # existed to wait out a congress screen nobody was operating; the driver now
    # submits the session itself, so a longer silent wait buys nothing and only
    # delays an honest hang report.
    assert sum(congress) == 730.0
    assert congress[: len(plain)] == plain
