"""Move target decisions through GameState and real Lua, with an isolated engine."""

import asyncio

import pytest

from civ_mcp.game_state import GameState
from test_action_lua_execution import MovementConn


class TargetMovementConn(MovementConn):
    def __init__(self):
        super().__init__()
        self.lose_observation = False
        self.lua.execute('''
can_target, war, perform_move = false, false, true
checks, target_checks = {}, 0
UnitOperationTypes.PARAM_MODIFIERS=4
UnitOperationMoveModifiers={NONE=0, ATTACK=8}
unit.GetMovesRemaining=function() return 3 end
unit.GetMovementMovesRemaining=function() return 3 end
Players[0].GetDiplomacy=function() return {
    IsAtWarWith=function(_, owner) return war end,
} end
UnitManager.CanStartOperation=function(u, operation, exclusions, params)
    assert(u==unit and operation==UnitOperationTypes.MOVE_TO and exclusions==nil)
    if type(params)~="table" then return true end
    target_checks=target_checks+1
    checked_params=params
    table.insert(checks,{x=params[2],y=params[3],modifiers=params[4]})
    return can_target
end
UnitManager.RequestOperation=function(u, operation, params)
    submissions=submissions+1
    submitted_params=params
    if perform_move then
        x,y=params[2],params[3]
        Events.UnitOperationSegmentComplete.Fire(0,u:GetID())
    end
end
''')


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.mark.parametrize("after_path", [
    'return {plots={17019,17020,destination},turns={1,1,3}}',
    'return {plots={17019,destination},turns={1,3}}',
    'return {plots={},turns={}}',
    'return nil',
    'return {plots="malformed",turns={}}',
    'error("route changed after segment completion")',
])
def test_completed_segment_confirms_only_the_partial_move_independently_of_a_new_path(after_path):
    conn = TargetMovementConn()
    conn.lua.execute('''
x,y=20,17
can_target=true
UnitManager.GetQueuedDestination=function() return 21019 end
UnitManager.RequestOperation=function(u, operation, params)
    submissions=submissions+1
    x,y=19,17
    unit.GetMovesRemaining=function() return 1 end
    unit.GetMovementMovesRemaining=function() return 1 end
    Events.UnitOperationSegmentComplete.Fire(0,u:GetID())
end
UnitManager.GetMoveToPathEx=function(u,destination)
    if submissions==0 then return {plots={17020,17019,destination},turns={1,1,3}} end
    AFTER_PATH
end
'''.replace('AFTER_PATH', after_path))
    state = GameState(conn)
    result = run(state.move_unit(6, 19, 21))
    assert "now_at:19,17" in result and "moves_remaining:1" in result
    assert "STOPPED_MID_PATH (native segment complete)" in result
    assert "now_at:19,21" not in result and "requires later turn" not in result
    assert state.pending_action_summary is None
    assert conn.lua.globals().submissions == 1
    assert conn.lua.globals().__civmcpMoveSegment.closed


@pytest.fixture
def short_confirmation(monkeypatch):
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_TIMEOUT", 0.015)
    monkeypatch.setattr("civ_mcp.game_state.ACTION_CONFIRM_INTERVAL", 0.001)


