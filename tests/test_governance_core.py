"""Unit contracts for deterministic governance models and arbitration."""

from __future__ import annotations

import pytest

from civ6_belief_engine.governance.council import GovernanceCouncil, pareto_dominates
from civ6_belief_engine.governance.devils_advocate import (
    CounterEvidence,
    DevilsAdvocate,
    DevilsAdvocateReview,
    DevilsAdvocateVerdict,
)
from civ6_belief_engine.governance.models import (
    ActionIntent,
    BudgetLock,
    EvidenceRequirement,
    Outcome,
    OutcomeStatus,
    ProbabilityConfidence,
    Proposal,
    RulesetCapabilities,
)


def _proposal(
    proposal_id: str,
    *,
    priority: int = 50,
    probability: float = 0.7,
    confidence: float = 0.8,
    hard_constraints: dict[str, bool] | None = None,
    locks: tuple[BudgetLock, ...] = (),
    benefits: dict[str, float] | None = None,
    costs: dict[str, float] | None = None,
    opportunity_cost: float = 0,
    expires_turn: int | None = None,
) -> Proposal:
    return Proposal(
        proposal_id=proposal_id,
        department="military",
        summary=f"Proposal {proposal_id}",
        goal_ids=("survive",),
        success=ProbabilityConfidence(probability, confidence),
        priority=priority,
        hard_constraints=hard_constraints or {},
        budget_locks=locks,
        benefits=benefits or {},
        costs=costs or {},
        opportunity_cost=opportunity_cost,
        expires_turn=expires_turn,
    )


def test_probability_and_confidence_are_separate_strict_values():
    assessment = ProbabilityConfidence(probability=0.1, confidence=0.95)
    assert assessment.probability == 0.1
    assert assessment.confidence == 0.95

    for field_name in ("probability", "confidence"):
        values = {"probability": 0.5, "confidence": 0.5, field_name: True}
        with pytest.raises(TypeError, match="not bool"):
            ProbabilityConfidence(**values)
        values[field_name] = -0.01
        with pytest.raises(ValueError):
            ProbabilityConfidence(**values)
        values[field_name] = 1.01
        with pytest.raises(ValueError):
            ProbabilityConfidence(**values)


def test_capabilities_reject_truthy_non_boole():
    assert RulesetCapabilities.standard().world_congress is False
    with pytest.raises(TypeError, match="governors must be a bool"):
        RulesetCapabilities(governors=1)  # type: ignore[arg-type]


def test_action_intent_binds_exact_arguments_and_relevant_evidence():
    evidence = EvidenceRequirement(
        requirement_id="combat:7:9",
        tool="get_combat_estimate",
        target_entity_id="unit:9",
        params={"attacker_unit_id": 7, "defender_unit_id": 9},
        min_observation_sequence=12,
        max_age_turns=0,
        required_facts=("attacker_cs", "defender_cs", "est_damage_to_defender"),
        required_metrics=("combat.attacker_cs", "combat.defender_cs"),
        expected_facts={"attacker_unit_id": 7},
    )
    intent = ActionIntent(
        intent_id="attack:7:9",
        tool="unit_action",
        arguments={"unit_id": 7, "action": "attack", "target_id": 9},
        proposal_id="secure-border",
        evidence_requirements=(evidence,),
        allowed_turn=42,
    )
    same = ActionIntent(
        intent_id="attack:7:9-copy",
        tool="unit_action",
        arguments={"target_id": 9, "action": "attack", "unit_id": 7},
        proposal_id="secure-border",
    )
    assert intent.arguments_hash == same.arguments_hash
    assert len(intent.arguments_hash) == 64
    assert evidence.params == {"attacker_unit_id": 7, "defender_unit_id": 9}
    assert evidence.required_metrics == (
        "combat.attacker_cs",
        "combat.defender_cs",
    )
    assert evidence.expected_facts == {"attacker_unit_id": 7}
    nested = {"path": {"waypoints": [1, 2]}}
    frozen = ActionIntent(
        intent_id="move:7",
        tool="unit_action",
        arguments=nested,
        proposal_id="secure-border",
    )
    nested["path"]["waypoints"].append(3)
    assert frozen.arguments["path"]["waypoints"] == (1, 2)
    with pytest.raises(TypeError):
        evidence.params["attacker_unit_id"] = 8  # type: ignore[index]
    with pytest.raises(TypeError):
        intent.arguments["unit_id"] = 8  # type: ignore[index]
    with pytest.raises(ValueError, match="does not match"):
        ActionIntent(
            intent_id="bad",
            tool="unit_action",
            arguments={"unit_id": 7},
            proposal_id="secure-border",
            arguments_hash="wrong",
        )


