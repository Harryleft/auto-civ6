"""Tests for the one-shot Great People overview query."""

from civ_mcp import narrate
from civ_mcp.lua.great_people import (
    build_great_people_overview_query,
    parse_great_people_overview_response,
)


def test_overview_query_covers_official_api() -> None:
    query = build_great_people_overview_query()

    assert "GetPastTimeline" in query
    assert "GetPointsPerTurn" in query
    assert "CountPeopleReceivedByPlayer" in query
    assert "Game.GetPlayers({Major = true, Alive = true})" in query
    assert "HasMet" in query
    assert "GP_CLASS|" in query
    assert "GP_HIST|" in query
    assert "GP_UNIT|" in query
    assert "GP|" in query
    assert "ERR:NO_GP_SYSTEM" in query
    assert 'print("---END---")' in query


def test_parse_overview_happy_path() -> None:
    ov = parse_great_people_overview_response(
        [
            "GP_CLASS|Great Scientist|GREAT_PERSON_CLASS_SCIENTIST|0|Greece|34|6|2",
            "GP_CLASS|Great Scientist|GREAT_PERSON_CLASS_SCIENTIST|1|France|51|7|3",
            "GP_CLASS|Great Scientist|GREAT_PERSON_CLASS_SCIENTIST|2|Unmet|12|1|0",
            "GP_CLASS|Great Engineer|GREAT_PERSON_CLASS_ENGINEER|0|Greece|9|2|1",
            "GP|Great Scientist|Hypatia|Ancient Era|40|Unclaimed|34|Boosts Libraries.|gold:600,faith:1000,recruit:false|2",
            "GP_HIST|Ada Lovelace|Great Scientist|Industrial Era|France|87|5",
            "GP_HIST|Euclid|Great Scientist|Classical Era|Greece|31|1",
            "GP_UNIT|42|Hypatia|GREAT_PERSON_CLASS_SCIENTIST|34,21|1",
            "---END---",
        ]
    )

    assert len(ov.standings) == 2
    scientist = ov.standings[0]
    assert scientist.class_name == "Great Scientist"
    assert scientist.class_type == "GREAT_PERSON_CLASS_SCIENTIST"
    assert len(scientist.entries) == 3
    assert scientist.entries[1].player_name == "France"
    assert scientist.entries[1].points_total == 51
    assert scientist.entries[1].points_per_turn == 7
    assert scientist.entries[1].instances_earned == 3
    assert scientist.entries[2].player_name == "Unmet"
    assert ov.standings[1].entries[0].player_id == 0
    assert len(ov.timeline) == 1
    assert ov.timeline[0].individual_name == "Hypatia"
    assert ov.timeline[0].gold_cost == 600
    assert ov.timeline[0].faith_cost == 1000
    assert ov.timeline[0].individual_id == 2
    assert len(ov.history) == 2
    assert ov.history[0].individual_name == "Ada Lovelace"
    assert ov.history[0].class_name == "Great Scientist"
    assert ov.history[0].era_name == "Industrial Era"
    assert ov.history[0].claimant == "France"
    assert ov.history[0].turn_granted == 87
    assert ov.history[0].individual_id == 5
    assert len(ov.own_units) == 1
    assert ov.own_units[0].unit_id == 42
    assert ov.own_units[0].name == "Hypatia"
    assert ov.own_units[0].gp_class == "GREAT_PERSON_CLASS_SCIENTIST"
    assert (ov.own_units[0].x, ov.own_units[0].y) == (34, 21)
    assert ov.own_units[0].charges == 1


def test_parse_overview_skips_malformed_lines() -> None:
    ov = parse_great_people_overview_response(
        [
            "GP_CLASS|Great Scientist|GREAT_PERSON_CLASS_SCIENTIST|0|Greece|34|6",  # too short
            "GP_CLASS|Great Scientist|GREAT_PERSON_CLASS_SCIENTIST|0|Greece|34|6|2",
            "GP_HIST|Broken Row",
            "GP_UNIT|42|Hypatia|GREAT_PERSON_CLASS_SCIENTIST|no-coords|1",
            "GP_UNIT|43|Euclid|GREAT_PERSON_CLASS_SCIENTIST|34,21|not-a-charge",
            "---END---",
        ]
    )

    assert len(ov.standings) == 1
    assert len(ov.standings[0].entries) == 1
    assert not ov.history
    assert not ov.own_units


def test_narrate_overview_sections_and_masks() -> None:
    ov = parse_great_people_overview_response(
        [
            "GP_CLASS|Great Scientist|GREAT_PERSON_CLASS_SCIENTIST|0|Greece|34|6|2",
            "GP_CLASS|Great Scientist|GREAT_PERSON_CLASS_SCIENTIST|1|Unmet|51|7|3",
            "GP|Great Scientist|Hypatia|Ancient Era|40|Unclaimed|40|Boosts Libraries.|gold:2000000000,faith:1000,recruit:true|2",
            "GP_HIST|Ada Lovelace|Great Scientist|Industrial Era|France|87|5",
            "GP_UNIT|42|Hypatia|GREAT_PERSON_CLASS_SCIENTIST|34,21|1",
            "---END---",
        ]
    )
    text = narrate.narrate_great_people_overview(ov)

    assert "=== Great People Overview ===" in text
    assert "YOU 34/6 (2)" in text
    assert "Unmet 51/7 (3)" in text
    assert "[CAN RECRUIT]" in text
    assert "Patronize:" in text
    assert "2000000000g" not in text
    assert "claimed by France" in text
    assert "get_gp_advisor(unit_id=42) for placement" in text


def test_narrate_overview_degrades_without_history() -> None:
    ov = parse_great_people_overview_response(
        [
            "GP_CLASS|Great Scientist|GREAT_PERSON_CLASS_SCIENTIST|0|Greece|34|6|2",
            "---END---",
        ]
    )
    text = narrate.narrate_great_people_overview(ov)

    assert "No history available." in text
    assert "(none)" in text
    assert "No Great People in timeline." in text
