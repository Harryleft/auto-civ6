"""Experimental FastMCP entry point for the isolated Runtime Core.

This is intentionally separate from the production ``civ-mcp`` command until
the K1 live-game and recovery gates are met.  Its tools call RuntimeAssembly
only; neither a model-visible tool nor this module's normal tool path reaches
the FireTuner transport directly.
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
    """Resources owned for one experimental Runtime MCP process."""

    assembly: RuntimeAssembly
    connection: RuntimeConnection


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[RuntimeAppContext]:
    """Open and bind the new Runtime Core without legacy background services."""
    branch_token = os.environ.get(RUNTIME_BRANCH_ENV, "").strip()
    if not branch_token:
        raise RuntimeServerConfigurationError(
            f"{RUNTIME_BRANCH_ENV} 必须指向当前存档的稳定分支标识。"
        )
    store_path = Path(
        os.environ.get(
            RUNTIME_STORE_ENV,
            str(Path.home() / ".civ6-mcp" / "runtime" / "operations.sqlite3"),
        )
    )
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
    "Civilization VI Runtime Core (experimental)",
    instructions=(
        "This experimental surface exposes only the isolated Runtime Core. "
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
async def get_unit_promotions(ctx: Context, unit_index: int) -> dict[str, object]:
    """Return current legal and already-owned promotions for one unit."""
    return _json_value(
        await _runtime(ctx).assembly.surface.get_unit_promotions(unit_index)
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
    """Run the experimental server over stdio; it is not the civ-mcp default."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
