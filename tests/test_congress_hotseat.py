"""Native hotseat continuation is a seat handoff, not global stage completion."""
import asyncio

import pytest

from civ_mcp.game_state import GameState
from test_congress_eligibility import CongressConn


def hotseat_connection():
    conn = CongressConn({}, stage='STAGE1')
    conn.lua.execute('''
local_owner=0;paused=false;human0=true;human1=true
Game.GetLocalPlayer=function() return local_owner end
GameConfiguration.IsHotseat=function() return true end
GameConfiguration.IsPaused=function() return paused end
PlayerConfigurations[1]={GetCivilizationTypeName=function() return "CIVILIZATION_SWEDEN" end}
Players[0].IsHuman=function() return human0 end
Players[1]={IsHuman=function() return human1 end,IsTurnActive=function() return true end}
UI.RequestAction=function(action) record_action(action);local_owner=1;paused=true end
''')
    return conn


def test_exact_votes_confirm_hotseat_handoff_without_stage_advance_or_replay():
    conn=hotseat_connection();game=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(game.queue_wc_votes([]))
    assert game.congress_pending
    result=asyncio.run(game.confirm_hotseat_congress_handoff())
    assert result.startswith('HS_HANDOFF|from:0|to:1|turn:67|both_human:true|via:WORLD_CONGRESS')
    assert not game.congress_pending
    assert conn.operations==[(0,'submit',{})] and conn.actions==['continue']
    assert 'NO_CHANGE' in asyncio.run(game.confirm_hotseat_congress_handoff())
    assert len(conn.actions)==1


@pytest.mark.parametrize('change', [
    'human1=false', 'paused=false', '__civmcp_wc_request.continued=false',
    '__civmcp_wc_request.token="other"', '__civmcp_wc_request.seed=999',
    '__civmcp_wc_request.state="SUBMIT_REQUESTED"',
])
def test_unconfirmed_or_wrong_scope_handoff_keeps_original_gate(change):
    conn=hotseat_connection();game=GameState(conn)
    asyncio.run(game.queue_wc_votes([]))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(game.confirm_hotseat_congress_handoff())
    assert game.congress_pending and len(conn.actions)==1


def test_lost_vote_reply_is_confirmed_from_native_latch_without_replay():
    conn=hotseat_connection();game=GameState(conn);conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(game.queue_wc_votes([]))
    assert asyncio.run(game.confirm_hotseat_congress_handoff()).startswith('HS_HANDOFF|')
    assert conn.operations==[(0,'submit',{})] and conn.actions==['continue']


def test_t217_last_human_vote_can_close_session_and_handoff_at_next_turn():
    conn=hotseat_connection();game=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(game.queue_wc_votes([]))
    conn.lua.execute('Game.GetCurrentGameTurn=function() return 68 end;in_session=false;segment="TURNSEG_NORMAL"')
    result=asyncio.run(game.confirm_hotseat_congress_handoff())
    assert result.startswith('HS_HANDOFF|from:0|to:1|turn:68|both_human:true|via:WORLD_CONGRESS')
    assert '|stage_finished:true' in result
    assert not game.congress_pending
    assert conn.operations==[(0,'submit',{})] and conn.actions==['continue']


@pytest.mark.parametrize('change', [
    'in_session=true', 'segment="TURNSEG_WORLDCONGRESS_RESOLUTION"',
    'segment="TURNSEG_WORLDCONGRESS_1"',
    'Game.GetCurrentGameTurn=function() return 69 end',
    '__civmcp_wc_request.continued=false', 'paused=false', 'human1=false',
])
def test_next_turn_handoff_needs_closed_session_native_normal_phase_and_original_latch(change):
    conn=hotseat_connection();game=GameState(conn)
    asyncio.run(game.queue_wc_votes([]))
    conn.lua.execute('Game.GetCurrentGameTurn=function() return 68 end;in_session=false;segment="TURNSEG_NORMAL";' + change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(game.confirm_hotseat_congress_handoff())
    assert game.congress_pending and conn.actions==['continue']


@pytest.mark.parametrize('change,restore', [
    ('GameConfiguration.GetValue=function() return 1 end', 'GameConfiguration.GetValue=function() return 719665280 end'),
    ('PlayerConfigurations[0].GetCivilizationTypeName=function() return "CIVILIZATION_OTHER" end',
     'PlayerConfigurations[0].GetCivilizationTypeName=function() return "CIVILIZATION_SUMERIA" end'),
])
def test_observed_hotseat_world_change_cannot_borrow_a_restored_handoff(change,restore):
    conn=hotseat_connection();game=GameState(conn)
    asyncio.run(game.queue_wc_votes([]))
    conn.lua.execute(change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(game.confirm_hotseat_congress_handoff())
    conn.lua.execute(restore)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(game.confirm_hotseat_congress_handoff())
    assert game.congress_pending and conn.actions==['continue']
