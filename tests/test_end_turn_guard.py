"""Regression tests for end-turn safety gates."""

from civ_mcp.end_turn import _can_override_end_turn_blockers


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
