"""Server-side narration — human-readable text formatting for LLM consumption.

All functions are pure: data in, string out. No side effects, no I/O.
"""

from __future__ import annotations

from civ_mcp import lua as lq


def narrate_score_comparison(saved: dict | None, current: dict) -> str:
    """Describe paired native readings without inferring whole-world equality."""
    if saved is None:
        return (
            "Score comparison: save-time score evidence unavailable (legacy checkpoint); "
            f"current official GetScore: {current['official']}; category comparison unknown."
        )
    text = f"Score comparison: official GetScore: {saved['official']} -> {current['official']}; "
    before, after = saved["categories"], current["categories"]
    if before is None or after is None:
        text += "positive-weight category comparison unknown (complete native evidence unavailable)."
    elif before == after:
        text += "positive-weight categories unchanged."
        if saved["official"] != current["official"]:
            text += " Native total changed while the complete category vector stayed identical."
    else:
        changes = [
            f"{key}: {before.get(key, 'unreported')} -> {after.get(key, 'unreported')}"
            for key in sorted(before.keys() | after.keys()) if before.get(key) != after.get(key)
        ]
        text += "positive-weight categories changed: " + "; ".join(changes) + "."
    return text + " Score evidence alone does not establish full world-state equality."


def narrate_overview(ov: lq.GameOverview) -> str:
    diff_str = f" | {ov.difficulty}" if ov.difficulty else ""
    speed_str = ""
    if ov.game_speed_name:
        if ov.speed_cost_multiplier != 100:
            speed_str = (
                f" | {ov.game_speed_name} speed ({ov.speed_cost_multiplier}% costs)"
            )
        else:
            speed_str = f" | {ov.game_speed_name} speed"
    lines = []
    lines.extend(
        [
            f"Turn {ov.turn}{f'/{ov.max_turns}' if ov.max_turns else ''} | {ov.civ_name} ({ov.leader_name}) | Score: {ov.score}{diff_str}{speed_str}",
            f"Gold: {ov.gold:.0f} ({ov.gold_per_turn:+.0f}/turn)"
            + (
                f" | Income: {ov.gold_income:.0f} | Maintenance: -{ov.total_maintenance:.0f} (units: {ov.unit_maintenance})"
                if ov.gold_income > 0 or ov.total_maintenance > 0
                else ""
            )
            + f" | Science: {ov.science_yield:.1f} | Culture: {ov.culture_yield:.1f} | Faith: {ov.faith:.0f} | Favor: {ov.diplomatic_favor} ({ov.favor_per_turn:+d}/turn)",
            f"Research: {ov.current_research} | Civic: {ov.current_civic}",
            f"Cities: {ov.num_cities} | Population: {ov.total_population} | Units: {ov.num_units}"
            + (
                " — "
                + ", ".join(
                    f"{count} {name}"
                    for name, count in sorted(
                        ov.unit_breakdown.items(), key=lambda x: x[1], reverse=True
                    )
                )
                if ov.unit_breakdown
                else ""
            ),
        ]
    )
    lines.append(f"Identity: civilization_type={ov.civilization_type or 'unknown'} | leader_type={ov.leader_type or 'unknown'}")
    if ov.score_categories is None:
        lines.append("Score categories (GetCategoryScore): unknown; complete native evidence unavailable.")
    else:
        lines.append("Score categories (GetCategoryScore, positive-weight): " + "; ".join(
            f"{category.name} [{category.category_type}]: {category.score}"
            for category in ov.score_categories
        ))
        consistency = (
            "matches official GetScore"
            if ov.score_category_total == ov.score
            else f"differs from official GetScore: {ov.score}; native aggregate and category readings are inconsistent"
        )
        lines.append(f"Diagnostic category sum: {ov.score_category_total} ({consistency}).")
    if ov.total_land > 0:
        pct = ov.explored_land * 100 // ov.total_land
        lines.append(
            f"Explored: {pct}% of land ({ov.explored_land}/{ov.total_land} tiles)"
        )
    if ov.religions_max > 0:
        if ov.our_religion:
            lines.append(
                f"Religion: {ov.our_religion} ({ov.religions_founded}/{ov.religions_max} slots)"
            )
        elif ov.religions_founded >= ov.religions_max:
            others = ", ".join(
                f"{r.civ_name}: {r.religion_name}" for r in (ov.founded_religions or [])
            )
            lines.append(
                f"Religion: NONE — all {ov.religions_max} slots filled ({others})"
            )
        else:
            remaining = ov.religions_max - ov.religions_founded
            lines.append(
                f"Religion: none yet ({remaining}/{ov.religions_max} slots remaining — Great Prophet needed)"
            )
    if ov.enabled_victories:
        vname_map = {
            "VICTORY_TECHNOLOGY": "Science",
            "VICTORY_CONQUEST": "Domination",
            "VICTORY_CULTURE": "Culture",
            "VICTORY_RELIGIOUS": "Religious",
            "VICTORY_DIPLOMATIC": "Diplomatic",
        }
        all_types = [
            "VICTORY_TECHNOLOGY",
            "VICTORY_CONQUEST",
            "VICTORY_CULTURE",
            "VICTORY_RELIGIOUS",
            "VICTORY_DIPLOMATIC",
        ]
        enabled = [vname_map[v] for v in all_types if v in ov.enabled_victories]
        disabled = [vname_map[v] for v in all_types if v not in ov.enabled_victories]
        lines.append(
            f"Victory: {', '.join(enabled)} only"
            + (f" (disabled: {', '.join(disabled)})" if disabled else "")
        )
    if ov.era_name:
        lines.append(f"Era: {ov.era_name} | Current age: {ov.current_age or 'unknown'}")
        lines.append(
            f"Era score: {ov.era_score} | Next-era thresholds (Dark: {ov.era_dark_threshold}, Golden: {ov.era_golden_threshold})"
        )
    if ov.rankings:
        all_scores = [(ov.civ_name, ov.score)] + [
            (r.civ_name, r.score) for r in ov.rankings
        ]
        all_scores.sort(key=lambda x: x[1], reverse=True)
        rank_strs = [f"{name} {score}" for name, score in all_scores]
        lines.append(f"Rankings: {' > '.join(rank_strs)}")
    return "\n".join(lines)


_RANK_NAMES = {1: "Recruit", 2: "Agent", 3: "Special Agent", 4: "Senior Agent"}


def narrate_spies(spies: list[lq.SpyInfo]) -> str:
    if not spies:
        return "No spies available yet."
    lines = [f"Spies ({len(spies)}):"]
    for s in spies:
        rank_name = _RANK_NAMES.get(s.rank, f"Rank {s.rank}")
        loc = f"({s.x},{s.y})"
        if s.city_name != "none":
            owner_tag = " [own]" if s.city_owner == 0 else ""
            loc = f"{s.city_name}{owner_tag} ({s.x},{s.y})"
        if s.unit_id is None:
            lines.append(f"  [{rank_name}] {s.name} — in transit to {loc} | return_turn:{s.return_turn} | unit ID unavailable until arrival")
            continue
        ops = ", ".join(s.available_ops) if s.available_ops else "none"
        mission_tag = (
            f" | mission: {s.current_mission}" if s.current_mission != "none" else ""
        )
        escape_tag = (" ** [ESCAPING — needs escape route] ** routes: " + (", ".join(s.escape_routes) or "UNKNOWN")) if s.is_escaping else ""
        lines.append(
            f"  id:{s.unit_id} [{rank_name}] {s.name} — at {loc} | moves:{s.moves}"
            f" | xp:{s.xp}{mission_tag} | ops: {ops}{escape_tag}"
        )
    if not any(s.unit_id is not None for s in spies):
        return "\n".join(lines)
    lines.append("")
    lines.append("Actions:")
    if any(s.is_escaping for s in spies):
        lines.append("  Escape: spy_action(unit_id, action='escape:<listed district>', target_x=0, target_y=0) — choose one legal route; route selection does not confirm completed travel.")
    lines.append(
        "  Travel: spy_action(unit_id, action='travel', target_x, target_y)"
        " — send spy to own city or city-state"
    )
    lines.append(
        "  Mission: spy_action(unit_id, action=MISSION_TYPE, target_x, target_y)"
        " — spy must already be in target city"
    )
    lines.append(
        "  Mission types: COUNTERSPY, GAIN_SOURCES, SIPHON_FUNDS, STEAL_TECH_BOOST, DISRUPT_ROCKETRY,"
        " SABOTAGE_PRODUCTION, GREAT_WORK_HEIST, RECRUIT_PARTISANS,"
        " NEUTRALIZE_GOVERNOR, FABRICATE_SCANDAL"
    )
    return "\n".join(lines)


def narrate_units(
    units: list[lq.UnitInfo],
    threats: list[lq.ThreatInfo] | None = None,
    trade_status: lq.TradeRouteStatus | None = None,
) -> str:
    if not units:
        return "No units."
    # Build trader route lookup: unit_id -> TraderInfo
    trader_routes: dict[int, lq.TraderInfo] = {}
    if trade_status:
        for t in trade_status.traders:
            if t.on_route:
                trader_routes[t.unit_id] = t
    lines = [f"{len(units)} units:"]
    for u in units:
        strength = ""
        if u.combat_strength > 0:
            strength = f" CS:{u.combat_strength}"
            if u.ranged_strength > 0:
                strength += f" RS:{u.ranged_strength}"
        status = ""
        if u.health < u.max_health:
            status = f" [HP: {u.health}/{u.max_health}]"
        if u.moves_remaining < 0.01:
            status += " (no moves)"
        # Annotate traders on active routes
        route_flag = ""
        if u.unit_id in trader_routes:
            tr = trader_routes[u.unit_id]
            route_type = "Domestic" if tr.is_domestic else "International"
            route_flag = (
                f" [ON ROUTE: {tr.route_origin} -> {tr.route_dest} ({route_type})]"
            )
        charges = f" charges:{u.build_charges}" if u.build_charges > 0 else ""
        religion_flag = ""
        if u.religion:
            short = u.religion.replace("RELIGION_", "")
            religion_flag = f" [{short}]"
        promo_flag = " **NEEDS PROMOTION**" if u.needs_promotion else ""
        upgrade_flag = ""
        if u.can_upgrade:
            upgrade_flag = f" **CAN UPGRADE to {u.upgrade_target} ({u.upgrade_cost}g)**"
        moves_disp = (
            f"{int(u.moves_remaining)}/{int(u.max_moves)}"
            if u.moves_remaining == int(u.moves_remaining)
            else f"{u.moves_remaining:.1f}/{int(u.max_moves)}"
        )
        lines.append(
            f"  {u.name} ({u.unit_type}) at ({u.x},{u.y}) —{strength} "
            f"moves {moves_disp}{charges}{religion_flag}{status}{route_flag}{promo_flag}{upgrade_flag} "
            f"[id:{u.unit_id}, idx:{u.unit_index}]"
        )
        if u.targets:
            for t in u.targets:
                lines.append(f"    >> CAN ATTACK: {t}")
        if u.valid_improvements:
            lines.append(f"    >> Can build: {', '.join(u.valid_improvements)}")
    if threats:
        lines.append("")
        lines.append(f"Nearby threats ({len(threats)}):")
        # Group threats by owner
        from collections import defaultdict

        by_owner: dict[int, list[lq.ThreatInfo]] = defaultdict(list)
        for t in threats:
            by_owner[t.owner_id].append(t)
        # Sort: at-war/barbarian owners first, then by unit count
        for owner_id in sorted(
            by_owner.keys(),
            key=lambda oid: (0 if oid == 63 else 1, -len(by_owner[oid])),
        ):
            owner_threats = by_owner[owner_id]
            owner_name = owner_threats[0].owner_name
            if owner_id == 63:
                label = f"Barbarian ({len(owner_threats)} unit{'s' if len(owner_threats) != 1 else ''}):"
            else:
                cs_tag = " (city-state)" if owner_threats[0].is_city_state else ""
                label = f"{owner_name}{cs_tag} ({len(owner_threats)} unit{'s' if len(owner_threats) != 1 else ''}):"
            lines.append(f"  {label}")
            for t in sorted(owner_threats, key=lambda t: t.distance):
                rs_str = f" RS:{t.ranged_strength}" if t.ranged_strength > 0 else ""
                lines.append(
                    f"    {t.unit_type} at ({t.x},{t.y}) — CS:{t.combat_strength}{rs_str} "
                    f"HP:{t.hp}/{t.max_hp} ({t.distance} tiles away)"
                )
    return "\n".join(lines)


