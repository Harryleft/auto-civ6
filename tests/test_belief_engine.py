"""Behavioural tests for the event-sourced Civ Belief Engine.

These tests deliberately exercise the engine without a live game.  A live
session is an integration concern; the contracts below must remain reliable
across restarts so that an agent can safely use the recorded state in-game.
"""

from __future__ import annotations

import json

import pytest

from civ_mcp.belief_engine import (
    BeliefEngine,
    BeliefEngineError,
    action_args_hash,
    evaluate_condition,
    normalize_tool_result,
)


@pytest.fixture
def engine(tmp_path):
    instance = BeliefEngine(run_id="test-run", directory=tmp_path)
    instance.bind_game("CIVILIZATION_TEST", 42)
    return instance


def belief_payload(**overrides):
    payload = {
        "statement": "The northern frontier is defensible.",
        "category": "military",
        "probability": 0.7,
        "confidence": 0.8,
    }
    payload.update(overrides)
    return payload


def hypothesis_payload(**overrides):
    payload = {
        "statement": "The neighbour is preparing a war.",
        "topic_id": "neighbour-intent",
        "probability": 0.5,
        "confidence": 0.6,
    }
    payload.update(overrides)
    return payload


def prediction_payload(**overrides):
    payload = {
        "statement": "Gold will exceed 100 next turn.",
        "probability": 0.8,
        "confidence": 0.7,
        "deadline_turn": 12,
    }
    payload.update(overrides)
    return payload


def plan_payload(**overrides):
    payload = {
        "goal": "Secure the northern frontier.",
        "horizon": 10,
        "probability_of_success": 0.7,
    }
    payload.update(overrides)
    return payload


class TestEventSourcingAndCrud:
    def test_events_persist_reload_and_replay_tombstone(self, tmp_path):
        first = BeliefEngine(run_id="run-a", directory=tmp_path)
        first.bind_game("CIVILIZATION_ROME", -91)
        created = first.create("belief", belief_payload(), turn=4, entity_id="border")
        updated = first.update(
            "belief", created["id"], {"probability": 0.45}, turn=5
        )
        deleted = first.delete("belief", created["id"], reason="superseded", turn=6)

        assert updated["version"] == 2
        assert deleted["status"] == "deleted"
        assert len(first.drain_events()) == 3
        assert first.drain_events() == []

        # A second process must reconstruct current state from append-only history.
        reloaded = BeliefEngine(run_id="run-b", directory=tmp_path)
        reloaded.bind_game("CIVILIZATION_ROME", -91)
        assert reloaded.get("belief", "border")["deleted_reason"] == "superseded"
        assert reloaded.list("belief") == []
        assert len(reloaded.list("belief", status=None)) == 1
        assert [event["event_type"] for event in reloaded.history(entity_id="border")] == [
            "entity.created",
            "entity.updated",
            "entity.deleted",
        ]

    def test_upsert_recreate_after_tombstone_and_snapshot(self, engine):
        engine.create("belief", belief_payload(), turn=1, entity_id="border")
        engine.upsert("belief", "border", {"confidence": 0.9}, turn=2)
        engine.delete("belief", "border", reason="obsolete", turn=3)
        resurrected = engine.upsert("belief", "border", belief_payload(probability=0.2), turn=4)

        assert resurrected["status"] == "active"
        assert resurrected["probability"] == 0.2
        assert engine.snapshot(include_deleted=True)["last_sequence"] == 4
        assert len(engine.history(entity_id="border")) == 4

    def test_update_is_a_noop_when_patch_changes_nothing(self, engine):
        engine.create("belief", belief_payload(), turn=1, entity_id="border")
        before = engine.get("belief", "border")
        after = engine.update("belief", "border", {"probability": 0.7}, turn=2)

        assert after == before
        assert len(engine.history(entity_id="border")) == 1

    def test_corrupt_jsonl_lines_are_ignored_without_losing_valid_events(self, tmp_path):
        instance = BeliefEngine(run_id="run", directory=tmp_path)
        instance.bind_game("CIVILIZATION_TEST", 1)
        instance.create("belief", belief_payload(), turn=1, entity_id="valid")
        assert instance.path is not None
        with instance.path.open("a") as handle:
            handle.write("not-json\n")
            handle.write(json.dumps(["also-not-an-event"]) + "\n")

        reloaded = BeliefEngine(run_id="other-run", directory=tmp_path)
        reloaded.bind_game("CIVILIZATION_TEST", 1)
        assert reloaded.get("belief", "valid")["statement"] == belief_payload()["statement"]
        assert len(reloaded.history()) == 1


