"""Query tools (read-only) for the Civilization VI MCP server."""

from mcp.server.fastmcp import Context

import logging

from civ_mcp import facts as fact_view
from civ_mcp import heartbeat, lua as lq, narrate as nr

log = logging.getLogger(__name__)
from civ_mcp.server import pipeline
from civ_mcp.server.assembly import mcp
from civ_mcp.server.governance_snapshot import (
    _capture_governance_snapshot,
)

# ---------------------------------------------------------------------------
# Query tools (read-only)
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_game_overview(ctx: Context) -> str:
    """Get a high-level summary of the current game state.

    Returns turn number, civilization, yields (gold/science/culture/faith),
    current research and civic, and counts of cities and units.
    Call this first to orient yourself.
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        ov = await gs.get_game_overview()
        logger = pipeline._get_logger(ctx)
        logger.set_turn(ov.turn)
        spatial = pipeline._get_spatial(ctx)
        spatial.set_turn(ov.turn)
        try:
            civ, seed = await gs.get_game_identity()
            logger.bind_game(civ, seed)
            if pipeline._get_belief_mode(ctx).records_events:
                await pipeline._bind_belief_engine(
                    ctx, pipeline._get_beliefs(ctx), civ=civ, seed=seed
                )
            spatial.bind_game(civ, seed)
            heartbeat.bind_game(civ, seed)
            gs.spatial = spatial
        except Exception:
            pass
        # Seed revealed tiles for visibility diff (once per session)
        if not spatial._revealed_seeded:
            try:
                seed_lines = await gs.conn.execute_read(
                    lq.build_revealed_tiles_seed_query()
                )
                seed_tiles = lq.parse_revealed_tiles_seed(seed_lines)
                spatial.seed_revealed(seed_tiles)
                log.info(
                    "Seeded spatial tracker with %d revealed tiles", len(seed_tiles)
                )
            except Exception:
                log.debug("Failed to seed revealed tiles", exc_info=True)
        text = nr.narrate_overview(ov)
        text += "\n\n" + pipeline._format_runtime_policy(pipeline._get_belief_mode(ctx))
        # Check for game-over state
        gameover = await gs.check_game_over()
        if gameover is not None:
            vtype = (
                gameover.victory_type.replace("VICTORY_", "").replace("_", " ").title()
            )
            if gameover.is_defeat:
                text += (
                    f"\n\n*** GAME OVER — DEFEAT ***\n"
                    f"{gameover.winner_leader} of {gameover.winner_name} won a {vtype} victory.\n"
                    f"No further actions are possible."
                )
            else:
                text += f"\n\n*** GAME OVER — VICTORY ***\nYou won a {vtype} victory!"
            try:
                await logger.log_game_over(
                    is_defeat=gameover.is_defeat,
                    winner_civ=gameover.winner_name,
                    winner_leader=gameover.winner_leader,
                    victory_type=vtype,
                    player_alive=gameover.player_alive,
                )
            except Exception:
                log.warning("Failed to log game-over in overview", exc_info=True)
        else:
            # Barbarian camps are the spawn source and are not included in the
            # generic hostile-unit scan. Surface a compact scan at the
            # canonical per-turn entry point so the agent cannot silently
            # skip camp clearing while the standalone tool remains available
            # for the full list and coordinates.
            try:
                barbarian_overview = await gs.get_barbarian_overview()
                text += "\n\n" + nr.narrate_barbarian_overview(
                    barbarian_overview, compact=True
                )
            except Exception:
                log.debug("Barbarian overview failed", exc_info=True)
            # Empty policy slots are a real opportunity cost, but the game
            # does not emit a blocking notification for every empty slot
            # (notably a newly available wildcard slot). Surface the
            # authoritative policy state at the canonical per-turn entry
            # point so the agent cannot overlook an available card.
            try:
                policy_status = await gs.get_policies()
                if policy_status.available_policies and any(
                    slot.current_policy is None for slot in policy_status.slots
                ):
                    text += "\n\n" + nr.narrate_policies(policy_status)
            except Exception:
                log.debug("Policy slot check failed", exc_info=True)
        # Governance is deliberately absent from the lightweight live modes.
        # Enforcement retains the legacy snapshot and action-gate behavior.
        if pipeline._get_belief_mode(ctx).captures_governance_snapshot:
            try:
                engine = pipeline._get_beliefs(ctx)
                # An async action may take effect after an earlier read-back.
                # Journal timestamps cannot prove freshness across requests;
                # only this request's bounded collection can share reads.
                snapshot, world, projection, released, locks = (
                    await _capture_governance_snapshot(ctx, engine)
                )
                belief_brief = engine.turn_brief(turn=snapshot.turn)
                await pipeline._flush_belief_events(ctx)
                text += (
                    "\n\n=== GOVERNANCE SNAPSHOT ===\n"
                    f"snapshot={snapshot.snapshot_id} ruleset={world['ruleset']} source=captured "
                    f"entities_changed={len(projection['world_entities_changed'])} "
                    f"entities_archived={len(projection['world_entities_archived'])} "
                    f"active_budget_locks={len(locks)} released_locks={len(released)}"
                )
                changes = projection.get("world_changes")
                if changes:
                    text += "\n" + pipeline._format_world_changes(changes)
                text += pipeline._format_belief_turn_brief(belief_brief)
            except Exception as exc:
                log.warning("Governance: failed to capture typed turn state", exc_info=True)
                text += (
                    "\n\n=== GOVERNANCE SNAPSHOT ERROR ===\n"
                    f"{exc}\nKey actions and end_turn remain blocked until "
                    "get_governance_brief succeeds."
                )
        return text

    return await pipeline._logged(ctx, "get_game_overview", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_units(ctx: Context) -> str:
    """List all your units with position, type, movement, and health.

    Each unit shows its id and idx (needed for action commands).
    Consumed units (e.g. settlers that founded cities) are excluded.

    Returns a JSON envelope with structured ``facts``
    (own_units / foreign_units / trade_routes with per-set ``coverage``).
    """
    gs = pipeline._get_game(ctx)
    unit_tiles: set[tuple[int, int]] = set()

    async def _run():
        units = await gs.get_units()
        unit_tiles.update((u.x, u.y) for u in units if u.x >= 0)
        try:
            threats = await gs.get_threat_scan()
        except Exception:
            threats = None
        trade_status = None
        try:
            trade_status = await gs.get_trade_routes()
        except Exception:
            pass
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.units_envelope(
                turn=turn,
                units=units,
                threats=threats,
                trade_status=trade_status,
            )
        )

    return await pipeline._logged(ctx, "get_units", {}, _run, tiles=unit_tiles)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_barbarian_overview(ctx: Context) -> str:
    """Query revealed barbarian camps and currently visible barbarian units.

    Camps remain listed after leaving their tile's visibility as long as the
    tile has been revealed. Barbarian unit locations require current vision.
    Results include distance to the nearest own city and military unit so the
    agent can clear the spawn source instead of reacting to endless waves.

    Returns a JSON envelope with structured ``facts`` (camps with
    KNOWN_HISTORY coverage, units with CURRENTLY_VISIBLE coverage).
    """
    gs = pipeline._get_game(ctx)
    barbarian_tiles: set[tuple[int, int]] = set()

    async def _run():
        overview = await gs.get_barbarian_overview()
        barbarian_tiles.update((camp.x, camp.y) for camp in overview.camps)
        barbarian_tiles.update((unit.x, unit.y) for unit in overview.units)
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.barbarian_envelope(
                turn=turn,
                overview=overview,
            )
        )

    return await pipeline._logged(
        ctx,
        "get_barbarian_overview",
        {},
        _run,
        tiles=barbarian_tiles,
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_village_overview(ctx: Context) -> str:
    """查询已揭示的部落村落（一次性奖励）及取用优先级。

    只返回当前仍存在的村落: 任一单位踏入村落即取用并使其消失, 已取用
    村落不出现在结果中, 本工具不保留取用历史。每条结果包含坐标、当前
    可见状态、所在领土归属, 以及到最近己方城市和最近己方战斗/侦察单位
    的距离, 用于抢先取用决策。奖励内容在取用后由游戏结算, 不可查询。

    返回 JSON 信封：结构化 ``facts``（huts 覆盖语义为 KNOWN_HISTORY，
    消失只说明已被取用）。
    """
    gs = pipeline._get_game(ctx)
    village_tiles: set[tuple[int, int]] = set()

    async def _run():
        overview = await gs.get_village_overview()
        village_tiles.update((hut.x, hut.y) for hut in overview.huts)
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.village_envelope(
                turn=turn,
                overview=overview,
            )
        )

    return await pipeline._logged(
        ctx,
        "get_village_overview",
        {},
        _run,
        tiles=village_tiles,
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_era_progress(ctx: Context) -> str:
    """Get chronological era progress for the game and every major civilization.

    Always returns: current world era, the full era sequence of this game,
    and each major civ's current era (who is ahead or behind).
    Under Rise and Fall / Gathering Storm rules also returns the next-era
    countdown clock and your era score vs dark/golden thresholds, with the
    score source breakdown. Under Standard rules the age block is explicitly
    reported as unavailable instead of being silently omitted.

    Returns a JSON envelope with structured ``facts`` (eras/players/
    local_age with COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        status = await gs.get_era_progress()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.era_progress_envelope(
                turn=turn,
                status=status,
            )
        )

    return await pipeline._logged(ctx, "get_era_progress", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_spies(ctx: Context) -> str:
    """List all your spy units with position, rank, city, and available missions.

    Shows each spy's composite id (needed for spy_action), current location,
    rank (Recruit/Agent/Special Agent/Senior Agent), XP, and which operations
    are available at their current position.

    Note: offensive missions only become available once the spy has physically
    arrived in the target city. Use spy_action with action='travel' first.

    Returns a JSON envelope with structured ``facts`` (spies with
    COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        spies = await gs.get_spies()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.spies_envelope(
                turn=turn,
                spies=spies,
            )
        )

    return await pipeline._logged(ctx, "get_spies", {}, _run)


@mcp.tool()
async def spy_action(
    ctx: Context,
    unit_id: int,
    action: str,
    target_x: int,
    target_y: int,
) -> str:
    """Send a spy to a city or launch a spy mission.

    Args:
        unit_id: The spy's composite ID (from get_spies output)
        action: 'travel' to move spy to a city, or a mission type to launch a mission.
            Mission types: COUNTERSPY, GAIN_SOURCES, SIPHON_FUNDS, STEAL_TECH_BOOST,
            SABOTAGE_PRODUCTION, GREAT_WORK_HEIST, RECRUIT_PARTISANS,
            NEUTRALIZE_GOVERNOR, FABRICATE_SCANDAL
        target_x: X coordinate of the target city tile
        target_y: Y coordinate of the target city tile

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
    gs = pipeline._get_game(ctx)
    unit_index = unit_id % 65536
    params = {
        "unit_id": unit_id,
        "action": action,
        "target_x": target_x,
        "target_y": target_y,
    }

    async def _run():
        if action.lower() == "travel":
            return await gs.spy_travel(unit_index, target_x, target_y)
        return await gs.spy_mission(unit_index, action.upper(), target_x, target_y)

    result = await pipeline._logged(ctx, "spy_action", params, _run)
    pipeline._get_camera(ctx).push(target_x, target_y, f"spy {action}")
    return result


@mcp.tool(annotations={"readOnlyHint": True})
async def get_cities(ctx: Context) -> str:
    """List all your cities with yields, population, production, growth, and loyalty.

    Each city shows its id (needed for production commands).
    Cities losing loyalty show warnings with flip timers.

    Returns a JSON envelope with structured ``facts`` (cities with
    COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        cities, distances = await gs.get_cities()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.cities_envelope(
                turn=turn,
                cities=cities,
                distances=distances,
            )
        )

    return await pipeline._logged(ctx, "get_cities", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_city_production(ctx: Context, city_id: int) -> str:
    """List what a city can produce right now.

    Args:
        city_id: City ID (from get_cities output)

    Returns available units, buildings, and districts with production costs.
    Call this when a city finishes building or to decide what to produce next.

    Returns a JSON envelope with structured ``facts`` (options with
    COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        options = await gs.list_city_production(city_id)
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.city_production_envelope(
                turn=turn,
                city_id=city_id,
                options=options,
            )
        )

    return await pipeline._logged(ctx, "get_city_production", {"city_id": city_id}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_map_area(
    ctx: Context, center_x: int, center_y: int, radius: int = 2
) -> str:
    """Get terrain info for tiles around a point.

    Args:
        center_x: X coordinate of center tile
        center_y: Y coordinate of center tile
        radius: How many tiles out from center (default 2, max 4)

    Returns a JSON envelope with structured ``facts`` (requested
    tile set is COMPLETE; each tile's own coverage is its ``visibility``
    field).
    """
    radius = min(radius, 4)
    gs = pipeline._get_game(ctx)
    tile_coords: set[tuple[int, int]] = set()

    async def _run():
        tiles = await gs.get_map_area(center_x, center_y, radius)
        tile_coords.update((t.x, t.y) for t in tiles)
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.map_area_envelope(
                turn=turn,
                center_x=center_x,
                center_y=center_y,
                radius=radius,
                tiles=tiles,
            )
        )

    result = await pipeline._logged(
        ctx,
        "get_map_area",
        {"center_x": center_x, "center_y": center_y, "radius": radius},
        _run,
        tiles=tile_coords,
    )
    pipeline._get_camera(ctx).push(center_x, center_y, f"map_area ({center_x},{center_y})")
    return result


@mcp.tool(annotations={"readOnlyHint": True})
async def get_settle_advisor(ctx: Context, unit_id: int) -> str:
    """List best settle locations near a settler unit.

    Args:
        unit_id: The settler's composite ID (from get_units output)

    Scores locations by yields, water, defense, and resource value.
    Returns top 5 candidates sorted by score. When no candidate exists
    within 5 tiles, falls back to the best sites on the revealed map.

    Returns a JSON envelope with structured ``facts`` (candidates
    with KNOWN_HISTORY coverage and a ``source`` field).
    """
    gs = pipeline._get_game(ctx)
    unit_index = unit_id % 65536

    async def _run():
        candidates, source = await gs.get_settle_candidates(unit_index)
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.settle_envelope(
                turn=turn,
                tool="get_settle_advisor",
                unit_id=unit_id,
                candidates=candidates,
                source=source,
            )
        )

    return await pipeline._logged(
        ctx,
        "get_settle_advisor",
        {"unit_id": unit_id},
        _run,
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

    Returns a JSON envelope with structured ``facts`` (estimate
    with COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)
    unit_index = unit_id % 65536

    async def _run():
        est = await gs.get_pathing_estimate(unit_index, target_x, target_y)
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.pathing_envelope(
                turn=turn,
                unit_id=unit_id,
                target_x=target_x,
                target_y=target_y,
                estimate=est,
            )
        )

    return await pipeline._logged(
        ctx,
        "get_pathing_estimate",
        {"unit_id": unit_id, "target_x": target_x, "target_y": target_y},
        _run,
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_combat_estimate(
    ctx: Context, unit_id: int, target_x: int, target_y: int
) -> str:
    """Quantify a unit matchup without executing an attack.

    Returns effective combat strengths, current HP, terrain/fortification/
    promotion/flanking/support modifiers, and estimated damage to both sides.
    Use this after a proximity scan and before revising a route-safety belief;
    merely seeing a hostile unit is not evidence that the route is unsafe.

    Args:
        unit_id: Attacking or escort unit composite ID from get_units
        target_x: Hostile unit X coordinate
        target_y: Hostile unit Y coordinate

    Returns a JSON envelope with structured ``facts`` (available /
    estimate with COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)
    unit_index = unit_id % 65536

    async def _run():
        estimate = await gs.get_combat_estimate(unit_index, target_x, target_y)
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.combat_estimate_envelope(
                turn=turn,
                estimate=estimate,
            )
        )

    return await pipeline._logged(
        ctx,
        "get_combat_estimate",
        {"unit_id": unit_id, "target_x": target_x, "target_y": target_y},
        _run,
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_global_settle_advisor(ctx: Context) -> str:
    """Find the best settle locations across the entire revealed map.

    Unlike get_settle_advisor (which searches near a specific settler),
    this scans all revealed land for the top 10 settle candidates.
    Use this when deciding WHERE to send a settler, not just where to settle.

    Returns a JSON envelope with structured ``facts`` (candidates
    with KNOWN_HISTORY coverage and source="global").
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        candidates = await gs.get_global_settle_scan()
        source = "global" if candidates else "none"
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.settle_envelope(
                turn=turn,
                tool="get_global_settle_advisor",
                unit_id=-1,
                candidates=candidates,
                source=source,
            )
        )

    return await pipeline._logged(ctx, "get_global_settle_advisor", {}, _run)


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

    Returns a JSON envelope with structured ``facts`` (tasks/
    builders with COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        tasks, builders = await gs.get_builder_tasks()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.builder_tasks_envelope(
                turn=turn,
                tasks=tasks,
                builders=builders,
            )
        )

    return await pipeline._logged(ctx, "get_builder_tasks", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_empire_resources(ctx: Context) -> str:
    """Get a summary of all resources in and near your empire.

    Shows owned resources (improved/unimproved) grouped by type,
    and unclaimed resources near your cities.

    Returns a JSON envelope with structured ``facts`` (stockpiles/
    owned COMPLETE, nearby KNOWN_HISTORY).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        stockpiles, owned, nearby, luxuries = await gs.get_empire_resources()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.empire_resources_envelope(
                turn=turn,
                stockpiles=stockpiles,
                owned=owned,
                nearby=nearby,
                luxuries=luxuries,
            )
        )

    return await pipeline._logged(ctx, "get_empire_resources", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_strategic_map(ctx: Context) -> str:
    """Get fog-of-war boundaries and unclaimed resources across the map.

    Shows how far explored territory extends from each city (in 6 directions),
    highlighting directions that need exploration. Also lists unclaimed luxury
    and strategic resources on revealed but unowned land.

    Returns a JSON envelope with structured ``facts`` (fog_boundaries
    COMPLETE, unclaimed_resources KNOWN_HISTORY).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        data = await gs.get_strategic_map()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.strategic_map_envelope(
                turn=turn,
                data=data,
            )
        )

    return await pipeline._logged(ctx, "get_strategic_map", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_diplomacy(ctx: Context) -> str:
    """Get diplomatic status with all known civilizations.

    Shows diplomatic state (Friendly/Neutral/Unfriendly), relationship modifiers
    with scores and reasons, grievances, delegations/embassies, and available
    diplomatic actions you can take. Also shows visible enemy city details
    (name, population, loyalty, walls).

    Returns a JSON envelope with structured ``facts`` (civs with
    COMPLETE coverage; per-civ intelligence is visibility-limited).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        civs = await gs.get_diplomacy()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.diplomacy_envelope(
                turn=turn,
                civs=civs,
            )
        )

    return await pipeline._logged(ctx, "get_diplomacy", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_tech_civics(ctx: Context) -> str:
    """Get technology and civic research status.

    Shows current research, current civic, turns remaining,
    completed technology names, and lists of available technologies and civics
    to choose from.

    Returns a JSON envelope with structured ``facts`` (research/
    civics with COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        status = await gs.get_tech_civics()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.tech_civics_envelope(
                turn=turn,
                status=status,
            )
        )

    return await pipeline._logged(ctx, "get_tech_civics", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_pending_trades(ctx: Context) -> str:
    """Check for pending trade deal offers from other civilizations.

    Shows what each civ is offering and what they want in return.
    Use respond_to_trade to accept or reject.

    Returns a JSON envelope with structured ``facts`` (deals with
    COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        deals = await gs.get_pending_deals()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.pending_trades_envelope(
                turn=turn,
                deals=deals,
            )
        )

    return await pipeline._logged(ctx, "get_pending_trades", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_policies(ctx: Context) -> str:
    """Get current government, policy slots, and available policies.

    Shows current government type, each policy slot with its type and current
    policy (if any), and all unlocked policies grouped by compatible slot type.
    Wildcard slots accept any policy type.

    Returns a JSON envelope with structured ``facts`` (government/
    policies with COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        status = await gs.get_policies()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.policies_envelope(
                turn=turn,
                status=status,
            )
        )

    return await pipeline._logged(ctx, "get_policies", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_notifications(ctx: Context) -> str:
    """Get all active game notifications.

    Shows action-required items (need your decision) and informational
    notifications. Action-required items include which MCP tool to use
    to resolve them. Call this to check what needs attention without
    ending the turn.

    Returns a JSON envelope with structured ``facts``
    (notifications with COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        notifications = await gs.get_notifications()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.notifications_envelope(
                turn=turn,
                notifications=notifications,
            )
        )

    return await pipeline._logged(ctx, "get_notifications", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_pending_diplomacy(ctx: Context) -> str:
    """Check for pending diplomacy encounters (e.g. first meeting with a civ).

    Diplomacy encounters block turn progression. Call this if end_turn
    reports the turn didn't advance. Returns any open sessions with their
    dialogue text, visible buttons, and response guidance.

    Returns a JSON envelope with structured ``facts`` (sessions
    with COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        sessions = await gs.get_diplomacy_sessions()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.pending_diplomacy_envelope(
                turn=turn,
                sessions=sessions,
            )
        )

    return await pipeline._logged(ctx, "get_pending_diplomacy", {}, _run)