def narrate_builder_tasks(report: lq.BuilderTaskReport) -> str:
    tasks, builders = report.tasks, report.builders
    ready = [b for b in builders if b.ready is True]
    lines = [f"=== BUILDER TASKS ({len(tasks)} tasks, {len(ready)} native-ready builders) ==="]
    if report.resource_visibility_known is not True:
        lines.append(
            "Resource advice incomplete: native resource visibility unknown; "
            "safe terrain and repair candidates retained."
        )
    lines.append("Advisory candidates; confirm current native eligibility before acting.")
    if not builders:
        lines.append("No builders with charges available.")
    if not tasks:
        if report.resource_visibility_known is True:
            lines.append("No improvement candidates identified in your territory.")
        else:
            lines.append("No confirmed improvement candidates identified; resource advice is incomplete.")
        if builders:
            lines.append("")
            _append_builder_list(lines, builders)
        return "\n".join(lines)

    # Observation order only: repair/resource/terrain are facts, not priorities.
    lines.append('Terrain/resource mapping is advisory; no remote operation checks. Distances are geometric, not path or reachability.')
    for t in tasks:
        if t.resource_class == 'pillaged':
            action = f'repair {t.resource}'
        else:
            imp = t.improvement.removeprefix('IMPROVEMENT_')
            action = (f'{t.resource} — ' if t.resource else '') + f'build {imp}'
        builder = ''
        if t.nearest_builder_id >= 0:
            builder = f' — nearest builder id:{t.nearest_builder_id}, hex distance {t.distance} (reachability unknown)'
        lines.append(f'  ({t.x},{t.y}): {action} [city: {t.city_name}]{builder}; {t.eligibility_note}')
    lines.append("")
    _append_builder_list(lines, builders)
    return "\n".join(lines)


def _append_builder_list(lines: list[str], builders: list[lq.BuilderInfo]) -> None:
    lines.append(f'BUILDERS ({len(builders)}):')
    for b in builders:
        ready = {True:'yes',False:'no',None:'unknown'}[b.ready]
        lines.append(f'  id:{b.unit_id} at ({b.x},{b.y}) — {b.charges} charges, {b.moves:g} moves; native ready {ready}')


def narrate_cities(
    cities: list[lq.CityInfo], distances: list[str] | None = None
) -> str:
    if not cities:
        return "No cities."
    lines = [f"{len(cities)} cities:"]
    for c in cities:
        building = (
            c.currently_building
            if c.currently_building not in ("NONE", "nothing")
            else "nothing"
        )
        prod_str = f"Building: {building}"
        if building != "nothing" and c.production_turns_left > 0:
            prod_str += f" ({c.production_turns_left} turns)"
        defense = ""
        if c.wall_max_hp > 0:
            defense = (
                f" | Walls {c.wall_hp}/{c.wall_max_hp}"
                f" Garrison {c.garrison_hp}/{c.garrison_max_hp}"
                f" Def:{c.defense_strength}"
            )
        elif c.garrison_max_hp > 0:
            defense = (
                f" | HP:{c.garrison_hp}/{c.garrison_max_hp} Def:{c.defense_strength}"
            )
        elif c.defense_strength > 0:
            defense = f" | Def:{c.defense_strength}"
        garrison_str = (
            c.garrison_unit.replace("UNIT_", "") if c.garrison_unit else "none"
        )
        if defense:
            defense += f" Gar:{garrison_str}"
        else:
            defense = f" | Gar:{garrison_str}"
        loyalty_str = ""
        if c.loyalty_per_turn < 0 or c.loyalty < 75:
            flip_info = (
                f", flips in {c.turns_to_loyalty_flip} turns"
                if c.turns_to_loyalty_flip > 0
                else ""
            )
            loyalty_str = f" | !! Loyalty: {c.loyalty:.0f}/{c.loyalty_max:.0f} ({c.loyalty_per_turn:+.1f}/turn{flip_info})"
        # Growth display: show surplus and progress, not just turns
        if c.turns_to_grow <= 0 or c.food_surplus <= 0:
            growth_str = f"STAGNANT ({c.food_surplus:+.1f} food/t)"
        elif c.turns_to_grow <= 5:
            growth_str = f"{c.food_stored:.0f}/{c.growth_threshold} food ({c.turns_to_grow}t, {c.food_surplus:+.1f}/t)"
        else:
            growth_str = f"{c.food_stored:.0f}/{c.growth_threshold} food ({c.turns_to_grow}t, {c.food_surplus:+.1f}/t)"
        lines.append(
            f"  {c.name} (pop {c.population}) at ({c.x},{c.y}) — "
            f"Food {c.food:.0f} Prod {c.production:.0f} Gold {c.gold:.0f} "
            f"Sci {c.science:.0f} Cul {c.culture:.0f} | "
            f"Housing {c.housing:.0f} Amenities {c.amenities} | "
            f"Growth: {growth_str} | {prod_str}{defense}{loyalty_str} "
            f"[id:{c.city_id}]"
        )
        # Growth warnings
        if c.food_surplus < 0:
            lines.append(
                f"    !! STARVING: {c.food_surplus:+.1f} food/t — city will lose population!"
            )
        elif c.food_surplus == 0 and c.turns_to_grow <= 0:
            lines.append(
                "    !! STAGNANT: 0 food surplus; cause not established by this query"
            )
        elif c.turns_to_grow > 15:
            lines.append(
                f"    !! SLOW GROWTH: {c.turns_to_grow} turns to next pop; inspect food, housing and native growth modifiers"
            )
        if c.currently_building == "CORRUPTED_QUEUE":
            lines.append(
                "    !! INVALID QUEUE (nonempty, zero item hash; unchanged) — inspect production before choosing a new item"
            )
        if c.attack_targets_known is False:
            lines.append("    City ranged targets: unknown (native observation unavailable)")
        for t in c.attack_targets:
            label = "NATIVE RANGED TARGET" if c.attack_targets_known is True else "UNVERIFIED TARGET"
            lines.append(f"    >> {label}: {t}")
        if c.districts:
            dist_strs = []
            for d in c.districts:
                dtype, coords = d.split("@")
                short = dtype.replace("DISTRICT_", "")
                dist_strs.append(f"{short}({coords})")
            lines.append(f"    Districts: {' '.join(dist_strs)}")
        if c.buildings:
            lines.append(f"    Buildings: {', '.join(c.buildings)}")
        if c.pillaged_districts or c.pillaged_buildings:
            pill_names = [d.replace("DISTRICT_", "") for d in c.pillaged_districts]
            pill_bldgs = [b.replace("BUILDING_", "") for b in c.pillaged_buildings]
            all_pillaged = pill_names + pill_bldgs
            lines.append(
                f"    !! PILLAGED: {', '.join(all_pillaged)}"
                " (repair via set_city_production)"
            )
        if c.pillaged_improvements:
            pill_imps = [p.replace("@", " (") + ")" for p in c.pillaged_improvements]
            lines.append(
                f"    !! PILLAGED TILES: {', '.join(pill_imps)} (send builder to repair)"
            )
        if c.resource_visibility_known is False:
            lines.append("    Resource advice incomplete: native resource visibility unknown.")
        if c.unimproved_resources:
            res_names = [r.replace("@", " (") + ")" for r in c.unimproved_resources]
            lines.append(f"    Unimproved owned resources (eligibility unchecked): {', '.join(res_names)}")
        if c.archaeology_sites:
            lines.append(f"    Archaeology sites (not builder tasks): {', '.join(c.archaeology_sites)}")
    if distances:
        lines.append("")
        lines.append("City Distances:")
        for d in distances:
            lines.append(f"  {d}")
    return "\n".join(lines)


def narrate_pathing_estimate(est: lq.PathingEstimate) -> str:
    if est.turns is None:
        return "Path/reachability unknown: native observation missing or inconsistent."
    if est.turns == -2:
        return "Unit has no moves remaining this turn."
    if est.turns < 0:
        return (
            "No native path to the destination returned. It may be in fog, "
            "behind foreign borders, or blocked by impassable terrain."
        )
    if est.turns == 0:
        return f"Reachable this turn ({est.total_tiles} tiles in path, all within movement range)."
    wp_str = ""
    if est.waypoints and len(est.waypoints) > 2:
        wp_str = f"\n  Path: {est.waypoints[0]} -> ... -> {est.waypoints[-1]}"
    return (
        f"~{est.turns} turns (heuristic assuming current reachable tiles per turn; terrain and future costs may differ; {est.total_tiles} tiles total, "
        f"{est.reachable_this_turn} reachable this turn){wp_str}"
    )


def narrate_combat_estimate(est: lq.CombatEstimate) -> str:
    atk_type = "Ranged" if est.is_ranged else "Melee"
    mods_str = ", ".join(est.modifiers) if est.modifiers else "none"
    lines = [
        f"Native combat preview ({atk_type}):",
        f"  {est.attacker_type} (CS:{est.attacker_cs}, HP:{est.attacker_hp}) vs "
        f"{est.defender_type} (CS:{est.defender_cs}, HP:{est.defender_hp})",
        f"  Modifiers: {mods_str}",
        f"  Est damage to defender: ~{est.est_damage_to_defender}",
    ]
    if not est.is_ranged:
        lines.append(f"  Est damage to attacker: ~{est.est_damage_to_attacker}")
    if est.est_damage_to_defender >= est.defender_hp:
        lines.append("  -> LIKELY KILL")
    elif not est.is_ranged and est.est_damage_to_attacker >= est.attacker_hp:
        lines.append("  -> WARNING: attacker likely dies!")
    return "\n".join(lines)


def narrate_city_production(options: list[lq.ProductionOption]) -> str:
    if not options:
        return "No production options available."
    units = [o for o in options if o.category == "UNIT" and not o.is_repair]
    buildings = [o for o in options if o.category == "BUILDING" and not o.is_repair]
    districts = [o for o in options if o.category == "DISTRICT" and not o.is_repair]
    projects = [o for o in options if o.category == "PROJECT"]
    repairs = [o for o in options if o.is_repair]
    def number(value):
        return 'unknown' if value is None else str(value).removesuffix('.0')

    def _fmt(o: lq.ProductionOption) -> str:
        t = f", {o.turns} turns" if o.turns is not None else ", turns unknown"
        purchases = []
        for q in o.purchases:
            currency = 'gold' if q.currency == 'YIELD_GOLD' else 'faith'
            price = number(q.cost) if q.cost is not None else 'unavailable'
            state = {True: 'can buy', False: 'cannot buy', None: 'eligibility unknown'}[q.can_purchase]
            balance = f"; balance {number(q.balance)}" if q.balance is not None else '; balance unknown'
            if q.cost is not None and q.balance is not None and q.balance < q.cost:
                balance += f"; shortfall {number(q.cost-q.balance)}"
            reasons = '; ' + '; '.join(q.reasons).replace('[NEWLINE]', ' / ').replace('\n', ' / ') if q.reasons else ''
            purchases.append(f"{currency} {price} [{state}{balance}{reasons}]")
        if not o.purchases and o.gold_cost >= 0:
            purchases.append(f"gold {number(o.gold_cost)} [eligibility unknown; legacy price only]")
        buy = '; ' + '; '.join(purchases) if purchases else ''
        production = f"production {number(o.cost)}{t}" if o.can_produce is True else (
            'production unavailable' if o.can_produce is False else 'production eligibility unknown')
        if o.can_produce is True:
            production+=f", invested {number(o.production_progress)}"
        if o.base_cost is not None and o.base_cost!=o.cost:
            production+=f", static base {number(o.base_cost)}"
        tag = " [REPAIR]" if o.is_repair else ""
        coords = ""
        if o.is_repair and o.repair_x is not None:
            coords = f" at ({o.repair_x},{o.repair_y})"
        return f"  {o.item_name}{tag}{coords} ({production}{buy})"

    lines = []
    if units:
        lines.append("Units:")
        for o in units:
            lines.append(_fmt(o))
    if buildings:
        lines.append("Buildings:")
        for o in buildings:
            lines.append(_fmt(o))
    if districts:
        lines.append("Districts:")
        for o in districts:
            lines.append(_fmt(o))
    if projects:
        lines.append("Projects:")
        for o in projects:
            lines.append(_fmt(o))
    if repairs:
        lines.append("Repairs (pillaged — queue to fix):")
        for o in repairs:
            lines.append(_fmt(o))
    return "\n".join(lines)


