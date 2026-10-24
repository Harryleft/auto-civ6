"""T75 replacement upgrade through public GameState/MCP and native Lua 5.1."""

import asyncio

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import app_of, make_ctx
from test_unit_transforms import TransformConn


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT", .01)
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL", .001)


class ReplacementConn(TransformConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
turn,seed,balance,cost,targetType=75,-1896272000,219.01953125,165,47
units[196608]=nil
u.id=655363;u.type=28;units[655363]=u
u.GetX=function() return 37 end;u.GetY=function() return 25 end
UnitCommandTypes.UPGRADE=-750032448
GameInfo.Units={[28]={UnitType='UNIT_ARCHER'},[47]={UnitType='UNIT_CROSSBOWMAN'}}
handlers={}
Events={}
for _,name in ipairs({'UnitCommandStarted','UnitRemovedFromMap','UnitAddedToMap','UnitUpgraded'}) do
 local listeners={};handlers[name]=listeners
 Events[name]={Add=function(f) listeners[f]=true end,Remove=function(f) listeners[f]=nil end,
  Fire=function(...)
   local copy={};for f in pairs(listeners) do table.insert(copy,f) end
   for _,f in ipairs(copy) do f(unpack(arg)) end
  end}
end
function makeReplacement(id)
 local new=addUnit(id,false,0,0)
 new.type=47
 new.GetType=function(self) return self.type end
 new.GetX=function() return 37 end;new.GetY=function() return 25 end
 return new
end
function applyReplacement()
 Events.UnitCommandStarted.Fire(0,655363,-750032448,0,0)
 units[655363]=nil
 Events.UnitRemovedFromMap.Fire(0,655363)
 replacement=makeReplacement(1245193)
 Events.UnitAddedToMap.Fire(0,1245193,37,25)
 balance=balance-cost
 Events.UnitUpgraded.Fire(0,1245193,47)
 -- The original engine trace duplicates this exact command after upgrading.
 Events.UnitCommandStarted.Fire(0,655363,-750032448,0,0)
end
UnitManager.RequestCommand=function(unit,command,params)
 table.insert(submissions,{id=unit.id,command=command})
 assert(unit.id==655363 and command==-750032448 and type(params)=='table')
 if not deferred then applyReplacement() end
 if fail_request then error('native failed after replacement') end
end
function upgradeListeners()
 local count=0
 for _,listeners in pairs(handlers) do for _ in pairs(listeners) do count=count+1 end end
 return count
end
''')


def test_native_upgrade_replacement_reports_exact_new_id_and_original_unit():
    conn = ReplacementConn()
    state = GameState(conn)
    ctx = make_ctx()
    app_of(ctx).game = state
    result = asyncio.run(server.upgrade_unit(ctx, 655363))
    assert result.startswith("UPGRADED|unit:1245193|")
    assert "old_unit:655363" in result and "type:28->47" in result
    assert state.pending_action_summary is None
    assert conn.lua.eval("#submissions") == 1
    assert conn.lua.eval("balance") == 54.01953125


@pytest.mark.parametrize("change", [
    "Events.UnitCommandStarted.Fire=function() end",
    "Events.UnitRemovedFromMap.Fire=function() end",
    "Events.UnitAddedToMap.Fire=function() end",
    "Events.UnitUpgraded.Fire=function() end",
    "local f=Events.UnitCommandStarted.Fire;Events.UnitCommandStarted.Fire=function(o,id,h,a,b) f(1,id,h,a,b) end",
    "local f=Events.UnitCommandStarted.Fire;Events.UnitCommandStarted.Fire=function(o,id,h,a,b) f(o,720899,h,a,b) end",
    "local f=Events.UnitCommandStarted.Fire;Events.UnitCommandStarted.Fire=function(o,id,h,a,b) f(o,id,21,a,b) end",
    "local f=Events.UnitCommandStarted.Fire;Events.UnitCommandStarted.Fire=function(o,id,h,a,b) f(o,id,h,1,b) end",
    "local f=Events.UnitRemovedFromMap.Fire;Events.UnitRemovedFromMap.Fire=function(o,id) f(o,720899) end",
    "local f=Events.UnitAddedToMap.Fire;Events.UnitAddedToMap.Fire=function(o,id,x,y) f(1,id,x,y) end",
    "local f=Events.UnitAddedToMap.Fire;Events.UnitAddedToMap.Fire=function(o,id,x,y) f(o,1310729,x,y) end",
    "local f=Events.UnitAddedToMap.Fire;Events.UnitAddedToMap.Fire=function(o,id,x,y) f(o,id,38,y) end",
    "local f=Events.UnitAddedToMap.Fire;Events.UnitAddedToMap.Fire=function(o,id,x,y) f(o,id,nil,y) end",
    "local f=Events.UnitAddedToMap.Fire;Events.UnitAddedToMap.Fire=function(o,id,x,y) f(o,id,'37',y) end",
    "local f=Events.UnitUpgraded.Fire;Events.UnitUpgraded.Fire=function(o,id,t) f(o,1310729,t) end",
    "local f=Events.UnitUpgraded.Fire;Events.UnitUpgraded.Fire=function(o,id,t) f(o,id,28) end",
    "local f=Events.UnitUpgraded.Fire;Events.UnitUpgraded.Fire=function(o,id,t) f(o,id,'47') end",
    "local f=Events.UnitUpgraded.Fire;Events.UnitUpgraded.Fire=function(o,id,t) f(o,nil,t) end",
    "makeReplacement(1245193)",  # The alleged result already existed before entry.
    "local f=Events.UnitAddedToMap.Fire;Events.UnitAddedToMap.Fire=function(...) f(unpack(arg));"
    "makeReplacement(1310729);f(0,1310729,37,25) end",  # Multiple eligible new candidates.
])
def test_unproven_or_ambiguous_replacement_remains_owned_and_never_resubmits(change):
    conn = ReplacementConn()
    conn.lua.execute(change)
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    assert state.pending_action_summary is not None
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.skip_unit(1245193))
    assert conn.lua.eval("#submissions") == 1


@pytest.mark.parametrize("change", [
    "units[655363]=u",  # Exact old identity still exists.
    "replacement.GetOwner=function() return 1 end",
    "replacement.id=1310729",  # Same low index, another generation.
    "replacement.type=28",
    "replacement.GetX=function() return 38 end",
    "replacement.GetY=function() return 26 end",
    "balance=219.01953125",  # No actual payment.
    "balance=53.01953125",  # A different payment is not the original cost.
    "__civmcpUpgrade.token='another-request'",
    "__civmcpUpgrade.requested=720899",
    "__civmcpUpgrade.target=28",
    "__civmcpUpgrade.id=720899",
    "__civmcpUpgrade.x=38",
])
def test_final_identity_position_original_intent_and_exact_costs_are_required(change):
    conn = ReplacementConn()
    conn.lua.execute('''
local apply=applyReplacement
applyReplacement=function()
 apply()
 CHANGE
end
'''.replace("CHANGE", change))
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    assert state.pending_action_summary is not None
    assert conn.lua.eval("#submissions") == 1


@pytest.mark.parametrize("change", [
    "u.allowed=false",
    "balance=164",
    "u.GetX=function() return nil end",
    "Players[0].GetUnits=function() return {Members=function() error('owned IDs unknown') end} end",
    "units[999]={GetID=function() return 'bad-id' end}",
])
def test_missing_baselines_and_native_refusals_never_enter_upgrade(change):
    conn = ReplacementConn()
    conn.lua.execute(change)
    state = GameState(conn)
    assert "UNIT_UPGRADE_REFUSED" in asyncio.run(state.upgrade_unit(655363))
    assert state.pending_action_summary is None
    assert conn.lua.eval("#submissions") == conn.lua.eval("upgradeListeners()") == 0


def test_out_of_order_chain_does_not_link_a_nearby_result_to_the_original():
    conn = ReplacementConn()
    conn.lua.execute('''
function applyReplacement()
 Events.UnitAddedToMap.Fire(0,1245193,37,25)
 Events.UnitCommandStarted.Fire(0,655363,-750032448,0,0)
 units[655363]=nil;Events.UnitRemovedFromMap.Fire(0,655363)
 replacement=makeReplacement(1245193);balance=balance-cost
 Events.UnitUpgraded.Fire(0,1245193,47)
end
''')
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    assert conn.lua.eval("#submissions") == 1


def test_events_during_listener_arming_cannot_prove_a_subsequent_unobserved_replacement():
    conn = ReplacementConn()
    conn.lua.execute('''
local add=Events.UnitUpgraded.Add
Events.UnitUpgraded.Add=function(handler)
 add(handler)
 applyReplacement()
 units[1245193]=nil;units[655363]=u;balance=219.01953125
end
UnitManager.RequestCommand=function(unit,command)
 table.insert(submissions,{id=unit.id,command=command})
 units[655363]=nil;replacement=makeReplacement(1245193);balance=balance-cost
end
''')
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    assert conn.lua.eval("#submissions") == 1


def test_replacement_reports_quote_and_observed_stock_without_claiming_a_debit():
    conn = ReplacementConn()
    conn.lua.execute('''
stock=14
GameInfo.Units_XP2={}
u.GetUpgradeResourceCost=function() return 3,5 end
Players[0].GetResources=function() return {GetResourceAmount=function(_,resource) assert(resource==3);return stock end} end
''')
    state = GameState(conn)
    result=asyncio.run(state.upgrade_unit(655363))
    assert result.startswith("UPGRADED|unit:1245193|")
    assert "resource_quote:5,resource_net_change:0" in result
    assert state.pending_action_summary is None
    assert conn.lua.eval("#submissions") == 1


def test_native_throw_after_effect_keeps_owner_without_a_native_return():
    conn = ReplacementConn()
    conn.lua.execute("fail_request=true")
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    assert conn.lua.eval("#submissions") == 1


@pytest.mark.parametrize("change,restore", [
    ("seed=1", "seed=-1896272000"),
    ("turn=76", "turn=75"),
    ("Game.GetLocalPlayer=function() return 1 end", "Game.GetLocalPlayer=function() return 0 end"),
    ("PlayerConfigurations[0].GetCivilizationTypeName=function() return 'CIVILIZATION_ROME' end",
     "PlayerConfigurations[0].GetCivilizationTypeName=function() return 'CIVILIZATION_SUMERIA' end"),
])
@pytest.mark.parametrize("same_id", [False, True])
def test_known_scope_history_blocks_both_replacement_and_same_id_after_scope_returns(change, restore, same_id):
    conn = ReplacementConn()
    conn.lose_reply = True
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    conn.lua.execute(change + ";Events.UnitCommandStarted.Fire(0,655363,-750032448,0,0);" + restore)
    if same_id:
        conn.lua.execute("u.type=47;units[655363]=u")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    assert state.pending_action_summary is not None
    assert conn.lua.eval("#submissions") == 1


def test_query_only_unknown_scope_can_recover_without_invented_invalidation():
    conn = ReplacementConn()
    conn.lose_reply = True
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    conn.lua.execute("GameConfiguration.GetValue=function() return nil end")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    conn.lua.execute("GameConfiguration.GetValue=function() return seed end")
    assert "UPGRADED|unit:1245193|" in asyncio.run(state.upgrade_unit(655363))
    assert conn.lua.eval("#submissions") == 1


@pytest.mark.parametrize("same_id", [False, True])
def test_scope_change_during_resource_getter_fences_every_confirmation_branch(same_id):
    conn = ReplacementConn()
    conn.lua.execute('''
once=false
Players[0].GetTreasury=function() return {GetGoldBalance=function()
 if #submissions>0 and not once then
  once=true;seed=1
  Events.UnitCommandStarted.Fire(0,655363,-750032448,0,0)
  seed=-1896272000
 end
 return balance
end} end
''')
    if same_id:
        conn.lua.execute("applyReplacement=function() u.type=47;balance=balance-cost end")
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    assert conn.lua.eval("#submissions") == 1


def test_delayed_native_chain_confirms_only_the_original_unit_request():
    conn = ReplacementConn()
    conn.lua.execute("deferred=true")
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    conn.lua.execute("applyReplacement()")
    assert "UPGRADED|unit:1245193|" in asyncio.run(state.upgrade_unit(655363))
    assert conn.lua.eval("#submissions") == 1


class LostProofConn(ReplacementConn):
    lost = False

    async def execute_write(self, code):
        rows = await super().execute_write(code)
        if not self.lost and any(row.startswith("UNIT_TRANSFORM_CONFIRMED|") for row in rows):
            self.lost = True
            raise OSError("confirmation reply lost")
        return rows


@pytest.mark.parametrize("phase", ["submit", "confirm"])
def test_lost_reply_retains_replacement_proof_and_native_cost_without_resubmission(phase):
    conn = ReplacementConn() if phase == "submit" else LostProofConn()
    conn.lose_reply = phase == "submit"
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    assert conn.lua.eval("upgradeListeners()") == 4
    assert "UPGRADED|unit:1245193|" in asyncio.run(state.upgrade_unit(655363))
    assert conn.lua.eval("#submissions") == 1
    assert conn.lua.eval("balance") == 54.01953125


@pytest.mark.parametrize("event", [
    "seed=1;Events.UnitCommandStarted.Fire(0,655363,-750032448,0,0);seed=-1896272000",
    "makeReplacement(1310729);Events.UnitAddedToMap.Fire(0,1310729,37,25)",
    "Events.UnitRemovedFromMap.Fire(0,1245193)",
    "Events.UnitCommandStarted.Fire(0,655363,-750032448,1,0)",
    "local read=GameConfiguration.GetValue;GameConfiguration.GetValue=function() return nil end;"
    "Events.UnitCommandStarted.Fire(0,655363,-750032448,0,0);GameConfiguration.GetValue=read",
])
def test_lost_proof_does_not_detach_later_scope_or_candidate_invalidation(event):
    conn = LostProofConn()
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    conn.lua.execute(event)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    assert state.pending_action_summary is not None
    assert conn.lua.eval("#submissions") == 1


def test_duplicate_exact_command_after_lost_proof_is_not_another_upgrade():
    conn = LostProofConn()
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.upgrade_unit(655363))
    conn.lua.execute("Events.UnitCommandStarted.Fire(0,655363,-750032448,0,0)")
    assert "UPGRADED|unit:1245193|" in asyncio.run(state.upgrade_unit(655363))
    assert conn.lua.eval("#submissions") == 1


def test_cancellation_after_native_return_preserves_original_replacement_owner():
    class CancelConn(ReplacementConn):
        async def execute_write(self, code):
            before = self.lua.eval("#submissions")
            rows = await super().execute_write(code)
            if self.lua.eval("#submissions") > before:
                raise asyncio.CancelledError()
            return rows

    conn = CancelConn()
    state = GameState(conn)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(state.upgrade_unit(655363))
    assert state.pending_action_summary is not None
    assert "UPGRADED|unit:1245193|" in asyncio.run(state.upgrade_unit(655363))
    assert conn.lua.eval("#submissions") == 1


def test_same_id_confirmation_works_without_replacement_observers():
    conn = ReplacementConn()
    conn.lua.execute('''
Events.UnitAddedToMap.Add=function() error('replacement event unavailable') end
applyReplacement=function() u.type=47;balance=balance-cost end
''')
    state = GameState(conn)
    assert "UPGRADED|unit:655363|type:28->47" == asyncio.run(state.upgrade_unit(655363))
    assert conn.lua.eval("upgradeListeners()") == 0
    assert conn.lua.eval("#submissions") == 1


def test_next_explicit_upgrade_retires_only_previous_request_observers():
    conn = ReplacementConn()
    state = GameState(conn)
    assert "UPGRADED|unit:1245193|" in asyncio.run(state.upgrade_unit(655363))
    conn.lua.execute('''
replacement.GetUpgradeCost=function() return 0 end
replacement.GetUpgradeResourceCost=function() return -1,0 end
targetType=48;GameInfo.Units[48]={UnitType='UNIT_FIELD_CANNON'}
UnitManager.RequestCommand=function(unit,command)
 table.insert(submissions,{id=unit.id,command=command})
 unit.type=48
end
''')
    assert "UPGRADED|unit:1245193|type:47->48" == asyncio.run(state.upgrade_unit(1245193))
    assert conn.lua.eval("upgradeListeners()") == 4
    assert conn.lua.eval("#submissions") == 2


def test_replacement_upgrade_separates_native_completion_from_net_resource_change():
    """T117: exact replacement, 165 gold paid, quote 13 iron, net debit 11."""
    conn=ReplacementConn()
    conn.lua.execute('''
GameInfo.Units_XP2={};stock=33
u.GetUpgradeResourceCost=function() return 43,13 end
Players[0].GetResources=function() return {GetResourceAmount=function(_,id) assert(id==43);return stock end} end
local apply=applyReplacement
applyReplacement=function() apply();stock=stock-13+2 end
''')
    state=GameState(conn);ctx=make_ctx();app_of(ctx).game=state
    result=asyncio.run(server.upgrade_unit(ctx,655363))
    assert result.startswith('UPGRADED|unit:1245193|'),result
    assert 'resource_quote:13,resource_net_change:-11' in result
    assert state.pending_action_summary is None and conn.lua.eval('#submissions')==1
    assert conn.lua.eval('balance')==54.01953125 and conn.lua.eval('stock')==22


@pytest.mark.parametrize('change',[
    'Events.UnitUpgraded.Fire=function() end',
    'local f=Events.UnitAddedToMap.Fire;Events.UnitAddedToMap.Fire=function(owner,id,x,y) f(owner,id,x+1,y) end',
])
def test_net_resource_change_never_substitutes_for_exact_upgrade_chain(change):
    conn=ReplacementConn()
    conn.lua.execute('''
GameInfo.Units_XP2={};stock=33
u.GetUpgradeResourceCost=function() return 43,13 end
Players[0].GetResources=function() return {GetResourceAmount=function(_,id) return stock end} end
local apply=applyReplacement
applyReplacement=function() apply();stock=22 end
'''+change)
    state=GameState(conn)
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.upgrade_unit(655363))
    assert 'OUTCOME_UNKNOWN' in asyncio.run(state.upgrade_unit(655363))
    assert state.pending_action_summary is not None and conn.lua.eval('#submissions')==1
