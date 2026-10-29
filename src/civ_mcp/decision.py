"""On-demand decision facts from existing readers, without policy or a cache.

Compose the InGame getters in one bounded transport request. Each reader keeps
its parser and visibility rules; isolated functions prevent local collisions.
Scope witnesses detect a changed world/turn, not an atomic engine transaction.
"""
from dataclasses import asdict, is_dataclass
import time

from civ_mcp import lua as lq, narrate as nr
from civ_mcp.connection import LuaError
from civ_mcp.lua.notifications import LUA_TURN_WORLD
from civ_mcp.snapshot import pending_facts

OPTIONAL_SECTIONS = ("research", "diplomacy", "trade")


def readers(include=(), city_ids=()):
    result = {
        "overview": (lq.build_overview_query(), lq.parse_overview_response, nr.narrate_overview),
        "units": (lq.build_units_query(), lq.parse_units_response, nr.narrate_units),
        "cities": (lq.build_cities_query(), lq.parse_cities_response, lambda v: nr.narrate_cities(*v)),
        "threats": (lq.build_threat_scan_query(), lq.parse_threat_scan_response, None),
        "victory": (lq.build_victory_progress_query(), lq.parse_victory_progress_response, nr.narrate_victory_progress),
        "notifications": (lq.build_notifications_query(), lq.parse_notifications_response, nr.narrate_notifications),
        "blockers": (lq.build_end_turn_blocking_query(), lq.parse_end_turn_blocking, None),
        "outcome": (lq.build_gameover_check(), lq.parse_gameover_response, None),
    }
    optional = {
        "research": (lq.build_tech_civics_query, lq.parse_tech_civics_response, nr.narrate_tech_civics),
        "diplomacy": (lq.build_diplomacy_query, lq.parse_diplomacy_response, nr.narrate_diplomacy),
        "trade": (lq.build_trade_routes_query, lq.parse_trade_routes_response, nr.narrate_trade_routes),
    }
    for key in dict.fromkeys(include):
        if key not in optional:
            raise ValueError(f"Unknown decision section: {key}")
        builder, parser, render = optional[key]
        result[key] = (builder(), parser, render)
    for city_id in dict.fromkeys(city_ids):
        if type(city_id) is not int or city_id < 0:
            raise ValueError("Production city IDs must be nonnegative native integers")
        result[f"production_{city_id}"] = (lq.build_city_production_query(city_id),
                                          lq.parse_city_production_response, nr.narrate_city_production)
    return result


def build_query(selected):
    parts = [LUA_TURN_WORLD, 'local decisionBefore=turnWorld()']
    for name, (source, _, _) in selected.items():
        parts.append(f'''do
print("DECISION_BEGIN|{name}")
local ok,err=pcall(function()
{source}
end)
if not ok then print("DECISION_ERROR|"..tostring(err):gsub("[\\r\\n]"," ")) end
print("DECISION_END|{name}")
end''')
    parts.append('printTurnWorld(decisionBefore)')
    return "\n".join(parts)


def unpack_sections(rows, selected):
    sections, current = {}, None
    for row in rows:
        if row.startswith("DECISION_BEGIN|"):
            name = row.split("|", 1)[1]
            if current is not None or name not in selected or name in sections:
                raise ValueError("Invalid or duplicate decision section frame")
            sections[name] = {"rows": [], "complete": False}
            current = name
        elif row.startswith("DECISION_END|"):
            if current != row.split("|", 1)[1]:
                raise ValueError("Mismatched decision section frame")
            sections[current]["complete"] = True
            current = None
        elif current is not None and row != lq.SENTINEL:
            sections[current]["rows"].append(row)
    return sections


def json_data(value):
    if is_dataclass(value):
        return json_data(asdict(value))
    if isinstance(value, dict):
        return {key: json_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_data(item) for item in (sorted(value) if isinstance(value, set) else value)]
    return value


