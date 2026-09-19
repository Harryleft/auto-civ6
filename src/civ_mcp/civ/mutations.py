"""Domain mutation factories for the new Runtime Core.

They build one exact Civ6 request plus the evidence probe needed by
SessionKernel.  They never submit, retry, restart, or infer combat success.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from civ_mcp.civ.adapter import CivAdapter, CivMutationRequest
from civ_mcp.lua.cities import build_produce_item, build_purchase_item
from civ_mcp.lua.diplomacy import build_propose_trade
from civ_mcp.lua.economy import build_make_trade_route
from civ_mcp.lua.tech import build_set_civic, build_set_research
from civ_mcp.lua.units import build_attack_unit, build_move_unit
from civ_mcp.runtime.contracts import Evidence, OperationId, OperationIntent
from civ_mcp.runtime.session import MutationExecution


AttackReadback = Callable[[], Awaitable[Evidence | None]]


class CivMutationFactory:
    """Build domain mutations; SessionKernel remains the only submitter."""

    def __init__(self, adapter: CivAdapter) -> None:
        self._adapter = adapter

    def move_unit(
        self,
        *,
        operation_id: OperationId,
        unit_index: int,
        target_x: int,
        target_y: int,
        observed_turn: int,
    ) -> MutationExecution:
        intent = OperationIntent.create(
            "move_unit",
            {"unit_index": unit_index, "target_x": target_x, "target_y": target_y},
        )

        async def verify() -> Evidence | None:
            try:
                units = await self._adapter.read_units(observed_turn=observed_turn)
            except Exception:
                return None
            for unit in units.value:
                if unit.unit_index == unit_index and (unit.x, unit.y) == (target_x, target_y):
                    return Evidence(
                        source="read_units",
                        observed_turn=units.observed_turn,
                        detail=f"unit_index={unit_index} at ({target_x},{target_y})",
                    )
            return None

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest("move_unit", build_move_unit(unit_index, target_x, target_y)),
            verify=verify,
            operation_id=operation_id,
        )

    def attack_unit(
        self,
        *,
        operation_id: OperationId,
        unit_index: int,
        target_x: int,
        target_y: int,
        readback: AttackReadback,
    ) -> MutationExecution:
        """Create an attack whose caller supplies a factual postcondition probe."""
        return MutationExecution(
            intent=OperationIntent.create(
                "attack_unit",
                {"unit_index": unit_index, "target_x": target_x, "target_y": target_y},
            ),
            request=CivMutationRequest("attack_unit", build_attack_unit(unit_index, target_x, target_y)),
            verify=readback,
            operation_id=operation_id,
        )

    def set_production(
        self,
        *,
        operation_id: OperationId,
        city_id: int,
        item_type: str,
        item_name: str,
        observed_turn: int,
        target_x: int | None = None,
        target_y: int | None = None,
    ) -> MutationExecution:
        """Confirm production by the city's current queue, never Lua's OK text."""
        intent = OperationIntent.create(
            "set_city_production",
            {
                "city_id": city_id,
                "item_type": item_type,
                "item_name": item_name,
                "target_x": target_x,
                "target_y": target_y,
            },
        )

        async def verify() -> Evidence | None:
            try:
                cities = await self._adapter.read_cities(observed_turn=observed_turn)
            except Exception:
                return None
            for city in cities.value:
                if city.city_id == city_id and city.currently_building == item_name:
                    return Evidence("read_cities", cities.observed_turn, f"city_id={city_id} producing {item_name}")
            return None

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest(
                "set_city_production",
                build_produce_item(city_id, item_type, item_name, target_x, target_y),
            ),
            verify=verify,
            operation_id=operation_id,
        )

    def purchase_item(
        self,
        *,
        operation_id: OperationId,
        city_id: int,
        item_type: str,
        item_name: str,
        yield_type: str,
        currency_before: float,
        observed_turn: int,
        known_unit_ids: frozenset[int] = frozenset(),
    ) -> MutationExecution:
        """Confirm purchase with both resource decrease and a new owned object."""
        intent = OperationIntent.create(
            "purchase_item",
            {"city_id": city_id, "item_type": item_type, "item_name": item_name, "yield_type": yield_type},
        )

        async def verify() -> Evidence | None:
            try:
                overview = await self._adapter.read_overview()
                balance = overview.value.faith if yield_type == "YIELD_FAITH" else overview.value.gold
                if balance >= currency_before:
                    return None
                if item_type.upper() == "UNIT":
                    units = await self._adapter.read_units(observed_turn=observed_turn)
                    if any(unit.unit_id not in known_unit_ids and unit.unit_type == item_name for unit in units.value):
                        return Evidence("read_overview+read_units", overview.observed_turn, f"purchased {item_name}")
                else:
                    cities = await self._adapter.read_cities(observed_turn=observed_turn)
                    if any(city.city_id == city_id and item_name in city.buildings for city in cities.value):
                        return Evidence("read_overview+read_cities", overview.observed_turn, f"purchased {item_name}")
            except Exception:
                return None
            return None

        return MutationExecution(
            intent=intent,
            request=CivMutationRequest("purchase_item", build_purchase_item(city_id, item_type, item_name, yield_type)),
            verify=verify,
            operation_id=operation_id,
        )

    def make_trade_route(
        self,
        *,
        operation_id: OperationId,
        unit_index: int,
        target_x: int,
        target_y: int,
        readback: AttackReadback,
    ) -> MutationExecution:
        """A route is confirmed only by a domain route readback supplied by caller."""
        return MutationExecution(
            intent=OperationIntent.create(
                "make_trade_route", {"unit_index": unit_index, "target_x": target_x, "target_y": target_y},
            ),
            request=CivMutationRequest("make_trade_route", build_make_trade_route(unit_index, target_x, target_y)),
            verify=readback,
            operation_id=operation_id,
        )

    def propose_trade(
        self,
        *,
        operation_id: OperationId,
        other_player_id: int,
        offer_items: list[dict[str, object]],
        request_items: list[dict[str, object]],
        readback: AttackReadback,
    ) -> MutationExecution:
        """Submit exactly these terms; a counter-offer is never auto-accepted."""
        return MutationExecution(
            intent=OperationIntent.create(
                "propose_trade",
                {
                    "other_player_id": other_player_id,
                    "offer_items": offer_items,
                    "request_items": request_items,
                },
            ),
            request=CivMutationRequest(
                "propose_trade",
                build_propose_trade(other_player_id, offer_items, request_items),
            ),
            verify=readback,
            operation_id=operation_id,
        )

    def set_research(
        self, *, operation_id: OperationId, tech_name: str, readback: AttackReadback
    ) -> MutationExecution:
        """Set research only after a fresh tech/civic readback can confirm it."""
        return self._readback_action(
            operation_id=operation_id,
            tool="set_research",
            arguments={"tech_name": tech_name},
            lua_code=build_set_research(tech_name),
            readback=readback,
        )

    def set_civic(
        self, *, operation_id: OperationId, civic_name: str, readback: AttackReadback
    ) -> MutationExecution:
        return self._readback_action(
            operation_id=operation_id,
            tool="set_civic",
            arguments={"civic_name": civic_name},
            lua_code=build_set_civic(civic_name),
            readback=readback,
        )

    @staticmethod
    def _readback_action(
        *,
        operation_id: OperationId,
        tool: str,
        arguments: dict[str, object],
        lua_code: str,
        readback: AttackReadback,
    ) -> MutationExecution:
        """Shared wiring only; each caller owns its domain-specific evidence."""
        return MutationExecution(
            intent=OperationIntent.create(tool, arguments),
            request=CivMutationRequest(tool, lua_code),
            verify=readback,
            operation_id=operation_id,
        )