def test_devils_advocate_can_agree_and_cannot_object_without_grounding():
    proposal = _proposal("hold-line")
    agreement = DevilsAdvocate.review(
        proposal,
        review_id="review:1",
        verdict=DevilsAdvocateVerdict.AGREE,
        rationale="Combat estimate and reinforcement time support the plan.",
        assessment=ProbabilityConfidence(0.8, 0.9),
    )
    assert agreement.verdict == "agree"

    with pytest.raises(ValueError, match="object requires counterevidence"):
        DevilsAdvocateReview(
            review_id="review:2",
            proposal_id=proposal.proposal_id,
            verdict=DevilsAdvocateVerdict.OBJECT,
            rationale="I disagree.",
            assessment=ProbabilityConfidence(0.4, 0.2),
        )

    grounded_objection = DevilsAdvocateReview(
        review_id="review:3",
        proposal_id=proposal.proposal_id,
        verdict=DevilsAdvocateVerdict.OBJECT,
        rationale="The projected defense assumes the archer arrives this turn.",
        assessment=ProbabilityConfidence(0.3, 0.85),
        counterevidence=(
            CounterEvidence(
                observation_id="observation:path:7",
                statement="Archer requires two turns to reach city:4.",
                source_tool="assess_route_combat_risk",
                observed_turn=42,
            ),
        ),
        alternative="Fortify unit:7 and move the archer before counterattacking.",
    )
    assert grounded_objection.alternative is not None

    invalidated_assumption_is_grounding = DevilsAdvocateReview(
        review_id="review:3b",
        proposal_id=proposal.proposal_id,
        verdict=DevilsAdvocateVerdict.OBJECT,
        rationale="The plan depends on an impossible arrival time.",
        assessment=ProbabilityConfidence(0.3, 0.85),
        invalidated_assumptions=("Archer can reach city:4 this turn",),
    )
    assert invalidated_assumption_is_grounding.invalidated_assumptions

    with pytest.raises(ValueError, match="requires concrete conditions"):
        DevilsAdvocateReview(
            review_id="review:4",
            proposal_id=proposal.proposal_id,
            verdict=DevilsAdvocateVerdict.AGREE_WITH_CONDITIONS,
            rationale="Accept only after verification.",
            assessment=ProbabilityConfidence(0.6, 0.6),
        )


def test_pareto_keeps_probability_and_confidence_as_independent_dimensions():
    safer = _proposal(
        "safer",
        probability=0.8,
        confidence=0.9,
        benefits={"survival": 0.8, "tempo": 0.5},
        costs={"unit_risk": 0.2},
    )
    weaker = _proposal(
        "weaker",
        probability=0.8,
        confidence=0.6,
        benefits={"survival": 0.7, "tempo": 0.5},
        costs={"unit_risk": 0.3},
    )
    assert pareto_dominates(safer, weaker) is True
    assert pareto_dominates(weaker, safer) is False

    probability_tradeoff = _proposal(
        "tradeoff",
        probability=0.9,
        confidence=0.5,
        benefits={"survival": 0.9},
        costs={"unit_risk": 0.4},
    )
    assert pareto_dominates(safer, probability_tradeoff) is False
    assert pareto_dominates(probability_tradeoff, safer) is False


def test_council_applies_hard_constraints_then_locks_and_priority():
    slot = BudgetLock("city_production", scope="city:4", exclusive=True)
    illegal = _proposal(
        "illegal",
        priority=100,
        hard_constraints={"mechanic_available": False},
        locks=(slot,),
    )
    urgent = _proposal(
        "urgent-defense",
        priority=90,
        probability=0.65,
        confidence=0.8,
        locks=(slot,),
        benefits={"survival": 0.8},
        opportunity_cost=4,
    )
    attractive_but_lower_priority = _proposal(
        "wonder",
        priority=70,
        probability=0.95,
        confidence=0.95,
        locks=(slot,),
        benefits={"culture": 1.0},
        opportunity_cost=1,
    )

    decision = GovernanceCouncil().decide(
        turn=42,
        proposals=(illegal, attractive_but_lower_priority, urgent),
        budget_limits={},
    )
    assert decision.selected_proposal_ids == ("urgent-defense",)
    assert "hard constraint failed" in decision.rejected_reasons["illegal"][0]
    assert "conflicts with selected proposal urgent-defense" in decision.rejected_reasons[
        "wonder"
    ][0]
    assert "weighted scoring" in decision.explanation[2]


