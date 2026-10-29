"""Decision facts cross the real GameState/CLI seam; no gameplay driver."""
import asyncio
import json

import pytest

from civ_mcp import decision, server
from civ_mcp.game_state import GameState
from civ_mcp.devtools.commands import parse_command
from test_server import make_ctx


WORLD = "CIVILIZATION_ROME|4242|0|12"


def response(overrides=None):
    sections = {
        "overview": ["12|0|Rome|Trajan|100|5|10|8|0|None|None|0|1|10",
                     "IDENTITY|0|CIVILIZATION_ROME|LEADER_TRAJAN"],
        "units": ["65536|0|Warrior|UNIT_WARRIOR|1,2|2/2|100/100"],
        "cities": [], "threats": ["NO_THREATS"],
        "victory": ["VICTORY_CONTEXT|12|0", "VICTORY_ENABLED_KNOWN|true",
                    "VFACT|0|Rome|0|33.5|0|0|0|0|1|0|0|0", "VICTORY_COMPLETE"],
        "notifications": ["TOTAL|0|0"], "blockers": ["NONE"], "outcome": ["GAME_ACTIVE"],
    }
    sections.update(overrides or {})
    rows = []
    for name, data in sections.items():
        rows.extend([f"DECISION_BEGIN|{name}", *data, f"DECISION_END|{name}"])
    return rows + [f"TURN_WORLD_SEEN|{WORLD}", f"TURN_WORLD_SEEN|{WORLD}", f"TURN_WORLD|{WORLD}"]


class Conn:
    def __init__(self, rows=None):
        self.rows = response() if rows is None else rows
        self.calls = []

    async def execute_write(self, query, timeout=None):
        self.calls.append(query)
        return self.rows


def read(rows=None, **kwargs):
    conn = Conn(rows)
    game = GameState(conn)
    result = asyncio.run(game.get_decision_context(**kwargs))
    return result, game, conn


def test_one_request_keeps_identity_facts_and_does_not_create_a_reporting_cache():
    value, game, conn = read(view="structured")
    assert value["status"] == "ready" and all(value["checks"].values())
    assert value["identity"] == dict(civilization="rome", seed=4242, player_id=0, turn=12)
    assert value["sections"]["units"]["data"][0]["unit_id"] == 65536
    assert value["sections"]["threats"]["data"] == []
    assert value["sections"]["outcome"]["data"] is None  # Explicit GAME_ACTIVE.
    assert len(conn.calls) == 1 and game._last_snapshot is None
    assert "research" not in value["sections"]  # Unqueried, not empty.


def test_brief_uses_existing_reports_while_structured_preserves_raw_fields():
    brief, _, _ = read()
    raw, _, _ = read(view="structured")
    from civ_mcp.narrate import narrate_units
    from civ_mcp.lua import UnitInfo
    assert brief["sections"]["units"]["data"] == narrate_units([
        UnitInfo(**raw["sections"]["units"]["data"][0])])
    assert "Turn 12 | Rome" in brief["sections"]["overview"]["data"]


@pytest.mark.parametrize("section,rows", [
    ("units", ["malformed entity"]), ("victory", ["VICTORY_CONTEXT|12|0"]),
    ("notifications", []), ("outcome", []), ("blockers", []),
    ("threats", ["DECISION_ERROR|native getter unavailable"]),
])
def test_failed_section_is_unknown_while_other_facts_remain_available(section, rows):
    value, _, _ = read(response({section: rows}))
    assert value["status"] == "unknown"
    assert value["sections"][section]["status"] == "unknown"
    assert "data" not in value["sections"][section]
    assert value["sections"]["overview"]["status"] == "ready"


@pytest.mark.parametrize("scope", ["UNKNOWN", "CIVILIZATION_ROME|4242|0|13", "CIVILIZATION_ROME|4243|0|12"])
def test_changed_or_unknown_scope_cannot_certify_old_facts(scope):
    rows = response()
    rows[-1] = "TURN_WORLD|" + scope
    value, _, _ = read(rows)
    assert value["status"] == "unknown"
    # A changed final witness may be well formed, but cannot match overview.
    assert not value["checks"]["overview_scope"]


@pytest.mark.parametrize("flag", ["_pending_save", "_pending_load", "_pending_end_turn"])
def test_pending_refuses_before_any_transport_and_preserves_owner(flag):
    conn = Conn()
    game = GameState(conn)
    setattr(game, flag, True)
    value = json.loads(asyncio.run(server.get_decision_context(make_ctx(game=game))))
    assert value["status"] == "blocked" and any(value["pending"].values())
    assert conn.calls == [] and getattr(game, flag)


def test_unowned_production_city_is_unknown_not_an_empty_candidate_set():
    value, _, _ = read(response({"production_7": ["UNITS:", "BUILDINGS:", "DISTRICTS:"]}), city_ids=[7])
    assert value["status"] == "unknown"
    assert "owned-city" in value["sections"]["production_7"]["error"]
    assert "data" not in value["sections"]["production_7"]


