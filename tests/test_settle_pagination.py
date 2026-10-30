"""Pages recover the unranked board; explicit regions do not change scope."""
import asyncio
import json

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from civ_mcp.devtools.commands import parse_command
from test_server import make_ctx
from test_settle_advice_scope import SettleConn


def page(ctx,**args):
    return json.loads(asyncio.run(server.get_global_settle_advisor(ctx,**args)))


def test_pages_recover_same_order_with_counts_including_empty_tail():
    ctx=make_ctx(game=GameState(SettleConn()))
    full=page(ctx,limit=200)
    first=page(ctx,limit=7);second=page(ctx,limit=7,offset=7);third=page(ctx,limit=7,offset=14)
    assert full['total']==20 and full['shown']==20
    assert [p['shown'] for p in (first,second,third)]==[7,7,6]
    assert first['next_offset']==7 and second['next_offset']==14 and third['next_offset'] is None
    assert first['omitted']==13 and first['truncated']
    coords=lambda p:[line.split(')')[0].split('(')[-1] for line in p['report'].splitlines() if line.startswith('  ')]
    assert coords(first)+coords(second)+coords(third)==coords(full)
    tail=page(ctx,offset=50);assert tail['shown']==0 and tail['total']==20 and tail['next_offset'] is None


def test_native_region_filters_before_scoring_and_reports_its_own_total():
    ctx=make_ctx(game=GameState(SettleConn()))
    p=page(ctx,center_x=10,center_y=0,radius=2,limit=200)
    assert p['total']==5 and p['scope']==dict(center_x=10,center_y=0,radius=2)
    assert '(8,0)' in p['report'] and '(12,0)'in p['report'] and '(7,0)'not in p['report']


def test_partial_region_rejected_before_game_io():
    conn=SettleConn()
    with pytest.raises(ValueError):asyncio.run(GameState(conn).get_global_settle_scan(center_x=1))


def test_named_cli_preserves_region_and_page_parameters():
    cmd=parse_command('map settle-global --center-x 40 --center-y 25 --radius 5 --limit 8 --offset 16')
    assert cmd.arguments==dict(center_x=40,center_y=25,radius=5,limit=8,offset=16)
