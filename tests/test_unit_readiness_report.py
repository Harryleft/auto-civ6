"""Readiness is an observed fact, never inferred from MP or a command hint."""
import pytest

from civ_mcp import narrate
from civ_mcp.lua.units import observe_units_response
from test_unit_upgrade_hint import UpgradeHintConn, read


@pytest.mark.parametrize("native,expected", [
    ("true", True), ("false", False), ("nil", None),
    ("1", None), ("'false'", None),
])
def test_native_readiness_crosses_game_state_without_extra_request(native, expected):
    conn = UpgradeHintConn(f"unit.IsReadyToMove=function()return {native} end")
    unit = read(conn)  # One query and zero native command submissions.
    assert unit.moves_remaining == 2
    assert unit.ready_to_move is expected
    label = "unknown" if expected is None else str(expected).lower()
    assert f"[ready:{label}]" in narrate.narrate_units([unit])


@pytest.mark.parametrize("patch", [
    "", "unit.IsReadyToMove=function()error('unavailable')end",
])
def test_unavailable_readiness_does_not_hide_other_unit_facts(patch):
    unit = read(UpgradeHintConn(patch))
    assert unit.ready_to_move is None
    assert unit.can_upgrade and unit.upgrade_target == "UNIT_MAN_AT_ARMS"


def test_legacy_rows_keep_readiness_unknown_and_bad_new_rows_are_incomplete():
    legacy = "65536|0|Warrior|UNIT_WARRIOR|1,2|2/2|100/100"
    full = legacy + "|20|0|0||0|0||0||"
    for row in (legacy, full):
        unit, = observe_units_response([row]).require_complete()
        assert unit.ready_to_move is None
    for invalid in ("", "1", "nil", "unknown", "true|false"):
        result = observe_units_response([legacy, full + "|" + invalid])
        assert not result.complete and len(result.value) == 1


def test_decision_context_retains_readiness_in_brief_and_structured_views():
    from test_decision_context import read as decision_read, response

    row = "65536|0|Warrior|UNIT_WARRIOR|1,2|2/2|100/100|20|0|0||0|0||0|||false"
    rows = response({"units": [row]})
    structured, _, conn = decision_read(rows, view="structured")
    assert len(conn.calls) == 1 and structured["status"] == "ready"
    assert structured["sections"]["units"]["data"][0]["ready_to_move"] is False
    brief, _, _ = decision_read(rows)
    assert "[ready:false]" in brief["sections"]["units"]["data"]
