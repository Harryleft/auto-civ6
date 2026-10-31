"""Current government eligibility must explain the native T57 free-window refusal."""
import asyncio

import pytest

from civ_mcp.game_state import GameState
from civ_mcp.lua import public_reports
from test_player_choices import ChoiceConn


def connection(patch=""):
    conn = ChoiceConn()
    conn.lua.execute('''
Locale={Lookup=function(value)return value end}
PlayersVisibility={[0]={}}
Players[0].GetDiplomacy=function()return{HasMet=function()return false end}end
Players[0].GetTreasury=function()error("Government eligibility must not infer policy-unlock payment")end
local row=GameInfo.Governments.GOVERNMENT_OLIGARCHY
setmetatable(GameInfo.Governments,{__call=function()
 local done=false;return function()if not done then done=true;return row end end
end})
GameInfo.Government_SlotCounts=function()return function()end end
''' + patch)
    return conn


def report(conn):
    conn.rows.clear()
    conn.lua.execute(public_reports.build("governments"))
    result = public_reports.parse(conn.rows)
    assert conn.lua.eval("#submissions") == 0
    assert conn.lua.eval("considerationCalls") == 0
    return result


def test_native_general_permission_does_not_hide_closed_free_change_window():
    conn = connection("civicCompleted=false")
    value = report(conn)
    assert value["status"] == "ready"
    data = value["data"]
    assert data["can_change"] is data["can_change_at_all"] is True
    assert data["change_conditions"]["free_change_window"] is False
    assert data["can_change_now"] is False
    target = data["governments"][0]
    assert target["can_select_now"] is False
    assert target["refusal_reasons"] == ["NO_FREE_GOVERNMENT_CHANGE_WINDOW"]
    assert target["anarchy_turns"] == 0
    assert "CANNOT_CHANGE_GOVERNMENT" in asyncio.run(GameState(conn).change_government("GOVERNMENT_OLIGARCHY"))
    assert conn.lua.eval("#submissions") == 0


@pytest.mark.parametrize("patch,reason", [
    ("busy=true", "CORE_BUSY_OR_UNKNOWN"),
    ("Players[0].IsTurnActive=function()return false end", "NOT_ACTIVE_TURN"),
    ("Game.IsAllowStrategicCommands=function()return false end", "STRATEGIC_COMMANDS_DISABLED"),
    ("eligible=false", "GOVERNMENT_CHANGE_DISABLED"),
    ("made=true", "GOVERNMENT_CHANGE_ALREADY_MADE"),
    ("anarchy=true", "ANARCHY_ALREADY_ACTIVE"),
    ("culture.SetGovernmentChangeConsidered=nil", "GOVERNMENT_CONSIDERATION_UNAVAILABLE"),
    ("culture.RequestChangeGovernment=nil", "GOVERNMENT_REQUEST_UNAVAILABLE"),
])
def test_known_preflight_refusals_are_reported_without_requests(patch, reason):
    value = report(connection(patch))
    target = value["data"]["governments"][0]
    assert target["can_select_now"] is False
    assert reason in target["refusal_reasons"]
    assert value["data"]["can_change_now"] is False


@pytest.mark.parametrize("body", ["return nil", "return 1", 'error("unavailable")'])
def test_unreadable_condition_is_unknown_not_false_or_permitted(body):
    value = report(connection(f"culture.CivicCompletedThisTurn=function(){body} end"))
    assert value["status"] == "partial"
    assert "government.civic_completed_this_turn" in value["unknown_fields"]
    assert value["data"]["change_conditions"]["civic_completed_this_turn"] is None
    assert value["data"]["change_conditions"]["free_change_window"] is None
    assert value["data"]["can_change_now"] is None
    assert value["data"]["governments"][0]["can_select_now"] is None
    assert value["data"]["governments"][0]["refusal_reasons"] == []


def test_known_refusal_survives_other_unknown_conditions():
    value = report(connection("made=true;culture.CivicCompletedThisTurn=nil"))
    target = value["data"]["governments"][0]
    assert value["status"] == "partial"
    assert target["can_select_now"] is False
    assert target["refusal_reasons"] == ["GOVERNMENT_CHANGE_ALREADY_MADE"]


