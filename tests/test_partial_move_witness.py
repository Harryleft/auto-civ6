"""Observed T69 interrupted movement through public GameState and Lua 5.1."""

import asyncio

import pytest
from lupa.lua51 import LuaError as NativeLuaError

from civ_mcp.connection import LuaError
from civ_mcp.game_state import GameState
from test_move_target_lua import TargetMovementConn


@pytest.fixture(autouse=True)
def short_confirmation(monkeypatch):
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT", 0.015)
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL", 0.001)


class InterruptedMoveConn(TargetMovementConn):
    def __init__(self):
        super().__init__()
        self.lua.execute('''
x,y,turn,seed=41,27,69,-1896272000
can_target=true
unit.GetID=function() return 786437 end
unit.GetOwner=function() return 0 end
unit.GetMovesRemaining=function() return moves end
unit.GetMovementMovesRemaining=function() return moves end
unit.IsReadyToMove=function() return true end
moves=2
UnitOperationTypes.MOVE_TO=1477390184
GameInfo.UnitOperations={UNITOPERATION_MOVE_TO={Hash=1477390184,Index=19}}
UI.IsProcessingMessages=function() return false end
queued=nil
UnitManager.GetQueuedDestination=function() return queued end
move_listeners={}
for _,name in ipairs({"UnitOperationAdded","UnitMoveComplete","UnitOperationDeactivated","UnitOperationsCleared"}) do
 local handlers={};move_listeners[name]=handlers
 Events[name]={Add=function(handler) handlers[handler]=true end,
  Remove=function(handler) handlers[handler]=nil end,
  Fire=function(...)
   local copy={};for handler in pairs(handlers) do table.insert(copy,handler) end
   for _,handler in ipairs(copy) do handler(unpack(arg)) end
  end}
end
function interruptedMove(u)
 -- Old ALERT cleanup precedes the operation this request actually adds.
 Events.UnitOperationDeactivated.Fire(0,u:GetID(),-448283625,1)
 Events.UnitOperationsCleared.Fire(0,u:GetID(),0,0)
 Events.UnitOperationAdded.Fire(0,u:GetID(),0,19)
 x,y,moves=42,27,1
 Events.UnitMoveComplete.Fire(0,u:GetID(),42,27)
 Events.UnitOperationDeactivated.Fire(0,u:GetID(),1477390184,1)
 Events.UnitOperationsCleared.Fire(0,u:GetID(),0,0)
end
UnitManager.RequestOperation=function(u,operation,params)
 submissions=submissions+1
 interruptedMove(u)
end
function moveListenerCount()
 local count=0
 for _,handlers in pairs(move_listeners) do for _ in pairs(handlers) do count=count+1 end end
 return count
end
''')

    async def execute_write(self, code):
        try:
            return await super().execute_write(code)
        except NativeLuaError as exc:
            raise LuaError(str(exc)) from exc


def test_evidenced_native_move_chain_confirms_partial_displacement_with_cleared_path():
    conn = InterruptedMoveConn()
    state = GameState(conn)
    result = asyncio.run(state.move_unit(786437, 42, 29))
    assert "now_at:42,27" in result and "moves_remaining:1" in result
    assert "PATH_INTERRUPTED (native move cleared)" in result
    assert "segment complete" not in result and "now_at:42,29" not in result
    assert state.pending_action_summary is None
    assert conn.lua.eval("submissions") == 1