def narrate_map(tiles: list[lq.TileInfo]) -> str:
    if not tiles:
        return "No tiles."
    lines = [f"{len(tiles)} tiles:",
             "Yield source: GameCore_Tuner Plot:GetYield; modifier completeness/native age unverified; "
             "not interchangeable with InGame city-worked yields. "
             "Within yield blocks, unlisted G/S/C/Fa=0; no yield block=unknown."]

    def number(value):
        # Keep the parsed native precision without adding .0 to ordinary integers.
        return str(int(value)) if value == int(value) else str(value)

    for t in tiles:
        parts = [t.terrain.replace("TERRAIN_", "")]
        if t.is_hills:
            parts.append("Hills")
        if t.feature:
            parts.append(t.feature.replace("FEATURE_", ""))
        if t.resource:
            res_label = t.resource.replace("RESOURCE_", "")
            if t.resource_class == "strategic":
                res_label += "*"
            elif t.resource_class == "luxury":
                res_label += "+"
            parts.append(f"[{res_label}]")
        if t.is_river:
            parts.append("River")
        if t.is_coastal:
            parts.append("Coast")
        if t.is_fresh_water and not t.is_river:
            # Fresh water from lake/oasis (river already implies fresh water)
            parts.append("FreshWater")
        if t.improvement:
            imp_label = t.improvement.replace("IMPROVEMENT_", "")
            if t.is_pillaged:
                imp_label += " PILLAGED"
            parts.append(f"({imp_label})")
        if t.route_type >= 0:
            route_name = "Railroad" if t.route_type == 4 else "Road"
            parts.append(f"({route_name})")
        if t.district:
            parts.append(f"[{t.district.replace('DISTRICT_', '')}]")
        if t.yields:
            f, p, g = t.yields[0], t.yields[1], t.yields[2]
            yield_str = f"F:{number(f)} P:{number(p)}"
            if g != 0:
                yield_str += f" G:{number(g)}"
            # Include science/culture/faith only if non-zero
            for label, val in [
                ("S", t.yields[3]),
                ("C", t.yields[4]),
                ("Fa", t.yields[5]),
            ]:
                if val != 0:
                    yield_str += f" {label}:{number(val)}"
            parts.append(f"{{{yield_str}}}")
        if t.owner_id < 0:
            owner = ""
        elif t.owner_name:
            label = t.owner_name.replace(":CS", " [City-State]")
            owner = f" (owned by {label})"
        else:
            owner = f" (owned by player {t.owner_id})"
        vis_tag = ""
        if t.visibility == "revealed":
            vis_tag = " [fog]"
        unit_str = ""
        if t.units:
            unit_str = f" **[{', '.join(t.units)}]**"
        own_str = ""
        if t.own_units:
            own_str = f" [my: {', '.join(t.own_units)}]"
        mv_str = ""
        if t.movement_cost > 1:
            mv_str = f" [mv:{t.movement_cost}]"
        lines.append(
            f"  ({t.x},{t.y}): {' '.join(parts)}{owner}{vis_tag}{mv_str}{own_str}{unit_str}"
        )
    return "\n".join(lines)


def narrate_strategic_map(data: lq.StrategicMapData) -> str:
    dir_names = ["N", "NE", "SE", "S", "SW", "NW"]
    lines = ["=== STRATEGIC MAP ===", ""]

    # Fog boundaries
    lines.append("FOG BOUNDARIES (distance to unexplored, -1 = fully explored):")
    for fb in data.fog_boundaries:
        dir_strs = []
        explore_dirs = []
        for i, d in enumerate(fb.fog_distances):
            label = dir_names[i] if i < len(dir_names) else f"D{i}"
            if d == -1:
                dir_strs.append(f"{label}:clear")
            else:
                dir_strs.append(f"{label}:{d}")
                if d <= 5:
                    explore_dirs.append(label)
        suffix = ""
        if explore_dirs:
            suffix = f" <- EXPLORE {'/'.join(explore_dirs)}!"
        lines.append(
            f"  {fb.city_name} ({fb.city_x},{fb.city_y}): {' '.join(dir_strs)}{suffix}"
        )

    # Unclaimed resources
    luxuries = [r for r in data.unclaimed_resources if "LUXURY" in r.resource_class]
    strategics = [
        r for r in data.unclaimed_resources if "STRATEGIC" in r.resource_class
    ]
    if luxuries or strategics:
        lines.append("")
        lines.append("UNCLAIMED RESOURCES (revealed, unowned):")
        for r in luxuries:
            name = r.resource_type.replace("RESOURCE_", "")
            lines.append(f"  {name}+ at ({r.x},{r.y}) — luxury")
        for r in strategics:
            name = r.resource_type.replace("RESOURCE_", "")
            lines.append(f"  {name}* at ({r.x},{r.y}) — strategic")
    elif not data.fog_boundaries:
        lines.append("\nNo data available.")

    return "\n".join(lines)


def narrate_settle_candidates(candidates: list[lq.SettleCandidate]) -> str:
    if not candidates:
        return "No settlement terrain candidates confirmed in the requested scope."
    lines = [f"{len(candidates)} settlement terrain possibilities (unranked; native founding eligibility unverified):",
             "Current F/P totals cover the revealed radius-3 footprint only when every tile is observed. Fogged dynamic yields/resources stay unknown; subtotals are current observations, not worked-city or net yields. Center ownership may be unknown; loyalty is the native settler warning, not a full forecast."]
    _WATER = {"fresh": "fresh water", "coast": "coast", "none": "no water"}
    for i, c in enumerate(candidates, 1):
        water = _WATER.get(c.water_type, c.water_type)
        loyalty = 'unknown' if c.loyalty_pressure is None else f'{c.loyalty_pressure:g}'
        number=lambda value:'unknown' if value is None else str(value)
        owner='unknown' if c.owner_id is None else ('unclaimed' if c.owner_id==-1 else str(c.owner_id))
        visible='unknown' if c.visible_now is None else ('yes' if c.visible_now else 'no')
        resource_status = '' if c.resource_visibility_known is True else '; resource observations incomplete'
        header = f"  #{i} ({c.x},{c.y}): F:{number(c.total_food)} P:{number(c.total_prod)} — {water}, revealed defense terrain:{c.defense_score}; owner {owner}, visible now {visible}; native loyalty warning {loyalty}{resource_status}"
        lines.append(header)
        if c.dynamic_tiles_total is not None:
            lines.append(f"     Current observed subtotal F:{number(c.observed_food_subtotal)} ({c.food_observed_tiles}/{c.dynamic_tiles_total} tiles), P:{number(c.observed_prod_subtotal)} ({c.production_observed_tiles}/{c.dynamic_tiles_total}); resource observations {c.resource_observed_tiles}/{c.dynamic_tiles_total}; visible tiles {c.current_visible_tiles}/{c.dynamic_tiles_total}.")
        if c.resources:
            # Format: [S] IRON, [L] DIAMONDS, [B] WHEAT
            res_parts = []
            for r in c.resources:
                if ":" in r:
                    prefix, name = r.split(":", 1)
                    res_parts.append(f"[{prefix}] {name}")
                else:
                    res_parts.append(r)
            lines.append(f"     {', '.join(res_parts)}")
    return "\n".join(lines)


def resource_plot_description(resource) -> str:
    owner='unknown' if resource.owner_id is None else ('unclaimed' if resource.owner_id==-1 else f'player {resource.owner_id}')
    state=lambda value:'unknown' if value is None else ('yes' if value else 'no')
    return (f"owner {owner}; visible now {state(resource.visible_now)}; "
        f"facility {resource.improvement_type or 'unknown'}; pillaged {state(resource.pillaged)}; "
        f"work unit {resource.work_unit or 'unknown'} (type hint, eligibility unchecked)")


def narrate_empire_resources(
    stockpiles: list[lq.ResourceStockpile],
    owned: list[lq.OwnedResource],
    nearby: list[lq.NearbyResource],
    luxuries: dict[str, int],
    *, visibility_known: bool | None = None,
) -> str:
    if not stockpiles and not owned and not nearby and not luxuries:
        return ("No resources found in the completed empire resource scan." if visibility_known is True else
            "Resource scan incomplete/unknown; no complete resource absence established.")
    _CLASS_PREFIX = {"strategic": "S", "luxury": "L", "bonus": "B"}
    lines = ["Empire Resources:"]
    if visibility_known is not True:
        lines.append('Resource visibility/scan completeness unknown; partial observations follow.')
    # Strategic stockpiles
    visible_strats = [s for s in stockpiles]
    if visible_strats:
        lines.append("\nStrategic Stockpiles:")
        for s in visible_strats:
            net = s.per_turn - s.demand
            net_str = f"+{net}" if net >= 0 else str(net)
            parts = [f"  {s.name}: {s.amount}/{s.cap} ({net_str}/turn)"]
            details = []
            if s.per_turn > 0:
                details.append(f"income {s.per_turn}")
            if s.imported > 0:
                details.append(f"import {s.imported}")
            if s.demand > 0:
                details.append(f"demand {s.demand}")
            if details:
                parts.append(f" [{', '.join(details)}]")
            lines.append("".join(parts))
    # Luxury summary
    if luxuries:
        lines.append("\nLuxury Resources:")
        for name, count in sorted(luxuries.items()):
            extra = f" ({count - 1} tradeable)" if count > 1 else ""
            lines.append(f"  {name}: {count}{extra}")
    # Owned tile resources grouped by class
    for cls, label in [
        ("strategic", "Strategic Tiles"),
        ("luxury", "Luxury Tiles"),
        ("bonus", "Bonus Tiles"),
        ("artifact", "Archaeology Sites"),
        ("unknown", "Other Resource Observations"),
    ]:
        items = [r for r in owned if r.resource_class == cls]
        if not items:
            continue
        lines.append(f"\n{label}:")
        for r in items:
            lines.append(f"  {r.name} at ({r.x},{r.y}) — " + resource_plot_description(r))
    # Nearby unclaimed
    if nearby:
        lines.append("\nNearby Resource Observations (ownership shown separately):")
        for r in nearby:
            prefix = _CLASS_PREFIX.get(r.resource_class, "?")
            lines.append(
                f"  [{prefix}] {r.name} at ({r.x},{r.y}) — {r.distance} tiles from {r.nearest_city}; " + resource_plot_description(r)
            )
    lines.append('Facility presence/pillage does not confirm resource supply or yields. Purchase/development eligibility unchecked; fogged foreign plot details stay unknown.')
    return "\n".join(lines)


