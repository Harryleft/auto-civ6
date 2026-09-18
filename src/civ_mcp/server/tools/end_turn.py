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
    """End the current turn.

    Move all units, set production, and choose research before calling this.

    The five reflection fields are the per-turn diary. In the legacy play
    profile all five must be non-empty; in the lean play profile they are
    optional and are recorded exactly as given, never filled in on your behalf:
        tactical: What happened this turn — combat, movements, improvements.
        strategic: Current standing vs rivals — yields, city count, victory path.
        tooling: Tool issues or observations.
        planning: Concrete actions for the next 5-10 turns.
        hypothesis: Predictions — enemy behavior, resource needs, timelines.

    Reflections are recorded BEFORE the AI plays, so anything that surfaces in
    the result belongs to the next turn's diary. If end_turn is blocked and you
    call it again after resolving the blocker, the first call's entry is kept —
    do not repeat the reflections.

    If the result reports an unknown outcome, verify it with get_game_overview
    and do not repeat any action that may already have been sent.
    """

    return await run_end_turn(
        ctx,
        tactical=tactical,
        strategic=strategic,
        tooling=tooling,
        planning=planning,
        hypothesis=hypothesis,
    )
