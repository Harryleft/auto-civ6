"""Contracts for the server-side Belief Engine action gate."""

from civ_mcp.belief_engine import action_args_hash
from civ_mcp.server import _belief_route_required, _canonical_action_params


def test_key_tools_are_gated_but_routine_unit_maintenance_is_not():
    assert _belief_route_required("set_research", {}) is True
    assert _belief_route_required("set_city_production", {}) is True
    assert _belief_route_required("unit_action", {"action": "attack"}) is True
    assert _belief_route_required("unit_action", {"action": "found_city"}) is True
    assert _belief_route_required("unit_action", {"action": "fortify"}) is False
    assert _belief_route_required("unit_action", {"action": "skip"}) is False


def test_diplomatic_and_economic_mutations_are_gated_centrally():
    assert _belief_route_required("propose_trade", {"mode": "send"}) is True
    assert _belief_route_required("propose_trade", {"mode": "test"}) is False
    assert _belief_route_required("propose_peace", {}) is True
    assert _belief_route_required("form_alliance", {}) is True
    assert _belief_route_required("purchase_item", {}) is True
    assert _belief_route_required("change_government", {}) is True
    assert _belief_route_required("run_lua", {"context": "gamecore"}) is False
    assert _belief_route_required("run_lua", {"context": "ingame"}) is True
    assert _belief_route_required("get_diplomacy", {}) is False


def test_city_actions_use_the_actual_logged_tool_names_in_the_gate():
    assert _belief_route_required("city_action", {"action": "attack"}) is True
    assert _belief_route_required("city_action", {"action": "raze"}) is True


def test_public_action_defaults_have_one_authorization_identity():
    assert _canonical_action_params(
        "set_research", {"tech_or_civic": "TECH_WRITING"}
    ) == {
        "category": "tech",
        "tech_or_civic": "TECH_WRITING",
    }
    assert _canonical_action_params("propose_trade", {"other_player_id": 1}) == {
        "offer_gold": 0,
        "offer_gold_per_turn": 0,
        "offer_resources": "",
        "offer_favor": 0,
        "offer_open_borders": False,
        "request_gold": 0,
        "request_gold_per_turn": 0,
        "request_resources": "",
        "request_favor": 0,
        "request_open_borders": False,
        "joint_war_target": 0,
        "mode": "send",
        "other_player_id": 1,
    }


def test_run_lua_authorization_binds_the_exact_code():
    first = _canonical_action_params("run_lua", {"code": "return 1"})
    second = _canonical_action_params("run_lua", {"code": "return 2"})
    assert action_args_hash(first) != action_args_hash(second)
