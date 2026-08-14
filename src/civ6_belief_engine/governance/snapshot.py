"""Pure typed-state adapter from GameState results into governance facts.

This module deliberately consumes the dataclasses returned by ``GameState``.
It never parses narrated MCP text, and it performs no network or clock I/O.
Callers bracket their typed queries with turn reads and pass both values here;
mixed-turn inputs are rejected before they can enter the belief layer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from math import isfinite
from typing import Any, Iterable, Mapping, Protocol, Sequence, TypedDict

from .capabilities import capabilities_for_ruleset
from .models import TurnSnapshot as GovernanceTurnSnapshot
from civ_mcp.lua.models import (
    BarbarianOverview,
    CityInfo,
    CivInfo,
    GameNotification,
    GameOverview,
    GovernmentStatus,
    ResourceStockpile,
    TechCivicStatus,
    UnitInfo,
    VictoryProgress,
)


class SnapshotConsistencyError(ValueError):
    """Raised when typed results cannot belong to one coherent turn."""


class TypedSnapshotSource(Protocol):
    """Structural contract for a future GameState snapshot coordinator.

    The protocol documents the typed boundary without coupling this pure module
    to GameState's connection or scheduling implementation.
    """

    async def get_game_overview(self) -> GameOverview: ...

    async def get_cities(self) -> tuple[list[CityInfo], list[str]]: ...

    async def get_units(self) -> list[UnitInfo]: ...

    async def get_diplomacy(self) -> list[CivInfo]: ...

    async def get_tech_civics(self) -> TechCivicStatus: ...

    async def get_policies(self) -> GovernmentStatus: ...

    async def get_barbarian_overview(self) -> BarbarianOverview: ...


class BeliefObservation(TypedDict):
    """Observation payload accepted by ``BeliefEngine.create``/ingest."""

    statement: str
    source: str
    facts: dict[str, Any]
    metrics: dict[str, int | float]
    reliability: float
    observed_turn: int
    tags: list[str]


def _canonical(value: Any) -> Any:
    """Convert typed results into stable, JSON-safe values without narration."""

    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(item) for item in value), key=_json_sort_key)
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, float) and not isfinite(value):
        raise SnapshotConsistencyError("Snapshot values must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise SnapshotConsistencyError(
        f"Snapshot contains unsupported value type: {type(value).__name__}"
    )


def _json_sort_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _stable_snapshot_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _canonical(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"snapshot_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _require_unique(values: Iterable[int], label: str) -> None:
    seen: set[int] = set()
    duplicates: set[int] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        joined = ", ".join(str(value) for value in sorted(duplicates))
        raise SnapshotConsistencyError(f"Duplicate {label} IDs: {joined}")


def build_turn_snapshot(
    *,
    turn_before: int,
    turn_after: int,
    captured_at: float,
    overview: GameOverview,
    cities: Sequence[CityInfo],
    units: Sequence[UnitInfo],
    diplomacy: Sequence[CivInfo] = (),
    tech_civic: TechCivicStatus | None = None,
    resources: Sequence[ResourceStockpile] = (),
    victory: VictoryProgress | None = None,
    notifications: Sequence[GameNotification] = (),
    policies: GovernmentStatus | None = None,
    barbarians: BarbarianOverview | None = None,
    extra: Mapping[str, Any] | None = None,
) -> GovernanceTurnSnapshot:
    """Build one immutable, same-turn governance snapshot from typed results."""

    if isinstance(turn_before, bool) or not isinstance(turn_before, int):
        raise SnapshotConsistencyError("turn_before must be an integer")
    if isinstance(turn_after, bool) or not isinstance(turn_after, int):
        raise SnapshotConsistencyError("turn_after must be an integer")
    if turn_before != turn_after or overview.turn != turn_before:
        raise SnapshotConsistencyError(
            "Typed snapshot crossed turns: "
            f"before={turn_before}, overview={overview.turn}, after={turn_after}"
        )
    if isinstance(captured_at, bool) or not isinstance(captured_at, (int, float)):
        raise SnapshotConsistencyError("captured_at must be a finite epoch number")
    if not isfinite(captured_at) or captured_at < 0:
        raise SnapshotConsistencyError("captured_at must be a finite epoch number")

    capabilities = capabilities_for_ruleset(overview.ruleset)
    city_rows = tuple(sorted(cities, key=lambda city: city.city_id))
    unit_rows = tuple(sorted(units, key=lambda unit: unit.unit_id))
    diplomacy_rows = tuple(sorted(diplomacy, key=lambda civ: civ.player_id))
    resource_rows = tuple(sorted(resources, key=lambda resource: resource.name))
    notification_rows = tuple(
        sorted(
            notifications,
            key=lambda item: (item.turn, item.type_name, item.x, item.y, item.message),
        )
    )

    _require_unique((city.city_id for city in city_rows), "city")
    _require_unique((unit.unit_id for unit in unit_rows), "unit")
    _require_unique((civ.player_id for civ in diplomacy_rows), "diplomacy player")
    if len(city_rows) != overview.num_cities:
        raise SnapshotConsistencyError(
            f"Overview reports {overview.num_cities} cities but received {len(city_rows)}"
        )
    if len(unit_rows) != overview.num_units:
        raise SnapshotConsistencyError(
            f"Overview reports {overview.num_units} units but received {len(unit_rows)}"
        )
    if any(civ.player_id == overview.player_id for civ in diplomacy_rows):
        raise SnapshotConsistencyError("Diplomacy results must not repeat the local player")
    if tech_civic is not None and (
        tech_civic.current_research != overview.current_research
        or tech_civic.current_civic != overview.current_civic
    ):
        raise SnapshotConsistencyError(
            "Tech/civic state disagrees with the same-turn overview"
        )
    if resource_rows and not capabilities.resource_stockpiles:
        raise SnapshotConsistencyError(
            f"Resource stockpiles are unavailable in {capabilities.ruleset}"
        )
    if not capabilities.alliances and any(
        civ.alliance_type or civ.alliance_level for civ in diplomacy_rows
    ):
        raise SnapshotConsistencyError(
            f"Alliance data is unavailable in {capabilities.ruleset}"
        )
    if not capabilities.diplomatic_favor and (
        overview.diplomatic_favor or overview.favor_per_turn
    ):
        raise SnapshotConsistencyError(
            f"Diplomatic favor is unavailable in {capabilities.ruleset}"
        )

    frozen_extra = _canonical(extra or {})
    # Capture time is provenance, not world state.  Excluding it makes a retry
    # of the same typed turn idempotent instead of revising every world entity
    # merely because the clock advanced.
    identity_payload = {
        "turn_before": turn_before,
        "turn_after": turn_after,
        "capabilities": capabilities,
        "overview": overview,
        "cities": city_rows,
        "units": unit_rows,
        "diplomacy": diplomacy_rows,
        "tech_civic": tech_civic,
        "resources": resource_rows,
        "victory": victory,
        "notifications": notification_rows,
        "policies": policies,
        "barbarians": barbarians,
        "extra": frozen_extra,
    }
    snapshot_id = _stable_snapshot_id(identity_payload)
    return GovernanceTurnSnapshot(
        snapshot_id=snapshot_id,
        turn_before=turn_before,
        turn_after=turn_after,
        turn=turn_before,
        player_id=overview.player_id,
        captured_at=float(captured_at),
        capabilities=capabilities,
        overview=overview,
        cities=city_rows,
        units=unit_rows,
        diplomacy=diplomacy_rows,
        tech_civic=tech_civic,
        resources=resource_rows,
        victory=victory,
        notifications=notification_rows,
        policies=policies,
        barbarians=barbarians,
        extra=frozen_extra,
    )


def _add_entity(
    entities: dict[str, dict[str, Any]],
    entity_type: str,
    entity_id: str,
    attributes: Mapping[str, Any],
) -> None:
    entities[entity_id] = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "attributes": _canonical(attributes),
    }


def _add_relation(
    relations: list[dict[str, Any]],
    relation_type: str,
    source_id: str,
    target_id: str,
    attributes: Mapping[str, Any] | None = None,
) -> None:
    relations.append(
        {
            "relation_type": relation_type,
            "source_id": source_id,
            "target_id": target_id,
            "attributes": _canonical(attributes or {}),
        }
    )


def snapshot_world_state(snapshot: GovernanceTurnSnapshot) -> dict[str, Any]:
    """Project a typed snapshot into stable world entities, relations, metrics."""

    overview = snapshot.overview
    if overview is None:
        raise SnapshotConsistencyError("A typed GameOverview is required")
    capabilities = snapshot.capabilities
    player_id = f"player:{snapshot.player_id}"
    entities: dict[str, dict[str, Any]] = {}
    relations: list[dict[str, Any]] = []
    metrics: dict[str, int | float] = {
        "player.gold": overview.gold,
        "player.gold_per_turn": overview.gold_per_turn,
        "player.science": overview.science_yield,
        "player.culture": overview.culture_yield,
        "player.faith": overview.faith,
        "player.score": overview.score,
        "player.cities": overview.num_cities,
        "player.units": overview.num_units,
        "player.population": overview.total_population,
        "player.gold_income": overview.gold_income,
        "player.total_maintenance": overview.total_maintenance,
        "player.unit_maintenance": overview.unit_maintenance,
        "player.religions_founded": overview.religions_founded,
        "player.religions_max": overview.religions_max,
    }
    if overview.total_land > 0:
        metrics["player.exploration_pct"] = round(
            overview.explored_land / overview.total_land * 100, 4
        )
    if capabilities.diplomatic_favor:
        metrics["player.diplomatic_favor"] = overview.diplomatic_favor
        metrics["player.favor_per_turn"] = overview.favor_per_turn
    if capabilities.ages:
        metrics["player.era_score"] = overview.era_score
        metrics["player.era_dark_threshold"] = overview.era_dark_threshold
        metrics["player.era_golden_threshold"] = overview.era_golden_threshold

    player_attributes = _canonical(overview)
    player_attributes["ruleset"] = capabilities.ruleset
    if not capabilities.ages:
        for field_name in (
            "era_name",
            "era_score",
            "era_dark_threshold",
            "era_golden_threshold",
        ):
            player_attributes.pop(field_name, None)
    if not capabilities.diplomatic_favor:
        player_attributes.pop("diplomatic_favor", None)
        player_attributes.pop("favor_per_turn", None)
    _add_entity(entities, "player", player_id, player_attributes)

    if overview.current_research and overview.current_research != "NONE":
        tech_id = f"technology:{overview.current_research}"
        _add_entity(
            entities,
            "technology",
            tech_id,
            {"technology_type": overview.current_research},
        )
        _add_relation(relations, "researching", player_id, tech_id)
    if overview.current_civic and overview.current_civic != "NONE":
        civic_id = f"civic:{overview.current_civic}"
        _add_entity(
            entities,
            "civic",
            civic_id,
            {"civic_type": overview.current_civic},
        )
        _add_relation(relations, "progressing", player_id, civic_id)

    for city in snapshot.cities:
        city_id = f"city:{snapshot.player_id}:{city.city_id}"
        tile_id = f"tile:{city.x}:{city.y}"
        _add_entity(entities, "tile", tile_id, {"x": city.x, "y": city.y})
        city_attributes = _canonical(city)
        if not capabilities.ages:
            # Loyalty is part of the expansion rules.  CityInfo necessarily
            # has defaults for parser compatibility; those defaults are not
            # observations in Standard games.
            for field_name in (
                "loyalty",
                "loyalty_max",
                "loyalty_per_turn",
                "turns_to_loyalty_flip",
            ):
                city_attributes.pop(field_name, None)
        _add_entity(
            entities,
            "city",
            city_id,
            city_attributes,
        )
        _add_relation(relations, "owns", player_id, city_id)
        _add_relation(relations, "located_at", city_id, tile_id)
        prefix = f"city.{city.city_id}"
        metrics.update(
            {
                f"{prefix}.population": city.population,
                f"{prefix}.food": city.food,
                f"{prefix}.food_surplus": city.food_surplus,
                f"{prefix}.production": city.production,
                f"{prefix}.gold": city.gold,
                f"{prefix}.science": city.science,
                f"{prefix}.culture": city.culture,
                f"{prefix}.faith": city.faith,
                f"{prefix}.housing": city.housing,
                f"{prefix}.amenities": city.amenities,
                f"{prefix}.turns_to_grow": city.turns_to_grow,
                f"{prefix}.production_turns_left": city.production_turns_left,
                f"{prefix}.defense_strength": city.defense_strength,
                f"{prefix}.wall_hp": city.wall_hp,
            }
        )
        if capabilities.ages:
            metrics[f"{prefix}.loyalty"] = city.loyalty
            metrics[f"{prefix}.loyalty_per_turn"] = city.loyalty_per_turn

    for unit in snapshot.units:
        unit_id = f"unit:{unit.unit_id}"
        tile_id = f"tile:{unit.x}:{unit.y}"
        _add_entity(entities, "tile", tile_id, {"x": unit.x, "y": unit.y})
        _add_entity(entities, "unit", unit_id, _canonical(unit))
        _add_relation(relations, "owns", player_id, unit_id)
        _add_relation(relations, "located_at", unit_id, tile_id)
        prefix = f"unit.{unit.unit_id}"
        metrics.update(
            {
                f"{prefix}.health": unit.health,
                f"{prefix}.max_health": unit.max_health,
                f"{prefix}.moves_remaining": unit.moves_remaining,
                f"{prefix}.max_moves": unit.max_moves,
                f"{prefix}.combat_strength": unit.combat_strength,
                f"{prefix}.ranged_strength": unit.ranged_strength,
                f"{prefix}.build_charges": unit.build_charges,
                f"{prefix}.upgrade_cost": unit.upgrade_cost,
            }
        )

    for civ in snapshot.diplomacy:
        rival_id = f"player:{civ.player_id}"
        civ_attributes = _canonical(civ)
        if not capabilities.alliances:
            civ_attributes.pop("alliance_type", None)
            civ_attributes.pop("alliance_level", None)
            civ_attributes.pop("defensive_pacts", None)
        _add_entity(entities, "civilization", rival_id, civ_attributes)
        relation_attributes: dict[str, Any] = {
            "is_at_war": civ.is_at_war,
            "diplomatic_state": civ.diplomatic_state,
            "relationship_score": civ.relationship_score,
            "grievances": civ.grievances,
            "access_level": civ.access_level,
            "has_delegation": civ.has_delegation,
            "has_embassy": civ.has_embassy,
        }
        if capabilities.alliances:
            relation_attributes.update(
                {
                    "alliance_type": civ.alliance_type,
                    "alliance_level": civ.alliance_level,
                    "defensive_pacts": civ.defensive_pacts,
                }
            )
        _add_relation(relations, "diplomacy", player_id, rival_id, relation_attributes)
        prefix = f"diplomacy.player_{civ.player_id}"
        metrics.update(
            {
                f"{prefix}.at_war": int(civ.is_at_war),
                f"{prefix}.relationship_score": civ.relationship_score,
                f"{prefix}.grievances": civ.grievances,
                f"{prefix}.access_level": civ.access_level,
                f"{prefix}.military_strength": civ.military_strength,
                f"{prefix}.cities": civ.num_cities,
            }
        )
        if capabilities.alliances:
            metrics[f"{prefix}.alliance_level"] = civ.alliance_level

    if snapshot.tech_civic is not None:
        metrics.update(
            {
                "research.turns_remaining": snapshot.tech_civic.current_research_turns,
                "civic.turns_remaining": snapshot.tech_civic.current_civic_turns,
                "research.completed_count": snapshot.tech_civic.completed_tech_count,
                "civic.completed_count": snapshot.tech_civic.completed_civic_count,
            }
        )

    for resource in snapshot.resources:
        resource_key = resource.name.lower().replace(" ", "_")
        resource_id = f"resource_stockpile:{snapshot.player_id}:{resource_key}"
        _add_entity(entities, "resource_stockpile", resource_id, _canonical(resource))
        _add_relation(relations, "stockpiles", player_id, resource_id)
        prefix = f"resource.{resource_key}"
        metrics.update(
            {
                f"{prefix}.amount": resource.amount,
                f"{prefix}.cap": resource.cap,
                f"{prefix}.per_turn": resource.per_turn,
                f"{prefix}.demand": resource.demand,
                f"{prefix}.imported": resource.imported,
            }
        )

    if snapshot.policies is not None:
        government_id = f"government:{snapshot.player_id}"
        _add_entity(
            entities,
            "government",
            government_id,
            {
                "government_name": snapshot.policies.government_name,
                "government_type": snapshot.policies.government_type,
            },
        )
        _add_relation(relations, "uses_government", player_id, government_id)
        empty_slots = 0
        for slot in snapshot.policies.slots:
            slot_id = f"policy_slot:{snapshot.player_id}:{slot.slot_index}"
            _add_entity(entities, "policy_slot", slot_id, _canonical(slot))
            _add_relation(relations, "has_policy_slot", government_id, slot_id)
            if slot.current_policy is None:
                empty_slots += 1
        metrics["government.policy_slots"] = len(snapshot.policies.slots)
        metrics["government.empty_policy_slots"] = empty_slots
        metrics["government.available_policies"] = len(
            snapshot.policies.available_policies
        )

    if snapshot.barbarians is not None:
        metrics["barbarian.known_camps"] = len(snapshot.barbarians.camps)
        metrics["barbarian.visible_units"] = len(snapshot.barbarians.units)
        for camp in snapshot.barbarians.camps:
            camp_id = f"barbarian_camp:{camp.x}:{camp.y}"
            tile_id = f"tile:{camp.x}:{camp.y}"
            _add_entity(entities, "tile", tile_id, {"x": camp.x, "y": camp.y})
            _add_entity(entities, "barbarian_camp", camp_id, _canonical(camp))
            _add_relation(relations, "located_at", camp_id, tile_id)
        for unit in snapshot.barbarians.units:
            unit_id = f"barbarian_unit:{unit.unit_id}"
            tile_id = f"tile:{unit.x}:{unit.y}"
            _add_entity(entities, "tile", tile_id, {"x": unit.x, "y": unit.y})
            _add_entity(entities, "barbarian_unit", unit_id, _canonical(unit))
            _add_relation(relations, "located_at", unit_id, tile_id)

    if snapshot.victory is not None:
        for progress in snapshot.victory.players:
            prefix = f"victory.player_{progress.player_id}"
            metrics.update(
                {
                    f"{prefix}.score": progress.score,
                    f"{prefix}.science_vp": progress.science_vp,
                    f"{prefix}.tourism": progress.tourism,
                    f"{prefix}.military_strength": progress.military_strength,
                    f"{prefix}.techs_researched": progress.techs_researched,
                    f"{prefix}.civics_completed": progress.civics_completed,
                    f"{prefix}.religion_cities": progress.religion_cities,
                }
            )
            if capabilities.world_congress:
                metrics[f"{prefix}.diplomatic_vp"] = progress.diplomatic_vp

    relations.sort(
        key=lambda relation: (
            relation["relation_type"],
            relation["source_id"],
            relation["target_id"],
        )
    )
    return {
        "snapshot_id": snapshot.snapshot_id,
        "turn": snapshot.turn,
        "turn_before": snapshot.turn_before,
        "turn_after": snapshot.turn_after,
        "captured_at": snapshot.captured_at,
        "ruleset": capabilities.ruleset,
        "capabilities": _canonical(capabilities),
        "entities": sorted(
            entities.values(), key=lambda entity: (entity["entity_type"], entity["entity_id"])
        ),
        "relations": relations,
        "metrics": dict(sorted(metrics.items())),
        "notifications": _canonical(snapshot.notifications),
        "policies": _canonical(snapshot.policies),
        "barbarians": _canonical(snapshot.barbarians),
        "extra": _canonical(snapshot.extra),
    }


def snapshot_to_belief_observation(
    snapshot: GovernanceTurnSnapshot,
) -> BeliefObservation:
    """Return a structured Observation payload ready for BeliefEngine ingest."""

    world = snapshot_world_state(snapshot)
    metrics = world.pop("metrics")
    return {
        "statement": f"Typed game-state snapshot captured for turn {snapshot.turn}",
        "source": "game_state:typed_snapshot",
        "facts": world,
        "metrics": metrics,
        "reliability": 1.0,
        "observed_turn": snapshot.turn,
        "tags": ["automatic", "typed", "governance", "snapshot"],
    }
