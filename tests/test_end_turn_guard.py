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
    def __init__(self, dismissed: str) -> None:
        self.dismiss_calls = 0
        self._dismissed = dismissed

    async def dismiss_popup(self) -> str:
        self.dismiss_calls += 1
        return self._dismissed


def test_early_dismiss_only_on_congress_turns() -> None:
    assert (
        _wc_early_dismiss_due(wc_turn=False, cumulative_wait=300.0, probed=False)
        is False
    )


def test_early_dismiss_waits_for_the_grace_period() -> None:
    assert (
        _wc_early_dismiss_due(wc_turn=True, cumulative_wait=30.0, probed=False)
        is False
    )
    assert (
        _wc_early_dismiss_due(wc_turn=True, cumulative_wait=61.0, probed=False)
        is True
    )


def test_early_dismiss_runs_at_most_once_per_turn() -> None:
    assert (
        _wc_early_dismiss_due(wc_turn=True, cumulative_wait=300.0, probed=True)
        is False
    )


def test_wc_probe_reports_a_real_dismissal() -> None:
    gs = _FakeGameState("Dismissed: WorldCongressIntro")
    assert asyncio.run(_probe_world_congress_popup(gs)) is True
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