def test_missing_section_frame_cannot_look_like_zero_entities():
    rows = response()
    rows.remove("DECISION_END|outcome")
    value, _, _ = read(rows)
    assert value["status"] == "unknown" and value["sections"]["outcome"]["status"] == "unknown"


def test_lua_composition_isolates_locals_errors_and_early_returns():
    from lupa.lua51 import LuaRuntime
    lua = LuaRuntime()
    rows = []
    lua.globals().print = lambda row: rows.append(str(row))
    lua.execute('''Game={GetLocalPlayer=function()return 0 end,GetCurrentGameTurn=function()return 12 end}
GameConfiguration={GetValue=function()return 4242 end}
PlayerConfigurations={[0]={GetCivilizationTypeName=function()return "CIVILIZATION_ROME"end}}
UI={RequestPlayerOperation=function()error("Mutation forbidden")end}''')
    selected = {"first": ('local a=1;print("one");return', None, None),
                "broken": ('error("unavailable")', None, None),
                "last": ('assert(a==nil);print("last")', None, None)}
    lua.execute(decision.build_query(selected))
    parsed = decision.unpack_sections(rows, selected)
    assert parsed["first"]["rows"] == ["one"]
    assert "DECISION_ERROR|" in parsed["broken"]["rows"][0]
    assert parsed["last"]["rows"] == ["last"]
    assert rows[-1] == f"TURN_WORLD|{WORLD}"


def test_named_contract_validates_optional_reads_against_real_tool_schema():
    from jsonschema import Draft202012Validator
    intent = parse_command("game decision --include research diplomacy --city 1 2 --view structured")
    assert intent.tool == "get_decision_context"
    Draft202012Validator(server.mcp._tool_manager.get_tool(intent.tool).parameters).validate(intent.arguments)
    assert intent.arguments == dict(include=["research", "diplomacy"], city_ids=[1, 2], view="structured")


def test_composed_native_threat_reader_keeps_barbarians_distinct_and_hides_fog():
    from lupa.lua51 import LuaRuntime
    from civ_mcp import lua as lq
    lua = LuaRuntime()
    rows = []
    lua.globals().print = lambda row: rows.append(str(row))
    lua.execute('''
Game={GetLocalPlayer=function()return 0 end,GetCurrentGameTurn=function()return 12 end}
GameConfiguration={GetValue=function()return 4242 end}
PlayerConfigurations={[0]={GetCivilizationTypeName=function()return "CIVILIZATION_ROME"end},
 [1]={GetCivilizationShortDescription=function()return "Major"end},
 [10]={GetCivilizationShortDescription=function()return "City State"end}}
Locale={Lookup=function(v)return v end}
GameInfo={Units={[0]={Combat=25,RangedCombat=0,UnitType="UNIT_SPEARMAN"}}}
Map={GetPlotDistance=function(x,y,a,b)return math.abs(x-a)+math.abs(y-b)end}
PlayersVisibility={[0]={IsVisible=function(_,x)return x<9 end}}
local function unit(x) return{GetX=function()return x end,GetY=function()return 0 end,
 GetType=function()return 0 end,GetMaxDamage=function()return 100 end,
 GetDamage=function()return 0 end,GetID=function()return x end}end
local function collection(values)return{Members=function()return ipairs(values)end}end
Players={[0]={GetDiplomacy=function()return{IsAtWarWith=function(_,id)return id==10 end}end,
 GetCities=function()return collection({})end,GetUnits=function()return collection({unit(0)})end}}
for _,id in ipairs({1,10,63})do
 local owner=id
 Players[id]={IsAlive=function()return true end,IsMajor=function()return owner==1 end,
  GetUnits=function()return collection({unit(2),unit(9)})end}
end
UI={RequestPlayerOperation=function()error("No mutations")end}
''')
    source = decision.build_query({"threats": (lq.build_threat_scan_query(), None, None)})
    lua.execute(source)
    data = decision.unpack_sections(rows, {"threats": None})["threats"]["rows"]
    threats = lq.parse_threat_scan_response(data)
    assert len(threats) == 3 and all(t.x == 2 for t in threats)
    assert {t.owner_id: t.is_city_state for t in threats} == {1: False, 10: True, 63: False}


def test_real_stdio_pending_action_is_not_confirmed_by_decision_reader(tmp_path):
    from test_debug_cli import MOVE, run_fake
    _, _, events, _, _ = run_fake(tmp_path, MOVE + "state decision\npending\nquit --abandon\n")
    event = next(r for r in events if r["event"] == "tool_result" and r["tool"] == "get_decision_context")
    value = json.loads(event["result"]["content"][0]["text"])
    assert value["status"] == "blocked" and value["pending"]["action"]
