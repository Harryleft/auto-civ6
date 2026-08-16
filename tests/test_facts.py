"""双轨输出契约（civ_mcp.facts）与观测正常化器信封路径的回归测试。

验证：
1. 信封构造：v/tool/turn/source/coverage/facts/narrated 齐备，JSON 可解析。
2. parse_envelope 只认双轨信封，不误认普通 JSON 或叙述文本。
3. normalize_tool_result 对信封：metrics 与旧叙述路径一致，facts 用
   结构化精确值覆盖（999 距离不再被叙述格式吞掉）。
4. _append_belief_context 对信封结果合并进 belief_context 结构，不再
   尾部追加文本（保持 JSON 可解析）；纯文本结果仍走旧追加路径。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from civ6_belief_engine.belief_engine import normalize_tool_result
from civ_mcp import facts as fact_view
from civ_mcp import lua as lq
from civ_mcp import narrate as nr
from civ_mcp.belief_mode import BeliefMode
from civ_mcp.lua.models import GPClassStanding, GPPlayerPoints, GreatPeopleOverview
from civ_mcp.server import pipeline as server_module
from civ_mcp.server.pipeline import _append_belief_context


def _unit(**overrides) -> lq.UnitInfo:
    base = dict(
        unit_id=131073,
        unit_index=1,
        name="勇士",
        unit_type="UNIT_WARRIOR",
        x=32,
        y=37,
        moves_remaining=0.0,
        max_moves=2.0,
        health=100,
        max_health=100,
        combat_strength=20,
    )
    base.update(overrides)
    return lq.UnitInfo(**base)


def _city(**overrides) -> lq.CityInfo:
    base = dict(
        city_id=65536,
        name="巴黎",
        x=43,
        y=38,
        population=4,
        food=11.0,
        production=15.0,
        gold=11.0,
        science=5.0,
        culture=5.0,
        faith=0.0,
        housing=8.0,
        amenities=5,
        turns_to_grow=12,
    )
    base.update(overrides)
    return lq.CityInfo(**base)


def _tile(**overrides) -> lq.TileInfo:
    base = dict(
        x=31,
        y=33,
        terrain="TERRAIN_PLAINS",
        feature=None,
        resource=None,
        is_hills=False,
        is_river=False,
        is_coastal=False,
        improvement=None,
        owner_id=-1,
        visibility="revealed",
    )
    base.update(overrides)
    return lq.TileInfo(**base)


class TestEnvelopeBuilders:
    def test_units_envelope_structure_and_coverage(self):
        threat = lq.ThreatInfo(
            unit_type="UNIT_GALLEY",
            x=32,
            y=38,
            hp=100,
            max_hp=100,
            combat_strength=30,
            ranged_strength=0,
            distance=1,
            owner_id=63,
        )
        envelope = fact_view.units_envelope(
            turn=56,
            units=[_unit()],
            threats=[threat],
            trade_status=None,
            narrated=nr.narrate_units([_unit()], [threat], None),
        )
        assert envelope["v"] == 1
        assert envelope["tool"] == "get_units"
        assert envelope["turn"] == 56
        assert envelope["source"] == "civ_mcp:GameState"
        assert envelope["coverage"] == {
            "own_units": "COMPLETE",
            "foreign_units": "CURRENTLY_VISIBLE",
            "trade_routes": "COMPLETE",
        }
        assert envelope["facts"]["own_units"][0]["unit_id"] == 131073
        assert envelope["facts"]["foreign_units"][0]["owner_id"] == 63
        assert "1 units:" in envelope["narrated"]
        # 无威胁时 foreign_units 不出现
        bare = fact_view.units_envelope(
            turn=56, units=[], threats=None, trade_status=None, narrated="No units."
        )
        assert "foreign_units" not in bare["facts"]
        # JSON 往返可解析（asdict 会把嵌套 tuple 变 list，故与 dumps 后结果比较）
        assert (
            fact_view.parse_envelope(fact_view.dumps(envelope))
            == json.loads(fact_view.dumps(envelope))
        )

    def test_barbarian_envelope_coverage_semantics(self):
        overview = lq.BarbarianOverview(
            camps=[
                lq.BarbarianCamp(
                    x=5, y=6, visibility="revealed",
                    distance_to_city=999, distance_to_military=2,
                )
            ],
            units=[
                lq.BarbarianUnit(
                    unit_id=7, unit_type="UNIT_SCOUT", x=7, y=8, hp=100,
                    max_hp=100, combat_strength=10, ranged_strength=0,
                    distance_to_city=3, distance_to_military=1,
                )
            ],
        )
        envelope = fact_view.barbarian_envelope(
            turn=12, overview=overview, narrated=nr.narrate_barbarian_overview(overview)
        )
        assert envelope["coverage"] == {
            "camps": "KNOWN_HISTORY",
            "units": "CURRENTLY_VISIBLE",
        }
        assert envelope["facts"]["camps"][0]["distance_to_city"] == 999
        assert envelope["facts"]["units"][0]["unit_id"] == 7

    def test_map_area_envelope_and_cities_envelope(self):
        map_env = fact_view.map_area_envelope(
            turn=3,
            center_x=31,
            center_y=33,
            radius=2,
            tiles=[_tile()],
            narrated=nr.narrate_map([_tile()]),
        )
        assert map_env["facts"]["center"] == [31, 33]
        assert map_env["facts"]["tiles"][0]["visibility"] == "revealed"
        assert map_env["coverage"] == {"tiles": "COMPLETE"}

        city_env = fact_view.cities_envelope(
            turn=3,
            cities=[_city()],
            distances=["巴黎 <-> 里昂: 8 tiles"],
            narrated=nr.narrate_cities([_city()]),
        )
        assert city_env["facts"]["cities"][0]["population"] == 4
        assert city_env["facts"]["city_distances"] == ["巴黎 <-> 里昂: 8 tiles"]
        assert city_env["coverage"] == {"cities": "COMPLETE"}

    def test_turn_placeholder_when_unknown(self):
        envelope = fact_view.units_envelope(
            turn=None, units=[], threats=None, trade_status=None, narrated="No units."
        )
        assert envelope["turn"] == "?"


class TestParseEnvelope:
    def test_rejects_plain_text_and_foreign_json(self):
        assert fact_view.parse_envelope("10 units:") is None
        assert fact_view.parse_envelope('{"v": 1, "tool": "x"}') is None
        assert fact_view.parse_envelope('{"a": 1}') is None
        assert fact_view.parse_envelope("not json") is None


class TestNormalizerEnvelopePath:
    def test_units_envelope_exact_facts_and_metric_parity(self):
        units = [
            _unit(unit_id=131073, x=32, y=37),
            _unit(unit_id=131074, x=43, y=40, name="建造者", unit_type="UNIT_BUILDER"),
        ]
        narrated = nr.narrate_units(units)
        envelope = fact_view.dumps(
            fact_view.units_envelope(
                turn=56, units=units, threats=None, trade_status=None, narrated=narrated
            )
        )
        normalized = normalize_tool_result("get_units", envelope)
        assert normalized["metrics"]["observed_unit_count"] == 2
        assert normalized["facts"]["unit_ids"] == [131073, 131074]
        assert normalized["facts"]["unit_position:131073"] == [32, 37]
        # 叙述路径回归：同样输入产出相同 metrics
        legacy = normalize_tool_result("get_units", narrated)
        assert normalized["metrics"] == legacy["metrics"]

    def test_cities_envelope_keeps_legacy_fact_keys(self):
        cities = [_city(), _city(city_id=131073, name="里昂", x=36, y=35, population=3)]
        narrated = nr.narrate_cities(cities)
        envelope = fact_view.dumps(
            fact_view.cities_envelope(turn=56, cities=cities, distances=None, narrated=narrated)
        )
        normalized = normalize_tool_result("get_cities", envelope)
        assert normalized["metrics"]["observed_city_count"] == 2
        assert normalized["facts"]["cities"][0] == {
            "name": "巴黎",
            "population": 4,
            "x": 43,
            "y": 38,
        }
        legacy = normalize_tool_result("get_cities", narrated)
        assert normalized["metrics"] == legacy["metrics"]

    def test_barbarian_envelope_preserves_999_distance(self):
        # 叙述路径把 999 渲染成 "no city distance"，正则提取丢失数值；
        # 信封路径必须保留精确距离。
        overview = lq.BarbarianOverview(
            camps=[
                lq.BarbarianCamp(
                    x=5, y=6, visibility="revealed",
                    distance_to_city=999, distance_to_military=2,
                ),
                lq.BarbarianCamp(
                    x=9, y=9, visibility="visible",
                    distance_to_city=3, distance_to_military=1,
                ),
            ],
            units=[],
        )
        narrated = nr.narrate_barbarian_overview(overview)
        envelope = fact_view.dumps(
            fact_view.barbarian_envelope(turn=12, overview=overview, narrated=narrated)
        )
        normalized = normalize_tool_result("get_barbarian_overview", envelope)
        camps = normalized["facts"]["barbarian_camps"]
        assert len(camps) == 2
        by_xy = {(c["x"], c["y"]): c for c in camps}
        assert by_xy[(5, 6)]["distance_to_city"] == 999
        assert by_xy[(5, 6)]["distance_to_military"] == 2
        assert by_xy[(9, 9)]["distance_to_city"] == 3
        assert normalized["metrics"]["barbarian.camp_count"] == 2
        assert normalized["metrics"]["barbarian.nearest_camp_distance"] == 3

    def test_non_matching_tool_envelope_falls_back_to_text(self):
        # 信封 tool 与调用方不符时走普通文本路径，不误用结构。
        envelope = fact_view.dumps(
            fact_view.units_envelope(
                turn=1, units=[], threats=None, trade_status=None, narrated="No units."
            )
        )
        normalized = normalize_tool_result("get_cities", envelope)
        assert normalized["facts"]["tool"] == "get_cities"
        assert "unit_position" not in normalized["facts"]


class TestBeliefContextMerge:
    def _context(self, mode: BeliefMode):
        class _EmptyEngine:
            def drain_events(self):
                return []

        return SimpleNamespace(
            request_context=SimpleNamespace(
                lifespan_context=SimpleNamespace(
                    belief_mode=mode,
                    beliefs=_EmptyEngine(),
                )
            )
        )

    @pytest.fixture
    def fake_brief_context(self, monkeypatch):
        class FakeEngine:
            def turn_brief(self, turn=None):
                return {"decision_gate": {"default_route": "fast"}, "review": {}}

            def drain_events(self):
                return []

        async def _fake_belief_context(ctx):
            return FakeEngine(), 3

        monkeypatch.setattr(server_module, "_belief_context", _fake_belief_context)

    def test_envelope_result_gets_structured_belief_context(self, fake_brief_context):
        envelope = fact_view.dumps(
            fact_view.units_envelope(
                turn=3, units=[_unit()], threats=None, trade_status=None,
                narrated=nr.narrate_units([_unit()]),
            )
        )
        result = asyncio.run(
            _append_belief_context(self._context(BeliefMode.ENFORCE), "get_units", envelope)
        )
        parsed = json.loads(result)
        assert "=== BELIEF CONTEXT ===" not in result
        assert parsed["belief_context"]["turn"] == 3
        assert parsed["belief_context"]["default_route"] == "fast"
        assert parsed["facts"]["own_units"][0]["unit_id"] == 131073
        # 合并后仍是可解析的双轨信封
        assert fact_view.parse_envelope(result) is not None

    def test_text_result_keeps_legacy_append(self, fake_brief_context):
        result = asyncio.run(
            _append_belief_context(self._context(BeliefMode.ENFORCE), "get_units", "one unit")
        )
        assert result.startswith("one unit")
        assert "=== BELIEF CONTEXT ===" in result
        assert "default_route=fast" in result


class TestCoreQueryEnvelopes:
    """第二批核心查询工具的双轨信封与正常化器精确覆盖。"""

    def test_combat_estimate_envelope_exact_matchup(self):
        est = lq.CombatEstimate(
            attacker_type="UNIT_WARRIOR",
            defender_type="UNIT_BARBARIAN_WARRIOR",
            attacker_cs=20,
            defender_cs=10,
            is_ranged=False,
            modifiers=["fortified +6"],
            est_damage_to_defender=8,
            est_damage_to_attacker=4,
            defender_hp=100,
            attacker_hp=100,
        )
        narrated = nr.narrate_combat_estimate(est)
        env = fact_view.combat_estimate_envelope(turn=5, estimate=est, narrated=narrated)
        assert env["facts"]["available"] is True
        assert env["facts"]["estimate"]["attacker_cs"] == 20
        assert env["coverage"] == {"estimate": "COMPLETE"}

        normalized = normalize_tool_result("get_combat_estimate", fact_view.dumps(env))
        assert normalized["facts"]["matchup"] == {
            "attacker_type": "UNIT_WARRIOR",
            "defender_type": "UNIT_BARBARIAN_WARRIOR",
        }
        # metrics 与旧叙述路径一致（含 combat.* 指标）
        legacy = normalize_tool_result("get_combat_estimate", narrated)
        assert normalized["metrics"] == legacy["metrics"]
        assert legacy["metrics"]["combat.attacker_cs"] == 20
        assert legacy["metrics"]["combat.expected_damage_to_defender"] == 8

    def test_combat_estimate_unavailable(self):
        env = fact_view.combat_estimate_envelope(
            turn=5,
            estimate=None,
            narrated="No quantified combat estimate is available for this matchup.",
        )
        assert env["facts"]["available"] is False
        assert "estimate" not in env["facts"]

    def test_diplomacy_envelope_exact_rivals(self):
        met = lq.CivInfo(
            player_id=2,
            civ_name="Germany",
            leader_name="Frederick",
            has_met=True,
            is_at_war=True,
            diplomatic_state="UNFRIENDLY",
            relationship_score=-12,
            military_strength=150,
            num_cities=3,
        )
        unmet = lq.CivInfo(
            player_id=3, civ_name="China", leader_name="Qin", has_met=False, is_at_war=False
        )
        narrated = nr.narrate_diplomacy([met, unmet])
        env = fact_view.diplomacy_envelope(turn=5, civs=[met, unmet], narrated=narrated)
        assert len(env["facts"]["civs"]) == 2

        normalized = normalize_tool_result("get_diplomacy", fact_view.dumps(env))
        assert normalized["facts"]["rivals"]["player_2"] == {
            "civilization": "Germany",
            "leader": "Frederick",
            "state": "UNFRIENDLY",
            "relationship_score": -12,
            "at_war": True,
            "military": 150,
            "cities": 3,
        }
        assert "player_3" not in normalized["facts"]["rivals"]
        legacy = normalize_tool_result("get_diplomacy", narrated)
        assert normalized["facts"]["rivals"] == legacy["facts"]["rivals"]
        assert normalized["metrics"] == legacy["metrics"]

    def test_great_people_envelope_exact_classes(self):
        standing = GPClassStanding(
            class_name="Great Scientist",
            class_type="GREAT_SCIENTIST",
            entries=[
                GPPlayerPoints(
                    player_id=0, player_name="YOU", points_total=12,
                    points_per_turn=3, instances_earned=0,
                ),
                GPPlayerPoints(
                    player_id=2, player_name="Germany", points_total=18,
                    points_per_turn=2, instances_earned=1,
                ),
            ],
        )
        ov = GreatPeopleOverview(standings=[standing])
        narrated = nr.narrate_great_people_overview(ov)
        env = fact_view.great_people_overview_envelope(turn=5, overview=ov, narrated=narrated)
        assert env["facts"]["standings"][0]["class_name"] == "Great Scientist"
        assert env["coverage"]["history"] == "KNOWN_HISTORY"

        normalized = normalize_tool_result("get_great_people_overview", fact_view.dumps(env))
        classes = normalized["facts"]["great_people_classes"]
        assert classes[0]["class"] == "Great Scientist"
        assert classes[0]["our_points"] == 12
        assert classes[0]["our_per_turn"] == 3
        assert classes[0]["leader_name"] == "Germany"
        assert classes[0]["leader_points"] == 18
        assert classes[0]["lead_gap"] == 6
        # 无对手领先时 leader 为 YOU
        lone = GreatPeopleOverview(
            standings=[
                GPClassStanding(
                    class_name="Great Writer", class_type="GREAT_WRITER",
                    entries=[GPPlayerPoints(
                        player_id=0, player_name="YOU", points_total=5,
                        points_per_turn=1, instances_earned=0,
                    )],
                )
            ]
        )
        env_lone = fact_view.great_people_overview_envelope(
            turn=5, overview=lone, narrated=nr.narrate_great_people_overview(lone)
        )
        lone_normalized = normalize_tool_result(
            "get_great_people_overview", fact_view.dumps(env_lone)
        )
        assert lone_normalized["facts"]["great_people_classes"][0]["leader_name"] == "YOU"
        assert lone_normalized["facts"]["great_people_classes"][0]["lead_gap"] == 0

    def test_tech_civics_envelope_metric_parity(self):
        tc = lq.TechCivicStatus(
            current_research="TECHNOLOGY_WRITING",
            current_research_turns=5,
            current_civic="CIVIC_CODE_OF_LAWS",
            current_civic_turns=2,
            available_techs=[],
            available_civics=[],
        )
        narrated = nr.narrate_tech_civics(tc)
        env = fact_view.tech_civics_envelope(turn=5, status=tc, narrated=narrated)
        assert env["facts"]["current_research"] == "TECHNOLOGY_WRITING"
        normalized = normalize_tool_result("get_tech_civics", fact_view.dumps(env))
        assert normalized["metrics"]["research.current"] == "TECHNOLOGY_WRITING"
        assert normalized["metrics"]["civic.turns_remaining"] == 2
        legacy = normalize_tool_result("get_tech_civics", narrated)
        assert normalized["metrics"] == legacy["metrics"]

    def test_victory_production_settle_era_trade_pathing_envelopes(self):
        vp = lq.VictoryProgress(
            players=[
                lq.VictoryPlayerProgress(
                    player_id=0, name="France", score=100, science_vp=5,
                    science_vp_needed=50, diplomatic_vp=2, tourism=10,
                    military_strength=80, techs_researched=20,
                    civics_completed=15, religion_cities=2,
                )
            ]
        )
        env = fact_view.victory_progress_envelope(
            turn=5, progress=vp, narrated=nr.narrate_victory_progress(vp)
        )
        assert env["facts"]["players"][0]["science_vp"] == 5
        assert env["coverage"] == {"players": "COMPLETE", "demographics": "COMPLETE"}

        option = lq.ProductionOption(
            category="UNIT", item_name="UNIT_WARRIOR", cost=40, turns=3, gold_cost=100
        )
        env = fact_view.city_production_envelope(
            turn=5, city_id=7, options=[option],
            narrated=nr.narrate_city_production([option]),
        )
        assert env["facts"]["city_id"] == 7
        assert env["facts"]["options"][0]["item_name"] == "UNIT_WARRIOR"

        candidate = lq.SettleCandidate(
            x=3, y=4, score=12.5, total_food=4, total_prod=3,
            water_type="fresh", resources=["L:DIAMONDS"],
        )
        env = fact_view.settle_envelope(
            turn=5, tool="get_settle_advisor", unit_id=9, candidates=[candidate],
            source="local", narrated=nr.narrate_settle_candidates([candidate]),
        )
        assert env["facts"]["source"] == "local"
        assert env["facts"]["candidates"][0]["water_type"] == "fresh"
        assert env["coverage"] == {"candidates": "KNOWN_HISTORY"}

        era = lq.EraProgress(ruleset="RULESET_EXPANSION_2", ages_supported=True)
        env = fact_view.era_progress_envelope(
            turn=5, status=era, narrated=nr.narrate_era_progress(era)
        )
        assert env["facts"]["ages_supported"] is True

        routes = lq.TradeRouteStatus(
            capacity=3, active_count=1,
            traders=[lq.TraderInfo(unit_id=5, x=1, y=2, has_moves=False)],
        )
        env = fact_view.trade_routes_envelope(
            turn=5, status=routes, narrated=nr.narrate_trade_routes(routes)
        )
        assert env["facts"]["capacity"] == 3
        assert env["facts"]["traders"][0]["unit_id"] == 5

        pathing = lq.PathingEstimate(turns=2, total_tiles=5, reachable_this_turn=3)
        env = fact_view.pathing_envelope(
            turn=5, unit_id=9, target_x=8, target_y=8, estimate=pathing,
            narrated=nr.narrate_pathing_estimate(pathing),
        )
        assert env["facts"]["estimate"]["turns"] == 2


class TestThirdBatchEnvelopes:
    """第三批核心查询工具的双轨信封。"""

    def test_village_envelope_known_history_coverage(self):
        overview = lq.VillageOverview(
            huts=[
                lq.Village(
                    x=3, y=4, visibility="visible", owner="none",
                    distance_to_city=2, distance_to_military=1,
                )
            ]
        )
        narrated = nr.narrate_village_overview(overview)
        env = fact_view.village_envelope(turn=5, overview=overview, narrated=narrated)
        assert env["facts"]["huts"][0]["x"] == 3
        assert env["coverage"] == {"huts": "KNOWN_HISTORY"}
        normalized = normalize_tool_result("get_village_overview", fact_view.dumps(env))
        assert normalized["facts"]["tool"] == "get_village_overview"
        assert normalized["metrics"] == {}

    def test_spies_envelope(self):
        spies = [
            lq.SpyInfo(
                unit_id=100, unit_index=100, name="Artimpasa", x=5, y=6, rank=1,
                xp=10, moves=2, city_name="none", city_owner=-1,
                available_ops=["TRAVEL"],
            )
        ]
        narrated = nr.narrate_spies(spies)
        env = fact_view.spies_envelope(turn=5, spies=spies, narrated=narrated)
        assert env["facts"]["spies"][0]["rank"] == 1
        assert env["coverage"] == {"spies": "COMPLETE"}

    def test_builder_tasks_envelope(self):
        tasks = [
            lq.BuilderTask(
                priority="urgent", x=7, y=8, improvement="IMPROVEMENT_MINE",
                resource="IRON", resource_class="strategic", city_name="巴黎",
                nearest_builder_id=3, distance=2,
            )
        ]
        builders = [
            lq.BuilderInfo(unit_id=3, unit_index=3, x=7, y=8, charges=2, moves=1.0)
        ]
        narrated = nr.narrate_builder_tasks(tasks, builders)
        env = fact_view.builder_tasks_envelope(
            turn=5, tasks=tasks, builders=builders, narrated=narrated
        )
        assert env["facts"]["tasks"][0]["resource"] == "IRON"
        assert env["facts"]["builders"][0]["charges"] == 2
        assert env["coverage"] == {
            "tasks": "COMPLETE",
            "builders": "COMPLETE",
        }

    def test_empire_resources_envelope_coverage(self):
        stockpile = lq.ResourceStockpile(
            name="Iron", amount=12, cap=20, per_turn=1, demand=2, imported=0
        )
        owned = lq.OwnedResource(
            name="Diamonds", resource_class="luxury", improved=False, x=3, y=4
        )
        nearby = lq.NearbyResource(
            name="Horses", resource_class="strategic", x=9, y=9,
            nearest_city="巴黎", distance=4,
        )
        narrated = nr.narrate_empire_resources(
            [stockpile], [owned], [nearby], {"Diamonds": 1}
        )
        env = fact_view.empire_resources_envelope(
            turn=5, stockpiles=[stockpile], owned=[owned], nearby=[nearby],
            luxuries={"Diamonds": 1}, narrated=narrated,
        )
        assert env["facts"]["stockpiles"][0]["amount"] == 12
        assert env["facts"]["luxuries"] == {"Diamonds": 1}
        assert env["coverage"] == {
            "stockpiles": "COMPLETE",
            "owned": "COMPLETE",
            "nearby": "KNOWN_HISTORY",
        }

    def test_notifications_and_policies_envelopes(self):
        notif = lq.GameNotification(
            type_name="NOTIFICATION_BARBARIAN_CAMP",
            message="Barbarian camp spotted",
            turn=5,
            x=3,
            y=4,
            is_action_required=True,
            resolution_hint="get_barbarian_overview",
        )
        env = fact_view.notifications_envelope(
            turn=5, notifications=[notif], narrated=nr.narrate_notifications([notif])
        )
        assert env["facts"]["notifications"][0]["is_action_required"] is True
        assert env["coverage"] == {"notifications": "COMPLETE"}

        gov = lq.GovernmentStatus(
            government_name="Oligarchy", government_type="GOVERNMENT_OLIGARCHY"
        )
        env = fact_view.policies_envelope(
            turn=5, status=gov, narrated=nr.narrate_policies(gov)
        )
        assert env["facts"]["government_name"] == "Oligarchy"
        assert env["coverage"] == {
            "government": "COMPLETE",
            "policies": "COMPLETE",
        }

    def test_strategic_map_envelope_coverage(self):
        data = lq.StrategicMapData(
            fog_boundaries=[],
            unclaimed_resources=[
                lq.UnclaimedResource(
                    resource_type="RESOURCE_IRON", x=5, y=6,
                    resource_class="RESOURCECLASS_STRATEGIC",
                )
            ],
        )
        narrated = nr.narrate_strategic_map(data)
        env = fact_view.strategic_map_envelope(turn=5, data=data, narrated=narrated)
        assert env["facts"]["unclaimed_resources"][0]["x"] == 5
        assert env["coverage"] == {
            "fog_boundaries": "COMPLETE",
            "unclaimed_resources": "KNOWN_HISTORY",
        }

    def test_pending_trades_and_diplomacy_envelopes(self):
        deal = lq.PendingDeal(
            other_player_id=2, other_player_name="Germany",
            other_leader_name="Frederick",
            items_from_them=[
                lq.DealItem(
                    from_player_id=2, from_player_name="Germany", item_type="GOLD",
                    name="Gold", amount=50, duration=0, is_from_us=False,
                )
            ],
        )
        env = fact_view.pending_trades_envelope(
            turn=5, deals=[deal], narrated=nr.narrate_pending_deals([deal])
        )
        assert env["facts"]["deals"][0]["items_from_them"][0]["amount"] == 50
        assert env["coverage"] == {"deals": "COMPLETE"}

        session = lq.DiplomacySession(
            session_id=1, other_player_id=2, other_civ_name="Germany",
            other_leader_name="Frederick", choices=[],
            dialogue_text="Greetings!", buttons="GOODBYE",
        )
        env = fact_view.pending_diplomacy_envelope(
            turn=5, sessions=[session],
            narrated=nr.narrate_diplomacy_sessions([session]),
        )
        assert env["facts"]["sessions"][0]["dialogue_text"] == "Greetings!"
        assert env["coverage"] == {"sessions": "COMPLETE"}

    def test_trade_destinations_great_people_unit_promotions(self):
        dest = lq.TradeDestination(
            city_name="柏林", owner_name="Germany", x=10, y=12,
            is_domestic=False, is_city_state=False,
        )
        env = fact_view.trade_destinations_envelope(
            turn=5, unit_id=9, destinations=[dest],
            narrated=nr.narrate_trade_destinations([dest]),
        )
        assert env["facts"]["unit_id"] == 9
        assert env["facts"]["destinations"][0]["city_name"] == "柏林"
        assert env["coverage"] == {"destinations": "COMPLETE"}

        gp = lq.GreatPersonInfo(
            class_name="Great Scientist", individual_name="Hypatia",
            era_name="Classical", cost=60, claimant="Unclaimed", player_points=30,
        )
        env = fact_view.great_people_envelope(
            turn=5, people=[gp], narrated=nr.narrate_great_people([gp])
        )
        assert env["facts"]["people"][0]["individual_name"] == "Hypatia"
        assert env["coverage"] == {"people": "COMPLETE"}

        promo = lq.UnitPromotionStatus(
            unit_id=131073, unit_index=1, unit_type="UNIT_WARRIOR",
            promotions=[lq.PromotionOption(
                promotion_type="PROMOTION_BATTLECRY", name="Battlecry",
                description="+7 CS",
            )],
            xp=30, xp_needed=10, promotion_count=1,
        )
        env = fact_view.unit_promotions_envelope(
            turn=5, status=promo, narrated=nr.narrate_unit_promotions(promo)
        )
        assert env["facts"]["promotions"][0]["promotion_type"] == "PROMOTION_BATTLECRY"
        assert env["coverage"] == {"promotions": "COMPLETE"}
