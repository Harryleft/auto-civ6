"""Native typed alliance eligibility at public GameState/MCP/Lua seams."""

import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_alliance_confirmation_lua import AllianceConn
from test_server import make_ctx


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT", .01)
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL", .001)


class EligibleAllianceConn(AllianceConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
turn=108;seed=-1896272000;has_civic=false
Players[2]=Players[1];Players[1]=nil
PlayerConfigurations[2].GetCivilizationShortDescription=function() return 'Brazil' end
GameInfo.Civics.CIVIC_CIVIL_SERVICE={Index=66}
GameInfo.Alliances.ALLIANCE_RESEARCH.Index=0
GameInfo.Alliances[0]=GameInfo.Alliances.ALLIANCE_RESEARCH
GameInfo.DiplomaticActions={DIPLOACTION_ALLIANCE_RESEARCH={
 DiplomaticActionType='DIPLOACTION_ALLIANCE_RESEARCH',Hash=430927667,
 InitiatorPrereqCivic='CIVIC_CIVIL_SERVICE',TargetPrereqCivic='CIVIC_CIVIL_SERVICE'}}
GameInfo.DiplomaticActions_XP1={DIPLOACTION_ALLIANCE_RESEARCH={
 DiplomaticActionType='DIPLOACTION_ALLIANCE_RESEARCH',AllianceType='ALLIANCE_RESEARCH'}}
eligibility_calls={};eligibility={DIPLOACTION_ALLIANCE_RESEARCH=true}
local diplo=Players[0]:GetDiplomacy()
diplo.IsDiplomaticActionValid=function(_,action,target,check)
 if action:find('^DIPLOACTION_ALLIANCE_') then
  assert(target==2 and check==true)
  table.insert(eligibility_calls,{action=action,target=target,check=check})
  return eligibility[action]
 end
 return false
end
alliance_steps={{[0]=44,[1]=44,alliance=0}}
for _,owner in ipairs({0,2}) do
 local actor=owner
 Players[actor].GetDiplomaticAI=function() return {
  GetDiplomaticStateIndex=function(_,target)
   assert(target==(actor==0 and 2 or 0))
   if not submitted then return pre_state end
   if actor==0 then observations=observations+1 end
   return alliance_steps[math.max(1,math.min(observations,#alliance_steps))][actor==0 and 0 or 1]
  end,GetDiplomaticModifiers=function() return {} end,
 } end
 Players[actor].GetCulture=function() return {HasCivic=function(_,index) return index==66 end} end
end
diplo.GetAllianceType=function(_,target) assert(target==2);return alliance_steps[math.max(1,math.min(observations,#alliance_steps))].alliance end
''')

    def enable_query_facts(self):
        self.lua.execute('''
PlayersVisibility={[0]={IsRevealed=function() return false end}}
GameInfo.Resources=function() return function() return nil end end
local diplo=Players[0]:GetDiplomacy()
diplo.HasOpenBordersFrom=function() return false end
diplo.GetVisibilityOn=function() return 0 end
diplo.GetGrievancesAgainst=function() return 0 end
diplo.HasDelegationAt=function() return false end
diplo.HasEmbassyAt=function() return false end
diplo.HasDefensivePact=function() return false end
for _,owner in ipairs({0,2}) do
 Players[owner].GetTreasury=function() return {GetGoldBalance=function() return 100 end,
  GetGoldYield=function() return 10 end,GetTotalMaintenance=function() return 1 end} end
 Players[owner].GetFavor=function() return 0 end
 Players[owner].GetCities=function() return {Members=function() return function() return nil end end} end
end
''')


def test_t108_native_research_eligible_before_diplomatic_service_submits_exact_type():
    conn=EligibleAllianceConn();state=GameState(conn)
    result=asyncio.run(server.form_alliance(make_ctx(game=state),2,"RESEARCH"))
    assert result=="ACCEPTED|RESEARCH alliance formed with Brazil"
    assert conn.lua.eval("eligibility_calls[1].action")=="DIPLOACTION_ALLIANCE_RESEARCH"
    assert conn.lua.eval("submitted_alliance")==0
    assert conn.lua.eval("#friendship_requests")==1
    assert conn.lua.eval("friendship_requests[1].target")==2
    assert [row.action for row in conn.lua.globals().alliance_sends.values()]==[3,1]


def test_t108_trade_and_diplomacy_queries_agree_with_native_research_eligibility():
    conn=EligibleAllianceConn();conn.enable_query_facts();state=GameState(conn)
    options=asyncio.run(state.get_deal_options(2))
    assert options.alliance_eligible is True
    rivals=asyncio.run(state.get_diplomacy())
    assert "MAKE_ALLIANCE" in rivals[0].available_actions
    text=asyncio.run(server.get_trade_options(make_ctx(game=state),2))
    assert "Diplomatic Service" not in text
    assert "at least one native type" in text
    assert list(conn.lua.globals().mutations.values())==[]


@pytest.mark.parametrize("change,reason",[
    ("eligibility.DIPLOACTION_ALLIANCE_RESEARCH=false","ALLIANCE_NOT_ELIGIBLE"),
    ("eligibility.DIPLOACTION_ALLIANCE_RESEARCH=nil","ALLIANCE_ELIGIBILITY_UNKNOWN"),
    ("eligibility.DIPLOACTION_ALLIANCE_RESEARCH=1","ALLIANCE_ELIGIBILITY_UNKNOWN"),
    ("eligibility.DIPLOACTION_ALLIANCE_RESEARCH='true'","ALLIANCE_ELIGIBILITY_UNKNOWN"),
    ("eligibility.DIPLOACTION_ALLIANCE_RESEARCH={}","ALLIANCE_ELIGIBILITY_UNKNOWN"),
    ("Players[0]:GetDiplomacy().IsDiplomaticActionValid=nil","ALLIANCE_ELIGIBILITY_UNKNOWN"),
    ("Players[0]:GetDiplomacy().IsDiplomaticActionValid=function() error('unknown eligibility') end","ALLIANCE_ELIGIBILITY_UNKNOWN"),
    ("GameInfo.DiplomaticActions=nil","ALLIANCE_ACTION_UNAVAILABLE"),
    ("GameInfo.DiplomaticActions.DIPLOACTION_ALLIANCE_RESEARCH=nil","ALLIANCE_ACTION_UNAVAILABLE"),
    ("GameInfo.DiplomaticActions.DIPLOACTION_ALLIANCE_RESEARCH.DiplomaticActionType='DIPLOACTION_ALLIANCE_MILITARY'","ALLIANCE_ACTION_UNAVAILABLE"),
    ("GameInfo.DiplomaticActions.DIPLOACTION_ALLIANCE_RESEARCH.Hash=nil","ALLIANCE_ACTION_UNAVAILABLE"),
    ("GameInfo.DiplomaticActions.DIPLOACTION_ALLIANCE_RESEARCH.Hash='430927667'","ALLIANCE_ACTION_UNAVAILABLE"),
    ("GameInfo.DiplomaticActions.DIPLOACTION_ALLIANCE_RESEARCH.Hash=0/0","ALLIANCE_ACTION_UNAVAILABLE"),
    ("GameInfo.DiplomaticActions_XP1=nil","ALLIANCE_ACTION_UNAVAILABLE"),
    ("GameInfo.DiplomaticActions_XP1.DIPLOACTION_ALLIANCE_RESEARCH=nil","ALLIANCE_ACTION_UNAVAILABLE"),
    ("GameInfo.DiplomaticActions_XP1.DIPLOACTION_ALLIANCE_RESEARCH.DiplomaticActionType='DIPLOACTION_ALLIANCE_MILITARY'","ALLIANCE_ACTION_UNAVAILABLE"),
    ("GameInfo.DiplomaticActions_XP1.DIPLOACTION_ALLIANCE_RESEARCH.AllianceType='ALLIANCE_MILITARY'","ALLIANCE_ACTION_UNAVAILABLE"),
])
def test_native_false_unknown_or_unbound_action_refuses_before_any_deal_or_session_write(change,reason):
    conn=EligibleAllianceConn();conn.enable_query_facts();conn.lua.execute(change);state=GameState(conn)
    options=asyncio.run(state.get_deal_options(2))
    assert options.alliance_eligible is False
    assert "MAKE_ALLIANCE" not in asyncio.run(state.get_diplomacy())[0].available_actions
    text=asyncio.run(server.get_trade_options(make_ctx(game=state),2))
    assert "no currently verified eligible native type" in text and "Diplomatic Service" not in text
    result=asyncio.run(state.form_alliance(2,"RESEARCH"))
    assert result.startswith("Error: RELATIONSHIP_REFUSED|") and reason in result
    assert state.pending_action_summary is None
    assert list(conn.lua.globals().mutations.values())==[]
    assert conn.lua.eval("#friendship_requests")==conn.lua.eval("#alliance_sends")==conn.lua.eval("reads")==0


def test_eligibility_for_another_type_cannot_authorize_the_requested_research_alliance():
    conn=EligibleAllianceConn();conn.enable_query_facts();state=GameState(conn)
    conn.lua.execute('''
eligibility.DIPLOACTION_ALLIANCE_RESEARCH=false
eligibility.DIPLOACTION_ALLIANCE_MILITARY=true
GameInfo.DiplomaticActions.DIPLOACTION_ALLIANCE_MILITARY={DiplomaticActionType='DIPLOACTION_ALLIANCE_MILITARY',Hash=123}
GameInfo.DiplomaticActions_XP1.DIPLOACTION_ALLIANCE_MILITARY={DiplomaticActionType='DIPLOACTION_ALLIANCE_MILITARY',AllianceType='ALLIANCE_MILITARY'}
''')
    # The public generic bool promises only that at least one typed action is valid.
    assert asyncio.run(state.get_deal_options(2)).alliance_eligible is True
    assert "MAKE_ALLIANCE" in asyncio.run(state.get_diplomacy())[0].available_actions
    result=asyncio.run(state.form_alliance(2,"RESEARCH"))
    assert "ALLIANCE_NOT_ELIGIBLE" in result
    assert list(conn.lua.globals().mutations.values())==[]
    conn.lua.execute("alliance_steps={{[0]=44,[1]=44,alliance=9}}")
    assert asyncio.run(state.form_alliance(2,"MILITARY"))=="ACCEPTED|MILITARY alliance formed with Brazil"
    assert conn.lua.eval("submitted_alliance")==9
    assert conn.lua.eval("#friendship_requests")==1


@pytest.mark.parametrize("change,reason",[
    ("Players[0]:GetDiplomacy().IsAtWarWith=function() return true end","AT_WAR"),
    ("pre_state=60","NOT_FRIENDS"),
    ("pre_state=999","RELATIONSHIP_UNKNOWN"),
    ("DealManager.HasPendingDeal=function() return true end","PENDING_DEAL"),
    ("DealManager.HasPendingDeal=function() return nil end","PENDING_DEAL"),
])
def test_native_typed_eligibility_never_bypasses_existing_relationship_or_pending_deal_gate(change,reason):
    conn=EligibleAllianceConn();conn.lua.execute(change);state=GameState(conn)
    result=asyncio.run(state.form_alliance(2,"RESEARCH"))
    assert result.startswith("Error: RELATIONSHIP_REFUSED|") and reason in result
    assert list(conn.lua.globals().mutations.values())==[]
    assert conn.lua.eval("#friendship_requests")==0


def test_existing_exact_alliance_remains_a_noop_even_if_proposal_eligibility_is_now_false():
    conn=EligibleAllianceConn();conn.lua.execute("pre_state=44;eligibility.DIPLOACTION_ALLIANCE_RESEARCH=false")
    state=GameState(conn)
    assert asyncio.run(state.form_alliance(2,"RESEARCH"))=="ACCEPTED|RESEARCH alliance formed with Brazil"
    assert conn.lua.eval("#eligibility_calls")==0
    assert list(conn.lua.globals().mutations.values())==[]


def test_native_eligible_proposal_without_bilateral_allied_state_remains_unknown_without_resend():
    conn=EligibleAllianceConn();conn.lua.execute("alliance_steps={{[0]=1,[1]=1,alliance=0}}")
    state=GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.form_alliance(2,"RESEARCH"))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.form_alliance(2,"RESEARCH"))
    assert conn.lua.eval("#friendship_requests")==1
    assert len(conn.lua.globals().alliance_sends)==2


def test_lost_native_proposal_reply_preserves_original_pair_and_type_with_one_sequence():
    class LostConn(EligibleAllianceConn):
        lost=False

        async def execute_write(self,source):
            before=self.lua.eval("#friendship_requests")
            rows=await super().execute_write(source)
            if not self.lost and self.lua.eval("#friendship_requests")>before:
                self.lost=True
                raise OSError("native alliance sequence ran; response lost")
            return rows

    conn=LostConn();state=GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.form_alliance(2,"RESEARCH"))
    assert asyncio.run(state.form_alliance(2,"RESEARCH"))=="ACCEPTED|RESEARCH alliance formed with Brazil"
    assert conn.lua.eval("#friendship_requests")==1
    assert len(conn.lua.globals().alliance_sends)==2