def test_unknown_current_government_does_not_apply_new_selection_only_refusals():
    value = report(connection("made=true;culture.GetCurrentGovernment=nil"))
    target = value["data"]["governments"][0]
    assert value["status"] == "partial"
    assert target["current"] is None
    assert target["can_select_now"] is None
    assert target["refusal_reasons"] == []


def test_keep_current_has_distinct_acknowledgment_eligibility():
    value = report(connection("government=2;eligible=false;made=true;civicCompleted=false;culture.RequestChangeGovernment=nil"))
    target = value["data"]["governments"][0]
    assert target["current"] is True
    assert target["can_select_now"] is True
    assert target["refusal_reasons"] == []
    assert value["data"]["can_change_now"] is False


def test_initial_selection_does_not_require_civic_completion():
    value = report(connection("government=-1;culture.CivicCompletedThisTurn=nil"))
    assert value["status"] == "partial"
    assert value["data"]["change_conditions"]["initial_selection"] is True
    assert value["data"]["change_conditions"]["free_change_window"] is True
    assert value["data"]["governments"][0]["can_select_now"] is True


def test_positive_anarchy_duration_reports_required_previous_interval():
    value = report(connection("anarchyTurns=3;endTurn=-1"))
    target = value["data"]["governments"][0]
    assert target["anarchy_turns"] == 3
    assert target["previous_anarchy_end_turn"] == -1
    assert target["can_select_now"] is True
    assert value["data"]["can_change_now"] is True


def test_unreadable_required_anarchy_interval_is_not_eligible():
    value = report(connection("anarchyTurns=3;culture.GetAnarchyEndTurn=nil"))
    target = value["data"]["governments"][0]
    assert value["status"] == "partial"
    assert target["previous_anarchy_end_turn"] is None
    assert target["can_select_now"] is None
    assert value["data"]["can_change_now"] is None


@pytest.mark.parametrize("number", ["math.huge", "-math.huge", "0/0"])
def test_nonfinite_anarchy_interval_is_unknown_not_native_integer(number):
    value = report(connection(f"anarchyTurns=3;endTurn={number}"))
    assert value["status"] == "partial"
    assert "government.previous_anarchy_end_turn" in value["unknown_fields"]
    assert value["data"]["governments"][0]["previous_anarchy_end_turn"] is None
    assert value["data"]["governments"][0]["can_select_now"] is None


@pytest.mark.parametrize("patch", [
    "eligible=false;culture.GovernmentChangeMade=function()error('must short circuit')end",
    "made=true;culture.CivicCompletedThisTurn=function()error('must short circuit')end",
    "Players[0].IsTurnActive=function()return false end;Game.IsAllowStrategicCommands=function()error('must short circuit')end",
])
def test_shared_predicates_preserve_write_short_circuits(patch):
    conn = connection(patch)
    result = asyncio.run(GameState(conn).change_government("GOVERNMENT_OLIGARCHY"))
    assert "CANNOT_CHANGE_GOVERNMENT" in result
    assert "ELIGIBILITY_UNKNOWN" not in result
    assert conn.lua.eval("#submissions") == 0
    assert conn.lua.eval("considerationCalls") == 0


def test_world_change_discards_government_conditions():
    conn = connection("local calls=0;Game.GetCurrentGameTurn=function()calls=calls+1;return calls==1 and turn or turn+1 end")
    value = report(conn)
    assert value["status"] == "unknown" and value["data"] is None


def test_government_report_uses_one_ingame_read_without_retiring_pending():
    conn = connection("civicCompleted=false")
    reads = []

    async def execute_in_named_state(context, source):
        reads.append(context)
        conn.rows.clear()
        conn.lua.execute(source)
        return list(conn.rows)

    conn.execute_in_named_state = execute_in_named_state
    state = GameState(conn)
    state._pending_end_turn = True
    result = asyncio.run(state.get_public_report("governments"))
    assert result["data"]["governments"][0]["can_select_now"] is False
    assert reads == ["InGame"]
    assert state._pending_end_turn is True
    assert conn.lua.eval("#submissions") == conn.lua.eval("considerationCalls") == 0
