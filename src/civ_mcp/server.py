"""本机 Civ6 MCP：游戏工具、单一连接和本地动作日志。"""

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from mcp.server.fastmcp import Context, FastMCP
from civ_mcp import game_launcher, game_lifecycle, checkpoints
from civ_mcp import lua as lq
from civ_mcp import narrate as nr
from civ_mcp.connection import GameConnection, LuaError
from civ_mcp.game_state import GameState
from civ_mcp.logger import GameLogger

log = logging.getLogger(__name__)
LOAD_TIMEOUT = 180.0


@dataclass
class AppContext:
    game: GameState
    logger: GameLogger
    # 一次工具可能包含检查、执行、回读；整体串行，防止并发工具插入。
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    conn = GameConnection()
    try:
        yield AppContext(game=GameState(conn), logger=GameLogger())
    finally:
        await conn.disconnect()


mcp = FastMCP(
    "Civilization VI",
    instructions="Read real game state before issuing actions. Call get_game_overview first. A lost response may mean an action already ran; verify before retrying. Recovery is explicit.",
    lifespan=lifespan,
)


def _get_game(ctx: Context) -> GameState:
    return ctx.request_context.lifespan_context.game


def _param_summary(params: dict[str, Any]) -> str:
    """Compact one-line summary of tool params for console logging."""
    if not params:
        return ""
    parts = []
    for k, v in params.items():
        s = str(v)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s}")
    return " ".join(parts)


def _result_summary(result: str) -> str:
    """First meaningful line of a result, truncated."""
    line = result.split("\n", 1)[0].strip()
    return line[:120] + "..." if len(line) > 120 else line


async def _logged(
    ctx: Context,
    tool_name: str,
    params: dict[str, Any],
    fn: Callable[[], Awaitable[str]],
) -> str:
    """整体串行调用并记录结果；连接异常不会触发重启或重放。"""
    app = ctx.request_context.lifespan_context
    async with app.lock:
        start = time.monotonic()
        try:
            result = await fn()
        except (LuaError, ValueError, OSError) as exc:
            result = f"Error: {exc}"
        ms = int((time.monotonic() - start) * 1000)
        log.info("%s(%s) %dms: %s", tool_name, _param_summary(params), ms, _result_summary(result))
        try:
            await app.logger.log_tool_call(tool_name, params, result, ms)
        except OSError:
            log.warning("Local action log could not be written", exc_info=True)
        return result


