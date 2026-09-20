"""Model-facing façade for the new Runtime Core.

This is deliberately framework-neutral: the eventual FastMCP registration is
an outer adapter.  Every callable here has exactly one downstream authority.
"""

from __future__ import annotations

from civ_mcp.runtime.context import ContextBuilder, RuntimeContext
from civ_mcp.runtime.contracts import OperationId, OperationRecord
from civ_mcp.runtime.session import MutationExecution, SessionKernel
from civ_mcp.runtime.store import HandoffNote
from civ_mcp.runtime.turn import TurnLoop, TurnResult


class RuntimeMcpSurface:
    """Expose Runtime Core capabilities without leaking implementation layers."""

    def __init__(
        self,
        *,
        context: ContextBuilder,
        session: SessionKernel,
        turn_loop: TurnLoop,
    ) -> None:
        self._context = context
        self._session = session
        self._turn_loop = turn_loop

    async def get_context(self) -> RuntimeContext:
        return await self._context.build()

    async def get_unit_promotions(self, unit_index: int):
        """Return promotion facts without exposing CivAdapter to MCP routing."""
        return await self._context.read_unit_promotions(unit_index)

    async def get_unit_attack_target(self, unit_index: int, target_x: int, target_y: int):
        """Return one live attack target without exposing CivAdapter to routing."""
        return await self._context.read_attack_target(unit_index, target_x, target_y)

    async def get_city_attack_target(self, city_id: int, target_x: int, target_y: int):
        """Return one live city target without exposing CivAdapter to routing."""
        return await self._context.read_city_attack_target(city_id, target_x, target_y)

    async def get_district_placements(self, city_id: int, district_type: str):
        """Return only current district coordinates accepted by the game operation."""
        return await self._context.read_district_placements(city_id, district_type)

    async def get_builder_improvement_candidates(self, unit_index: int):
        """Return only improvements legal on the builder's current tile."""
        return await self._context.read_builder_improvement_candidates(unit_index)

    async def get_pending_deals(self):
        """Return exact terms of trade offers that still require a decision."""
        return await self._context.read_pending_deals()

    async def get_trade_negotiation(self, other_player_id: int):
        """Return one direct deal-manager state without creating a proposal."""
        return await self._context.read_trade_negotiation(other_player_id)

    async def get_city_states(self):
        """Return envoy facts without exposing CivAdapter to MCP routing."""
        return await self._context.read_city_states()

    async def get_governors(self):
        """Return governor facts without exposing CivAdapter to MCP routing."""
        return await self._context.read_governors()

    async def get_governments(self):
        """Return government facts without exposing CivAdapter to MCP routing."""
        return await self._context.read_governments()

    async def get_policies(self):
        """Return policy facts without exposing CivAdapter to MCP routing."""
        return await self._context.read_policies()

    async def get_city_purchases(self, city_id: int, yield_type: str):
        """Return live purchase candidates without exposing CivAdapter to routing."""
        return await self._context.read_city_purchases(city_id, yield_type)

    async def get_city_production(self, city_id: int):
        """Return live production candidates without exposing CivAdapter to routing."""
        return await self._context.read_city_production(city_id)

    async def get_trade_destinations(self, unit_index: int):
        """Return live trade candidates without exposing CivAdapter to routing."""
        return await self._context.read_trade_destinations(unit_index)

    async def get_trade_routes(self):
        """Return active routes without exposing CivAdapter to MCP routing."""
        return await self._context.read_trade_routes()

    async def get_world_congress(self):
        """Return current World Congress facts without exposing a vote operation."""
        return await self._context.read_world_congress()

    async def get_climate_overview(self):
        """Return current climate facts without exposing any game mutation."""
        return await self._context.read_climate_overview()

    async def get_spies(self):
        """Return current spy facts without exposing a spy operation."""
        return await self._context.read_spies()

    async def get_religion_overview(self):
        """Return world religion facts without exposing a religion operation."""
        return await self._context.read_religion_overview()

    async def get_barbarian_overview(self):
        """Return fog-limited barbarian facts without exposing a combat operation."""
        return await self._context.read_barbarian_overview()

    async def get_village_overview(self):
        """Return revealed village facts without exposing a tile-entry operation."""
        return await self._context.read_village_overview()

    async def get_wonder_placements(self, city_id: int, wonder_name: str):
        """Return live wonder placements without exposing a production operation."""
        return await self._context.read_wonder_placements(city_id, wonder_name)

    async def get_settle_candidates(self, unit_index: int):
        """Return the game's own settle-quality judgments without founding a city."""
        return await self._context.read_settle_candidates(unit_index)

    async def get_game_over(self):
        """Return the authoritative end-of-game signal without assuming an outcome."""
        return await self._context.read_game_over()

    def save_handoff(
        self,
        *,
        strategic_focus: str,
        existing_arrangements: str,
        rationale: str,
        change_conditions: str,
    ) -> HandoffNote:
        """Persist strategy-only continuity for the currently bound branch."""
        return self._session.save_handoff(
            strategic_focus=strategic_focus,
            existing_arrangements=existing_arrangements,
            rationale=rationale,
            change_conditions=change_conditions,
        )

    async def execute_mutation(
        self, execution: MutationExecution, *, decision_turn: int
    ) -> OperationRecord:
        return await self._session.execute(execution, decision_turn=decision_turn)

    async def end_turn(
        self, execution: MutationExecution, *, decision_turn: int
    ) -> TurnResult:
        return await self._turn_loop.end_turn(execution, decision_turn=decision_turn)

    async def resume_turn_decision(self, operation_id: OperationId, choice: str) -> TurnResult:
        return await self._turn_loop.resume(operation_id, choice)
