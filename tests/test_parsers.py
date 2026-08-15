"""Unit tests for Lua response parsers.

Each parser takes list[str] (pipe-delimited lines from Lua print()) and returns
typed dataclasses. These tests verify the parsing logic with realistic fixtures.
"""

import pytest

from civ_mcp.lua.overview import parse_gameover_response, parse_overview_response
from civ_mcp.lua.units import (
    parse_combat_estimate,
    parse_threat_scan_response,
    parse_units_response,
)
from civ_mcp.lua.cities import parse_cities_response
from civ_mcp.lua.map import parse_map_response
from civ_mcp.lua.notifications import parse_end_turn_blocking


# ---------------------------------------------------------------------------
# parse_gameover_response
# ---------------------------------------------------------------------------


class TestParseGameover:
    def test_game_active(self):
        assert parse_gameover_response(["GAME_ACTIVE"]) is None

    def test_victory(self):
        lines = ["GAME_OVER|VICTORY|Gandhi|SCIENCE|alive|Gandhi"]
        result = parse_gameover_response(lines)
        assert result is not None
        assert result.is_game_over is True
        assert result.is_defeat is False
        assert result.winner_name == "Gandhi"
        assert result.victory_type == "SCIENCE"
        assert result.player_alive is True
        assert result.winner_leader == "Gandhi"

    def test_defeat(self):
        lines = ["GAME_OVER|DEFEAT|Gilgamesh|DOMINATION|dead|Gilgamesh"]
        result = parse_gameover_response(lines)
        assert result is not None
        assert result.is_defeat is True
        assert result.victory_type == "DOMINATION"
        assert result.player_alive is False

    def test_empty_lines(self):
        assert parse_gameover_response([]) is None

    def test_minimal_fields(self):
        """Only 2 fields — optional fields should get defaults."""
        lines = ["GAME_OVER|VICTORY"]
        result = parse_gameover_response(lines)
        assert result is not None
        assert result.winner_name == "Unknown"
        assert result.victory_type == "Unknown"


# ---------------------------------------------------------------------------
# parse_overview_response
# ---------------------------------------------------------------------------


class TestParseOverview:
    # Minimal 19-field main line: turn|pid|civ|leader|gold|gpt|sci|cul|faith|
    #   research|civic|cities|units|score|favor|fpt|pop|gold_income|maintenance
    MAIN_LINE = (
        "42|0|CIVILIZATION_INDIA|Gandhi|500.0|10.5|25.0|18.0|12.0|"
        "TECH_POTTERY|CIVIC_CODE_OF_LAWS|3|5|120|10|2|15|35.0|24.5"
    )

    def test_basic_fields(self):
        result = parse_overview_response([self.MAIN_LINE])
        assert result.turn == 42
        assert result.player_id == 0
        assert result.civ_name == "CIVILIZATION_INDIA"
        assert result.leader_name == "Gandhi"
        assert result.gold == 500.0
        assert result.gold_per_turn == 10.5
        assert result.science_yield == 25.0
        assert result.culture_yield == 18.0
        assert result.faith == 12.0
        assert result.current_research == "TECH_POTTERY"
        assert result.current_civic == "CIVIC_CODE_OF_LAWS"
        assert result.num_cities == 3
        assert result.num_units == 5
        assert result.score == 120

    def test_rankings(self):
        lines = [
            self.MAIN_LINE,
            "RANK|0|India|120",
            "RANK|1|Sumeria|95",
        ]
        result = parse_overview_response(lines)
        assert result.rankings is not None
        assert len(result.rankings) == 2
        assert result.rankings[0].civ_name == "India"
        assert result.rankings[1].score == 95

    def test_era_info(self):
        lines = [self.MAIN_LINE, "ERA|Classical|15|12|24"]
        result = parse_overview_response(lines)
        assert result.era_name == "Classical"
        assert result.era_score == 15
        assert result.era_dark_threshold == 12
        assert result.era_golden_threshold == 24

    def test_exploration(self):
        lines = [self.MAIN_LINE, "EXPLORE|200|1000"]
        result = parse_overview_response(lines)
        assert result.explored_land == 200
        assert result.total_land == 1000

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="Empty overview response"):
            parse_overview_response([])

    def test_too_few_fields_raises(self):
        with pytest.raises(ValueError, match="expected >=14"):
            parse_overview_response(["1|2|3"])