@pytest.mark.parametrize("completion", [
    '',
    'Events.UnitOperationSegmentComplete.Fire(1,u:GetID())',
    'Events.UnitOperationSegmentComplete.Fire(0,917510)',
    'turn=55; Events.UnitOperationSegmentComplete.Fire(0,u:GetID()); turn=54',
    'Events.UnitOperationSegmentComplete.Fire(0,u:GetID()); x,y=20,17',
    'Events.UnitOperationSegmentComplete.Fire(0,u:GetID()); UI.IsGameCoreBusy=function() return true end',
    'Events.UnitOperationSegmentComplete.Fire(0,u:GetID()); UI.IsGameCoreBusy=function() error("busy unknown") end',
    'Events.UnitOperationSegmentComplete.Fire(0,u:GetID()); UnitManager.GetQueuedDestination=nil',
    'Events.UnitOperationSegmentComplete.Fire(0,u:GetID()); UnitManager.GetQueuedDestination=function() return 21018 end',
    'Events.UnitOperationSegmentComplete.Fire(0,u:GetID()); UnitManager.GetQueuedDestination=function() error("queue unavailable") end',
    'Events.UnitOperationSegmentComplete.Fire(0,u:GetID()); __civmcpMoveSegment.token="stale-token"',
    'Events.UnitOperationSegmentComplete.Fire(0,u:GetID()); __civmcpMoveSegment.fromX=18',
    'Events.UnitOperationSegmentComplete.Fire(0,u:GetID()); __civmcpMoveSegment.targetX=18',
    'Events.UnitOperationSegmentComplete.Fire(0,u:GetID()); seed=1',
])
def test_incomplete_native_segment_facts_keep_the_gate_and_do_not_replay(completion, short_confirmation):
    conn = TargetMovementConn()
    conn.lua.execute('''
x,y=20,17
can_target=true
UnitManager.GetQueuedDestination=function() return 21019 end
UnitManager.RequestOperation=function(u, operation, params)
    submissions=submissions+1
    x,y=19,17
    COMPLETION
end
'''.replace('COMPLETION', completion))
    state = GameState(conn)
    result = run(state.move_unit(6, 19, 21))
    assert "OUTCOME_UNKNOWN" in result
    assert "STOPPED_MID_PATH" not in result
    assert state.pending_action_summary is not None
    assert "OUTCOME_UNKNOWN" in run(state.move_unit(6, 21, 17))
    assert state.pending_action_summary is not None
    assert conn.lua.globals().submissions == 1


def test_target_rejected_before_request_or_witness_and_next_legal_move_remains_available(short_confirmation):
    conn = TargetMovementConn()
    state = GameState(conn)
    result = run(state.move_unit(6, 23, 20))
    assert result.startswith("Error: CANNOT_MOVE_TARGET|"), result
    native = conn.lua.globals()
    assert native.submissions == 0 and native.listenerCount() == 1
    assert state.pending_action_summary is None
    native.can_target = True
    result = run(state.move_unit(6, 20, 17))
    assert "now_at:20,17" in result
    assert native.submissions == 1 and state.pending_action_summary is None


@pytest.mark.parametrize("owner, at_war, modifier", [(0, False, None), (3, False, None), (2, True, 8)])
def test_only_a_native_war_target_adds_attack_and_the_final_parameters_are_validated(owner, at_war, modifier):
    conn = TargetMovementConn()
    conn.lua.globals().war = at_war
    conn.lua.globals().occupant_owner = owner
    conn.lua.execute('''
can_target=true
GameInfo.Units[2]={UnitType="UNIT_BUILDER",FormationClass="FORMATION_CLASS_CIVILIAN"}
local occupant={GetOwner=function() return occupant_owner end,GetType=function() return 2 end}
Map.GetUnitsAt=function() return {Units=function()
    local used=false
    return function() if not used then used=true; return occupant end end
end} end
''')
    result = run(GameState(conn).move_unit(6, 20, 17))
    native = conn.lua.globals()
    assert "now_at:20,17" in result
    assert native.target_checks == native.submissions == 1
    check = native.checks[1]
    assert (check.x, check.y, check.modifiers) == (20, 17, modifier)
    assert conn.lua.eval("checked_params==submitted_params")
    assert ("CAPTURE_MOVE" in result) == at_war


