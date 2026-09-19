"""Read-only current-context construction for the Runtime Core."""

from __future__ import annotations

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
    unfinished_intents: tuple[OperationRecord, ...]
    handoff: HandoffNote | None
    further_queries: tuple[str, ...]


class ContextBuilder:
    """Build model context from fresh Civ reads and Runtime execution facts only."""

    def __init__(self, adapter: CivAdapter, session: SessionKernel) -> None:
        self._adapter = adapter
        self._session = session

    async def build(self) -> RuntimeContext:
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
        return RuntimeContext(
            facts=facts,
            unknown=tuple(unknown),
            unfinished_intents=unfinished,
            handoff=self._session.handoff_note(),
            further_queries=(
                "get_map_area",
                "get_combat_estimate",
                "get_trade_options",
            ),
        )

    async def read_unit_promotions(self, unit_index: int):
        """Read promotion candidates through the context boundary only."""
        overview = await self._adapter.read_overview()
        return await self._adapter.read_unit_promotions(
            unit_index=unit_index, observed_turn=overview.observed_turn
        )
