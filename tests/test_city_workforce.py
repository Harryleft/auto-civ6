"""Read native citizen-management arrays, never assign citizens or alter UI."""
import asyncio
import json

import pytest

from civ_mcp.city_workforce import read_context
from civ_mcp.game_state import GameState
from test_public_reports import engine


class WorkforceConn:
    def __init__(self, extra=''):
        self.lua,self.output=engine('''
turn=200;native_writes=0
function noWrite()native_writes=native_writes+1;error('no query writes')end
UI={RequestPlayerOperation=noWrite,SetInterfaceMode=noWrite,SetInterfaceModeParameter=noWrite}
Game.GetCurrentGameTurn=function()return turn end
GameInfo.Yields={};for i,k in ipairs({'FOOD','PRODUCTION','GOLD','SCIENCE','CULTURE','FAITH'})do GameInfo.Yields['YIELD_'..k]={Index=i-1}end
CityCommandTypes={MANAGE=1,PARAM_MANAGE_CITIZEN=8};CityCommandResults={PLOTS=2,CITIZENS=3,MAX_CITIZENS=4,LOCKED_CITIZENS=5}
nativeTargets={[2]={[0]=10,[7]=11,[9]=12},[3]={[0]=1,[7]=0,[9]=2},[4]={[0]=1,[7]=1,[9]=3},[5]={[0]=1,[7]=0,[9]=1}}
local citizens={IsFavoredYield=function(_,i)return i==0 end,IsDisfavoredYield=function(_,i)return i==3 end,
 IsPlotWorked=function(_,x,y)return x==10 or x==12 end}
city={GetID=function()return 131073 end,GetOwner=function()return 0 end,GetName=function()return '埃利都|100%\\n本城'end,
 GetX=function()return 10 end,GetY=function()return 0 end,GetPopulation=function()return 14 end,
 GetGrowth=function()return{GetFoodSurplus=function()return 0 end}end,GetCitizens=function()return citizens end}
Players[0].GetCities=function()return{Members=function()return ipairs({city})end}end
CityManager={RequestCommand=noWrite,GetCommandTargets=function(c,command,params)
 assert(c==city and command==1);manage_parameter=params[8]
 for key in pairs(params)do assert(key==8)end
 if params[8]==true then return nativeTargets end
 return {[2]=nativeTargets[2]} end}
Map={GetCityPlots=function()return{GetPurchasedPlots=function(_,c)assert(c==city);return{10,11,12,99}end}end,
 GetPlotByIndex=function(index)return{GetIndex=function()return index end,GetX=function()return index end,GetY=function()return 0 end,
 GetOwner=function()return 0 end,GetYield=function(_,i)if index==11 then return i==0 and 4.125 or 0 end;return i==0 and 2 or 1 end}end}
PlayersVisibility[0].IsVisible=function()return true end
'''+extra)
        self.calls=0

    async def execute_write(self,code,**options):
        self.calls+=1;self.output.clear();self.lua.execute(code);return list(self.output)


def read(conn,city_id=131073,view='structured'):
    return asyncio.run(read_context(GameState(conn),city_id,view))


def test_native_manage_pairs_keep_sparse_indices_counts_locks_and_unused_food_plot():
    conn=WorkforceConn();data=read(conn)
    assert data['status']=='ready' and data['scan_complete'] is True and conn.calls==1
    assert data['city']['city_id']==131073 and data['city']['population']==14
    assert [r['native_key']for r in data['plots']]==[0,7,9]
    assert [(r['citizens'],r['max_citizens'],r['locked_citizens'])for r in data['plots']]==[(1,1,1),(0,1,0),(2,3,1)]
    candidate=data['plots'][1]
    assert candidate['yields']['YIELD_FOOD']==4.125 and candidate['is_plot_worked'] is False
    assert candidate['purchased_by_city'] is True and candidate['owner_id']==0
    assert data['city']['yield_preferences']['YIELD_FOOD']==dict(favored=True,ignored=False)
    assert data['city']['yield_preferences']['YIELD_SCIENCE']==dict(favored=False,ignored=True)
    assert conn.lua.globals().native_writes==0
    assert conn.lua.globals().manage_parameter is True


def test_native_city_center_one_citizen_zero_maximum_remains_ready():
    # T50 live MANAGE observation: the center is worked with 1/0/0. The raw
    # columns have distinct engine meanings, not an inferred capacity equation.
    d=read(WorkforceConn('nativeTargets[4][0]=0;nativeTargets[5][0]=0'))
    center=d['plots'][0]
    assert (center['x'],center['y'])==(d['city']['x'],d['city']['y'])
    assert (center['citizens'],center['max_citizens'],center['locked_citizens'])==(1,0,0)
    assert center['is_plot_worked']is True
    assert d['status']=='ready' and d['unknown_fields']==[]
    assert 'city center can report 1/0'in d['scope']
    assert 'city center can report 1/0'in read(WorkforceConn(),view='brief')['report']


