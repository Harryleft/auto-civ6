"""Public relationship calls retain original native requests across uncertainty."""
import asyncio
import pytest
from test_friendship_confirmation_lua import FriendshipConn
from civ_mcp.game_state import GameState

class RelationshipConn(FriendshipConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''seed=123; turn=78
Game.GetCurrentGameTurn=function() return turn end
GameConfiguration={GetValue=function() return seed end}
PlayerConfigurations[0]={GetCivilizationTypeName=function() return "CIVILIZATION_SUMERIA" end}
UI={IsGameCoreBusy=function() return false end}
''')

@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT",0.01)
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL",0.001)

def test_lost_relationship_ack_is_retained_and_repeat_only_confirms():
    async def scenario():
        conn=RelationshipConn();gs=GameState(conn)
        original=conn.execute_write
        lost=False
        async def transport(source):
            nonlocal lost
            rows=await original(source)
            if len(conn.lua.globals().friendship_requests) and not lost:
                lost=True
                raise OSError("lost after native request")
            return rows
        conn.execute_write=transport
        first=await gs.send_diplomatic_action(1,"DECLARE_FRIENDSHIP")
        assert "OUTCOME_UNKNOWN" in first
        again=await gs.send_diplomatic_action(1,"DECLARE_FRIENDSHIP")
        assert "OUTCOME_UNKNOWN" in again
        assert len(conn.lua.globals().friendship_requests)==1
        conn.lua.execute("readbacks={{[0]=87,[1]=87}}")
        assert (await gs.send_diplomatic_action(1,"DECLARE_FRIENDSHIP")).startswith("ACCEPTED|")
        assert len(conn.lua.globals().friendship_requests)==1
    asyncio.run(scenario())

@pytest.mark.parametrize('stage',['request','response','close'])
@pytest.mark.parametrize('failure',['throw','false','cancel'])
def test_partial_native_phase_never_replays(stage,failure):
    async def scenario():
        from civ_mcp.connection import LuaError
        conn=RelationshipConn();gs=GameState(conn)
        native={'request':'RequestSession','response':'AddResponse','close':'CloseSession'}[stage]
        conn.lua.globals().failure_hook=lambda: (_ for _ in ()).throw(asyncio.CancelledError())
        conn.lua.execute(f'''
phase_calls=0
local original=DiplomacyManager.{native}
DiplomacyManager.{native}=function(...)
 phase_calls=phase_calls+1
 original(...)
 {'failure_hook()' if failure=='cancel' else 'error("native interruption")' if failure=='throw' else 'return false'}
end
''')
        original=conn.execute_write
        async def transport(code):
            from lupa.lua51 import LuaError as NativeLuaError
            try:return await original(code)
            except NativeLuaError as err:raise LuaError(str(err)) from err
        conn.execute_write=transport
        try:
            result=await gs.send_diplomatic_action(1,'DECLARE_FRIENDSHIP')
            assert 'OUTCOME_UNKNOWN' in result
        except asyncio.CancelledError:
            assert failure=='cancel'
        mutations=list(conn.lua.globals().mutations.values())
        if stage=="request" and failure=="false":
            assert mutations==[]
        calls=conn.lua.globals().phase_calls
        result=await gs.send_diplomatic_action(1,'DECLARE_FRIENDSHIP')
        assert 'OUTCOME_UNKNOWN' in result
        assert conn.lua.globals().phase_calls==calls
        assert list(conn.lua.globals().mutations.values())==mutations
    asyncio.run(scenario())

class WarConn(RelationshipConn):
    def __init__(self, peace=False):
        super().__init__()
        self.lua.globals().initial_war=peace
        self.lua.execute('''
war_reads=0; war_steps={{[0]=initial_war,[1]=initial_war}}; war_submitted=false
for owner=0,1 do
 local who=owner
 local d={HasMet=function() return true end,CanDeclareWarOn=function() return true end,
          CanMakePeaceWith=function() return true end}
 d.IsAtWarWith=function(_,target)
  assert(target==1-who)
  if not war_submitted then return initial_war end
  if who==0 then war_reads=war_reads+1 end
  return war_steps[math.max(1,math.min(war_reads,#war_steps))][who]
 end
 Players[owner].GetDiplomacy=function() return d end
end
local request=DiplomacyManager.RequestSession
DiplomacyManager.RequestSession=function(...) request(...);war_submitted=true end
DealProposalAction.PROPOSED=3
''')

@pytest.mark.parametrize('action',['MAKE_PEACE','DECLARE_SURPRISE_WAR','DECLARE_FORMAL_WAR','DECLARE_HOLY_WAR','DECLARE_LIBERATION_WAR','DECLARE_RECONQUEST_WAR','DECLARE_PROTECTORATE_WAR','DECLARE_COLONIAL_WAR','DECLARE_TERRITORIAL_WAR'])
def test_war_and_peace_require_bilateral_change_and_repeat_only_reads(action):
    async def scenario():
        conn=WarConn(action=='MAKE_PEACE');gs=GameState(conn)
        async def call():
            return await gs.propose_peace(1) if action=='MAKE_PEACE' else await gs.send_diplomatic_action(1,action)
        assert 'OUTCOME_UNKNOWN' in await call()
        count=len(conn.lua.globals().mutations)
        assert 'OUTCOME_UNKNOWN' in await call()
        assert len(conn.lua.globals().friendship_requests)==1
        assert len(conn.lua.globals().mutations)==count
        final='false' if action=='MAKE_PEACE' else 'true'
        conn.lua.execute(f'war_steps={{{{[0]={final},[1]={final}}}}}')
        assert (await call()).startswith('ACCEPTED|')
        assert len(conn.lua.globals().friendship_requests)==1
        assert len(asyncio.all_tasks())==1  # No delayed task can close a later session.
    asyncio.run(scenario())

def test_denounce_requires_our_new_denouncement_not_their_denouncement():
    async def scenario():
        conn=RelationshipConn();gs=GameState(conn)
        conn.lua.execute('''
denounce_turn=-1
Players[0]:GetDiplomacy().GetDenounceTurn=function() return denounce_turn end
DiplomaticStates.DENOUNCED=7010
GameInfo.DiplomaticStates[95]={Hash=7010,StateType="DIPLO_STATE_DENOUNCED"}
readbacks={{[0]=95,[1]=95}}
''')
        assert 'OUTCOME_UNKNOWN' in await gs.send_diplomatic_action(1,'DENOUNCE')
        conn.lua.execute('denounce_turn=78')
        assert await gs.send_diplomatic_action(1,'DENOUNCE')=='ACCEPTED|Denounced Sweden'
        assert len(conn.lua.globals().friendship_requests)==1
    asyncio.run(scenario())

@pytest.mark.parametrize('change',['seed=234','turn=79','Game.GetLocalPlayer=function() return 2 end'])
def test_original_world_turn_and_owner_are_latched(change):
    async def scenario():
        conn=RelationshipConn();gs=GameState(conn)
        assert 'OUTCOME_UNKNOWN' in await gs.send_diplomatic_action(1,'DECLARE_FRIENDSHIP')
        conn.lua.execute(change)
        assert 'OUTCOME_UNKNOWN' in await gs.send_diplomatic_action(1,'DECLARE_FRIENDSHIP')
        conn.lua.execute('seed=123;turn=78;Game.GetLocalPlayer=function() return 0 end;readbacks={{[0]=87,[1]=87}}')
        assert 'OUTCOME_UNKNOWN' in await gs.send_diplomatic_action(1,'DECLARE_FRIENDSHIP')
        assert len(conn.lua.globals().friendship_requests)==1
    asyncio.run(scenario())

def test_alliance_never_accepts_an_unrelated_working_deal():
    from test_alliance_confirmation_lua import AllianceConn
    conn=AllianceConn()
    conn.lua.execute('DealManager.AreWorkingDealsEqual=function() return false end;alliance_steps={{[0]=1,[1]=1,alliance=23}}')
    result=asyncio.run(GameState(conn).form_alliance(1,'RESEARCH'))
    assert 'OUTCOME_UNKNOWN' in result
    assert [item.action for item in conn.lua.globals().alliance_sends.values()]==[3]

def test_wrong_session_participants_cannot_authorize_responses_or_completion():
    async def scenario():
        conn=RelationshipConn();gs=GameState(conn)
        conn.lua.execute('''
local request=DiplomacyManager.RequestSession
DiplomacyManager.RequestSession=function(...) request(...);infos[80]={FromPlayer=2,ToPlayer=0} end
readbacks={{[0]=87,[1]=87}}
''')
        assert 'OUTCOME_UNKNOWN' in await gs.send_diplomatic_action(1,'DECLARE_FRIENDSHIP')
        assert 'OUTCOME_UNKNOWN' in await gs.send_diplomatic_action(1,'DECLARE_FRIENDSHIP')
        assert list(conn.lua.globals().mutations.values())==[]
        assert len(conn.lua.globals().friendship_requests)==1
    asyncio.run(scenario())

def test_existing_bilateral_friendship_is_noop():
    conn=RelationshipConn()
    conn.lua.execute('friendship_baseline=87;readbacks={{[0]=87,[1]=87}}')
    result=asyncio.run(GameState(conn).send_diplomatic_action(1,'DECLARE_FRIENDSHIP'))
    assert result.startswith('ACCEPTED|')
    assert len(conn.lua.globals().friendship_requests)==0
    assert list(conn.lua.globals().mutations.values())==[]

@pytest.mark.parametrize('action',['MAKE_PEACE','DECLARE_SURPRISE_WAR','ALLIANCE_RESEARCH','DENOUNCE'])
def test_existing_exact_relation_is_noop(action):
    if action=='ALLIANCE_RESEARCH':
        from test_alliance_confirmation_lua import AllianceConn
        conn=AllianceConn();conn.lua.execute('pre_state=44')
    elif action=='DENOUNCE':
        conn=RelationshipConn();conn.lua.execute('''
Players[0]:GetDiplomacy().GetDenounceTurn=function() return 78 end
DiplomaticStates.DENOUNCED=7010
GameInfo.DiplomaticStates[95]={Hash=7010,StateType="DIPLO_STATE_DENOUNCED"}
friendship_baseline=95;readbacks={{[0]=95,[1]=95}}
''')
    else:conn=WarConn(action!='MAKE_PEACE')
    gs=GameState(conn)
    call=gs.propose_peace(1) if action=='MAKE_PEACE' else gs.form_alliance(1,'RESEARCH') if action.startswith('ALLIANCE_') else gs.send_diplomatic_action(1,action)
    assert asyncio.run(call).startswith('ACCEPTED|')
    assert len(conn.lua.globals().friendship_requests)==0
    assert list(conn.lua.globals().mutations.values())==[]

@pytest.mark.parametrize('action',['DIPLOMATIC_DELEGATION','RESIDENT_EMBASSY'])
def test_diplomatic_access_needs_exact_original_gold_cost_and_outgoing_presence(action):
    async def scenario():
        from test_access_confirmation_lua import AccessConn
        conn=AccessConn();gs=GameState(conn)
        conn.lua.execute('charge_access=false;access_steps={true}')
        assert 'OUTCOME_UNKNOWN' in await gs.send_diplomatic_action(1,action)
        conn.lua.execute('gold=76')
        assert 'OUTCOME_UNKNOWN' in await gs.send_diplomatic_action(1,action)
        conn.lua.execute('gold=75')
        assert (await gs.send_diplomatic_action(1,action)).startswith('ACCEPTED|')
        assert len(conn.lua.globals().friendship_requests)==1
    asyncio.run(scenario())

@pytest.mark.parametrize('phase',['AddResponse','CloseSession'])
def test_native_interruption_after_relationship_effect_still_requires_returned_protocol(phase):
    async def scenario():
        conn=RelationshipConn();gs=GameState(conn)
        conn.lua.execute(f'''local original=DiplomacyManager.{phase}
DiplomacyManager.{phase}=function(...)
 original(...);readbacks={{{{[0]=87,[1]=87}}}};error("native interrupted after effect")
end''')
        assert 'OUTCOME_UNKNOWN' in await gs.send_diplomatic_action(1,'DECLARE_FRIENDSHIP')
        assert 'OUTCOME_UNKNOWN' in await gs.send_diplomatic_action(1,'DECLARE_FRIENDSHIP')
        assert len(conn.lua.globals().friendship_requests)==1
    asyncio.run(scenario())

def test_public_mcp_repeated_relationship_request_only_confirms():
    from civ_mcp import server
    from test_server import make_ctx,app_of
    async def scenario():
        conn=RelationshipConn();ctx=make_ctx();app_of(ctx).game=GameState(conn)
        assert 'OUTCOME_UNKNOWN' in await server.send_diplomatic_action(ctx,1,'DECLARE_FRIENDSHIP')
        conn.lua.execute('readbacks={{[0]=87,[1]=87}}')
        assert (await server.send_diplomatic_action(ctx,1,'DECLARE_FRIENDSHIP')).startswith('ACCEPTED|')
        assert len(conn.lua.globals().friendship_requests)==1
    asyncio.run(scenario())

def test_alliance_cannot_append_items_to_an_existing_pending_deal():
    from test_alliance_confirmation_lua import AllianceConn
    conn=AllianceConn();conn.lua.execute('DealManager.HasPendingDeal=function() return true end')
    gs=GameState(conn)
    result=asyncio.run(gs.form_alliance(1,'RESEARCH'))
    assert result.startswith('Error: RELATIONSHIP_REFUSED|'),result
    assert not gs.pending_action_summary
    assert len(conn.lua.globals().friendship_requests)==0
    assert list(conn.lua.globals().mutations.values())==[]