@pytest.mark.parametrize("change", [
    "Events.UnitOperationAdded.Fire=function() end",
    "Events.UnitMoveComplete.Fire=function() end",
    "Events.UnitOperationDeactivated.Fire=function() end",
    "Events.UnitOperationsCleared.Fire=function() end",
    "local f=Events.UnitOperationAdded.Fire;Events.UnitOperationAdded.Fire=function(o,id,a,b) f(o,id,a,1) end",
    "local f=Events.UnitOperationAdded.Fire;Events.UnitOperationAdded.Fire=function(o,id,a,b) f(o,id,1,b) end",
    "local f=Events.UnitMoveComplete.Fire;Events.UnitMoveComplete.Fire=function(o,id,a,b) f(1,id,a,b) end",
    "local f=Events.UnitMoveComplete.Fire;Events.UnitMoveComplete.Fire=function(o,id,a,b) f(o,851973,a,b) end",
    "local f=Events.UnitMoveComplete.Fire;Events.UnitMoveComplete.Fire=function(o,id,a,b) f(o,id,41,27) end",
    "local f=Events.UnitMoveComplete.Fire;Events.UnitMoveComplete.Fire=function(o,id,a,b) f(o,id,43,27) end",
    "local f=Events.UnitMoveComplete.Fire;Events.UnitMoveComplete.Fire=function(o,id,a,b) f(o,id,nil,b) end",
    "local f=Events.UnitMoveComplete.Fire;Events.UnitMoveComplete.Fire=function(o,id,a,b) f(o,id,'42',b) end",
    "local f=Events.UnitMoveComplete.Fire;Events.UnitMoveComplete.Fire=function(o,id,a,b) f(o,id,42.5,b) end",
    "local f=Events.UnitMoveComplete.Fire;Events.UnitMoveComplete.Fire=function(o,id,a,b) f(o,id,math.huge,b) end",
    "local f=Events.UnitOperationDeactivated.Fire;Events.UnitOperationDeactivated.Fire=function(o,id,a,b) f(o,id,-448283625,b) end",
    "local f=Events.UnitOperationDeactivated.Fire;Events.UnitOperationDeactivated.Fire=function(o,id,a,b) f(o,id,a,0) end",
    "local f=Events.UnitOperationsCleared.Fire;Events.UnitOperationsCleared.Fire=function(o,id,a,b) f(o,id,1,0) end",
    "UnitManager.GetQueuedDestination=function() return 29042 end",
    "UnitManager.GetQueuedDestination=function() return false end",
    "UnitManager.GetQueuedDestination=function() error('queue unknown') end",
    "UnitManager.GetQueuedDestination=nil",
    "UI.IsGameCoreBusy=function() return submissions>0 end",
    "UI.IsProcessingMessages=function() return true end",
    "UI.IsProcessingMessages=function() return nil end",
    "UI.IsProcessingMessages=nil",
    "unit.GetOwner=function() return 1 end",
    "GameInfo.UnitOperations.UNITOPERATION_MOVE_TO.Index=nil",
    "GameInfo.UnitOperations.UNITOPERATION_MOVE_TO.Hash=1",
])
def test_incomplete_or_wrong_chain_never_confirms_position_change_alone(change):
    conn = InterruptedMoveConn()
    conn.lua.execute(change)
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    assert state.pending_action_summary is not None
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 43, 29))
    assert conn.lua.eval("submissions") == 1


@pytest.mark.parametrize("change,restore", [
    ("turn=70", "turn=69"),
    ("seed=1", "seed=-1896272000"),
    ("Game.GetLocalPlayer=function() return 1 end", "Game.GetLocalPlayer=function() return 0 end"),
    ("PlayerConfigurations[0].GetCivilizationTypeName=function() return 'CIVILIZATION_ROME' end",
     "PlayerConfigurations[0].GetCivilizationTypeName=function() return 'CIVILIZATION_SUMERIA' end"),
    ("GameConfiguration.GetValue=function() return nil end", "GameConfiguration.GetValue=function() return seed end"),
])
def test_callback_with_changed_or_unknown_scope_cannot_be_reused_after_scope_returns(change, restore):
    conn = InterruptedMoveConn()
    conn.lua.execute('''
local fire=Events.UnitMoveComplete.Fire
Events.UnitMoveComplete.Fire=function(...)
 CHANGE
 fire(unpack(arg))
 RESTORE
end
'''.replace("CHANGE", change).replace("RESTORE", restore))
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    assert conn.lua.eval("submissions") == 1


def test_native_exception_after_chain_does_not_prove_return_or_allow_replay():
    conn = InterruptedMoveConn()
    conn.lua.execute('''
UnitManager.RequestOperation=function(u,operation,params)
 submissions=submissions+1
 interruptedMove(u)
 error("native entry did not return")
end
''')
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.confirm_pending_action())
    assert conn.lua.eval("submissions") == 1


@pytest.mark.parametrize("change", [
    "__civmcpMoveSegment.token='old-request'",
    "__civmcpMoveSegment.fromY=26",
    "__civmcpMoveSegment.targetY=28",
    "__civmcpMoveSegment.owner=1",
    "__civmcpMoveSegment.id=851973",
    "__civmcpMoveSegment.entered=false",
    "__civmcpMoveSegment.moveX=nil",
    "x,y=41,27",  # No lasting displacement.
    "unit.GetID=function() return 851973 end",  # Same low index, new generation.
])
def test_original_request_entity_origin_and_intent_are_required(change):
    conn = InterruptedMoveConn()
    conn.lua.execute('''
local request=UnitManager.RequestOperation
UnitManager.RequestOperation=function(...)
 request(unpack(arg))
 CHANGE
end
'''.replace("CHANGE", change))
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    assert state.pending_action_summary is not None
    assert conn.lua.eval("submissions") == 1


def test_chain_fired_while_observers_are_arming_is_not_a_request_witness():
    conn = InterruptedMoveConn()
    conn.lua.execute('''
local add=Events.UnitOperationsCleared.Add
Events.UnitOperationsCleared.Add=function(handler)
 add(handler)
 interruptedMove(unit)
 x,y,moves=41,27,2
end
UnitManager.RequestOperation=function()
 submissions=submissions+1
 x,y,moves=42,27,1
end
''')
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    assert conn.lua.eval("submissions") == 1