def test_native_locked_count_is_preserved_without_an_unproven_occupancy_equation():
    d=read(WorkforceConn('nativeTargets[5][7]=1'))
    assert d['plots'][1]['citizens']==0 and d['plots'][1]['locked_citizens']==1
    assert d['status']=='ready' and d['unknown_fields']==[]


def test_evidence_records_native_parameter_return_keys_and_all_column_shapes():
    d=read(WorkforceConn(),view='evidence');diagnostic=d['manage_diagnostics']
    assert diagnostic['parameter']==dict(name='PARAM_MANAGE_CITIZEN',key=8,key_type='number',value=True,value_type='boolean')
    assert diagnostic['result_type']=='table' and diagnostic['missing_columns']==[]
    assert diagnostic['top_level_keys']==[dict(key=i,key_type='number',value_type='table')for i in range(2,6)]
    assert diagnostic['columns']['LOCKED_CITIZENS']==dict(key=5,key_type='number',value_type='table',entry_count=3,key_types={'number':3},value_types={'number':3})
    assert 'manage_diagnostics'not in read(WorkforceConn())


def test_missing_columns_report_names_and_evidence_preserves_actual_false_and_string_keys():
    d=read(WorkforceConn("nativeTargets[3]=nil;nativeTargets[4]=false;nativeTargets[5]=7;nativeTargets['reason']='no arrays'"),view='evidence')
    assert d['status']=='unknown' and d['plots']is None
    diagnostic=d['manage_diagnostics']
    assert diagnostic['missing_columns']==['CITIZENS','MAX_CITIZENS','LOCKED_CITIZENS']
    assert all('manage.columns.'+name in d['unknown_fields']for name in diagnostic['missing_columns'])
    assert diagnostic['columns']['CITIZENS']['value_type']=='nil'
    assert diagnostic['columns']['MAX_CITIZENS']['value_type']=='boolean'
    assert diagnostic['columns']['LOCKED_CITIZENS']['value_type']=='number'
    assert dict(key='reason',key_type='string',value_type='string')in diagnostic['top_level_keys']
    assert dict(key=4,key_type='number',value_type='boolean')in diagnostic['top_level_keys']


def test_missing_citizen_parameter_fails_without_guessing_a_constant_or_reading_ui():
    conn=WorkforceConn('CityCommandTypes.PARAM_MANAGE_CITIZEN=nil')
    d=read(conn,view='evidence')
    assert d['status']=='unknown' and d['plots']is None
    assert 'manage.parameter.PARAM_MANAGE_CITIZEN'in d['unknown_fields']
    assert d['manage_diagnostics']['result_type']=='not_queried'
    assert conn.lua.globals().manage_parameter is None and conn.lua.globals().native_writes==0


@pytest.mark.parametrize('value',['false','0',"'not an array'"])
def test_non_array_purchased_plots_are_partial_not_ready(value):
    conn=WorkforceConn(f'Map.GetCityPlots=function()return{{GetPurchasedPlots=function()return {value} end}}end')
    data=read(conn)
    assert data['status']=='partial' and data['scan_complete'] is True
    assert data['purchased_plot_scan_complete'] is False
    assert all(row['purchased_by_city']is None for row in data['plots'])
    assert 'purchased_plots.type'in data['unknown_fields']


@pytest.mark.parametrize('change',[
    'nativeTargets[3][7]=nil',
    'nativeTargets[5][42]=0',
    'nativeTargets[2][7]=10',
    'nativeTargets[2]=nil',
    'CityManager.GetCommandTargets=function()return nil end',
])
def test_unpaired_or_missing_native_targets_never_become_empty_success(change):
    d=read(WorkforceConn(change))
    assert d['status']=='unknown' and d['scan_complete'] is False and d['plots'] is None
    assert d['unknown_fields']


def test_unknown_yield_preference_or_worked_flag_remains_unknown():
    conn=WorkforceConn('''
local c=city:GetCitizens();c.IsFavoredYield=function()return nil end;c.IsPlotWorked=function()return nil end
''')
    d=read(conn)
    assert d['status']=='partial' and d['scan_complete'] is True
    assert d['city']['yield_preferences']['YIELD_FOOD']['favored'] is None
    assert d['plots'][0]['is_plot_worked'] is None


