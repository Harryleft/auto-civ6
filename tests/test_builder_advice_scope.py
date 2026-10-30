"""Builder hints are complete observations, not action or priority guarantees."""
import asyncio

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_builder_tasks
from test_resource_hint_visibility import ResourceConn


def test_all_terrain_candidates_survive_the_old_twenty_tile_cap():
    conn=ResourceConn()
    conn.lua.execute('''
local p=plots[2]
Map.GetPlot=function(x,y)if math.abs(x)<=3 and math.abs(y)<=3 then return p end end
''')
    report=asyncio.run(GameState(conn).get_builder_tasks())
    assert len(report.tasks)==49
    assert conn.lua.eval('native_requests')==0


def test_moves_do_not_prove_builder_ready_or_task_reachable():
    conn=ResourceConn()
    conn.lua.execute('''
GameInfo.Units[99]={UnitType='UNIT_BUILDER'}
local builder={GetType=function()return 99 end,GetBuildCharges=function()return 2 end,
 GetX=function()return 2 end,GetY=function()return 0 end,GetID=function()return 65537 end,
 GetMovesRemaining=function()return 2 end,IsReadyToMove=function()return false end}
Players[0].GetUnits=function()return {Members=function()return ipairs({builder})end}end
''')
    report=asyncio.run(GameState(conn).get_builder_tasks())
    assert report.builders[0].ready is False
    text=narrate_builder_tasks(report)
    assert 'idle builders' not in text and 'IDLE BUILDERS' not in text
    assert 'ready no' in text and 'hex distance' in text
    assert 'URGENT' not in text and 'HIGH:' not in text


def test_locked_improvement_is_visible_with_reason_instead_of_silently_removed():
    class Conn:
        async def execute_write(self, _source):
            return ['TASK|high|1,0|IMPROVEMENT_MINE_LOCKED|IRON|strategic|Kish|-1|999',
                    'RESOURCE_VISIBILITY|true']
    report=asyncio.run(GameState(Conn()).get_builder_tasks())
    assert len(report.tasks)==1
    assert report.tasks[0].eligibility_note=='PREREQUISITE_TECH_LOCKED'
    assert 'PREREQUISITE_TECH_LOCKED' in narrate_builder_tasks(report)
