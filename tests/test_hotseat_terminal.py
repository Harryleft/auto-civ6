"""Global native victory, local elimination and original end-turn completion."""
import asyncio
import importlib.util
from pathlib import Path

import pytest
from lupa.lua51 import LuaRuntime


@pytest.fixture
def agent():
    path=Path(__file__).parents[1]/'scripts/hotseat_agent.py'
    spec=importlib.util.spec_from_file_location('terminal_agent_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class Native:
    def __init__(self,winner=-1,alive0=True):
        self.rows=[];self.lua=LuaRuntime()
        self.lua.globals().print=lambda text:self.rows.append(str(text))
        self.lua.globals().winner=winner;self.lua.globals().alive0=alive0
        self.lua.execute('''
Game={GetWinningTeam=function() return winner,5 end,GetCurrentGameTurn=function() return 101 end,GetLocalPlayer=function() return 1 end}
GameConfiguration={IsHotseat=function() return true end,GetValue=function() return -1896272000 end}
GameInfo={Victories={[5]={VictoryType="VICTORY_CULTURE"}}}
Players={};PlayerConfigurations={}
for i=0,3 do
 local id=i
 Players[i]={IsHuman=function() return id<2 end,IsAlive=function() return id~=0 or alive0 end,IsMajor=function() return true end,GetTeam=function() return id end}
 PlayerConfigurations[i]={GetCivilizationTypeName=function() return ({[0]="CIVILIZATION_SUMERIA",[1]="CIVILIZATION_SWEDEN",[2]="CIVILIZATION_BRAZIL",[3]="CIVILIZATION_CANADA"})[id] end}
end
''')
    async def execute_in_named_state(self,name,code):
        assert name=='GameCore_Tuner';self.rows.clear();self.lua.execute(code);return list(self.rows)


def test_one_eliminated_player_does_not_end_the_match(agent):
    status=asyncio.run(agent.match_status(Native(alive0=False)))
    assert status['alive']==[False,True] and status['terminal'] is False


def test_only_official_winning_team_establishes_global_terminal(agent):
    status=asyncio.run(agent.match_status(Native(winner=3)))
    assert status['terminal'] and status['winner_team']==3 and status['victory_type']=='VICTORY_CULTURE'
    assert status['winners']==[{'id':3,'civilization':'CIVILIZATION_CANADA'}]


def test_wrong_world_or_incomplete_native_facts_fail_closed(agent):
    native=Native();native.lua.execute('GameConfiguration.GetValue=function() return 88 end')
    with pytest.raises(ValueError):asyncio.run(agent.match_status(native))
    native=Native();native.lua.execute('Game.GetWinningTeam=function() error("native unavailable") end')
    with pytest.raises(Exception):asyncio.run(agent.match_status(native))


def test_native_nil_winning_tuple_means_active_as_stock_worldinput(agent):
    native=Native();native.lua.execute('Game.GetWinningTeam=function() return nil,nil end')
    assert not asyncio.run(agent.match_status(native))['terminal']


def test_gamecore_terminal_reader_does_not_require_ui_only_hotseat_api(agent):
    native=Native();native.lua.execute('GameConfiguration.IsHotseat=nil')
    assert not asyncio.run(agent.match_status(native))['terminal']