def test_missing_purchased_plot_scan_does_not_invent_city_assignment():
    d=read(WorkforceConn('Map.GetCityPlots=nil'))
    assert d['status']=='partial' and d['plots'][0]['purchased_by_city'] is None
    assert d['purchased_plot_scan_complete'] is False


def test_unknown_coordinates_are_not_passed_into_native_worked_getter():
    conn=WorkforceConn('''
invalid_worked_calls=0
city:GetCitizens().IsPlotWorked=function(_,x,y)
 if type(x)~='number'or type(y)~='number'then invalid_worked_calls=invalid_worked_calls+1 end
 return true end
local old=Map.GetPlotByIndex
Map.GetPlotByIndex=function(index)local p=old(index);if index==11 then p.GetX=function()return nil end end;return p end
''')
    d=read(conn)
    assert d['status']=='partial' and d['plots'][1]['x'] is None and d['plots'][1]['is_plot_worked'] is None
    assert conn.lua.globals().invalid_worked_calls==0


def test_known_zero_and_unknown_plot_yield_differ_without_reserving_foreign_fog_facts():
    conn=WorkforceConn('''
local old=Map.GetPlotByIndex
Map.GetPlotByIndex=function(index)local p=old(index)
 if index==11 then p.GetYield=function(_,i)if i==0 then return 0 end;error('unavailable')end end
 return p end
''')
    d=read(conn);plot=d['plots'][1]
    assert plot['yields']['YIELD_FOOD']==0 and plot['yields']['YIELD_PRODUCTION'] is None
    assert d['status']=='partial'


def test_wrong_or_unknown_world_or_city_identity_discards_plots():
    assert read(WorkforceConn(),city_id=196609)['plots'] is None
    assert read(WorkforceConn('city.GetOwner=function()return 1 end'))['plots'] is None
    for change in ('GameConfiguration.GetValue=function()return nil end',"city.GetPopulation=function()turn=201;return 14 end"):
        d=read(WorkforceConn(change));assert d['status']=='unknown' and d['plots'] is None


def test_foreign_fogged_plot_details_and_yields_are_withheld_without_reading_yields():
    conn=WorkforceConn('''
hidden_yield_reads=0
PlayersVisibility[0].IsVisible=function(_,index)return index~=11 end
local old=Map.GetPlotByIndex
Map.GetPlotByIndex=function(index)local p=old(index)
 if index==11 then p.GetOwner=function()return 1 end;p.GetYield=function()hidden_yield_reads=hidden_yield_reads+1;return 99 end end
 return p end
''')
    d=read(conn);p=d['plots'][1]
    assert p['owner_id'] is None and p['visible_now'] is False
    assert all(value is None for value in p['yields'].values())
    assert d['status']=='partial' and conn.lua.globals().hidden_yield_reads==0


def test_unstable_evidence_also_discards_auxiliary_city_plot_identifiers():
    d=read(WorkforceConn('city.GetPopulation=function()turn=201;return 14 end'),view='evidence')
    assert d['status']=='unknown' and d['city'] is None and d['plots'] is None
    assert d['purchased_plot_ids'] is None and d['native_manage_keys'] is None
    assert d['manage_diagnostics']is None


def test_pending_blocks_before_any_game_io():
    conn=WorkforceConn();game=GameState(conn);game._pending_load=True
    d=asyncio.run(read_context(game,131073))
    assert d['status']=='blocked' and conn.calls==0


def test_brief_keeps_all_manage_rows_and_distinguishes_locking_from_occupancy():
    conn=WorkforceConn();full=read(conn,view='evidence');brief=read(conn,view='brief')
    assert full['native_manage_keys']==[0,7,9]
    assert full['purchased_plot_ids']==[10,11,12,99]
    for token in ('(10,0)','(11,0)','(12,0)','citizens 2/3','locked 1','visible now yes','4.125','favored YIELD_FOOD','ignored YIELD_SCIENCE'):
        assert token in brief['report']
    assert 'free citizens'not in brief['report']
    assert len(json.dumps(brief,ensure_ascii=False))<len(json.dumps(full,ensure_ascii=False))


@pytest.mark.parametrize(('native_value','expected'),[('false','no'),('nil','unknown')])
def test_brief_retains_false_and_unknown_current_plot_visibility(native_value,expected):
    d=read(WorkforceConn(f'PlayersVisibility[0].IsVisible=function()return {native_value} end'),view='brief')
    plot_rows=[line for line in d['report'].splitlines()if '[plot:'in line]
    assert len(plot_rows)==3
    assert all(f'visible now {expected};'in row for row in plot_rows)
