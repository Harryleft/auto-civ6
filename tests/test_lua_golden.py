"""Golden tests for Lua query/action builders.

Every builder is the literal code executed inside the game.  These tests pin
the structural contracts that a broken builder would silently violate: the
sentinel terminator, error bail patterns, and parameter injection.  They do
not assert prose — only machine contracts.

The narrated-text layer is deliberately NOT snapshotted (presentation churn);
these are behaviour contracts on the wire format.
"""

from __future__ import annotations

import pytest

from civ_mcp.lua import cities, congress, diplomacy, units
from civ_mcp.lua.barbarians import build_barbarian_overview_query
from civ_mcp.lua.tech import build_tech_civics_query


def _assert_sentinel(query: str) -> None:
    assert query.rstrip().endswith('print("---END---")')


# ---------------------------------------------------------------------------
# 通用契约：所有 builder 以哨兵结尾
# ---------------------------------------------------------------------------

_BUILDERS = [
    lambda: build_barbarian_overview_query(),
    lambda: units.build_units_query(),
    lambda: units.build_threat_scan_query(),
    lambda: units.build_skip_remaining_units(),
    lambda: units.build_fortify_remaining_units(),
    lambda: cities.build_city_production_query(5),
    lambda: cities.build_city_yield_focus_query(5),
    lambda: diplomacy.build_pending_deals_query(),
    lambda: diplomacy.build_diplomacy_session_query(),
    lambda: diplomacy.build_war_dismiss_view(),
    lambda: build_tech_civics_query(),
    lambda: congress.build_world_congress_query(),
    lambda: congress.build_congress_submit(),
    lambda: congress.build_register_wc_voter(),
]


@pytest.mark.parametrize("builder", _BUILDERS)
def test_builders_terminate_with_sentinel(builder):
    query = builder()
    assert isinstance(query, str) and query.strip()
    _assert_sentinel(query)


# ---------------------------------------------------------------------------
# 参数注入与错误路径
# ---------------------------------------------------------------------------


def test_move_unit_injects_index_and_coordinates():
    query = units.build_move_unit(7, 3, 4)
    _assert_sentinel(query)
    assert "UnitManager.GetUnit(me, 7)" in query
    assert "[UnitOperationTypes.PARAM_X] = 3" in query
    assert "[UnitOperationTypes.PARAM_Y] = 4" in query
    # 守卫：无移动点、无法移动、堆叠冲突。
    assert "ERR:NO_MOVES" in query
    assert "ERR:CANNOT_MOVE" in query
    assert "ERR:STACKING_CONFLICT" in query


def test_attack_unit_injects_coordinates_and_war_guard():
    query = units.build_attack_unit(7, 3, 4)
    _assert_sentinel(query)
    assert "UnitManager.GetUnit(me, 7)" in query
    assert "PARAM_X] = 3" in query and "PARAM_Y] = 4" in query
    assert "ERR:NO_TARGET" in query or "ERR:NOT_AT_WAR" in query


def test_combat_estimate_injects_coordinates():
    query = units.build_combat_estimate_query(7, 3, 4)
    _assert_sentinel(query)
    assert "GetUnit(me, 7)" in query
    assert "3" in query and "4" in query


def test_unit_lookup_failure_bails_before_action():
    query = units.build_fortify_unit(9)
    assert "ERR:UNIT_NOT_FOUND" in query
    assert "UnitManager.GetUnit(me, 9)" in query
    _assert_sentinel(query)


def test_skip_and_heal_inject_unit_index():
    for builder, expected in (
        (units.build_skip_unit, "SKIP"),
        (units.build_heal_unit, "HEAL"),
        (units.build_alert_unit, "ALERT"),
        (units.build_sleep_unit, "SLEEP"),
    ):
        query = builder(12)
        # GameCore 上下文用 FindID，InGame 上下文用 GetUnit——两者都要注入索引。
        assert "12" in query
        assert "GetUnit(me, 12)" in query or "FindID(12)" in query
        _assert_sentinel(query)


def test_improve_tile_injects_improvement_name():
    query = units.build_improve_tile(7, "IMPROVEMENT_FARM")
    _assert_sentinel(query)
    assert "IMPROVEMENT_FARM" in query
    assert "ERR:UNIT_NOT_FOUND" in query


def test_pathing_estimate_injects_target():
    query = units.build_pathing_estimate_query(7, 3, 4)
    _assert_sentinel(query)
    assert "3" in query and "4" in query


def test_produce_item_injects_item_type_and_name():
    query = cities.build_produce_item(5, "BUILDING", "BUILDING_LIBRARY")
    _assert_sentinel(query)
    assert "CityManager.GetCity(me, 5 % 65536)" in query
    assert 'GameInfo.Buildings["BUILDING_LIBRARY"]' in query
    assert "ERR:CITY_NOT_FOUND" in query
    assert "ERR:ITEM_NOT_FOUND|BUILDING_LIBRARY" in query


def test_purchase_item_injects_item_and_costs():
    query = cities.build_purchase_item(5, "UNIT", "UNIT_SETTLER")
    _assert_sentinel(query)
    assert 'GameInfo.Units["UNIT_SETTLER"]' in query
    assert "ERR:ITEM_NOT_FOUND" in query


def test_city_attack_injects_coordinates():
    query = cities.build_city_attack(5, 3, 4)
    _assert_sentinel(query)
    assert "5" in query and "3" in query and "4" in query


def test_diplomacy_respond_injects_player_and_response():
    query = diplomacy.build_diplomacy_respond(3, "ACCEPT")
    _assert_sentinel(query)
    assert "3" in query and "ACCEPT" in query


def test_respond_to_deal_injects_player_and_accept_flag():
    query = diplomacy.build_respond_to_deal(3, True)
    _assert_sentinel(query)
    assert "3" in query
    assert "ACCEPT" in query or "true" in query.lower()
    reject = diplomacy.build_respond_to_deal(3, False)
    assert "REJECT" in reject or "false" in reject.lower()


def test_congress_vote_injects_resolution_hash_and_options():
    query = congress.build_congress_vote(4, 1, 2, 1)
    _assert_sentinel(query)
    assert "4" in query and "1" in query


def test_barbarian_query_scans_camps_and_units():
    query = build_barbarian_overview_query()
    _assert_sentinel(query)
    assert "IMPROVEMENT_BARBARIAN_CAMP" in query
    assert "BARB_CAMP|" in query and "BARB_UNIT|" in query
