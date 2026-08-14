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