# ---------------------------------------------------------------------------
# Query tools (read-only)
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_game_overview(ctx: Context) -> str:
    """Get current turn, civilization, yields, research, cities, units and game outcome."""
    gs = _get_game(ctx)

    async def _run():
        await gs.get_game_identity()
        ov = await gs.get_game_overview()
        if gs._revealed_tiles is None:
            try:
                lines = await gs.conn.execute_read(lq.build_revealed_tiles_seed_query())
                gs._revealed_tiles = lq.parse_revealed_tiles_seed(lines)
            except (LuaError, OSError):
                log.debug("Revealed tile baseline unavailable", exc_info=True)
        text = nr.narrate_overview(ov)
        gameover = await gs.check_game_over()
        if gameover is not None:
            text += f"\n\nGAME OVER: {'DEFEAT' if gameover.is_defeat else 'VICTORY'} — {gameover.winner_name}, {gameover.victory_type}"
        return text

    return await _logged(ctx, "get_game_overview", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_units(ctx: Context) -> str:
    """List all your units with position, type, movement, and health.

    Each unit shows its id and idx (needed for action commands).
    Consumed units (e.g. settlers that founded cities) are excluded.
    """
    gs = _get_game(ctx)

    async def _run():
        units = await gs.get_units()
        try:
            threats = await gs.get_threat_scan()
        except Exception:
            threats = None
        trade_status = None
        try:
            trade_status = await gs.get_trade_routes()
        except Exception:
            pass
        return nr.narrate_units(units, threats, trade_status)

    return await _logged(ctx, "get_units", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_spies(ctx: Context) -> str:
    """List all your spy units with position, rank, city, and available missions.

    Shows each spy's composite id (needed for spy_action), current location,
    rank (Recruit/Agent/Special Agent/Senior Agent), XP, and which operations
    are available at their current position.

    Note: offensive missions only become available once the spy has physically
    arrived in the target city. Use spy_action with action='travel' first.
    """
    gs = _get_game(ctx)

    async def _run():
        spies = await gs.get_spies()
        return nr.narrate_spies(spies)

    return await _logged(ctx, "get_spies", {}, _run)


@mcp.tool()
async def spy_action(
    ctx: Context,
    unit_id: int,
    action: str,
    target_x: int,
    target_y: int,
) -> str:
    """Send a spy to a city, launch a mission, or choose an explicit escape route.

    Args:
        unit_id: The spy's composite ID (from get_spies output)
        action: 'travel' to move spy to a city, or a mission type to launch a mission.
            For a caught spy, use 'escape:DISTRICT_AERODROME',
            'escape:DISTRICT_HARBOR', 'escape:DISTRICT_COMMERCIAL_HUB', or
            'escape:DISTRICT_CITY_CENTER'. No route is chosen automatically.
            Mission types: COUNTERSPY, GAIN_SOURCES, SIPHON_FUNDS, STEAL_TECH_BOOST,
            SABOTAGE_PRODUCTION, GREAT_WORK_HEIST, RECRUIT_PARTISANS,
            NEUTRALIZE_GOVERNOR, FABRICATE_SCANDAL
        target_x: X coordinate of the target city tile; ignored for escape
        target_y: Y coordinate of the target city tile; ignored for escape

    Travel notes:
        - Valid targets: your own cities and city-states only.
        - Allied civ cities are NOT valid travel targets.
        - Travel is queued end-of-turn; spy position updates after turn ends.

    Mission notes:
        - Spy must be physically IN the target city to launch any offensive mission.
        - Use 'travel' first, then end the turn, then launch the mission.
        - COUNTERSPY defends your own city (spy must be in your city).
        - get_spies shows which ops are available at the spy's current location.
    """
    gs = _get_game(ctx)
    unit_index = unit_id % 65536
    params = {
        "unit_id": unit_id,
        "action": action,
        "target_x": target_x,
        "target_y": target_y,
    }

    async def _run():
        if action.lower().startswith("escape:"):
            return await gs.spy_escape(unit_index, action.split(":", 1)[1].strip().upper())
        if action.lower() == "travel":
            return await gs.spy_travel(unit_index, target_x, target_y)
        return await gs.spy_mission(unit_index, action.upper(), target_x, target_y)

    result = await _logged(ctx, "spy_action", params, _run)
    return result


@mcp.tool(annotations={"readOnlyHint": True})
async def get_cities(ctx: Context) -> str:
    """List all your cities with yields, population, production, growth, and loyalty.

    Each city shows its id (needed for production commands).
    Cities losing loyalty show warnings with flip timers.
    """
    gs = _get_game(ctx)

    async def _run():
        cities, distances = await gs.get_cities()
        return nr.narrate_cities(cities, distances)

    return await _logged(ctx, "get_cities", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_city_production(ctx: Context, city_id: int) -> str:
    """List what a city can produce right now.

    Args:
        city_id: City ID (from get_cities output)

    Returns available units, buildings, and districts with production costs.
    Call this when a city finishes building or to decide what to produce next.
    """
    gs = _get_game(ctx)

    async def _run():
        options = await gs.list_city_production(city_id)
        return nr.narrate_city_production(options)

    return await _logged(ctx, "get_city_production", {"city_id": city_id}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_map_area(
    ctx: Context, center_x: int, center_y: int, radius: int = 2
) -> str:
    """Get terrain info for tiles around a point.

    Args:
        center_x: X coordinate of center tile
        center_y: Y coordinate of center tile
        radius: How many tiles out from center (default 2, max 4)
    """
    radius = max(1, min(radius, 4))
    gs = _get_game(ctx)

    async def _run():
        tiles = await gs.get_map_area(center_x, center_y, radius)
        return nr.narrate_map(tiles)

    result = await _logged(
        ctx,
        "get_map_area",
        {"center_x": center_x, "center_y": center_y, "radius": radius},
        _run,
    )
    return result


@mcp.tool(annotations={"readOnlyHint": True})
async def get_settle_advisor(ctx: Context, unit_id: int) -> str:
    """List best settle locations near a settler unit.

    Args:
        unit_id: The settler's composite ID (from get_units output)

    Scores locations by yields, water, defense, and resource value.
    Returns top 5 candidates sorted by score.
    """
    gs = _get_game(ctx)
    unit_index = unit_id % 65536
    return await _logged(
        ctx,
        "get_settle_advisor",
        {"unit_id": unit_id},
        lambda: gs.get_settle_advisor(unit_index),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_pathing_estimate(
    ctx: Context, unit_id: int, target_x: int, target_y: int
) -> str:
    """Estimate how many turns a unit needs to reach a destination.

    Args:
        unit_id: The unit's composite ID (from get_units output)
        target_x: Destination X coordinate
        target_y: Destination Y coordinate

    Returns estimated turns, path length, and reachable tiles this turn.
    """
    gs = _get_game(ctx)
    unit_index = unit_id % 65536

    async def _run():
        est = await gs.get_pathing_estimate(unit_index, target_x, target_y)
        return nr.narrate_pathing_estimate(est)

    return await _logged(
        ctx,
        "get_pathing_estimate",
        {"unit_id": unit_id, "target_x": target_x, "target_y": target_y},
        _run,
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_global_settle_advisor(ctx: Context) -> str:
    """Find the best settle locations across the entire revealed map.

    Unlike get_settle_advisor (which searches near a specific settler),
    this scans all revealed land for the top 10 settle candidates.
    Use this when deciding WHERE to send a settler, not just where to settle.
    """
    gs = _get_game(ctx)

    async def _run():
        candidates = await gs.get_global_settle_scan()
        if not candidates:
            return "No valid settle locations found on revealed map."
        return nr.narrate_settle_candidates(candidates)

    return await _logged(ctx, "get_global_settle_advisor", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_builder_tasks(ctx: Context) -> str:
    """Get a prioritized task board for all your builders.

    Scans your territory for tiles needing improvements and matches them
    with idle builders. Like the builder lens in the UI — shows what to
    build where and which builder is closest.

    Priority tiers:
    - URGENT: Pillaged improvements (yield loss), unimproved strategic resources
    - HIGH: Unimproved luxury/bonus resources
    - NORMAL: Empty tiles that could benefit from farms/mines/lumber mills

    Call this before issuing builder orders each turn.
    """
    gs = _get_game(ctx)

    async def _run():
        tasks, builders = await gs.get_builder_tasks()
        return nr.narrate_builder_tasks(tasks, builders)

    return await _logged(ctx, "get_builder_tasks", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_empire_resources(ctx: Context) -> str:
    """Get a summary of all resources in and near your empire.

    Shows owned resources (improved/unimproved) grouped by type,
    and unclaimed resources near your cities.
    """
    gs = _get_game(ctx)

    async def _run():
        stockpiles, owned, nearby, luxuries = await gs.get_empire_resources()
        return nr.narrate_empire_resources(stockpiles, owned, nearby, luxuries)

    return await _logged(ctx, "get_empire_resources", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_strategic_map(ctx: Context) -> str:
    """Get fog-of-war boundaries and unclaimed resources across the map.

    Shows how far explored territory extends from each city (in 6 directions),
    highlighting directions that need exploration. Also lists unclaimed luxury
    and strategic resources on revealed but unowned land.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_strategic_map",
        {},
        lambda: _narrate(gs.get_strategic_map, nr.narrate_strategic_map),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_diplomacy(ctx: Context) -> str:
    """Get diplomatic status with all known civilizations.

    Shows diplomatic state (Friendly/Neutral/Unfriendly), relationship modifiers
    with scores and reasons, grievances, delegations/embassies, and available
    diplomatic actions you can take. Also shows visible enemy city details
    (name, population, loyalty, walls).
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_diplomacy",
        {},
        lambda: _narrate(gs.get_diplomacy, nr.narrate_diplomacy),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_tech_civics(ctx: Context) -> str:
    """Get technology and civic research status.

    Shows current research, current civic, turns remaining,
    and lists of available technologies and civics to choose from.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_tech_civics",
        {},
        lambda: _narrate(gs.get_tech_civics, nr.narrate_tech_civics),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_pending_trades(ctx: Context) -> str:
    """Check for pending trade deal offers from other civilizations.

    Shows what each civ is offering and what they want in return.
    Use respond_to_trade to accept or reject.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_pending_trades",
        {},
        lambda: _narrate(gs.get_pending_deals, nr.narrate_pending_deals),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_policies(ctx: Context) -> str:
    """Get current government, policy slots, and available policies.

    Shows current government type, each policy slot with its type and current
    policy (if any), and all unlocked policies grouped by compatible slot type.
    Wildcard slots accept any policy type.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx, "get_policies", {}, lambda: _narrate(gs.get_policies, nr.narrate_policies)
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_notifications(ctx: Context) -> str:
    """Get all active game notifications.

    Shows action-required items (need your decision) and informational
    notifications. Action-required items include which MCP tool to use
    to resolve them. Call this to check what needs attention without
    ending the turn.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_notifications",
        {},
        lambda: _narrate(gs.get_notifications, nr.narrate_notifications),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_pending_diplomacy(ctx: Context) -> str:
    """Check for pending diplomacy encounters (e.g. first meeting with a civ).

    Diplomacy encounters block turn progression. Call this if end_turn
    reports the turn didn't advance. Returns any open sessions with their
    dialogue text, visible buttons, and response guidance.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_pending_diplomacy",
        {},
        lambda: _narrate(gs.get_diplomacy_sessions, nr.narrate_diplomacy_sessions),
    )


# ---------------------------------------------------------------------------
# Action tools (mutating)
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_governors(ctx: Context) -> str:
    """Get governor status, appointed governors, and available types.

    Shows governor points, currently appointed governors with assignments,
    and governors available to appoint. Use appoint_governor to appoint one.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_governors",
        {},
        lambda: _narrate(gs.get_governors, nr.narrate_governors),
    )


@mcp.tool()
async def appoint_governor(ctx: Context, governor_type: str) -> str:
    """Appoint a new governor.

    Args:
        governor_type: e.g. GOVERNOR_THE_EDUCATOR (Pingala), GOVERNOR_THE_DEFENDER (Victor)

    Requires available governor points. Use get_governors to see options.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "appoint_governor",
        {"governor_type": governor_type},
        lambda: gs.appoint_governor(governor_type),
    )


@mcp.tool()
async def assign_governor(ctx: Context, governor_type: str, city_id: int) -> str:
    """Assign an appointed governor to a city.

    Args:
        governor_type: The governor type (from get_governors output)
        city_id: The city ID (from get_cities output)

    Governor must already be appointed. Takes several turns to establish.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "assign_governor",
        {"governor_type": governor_type, "city_id": city_id},
        lambda: gs.assign_governor(governor_type, city_id),
    )


@mcp.tool()
async def promote_governor(
    ctx: Context, governor_type: str, promotion_type: str
) -> str:
    """Promote a governor with a new ability.

    Args:
        governor_type: The governor type (from get_governors output)
        promotion_type: The promotion type (from get_governors output, shown under each governor)

    Requires available governor points. Use get_governors to see available promotions.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "promote_governor",
        {"governor_type": governor_type, "promotion_type": promotion_type},
        lambda: gs.promote_governor(governor_type, promotion_type),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_unit_promotions(ctx: Context, unit_id: int) -> str:
    """List available promotions for a unit.

    Args:
        unit_id: The unit's composite ID (from get_units output)

    Shows promotions filtered by the unit's promotion class.
    Only units with enough XP will have promotions available.
    """
    gs = _get_game(ctx)

    async def _run():
        status = await gs.get_unit_promotions(unit_id)
        return nr.narrate_unit_promotions(status)

    return await _logged(ctx, "get_unit_promotions", {"unit_id": unit_id}, _run)


@mcp.tool()
async def promote_unit(ctx: Context, unit_id: int, promotion_type: str) -> str:
    """Apply a promotion to a unit.

    Args:
        unit_id: The unit's composite ID (from get_units output)
        promotion_type: e.g. PROMOTION_BATTLECRY, PROMOTION_TORTOISE

    Use get_unit_promotions first to see available options.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "promote_unit",
        {"unit_id": unit_id, "promotion_type": promotion_type},
        lambda: gs.promote_unit(unit_id, promotion_type),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_city_states(ctx: Context) -> str:
    """List known city-states with envoy counts and types.

    Shows envoy tokens available, each city-state's type (Scientific,
    Industrial, etc.), how many envoys you've sent, and who is suzerain.
    Use send_envoy to send an envoy.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_city_states",
        {},
        lambda: _narrate(gs.get_city_states, nr.narrate_city_states),
    )


@mcp.tool()
async def send_envoy(ctx: Context, player_id: int) -> str:
    """Send an envoy to a city-state.

    Args:
        player_id: The city-state's player ID (from get_city_states)

    Requires available envoy tokens. Use get_city_states to see options.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx, "send_envoy", {"player_id": player_id}, lambda: gs.send_envoy(player_id)
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_pantheon_beliefs(ctx: Context) -> str:
    """Get pantheon status and available beliefs for selection.

    Shows current pantheon (if any), faith balance, and all available
    pantheon beliefs with their bonuses. Use choose_pantheon to found one.
    """
    gs = _get_game(ctx)

    async def _run():
        status = await gs.get_pantheon_status()
        return nr.narrate_pantheon_status(status)

    return await _logged(ctx, "get_pantheon_beliefs", {}, _run)


@mcp.tool()
async def choose_pantheon(ctx: Context, belief_type: str) -> str:
    """Found a pantheon with the specified belief.

    Args:
        belief_type: e.g. BELIEF_GOD_OF_THE_FORGE, BELIEF_DIVINE_SPARK

    Use get_pantheon_beliefs first to see options. Requires enough faith
    and no existing pantheon.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "choose_pantheon",
        {"belief_type": belief_type},
        lambda: gs.choose_pantheon(belief_type),
    )


@mcp.tool()
async def get_religion_beliefs(ctx: Context) -> str:
    """Get religion founding status, available religions, and available beliefs.

    Shows whether you've founded a religion, available religion types to choose,
    and beliefs grouped by class (Follower, Founder, Enhancer, Worship).
    Use found_religion to found a religion after your Great Prophet activates.
    """
    gs = _get_game(ctx)

    async def _run():
        status = await gs.get_religion_founding_status()
        return nr.narrate_religion_founding_status(status)

    return await _logged(ctx, "get_religion_beliefs", {}, _run)


@mcp.tool()
async def found_religion(
    ctx: Context, religion_type: str, follower_belief: str, founder_belief: str
) -> str:
    """Found a religion with a chosen name, follower belief, and founder belief.

    Args:
        religion_type: e.g. RELIGION_HINDUISM, RELIGION_BUDDHISM, RELIGION_ISLAM
        follower_belief: e.g. BELIEF_WORK_ETHIC, BELIEF_CHORAL_MUSIC
        founder_belief: e.g. BELIEF_STEWARDSHIP, BELIEF_CHURCH_PROPERTY

    Requires your Great Prophet to have already activated on a Holy Site
    (via UNITOPERATION_FOUND_RELIGION). Use get_religion_beliefs
    first to see available options.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "found_religion",
        {
            "religion_type": religion_type,
            "follower_belief": follower_belief,
            "founder_belief": founder_belief,
        },
        lambda: gs.found_religion(religion_type, follower_belief, founder_belief),
    )


@mcp.tool()
async def upgrade_unit(ctx: Context, unit_id: int) -> str:
    """Upgrade a unit to its next type (e.g. Slinger -> Archer).

    Args:
        unit_id: The unit's composite ID (from get_units output)

    Requires the right technology, enough gold, and the unit must have
    moves remaining. The unit's movement is consumed by upgrading.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx, "upgrade_unit", {"unit_id": unit_id}, lambda: gs.upgrade_unit(unit_id)
    )


@mcp.tool()
async def get_dedications(ctx: Context) -> str:
    """Get current era age, available dedications, and active ones.

    Shows era score thresholds, whether you're in a Golden/Dark/Normal age,
    and lists available dedication choices with their bonuses.
    Use choose_dedication to select one when required.
    """
    gs = _get_game(ctx)

    async def _run():
        status = await gs.get_dedications()
        return nr.narrate_dedications(status)

    return await _logged(ctx, "get_dedications", {}, _run)


@mcp.tool()
async def choose_dedication(ctx: Context, dedication_index: int) -> str:
    """Choose a dedication/commemoration for the current era.

    Args:
        dedication_index: The index of the dedication (from get_dedications output)

    Use get_dedications first to see available options and their bonuses.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "choose_dedication",
        {"dedication_index": dedication_index},
        lambda: gs.choose_dedication(dedication_index),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_trade_options(ctx: Context, other_player_id: int) -> str:
    """See what both sides can trade — like opening the trade screen.

    Args:
        other_player_id: The player ID (from get_diplomacy output)

    Shows gold, resources, favor, open borders status, and alliance eligibility
    for both you and the other civilization. Use before propose_trade to see
    what's available.
    """
    gs = _get_game(ctx)

    async def _run():
        opts = await gs.get_deal_options(other_player_id)
        return nr.narrate_deal_options(opts)

    return await _logged(
        ctx, "get_trade_options", {"other_player_id": other_player_id}, _run
    )


@mcp.tool()
async def respond_to_trade(ctx: Context, other_player_id: int, accept: bool) -> str:
    """Accept or reject a pending trade deal.

    Args:
        other_player_id: The player ID of the civilization (from get_pending_trades)
        accept: True to accept the deal, False to reject it

    Use get_pending_trades first to see what's being offered.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "respond_to_trade",
        {"other_player_id": other_player_id, "accept": accept},
        lambda: gs.respond_to_deal(other_player_id, accept),
    )


@mcp.tool()
async def propose_trade(
    ctx: Context,
    other_player_id: int,
    offer_gold: int = 0,
    offer_gold_per_turn: int = 0,
    offer_resources: str = "",
    offer_favor: int = 0,
    offer_open_borders: bool = False,
    request_gold: int = 0,
    request_gold_per_turn: int = 0,
    request_resources: str = "",
    request_favor: int = 0,
    request_open_borders: bool = False,
    joint_war_target: int = 0,
    mode: str = "send",
) -> str:
    """Propose a trade deal to another civilization.

    Args:
        other_player_id: The player ID (from get_diplomacy output)
        offer_gold: Lump sum gold to give them
        offer_gold_per_turn: Gold per turn to give them (30-turn duration)
        offer_resources: Comma-separated resource types to offer, e.g. "RESOURCE_SILK,RESOURCE_TEA"
        offer_favor: Diplomatic favor to offer
        offer_open_borders: True to offer our open borders
        request_gold: Lump sum gold to request from them
        request_gold_per_turn: Gold per turn to request (30-turn duration)
        request_resources: Comma-separated resource types to request
        request_favor: Diplomatic favor to request from them
        request_open_borders: True to request their open borders
        joint_war_target: Player ID of a third civ to declare joint war against
        mode: "send" to commit the deal, "test" to preview AI's counter-offer without committing

    Examples: Gift 100 gold: offer_gold=100. Trade silk for 3 gpt: offer_resources="RESOURCE_SILK", request_gold_per_turn=3.
    Mutual open borders: offer_open_borders=True, request_open_borders=True.
    Test a deal first: mode="test" to see what the AI thinks is fair, then mode="send" to commit.
    """
    gs = _get_game(ctx)

    offer_items: list[dict] = []
    request_items: list[dict] = []
    if offer_gold > 0:
        offer_items.append({"type": "GOLD", "amount": offer_gold, "duration": 0})
    if offer_gold_per_turn > 0:
        offer_items.append(
            {"type": "GOLD", "amount": offer_gold_per_turn, "duration": 30}
        )
    for res in (r.strip() for r in offer_resources.split(",") if r.strip()):
        offer_items.append(
            {"type": "RESOURCE", "name": res, "amount": 1, "duration": 30}
        )
    if offer_favor > 0:
        offer_items.append({"type": "FAVOR", "amount": offer_favor})
    if offer_open_borders:
        offer_items.append({"type": "AGREEMENT", "subtype": "OPEN_BORDERS"})
    if request_gold > 0:
        request_items.append({"type": "GOLD", "amount": request_gold, "duration": 0})
    if request_gold_per_turn > 0:
        request_items.append(
            {"type": "GOLD", "amount": request_gold_per_turn, "duration": 30}
        )
    for res in (r.strip() for r in request_resources.split(",") if r.strip()):
        request_items.append(
            {"type": "RESOURCE", "name": res, "amount": 1, "duration": 30}
        )
    if request_favor > 0:
        request_items.append({"type": "FAVOR", "amount": request_favor})
    if request_open_borders:
        request_items.append({"type": "AGREEMENT", "subtype": "OPEN_BORDERS"})
    if joint_war_target > 0:
        # Joint war is mutual — both sides commit
        offer_items.append({"type": "AGREEMENT", "subtype": "JOINT_WAR"})
        request_items.append({"type": "AGREEMENT", "subtype": "JOINT_WAR"})

    if not offer_items and not request_items:
        return "Error: must specify at least one offer or request item"

    if mode == "test":
        return await _logged(
            ctx,
            "test_trade",
            {
                "other_player_id": other_player_id,
                "offer_items": offer_items,
                "request_items": request_items,
            },
            lambda: gs.test_trade(other_player_id, offer_items, request_items),
        )

    return await _logged(
        ctx,
        "propose_trade",
        {
            "other_player_id": other_player_id,
            "offer_items": offer_items,
            "request_items": request_items,
        },
        lambda: gs.propose_trade(other_player_id, offer_items, request_items),
    )


@mcp.tool()
async def propose_peace(ctx: Context, other_player_id: int) -> str:
    """Propose white peace to a civilization you're at war with.

    Args:
        other_player_id: The player ID (from get_diplomacy output)

    Requires being at war and past the 10-turn war cooldown.
    The AI may accept or reject based on war score and relationship.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "propose_peace",
        {"other_player_id": other_player_id},
        lambda: gs.propose_peace(other_player_id),
    )


@mcp.tool()
async def set_policies(ctx: Context, assignments: str) -> str:
    """Set policy cards in government slots.

    Args:
        assignments: Comma-separated slot assignments, e.g.
            "0=POLICY_AGOGE,1=POLICY_URBAN_PLANNING"
            Slots not listed keep their current policy. Use NONE to
            explicitly clear a slot (e.g. "2=NONE"). Use get_policies to
            see available policies and slot indices.

    Wildcard slots can accept any policy type. Military slots accept
    military policies, economic slots accept economic policies, etc.
    """
    gs = _get_game(ctx)

    async def _run():
        parsed: dict[int, str] = {}
        for pair in assignments.split(","):
            pair = pair.strip()
            if "=" not in pair:
                continue
            idx_str, policy = pair.split("=", 1)
            parsed[int(idx_str.strip())] = policy.strip()
        if not parsed:
            return "Error: no valid assignments. Format: '0=POLICY_AGOGE,1=POLICY_URBAN_PLANNING'"
        return await gs.set_policies(parsed)

    return await _logged(ctx, "set_policies", {"assignments": assignments}, _run)


@mcp.tool()
async def respond_to_diplomacy(
    ctx: Context, other_player_id: int, response: str
) -> str:
    """Respond to a pending diplomacy encounter.

    Args:
        other_player_id: The player ID of the other civilization (from get_pending_diplomacy)
        response: "POSITIVE" (friendly) or "NEGATIVE" (dismissive)

    First meetings typically have 2-3 rounds. The tool automatically detects
    and closes goodbye-phase sessions (where dialogue text stops changing).
    If SESSION_CONTINUES is returned, send another response for the next round.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "respond_to_diplomacy",
        {"other_player_id": other_player_id, "response": response},
        lambda: gs.diplomacy_respond(other_player_id, response),
    )


@mcp.tool()
async def send_diplomatic_action(
    ctx: Context, other_player_id: int, action: str
) -> str:
    """Send a proactive diplomatic action to another civilization.

    Args:
        other_player_id: The player ID (from get_diplomacy output)
        action: One of: DIPLOMATIC_DELEGATION, DECLARE_FRIENDSHIP, DENOUNCE,
                RESIDENT_EMBASSY, OPEN_BORDERS,
                DECLARE_SURPRISE_WAR, DECLARE_FORMAL_WAR, DECLARE_HOLY_WAR,
                DECLARE_LIBERATION_WAR, DECLARE_RECONQUEST_WAR,
                DECLARE_PROTECTORATE_WAR, DECLARE_COLONIAL_WAR,
                DECLARE_TERRITORIAL_WAR

    Delegations cost 25 gold and can be rejected if the civ dislikes you.
    Embassies require Writing tech. Use get_diplomacy to see available actions.
    Surprise war is always available if not allied/friends. Other war types
    (casus belli) require specific civics and conditions.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "send_diplomatic_action",
        {"other_player_id": other_player_id, "action": action},
        lambda: gs.send_diplomatic_action(other_player_id, action),
    )


@mcp.tool()
async def form_alliance(
    ctx: Context, other_player_id: int, alliance_type: str = "MILITARY"
) -> str:
    """Form an alliance with another civilization.

    Args:
        other_player_id: The player ID (from get_diplomacy output)
        alliance_type: One of: MILITARY, RESEARCH, CULTURAL, ECONOMIC, RELIGIOUS

    Requires declared friendship and Diplomatic Service civic.
    Use get_trade_options to check alliance eligibility first.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "form_alliance",
        {"other_player_id": other_player_id, "alliance_type": alliance_type},
        lambda: gs.form_alliance(other_player_id, alliance_type.upper()),
    )


@mcp.tool()
async def city_action(
    ctx: Context,
    city_id: int,
    action: str,
    target_x: Optional[int] = None,
    target_y: Optional[int] = None,
) -> str:
    """Issue a command to a city.

    Args:
        city_id: City ID (from get_cities output)
        action: Currently supported: 'attack' (city ranged attack)
        target_x: Target X coordinate (required for attack)
        target_y: Target Y coordinate (required for attack)

    For attack: city must have walls and not have fired this turn.
    Range is 2 tiles from city center.

    For captured/disloyal city decisions (city_id is ignored, uses pending city):
    - 'keep': Keep the city (works for both captured and loyalty-flipped cities)
    - 'reject': Reject/free a disloyal city (loyalty flip only)
    - 'raze': Raze a captured city (military conquest only)
    - 'liberate_founder': Liberate to original founder
    - 'liberate_previous': Liberate to previous owner
    """
    gs = _get_game(ctx)
    match action:
        case "attack":
            if target_x is None or target_y is None:
                return "Error: attack requires target_x and target_y"
            result = await _logged(
                ctx,
                "city_attack",
                {"city_id": city_id, "x": target_x, "y": target_y},
                lambda: gs.city_attack(city_id, target_x, target_y),
            )
            return result
        case "keep" | "reject" | "raze" | "liberate_founder" | "liberate_previous":
            return await _logged(
                ctx,
                "resolve_city_capture",
                {"action": action},
                lambda: gs.resolve_city_capture(action),
            )
        case _:
            return f"Error: Unknown city action '{action}'. Available: attack, keep, reject, raze, liberate_founder, liberate_previous"


@mcp.tool()
async def unit_action(
    ctx: Context,
    unit_id: int,
    action: str,
    target_x: Optional[int] = None,
    target_y: Optional[int] = None,
    improvement: Optional[str] = None,
) -> str:
    """Issue a command to a unit.

    Args:
        unit_id: The unit's composite ID (from get_units output)
        action: One of: move, attack, fortify, skip, found_city, improve, repair, remove_improvement, remove_feature, build_route, automate, heal, alert, sleep, delete, trade_route, activate, sacrifice_charges, teleport, spread_religion
        target_x: Target X coordinate (required for move/attack/trade_route/teleport)
        target_y: Target Y coordinate (required for move/attack/trade_route/teleport)
        improvement: Improvement type for builders (required for improve), e.g.
            IMPROVEMENT_FARM, IMPROVEMENT_MINE, IMPROVEMENT_QUARRY,
            IMPROVEMENT_PLANTATION, IMPROVEMENT_CAMP, IMPROVEMENT_PASTURE,
            IMPROVEMENT_FISHING_BOATS, IMPROVEMENT_LUMBER_MILL

    For move/attack: provide target_x and target_y.
    For trade_route: provide target_x and target_y of destination city.
    For teleport: provide target_x and target_y of destination city. Traders only, must be idle (not on active route).
    For improve: provide improvement name. Builder must be on the tile.
    For repair: repairs a pillaged improvement on the builder's current tile. No improvement name needed.
    For remove_improvement: demolishes an intact improvement on the builder's current tile (e.g. to replace a farm with a mine). Costs one charge.
    For activate: activates a Great Person on their matching district.
    For sacrifice_charges: Royal Society builder sacrifice — spends ALL builder charges to boost a district project (2% of cost per charge). Builder must be on the district tile.
    For spread_religion: spreads religion at current tile. Missionaries/Apostles only.
    For build_route: builds road/railroad on current tile. Military Engineers only. No charges used; costs 1 Iron + 1 Coal per railroad tile.
    For fortify/skip/found_city/automate/heal/alert/sleep/delete: no target needed.
    heal = fortify until healed (auto-wake at full HP).
    alert = sleep but auto-wake when enemy enters sight range.
    delete = permanently disband the unit.
    """
    gs = _get_game(ctx)
    unit_index = unit_id % 65536
    params: dict[str, Any] = {"unit_id": unit_id, "action": action}
    if target_x is not None:
        params["target_x"] = target_x
    if target_y is not None:
        params["target_y"] = target_y
    if improvement:
        params["improvement"] = improvement

    async def _run():
        match action.lower():
            case "move":
                if target_x is None or target_y is None:
                    return "Error: move requires target_x and target_y"
                return await gs.move_unit(unit_index, target_x, target_y)
            case "attack":
                if target_x is None or target_y is None:
                    return "Error: attack requires target_x and target_y"
                return await gs.attack_unit(unit_index, target_x, target_y)
            case "fortify":
                return await gs.fortify_unit(unit_index)
            case "skip":
                return await gs.skip_unit(unit_index)
            case "found_city":
                return await gs.found_city(unit_index)
            case "improve":
                if not improvement:
                    return "Error: improve requires improvement name (e.g. IMPROVEMENT_FARM). To repair a pillaged improvement, use action='repair' instead."
                return await gs.improve_tile(unit_index, improvement)
            case "repair":
                return await gs.repair_improvement(unit_index)
            case "remove_improvement":
                return await gs.remove_improvement(unit_index)
            case "remove_feature":
                return await gs.remove_feature(unit_index)
            case "build_route":
                return await gs.build_route(unit_index)
            case "automate":
                return await gs.automate_explore(unit_index)
            case "heal":
                return await gs.heal_unit(unit_index)
            case "alert":
                return await gs.alert_unit(unit_index)
            case "sleep":
                return await gs.sleep_unit(unit_index)
            case "delete":
                return await gs.delete_unit(unit_index)
            case "trade_route":
                if target_x is None or target_y is None:
                    return "Error: trade_route requires target_x and target_y of destination city"
                return await gs.make_trade_route(unit_index, target_x, target_y)
            case "activate":
                return await gs.activate_great_person(unit_index)
            case "sacrifice_charges":
                return await gs.sacrifice_builder_charges(unit_index)
            case "spread_religion":
                return await gs.spread_religion(unit_index)
            case "teleport":
                if target_x is None or target_y is None:
                    return "Error: teleport requires target_x and target_y of the destination city"
                return await gs.teleport_to_city(unit_index, target_x, target_y)
            case _:
                return f"Error: Unknown action '{action}'. Valid: move, attack, fortify, skip, found_city, improve, repair, remove_improvement, remove_feature, build_route, automate, heal, alert, sleep, delete, trade_route, activate, sacrifice_charges, teleport, spread_religion"

    result = await _logged(ctx, "unit_action", params, _run)
    return result


@mcp.tool()
async def skip_remaining_units(ctx: Context) -> str:
    """Skip all units that still have moves remaining.

    Useful after diplomacy encounters invalidate all standing orders.
    Uses GameCore FinishMoves on each unit — fast, reliable, no async issues.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx, "skip_remaining_units", {}, lambda: gs.skip_remaining_units()
    )


@mcp.tool()
async def set_city_production(
    ctx: Context,
    city_id: int,
    item_type: str,
    item_name: str,
    target_x: int | None = None,
    target_y: int | None = None,
) -> str:
    """Set what a city should produce.

    Args:
        city_id: City ID (from get_cities output)
        item_type: UNIT, BUILDING, DISTRICT, or PROJECT
        item_name: e.g. UNIT_WARRIOR, BUILDING_MONUMENT, DISTRICT_CAMPUS, PROJECT_LAUNCH_EARTH_SATELLITE
        target_x: X coordinate for district/wonder placement (required for districts — use get_district_advisor to find best tile)
        target_y: Y coordinate for district/wonder placement

    Tip: call get_cities first to see your cities and their IDs.
    """
    gs = _get_game(ctx)
    params: dict = {"city_id": city_id, "item_type": item_type, "item_name": item_name}
    if target_x is not None:
        params["target_x"] = target_x
        params["target_y"] = target_y
    return await _logged(
        ctx,
        "set_city_production",
        params,
        lambda: gs.set_city_production(
            city_id, item_type, item_name, target_x, target_y
        ),
    )


@mcp.tool()
async def purchase_item(
    ctx: Context,
    city_id: int,
    item_type: str,
    item_name: str,
    yield_type: str = "YIELD_GOLD",
) -> str:
    """Purchase a unit or building instantly with gold or faith.

    Args:
        city_id: City ID (from get_cities output)
        item_type: UNIT or BUILDING
        item_name: e.g. UNIT_WARRIOR, BUILDING_MONUMENT
        yield_type: YIELD_GOLD (default) or YIELD_FAITH

    Costs gold/faith immediately. Use get_city_production to see what's available.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "purchase_item",
        {
            "city_id": city_id,
            "item_type": item_type,
            "item_name": item_name,
            "yield_type": yield_type,
        },
        lambda: gs.purchase_item(city_id, item_type, item_name, yield_type),
    )


@mcp.tool()
async def set_research(ctx: Context, tech_or_civic: str, category: str = "tech") -> str:
    """Choose a technology or civic to research.

    Args:
        tech_or_civic: The type name, e.g. TECH_POTTERY or CIVIC_CRAFTSMANSHIP
        category: "tech" or "civic" (default: tech)

    Tip: call get_tech_civics first to see available options.
    """
    gs = _get_game(ctx)

    async def _run():
        if category.lower() == "civic":
            return await gs.set_civic(tech_or_civic)
        return await gs.set_research(tech_or_civic)

    return await _logged(
        ctx,
        "set_research",
        {"tech_or_civic": tech_or_civic, "category": category},
        _run,
    )


@mcp.tool(annotations={"destructiveHint": True})
async def end_turn(ctx: Context) -> str:
    """Advance once and confirm the next turn. Resolve reported blockers first.

    If a response is lost or the AI takes too long, the request stays pending:
    a later call confirms it instead of sending another ACTION_ENDTURN.
    Use restart_and_load explicitly when recovery is required.
    """
    return await _logged(ctx, "end_turn", {}, _get_game(ctx).end_turn)


# ---------------------------------------------------------------------------
# Diary
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Trade routes
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_trade_routes(ctx: Context) -> str:
    """Get trade route capacity, active routes, and trader status.

    Shows how many routes are active vs capacity, and lists all trader
    units with their positions and whether they're idle or on a route.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "get_trade_routes",
        {},
        lambda: _narrate(gs.get_trade_routes, nr.narrate_trade_routes),
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_trade_destinations(ctx: Context, unit_id: int) -> str:
    """List valid trade route destinations for a trader unit.

    Args:
        unit_id: The trader's composite ID (from get_units output)

    Shows domestic and international destinations. Use unit_action
    with action='trade_route' and target_x/target_y to start a route.
    """
    gs = _get_game(ctx)
    unit_index = unit_id % 65536

    async def _run():
        dests = await gs.get_trade_destinations(unit_index)
        return nr.narrate_trade_destinations(dests)

    return await _logged(ctx, "get_trade_destinations", {"unit_id": unit_id}, _run)


# ---------------------------------------------------------------------------
# District advisor
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_district_advisor(ctx: Context, city_id: int, district_type: str) -> str:
    """Show best tiles to place a district with adjacency bonuses.

    Args:
        city_id: City ID (from get_cities)
        district_type: e.g. DISTRICT_CAMPUS, DISTRICT_HOLY_SITE, DISTRICT_INDUSTRIAL_ZONE

    Returns valid placement tiles ranked by adjacency bonus.
    Use set_city_production with target_x/target_y to build the district.
    """
    gs = _get_game(ctx)

    async def _run():
        result = await gs.get_district_advisor(city_id, district_type)
        if isinstance(result, str):
            return f"Error: {result}"  # propagate specific error reason
        narrated = nr.narrate_district_advisor(result, district_type)
        return narrated

    return await _logged(
        ctx,
        "get_district_advisor",
        {"city_id": city_id, "district_type": district_type},
        _run,
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_wonder_advisor(ctx: Context, city_id: int, wonder_name: str) -> str:
    """Show best tiles to place a wonder with displacement cost analysis.

    Args:
        city_id: City ID (from get_cities output)
        wonder_name: Wonder building type, e.g. BUILDING_CHICHEN_ITZA, BUILDING_ORSZAGHAZ

    Returns valid placement tiles ranked by displacement cost (lowest = best):
    tiles with no improvements or resources are preferred over productive tiles.
    Also shows terrain, feature, river/coastal status, and any resources/improvements
    that would be removed by placing the wonder there.
    Use set_city_production with target_x/target_y to build the wonder.
    """
    gs = _get_game(ctx)

    async def _run():
        placements = await gs.get_wonder_advisor(city_id, wonder_name)
        if isinstance(placements, str):
            return f"Error: {placements}"  # propagate placement error
        narrated = nr.narrate_wonder_advisor(placements, wonder_name)
        return narrated

    return await _logged(
        ctx,
        "get_wonder_advisor",
        {"city_id": city_id, "wonder_name": wonder_name},
        _run,
    )


# ---------------------------------------------------------------------------
# Tile purchase tools
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_purchasable_tiles(ctx: Context, city_id: int) -> str:
    """List tiles a city can purchase with gold.

    Args:
        city_id: City ID (from get_cities)

    Shows cost, terrain, and resources for each purchasable tile.
    Tiles with luxury/strategic resources are listed first.
    """
    gs = _get_game(ctx)

    async def _run():
        tiles = await gs.get_purchasable_tiles(city_id)
        return nr.narrate_purchasable_tiles(tiles)

    return await _logged(ctx, "get_purchasable_tiles", {"city_id": city_id}, _run)


@mcp.tool()
async def purchase_tile(ctx: Context, city_id: int, x: int, y: int) -> str:
    """Buy a tile for a city with gold.

    Args:
        city_id: City ID
        x: Tile X coordinate
        y: Tile Y coordinate

    Use get_purchasable_tiles first to see costs and options.
    """
    gs = _get_game(ctx)
    result = await _logged(
        ctx,
        "purchase_tile",
        {"city_id": city_id, "x": x, "y": y},
        lambda: gs.purchase_tile(city_id, x, y),
    )
    return result


# ---------------------------------------------------------------------------
# Government change
# ---------------------------------------------------------------------------


@mcp.tool()
async def change_government(ctx: Context, government_type: str) -> str:
    """Switch to a different government type.

    Args:
        government_type: e.g. GOVERNMENT_CLASSICAL_REPUBLIC, GOVERNMENT_OLIGARCHY

    Use get_policies to see current government. First switch after
    unlocking a new tier is free (no anarchy).
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "change_government",
        {"government_type": government_type},
        lambda: gs.change_government(government_type),
    )


# ---------------------------------------------------------------------------
# Great People
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_great_people(ctx: Context) -> str:
    """See available Great People and recruitment progress.

    Shows which Great People are available, their recruitment cost,
    and which civilization (if any) is recruiting them.
    """
    gs = _get_game(ctx)

    async def _run():
        gp = await gs.get_great_people()
        return nr.narrate_great_people(gp)

    return await _logged(ctx, "get_great_people", {}, _run)


@mcp.tool()
async def get_gp_advisor(ctx: Context, unit_index: int) -> str:
    """Show best cities to activate a Great Person, ranked by suitability.

    Args:
        unit_index: The Great Person unit's index (from get_units output).

    Lists all cities with the matching district (e.g., campuses for Great Scientists),
    showing which ones the GP can activate on, distance, city yield, and great work
    slot availability for cultural GPs.
    """
    gs = _get_game(ctx)

    async def _run():
        result = await gs.get_gp_advisor(unit_index)
        if result is None:
            return "Could not get GP advisor info. Is this a Great Person unit?"
        return nr.narrate_gp_advisor(result)

    return await _logged(ctx, "get_gp_advisor", {"unit": unit_index}, _run)


@mcp.tool()
async def recruit_great_person(ctx: Context, individual_id: int) -> str:
    """Recruit a Great Person using accumulated GP points.

    Args:
        individual_id: The individual's ID (from get_great_people output, shown after ability)

    Requires enough Great Person points for that class.
    The GP spawns in your capital. Use get_great_people to check [CAN RECRUIT] status.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "recruit_great_person",
        {"id": individual_id},
        lambda: gs.recruit_great_person(individual_id),
    )


@mcp.tool()
async def patronize_great_person(
    ctx: Context, individual_id: int, yield_type: str = "YIELD_GOLD"
) -> str:
    """Buy a Great Person instantly with gold or faith.

    Args:
        individual_id: The individual's ID (from get_great_people output)
        yield_type: YIELD_GOLD (default) or YIELD_FAITH

    Costs shown in get_great_people output under "Patronize:".
    Requires enough gold/faith to cover the cost.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "patronize_great_person",
        {"id": individual_id, "yield": yield_type},
        lambda: gs.patronize_great_person(individual_id, yield_type),
    )


@mcp.tool()
async def reject_great_person(ctx: Context, individual_id: int) -> str:
    """Pass on a Great Person (skip to the next one in that class).

    Args:
        individual_id: The individual's ID (from get_great_people output)

    Costs faith. The next Great Person in that class becomes available.
    Use when you don't want the current GP and want to save points for a better one.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "reject_great_person",
        {"id": individual_id},
        lambda: gs.reject_great_person(individual_id),
    )


# ---------------------------------------------------------------------------
# World Congress
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_world_congress(ctx: Context) -> str:
    """Get World Congress status, active resolutions, and voting options.

    Shows whether congress is in session, resolutions to vote on (with options A/B
    and possible targets), turns until next session, and your diplomatic favor.
    When in session, use queue_wc_votes to register votes before end_turn.
    """
    gs = _get_game(ctx)

    async def _run():
        status = await gs.get_world_congress()
        return nr.narrate_world_congress(status)

    return await _logged(ctx, "get_world_congress", {}, _run)


@mcp.tool()
async def queue_wc_votes(ctx: Context, votes: str) -> str:
    """Pre-configure World Congress votes for the upcoming session.

    Args:
        votes: JSON array of vote objects, e.g.
            '[{"hash": -513644209, "option": 1, "target": 2, "votes": 5}]'
            hash = resolution type hash (from get_world_congress)
            option = 1 for A, 2 for B
            target = player ID for PlayerType resolutions (from get_world_congress
                     target list, e.g. [target=2] Portugal); for other resolutions,
                     use the shown 0-based target index or a raw target string.
            votes = max votes to allocate (will use as many as favor allows)

    Call this BEFORE end_turn when get_world_congress shows 0 turns until next
    session. Registers an event handler that fires during WC processing and
    casts your votes with the specified preferences. Provide all four fields
    for every resolution; missing choices never receive default votes.
    If already in session, the specified votes are submitted immediately so
    the original pending end-turn can continue without another request.

    If you don't call this, end_turn will pause at the World Congress session
    and return control to you for interactive voting.
    """
    gs = _get_game(ctx)

    async def _run():
        vote_list = json.loads(votes)
        return await gs.queue_wc_votes(vote_list)

    return await _logged(ctx, "queue_wc_votes", {"votes": votes}, _run)


# ---------------------------------------------------------------------------
# Victory progress
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_victory_progress(ctx: Context) -> str:
    """Get victory condition progress for all civilizations.

    Shows progress toward Science, Domination, Culture, Religious,
    Diplomatic, and Score victories. Includes space race VP, diplomatic VP,
    tourism vs domestic tourists, religion spread, capital ownership,
    and military strength. Call every 20-30 turns to track the race.
    """
    gs = _get_game(ctx)

    async def _run():
        vp = await gs.get_victory_progress()
        return nr.narrate_victory_progress(vp)

    return await _logged(ctx, "get_victory_progress", {}, _run)


# ---------------------------------------------------------------------------
# Religion status
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_religion_spread(ctx: Context) -> str:
    """Get per-city religion breakdown across all visible cities.

    Shows which religion is majority in each city, follower counts,
    and which religions are closest to religious victory.
    """
    gs = _get_game(ctx)

    async def _run():
        rs = await gs.get_religion_status()
        return nr.narrate_religion_status(rs)

    return await _logged(ctx, "get_religion_spread", {}, _run)


# ---------------------------------------------------------------------------
# City yield focus
# ---------------------------------------------------------------------------


@mcp.tool()
async def set_city_focus(ctx: Context, city_id: int, focus: str) -> str:
    """Set a city's citizen yield priority.

    Args:
        city_id: City ID
        focus: One of: food, production, gold, science, culture, faith, default
               'default' clears all focus settings.

    Cities automatically assign citizens to tiles. This biases the AI
    toward the chosen yield type when assigning new citizens.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx,
        "set_city_focus",
        {"city_id": city_id, "focus": focus},
        lambda: gs.set_city_focus(city_id, focus),
    )


# ---------------------------------------------------------------------------
# Utility tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def dismiss_popup(ctx: Context) -> str:
    """Dismiss any blocking popup in the game UI.

    Call this if you suspect a popup (e.g. historic moment, boost notification)
    is blocking interaction.
    """
    gs = _get_game(ctx)
    return await _logged(ctx, "dismiss_popup", {}, gs.dismiss_popup)


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
    gs = _get_game(ctx)
    return await _logged(
        ctx, "run_lua", {"context": context}, lambda: gs.execute_lua(code, context)
    )


# ---------------------------------------------------------------------------
# Save / Load
# ---------------------------------------------------------------------------


@mcp.tool()
async def save_game(ctx: Context, save_name: str) -> str:
    """Save the active single-player game under a local filename.

    Confirms a fresh nonempty file, civilization, seed and unchanged turn.
    An existing filename is overwritten; use unique names for checkpoints.
    An unknown result is never resent automatically. Confirm pending end-turn
    results before saving, and explicitly name a confirmed save for recovery.
    """
    gs = _get_game(ctx)
    return await _logged(
        ctx, "save_game", {"save_name": save_name}, lambda: gs.save_game(save_name)
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def list_saves(ctx: Context) -> str:
    """List available save files (normal, autosave).

    Returns indexed list of saves. Use load_save(save_index=N) to load one.
    Call this before load_save to see what's available.
    """
    gs = _get_game(ctx)
    return await _logged(ctx, "list_saves", {}, gs.list_saves)


@mcp.tool(annotations={"destructiveHint": True})
async def load_save(ctx: Context, save_index: int) -> str:
    """Load a confirmed checkpoint from the latest filesystem listing, once.

    The same MainMenu, expected identity and readiness checks apply to named
    and index-based loads. Unknown legacy files cannot bypass those checks.
    """
    async def _run():
        names = game_lifecycle._filesystem_save_names
        if not 1 <= save_index <= len(names):
            return "Error: No matching cached save index; call list_saves or use load_game_save with an explicit confirmed name."
        return await _load_and_verify(_get_game(ctx), names[save_index - 1])
    return await _logged(ctx, "load_save", {"save_index": save_index}, _run)


@mcp.tool(annotations={"destructiveHint": True})
async def load_game_save(ctx: Context, save_name: str) -> str:
    """Load a named save once through native FrontEnd APIs, from the main menu.

    Confirms the loaded identity and turn. No OCR, restart or automatic resend.
    """
    return await _logged(ctx, "load_game_save", {"save_name": save_name}, lambda: _load_and_verify(_get_game(ctx), save_name))


# ---------------------------------------------------------------------------
# Game Lifecycle (kill / launch / load from menu)
# ---------------------------------------------------------------------------
# These tools do NOT require a FireTuner connection — they manage the game
# process itself. Hardcoded to Civ 6 only (no arbitrary system commands).


@mcp.tool(annotations={"destructiveHint": True})
async def kill_game(ctx: Context) -> str:
    """Explicitly stop Civ6 and release its FireTuner session."""
    gs = _get_game(ctx)
    async def _run():
        await gs.conn.disconnect()
        result = await game_launcher.kill_game()
        gs.reset_session()
        return result
    return await _logged(ctx, "kill_game", {}, _run)


@mcp.tool(annotations={"destructiveHint": True})
async def launch_game(ctx: Context) -> str:
    """Launch Civ6 through Steam; load a save explicitly from the main menu."""
    return await _logged(ctx, "launch_game", {}, game_launcher.launch_game)


@mcp.tool(annotations={"destructiveHint": True})
async def load_save_from_menu(ctx: Context, save_name: str | None = None) -> str:
    """Load a named save, or the latest autosave, through the native FrontEnd API."""
    name = save_name
    if not name:
        return "Error: Provide an explicit confirmed checkpoint save_name; autosave guessing is disabled."
    return await _logged(ctx, "load_save_from_menu", {"save_name": name}, lambda: _load_and_verify(_get_game(ctx), name))


@mcp.tool(annotations={"destructiveHint": True})
async def restart_and_load(ctx: Context, save_name: str | None = None) -> str:
    """Explicitly restart Civ6, load once, and verify the new session.

    Unknown load results are confirmed by reading state, never by reloading.
    """
    gs = _get_game(ctx)
    name = save_name
    if not name:
        return "Error: Provide an explicit confirmed checkpoint save_name; autosave guessing is disabled."
    error = game_lifecycle._validate_save_name(name)
    if error:
        return error
    if checkpoints.expected_checkpoint(name) is None:
        return "Error: Checkpoint completion and expected identity are unavailable; no restart performed."
    async def _run():
        await gs.conn.disconnect()
        stopped = await game_launcher.kill_game()
        if stopped.startswith(("Error", "FAILED", "WARNING")):
            return f"Error: Restart stopped because Civ6 shutdown was not confirmed: {stopped}"
        gs.reset_session()
        started = await game_launcher.launch_game()
        if started.startswith(("Error", "FAILED", "WARNING")) or "not detected" in started:
            return f"Error: {stopped} | {started}"
        loaded = await _load_and_verify(gs, name)
        return f"{loaded} | {stopped} | {started}"
    return await _logged(ctx, "restart_and_load", {"save_name": name}, _run)


async def _load_and_verify(gs: GameState, save_name: str) -> str:
    """同一个连接负责加载与验证；确认窗口内不重复发送加载命令。"""
    error = game_lifecycle._validate_save_name(save_name)
    if error:
        return error
    if getattr(gs, "_pending_load", False):
        return "ERR:OUTCOME_UNKNOWN|An earlier load is unconfirmed; inspect state or use explicit recovery, not another load."
    expected = checkpoints.expected_checkpoint(save_name)
    if expected is None:
        return "Error: Checkpoint completion and expected identity are unavailable; no load sent."
    submitted = False
    try:
        async with asyncio.timeout(LOAD_TIMEOUT):
            # During startup FireTuner briefly opens before MainMenu exists,
            # then resets the socket. Retry only this read-only discovery;
            # the actual load below is still submitted exactly once.
            while True:
                try:
                    await gs.conn.reconnect()
                    if gs.conn.gamecore_index is not None:
                        return "Error: A game is loaded; return to the main menu first."
                    if "MainMenu" in gs.conn.lua_states.values():
                        break
                except (OSError, LuaError, ValueError):
                    pass
                await asyncio.sleep(2)
            submitted = True
            result = await game_lifecycle.load_save_from_frontend(gs.conn, save_name)
            if not result.startswith(("Loading", "ERR:OUTCOME_UNKNOWN")):
                return result
            gs.reset_session()
            gs._pending_load = True
            while True:
                try:
                    await gs.conn.reconnect()
                    if gs.conn.gamecore_index is not None and gs.conn.ingame_index is not None:
                        from civ_mcp.end_turn import read_turn_state
                        turn, ready = await read_turn_state(gs)
                        if ready is True:
                            identity = await gs.get_game_identity()
                            gs._pending_load = True
                            ov = await gs.get_game_overview()
                            # Identity discovery can reset cached session data;
                            # restore the barrier until every comparison passes.
                            gs._pending_load = True
                            actual = (*identity, ov.turn)
                            if actual != expected or turn != ov.turn:
                                return f"ERR:OUTCOME_UNKNOWN|Loaded state mismatch: expected {expected}, observed {actual}, active turn {turn}. No load replayed."
                            if checkpoints.expected_checkpoint(save_name) != expected:
                                return "ERR:OUTCOME_UNKNOWN|Checkpoint file changed during load; recovery not verified."
                            gs._pending_load = False
                            return f"Loaded {save_name}: {identity[0]}, seed {identity[1]}, turn {ov.turn}."
                except (OSError, LuaError, ValueError):
                    gs._pending_load = True
                await asyncio.sleep(2)
    except TimeoutError:
        if submitted:
            gs._pending_load = True
        await gs.conn.disconnect()
        if not submitted:
            return "Error: MainMenu was not ready within 180 seconds; no load command was sent."
        return "ERR:OUTCOME_UNKNOWN|Loading was not confirmed within 180 seconds. Read state before another load."


async def _narrate(
    query_fn: Callable[[], Awaitable[Any]], narrate_fn: Callable[..., str]
) -> str:
    """Helper: call a query function then narrate the result."""
    data = await query_fn()
    return narrate_fn(data)


def main():
    """Entry point for the MCP server."""
    import signal

    logging.basicConfig(level=logging.INFO)

    # Remap SIGTERM → SIGINT so asyncio's existing SIGINT handler triggers a
    # graceful shutdown (cancels all tasks → lifespan finally block runs →
    # conn.disconnect() closes the FireTuner TCP connection cleanly).
    # Without this, SIGTERM kills the process immediately, leaving the game
    # with an abrupt TCP RST which can cause it to crash.
    # SIGTERM is not available on Windows, so skip the remap there.
    if hasattr(signal, "SIGTERM"):
        signal.signal(
            signal.SIGTERM, lambda sig, frame: os.kill(os.getpid(), signal.SIGINT)
        )

    if os.environ.get("CIV_MCP_DISABLE_LUA"):
        mcp._tool_manager.remove_tool("run_lua")

    mcp.run(transport="stdio")
