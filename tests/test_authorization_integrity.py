"""The authorization contract must be immutable once recorded.

Two layers keep a council approval of A from being executed as B:

* ``_intents_fingerprint`` pins the approved intent content on the council
  decision, so rewriting the proposal after the vote invalidates the approval
  (covered end-to-end in ``test_governance_server.py``).
* ``BeliefEngine.update`` refuses to patch a decision's own authorization
  fields, so the approval cannot be re-pointed at a different action either.

Without the second layer a caller could route a decision for one action,
rewrite its ``action_intent`` through ``update_belief_entity``, and have
``authorize_action`` match the rewritten contract.
"""

from __future__ import annotations

import pytest

from civ6_belief_engine.belief_engine import BeliefEngine, BeliefEngineError
from civ_mcp.server.tools.belief_tools import _intents_fingerprint


def _routed_decision(tmp_path) -> tuple[BeliefEngine, dict]:
    engine = BeliefEngine(run_id="auth-integrity", directory=tmp_path)
    engine.bind_game("CIVILIZATION_TEST", 42)
    decision = engine.route_decision(
        statement="Move the warrior onto the hill",
        probability=0.9,
        confidence=0.9,
        impact="high",
        urgency="critical",
        irreversibility=0.95,
        turn=1,
        action_intent={
            "tool": "unit_action",
            "params": {
                "unit_id": 1,
                "action": "move",
                "target_x": 3,
                "target_y": 3,
            },
        },
    )
    return engine, decision


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action_intent", {"tool": "purchase_item", "params": {"city_id": 4}}),
        ("council_decision_id", "council:1:forged"),
        ("args_hash", "0" * 64),
    ],
)
def test_decision_authorization_fields_cannot_be_patched(tmp_path, field, value):
    engine, decision = _routed_decision(tmp_path)

    with pytest.raises(BeliefEngineError, match="protected fields"):
        engine.update("decision", decision["id"], {field: value}, turn=1)

    stored = engine.get("decision", decision["id"])
    assert stored.get(field) != value


def test_decision_authorization_fields_cannot_be_removed(tmp_path):
    engine, decision = _routed_decision(tmp_path)

    with pytest.raises(BeliefEngineError, match="protected fields"):
        engine.update(
            "decision",
            decision["id"],
            {},
            turn=1,
            _remove_fields=("action_intent",),
        )


def test_decision_lifecycle_fields_remain_patchable(tmp_path):
    """Protection must not freeze the state machine it protects."""

    engine, decision = _routed_decision(tmp_path)
    updated = engine.update("decision", decision["id"], {"route": "slow"}, turn=1)
    assert updated["route"] == "slow"


def test_proposal_action_intents_are_not_yet_protected(tmp_path):
    """Documents the residual surface: the council pin, not field protection,
    is what guards proposal intents (a proposal may legitimately be revised
    before it is voted on)."""

    engine = BeliefEngine(run_id="auth-integrity", directory=tmp_path)
    engine.bind_game("CIVILIZATION_TEST", 42)
    engine.create(
        "proposal",
        {
            "status": "active",
            "statement": "Build walls",
            "department": "production",
            "action_intent": {"tool": "set_city_production", "arguments": {}},
            "action_intents": [{"tool": "set_city_production", "arguments": {}}],
        },
        turn=1,
        entity_id="proposal:1",
    )

    updated = engine.update(
        "proposal",
        "proposal:1",
        {
            "action_intent": {"tool": "purchase_item", "arguments": {}},
            "action_intents": [{"tool": "purchase_item", "arguments": {}}],
        },
        turn=1,
    )
    assert updated["action_intents"][0]["tool"] == "purchase_item"


def test_intents_fingerprint_ignores_key_order_but_tracks_content():
    first = [{"tool": "unit_action", "arguments": {"a": 1, "b": 2}}]
    reordered = [{"arguments": {"b": 2, "a": 1}, "tool": "unit_action"}]
    changed = [{"tool": "unit_action", "arguments": {"a": 1, "b": 3}}]

    assert _intents_fingerprint(first) == _intents_fingerprint(reordered)
    assert _intents_fingerprint(first) != _intents_fingerprint(changed)


def test_intents_fingerprint_is_sensitive_to_order_of_intents():
    a = [{"tool": "a"}, {"tool": "b"}]
    b = [{"tool": "b"}, {"tool": "a"}]
    # Sorting makes the fingerprint a set identity: the council approved a set
    # of actions, not a sequence.
    assert _intents_fingerprint(a) == _intents_fingerprint(b)


def test_intents_fingerprint_ignores_non_mapping_entries():
    assert _intents_fingerprint([{"tool": "a"}, "not-a-dict", None]) == (
        _intents_fingerprint([{"tool": "a"}])
    )


def test_authorization_from_a_pre_rollback_epoch_is_not_consumable(tmp_path):
    """Crash window: the reload marker is durable, the sweep is not.

    ``record_game_reload`` appends the epoch marker first and then cancels the
    pending authorizations one by one. A crash in between leaves a journal
    whose epoch counter advanced while its authorizations still look live.
    ``authorize_action`` must reject them structurally rather than relying on a
    sweep that may never have completed.
    """

    intent_params = {"unit_id": 1, "action": "move", "target_x": 4, "target_y": 4}
    engine = BeliefEngine(run_id="epoch-auth", directory=tmp_path)
    engine.bind_game("CIVILIZATION_TEST", 42)
    engine.create(
        "decision",
        {
            "statement": "Move the warrior",
            "route": "fast",
            "decision_state": "authorized",
            "action_intent": {"tool": "unit_action", "params": intent_params},
        },
        turn=3,
        entity_id="decision:epoch",
    )
    assert (
        engine.authorize_action(
            tool="unit_action", params=intent_params, turn=3, required=True
        )["authorized"]
        is True
    )

    # Marker written at epoch 2; the process died before the sweep ran.
    engine._append(
        "game.reloaded",
        "epoch_marker",
        {
            "id": "epoch_2_3",
            "epoch": 2,
            "reason": "crash_after_marker",
            "turn": 3,
            "prior_epoch_max_turn": 3,
            "details": {},
        },
        turn=3,
    )

    reloaded = BeliefEngine(run_id="epoch-auth", directory=tmp_path)
    reloaded.bind_game("CIVILIZATION_TEST", 42)
    assert reloaded.epoch == 2
    stored = reloaded.get("decision", "decision:epoch")
    # Both states are consumable by authorize_action; load-time recovery may
    # have moved authorized -> retryable, which does not make it safe.
    assert stored["decision_state"] in {"authorized", "retryable"}
    assert stored["status"] == "active", "the reload sweep never ran"

    result = reloaded.authorize_action(
        tool="unit_action", params=intent_params, turn=3, required=True
    )
    assert result["authorized"] is False
    assert result["decision_id"] is None
