"""Typed GameState → governance snapshot contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from civ_mcp.belief_engine import BeliefEngine
from civ_mcp.server import _capture_governance_snapshot
from civ_mcp.governance.capabilities import (
    RULESET_EXPANSION_1,
    RULESET_EXPANSION_2,
    RULESET_STANDARD,
    UnsupportedRulesetError,
    capabilities_for_ruleset,
)
from civ_mcp.governance.snapshot import (
    SnapshotConsistencyError,
    build_turn_snapshot,
    snapshot_to_belief_observation,
    snapshot_world_state,
)
from civ_mcp.lua.models import (
    BarbarianCamp,
    BarbarianOverview,
    BarbarianUnit,
    CityInfo,
    CivInfo,
    GameOverview,
    GovernmentStatus,
    PolicyInfo,
    PolicySlot,
    ResourceStockpile,
    TechCivicStatus,
    UnitInfo,
)
from civ6_belief_engine.graph import GraphView


def _overview(
    *,
    ruleset: str = RULESET_STANDARD,
    turn: int = 42,
    num_cities: int = 2,
    num_units: int = 2,
) -> GameOverview:
    return GameOverview(
        turn=turn,
        player_id=0,
        civ_name="CIVILIZATION_INDIA",
        leader_name="Gandhi",
        gold=245.5,
        gold_per_turn=12.0,
        science_yield=30.0,
        culture_yield=18.0,
        faith=90.0,
        current_research="TECH_EDUCATION",
        current_civic="CIVIC_FEUDALISM",
        num_cities=num_cities,
        num_units=num_units,
        score=320,
        explored_land=36,
        total_land=100,
        total_population=12,
        difficulty="Emperor",
        game_speed="GAMESPEED_STANDARD",
        game_speed_name="Standard",
        enabled_victories={"VICTORY_SCIENCE", "VICTORY_CULTURE"},
        ruleset=ruleset,
    )


def _city(city_id: int, x: int, y: int) -> CityInfo:
    return CityInfo(
        city_id=city_id,
        name=f"City {city_id}",
        x=x,
        y=y,
        population=city_id + 3,
        food=10.0,
        production=8.0,
        gold=5.0,
        science=4.0,
        culture=3.0,
        faith=2.0,
        housing=7.0,
        amenities=1,
        turns_to_grow=5,
        currently_building="BUILDING_MONUMENT",
        districts=["DISTRICT_CITY_CENTER"],
        buildings=["PALACE"],
    )


def _unit(unit_id: int, x: int, y: int) -> UnitInfo:
    return UnitInfo(
        unit_id=unit_id,
        unit_index=unit_id,
        name=f"Unit {unit_id}",
        unit_type="UNIT_WARRIOR",
        x=x,
        y=y,
        moves_remaining=1.0,
        max_moves=2.0,
        health=90,
        max_health=100,
        combat_strength=20,
    )


def _tech_civic() -> TechCivicStatus:
    return TechCivicStatus(
        current_research="TECH_EDUCATION",
        current_research_turns=4,
        current_civic="CIVIC_FEUDALISM",
        current_civic_turns=3,
        available_techs=[],
        available_civics=[],
        completed_tech_count=12,
        completed_civic_count=9,
    )


def test_ruleset_capability_table_is_explicit_and_fails_closed():
    standard = capabilities_for_ruleset("Standard")
    assert standard.ruleset == RULESET_STANDARD
    assert standard.governors is False
    assert standard.ages is False
    assert standard.dedications is False
    assert standard.alliances is False
    assert standard.diplomatic_favor is False
    assert standard.world_congress is False
    assert standard.resource_stockpiles is False
    assert standard.basic_diplomacy is True
    assert standard.trade is True
    assert standard.city_states is True
    assert standard.religion is True
    assert standard.combat_estimate is True

    expansion_1 = capabilities_for_ruleset(RULESET_EXPANSION_1)
    assert expansion_1.governors is True
    assert expansion_1.ages is True
    assert expansion_1.dedications is True
    assert expansion_1.alliances is True
    assert expansion_1.diplomatic_favor is False
    assert expansion_1.world_congress is False
    assert expansion_1.resource_stockpiles is False

    expansion_2 = capabilities_for_ruleset("Expansion2")
    assert expansion_2.ruleset == RULESET_EXPANSION_2
    assert expansion_2.governors is True
    assert expansion_2.ages is True
    assert expansion_2.dedications is True
    assert expansion_2.alliances is True
    assert expansion_2.diplomatic_favor is True
    assert expansion_2.world_congress is True
    assert expansion_2.resource_stockpiles is True

    with pytest.raises(UnsupportedRulesetError):
        capabilities_for_ruleset("")
    with pytest.raises(UnsupportedRulesetError):
        capabilities_for_ruleset("RULESET_FUTURE_UNKNOWN")


def test_build_snapshot_is_same_turn_stable_and_order_independent():
    overview = _overview()
    cities = [_city(2, 6, 7), _city(1, 3, 4)]
    units = [_unit(22, 6, 8), _unit(11, 3, 5)]

    first = build_turn_snapshot(
        turn_before=42,
        turn_after=42,
        captured_at=1_723_500_000.25,
        overview=overview,
        cities=cities,
        units=units,
        tech_civic=_tech_civic(),
        extra={"collector": "typed-game-state", "retry": 0},
    )
    second = build_turn_snapshot(
        turn_before=42,
        turn_after=42,
        captured_at=1_723_500_005.75,
        overview=overview,
        cities=list(reversed(cities)),
        units=list(reversed(units)),
        tech_civic=_tech_civic(),
        extra={"retry": 0, "collector": "typed-game-state"},
    )

    assert first.snapshot_id == second.snapshot_id
    assert first.captured_at != second.captured_at
    assert first.snapshot_id.startswith("snapshot_")
    assert first.turn_before == first.turn == first.turn_after == 42
    assert [city.city_id for city in first.cities] == [1, 2]
    assert [unit.unit_id for unit in first.units] == [11, 22]
    standard_world = snapshot_world_state(first)
    assert "player.diplomatic_favor" not in standard_world["metrics"]
    assert "player.era_score" not in standard_world["metrics"]
    assert "city.1.loyalty" not in standard_world["metrics"]


def test_build_snapshot_rejects_cross_turn_and_inconsistent_typed_results():
    overview = _overview()
    cities = [_city(1, 3, 4), _city(2, 6, 7)]
    units = [_unit(11, 3, 5), _unit(22, 6, 8)]

    with pytest.raises(SnapshotConsistencyError, match="crossed turns"):
        build_turn_snapshot(
            turn_before=42,
            turn_after=43,
            captured_at=1_723_500_000.0,
            overview=overview,
            cities=cities,
            units=units,
        )

    with pytest.raises(SnapshotConsistencyError, match="reports 2 cities"):
        build_turn_snapshot(
            turn_before=42,
            turn_after=42,
            captured_at=1_723_500_000.0,
            overview=overview,
            cities=cities[:1],
            units=units,
        )

    mismatched_progress = _tech_civic()
    mismatched_progress.current_research = "TECH_MINING"
    with pytest.raises(SnapshotConsistencyError, match="Tech/civic state disagrees"):
        build_turn_snapshot(
            turn_before=42,
            turn_after=42,
            captured_at=1_723_500_000.0,
            overview=overview,
            cities=cities,
            units=units,
            tech_civic=mismatched_progress,
        )


def test_standard_rules_reject_expansion_only_typed_data():
    with pytest.raises(SnapshotConsistencyError, match="stockpiles are unavailable"):
        build_turn_snapshot(
            turn_before=42,
            turn_after=42,
            captured_at=1_723_500_000.0,
            overview=_overview(),
            cities=[_city(1, 3, 4), _city(2, 6, 7)],
            units=[_unit(11, 3, 5), _unit(22, 6, 8)],
            resources=[
                ResourceStockpile(
                    name="Iron", amount=20, cap=50, per_turn=2, demand=0, imported=0
                )
            ],
        )


def test_world_projection_and_belief_payload_are_structured_typed_facts(tmp_path):
    overview = _overview(ruleset=RULESET_EXPANSION_2)
    overview.diplomatic_favor = 45
    overview.favor_per_turn = 2
    overview.era_name = "ERA_MEDIEVAL"
    overview.era_score = 30
    overview.era_dark_threshold = 25
    overview.era_golden_threshold = 45
    rival = CivInfo(
        player_id=3,
        civ_name="CIVILIZATION_PERSIA",
        leader_name="Cyrus",
        has_met=True,
        is_at_war=False,
        diplomatic_state="UNFRIENDLY",
        relationship_score=-15,
        military_strength=240,
        num_cities=4,
        alliance_type=None,
    )
    snapshot = build_turn_snapshot(
        turn_before=42,
        turn_after=42,
        captured_at=1_723_500_000.0,
        overview=overview,
        cities=[_city(1, 3, 4), _city(2, 6, 7)],
        units=[_unit(11, 3, 5), _unit(22, 6, 8)],
        diplomacy=[rival],
        tech_civic=_tech_civic(),
        resources=[
            ResourceStockpile(
                name="Iron", amount=20, cap=50, per_turn=2, demand=1, imported=0
            )
        ],
        policies=GovernmentStatus(
            government_name="Classical Republic",
            government_type="GOVERNMENT_CLASSICAL_REPUBLIC",
            slots=[
                PolicySlot(0, "SLOT_ECONOMIC", "POLICY_URBAN_PLANNING", "Urban Planning"),
                PolicySlot(1, "SLOT_WILDCARD", None, None),
            ],
            available_policies=[
                PolicyInfo("POLICY_AGOGE", "Agoge", "Unit production", "SLOT_MILITARY")
            ],
        ),
        barbarians=BarbarianOverview(
            camps=[BarbarianCamp(8, 9, distance_to_city=4, distance_to_military=2)],
            units=[BarbarianUnit(63, "UNIT_WARRIOR", 8, 8, 100, 100, 20, 0, 3, 1)],
        ),
    )

    world = snapshot_world_state(snapshot)
    entity_ids = {entity["entity_id"] for entity in world["entities"]}
    assert {
        "player:0",
        "city:0:1",
        "unit:11",
        "player:3",
        "technology:TECH_EDUCATION",
        "resource_stockpile:0:iron",
        "government:0",
        "policy_slot:0:1",
        "barbarian_camp:8:9",
        "barbarian_unit:63",
    } <= entity_ids
    assert {
        relation["relation_type"] for relation in world["relations"]
    } >= {"owns", "located_at", "researching", "progressing", "diplomacy", "stockpiles"}
    assert world["metrics"]["player.gold"] == 245.5
    assert world["metrics"]["diplomacy.player_3.military_strength"] == 240
    assert world["metrics"]["resource.iron.amount"] == 20
    assert world["metrics"]["government.empty_policy_slots"] == 1
    assert world["metrics"]["barbarian.known_camps"] == 1
    assert world["metrics"]["barbarian.visible_units"] == 1

    observation = snapshot_to_belief_observation(snapshot)
    assert observation["source"] == "game_state:typed_snapshot"
    assert observation["observed_turn"] == 42
    assert observation["reliability"] == 1.0
    assert observation["facts"]["snapshot_id"] == snapshot.snapshot_id
    assert observation["facts"]["turn_before"] == 42
    assert observation["facts"]["turn_after"] == 42
    assert observation["metrics"]["city.1.population"] == 4
    assert "raw" not in observation

    engine = BeliefEngine(run_id="typed-snapshot-test", directory=tmp_path)
    engine.bind_game("CIVILIZATION_INDIA", 123)
    ingested = engine.ingest_typed_snapshot(world, turn=42)
    assert ingested["snapshot_id"] == snapshot.snapshot_id
    assert "city:0:1" in ingested["world_entities_changed"]
    city_node = engine.get("world_entity", "city:0:1")
    assert city_node is not None
    assert city_node["attributes"]["population"] == 4
    assert any(link["relation"] == "owns" for link in city_node["links"])


def test_server_capture_runs_old_projection_and_shadow_graph_together(tmp_path):
    snapshot = build_turn_snapshot(
        turn_before=42,
        turn_after=42,
        captured_at=1_723_500_000.0,
        overview=_overview(),
        cities=[_city(1, 3, 4), _city(2, 6, 7)],
        units=[_unit(11, 3, 5), _unit(22, 6, 8)],
    )

    class _Game:
        async def get_governance_snapshot(self):
            return snapshot

    engine = BeliefEngine(run_id="shadow-graph-test", directory=tmp_path)
    engine.bind_game("CIVILIZATION_INDIA", 123)
    lifespan = SimpleNamespace(game=_Game(), beliefs=engine)
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context=lifespan))

    _, world, projection, _, _ = asyncio.run(
        _capture_governance_snapshot(ctx, engine)
    )

    assert projection["graph_shadow"]["status"] == "matched"
    assert projection["graph_shadow"]["source_nodes"] == len(world["entities"])
    assert projection["graph_shadow"]["source_edges"] == len(world["relations"])
    assert engine.graph_view.snapshot_id == snapshot.snapshot_id
    assert "city:3:4" in engine.graph_view.nodes
    assert "city:0:1" not in engine.graph_view.nodes
    assert engine.get("world_entity", "city:0:1") is not None

    reloaded = BeliefEngine(run_id="shadow-graph-reload", directory=tmp_path)
    reloaded.bind_game("CIVILIZATION_INDIA", 123)
    assert reloaded.graph_view.state_hash == engine.graph_view.state_hash
    assert reloaded.graph_replay_error is None

    engine.record_game_reload(reason="test_reload", turn=40)
    assert engine.graph_view.epoch == 2
    assert not engine.graph_view.nodes
    asyncio.run(_capture_governance_snapshot(ctx, engine))
    assert engine.graph_view.epoch == 2
    reloaded_after_epoch = BeliefEngine(
        run_id="shadow-graph-epoch-reload",
        directory=tmp_path,
    )
    reloaded_after_epoch.bind_game("CIVILIZATION_INDIA", 123)
    assert reloaded_after_epoch.graph_view.state_hash == engine.graph_view.state_hash

    engine.bind_game("CIVILIZATION_INDIA", 999)
    assert engine.graph_view == GraphView.empty()


def test_shadow_projection_failure_does_not_break_legacy_snapshot(tmp_path, monkeypatch):
    malformed_world = {
        "snapshot_id": "snapshot:legacy-only",
        "turn": 1,
        "turn_before": 1,
        "turn_after": 1,
        "entities": [
            {
                "entity_type": "city",
                "entity_id": "city:0:7",
                "attributes": {"city_id": 7, "name": "No coordinates"},
            }
        ],
        "relations": [],
        "metrics": {},
    }
    monkeypatch.setattr(
        "civ6_belief_engine.governance.snapshot.snapshot_world_state",
        lambda _snapshot: malformed_world,
    )

    class _Game:
        async def get_governance_snapshot(self):
            return SimpleNamespace(snapshot_id="snapshot:legacy-only", turn=1)

    engine = BeliefEngine(run_id="shadow-fail-open", directory=tmp_path)
    engine.bind_game("CIVILIZATION_INDIA", 456)
    lifespan = SimpleNamespace(game=_Game(), beliefs=engine)
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context=lifespan))

    _, _, projection, _, _ = asyncio.run(_capture_governance_snapshot(ctx, engine))

    assert projection["graph_shadow"]["status"] == "error"
    assert "requires integer x/y" in projection["graph_shadow"]["error"]
    assert engine.get("world_entity", "city:0:7")["status"] == "active"
    assert not engine.graph_view.nodes
