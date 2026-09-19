"""Domain mutation factories for the new Runtime Core.

They build one exact Civ6 request plus the evidence probe needed by
SessionKernel.  They never submit, retry, restart, or infer combat success.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from civ_mcp.civ.adapter import CivAdapter, CivMutationRequest
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
