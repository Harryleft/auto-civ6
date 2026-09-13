"""Regression tests for end-turn safety gates."""

import asyncio

from civ_mcp.end_turn import (
    _can_override_end_turn_blockers,
    _end_turn_poll_delays,
    _probe_world_congress_popup,
    _wc_early_dismiss_due,
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
        _wc_early_dismiss_due(wc_turn=False, cumulative_wait=300.0, probes=0)
        is False
    )


def test_early_dismiss_waits_for_the_grace_period() -> None:
    assert (
        _wc_early_dismiss_due(wc_turn=True, cumulative_wait=30.0, probes=0)
        is False
    )
    assert (
        _wc_early_dismiss_due(wc_turn=True, cumulative_wait=61.0, probes=0)
        is True
    )


def test_early_dismiss_attempts_are_bounded_per_turn() -> None:
    # 每次探测间隔 60 秒，最多 _WC_MAX_DRIVE_PROBES 次。
    assert (
        _wc_early_dismiss_due(wc_turn=True, cumulative_wait=61.0, probes=1)
        is False
    )
    assert (
        _wc_early_dismiss_due(wc_turn=True, cumulative_wait=121.0, probes=1)
        is True
    )
    assert (
        _wc_early_dismiss_due(wc_turn=True, cumulative_wait=999.0, probes=3)
        is False
    )


def test_wc_probe_drives_the_open_session_and_submits() -> None:
    """议会已开会时：程序自己按策略投票并提交，不需要人点界面。"""

    gs = _FakeGameState(
        "No popups to dismiss.",
        drive="WC_DRIVE|submitted|spent:0|123:WC_RES_LUXURY:1:0:1:0:type",
    )
    assert asyncio.run(_probe_world_congress_popup(gs)) is True
    assert gs.drive_calls == 1
    assert gs.dismiss_calls == 0


def test_wc_probe_falls_back_to_popup_dismissal_without_a_session() -> None:
    gs = _FakeGameState("Dismissed: WorldCongressIntro")
    assert asyncio.run(_probe_world_congress_popup(gs)) is True
    assert gs.drive_calls == 1
    assert gs.dismiss_calls == 1


def test_wc_probe_reports_nothing_to_dismiss() -> None:
    gs = _FakeGameState("No popups to dismiss.")
    assert asyncio.run(_probe_world_congress_popup(gs)) is False
    assert gs.dismiss_calls == 1


def test_plain_turn_poll_budget_is_unchanged() -> None:
    delays = _end_turn_poll_delays(wc_turn=False)
    assert sum(delays) == 550.0
    assert all(delay > 0 for delay in delays)


def test_congress_turn_gets_a_bounded_extra_poll_budget() -> None:
    plain = _end_turn_poll_delays(wc_turn=False)
    congress = _end_turn_poll_delays(wc_turn=True)

    assert sum(congress) == 1210.0  # ~20 min, bounded
    assert congress[: len(plain)] == plain
