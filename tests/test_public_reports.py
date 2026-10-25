"""Execute the native report builders in Lua 5.1, including visibility gates."""
import json

import pytest
from lupa.lua51 import LuaRuntime

from civ_mcp.lua import public_reports


def engine(extra=""):
    lua = LuaRuntime(unpack_returned_tuples=True)
    output = []
    lua.globals().capture = lambda value: output.append(str(value))
    lua.execute('''
print=capture
Game={GetLocalPlayer=function()return 0 end,GetCurrentGameTurn=function()return 16 end}
GameConfiguration={GetValue=function()return 7 end}
Locale={Lookup=function(value)return value end}
local cfg={GetCivilizationTypeName=function()return "CIVILIZATION_SUMERIA"end,
 GetLeaderTypeName=function()return "LEADER_GILGAMESH"end,GetCivilizationShortDescription=function()return "苏美尔"end}
PlayerConfigurations={[0]=cfg,[1]=cfg,[2]=cfg}
local dip={HasMet=function(_,id)return id==1 end}
Players={[0]={GetDiplomacy=function()return dip end,IsMajor=function()return true end},
 [1]={IsMajor=function()return true end},[2]={IsMajor=function()return true end}}
PlayersVisibility={[0]={IsRevealed=function(_,x)return x>0 end,IsVisible=function(_,x)return x==2 end}}
GameInfo={}
UI={RequestPlayerOperation=function()error("Query must not mutate")end}
'''+extra)
    return lua, output


def execute(section, extra="", **options):
    lua, output = engine(extra)
    lua.execute(public_reports.build(section, **options))
    return public_reports.parse(output), lua


def test_missing_climate_values_are_unknown_not_zero():
    value, _ = execute('climate', 'GameClimate={GetTotalCO2Footprint=function()return 0 end}')
    assert value['data']['total_co2_footprint'] == 0
    assert value['data']['temperature_change_celsius'] is None
    assert value['status'] == 'partial' and value['unknown_fields']
    assert value['data']['co2_concentration_ppm'] is None


def test_empty_climate_history_and_hidden_local_events_are_distinct():
    value, _ = execute('climate-history', '''
GameRandomEvents={GetEventsForTurn=function(t)if t==12 then return{RandomEvent=0,CurrentLocation=0}end end}
GameInfo.RandomEvents={[0]={RandomEventType="FLOOD",Name="洪水",Global=0}}
Map={GetPlotByIndex=function()return{GetX=function()return 0 end,GetY=function()return 0 end}end}
''')
    assert value['status'] == 'ready' and value['data']['events'] == []
    assert value['total'] == 0 and value['unknown_fields'] == []


def test_missing_history_getter_is_unknown_not_empty_history():
    value, _ = execute('climate-history')
    assert value['status'] == 'partial' and value['data']['events'] is None


def test_missing_gossip_getter_is_unknown_not_no_gossip():
    value, _ = execute('gossip')
    assert value['status'] == 'partial' and value['data']['messages'] is None


def test_world_resources_uses_trade_visibility_not_foreign_inventory():
    value, lua = execute('world-resources', '''
foreign_reads=0
Players[0].GetResources=function()return{GetResourceAmount=function()return 0 end}end
Players[1].GetResources=function()foreign_reads=foreign_reads+1;error("hidden inventory")end
local resource={Index=0,ResourceType="RESOURCE_TEA",Name="茶"}
GameInfo.Resources=setmetatable({[0]=resource},{__call=function()local n=0;return function()n=n+1;if n==1 then return resource end end end})
DealDirection={OUTGOING=1};DealItemTypes={RESOURCES=1}
DealManager={GetWorkingDeal=function()return{}end,GetPossibleDealItems=function()return{{ForType=0,MaxAmount=1}}end,
 ClearWorkingDeal=function()error("No mutation")end}
''')
    assert value['data']['resources'][0]['amount']==1 and value['status']=='ready'
    assert lua.globals().foreign_reads==0


def test_map_ignores_fog_and_unrevealed_resources_and_huts():
    value, _ = execute('map-search', '''
Players[0].GetResources=function()return{IsResourceVisible=function(_,i)return i~=9 end}end
Map={GetPlotCount=function()return 4 end,GetPlotByIndex=function(i)return{
 GetX=function()return i end,GetY=function()return 0 end,
 GetResourceType=function()return i==1 and 9 or 1 end,
 GetImprovementType=function()return 0 end}end}
GameInfo.Resources={[1]={ResourceType="RESOURCE_TEA",ResourceClassType="RESOURCECLASS_LUXURY",Name="茶"},
 [9]={ResourceType="RESOURCE_URANIUM",ResourceClassType="RESOURCECLASS_STRATEGIC",Name="铀"}}
GameInfo.Improvements={[0]={ImprovementType="IMPROVEMENT_GOODY_HUT",Name="村庄",Goody=1}}
''', search_kind='luxury')
    assert value['status'] == 'ready'
    assert [row['x'] for row in value['data']['locations']] == [2,3]
    village, _ = execute('map-search', '''
Players[0].GetResources=function()return{IsResourceVisible=function()return true end}end
Map={GetPlotCount=function()return 4 end,GetPlotByIndex=function(i)return{
 GetX=function()return i end,GetY=function()return 0 end,GetResourceType=function()return -1 end,
 GetImprovementType=function()return 0 end}end}
GameInfo.Improvements={[0]={ImprovementType="IMPROVEMENT_GOODY_HUT",Name="村庄",Goody=1}}
''', search_kind='village')
    assert [row['x'] for row in village['data']['locations']] == [2]


