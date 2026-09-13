"""Derived read models are memoized on the journal sequence.

``current_metrics`` / ``review`` / ``governance_turn_gate`` / ``turn_brief`` all
recompute derived state from the whole entity store, and several of them run on
every tool result. They are cached keyed on ``BeliefEngine._sequence``, which
every append advances — so the cache is sound only if a write really does
invalidate it, and only if callers cannot corrupt the cached value.

``review`` and ``governance_turn_gate`` additionally append events themselves,
which is why their memos are only populated when a run wrote nothing: a repeat
call would then see an identical store and write nothing again.
"""

from __future__ import annotations

from civ6_belief_engine.belief_engine import BeliefEngine


def _belief(statement: str) -> dict:
    return {
        "statement": statement,
        "category": "military",
        "probability": 0.8,
        "confidence": 0.7,
        "unknown_basis": True,
    }


def _engine(tmp_path) -> BeliefEngine:
    engine = BeliefEngine(run_id="derived-cache", directory=tmp_path)
    engine.bind_game("CIVILIZATION_TEST", 42)
    return engine


def test_repeated_reads_return_equal_results(tmp_path):
    engine = _engine(tmp_path)
    engine.create("belief", _belief("首都需要防御"), turn=10, entity_id="belief:one")

    first_metrics = engine.current_metrics()
    first_review = engine.review(turn=10)
    first_gate = engine.governance_turn_gate(turn=10)
    first_brief = engine.turn_brief(turn=10)

    assert engine.current_metrics() == first_metrics
    assert engine.review(turn=10) == first_review
    assert engine.governance_turn_gate(turn=10) == first_gate
    assert engine.turn_brief(turn=10) == first_brief


def test_a_write_invalidates_every_cache(tmp_path):
    engine = _engine(tmp_path)
    engine.create("belief", _belief("首都需要防御"), turn=10, entity_id="belief:one")

    before_brief = engine.turn_brief(turn=10)
    assert engine.governance_turn_gate(turn=10)["active_proposal_ids"] == []
    assert engine.current_metrics() == {}
    assert engine.review(turn=10)["metrics"] == {}

    # One write per cache, each observable in that cache's own output.
    engine.create("belief", _belief("边境需要巡逻"), turn=10, entity_id="belief:two")
    engine.create(
        "proposal",
        {
            "statement": "Build walls",
            "department": "production",
            "action_intent": {"tool": "set_city_production", "arguments": {}},
        },
        turn=10,
        entity_id="proposal:one",
    )
    engine.create(
        "observation",
        {
            "statement": "observed",
            "source": "get_game_overview",
            "observed_turn": 10,
            "metrics": {"player.gold": 250},
        },
        turn=10,
        entity_id="observation:one",
    )

    assert len(engine.turn_brief(turn=10)["beliefs"]) == len(before_brief["beliefs"]) + 1
    assert "proposal:one" in engine.governance_turn_gate(turn=10)["active_proposal_ids"]
    assert engine.current_metrics()["player.gold"] == 250
    # review() embeds the metrics it evaluated, so a stale memo would show {}.
    assert engine.review(turn=10)["metrics"]["player.gold"] == 250


def test_observation_write_refreshes_metrics(tmp_path):
    """Metrics come from observations, so a new observation must be visible."""

    engine = _engine(tmp_path)

    def observe(entity_id: str, metrics: dict) -> None:
        engine.create(
            "observation",
            {
                "statement": "observed",
                "source": "get_game_overview",
                "observed_turn": 10,
                "metrics": metrics,
            },
            turn=10,
            entity_id=entity_id,
        )

    observe("observation:one", {"player.gold": 100})
    assert engine.current_metrics().get("player.gold") == 100

    observe("observation:two", {"player.gold": 250})
    assert engine.current_metrics().get("player.gold") == 250


def test_callers_cannot_corrupt_the_cache(tmp_path):
    """A returned dict is caller-owned; mutating it must not poison later reads."""

    engine = _engine(tmp_path)
    engine.create("belief", _belief("首都需要防御"), turn=10, entity_id="belief:one")

    baseline = len(engine.turn_brief(turn=10)["beliefs"])
    engine.turn_brief(turn=10)["beliefs"].append({"id": "injected"})
    assert len(engine.turn_brief(turn=10)["beliefs"]) == baseline

    engine.governance_turn_gate(turn=10)["blockers"].append("injected")
    assert "injected" not in engine.governance_turn_gate(turn=10)["blockers"]

    engine.review(turn=10)["knowledge_stale"].append({"id": "injected"})
    assert all(
        item.get("id") != "injected"
        for item in engine.review(turn=10)["knowledge_stale"]
        if isinstance(item, dict)
    )


def test_cache_is_keyed_on_turn(tmp_path):
    engine = _engine(tmp_path)
    engine.create("belief", _belief("首都需要防御"), turn=10, entity_id="belief:one")

    assert engine.turn_brief(turn=10)["turn"] == 10
    assert engine.turn_brief(turn=11)["turn"] == 11
    assert engine.turn_brief(turn=10)["turn"] == 10


def test_reload_does_not_reuse_a_cache_from_another_instance(tmp_path):
    """A reload rebuilds the entity store; the cache must not survive it."""

    engine = _engine(tmp_path)
    engine.create("belief", _belief("首都需要防御"), turn=10, entity_id="belief:one")
    before = engine.turn_brief(turn=10)

    reloaded = BeliefEngine(run_id="derived-cache-2", directory=tmp_path)
    reloaded.bind_game("CIVILIZATION_TEST", 42)
    assert reloaded.turn_brief(turn=10) == before


def test_direct_reload_on_the_same_instance_resets_caches(tmp_path):
    """``_load`` rebuilds state in place, so the caches must be dropped."""

    engine = _engine(tmp_path)
    engine.create("belief", _belief("首都需要防御"), turn=10, entity_id="belief:one")
    before = engine.turn_brief(turn=10)

    engine.bind_game("CIVILIZATION_OTHER", 7)
    assert engine.turn_brief(turn=10)["beliefs"] != before["beliefs"]