class TestNormalizationAndObservations:
    def test_normalizes_overview_diplomacy_cities_units_and_victory(self):
        overview = normalize_tool_result(
            "get_game_overview",
            """Turn 37 | Rome (Trajan) | Score: 234
Gold: 1,200 (+34/turn) | Science: 55.5 | Culture: 22.0 | Faith: 7 | Favor: 3 (+1/turn)
Cities: 4 | Population: 21 | Units: 9
Explored: 56% of land (120/215 tiles)
Era: Classical | Score: 18 (Dark: 12, Golden: 24)""",
        )
        assert overview["facts"]["summary"].startswith("Turn 37")
        assert overview["metrics"] == {
            "turn": 37,
            "score": 234,
            "gold": 1200,
            "gold_per_turn": 34,
            "science": 55.5,
            "culture": 22,
            "faith": 7,
            "favor": 3,
            "cities": 4,
            "population": 21,
            "units": 9,
            "exploration_pct": 56,
            "era_score": 18,
        }

        diplomacy = normalize_tool_result(
            "get_diplomacy",
            """2 civilizations:
  Germany (Frederick) — UNFRIENDLY (-12) **AT WAR** [player 2]
    Cities: 3 (all in fog)
    Military: 150 vs our 100
""",
        )
        assert diplomacy["facts"]["rivals"]["player_2"]["at_war"] is True
        assert diplomacy["metrics"]["diplomacy.player_2.military"] == 150
        assert diplomacy["metrics"]["our_military"] == 100

        cities = normalize_tool_result(
            "get_cities", "  Rome (pop 7) at (11,24)\n  Antium (pop 3) at (14,25)"
        )
        units = normalize_tool_result("get_units", "Archer [id:7]\nWarrior [id:11]")
        victory = normalize_tool_result(
            "get_victory_progress", "  Rome: 12/20 VP | 33 techs\n  Germany: 9/20 VP"
        )
        assert cities["metrics"]["observed_city_count"] == 2
        assert cities["facts"]["cities"][0]["x"] == 11
        assert units["metrics"]["observed_unit_count"] == 2
        assert units["facts"]["unit_ids"] == [7, 11]
        assert victory["metrics"] == {
            "victory.rome.vp": 12,
            "victory.rome.vp_target": 20,
            "victory.rome.techs": 33,
            "victory.germany.vp": 9,
            "victory.germany.vp_target": 20,
        }
        assert normalize_tool_result(
            "get_combat_estimate",
            "No quantified combat estimate is available for this matchup.",
        )["metrics"] == {}

    def test_recorded_query_observations_supply_latest_metrics_and_actions_are_traced(self, engine):
        assert (
            BeliefEngine(run_id="unbound").record_tool_result(
                tool="get_game_overview",
                params={},
                result="Turn 1",
                turn=1,
                category="query",
                success=True,
                duration_ms=1,
            )
            is None
        )
        observation = engine.record_tool_result(
            tool="get_game_overview",
            params={"full": True},
            result="Turn 5 | Rome (Trajan) | Score: 123\nGold: 50 (+5/turn)",
            turn=5,
            category="query",
            success=True,
            duration_ms=4,
        )
        action = engine.record_tool_result(
            tool="set_research",
            params={"tech": "TECH_WRITING"},
            result="Research set",
            turn=5,
            category="action",
            success=True,
            duration_ms=8,
        )

        assert observation is not None and observation["source"] == "mcp:get_game_overview"
        assert engine.current_metrics()["gold"] == 50
        assert action["verification"]["verified"] is True
        assert engine.record_tool_result(
            tool="get_units", params={}, result="", turn=5, category="query", success=False, duration_ms=1
        ) is None

    def test_current_metrics_use_only_latest_snapshot_from_each_tool(self, engine):
        engine.create(
            "observation",
            {
                "statement": "old victory schema",
                "source": "mcp:get_victory_progress",
                "metrics": {"victory.unknown.score": 2, "victory.rome.score": 10},
                "observed_turn": 4,
            },
            turn=4,
        )
        engine.create(
            "observation",
            {
                "statement": "corrected victory schema",
                "source": "mcp:get_victory_progress",
                "metrics": {"victory.rome.score": 12},
                "observed_turn": 5,
            },
            turn=5,
        )
        assert engine.current_metrics() == {"victory.rome.score": 12}