def narrate_diplomacy(civs: list[lq.CivInfo]) -> str:
    if not civs:
        return "No known civilizations."
    lines = [f"{len(civs)} civilizations:"]
    for c in civs:
        if not c.has_met:
            lines.append(f"  {c.civ_name} ({c.leader_name}) — not met")
            continue
        # Header with state and score
        war_str = " **AT WAR**" if c.is_at_war else ""
        if c.alliance_type:
            level_str = f" Lv{c.alliance_level}" if c.alliance_level > 0 else ""
            alliance_str = f" ({c.alliance_type} alliance{level_str})"
        else:
            alliance_str = ""
        lines.append(
            f"  {c.civ_name} ({c.leader_name}) — {c.diplomatic_state} ({c.relationship_score:+d}){war_str}{alliance_str} [player {c.player_id}]"
        )
        lines.append(f"    Identity: civilization_type={c.civilization_type or 'unknown'} | leader_type={c.leader_type or 'unknown'}")
        # City details
        if c.num_cities > 0:
            if c.visible_cities:
                city_parts = []
                for vc in c.visible_cities:
                    loy_warn = ""
                    if vc.loyalty_per_turn < -0.5 or vc.loyalty < 50:
                        loy_warn = (
                            f" !! loy {vc.loyalty:.0f} ({vc.loyalty_per_turn:+.1f}/t)"
                        )
                    walls_str = " [walls]" if vc.has_walls else ""
                    city_parts.append(
                        f"{vc.name} pop {vc.population} ({vc.x},{vc.y}){walls_str}{loy_warn}"
                    )
                hidden = c.num_cities - len(c.visible_cities)
                fog_str = f" + {hidden} in fog" if hidden > 0 else ""
                lines.append(
                    f"    Cities ({c.num_cities}): {'; '.join(city_parts)}{fog_str}"
                )
            else:
                lines.append(f"    Cities: {c.num_cities} (all in fog)")
        # Military strength comparison
        if c.military_strength > 0:
            # Find our military strength from the MILITARY line (stored per-civ parse)
            our_mil = getattr(c, "_our_military", 0)
            if our_mil > 0:
                ratio = c.military_strength / our_mil
                ratio_str = f" ({ratio:.1f}x)" if ratio >= 1.2 or ratio <= 0.8 else ""
                threat_flag = ""
                if ratio >= 1.5 and c.diplomatic_state in (
                    "UNFRIENDLY",
                    "DENOUNCED",
                    "WAR",
                ):
                    threat_flag = " !! MILITARY THREAT"
                elif ratio >= 2.0:
                    threat_flag = " !! MUCH STRONGER"
                lines.append(
                    f"    Military: {c.military_strength} vs our {our_mil}{ratio_str}{threat_flag}"
                )
            else:
                lines.append(f"    Military: {c.military_strength}")
        # Access: delegations/embassies
        access = []
        if c.has_delegation:
            access.append("we have delegation")
        if c.they_have_delegation:
            access.append("they have delegation")
        if c.has_embassy:
            access.append("we have embassy")
        if c.they_have_embassy:
            access.append("they have embassy")
        if c.grievances > 0:
            access.append(f"grievances: {c.grievances}")
        if access:
            lines.append(f"    Access: {', '.join(access)}")
        # Relationship modifiers
        if c.modifiers:
            for m in c.modifiers:
                lines.append(f"    {m.score:+d} {m.text}")
        # Defensive pacts
        if c.defensive_pacts:
            pact_names = []
            for pid in c.defensive_pacts:
                # Find the civ name for this player ID
                pact_civ = next((ci for ci in civs if ci.player_id == pid), None)
                if pact_civ:
                    pact_names.append(f"{pact_civ.civ_name} (player {pid})")
                else:
                    pact_names.append(f"player {pid}")
            lines.append(f"    !! DEFENSIVE PACTS with: {', '.join(pact_names)}")
        # Agendas
        if c.agendas:
            for a in c.agendas:
                if a.name == "???":
                    lines.append(f"    Agenda: [Hidden] — {a.description}")
                else:
                    prefix = "[Hidden] " if a.category == "HIDDEN" else ""
                    lines.append(f"    Agenda: {prefix}{a.name} — {a.description}")
        # Available actions
        if c.available_actions:
            actions_str = ", ".join(
                a.replace("_", " ").title() for a in c.available_actions
            )
            lines.append(f"    Can: {actions_str}")
    return "\n".join(lines)


def narrate_diplomacy_sessions(context: lq.DiplomacyContext) -> str:
    if context.status == "UNKNOWN":
        identities = ", ".join(s.other_civ_name for s in context.sessions)
        return (f"Diplomacy context UNKNOWN: {context.reason}. "
                f"Verified session participants: {identities or 'unavailable'}. "
                "Read again before responding; current dialogue and incoming proposal are unverified.")
    sessions = context.sessions
    if context.status == "NONE":
        return "No pending diplomacy sessions."
    lines = [f"Diplomacy context {context.status}: {len(sessions)} pending session(s):"]
    if context.negotiation_summary:
        lines.append(context.negotiation_summary)
    for s in sessions:
        lines.append(
            f"  {s.other_civ_name} ({s.other_leader_name}) — "
            f"session {s.session_id}, player {s.other_player_id}"
        )
        if s.dialogue_text:
            lines.append(f'  Says: "{s.dialogue_text}"')
        if s.reason_text:
            lines.append(f"  Reason: {s.reason_text}")
        if s.buttons:
            lines.append(f"  Buttons: {s.buttons}")
        # Phase-appropriate guidance
        if s.deal_summary:
            summary = s.deal_summary
            if context.negotiation_summary and context.negotiation_phase != "ACCEPTABLE":
                summary = summary.replace("They offer:", "Native incoming items from them:").replace("You offer:", "Native incoming items from us:")
            lines.append(f"  Deal: {summary}")
            lines.append(
                f"  Current incoming deal or demand; inspect negotiation eligibility before respond_to_trade(other_player_id={s.other_player_id}, accept=True/False)"
            )
        elif getattr(s, "choice_status", "UNKNOWN") == "KNOWN":
            choices = ", ".join(key.removeprefix("CHOICE_") for key in s.choice_keys)
            lines.append("  Current native choices: " + (choices or "none"))
        else:
            lines.append("  Current native choices UNKNOWN; no response is authorized by visible text alone.")
    return "\n".join(lines)


def narrate_tech_civics(tc: lq.TechCivicStatus) -> str:
    lines = []
    completed = ""
    if tc.completed_tech_count > 0 or tc.completed_civic_count > 0:
        completed = f" | Completed: {tc.completed_tech_count} techs, {tc.completed_civic_count} civics"
    if tc.current_research != "None":
        research_turns = f"{tc.current_research_turns} turns" if tc.current_research_turns >= 0 else "turns unknown"
        lines.append(
            f"Researching: {tc.current_research} ({research_turns}){completed}"
        )
    else:
        lines.append(f"No technology being researched!{completed}")
    if tc.current_civic != "None":
        turns = (
            f"{tc.current_civic_turns} turns"
            if tc.current_civic_turns is not None and tc.current_civic_turns >= 0
            else "turns unknown"
        )
        if tc.current_civic_turns_source == "remaining_cost_estimate":
            turns += " (estimate)"
        progress = (
            f"{tc.current_civic_progress_pct}%, "
            if tc.current_civic_progress_pct is not None else ""
        )
        if tc.current_civic_progress_pct is None and tc.current_civic_turns_source is not None:
            progress = "progress unknown, "
        lines.append(f"Civic: {tc.current_civic} ({progress}{turns})")
    else:
        lines.append("No civic being progressed!")
    if tc.available_techs:
        lines.append("\nAvailable techs:")
        for t in sorted(tc.available_techs, key=lambda x: x.turns if x.turns >= 0 else float("inf")):
            boost_str = " BOOSTED" if t.boosted else ""
            boost_desc = f" [Boost: {t.boost_desc}]" if t.boost_desc else ""
            unlocks = f" -> {t.unlocks}" if t.unlocks else ""
            era_str = f" [{t.era.replace('ERA_', '')}]" if t.era else ""
            prereq_str = f" (needs: {t.prereqs})" if t.prereqs else ""
            turns = f"{t.turns} turns" if t.turns >= 0 else "turns unknown"
            lines.append(
                f"  {t.name} ({t.tech_type}){era_str} — {t.progress_pct}%, {turns}{boost_str}{boost_desc}{unlocks}{prereq_str}"
            )
    if tc.available_civics:
        lines.append("\nAvailable civics:")
        for c in sorted(
            tc.available_civics,
            key=lambda x: x.turns if x.turns is not None and x.turns >= 0 else float("inf"),
        ):
            boost_str = " BOOSTED" if c.boosted else ""
            boost_desc = f" [Boost: {c.boost_desc}]" if c.boost_desc else ""
            era_str = f" [{c.era.replace('ERA_', '')}]" if c.era else ""
            prereq_str = f" (needs: {c.prereqs})" if c.prereqs else ""
            progress = (
                f"{c.progress_pct}%" if c.progress_pct is not None else "progress unknown"
            )
            turns = (
                f"{c.turns} turns"
                if c.turns is not None and c.turns >= 0 else "turns unknown"
            )
            if c.turns_source == "remaining_cost_estimate":
                turns += " (estimate)"
            lines.append(
                f"  {c.name} ({c.civic_type}){era_str} — {progress}, {turns}{boost_str}{boost_desc}{prereq_str}"
            )
    era_order = [
        "ERA_ANCIENT",
        "ERA_CLASSICAL",
        "ERA_MEDIEVAL",
        "ERA_RENAISSANCE",
        "ERA_INDUSTRIAL",
        "ERA_MODERN",
        "ERA_ATOMIC",
        "ERA_INFORMATION",
        "ERA_FUTURE",
    ]
    era_rank = {e: i for i, e in enumerate(era_order)}

    if tc.locked_techs:
        lines.append("\nLocked techs (prerequisites missing):")
        by_era: dict[str, list[str]] = {}
        for lt in tc.locked_techs:
            era = lt.era or "UNKNOWN"
            boost = " BOOSTED" if lt.boosted else ""
            boost_desc = f" [Boost: {lt.boost_desc}]" if lt.boost_desc else ""
            line = f"  {lt.name} ({lt.tech_type}) — needs: {', '.join(lt.missing_prereqs)}{boost}{boost_desc}"
            by_era.setdefault(era, []).append(line)
        for era in sorted(by_era, key=lambda e: era_rank.get(e, 99)):
            label = era.replace("ERA_", "")
            lines.append(f"  -- {label} --")
            lines.extend(by_era[era])

    if tc.locked_civics:
        lines.append("\nLocked civics (prerequisites missing):")
        by_era_c: dict[str, list[str]] = {}
        for lc in tc.locked_civics:
            era = lc.era or "UNKNOWN"
            boost = " BOOSTED" if lc.boosted else ""
            boost_desc = f" [Boost: {lc.boost_desc}]" if lc.boost_desc else ""
            line = f"  {lc.name} ({lc.civic_type}) — needs: {', '.join(lc.missing_prereqs)}{boost}{boost_desc}"
            by_era_c.setdefault(era, []).append(line)
        for era in sorted(by_era_c, key=lambda e: era_rank.get(e, 99)):
            label = era.replace("ERA_", "")
            lines.append(f"  -- {label} --")
            lines.extend(by_era_c[era])

    return "\n".join(lines)