def test_unknown_move_in_allied_territory_does_not_invent_a_missing_open_borders_reason(short_confirmation):
    conn = TargetMovementConn()
    conn.lua.execute('''
can_target,perform_move=true,false
Players[3]={IsMajor=function() return true end}
Players[0].GetDiplomacy=function() return {
    IsAtWarWith=function() return false end,
    HasOpenBordersFrom=function() return true end,
} end
plot_owner=3
''')
    state = GameState(conn)
    result = run(state.move_unit(6, 23, 20))
    assert "OUTCOME_UNKNOWN" in result
    assert "foreign territory" in result
    assert "need Open Borders" not in result and "need suzerainty" not in result
    assert state.pending_action_summary is not None
    assert conn.lua.globals().submissions == 1


@pytest.mark.parametrize("setup", [
    'UnitManager.CanStartOperation=nil',
    'UnitManager.CanStartOperation=function() error("native eligibility unavailable") end',
])
def test_unavailable_movement_eligibility_is_zero_submit_and_does_not_latch_unknown(setup):
    conn = TargetMovementConn()
    conn.lua.execute(setup)
    state = GameState(conn)
    result = run(state.move_unit(6, 20, 17))
    assert result.startswith("Error: CANNOT_MOVE|"), result
    assert state.pending_action_summary is None
    assert conn.lua.globals().submissions == 0 and conn.lua.globals().listenerCount() == 1


@pytest.mark.parametrize("outcome", ['false', 'nil', '1', '"true"', 'error("target read unavailable")'])
def test_target_eligibility_requires_explicit_native_true_before_any_witness(outcome):
    conn = TargetMovementConn()
    conn.lua.execute('''
UnitManager.CanStartOperation=function(_,_,_,params)
    if type(params)~="table" then return true end
    TARGET_OUTCOME
end
'''.replace('TARGET_OUTCOME', outcome if outcome.startswith('error(') else 'return '+outcome))
    state = GameState(conn)
    result = run(state.move_unit(6, 20, 17))
    assert result.startswith("Error: CANNOT_MOVE_TARGET|"), result
    assert state.pending_action_summary is None
    assert conn.lua.globals().submissions == 0 and conn.lua.globals().listenerCount() == 1


@pytest.mark.parametrize("outcome", ['nil', '1', 'error("diplomacy unavailable")'])
def test_unknown_foreign_war_relationship_rejects_without_guessing_or_submitting(outcome):
    conn = TargetMovementConn()
    conn.lua.execute('''
can_target=true
local occupant={GetOwner=function() return 3 end}
Map.GetUnitsAt=function() return {Units=function()
    local used=false
    return function() if not used then used=true; return occupant end end
end} end
Players[0].GetDiplomacy=function() return {IsAtWarWith=function()
    WAR_OUTCOME
end} end
'''.replace('WAR_OUTCOME', outcome if outcome.startswith('error(') else 'return '+outcome))
    state = GameState(conn)
    result = run(state.move_unit(6, 20, 17))
    assert result.startswith("Error: MOVE_RELATION_UNKNOWN|"), result
    assert state.pending_action_summary is None
    assert conn.lua.globals().submissions == conn.lua.globals().target_checks == 0
    assert conn.lua.globals().listenerCount() == 1


@pytest.mark.parametrize("setup, error", [
    ('unit.GetMovesRemaining=function() return 0 end', 'NO_MOVES'),
    ('UnitManager.CanStartOperation=function() return false end', 'CANNOT_MOVE'),
    ('''local occupant={GetOwner=function() return 0 end,GetType=function() return 1 end}
Map.GetUnitsAt=function() return {Units=function()
    local used=false
    return function() if not used then used=true; return occupant end end
end} end''', 'STACKING_CONFLICT'),
])
def test_existing_no_moves_generic_refusal_and_own_stacking_guards_still_prevent_submission(setup, error):
    conn = TargetMovementConn()
    conn.lua.execute(setup)
    state = GameState(conn)
    result = run(state.move_unit(6, 20, 17))
    assert result.startswith(f"Error: {error}|"), result
    assert state.pending_action_summary is None
    assert conn.lua.globals().submissions == conn.lua.globals().target_checks == 0
    assert conn.lua.globals().listenerCount() == 1