class TestHypothesesPredictionsAndSurprises:
    def test_rebalances_a_competing_hypothesis_pool(self, engine):
        engine.create("hypothesis", hypothesis_payload(probability=0.6), turn=1, entity_id="war")
        engine.create(
            "hypothesis",
            hypothesis_payload(statement="The neighbour is bluffing.", probability=0.4),
            turn=1,
            entity_id="bluff",
        )

        updated = engine.rebalance_hypotheses(
            "neighbour-intent", {"war": 0.25, "bluff": 0.75}, turn=2
        )
        assert [item["probability"] for item in updated] == [0.25, 0.75]
        with pytest.raises(BeliefEngineError, match="must sum to 1"):
            engine.rebalance_hypotheses("neighbour-intent", {"war": 0.9, "bluff": 0.9}, turn=3)
        with pytest.raises(BeliefEngineError, match="does not belong"):
            engine.rebalance_hypotheses("wrong-topic", {"war": 1.0}, turn=3)

    def test_hypothesis_rebalance_is_atomic_when_any_member_is_invalid(self, engine):
        engine.create("hypothesis", hypothesis_payload(probability=0.6), turn=1, entity_id="war")
        engine.create(
            "hypothesis",
            hypothesis_payload(statement="The neighbour is bluffing.", probability=0.4),
            turn=1,
            entity_id="bluff",
        )

        with pytest.raises(BeliefEngineError, match="does not belong"):
            engine.rebalance_hypotheses(
                "neighbour-intent", {"war": 0.3, "missing": 0.7}, turn=2
            )

        # The MCP contract promises a whole-pool redistribution, never a partial write.
        assert engine.get("hypothesis", "war")["probability"] == 0.6
        assert engine.get("hypothesis", "bluff")["probability"] == 0.4

    def test_manual_prediction_resolution_creates_surprise_and_marks_linked_belief(self, engine):
        engine.create("belief", belief_payload(), turn=1, entity_id="border")
        engine.create(
            "prediction",
            prediction_payload(probability=0.9, belief_ids=["border"]),
            turn=2,
            entity_id="gold",
        )

        resolved = engine.resolve_prediction("gold", outcome=False, actual=25, turn=12)
        surprise = engine.list("surprise")[0]
        assert resolved["status"] == "disconfirmed"
        assert resolved["prediction_error"] == 0.9
        assert surprise["severity"] == "major"
        assert surprise["requires_slow_review"] is True
        assert engine.get("belief", "border")["review_required"] is True

    def test_review_resolves_predictions_automatically_or_marks_missing_data_overdue(self, engine):
        engine.create(
            "observation", {"statement": "Gold is 110.", "source": "fixture", "metrics": {"gold": 110}}, turn=5
        )
        engine.create(
            "prediction",
            prediction_payload(evaluation={"metric": "gold", "operator": ">=", "value": 100}),
            turn=5,
            entity_id="success",
        )
        engine.create(
            "prediction",
            prediction_payload(deadline_turn=6, evaluation={"metric": "science", "operator": ">=", "value": 20}),
            turn=5,
            entity_id="unknown",
        )
        engine.create(
            "prediction",
            prediction_payload(deadline_turn=6, evaluation={"metric": "gold", "operator": ">", "value": 200}),
            turn=5,
            entity_id="failure",
        )

        reviewed = engine.review(turn=6)
        assert set(reviewed["predictions_resolved"]) == {"success", "failure"}
        assert reviewed["predictions_overdue"] == ["unknown"]
        assert engine.get("prediction", "success")["resolution_source"] == "automatic"
        assert engine.get("prediction", "failure")["status"] == "disconfirmed"
        assert engine.get("prediction", "unknown")["status"] == "overdue"


