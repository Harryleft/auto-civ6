"""Native UI choices and current command eligibility, with an isolated Lua engine."""
import asyncio
import json
import math

import pytest
from lupa.lua51 import LuaRuntime

from civ_mcp import narrate, server
from civ_mcp.connection import LuaError
from civ_mcp.game_state import GameState
from civ_mcp.lua import unit_promotions
from test_server import make_ctx


class PromotionReadConn:
    def __init__(self, patch=""):
        self.lua = LuaRuntime()
        self.rows = []
        self.queries = []
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.execute('''
turn,seed,owner,fullid,x,y,unitType=72,-1896272000,0,1441796,40,26,7
moves,xp,xpNeeded,busy=4,0,25,false
uiAllowed,currentAllowed=true,true
uiChoices,currentChoices={14,15,16},{14,15,16}
has,checks,writes={},{},0
function noWrite()writes=writes+1;error('read must not mutate')end
exp={GetExperiencePoints=function()return xp end,GetExperienceForNextLevel=function()return xpNeeded end,
 GetPromotions=function()local t={};for i,v in pairs(has)do if v then t[#t+1]=i end end;return t end,
 HasPromotion=function(_,i)return has[i]==true end,SetPromotion=noWrite}
unit={GetOwner=function()return owner end,GetID=function()return fullid end,
 GetX=function()return x end,GetY=function()return y end,GetUnitType=function()return unitType end,
 GetMovesRemaining=function()return moves end,GetExperience=function()return exp end}
UnitManager={GetUnit=function(p,i)assert(p==0 and i==4);return unit end,
 CanStartCommand=function(u,command,loose,results)
  assert(u==unit and command==101 and results==true);checks[#checks+1]=loose
  if loose then return uiAllowed,{[102]=uiChoices}end
  return currentAllowed,{[102]=currentChoices}
 end,RequestCommand=noWrite,RequestOperation=noWrite}
UnitCommandTypes={PROMOTE=101};UnitCommandResults={PROMOTIONS=102}
Game={GetLocalPlayer=function()return 0 end,GetCurrentGameTurn=function()return turn end}
GameConfiguration={GetValue=function()return seed end}
PlayerConfigurations={[0]={GetCivilizationTypeName=function()return 'CIVILIZATION_SUMERIA'end}}
GameInfo={Units={[7]={UnitType='UNIT_APOSTLE',NumRandomChoices=3}},UnitPromotions={}}
for i=14,22 do
 GameInfo.UnitPromotions[i]={Index=i,UnitPromotionType='PROMOTION_TEST_'..i,
 Name='候选 '..i,Description='描述 '..i}
end
Locale={Lookup=function(value)return value end}
UI={IsGameCoreBusy=function()return busy end,SetInterfaceMode=noWrite,RequestPlayerOperation=noWrite}
''' + patch)

    async def execute_in_named_state(self, context, source):
        assert context == "InGame"
        self.queries.append((context, source))
        self.rows.clear()
        self.lua.execute(source)
        return list(self.rows)

    async def execute_read(self, source):
        pytest.fail("Promotion UI/current command facts must use InGame")

    async def execute_write(self, source):
        pytest.fail("Promotion observation must use the named read context")

    def facts(self):
        values = [self.lua.eval(k) for k in ("turn", "seed", "owner", "fullid", "x", "y", "moves", "xp", "writes")]
        return ["NaN" if type(v) is float and math.isnan(v) else v for v in values]


def observe(conn, unit_id=1441796):
    state = GameState(conn)
    before = conn.facts()
    result = asyncio.run(state.get_unit_promotions(unit_id))
    assert conn.facts() == before
    assert len(conn.queries) == 1
    return result, state


def test_free_native_promotion_uses_three_current_choices_not_nine_static_entries():
    conn = PromotionReadConn()
    result, _ = observe(conn)
    assert result.status == "ready" and result.xp < result.xp_needed
    assert result.unit_id == 1441796 and result.unit_index == 4
    assert result.identity["turn"] == 72 and result.identity["civilization"] == "CIVILIZATION_SUMERIA"
    assert result.ui_candidates == result.current_candidates == [14, 15, 16]
    assert [p.index for p in result.promotions] == [14, 15, 16]
    assert all(p.can_select_now is True for p in result.promotions)
    assert conn.lua.eval("#checks") == 2
    assert conn.lua.eval("checks[1]") is True and conn.lua.eval("checks[2]") is False


