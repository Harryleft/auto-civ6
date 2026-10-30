"""Tile quote precision, native eligibility and exact purchase receipts."""
import asyncio

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_purchasable_tiles
from test_tile_purchase_confirmation import TileConn


def connection():
    conn = TileConn()
    conn.lua.execute('''
Locale={Lookup=function(v)return v end}
GameInfo.Terrains={[0]={Name='Grass'}}
GameInfo.Resources={};GameInfo.Features={};GameInfo.Technologies={}
Players[0].GetTechs=function()return {}end
for _,p in pairs(plots)do
 p.GetTerrainType=function()return 0 end;p.IsHills=function()return false end
 p.GetResourceType=function()return -1 end
end
''')
    return conn


def read(conn):
    return asyncio.run(GameState(conn).get_purchasable_tiles(65537))


def test_unrelated_target_arrays_do_not_become_purchasable_tiles_and_fraction_is_exact():
    conn=connection()
    conn.lua.execute("CityManager.GetCommandTargets=function()return {plots={11},blocked={12},other={12}}end")
    result=read(conn)
    assert len(result)==1 and result[0].cost==35.25
    assert result[0].can_purchase is True
    assert conn.lua.eval('#submissions')==0


def test_zero_price_quote_is_kept_and_native_purchase_has_zero_exact_charge():
    conn=connection();conn.lua.execute('cost=0')
    result=read(conn)
    assert len(result)==2 and result[0].cost==0
    assert result[0].can_purchase is True
    receipt=asyncio.run(GameState(conn).purchase_tile(65537,11,10))
    assert receipt.startswith('TILE_PURCHASED|'),receipt
    assert conn.lua.eval('balance')==120.5 and conn.lua.eval('#submissions')==1


def test_unaffordable_and_native_refused_quotes_show_distinct_reasons():
    conn=connection();conn.lua.execute('balance=10;commandAllowed=false')
    tile=read(conn)[0]
    assert tile.can_purchase is False
    assert tile.reason=='INSUFFICIENT_GOLD'
    assert 'short 25.25' in narrate_purchasable_tiles([tile])
    conn.lua.execute('balance=120.5')
    tile=read(conn)[0]
    assert tile.reason=='NATIVE_INELIGIBLE'


def test_unknown_price_cannot_become_zero_or_purchase_eligible():
    conn=connection();conn.lua.execute("city.GetGold=function()return {GetPlotPurchaseCost=function()error('unknown')end}end")
    tile=read(conn)[0]
    assert tile.cost is None and tile.can_purchase is None
    assert 'unknown' in narrate_purchasable_tiles([tile])


def test_missing_native_targets_preserves_error_instead_of_empty_list():
    conn=connection();conn.lua.execute('CityManager.GetCommandTargets=function()return nil end')
    assert isinstance(read(conn),str) and 'NO_TARGETS' in read(conn)


def test_large_fractional_quote_and_receipt_keep_full_precision():
    conn=connection();conn.lua.execute('cost=1000001.25;balance=2000002.5')
    tile=read(conn)[0]
    assert 'gold 1000001.25' in narrate_purchasable_tiles([tile])
    receipt=asyncio.run(GameState(conn).purchase_tile(65537,11,10))
    assert 'cost:1000001.25' in receipt and '2000002.5->1000001.25' in receipt
