"""Compact reports preserve decision facts, unknowns and legacy narration."""
import json

from civ_mcp import narrate as nr
from civ_mcp.lua.models import CityInfo, UnitInfo, VictoryPlayerProgress, VictoryProgress


def rows(text):
    return [json.loads(line) for line in text.splitlines() if line.startswith("[")]


def test_unit_compact_distinguishes_readiness_and_keeps_qualification_details():
    units = [UnitInfo(1, 0, 'A|"\nB', "UNIT_TEST", 2, 3, 0, 2, 42, 100,
                      combat_strength=40, ranged_strength=55, ready_to_move=None,
                      can_upgrade=True, upgrade_target="", upgrade_cost=0,
                      needs_promotion=True, targets=["native target"],
                      valid_improvements=["IMPROVEMENT_FARM"]),
             UnitInfo(2, 1, "B", "UNIT_TEST", 3, 4, 1.5, 2, 100, 100,
                      ready_to_move=False)]
    result = nr.narrate_units(units, compact=True)
    assert rows(result)[0][2] == 'A|"\nB'
    assert "unknown" in result and "false" in result
    for fact in ("42/100", "1.5/2", "NEEDS PROMOTION", "target unknown; 0g",
                 "CAN ATTACK: native target", "IMPROVEMENT_FARM", "no moves"):
        assert fact in result
    assert nr.narrate_units(units) == nr.narrate_units(units, compact=False)


def test_city_compact_keeps_unsafe_states_and_unknown_not_none():
    city = CityInfo(7, 'City|"\nName', 1, 2, 5, 9, 10, 11, 12, 13, 0, 6, 2, -1,
                    food_surplus=-1, currently_building="CORRUPTED_QUEUE",
                    wall_hp=0, wall_max_hp=100, garrison_hp=55, garrison_max_hp=200,
                    defense_strength=42, loyalty=70, loyalty_per_turn=-2,
                    turns_to_loyalty_flip=35, attack_targets_known=False,
                    attack_targets=["candidate"], pillaged_buildings=["BUILDING_LIBRARY"],
                    pillaged_improvements=["MINE@1,3"],
                    resource_visibility_known=False, unimproved_resources=["COAL@1,4"],
                    archaeology_sites=["ANTIQUITY_SITE@2,4"],
                    religion_majority_source="InGame")
    result = nr.narrate_cities([city], ["City <-> Other: 3 tiles"], compact=True)
    assert rows(result)[0][1] == 'City|"\nName'
    for fact in ("STARVING", "INVALID QUEUE", "Loyalty", "35 turns", "0/100",
                 "55/200", "unknown", "UNVERIFIED TARGET: candidate", "PILLAGED",
                 "Resource advice incomplete", "eligibility unchecked", "not builder tasks",
                 "GameCore freshness unverified", "City <-> Other: 3 tiles"):
        assert fact in result
    city.religion_majority_id = -1
    assert "none (id:-1)" in nr.narrate_cities([city], compact=True)


def test_victory_compact_preserves_zero_unknown_and_statistic_scope():
    player = VictoryPlayerProgress(0, 'Player|"\nName', 80, 0, None, None, 12, 33,
                                   20, 15, 7, science_yield=123.5,
                                   science_rate=0, spaceports=None, has_religion=None)
    report = VictoryProgress([player], religions_founded=None, religions_max=3,
                             diplomatic_vp_needed=20)
    result = nr.narrate_victory_progress(report, compact=True)
    assert "native progress/turn" in result
    assert "science yield" in result and "123.5" in result
    assert "unknown" in result and "0/unknown" in result
    assert "engine statistic; not owned-city count" in result
    assert rows(result)[0][0] == 'Player|"\nName'
    report.enabled_known = True
    report.enabled_victories = {"VICTORY_SCORE"}
    disabled = nr.narrate_victory_progress(report, compact=True)
    assert "SCIENCE" not in disabled and "RELIGION" not in disabled
    assert "SCORE AND CIVILIZATION DATA" in disabled
    assert nr.narrate_victory_progress(report) == nr.narrate_victory_progress(report, compact=False)


def test_compact_city_preserves_nonfinite_display_without_new_format_errors():
    city = CityInfo(7, "City", 1, 2, 5, float("nan"), float("inf"), float("-inf"),
                    -0.01, 0, 0, float("nan"), 2, -1)
    result = rows(nr.narrate_cities([city], compact=True))[0]
    assert result[4] == ["nan", "inf", "-inf", "-0", 0]
    assert result[5] == "nan"
