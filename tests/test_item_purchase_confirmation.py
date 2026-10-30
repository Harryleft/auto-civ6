"""Real MCP/GameState and generated Lua; native purchase/transport adapter only."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx, app_of
from test_unit_readiness import ReadyConn


class PurchaseConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply = False
        self.lua.execute('''
gold,faith,cost,allowed=600.5,500.5,100.25,true
built={};units={}
GameInfo.Units={UNIT_WARRIOR={Hash=1001,Index=11,UnitType='UNIT_WARRIOR',PurchaseYield='YIELD_GOLD'},
 UNIT_ARCHER={Hash=1002,Index=12,UnitType='UNIT_ARCHER',PurchaseYield='YIELD_GOLD'}}
GameInfo.Buildings={BUILDING_MONUMENT={Hash=2001,Index=21,BuildingType='BUILDING_MONUMENT',PurchaseYield='YIELD_GOLD'}}
GameInfo.Yields={YIELD_GOLD={Index=0},YIELD_FAITH={Index=5}}
MilitaryFormationTypes={STANDARD_MILITARY_FORMATION=0}
CityCommandTypes={PURCHASE=7,PARAM_UNIT_TYPE='unit',PARAM_BUILDING_TYPE='building',
 PARAM_YIELD_TYPE='yield',PARAM_MILITARY_FORMATION_TYPE='formation'}
city={id=65537,owner=0,x=10,y=10}
city.GetID=function(self) return self.id end
city.GetOwner=function(self) return self.owner end
city.GetX=function(self) return self.x end
city.GetY=function(self) return self.y end
city.GetGold=function() return {
 IsUnitFaithPurchaseEnabled=function() return true end,
 IsBuildingFaithPurchaseEnabled=function() return true end,
 GetPurchaseCost=function(_,yield,hash,formation)
 assert(yield==0 or yield==5);assert(hash==1001 or hash==1002 or hash==2001)
 if hash==2001 then assert(formation==nil) else assert(formation==0) end
 return cost end} end
city.GetBuildings=function() return {HasBuilding=function(_,index) return built[index]==true end} end
Players[0].GetTreasury=function() return {GetGoldBalance=function() return gold end} end
Players[0].GetReligion=function() return {GetFaithBalance=function() return faith end} end
Players[0].GetUnits=function() return {Members=function() return pairs(units) end} end
function newUnit(id,kind,x,y)
 local u={id=id,kind=kind,x=x,y=y,owner=0}
 u.GetID=function(self) return self.id end
 u.GetType=function(self) return self.kind end
 u.GetX=function(self) return self.x end
 u.GetY=function(self) return self.y end
 u.GetOwner=function(self) return self.owner end
 units[id]=u;return u
end
newUnit(196608,11,20,20)
Map={GetPlot=function(x,y) return {x=x,y=y} end,
 GetUnitsAt=function() return {GetCount=function() return 0 end} end}
Cities={GetPlotPurchaseCity=function(plot) if plot.x<15 then return city end end}
function applyPurchase(params)
 if params.yield==0 then gold=gold-cost else faith=faith-cost end
 if params.unit then newUnit(262144,params.unit==1001 and 11 or 12,10,10)
 else built[21]=true end
end
CityManager={GetCity=function(owner,id) if id==city.id%65536 then return city end end,
 CanStartCommand=function(c,command,visibility,params,failures)
  assert(c==city and command==7 and visibility==false and failures==true)
  return allowed
 end,
 RequestCommand=function(c,command,params)
  assert(c==city and command==7);table.insert(submissions,params)
  if not deferred then applyPurchase(params) end
  if fail_request then error('purchase entered then native failure') end
 end}
''')

    async def execute_write(self, code):
        before = self.lua.eval('#submissions')
        rows = await super().execute_write(code)
        if self.lose_reply and self.lua.eval('#submissions') > before:
            self.lose_reply = False
            raise OSError('purchase executed; response lost')
        return rows


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


def test_lost_unit_purchase_reply_only_confirms_on_repeated_mcp_call():
    conn=PurchaseConn(); state=GameState(conn); ctx=make_ctx(); app_of(ctx).game=state
    conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.purchase_item(ctx,65537,'UNIT','UNIT_WARRIOR'))
    assert state.pending_action_kind=='purchase_item'
    result=asyncio.run(server.purchase_item(ctx,65537,'UNIT','UNIT_WARRIOR'))
    assert result.startswith('PURCHASED|UNIT_WARRIOR|')
    assert 'unit:262144' in result
    assert conn.lua.eval('gold')==500.25
    assert conn.lua.eval('#submissions')==1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('kind,name', [('UNIT','UNIT_WARRIOR'),('BUILDING','BUILDING_MONUMENT')])
@pytest.mark.parametrize('currency,balance', [('YIELD_GOLD','gold'),('YIELD_FAITH','faith')])
def test_normal_purchase_confirms_product_and_requested_currency(kind,name,currency,balance):
    conn=PurchaseConn(); state=GameState(conn)
    result=asyncio.run(state.purchase_item(65537,kind,name,currency))
    assert result.startswith(f'PURCHASED|{name}|city:65537|{currency}|')
    assert conn.lua.eval(balance)==(500.25 if balance=='gold' else 400.25)
    assert conn.lua.eval('faith' if balance=='gold' else 'gold')==(500.5 if balance=='gold' else 600.5)
    assert conn.lua.eval('#submissions')==1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('kind,name', [('UNIT','UNIT_WARRIOR'),('BUILDING','BUILDING_MONUMENT')])
def test_delayed_purchase_reentry_only_observes_original(kind,name):
    conn=PurchaseConn(); state=GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,kind,name))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,kind,name))
    conn.lua.execute('applyPurchase(submissions[1])')
    assert asyncio.run(state.purchase_item(65537,kind,name)).startswith(f'PURCHASED|{name}|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('effect', [
    '', 'gold=500.25', 'newUnit(262144,11,10,10)',
    'gold=500.25;units[196608].x=10;units[196608].y=10',
    'gold=500.25;newUnit(262144,12,10,10)',
    'gold=500.25;newUnit(262144,11,20,20)',
    'gold=500.25;newUnit(262144,11,10,10).owner=1',
    'applyPurchase(submissions[1]);gold=500',
])
def test_old_wrong_or_partial_unit_product_cannot_confirm_purchase(effect):
    conn=PurchaseConn(); state=GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR'))
    conn.lua.execute(effect)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR'))
    assert state.pending_action_kind=='purchase_item'
    assert conn.lua.eval('#submissions')==1


def test_building_and_faith_changes_can_arrive_separately():
    conn=PurchaseConn(); state=GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,'BUILDING','BUILDING_MONUMENT','YIELD_FAITH'))
    conn.lua.execute('built[21]=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,'BUILDING','BUILDING_MONUMENT','YIELD_FAITH'))
    conn.lua.execute('faith=400.25')
    assert 'building:21' in asyncio.run(state.purchase_item(65537,'BUILDING','BUILDING_MONUMENT','YIELD_FAITH'))
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('setup', [
    'allowed=false', 'allowed=nil', 'gold=1', 'gold=nil', 'cost=-1', 'cost=nil',
    'city.id=131073', 'city.owner=1', 'busy=true', 'GameInfo.Units.UNIT_WARRIOR=nil',
    'CityManager.RequestCommand=nil', 'CityManager.CanStartCommand=nil',
])
def test_native_rejection_is_pre_entry_and_retires_pending(setup):
    conn=PurchaseConn(); state=GameState(conn); conn.lua.execute(setup)
    assert asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR')).startswith('Error: ITEM_PURCHASE_REFUSED|')
    assert conn.lua.eval('#submissions')==0
    assert state.pending_action_summary is None


def test_existing_building_is_not_a_new_purchase_and_never_spends():
    conn=PurchaseConn(); state=GameState(conn); conn.lua.execute('built[21]=true')
    assert 'ITEM_PURCHASE_REFUSED' in asyncio.run(state.purchase_item(65537,'BUILDING','BUILDING_MONUMENT'))
    assert conn.lua.eval('#submissions')==0
    assert conn.lua.eval('gold')==600.5
    assert state.pending_action_summary is None


@pytest.mark.parametrize('args', [
    (65537,'DISTRICT','DISTRICT_CAMPUS','YIELD_GOLD'),
    (65537,'UNIT','UNIT_WARRIOR','YIELD_CULTURE'),
    (True,'UNIT','UNIT_WARRIOR','YIELD_GOLD'),
    (65537,'UNIT','UNIT_WARRIOR\";error(1)','YIELD_GOLD'),
])
def test_invalid_intent_never_submits(args):
    conn=PurchaseConn(); state=GameState(conn)
    assert 'INVALID_PURCHASE' in asyncio.run(state.purchase_item(*args))
    assert conn.lua.eval('#submissions')==0


@pytest.mark.parametrize('args', [
    (65537,'UNIT','UNIT_WARRIOR','YIELD_FAITH'),
    (65537,'UNIT','UNIT_ARCHER','YIELD_GOLD'),
    (131073,'UNIT','UNIT_WARRIOR','YIELD_GOLD'),
    (65537,'BUILDING','BUILDING_MONUMENT','YIELD_GOLD'),
])
def test_different_intent_waits_without_replacing_original(args):
    conn=PurchaseConn(); state=GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(*args))
    conn.lua.execute('applyPurchase(submissions[1])')
    assert asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR')).startswith('PURCHASED|UNIT_WARRIOR|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('kind,name', [('UNIT','UNIT_WARRIOR'),('BUILDING','BUILDING_MONUMENT')])
def test_cancellation_after_native_purchase_preserves_reentry(kind,name):
    class CancelConn(PurchaseConn):
        async def execute_write(self,code):
            before=self.lua.eval('#submissions')
            rows=await super().execute_write(code)
            if self.lua.eval('#submissions')>before:
                raise asyncio.CancelledError()
            return rows
    conn=CancelConn(); state=GameState(conn)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(state.purchase_item(65537,kind,name))
    assert state.pending_action_kind=='purchase_item'
    assert asyncio.run(state.purchase_item(65537,kind,name)).startswith(f'PURCHASED|{name}|')
    assert conn.lua.eval('#submissions')==1


@pytest.mark.parametrize('change', [
    'seed=99', 'turn=42', 'city.id=131073', 'city.x=20',
    '__civmcpItemPurchase=nil', '__civmcpItemPurchase.token="other"',
    '__civmcpItemPurchase.returned=false', 'GameInfo.Units.UNIT_WARRIOR.Hash=999',
])
def test_original_scope_receipt_and_product_identity_are_required(change):
    conn=PurchaseConn(); state=GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR'))
    conn.lua.execute('applyPurchase(submissions[1]);'+change)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR'))
    assert conn.lua.eval('#submissions')==1


def test_pending_purchase_blocks_other_mcp_and_direct_consumption():
    conn=PurchaseConn(); state=GameState(conn); ctx=make_ctx(); app_of(ctx).game=state
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.purchase_item(ctx,65537,'UNIT','UNIT_WARRIOR'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.purchase_tile(ctx,65537,11,10))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.send_envoy(3))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.set_research('TECH_POTTERY'))
    assert conn.lua.eval('#submissions')==1
    assert asyncio.run(state.get_game_identity())==('sumeria',719665280)


def test_native_throw_after_entry_does_not_retire_or_resubmit_purchase():
    conn=PurchaseConn(); state=GameState(conn); conn.lua.execute('fail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR'))
    assert conn.lua.eval('#submissions')==1


def test_purchase_unit_spawned_on_city_district_can_be_confirmed():
    conn=PurchaseConn(); state=GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR'))
    conn.lua.execute('applyPurchase(submissions[1]);units[262144].x=12')
    assert 'unit:262144' in asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR'))
    assert conn.lua.eval('#submissions')==1


def test_normalized_same_intent_does_not_spend_twice_after_lost_reply():
    conn=PurchaseConn(); state=GameState(conn); conn.lose_reply=True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,'unit','unit_warrior','yield_gold'))
    assert asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR')).startswith('PURCHASED|')
    assert conn.lua.eval('#submissions')==1


def test_native_extra_units_keep_exact_new_ids_without_repeating_payment():
    conn=PurchaseConn(); state=GameState(conn); conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR'))
    conn.lua.execute('applyPurchase(submissions[1]);newUnit(262145,11,12,10)')
    result=asyncio.run(state.purchase_item(65537,'UNIT','UNIT_WARRIOR'))
    assert 'unit:262144,262145' in result
    assert conn.lua.eval('#submissions')==1
    assert conn.lua.eval('gold')==500.25