# ---------------------------------------------------------------------------
# parse_units_response
# ---------------------------------------------------------------------------


class TestParseUnits:
    # Fields: uid|index|name|type|x,y|moves/max|hp/max|cs|rs|charges|targets|promo|upgrade|upgrade_target|upgrade_cost|valid_imps|religion|fortify_turns|can_fortify
    WARRIOR = "0|0|Warrior|UNIT_WARRIOR|10,24|2.0/2.0|100/100|20|0|0||0|0|||||2|1"
    BUILDER = "1|1|Builder|UNIT_BUILDER|12,22|2.0/2.0|100/100|0|0|3||0|0|||IMPROVEMENT_FARM;IMPROVEMENT_MINE|"

    def test_basic_warrior(self):
        units = parse_units_response([self.WARRIOR])
        assert len(units) == 1
        u = units[0]
        assert u.unit_id == 0
        assert u.name == "Warrior"
        assert u.unit_type == "UNIT_WARRIOR"
        assert u.x == 10
        assert u.y == 24
        assert u.moves_remaining == 2.0
        assert u.health == 100
        assert u.combat_strength == 20
        assert u.ranged_strength == 0
        assert u.build_charges == 0
        assert u.fortify_turns == 2
        assert u.can_fortify is True

    def test_builder_with_improvements(self):
        units = parse_units_response([self.BUILDER])
        u = units[0]
        assert u.build_charges == 3
        assert "IMPROVEMENT_FARM" in u.valid_improvements
        assert "IMPROVEMENT_MINE" in u.valid_improvements

    def test_multiple_units(self):
        units = parse_units_response([self.WARRIOR, self.BUILDER])
        assert len(units) == 2

    def test_short_line_skipped(self):
        units = parse_units_response(["too|few|fields"])
        assert len(units) == 0

    def test_targets(self):
        line = "2|2|Archer|UNIT_ARCHER|5,5|2.0/2.0|100/100|25|25|0|14,6;15,7|0|0|||"
        units = parse_units_response([line])
        assert units[0].targets == ["14,6", "15,7"]


# ---------------------------------------------------------------------------
# parse_combat_estimate
# ---------------------------------------------------------------------------


class TestParseCombat:
    def test_melee_combat(self):
        # ESTIMATE|att_type|def_type|eff_att_cs|eff_def_cs|is_ranged|modifiers|my_hp|enemy_hp
        line = "ESTIMATE|UNIT_WARRIOR|UNIT_WARRIOR|20|20|0|Flanking +2;Fortified -4|100|100"
        result = parse_combat_estimate([line], att_cs=20, def_cs=20)
        assert result is not None
        assert result.attacker_type == "UNIT_WARRIOR"
        assert result.defender_type == "UNIT_WARRIOR"
        assert result.attacker_cs == 20
        assert result.defender_cs == 20
        assert result.is_ranged is False
        assert "Flanking +2" in result.modifiers
        assert "Fortified -4" in result.modifiers
        # Equal CS: damage should be base (24) for both sides
        assert result.est_damage_to_defender == 24
        assert result.est_damage_to_attacker == 24

    def test_ranged_no_counter(self):
        line = "ESTIMATE|UNIT_ARCHER|UNIT_WARRIOR|25|20|1||100|100"
        result = parse_combat_estimate([line], att_cs=25, def_cs=20)
        assert result is not None
        assert result.is_ranged is True
        assert result.est_damage_to_attacker == 0  # ranged = no counter
        assert result.est_damage_to_defender > 24  # attacker stronger

    def test_no_estimate_line(self):
        assert parse_combat_estimate(["some other line"], att_cs=20, def_cs=20) is None


# ---------------------------------------------------------------------------
# parse_threat_scan_response
# ---------------------------------------------------------------------------