async def read_context(game, include=(), city_ids=(), view="brief"):
    pending = pending_facts(game)
    result = {"schema_version": 1, "status": "blocked", "pending": pending,
              "scope": "owned entities and current public/visible facts; not an atomic snapshot",
              "identity": None, "checks": {}, "sections": {}}
    # Entity readers must not reconcile an original owner's pending request.
    if any(pending.values()):
        result["error"] = "Pending operation exists; inspect and confirm its original request first."
        return result
    if view not in {"brief", "structured"}:
        raise ValueError("Decision view must be brief or structured")
    selected = readers(include, city_ids)
    started = time.monotonic()
    rows = await game.conn.execute_write(build_query(selected), timeout=30.0)
    result["collection_seconds"] = round(time.monotonic() - started, 6)
    framed = unpack_sections(rows, selected)
    parsed = {}
    for name, (_, parse, _) in selected.items():
        try:
            frame = framed.get(name)
            if frame is None or not frame["complete"]:
                raise ValueError("Section response incomplete")
            data = frame["rows"]
            error = next((r for r in data if r.startswith(("DECISION_ERROR|", "ERR:", "Error:"))), None)
            if error:
                raise ValueError(error)
            value = parse(data)
            if name == "units" and len(value) != len(data):
                raise ValueError("Unit rows could not all be parsed")
            if name == "notifications" and not any(r.startswith("TOTAL|") for r in data):
                raise ValueError("Notification completeness unknown")
            if name == "threats" and not (data == ["NO_THREATS"] or
                    data and all(r.startswith("THREAT|") for r in data) and len(value) == len(data)):
                raise ValueError("Visible-threat completeness unknown")
            if name == "blockers" and not any(r == "NONE" or r.startswith("BLOCKING|") for r in data):
                raise ValueError("Blocker completeness unknown")
            if name == "outcome" and not any(r == "GAME_ACTIVE" or r.startswith("GAME_OVER|") for r in data):
                raise ValueError("Game outcome unknown")
            if name == "outcome" and value is None and data != ["GAME_ACTIVE"]:
                raise ValueError("Game outcome could not be parsed")
            if name == "victory" and not value.complete:
                raise ValueError("Victory response incomplete")
            parsed[name] = value
            result["sections"][name] = {"status": "ready"}
        except (ValueError, TypeError, IndexError, KeyError, LuaError) as exc:
            result["sections"][name] = {"status": "unknown", "error": str(exc)}

    from civ_mcp.end_turn import _world_observation
    try:
        world, turn = _world_observation(rows)
        civilization_type, seed, player_id = world
        civ = civilization_type.removeprefix("CIVILIZATION_").lower()
        result["identity"] = dict(civilization=civ, seed=seed, player_id=player_id, turn=turn)
        witnesses = [r.removeprefix("TURN_WORLD_SEEN|") for r in rows if r.startswith("TURN_WORLD_SEEN|")]
        stable = witnesses == [f"{civilization_type}|{seed}|{player_id}|{turn}"] * 2
    except ValueError:
        world, turn, stable = None, None, False
    overview = parsed.get("overview")
    units, cities = parsed.get("units"), parsed.get("cities")
    result["checks"] = {
        "stable_world_turn": stable,
        "overview_scope": bool(stable and overview and
            (overview.civilization_type, overview.player_id, overview.turn) == (civilization_type, player_id, turn)),
        "unit_scope": bool(overview and units is not None and len(units) <= overview.num_units
                           and len({u.unit_id for u in units}) == len(units)),
        "city_scope": bool(stable and overview and cities is not None and len(cities[0]) == overview.num_cities
            and len({c.city_id for c in cities[0]}) == len(cities[0])
            and all(c.owner_id == player_id and c.game_identity == (civ, seed) for c in cities[0])),
        "victory_scope": bool(stable and "victory" in parsed and
            (parsed["victory"].turn, parsed["victory"].local_player_id) == (turn, player_id)),
    }
    for city_id in city_ids:
        if cities is None or city_id not in {c.city_id for c in cities[0]}:
            name = f"production_{city_id}"
            parsed.pop(name, None)
            result["sections"][name] = {"status": "unknown", "error": "Requested city not in current owned-city scope"}
    for name, value in parsed.items():
        render = selected[name][2]
        if view == "brief" and render:
            value = render(value)
        elif name == "cities":
            value = {"cities": value[0], "distances": value[1]}
        result["sections"][name]["data"] = json_data(value)
    result["status"] = "ready" if all(result["checks"].values()) and all(
        s["status"] == "ready" for s in result["sections"].values()) else "unknown"
    if result["status"] == "unknown":
        result["error"] = "Decision facts incomplete or inconsistent; inspect checks and section errors."
    return result
