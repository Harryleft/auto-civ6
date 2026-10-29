"""Facts exposed by public GameState queries and confirmed-turn reports."""

import asyncio

import pytest

from civ_mcp.game_state import GameState
from test_game_state import FakeConn


OVERVIEW = "41|0|Sumeria|Gilgamesh|29|12|13.2|13.5|20|Wheel|Mysticism|0|1|92"


def test_unknown_research_duration_is_not_an_urgent_recommendation():
    from civ_mcp.lua import TechOption, TechCivicStatus
    from civ_mcp.narrate import narrate_tech_civics
    options = [TechOption("Unknown", "TECH_A", 10, 0, -1, False, "", ""),
               TechOption("Known", "TECH_B", 10, 0, 2, False, "", "")]
    text = narrate_tech_civics(TechCivicStatus("None", -1, "None", None, options, []))
    assert "turns unknown" in text and "-1 turns" not in text
    assert "GRAB THIS" not in text and text.index("Known") < text.index("Unknown")
    text = narrate_tech_civics(TechCivicStatus("Mining", -1, "None", None, options, []))
    assert "Researching: Mining (turns unknown)" in text


def test_confirmed_turn_reports_missing_unit_without_inventing_a_cause():
    conn = FakeConn([
        ("write", [OVERVIEW]),
        ("write", ["65536|1|Archer|UNIT_ARCHER|18,18|2/2|100/100"]),
        ("write", []), ("write", []),
        ("write", [OVERVIEW.replace("41|", "42|", 1)]),
        ("write", []), ("write", []), ("write", []),
        ("write", ["TOTAL|0|0"]),
        ("write", ["VICTORY_CONTEXT|42|0", "VICTORY_ENABLED_KNOWN|true", "VICTORY_COMPLETE"]),
    ])
    game = GameState(conn)

    async def report():
        baseline = await game.capture_turn_snapshot()
        return await game.report_turn_advance(41, 42, baseline)

    text = asyncio.run(report())
    assert "Archer (UNIT_ARCHER) no longer appears" in text
    assert "cause unknown" in text
    assert "(18,18)" in text
    assert "killed" not in text and "disbanded" not in text and "captured" not in text
    assert conn.exhausted


def test_trade_destinations_assign_domestic_food_and_production_to_origin():
    from civ_mcp.narrate import narrate_trade_destinations

    conn = FakeConn([("write", [
        "TDEST|Kish|Sumeria|23,16|1|0|0|0|0||0||F1P1G2|",
    ])])
    destinations = asyncio.run(GameState(conn).get_trade_destinations(5))
    text = narrate_trade_destinations(destinations)
    assert "Domestic (food + production to origin):" in text
    assert "Kish" in text and "Food:1" in text and "Prod:1" in text
    assert "to destination" not in text
    assert conn.exhausted


def test_overview_keeps_native_golden_age_separate_from_next_era_thresholds():
    from test_civilization_identity import IdentityConn
    from civ_mcp.narrate import narrate_overview

    conn = IdentityConn()
    conn.lua.execute('''
eras=Game.GetEras()
Game.GetEras=function() return eras end
eras.GetPlayerCurrentScore=function() return 25 end
eras.GetPlayerDarkAgeThreshold=function() return 43 end
eras.GetPlayerGoldenAgeThreshold=function() return 56 end
eras.HasHeroicGoldenAge=function() return false end
eras.HasGoldenAge=function() return true end
eras.HasDarkAge=function() return false end
''')
    overview = asyncio.run(GameState(conn).get_game_overview())
    text = narrate_overview(overview)
    assert "Current age: Golden" in text
    assert "Next-era thresholds" in text and "43" in text and "56" in text
    assert "short of avoiding Dark Age" not in text
    assert "-> GOLDEN AGE" not in text


@pytest.mark.parametrize("heroic,golden,dark,expected", [
    ("false", "false", "false", "Normal"),
    ("false", "true", "false", "Golden"),
    ("true", "true", "false", "Heroic"),
    ("false", "false", "true", "Dark"),
    ("nil", "false", "false", None),
    ('error("read failed")', "false", "false", None),
    ("false", '"false"', "false", None),
    ("false", "true", "true", None),
])
def test_overview_uses_only_complete_native_age_flags(heroic, golden, dark, expected):
    from test_civilization_identity import IdentityConn
    from civ_mcp.narrate import narrate_overview

    conn = IdentityConn()
    conn.lua.execute(f'''
eras=Game.GetEras()
Game.GetEras=function() return eras end
eras.GetPlayerCurrentScore=function() return 999 end
eras.GetPlayerDarkAgeThreshold=function() return 43 end
eras.GetPlayerGoldenAgeThreshold=function() return 56 end
eras.HasHeroicGoldenAge=function() return {heroic} end
eras.HasGoldenAge=function() return {golden} end
eras.HasDarkAge=function() return {dark} end
''')
    overview = asyncio.run(GameState(conn).get_game_overview())
    assert overview.current_age == expected
    text = narrate_overview(overview)
    assert f"Current age: {expected or 'unknown'}" in text
    assert "Next-era thresholds" in text and "Era score: 999" in text
    assert "-> GOLDEN AGE" not in text


def test_missing_native_age_api_keeps_overview_available_and_age_unknown():
    from test_civilization_identity import IdentityConn
    from civ_mcp.narrate import narrate_overview

    overview = asyncio.run(GameState(IdentityConn()).get_game_overview())
    assert overview.current_age is None
    assert "Current age: unknown" in narrate_overview(overview)
    assert overview.gold == 30


@pytest.mark.parametrize("age_suffix", ["", "|unknown", "|invalid"])
def test_legacy_or_unrecognized_age_does_not_infer_age_from_score(age_suffix):
    from test_civilization_identity import ResponseConn
    from civ_mcp.narrate import narrate_overview

    overview = asyncio.run(GameState(ResponseConn([
        OVERVIEW, "ERA|Classical|999|43|56" + age_suffix,
    ])).get_game_overview())
    assert overview.current_age is None
    assert "Current age: unknown" in narrate_overview(overview)
    assert "-> GOLDEN AGE" not in narrate_overview(overview)