def test_current_command_refusal_is_separate_from_ui_candidates_and_reasons():
    result, _ = observe(PromotionReadConn("moves=0;currentAllowed=false"))
    assert result.can_offer_promotions is True and result.can_promote_now is False
    assert result.ui_candidates == [14, 15, 16] and all(p.current_candidate for p in result.promotions)
    assert all(p.can_select_now is False for p in result.promotions)
    text = narrate.narrate_unit_promotions(result)
    assert "execute command now: no" in text
    assert "do not establish a refusal reason" in text
    assert "No promotions available" not in text


def test_ui_and_current_sets_remain_distinct_without_hiding_current_only_options():
    result, _ = observe(PromotionReadConn("uiChoices={14,15};currentChoices={15,16}"))
    options = {p.index: p for p in result.promotions}
    assert options[14].ui_candidate is True and options[14].can_select_now is False
    assert options[15].ui_candidate is True and options[15].can_select_now is True
    assert options[16].ui_candidate is False and options[16].can_select_now is True


def test_owned_promotion_is_shown_without_claiming_it_is_selectable():
    result, _ = observe(PromotionReadConn("has[14]=true"))
    assert result.promotions[0].has_promotion is True
    assert result.promotions[0].can_select_now is False
    assert "owned:yes, selectable now:no" in narrate.narrate_unit_promotions(result)


@pytest.mark.parametrize("patch,field", [
    ("currentAllowed=nil", "current.allowed"),
    ("currentAllowed=1", "current.allowed"),
    ("currentChoices=nil", "current.candidates"),
    ("currentChoices={14,14}", "current.candidates"),
    ("currentChoices={14,'15'}", "current.candidates"),
    ("currentChoices={14,999}", "current.candidates"),
    ("UnitManager.CanStartCommand=function()error('unavailable')end", "current.candidates"),
])
def test_unreadable_native_permission_or_set_never_becomes_false_or_empty(patch, field):
    result, _ = observe(PromotionReadConn(patch))
    assert result.status == "partial" and field in result.unknown_fields
    if field == "current.allowed":
        assert result.can_promote_now is None
    else:
        assert result.current_candidates is None
    if result.promotions:
        assert all(p.can_select_now is None for p in result.promotions)


def test_missing_both_sets_is_unknown_not_no_available_promotions():
    result, _ = observe(PromotionReadConn("uiChoices=nil;currentChoices=nil"))
    assert result.promotions is None
    assert result.ui_candidates is result.current_candidates is None
    assert "candidate indices: unknown" in narrate.narrate_unit_promotions(result)


def test_native_known_empty_sets_are_explicitly_distinct():
    result, _ = observe(PromotionReadConn("uiAllowed=false;currentAllowed=false;uiChoices={};currentChoices={}"))
    assert result.status == "ready" and result.promotions == []
    assert result.ui_candidates == result.current_candidates == []
    assert "none (known empty)" in narrate.narrate_unit_promotions(result)


@pytest.mark.parametrize("patch", [
    "fullid=1507332",
    "owner=1",
    "local calls=0;Game.GetCurrentGameTurn=function()calls=calls+1;return calls==1 and turn or turn+1 end",
    "local calls=0;GameConfiguration.GetValue=function()calls=calls+1;return calls==1 and seed or seed+1 end",
    "local calls=0;unit.GetX=function()calls=calls+1;return calls==1 and 40 or 41 end",
])
def test_replaced_unit_or_changing_world_discards_mixed_facts(patch):
    result, _ = observe(PromotionReadConn(patch))
    assert result.status == "unknown" and result.promotions is None
    assert result.can_promote_now is None


def test_low_part_does_not_silently_bind_a_different_full_id():
    result, _ = observe(PromotionReadConn(), 4)
    assert result.status == "unknown" and result.promotions is None
    assert "UNIT_ID_CHANGED" in result.error