@pytest.mark.parametrize("target", [(20, 17), (50, 30)])
@pytest.mark.parametrize("visible", [True, False])
def test_current_turn_native_paths_allow_near_far_and_fog_targets(target, visible):
    conn = TargetMovementConn()
    conn.lua.globals().visible=visible
    conn.lua.execute('''
can_target=true
PlayersVisibility[0].IsVisible=function() return visible end
UnitManager.GetMoveToPathEx=function(u,destination)
    return {plots={u:GetX()+1000*u:GetY(),destination},turns={1,1}}
end
''')
    state = GameState(conn)
    result = run(state.move_unit(6, *target))
    assert f"now_at:{target[0]},{target[1]}" in result
    assert state.pending_action_summary is None
    native=conn.lua.globals()
    assert native.submissions == native.target_checks == 1
    assert (native.checks[1].x,native.checks[1].y)==target


def test_submitted_without_completion_stays_unknown_when_target_and_current_path_later_disappear(short_confirmation):
    conn = TargetMovementConn()
    conn.lua.execute('can_target,perform_move=true,false')
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in run(state.move_unit(6, 23, 20))
    assert state.pending_action_summary is not None
    conn.lua.execute('''
can_target=false
path_reads=0
Map.GetPlot=function() return {GetIndex=function() return 40 end} end
UnitManager.GetMoveToPathEx=function()
    path_reads=path_reads+1
    return {plots={},turns={}}
end
''')
    # Same intent performs only confirmation, and a different intent is gated.
    assert "OUTCOME_UNKNOWN" in run(state.move_unit(6, 23, 20))
    assert "OUTCOME_UNKNOWN" in run(state.move_unit(6, 21, 17))
    native=conn.lua.globals()
    assert state.pending_action_summary is not None
    assert native.submissions == native.target_checks == 1
    assert native.path_reads >= 2
    assert native.listenerCount() == 2



def test_visible_target_with_explicit_empty_native_path_is_refused_without_request_or_listener():
    conn = TargetMovementConn()
    conn.lua.execute('can_target=true; UnitManager.GetMoveToPathEx=function() return {plots={},turns={}} end')
    state = GameState(conn)
    result = run(state.move_unit(6, 23, 20))
    assert result.startswith("Error: NO_PATH|"), result
    assert state.pending_action_summary is None
    native=conn.lua.globals()
    assert native.submissions == 0 and native.listenerCount() == 1


def test_native_path_for_a_different_source_cannot_authorize_this_move():
    conn = TargetMovementConn()
    conn.lua.execute('can_target=true; UnitManager.GetMoveToPathEx=function() return {plots={17018,17020},turns={1,1}} end')
    state = GameState(conn)
    result = run(state.move_unit(6, 20, 17))
    assert result.startswith("Error: PATH_UNKNOWN|"), result
    assert state.pending_action_summary is None
    assert conn.lua.globals().submissions == 0 and conn.lua.globals().listenerCount() == 1