class TestParseThreatScan:
    def test_standard_threat(self):
        line = "THREAT|63|Barbarian|UNIT_WARRIOR|15,30|100/100|CS:20|RS:0|dist:3|cs:0|uid:42|city:7|citydist:4|war:1|cities:7=4,8=6"
        threats = parse_threat_scan_response([line])
        assert len(threats) == 1
        t = threats[0]
        assert t.owner_id == 63
        assert t.owner_name == "Barbarian"
        assert t.unit_type == "UNIT_WARRIOR"
        assert t.x == 15
        assert t.y == 30
        assert t.hp == 100
        assert t.combat_strength == 20
        assert t.distance == 3
        assert t.unit_id == 42
        assert t.nearest_city_id == 7
        assert t.distance_to_city == 4
        assert t.is_at_war is True
        assert t.city_distances == ((7, 4), (8, 6))

    def test_city_state_threat(self):
        line = (
            "THREAT|10|Zanzibar|UNIT_ARCHER|8,12|80/100|CS:25|RS:25|dist:2|cs:1|uid:5|city:2|citydist:2|war:1|cities:2=2"
        )
        threats = parse_threat_scan_response([line])
        assert threats[0].is_city_state is True
        assert threats[0].nearest_city_id == 2
        assert threats[0].distance_to_city == 2

    def test_explicit_no_threats_is_empty(self):
        assert parse_threat_scan_response(["NO_THREATS"]) == []

    def test_missing_scan_status_is_not_treated_as_empty(self):
        with pytest.raises(ValueError, match="neither THREAT nor NO_THREATS"):
            parse_threat_scan_response(["SOME_OTHER_LINE", "ALSO_NOT_THREAT"])

    def test_lua_error_is_not_treated_as_empty(self):
        with pytest.raises(ValueError, match="threat scan failed"):
            parse_threat_scan_response(["ERR:QUERY_FAILED"])

    def test_conflicting_empty_and_threat_markers_are_rejected(self):
        line = "THREAT|63|Barbarian|UNIT_WARRIOR|15,30|100/100|CS:20|RS:0|dist:3"
        with pytest.raises(ValueError, match="both THREAT and NO_THREATS"):
            parse_threat_scan_response(["NO_THREATS", line])

    def test_legacy_format(self):
        """Older format without owner_id/owner_name."""
        line = "THREAT|UNIT_WARRIOR|15,30|100/100|CS:20|RS:0|dist:3"
        threats = parse_threat_scan_response([line])
        assert len(threats) == 1
        assert threats[0].unit_type == "UNIT_WARRIOR"
        assert threats[0].x == 15


# ---------------------------------------------------------------------------
# parse_cities_response
# ---------------------------------------------------------------------------


class TestParseCities:
    # 30 pipe-separated fields: id|name|x,y|pop|food|prod|gold|sci|cul|faith|
    #   housing|amenities|turns_grow|building|prod_turns|defense|gar_hp|wall_hp|
    #   attack_targets|pillaged_districts|districts|loyalty|loyalty_max|loyalty_pt|
    #   turns_flip|food_surplus|food_stored|growth_threshold|pillaged_buildings|garrison
    CITY_LINE = (
        "0|Delhi|10,24|4|8.0|5.0|3.0|2.0|1.5|0.0|"
        "6.0|3|12|BUILDING_GRANARY|5|"
        "15|200/200|0/0|"
        "||DISTRICT_CITY_CENTER;DISTRICT_CAMPUS|"
        "100.0|100.0|5.0|0|3.5|20.0|36||Warrior"
    )

    def test_basic_city(self):
        cities, distances = parse_cities_response([self.CITY_LINE])
        assert len(cities) == 1
        c = cities[0]
        assert c.city_id == 0
        assert c.name == "Delhi"
        assert c.x == 10
        assert c.y == 24
        assert c.population == 4
        assert c.food == 8.0
        assert c.production == 5.0
        assert c.currently_building == "BUILDING_GRANARY"
        assert c.production_turns_left == 5

    def test_districts_parsed(self):
        cities, _ = parse_cities_response([self.CITY_LINE])
        assert "DISTRICT_CITY_CENTER" in cities[0].districts
        assert "DISTRICT_CAMPUS" in cities[0].districts

    def test_distances(self):
        lines = [self.CITY_LINE, "DIST|Delhi|Agra|8"]
        cities, distances = parse_cities_response(lines)
        assert len(cities) == 1
        assert len(distances) == 1
        assert "8 tiles" in distances[0]

    def test_short_line_skipped(self):
        cities, _ = parse_cities_response(["too|short"])
        assert len(cities) == 0


# ---------------------------------------------------------------------------
# parse_map_response
# ---------------------------------------------------------------------------