def test_council_uses_pareto_then_opportunity_cost_without_total_score():
    slot = BudgetLock("research_slot", exclusive=True)
    dominant = _proposal(
        "dominant",
        locks=(slot,),
        benefits={"science": 0.8},
        costs={"turns": 4},
        opportunity_cost=8,
    )
    dominated = _proposal(
        "dominated",
        locks=(slot,),
        benefits={"science": 0.7},
        costs={"turns": 5},
        opportunity_cost=1,
    )
    decision = GovernanceCouncil().decide(
        turn=42,
        proposals=(dominated, dominant),
        budget_limits={},
    )
    assert decision.selected_proposal_ids == ("dominant",)

    city_slot = BudgetLock("city_production", scope="city:8", exclusive=True)
    growth = _proposal(
        "growth",
        locks=(city_slot,),
        benefits={"growth": 0.8, "defense": 0.2},
        opportunity_cost=3,
    )
    defense = _proposal(
        "defense",
        locks=(city_slot,),
        benefits={"growth": 0.2, "defense": 0.8},
        opportunity_cost=7,
    )
    tie_break = GovernanceCouncil().decide(
        turn=42,
        proposals=(defense, growth),
        budget_limits={},
    )
    assert tie_break.selected_proposal_ids == ("growth",)


def test_council_reserves_consumable_budget_and_fails_closed_without_capacity():
    first = _proposal("buy-builder", locks=(BudgetLock("gold", 120),))
    second = _proposal("upgrade-unit", locks=(BudgetLock("gold", 100),))
    decision = GovernanceCouncil().decide(
        turn=42,
        proposals=(first, second),
        budget_limits={"gold": 150},
    )
    assert len(decision.selected_proposal_ids) == 1
    rejected_id = ({"buy-builder", "upgrade-unit"} - set(decision.selected_proposal_ids)).pop()
    assert "would use 220/150" in decision.rejected_reasons[rejected_id][0]

    missing = GovernanceCouncil().decide(
        turn=42,
        proposals=(first,),
        budget_limits={},
    )
    assert missing.selected_proposal_ids == ()
    assert missing.rejected_reasons["buy-builder"] == (
        "budget capacity missing for gold:global",
    )


def test_council_respects_budget_locks_held_by_an_earlier_session():
    active_gold = BudgetLock("gold", 80)
    purchase = _proposal("buy-builder", locks=(BudgetLock("gold", 40),))
    occupied_city = BudgetLock(
        "city_production", scope="city:0:4", exclusive=True
    )
    production = _proposal(
        "build-walls",
        locks=(
            BudgetLock(
                "city_production", scope="city:0:4", exclusive=True
            ),
        ),
    )

    decision = GovernanceCouncil().decide(
        turn=12,
        proposals=(purchase, production),
        budget_limits={"gold": 100},
        held_locks=(active_gold, occupied_city),
    )

    assert decision.selected_proposal_ids == ()
    assert "would use 120/100" in decision.rejected_reasons["buy-builder"][0]
    assert "active reservation" in decision.rejected_reasons["build-walls"][0]


def test_outcome_state_requires_an_error_for_non_success():
    succeeded = Outcome(
        outcome_id="outcome:1",
        intent_id="intent:1",
        proposal_id="proposal:1",
        decision_id="decision:1",
        status=OutcomeStatus.SUCCEEDED,
        turn=42,
        result={"action_success": True},
        observation_ids=("observation:1",),
    )
    assert succeeded.result["action_success"] is True
    with pytest.raises(ValueError, match="must contain an error"):
        Outcome(
            outcome_id="outcome:2",
            intent_id="intent:2",
            proposal_id="proposal:1",
            decision_id="decision:1",
            status=OutcomeStatus.RETRYABLE,
            turn=42,
        )
