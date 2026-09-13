"""Late single-purpose tools: diary, trade routes, advisors, tiles, government,
great people, world congress, victory, religion, and city focus."""

import json
from typing import Optional

from mcp.server.fastmcp import Context

from civ_mcp import facts as fact_view
from civ_mcp.diary import (
    diary_path as _diary_path,
    read_diary_entries as _read_diary_entries,
)
from civ_mcp.server import pipeline
from civ_mcp.server.assembly import mcp

# ---------------------------------------------------------------------------
# Diary
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_diary(
    ctx: Context,
    last_n: int = 5,
    turn: Optional[int] = None,
    from_turn: Optional[int] = None,
    to_turn: Optional[int] = None,
) -> str:
    """Read diary entries for game memory.

    Args:
        last_n: Number of most recent entries to return (default 5, max 50).
                Used when turn/from_turn/to_turn are not specified.
        turn: Return the single entry for this turn number.
        from_turn: Return entries from this turn onward (inclusive).
        to_turn: Return entries up to this turn (inclusive).

    Auto-detects the current game from the live connection. Each game has
    its own diary file (keyed by civ + random seed).

    Call this at the start of a session or after context compaction to
    restore strategic memory from previous turns.

    Returns a JSON envelope with structured ``facts`` (entries with
    COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)
    try:
        civ_type, seed = await gs.get_game_identity()
    except Exception:
        return "Could not detect current game. Is the game running?"

    run_id = pipeline._get_logger(ctx).session_id
    path = _diary_path(civ_type, seed, run_id)
    if not path.exists():
        return f"No diary entries yet for this game ({civ_type}, seed {seed})."

    entries = _read_diary_entries(path)
    if not entries:
        return f"No diary entries yet for this game ({civ_type}, seed {seed})."

    # New format (v2) has N rows per turn — filter to agent rows only.
    # Old format entries (no "v" key) pass through unchanged.
    entries = [e for e in entries if "v" not in e or e.get("is_agent")]

    # Filter by query mode
    if turn is not None:
        entries = [e for e in entries if e.get("turn") == turn]
    elif from_turn is not None or to_turn is not None:
        lo = from_turn if from_turn is not None else 0
        hi = to_turn if to_turn is not None else 999999
        entries = [e for e in entries if lo <= e.get("turn", 0) <= hi]
    else:
        last_n = min(max(last_n, 1), 50)
        entries = entries[-last_n:]

    if not entries:
        return "No diary entries match the query."

    return fact_view.dumps(
        fact_view.diary_envelope(
            turn=pipeline._get_logger(ctx)._turn,
            entries=entries,
        )
    )


# ---------------------------------------------------------------------------
# Trade routes
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_trade_routes(ctx: Context) -> str:
    """Get trade route capacity, active routes, and trader status.

    Shows how many routes are active vs capacity, and lists all trader
    units with their positions and whether they're idle or on a route.

    Returns a JSON envelope with structured ``facts`` (routes/
    traders with COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        status = await gs.get_trade_routes()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.trade_routes_envelope(
                turn=turn,
                status=status,
            )
        )

    return await pipeline._logged(ctx, "get_trade_routes", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_trade_destinations(ctx: Context, unit_id: int) -> str:
    """List valid trade route destinations for a trader unit.

    Args:
        unit_id: The trader's composite ID (from get_units output)

    Shows domestic and international destinations. Use unit_action
    with action='trade_route' and target_x/target_y to start a route.

    Returns a JSON envelope with structured ``facts`` (destinations
    with COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)
    unit_index = unit_id % 65536

    async def _run():
        dests = await gs.get_trade_destinations(unit_index)
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.trade_destinations_envelope(
                turn=turn,
                unit_id=unit_id,
                destinations=dests,
            )
        )

    return await pipeline._logged(ctx, "get_trade_destinations", {"unit_id": unit_id}, _run)


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

    Returns a JSON envelope with structured ``facts`` (placements
    with COMPLETE coverage; advisor budget warning in ``warning``).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        result = await gs.get_district_advisor(city_id, district_type)
        if isinstance(result, str):
            return f"Error: {result}"  # propagate specific error reason
        warning = None
        if gs._advisor_budget_warning:
            warning = gs._advisor_budget_warning
            gs._advisor_budget_warning = None
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.district_advisor_envelope(
                turn=turn,
                city_id=city_id,
                district_type=district_type,
                placements=result,
                warning=warning,
            )
        )

    return await pipeline._logged(
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

    Returns a JSON envelope with structured ``facts`` (placements
    with COMPLETE coverage; advisor budget warning in ``warning``).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        placements = await gs.get_wonder_advisor(city_id, wonder_name)
        if isinstance(placements, str):
            return f"Error: {placements}"  # propagate budget/error string
        warning = None
        if gs._advisor_budget_warning:
            warning = gs._advisor_budget_warning
            gs._advisor_budget_warning = None
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.wonder_advisor_envelope(
                turn=turn,
                city_id=city_id,
                wonder_name=wonder_name,
                placements=placements,
                warning=warning,
            )
        )

    return await pipeline._logged(
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

    Returns a JSON envelope with structured ``facts`` (tiles with
    COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        tiles = await gs.get_purchasable_tiles(city_id)
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.purchasable_tiles_envelope(
                turn=turn,
                city_id=city_id,
                tiles=tiles,
            )
        )

    return await pipeline._logged(ctx, "get_purchasable_tiles", {"city_id": city_id}, _run)


@mcp.tool()
async def purchase_tile(ctx: Context, city_id: int, x: int, y: int) -> str:
    """Buy a tile for a city with gold.

    Args:
        city_id: City ID
        x: Tile X coordinate
        y: Tile Y coordinate

    Use get_purchasable_tiles first to see costs and options.
    """
    gs = pipeline._get_game(ctx)
    result = await pipeline._logged(
        ctx,
        "purchase_tile",
        {"city_id": city_id, "x": x, "y": y},
        lambda: gs.purchase_tile(city_id, x, y),
    )
    pipeline._get_camera(ctx).push(x, y, f"purchase tile ({x},{y})")
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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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

    Returns a JSON envelope with structured ``facts`` (people with
    COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        gp = await gs.get_great_people()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.great_people_envelope(
                turn=turn,
                people=gp,
            )
        )

    return await pipeline._logged(ctx, "get_great_people", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_great_people_overview(ctx: Context) -> str:
    """One-shot Great People report: standings, recruit pool, history, and your
    idle great people. Returns four sections: 1. Points standings per great
    person class for every major alive civ — total points, points per turn,
    and great people already received. Civilizations you have not met are
    masked as "Unmet". 2. Current recruit pool: each available individual
    with era, recruit cost, ability, patronize gold/faith costs, and your
    points toward that class (same rows as get_great_people; [CAN RECRUIT]
    marks affordable ones). 3. History: already-claimed great people with
    claimant and turn granted. 4. Your great person units on the map with
    activation charges; pair with get_gp_advisor(unit_index) and
    unit_action(action='activate'). Use this when deciding GP point
    investment, patronage, or who will win a class race.
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        ov = await gs.get_great_people_overview()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.great_people_overview_envelope(
                turn=turn,
                overview=ov,
            )
        )

    return await pipeline._logged(ctx, "get_great_people_overview", {}, _run)