@pytest.mark.parametrize("patch,field", [
    ("moves=math.huge", "moves"),
    ("xp=0/0", "xp"),
    ("exp.GetExperienceForNextLevel=nil", "xp_needed"),
    ("exp.HasPromotion=function()return nil end", "promotion.14.has_promotion"),
    ("UI.IsGameCoreBusy=nil", "core_busy"),
])
def test_optional_reference_unknown_does_not_filter_native_free_candidates(patch, field):
    result, _ = observe(PromotionReadConn(patch))
    assert result.status == "partial" and field in result.unknown_fields
    assert result.ui_candidates == result.current_candidates == [14, 15, 16]
    if field.startswith("promotion.") or field == "core_busy":
        assert all(p.can_select_now is None for p in result.promotions)
    else:
        assert all(p.can_select_now is True for p in result.promotions)


def test_text_preserves_native_unicode_quotes_pipes_and_newlines():
    conn = PromotionReadConn()
    native = '译员"|\\\n原生描述'
    conn.lua.globals().description = native
    conn.lua.execute("GameInfo.UnitPromotions[14].Description=description")
    result, _ = observe(conn)
    assert result.promotions[0].description == native


def test_read_does_not_confirm_or_clear_runtime_pending():
    conn = PromotionReadConn()
    state = GameState(conn)
    state._pending_end_turn = True
    result = asyncio.run(state.get_unit_promotions(1441796))
    assert result.status == "ready" and state._pending_end_turn is True
    assert len(conn.queries) == 1 and conn.lua.eval("writes") == 0


def test_transport_failure_is_unknown_not_zero_or_empty():
    conn = PromotionReadConn()

    async def failed(*args):
        raise LuaError("InGame unavailable")

    conn.execute_in_named_state = failed
    result = asyncio.run(GameState(conn).get_unit_promotions(1441796))
    assert result.status == "unknown" and result.promotions is None and result.xp is None


@pytest.mark.parametrize("value", [True, -1, 1.5, "1441796"])
def test_invalid_id_is_rejected_without_native_query(value):
    conn = PromotionReadConn()
    with pytest.raises(ValueError):
        asyncio.run(GameState(conn).get_unit_promotions(value))
    assert conn.queries == [] and conn.lua.eval("writes") == 0


def test_cli_and_public_mcp_keep_same_read_only_entrypoint():
    from civ_mcp.devtools.commands import parse_command
    intent = parse_command("unit promotions 1441796")
    assert intent.tool == "get_unit_promotions" and intent.arguments == {"unit_id": 1441796}
    tool = server.mcp._tool_manager.get_tool(intent.tool)
    assert tool.annotations.readOnlyHint is True
    conn = PromotionReadConn()
    text = asyncio.run(server.get_unit_promotions(make_ctx(game=GameState(conn)), 1441796))
    assert "id:1441796, owner:0, turn:72" in text
    assert "PROMOTION_TEST_22" not in text
    assert len(conn.queries) == 1 and conn.lua.eval("writes") == 0


@pytest.mark.parametrize("tamper", [
    lambda rows: [],
    lambda rows: rows + rows[:1],
    lambda rows: [rows[0].replace('"requested_unit_id":1441796', '"requested_unit_id":1507332')],
    lambda rows: [rows[0].replace('"current_candidates":[14,15,16]', '"current_candidates":[14,14,16]')],
    lambda rows: [rows[0].replace('"can_promote_now":true', '"can_promote_now":1')],
    lambda rows: [rows[0].replace('"moves":4', '"moves":4,"moves":0')],
])
def test_invalid_or_duplicate_native_receipts_stay_unknown(tamper):
    conn = PromotionReadConn()
    conn.lua.execute(unit_promotions.build(1441796))
    rows = [r for r in conn.rows if r.startswith("UNIT_PROMOTIONS|")]
    result = unit_promotions.parse(tamper(rows), 1441796)
    assert result.status == "unknown" and result.promotions is None


