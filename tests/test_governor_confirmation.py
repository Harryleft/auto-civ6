"""Governor operations through MCP/GameState, real Lua and native-only adapters."""
import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from test_unit_readiness import ReadyConn

GOV = 'GOVERNOR_THE_EDUCATOR'
PROMO = 'GOVERNOR_EDUCATOR_LIBRARIAN'


class GovernorConn(ReadyConn):
    def __init__(self):
        super().__init__()
        self.lose_reply = False
        self.cancel_reply = False
        self.lua.execute('''
points,spent,eligible=4,0,true
governors,cities={},{}
function database(list,key)
 local lookup={}
 for _,row in ipairs(list) do lookup[row[key]]=row;lookup[row.Index]=row;lookup[row.Hash]=row end
 return setmetatable(lookup,{__call=function()
  local i=0;return function() i=i+1;return list[i] end
 end})
end
GameInfo.Governors=database({
 {GovernorType='GOVERNOR_THE_EDUCATOR',Index=1,Hash=101,Name='Pingala',Title='Educator',TransitionStrength=1},
 {GovernorType='GOVERNOR_THE_DEFENDER',Index=2,Hash=102,Name='Victor',Title='Defender',TransitionStrength=1},
},'GovernorType')
GameInfo.GovernorPromotions=database({
 {GovernorPromotionType='GOVERNOR_EDUCATOR_LIBRARIAN',Index=11,Hash=211,Name='Librarian',Description='Science',Level=0},
 {GovernorPromotionType='GOVERNOR_EDUCATOR_RESEARCHER',Index=12,Hash=212,Name='Researcher',Description='More science',Level=1},
},'GovernorPromotionType')
GameInfo.GovernorPromotionSets=function()
 local i=10
 return function() i=i+1;if i<=12 then return {GovernorType='GOVERNOR_THE_EDUCATOR',GovernorPromotion=GameInfo.GovernorPromotions[i].GovernorPromotionType} end end
end
Locale={Lookup=function(value) return value end}
for _,id in ipairs({65537,65538}) do
 local city={id=id,owner=0,x=id%65536,y=8}
 city.GetID=function(self) return self.id end
 city.GetOwner=function(self) return self.owner end
 city.GetX=function(self) return self.x end
 city.GetY=function(self) return self.y end
 city.GetName=function() return 'Uruk' end
 cities[id]=city
end
CityManager={GetCity=function(owner,id) for _,city in pairs(cities) do if city.id%65536==id then return city end end end}
function addGovernor(hash)
 local g={promotions={},assigned=nil}
 g.HasPromotion=function(self,promo) assert(promo==211 or promo==212,'HasPromotion needs Hash');return self.promotions[promo]==true end
 g.GetAssignedCity=function(self) return self.assigned end
 g.IsEstablished=function() return false end
 g.GetTurnsToEstablish=function() return 5 end
 governors[hash]=g;return g
end
local pg={
 GetGovernorPoints=function() return points end,
 GetGovernorPointsSpent=function() return spent end,
 HasGovernor=function(_,hash) return governors[hash]~=nil end,
 GetGovernor=function(_,hash) return governors[hash] end,
 CanAppoint=function() return eligible and points>spent end,
 CanEverAppointGovernor=function(_,hash) return eligible and (hash==101 or hash==102) end,
 CanEarnPromotion=function(_,hash,promo) assert(hash==101 and (promo==211 or promo==212));return eligible and points>spent end,
 CanAssignGovernor=function(_,hash,city,check) assert(check==true and city.owner==0);return eligible end,
}
Players[0].GetGovernors=function() return pg end
PlayerOperations={APPOINT_GOVERNOR=1,PROMOTE_GOVERNOR=2,ASSIGN_GOVERNOR=3,
 PARAM_GOVERNOR_TYPE='governor',PARAM_GOVERNOR_PROMOTION_TYPE='promotion',PARAM_CITY_DEST='city',PARAM_PLAYER_ONE='owner'}
function applyGovernor(req)
 local hash=GameInfo.Governors[req.governor].Hash
 if req.op==1 then addGovernor(hash);spent=spent+1
 elseif req.op==2 then governors[hash].promotions[GameInfo.GovernorPromotions[req.promotion].Hash]=true;spent=spent+1
 else governors[hash].assigned=cities[req.city] end
end
UI.RequestPlayerOperation=function(owner,op,params)
 assert(owner==0 and (params.governor==1 or params.governor==2),'request needs Index')
 params.op=op;table.insert(submissions,params)
 if not deferred then applyGovernor(params) end
 if fail_request then error('native governor operation failed after entry') end
end
''')

    async def execute_write(self, code):
        before = self.lua.eval('#submissions')
        rows = await super().execute_write(code)
        if self.lua.eval('#submissions') > before:
            if self.cancel_reply:
                raise asyncio.CancelledError()
            if self.lose_reply:
                self.lose_reply = False
                raise OSError('native governor operation ran; response lost')
        return rows


