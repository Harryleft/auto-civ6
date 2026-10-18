"""Native chosen labels, not Winner numbers, determine the displayed effect."""

import asyncio

import pytest

from civ_mcp.game_state import GameState
from civ_mcp.lua.congress import parse_world_congress_response
from civ_mcp.narrate import narrate_world_congress
from test_congress_eligibility import CongressConn


def outcome_conn():
    conn = CongressConn({}, stage="NONE", resolutions=[{
        "Type": 13, "Winner": 0, "ChosenLabel": "B", "ChosenThing": "Production",
    }])
    conn.lua.execute('''
in_session=false
GameInfo.Resolutions[13].Effect1Description="Military production costs more"
GameInfo.Resolutions[13].Effect2Description="Military production costs less"
''')
    return conn


def test_native_chosen_b_overrides_winner_zero_in_query_and_display():
    conn = outcome_conn()
    status = asyncio.run(GameState(conn).get_world_congress())
    text = narrate_world_congress(status)
    assert "Outcome B (Production): Military production costs less" in text
    assert "Military production costs more" not in text
    assert status.resolutions[0].winner == 0
    assert status.resolutions[0].chosen_label == "B"
    assert conn.operations == conn.actions == conn.opens == []


@pytest.mark.parametrize("turns_left", [0, 15])
@pytest.mark.parametrize("winner", [0, 1, 42])
@pytest.mark.parametrize("label,effect", [
    ("A", "Military production costs more"),
    ("B", "Military production costs less"),
])
def test_native_label_alone_selects_effect_in_both_post_session_views(turns_left, winner, label, effect):
    conn = outcome_conn()
    conn.lua.globals().resolutions[1].Winner = winner
    conn.lua.globals().resolutions[1].ChosenLabel = label
    conn.lua.globals().turns_left = turns_left
    conn.lua.execute('''
local world=Game.GetWorldCongress()
world.GetMeetingStatus=function() return {TurnsLeft=turns_left} end
Game.GetWorldCongress=function() return world end
''')
    status = asyncio.run(GameState(conn).get_world_congress())
    assert status.resolutions[0].chosen_label == label
    assert status.resolutions[0].winner == winner
    assert f"Outcome {label} (Production): {effect}" in narrate_world_congress(status)
    assert conn.operations == conn.actions == conn.opens == []


@pytest.mark.parametrize("label", [None, "", "b", "B ", "A|B", 0, False])
def test_missing_or_invalid_native_label_never_defaults_from_winner(label):
    conn = outcome_conn()
    conn.lua.globals().resolutions[1].ChosenLabel = label
    status = asyncio.run(GameState(conn).get_world_congress())
    text = narrate_world_congress(status)
    assert status.resolutions[0].chosen_label == ""
    assert "Outcome UNKNOWN (Production)" in text
    assert "Military production costs" not in text
    assert conn.operations == conn.actions == conn.opens == []


def test_unreadable_native_label_retains_unknown_result_without_failing_query():
    conn = outcome_conn()
    conn.lua.execute('''
resolutions[1].ChosenLabel=nil
setmetatable(resolutions[1], {__index=function(_,key)
    if key=="ChosenLabel" then error("native label unavailable") end
end})
''')
    status = asyncio.run(GameState(conn).get_world_congress())
    assert status.resolutions[0].chosen_label == ""
    assert "Outcome UNKNOWN" in narrate_world_congress(status)
    assert conn.operations == conn.actions == conn.opens == []


@pytest.mark.parametrize("suffix", ["", "|", "|0", "|b", "|A "])
def test_parser_legacy_or_invalid_label_does_not_infer_option_from_winner(suffix):
    status = parse_world_congress_response([
        "WC_STATUS|false|15|10|0|",
        "WC_RES|13|TEST|Resolution|YIELD|Effect A|Effect B|1|0|Production|" + suffix,
    ])
    assert status.resolutions[0].chosen_label == ""
    assert "Outcome UNKNOWN (Production)" in narrate_world_congress(status)
    assert "Effect A" not in narrate_world_congress(status)


def test_in_session_candidates_keep_both_effects_without_past_outcome():
    conn = outcome_conn()
    conn.lua.execute('in_session=true;segment="TURNSEG_WORLDCONGRESS_1"')
    status = asyncio.run(GameState(conn).get_world_congress())
    text = narrate_world_congress(status)
    assert "Option A: Military production costs more" in text
    assert "Option B: Military production costs less" in text
    assert "Outcome" not in text
    assert status.resolutions[0].chosen_label == ""
    assert conn.operations == conn.actions == conn.opens == []
