"""Destination-owner binding through public MCP/GameState and native Lua 5.1."""

import asyncio

import anyio
from mcp import ClientSession
import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_governor_confirmation import GovernorConn
from test_server import FakeLogger, make_ctx


AMANI = "GOVERNOR_THE_AMBASSADOR"


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT", .01)
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL", .001)


class ForeignGovernorConn(GovernorConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
turn=91;seed=-1896272000
local amani={GovernorType='GOVERNOR_THE_AMBASSADOR',Index=3,Hash=103}
GameInfo.Governors.GOVERNOR_THE_AMBASSADOR=amani
GameInfo.Governors[3]=amani;GameInfo.Governors[103]=amani
function destination(owner,id,x,y)
 local c={owner=owner,id=id,x=x,y=y}
 c.GetOwner=function(self) return self.owner end;c.GetID=function(self) return self.id end
 c.GetX=function(self) return self.x end;c.GetY=function(self) return self.y end
 return c
end
own=destination(0,65536,40,25)
foreign=destination(5,65536,40,6)
destinations={[0]={own},[5]={foreign}}
CityManager.GetCity=function(owner,index)
 for _,c in pairs(destinations[owner] or {}) do if c.id%65536==index then return c end end
end
addGovernor(103).assigned=own
Players[0]:GetGovernors().CanAssignGovernor=function(_,hash,city,check)
 assert(hash==103 and check==true)
 eligibility_owner=city:GetOwner();eligibility_id=city:GetID()
 return eligible
end
function applyDestination(req)
 governors[103].assigned=CityManager.GetCity(req.owner,req.city%65536)
end
UI.RequestPlayerOperation=function(actor,op,params)
 assert(actor==0 and op==3 and params.governor==3)
 params.op=op;table.insert(submissions,params)
 if not deferred then applyDestination(params) end
 if fail_request then error('assignment failed after native entry') end
end
''')

    async def disconnect(self):
        pass


def test_explicit_foreign_owner_disambiguates_same_id_own_city_and_native_parameters():
    conn=ForeignGovernorConn();state=GameState(conn)
    result=asyncio.run(server.assign_governor(make_ctx(game=state),AMANI,65536,target_owner=5))
    assert result.startswith("ASSIGNED|")
    assert "target_owner:5" in result and "city:65536" in result
    assert "established:false" in result
    assert conn.lua.eval("eligibility_owner")==5
    assert conn.lua.eval("eligibility_id")==65536
    assert conn.lua.eval("submissions[1].owner")==5
    assert conn.lua.eval("submissions[1].city")==65536
    assert conn.lua.eval("governors[103].assigned==foreign")
    assert conn.lua.eval("#submissions")==1
    assert state.pending_action_summary is None


@pytest.mark.parametrize("target_owner", [None,0])
def test_default_and_explicit_local_owner_keep_existing_own_city_calls(target_owner):
    conn=ForeignGovernorConn();state=GameState(conn)
    conn.lua.execute("governors[103].assigned=nil")
    result=asyncio.run(state.assign_governor(AMANI,65536,target_owner=target_owner))
    assert result.startswith("ASSIGNED|") and "target_owner:0" in result
    assert conn.lua.eval("submissions[1].owner")==0
    assert conn.lua.eval("governors[103].assigned==own")


@pytest.mark.parametrize("setup,owner",[("",0),("governors[103].assigned=foreign",5)])
def test_unchanged_assignment_is_bound_to_owner_full_id_and_position(setup,owner):
    conn=ForeignGovernorConn();state=GameState(conn);conn.lua.execute(setup)
    result=asyncio.run(state.assign_governor(AMANI,65536,target_owner=owner))
    assert result.startswith("GOVERNOR_UNCHANGED|") and f"target_owner:{owner}" in result
    assert conn.lua.eval("#submissions")==0


@pytest.mark.parametrize("owner",[-1,64,True,False,5.5,"5",float("nan"),float("inf")])
def test_invalid_explicit_owner_never_defaults_or_submits(owner):
    conn=ForeignGovernorConn();state=GameState(conn)
    result=asyncio.run(state.assign_governor(AMANI,65536,target_owner=owner))
    assert result.startswith("Error: INVALID_TARGET_OWNER|")
    assert conn.lua.eval("#submissions")==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize("city_id",[-1,None,True,65536.0,"65536"])
def test_invalid_city_id_never_chooses_same_id_in_another_owner(city_id):
    conn=ForeignGovernorConn();state=GameState(conn)
    assert "INVALID_CITY_ID" in asyncio.run(state.assign_governor(AMANI,city_id,target_owner=5))
    assert conn.lua.eval("#submissions")==0


@pytest.mark.parametrize("change",[
    "eligible=false","destinations[5]=nil","foreign.id=131072","foreign.owner=0",
    "foreign.x=nil","foreign.y=-9999",
    "Players[0]:GetGovernors().CanAssignGovernor=function() return nil end",
])
def test_native_refusal_or_unreadable_exact_city_is_safe_before_entry(change):
    conn=ForeignGovernorConn();state=GameState(conn);conn.lua.execute(change)
    assert "GOVERNOR_REFUSED" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    assert conn.lua.eval("#submissions")==0
    assert state.pending_action_summary is None


@pytest.mark.parametrize("change",[
    "governors[103].assigned=own",  # Same full ID, wrong owner.
    "foreign.id=131072","foreign.owner=0","foreign.x=41","foreign.y=7",
    "governors[103].assigned.GetOwner=function() return nil end",
    "__civmcpGovernor.targetOwner=0","__civmcpGovernor.city=131072",
    "__civmcpGovernor.cityX=41","__civmcpGovernor.token='stale'",
])
def test_confirmation_preserves_original_owner_city_position_and_receipt(change):
    conn=ForeignGovernorConn();state=GameState(conn);conn.lose_reply=True
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    conn.lua.execute(change)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    assert state.pending_action_summary is not None
    assert conn.lua.eval("#submissions")==1


def test_lost_reply_confirms_original_foreign_target_without_another_assignment():
    conn=ForeignGovernorConn();state=GameState(conn);ctx=make_ctx(game=state)
    conn.lose_reply=True
    assert "OUTCOME_UNKNOWN" in asyncio.run(server.assign_governor(ctx,AMANI,65536,target_owner=5))
    result=asyncio.run(server.assign_governor(ctx,AMANI,65536,target_owner=5))
    assert result.startswith("ASSIGNED|") and "target_owner:5" in result
    assert conn.lua.eval("#submissions")==1


def test_changed_owner_cannot_silently_confirm_then_retarget_within_one_call():
    conn=ForeignGovernorConn();state=GameState(conn);conn.lose_reply=True
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=0))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536))
    assert conn.lua.eval("#submissions")==1
    result=asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    assert result.startswith("ASSIGNED|") and "target_owner:5" in result


def test_default_local_intent_matches_explicit_local_owner_on_confirmation_only():
    conn=ForeignGovernorConn();state=GameState(conn)
    conn.lua.execute("governors[103].assigned=nil");conn.lose_reply=True
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536))
    assert asyncio.run(state.assign_governor(AMANI,65536,target_owner=0)).startswith("ASSIGNED|")
    assert conn.lua.eval("#submissions")==1


def test_mcp_schema_and_wire_forward_only_valid_explicit_owner(monkeypatch):
    conn=ForeignGovernorConn()
    monkeypatch.setattr(server,"GameConnection",lambda:conn)
    monkeypatch.setattr(server,"GameLogger",FakeLogger)

    async def go():
        client_write,server_read=anyio.create_memory_object_stream(10)
        server_write,client_read=anyio.create_memory_object_stream(10)
        lowlevel=server.mcp._mcp_server
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(lowlevel.run,server_read,server_write,lowlevel.create_initialization_options())
            async with ClientSession(client_read,client_write) as session:
                await session.initialize()
                tools=(await session.list_tools()).tools
                schema=next(tool.inputSchema for tool in tools if tool.name=="assign_governor")
                assert schema["required"]==["governor_type","city_id"]
                assert schema["properties"]["target_owner"]["default"] is None
                for owner in [True,False,5.0,"5",-1,64]:
                    result=await session.call_tool("assign_governor",{
                        "governor_type":AMANI,"city_id":65536,"target_owner":owner,
                    })
                    assert result.isError
                    assert conn.lua.eval("#submissions")==0
                result=await session.call_tool("assign_governor",{
                    "governor_type":AMANI,"city_id":65536,"target_owner":5,
                })
                assert not result.isError and "target_owner:5" in result.content[0].text
                assert conn.lua.eval("submissions[1].owner")==5
            tasks.cancel_scope.cancel()

    asyncio.run(go())


def test_foreign_assignment_waits_for_exact_native_target_and_reports_establishment_separately():
    conn=ForeignGovernorConn();state=GameState(conn);conn.lua.execute("deferred=true")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    # The same ID in our capital is insufficient while the assignment is delayed.
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    conn.lua.execute("applyDestination(submissions[1])")
    result=asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    assert result.startswith("ASSIGNED|") and "established:false,turns:5" in result
    assert conn.lua.eval("#submissions")==1


def test_foreign_assignment_report_failure_does_not_invent_establishment_or_block_assignment():
    conn=ForeignGovernorConn();state=GameState(conn)
    conn.lua.execute("governors[103].GetTurnsToEstablish=function() error('unreadable') end")
    result=asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    assert result.startswith("ASSIGNED|") and "established:unknown" in result
    assert conn.lua.eval("#submissions")==1


def test_foreign_noop_still_requires_the_exact_owner_during_readback():
    class ChangedNoopConn(ForeignGovernorConn):
        async def execute_write(self,code):
            rows=await super().execute_write(code)
            if any(row.startswith("OK:GOVERNOR_REQUESTED|") for row in rows):
                self.lua.execute("governors[103].assigned=own")
            return rows
    conn=ChangedNoopConn();state=GameState(conn)
    conn.lua.execute("governors[103].assigned=foreign")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    assert conn.lua.eval("#submissions")==0
    conn.lua.execute("governors[103].assigned=foreign")
    assert asyncio.run(state.assign_governor(AMANI,65536,target_owner=5)).startswith("GOVERNOR_UNCHANGED|")


@pytest.mark.parametrize("change,restore",[
    ("seed=1","seed=-1896272000"),("turn=92","turn=91"),
])
def test_foreign_target_confirmation_never_revives_after_a_known_scope_change(change,restore):
    conn=ForeignGovernorConn();state=GameState(conn);conn.lose_reply=True
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    conn.lua.execute(change)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    conn.lua.execute(restore)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    assert conn.lua.eval("#submissions")==1


def test_unknown_scope_query_recovers_for_original_owner_without_reassignment():
    conn=ForeignGovernorConn();state=GameState(conn);conn.lose_reply=True
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    conn.lua.execute("GameConfiguration.GetValue=function() return nil end")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    conn.lua.execute("GameConfiguration.GetValue=function() return seed end")
    assert asyncio.run(state.assign_governor(AMANI,65536,target_owner=5)).startswith("ASSIGNED|")
    assert conn.lua.eval("#submissions")==1


def test_scope_change_during_establishment_readback_is_not_a_completed_assignment():
    conn=ForeignGovernorConn();state=GameState(conn)
    conn.lua.execute("governors[103].IsEstablished=function() seed=1;return false end")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    conn.lua.execute("seed=-1896272000;governors[103].IsEstablished=function() return false end")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    assert conn.lua.eval("#submissions")==1


@pytest.mark.parametrize("kind",["cancel","throw"])
def test_foreign_native_interruption_preserves_original_target_and_single_submission(kind):
    conn=ForeignGovernorConn();state=GameState(conn)
    if kind=="cancel":
        conn.cancel_reply=True
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
        assert asyncio.run(state.assign_governor(AMANI,65536,target_owner=5)).startswith("ASSIGNED|")
    else:
        conn.lua.execute("fail_request=true")
        assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
        assert "OUTCOME_UNKNOWN" in asyncio.run(state.assign_governor(AMANI,65536,target_owner=5))
    assert conn.lua.eval("#submissions")==1
