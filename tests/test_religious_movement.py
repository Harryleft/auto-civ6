"""Religious-layer movement through GameState with generated Lua executed."""

import pytest

from civ_mcp import server
from civ_mcp.game_state import GameState
from test_server import make_ctx
from test_move_target_lua import TargetMovementConn, run, short_confirmation


def religious_mover():
    conn = TargetMovementConn()
    conn.lua.execute('''
x,y,turn=35,24,139
unit.GetID=function() return 3145737 end
unit.GetOwner=function() return 0 end
GameInfo.Units[1]={UnitType="UNIT_APOSTLE",FormationClass="FORMATION_CLASS_CIVILIAN",
 TrackReligion=true,ReligiousStrength=110}
GameInfo.Units[2]={UnitType="UNIT_BUILDER",FormationClass="FORMATION_CLASS_CIVILIAN",
 TrackReligion=false,ReligiousStrength=0}
local builder={GetOwner=function() return 0 end,GetID=function() return 3211267 end,
 GetType=function() return 2 end,GetX=function() return 35 end,GetY=function() return 22 end}
builder_unit=builder
Map.GetUnitsAt=function() return {Units=function()
 local used=false
 return function() if not used then used=true;return builder end end
end} end
-- TunerMapPanel.lua explicitly identifies GameCore's religious layer as 3.
-- Reproduce actual #20 source/intermediate/destination path and #18 city builder.
local function plot(px,py)
 return {GetIndex=function() return px+60*py end,GetX=function() return px end,
 GetY=function() return py end,GetOwner=function() return 0 end,
 IsWater=function() return false end,IsMountain=function() return false end,
 IsImpassable=function() return false end}
end
Map.GetPlot=plot
Map.GetPlotByIndex=function(i) return plot(i%60,math.floor(i/60)) end
Units={GetUnitsInPlotLayerID=function(p,layer)
 assert(layer==3)
 if p:GetX()==x and p:GetY()==y then return {unit} end
 return {}
end}
PlayersVisibility[0].IsUnitVisible=function() return true end
can_target=true
UnitManager.GetMoveToPathEx=function(_,destination)
 assert(destination==1355)
 return {plots={1475,1415,1355},turns={1,1,1}}
end
''')
    return conn


def test_native_religious_layer_can_enter_builder_city_when_native_path_and_qualification_allow():
    conn = religious_mover()
    state = GameState(conn)
    result = run(server.unit_action(make_ctx(game=state), 3145737, "move", 35, 22))
    assert "now_at:35,22" in result, result
    assert conn.lua.globals().submissions == conn.lua.globals().target_checks == 1
    assert conn.lua.eval("checked_params==submitted_params")
    assert conn.lua.eval("builder_unit:GetX()==35 and builder_unit:GetY()==22")
    assert state.pending_action_summary is None


@pytest.mark.parametrize("owner", [0, 2])
def test_actual_same_religious_layer_occupant_cannot_be_bypassed_by_civilian_formation(owner):
    conn = religious_mover()
    conn.lua.globals().target_owner = owner
    conn.lua.execute('''
local source=Units.GetUnitsInPlotLayerID
Units.GetUnitsInPlotLayerID=function(p,layer)
 if p:GetX()==35 and p:GetY()==22 then
  return {{GetID=function() return 999003 end,GetOwner=function() return target_owner end,
   GetX=function() return 35 end,GetY=function() return 22 end}}
 end
 return source(p,layer)
end
''')
    state = GameState(conn)
    result = run(state.move_unit(3145737, 35, 22))
    assert result.startswith("Error: STACKING_CONFLICT|"), result
    assert conn.lua.globals().submissions == conn.lua.globals().target_checks == 0
    assert state.pending_action_summary is None
    assert conn.lua.globals().__civmcpMoveSegment is None


@pytest.mark.parametrize("setup", [
    "Units=nil",
    "Units.GetUnitsInPlotLayerID=nil",
    "Units.GetUnitsInPlotLayerID=function() error('unavailable') end",
    "Units.GetUnitsInPlotLayerID=function() return nil end",
    "Units.GetUnitsInPlotLayerID=function() return {} end",
    "Units.GetUnitsInPlotLayerID=function() return {unit,extra=unit} end",
    "Units.GetUnitsInPlotLayerID=function() return {[2]=unit} end",
    "local original=Units.GetUnitsInPlotLayerID; Units.GetUnitsInPlotLayerID=function(p,l) "
    "if p:GetY()==24 then return {unit,unit} end; return original(p,l) end",
    "unit.GetOwner=function() return 1 end",
    "unit.GetOwner=function() return nil end",
    "unit.GetOwner=function() return '0' end",
    "local original=Units.GetUnitsInPlotLayerID; Units.GetUnitsInPlotLayerID=function(p,l) "
    "if p:GetY()==22 then return false end; return original(p,l) end",
    "local original=Units.GetUnitsInPlotLayerID; Units.GetUnitsInPlotLayerID=function(p,l) "
    "if p:GetY()==22 then return {builder_unit} end; return original(p,l) end; "
    "builder_unit.GetX=function() return 34 end",
])
def test_unknown_native_layer_never_becomes_permission_to_share_builder_city(setup):
    conn = religious_mover()
    conn.lua.execute(setup)
    state = GameState(conn)
    result = run(state.move_unit(3145737, 35, 22))
    assert result.startswith("Error: MOVE_LAYER_UNKNOWN|"), result
    assert conn.lua.globals().submissions == conn.lua.globals().target_checks == 0
    assert state.pending_action_summary is None
    assert conn.lua.globals().__civmcpMoveSegment is None