def test_out_of_order_chain_cannot_be_completed_by_late_stale_events():
    conn = InterruptedMoveConn()
    conn.lua.execute('''
UnitManager.RequestOperation=function(u)
 submissions=submissions+1
 Events.UnitOperationAdded.Fire(0,u:GetID(),0,19)
 Events.UnitOperationDeactivated.Fire(0,u:GetID(),1477390184,1)
 x,y,moves=42,27,1
 Events.UnitMoveComplete.Fire(0,u:GetID(),42,27)
 Events.UnitOperationsCleared.Fire(0,u:GetID(),0,0)
end
''')
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    conn.lua.execute("interruptedMove(unit)")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.confirm_pending_action())
    assert conn.lua.eval("submissions") == 1


def test_delayed_chain_confirms_original_request_without_another_submission():
    conn = InterruptedMoveConn()
    conn.lua.execute("UnitManager.RequestOperation=function() submissions=submissions+1 end")
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    conn.lua.execute("interruptedMove(unit)")
    result = asyncio.run(state.move_unit(786437, 42, 29))
    assert "PATH_INTERRUPTED" in result and "now_at:42,27" in result
    assert state.pending_action_summary is None
    assert conn.lua.eval("submissions") == 1


class LostReplyConn(InterruptedMoveConn):
    def __init__(self, phase, error=ConnectionError):
        super().__init__()
        self.phase, self.error, self.lost = phase, error, False

    async def execute_write(self, code):
        rows = await super().execute_write(code)
        matches = ('UnitManager.RequestOperation(unit, UnitOperationTypes.MOVE_TO, params)' in code
                   if self.phase == "submit" else 'print("MOVE_INTERRUPTED|' in code)
        if matches and not self.lost:
            self.lost = True
            raise self.error("reply lost")
        return rows


@pytest.mark.parametrize("phase", ["submit", "confirm"])
def test_lost_reply_preserves_native_proof_and_only_confirms_same_request(phase):
    conn = LostReplyConn(phase)
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    assert conn.lua.eval("moveListenerCount()") == 4
    result = asyncio.run(state.move_unit(786437, 42, 29))
    assert "PATH_INTERRUPTED" in result and "now_at:42,27" in result
    assert state.pending_action_summary is None
    assert conn.lua.eval("submissions") == 1


@pytest.mark.parametrize("event", [
    "Events.UnitOperationAdded.Fire(0,786437,0,1)",
    "Events.UnitMoveComplete.Fire(0,786437,43,27)",
    "turn=70;Events.UnitOperationsCleared.Fire(0,786437,0,0);turn=69",
    "local read=GameConfiguration.GetValue;GameConfiguration.GetValue=function() return nil end;"
    "Events.UnitOperationsCleared.Fire(0,786437,0,0);GameConfiguration.GetValue=read",
])
def test_lost_confirmation_reply_retains_later_native_invalidations(event):
    conn = LostReplyConn("confirm")
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    conn.lua.execute(event)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    assert state.pending_action_summary is not None
    assert conn.lua.eval("submissions") == 1


def test_cancelled_confirmation_keeps_owner_and_does_not_replay_native_move():
    conn = LostReplyConn("confirm", asyncio.CancelledError)
    state = GameState(conn)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(state.move_unit(786437, 42, 29))
    assert state.pending_action_summary is not None
    assert "PATH_INTERRUPTED" in asyncio.run(state.confirm_pending_action())
    assert conn.lua.eval("submissions") == 1


def test_next_explicit_move_replaces_only_retired_native_observers():
    conn = InterruptedMoveConn()
    state = GameState(conn)
    assert "PATH_INTERRUPTED" in asyncio.run(state.move_unit(786437, 42, 29))
    token = conn.lua.globals().__civmcpMoveSegment.token
    conn.lua.execute('''
UnitManager.RequestOperation=function(u,operation,params)
 submissions=submissions+1
 x,y=params[2],params[3]
 Events.UnitOperationSegmentComplete.Fire(0,u:GetID())
end
''')
    result = asyncio.run(state.move_unit(786437, 43, 27))
    assert "now_at:43,27" in result
    assert conn.lua.eval("submissions") == 2
    assert conn.lua.eval("moveListenerCount()") == 4
    assert conn.lua.globals().__civmcpMoveSegment.token != token


