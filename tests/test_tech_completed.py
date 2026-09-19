"""Regression tests for the completed technology name list."""

from civ_mcp.lua.tech import build_tech_civics_query, parse_tech_civics_response
from civ_mcp.narrate import narrate_tech_civics


def test_parse_and_narrate_completed_technology_names() -> None:
    status = parse_tech_civics_response(
        [
            "CURRENT|Writing|3|Code of Laws|2",
            "COMPLETED_TECH|采矿",
            "COMPLETED_TECH|制陶",
            "COMPLETED_CIVIC|法典",
            "COMPLETED|2|1",
        ]
    )

    assert status.completed_tech_count == 2
    assert status.completed_techs == ["采矿", "制陶"]
    assert status.completed_civics == ["法典"]
    rendered = narrate_tech_civics(status)
    assert "已完成科技（完整名称）：" in rendered
    assert "采矿" in rendered
    assert "制陶" in rendered
    assert "已完成市政（完整名称）：" in rendered
    assert "法典" in rendered


def test_parse_civic_unlock_mapping_and_eta_fields() -> None:
    status = parse_tech_civics_response(
        [
            "CURRENT|Writing|3|Craftsmanship|2",
            "CIVIC|Craftsmanship|CIVIC_CRAFTSMANSHIP|100|50|5|BOOSTED|trigger|CIVIC_CODE_OF_LAWS|ERA_ANCIENT|POLICY:POLICY_AGOGE:Agoge, GOVERNMENT:GOVERNMENT_CHIEFDOM:Chiefdom",
            "COMPLETED|2|1",
        ]
    )

    civic = status.available_civics[0]
    assert civic.turns == 5
    assert civic.unlocks == (
        "POLICY:POLICY_AGOGE:Agoge, GOVERNMENT:GOVERNMENT_CHIEFDOM:Chiefdom"
    )
    assert "POLICY_AGOGE" in narrate_tech_civics(status)
    assert "GOVERNMENT_CHIEFDOM" in narrate_tech_civics(status)


def test_query_emits_each_completed_technology_name() -> None:
    query = build_tech_civics_query()

    assert "te:HasTech(tech.Index)" in query
    assert 'print("COMPLETED_TECH|" .. Locale.Lookup(tech.Name)' in query


def test_query_keeps_missing_eta_helpers_as_explicit_unknowns() -> None:
    query = build_tech_civics_query()

    assert "pcall(function() techTurns = te:GetTurnsToResearch(techIdx) end)" in query
    assert "pcall(function() civicTurns = cu:GetTurnsLeftOnCurrentCivic() end)" in query


def test_query_emits_completed_civics_unlocks_and_remaining_cost_eta() -> None:
    query = build_tech_civics_query()

    assert 'print("COMPLETED_CIVIC|" .. Locale.Lookup(civic.Name)' in query
    assert "remainingCost = math.max(cost - currentProg, 0)" in query
    assert "math.ceil(remainingCost / cultureYield)" in query
    assert "GameInfo.Policies()" in query
    assert "GameInfo.Governments()" in query
    assert '"POLICY:" .. policy.PolicyType' in query
    assert '"GOVERNMENT:" .. government.GovernmentType' in query
