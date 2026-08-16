"""未知的未知审计修复的回归测试。

覆盖 2026-08 审计（docs/audit-unknown-unknowns.md）确认的缺陷修复：
1. known-gap：diplomacy 观测显式携带 visible/unobserved 城市数（信封路径），
   "已知存在但未观测"不再只活在叙述文本里。
2. belief 证据强制：无 evidence_ids 的信念必须显式 unknown_basis=true。
3. stale-knowledge：review() 对超时未刷新的自动信念输出提示（不阻断）。
4. reliability 分级：信封=1.0 / 正则提取=0.9 / 纯摘要=0.8 / 快照=0.85。
5. 归档 resolution_kind：未知归档与明确解除归档可区分。
"""

from __future__ import annotations

import json

import pytest

from civ6_belief_engine.belief_engine import (
    BeliefEngine,
    BeliefEngineError,
    normalize_tool_result,
)
from civ_mcp import facts as fact_view
from civ_mcp import lua as lq


def _diplomacy_civ() -> lq.CivInfo:
    civ = lq.CivInfo(
        player_id=2,
        civ_name="Germany",
        leader_name="Frederick",
        has_met=True,
        is_at_war=False,
        diplomatic_state="NEUTRAL",
        relationship_score=0,
        military_strength=150,
        num_cities=4,
        visible_cities=[
            lq.VisibleCity(name="Berlin", x=10, y=10, population=7),
            lq.VisibleCity(name="Hamburg", x=12, y=14, population=3),
        ],
    )
    return civ


class TestKnownGap:
    def test_envelope_path_exact_known_gap(self):
        civ = _diplomacy_civ()
        env = fact_view.dumps(fact_view.diplomacy_envelope(turn=5, civs=[civ]))
        normalized = normalize_tool_result("get_diplomacy", env)
        rivals = normalized["facts"]["rivals"]["player_2"]
        assert rivals["cities"] == 4
        assert rivals["visible_cities"] == 2
        assert rivals["unobserved_cities"] == 2
        assert normalized["metrics"]["diplomacy.player_2.visible_cities"] == 2
        assert (
            normalized["metrics"]["diplomacy.player_2.unobserved_cities"] == 2
        )
        assert normalized["reliability"] == 1.0

    def test_envelope_path_all_in_fog(self):
        civ = _diplomacy_civ()
        civ.visible_cities = []
        env = fact_view.dumps(fact_view.diplomacy_envelope(turn=5, civs=[civ]))
        normalized = normalize_tool_result("get_diplomacy", env)
        rivals = normalized["facts"]["rivals"]["player_2"]
        assert rivals["visible_cities"] == 0
        assert rivals["unobserved_cities"] == 4


class TestBeliefEvidenceRequirement:
    def test_belief_without_evidence_is_rejected(self, engine):
        with pytest.raises(BeliefEngineError, match="evidence_ids"):
            engine.upsert(
                "belief",
                "b:no-evidence",
                {
                    "statement": "Germany prepares for war.",
                    "category": "military",
                    "probability": 0.9,
                    "confidence": 0.8,
                },
                turn=5,
            )

    def test_belief_with_unknown_basis_is_accepted(self, engine):
        belief = engine.upsert(
            "belief",
            "b:unknown-basis",
            {
                "statement": "Germany prepares for war.",
                "category": "military",
                "probability": 0.9,
                "confidence": 0.8,
                "unknown_basis": True,
            },
            turn=5,
        )
        assert belief["unknown_basis"] is True
        assert belief["status"] == "active"

    def test_belief_with_evidence_is_accepted(self, engine):
        obs = engine.create(
            "observation",
            {
                "statement": "Observed result from get_diplomacy",
                "source": "mcp:get_diplomacy",
                "facts": {"tool": "get_diplomacy"},
                "metrics": {},
            },
            turn=5,
        )
        belief = engine.upsert(
            "belief",
            "b:with-evidence",
            {
                "statement": "Rival military is strong.",
                "category": "military",
                "probability": 0.7,
                "confidence": 0.8,
                "evidence_ids": [obs["id"]],
            },
            turn=5,
        )
        assert belief["evidence_ids"] == [obs["id"]]