@pytest.mark.parametrize("extra", [
    "ERR:UNIT_ID_CHANGED",
    "UNIT|1507332|4|UNIT_APOSTLE",
    "UNIT|1441796|4|UNIT_APOSTLE",
    "POS|40|26|moves:4",
    "unexplained native output",
    "",
])
@pytest.mark.parametrize("prepend", [False, True])
def test_extra_native_response_frames_cannot_leave_promotion_candidates_ready(extra, prepend):
    conn = PromotionReadConn()
    conn.lua.execute(unit_promotions.build(1441796))
    rows = [r for r in conn.rows if r.startswith("UNIT_PROMOTIONS|")]
    lines = [extra, *rows] if prepend else [*rows, extra]
    result = unit_promotions.parse(lines, 1441796)
    assert result.status == "unknown" and result.promotions is None
    assert result.can_promote_now is None


def test_mixed_error_response_preserves_pending_and_does_not_retry_native_query():
    conn = PromotionReadConn()
    execute = conn.execute_in_named_state

    async def mixed(context, source):
        return [*(await execute(context, source)), "ERR:UNIT_ID_CHANGED"]

    conn.execute_in_named_state = mixed
    state = GameState(conn)
    state._pending_end_turn = True
    result = asyncio.run(state.get_unit_promotions(1441796))
    assert result.status == "unknown" and result.promotions is None
    assert state._pending_end_turn is True
    assert len(conn.queries) == 1 and conn.lua.eval("writes") == 0


def test_single_report_accepts_transport_stripped_or_one_trailing_sentinel():
    from civ_mcp.lua._helpers import SENTINEL
    conn = PromotionReadConn()
    conn.lua.execute(unit_promotions.build(1441796))
    rows = [r for r in conn.rows if r.startswith("UNIT_PROMOTIONS|")]
    for lines in (rows, [*rows, SENTINEL]):
        result = unit_promotions.parse(lines, 1441796)
        assert result.status == "ready" and len(result.promotions) == 3
        text = narrate.narrate_unit_promotions(result)
        assert "Execution rechecks current native qualification." in text
        assert "Recheck before choosing" not in text


def test_sentinel_before_report_or_duplicate_sentinels_are_unexplained_frames():
    from civ_mcp.lua._helpers import SENTINEL
    conn = PromotionReadConn()
    conn.lua.execute(unit_promotions.build(1441796))
    rows = [r for r in conn.rows if r.startswith("UNIT_PROMOTIONS|")]
    for lines in ([SENTINEL, *rows], [*rows, SENTINEL, SENTINEL]):
        result = unit_promotions.parse(lines, 1441796)
        assert result.status == "unknown" and result.promotions is None


STDIO_DRIVER = '''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from test_unit_promotion_candidates import PromotionReadConn
from civ_mcp import server
conn=PromotionReadConn();conn.is_connected=True
before=conn.facts()
def persist():
 Path(sys.argv[1]).write_text(json.dumps({'before':before,'after':conn.facts(),
  'queries':len(conn.queries),'command_checks':[conn.lua.eval('checks[1]'),conn.lua.eval('checks[2]')],
  'pending':game.pending_action_summary,'end_turn_pending':game._pending_end_turn}))
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


def test_named_promotions_real_stdio_uses_one_native_read_and_no_submission(tmp_path):
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
    evidence = Evidence(project, ["unit-promotion-stdio"], str(tmp_path / "evidence"))
    state, events = {}, []
    command = "unit promotions 1441796"
    status = asyncio.run(serve(evidence, state, offline=False, timeout=5,
        source=StringIO("unit promote 1441796 PROMOTION_TEST_14 --dry-run\n" + command + "\npending\nquit\n"), emit=events.append,
        server=StdioServerParameters(command=sys.executable,
            args=[str(driver), str(receipt), str(project / "tests")], cwd=str(project))))
    engine = json.loads(receipt.read_text())
    assert status == "PASS" and state["tool_calls"] == 1
    assert engine["queries"] == 1 and engine["command_checks"] == [True, False]
    assert engine["before"] == engine["after"] and engine["pending"] is None
    assert engine["end_turn_pending"] is False
    dry = next(row for row in events if row["event"] == "dry_run")
    assert dry["scope"] == "schema_only" and dry["would_send"] is False
    assert dry["potentially_mutating"] is True
    result = next(row for row in events if row["event"] == "tool_result" and row["tool"] == "get_unit_promotions")
    text = next(row["text"] for row in result["result"]["content"] if row["type"] == "text")
    assert "Current candidate indices: 14,15,16" in text
    assert "PROMOTION_TEST_22" not in text
