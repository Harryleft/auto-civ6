"""Settlement observations must be independent of hidden foreign cities."""
import asyncio

from lupa.lua51 import LuaRuntime

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_settle_candidates


class SettleConn:
    def __init__(self):
        self.rows=[];self.lua=LuaRuntime();self.lua.globals().print=lambda v:self.rows.append(str(v))
        self.lua.execute('''
hidden=false
Game={GetLocalPlayer=function()return 0 end}
local function members(items)return {Members=function()return ipairs(items)end,GetCapitalCity=function()return items[1]end}end
local foreign={GetX=function()return 20 end,GetY=function()return 0 end,
 GetID=function()return 99 end,GetPopulation=function()error('hidden population read')end}
Players={[0]={IsAlive=function()return true end,IsMajor=function()return true end,GetTechs=function()return {}end,GetCities=function()return members({})end},
 [1]={IsAlive=function()return hidden end,IsMajor=function()return true end,GetCities=function()return members({foreign})end}}
PlayersVisibility={[0]={IsRevealed=function(_,i)return i<20 end,IsVisible=function(_,i)return i<20 end}}
GameInfo={Resources={},Technologies={}}
Map={GetGridSize=function()return 20,1 end,
 GetPlotDistance=function(a,b,c,d)return math.abs(a-c)+math.abs(b-d)end,
 GetContinentPlotsLoyalty=function()return {[0]=-7.5}end,
 GetPlot=function(x,y)
 if y~=0 or x<0 or x>20 then return nil end
 return {GetIndex=function()return x end,IsWater=function()return false end,IsMountain=function()return false end,
 GetOwner=function()return -1 end,IsFreshWater=function()return true end,IsCoastalLand=function()return false end,
 IsHills=function()return false end,IsRiver=function()return false end,
 GetYield=function(_,kind)return kind==0 and 1 or 0 end,GetResourceType=function()return -1 end}
 end}
''')

    async def execute_read(self, code):
        self.rows=[];self.lua.execute(code);return self.rows

    execute_write=execute_read


def read(conn):
    return asyncio.run(GameState(conn).get_global_settle_scan())


def test_hidden_city_never_changes_or_breaks_known_candidates_and_no_silent_top_ten():
    conn=SettleConn();before=read(conn)
    conn.lua.execute('hidden=true')
    after=read(conn)
    assert before==after and len(after)==20


def test_native_loyalty_warning_is_distinct_from_absent_unknown_and_no_weighted_rank():
    result=read(SettleConn())
    assert result[0].loyalty_pressure==-7.5
    assert result[1].loyalty_pressure is None
    text=narrate_settle_candidates(result)
    assert 'Score' not in text and 'Top ' not in text
    assert 'unranked' in text and 'loyalty warning -7.5' in text and 'loyalty warning unknown' in text


def test_empty_local_query_does_not_hide_an_extra_global_scan_or_change_scope():
    class Conn:
        calls = 0
        async def execute_write(self, code):
            self.calls += 1
            return ['NONE']
    conn=Conn()
    result=asyncio.run(GameState(conn).get_settle_advisor(65541))
    assert conn.calls==1
    assert 'local' in result and 'map settle-global' in result


def test_stale_composite_id_cannot_read_a_reused_unit_index():
    conn=SettleConn()
    conn.lua.execute("UnitManager={GetUnit=function(owner,index)assert(owner==0 and index==5);return {GetID=function()return 65541 end,GetX=function()return 0 end,GetY=function()return 0 end}end}")
    result=asyncio.run(GameState(conn).get_settle_advisor(131077))
    assert result=='ERR:UNIT_ID_CHANGED'


def test_new_city_on_previously_revealed_but_currently_fogged_land_cannot_change_candidates():
    conn=SettleConn()
    conn.lua.execute('PlayersVisibility[0].IsRevealed=function()return true end')
    before=read(conn);conn.lua.execute('hidden=true')
    assert read(conn)==before


def test_unrevealed_neighbor_hills_cannot_change_observed_defense_geometry():
    conn=SettleConn();before=read(conn)
    conn.lua.execute('''
local original=Map.GetPlot
Map.GetPlot=function(x,y)local p=original(x,y);if p and x==20 then p.IsHills=function()return true end end;return p end
''')
    assert read(conn)==before