class TestStaleKnowledge:
    def test_stale_camp_belief_shows_in_review(self, engine):
        engine.create(
            "belief",
            {
                "id": "auto:belief:camp_threat:5_6",
                "statement": "Barbarian camp at (5,6) threatens our cities",
                "category": "military",
                "probability": 0.7,
                "confidence": 0.8,
                "evidence_ids": ["obs:1"],
                "last_seen_turn": 10,
                "tags": ["automatic"],
            },
            turn=10,
            entity_id="auto:belief:camp_threat:5_6",
        )
        review = engine.review(turn=13)
        stale = review["knowledge_stale"]
        assert len(stale) == 1
        assert stale[0]["id"] == "auto:belief:camp_threat:5_6"
        assert stale[0]["unseen_turns"] == 3
        assert stale[0]["refresh_tool"] == "get_barbarian_overview"
        # 不阻断：default_route 不受影响
        brief = engine.turn_brief(turn=13)
        assert brief["decision_gate"]["default_route"] == "fast"
        assert brief["decision_gate"]["knowledge_stale"][0]["unseen_turns"] == 3

    def test_fresh_belief_has_no_stale_flag(self, engine):
        engine.create(
            "belief",
            {
                "id": "auto:belief:camp_threat:1_2",
                "statement": "Barbarian camp at (1,2)",
                "category": "military",
                "probability": 0.7,
                "confidence": 0.8,
                "evidence_ids": ["obs:1"],
                "last_seen_turn": 13,
                "tags": ["automatic"],
            },
            turn=13,
            entity_id="auto:belief:camp_threat:1_2",
        )
        review = engine.review(turn=13)
        assert review["knowledge_stale"] == []

    def test_manual_belief_never_stale(self, engine):
        engine.create(
            "belief",
            {
                "id": "b:manual",
                "statement": "Manual belief",
                "category": "military",
                "probability": 0.5,
                "confidence": 0.5,
                "evidence_ids": ["obs:1"],
                "last_seen_turn": 1,
            },
            turn=1,
            entity_id="b:manual",
        )
        review = engine.review(turn=50)
        assert review["knowledge_stale"] == []


class TestReliabilityGrading:
    def test_envelope_path_reliability(self):
        env = fact_view.dumps(
            fact_view.units_envelope(
                turn=5, units=[], threats=None, trade_status=None,
            )
        )
        normalized = normalize_tool_result("get_units", env)
        assert normalized["reliability"] == 1.0

    def test_plain_text_fallback_reliability(self):
        # 纯文本结果走兼容正则回退路径（可靠工具 0.9），字段级提取仍生效。
        normalized = normalize_tool_result(
            "get_units", "Archer at (3,4) [id:7, idx:1]\nWarrior at (8,9) [id:11, idx:2]"
        )
        assert normalized["reliability"] == 0.9
        assert normalized["facts"]["unit_ids"] == [7, 11]

    def test_summary_only_reliability(self):
        normalized = normalize_tool_result("get_notifications", "No notifications.")
        assert normalized["reliability"] == 0.8

    def test_recorded_observation_uses_graded_reliability(self, engine):
        engine.record_tool_result(
            tool="get_notifications",
            params={},
            result="No notifications.",
            turn=5,
            category="query",
            success=True,
            duration_ms=1,
        )
        observations = engine.list("observation", status="active")
        obs = [
            o for o in observations if o.get("source") == "mcp:get_notifications"
        ]
        assert obs and obs[0]["reliability"] == 0.8

    def test_snapshot_observation_reliability(self, engine):
        engine.ingest_typed_snapshot(
            {
                "snapshot_id": "snap-1",
                "turn": 5,
                "entities": [],
                "relations": [],
                "metrics": {"turn": 5},
                "capabilities": {},
            },
            turn=5,
        )
        observations = engine.list("observation", status="active")
        snap = [
            o for o in observations if o.get("source") == "game_state:typed_snapshot"
        ]
        assert snap and snap[0]["reliability"] == 0.85


class TestArchiveResolutionKind:
    def test_camp_archive_kind_unknown(self, engine):
        from civ6_belief_engine import derivation

        # 先构造 last_seen=10 的 camp belief → 回合差 10 ≥ 5 → 归档
        engine.create(
            "belief",
            {
                "id": "auto:belief:camp_threat:5_6",
                "statement": "camp",
                "category": "military",
                "probability": 0.7,
                "confidence": 0.8,
                "evidence_ids": ["obs:1"],
                "last_seen_turn": 10,
                "tags": ["automatic"],
            },
            turn=10,
            entity_id="auto:belief:camp_threat:5_6",
        )
        ops = list(
            derivation._camp_threats(
                derivation.RuleContext(
                    engine=engine,
                    tool="get_barbarian_overview",
                    facts={"barbarian_camps": []},
                    metrics={},
                    turn=20,
                    observation_id="obs:1",
                )
            )
        )
        archived = [op for op in ops if isinstance(op, derivation.ArchiveEntity)]
        assert archived
        assert archived[0].kind == "unknown"
        archived[0].apply(engine, turn=20)
        entity = engine.get("belief", "auto:belief:camp_threat:5_6")
        assert entity["status"] == "archived"
        assert entity["resolution_kind"] == "unknown"
