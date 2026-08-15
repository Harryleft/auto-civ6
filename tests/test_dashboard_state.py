"""Offline tests for the run-dashboard state builder.

Drives the real ``BeliefEngine`` into a tmp journal, then asserts the
read-only dashboard derivation: masthead identity, empire metrics, the
chronicle ordering with chapter breaks, ledger split, and the gate mirror.
"""

from __future__ import annotations

from pathlib import Path

from civ6_belief_engine.belief_engine import BeliefEngine
from civ6_belief_engine.dashboard import (
    build_dashboard_state,
    pick_live_journal,
)

OVERVIEW_T3 = """Turn 3 | 英国 (维多利亚) | Score: 7 | 神
Gold: 80 (+4.0/turn) | Science: 9.0 | Culture: 6.0 | Faith: 12 | Favor: 0 (+0/turn)
Research: Writing | Civic: 法典
Cities: 1 | Population: 3 | Units: 2
Explored: 8% of land (80/1000 tiles)
Era: 远古时代 | Score: 7 (Dark: 9, Golden: 22)"""


def _engine(tmp_path: Path, run_id: str) -> BeliefEngine:
    engine = BeliefEngine(run_id=run_id, directory=tmp_path)
    engine.bind_game("england", 42)
    return engine


def test_dashboard_state_from_real_journal(tmp_path):
    first = _engine(tmp_path, "run-alpha")
    first.record_tool_result(
        tool="get_game_overview",
        params={},
        result=OVERVIEW_T3,
        turn=3,
        category="query",
        success=True,
        duration_ms=5,
    )
    first.record_tool_result(
        tool="get_cities",
        params={},
        result="  伦敦 (pop 3) at (43,38)",
        turn=3,
        category="query",
        success=True,
        duration_ms=4,
    )
    decision = first.route_decision(
        statement="Settle second city",
        probability=0.8,
        confidence=0.7,
        impact="high",
        urgency="medium",
        irreversibility=0.9,
        turn=3,
    )

    # A second run id simulates a process handoff: must become a chapter break.
    second = _engine(tmp_path, "run-beta")
    second.update(
        "decision",
        decision["id"],
        {
            "decision_state": "cancelled",
            "cancellation_reason": "重复授权，重试不再合理。",
        },
        turn=4,
    )

    journal = tmp_path / "belief_england_42.jsonl"
    state = build_dashboard_state(journal)

    assert state["status"] == "ok"
    assert state["game_id"] == "england_42"
    assert state["civ"]["english"] == "ENGLAND"
    assert state["civ"]["chinese"] == "英格兰"
    assert state["civ"]["leader"] == "维多利亚"
    assert state["difficulty"] == "神级 DEITY"
    assert state["turn"] == 4
    assert state["policy"]["governance"] == "OBSERVE"

    empire = state["empire"]
    assert empire["score"] == 7
    assert empire["science"] == 9.0
    assert empire["gold_per_turn"] == 4.0
    assert empire["era"] == "远古时代"
    assert empire["era_dark"] == 9
    assert empire["era_golden"] == 22
    assert empire["cities"][0]["name"] == "伦敦"
    assert empire["cities"][0]["x"] == 43

    chronicle = state["chronicle"]
    assert chronicle[0]["kind_zh"] == "决策"
    assert "重复授权" in chronicle[0]["text"]
    assert chronicle[0]["stamp"] == "CANCELLED"
    breaks = [c for c in chronicle if c.get("break")]
    assert breaks and breaks[0]["run_changed"] is True

    ledger = state["ledger"]
    assert ledger["decisions_total"] == 1
    assert ledger["decisions_with_derived_support"] == 0

    gate = state["gate"]
    assert gate["ready"] is True
    assert gate["blockers"] == []


def test_gate_mirror_reports_pending_authorization(tmp_path):
    engine = _engine(tmp_path, "run-alpha")
    engine.record_tool_result(
        tool="get_game_overview",
        params={},
        result=OVERVIEW_T3,
        turn=3,
        category="query",
        success=True,
        duration_ms=5,
    )
    # An authorized-but-unexecuted decision is what the turn gate blocks on.
    # (A bare route_decision stays "unbound", which neither gate counts.)
    engine.create(
        "decision",
        {
            "statement": "Move warrior east",
            "route": "fast",
            "decision_state": "authorized",
            "action_intent": {"tool": "unit_action", "params": {"unit_id": 7}},
        },
        turn=3,
    )
    state = build_dashboard_state(tmp_path / "belief_england_42.jsonl")
    assert state["gate"]["ready"] is False
    assert "routed_actions_not_completed" in state["gate"]["blockers"]


def test_pick_live_journal_prefers_latest_mtime(tmp_path):
    stale = tmp_path / "belief_england_1.jsonl"
    fresh = tmp_path / "belief_france_2.jsonl"
    stale.write_text("{}\n", encoding="utf-8")
    fresh.write_text("{}\n", encoding="utf-8")
    import os

    os.utime(stale, (1, 1))
    assert pick_live_journal(tmp_path) == fresh
    assert pick_live_journal(tmp_path / "does-not-exist") is None


def test_empty_journal_reports_empty(tmp_path):
    empty = tmp_path / "belief_england_9.jsonl"
    empty.write_text("", encoding="utf-8")
    state = build_dashboard_state(empty)
    assert state["status"] == "empty"