@pytest.mark.parametrize("setup", [
    'UnitManager.GetMoveToPathEx=nil',
    'UnitManager.GetMoveToPathEx=function() error("path unavailable") end',
    'UnitManager.GetMoveToPathEx=function() return nil end',
    'UnitManager.GetMoveToPathEx=function() return false end',
    'UnitManager.GetMoveToPathEx=function() return {} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={},turns={1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={17019,17020}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots="unknown",turns={}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={[1]=17019,[3]=17020},turns={[1]=1,[3]=1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={[0]=17019,[1]=17020},turns={[0]=1,[1]=1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={17019,17020,extra=17021},turns={1,1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={17019,17021},turns={1,1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={17019},turns={1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={17019,17020.5},turns={1,1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={17019,math.huge},turns={1,1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={17019,17020},turns={1,"2"}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={17019,17020},turns={1,0/0}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={17019,17020},turns={1,math.huge}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={17019,17020},turns={2,1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={17019,17020},turns={2,2}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={17019,17019,17020},turns={1,1,2}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={17019,17019},turns={1,1}} end',
    'UnitManager.GetMoveToPathEx=function() return {plots={17019,17020},turns={1,0}} end',
    'Map.GetPlot=function() return nil end',
    'Map.GetPlotByIndex=function() return nil end',
    'Map.GetPlotByIndex=function() error("missing native plot") end',
    'UI.IsGameCoreBusy=function() return true end',
    'UI.IsGameCoreBusy=function() return nil end',
    'UI.IsGameCoreBusy=function() error("busy state unknown") end',
])
def test_unavailable_or_malformed_native_paths_are_unknown_and_never_submit(setup):
    conn = TargetMovementConn()
    conn.lua.execute('can_target=true; '+setup)
    state = GameState(conn)
    result = run(state.move_unit(6, 20, 17))
    assert result.startswith("Error: PATH_UNKNOWN|"), result
    assert state.pending_action_summary is None
    native=conn.lua.globals()
    assert native.submissions == 0 and native.listenerCount() == 1


@pytest.mark.parametrize("visibility", ['false', 'nil', '"true"', 'error("visibility unknown")'])
def test_empty_path_without_confirmed_current_visibility_is_unknown_zero_submit(visibility):
    conn = TargetMovementConn()
    conn.lua.execute('''
can_target=true
UnitManager.GetMoveToPathEx=function() return {plots={},turns={}} end
PlayersVisibility[0].IsVisible=function() VISIBILITY end
'''.replace('VISIBILITY',visibility if visibility.startswith('error(') else 'return '+visibility))
    state = GameState(conn)
    result = run(state.move_unit(6, 20, 17))
    assert result.startswith("Error: PATH_UNKNOWN|"), result
    assert state.pending_action_summary is None
    native=conn.lua.globals()
    assert native.submissions == 0 and native.listenerCount() == 1


def test_missing_middle_plot_cannot_authorize_an_otherwise_matching_path():
    conn = TargetMovementConn()
    conn.lua.execute('''
can_target=true
UnitManager.GetMoveToPathEx=function() return {plots={17019,17018,17020},turns={1,1,2}} end
local original=Map.GetPlotByIndex
Map.GetPlotByIndex=function(index)
    if index~=17018 then return original(index) end
end
''')
    state = GameState(conn)
    result = run(state.move_unit(6, 20, 17))
    assert result.startswith("Error: PATH_UNKNOWN|"), result
    assert state.pending_action_summary is None
    assert conn.lua.globals().submissions == 0 and conn.lua.globals().listenerCount() == 1


def test_recorded_207_empty_city_path_is_rejected_then_native_neighbour_and_fog_paths_still_move():
    conn = TargetMovementConn()
    conn.lua.execute('''
x,y=23,22
can_target=true
local function plot(px,py)
    return {GetIndex=function() return px+60*py end,
            GetX=function() return px end,GetY=function() return py end,
            GetOwner=function() return 3 end,
            IsWater=function() return false end,IsMountain=function() return false end,
            IsImpassable=function() return false end}
end
Map.GetPlot=plot
Map.GetPlotByIndex=function(index) return plot(index%60,math.floor(index/60)) end
PlayersVisibility[0].IsVisible=function(_,px,py) return px~=24 end
UnitManager.GetMoveToPathEx=function(u,destination)
    if destination==1223 then return {plots={},turns={}} end
    if destination==1283 then return {plots={1343,1283},turns={1,1}} end
    return {plots={1343,1283,1224},turns={1,1,1}}
end
''')
    state = GameState(conn)
    result = run(state.move_unit(6, 23, 20))
    assert result.startswith("Error: NO_PATH|"), result
    assert state.pending_action_summary is None
    assert conn.lua.globals().submissions == 0 and conn.lua.globals().listenerCount() == 1
    assert "now_at:23,21" in run(state.move_unit(6, 23, 21))
    # Reset the isolated native fixture to reuse the recorded fog origin.
    conn.lua.execute('x,y=23,22')
    assert "now_at:24,20" in run(state.move_unit(6, 24, 20))
    assert state.pending_action_summary is None
    assert conn.lua.globals().submissions == 2 and conn.lua.globals().listenerCount() == 1


