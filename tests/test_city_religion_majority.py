"""Owned city religion facts share the existing city/decision query, never a scan."""
import asyncio
import json

import pytest

from civ_mcp import decision, narrate
from civ_mcp.game_state import GameState
from civ_mcp.lua import cities
from test_resource_hint_visibility import ResourceConn


class MajorityConn(ResourceConn):
    def __init__(self, patch=""):
        super().__init__()
        self.calls = []
        self.lua.execute('''
Game.GetCurrentGameTurn=function()return 77 end
GameConfiguration={GetValue=function()return -1896272000 end}
PlayerConfigurations={[0]={GetCivilizationTypeName=function()return 'CIVILIZATION_SUMERIA'end}}
counts={city_religion=0,majority=0,game_religion=0,name=0}
majorities={[1]=11,[2]=8,[3]=11}
names={[11]='Taoism',[8]='Protestantism',[0]='Pantheon'}
Game.GetReligion=function()
 counts.game_religion=counts.game_religion+1
 return{GetName=function(_,id)counts.name=counts.name+1;return names[id]end}
end
owned={}
for i,id in ipairs({65536,131073,196610})do
 local pos,full=i,id
 local clone={};for key,value in pairs(city)do clone[key]=value end
 clone.GetID=function()return full end
 clone.GetName=function()return ({'Uruk','Eridu','Bad-tibira'})[pos]end
 clone.GetX=function()return pos end
 clone.GetReligion=function()
  counts.city_religion=counts.city_religion+1
  return{GetMajorityReligion=function()counts.majority=counts.majority+1;return majorities[pos]end}
 end
 owned[i]=clone
end
Players[0].GetCities=function()return{Members=function()return ipairs(owned)end}end
''' + patch)

    async def execute_write(self, source, timeout=None):
        self.calls.append(source)
        return await super().execute_write(source)


def read(conn):
    values, _ = asyncio.run(GameState(conn).get_cities())
    assert len(conn.calls) == 1 and conn.lua.eval("native_requests") == 0
    return values


def test_each_owned_city_has_its_own_native_majority_and_names_are_query_cached():
    conn = MajorityConn()
    values = read(conn)
    assert [c.religion_majority_id for c in values] == [11, 8, 11]
    assert [c.religion_majority_name for c in values] == ["Taoism", "Protestantism", "Taoism"]
    assert all(c.religion_majority_source == "InGame" for c in values)
    assert all(c.owner_id == 0 and c.game_identity == ("sumeria", -1896272000) for c in values)
    assert dict(conn.lua.globals().counts.items()) == {"city_religion": 3, "majority": 3, "game_religion": 1, "name": 2}
    text = narrate.narrate_cities(values)
    assert "Eridu" in text and "Religion:Protestantism (id:8)" in text
    assert "InGame native getter; GameCore freshness unverified" in text


def test_native_none_zero_and_unknown_remain_distinct():
    conn = MajorityConn("majorities[1]=-1;majorities[2]=0;majorities[3]=nil")
    values = read(conn)
    assert [c.religion_majority_id for c in values] == [-1, 0, None]
    assert [c.religion_majority_name for c in values] == [None, "Pantheon", None]
    text = narrate.narrate_cities(values)
    assert "Religion:none (id:-1)" in text
    assert "Religion:Pantheon (id:0)" in text
    assert "Religion:unknown" in text


@pytest.mark.parametrize("value", ["nil", "false", "'11'", "11.5", "-2", "math.huge", "0/0"])
def test_invalid_or_absent_native_majority_is_unknown_not_none(value):
    values = read(MajorityConn(f"majorities[2]={value}"))
    assert values[1].religion_majority_id is None
    assert values[1].religion_majority_name is None
    assert values[0].religion_majority_id == values[2].religion_majority_id == 11


def test_majority_getter_exception_does_not_erase_other_city_facts():
    conn = MajorityConn("owned[2].GetReligion=function()error('getter unavailable')end")
    value = read(conn)[1]
    assert value.name == "Eridu" and value.population == 3
    assert value.religion_majority_id is None
    assert value.religion_majority_source == "InGame"


def test_missing_name_preserves_known_native_id():
    values = read(MajorityConn("names[8]=nil"))
    assert values[1].religion_majority_id == 8 and values[1].religion_majority_name is None
    assert "Religion:name unknown (id:8)" in narrate.narrate_cities(values)


def test_native_custom_name_roundtrips_unicode_pipes_quotes_and_controls():
    conn = MajorityConn()
    name = '自定义"\\\n宗教' + '|a' * 20 + '\x01'
    conn.lua.globals().custom = name
    conn.lua.execute("names[11]=custom")
    values = read(conn)
    assert values[0].religion_majority_name == values[2].religion_majority_name == name


def test_query_cache_does_not_persist_names_across_reads():
    conn = MajorityConn()
    read(conn)
    conn.lua.execute("names[11]='Renamed'")
    values, _ = asyncio.run(GameState(conn).get_cities())
    assert values[0].religion_majority_name == "Renamed"
    assert conn.lua.eval("counts.game_religion") == 2 and conn.lua.eval("counts.name") == 4


def test_foreign_collection_mismatch_never_reads_its_religion():
    conn = MajorityConn("owned[2].GetOwner=function()return 1 end;owned[2].GetReligion=function()error('foreign religion must not be read')end")
    values = read(conn)
    assert values[1].owner_id == 1 and values[1].religion_majority_id is None
    assert values[1].religion_majority_source is None
    assert conn.lua.eval("counts.city_religion") == 2


