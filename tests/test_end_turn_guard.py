"""Regression tests for end-turn safety gates."""

import asyncio

from civ_mcp import lua as lq
from civ_mcp.end_turn import (
    _can_override_end_turn_blockers,
    _probe_world_congress_popup,
    _wc_popup_probe_needed,
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


class _FakeConn:
    def __init__(self) -> None:
        self.write_calls = 0

    async def execute_write(self, lua: str) -> list[str]:
        self.write_calls += 1
        return ["BLOCKER"]


class _FakeGameState:
    def __init__(self, dismissed: str) -> None:
        self.conn = _FakeConn()
        self.dismiss_calls = 0
        self._dismissed = dismissed

    async def dismiss_popup(self) -> str:
        self.dismiss_calls += 1
        return self._dismissed


def test_wc_probe_needed_for_congress_blockers() -> None:
    assert (
        _wc_popup_probe_needed(
            [("ENDTURN_BLOCKING_WORLD_CONGRESS_SESSION", "继续议会")]
        )
        is True
    )
    assert (
        _wc_popup_probe_needed(
            [("ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK", "查看议会结果")]
        )
        is True
    )


def test_wc_probe_not_needed_for_other_blockers() -> None:
    assert (
        _wc_popup_probe_needed(
            [("ENDTURN_BLOCKING_PRODUCTION", "London needs production")]
        )
        is False
    )
    assert _wc_popup_probe_needed([]) is False


def test_wc_probe_dismisses_once_when_congress_blocker_present(
    monkeypatch,
) -> None:
    monkeypatch.setattr(lq, "build_end_turn_blocking_query", lambda: "Q")
    monkeypatch.setattr(
        lq,
        "parse_end_turn_blocking",
        lambda lines: [("ENDTURN_BLOCKING_WORLD_CONGRESS_LOOK", "look")],
    )
    gs = _FakeGameState("Dismissed: WorldCongressIntro")

    assert asyncio.run(_probe_world_congress_popup(gs)) is True
    assert gs.dismiss_calls == 1


def test_wc_probe_skips_dismiss_without_congress_blocker(monkeypatch) -> None:
    monkeypatch.setattr(lq, "build_end_turn_blocking_query", lambda: "Q")
    monkeypatch.setattr(
        lq,
        "parse_end_turn_blocking",
        lambda lines: [("ENDTURN_BLOCKING_UNITS", "Scout needs orders")],
    )
    gs = _FakeGameState("Dismissed: Something")

    assert asyncio.run(_probe_world_congress_popup(gs)) is False
    assert gs.dismiss_calls == 0
