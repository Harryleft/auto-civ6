"""Adapter-neutral input contracts for the governance snapshot.

The MCP adapter currently returns dataclasses from ``civ_mcp.lua.models``.
Governance should not import those concrete adapter types: a future game
adapter must be able to provide the same fields without importing MCP code.
These structural contracts keep that seam explicit while preserving the
current immutable ``TypedTurnSnapshot`` validation.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GameOverviewInput(Protocol):
    turn: int
    player_id: int
    ruleset: str
    num_cities: int
    num_units: int
    current_research: str
    current_civic: str
    gold: float
    gold_per_turn: float
    science_yield: float
    culture_yield: float
    faith: float
    score: int
    total_population: int
    gold_income: float
    total_maintenance: float
    unit_maintenance: int
    religions_founded: int
    religions_max: int
    explored_land: int
    total_land: int
    diplomatic_favor: int
    favor_per_turn: int
    era_score: int
    era_dark_threshold: int
    era_golden_threshold: int


@runtime_checkable
class CityInput(Protocol):
    city_id: int
    name: str
    x: int
    y: int
    population: int
    food: float
    production: float
    gold: float
    science: float
    culture: float
    faith: float
    housing: float
    amenities: int
    turns_to_grow: int
    food_surplus: float
    production_turns_left: int
    defense_strength: int
    wall_hp: int
    loyalty: float
    loyalty_per_turn: float


@runtime_checkable
class UnitInput(Protocol):
    unit_id: int
    unit_type: str
    x: int
    y: int
    health: int
    max_health: int
    moves_remaining: float
    max_moves: float
    combat_strength: int
    ranged_strength: int
    build_charges: int
    upgrade_cost: int
    can_upgrade: bool


@runtime_checkable
class DiplomacyInput(Protocol):
    player_id: int
    civ_name: str
    leader_name: str
    has_met: bool
    is_at_war: bool
    diplomatic_state: str
    relationship_score: int
    grievances: int
    access_level: int
    has_delegation: bool
    has_embassy: bool
    alliance_type: str | None
    alliance_level: int
    defensive_pacts: Sequence[int]
    military_strength: int
    num_cities: int


@runtime_checkable
class TechCivicInput(Protocol):
    current_research: str
    current_research_turns: int
    current_civic: str
    current_civic_turns: int
    completed_tech_count: int
    completed_civic_count: int
    available_techs: Sequence[Any]
    available_civics: Sequence[Any]


@runtime_checkable
class ResourceStockpileInput(Protocol):
    name: str
    amount: int
    cap: int
    per_turn: int
    demand: int
    imported: int


@runtime_checkable
class NotificationInput(Protocol):
    type_name: str
    message: str
    turn: int
    x: int
    y: int


@runtime_checkable
class GovernmentInput(Protocol):
    government_name: str
    government_type: str
    slots: Sequence[Any]
    available_policies: Sequence[Any]


@runtime_checkable
class BarbarianOverviewInput(Protocol):
    camps: Sequence[Any]
    units: Sequence[Any]


@runtime_checkable
class GreatPeopleInput(Protocol):
    standings: Sequence[Any]


@runtime_checkable
class ThreatInput(Protocol):
    owner_id: int
    owner_name: str
    unit_id: int
    unit_type: str
    x: int
    y: int
    combat_strength: int
    ranged_strength: int
    distance: int
    nearest_city_id: int
    distance_to_city: int
    city_distances: Sequence[tuple[int, int]]
    is_at_war: bool
    is_city_state: bool


@runtime_checkable
class VictoryInput(Protocol):
    players: Sequence[Any]