class TestReviewRoutingAttributionAndMetrics:
    def test_review_detects_contradictions_and_dynamic_plan_triggers(self, engine):
        engine.create(
            "observation", {"statement": "Gold is low.", "source": "fixture", "metrics": {"gold": 40}}, turn=1
        )
        engine.create(
            "belief",
            belief_payload(expectations=[{"metric": "gold", "operator": ">=", "value": 100, "expected": True}]),
            turn=1,
            entity_id="economy",
        )
        engine.create("belief", belief_payload(probability=0.2), turn=1, entity_id="assumption")
        engine.create(
            "plan",
            plan_payload(
                exit_conditions=[{"metric": "gold", "operator": "<", "value": 50}],
                assumption_thresholds={"assumption": 0.4},
            ),
            turn=1,
            entity_id="defense-plan",
        )
        engine.create("plan", plan_payload(review_turn=2), turn=1, entity_id="scheduled-plan")

        first = engine.review(turn=2)
        second = engine.review(turn=3)
        contradiction = engine.list("contradiction")[0]
        assert first["contradictions_created"] == [contradiction["id"]]
        assert first["plans_needing_replan"] == ["defense-plan"]
        assert engine.get("belief", "economy")["review_required"] is True
        assert engine.get("plan", "defense-plan")["status"] == "needs_replan"
        assert engine.get("plan", "scheduled-plan")["review_reason"] == "scheduled"
        assert second["contradictions_created"] == []

    def test_fast_slow_routing_and_surprise_escalation(self, engine):
        assert engine.route_decision(
            statement="Move scout", probability=0.1, confidence=0.95, impact="low", urgency="low", irreversibility=0, turn=1, persist=False
        )["route"] == "fast"
        assert engine.route_decision(
            statement="Choose research", probability=0.5, confidence=0.9, impact="medium", urgency="critical", irreversibility=0.9, turn=1, persist=False
        )["route"] == "verify_then_fast"
        slow = engine.route_decision(
            statement="Declare war", probability=0.9, confidence=0.9, impact="high", urgency="critical", irreversibility=0.99, turn=1
        )
        assert slow["route"] == "slow"
        engine.create("surprise", {"statement": "Capital attacked", "severity": "major"}, turn=2)
        escalated = engine.route_decision(
            statement="Move warrior", probability=0.1, confidence=0.99, impact="low", urgency="low", irreversibility=0, turn=2, persist=False
        )
        assert escalated["route"] == "slow"
        assert escalated["slow_thinking_budget"] == "high"

    def test_route_uses_referenced_belief_state_not_only_agent_supplied_numbers(self, engine):
        engine.create(
            "belief",
            belief_payload(
                probability=0.9,
                confidence=0.9,
                review_required=True,
            ),
            turn=1,
            entity_id="frontier",
        )

        routed = engine.route_decision(
            statement="Move through the northern frontier",
            probability=0.1,
            confidence=0.95,
            impact="low",
            urgency="low",
            irreversibility=0,
            belief_ids=["frontier"],
            turn=1,
        )

        assert routed["route"] == "slow"
        assert routed["belief_context"]["review_required"] is True
        assert routed["belief_context"]["disagreement"] == 0.8

    def test_failure_attribution_and_research_metrics(self, engine):
        engine.create(
            "attribution",
            {
                "failure": "The city fell.",
                "candidates": [
                    {"name": "No walls", "prior": 0.6, "evidence_for": [{"weight": 0.5}], "evidence_against": []},
                    {"name": "Bad terrain", "prior": 0.4, "evidence_for": [], "evidence_against": [{"weight": 0.5}]},
                ],
            },
            turn=1,
            entity_id="loss",
        )
        attribution = engine.update_attribution_posteriors("loss", turn=2)
        assert attribution["candidates"][0]["posterior"] > attribution["candidates"][1]["posterior"]
        assert sum(item["posterior"] for item in attribution["candidates"]) == pytest.approx(1)

        engine.create("belief", belief_payload(), turn=2)
        engine.create("hypothesis", hypothesis_payload(), turn=2)
        engine.create("prediction", prediction_payload(probability=0.8), turn=2, entity_id="wrong")
        engine.resolve_prediction("wrong", outcome=False, actual=0, turn=3)
        engine.create("plan", plan_payload(), turn=2, entity_id="done")
        engine.update("plan", "done", {"status": "completed"}, turn=3)
        engine.route_decision(
            statement="Risky action", probability=0.9, confidence=0.9, impact="high", urgency="critical", irreversibility=0.9, turn=3
        )
        metrics = engine.metrics()
        assert metrics["mean_prediction_error"] == 0.8
        assert metrics["overconfidence_rate"] == 1.0
        assert metrics["plan_completion_rate"] == 1.0
        assert metrics["slow_thinking_trigger_rate"] == 1.0
        assert metrics["event_count"] == len(engine.history(last_n=1000))


