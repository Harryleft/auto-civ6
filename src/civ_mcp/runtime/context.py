"""Read-only current-context construction for the Runtime Core."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from civ_mcp.civ.adapter import CivAdapter
from civ_mcp.runtime.contracts import OperationRecord, OutcomeState
from civ_mcp.runtime.session import SessionKernel
from civ_mcp.runtime.store import HandoffNote


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Facts and uncertainty for model input, never a second world-state store."""

    facts: dict[str, object]
    unknown: tuple[str, ...]
    pending_decisions: tuple[object, ...]
    unfinished_intents: tuple[OperationRecord, ...]
    handoff: HandoffNote | None
    further_queries: tuple[str, ...]


class ContextBuilder:
    """Build model context from fresh Civ reads and Runtime execution facts only.

    ``pending_decisions`` is a read-only view of the TurnLoop's currently
    resumable interrupts.  It carries no continuation and cannot mutate the
    game; after a host restart it is intentionally empty and the persisted
    unfinished operation remains the recovery boundary.
    """

    def __init__(
        self,
        adapter: CivAdapter,
        session: SessionKernel,
        *,
        pending_decisions: Callable[[], tuple[object, ...]] | None = None,
    ) -> None:
        self._adapter = adapter
        self._session = session
        self._pending_decisions = pending_decisions or (lambda: ())

    async def build(self) -> RuntimeContext:
        binding = await self._session.current_binding()
        overview = await self._adapter.read_overview()
        turn = overview.observed_turn
        facts: dict[str, object] = {"overview": overview}
        unknown: list[str] = []
        for name, read in (
            ("cities", self._adapter.read_cities),
            ("pending_city_capture", self._adapter.read_pending_city_capture),
            ("units", self._adapter.read_units),
            ("diplomacy", self._adapter.read_diplomacy),
            ("pending_diplomacy", self._adapter.read_diplomacy_sessions),
            ("tech_civics", self._adapter.read_tech_civics),
            ("pantheon", self._adapter.read_pantheon_status),
            ("dedications", self._adapter.read_dedications),
            ("great_people", self._adapter.read_great_people),
            ("victory_progress", self._adapter.read_victory_progress),
        ):
            try:
                facts[name] = await read(observed_turn=turn)
            except Exception as exc:
                unknown.append(f"{name}: {type(exc).__name__}")
        unfinished = tuple(
            operation
            for operation in self._session.current_operations()
            if operation.outcome_state in {OutcomeState.OBSERVING, OutcomeState.UNKNOWN}
        )
        await self._session.assert_binding_current(binding)
        return RuntimeContext(
            facts=facts,
            unknown=tuple(unknown),
            pending_decisions=self._pending_decisions(),
            unfinished_intents=unfinished,
            handoff=self._session.handoff_note(),
            further_queries=(
                "get_unit_promotions",
                "get_unit_attack_target",
                "get_city_attack_target",
                "get_district_placements",
                "get_builder_improvement_candidates",
                "get_pending_deals",
                "get_trade_negotiation",
                "get_city_states",
                "get_governors",
                "get_governments",
                "get_policies",
                "get_city_purchases",
                "get_city_production",
                "get_trade_destinations",
                "get_trade_routes",
                "get_world_congress",
                "get_climate_overview",
            ),
        )

    async def read_unit_promotions(self, unit_index: int):
        """Read promotion candidates through the context boundary only."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_unit_promotions(
            unit_index=unit_index, observed_turn=overview.observed_turn
        )

    async def read_attack_target(self, unit_index: int, target_x: int, target_y: int):
        """Read one game-approved attack target through the context boundary."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_attack_target(
            unit_index=unit_index,
            target_x=target_x,
            target_y=target_y,
            observed_turn=overview.observed_turn,
        )

    async def read_city_attack_target(self, city_id: int, target_x: int, target_y: int):
        """Read one game-approved city attack target through the context boundary."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_city_attack_target(
            city_id=city_id,
            target_x=target_x,
            target_y=target_y,
            observed_turn=overview.observed_turn,
        )

    async def read_district_placements(self, city_id: int, district_type: str):
        """Read current game-approved placement coordinates for one district."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_district_placements(
            city_id=city_id,
            district_type=district_type,
            observed_turn=overview.observed_turn,
        )

    async def read_builder_improvement_candidates(self, unit_index: int):
        """Read only improvements legal on this builder's present tile."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_builder_improvement_candidates(
            unit_index=unit_index,
            observed_turn=overview.observed_turn,
        )

    async def read_pending_deals(self):
        """Read exact terms of current pending trade offers."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_pending_deals(observed_turn=overview.observed_turn)

    async def read_trade_negotiation(self, other_player_id: int):
        """Read one live trade proposal/counter-offer without sending a deal."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_trade_negotiation(
            other_player_id=other_player_id,
            observed_turn=overview.observed_turn,
        )

    async def read_city_states(self):
        """Read envoy decisions through the context boundary only."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_city_states(observed_turn=overview.observed_turn)

    async def read_governors(self):
        """Read governor decisions through the context boundary only."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_governors(observed_turn=overview.observed_turn)

    async def read_governments(self):
        """Read government choices through the context boundary only."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_governments(observed_turn=overview.observed_turn)

    async def read_policies(self):
        """Read policy configuration through the context boundary only."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_policies(observed_turn=overview.observed_turn)

    async def read_city_purchases(self, city_id: int, yield_type: str):
        """Read one city's legal immediate purchases through the context boundary."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_city_purchases(
            city_id=city_id,
            yield_type=yield_type,
            observed_turn=overview.observed_turn,
        )

    async def read_city_production(self, city_id: int):
        """Read one city's current production candidates through the boundary."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_city_production(
            city_id=city_id, observed_turn=overview.observed_turn
        )

    async def read_trade_destinations(self, unit_index: int):
        """Read one trader's live destination candidates through the context boundary."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_trade_destinations(
            unit_index=unit_index, observed_turn=overview.observed_turn
        )

    async def read_trade_routes(self):
        """Read active trade routes through the context boundary only."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_trade_routes(observed_turn=overview.observed_turn)

    async def read_world_congress(self):
        """Read current World Congress facts without submitting a vote."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_world_congress(observed_turn=overview.observed_turn)

    async def read_climate_overview(self):
        """Read Gathering Storm climate facts without changing the world state."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_climate_overview(observed_turn=overview.observed_turn)