class TestParseMap:
    # Fields: x,y|terrain|feature|resource|hills|river|coastal|improvement|owner|units|
    #   visibility|fresh_water|yields|district|owner_name|own_units|route|move_cost
    PLAINS_TILE = "10,24|TERRAIN_PLAINS|none|none|0|1|0|none|-1|none|visible|1|2,1,0,0,0,0|none||||-1|1"
    HILLS_WITH_MINE = (
        "12,22|TERRAIN_PLAINS|none|RESOURCE_IRON:RESOURCECLASS_STRATEGIC|1|0|0|"
        "IMPROVEMENT_MINE|0|none|visible|0|1,3,0,0,0,0|none|India|none|-1|2"
    )

    def test_basic_tile(self):
        tiles = parse_map_response([self.PLAINS_TILE])
        assert len(tiles) == 1
        t = tiles[0]
        assert t.x == 10
        assert t.y == 24
        assert t.terrain == "TERRAIN_PLAINS"
        assert t.feature is None
        assert t.resource is None
        assert t.is_hills is False
        assert t.is_river is True
        assert t.owner_id == -1
        assert t.visibility == "visible"
        assert t.is_fresh_water is True

    def test_resource_with_class(self):
        tiles = parse_map_response([self.HILLS_WITH_MINE])
        t = tiles[0]
        assert t.resource == "RESOURCE_IRON"
        assert t.resource_class == "strategic"
        assert t.is_hills is True
        assert t.improvement == "IMPROVEMENT_MINE"
        assert t.owner_name == "India"
        assert t.movement_cost == 2

    def test_pillaged_improvement(self):
        line = "5,5|TERRAIN_GRASSLAND|none|none|0|0|0|IMPROVEMENT_FARM:PILLAGED|0|none|visible|0|0,0,0,0,0,0|none||||-1|1"
        tiles = parse_map_response([line])
        assert tiles[0].improvement == "IMPROVEMENT_FARM"
        assert tiles[0].is_pillaged is True

    def test_yields_parsing(self):
        tiles = parse_map_response([self.PLAINS_TILE])
        assert tiles[0].yields == (2, 1, 0, 0, 0, 0)

    def test_short_line_skipped(self):
        tiles = parse_map_response(["too|few|fields"])
        assert len(tiles) == 0


# ---------------------------------------------------------------------------
# parse_end_turn_blocking
# ---------------------------------------------------------------------------


class TestParseEndTurnBlocking:
    def test_none(self):
        assert parse_end_turn_blocking(["NONE"]) == []

    def test_single_blocker(self):
        blockers = parse_end_turn_blocking(
            ["BLOCKING|UNIT_NEEDS_ORDERS|Warrior at 10,24"]
        )
        assert len(blockers) == 1
        assert blockers[0] == ("UNIT_NEEDS_ORDERS", "Warrior at 10,24")

    def test_multiple_blockers(self):
        lines = [
            "BLOCKING|UNIT_NEEDS_ORDERS|Warrior at 10,24",
            "BLOCKING|CHOOSE_PRODUCTION|Delhi needs production",
        ]
        blockers = parse_end_turn_blocking(lines)
        assert len(blockers) == 2

    def test_empty_lines(self):
        assert parse_end_turn_blocking([]) == []


def test_diary_query_uses_real_heroic_age_api() -> None:
    """Diary PLAYER 行的英雄时代语义回归。

    build_diary_full_query 曾用不存在的方法名 HasHeroicAge(i), pcall 吞错
    后英雄黄金时代在 diary 中恒记为 NORMAL。正确 API 是 HasHeroicGoldenAge
    (lua/governance.py 同款), 修复后 HEROIC 才可能被记录。
    """
    from civ_mcp.lua.overview import build_diary_full_query

    query = build_diary_full_query()
    assert "eraManager:HasHeroicGoldenAge(i)" in query
    assert "HasHeroicAge(" not in query


