"""Assemble the new Runtime Core around an already-connected Civ adapter.

This is an outer composition layer, not an MCP tool module.  It has one
purpose: bind the current game to an explicit branch token and construct the
objects that own execution, context, and domain mutation construction.
"""

from __future__ import annotations

from dataclasses import dataclass

from civ_mcp.civ.adapter import CivAdapter
from civ_mcp.civ.mutations import CivMutationFactory
from civ_mcp.runtime.context import ContextBuilder
from civ_mcp.runtime.contracts import BranchIdentity
from civ_mcp.runtime.mcp_surface import RuntimeMcpSurface
from civ_mcp.runtime.session import SessionBinding, SessionKernel
from civ_mcp.runtime.store import OperationStore
from civ_mcp.runtime.turn import TurnLoop


@dataclass(frozen=True, slots=True)
class RuntimeAssembly:
    """The bound Runtime Core components for exactly one game branch."""

    binding: SessionBinding
    adapter: CivAdapter
    store: OperationStore
    session: SessionKernel
    context: ContextBuilder
    mutations: CivMutationFactory
    turn_loop: TurnLoop
    surface: RuntimeMcpSurface


async def assemble_runtime(
    adapter: CivAdapter,
    store: OperationStore,
    *,
    branch_token: str,
) -> RuntimeAssembly:
    """Bind one explicit game branch without starting legacy services.

    ``branch_token`` must be provided by the host from its stable save/load
    boundary.  The Runtime never guesses that a newly loaded timeline belongs
    to a previous branch.
    """
    if not branch_token.strip():
        raise ValueError("branch_token 不能为空；host 必须显式标识当前存档分支。")

    async def identity_probe():
        return (await adapter.read_game_identity()).value

    async def turn_probe() -> int:
        return (await adapter.read_overview()).value.turn

    session = SessionKernel(
        adapter,
        store,
        identity_probe=identity_probe,
        turn_probe=turn_probe,
    )
    game_id = await identity_probe()
    branch_id = BranchIdentity(game_id, f"{game_id.value}:{branch_token}")
    binding = await session.bind(game_id, branch_id)
    context = ContextBuilder(adapter, session)
    turn_loop = TurnLoop(session)
    return RuntimeAssembly(
        binding=binding,
        adapter=adapter,
        store=store,
        session=session,
        context=context,
        mutations=CivMutationFactory(adapter),
        turn_loop=turn_loop,
        surface=RuntimeMcpSurface(
            context=context,
            session=session,
            turn_loop=turn_loop,
        ),
    )
