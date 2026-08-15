"""Static contracts for optional Civilization VI ruleset mechanics."""

from civ_mcp.lua.congress import (
    build_congress_submit,
    build_congress_vote,
    build_register_wc_voter,
    build_world_congress_query,
)
from civ_mcp.lua.diplomacy import (
    build_deal_options_query,
    build_diplomacy_query,
    build_form_alliance,
)
from civ_mcp.lua.governance import (
    build_appoint_governor,
    build_assign_governor,
    build_choose_dedication,
    build_dedications_query,
    build_governors_query,
    build_promote_governor,
)
from civ_mcp.lua.map import build_empire_resources_query, build_stockpile_query
from civ_mcp.lua.eras import build_era_progress_query
from civ_mcp.lua.overview import build_overview_query


def test_governor_builders_return_a_ruleset_error_before_optional_apis():
    builders = (
        build_governors_query,
        lambda: build_appoint_governor("GOVERNOR_MAGNUS"),
        lambda: build_assign_governor("GOVERNOR_MAGNUS", 1),
        lambda: build_promote_governor(
            "GOVERNOR_MAGNUS", "GOVERNOR_PROMOTION_PROVISION"
        ),
    )
    for build in builders:
        lua = build()
        assert "ERR:NO_GOVERNORS_IN_RULESET" in lua
        assert "GameConfiguration.GetRuleSet" in lua
        assert "RULESET_EXPANSION_1" in lua
        assert "{_bail" not in lua


def test_dedication_builders_return_a_ruleset_error_before_optional_apis():
    for build in (build_dedications_query, lambda: build_choose_dedication(0)):
        lua = build()
        assert "ERR:NO_DEDICATIONS_IN_RULESET" in lua
        assert "GameConfiguration.GetRuleSet" in lua
        assert "RULESET_EXPANSION_1" in lua
        assert "{_bail" not in lua


def test_world_congress_builders_have_a_ruleset_guard():
    builders = (
        build_world_congress_query,
        lambda: build_congress_vote(1, 1, 0, 1),
        build_congress_submit,
        build_register_wc_voter,
    )
    for build in builders:
        lua = build()
        assert "ERR:NO_WORLD_CONGRESS_IN_RULESET" in lua
        assert "RULESET_EXPANSION_2" in lua
        assert "{_bail" not in lua


def test_stockpile_only_query_has_a_ruleset_guard_but_resource_scan_survives():
    stockpile_lua = build_stockpile_query()
    assert "ERR:NO_RESOURCE_STOCKPILES_IN_RULESET" in stockpile_lua
    assert "RULESET_EXPANSION_2" in stockpile_lua
    assert "{_bail" not in stockpile_lua

    resources_lua = build_empire_resources_query()
    assert "local hasStockpiles" in resources_lua
    assert 'if cls == "strategic" and hasStockpiles then' in resources_lua
    assert "OWNED|" in resources_lua
    assert "NEARBY|" in resources_lua


def test_standard_rules_suppress_expansion_diplomacy_and_era_claims():
    diplomacy_lua = build_diplomacy_query()
    assert 'activeRuleset ~= "RULESET_STANDARD"' in diplomacy_lua

    options_lua = build_deal_options_query(4)
    assert 'activeRuleset == "RULESET_EXPANSION_2"' in options_lua
    assert 'print("RULESET|"' in options_lua

    alliance_lua = build_form_alliance(4, "MILITARY")
    assert "ERR:NO_ALLIANCES_IN_RULESET" in alliance_lua
    assert "RULESET_EXPANSION_1" in alliance_lua

    overview_lua = build_overview_query()
    assert 'activeRuleset ~= "RULESET_STANDARD"' in overview_lua
    assert 'activeRuleset == "RULESET_EXPANSION_2"' in overview_lua
    assert 'print("RULESET|"' in overview_lua


def test_era_progress_builder_gates_xp1_sections():
    """Era basics are ungated; the XP1 clock/age blocks carry the ruleset guard."""

    lua = build_era_progress_query()
    # XP1 块守卫 (与 overview.py 现行写法一致)
    assert 'activeRuleset ~= "RULESET_STANDARD"' in lua
    # 基础层不得被规则集门控: 每玩家纪元与 GameInfo.Eras 在守卫之外
    assert "p:GetEra()" in lua
    assert "GameInfo.Eras" in lua
    # 不设硬错误路径 (与 dedications 的行为差异即契约)
    import re

    assert not re.search(r"ERR:NO_[A-Z_]*IN_RULESET", lua)
    assert "{_bail" not in lua
    # RULESET 回显契约
    assert 'print("RULESET|"' in lua