def narrate_pending_deals(context: lq.DiplomacyContext) -> str:
    if context.outgoing_summary:
        return context.outgoing_summary + "\nCurrent owned outgoing counter: use respond_to_trade(accept=True/False) for an explicit decision."
    if context.status == "UNKNOWN":
        return (f"Incoming proposal context UNKNOWN: {context.reason}. "
                "Read get_pending_diplomacy again before responding.")
    if context.status == "DIALOGUE":
        return "Diplomacy context DIALOGUE: current encounter is dialogue; use get_pending_diplomacy."
    if context.status == "NONE":
        return "No pending trade deals (NONE: no open diplomacy session)."
    deals = context.deals
    lines = [f"Current INCOMING deal or demand: {len(deals)} proposal(s):"]
    if context.negotiation_summary:
        lines.append(context.negotiation_summary)
    for d in deals:
        lines.append(
            f"\n  From: {d.other_player_name} ({d.other_leader_name}) [player {d.other_player_id}]"
        )

        # Detect mutual items (same name on both sides — alliances, open borders, joint wars)
        their_names = {i.name for i in d.items_from_them}
        our_names = {i.name for i in d.items_from_us}
        mutual_names = their_names & our_names

        mutual = [i for i in d.items_from_them if i.name in mutual_names]
        their_only = [i for i in d.items_from_them if i.name not in mutual_names]
        our_only = [i for i in d.items_from_us if i.name not in mutual_names]

        if mutual:
            lines.append("  Mutual:")
            for item in mutual:
                dur = f" for {item.duration} turns" if item.duration > 0 else ""
                lines.append(f"    = {item.name}{dur}")
        if their_only:
            lines.append("  Native incoming items from them:" if context.negotiation_summary and context.negotiation_phase != "ACCEPTABLE" else "  They offer:")
            for item in their_only:
                dur = f" for {item.duration} turns" if item.duration > 0 else ""
                amt = (
                    f" x{item.amount}"
                    if item.amount > 1 or item.item_type == "GOLD"
                    else ""
                )
                lines.append(f"    + {item.name}{amt}{dur}")
        if our_only:
            lines.append("  Native incoming items from us:" if context.negotiation_summary and context.negotiation_phase != "ACCEPTABLE" else "  They want:")
            for item in our_only:
                dur = f" for {item.duration} turns" if item.duration > 0 else ""
                amt = (
                    f" x{item.amount}"
                    if item.amount > 1 or item.item_type == "GOLD"
                    else ""
                )
                lines.append(f"    - {item.name}{amt}{dur}")
        lines.append(
            f"  -> Inspect native negotiation eligibility before respond_to_trade(other_player_id={d.other_player_id}, accept=True/False)"
        )
    return "\n".join(lines)


def narrate_deal_options(opts: lq.DealOptions) -> str:
    lines = [
        f"Trade options with {opts.other_civ_name} (player {opts.other_player_id}):"
    ]
    lines.append("\nEconomy:")
    lines.append(
        f"  Our gold: {opts.our_gold} ({opts.our_gpt:+d}/turn) | Favor: {opts.our_favor}"
    )
    lines.append(
        f"  Their gold: {opts.their_gold} ({opts.their_gpt:+d}/turn) | Favor: {opts.their_favor}"
    )
    if opts.our_luxuries or opts.our_strategics:
        lines.append("\nOur tradeable resources:")
        if opts.our_luxuries:
            lines.append(f"  Luxuries: {', '.join(opts.our_luxuries)}")
        if opts.our_strategics:
            lines.append(f"  Strategics: {', '.join(opts.our_strategics)}")
    if opts.their_luxuries or opts.their_strategics:
        lines.append("\nTheir tradeable resources:")
        if opts.their_luxuries:
            lines.append(f"  Luxuries: {', '.join(opts.their_luxuries)}")
        if opts.their_strategics:
            lines.append(f"  Strategics: {', '.join(opts.their_strategics)}")
    if opts.our_cities:
        lines.append(f"\nOur cities ({len(opts.our_cities)}):")
        for c in opts.our_cities:
            cap = " (CAPITAL)" if c.is_capital else ""
            lines.append(f"  {c.name} (id={c.city_id}, pop {c.population}){cap}")
    if opts.their_cities:
        lines.append(f"\nTheir cities ({len(opts.their_cities)}):")
        for c in opts.their_cities:
            cap = " (CAPITAL)" if c.is_capital else ""
            lines.append(f"  {c.name} (id={c.city_id}, pop {c.population}){cap}")
    lines.append("\nAgreements:")
    ob_status = "active" if opts.has_open_borders else "not active (available)"
    lines.append(f"  Open borders: {ob_status}")
    if opts.current_alliance:
        lines.append(f"  Alliance: {opts.current_alliance} (active)")
    elif opts.alliance_eligible:
        lines.append(
            "  Alliance: at least one native type is eligible; form_alliance checks the selected type"
        )
    else:
        lines.append(
            "  Alliance: no currently verified eligible native type"
        )
    return "\n".join(lines)


def _describe_trade_item(item: lq.TestTradeItem) -> str:
    """Human-readable description of a trade deal item."""
    if item.item_type == "GOLD":
        if item.duration > 0:
            return f"{item.amount} gold/turn ({item.duration} turns)"
        return f"{item.amount} gold"
    elif item.item_type == "RESOURCE":
        name = (f"Resource (id={item.value_id})" if item.value_id.lstrip("-").isdigit()
                else item.value_id.replace("RESOURCE_", "").replace("_", " ").title())
        dur = f" ({item.duration} turns)" if item.duration > 0 else ""
        amt = f" x{item.amount}" if item.amount > 1 else ""
        return f"{name}{amt}{dur}"
    elif item.item_type == "AGREEMENT":
        if item.subtype_id.lstrip("-").isdigit():
            return f"Agreement (subtype={item.subtype_id}, value={item.value_id}, duration={item.duration} turns)"
        name = item.subtype_id.replace("DIPLOACTION_", "").replace("_", " ").title()
        return f"{name} (duration={item.duration} turns)"
    elif item.item_type == "FAVOR":
        return f"{item.amount} diplomatic favor"
    elif item.item_type == "CITY":
        return f"City (id={item.value_id})"
    elif item.item_type == "GREAT_WORK":
        return f"Great Work (id={item.value_id or 'UNKNOWN'})"
    return f"{item.item_type} ({item.amount})"


def narrate_test_trade(result: lq.TestTradeResult, *, preview: bool = True, include_guidance: bool = True) -> str:
    lines = [
        f"{'Trade test' if preview else 'Native trade response'} with {result.other_civ_name} (player {result.other_player_id}):"
    ]

    our_proposed = [i for i in result.proposed if i.side == "US"]
    their_proposed = [i for i in result.proposed if i.side == "THEM"]
    if result.proposed:
        lines.append("\nYour proposal:")
    if our_proposed:
        lines.append(
            "  We offer: " + ", ".join(_describe_trade_item(i) for i in our_proposed)
        )
    if their_proposed:
        lines.append(
            "  We request: "
            + ", ".join(_describe_trade_item(i) for i in their_proposed)
        )

    if result.rejected:
        lines.append("\nAI response: REJECTED — these terms were not accepted")
    else:
        our_counter = [i for i in result.counter if i.side == "US"]
        their_counter = [i for i in result.counter if i.side == "THEM"]
        # Check if counter matches proposal
        proposed_key = sorted(
            (i.side, i.item_type, i.amount, i.duration) for i in result.proposed
        )
        counter_key = sorted(
            (i.side, i.item_type, i.amount, i.duration) for i in result.counter
        )
        if proposed_key == counter_key:
            lines.append(
                "\nAI response: ACCEPTABLE — this deal would be accepted as-is"
            )
        else:
            lines.append("\nAI counter-offer (what they consider fair):")
            if our_counter:
                lines.append(
                    "  We give: "
                    + ", ".join(_describe_trade_item(i) for i in our_counter)
                )
            if their_counter:
                lines.append(
                    "  They give: "
                    + ", ".join(_describe_trade_item(i) for i in their_counter)
                )
            if include_guidance:
                lines.append("\nAdjust your proposal to match or exceed the counter-offer, then use mode='send' to commit.")

    return "\n".join(lines)


def narrate_policies(gov: lq.GovernmentStatus) -> str:
    lines = [f"Government: {gov.government_name} ({gov.government_type})"]
    if gov.policy_unlock_cost == 0:
        lines.append("Policy change cost: free")
    elif gov.policy_unlock_cost is not None:
        currency = gov.policy_unlock_currency or "unknown currency"
        balance = f"{gov.policy_unlock_balance:g}" if gov.policy_unlock_balance is not None else "unknown"
        lines.append(f"Policy change cost: {gov.policy_unlock_cost:g} {currency}; balance: {balance}")
    else:
        lines.append("Policy change cost: unknown")

    state = {True:"yes",False:"no",None:"unknown"}[gov.can_change_now]
    lines.append(f"Changed plan allowed by general checks: {state}; {gov.change_reason}. Complete slot plan still requires native validation.")
    if gov.coverage:
        lines.append(f"Candidate coverage: {gov.coverage}; excludes obsolete/no-add-slot cards by default. Inspect one with policy show --policy POLICY_ID.")

    if gov.slots:
        lines.append(f"\n{len(gov.slots)} policy slots:")
        for s in gov.slots:
            slot_label = s.slot_type.replace("SLOT_", "").title()
            if s.current_policy:
                lines.append(
                    f"  Slot {s.slot_index} ({slot_label}): {s.current_policy_name} ({s.current_policy})"
                )
            else:
                lines.append(f"  Slot {s.slot_index} ({slot_label}): EMPTY")

    if gov.available_policies:
        by_type: dict[str, list[lq.PolicyInfo]] = {}
        for p in gov.available_policies:
            by_type.setdefault(p.slot_type, []).append(p)
        lines.append("\nPolicy candidates (current change restrictions apply):")
        for slot_type in [
            "SLOT_MILITARY",
            "SLOT_ECONOMIC",
            "SLOT_DIPLOMATIC",
            "SLOT_WILDCARD",
        ]:
            policies = by_type.get(slot_type, [])
            if policies:
                label = slot_type.replace("SLOT_", "").title()
                lines.append(f"  {label}:")
                for p in policies:
                    state = {True:"yes",False:"no",None:"unknown"}[p.can_use_now]
                    lines.append(f"    {p.name} ({p.policy_type}): {p.description} [can add now {state}; {p.reason}]")

    lines.append(
        "\nUse set_policies with slot assignments, e.g. '0=POLICY_AGOGE,1=POLICY_URBAN_PLANNING'"
    )
    lines.append("Wildcard slots can accept any policy type.")
    return "\n".join(lines)


def narrate_governors(gov: lq.GovernorStatus) -> str:
    lines = [
        f"Governor Points: {gov.points_available} available, {gov.points_spent} spent"
    ]
    lines.append(f"Native-ineligible choices omitted: {gov.omitted_promotions} promotions, {gov.omitted_appointments} appointments. Full current tree: governor list --include-ineligible.")
    if gov.can_appoint:
        lines.append("** Can appoint a new governor! **")

    if gov.appointed:
        lines.append(f"\nAppointed ({len(gov.appointed)}):")
        for g in gov.appointed:
            if g.assigned_city_id >= 0:
                est = (
                    " (established)"
                    if g.is_established
                    else f" ({g.turns_to_establish} turns to establish)"
                )
                lines.append(
                    f"  {g.name} ({g.governor_type}) — {g.assigned_city_name}{est}"
                )
            else:
                lines.append(f"  {g.name} ({g.governor_type}) — Unassigned")
            if g.available_promotions:
                lines.append("    Unacquired promotions with native eligibility:")
                for p in g.available_promotions:
                    lines.append(
                        f"      {p.name} ({p.promotion_type}): {p.description}; native can earn " + {True:"yes",False:"no",None:"unknown"}[p.can_earn]
                    )

    if gov.available_to_appoint:
        lines.append(f"\nUnappointed candidates ({len(gov.available_to_appoint)}):")
        for g in gov.available_to_appoint:
            lines.append(f"  {g.name} — {g.title} ({g.governor_type}); native can appoint " + {True:"yes",False:"no",None:"unknown"}[g.can_appoint])
            if g.description:
                lines.append(f"    {g.description}")
            if g.base_ability:
                lines.append(f"    Base: {g.base_ability} — {g.base_ability_desc}")
            if g.promotions:
                for p in sorted(g.promotions, key=lambda x: (x.level, x.column)):
                    lines.append(
                        f"    L{p.level}: {p.name} ({p.promotion_type}) — {p.description}"
                    )

    lines.append(
        "\nUse appoint_governor/assign_governor/promote_governor(governor_type, promotion_type)."
    )
    return "\n".join(lines)


def narrate_unit_promotions(status: lq.UnitPromotionStatus) -> str:
    if not status.promotions:
        return f"No promotions available for {status.unit_type} (id:{status.unit_id})."
    lines = [f"Promotions for {status.unit_type} (id:{status.unit_id}):"]
    for p in status.promotions:
        lines.append(f"  {p.name} ({p.promotion_type}): {p.description}")
    lines.append("\nUse promote_unit(unit_id, promotion_type) to apply.")
    return "\n".join(lines)


