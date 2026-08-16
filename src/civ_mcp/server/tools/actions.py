"""Action tools (mutating) for the Civilization VI MCP server."""

from typing import Any, Optional

from mcp.server.fastmcp import Context

from civ6_belief_engine.belief_engine import BeliefEngineError

from civ_mcp import facts as fact_view
from civ_mcp import narrate as nr
from civ_mcp.server import pipeline
from civ_mcp.server.assembly import mcp

# ---------------------------------------------------------------------------
# Action tools (mutating)
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True})
async def get_governors(ctx: Context) -> str:
    """Get governor status, appointed governors, and available types.

    Shows governor points, currently appointed governors with assignments,
    and governors available to appoint. Use appoint_governor to appoint one.
    """
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
        ctx,
        "get_governors",
        {},
        lambda: pipeline._narrate(gs.get_governors, nr.narrate_governors),
    )


@mcp.tool()
async def appoint_governor(ctx: Context, governor_type: str) -> str:
    """Appoint a new governor.

    Args:
        governor_type: e.g. GOVERNOR_THE_EDUCATOR (Pingala), GOVERNOR_THE_DEFENDER (Victor)

    Requires available governor points. Use get_governors to see options.
    """
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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

    Returns a double-track JSON envelope: structured ``facts`` (promotions
    with COMPLETE coverage) plus the legacy human-readable ``narrated`` view.
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        status = await gs.get_unit_promotions(unit_id)
        turn = pipeline._get_logger(ctx)._turn
        return fact_view.dumps(
            fact_view.unit_promotions_envelope(
                turn=turn,
                status=status,
                narrated=nr.narrate_unit_promotions(status),
            )
        )

    return await pipeline._logged(ctx, "get_unit_promotions", {"unit_id": unit_id}, _run)


@mcp.tool()
async def promote_unit(ctx: Context, unit_id: int, promotion_type: str) -> str:
    """Apply a promotion to a unit.

    Args:
        unit_id: The unit's composite ID (from get_units output)
        promotion_type: e.g. PROMOTION_BATTLECRY, PROMOTION_TORTOISE

    Use get_unit_promotions first to see available options.
    """
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
        ctx,
        "get_city_states",
        {},
        lambda: pipeline._narrate(gs.get_city_states, nr.narrate_city_states),
    )


@mcp.tool()
async def send_envoy(ctx: Context, player_id: int) -> str:
    """Send an envoy to a city-state.

    Args:
        player_id: The city-state's player ID (from get_city_states)

    Requires available envoy tokens. Use get_city_states to see options.
    """
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
        ctx, "send_envoy", {"player_id": player_id}, lambda: gs.send_envoy(player_id)
    )