class TestRouteCombatRiskGuardrail:
    """Proximity is an observation cue, not evidence that a route is unsound."""

    def test_nearby_hostiles_only_trigger_verification_without_lowering_route_belief(
        self, engine
    ):
        engine.create(
            "belief",
            belief_payload(
                statement="Peaceful northern expansion remains viable.",
                category="route",
                probability=0.8,
            ),
            turn=10,
            entity_id="northern-expansion",
        )

        result = engine.assess_route_combat_risk(
            "northern-expansion",
            nearby_hostiles=[
                {
                    "unit_id": 71,
                    "unit_type": "UNIT_SWORDSMAN",
                    "distance": 2,
                    # These are a threat-scan observation, not a matchup result.
                    "combat_strength": 35,
                    "hp": 100,
                }
            ],
            assessment=None,
            turn=10,
        )

        assert result["belief_updated"] is False
        assert result["route"] == "verify_then_fast"
        assert result["required_evidence"] == "combat_estimate"
        assert engine.get("belief", "northern-expansion")["probability"] == 0.8

    def test_quantified_combat_assessment_can_lower_route_belief_with_provenance(self, engine):
        engine.create(
            "belief",
            belief_payload(category="route", probability=0.8),
            turn=10,
            entity_id="northern-expansion",
        )
        assessment = {
            "source": "combat_estimate",
            "revised_probability": 0.35,
            "attacker_cs": 20,
            "defender_cs": 45,
            "attacker_hp": 100,
            "defender_hp": 100,
            "expected_damage_to_attacker": 60,
            "expected_damage_to_defender": 12,
        }

        result = engine.assess_route_combat_risk(
            "northern-expansion",
            nearby_hostiles=[{"unit_id": 71, "distance": 2}],
            assessment=assessment,
            turn=10,
        )

        belief = engine.get("belief", "northern-expansion")
        assert result["belief_updated"] is True
        assert belief["probability"] == 0.35
        assert belief["last_combat_risk_assessment"] == assessment
        assert belief["last_combat_risk_assessment_turn"] == 10

    def test_incomplete_quantitative_assessment_cannot_mutate_route_belief(self, engine):
        engine.create(
            "belief",
            belief_payload(category="route", probability=0.8),
            turn=10,
            entity_id="northern-expansion",
        )

        with pytest.raises(BeliefEngineError, match="combat assessment"):
            engine.assess_route_combat_risk(
                "northern-expansion",
                nearby_hostiles=[{"unit_id": 71, "distance": 2}],
                assessment={"source": "combat_estimate", "revised_probability": 0.35},
                turn=10,
            )

        assert engine.get("belief", "northern-expansion")["probability"] == 0.8