@pytest.mark.parametrize("corrupt", [
    lambda row: None,
    lambda row: "MOVE_INTERRUPTED|truncated",
    lambda row: row.replace("entered:true", "entered:false"),
    lambda row: row.replace("returned:true", "returned:unknown"),
    lambda row: row.replace("chain:CLEARED", "chain:ADDED"),
    lambda row: row.replace("queue:nil", "queue:unknown"),
    lambda row: row.replace("|42|27|entered", "|NaN|27|entered"),
])
def test_incomplete_native_receipt_cannot_retire_owner(corrupt):
    class CorruptReplyConn(InterruptedMoveConn):
        async def execute_write(self, code):
            rows = await super().execute_write(code)
            result = []
            for row in rows:
                value = corrupt(row) if row.startswith("MOVE_INTERRUPTED|") else row
                if value is not None:
                    result.append(value)
            return result

    conn = CorruptReplyConn()
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    assert state.pending_action_summary is not None
    assert conn.lua.eval("submissions") == 1


@pytest.mark.parametrize("missing", [
    "UnitOperationAdded", "UnitMoveComplete", "UnitOperationDeactivated", "UnitOperationsCleared",
])
def test_unavailable_alternative_observer_keeps_existing_segment_confirmation(missing):
    conn = InterruptedMoveConn()
    conn.lua.execute(f"Events.{missing}.Add=function() error('observer unavailable') end")
    conn.lua.execute('''
UnitManager.GetQueuedDestination=function() return 29042 end
UnitManager.RequestOperation=function(u)
 submissions=submissions+1
 x,y,moves=42,27,1
 Events.UnitOperationSegmentComplete.Fire(0,u:GetID())
end
''')
    state = GameState(conn)
    result = asyncio.run(state.move_unit(786437, 42, 29))
    assert "native segment complete" in result and "now_at:42,27" in result
    assert conn.lua.eval("submissions") == 1
    assert conn.lua.eval("moveListenerCount()") == 0


def test_explicit_native_refusal_never_arms_or_submits_new_witness():
    conn = InterruptedMoveConn()
    conn.lua.execute("can_target=false")
    state = GameState(conn)
    assert asyncio.run(state.move_unit(786437, 42, 29)).startswith("Error: CANNOT_MOVE_TARGET|")
    assert state.pending_action_summary is None
    assert conn.lua.eval("submissions") == conn.lua.eval("moveListenerCount()") == 0


@pytest.mark.parametrize("fallback", [
    "queued=29042;Events.UnitOperationSegmentComplete.Fire(0,786437)",
    "x,y=42,29",  # Reaching the original destination cannot erase invalidation.
    "moves=0",  # Ordinary exhaustion.
    "unit.GetMovementMovesRemaining=function() return 0 end",  # Effective exhaustion.
    "unit.HasMovedIntoZOC=function() return true end",
])
def test_observed_scope_change_cannot_confirm_through_old_paths_after_scope_returns(fallback):
    conn = InterruptedMoveConn()
    conn.lose_observation = True
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    conn.lua.execute('''
seed=1
Events.UnitMoveComplete.Fire(0,786437,42,27)
seed=-1896272000
''')
    conn.lua.execute(fallback)
    result = asyncio.run(state.move_unit(786437, 42, 29))
    assert "OUTCOME_UNKNOWN" in result
    assert state.pending_action_summary is not None
    assert conn.lua.eval("submissions") == 1


def test_alternative_chain_mismatch_does_not_invalidate_an_independent_valid_segment():
    conn = InterruptedMoveConn()
    conn.lose_observation = True
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    conn.lua.execute('''
Events.UnitMoveComplete.Fire(0,786437,43,27)
queued=29042
Events.UnitOperationSegmentComplete.Fire(0,786437)
''')
    result = asyncio.run(state.move_unit(786437, 42, 29))
    assert "native segment complete" in result and "now_at:42,27" in result
    assert state.pending_action_summary is None
    assert conn.lua.eval("submissions") == 1


def test_scope_change_before_response_complete_blocks_previously_emitted_interruption_proof():
    conn = InterruptedMoveConn()
    conn.lua.execute('''
local emit=print
print=function(row)
 emit(row)
 if tostring(row):find("^MOVE_INTERRUPTED|") then seed=1 end
end
''')
    state = GameState(conn)
    result = asyncio.run(state.move_unit(786437, 42, 29))
    assert "OUTCOME_UNKNOWN" in result
    conn.lua.execute("seed=-1896272000;queued=29042")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    assert conn.lua.eval("submissions") == 1


def test_temporarily_unknown_query_scope_does_not_latch_known_scope_invalidation():
    conn = InterruptedMoveConn()
    conn.lose_observation = True
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(786437, 42, 29))
    conn.lua.execute("GameConfiguration.GetValue=function() return nil end")
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.confirm_pending_action())
    conn.lua.execute("GameConfiguration.GetValue=function() return seed end")
    assert "PATH_INTERRUPTED" in asyncio.run(state.confirm_pending_action())
    assert conn.lua.eval("submissions") == 1