@pytest.mark.parametrize("tamper", [
    lambda rows, rel: rows + [rel],
    lambda rows, rel: rows + [rel.replace('|11|', '|8|')],
    lambda rows, rel: [row for row in rows if not row.startswith('CITYRELIGION|65536|')],
    lambda rows, rel: [row.replace('CITYRELIGION|65536|0|0|', 'CITYRELIGION|65536|1|0|') for row in rows],
    lambda rows, rel: [row.replace('CITYRELIGION|65536|0|0|CIVILIZATION_SUMERIA|-1896272000|', 'CITYRELIGION|65536|0|0|CIVILIZATION_SUMERIA|7|') for row in rows],
    lambda rows, rel: rows + ['CITYRELIGION|999|0|0|CIVILIZATION_SUMERIA|-1896272000|11|"Taoism"'],
    lambda rows, rel: rows + ['ERR:CITY_ID_CHANGED'],
])
def test_ambiguous_missing_or_conflicting_extensions_stay_unknown(tamper):
    conn = MajorityConn()
    read(conn)
    rows = conn.rows
    rel = next(row for row in rows if row.startswith("CITYRELIGION|65536|"))
    values, _ = cities.parse_cities_response(tamper(rows, rel))
    assert values[0].religion_majority_id is None
    assert values[0].religion_majority_name is None
    assert values[0].population == 3


def test_legacy_city_rows_keep_unknown_compatibility_without_inference():
    conn = MajorityConn()
    read(conn)
    values, _ = cities.parse_cities_response([r for r in conn.rows if not r.startswith("CITYRELIGION|")])
    assert all(c.religion_majority_id is None and c.religion_majority_source is None for c in values)
    assert "Religion:unknown" in narrate.narrate_cities(values)


def test_combined_city_query_reuses_the_same_reader_in_one_transport_request():
    conn = MajorityConn()
    selected = {"cities": decision.readers()["cities"]}
    rows = asyncio.run(conn.execute_write(decision.build_query(selected)))
    frame = decision.unpack_sections(rows, selected)["cities"]
    values, _ = cities.parse_cities_response(frame["rows"])
    assert [c.religion_majority_id for c in values] == [11, 8, 11]
    assert len(conn.calls) == 1 and conn.lua.eval("native_requests") == 0


def test_default_decision_brief_and_structured_keep_five_scope_checks():
    from test_decision_context import Conn, response
    conn = MajorityConn("GameConfiguration.GetValue=function()return 4242 end;PlayerConfigurations[0].GetCivilizationTypeName=function()return 'CIVILIZATION_ROME'end")
    read(conn)
    sections = {"cities": conn.rows}
    framed = response(sections)
    overview = framed.index("DECISION_BEGIN|overview") + 1
    parts = framed[overview].split("|");parts[11] = "3";framed[overview] = "|".join(parts)
    for view in ("brief", "structured"):
        fake = Conn(framed)
        result = asyncio.run(GameState(fake).get_decision_context(view=view))
        assert result["status"] == "ready" and len(result["checks"]) == 5 and all(result["checks"].values())
        assert len(fake.calls) == 1
        data = result["sections"]["cities"]["data"]
        if view == "brief":
            assert "Religion:Protestantism (id:8)" in data
        else:
            assert [c["religion_majority_id"] for c in data["cities"]] == [11, 8, 11]


STDIO_DRIVER = '''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from test_city_religion_majority import MajorityConn
from civ_mcp import server
conn=MajorityConn();conn.is_connected=True
def persist():
 Path(sys.argv[1]).write_text(json.dumps({'queries':len(conn.calls),
  'counts':dict(conn.lua.globals().counts.items()),'writes':conn.lua.eval('native_requests'),
  'pending':game.pending_action_summary}))
async def disconnect():persist()
conn.disconnect=disconnect
server.GameConnection=lambda:conn
game=server.GameState(conn)
server.GameState=lambda connection:game
class Logger:
 async def log_tool_call(self,*args):persist()
server.GameLogger=Logger
server.main()
'''


def test_named_city_list_real_stdio_shows_each_majority_without_an_extra_query(tmp_path):
    from io import StringIO
    from pathlib import Path
    import sys
    from mcp import StdioServerParameters
    from civ_mcp.devtools.debug import serve
    from civ_mcp.devtools.evidence import Evidence
    project = Path(__file__).resolve().parents[1]
    driver = tmp_path / "driver.py"
    driver.write_text(STDIO_DRIVER)
    receipt = tmp_path / "receipt.json"
    evidence = Evidence(project, ["city-majority-stdio"], str(tmp_path / "evidence"))
    state, events = {}, []
    status = asyncio.run(serve(evidence, state, offline=False, timeout=5,
        source=StringIO("city list\npending\nquit\n"), emit=events.append,
        server=StdioServerParameters(command=sys.executable,
            args=[str(driver), str(receipt), str(project / "tests")], cwd=str(project))))
    engine = json.loads(receipt.read_text())
    assert status == "PASS" and state["tool_calls"] == 1
    assert engine["queries"] == 1 and engine["writes"] == 0 and engine["pending"] is None
    assert engine["counts"] == {"city_religion": 3, "majority": 3, "game_religion": 1, "name": 2}
    result = next(row for row in events if row["event"] == "tool_result" and row["tool"] == "get_cities")
    text = next(row["text"] for row in result["result"]["content"] if row["type"] == "text")
    assert "Religion:Taoism (id:11)" in text and "Religion:Protestantism (id:8)" in text