def narrate_city_states(status: lq.EnvoyStatus) -> str:
    lines = [f"Envoy tokens available: {status.tokens_available}"]
    if not status.city_states:
        lines.append("No known city-states.")
    else:
        lines.append(f"\n{len(status.city_states)} known city-states:")
        for cs in status.city_states:
            suz = f" (Suzerain: {cs.suzerain_name})" if cs.suzerain_id >= 0 else ""
            can = (
                " [can send]"
                if cs.can_send_envoy and status.tokens_available > 0
                else ""
            )
            lines.append(
                f"  {cs.name} ({cs.city_state_type}) — {cs.envoys_sent} envoys{suz}{can} [player {cs.player_id}]"
            )
    if status.tokens_available > 0:
        lines.append("\nUse send_envoy(player_id) to send an envoy.")
    return "\n".join(lines)


def narrate_pantheon_status(status: lq.PantheonStatus) -> str:
    lines = []
    if status.has_pantheon:
        lines.append(
            f"Pantheon: {status.current_belief_name} ({status.current_belief})"
        )
        lines.append(f"Faith: {status.faith_balance:.0f}")
    else:
        lines.append(f"No pantheon selected. Faith: {status.faith_balance:.0f}")
        if status.available_beliefs:
            lines.append(f"\n{len(status.available_beliefs)} available beliefs:")
            for b in status.available_beliefs:
                lines.append(f"  {b.name} ({b.belief_type}): {b.description}")
            lines.append("\nUse choose_pantheon(belief_type) to found a pantheon.")
        else:
            lines.append("No beliefs available (all taken or insufficient faith).")
    return "\n".join(lines)


def narrate_religion_founding_status(status: lq.ReligionFoundingStatus) -> str:
    lines = []
    if status.has_religion:
        lines.append(f"Religion: {status.religion_name} ({status.religion_type})")
        lines.append(f"Faith: {status.faith_balance:.0f}")
        lines.append("You have already founded a religion.")
    else:
        lines.append(f"No religion founded. Faith: {status.faith_balance:.0f}")
        if status.pantheon_index >= 0:
            lines.append(f"Pantheon: index {status.pantheon_index}")
        else:
            lines.append("No pantheon selected.")

        if status.available_religions:
            lines.append(f"\nAvailable religions ({len(status.available_religions)}):")
            for rtype, rname in status.available_religions:
                lines.append(f"  {rname} ({rtype})")

        for cls_name, beliefs in status.beliefs_by_class.items():
            short = cls_name.replace("BELIEF_CLASS_", "").title()
            lines.append(f"\n{short} beliefs ({len(beliefs)}):")
            for b in beliefs:
                lines.append(f"  {b.name} ({b.belief_type}): {b.description}")

        if status.available_religions and status.beliefs_by_class:
            lines.append(
                "\nUse found_religion(religion_type, follower_belief, founder_belief) "
                "after your Great Prophet has activated on a Holy Site."
            )
    return "\n".join(lines)


def narrate_dedications(status: lq.DedicationStatus) -> str:
    era_names = {
        0: "Ancient",
        1: "Classical",
        2: "Medieval",
        3: "Renaissance",
        4: "Industrial",
        5: "Modern",
        6: "Atomic",
        7: "Information",
    }
    era_name = era_names.get(status.era, f"Era {status.era}")
    lines = [
        f"{status.age_type} Age — {era_name} Era",
        f"Era score: {status.era_score} | Next-era thresholds (Dark: {status.dark_threshold}, Golden: {status.golden_threshold})",
    ]
    if status.active:
        lines.append(f"\nActive dedications: {', '.join(status.active)}")
    if status.selections_allowed > 0:
        lines.append(f"\n{status.selections_allowed} dedication(s) to choose:")
        for c in status.choices:
            desc = (
                c.golden_desc
                if status.age_type in ("Golden", "Heroic")
                else (c.dark_desc if status.age_type == "Dark" else c.normal_desc)
            )
            lines.append(f"  [{c.index}] {c.name}: {desc}")
        lines.append("\nUse choose_dedication(dedication_index=N) to select.")
    elif not status.active:
        lines.append("\nNo dedications available or required.")
    return "\n".join(lines)


def narrate_district_advisor(
    placements: list[lq.DistrictPlacement], district_type: str
) -> str:
    if not placements:
        return f"No valid placement tiles for {district_type}."
    lines = [f"{district_type} placement options ({len(placements)} native available tiles; unranked):"]
    for i, p in enumerate(placements, 1):
        adj_parts = [f"{k} unknown" if v is None else f"{v:g} {k}" for k, v in p.adjacency.items()]
        adj_str = ", ".join(adj_parts) if adj_parts else "no adjacency"
        total = "unknown" if p.total_adjacency is None else f"{p.total_adjacency:g}"
        lines.append(
            f"  #{i} ({p.x},{p.y}) Native plot adjacency: {total} ({adj_str}) — {p.terrain_desc}"
        )
    return "\n".join(lines)


def narrate_wonder_advisor(
    placements: list[lq.WonderPlacement], wonder_name: str
) -> str:
    if not placements:
        return f"No valid placement tiles for {wonder_name} in this city."
    short_name = wonder_name.replace("BUILDING_", "").replace("_", " ").title()
    lines = [f"{wonder_name} placement options ({len(placements)} native available tiles; unranked):"]
    for i, p in enumerate(placements, 1):
        # Build terrain description
        terrain = p.terrain.replace("TERRAIN_", "").replace("_", " ").lower()
        feat = ""
        if p.feature != "none":
            feat = " " + p.feature.replace("FEATURE_", "").replace("_", " ").lower()
        tags = []
        if p.has_river:
            tags.append("river")
        if p.is_coastal:
            tags.append("coastal")
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        warn_parts = []
        if p.improvement != "none":
            imp = p.improvement.replace("IMPROVEMENT_", "").replace("_", " ").lower()
            warn_parts.append(f"⚠ REMOVES {imp}")
        if p.resource != "none":
            res = p.resource.replace("RESOURCE_", "").replace("_", " ").lower()
            warn_parts.append(f"⚠ DISPLACES {res}")
        warn_str = f" — {', '.join(warn_parts)}" if warn_parts else ""
        prefix = "!!" if warn_parts else "  "
        lines.append(
            f"{prefix} #{i} ({p.x},{p.y}) {terrain}{feat}{tag_str}"
            f"{warn_str}"
        )
    return "\n".join(lines)


def narrate_purchasable_tiles(tiles: list[lq.PurchasableTile]) -> str:
    if not tiles:
        return "No purchasable tiles."
    lines = [f"{len(tiles)} native tile purchase candidates (unranked):"]
    for t in tiles:
        res_str = ""
        if t.resource:
            cls_tag = {"strategic": "*", "luxury": "+", "bonus": ""}.get(
                t.resource_class or "", ""
            )
            res_str = f" [{t.resource}{cls_tag}]"
        cost = 'unknown' if t.cost is None else str(t.cost).removesuffix('.0')
        balance = 'unknown' if t.balance is None else str(t.balance).removesuffix('.0')
        eligible = {True:'yes',False:'no',None:'unknown'}[t.can_purchase]
        short = '; short ' + str(t.cost-t.balance).removesuffix('.0') if t.cost is not None and t.balance is not None and t.balance<t.cost else ''
        lines.append(f"  ({t.x},{t.y}): gold {cost}; balance {balance}; can buy {eligible}; {t.reason}{short} — {t.terrain}{res_str}")
    return "\n".join(lines)


def narrate_great_people(gp: list[lq.GreatPersonInfo], *, report=None) -> str:
    number = lambda v: "unknown" if v is None else str(v).removesuffix('.0')
    lines=[]
    if report is not None:
        complete='yes' if report.scan_complete else 'unknown'
        lines.append(f"Current Great People timeline: native total {number(report.native_total)}; matched {number(report.matched)}; shown {len(gp)}; scan complete {complete}.")
        filters=[]
        if report.individual_id is not None:filters.append(f'individual_id={report.individual_id}')
        if report.class_type is not None:filters.append(f'class_type={report.class_type}')
        if filters:lines.append('Filter: '+', '.join(filters)+' (AND; current timeline only).')
        if report.exhausted_classes:
            lines.append(f'Exhausted native classes ({report.exhausted_count}): '+', '.join(report.exhausted_classes)+'. No current individual in these classes.')
        if report.errors:lines.append('Incomplete observation: '+', '.join(report.errors))
    if not gp:
        if report is None:
            return "No Great People in timeline."
        lines.append('No current timeline candidate matches this filter.' if report.scan_complete and (report.individual_id is not None or report.class_type is not None) else
            ('No current timeline candidates; all observed classes are exhausted.' if report.scan_complete and report.exhausted_count else
             ('Current timeline is empty.' if report.scan_complete else 'Candidate absence is unknown; no complete timeline result established.')))
        return '\n'.join(lines)
    lines.append(f"{len(gp)} Great People:")
    # Share balances only when every selected row contains the same known
    # observation. A changing/unknown currency keeps its per-person facts.
    shared={}
    if len(gp)>1:
        for label,attribute in (('gold','gold_balance'),('faith','faith_balance')):
            values=[getattr(person,attribute) for person in gp]
            if all(value is not None and value==values[0] for value in values):
                shared[label]=values[0]
    if shared:
        lines.append('Shared balances in this response: '+'; '.join(f'{label} {number(value)}' for label,value in shared.items())+'.')
    for g in gp:
        progress = f"{number(g.player_points)}/{number(g.cost)}"
        recruit_tag = " [CAN RECRUIT]" if g.can_recruit else ""
        entry = f"  {g.class_name}: {g.individual_name} ({g.era_name}) — {g.claimant} — your points: {progress}{recruit_tag}"
        if g.ability:
            entry += f"\n    Ability: {g.ability}"
        def quote(label, cost, balance, eligible):
            number = lambda v: 'unknown' if v is None else str(v).removesuffix('.0')
            state = {True: 'yes', False: 'no', None: 'unknown'}[eligible]
            reason = ''
            if cost is not None and balance is not None and balance < cost:
                reason = '; INSUFFICIENT_RESOURCE, short ' + str(cost-balance).removesuffix('.0')
            elif eligible is False:
                reason = '; NATIVE_INELIGIBLE (native API gives no detailed reason)'
            elif cost is None or eligible is None or balance is None:
                reason = '; OBSERVATION_UNAVAILABLE'
            balance_text='' if label in shared else f'; balance {number(balance)}'
            return f'{label} {number(cost)}{balance_text}; can patronize {state}{reason}'
        entry += '\n    Patronage quote: ' + quote('gold', g.gold_cost, g.gold_balance, g.can_patronize_gold)
        entry += ' / ' + quote('faith', g.faith_cost, g.faith_balance, g.can_patronize_faith)
        if g.can_recruit is None:
            entry += '\n    Recruitment eligibility: unknown'
        class_text=f'; class_type: {g.class_type}' if g.class_type is not None else ''
        entry += f"\n    (individual_id: {g.individual_id}{class_text})"
        lines.append(entry)
    return "\n".join(lines)