def test_great_people_history_redacts_unmet_claimants_and_unknown_payment():
    value, _ = execute('great-people-history', '''
Game.GetGreatPeople=function()return{GetPastTimeline=function()return{
 {Individual=3,Claimant=2},{Individual=4,Claimant=0,Turn=10}}end}end
GameInfo.GreatPersonIndividuals={[3]={Name="人物甲"},[4]={Name="人物乙"}}
''')
    assert value['data']['people'][0]['claimant_id'] is None
    assert value['data']['people'][1]['claimant_id'] == 0
    assert value['data']['people'][1]['payment_amount'] is None


def test_unmet_target_does_not_enter_report_getter():
    value, lua = execute('great-people-history', 'calls=0;Game.GetGreatPeople=function()calls=calls+1;error("not allowed")end', target_player=2)
    assert value['status'] == 'unknown' and value['data'] is None
    assert lua.globals().calls == 0


def test_world_change_discards_mixed_report():
    value, _ = execute('empire', 'local n=0;Game.GetCurrentGameTurn=function()n=n+1;return n==1 and 16 or 17 end')
    assert value['status'] == 'unknown' and value['data'] is None


def test_json_strings_roundtrip_without_corrupting_chinese_or_controls():
    text = '文明"\\\n\t\x01'
    lua, output = engine()
    lua.globals().message = text
    lua.execute(public_reports.HEADER+'\nprint("PUBLIC_REPORT|"..encode({text=message}))')
    assert public_reports.parse(output)['text'] == text


@pytest.mark.parametrize('kwargs', [dict(limit=0),dict(offset=-1),dict(target_player=64),dict(query='a\nb')])
def test_invalid_report_parameters_submit_nothing(kwargs):
    with pytest.raises(ValueError):
        public_reports.build('map-search', **kwargs)


def test_visible_climate_history_filters_and_paginates_native_events():
    value, _ = execute('climate-history', '''
GameRandomEvents={GetEventsForTurn=function(t)if t>=14 then return{RandomEvent=0,CurrentLocation=2}end end}
GameInfo.RandomEvents={[0]={RandomEventType="FLOOD",Name="洪水",Global=0,ClimateChangePoints=2}}
Map={GetPlotByIndex=function()return{GetX=function()return 2 end,GetY=function()return 0 end}end}
''', since_turn=14, limit=1, offset=1)
    assert value['status'] == 'ready' and value['total'] == 3
    assert value['truncated'] and value['data']['events'][0]['turn'] == 15


def test_consumable_map_sites_require_current_visibility():
    value, _ = execute('map-search', '''
Players[0].GetResources=function()return{IsResourceVisible=function()return true end}end
Map={GetPlotCount=function()return 4 end,GetPlotByIndex=function(i)return{
 GetX=function()return i end,GetY=function()return 0 end,GetResourceType=function()return 0 end}end}
GameInfo.Resources={[0]={ResourceType="RESOURCE_ANTIQUITY_SITE",Name="古迹"}}
''', search_kind='antiquity')
    assert [row['x'] for row in value['data']['locations']] == [2]


def test_invalid_report_does_not_access_game_connection():
    import asyncio
    from civ_mcp.game_state import GameState
    class NoConnection:
        async def execute_in_named_state(self, *args):
            pytest.fail('Invalid query must be rejected before game access')
    value = asyncio.run(GameState(NoConnection()).get_public_report('map-search', limit=0))
    assert value['status'] == 'error' and value['data'] is None


def test_unknown_resource_stock_is_not_known_empty_inventory():
    value, _ = execute('world-resources', '''
local resource={Index=0,ResourceType="RESOURCE_TEA",Name="茶"}
GameInfo.Resources=setmetatable({}, {__call=function()local n=0;return function()n=n+1;if n==1 then return resource end end end})
''', target_player=0)
    assert value['status'] == 'partial' and value['data']['resources'] is None


def test_unknown_government_unlocks_are_not_known_empty_choices():
    value, _ = execute('governments', '''
Players[0].GetCulture=function()return{}end
local government={Index=0,Hash=1,GovernmentType="GOVERNMENT_CHIEFDOM",Name="酋邦"}
GameInfo.Governments=setmetatable({}, {__call=function()local n=0;return function()n=n+1;if n==1 then return government end end end})
''')
    assert value['status'] == 'partial' and value['data']['governments'] is None