@pytest.fixture(autouse=True)
def quick_confirmation(monkeypatch):
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT', .01)
    monkeypatch.setattr('civ_mcp.game_state.ACTION_CONFIRM_INTERVAL', .001)


@pytest.mark.parametrize('method,args,setup,prefix,spent', [
    ('appoint_governor', (GOV,), '', 'APPOINTED|', 1),
    ('promote_governor', (GOV, PROMO), 'addGovernor(101)', 'PROMOTED|', 1),
    ('assign_governor', (GOV, 65537), 'addGovernor(101)', 'ASSIGNED|', 0),
])
def test_lost_governor_reply_only_confirms_the_original_request(method,args,setup,prefix,spent):
    conn = GovernorConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute(setup)
    conn.lose_reply = True
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(server,method)(ctx,*args))
    assert asyncio.run(getattr(server,method)(ctx,*args)).startswith(prefix)
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('spent') == spent
    assert state.pending_action_summary is None


def test_delayed_promotion_uses_hash_for_eligibility_and_index_for_submission():
    conn = GovernorConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('addGovernor(101); deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.promote_governor(ctx, GOV, PROMO))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.promote_governor(ctx, GOV, PROMO))
    assert conn.lua.eval('#submissions') == 1
    conn.lua.execute('applyGovernor(submissions[1])')
    assert asyncio.run(server.promote_governor(ctx, GOV, PROMO)).startswith('PROMOTED|')
    assert conn.lua.eval('governors[101].promotions[211]') is True
    assert conn.lua.eval('spent') == 1
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


def test_delayed_assignment_confirms_the_city_without_waiting_for_establishment():
    conn = GovernorConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('addGovernor(101); deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.assign_governor(ctx, GOV, 65537))
    conn.lua.execute('applyGovernor(submissions[1])')
    result = asyncio.run(server.assign_governor(ctx, GOV, 65537))
    assert result.startswith('ASSIGNED|'), result
    assert 'city:65537' in result and 'established:false' in result
    assert conn.lua.eval('#submissions') == 1
    assert conn.lua.eval('spent') == 0
    assert state.pending_action_summary is None


def test_governor_query_omits_the_promotion_that_was_actually_earned():
    conn = GovernorConn(); state = GameState(conn)
    conn.lua.execute('addGovernor(101)')
    assert asyncio.run(state.promote_governor(GOV, PROMO)).startswith('PROMOTED|')
    status = asyncio.run(state.get_governors())
    assert status.points_spent == 1
    assert [promotion.promotion_type for promotion in status.appointed[0].available_promotions] == ['GOVERNOR_EDUCATOR_RESEARCHER']


@pytest.mark.parametrize('method,args,setup', [
    ('appoint_governor', (GOV,), 'addGovernor(101)'),
    ('promote_governor', (GOV, PROMO), 'addGovernor(101).promotions[211]=true'),
    ('assign_governor', (GOV, 65537), 'addGovernor(101).assigned=cities[65537]'),
])
def test_satisfied_governor_intent_is_noop_even_without_titles_or_eligibility(method,args,setup):
    conn = GovernorConn(); state = GameState(conn)
    conn.lua.execute(setup+'; points=0; eligible=false')
    result = asyncio.run(getattr(state,method)(*args))
    assert result.startswith('GOVERNOR_UNCHANGED|'), result
    assert conn.lua.eval('#submissions') == conn.lua.eval('spent') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('method,args,setup', [
    ('appoint_governor', (GOV,), ''),
    ('promote_governor', (GOV, PROMO), 'addGovernor(101)'),
    ('assign_governor', (GOV, 65537), 'addGovernor(101)'),
])
def test_native_eligibility_refuses_before_submission(method,args,setup):
    conn = GovernorConn(); state = GameState(conn)
    conn.lua.execute(setup+'\neligible=false')
    result = asyncio.run(getattr(state,method)(*args))
    assert result.startswith('Error: GOVERNOR_REFUSED|'), result
    assert conn.lua.eval('#submissions') == conn.lua.eval('spent') == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize('method,args,setup', [
    ('appoint_governor', (GOV,), ''),
    ('promote_governor', (GOV, PROMO), 'addGovernor(101)'),
    ('assign_governor', (GOV, 65537), 'addGovernor(101)'),
])
def test_cancelled_governor_request_only_confirms_original_on_reentry(method,args,setup):
    conn = GovernorConn(); state = GameState(conn)
    conn.lua.execute(setup)
    conn.cancel_reply = True
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(getattr(state,method)(*args))
    assert state.pending_action_kind == method
    assert 'OUTCOME_UNKNOWN' not in asyncio.run(getattr(state,method)(*args))
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


@pytest.mark.parametrize('method,args,setup', [
    ('appoint_governor', (GOV,), ''),
    ('promote_governor', (GOV, PROMO), 'addGovernor(101)'),
    ('assign_governor', (GOV, 65537), 'addGovernor(101)'),
])
def test_native_exception_keeps_governor_request_unknown_without_replay(method,args,setup):
    conn = GovernorConn(); state = GameState(conn)
    conn.lua.execute(setup+'\nfail_request=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state,method)(*args))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(getattr(state,method)(*args))
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_kind == method


@pytest.mark.parametrize('effect', [
    'spent=1',
    'addGovernor(101)',
    'spent=1; addGovernor(102)',
])
def test_appointment_requires_both_exact_governor_and_title_consumption(effect):
    conn = GovernorConn(); state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.appoint_governor(GOV))
    conn.lua.execute(effect)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.appoint_governor(GOV))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('effect', [
    'spent=1',
    'governors[101].promotions[211]=true',
    'spent=1; governors[101].promotions[212]=true',
])
def test_promotion_requires_both_exact_promotion_and_title_consumption(effect):
    conn = GovernorConn(); state = GameState(conn)
    conn.lua.execute('addGovernor(101); deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.promote_governor(GOV,PROMO))
    conn.lua.execute(effect)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.promote_governor(GOV,PROMO))
    assert conn.lua.eval('#submissions') == 1


@pytest.mark.parametrize('effect', [
    'governors[101].assigned=cities[65538]',
    'governors[101].assigned=cities[65537]; cities[65537].id=131073',
    'governors[101].assigned=cities[65537]; cities[65537].owner=1',
])
def test_assignment_requires_exact_original_city(effect):
    conn = GovernorConn(); state = GameState(conn)
    conn.lua.execute('addGovernor(101); deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.assign_governor(GOV,65537))
    conn.lua.execute(effect)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.assign_governor(GOV,65537))
    assert conn.lua.eval('#submissions') == 1


def test_negative_title_baseline_is_not_valid_consumption_evidence():
    conn = GovernorConn(); state = GameState(conn)
    conn.lua.execute('spent=-1')
    result = asyncio.run(state.appoint_governor(GOV))
    assert result.startswith('Error: GOVERNOR_REFUSED|'), result
    assert conn.lua.eval('#submissions') == 0
    assert state.pending_action_summary is None


def test_pending_governor_request_blocks_other_mutations_but_allows_state_queries():
    conn = GovernorConn(); state = GameState(conn); ctx = make_ctx(game=state)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.appoint_governor(GOV))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.appoint_governor('GOVERNOR_THE_DEFENDER'))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.assign_governor(ctx,GOV,65537))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.promote_governor(GOV,PROMO))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(server.end_turn(ctx))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.save_game('pending_governor'))
    assert asyncio.run(state.get_governors()).points_spent == 0
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_kind == 'appoint_governor'