def narrate_gp_advisor(result: lq.GPAdvisorResult) -> str:
    district_short = (
        result.target_district.replace("DISTRICT_", "").replace("_", " ").title()
    )
    class_short = (
        result.gp_class.replace("GREAT_PERSON_CLASS_", "").replace("_", " ").title()
    )
    lines = [
        f"Activation city observations for {result.gp_name} ({class_short} -> {district_short}):"
    ]
    if result.charges > 0:
        lines[0] += f" [{result.charges} charge(s)]"
    lines.append('Unranked native highlights; current unit readiness and activation effects require separate checks.')
    if result.cities_known is not True:
        lines.append('City scan: unknown or incomplete.')
    if not result.cities:
        lines.append('  No matching city confirmed in this observation.')
        return "\n".join(lines)
    for i, c in enumerate(result.cities, 1):
        eligible = {True:'yes',False:'no',None:'unknown'}[c.can_activate]
        status = f'native highlight {eligible}; distance {c.distance}'
        yield_str = ', current city yield ' + ('unknown' if c.city_yield is None else str(c.city_yield).removesuffix('.0'))
        slots_str = ""
        if c.slots_free >= 0:
            slots_str = f", {c.slots_free}/{c.slots_total} great work slots free"
        elif result.gp_class in {"GREAT_PERSON_CLASS_WRITER", "GREAT_PERSON_CLASS_ARTIST", "GREAT_PERSON_CLASS_MUSICIAN"}:
            slots_str = ", great work slots unknown"
        lines.append(
            f"  {i}. {c.city_name} ({c.district_x},{c.district_y}) — {status}{yield_str}{slots_str}"
        )
    return "\n".join(lines)


def narrate_religion_status(rs: lq.ReligionStatus) -> str:
    if not rs.cities and not rs.summary:
        return "No religion data available."
    lines: list[str] = []
    # Summary — religious victory proximity
    if rs.summary:
        lines.append("Religious Victory Tracker:")
        for s in rs.summary:
            warning = ""
            if s.civs_with_majority >= s.total_majors:
                warning = " !! VICTORY ACHIEVED"
            elif s.civs_with_majority >= s.total_majors - 1:
                warning = " !! IMMINENT"
            lines.append(
                f"  {s.religion_name}: majority in {s.civs_with_majority}/{s.total_majors} civilizations{warning}"
            )
    # Per-civ city breakdown
    if rs.cities:
        by_civ: dict[str, list[lq.CityReligionInfo]] = {}
        for c in rs.cities:
            by_civ.setdefault(c.civ_name, []).append(c)
        lines.append("")
        for civ_name, cities in by_civ.items():
            lines.append(f"{civ_name}:")
            for c in cities:
                follower_str = ""
                if c.followers:
                    parts = [f"{name}:{count}" for name, count in c.followers.items()]
                    follower_str = f" ({', '.join(parts)})"
                lines.append(
                    f"  {c.city_name} (pop {c.population}) — {c.majority_religion}{follower_str}"
                )
    return "\n".join(lines)


def narrate_trade_routes(status: lq.TradeRouteStatus) -> str:
    lines = [f"Trade Routes: {status.active_count}/{status.capacity} active"]
    on_route = [t for t in status.traders if t.on_route]
    idle = [t for t in status.traders if not t.on_route]
    if on_route:
        lines.append(f"\nOn route ({len(on_route)}):")
        for t in on_route:
            origin = t.route_origin or "?"
            dest = t.route_dest or "?"
            # Owner label
            if t.is_domestic:
                label = "Domestic"
            elif t.is_city_state:
                label = "City-State"
            else:
                label = t.route_owner or "?"
            parts = [f"  Trader (id:{t.unit_id}) {origin} -> {dest} ({label})"]
            # Yields
            yields = []
            if t.origin_yields:
                yields.append(t.origin_yields)
            if t.dest_yields:
                yields.append(f"-> dest: {t.dest_yields}")
            if yields:
                parts.append(" | " + " ".join(yields))
            # Flags
            flags = []
            if t.has_quest:
                flags.append("[QUEST]")
            if t.pressure_out > 0 and t.religion_out:
                flags.append(f"{t.religion_out} -> {t.pressure_out}")
            if t.pressure_in > 0 and t.religion_in:
                flags.append(f"{t.religion_in} <- {t.pressure_in}")
            if flags:
                parts.append(" | " + " ".join(flags))
            lines.append("".join(parts))
    if idle:
        lines.append(f"\nIdle ({len(idle)}):")
        for t in idle:
            lines.append(
                f"  Trader (id:{t.unit_id}) at ({t.x},{t.y}) — needs trade_route or teleport"
            )
    if not status.traders:
        lines.append("\nNo trader units.")
    free_slots = status.capacity - status.active_count
    if free_slots > 0:
        lines.append(f"\n{free_slots} free route slot(s) — build/buy a Trader to fill.")
    if status.ghost_count > 0:
        engine_total = status.active_count + status.ghost_count
        lines.append(
            f"\nWARNING: {status.ghost_count} ghost route record(s) in engine "
            f"(engine reports {engine_total}, only {status.active_count} have living traders)."
        )
    return "\n".join(lines)


def narrate_trade_destinations(dests: list[lq.TradeDestination]) -> str:
    if not dests:
        return "No legal trade route destinations for this ready trader (native checks completed)."
    domestic = [d for d in dests if d.is_domestic]
    foreign = [d for d in dests if not d.is_domestic]
    lines = [f"{len(dests)} trade route destinations:"]

    def _fmt_dest(d: lq.TradeDestination, show_owner: bool = False) -> str:
        owner = f" ({d.owner_name})" if show_owner and d.owner_name else ""
        parts = [f"  {d.city_name}{owner} at ({d.x},{d.y})"]
        # Yields
        yields = []
        if d.origin_yields:
            yields.append(d.origin_yields)
        if d.dest_yields:
            yields.append(f"-> dest: {d.dest_yields}")
        if yields:
            parts.append(" | " + " ".join(yields))
        # Flags
        flags = []
        if d.has_quest:
            flags.append("[QUEST]")
        if d.has_trading_post:
            flags.append("Trading Post")
        if d.pressure_out > 0 and d.religion_out:
            flags.append(f"{d.religion_out} -> {d.pressure_out}")
        if d.pressure_in > 0 and d.religion_in:
            flags.append(f"{d.religion_in} <- {d.pressure_in}")
        if flags:
            parts.append(" | " + " ".join(flags))
        return "".join(parts)

    if domestic:
        lines.append("\nDomestic (food + production to origin):")
        for d in domestic:
            lines.append(_fmt_dest(d))
    if foreign:
        lines.append("\nInternational (gold to origin):")
        for d in foreign:
            lines.append(_fmt_dest(d, show_owner=True))
    # Summarize city-state quests
    quest_cs = [d.city_name for d in dests if d.has_quest]
    if quest_cs:
        lines.append(
            f"\nCity-state quests (send trade route for envoy): {', '.join(quest_cs)}"
        )
    lines.append("\nUse unit_action with action='trade_route', target_x=X, target_y=Y")
    return "\n".join(lines)


def narrate_world_congress(status: lq.WorldCongressStatus) -> str:
    if status.local_request in ("CONTINUE_REQUESTED", "FINISHED"):
        lines = [f"World Congress: local request {status.local_request} | native stage: {status.native_stage}"]
        lines.append("Local request recorded, not a receipt of vote acceptance. Do not repeat votes, submission or continuation.")
        lines.append("Use end_turn only to confirm the original ordinary turn request; this recorded window needs no new voting plan.")
        if status.vote_allocation:
            lines.append(f"Local plan allocation (not acceptance): {status.vote_allocation}")
        lines.append(f"Favor: {status.favor}")
        return "\n".join(lines)
    lines = []
    imminent = not status.is_in_session and status.turns_until_next <= 0

    if status.is_in_session or imminent:
        if status.is_in_session:
            lines.append(f"World Congress: IN SESSION | native stage: {status.native_stage} | local request: {status.local_request}")
            if status.local_request in ("VOTES_REQUESTED", "SUBMIT_REQUESTED", "CONTINUE_REQUESTED"):
                lines.append("Local operations requested, not a receipt of acceptance or meeting completion. Do not repeat votes.")
            elif status.native_stage in ("STAGE1", "STAGE2") and status.local_request == "NONE":
                lines.append("No local tool request recorded; this is not proof votes are absent. Use queue_wc_votes only for new explicit choices; [] requires a verified no-choice stage.")
            eligibility = {
                "EMPTY": "EMPTY (no eligible proposals)",
                "CHOICES": "CHOICES (eligible proposals require explicit votes)",
            }.get(status.proposal_status, "UNKNOWN (eligibility not verified)")
            lines.append(f"Native proposal eligibility: {eligibility}; no default proposal votes are supplied.")
        else:
            lines.append(
                "World Congress: FIRES THIS TURN — use queue_wc_votes() before end_turn()!"
            )
        costs_str = ", ".join(f"{i+1}={cost}" for i, cost in enumerate(status.favor_costs))
        if status.vote_cost_status != "KNOWN": costs_str = "UNKNOWN"
        lines.append(f"Favor: {status.favor} | Resolution vote costs (cumulative): {costs_str}")
        if status.proposal_error:
            lines.append(f"Proposal details UNKNOWN: {status.proposal_error}")
        if status.vote_allocation:
            lines.append(f"Local plan allocation (not acceptance): {status.vote_allocation}")
    else:
        if status.turns_until_next >= 0:
            lines.append(
                f"World Congress: Next session in {status.turns_until_next} turns"
            )
        else:
            lines.append("World Congress: Not yet convened")
        lines.append(f"Favor: {status.favor}")

    if status.resolutions:
        lines.append("")
        for i, r in enumerate(status.resolutions, 1):
            if status.is_in_session:
                # Active session — show full voting details
                lines.append(f"Resolution #{i}: {r.name} (hash: {r.resolution_hash})")
                lines.append(f"  Target type: {r.target_kind}")
                if r.effect_a:
                    lines.append(f"  Option A: {r.effect_a}")
                if r.effect_b:
                    lines.append(f"  Option B: {r.effect_b}")
                if r.possible_targets:
                    tgt_strs = []
                    for t in r.possible_targets:
                        if ":" in t:
                            tid, tname = t.split(":", 1)
                            tgt_strs.append(f"[target={tid}] {tname}")
                        else:
                            tgt_strs.append(t)
                    lines.append(f"  Targets: {', '.join(tgt_strs)}")
                lines.append(
                    f'  -> queue_wc_votes(votes=\'[{{"hash": {r.resolution_hash}, "option": 1or2, "target": 0, "votes": 1}}]\')'
                )
            else:
                # Prior-session effects use the native label; Winner is not an option index.
                outcome = r.chosen_label if r.chosen_label in ("A", "B") else "UNKNOWN"
                effect = (
                    r.effect_a if outcome == "A" else r.effect_b if outcome == "B" else ""
                )
                chosen = f" ({r.chosen_thing})" if r.chosen_thing else ""
                lines.append(f"  {r.name} — Outcome {outcome}{chosen}: {effect}")

        if imminent:
            lines.append("")
            lines.append(
                "NOTE: Above are ACTIVE EFFECTS from last session. Upcoming resolutions will be different."
            )
            lines.append(
                "The handler resolves targets at runtime during the WC session."
            )
            lines.append("")
            lines.append(
                'To vote: queue_wc_votes(votes=\'[{"hash": <hash>, "option": 1or2, "target": <player_id>, "votes": N}, ...]\')'
            )
            lines.append(
                "Common hashes: Diplomatic Victory = 334823573. Use get_diplomacy for player IDs."
            )
            lines.append("Then call end_turn() — handler fires during WC processing.")

    if status.proposals:
        lines.append("\nProposals:")
        for p in status.proposals:
            lines.append(f"  {p.name} [{p.category_name} ({p.category}), {p.type_name}, type={p.proposal_type}, emergency={p.emergency_type}]")
            lines.append(f"  proposal_key: {p.proposal_key}; sender={p.sender_id if p.sender_id is not None else 'NONE'} ({p.sender_name}); target={p.target_id} ({p.target_name})")
            lines.append(f"  {p.description}")
            directions = ", ".join(label for allowed, label in ((p.can_upvote, '1=up'), (p.can_downvote, '2=down')) if allowed)
            lines.append(f"  Eligible: {p.eligible}; Disabled: {p.disabled}; options: {directions or 'none'}; restriction: {p.voting_restriction or p.proposal_blocker_type or 'none'} {p.restriction_reason}")
            lines.append("  Cumulative favor: " + ", ".join(f"{i+1}={cost}" for i, cost in enumerate(p.favor_costs)) + f"; max votes: {p.max_votes}")
            if p.eligible:
                option = 1 if p.can_upvote else 2
                lines.append(f'  Explicit choice format (choose option/votes): {{"proposal_key": "{p.proposal_key}", "option": {option}, "votes": 1}}')
        lines.append("Provide every eligible resolution/proposal once in one votes array. Reserve every first-vote cost, then allocate extra votes in input order within one favor budget; send resolutions before proposals.")

    return "\n".join(lines)


