"""Victory output reports game facts without assigning strategic scores."""

from civ_mcp import lua as lq
from civ_mcp.narrate import narrate_victory_progress


def player(**changes):
    fields = dict(player_id=7, name="Rome", score=123, science_vp=12,
                  science_vp_needed=50, diplomatic_vp=9, tourism=34,
                  military_strength=456, techs_researched=22, civics_completed=19,
                  religion_cities=3, staycationers=81, has_religion=True,
                  num_cities=5, science_yield=67.5, culture_yield=41.2,
                  gold_yield=-3.5, spaceports=2, space_progress="12/50")
    return lq.VictoryPlayerProgress(**(fields | changes))


def test_victory_progress_reports_observed_fields_without_strategy_score():
    vp = lq.VictoryProgress(
        players=[player()],
        our_tourists_from={"Greece": 14},
        their_staycationers={"Greece": 52},
        capitals_held={"Rome": False},
        religion_majority={"Rome": "Buddhism"},
        religion_founded_names={"Rome": "Buddhism"},
        diplomatic_vp_needed=20,
        religions_founded=3,
        religions_max=5,
        demographics={"Land": lq.DemographicEntry(2, 50, 80, 60, 40)},
        space_projects=[lq.SpaceProject("PROJECT_LAUNCH_MOON_LANDING", "Moon Landing",
                                       "building", 35, 7, 600, "TECH_SATELLITES", True, "Rome")],
    )
    result = narrate_victory_progress(vp)
    for fact in (
        "Rome: 12/50 VP", "22 techs", "2 spaceports", "Moon Landing: building",
        "35% | 7 turns | Rome", "prerequisite TECH_SATELLITES (researched=True)",
        "cost 600", "original capital lost", "tourism 34 | domestic tourists 81",
        "Our tourists from Greece: 14 | their domestic tourists: 52", "founded 3/5",
        "majority=Buddhism", "religion cities 3", "9/20 VP", "score 123",
        "science 67.5", "culture 41.2", "gold -3.5", "military 456", "civics 19",
        "Land: rank 2 | ours 50 | best 80 | average 60 | worst 40",
    ):
        assert fact in result
    for removed in ("ASSESSMENT", "RECOMMENDED", "viability", "pivot", "turn 500", "4 space projects"):
        assert removed not in result


def test_unreported_capital_and_culture_data_stays_unknown():
    vp = lq.VictoryProgress(players=[player()], our_tourists_from={"Greece": 14})
    result = narrate_victory_progress(vp)
    assert "original capital unknown" in result
    assert "their domestic tourists: unknown" in result
    assert "Enabled victory types: not reported" in result


def test_enabled_victories_control_condition_sections():
    vp = lq.VictoryProgress(players=[player()], enabled_victories={"VICTORY_TECHNOLOGY"})
    result = narrate_victory_progress(vp)
    assert "Enabled: Science" in result
    assert "SCIENCE" in result
    for disabled in ("DOMINATION", "\nCULTURE", "\nRELIGION", "\nDIPLOMATIC"):
        assert disabled not in result
    assert "score 123" in result


def test_empty_victory_data():
    assert narrate_victory_progress(lq.VictoryProgress(players=[])) == "No victory data available."