def test_governor_scope_mismatch_stays_unknown_after_return_to_original_world():
    conn = GovernorConn(); state = GameState(conn)
    conn.lua.execute('deferred=true')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.appoint_governor(GOV))
    conn.lua.execute('applyGovernor(submissions[1]); seed=123')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.appoint_governor(GOV))
    conn.lua.execute('seed=719665280')
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.appoint_governor(GOV))
    assert conn.lua.eval('#submissions') == 1


def test_confirmed_assignment_does_not_depend_on_establishment_report_availability():
    conn = GovernorConn(); state = GameState(conn)
    conn.lua.execute('addGovernor(101).GetTurnsToEstablish=function() error("report unavailable") end')
    result = asyncio.run(state.assign_governor(GOV,65537))
    assert result.startswith('ASSIGNED|'), result
    assert 'city:65537' in result and 'established:unknown' in result
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None


def test_assignment_does_not_require_unrelated_title_accounting():
    conn = GovernorConn(); state = GameState(conn)
    conn.lua.execute('addGovernor(101); Players[0]:GetGovernors().GetGovernorPointsSpent=nil')
    assert asyncio.run(state.assign_governor(GOV,65537)).startswith('ASSIGNED|')
    assert conn.lua.eval('#submissions') == 1
    assert state.pending_action_summary is None