def test_recorded_turn66_partial_move_then_future_step_defers_without_replacing_the_old_queue(short_confirmation):
    conn = TargetMovementConn()
    conn.lua.execute('''
x,y=18,19
can_target=true
movement_left=2
unit.GetMovesRemaining=function() return movement_left end
unit.GetMovementMovesRemaining=function() return movement_left end
queued_destination=19016
UnitManager.GetQueuedDestination=function() return queued_destination end
UnitManager.GetMoveToPathEx=function(u,destination)
    if x==18 then return {plots={19018,19017,destination},turns={1,1,2}} end
    return {plots={19017,destination},turns={1,2}}
end
UnitManager.RequestOperation=function(u,operation,params)
    submissions=submissions+1
    queued_destination=params[2]+1000*params[3]
    x,y=17,19
    movement_left=1
    Events.UnitOperationSegmentComplete.Fire(0,u:GetID())
end
''')
    state = GameState(conn)
    first = run(state.move_unit(6, 16, 19))
    assert "now_at:17,19" in first and "native segment complete" in first
    native = conn.lua.globals()
    old_witness = native.__civmcpMoveSegment
    assert old_witness.closed and state.pending_action_summary is None
    conn.lua.execute("old_witness=__civmcpMoveSegment")

    second = run(state.move_unit(6, 16, 19))
    assert second.startswith("Error: MOVE_DEFERRED|"), second
    assert "no move submitted" in second and "existing orders unchanged" in second
    assert native.submissions == 1 and native.listenerCount() == 1
    assert conn.lua.eval("__civmcpMoveSegment==old_witness")
    assert native.queued_destination == 19016 and state.pending_action_summary is None

    # The rejected second intent must leave the ordinary action gate usable.
    conn.lua.execute('''
UnitManager.GetMoveToPathEx=function(u,destination)
    return {plots={u:GetX()+1000*u:GetY(),destination},turns={1,1}}
end
UnitManager.RequestOperation=function(u,operation,params)
    submissions=submissions+1
    x,y=params[2],params[3]
    queued_destination=params[2]+1000*params[3]
    Events.UnitOperationSegmentComplete.Fire(0,u:GetID())
end
''')
    assert "now_at:18,19" in run(state.move_unit(6, 18, 19))
    assert native.submissions == 2 and state.pending_action_summary is None


@pytest.mark.parametrize("target", [(20, 17), (50, 30)])
@pytest.mark.parametrize("visible", [True, False])
def test_first_future_native_step_defers_near_far_and_fog_destinations_without_a_request(target, visible):
    conn = TargetMovementConn()
    conn.lua.globals().visible = visible
    conn.lua.execute("""
can_target=true
PlayersVisibility[0].IsVisible=function() return visible end
UnitManager.GetMoveToPathEx=function(u,destination)
    return {plots={u:GetX()+1000*u:GetY(),destination},turns={1,4}}
end
""")
    state = GameState(conn)
    result = run(state.move_unit(6, *target))
    native = conn.lua.globals()
    assert result.startswith("Error: MOVE_DEFERRED|"), result
    assert "no move submitted" in result
    assert state.pending_action_summary is None
    assert native.submissions == 0 and native.listenerCount() == 1
    assert native.__civmcpMoveSegment is None