class TestParseEraProgress:
    def test_xp2_full_sample(self):
        from civ_mcp.lua.eras import parse_era_progress_response

        lines = ["RULESET|RULESET_EXPANSION_2", "LOCAL|0"]
        lines += [f"ERAS|idx{i}|ERA_T{i}|Era {i}" for i in range(9)]
        lines += [
            "GAMEERA|2|ERA_MEDIEVAL|Medieval|false",
            "CLOCK|45|7|85|105|3|5",
            "PERA|0|Rome|2|ERA_MEDIEVAL|Medieval|Normal|15",
            "PERA|1|Egypt|2|ERA_MEDIEVAL|Medieval|Normal|9",
            "PERA|2|Kongo|1|ERA_CLASSICAL|Classical|Dark|2",
            "AGE|15|12|24|0|5",
            "AGEDETAIL|Built a wonder|5",
            "AGEDETAIL|First to meet a city-state|3",
            "---END---",
        ]
        ep = parse_era_progress_response(lines)

        assert ep.ruleset == "RULESET_EXPANSION_2"
        assert ep.ages_supported is True
        assert ep.current_era_index == 2
        assert ep.current_era_type == "ERA_MEDIEVAL"
        assert ep.current_era_name == "Medieval"
        assert ep.final_era is False
        assert len(ep.era_sequence) == 9
        assert ep.era_sequence[0].era_index == 0
        assert ep.era_sequence[0].era_type == "ERA_T0"
        assert ep.era_start_turn == 45
        assert ep.next_era_countdown == 7
        assert ep.min_end_turn == 85
        assert ep.max_end_turn == 105
        assert ep.players_more_advanced == 3
        assert ep.players_as_or_less_advanced == 5
        assert [p.is_local for p in ep.players] == [True, False, False]
        assert ep.players[2].age == "Dark"
        assert ep.players[2].era_score == 2
        assert ep.local_age is not None
        assert ep.local_age.era_score == 15
        assert ep.local_age.dark_threshold == 12
        assert ep.local_age.golden_threshold == 24
        assert ep.local_age.threshold_baseline == 0
        assert ep.local_age.previous_era_score == 5
        assert ep.local_age.score_breakdown == [
            ("Built a wonder", 5),
            ("First to meet a city-state", 3),
        ]

    def test_standard_sample_has_no_xp1_rows(self):
        from civ_mcp.lua.eras import parse_era_progress_response

        lines = ["RULESET|RULESET_STANDARD", "LOCAL|0"]
        lines += [f"ERAS|idx{i}|ERA_T{i}|Era {i}" for i in range(8)]
        lines += [
            "GAMEERA|1|ERA_CLASSICAL|Classical|false",
            "PERA|0|Rome|1|ERA_CLASSICAL|Classical|-|-1",
            "PERA|1|Egypt|1|ERA_CLASSICAL|Classical|-|-1",
            "---END---",
        ]
        ep = parse_era_progress_response(lines)

        assert ep.ruleset == "RULESET_STANDARD"
        assert ep.ages_supported is False
        assert len(ep.era_sequence) == 8
        assert ep.era_start_turn is None
        assert ep.next_era_countdown is None
        assert ep.min_end_turn is None
        assert ep.max_end_turn is None
        assert ep.players_more_advanced is None
        assert ep.players_as_or_less_advanced is None
        assert ep.local_age is None
        for player in ep.players:
            assert player.age is None
            assert player.era_score is None
            assert player.era_type == "ERA_CLASSICAL"

    def test_sentinels_malformed_lines_and_missing_ruleset(self):
        from civ_mcp.lua.eras import parse_era_progress_response

        ep = parse_era_progress_response(
            [
                "RULESET|RULESET_EXPANSION_2",
                "CLOCK|-1|-1|-1|-1|-1|-1",
                "GAMEERA|2|ERA_MEDIEVAL|Medieval|false",
                "GAMEERA|bad",
                "PERA|0|Rome|2|ERA_MEDIEVAL|Medieval|Normal|15",
                "PERA|x|Rome|2|ERA_MEDIEVAL|Medieval|Normal|15",
                "garbage",
            ]
        )
        assert ep.era_start_turn is None
        assert ep.next_era_countdown is None
        assert ep.current_era_index == 2
        assert len(ep.players) == 1

        import pytest

        with pytest.raises(ValueError, match="missing RULESET"):
            parse_era_progress_response(["GAMEERA|1|ERA_CLASSICAL|Classical|false"])

    def test_age_detail_source_text_is_sanitized_in_builder(self):
        from civ_mcp.lua.eras import build_era_progress_query

        query = build_era_progress_query()
        assert 'gsub("[|,]","/")' in query
