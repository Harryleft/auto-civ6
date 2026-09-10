"""Utility and lifecycle tools: popup dismissal, run_lua, saves, launch/kill."""

import asyncio
import logging

from mcp.server.fastmcp import Context

log = logging.getLogger(__name__)

from civ_mcp import game_launcher
from civ_mcp.connection import LuaError
from civ_mcp.server import pipeline
from civ_mcp.server.assembly import mcp

# ---------------------------------------------------------------------------
# Utility tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def dismiss_popup(ctx: Context) -> str:
    """Dismiss any blocking popup in the game UI.

    Call this if you suspect a popup (e.g. historic moment, boost notification)
    is blocking interaction.
    """
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(ctx, "dismiss_popup", {}, gs.dismiss_popup)


@mcp.tool(annotations={"destructiveHint": True})
async def run_lua(ctx: Context, code: str, context: str = "gamecore") -> str:
    """Run arbitrary Lua code in the game. Advanced escape hatch — prefer built-in tools.

    Args:
        code: Lua code to execute. Use print() for output, end with print("---END---").
        context: "gamecore" (default) for read-only state queries.
                 "ingame" for commands and UI-dependent queries.

    Context differences:
      gamecore: Players[], GameInfo.*, Map.*, Game.* — safe read-only access.
                CANNOT use: UI.*, UnitManager.*, CityManager.*, notifications.
      ingame:   All APIs including UI.*, UnitManager.*, CityManager.*.
                Use for: moving units, setting research, diplomacy actions.

    Always use print() for output (not return).
    """
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
        ctx,
        "run_lua",
        {"code": code, "context": context},
        lambda: gs.execute_lua(code, context),
    )


# ---------------------------------------------------------------------------
# Save / Load
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def list_saves(ctx: Context) -> str:
    """List available save files (normal, autosave).

    Returns indexed list of saves. Use load_save(save_index=N) to load one.
    Call this before load_save to see what's available.
    """
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(ctx, "list_saves", {}, gs.list_saves)


@mcp.tool(annotations={"destructiveHint": True})
async def load_save(ctx: Context, save_index: int) -> str:
    """Load a save file by index from the most recent list_saves() result.

    Args:
        save_index: Index number from list_saves output (1-based)

    The game will reload entirely. Wait ~10 seconds after calling this,
    then use get_game_overview to verify the loaded state.
    """
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
        ctx, "load_save", {"save_index": save_index}, lambda: gs.load_save(save_index)
    )


@mcp.tool(annotations={"destructiveHint": True})
async def load_game_save(ctx: Context, save_name: str) -> str:
    """Load a save file by name. No need to call list_saves first.

    Args:
        save_name: Save name without extension (e.g. "0_MCP_0079",
                   "0A_GROUND_CONTROL", "AutoSave_0221", "quicksave").

    Tries Lua-based loading first (fast, ~5s). If the save isn't found
    via Lua (common for autosaves/quicksaves), restarts into Civ VI's
    FrontEnd API path after verifying the file exists on disk. OCR is only
    available with the explicit ``CIV_MCP_ENABLE_OCR_RECOVERY=1`` opt-in.
    """
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
        ctx,
        "load_game_save",
        {"save_name": save_name},
        lambda: gs.load_game_save(save_name),
    )


# ---------------------------------------------------------------------------
# Game Lifecycle (kill / launch / load from menu)
# ---------------------------------------------------------------------------
# These tools do NOT require a FireTuner connection — they manage the game
# process itself. Hardcoded to Civ 6 only (no arbitrary system commands).


@mcp.tool(annotations={"destructiveHint": True})
async def kill_game(ctx: Context) -> str:
    """Kill the Civ 6 game process and wait for Steam to deregister.

    Only kills Civ 6 processes. Waits ~10 seconds for Steam to deregister
    so the game can be relaunched cleanly.
    """
    return await game_launcher.kill_game()