@pytest.mark.parametrize("last_turn", [2, 4])
@pytest.mark.parametrize("remaining_moves", [1, 2])
@pytest.mark.parametrize("visible", [True, False])
def test_first_current_step_allows_future_destination_and_confirms_positive_remaining_move_segment(last_turn, remaining_moves, visible):
    conn = TargetMovementConn()
    conn.lua.globals().last_turn = last_turn
    conn.lua.globals().remaining_moves = remaining_moves
    conn.lua.globals().visible = visible
    conn.lua.execute("""
can_target=true
PlayersVisibility[0].IsVisible=function() return visible end
UnitManager.GetQueuedDestination=function() return 17021 end
UnitManager.GetMoveToPathEx=function(u,destination)
    if x==19 then return {plots={17019,17020,destination},turns={1,1,last_turn}} end
    return {plots={17020,destination},turns={1,last_turn}}
end
UnitManager.RequestOperation=function(u,operation,params)
    submissions=submissions+1
    x,y=20,17
    unit.GetMovesRemaining=function() return remaining_moves end
    unit.GetMovementMovesRemaining=function() return remaining_moves end
    Events.UnitOperationSegmentComplete.Fire(0,u:GetID())
end
""")
    state = GameState(conn)
    result = run(state.move_unit(6, 21, 17))
    native = conn.lua.globals()
    assert "now_at:20,17" in result and "native segment complete" in result
    assert f"moves_remaining:{remaining_moves}" in result
    assert "now_at:21,17" not in result
    assert state.pending_action_summary is None
    assert native.submissions == 1 and native.__civmcpMoveSegment.closed


def test_lost_ack_for_a_real_partial_segment_confirms_the_old_intent_before_future_path_preflight():
    class LostMoveAckConn(TargetMovementConn):
        async def execute_write(self, code):
            rows = await super().execute_write(code)
            if 'RequestOperation' in code:
                raise ConnectionError("lost move acknowledgement")
            return rows

    conn = LostMoveAckConn()
    conn.lua.execute("""
can_target=true
UnitManager.GetQueuedDestination=function() return 17021 end
UnitManager.GetMoveToPathEx=function(u,destination)
    if x==19 then return {plots={17019,17020,destination},turns={1,1,2}} end
    return {plots={17020,destination},turns={1,2}}
end
UnitManager.RequestOperation=function(u,operation,params)
    submissions=submissions+1
    x,y=20,17
    unit.GetMovesRemaining=function() return 1 end
    Events.UnitOperationSegmentComplete.Fire(0,u:GetID())
end
""")
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in run(state.move_unit(6, 21, 17))
    assert state.pending_action_summary is not None
    result = run(state.move_unit(6, 21, 17))
    assert "native segment complete" in result and "now_at:20,17" in result
    assert "MOVE_DEFERRED" not in result
    assert state.pending_action_summary is None
    assert conn.lua.globals().submissions == 1


def test_submitted_no_displacement_with_a_future_queue_keeps_pending_even_with_native_segment_event(short_confirmation):
    conn = TargetMovementConn()
    conn.lua.execute('''
can_target=true
UnitManager.GetQueuedDestination=function() return 17021 end
UnitManager.GetMoveToPathEx=function(u,destination)
    if submissions==0 then return {plots={17019,17020,destination},turns={1,1,2}} end
    return {plots={17019,destination},turns={1,2}}
end
UnitManager.RequestOperation=function(u,operation,params)
    submissions=submissions+1
    Events.UnitOperationSegmentComplete.Fire(0,u:GetID())
end
''')
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in run(state.move_unit(6, 21, 17))
    native = conn.lua.globals()
    conn.lua.execute('old_witness=__civmcpMoveSegment')
    assert native.__civmcpMoveSegment.done and not native.__civmcpMoveSegment.closed
    # A future preflight can reject a new request only; it cannot clear an
    # already submitted intent that never produced displacement evidence.
    assert "OUTCOME_UNKNOWN" in run(state.move_unit(6, 21, 17))
    assert "OUTCOME_UNKNOWN" in run(state.move_unit(6, 20, 17))
    assert state.pending_action_summary is not None
    assert native.submissions == native.target_checks == 1
    assert conn.lua.eval('__civmcpMoveSegment==old_witness')
