"""Read a bounded current-player snapshot; never confirm pending operations."""

from dataclasses import asdict


def pending_facts(game) -> dict[str, bool]:
    return {
        "action": game.pending_action_summary is not None,
        "turn": game.turn_pending, "diplomacy": game.diplomacy_pending,
        "congress": game.congress_pending, "save": game.save_pending,
        "replacement": game.replacement_pending,
        "popup": getattr(game, "popup_pending", False),
    }


def overview_data(value) -> dict:
    result = asdict(value)
    result["enabled_victories"] = sorted(value.enabled_victories)
    return result


async def read_snapshot(game) -> dict:
    # get_units/get_cities may reconcile a pending action. Refuse before any
    # engine I/O so this verification surface cannot consume that confirmation.
    pending = pending_facts(game)
    result = {"schema_version": 1, "scope": "current_player", "status": "blocked",
              "pending": pending, "data": {}, "checks": {}}
    if any(pending.values()):
        result["error"] = "Pending operation exists; inspect/confirm it through its original tool before snapshot verification."
        return result
    identity = await game.get_game_identity()
    before = await game.get_game_overview()
    units = await game.get_units()
    cities, _ = await game.get_cities()
    after = await game.get_game_overview()
    final_identity = await game.get_game_identity()
    result["data"] = {
        "identity": {"civilization": identity[0], "seed": identity[1],
                     "player_id": before.player_id, "turn": before.turn},
        "overview": overview_data(before), "overview_after": overview_data(after),
        "units": [asdict(unit) for unit in units], "cities": [asdict(city) for city in cities],
        "entity_scope": "Current player's listed on-map units and owned cities; not a whole-map census.",
    }
    checks = {
        "identity_known": identity[0] != "unknown" and bool(before.civilization_type) and before.player_id >= 0 and before.turn >= 0,
        "world_stable": identity == final_identity,
        "player_turn_stable": (before.player_id, before.turn, before.civilization_type, before.leader_type)
            == (after.player_id, after.turn, after.civilization_type, after.leader_type),
        "city_count": len(cities) == before.num_cities == after.num_cities,
        # Off-map spies can be counted by overview but omitted by get_units.
        "unit_count_range": len(units) <= before.num_units == after.num_units,
        "unit_ids_unique": len({unit.unit_id for unit in units}) == len(units),
        "city_ids_unique": len({city.city_id for city in cities}) == len(cities),
        "city_scope": all(city.owner_id == before.player_id and city.game_identity == identity for city in cities),
        "identity_matches_overview": before.civilization_type == "CIVILIZATION_" + identity[0].upper(),
    }
    result["checks"] = checks
    result["status"] = "ready" if all(checks.values()) else "unknown"
    return result