@mcp.tool()
async def get_gp_advisor(ctx: Context, unit_index: int) -> str:
    """Show best cities to activate a Great Person, ranked by suitability.

    Args:
        unit_index: The Great Person unit's index (from get_units output).

    Lists all cities with the matching district (e.g., campuses for Great Scientists),
    showing which ones the GP can activate on, distance, city yield, and great work
    slot availability for cultural GPs.
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        result = await gs.get_gp_advisor(unit_index)
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.gp_advisor_envelope(
                turn=turn,
                unit_index=unit_index,
                result=result,
            )
        )

    return await pipeline._logged(ctx, "get_gp_advisor", {"unit": unit_index}, _run)


@mcp.tool()
async def recruit_great_person(ctx: Context, individual_id: int) -> str:
    """Recruit a Great Person using accumulated GP points.

    Args:
        individual_id: The individual's ID (from get_great_people output, shown after ability)

    Requires enough Great Person points for that class.
    The GP spawns in your capital. Use get_great_people to check [CAN RECRUIT] status.
    """
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
        ctx,
        "recruit_great_person",
        {"individual_id": individual_id},
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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
        ctx,
        "patronize_great_person",
        {"individual_id": individual_id, "yield_type": yield_type},
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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
        ctx,
        "reject_great_person",
        {"individual_id": individual_id},
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

    Returns a JSON envelope with structured ``facts`` (congress with
    COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        status = await gs.get_world_congress()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.world_congress_envelope(
                turn=turn,
                status=status,
            )
        )

    return await pipeline._logged(ctx, "get_world_congress", {}, _run)


@mcp.tool()
async def queue_wc_votes(ctx: Context, votes: str) -> str:
    """Pre-configure World Congress votes for the upcoming session.

    Args:
        votes: JSON array of vote objects, e.g.
            '[{"hash": -513644209, "option": 1, "target": 2, "votes": 5}]'
            也可用决议类型名代替 hash，例如
            '[{"type": "WC_RES_WORLD_RELIGION", "option": 1, "votes": 4}]'。
            **推荐用 type**：开会前的 get_world_congress 预览列出的是上一届
            决议，hash 到真正开会时经常对不上；type 是稳定标识，处理器会在
            议会真正打开、拿到真实决议清单时再匹配。
            hash = resolution type hash (from get_world_congress)
            type = resolution type name, e.g. WC_RES_LUXURY / WC_RES_WORLD_RELIGION
            option = 1 for A, 2 for B
            target = player ID for PlayerType resolutions (from get_world_congress
                     target list, e.g. [target=2] Portugal), or target value for
                     non-player resolutions. The handler resolves to the correct
                     0-based index at runtime.
            votes = max votes to allocate (default 1 = 免费的第一票；未列出的
                     决议也按 1 票处理，不会消耗 favor)

    Call this BEFORE end_turn when get_world_congress shows 0 turns until next
    session. Registers an event handler that fires during WC processing and
    casts your votes with the specified preferences.

    投票与提交都由本程序完成（无需玩家点界面）：处理器在议会打开时按上述
    策略投票并提交；若事件处理器因引擎时序没有触发，end_turn 的等待循环会
    在议会回合自行驱动一次——读取**真实**决议清单、套用同一策略、投票并提交
    （见 `build_wc_drive_and_submit`），随后把实际票型写入 `__civmcp_wc_report`。

    未在 votes 里出现的决议只投 1 票（第 1 票成本为 0），不会消耗 favor。
    get_world_congress 在开会前的预览可能给出与实际开会不同的决议集合，
    因此"未匹配"是正常情况，不会被当成默认策略去按最大票数盲投。

    If you don't call this, end_turn will pause at the World Congress session
    and return control to you for interactive voting.
    """
    gs = pipeline._get_game(ctx)
    vote_list = json.loads(votes)

    async def _run():
        return await gs.queue_wc_votes(vote_list)

    return await pipeline._logged(ctx, "queue_wc_votes", {"votes": votes}, _run)


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

    Returns a JSON envelope with structured ``facts`` (players/
    demographics with COMPLETE coverage).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        vp = await gs.get_victory_progress()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.victory_progress_envelope(
                turn=turn,
                progress=vp,
            )
        )

    return await pipeline._logged(ctx, "get_victory_progress", {}, _run)


# ---------------------------------------------------------------------------
# Religion status
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_religion_spread(ctx: Context) -> str:
    """Get per-city religion breakdown across all visible cities.

    Shows which religion is majority in each city, follower counts,
    and which religions are closest to religious victory.

    Returns a JSON envelope with structured ``facts`` (cities
    CURRENTLY_VISIBLE, summary COMPLETE).
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        rs = await gs.get_religion_status()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.religion_spread_envelope(
                turn=turn,
                status=rs,
            )
        )

    return await pipeline._logged(ctx, "get_religion_spread", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_religion_overview(ctx: Context) -> str:
    """世界宗教状态总览：已创宗教及其创立者、圣城、信条构成；各宗教的信徒
    城市数与信徒总数；各主要文明的己创/主流宗教与万神殿对照；己方信仰值
    存量与创教名额余量。未见面创立者遮蔽为 Unmet。

    聚合视图。逐城明细见 get_religion_spread，信条候选见
    get_pantheon_beliefs / get_religion_beliefs。用于决定万神殿时机、
    创教竞速与传教目标。

    返回 JSON 信封：结构化 ``facts``（religions COMPLETE、players
    KNOWN_HISTORY）。
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        ro = await gs.get_religion_overview()
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.religion_overview_envelope(
                turn=turn,
                status=ro,
            )
        )

    return await pipeline._logged(ctx, "get_religion_overview", {}, _run)


@mcp.tool(annotations={"readOnlyHint": True})
async def get_climate_overview(ctx: Context, history_turns: int = 30) -> str:
    """Get the Gathering Storm climate report: sea-level phase, CO2, disaster
    risks, and recent weather events (Gathering Storm ruleset only).

    Args:
        history_turns: How many turns of event history to include (1-200, default 30).

    Shows: sea-level phase and points to next rise, world/your CO2 and top
    contributors, storm/flood/eruption/drought risk percentages, this turn's
    disaster with affected cities, and recent event history with damage.
    Call every ~10 turns, or after any flood/volcano/blizzard notification.
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        overview = await gs.get_climate_overview(history_turns)
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.climate_envelope(
                turn=turn,
                status=overview,
            )
        )

    return await pipeline._logged(
        ctx, "get_climate_overview", {"history_turns": history_turns}, _run
    )


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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
        ctx,
        "set_city_focus",
        {"city_id": city_id, "focus": focus},
        lambda: gs.set_city_focus(city_id, focus),
    )
