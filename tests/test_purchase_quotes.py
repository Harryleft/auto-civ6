"""Quote, display and purchase through GameState with a native Lua 5.1 adapter."""
import asyncio

import pytest

from civ_mcp import narrate, server
from civ_mcp.game_state import GameState
from test_item_purchase_confirmation import PurchaseConn
from test_server import make_ctx


class QuoteConn(PurchaseConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
local warrior=GameInfo.Units.UNIT_WARRIOR
warrior.Cost=40;warrior.PurchaseYield='YIELD_GOLD'
local apostle={Hash=1003,Index=13,UnitType='UNIT_APOSTLE',Cost=200,MustPurchase=1,PurchaseYield='YIELD_FAITH'}
GameInfo.Units.UNIT_APOSTLE=apostle
local monument=GameInfo.Buildings.BUILDING_MONUMENT;monument.Cost=60;monument.PurchaseYield='YIELD_GOLD'
function rows(values)
 return setmetatable(values,{__call=function()
  local i=0;return function() i=i+1;return values[i] end
 end})
end
GameInfo.Units=rows({warrior,apostle})
GameInfo.Units.UNIT_WARRIOR=warrior;GameInfo.Units.UNIT_APOSTLE=apostle
GameInfo.Units[11]=warrior;GameInfo.Units[13]=apostle
GameInfo.Buildings=rows({monument});GameInfo.Buildings.BUILDING_MONUMENT=monument
GameInfo.Buildings.BUILDING_TEMPLE={Index=23,BuildingType='BUILDING_TEMPLE'}
GameInfo.Unit_BuildingPrereqs=rows({{Unit='UNIT_APOSTLE',PrereqBuilding='BUILDING_TEMPLE'}})
GameInfo.Districts=rows({});GameInfo.Projects=rows({})
Locale={Lookup=function(value) return ({LOC_NEEDS_TEMPLE='需要寺庙|未建造;100%[NEWLINE]请核对'})[value] or value end}
CityCommandResults={FAILURE_REASONS='failures'}
CityOperationTypes={BUILD=8,PARAM_UNIT_TYPE='unit',PARAM_BUILDING_TYPE='building'}
purchaseOnly=false;hasTemple=true;faithWarrior=false;nativeUnknown=false;priceUnknown=false
city.GetBuildQueue=function() return {
 CanProduce=function(_,hash) return not (purchaseOnly and hash==1003) end,
 GetTurnsLeft=function() return 3 end,GetProductionCost=function() return 60 end} end
city.GetDistricts=function() return {Members=function() return pairs({}) end} end
city.GetBuildings=function() return {HasBuilding=function(_,index) if index==23 then return hasTemple end;return built[index]==true end,
 IsPillaged=function() return false end} end
city.GetGold=function() return {
 IsUnitFaithPurchaseEnabled=function() return faithWarrior end,
 IsBuildingFaithPurchaseEnabled=function() return faithWarrior end,
 GetPurchaseCost=function(_,yield,hash,formation)
 if hash==2001 then assert(formation==nil) else assert(formation==0) end
 if priceUnknown then error('price unavailable') end
 if yield==5 then return 115 end
 if hash==1003 then return 775 end
 return 100.25 end} end
Players[0].GetTrade=function() return {GetOutgoingRouteCapacity=function() return 4 end} end
CityManager.CanStartOperation=function() return true end
CityManager.CanStartCommand=function(c,command,visibility,params,failures)
 if nativeUnknown then error('native eligibility unavailable') end
 local supported=(params.unit==1003 and params.yield==5) or
    (params.unit~=1003 and (params.yield==0 or faithWarrior))
 if visibility then return supported end
 if params.unit==1003 and not hasTemple then return false,{failures={'LOC_NEEDS_TEMPLE'}} end
 return supported and allowed
end
function applyPurchase(params)
 local price=city:GetGold():GetPurchaseCost(params.yield,params.unit or params.building,params.formation)
 if params.yield==0 then gold=gold-price else faith=faith-price end
 if params.unit then newUnit(262144,params.unit==1003 and 13 or 11,10,10)
 else built[21]=true end
end
''')


def options(conn, **kwargs):
    return asyncio.run(GameState(conn).list_city_production(65537, **kwargs))


def test_religious_item_does_not_advertise_a_fictitious_gold_purchase():
    conn=QuoteConn()
    text=narrate.narrate_city_production(options(conn))
    row=next(line for line in text.splitlines() if 'UNIT_APOSTLE' in line)
    assert 'faith 115' in row
    assert '775g' not in row
    assert 'gold unavailable' in row
    assert conn.lua.eval('#submissions')==0


def test_permissive_command_cannot_enable_unsupported_currency_like_native_panel():
    conn=QuoteConn();conn.lua.execute('gold=1000;CityManager.CanStartCommand=function() return true end')
    for name,index in [('UNIT_APOSTLE',0),('UNIT_WARRIOR',1)]:
        quote=options(conn,item_type='UNIT',item_name=name)[0].purchases[index]
        assert quote.can_purchase is False and quote.cost is None
        assert 'CURRENCY_NOT_SUPPORTED' in quote.reasons
    result=asyncio.run(GameState(conn).purchase_item(65537,'UNIT','UNIT_APOSTLE','YIELD_GOLD'))
    assert 'CURRENCY_NOT_SUPPORTED' in result
    assert conn.lua.eval('#submissions')==0 and conn.lua.eval('gold')==1000


def test_purchase_only_unit_survives_production_filter():
    conn=QuoteConn();conn.lua.execute('purchaseOnly=true')
    item=next(o for o in options(conn) if o.item_name=='UNIT_APOSTLE')
    assert item.can_produce is False
    assert item.purchases[1].can_purchase is True


@pytest.mark.parametrize('kind,name,currency',[
    ('UNIT','UNIT_WARRIOR','YIELD_GOLD'),('UNIT','UNIT_WARRIOR','YIELD_FAITH'),
    ('UNIT','UNIT_APOSTLE','YIELD_FAITH'),('BUILDING','BUILDING_MONUMENT','YIELD_GOLD'),
    ('BUILDING','BUILDING_MONUMENT','YIELD_FAITH'),
])
def test_quote_matches_exact_deduction_and_other_currency_is_unchanged(kind,name,currency):
    conn=QuoteConn();conn.lua.execute('faithWarrior=true')
    game=GameState(conn)
    before=(conn.lua.eval('gold'),conn.lua.eval('faith'))
    item=asyncio.run(game.list_city_production(65537,kind,name))[0]
    quote=next(q for q in item.purchases if q.currency==currency)
    assert quote.can_purchase is True
    assert conn.lua.eval('#submissions')==0
    result=asyncio.run(game.purchase_item(65537,kind,name,currency))
    assert result.startswith('PURCHASED|'),result
    index=0 if currency=='YIELD_GOLD' else 1
    after=(conn.lua.eval('gold'),conn.lua.eval('faith'))
    assert before[index]-after[index]==quote.cost
    assert before[1-index]==after[1-index]
    assert conn.lua.eval('#submissions')==1 and game.pending_action_summary is None


def test_insufficient_faith_keeps_price_balance_shortfall_and_never_submits():
    conn=QuoteConn();conn.lua.execute('faith=40.5')
    item=options(conn,item_type='UNIT',item_name='UNIT_APOSTLE')[0]
    quote=item.purchases[1]
    assert quote.cost==115 and quote.balance==40.5 and quote.can_purchase is False
    text=narrate.narrate_city_production([item])
    assert 'shortfall 74.5' in text and 'INSUFFICIENT_BALANCE' in text
    assert 'ITEM_PURCHASE_REFUSED' in asyncio.run(GameState(conn).purchase_item(65537,'UNIT','UNIT_APOSTLE','YIELD_FAITH'))
    assert conn.lua.eval('#submissions')==0 and conn.lua.eval('faith')==40.5


def test_native_reason_survives_delimiters_localization_and_read_only_mcp():
    conn=QuoteConn();conn.lua.execute('hasTemple=false')
    game=GameState(conn)
    result=asyncio.run(server.get_city_production(make_ctx(game=game),65537,'UNIT','UNIT_APOSTLE'))
    assert 'LOC_NEEDS_TEMPLE' in result and '需要寺庙|未建造;100%' in result
    assert 'PREREQUISITE_BUILDINGS_ABSENT: BUILDING_TEMPLE' in result
    assert 'cannot buy' in result and conn.lua.eval('#submissions')==0


@pytest.mark.parametrize('setup',[
    'nativeUnknown=true', 'priceUnknown=true',
    'Players[0].GetReligion=function() return {} end',
])
def test_unavailable_native_facts_never_become_purchase_permission(setup):
    conn=QuoteConn();conn.lua.execute(setup)
    item=options(conn,item_type='UNIT',item_name='UNIT_APOSTLE')[0]
    assert item.purchases[1].can_purchase is None
    assert 'eligibility unknown' in narrate.narrate_city_production([item])
    assert conn.lua.eval('#submissions')==0


def test_query_does_not_retire_pending_and_execution_refreshes_changed_price():
    conn=QuoteConn();game=GameState(conn)
    game._pending_save=True
    assert asyncio.run(game.list_city_production(65537,'UNIT','UNIT_WARRIOR'))[0].purchases[0].cost==100.25
    assert game._pending_save is True and conn.lua.eval('#submissions')==0
    game._pending_save=False
    conn.lua.execute('city.GetGold=function() return {GetPurchaseCost=function() return 125.25 end} end')
    result=asyncio.run(game.purchase_item(65537,'UNIT','UNIT_WARRIOR'))
    assert 'cost:125.25' in result and conn.lua.eval('gold')==475.25


@pytest.mark.parametrize('kwargs',[{'item_type':'UNIT'}, {'item_name':'UNIT_APOSTLE'},
    {'item_type':'PROJECT','item_name':'P'}, {'item_type':'UNIT','item_name':'bad";lua()'}])
def test_invalid_target_query_is_rejected_before_io(kwargs):
    class NoIO:
        async def execute_write(self,*args):pytest.fail('Invalid query reached game')
    with pytest.raises(ValueError):asyncio.run(GameState(NoIO()).list_city_production(65537,**kwargs))


def test_targeted_lookup_reports_existing_building_and_missing_item_explicitly():
    conn=QuoteConn();conn.lua.execute('built[21]=true')
    item=options(conn,item_type='BUILDING',item_name='BUILDING_MONUMENT')[0]
    assert all(q.can_purchase is False and 'BUILDING_ALREADY_PRESENT' in q.reasons for q in item.purchases)
    with pytest.raises(ValueError,match='ITEM_UNAVAILABLE'):
        options(conn,item_type='UNIT',item_name='UNIT_UNKNOWN')


def test_named_quote_parameters_validate_against_real_schema():
    from civ_mcp.devtools.commands import parse_command
    from jsonschema import Draft202012Validator
    intent=parse_command('city production 65537 --item-type UNIT --item UNIT_APOSTLE')
    assert intent.arguments=={'city_id':65537,'item_type':'UNIT','item_name':'UNIT_APOSTLE'}
    Draft202012Validator(server.mcp._tool_manager.get_tool(intent.tool).parameters).validate(intent.arguments)


def test_zero_price_and_negative_balance_are_observed_without_rounding():
    conn=QuoteConn();conn.lua.execute('city.GetGold=function() return {GetPurchaseCost=function() return 0 end} end')
    quote=options(conn,item_type='UNIT',item_name='UNIT_WARRIOR')[0].purchases[0]
    assert quote.cost==0 and quote.can_purchase is True
    assert 'gold 0 [can buy' in narrate.narrate_city_production(options(conn))
    conn.lua.execute('gold=-3.5')
    quote=options(conn,item_type='UNIT',item_name='UNIT_WARRIOR')[0].purchases[0]
    assert quote.balance==-3.5 and quote.can_purchase is False


def test_trader_capacity_is_shared_with_pre_submit_and_never_spends():
    conn=QuoteConn();conn.lua.execute('''
 local trader=GameInfo.Units.UNIT_WARRIOR
 trader.UnitType='UNIT_TRADER';GameInfo.Units.UNIT_TRADER=trader
 Players[0].GetTrade=function() return {GetOutgoingRouteCapacity=function() return 1 end} end
 ''')
    item=options(conn,item_type='UNIT',item_name='UNIT_TRADER')[0]
    assert item.can_produce is False
    assert all(q.can_purchase is False for q in item.purchases)
    assert 'TRADER_CAPACITY_REACHED (1/1)' in item.purchases[0].reasons
    assert 'TRADER_CAPACITY_REACHED' in asyncio.run(GameState(conn).purchase_item(65537,'UNIT','UNIT_TRADER'))
    assert conn.lua.eval('#submissions')==0 and conn.lua.eval('gold')==600.5


def test_legacy_price_does_not_claim_eligibility_and_repair_remains_distinct():
    from civ_mcp.lua.cities import parse_city_production_response
    data=parse_city_production_response(['UNIT|UNIT_WARRIOR|40|2|100',
                                        'BUILDING|BUILDING_MONUMENT|0|1|-1|REPAIR'])
    text=narrate.narrate_city_production(data)
    assert 'legacy price only' in text and 'eligibility unknown' in text
    assert '[REPAIR]' in text and data[1].purchases==[]


def test_large_or_fractional_quote_is_not_rounded_for_display():
    conn=QuoteConn();conn.lua.execute('city.GetGold=function() return {GetPurchaseCost=function() return 12345.25 end} end')
    text=narrate.narrate_city_production(options(conn,item_type='UNIT',item_name='UNIT_WARRIOR'))
    assert 'gold 12345.25' in text and 'balance 600.5' in text


def test_failed_default_candidate_scan_is_not_an_empty_available_list():
    conn=QuoteConn();conn.lua.execute('''
 nativeUnknown=true
 city.GetBuildQueue=function() return {CanProduce=function() error('read failed') end} end
 ''')
    with pytest.raises(ValueError,match='PRODUCTION_OPTIONS_INCOMPLETE'):options(conn)
    # A targeted read can still report independently known purchase facts.
    conn.lua.execute('nativeUnknown=false')
    item=options(conn,item_type='UNIT',item_name='UNIT_WARRIOR')[0]
    assert item.can_produce is None and item.purchases[0].can_purchase is True


def test_visible_disabled_production_candidate_is_not_silently_dropped():
    conn=QuoteConn();conn.lua.execute('''
 GameInfo.Units.UNIT_WARRIOR.PurchaseYield=nil
 CityManager.CanStartOperation=function() return false end
 ''')
    item=next(o for o in options(conn) if o.item_name=='UNIT_WARRIOR')
    assert item.can_produce is False and all(q.can_purchase is False for q in item.purchases)