class TestHarnessActionLifecycle:
    def test_key_action_requires_and_consumes_matching_routed_decision(self, engine):
        blocked = engine.authorize_action(
            tool="set_research",
            params={"tech_or_civic": "TECH_WRITING"},
            turn=1,
            required=True,
        )
        assert blocked["authorized"] is False
        assert "route_belief_decision" in blocked["reason"]

        decision = engine.route_decision(
            statement="Switch research to Writing",
            probability=0.1,
            confidence=0.95,
            impact="low",
            urgency="low",
            irreversibility=0,
            belief_ids=[],
            action_intent={
                "tool": "set_research",
                "params": {"tech_or_civic": "TECH_WRITING"},
                "args_hash": action_args_hash(
                    {"tech_or_civic": "TECH_WRITING"}
                ),
            },
            turn=1,
        )

        authorized = engine.authorize_action(
            tool="set_research",
            params={"tech_or_civic": "TECH_WRITING"},
            turn=1,
            required=True,
        )
        assert authorized["authorized"] is True
        assert authorized["decision_id"] == decision["id"]
        assert engine.get("decision", decision["id"])["decision_state"] == "executing"

        engine.record_tool_result(
            tool="set_research",
            params={"tech_or_civic": "TECH_WRITING"},
            result="Research set to TECH_WRITING",
            turn=1,
            category="action",
            success=True,
            duration_ms=5,
            decision_id=decision["id"],
            decision_route="fast",
        )
        completed = engine.get("decision", decision["id"])
        assert completed["status"] == "resolved"
        assert completed["decision_state"] == "succeeded"

        second = engine.authorize_action(
            tool="set_research",
            params={"tech_or_civic": "TECH_POTTERY"},
            turn=1,
            required=True,
        )
        assert second["authorized"] is False

    def test_successful_action_is_also_recorded_as_observation(self, engine):
        action = engine.record_tool_result(
            tool="set_research",
            params={"tech_or_civic": "TECH_WRITING"},
            result="Research set to TECH_WRITING",
            turn=3,
            category="action",
            success=True,
            duration_ms=5,
            decision_id="decision_1",
            decision_route="fast",
        )

        assert action is not None
        assert action["decision_id"] == "decision_1"
        observations = engine.list("observation", status="active")
        assert len(observations) == 1
        assert observations[0]["source"] == "action:set_research"
        assert observations[0]["facts"]["action_success"] is True

    def test_verify_then_fast_requires_fresh_game_observation(self, engine):
        decision = engine.route_decision(
            statement="Choose the next research target",
            probability=0.5,
            confidence=0.9,
            impact="medium",
            urgency="critical",
            irreversibility=0.9,
            action_intent={
                "tool": "set_research",
                "params": {},
                "args_hash": action_args_hash({}),
            },
            evidence_requirements=[
                {"tool": "get_tech_civics", "params": {}, "max_age_turns": 0}
            ],
            turn=2,
        )

        before_evidence = engine.authorize_action(
            tool="set_research",
            params={},
            turn=2,
            required=True,
        )
        assert before_evidence["authorized"] is False
        assert before_evidence["route"] == "verify_then_fast"

        engine.record_tool_result(
            tool="get_tech_civics",
            params={},
            result="Researching: TECH_POTTERY (3 turns)",
            turn=2,
            category="query",
            success=True,
            duration_ms=3,
        )
        after_evidence = engine.authorize_action(
            tool="set_research",
            params={},
            turn=2,
            required=True,
        )
        assert after_evidence["authorized"] is True

    def test_verify_then_fast_rejects_unrelated_query(self, engine):
        decision = engine.route_decision(
            statement="Attack the fortified target",
            probability=0.5,
            confidence=0.5,
            impact="medium",
            urgency="high",
            irreversibility=0.2,
            action_intent={
                "tool": "unit_action",
                "params": {
                    "unit_id": 10,
                    "action": "attack",
                    "target_x": 4,
                    "target_y": 5,
                },
                "args_hash": action_args_hash(
                    {
                        "unit_id": 10,
                        "action": "attack",
                        "target_x": 4,
                        "target_y": 5,
                    }
                ),
            },
            evidence_requirements=[
                {
                    "tool": "get_combat_estimate",
                    "params": {"unit_id": 10, "target_x": 4, "target_y": 5},
                    "metric_keys": ["combat.attacker_cs", "combat.defender_cs"],
                }
            ],
            turn=4,
        )
        assert decision["route"] == "verify_then_fast"
        engine.record_tool_result(
            tool="get_cities",
            params={},
            result="Cities: 2",
            turn=4,
            category="query",
            success=True,
            duration_ms=1,
        )

        blocked = engine.authorize_action(
            tool="unit_action",
            params={
                "unit_id": 10,
                "action": "attack",
                "target_x": 4,
                "target_y": 5,
            },
            turn=4,
            required=True,
        )

        assert blocked["authorized"] is False
        assert blocked["missing_evidence"][0]["tool"] == "get_combat_estimate"

    def test_failed_execution_becomes_retryable_without_reauthoring_strategy(self, engine):
        params = {"tech_or_civic": "TECH_WRITING"}
        decision = engine.route_decision(
            statement="Research Writing",
            probability=0.1,
            confidence=0.9,
            impact="low",
            urgency="low",
            irreversibility=0,
            action_intent={
                "tool": "set_research",
                "params": params,
                "args_hash": action_args_hash(params),
            },
            turn=5,
        )
        first = engine.authorize_action(
            tool="set_research", params=params, turn=5, required=True
        )
        assert first["authorized"] is True
        engine.record_tool_result(
            tool="set_research",
            params=params,
            result="ERR:BUSY",
            turn=5,
            category="action",
            success=False,
            duration_ms=2,
            decision_id=decision["id"],
            decision_route="fast",
        )
        assert engine.get("decision", decision["id"])["decision_state"] == "retryable"
        retry = engine.authorize_action(
            tool="set_research", params=params, turn=5, required=True
        )
        assert retry["authorized"] is True

    def test_slow_gate_only_blocks_conflicting_scope(self, engine):
        engine.create(
            "belief",
            belief_payload(
                review_required=True,
                gate_scope="military:east",
            ),
            turn=6,
            entity_id="east-front",
        )
        engine.create(
            "contradiction",
            {
                "statement": "Eastern military evidence conflicts.",
                "severity": "high",
                "belief_id": "east-front",
                "requires_slow_review": True,
            },
            turn=6,
        )
        params = {"tech_or_civic": "TECH_WRITING"}
        engine.route_decision(
            statement="Research Writing",
            probability=0.1,
            confidence=0.9,
            impact="low",
            urgency="low",
            irreversibility=0,
            action_intent={
                "tool": "set_research",
                "params": params,
                "args_hash": action_args_hash(params),
            },
            gate_scope="research",
            turn=6,
        )

        allowed = engine.authorize_action(
            tool="set_research", params=params, turn=6, required=True
        )

        assert engine.turn_brief(turn=6)["decision_gate"]["default_route"] == "slow"
        assert allowed["authorized"] is True


