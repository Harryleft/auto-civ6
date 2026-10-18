"""Real GameState and Lua 5.1; only the engine/transport adapter is simulated."""
import asyncio

from lupa.lua51 import LuaRuntime

from civ_mcp.game_state import GameState


class MovementConn:
    def __init__(self):
        self.lua = LuaRuntime()
        self.rows = []
        self.lose_observation = True
        self.lua.globals().print = lambda row: self.rows.append(str(row))
        self.lua.execute('''
x, y, turn, seed = 19, 17, 54, 719665280
local listeners = {}
otherEvents, submissions, armedAtSubmit = 0, 0, false
local other = function() otherEvents = otherEvents + 1 end
listeners[other] = true
Events = {UnitOperationSegmentComplete = {
    Add=function(handler) listeners[handler] = true end,
    Remove=function(handler) listeners[handler] = nil end,
    Fire=function(owner, id)
        local callbacks = {}
        for handler in pairs(listeners) do table.insert(callbacks, handler) end
        for _, handler in ipairs(callbacks) do handler(owner, id, 177, 9) end
    end,
}}
function listenerCount()
    local count = 0
    for _ in pairs(listeners) do count = count + 1 end
    return count
end
Game = {GetLocalPlayer=function() return 0 end, GetCurrentGameTurn=function() return turn end}
GameConfiguration = {GetValue=function() return seed end}
PlayerConfigurations = {[0]={GetCivilizationTypeName=function() return "CIVILIZATION_SUMERIA" end}}
unit = {
    GetID=function() return 851974 end,
    GetX=function() return x end, GetY=function() return y end,
    GetType=function() return 1 end, GetMovesRemaining=function() return 1 end,
}
Players = {[0]={GetUnits=function() return {FindID=function() return unit end} end}}
GameInfo = {Units={[1]={FormationClass="FORMATION_CLASS_LAND_COMBAT"}}}
UnitOperationTypes = {MOVE_TO=1, PARAM_X=2, PARAM_Y=3}
UnitManager = {
    GetUnit=function() return unit end,
    CanStartOperation=function() return true end,
    RequestOperation=function()
        submissions = submissions + 1
        armedAtSubmit = listenerCount() == 2
    end,
}
UI = {IsGameCoreBusy=function() return false end}
-- Isolated map/path facts for the explicit source and requested target.
plot_owner=-1
local function plot(px,py)
    return {GetIndex=function() return px+1000*py end,
            GetX=function() return px end,GetY=function() return py end,
            GetOwner=function() return plot_owner end,
            IsWater=function() return false end,IsMountain=function() return false end,
            IsImpassable=function() return false end}
end
Map = {GetUnitsAt=function() return nil end, GetPlot=plot,
       GetPlotByIndex=function(index) return plot(index%1000,math.floor(index/1000)) end}
PlayersVisibility={[0]={IsVisible=function() return true end}}
UnitManager.GetMoveToPathEx=function(u,destination)
    return {plots={u:GetX()+1000*u:GetY(),destination},turns={1,1}}
end
''')

    async def execute_write(self, code):
        if 'ACTION_' not in code:
            # The optional popup context is unavailable. GameState must still
            # execute the action through its normal scope and confirmation.
            raise ConnectionError("optional popup context unavailable")
        if 'ACTION_IDENTITY|' in code and self.lose_observation:
            self.lose_observation = False
            raise ConnectionError("lost observation")
        self.rows.clear()
        self.lua.execute(code)
        return [row for row in self.rows if row != "---END---"]


def test_native_move_listener_is_armed_before_submission_ignores_foreign_events_and_retires_only_itself():
    conn = MovementConn()
    state = GameState(conn)
    assert "OUTCOME_UNKNOWN" in asyncio.run(state.move_unit(6, 18, 18))
    native = conn.lua.globals()
    assert native.submissions == 1 and native.armedAtSubmit
    assert native.listenerCount() == 2

    # A different runtime unit with the same low ID and a stale turn cannot
    # stand in for the request's full native identity.
    conn.lua.execute('Events.UnitOperationSegmentComplete.Fire(0, 917510)')
    conn.lua.execute('turn=55; Events.UnitOperationSegmentComplete.Fire(0, 851974); turn=54')
    assert native.listenerCount() == 2
    assert not native.__civmcpMoveSegment.done
    conn.lua.execute('Events.UnitOperationSegmentComplete.Fire(0, 851974); x=18; y=18')
    assert native.listenerCount() == 1
    assert "now_at:18,18" in asyncio.run(state.move_unit(6, 18, 18))
    assert state.pending_action_summary is None
    assert native.__civmcpMoveSegment.closed
    assert native.submissions == 1
    conn.lua.execute('Events.UnitOperationSegmentComplete.Fire(0, 851974)')
    assert native.otherEvents == 4 and native.listenerCount() == 1
