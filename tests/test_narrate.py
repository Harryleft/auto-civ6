"""Victory output reports game facts without assigning strategic scores."""

from civ_mcp import lua as lq
from civ_mcp.narrate import narrate_map, narrate_victory_progress


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
        "majority=Buddhism", "native religion cities 3 (engine statistic; not owned-city count)", "9/20 VP", "score 123",
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


def map_tile(yields, **changes):
    return lq.TileInfo(**(dict(x=1,y=2,terrain="TERRAIN_PLAINS",feature=None,resource=None,
                              is_hills=False,is_river=False,is_coastal=False,improvement=None,
                              owner_id=-1,yields=yields) | changes))


def test_map_yields_keep_fractional_and_negative_values():
    text=narrate_map([map_tile((4.75,2.5,-0.75,0.125,-0.5,0.000000123456789))])
    assert "{F:4.75 P:2.5 G:-0.75 S:0.125 C:-0.5 Fa:1.23456789e-07}" in text


def test_map_integers_and_zeroes_stay_compact_with_explicit_source_boundary():
    text=narrate_map([map_tile((4.0,2.0,0,0,0,0)),map_tile((0,0,0,0,0,0),x=2)])
    assert "{F:4 P:2}" in text and "{F:0 P:0}" in text
    assert "F:4.0" not in text and "G:0" not in text
    assert text.count("Yield source:")==1
    for fact in ("GameCore_Tuner Plot:GetYield", "modifier completeness/native age unverified",
                 "not interchangeable with InGame city-worked yields", "unlisted G/S/C/Fa=0"):
        assert fact in text


def test_map_unknown_fog_and_ownership_are_preserved():
    text=narrate_map([map_tile(None,visibility="revealed",owner_id=3,owner_name="Canada")])
    assert "(owned by Canada) [fog]" in text
    assert "{F:" not in text and "no yield block=unknown" in text
    assert narrate_map([])=="No tiles."