class TestTurnBrief:
    def test_turn_brief_reviews_and_exposes_decision_gates(self, engine):
        engine.create(
            "belief",
            belief_payload(
                impact="high",
                urgency="high",
                review_required=True,
                review_reason="new combat evidence",
            ),
            turn=9,
            entity_id="frontier",
        )
        engine.create(
            "plan",
            {
                **plan_payload(
                    status="needs_replan",
                    review_required=True,
                    status_reason="frontier belief changed",
                ),
            },
            turn=9,
            entity_id="frontier-plan",
        )

        brief = engine.turn_brief(turn=10)

        assert brief["decision_gate"]["default_route"] == "slow"
        assert brief["decision_gate"]["beliefs_requiring_review"] == ["frontier"]
        assert brief["decision_gate"]["plans_requiring_review"] == ["frontier-plan"]
        assert brief["beliefs"][0]["id"] == "frontier"
        assert len(brief["guardrails"]) == 3


class TestTypedGovernanceGraph:
    def test_typed_snapshot_projects_world_entities_relations_and_metrics(self, engine):
        result = engine.ingest_typed_snapshot(
            {
                "snapshot_id": "snapshot_42",
                "turn_before": 42,
                "turn_after": 42,
                "capabilities": {"ruleset": "RULESET_STANDARD"},
                "entities": [
                    {
                        "entity_type": "player",
                        "entity_id": "player:0",
                        "attributes": {"gold": 120},
                    },
                    {
                        "entity_type": "city",
                        "entity_id": "city:0:1",
                        "attributes": {"name": "Capital"},
                    },
                ],
                "relations": [
                    {
                        "relation_type": "owns",
                        "source_id": "player:0",
                        "target_id": "city:0:1",
                        "attributes": {},
                    }
                ],
                "metrics": {"player.gold": 120},
            },
            turn=42,
        )

        assert result["world_entities_changed"] == ["player:0", "city:0:1"]
        player = engine.get("world_entity", "player:0")
        city = engine.get("world_entity", "city:0:1")
        assert player["node_type"] == "player"
        assert player["links"][0]["direction"] == "outgoing"
        assert city["links"][0]["direction"] == "incoming"
        assert engine.current_metrics()["player.gold"] == 120

    def test_typed_snapshot_rejects_cross_turn_state(self, engine):
        with pytest.raises(BeliefEngineError, match="multiple turns"):
            engine.ingest_typed_snapshot(
                {
                    "snapshot_id": "bad",
                    "turn_before": 42,
                    "turn_after": 43,
                    "entities": [],
                },
                turn=42,
            )