@pytest.mark.parametrize("setup,error", [
    ("can_target=false", "CANNOT_MOVE_TARGET"),
    ("UnitManager.GetMoveToPathEx=function() return {plots={},turns={}} end", "NO_PATH"),
    ("UnitManager.GetMoveToPathEx=function() return nil end", "PATH_UNKNOWN"),
    ("UnitManager.GetMoveToPathEx=function() return {plots={1475,1415,1355},turns={1,2,2}} end", "MOVE_DEFERRED"),
    ("Players[0].GetDiplomacy=function() return {IsAtWarWith=function() return nil end} end; "
     "builder_unit.GetOwner=function() return 2 end", "MOVE_RELATION_UNKNOWN"),
])
def test_religious_layer_compatibility_does_not_skip_native_legality_path_or_diplomacy(setup, error):
    conn = religious_mover()
    conn.lua.execute(setup)
    state = GameState(conn)
    result = run(state.move_unit(3145737, 35, 22))
    assert result.startswith(f"Error: {error}|"), result
    assert conn.lua.globals().submissions == 0
    assert state.pending_action_summary is None
    assert conn.lua.globals().__civmcpMoveSegment is None


def test_lost_religious_move_ack_confirms_original_without_replaying_or_rereading_new_layer():
    conn = religious_mover()
    original_write = conn.execute_write

    async def lost_ack(code):
        rows = await original_write(code)
        if "RequestOperation" in code:
            raise ConnectionError("lost religious move acknowledgement")
        return rows

    conn.execute_write = lost_ack
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in run(state.move_unit(3145737, 35, 22))
    assert state.pending_action_summary is not None
    conn.lua.execute("Units=nil; can_target=false")
    result = run(state.move_unit(3145737, 35, 22))
    assert "now_at:35,22" in result, result
    assert conn.lua.globals().submissions == 1
    assert state.pending_action_summary is None


def test_unconfirmed_religious_move_keeps_single_owner_when_native_layer_later_changes(short_confirmation):
    conn = religious_mover()
    conn.lua.execute("perform_move=false")
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in run(state.move_unit(3145737, 35, 22))
    conn.lua.execute("Units=nil; can_target=false")
    assert "OUTCOME_UNKNOWN" in run(state.move_unit(3145737, 35, 22))
    assert "OUTCOME_UNKNOWN" in run(state.move_unit(3145737, 34, 24))
    assert conn.lua.globals().submissions == conn.lua.globals().target_checks == 1
    assert state.pending_action_summary is not None


@pytest.mark.parametrize("visibility", ["false", "nil", "'true'", "error('unavailable')"])
@pytest.mark.parametrize("occupied", [False, True])
def test_unseen_or_unknown_plot_does_not_read_or_disclose_religious_occupancy(visibility, occupied):
    conn = religious_mover()
    conn.lua.globals().occupied_target = occupied
    conn.lua.execute('''
target_layer_reads=0
local original=Units.GetUnitsInPlotLayerID
Units.GetUnitsInPlotLayerID=function(p,l)
 if p:GetY()==22 then
  target_layer_reads=target_layer_reads+1
  if occupied_target then return {builder_unit} end
 end
 return original(p,l)
end
PlayersVisibility[0].IsVisible=function(_,px,py)
 if py==22 then VISIBILITY end
 return true
end
'''.replace("VISIBILITY", visibility if visibility.startswith("error(") else "return " + visibility))
    state = GameState(conn)
    result = run(server.unit_action(make_ctx(game=state), 3145737, "move", 35, 22))
    assert result == "Error: MOVE_LAYER_UNKNOWN|Native religious placement layer unavailable or unverified; no move submitted."
    assert conn.lua.globals().target_layer_reads == 0
    assert conn.lua.globals().submissions == 0
    assert state.pending_action_summary is None


@pytest.mark.parametrize("visibility", ["false", "nil", "'true'", "error('unavailable')"])
def test_unseen_or_unknown_unit_does_not_disclose_target_layer_occupancy(visibility):
    conn = religious_mover()
    conn.lua.execute('''
local original=Units.GetUnitsInPlotLayerID
Units.GetUnitsInPlotLayerID=function(p,l)
 if p:GetY()==22 then return {builder_unit} end
 return original(p,l)
end
PlayersVisibility[0].IsUnitVisible=function(_,u)
 if u==builder_unit then VISIBILITY end
 return true
end
'''.replace("VISIBILITY", visibility if visibility.startswith("error(") else "return " + visibility))
    state = GameState(conn)
    result = run(server.unit_action(make_ctx(game=state), 3145737, "move", 35, 22))
    assert result == "Error: MOVE_LAYER_UNKNOWN|Native religious placement layer unavailable or unverified; no move submitted."
    assert conn.lua.globals().submissions == 0
    assert state.pending_action_summary is None
