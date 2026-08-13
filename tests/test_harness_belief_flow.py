"""Contracts for the server-side Belief Engine action gate."""

from civ_mcp.server import _belief_route_required


def test_key_tools_are_gated_but_routine_unit_maintenance_is_not():
    assert _belief_route_required("set_research", {}) is True
    assert _belief_route_required("set_city_production", {}) is True
    assert _belief_route_required("unit_action", {"action": "attack"}) is True
    assert _belief_route_required("unit_action", {"action": "found_city"}) is True
    assert _belief_route_required("unit_action", {"action": "fortify"}) is False
    assert _belief_route_required("unit_action", {"action": "skip"}) is False


def test_diplomatic_and_economic_mutations_are_gated_centrally():
    assert _belief_route_required("propose_trade", {}) is True
    assert _belief_route_required("propose_peace", {}) is True
    assert _belief_route_required("form_alliance", {}) is True
    assert _belief_route_required("purchase_item", {}) is True
    assert _belief_route_required("change_government", {}) is True
    assert _belief_route_required("run_lua", {"context": "gamecore"}) is False
    assert _belief_route_required("run_lua", {"context": "ingame"}) is True
    assert _belief_route_required("get_diplomacy", {}) is False
