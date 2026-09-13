"""World-delta context separates observations, fog, and game reload branches."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from civ6_belief_engine.graph import GraphDelta, GraphView, Node, project_world_state
from civ6_belief_engine.graph.context import summarize_world_changes
from civ6_belief_engine.graph.project import GOVERNANCE_SOURCE


def _world(turn, entities, relations=()):
    return {
        "snapshot_id": f"snapshot:{turn}",
        "turn": turn,
        "entities": entities,
        "relations": relations,
    }


def _entity(kind, entity_id, **attributes):
    return {"entity_type": kind, "entity_id": entity_id, "attributes": attributes}


def _player(**attributes):
    return _entity("player", "player:0", player_id=0, **attributes)


def _view(entities, relations=()):
    return GraphView.empty().apply(project_world_state(_world(1, entities, relations)))


def test_first_snapshot_and_reload_create_baselines_not_mass_changes():
    empty = GraphView.empty()
    delta = project_world_state(_world(1, [_player(gold=10)]))
    initial = summarize_world_changes(empty, delta)
    assert initial["mode"] == "baseline"
    assert initial["baseline_reason"] == "first_world_snapshot"
    assert initial["baseline_counts"] == {"nodes": 1, "edges": 0}
    assert initial["counts"]["node_changes"] == 0
    prior = empty.apply(delta)
    reload_delta = project_world_state(
        _world(0, [_player(gold=0)]), previous=prior, epoch=2
    )
    reloaded = summarize_world_changes(prior, reload_delta)
    assert reloaded["mode"] == "baseline"
    assert reloaded["baseline_reason"] == "epoch_changed"
    assert reloaded["turn"] == 0 and reloaded["epoch"] == 2
    assert not reloaded["node_changes"] and not reloaded["affected_domains"]


def test_no_content_change_uses_batch_metadata_without_stamping_retained_nodes():
    player = _player(gold=10)
    prior = _view([player])
    delta = project_world_state(_world(10, [player]), previous=prior)
    assert not delta.upsert_nodes
    assert prior.node("player:0").last_observed_turn == 1
    summary = summarize_world_changes(prior, delta)
    assert summary["snapshot_id"] == "snapshot:10"
    assert summary["turn"] == 10
    assert not summary["node_changes"] and not summary["affected_domains"]
    # Even a redundant upsert with only metadata changes is not a game change.
    metadata_only = GraphDelta(
        "metadata",
        10,
        1,
        upsert_nodes=(replace(prior.node("player:0"), last_observed_turn=10),),
    )
    assert summarize_world_changes(prior, metadata_only)["counts"]["node_changes"] == 0
    prior = _view([_player(gold=10, turn=1)])
    overview_clock = project_world_state(
        _world(2, [_player(gold=10, turn=2)]), previous=prior
    )
    assert summarize_world_changes(prior, overview_clock)["counts"]["node_changes"] == 0


def test_changed_fields_are_compact_and_map_resource_check_is_not_a_fact():
    prior = _view(
        [
            _player(
                gold=10,
                tech_civic={
                    "completed_tech_count": 1,
                    "completed_techs": ["Mining"],
                    "available_techs": [{"name": "A"}],
                },
            )
        ]
    )
    delta = project_world_state(
        _world(
            2,
            [
                _player(
                    gold=20,
                    tech_civic={
                        "completed_tech_count": 2,
                        "completed_techs": ["Mining", "Bronze Working"],
                        "available_techs": [{"name": "large-value" * 1000}],
                    },
                )
            ],
        ),
        previous=prior,
    )
    summary = summarize_world_changes(prior, delta)
    change = summary["node_changes"][0]
    assert set(change["changed_fields"]) == {
        "gold",
        "tech_civic.completed_tech_count",
        "tech_civic.completed_techs",
        "tech_civic.available_techs",
    }
    assert {"science", "production", "economy"} <= set(summary["affected_domains"])
    assert "map" not in summary["affected_domains"]
    assert summary["checks_required"][0]["domain"] == "map"
    assert summary["checks_required"][0]["status"] == "needs_observation"
    assert "large-value" not in json.dumps(summary)


def test_research_countdown_does_not_claim_new_resource_visibility():
    prior = _view([_player(tech_civic={"current_research_turns": 3})])
    delta = project_world_state(
        _world(2, [_player(tech_civic={"current_research_turns": 2})]), previous=prior
    )
    summary = summarize_world_changes(prior, delta)
    assert set(summary["affected_domains"]) == {"science", "production"}
    assert not summary["checks_required"]


def test_lost_sight_is_distinct_from_complete_snapshot_removal():
    prior = _view(
        [
            _player(),
            _entity("unit", "unit:1", x=1, y=1),
            _entity("barbarian_unit", "barbarian_unit:9", x=5, y=5),
            _entity("tile", "tile:5:5", x=5, y=5),
        ],
        [
            {
                "relation_type": "located_at",
                "source_id": "barbarian_unit:9",
                "target_id": "tile:5:5",
            }
        ],
    )
    delta = project_world_state(_world(2, [_player()]), previous=prior)
    summary = summarize_world_changes(prior, delta)
    changes = {item["node_id"]: item for item in summary["node_changes"]}
    assert changes["unit:1"]["change"] == "removed"
    assert changes["unit:barbarian:9"]["change"] == "no_longer_visible"
    assert summary["edge_changes"][0]["change"] == "no_longer_visible"
    assert {"military", "map"} <= set(summary["affected_domains"])
    assert prior.node("unit:barbarian:9").observed  # no mutation


def test_reappearing_unit_is_reobserved_and_missing_fields_are_unavailable():
    entities = [_player(), _entity("barbarian_unit", "barbarian_unit:9", x=5, y=5)]
    prior = _view(entities)
    fog = project_world_state(_world(2, [_player()]), previous=prior)
    prior = prior.apply(fog)
    visible = project_world_state(_world(3, entities), previous=prior)
    summary = summarize_world_changes(prior, visible)
    unit = next(item for item in summary["node_changes"] if item["node_type"] == "unit")
    assert unit["change"] == "reobserved"
    prior = _view([_player(tech_civic={"completed_tech_count": 5})])
    unavailable = project_world_state(
        _world(2, [_player(tech_civic_available=False)]), previous=prior
    )
    summary = summarize_world_changes(prior, unavailable)
    assert summary["node_changes"][0]["unavailable_fields"] == ["tech_civic"]
    assert summary["counts"]["removed"] == 0
    assert not summary["checks_required"]


def test_governance_only_changes_do_not_pollute_world_context():
    prior = _view([_player(gold=10)])
    governance = Node(
        "proposal:1", "proposal", {"statement": "prior"}, source=GOVERNANCE_SOURCE
    )
    prior = prior.apply(GraphDelta("governance:1", 1, 1, upsert_nodes=(governance,)))
    delta = GraphDelta(
        "governance:2",
        2,
        1,
        upsert_nodes=(replace(governance, attributes={"statement": "new"}),),
    )
    summary = summarize_world_changes(prior, delta)
    assert summary["mode"] == "changes"
    assert not summary["node_changes"] and not summary["affected_domains"]
    removed = GraphDelta("governance:3", 3, 1, remove_node_ids=("proposal:1",))
    assert summarize_world_changes(prior, removed)["counts"]["node_changes"] == 0


def test_diplomacy_edge_change_affects_military_without_node_attribute_change():
    entities = [_player(), _entity("civilization", "player:1", player_id=1)]
    relation = {
        "relation_type": "diplomacy",
        "source_id": "player:0",
        "target_id": "player:1",
        "attributes": {"is_at_war": False},
    }
    prior = _view(entities, [relation])
    delta = project_world_state(
        _world(2, entities, [{**relation, "attributes": {"is_at_war": True}}]),
        previous=prior,
    )
    summary = summarize_world_changes(prior, delta)
    assert not summary["node_changes"]
    assert summary["edge_changes"][0]["changed_fields"] == ["is_at_war"]
    assert set(summary["affected_domains"]) == {"diplomacy", "military"}


def test_output_limit_never_truncates_counts_or_affected_domains():
    prior = _view([_player()])
    entities = [_player()] + [_entity("unit", f"unit:{i}", x=i, y=i) for i in range(20)]
    entities += [_entity("resource_stockpile", "resource:iron", amount=5)]
    delta = project_world_state(_world(2, entities), previous=prior)
    summary = summarize_world_changes(prior, delta, limit=2)
    assert len(summary["node_changes"]) + len(summary["edge_changes"]) == 2
    assert summary["counts"]["node_changes"] == 21
    assert summary["omitted_change_count"] == 19
    assert {"economy", "production", "military"} <= set(summary["affected_domains"])
    with pytest.raises(ValueError):
        summarize_world_changes(prior, delta, limit=-1)
