"""Trade candidate queries through GameState and the native Lua boundary."""
import asyncio

import pytest
from lupa.lua51 import LuaRuntime

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_trade_destinations


class TradeConn:
    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.execute('''
me=0; moves=2; awake=1; activity=1; outgoing=0; capacity=1; writes=0
met={[1]=true}; revealed={['20,20']=true,['21,20']=true}
allowed={['20,20']=true,['21,20']=true,['40,40']=true,['60,60']=true}
paths={['20,20']={1,2},['21,20']={1,3},['40,40']={1,4},['60,60']={1,5}}
checks={}; enriched={}
function city(owner,id,name,x,y)
 return {GetOwner=function() return owner end,GetID=function() return id end,
 GetName=function() enriched[name]=true; return name end,
 GetX=function() return x end,GetY=function() return y end,
 GetTrade=function() return {HasActiveTradingPost=function() return false end} end,
 GetReligion=function() return {GetMajorityReligion=function() return -1 end} end}
end
origin=city(0,10,'Uruk',10,10)
known=city(1,20,'Known',20,20); unreachable=city(1,21,'Unreachable',21,20)
unseen=city(1,40,'Unseen',40,40); unmet=city(2,60,'Unmet',60,60)
cities={[0]={origin},[1]={known,unreachable,unseen},[2]={unmet}}
function player(id)
 return {IsAlive=function() return cities[id]~=nil end,
 GetCities=function() return {Members=function() return pairs(cities[id] or {}) end} end,
 GetDiplomacy=function() return {HasMet=function(_,other) return met[other] or false end,IsAtWarWith=function() return false end} end,
 GetTrade=function() return {GetNumOutgoingRoutes=function() return outgoing end,GetOutgoingRouteCapacity=function() return capacity end} end,
 GetInfluence=function() return {CanReceiveInfluence=function() return false end} end}
end
Players=setmetatable({},{__index=function(_,id) return player(id) end})
PlayersVisibility={[0]={IsRevealed=function(_,x,y) return revealed[x..','..y] or false end}}
PlayerConfigurations=setmetatable({},{__index=function(_,id) return {GetCivilizationShortDescription=function() return 'Civ'..id end} end})
unit={GetX=function() return 10 end,GetY=function() return 10 end,GetOwner=function() return 0 end,
 GetType=function() return 1 end,GetMovesRemaining=function() return moves end}
ActivityTypes={ACTIVITY_AWAKE=awake}
UnitOperationTypes={PARAM_X0='dx',PARAM_Y0='dy',PARAM_X1='ox',PARAM_Y1='oy'}
UnitManager={GetUnit=function() return unit end,GetActivityType=function() return activity end,
 CanStartOperation=function(_,_,_,p,visibilityOnly)
   local key=p.dx..','..p.dy; checks[key]=true
   return moves>0 and activity==awake and outgoing<capacity and allowed[key] or false
 end,
 RequestOperation=function() writes=writes+1; error('query wrote native state') end}
tradeManager={GetTradeRoutePath=function(_,_,_,owner,id)
 for _,c in pairs(cities[owner]) do if c:GetID()==id then return paths[c:GetX()..','..c:GetY()] end end end}
Game={GetLocalPlayer=function() return 0 end,GetTradeManager=function() return tradeManager end,
 GetQuestsManager=function() return {} end}
GameInfo={Units={[1]={MakeTradeRoute=true}},UnitOperations={UNITOPERATION_MAKE_TRADE_ROUTE={Hash=100}},Quests={}}
CityManager={GetCityAt=function() return origin end}
Locale={Lookup=function(s) return s end}
''')

    async def execute_write(self, code):
        self.rows.clear()
        self.lua.execute(code)
        return [row for row in self.rows if row != '---END---']

    async def execute_read(self, code):
        raise AssertionError('Trade route candidates use the native InGame context')

    def query(self):
        return asyncio.run(GameState(self).get_trade_destinations(5))


def test_trader_without_moves_returns_unavailable_instead_of_world_city_fallback():
    conn = TradeConn()
    conn.lua.execute('moves=0')
    with pytest.raises(ValueError, match='TRADE_DESTINATIONS_UNAVAILABLE.*not ready'):
        conn.query()
    assert conn.lua.eval('next(enriched)') is None
    assert conn.lua.eval('writes') == 0


def test_only_met_revealed_native_legal_reachable_cities_are_enriched():
    conn = TradeConn()
    conn.lua.execute("paths['21,20']={}")
    destinations = conn.query()
    assert [d.city_name for d in destinations] == ['Known']
    assert (destinations[0].x, destinations[0].y) == (20, 20)
    assert conn.lua.eval("checks['40,40']") is None
    assert conn.lua.eval("checks['60,60']") is None
    assert conn.lua.eval('enriched.Unseen') is None
    assert conn.lua.eval('enriched.Unmet') is None
    assert conn.lua.eval('enriched.Unreachable') is None
    assert conn.lua.eval('moves') == 2
    assert conn.lua.eval('writes') == 0


