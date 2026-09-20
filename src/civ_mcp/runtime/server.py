"""FastMCP entry point for the isolated Runtime Core.

``civ-mcp`` resolves here after K1. Its tools call ``RuntimeAssembly`` only;
neither a model-visible tool nor this module's normal tool path reaches the
FireTuner transport directly.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import Enum
import os
from pathlib import Path
from typing import Any, AsyncIterator

from mcp.server.fastmcp import Context, FastMCP

from civ_mcp.civ.adapter import CivAdapter
from civ_mcp.runtime.bootstrap import RuntimeAssembly, assemble_runtime
from civ_mcp.runtime.connection import RuntimeConnection
from civ_mcp.runtime.contracts import OperationId, OperationRecord
from civ_mcp.runtime.store import OperationStore
from civ_mcp.runtime.turn import TurnResult


RUNTIME_BRANCH_ENV = "CIV_MCP_RUNTIME_BRANCH"
RUNTIME_STORE_ENV = "CIV_MCP_RUNTIME_STORE"


class RuntimeServerConfigurationError(RuntimeError):
    """The host omitted a stable branch or persistence location."""


@dataclass(slots=True)
class RuntimeAppContext:
    """Resources owned for one formal Runtime MCP process."""

    assembly: RuntimeAssembly
    connection: RuntimeConnection


def _runtime_store_path() -> Path:
    """Resolve an optional host path without treating an empty env var as '.'."""
    configured = os.environ.get(RUNTIME_STORE_ENV, "").strip()
    if configured:
        return Path(configured)
    return Path.home() / ".civ6-mcp" / "runtime" / "operations.sqlite3"


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[RuntimeAppContext]:
    """Open and bind the new Runtime Core without legacy background services."""
    branch_token = os.environ.get(RUNTIME_BRANCH_ENV, "").strip()
    if not branch_token:
        raise RuntimeServerConfigurationError(
            f"{RUNTIME_BRANCH_ENV} 必须指向当前存档的稳定分支标识。"
        )
    store_path = _runtime_store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    connection: RuntimeConnection | None = None
    store: OperationStore | None = None
    try:
        connection = await RuntimeConnection.connect()
        adapter = CivAdapter(connection.transport, state_resolver=connection.state_for)
        store = OperationStore(store_path)
        assembly = await assemble_runtime(adapter, store, branch_token=branch_token)
        yield RuntimeAppContext(assembly=assembly, connection=connection)
    finally:
        if store is not None:
            store.close()
        if connection is not None:
            await connection.close()


mcp = FastMCP(
    "Civilization VI Runtime Core",
    instructions=(
        "This surface exposes only the isolated Runtime Core. "
        "Read current context before a mutation; retain the same operation_id "
        "when checking an uncertain result, and never assume UNKNOWN succeeded."
    ),
    lifespan=lifespan,
)


def _runtime(ctx: Context) -> RuntimeAppContext:
    return ctx.request_context.lifespan_context


@mcp.tool(annotations={"readOnlyHint": True})
async def get_runtime_context(ctx: Context) -> dict[str, object]:
    """Return fresh facts, unknowns, unfinished operations, and handoff context."""
    return _json_value(await _runtime(ctx).assembly.surface.get_context())


@mcp.tool(annotations={"readOnlyHint": True})
async def get_game_over(ctx: Context) -> dict[str, object]:
    """Read the authoritative end-of-game signal; a read failure is not "not over"."""
    return _json_value(await _runtime(ctx).assembly.surface.get_game_over())


@mcp.tool(annotations={"readOnlyHint": True})
async def get_unit_promotions(ctx: Context, unit_index: int) -> dict[str, object]:
    """Return current legal and already-owned promotions for one unit."""
    return _json_value(
        await _runtime(ctx).assembly.surface.get_unit_promotions(unit_index)
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_unit_attack_target(
    ctx: Context, unit_index: int, target_x: int, target_y: int
) -> dict[str, object]:
    """Validate one target against the live attack operation without sending it."""
    return _json_value(
        await _runtime(ctx).assembly.surface.get_unit_attack_target(
            unit_index, target_x, target_y
        )
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_city_attack_target(
    ctx: Context, city_id: int, target_x: int, target_y: int
) -> dict[str, object]:
    """Validate one city target against the live ranged-attack command without sending it."""
    return _json_value(
        await _runtime(ctx).assembly.surface.get_city_attack_target(
            city_id, target_x, target_y
        )
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_district_placements(
    ctx: Context, city_id: int, district_type: str
) -> dict[str, object]:
    """Return only current coordinates accepted for this city and district type."""
    return _json_value(
        await _runtime(ctx).assembly.surface.get_district_placements(
            city_id, district_type
        )
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_builder_improvement_candidates(
    ctx: Context, unit_index: int
) -> dict[str, object]:
    """Return only improvements legal on a builder's current tile."""
    return _json_value(
        await _runtime(ctx).assembly.surface.get_builder_improvement_candidates(unit_index)
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_pending_deals(ctx: Context) -> dict[str, object]:
    """Return exact pending trade terms; no offer is accepted automatically."""
    return _json_value(await _runtime(ctx).assembly.surface.get_pending_deals())


@mcp.tool(annotations={"readOnlyHint": True})
async def get_trade_negotiation(
    ctx: Context, other_player_id: int
) -> dict[str, object]:
    """Read an actual proposal/counter-offer state for one civilization."""
    return _json_value(
        await _runtime(ctx).assembly.surface.get_trade_negotiation(other_player_id)
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_city_states(ctx: Context) -> dict[str, object]:
    """Return current envoy tokens and every met city-state's send eligibility."""
    return _json_value(await _runtime(ctx).assembly.surface.get_city_states())


@mcp.tool(annotations={"readOnlyHint": True})
async def get_governors(ctx: Context) -> dict[str, object]:
    """Return current governor appointment, placement, and legal promotion facts."""
    return _json_value(await _runtime(ctx).assembly.surface.get_governors())


@mcp.tool(annotations={"readOnlyHint": True})
async def get_governments(ctx: Context) -> dict[str, object]:
    """Return every unlocked government and mark the current one."""
    return _json_value(await _runtime(ctx).assembly.surface.get_governments())


@mcp.tool(annotations={"readOnlyHint": True})
async def get_policies(ctx: Context) -> dict[str, object]:
    """Return policy slots and the exact replacement slots legal for each card."""
    return _json_value(await _runtime(ctx).assembly.surface.get_policies())


@mcp.tool(annotations={"readOnlyHint": True})
async def get_city_purchases(
    ctx: Context, city_id: int, yield_type: str = "YIELD_GOLD"
) -> dict[str, object]:
    """Return immediate unit/building purchases legal for one city and currency."""
    return _json_value(
        await _runtime(ctx).assembly.surface.get_city_purchases(city_id, yield_type)
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_city_production(ctx: Context, city_id: int) -> dict[str, object]:
    """Return current unit, building and project candidates for one city."""
    return _json_value(
        await _runtime(ctx).assembly.surface.get_city_production(city_id)
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_trade_destinations(ctx: Context, unit_index: int) -> dict[str, object]:
    """Return only destinations the current trader may legally route to now."""
    return _json_value(
        await _runtime(ctx).assembly.surface.get_trade_destinations(unit_index)
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_trade_routes(ctx: Context) -> dict[str, object]:
    """Return active/idle traders and exact active-route destinations."""
    return _json_value(await _runtime(ctx).assembly.surface.get_trade_routes())


@mcp.tool(annotations={"readOnlyHint": True})
async def get_world_congress(ctx: Context) -> dict[str, object]:
    """Return actual World Congress status, resolutions, and proposals; never vote."""
    return _json_value(await _runtime(ctx).assembly.surface.get_world_congress())


@mcp.tool(annotations={"readOnlyHint": True})
async def get_climate_overview(ctx: Context) -> dict[str, object]:
    """Return Gathering Storm climate facts; this tool never changes the game."""
    return _json_value(await _runtime(ctx).assembly.surface.get_climate_overview())


@mcp.tool(annotations={"readOnlyHint": True})
async def get_spies(ctx: Context) -> dict[str, object]:
    """Return current spies and legal missions; this tool never starts an operation."""
    return _json_value(await _runtime(ctx).assembly.surface.get_spies())


@mcp.tool(annotations={"readOnlyHint": True})
async def get_religion_overview(ctx: Context) -> dict[str, object]:
    """Return world religion facts; this tool never selects or spreads a religion."""
    return _json_value(await _runtime(ctx).assembly.surface.get_religion_overview())


@mcp.tool(annotations={"readOnlyHint": True})
async def get_barbarian_overview(ctx: Context) -> dict[str, object]:
    """Return revealed barbarian camps and visible units; this tool never fights."""
    return _json_value(await _runtime(ctx).assembly.surface.get_barbarian_overview())


@mcp.tool(annotations={"readOnlyHint": True})
async def get_village_overview(ctx: Context) -> dict[str, object]:
    """Return revealed tribal villages; this tool never enters a village tile."""
    return _json_value(await _runtime(ctx).assembly.surface.get_village_overview())


@mcp.tool(annotations={"readOnlyHint": True})
async def get_wonder_placements(
    ctx: Context, city_id: int, wonder_name: str
) -> dict[str, object]:
    """Return live legal placements for one wonder; this tool never starts production."""
    return _json_value(
        await _runtime(ctx).assembly.surface.get_wonder_placements(city_id, wonder_name)
    )


@mcp.tool()
async def save_handoff(
    ctx: Context,
    strategic_focus: str,
    existing_arrangements: str,
    rationale: str,
    change_conditions: str,
) -> dict[str, object]:
    """Save strategic continuity only; game facts and operation records stay immutable."""
    return _json_value(
        _runtime(ctx).assembly.surface.save_handoff(
            strategic_focus=strategic_focus,
            existing_arrangements=existing_arrangements,
            rationale=rationale,
            change_conditions=change_conditions,
        )
    )


@mcp.tool()
async def move_unit(
    ctx: Context,
    operation_id: str,
    unit_index: int,
    target_x: int,
    target_y: int,
    decision_turn: int,
) -> dict[str, object]:
    """Submit one hash-bound move; confirmation requires a fresh unit readback."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.move_unit(
        operation_id=OperationId(operation_id),
        unit_index=unit_index,
        target_x=target_x,
        target_y=target_y,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def attack_unit(
    ctx: Context,
    operation_id: str,
    unit_index: int,
    target_x: int,
    target_y: int,
    decision_turn: int,
) -> dict[str, object]:
    """Attack one game-approved target; only observed HP loss/removal confirms it."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.attack_unit(
        operation_id=OperationId(operation_id),
        unit_index=unit_index,
        target_x=target_x,
        target_y=target_y,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def attack_city(
    ctx: Context,
    operation_id: str,
    city_id: int,
    target_x: int,
    target_y: int,
    decision_turn: int,
) -> dict[str, object]:
    """Attack one city-approved target; only observed HP loss/removal confirms it."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.attack_city(
        operation_id=OperationId(operation_id),
        city_id=city_id,
        target_x=target_x,
        target_y=target_y,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def build_improvement(
    ctx: Context,
    operation_id: str,
    unit_index: int,
    improvement_type: str,
    decision_turn: int,
) -> dict[str, object]:
    """Build one current-tile improvement; exact candidate and tile readback are required."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.build_improvement(
        operation_id=OperationId(operation_id),
        unit_index=unit_index,
        improvement_type=improvement_type,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def propose_trade(
    ctx: Context,
    operation_id: str,
    other_player_id: int,
    offer_items: list[dict[str, object]],
    request_items: list[dict[str, object]],
    decision_turn: int,
) -> dict[str, object]:
    """Propose exact terms once; no direct counter-offer evidence remains UNKNOWN."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.propose_trade(
        operation_id=OperationId(operation_id),
        other_player_id=other_player_id,
        offer_items=offer_items,
        request_items=request_items,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def upgrade_unit(
    ctx: Context, operation_id: str, unit_index: int, decision_turn: int
) -> dict[str, object]:
    """Upgrade one eligible unit; confirmation requires its target UNIT_* type."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.upgrade_unit(
        operation_id=OperationId(operation_id),
        unit_index=unit_index,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def promote_unit(
    ctx: Context,
    operation_id: str,
    unit_index: int,
    promotion_type: str,
    decision_turn: int,
) -> dict[str, object]:
    """Promote one legal unit choice; confirmation requires owned promotion state."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.promote_unit(
        operation_id=OperationId(operation_id),
        unit_index=unit_index,
        promotion_type=promotion_type,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def send_envoy(
    ctx: Context,
    operation_id: str,
    city_state_player_id: int,
    decision_turn: int,
) -> dict[str, object]:
    """Send one legal envoy; both envoy and token deltas must be freshly proven."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.send_envoy(
        operation_id=OperationId(operation_id),
        city_state_player_id=city_state_player_id,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def appoint_governor(
    ctx: Context, operation_id: str, governor_type: str, decision_turn: int
) -> dict[str, object]:
    """Appoint one legal governor; confirmation requires the governor and point delta."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.appoint_governor(
        operation_id=OperationId(operation_id),
        governor_type=governor_type,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def assign_governor(
    ctx: Context,
    operation_id: str,
    governor_type: str,
    city_id: int,
    decision_turn: int,
) -> dict[str, object]:
    """Assign one appointed governor; confirmation requires its target city ID."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.assign_governor(
        operation_id=OperationId(operation_id),
        governor_type=governor_type,
        city_id=city_id,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def promote_governor(
    ctx: Context,
    operation_id: str,
    governor_type: str,
    promotion_type: str,
    decision_turn: int,
) -> dict[str, object]:
    """Promote one legal governor choice; proof requires ownership and point delta."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.promote_governor(
        operation_id=OperationId(operation_id),
        governor_type=governor_type,
        promotion_type=promotion_type,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def change_government(
    ctx: Context, operation_id: str, government_type: str, decision_turn: int
) -> dict[str, object]:
    """Change government once; confirmation requires the new current type."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.change_government(
        operation_id=OperationId(operation_id),
        government_type=government_type,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def set_policies(
    ctx: Context,
    operation_id: str,
    assignments: dict[int, str],
    decision_turn: int,
) -> dict[str, object]:
    """Set named slots only when each card is currently legal for that slot."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.set_policies(
        operation_id=OperationId(operation_id),
        assignments=assignments,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def set_city_production(
    ctx: Context,
    operation_id: str,
    city_id: int,
    item_type: str,
    item_name: str,
    decision_turn: int,
    target_x: int | None = None,
    target_y: int | None = None,
) -> dict[str, object]:
    """Set one city queue; the city readback must prove the selected item."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.set_production(
        operation_id=OperationId(operation_id),
        city_id=city_id,
        item_type=item_type,
        item_name=item_name,
        target_x=target_x,
        target_y=target_y,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def purchase_item(
    ctx: Context,
    operation_id: str,
    city_id: int,
    item_type: str,
    item_name: str,
    decision_turn: int,
    yield_type: str = "YIELD_GOLD",
) -> dict[str, object]:
    """Purchase one currently legal unit/building with fresh two-part evidence."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.purchase_item(
        operation_id=OperationId(operation_id),
        city_id=city_id,
        item_type=item_type,
        item_name=item_name,
        yield_type=yield_type,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def make_trade_route(
    ctx: Context,
    operation_id: str,
    unit_index: int,
    target_x: int,
    target_y: int,
    decision_turn: int,
) -> dict[str, object]:
    """Route one trader to a live destination; exact route readback confirms it."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.make_trade_route(
        operation_id=OperationId(operation_id),
        unit_index=unit_index,
        target_x=target_x,
        target_y=target_y,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def set_research(
    ctx: Context, operation_id: str, tech_name: str, decision_turn: int
) -> dict[str, object]:
    """Set one currently legal technology; confirmation uses its stable type ID."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.set_research(
        operation_id=OperationId(operation_id),
        tech_name=tech_name,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def set_civic(
    ctx: Context, operation_id: str, civic_name: str, decision_turn: int
) -> dict[str, object]:
    """Set one currently legal civic; confirmation uses its stable type ID."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.set_civic(
        operation_id=OperationId(operation_id),
        civic_name=civic_name,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def choose_pantheon(
    ctx: Context, operation_id: str, belief_type: str, decision_turn: int
) -> dict[str, object]:
    """Found one legal pantheon; readback must match its stable BELIEF_* ID."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.choose_pantheon(
        operation_id=OperationId(operation_id),
        belief_type=belief_type,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def choose_dedication(
    ctx: Context, operation_id: str, dedication_index: int, decision_turn: int
) -> dict[str, object]:
    """Choose one legal commemoration; confirmation requires it to become active."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.choose_dedication(
        operation_id=OperationId(operation_id),
        dedication_index=dedication_index,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def recruit_great_person(
    ctx: Context, operation_id: str, individual_id: int, decision_turn: int
) -> dict[str, object]:
    """Recruit one currently legal individual; confirmation requires local claim state."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.recruit_great_person(
        operation_id=OperationId(operation_id),
        individual_id=individual_id,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def found_city(
    ctx: Context,
    operation_id: str,
    unit_index: int,
    target_x: int,
    target_y: int,
    decision_turn: int,
) -> dict[str, object]:
    """Found a city only from a verified settler tile and a new city readback."""
    assembly = _runtime(ctx).assembly
    execution = assembly.mutations.found_city(
        operation_id=OperationId(operation_id),
        unit_index=unit_index,
        target_x=target_x,
        target_y=target_y,
        observed_turn=decision_turn,
    )
    return _operation_payload(
        await assembly.surface.execute_mutation(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def end_turn(
    ctx: Context, operation_id: str, decision_turn: int
) -> dict[str, object]:
    """Advance a turn once; TurnLoop owns its read-only wait and evidence."""
    assembly = _runtime(ctx).assembly

    async def no_immediate_evidence() -> None:
        return None

    execution = assembly.mutations.end_turn(
        operation_id=OperationId(operation_id), readback=no_immediate_evidence
    )
    return _turn_payload(
        await assembly.surface.end_turn(execution, decision_turn=decision_turn)
    )


@mcp.tool()
async def resume_turn_decision(
    ctx: Context, operation_id: str, choice: str
) -> dict[str, object]:
    """Apply one listed blocker choice and continue its original end-turn only."""
    return _turn_payload(
        await _runtime(ctx).assembly.surface.resume_turn_decision(
            OperationId(operation_id), choice
        )
    )


def _operation_payload(operation: OperationRecord) -> dict[str, object]:
    return _json_value(operation)


def _turn_payload(result: TurnResult) -> dict[str, object]:
    return _json_value(result)


def _json_value(value: Any) -> Any:
    """Make typed runtime facts safe for the MCP JSON boundary without mutation."""
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    return value


def main() -> None:
    """Run the formal Runtime Core server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
