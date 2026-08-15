"""Regression tests for the completed technology name list."""

from civ_mcp.lua.tech import build_tech_civics_query, parse_tech_civics_response
from civ_mcp.narrate import narrate_tech_civics


def test_parse_and_narrate_completed_technology_names() -> None:
    status = parse_tech_civics_response(
        [
            "CURRENT|Writing|3|Code of Laws|2",
            "COMPLETED_TECH|采矿",
            "COMPLETED_TECH|制陶",
            "COMPLETED|2|1",
        ]
    )

    assert status.completed_tech_count == 2
    assert status.completed_techs == ["采矿", "制陶"]
    rendered = narrate_tech_civics(status)
    assert "已完成科技（完整名称）：" in rendered
    assert "采矿" in rendered
    assert "制陶" in rendered


def test_query_emits_each_completed_technology_name() -> None:
    query = build_tech_civics_query()

    assert "te:HasTech(tech.Index)" in query
    assert 'print("COMPLETED_TECH|" .. Locale.Lookup(tech.Name)' in query