@mcp.tool(annotations={"readOnlyHint": True})
async def get_pantheon_beliefs(ctx: Context) -> str:
    """Get pantheon status and available beliefs for selection.

    Shows current pantheon (if any), faith balance, and all available
    pantheon beliefs with their bonuses. Use choose_pantheon to found one.
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        status = await gs.get_pantheon_status()
        return nr.narrate_pantheon_status(status)

    return await pipeline._logged(ctx, "get_pantheon_beliefs", {}, _run)


@mcp.tool()
async def choose_pantheon(ctx: Context, belief_type: str) -> str:
    """Found a pantheon with the specified belief.

    Args:
        belief_type: e.g. BELIEF_GOD_OF_THE_FORGE, BELIEF_DIVINE_SPARK

    Use get_pantheon_beliefs first to see options. Requires enough faith
    and no existing pantheon.
    """
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)

    async def _run():
        status = await gs.get_religion_founding_status()
        return nr.narrate_religion_founding_status(status)

    return await pipeline._logged(ctx, "get_religion_beliefs", {}, _run)


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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
        ctx, "upgrade_unit", {"unit_id": unit_id}, lambda: gs.upgrade_unit(unit_id)
    )


@mcp.tool()
async def get_dedications(ctx: Context) -> str:
    """读取当前时代、可选时代着力点与已生效着力点。

    返回时代分门槛、黄金/黑暗/普通时代状态，以及每个候选项在当前时代的
    实际加成。若显示必须选择，直接调用 choose_dedication 完成本回合必办项。
    """
    gs = pipeline._get_game(ctx)

    async def _run():
        status = await gs.get_dedications()
        return nr.narrate_dedications(status)

    return await pipeline._logged(ctx, "get_dedications", {}, _run)


@mcp.tool()
async def choose_dedication(ctx: Context, dedication_index: int) -> str:
    """选择当前纪元的时代着力点（献礼）。

    Args:
        dedication_index: get_dedications 返回的候选索引。

    先读取 get_dedications 中的候选项与加成。本操作只在游戏要求选择时可用，
    会回读确认；不需要额外理事会审批，以免阻塞回合推进。
    """
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)

    async def _run():
        opts = await gs.get_deal_options(other_player_id)
        return nr.narrate_deal_options(opts)

    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)
    try:
        mode = pipeline._normalize_trade_mode(mode)
    except BeliefEngineError as exc:
        return f"Error: {exc}"

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

    public_params = {
        "other_player_id": other_player_id,
        "offer_gold": offer_gold,
        "offer_gold_per_turn": offer_gold_per_turn,
        "offer_resources": offer_resources,
        "offer_favor": offer_favor,
        "offer_open_borders": offer_open_borders,
        "request_gold": request_gold,
        "request_gold_per_turn": request_gold_per_turn,
        "request_resources": request_resources,
        "request_favor": request_favor,
        "request_open_borders": request_open_borders,
        "joint_war_target": joint_war_target,
        "mode": mode,
    }
    if mode == "test":
        return await pipeline._logged(
            ctx,
            "propose_trade",
            public_params,
            lambda: gs.test_trade(other_player_id, offer_items, request_items),
        )

    return await pipeline._logged(
        ctx,
        "propose_trade",
        public_params,
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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)

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

    return await pipeline._logged(ctx, "set_policies", {"assignments": assignments}, _run)


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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)
    match action:
        case "attack":
            if target_x is None or target_y is None:
                return "Error: attack requires target_x and target_y"
            result = await pipeline._logged(
                ctx,
                "city_action",
                {
                    "city_id": city_id,
                    "action": action,
                    "target_x": target_x,
                    "target_y": target_y,
                },
                lambda: gs.city_attack(city_id, target_x, target_y),
            )
            pipeline._get_camera(ctx).push(target_x, target_y, "city attack")
            return result
        case "keep" | "reject" | "raze" | "liberate_founder" | "liberate_previous":
            return await pipeline._logged(
                ctx,
                "city_action",
                {"city_id": city_id, "action": action},
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
    gs = pipeline._get_game(ctx)
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

    result = await pipeline._logged(ctx, "unit_action", params, _run)
    if (
        action.lower() in ("move", "attack", "trade_route", "teleport")
        and target_x is not None
        and target_y is not None
    ):
        pipeline._get_camera(ctx).push(target_x, target_y, f"{action}→({target_x},{target_y})")
    return result


@mcp.tool()
async def skip_remaining_units(ctx: Context) -> str:
    """Skip all units that still have moves remaining.

    Useful after diplomacy encounters invalidate all standing orders.
    Uses GameCore FinishMoves on each unit — fast, reliable, no async issues.
    """
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)
    params: dict = {"city_id": city_id, "item_type": item_type, "item_name": item_name}
    if target_x is not None:
        params["target_x"] = target_x
        params["target_y"] = target_y
    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)
    return await pipeline._logged(
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
    gs = pipeline._get_game(ctx)

    async def _run():
        if category.lower() == "civic":
            return await gs.set_civic(tech_or_civic)
        return await gs.set_research(tech_or_civic)

    return await pipeline._logged(
        ctx,
        "set_research",
        {"tech_or_civic": tech_or_civic, "category": category},
        _run,
    )
