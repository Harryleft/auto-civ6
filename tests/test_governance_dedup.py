"""Regression tests for duplicate council intents and the turn gate.

Replays the 2026-08-15 duplicate-settle incident shape: three approved
settle-capital proposals whose decisions carry no ``intent_id`` (legacy
records). A cancelled duplicate must clear both the dedup guard and the
turn gate; an unfinished one must keep both engaged.
"""

from __future__ import annotations

import pytest

from civ6_belief_engine.belief_engine import BeliefEngine


SETTLE_ARGS = {"unit_id": 65536, "action": "found_city", "target_x": 43, "target_y": 38}


def approve_proposal(
    engine: BeliefEngine,
    proposal_id: str,
    *,
    council_id: str,
    intent_id: str,
    args: dict | None = None,
    turn: int = 2,
) -> None:
    args = args if args is not None else SETTLE_ARGS
    engine.upsert(
        "proposal",
        proposal_id,
        {
            "statement": f"Settle the capital ({proposal_id}).",
            "department": "settlement",
            "action_intent": {
                "intent_id": intent_id,
                "tool": "unit_action",
                "arguments": args,
                "proposal_id": proposal_id,
            },
            "action_intents": [
                {
                    "intent_id": intent_id,
                    "tool": "unit_action",
                    "arguments": args,
                    "proposal_id": proposal_id,
                }
            ],
            "council_state": "approved",
            "council_decision_id": council_id,
            "submitted_turn": turn,
        },
        turn=turn,
    )


def route_decision_entity(
    engine: BeliefEngine,
    proposal_id: str,
    *,
    council_id: str,
    decision_state: str,
    args: dict | None = None,
    intent_id: str | None = None,
    turn: int = 2,
) -> str:
    """Create the routed decision the gate matches against.

    ``intent_id=None`` reproduces the legacy incident records exactly.
    """

    args = args if args is not None else SETTLE_ARGS
    action_intent: dict = {"tool": "unit_action", "proposal_id": proposal_id}
    action_intent["params"] = args
    if intent_id is not None:
        action_intent["intent_id"] = intent_id
    decision = engine.create(
        "decision",
        {
            "statement": f"Route settle action for {proposal_id}.",
            "route": "slow",
            "decision_state": decision_state,
            "council_decision_id": council_id,
            "action_intent": action_intent,
        },
        turn=turn,
    )
    return decision["id"]


def test_cancelled_legacy_duplicate_clears_the_turn_gate(engine):
    approve_proposal(
        engine, "prop_settle_capital_t2", council_id="council:2:a", intent_id="intent_settle_capital"
    )
    route_decision_entity(
        engine,
        "prop_settle_capital_t2",
        council_id="council:2:a",
        decision_state="cancelled",
    )

    gate = engine.governance_turn_gate(turn=2)
    assert "council_action_intents_not_completed" not in gate["blockers"]
    assert gate["pending_council_intents"] == []


def test_unfinished_duplicate_keeps_the_turn_gate_engaged(engine):
    approve_proposal(
        engine, "settle:capital-4338-turn2", council_id="council:2:b", intent_id="intent:found-capital-4338"
    )
    # Authorized but never executed and never cancelled: the gate must hold.
    route_decision_entity(
        engine,
        "settle:capital-4338-turn2",
        council_id="council:2:b",
        decision_state="authorized",
    )

    gate = engine.governance_turn_gate(turn=2)
    assert "council_action_intents_not_completed" in gate["blockers"]
    assert gate["pending_council_intents"][0]["proposal_id"] == "settle:capital-4338-turn2"


def test_dedup_guard_blocks_pending_and_releases_after_terminal(engine):
    approve_proposal(
        engine, "p_settle_capital", council_id="council:2:c", intent_id="intent_settle_capital"
    )
    decision_id = route_decision_entity(
        engine,
        "p_settle_capital",
        council_id="council:2:c",
        decision_state="authorized",
    )

    duplicate = engine.find_duplicate_pending_intent(
        tool="unit_action", params=dict(SETTLE_ARGS)
    )
    assert duplicate == {
        "proposal_id": "p_settle_capital",
        "intent_id": "intent_settle_capital",
    }

    # Different arguments or a different tool are not duplicates.
    assert (
        engine.find_duplicate_pending_intent(
            tool="unit_action", params={**SETTLE_ARGS, "target_x": 99}
        )
        is None
    )
    assert (
        engine.find_duplicate_pending_intent(
            tool="set_research", params=dict(SETTLE_ARGS)
        )
        is None
    )

    # A terminal decision releases the guard: retry becomes legitimate.
    engine.update(
        "decision", decision_id, {"decision_state": "cancelled"}, turn=3
    )
    assert (
        engine.find_duplicate_pending_intent(
            tool="unit_action", params=dict(SETTLE_ARGS)
        )
        is None
    )


def test_dedup_guard_matches_terminal_via_legacy_args_hash(engine):
    # Legacy decision without intent_id must still count as terminal via
    # the tool + canonical-argument-hash fallback.
    approve_proposal(
        engine, "prop_legacy", council_id="council:2:d", intent_id="intent_legacy"
    )
    route_decision_entity(
        engine,
        "prop_legacy",
        council_id="council:2:d",
        decision_state="cancelled",
        intent_id=None,
    )
    assert (
        engine.find_duplicate_pending_intent(
            tool="unit_action", params=dict(SETTLE_ARGS)
        )
        is None
    )


def test_dedup_guard_allows_resubmitting_the_same_proposal(engine):
    approve_proposal(
        engine, "p_settle_capital", council_id="council:2:f", intent_id="intent_f"
    )
    route_decision_entity(
        engine,
        "p_settle_capital",
        council_id="council:2:f",
        decision_state="authorized",
    )
    # Editing/reactivating one's own authorization is not a duplicate.
    assert (
        engine.find_duplicate_pending_intent(
            tool="unit_action",
            params=dict(SETTLE_ARGS),
            exclude_proposal_id="p_settle_capital",
        )
        is None
    )


def test_dedup_guard_ignores_non_approved_proposals(engine):
    approve_proposal(
        engine, "prop_proposed", council_id="council:2:e", intent_id="intent_e"
    )
    engine.update(
        "proposal", "prop_proposed", {"council_state": "rejected"}, turn=2
    )
    assert (
        engine.find_duplicate_pending_intent(
            tool="unit_action", params=dict(SETTLE_ARGS)
        )
        is None
    )
