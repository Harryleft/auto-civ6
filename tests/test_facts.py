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