@pytest.mark.parametrize('native_state, reason', [
    ('activity=2', 'not ready'),
    ('outgoing=1', 'capacity'),
    ('CityManager.GetCityAt=function() return nil end', 'own city'),
    ('GameInfo.Units[1].MakeTradeRoute=false', 'not a trader'),
])
def test_unavailable_trader_explains_native_precondition_without_candidates(native_state, reason):
    conn = TradeConn()
    conn.lua.execute(native_state)
    with pytest.raises(ValueError, match='TRADE_DESTINATIONS_UNAVAILABLE.*' + reason):
        conn.query()
    assert conn.lua.eval('next(enriched)') is None
    assert conn.lua.eval('next(checks)') is None
    assert conn.lua.eval('writes') == 0


@pytest.mark.parametrize('native_failure', [
    'PlayersVisibility=nil',
    'PlayersVisibility[0].IsRevealed=function() return nil end',
    'PlayersVisibility[0].IsRevealed=function() error("native read failed") end',
    'Players[0]=player(0); Players[0].GetDiplomacy=nil',
    'met[1]=nil; Players[0]=player(0); Players[0].GetDiplomacy=function() return {HasMet=function() return nil end} end',
    'UnitManager.CanStartOperation=function() error("native eligibility failed") end',
    'UnitManager.CanStartOperation=function() return nil end',
    'tradeManager.GetTradeRoutePath=function() error("native path failed") end',
    'tradeManager.GetTradeRoutePath=function() return "invalid" end',
    'UnitManager.GetActivityType=function() return nil end',
    'GameInfo.UnitOperations.UNITOPERATION_MAKE_TRADE_ROUTE=nil',
])
def test_unreadable_native_candidate_facts_remain_unknown_not_empty(native_failure):
    conn = TradeConn()
    conn.lua.execute(native_failure)
    with pytest.raises(ValueError, match='TRADE_DESTINATIONS_UNKNOWN'):
        conn.query()
    assert conn.lua.eval('writes') == 0


def test_all_known_destinations_rejected_is_confirmed_empty_not_unknown():
    conn = TradeConn()
    conn.lua.execute('allowed={}')
    result = conn.query()
    assert result == []
    assert 'native checks completed' in narrate_trade_destinations(result)
    assert conn.lua.eval('next(enriched)') is None
    assert conn.lua.eval('writes') == 0


@pytest.mark.parametrize('response', [
    [], ['WARN:CANNOT_START|All rejected'], ['TDEST_STATUS|AVAILABLE|'],
    ['TDEST_STATUS|EMPTY|', 'TDEST|Known|Civ1|20,20|0'],
    ['TDEST_STATUS|AVAILABLE|', 'TDEST|Known|Civ1|bad coordinates|0'],
    ['TDEST_STATUS|EMPTY|', 'TDEST_STATUS|AVAILABLE|'],
])
def test_incomplete_or_conflicting_query_receipt_is_not_a_legal_empty_list(response):
    from test_game_state import FakeConn

    with pytest.raises(ValueError, match='TRADE_DESTINATIONS_UNKNOWN'):
        asyncio.run(GameState(FakeConn([('write', response)])).get_trade_destinations(5))


def test_legacy_nonempty_rows_keep_the_list_contract():
    from test_game_state import FakeConn

    result = asyncio.run(GameState(FakeConn([('write', [
        'TDEST|Known|Civ1|20,20|0',
    ])])).get_trade_destinations(5))
    assert isinstance(result, list)
    assert result[0].city_name == 'Known'


def test_native_operation_rejection_is_not_replaced_by_a_yield_preview():
    conn = TradeConn()
    conn.lua.execute("allowed['20,20']=false; allowed['21,20']=false")
    assert conn.query() == []
    assert conn.lua.eval('next(enriched)') is None
    assert conn.lua.eval('writes') == 0


@pytest.mark.parametrize('native_state, expected', [
    ('moves=0', 'Error: TRADE_DESTINATIONS_UNAVAILABLE|'),
    ('tradeManager.GetTradeRoutePath=nil', 'Error: TRADE_DESTINATIONS_UNKNOWN|'),
    ('allowed={}', 'No legal trade route destinations'),
])
def test_public_tool_keeps_unavailable_unknown_and_empty_distinct(native_state, expected):
    from civ_mcp import server
    from test_server import make_ctx

    conn = TradeConn()
    conn.lua.execute(native_state)
    text = asyncio.run(server.get_trade_destinations(make_ctx(game=GameState(conn)), 65541))
    assert text.startswith(expected)
    assert 'Unmet' not in text and 'Unseen' not in text
    assert conn.lua.eval('writes') == 0


def test_known_domestic_destination_does_not_require_contact_with_self():
    conn = TradeConn()
    conn.lua.execute('''
local kish=city(0,11,'Kish',12,10); table.insert(cities[0],kish)
revealed['12,10']=true; allowed['12,10']=true; paths['12,10']={1,6}
met={}
''')
    result = conn.query()
    assert [d.city_name for d in result] == ['Kish']
    assert result[0].is_domestic is True
    assert conn.lua.eval('writes') == 0