@mcp.tool(annotations={"destructiveHint": True})
async def launch_game(ctx: Context) -> str:
    """Launch Civ 6 via Steam.

    Starts the game and waits for the process to appear (~15-30 seconds).
    The game will be at the main menu after launch — use load_save or
    restart_and_load to load a specific save.

    NOTE: FireTuner connection is NOT available at the main menu.
    Only in-game MCP tools work after a save is loaded.
    """
    return await game_launcher.launch_game()


@mcp.tool(annotations={"destructiveHint": True})
async def load_save_from_menu(ctx: Context, save_name: str | None = None) -> str:
    """Load a save while at the main menu, preferring the FrontEnd API.

    Args:
        save_name: Autosave name (e.g. "AutoSave_0221"). If not provided,
                   loads the most recent autosave.

    Requires the game to be running and at the main menu. Civ VI's native
    FrontEnd API performs the load and built-in Continue action. OCR is only
    an explicit fallback via ``CIV_MCP_ENABLE_OCR_RECOVERY=1``.

    After loading, wait ~10 seconds then call get_game_overview to verify.

    Requires pyobjc: uv pip install 'civ6-mcp[launcher]'
    """
    gs = pipeline._get_game(ctx)
    from civ_mcp.game_lifecycle import load_game_save

    if save_name is None:
        save_name = game_launcher.get_latest_autosave()
        if save_name is None:
            return "No autosaves found in save directory."
    return await pipeline._logged(
        ctx,
        "load_save_from_menu",
        {"save_name": save_name},
        lambda: load_game_save(gs.conn, save_name),
    )


@mcp.tool(annotations={"destructiveHint": True})
async def restart_and_load(ctx: Context, save_name: str | None = None) -> str:
    """Full game recovery: kill, relaunch, and load a save.

    Args:
        save_name: Autosave name (e.g. "AutoSave_0221"). If not provided,
                   loads the most recent autosave.

    This is the recommended tool for recovering from game hangs (e.g. AI turn
    processing stuck in infinite loop). Takes 60-120 seconds total:
    1. Kills the game process
    2. Waits for Steam to deregister (~10s)
    3. Relaunches via Steam (~15-30s for process start + main menu)
    4. Uses Civ VI FrontEnd API to load and confirm the save (~30-60s)

    After completion, wait ~10 seconds then call get_game_overview to verify.
    """
    gs = pipeline._get_game(ctx)
    identity_before = gs._game_identity

    result = await game_launcher.restart_and_load(save_name, conn=gs.conn)
    # Loading a save rewinds the world; mark the branch boundary so the
    # authorizations recorded for the abandoned future stop being consumable.
    await pipeline._record_game_reload_epoch(
        ctx,
        reason="restart_and_load_tool",
        turn=pipeline._get_logger(ctx)._turn,
        details={"save": save_name},
    )

    # Reconnect and verify correct game loaded
    conn = gs.conn
    for attempt in range(30):
        try:
            await conn.reconnect()
            if conn.gamecore_index is not None:
                break
        except ConnectionError:
            pass
        await asyncio.sleep(1)

    if conn.gamecore_index is not None and identity_before is not None:
        try:
            actual = await gs.get_game_identity()
            if actual != identity_before:
                log.warning(
                    "restart_and_load: wrong game loaded "
                    "(expected %s, got %s) — retrying",
                    identity_before,
                    actual,
                )
                result2 = await game_launcher.restart_and_load(save_name, conn=gs.conn)
                await pipeline._record_game_reload_epoch(
                    ctx,
                    reason="restart_and_load_tool_retry",
                    turn=pipeline._get_logger(ctx)._turn,
                    details={"save": save_name},
                )
                for attempt in range(30):
                    try:
                        await conn.reconnect()
                        if conn.gamecore_index is not None:
                            break
                    except ConnectionError:
                        pass
                    await asyncio.sleep(1)
                try:
                    actual2 = await gs.get_game_identity()
                    if actual2 != identity_before:
                        return (
                            f"{result2} | WARNING: Wrong game loaded "
                            f"(expected {identity_before[0]}, "
                            f"got {actual2[0]}). Manual recovery needed."
                        )
                except Exception:
                    pass
                return f"{result2} | Reloaded after wrong-game detection."
        except Exception:
            log.debug("Post-load identity check failed", exc_info=True)

    return result