def _victory_number(value: float | int | None) -> str:
    return "unknown" if value is None else f"{value:g}"


def narrate_victory_warnings(vp: lq.VictoryProgress) -> str:
    """Risk heuristics over shared observations; never a game outcome or action."""
    if not vp.complete or not vp.enabled_known:
        return "Victory warnings unavailable: victory facts or enabled types unknown; use get_victory_progress."
    warnings: list[str] = []
    unknown: set[str] = set() if vp.contacts_known else {"contact status"}
    for player in vp.players:
        if player.player_id == vp.local_player_id:
            continue
        if "VICTORY_TECHNOLOGY" in vp.enabled_victories:
            values = (player.spaceports, player.science_vp, player.completed_space_projects)
            if any(value is None for value in (*values, player.science_vp_needed)):
                unknown.add("science")
            if player.spaceports_verified > 0 or any(value is not None and value > 0 for value in values):
                near = (player.science_vp is not None and player.science_vp_needed is not None
                        and player.science_vp_needed > 0 and player.science_vp / player.science_vp_needed >= .75)
                warnings.append(
                    f"{player.name}: space race observed | {_victory_number(player.spaceports)} completed spaceport(s) | "
                    f"{_victory_number(player.science_vp)}/{_victory_number(player.science_vp_needed)} VP"
                    + (" | near requirement (>=75%)" if near else "")
                    + f" | completed milestones {_victory_number(player.completed_space_projects)}"
                    + (f" | at least {_victory_number(player.spaceports_verified)} verified completed spaceport(s)"
                       if player.spaceports is None and player.spaceports_verified > 0 else "")
                )
        if "VICTORY_CULTURE" in vp.enabled_victories:
            visiting, required = player.culture_visiting, player.culture_required
            if visiting is None or required is None or required <= 0:
                unknown.add("culture")
            elif visiting / required >= .75:
                warnings.append(f"{player.name}: visiting tourists {_victory_number(visiting)}/{_victory_number(required)} (>=75%)")
        if "VICTORY_DIPLOMATIC" in vp.enabled_victories:
            points, required = player.diplomatic_vp, vp.diplomatic_vp_needed
            if points is None or required is None or required <= 0:
                unknown.add("diplomatic")
            elif points / required >= .5:
                warnings.append(f"{player.name}: diplomatic {_victory_number(points)}/{_victory_number(required)} VP (>=50%)")
        if "VICTORY_RELIGIOUS" in vp.enabled_victories:
            count, required = player.religion_civilizations, player.religion_civilizations_required
            if count is None or required is None or required <= 0:
                unknown.add("religious")
            elif count > 0 and count >= required - 1:
                warnings.append(f"{player.name}: founded religion is majority in {_victory_number(count)}/{_victory_number(required)} civilizations")
    if not warnings and not unknown:
        return ""
    lines = ["Victory risk (heuristic; not a terminal result):", *("  " + item for item in warnings)]
    if unknown:
        lines.append("  Unknown observations: " + ", ".join(sorted(unknown)) + ".")
    lines.append("Inspect get_victory_progress and reassess the victory route; no exact remaining-turn estimate.")
    return "\n".join(lines)


def narrate_victory_progress(vp: lq.VictoryProgress) -> str:
    """Render observed victory progress, leaving strategy to the caller."""
    if not vp.players:
        return "No victory data available."
    names = {
        "VICTORY_TECHNOLOGY": "Science",
        "VICTORY_CONQUEST": "Domination",
        "VICTORY_CULTURE": "Culture",
        "VICTORY_RELIGIOUS": "Religious",
        "VICTORY_DIPLOMATIC": "Diplomatic",
        "VICTORY_SCORE": "Score",
    }
    enabled = vp.enabled_victories
    lines = ["=== VICTORY PROGRESS ==="]
    if vp.enabled_known and not enabled:
        lines.append("Enabled: none.")
    elif enabled:
        lines.append("Enabled: " + ", ".join(names.get(key, key) for key in sorted(enabled)))
        if not vp.enabled_known:
            lines.append("Enabled victory set: incomplete/unknown.")
    else:
        lines.append("Enabled victory types: not reported.")

    if (not vp.enabled_known and not enabled) or "VICTORY_TECHNOLOGY" in enabled:
        lines.append("\nSCIENCE")
        for player in sorted(vp.players, key=lambda p: p.science_vp if p.science_vp is not None else -1, reverse=True):
            lines.append(
                f"  {player.name}: {_victory_number(player.science_vp)}/{_victory_number(player.science_vp_needed)} VP | "
                f"{player.techs_researched} techs | {_victory_number(player.spaceports)} spaceports"
                + (f" | native progress/turn {_victory_number(player.science_rate)}" if player.science_rate is not None else "")
                + (f" | space progress {player.space_progress}" if player.space_progress else "")
            )
        for project in vp.space_projects:
            detail = f"  {project.name}: {project.status}"
            if project.status == "building":
                detail += f" | {_victory_number(project.progress_pct)}% | {_victory_number(project.turns_remaining)} turns | {project.city_name}"
            if project.tech_prereq:
                detail += f" | prerequisite {project.tech_prereq} (researched={project.has_tech})"
            if project.cost:
                detail += f" | cost {_victory_number(project.cost)}"
            lines.append(detail)

    if (not vp.enabled_known and not enabled) or "VICTORY_CONQUEST" in enabled:
        lines.append("\nDOMINATION (original capitals)")
        for player in vp.players:
            held = vp.capitals_held.get(player.name)
            status = "unknown" if held is None else "held" if held else "lost"
            lines.append(f"  {player.name}: original capital {status}")

    if (not vp.enabled_known and not enabled) or "VICTORY_CULTURE" in enabled:
        lines.append("\nCULTURE")
        for player in vp.players:
            lines.append(
                f"  {player.name}: tourism {player.tourism} | domestic tourists {_victory_number(player.staycationers)}"
                + f" | visiting tourists {_victory_number(player.culture_visiting)}/{_victory_number(player.culture_required)}"
            )
        for name, tourists in vp.our_tourists_from.items():
            domestic = vp.their_staycationers.get(name)
            lines.append(
                f"  Our tourists from {name}: {_victory_number(tourists)} | "
                f"their domestic tourists: {_victory_number(domestic)}"
            )

    if (not vp.enabled_known and not enabled) or "VICTORY_RELIGIOUS" in enabled:
        lines.append(f"\nRELIGION (founded {_victory_number(vp.religions_founded)}/{vp.religions_max})")
        for player in vp.players:
            majority = vp.religion_majority.get(player.name, "unknown")
            founded = vp.religion_founded_names.get(player.name)
            detail = (
                f"  {player.name}: majority={majority} | "
                f"native religion cities {player.religion_cities} (engine statistic; not owned-city count) | founded religion={player.has_religion if player.has_religion is not None else 'unknown'}"
            )
            if founded:
                detail += f" ({founded})"
            lines.append(detail)

    if (not vp.enabled_known and not enabled) or "VICTORY_DIPLOMATIC" in enabled:
        lines.append("\nDIPLOMATIC")
        for player in sorted(vp.players, key=lambda p: p.diplomatic_vp if p.diplomatic_vp is not None else -1, reverse=True):
            lines.append(f"  {player.name}: {_victory_number(player.diplomatic_vp)}/{_victory_number(vp.diplomatic_vp_needed)} VP")

    lines.append("\nSCORE AND CIVILIZATION DATA")
    for player in sorted(vp.players, key=lambda p: p.score, reverse=True):
        lines.append(
            f"  {player.name}: score {player.score} | {player.num_cities} cities | "
            f"science {player.science_yield:g} | culture {player.culture_yield:g} | "
            f"gold {player.gold_yield:+g} | military {player.military_strength} | "
            f"civics {player.civics_completed}"
        )
    if vp.demographics:
        lines.append("\nDEMOGRAPHICS")
        for name, metric in vp.demographics.items():
            lines.append(
                f"  {name}: rank {metric.rank} | ours {metric.value:g} | "
                f"best {metric.best:g} | average {metric.average:g} | worst {metric.worst:g}"
            )
    warnings = narrate_victory_warnings(vp)
    if warnings:
        lines.append("\n" + warnings)
    return "\n".join(lines)


def narrate_notification_message(notification: lq.GameNotification) -> str:
    """Compose native notification text for lists and confirmed-turn reports."""
    message = notification.message.strip()
    summary = notification.summary.strip()
    if not summary or summary in message:
        return message
    if not message or message in summary:
        return summary
    return f"{message} — {summary}"


def narrate_notifications(notifs: list[lq.GameNotification]) -> str:
    if not notifs:
        return "No active notifications."

    action_required = [n for n in notifs if n.is_action_required]
    info_notifs = [n for n in notifs if not n.is_action_required]

    lines = []
    if action_required:
        lines.append(f"== Action Required ({len(action_required)}) ==")
        for n in action_required:
            hint = f"  -> Use: {n.resolution_hint}" if n.resolution_hint else ""
            loc = f" at ({n.x},{n.y})" if n.x >= 0 else ""
            lines.append(f"  * {narrate_notification_message(n)}{loc}{hint}")

    if info_notifs:
        if lines:
            lines.append("")
        lines.append(f"== Notifications ({len(info_notifs)}) ==")
        for n in info_notifs:
            hint = f"  -> {n.resolution_hint}" if n.resolution_hint else ""
            loc = f" at ({n.x},{n.y})" if n.x >= 0 else ""
            lines.append(f"  - {narrate_notification_message(n)}{loc}{hint}")

    return "\n".join(lines)


def narrate_move_discoveries(
    newly_revealed: list[tuple[int, int, dict]],
    total_new: int,
) -> str:
    """Narrate newly revealed tiles after a unit move.

    *newly_revealed* is a list of ``(x, y, metadata)`` for each tile that
    the engine had never revealed to this player before this move.
    *total_new* is the full count (may exceed len(newly_revealed) if some
    tiles lacked metadata).

    Returns empty string when nothing new was revealed.
    """
    if total_new == 0:
        return ""

    # Classify tiles as "notable" (resource, enemy unit, camp, city, river+hills)
    notable: list[str] = []
    for x, y, m in newly_revealed:
        parts: list[str] = []
        terrain = m.get("terrain", "").replace("TERRAIN_", "").replace("_", " ").title()
        if m.get("hills"):
            terrain += " Hills"
        parts.append(terrain)
        if m.get("feature"):
            feat = m["feature"].replace("FEATURE_", "").replace("_", " ").title()
            parts.append(feat)
        is_notable = False
        if m.get("resource"):
            res = m["resource"].replace("RESOURCE_", "").replace("_", " ").title()
            cls = m.get("resource_class", "")
            marker = "*" if cls == "strategic" else "+" if cls == "luxury" else ""
            parts.append(f"[{res}{marker}]")
            is_notable = True
        if m.get("units"):
            for u in m["units"]:
                parts.append(f"**[{u}]**")
            is_notable = True
        if m.get("camp"):
            parts.append("**[Barbarian Camp!]**")
            is_notable = True
        if m.get("city"):
            parts.append(f"**[City: {m['city']}]**")
            is_notable = True
        if is_notable:
            notable.append(f"  ({x},{y}): {' '.join(parts)}")

    n_notable = len(notable)
    n_mundane = total_new - n_notable
    lines: list[str] = []
    if n_notable:
        lines.append(f"Revealed {total_new} new tiles ({n_notable} notable):")
        lines.extend(notable)
        if n_mundane > 0:
            lines.append(f"  + {n_mundane} mundane tiles")
    else:
        lines.append(f"Revealed {total_new} new tiles (no notable features)")
    return "\n".join(lines)
