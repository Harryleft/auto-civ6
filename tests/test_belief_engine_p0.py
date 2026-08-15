"""Regression tests for the confirmed P0 reliability defects.

Covers three failure classes that each ended a whole game before:

- P0-1: a decision left in "executing" by a crash or an uncaught exception
  could never be cancelled and permanently blocked the governance turn gate.
- P0-3: a truncated JSONL tail fused with the next append and silently
  swallowed every later event on reload.
- P0-4: an autosave rollback created two conflicting histories for the same
  turn with no epoch marker separating them.

All tests are offline; persistence goes through tmp_path only.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from civ6_belief_engine.belief_engine import BeliefEngine, BeliefEngineError, action_args_hash
from civ_mcp.server import pipeline as server_module
from civ_mcp.server import _logged

GAME = ("CIVILIZATION_TEST", 7)


@pytest.fixture
def engine(tmp_path):
    instance = BeliefEngine(run_id="p0-test", directory=tmp_path)
    instance.bind_game(*GAME)
    return instance


def _bind(tmp_path, run_id: str) -> BeliefEngine:
    instance = BeliefEngine(run_id=run_id, directory=tmp_path)
    instance.bind_game(*GAME)
    return instance


def _typed_snapshot(engine: BeliefEngine, turn: int) -> None:
    engine.ingest_typed_snapshot(
        {
            "snapshot_id": f"snapshot_{turn}",
            "turn_before": turn,
            "turn_after": turn,
            "capabilities": {"ruleset": "RULESET_STANDARD"},
            "entities": [],
            "relations": [],
            "metrics": {"player.gold": 100 + turn},
        },
        turn=turn,
    )


def _route_authorized_decision(
    engine: BeliefEngine,
    *,
    turn: int,
    council_decision_id: str | None = None,
) -> dict:
    params = {"tech_or_civic": "TECH_WRITING"}
    return engine.route_decision(
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
        council_decision_id=council_decision_id,
        turn=turn,
    )


class TestJsonlIntegrity:
    def test_truncated_tail_is_quarantined_and_log_stays_appendable(self, tmp_path):
        first = _bind(tmp_path, "run-a")
        first.create("belief", {"statement": "s", "category": "military", "probability": 0.7, "confidence": 0.8}, turn=1, entity_id="border")
        first.update("belief", "border", {"probability": 0.5}, turn=2)
        assert first.path is not None
        with first.path.open("a") as handle:
            # Simulate a crash mid-write: half a JSON line, no trailing newline.
            handle.write('{"v":1,"event_id":"poison","sequ')

        reloaded = _bind(tmp_path, "run-b")
        assert reloaded.get("belief", "border")["probability"] == 0.5
        assert len(reloaded.history()) == 3  # created, updated, integrity marker

        # The rewrite replaced the poison line and ends with a newline, so a
        # later append can no longer fuse with a truncated line.
        lines = reloaded.path.read_text().splitlines()
        events = [json.loads(line) for line in lines]
        markers = [event for event in events if event["event_type"] == "log.integrity"]
        assert len(markers) == 1
        payload = markers[0]["entity"]
        assert payload["quarantined_line_count"] == 1
        assert payload["quarantined_line_numbers"] == [3]
        quarantined = payload["quarantined_lines"][0]
        assert quarantined["line_number"] == 3
        assert quarantined["preview"].startswith('{"v":1,"event_id":"poison"')
        assert len(quarantined["sha256"]) == 64
        assert payload["repaired_at"] > 0
        # marker references the last intact event's turn
        assert markers[0]["turn"] == 2

        reloaded.create("belief", {"statement": "after repair", "category": "military", "probability": 0.7, "confidence": 0.8}, turn=3, entity_id="after")
        final = _bind(tmp_path, "run-c")
        assert final.get("belief", "border") is not None
        assert final.get("belief", "after") is not None
        # No new corruption and no repeated marker on a clean reload.
        assert len([e for e in final.history() if e["event_type"] == "log.integrity"]) == 1

    def test_non_dict_lines_are_quarantined_too(self, tmp_path):
        first = _bind(tmp_path, "run-a")
        first.create("belief", {"statement": "s", "category": "military", "probability": 0.7, "confidence": 0.8}, turn=1, entity_id="border")
        with first.path.open("a") as handle:
            handle.write(json.dumps(["also-not-an-event"]) + "\n")

        reloaded = _bind(tmp_path, "run-b")

        assert reloaded.get("belief", "border") is not None
        markers = [e for e in reloaded.history() if e["event_type"] == "log.integrity"]
        assert len(markers) == 1
        assert markers[0]["entity"]["quarantined_line_numbers"] == [2]

    def test_schema_invalid_json_lines_are_quarantined(self, tmp_path):
        """Valid JSON that breaks the event schema must not crash the load
        (a non-numeric sequence used to raise out of int()) or silently
        corrupt the replay."""
        first = _bind(tmp_path, "run-a")
        first.create("belief", {"statement": "s", "category": "military", "probability": 0.7, "confidence": 0.8}, turn=1, entity_id="border")
        bad_lines = [
            {"v": 1, "event_id": "x1", "sequence": "not-a-number", "event_type": "entity.created", "entity": {"id": "b1"}},  # sequence non-numeric
            {"v": 1, "event_id": "x2", "sequence": 5, "entity": {"id": "b2"}},  # missing event_type
            {"v": 1, "event_id": "x3", "sequence": 6, "event_type": "entity.created", "entity": {"no_id": True}},  # entity without str id
        ]
        with first.path.open("a") as handle:
            for bad in bad_lines:
                handle.write(json.dumps(bad) + "\n")

        reloaded = _bind(tmp_path, "run-b")

        assert reloaded.get("belief", "border") is not None
        markers = [e for e in reloaded.history() if e["event_type"] == "log.integrity"]
        assert len(markers) == 1
        assert markers[0]["entity"]["quarantined_line_numbers"] == [2, 3, 4]


class TestOrphanedExecutingRecovery:
    def test_rebind_recovers_orphaned_executing_decision(self, tmp_path):
        first = _bind(tmp_path, "run-a")
        decision = _route_authorized_decision(first, turn=5)
        first.authorize_action(
            tool="set_research",
            params={"tech_or_civic": "TECH_WRITING"},
            turn=5,
            required=True,
        )
        assert first.get("decision", decision["id"])["decision_state"] == "executing"

        reloaded = _bind(tmp_path, "run-b")

        recovered = reloaded.get("decision", decision["id"])
        assert recovered["decision_state"] == "retryable"
        assert recovered["recovery_reason"] == "process_restarted_during_execution"
        # The recovery itself is persisted so the intervention is auditable.
        events = reloaded.history(entity_id=decision["id"])
        assert events[-1]["event_type"] == "entity.updated"
        assert events[-1]["entity"]["recovery_reason"] == (
            "process_restarted_during_execution"
        )

    def test_turn_gate_reclaims_stale_executing_and_unlocks_cancellation(self, engine):
        _typed_snapshot(engine, 30)
        decision = _route_authorized_decision(engine, turn=30)
        engine.authorize_action(
            tool="set_research",
            params={"tech_or_civic": "TECH_WRITING"},
            turn=30,
            required=True,
        )
        _typed_snapshot(engine, 31)

        blocked = engine.governance_turn_gate(turn=31)

        # The obligation still gates the turn...
        assert blocked["blockers"] == ["routed_actions_not_completed"]
        # ...but as retryable, so the agent can close it instead of deadlocking.
        assert blocked["pending_authorizations"][0]["decision_state"] == "retryable"
        reclaimed = engine.get("decision", decision["id"])
        assert reclaimed["decision_state"] == "retryable"
        assert reclaimed["recovery_reason"] == "stale_executing_reclaimed_turn_boundary"

        engine.cancel_action_authorization(
            decision["id"], reason="Execution was orphaned at the turn boundary.", turn=31
        )
        assert engine.governance_turn_gate(turn=31)["ready"] is True

    def test_same_turn_executing_is_never_reclaimed(self, engine):
        _typed_snapshot(engine, 30)
        decision = _route_authorized_decision(engine, turn=30)
        engine.authorize_action(
            tool="set_research",
            params={"tech_or_civic": "TECH_WRITING"},
            turn=30,
            required=True,
        )

        gate = engine.governance_turn_gate(turn=30)

        assert gate["pending_authorizations"][0]["decision_state"] == "executing"
        assert engine.get("decision", decision["id"])["decision_state"] == "executing"

    def test_executing_can_be_cancelled_same_turn_and_late_result_is_noop(self, engine):
        """Same-turn cancellation must not deadlock, and a late completion
        from a genuinely in-flight action cannot resurrect the authorization."""
        decision = _route_authorized_decision(engine, turn=5)
        executing = engine.authorize_action(
            tool="set_research",
            params={"tech_or_civic": "TECH_WRITING"},
            turn=5,
            required=True,
        )
        assert executing["authorized"] is True
        assert engine.get("decision", decision["id"])["decision_state"] == "executing"

        # Same-turn cancel used to raise — the same-turn deadlock: if the
        # record path also failed, nothing but a process restart cleared it.
        cancelled = engine.cancel_action_authorization(
            decision["id"], reason="recorder failed; outcome unknown", turn=5
        )
        assert cancelled["decision_state"] == "cancelled"

        # A late result arrives after the cancellation: the action event is
        # recorded, but complete_action_authorization no-ops on the
        # cancelled decision and budget locks stay released.
        engine.record_tool_result(
            tool="set_research",
            params={"tech_or_civic": "TECH_WRITING"},
            result="OK",
            turn=5,
            category="action",
            success=True,
            duration_ms=10,
            decision_id=decision["id"],
            decision_route="fast",
        )
        assert engine.get("decision", decision["id"])["decision_state"] == "cancelled"

        cancelled_again = engine.cancel_action_authorization(
            decision["id"], reason="already cancelled", turn=6
        )
        assert cancelled_again["decision_state"] == "cancelled"

    def test_stale_executing_can_be_cancelled_across_turns(self, engine):
        decision = _route_authorized_decision(engine, turn=5)
        engine.authorize_action(
            tool="set_research",
            params={"tech_or_civic": "TECH_WRITING"},
            turn=5,
            required=True,
        )

        cancelled = engine.cancel_action_authorization(
            decision["id"], reason="Action window passed without an outcome.", turn=6
        )
        assert cancelled["decision_state"] == "cancelled"
        assert cancelled["cancellation_reason"] == (
            "Action window passed without an outcome."
        )


class TestGameReloadEpochs:
    def test_record_game_reload_marks_epoch_and_voids_pending_authorizations(self, tmp_path):
        engine = _bind(tmp_path, "run-a")
        _typed_snapshot(engine, 9)
        decision = _route_authorized_decision(
            engine, turn=9, council_decision_id="council:9:test"
        )
        engine.create(
            "budget_lock",
            {
                "resource": "research",
                "amount": 1.0,
                "proposal_id": "proposal:test",
                "council_decision_id": "council:9:test",
            },
            turn=9,
            entity_id="lock:test",
        )

        marker = engine.record_game_reload(
            reason="connection_recovery_restart_and_load",
            turn=8,
            details={"save": "autosave_8.Civ6Save"},
        )

        assert marker is not None
        payload = marker["entity"]
        assert payload["id"] == "epoch_2_8"
        assert payload["epoch"] == 2
        assert payload["reason"] == "connection_recovery_restart_and_load"
        assert payload["turn"] == 8
        assert payload["prior_epoch_max_turn"] == 9
        assert payload["details"] == {"save": "autosave_8.Civ6Save"}
        assert marker["event_type"] == "game.reloaded"
        assert marker["epoch"] == 2

        voided = engine.get("decision", decision["id"])
        assert voided["status"] == "resolved"
        assert voided["decision_state"] == "cancelled"
        assert voided["cancellation_reason"] == (
            "invalidated_by_game_reload:connection_recovery_restart_and_load"
        )
        assert voided["cancelled_turn"] == 8
        lock = engine.get("budget_lock", "lock:test")
        assert lock["status"] == "archived"
        assert lock["released_turn"] == 8
        assert lock["release_reason"] == (
            "decision_voided_by_reload:connection_recovery_restart_and_load"
        )

        # Events after the marker carry the new epoch...
        engine.create("belief", {"statement": "post reload", "category": "military", "probability": 0.7, "confidence": 0.8}, turn=8, entity_id="post")
        assert engine.history(entity_id="post")[-1]["epoch"] == 2
        # ...and a restart recomputes the epoch from the marker count.
        reloaded = _bind(tmp_path, "run-b")
        reloaded.create("belief", {"statement": "after restart", "category": "military", "probability": 0.7, "confidence": 0.8}, turn=8, entity_id="restart")
        assert reloaded.history(entity_id="restart")[-1]["epoch"] == 2

    def test_unbound_engine_records_nothing(self):
        assert (
            BeliefEngine(run_id="unbound").record_game_reload(reason="anything")
            is None
        )

    def test_reload_defaults_turn_to_prior_epoch_max_turn(self, tmp_path):
        engine = _bind(tmp_path, "run-a")
        _typed_snapshot(engine, 9)

        marker = engine.record_game_reload(reason="manual save load")

        assert marker["entity"]["turn"] == 9
        assert marker["entity"]["prior_epoch_max_turn"] == 9

    def test_ingest_detects_turn_regression_and_starts_new_epoch(self, tmp_path):
        engine = _bind(tmp_path, "run-a")
        _typed_snapshot(engine, 10)
        decision = _route_authorized_decision(engine, turn=10)
        assert engine.get("decision", decision["id"])["decision_state"] == "authorized"

        engine.ingest_typed_snapshot(
            {
                "snapshot_id": "snapshot_8_rolled_back",
                "turn_before": 8,
                "turn_after": 8,
                "capabilities": {},
                "entities": [],
                "relations": [],
                "metrics": {},
            },
            turn=8,
        )

        reloads = [
            event
            for event in engine.history(last_n=1000)
            if event["event_type"] == "game.reloaded"
        ]
        assert len(reloads) == 1
        assert reloads[0]["entity"]["reason"] == "detected_turn_regression"
        assert reloads[0]["entity"]["prior_epoch_max_turn"] == 10
        assert reloads[0]["entity"]["details"] == {
            "prior_epoch_max_turn": 10,
            "observed_turn": 8,
        }
        assert reloads[0]["epoch"] == 2
        # The pending authorization from the abandoned future is voided.
        voided = engine.get("decision", decision["id"])
        assert voided["decision_state"] == "cancelled"
        # The rolled-back snapshot still projects into the new epoch.
        assert engine.get("world_entity", "player:0") is None
        gate = engine.governance_turn_gate(turn=8)
        assert gate["typed_snapshot_id"] == "snapshot_8_rolled_back"
        assert gate["ready"] is True

    def test_forward_turns_do_not_trigger_regression_reload(self, engine):
        _typed_snapshot(engine, 10)
        _typed_snapshot(engine, 11)

        assert [
            event
            for event in engine.history(last_n=1000)
            if event["event_type"] == "game.reloaded"
        ] == []

    def test_reload_archives_old_epoch_facts_until_refreshed(self, engine):
        """After a rollback the projection must not mix epochs: pre-rollback
        observations stop feeding current_metrics and the turn gate, and
        world entities reactivate only when the new epoch re-observes them."""

        def _snapshot_with_unit(turn: int) -> None:
            engine.ingest_typed_snapshot(
                {
                    "snapshot_id": f"snapshot_unit_{turn}",
                    "turn_before": turn,
                    "turn_after": turn,
                    "capabilities": {"ruleset": "RULESET_STANDARD"},
                    "entities": [
                        {
                            "entity_id": "unit:7",
                            "entity_type": "unit",
                            "attributes": {"health": 90},
                        }
                    ],
                    "relations": [],
                    "metrics": {"player.gold": 100 + turn},
                },
                turn=turn,
            )

        _snapshot_with_unit(30)
        assert engine.current_metrics()  # old-epoch metrics are live pre-reload
        assert engine.list("world_entity", status="active")

        engine.record_game_reload(reason="autosave_rollback", turn=28)

        # Old-epoch facts are archived, not deleted — history keeps them.
        assert engine.current_metrics() == {}
        assert engine.list("observation", status="active") == []
        assert engine.list("world_entity", status="active") == []
        archived_reasons = {
            entity["id"]: entity.get("archived_reason")
            for entity in engine.list("world_entity", status=None)
            if entity.get("status") == "archived"
        }
        assert archived_reasons
        assert all(
            reason == "epoch_superseded_by_reload:autosave_rollback"
            for reason in archived_reasons.values()
        )
        # The turn gate demands a fresh typed snapshot for the rolled-back
        # turn instead of being satisfied by the old epoch's snapshot.
        gate = engine.governance_turn_gate(turn=28)
        assert "current_turn_typed_snapshot_missing" in gate["blockers"]

        # The new epoch re-observes the world: entities reactivate.
        _snapshot_with_unit(28)
        assert engine.list("world_entity", status="active")
        assert engine.governance_turn_gate(turn=28)["ready"] is True


class TestLoggedUnexpectedException:
    def test_unexpected_exception_records_unknown_outcome_requiring_verification(
        self, tmp_path
    ):
        class _Emitter:
            async def emit(self, _event_type, _payload):
                return None

        class _Logger:
            _turn = 42
            _emitter = _Emitter()

            async def log_tool_call(self, *_args):
                return None

            async def log_error(self, *_args):
                return None

        engine = BeliefEngine(run_id="p0-server", directory=tmp_path)
        engine.bind_game(*GAME)
        params = {"unit_id": 7, "action": "attack", "target_x": 8, "target_y": 9}
        decision = engine.route_decision(
            statement="Attack the quantified target",
            probability=0.9,
            confidence=0.9,
            impact="low",
            urgency="low",
            irreversibility=0,
            action_intent={
                "tool": "unit_action",
                "params": params,
                "args_hash": action_args_hash(params),
            },
            turn=42,
        )
        ctx = SimpleNamespace(
            request_context=SimpleNamespace(
                lifespan_context=SimpleNamespace(
                    beliefs=engine,
                    logger=_Logger(),
                    game=SimpleNamespace(),
                )
            )
        )

        async def operation():
            raise RuntimeError("connection reader died mid-action")

        result = asyncio.run(
            _logged(ctx, "unit_action", params, operation)
        )

        # The exception may arrive after the game already accepted the
        # mutation, so the decision is neither succeeded nor safe-to-retry:
        # it stays unknown until the agent reads the game back.
        assert result == "Error: connection reader died mid-action"
        assert (
            engine.get("decision", decision["id"])["decision_state"]
            == "outcome_unknown"
        )
        actions = engine.list("action", status=None)
        assert actions[-1]["success"] is False
        assert actions[-1]["decision_id"] == decision["id"]
        assert actions[-1]["outcome_status"] == "unknown"
        outcomes = engine.list("outcome", status=None)
        assert outcomes[-1]["decision_id"] == decision["id"]
        assert outcomes[-1]["success"] is False
        assert outcomes[-1]["outcome_status"] == "unknown"
        # The unknown outcome still gates the turn and cannot be cancelled
        # away — that is the deadlock fix without pretending to know what
        # happened to the mutation.
        _typed_snapshot(engine, 42)
        gate = engine.governance_turn_gate(turn=42)
        assert gate["blockers"] == ["routed_actions_not_completed"]
        with pytest.raises(
            BeliefEngineError, match="verified before cancellation or retry"
        ):
            engine.cancel_action_authorization(
                decision["id"], reason="RuntimeError killed the execution.", turn=42
            )
        # A verification read-back settles the unknown outcome: the game
        # shows the attack never landed, so the decision becomes retryable
        # and can now be cancelled to unblock the turn.
        engine.record_tool_result(
            tool="get_units",
            params={},
            result="Verified: unit 7 never attacked.",
            turn=42,
            category="action",
            success=False,
            duration_ms=5,
            decision_id=decision["id"],
            execution_status="failed",
        )
        assert (
            engine.get("decision", decision["id"])["decision_state"] == "retryable"
        )
        engine.cancel_action_authorization(
            decision["id"], reason="Verification showed the attack failed.", turn=42
        )
        assert engine.governance_turn_gate(turn=42)["ready"] is True

    def test_logged_keeps_uncaught_exception_out_of_the_error_string(self, monkeypatch):
        """The generic handler must not swallow CancelledError propagation."""

        class _Logger:
            _turn = 1

            async def log_error(self, *_args):
                return None

        async def preflight(*_args, **_kwargs):
            return {"authorized": True, "decision_id": None, "route": "routine"}

        async def record(*_args, **_kwargs):
            return None

        monkeypatch.setattr(server_module, "_belief_action_preflight", preflight)
        monkeypatch.setattr(server_module, "_record_belief_tool_result", record)
        ctx = SimpleNamespace(
            request_context=SimpleNamespace(
                lifespan_context=SimpleNamespace(logger=_Logger())
            )
        )

        async def cancelled():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(_logged(ctx, "get_units", {}, cancelled))


def test_graph_delta_kinds_get_distinct_event_ids(tmp_path):
    """World and goal deltas recorded for the same snapshot must not share
    one event id — audit searches would conflate the two streams."""
    from civ6_belief_engine.graph import GraphDelta

    engine = _bind(tmp_path, "run-a")
    empty = GraphDelta(snapshot_id="snapshot_x", turn=3, epoch=1)
    engine.record_graph_delta(empty)
    engine.record_graph_delta(empty, kind="goals")

    ids = [
        event["entity_id"]
        for event in engine.history(last_n=10)
        if event["event_type"] == "graph.delta"
    ]
    assert len(ids) == 2
    assert len(set(ids)) == 2
    assert ids == [
        "graph_delta:world:1:snapshot_x",
        "graph_delta:goals:1:snapshot_x",
    ]
