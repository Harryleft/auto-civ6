"""Phase-one shadow graph contracts."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from civ6_belief_engine.graph import (
    Coverage,
    GraphDelta,
    GraphInvariantError,
    GraphProjectionError,
    GraphView,
    compare_shadow_projection,
    project_active_goals,
    project_world_state,
    replay_deltas,
)


def _world(
    snapshot_id: str,
    turn: int,
    entities: list[dict],
    relations: list[dict] | None = None,
) -> dict:
    return {
        "snapshot_id": snapshot_id,
        "turn": turn,
        "turn_before": turn,
        "turn_after": turn,
        "entities": entities,
        "relations": relations or [],
    }


def _player(player_id: int) -> dict:
    return {
        "entity_type": "player",
        "entity_id": f"player:{player_id}",
        "attributes": {"player_id": player_id},
    }


def _city(owner_id: int, city_id: int, x: int, y: int) -> dict:
    return {
        "entity_type": "city",
        "entity_id": f"city:{owner_id}:{city_id}",
        "attributes": {"city_id": city_id, "name": "Capital", "x": x, "y": y},
    }


def _owns(owner_id: int, city_id: int) -> dict:
    return {
        "relation_type": "owns",
        "source_id": f"player:{owner_id}",
        "target_id": f"city:{owner_id}:{city_id}",
        "attributes": {},
    }


def test_projection_is_order_independent_and_keeps_one_canonical_edge():
    entities = [_player(0), _city(0, 7, 3, 4)]
    relations = [_owns(0, 7)]
    first = project_world_state(_world("snapshot:1", 1, entities, relations))
    second = project_world_state(
        _world("snapshot:1", 1, list(reversed(entities)), list(reversed(relations)))
    )

    assert first.digest == second.digest
    view = GraphView.empty().apply(first)
    assert len(view.edges) == 1
    assert view.edges_from("player:0", "owns") == view.edges_to("city:3:4", "owns")
    assert view.edges_from("player:0")[0].key == ("OWNS", "player:0", "city:3:4")


def test_city_identity_survives_owner_change_without_retaining_old_ownership():
    first = project_world_state(
        _world("snapshot:1", 1, [_player(0), _city(0, 7, 3, 4)], [_owns(0, 7)])
    )
    view = GraphView.empty().apply(first)
    captured_city = _city(1, 99, 3, 4)
    captured_city["attributes"]["name"] = "Captured Capital"
    second = project_world_state(
        _world("snapshot:2", 2, [_player(1), captured_city], [_owns(1, 99)]),
        previous=view,
    )
    view = view.apply(second)

    assert "city:3:4" in view.nodes
    assert not any(key[1:] == ("player:0", "city:3:4") for key in view.edges)
    assert ("OWNS", "player:1", "city:3:4") in view.edges
    assert view.node("city:3:4").first_observed_turn == 1
    assert view.node("player:0").observed is False


def test_visible_only_absence_becomes_unknown_while_complete_unit_is_removed():
    entities = [
        _player(0),
        {
            "entity_type": "unit",
            "entity_id": "unit:7",
            "attributes": {"unit_id": 7, "x": 1, "y": 1},
        },
        {
            "entity_type": "barbarian_unit",
            "entity_id": "barbarian_unit:63",
            "attributes": {"unit_id": 63, "x": 8, "y": 8},
        },
        {
            "entity_type": "tile",
            "entity_id": "tile:8:8",
            "attributes": {"x": 8, "y": 8},
        },
    ]
    relations = [
        {
            "relation_type": "located_at",
            "source_id": "barbarian_unit:63",
            "target_id": "tile:8:8",
        }
    ]
    view = GraphView.empty().apply(
        project_world_state(_world("snapshot:1", 1, entities, relations))
    )
    delta = project_world_state(
        _world("snapshot:2", 2, [_player(0)]),
        previous=view,
    )
    view = view.apply(delta)

    assert "unit:7" not in view.nodes
    assert view.node("unit:barbarian:63").coverage is Coverage.CURRENTLY_VISIBLE
    assert view.node("unit:barbarian:63").observed is False
    assert view.node("tile:8:8").observed is False
    assert view.edges_from("unit:barbarian:63", "LOCATED_AT")[0].observed is False


def test_reobserved_visible_unit_replaces_stale_location_edge():
    first_entities = [
        _player(0),
        {
            "entity_type": "barbarian_unit",
            "entity_id": "barbarian_unit:63",
            "attributes": {"unit_id": 63, "x": 8, "y": 8},
        },
        {"entity_type": "tile", "entity_id": "tile:8:8", "attributes": {"x": 8, "y": 8}},
    ]
    first_relations = [
        {
            "relation_type": "located_at",
            "source_id": "barbarian_unit:63",
            "target_id": "tile:8:8",
        }
    ]
    view = GraphView.empty().apply(
        project_world_state(_world("snapshot:1", 1, first_entities, first_relations))
    )
    second_entities = [
        _player(0),
        {
            "entity_type": "barbarian_unit",
            "entity_id": "barbarian_unit:63",
            "attributes": {"unit_id": 63, "x": 9, "y": 8},
        },
        {"entity_type": "tile", "entity_id": "tile:9:8", "attributes": {"x": 9, "y": 8}},
    ]
    second_relations = [
        {
            "relation_type": "located_at",
            "source_id": "barbarian_unit:63",
            "target_id": "tile:9:8",
        }
    ]
    view = view.apply(
        project_world_state(
            _world("snapshot:2", 2, second_entities, second_relations),
            previous=view,
        )
    )

    assert [edge.target_id for edge in view.edges_from("unit:barbarian:63", "LOCATED_AT")] == [
        "tile:9:8"
    ]
    assert view.node("tile:8:8").observed is False


def test_threats_near_city_returns_current_edges_and_hides_stale_units():
    entities = [
        _player(0),
        _city(0, 7, 3, 4),
        {
            "entity_type": "civilization",
            "entity_id": "player:3",
            "attributes": {"player_id": 3},
        },
        {
            "entity_type": "foreign_unit",
            "entity_id": "foreign_unit:3:9",
            "attributes": {"unit_id": 9, "x": 5, "y": 4},
        },
        {
            "entity_type": "tile",
            "entity_id": "tile:5:4",
            "attributes": {"x": 5, "y": 4},
        },
    ]
    relations = [
        _owns(0, 7),
        {
            "relation_type": "owns",
            "source_id": "player:3",
            "target_id": "foreign_unit:3:9",
        },
        {
            "relation_type": "located_at",
            "source_id": "foreign_unit:3:9",
            "target_id": "tile:5:4",
        },
        {
            "relation_type": "threatens",
            "source_id": "foreign_unit:3:9",
            "target_id": "city:0:7",
            "attributes": {"distance": 2, "visibility": "visible"},
        },
    ]
    view = GraphView.empty().apply(
        project_world_state(_world("snapshot:1", 1, entities, relations))
    )

    assert [edge.source_id for edge in view.threats_near_city("city:3:4")] == [
        "unit:3:9"
    ]
    assert view.threats_near_city("city:3:4", max_distance=1) == ()

    next_view = view.apply(
        project_world_state(
            _world("snapshot:2", 2, [_player(0), _city(0, 7, 3, 4)], [_owns(0, 7)]),
            previous=view,
        )
    )
    assert next_view.threats_near_city("city:3:4") == ()
    assert len(next_view.threats_near_city("city:3:4", include_stale=True)) == 1


def test_projection_rejects_dangling_relations():
    with pytest.raises(GraphProjectionError, match="dangling source relation"):
        project_world_state(
            _world(
                "snapshot:bad",
                1,
                [_player(0)],
                [
                    {
                        "relation_type": "owns",
                        "source_id": "player:0",
                        "target_id": "city:0:404",
                    }
                ],
            )
        )


def test_projection_requires_city_coordinates_and_delta_requires_real_edge_keys():
    with pytest.raises(GraphProjectionError, match="requires integer x/y"):
        project_world_state(
            _world(
                "snapshot:bad-city",
                1,
                [
                    _player(0),
                    {
                        "entity_type": "city",
                        "entity_id": "city:0:7",
                        "attributes": {"city_id": 7},
                    },
                ],
            )
        )
    with pytest.raises(ValueError, match="three non-empty strings"):
        GraphDelta.from_dict(
            {
                "snapshot_id": "snapshot:bad-edge-key",
                "turn": 1,
                "epoch": 1,
                "remove_edge_keys": ["OWNS"],
            }
        )
    with pytest.raises(GraphProjectionError, match="non-negative integer priority"):
        project_active_goals(
            ({"goal_id": "bad", "statement": "invalid", "priority": -1},),
            previous=GraphView.empty(),
            snapshot_id="snapshot:bad-goal",
            turn=1,
            epoch=1,
        )


def test_shadow_comparison_detects_legacy_attribute_drift():
    world = _world("snapshot:1", 1, [_player(0)])
    view = GraphView.empty().apply(project_world_state(world))
    legacy = (
        {
            "id": "player:0",
            "node_type": "player",
            "attributes": {"player_id": 99},
            "links": [],
        },
    )

    assert compare_shadow_projection(world, legacy, view) == (
        "legacy_attributes:player:0",
    )


def test_epoch_transition_resets_current_branch_and_replay_is_deterministic():
    first = project_world_state(_world("snapshot:10", 10, [_player(0)]), epoch=1)
    first_view = GraphView.empty().apply(first)
    second = project_world_state(
        _world("snapshot:8:reload", 8, [_player(1)]),
        previous=first_view,
        epoch=2,
    )

    reloaded = first_view.apply(second)
    replayed = replay_deltas(
        [
            GraphDelta.from_dict(json.loads(json.dumps(first.to_dict()))),
            GraphDelta.from_dict(second.to_dict()),
        ]
    )
    assert reloaded.state_hash == replayed.state_hash
    assert set(reloaded.nodes) == {"player:1"}
    assert reloaded.epoch == 2
    with pytest.raises(GraphInvariantError, match="cannot apply epoch"):
        reloaded.apply(first)


def test_active_goals_survive_world_refresh_and_replay_deterministically():
    world = _world("snapshot:12", 12, [_player(0)])
    world_delta = project_world_state(world)
    world_view = GraphView.empty().apply(world_delta)
    goal_delta = project_active_goals(
        (
            {
                "goal_id": "science",
                "statement": "完成当前科技",
                "priority": 40,
            },
            {
                "goal_id": "survive",
                "statement": "守住首都",
                "priority": 100,
            },
        ),
        previous=world_view,
        snapshot_id="snapshot:12",
        turn=12,
        epoch=1,
    )
    goal_view = world_view.apply(goal_delta)

    assert [node.attributes["goal_id"] for node in goal_view.active_goals()] == [
        "survive",
        "science",
    ]

    refreshed_world_delta = project_world_state(world, previous=goal_view)
    refreshed = goal_view.apply(refreshed_world_delta)
    assert [node.attributes["goal_id"] for node in refreshed.active_goals()] == [
        "survive",
        "science",
    ]
    assert compare_shadow_projection(
        world,
        (
            {
                "id": "player:0",
                "node_type": "player",
                "attributes": {"player_id": 0},
                "links": [],
            },
        ),
        refreshed,
    ) == ()

    replayed = replay_deltas((world_delta, goal_delta, refreshed_world_delta))
    assert replayed.state_hash == refreshed.state_hash


def test_projecting_complete_active_goal_set_removes_archived_goals():
    base = GraphView.empty(turn=12)
    with_goal = base.apply(
        project_active_goals(
            ({"goal_id": "survive", "statement": "守住首都", "priority": 100},),
            previous=base,
            snapshot_id="snapshot:12",
            turn=12,
            epoch=1,
        )
    )
    without_goal = with_goal.apply(
        project_active_goals(
            (),
            previous=with_goal,
            snapshot_id="snapshot:12",
            turn=12,
            epoch=1,
        )
    )

    assert without_goal.active_goals() == ()
    assert "goal:survive" not in without_goal.nodes


def test_goal_update_preserves_identity_and_unchanged_projection_is_empty():
    base = GraphView.empty(turn=12)
    first = base.apply(
        project_active_goals(
            ({"goal_id": "goal:survive", "statement": "守住首都", "priority": 80},),
            previous=base,
            snapshot_id="snapshot:12",
            turn=12,
            epoch=1,
        )
    )
    update = project_active_goals(
        ({"goal_id": "goal:survive", "statement": "守住首都", "priority": 100},),
        previous=first,
        snapshot_id="snapshot:13",
        turn=13,
        epoch=1,
    )
    updated = first.apply(update)
    unchanged = project_active_goals(
        ({"goal_id": "goal:survive", "statement": "守住首都", "priority": 100},),
        previous=updated,
        snapshot_id="snapshot:13",
        turn=13,
        epoch=1,
    )

    assert set(updated.nodes) == {"goal:survive"}
    assert updated.node("goal:survive").first_observed_turn == 12
    assert updated.node("goal:survive").last_observed_turn == 13
    assert unchanged.upsert_nodes == ()
    assert unchanged.remove_node_ids == ()


def test_graph_values_and_indexes_are_immutable():
    view = GraphView.empty().apply(
        project_world_state(_world("snapshot:1", 1, [_player(0)]))
    )
    with pytest.raises(TypeError):
        view.nodes["player:1"] = view.node("player:0")  # type: ignore[index]
    with pytest.raises(TypeError):
        view.node("player:0").attributes["player_id"] = 2  # type: ignore[index]


def test_graph_package_does_not_import_the_mcp_adapter():
    graph_dir = Path(__file__).parents[1] / "src" / "civ6_belief_engine" / "graph"
    imported: set[str] = set()
    for path in graph_dir.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert not any(name == "civ_mcp" or name.startswith("civ_mcp.") for name in imported)


def test_unchanged_world_projection_writes_no_full_upserts():
    """Content dedup guard: an unchanged turn must not re-persist the whole
    entity set (measured: a 16-entity turn-2 snapshot cost 14.6KB per delta,
    doubling journal growth next to the legacy world-entity events)."""
    entities = [_player(0), _city(0, 1, 10, 24)]
    relations = [_owns(0, 1)]
    first = project_world_state(_world("s1", 5, entities, relations))
    view = GraphView.empty().apply(first)

    second = project_world_state(_world("s2", 6, entities, relations), previous=view)

    assert second.upsert_nodes == ()
    assert second.upsert_edges == ()
    assert second.remove_node_ids == ()
    assert second.remove_edge_keys == ()

    replayed = replay_deltas([first, second])
    assert replayed.turn == 6 and replayed.snapshot_id == "s2"
    assert {node.node_id for node in replayed.nodes.values()} == {
        "player:0",
        "city:10:24",
    }
    # Shadow comparison stays clean on the graph side against the deduplicated
    # view: the observed-set check must not depend on per-turn
    # last_observed_turn (legacy rows are absent here, so only graph_* issues
    # are meaningful).
    issues = compare_shadow_projection(_world("s2", 6, entities, relations), (), replayed)
    assert not [issue for issue in issues if issue.startswith("graph_")]


def test_unobserved_entity_returns_through_dedup_with_observed_restore():
    """Fog round-trip under dedup: absent → observed=False; re-observed with
    unchanged attributes → the delta must still carry the observed=True
    restore (an observed-flag change always writes)."""
    unit = {
        "entity_type": "foreign_unit",
        "entity_id": "foreign_unit:9",
        "attributes": {"unit_id": 9, "x": 4, "y": 5},
    }
    seen = project_world_state(_world("s1", 5, [unit]))
    view = GraphView.empty().apply(seen)

    fogged = project_world_state(_world("s2", 6, []), previous=view)
    view = view.apply(fogged)
    assert view.node("unit:9").observed is False

    restored = project_world_state(_world("s3", 7, [unit]), previous=view)
    assert "unit:9" in {node.node_id for node in restored.upsert_nodes}
    view = view.apply(restored)
    assert view.node("unit:9").observed is True
