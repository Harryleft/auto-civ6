"""Governor advice uses the same native per-choice eligibility as actions."""
import asyncio

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_governors
from test_governor_confirmation import GovernorConn


def test_default_board_does_not_advertise_native_refused_promotions_or_appointments():
    conn=GovernorConn();conn.lua.execute('addGovernor(101);eligible=false')
    status=asyncio.run(GameState(conn).get_governors())
    assert status.appointed[0].available_promotions==[]
    assert status.available_to_appoint==[]
    assert status.omitted_promotions==2 and status.omitted_appointments==1
    assert '--include-ineligible' in narrate_governors(status)


def test_expansion_keeps_unowned_tree_and_explicit_native_refusal():
    conn=GovernorConn();conn.lua.execute('addGovernor(101);eligible=false')
    status=asyncio.run(GameState(conn).get_governors(include_ineligible=True))
    assert all(p.can_earn is False for p in status.appointed[0].available_promotions)
    assert status.available_to_appoint[0].can_appoint is False
    assert 'native can earn no' in narrate_governors(status)
    assert conn.lua.eval('#submissions')==0


def test_native_unknown_is_retained_in_default_instead_of_hiding_a_candidate():
    conn=GovernorConn();conn.lua.execute('addGovernor(101);Players[0]:GetGovernors().CanEarnPromotion=nil')
    status=asyncio.run(GameState(conn).get_governors())
    assert len(status.appointed[0].available_promotions)==2
    assert all(p.can_earn is None for p in status.appointed[0].available_promotions)
    assert 'native can earn unknown' in narrate_governors(status)