class TestInvalidInput:
    def test_requires_bound_game_and_required_entity_fields(self):
        engine = BeliefEngine(run_id="unbound")
        with pytest.raises(BeliefEngineError, match="not bound"):
            engine.create("belief", belief_payload(), turn=1)

    def test_rejects_invalid_entity_updates_and_conditions(self, engine):
        with pytest.raises(BeliefEngineError, match="Missing required belief fields"):
            engine.create("belief", {"statement": "missing fields"}, turn=1)
        with pytest.raises(BeliefEngineError, match="between 0 and 1"):
            engine.create("belief", belief_payload(probability=True), turn=1)
        with pytest.raises(BeliefEngineError, match="plan horizon"):
            engine.create("plan", plan_payload(horizon=7), turn=1)
        with pytest.raises(BeliefEngineError, match="Unsupported condition operator"):
            evaluate_condition({"metric": "gold", "operator": "approximately", "value": 1}, {"gold": 1})
        assert evaluate_condition({"metric": "missing", "operator": ">", "value": 1}, {}) is None
        assert evaluate_condition({"metric": "name", "operator": "contains", "value": "Rom"}, {"name": "Rome"}) is True

        engine.create("belief", belief_payload(), turn=1, entity_id="border")
        with pytest.raises(BeliefEngineError, match="protected fields"):
            engine.update("belief", "border", {"id": "other"}, turn=2)
        engine.delete("belief", "border", reason="obsolete", turn=2)
        with pytest.raises(BeliefEngineError, match="Cannot update deleted"):
            engine.update("belief", "border", {"probability": 0.2}, turn=3)

    def test_prediction_deadline_rejects_boolean_not_a_turn_number(self, engine):
        with pytest.raises(BeliefEngineError, match="deadline_turn"):
            engine.create("prediction", prediction_payload(deadline_turn=True), turn=1)
