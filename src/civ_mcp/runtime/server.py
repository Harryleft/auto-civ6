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
