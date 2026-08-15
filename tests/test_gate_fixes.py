"""Gate friction fixes: legacy ``failed`` closure and actionable block messages.

Born from the 2026-08-15 cross-journal analysis (21/107 runs, 330 blocked
actions, end_turn 56%): the gate invariant is sound, but its implementation
taxed zero-information bookkeeping. These tests pin the two fixes.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from civ6_belief_engine.belief_engine import BeliefEngine, BeliefEngineError
from civ_mcp.server import pipeline


def _raw_event(entity_type: str, entity: dict, *, turn: int, event_type: str = "entity.created"):
    return {
        "v": 1,
        "event_id": str(uuid.uuid4()),
        "sequence": turn,
        "timestamp": 1755300000.0 + turn,
        "game_id": "CIVILIZATION_FRANCE_2126806272",
        "run_id": "legacy-run",
        "turn": turn,
        "epoch": 1,
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity["id"],
        "entity": entity,
    }


def _legacy_journal(tmp_path: Path, decision_state: str) -> BeliefEngine:
    """A pre-a0b487c journal: approved council intent + decision the agent
    wrote out-of-band (no sanctioned path produces this state today)."""

    entities = [
        (
            "observation",
            {
                "id": "obs_snapshot",
                "statement": "typed snapshot",
                "source": "game_state:typed_snapshot",
                "observed_turn": 5,
                "facts": {"snapshot_id": "snap_1"},
                "status": "active",
            },
        ),
        (
            "proposal",
            {
                "id": "proposal_1",
                "statement": "Approve cleanup",
                "department": "military",
                "action_intent": {},
                "council_state": "approved",
                "council_decision_id": "council_1",
                "action_intents": [
                    {
                        "tool": "unit_action",
                        "intent_id": "intent_w",
                        "params": {"unit_id": 1, "action": "attack", "target_x": 1, "target_y": 1},
                    }
                ],
                "status": "resolved",
            },
        ),
        (
            "decision",
            {
                "id": "decision_legacy",
                "statement": "Warrior attack",
                "route": "fast",
                "council_decision_id": "council_1",
                "decision_state": decision_state,
                "action_intent": {
                    "tool": "unit_action",
                    "proposal_id": "proposal_1",
                    "intent_id": "intent_w",
                    "params": {"unit_id": 1, "action": "attack", "target_x": 1, "target_y": 1},
                },
                "status": "resolved" if decision_state == "failed" else "active",
            },
        ),
    ]
    path = tmp_path / "belief_CIVILIZATION_FRANCE_2126806272.jsonl"
    with path.open("w") as handle:
        for index, (entity_type, entity) in enumerate(entities, start=1):
            event = _raw_event(entity_type, entity, turn=index)
            event["sequence"] = index
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    engine = BeliefEngine(run_id="legacy-run", directory=tmp_path)
    engine.bind_game("CIVILIZATION_FRANCE", 2126806272)
    return engine


class TestLegacyFailedClosesCouncilIntent:
    def test_failed_decision_unblocks_end_turn(self, tmp_path):
        engine = _legacy_journal(tmp_path, "failed")
        gate = engine.governance_turn_gate(turn=5)
        assert gate["pending_council_intents"] == []
        assert "council_action_intents_not_completed" not in gate["blockers"]
        assert gate["ready"] is True

    def test_retryable_still_demands_an_answer(self, tmp_path):
        engine = _legacy_journal(tmp_path, "retryable")
        gate = engine.governance_turn_gate(turn=5)
        assert "council_action_intents_not_completed" in gate["blockers"]
        assert gate["ready"] is False


class TestGateReasonFormatting:
    def _gate(self):
        return {
            "blockers": ["council_action_intents_not_completed", "routed_actions_not_completed"],
            "pending_authorizations": [
                {"decision_id": "decision_a", "decision_state": "retryable"},
                {"decision_id": "decision_b", "decision_state": "outcome_unknown"},
                {"decision_id": "decision_c", "decision_state": "authorized"},
                {"decision_id": "decision_d", "decision_state": "executing"},
                {"decision_id": "decision_e", "decision_state": "retryable"},
            ],
            "pending_council_intents": [
                {"proposal_id": "proposal_1", "intent_id": "intent_x", "decision_id": None, "decision_state": "not_routed"},
            ],
            "active_proposal_ids": [],
        }

    def test_each_stuck_decision_names_its_exact_next_call(self):
        reason = pipeline._format_governance_gate_reason(self._gate())
        assert "decision_a [retryable]" in reason
        assert "cancel_routed_action" in reason
        assert "decision_b [outcome_unknown]" in reason
        assert "record_action_verification" in reason
        assert "decision_c [authorized]" in reason
        assert "decision_d [executing]" in reason
        assert "另有 1 项" in reason  # only 4 of 5 shown
        assert "proposal_1/intent_x [not_routed]" in reason
        assert "route_belief_decision" in reason
        assert "不要重复调用 end_turn" in reason



class TestReviewFollowUps:
    def test_legacy_failed_decision_cannot_be_reopened(self, tmp_path):
        engine = _legacy_journal(tmp_path, "failed")
        with pytest.raises(BeliefEngineError, match="Cannot reopen a failed"):
            engine.update(
                "decision", "decision_legacy", {"decision_state": "authorized"}, turn=6
            )

    def test_reroute_supersedes_stale_authorization_for_same_intent(self):
        instance = BeliefEngine(run_id="test-run")
        instance.bind_game("CIVILIZATION_TEST", 42)
        intent = {
            "tool": "unit_action",
            "params": {"unit_id": 1, "action": "move", "target_x": 3, "target_y": 3},
        }
        first = instance.route_decision(
            statement="Move warrior",
            probability=0.9,
            confidence=0.9,
            impact="high",
            urgency="critical",
            irreversibility=0.99,
            turn=1,
            action_intent=dict(intent),
        )
        assert first["route"] == "slow"
        # Documented exit 2: re-route through a resolved council.
        second = instance.route_decision(
            statement="Move warrior",
            probability=0.9,
            confidence=0.9,
            impact="high",
            urgency="critical",
            irreversibility=0.99,
            turn=1,
            action_intent=dict(intent),
            council_decision_id="council_1",
        )
        assert second["route"] == "fast"
        stored_first = instance.get("decision", first["id"])
        assert stored_first["decision_state"] == "cancelled"
        assert stored_first["cancellation_reason"] == "superseded_by_reroute"
        # Exactly one non-terminal decision remains for this intent.
        non_terminal = [
            item
            for item in instance.list("decision", status="active")
            if item.get("decision_state") in {"authorized", "retryable"}
        ]
        assert [item["id"] for item in non_terminal] == [second["id"]]

    def test_reroute_never_supersedes_in_flight_authorization(self):
        instance = BeliefEngine(run_id="test-run")
        instance.bind_game("CIVILIZATION_TEST", 42)
        intent = {
            "tool": "unit_action",
            "params": {"unit_id": 1, "action": "move", "target_x": 3, "target_y": 3},
        }
        first = instance.route_decision(
            statement="Move warrior",
            probability=0.9,
            confidence=0.9,
            impact="high",
            urgency="critical",
            irreversibility=0.99,
            turn=1,
            action_intent=dict(intent),
            council_decision_id="council_1",
        )
        instance.update(
            "decision", first["id"], {"decision_state": "executing"}, turn=1
        )
        second = instance.route_decision(
            statement="Move warrior",
            probability=0.9,
            confidence=0.9,
            impact="high",
            urgency="critical",
            irreversibility=0.99,
            turn=1,
            action_intent=dict(intent),
            council_decision_id="council_1",
        )
        assert instance.get("decision", first["id"])["decision_state"] == "executing"
        assert instance.get("decision", second["id"])["decision_state"] == "authorized"

    def test_snapshot_missing_blocker_carries_recovery_hint(self):
        gate = {"blockers": ["current_turn_typed_snapshot_missing"]}
        reason = pipeline._format_governance_gate_reason(gate)
        assert "current_turn_typed_snapshot_missing" in reason
        assert "get_governance_brief" in reason
        assert "待清算授权" not in reason
