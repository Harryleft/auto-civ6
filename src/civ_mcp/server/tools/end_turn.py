"""MCP registration facade for the end-turn orchestration flow."""

from mcp.server.fastmcp import Context

from civ_mcp.server.assembly import mcp
from civ_mcp.server.tools.end_turn_flow import run_end_turn


@mcp.tool(annotations={"destructiveHint": True})
async def end_turn(
    ctx: Context,
    tactical: str = "",
    strategic: str = "",
    tooling: str = "",
    planning: str = "",
    hypothesis: str = "",
) -> str:
    """End the current turn through the centralized orchestration flow."""

    return await run_end_turn(
        ctx,
        tactical=tactical,
        strategic=strategic,
        tooling=tooling,
        planning=planning,
        hypothesis=hypothesis,
    )
