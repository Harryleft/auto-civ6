"""Read actual generated Lua through GameState with only native adapters faked."""
import asyncio
from functools import lru_cache
import json
from pathlib import Path

from lupa.lua51 import LuaRuntime
import pytest

from civ_mcp import server
from civ_mcp.devtools.commands import parse_command
from civ_mcp.game_state import GameState
from test_server import make_ctx


@lru_cache
def rationalism_rows():
    cards = json.loads((Path(__file__).resolve().parents[1] / 'civ6_knowledge/rules/economy.json').read_text())
    # Use the committed rule graph as native adapter input, never the product's
    # predicate implementation. Boundary cases below change the current rows.
    entries = cards['cards']
    card = next(c for c in entries if c['id'] == 'POLICY_RATIONALISM')
    tables = {'Policies': [card['rule']['facts']]}
    for row in card['rule']['related_records']:
        tables.setdefault(row['source_ref']['table'], []).append(row['facts'])
    return tables


class NativeConn:
    def __init__(self):
        self.lua = LuaRuntime()
        self.calls = []
        self.output = []
        self.lua.globals().print = lambda row: self.output.append(str(row))
        self.lua.globals().source = self.lua.table_from(rationalism_rows(), recursive=True)
        self.lua.execute('''
function rows(values,field)
 local result={};for i,row in ipairs(values)do result[i]=row;if field then result[row[field]]=row end end
 return setmetatable(result,{__call=function()local i=0;return function()i=i+1;return values[i]end end})
end
GameInfo={};for tab,values in pairs(source)do
 local key=({Policies='PolicyType',Modifiers='ModifierId',DynamicModifiers='ModifierType',Requirements='RequirementId',RequirementSets='RequirementSetId'})[tab]
 GameInfo[tab]=rows(values,key)
end
GameInfo.Policies.POLICY_RATIONALISM.Index=9;GameInfo.Policies.POLICY_RATIONALISM.Hash=109
GameInfo.Yields={};for i,name in ipairs({'FOOD','PRODUCTION','GOLD','SCIENCE','CULTURE','FAITH'})do GameInfo.Yields['YIELD_'..name]={Index=i-1}end
GameInfo.Districts={[0]={Index=0,DistrictType='DISTRICT_CAMPUS'}}
GameInfo.Buildings=rows({{Index=1,BuildingType='BUILDING_LIBRARY',PrereqDistrict='DISTRICT_CAMPUS'}})
turn=200;population=14;adjacency=3.5;complete=true;pillaged=false;slotted=true;unlocked=true;cityOwner=0;campus=true
Game={GetLocalPlayer=function()return 0 end,GetCurrentGameTurn=function()return turn end}
GameConfiguration={GetValue=function()return 4242 end}
PlayerConfigurations={[0]={GetCivilizationTypeName=function()return 'CIVILIZATION_SUMERIA'end}}
Locale={Lookup=function(v)return v end}
district={GetID=function()return 4 end,GetType=function()return 0 end,GetX=function()return 8 end,GetY=function()return 9 end,
 IsComplete=function()return complete end,IsPillaged=function()return pillaged end,
 GetAdjacencyYield=function(_,idx)assert(idx>=0 and idx<=5);return idx==3 and adjacency or 0 end,
 GetYield=function(_,idx)return idx==3 and 12.25 or 0 end}
city={GetID=function()return 65536 end,GetOwner=function()return cityOwner end,GetName=function()return '埃利都|100%\\n本城'end,
 GetPopulation=function()return population end,GetYield=function(_,idx)assert(idx==3);return 33.125 end,
 GetGrowth=function()return{GetFoodSurplus=function()return 0 end}end,
 GetDistricts=function()return{Members=function()return ipairs(campus and {district} or {})end}end,
 GetBuildings=function()return{HasBuilding=function()return campus end,IsPillaged=function()return pillaged end}end,
 GetBuildingYield=function(_,building,yield)return yield=='YIELD_SCIENCE' and 2 or 0 end,
 GetYieldToolTip=function()return '科技|100%[NEWLINE]来自建筑'end}
Players={[0]={GetCities=function()return{Members=function()return ipairs({city})end}end,
 GetCulture=function()return{GetNumPolicySlots=function()return 2 end,GetSlotPolicy=function(_,s)return slotted and s==0 and 9 or -1 end,
 CanSlotPolicy=function()return unlocked end,IsPolicyUnlocked=function()return unlocked end}end}}
Map={GetPlot=function()return{GetAdjacencyYield=function(_,owner,cid,dist,idx)assert(owner==0 and cid==65536 and dist==0);return idx==3 and adjacency or 0 end}end}
UI={RequestPlayerOperation=function()error('Mutation forbidden')end}
nativeMet={[10]=false,[11]=false}
GameEffects={
 GetModifiers=function()return{10,11}end,
 GetModifierDefinition=function(id)return{Id=id==10 and 'RATIONALISM_BUILDING_YIELDS_HIGH_ADJACENCY' or 'RATIONALISM_BUILDING_YIELDS_HIGH_POP'}end,
 GetModifierOwner=function()return 0 end,GetObjectsPlayerId=function()return 0 end,
 GetModifierActive=function()return true end,
 GetModifierSubjectCount=function(id)return nativeMet[id] and 1 or 0 end,
 GetModifierTrackedObjectCount=function()return 1 end,
 GetModifierSubjects=function(id)if nativeMet[id]then return{20}end end,
 GetModifierTrackedObjects=function()return{20}end,
 GetObjectType=function()return 'LOC_MODIFIER_OBJECT_CITY'end,
 GetObjectName=function()return city:GetName()end,
 GetObjectString=function()return 'City (65536), Owner: 0, Name: '..city:GetName()end,
 GetModifierTrackedOjectRequirementSet=function(id)return id+100 end,
 GetRequirementSetState=function(id)return nativeMet[id-100] and 'Met' or 'NotMet'end,
 GetRequirementSetDefinition=function(id)return{Id=id==110 and 'CAMPUS_HAS_HIGH_ADJACENCY' or 'CITY_HAS_HIGH_POPULATION'}end,
 GetRequirementSetInnerRequirements=function(id)return{id}end,
 GetRequirementDefinition=function(id)return{Id=id==110 and 'REQUIRES_CAMPUS_HAS_HIGH_ADJACENCY' or 'REQUIRES_CITY_HAS_HIGH_POPULATION'}end,
 GetRequirementState=function(id)return nativeMet[id-100] and 'Met' or 'NotMet'end}
''')

    async def execute_write(self, code, timeout=None):
        self.calls.append(code); self.output = []
        self.lua.execute(code)
        return self.output

    execute_read = execute_write


def read(conn=None, **kwargs):
    conn = conn or NativeConn()
    game = GameState(conn)
    return asyncio.run(game.get_city_yields(**kwargs)), game, conn


def test_bounded_context_reads_keep_native_precision_rules_and_separate_predicates():
    result, game, conn = read(view='structured')
    assert result['status'] == 'ready', result
    assert len(conn.calls) == 3 and game._last_snapshot is None
    c = result['cities'][0]
    assert c['science'] == 33.125 and c['name'] == '埃利都|100%\n本城'
    assert c['districts'][0]['adjacency']['YIELD_SCIENCE'] == 3.5
    assert c['districts'][0]['adjacency']['YIELD_FOOD'] == 0
    predicates = {r['threshold']: r for r in c['policy_conditions']}
    assert predicates[15]['observed'] == 14 and predicates[15]['meets_observed_threshold'] is False
    assert predicates[4]['observed'] == 3.5 and predicates[4]['meets_observed_threshold'] is False
    assert len(result['modifiers']) == 2 and all(m['amount_percent'] == 50 for m in result['modifiers'])
    assert all(r['native_effect_confirmed'] is False for r in predicates.values())


@pytest.mark.parametrize('population,adjacency,expected', [(15,4,True),(14,3.999,False),(16,4.5,True)])
def test_exact_current_thresholds(population, adjacency, expected):
    conn=NativeConn();conn.lua.execute(f'population={population};adjacency={adjacency}')
    result,_,_=read(conn,view='structured')
    assert all(c['meets_observed_threshold'] is expected for c in result['cities'][0]['policy_conditions'])


def test_live_rule_change_is_used_instead_of_fixed_15_4_or_50():
    conn=NativeConn();conn.lua.execute('''
for row in GameInfo.RequirementArguments()do if row.Name=='Amount'then row.Value='12'end end
for row in GameInfo.ModifierArguments()do if row.Name=='Amount'then row.Value='25'end end
adjacency=12
''')
    result,_,_=read(conn,view='structured')
    assert all(c['threshold']==12 and c['meets_observed_threshold'] is True for c in result['cities'][0]['policy_conditions'])
    assert all(m['amount_percent']==25 for m in result['modifiers'])


def test_native_gameinfo_cached_relations_are_not_copied_or_mistaken_for_sql_fields():
    conn=NativeConn();conn.lua.execute("GameInfo.Policies.POLICY_RATIONALISM.PolicyModifiers={nested={}}")
    result,_,_=read(conn,view='structured')
    assert result['status']=='ready' and len(result['modifiers'])==2
    assert result['cached_relations']==[['Policies','POLICY_RATIONALISM','PolicyModifiers']]


def test_modified_district_adjacency_cannot_silently_pass_a_plot_threshold():
    conn=NativeConn();conn.lua.execute('''
district.GetAdjacencyYield=function(_,idx)return idx==3 and 6 or 0 end
adjacency=3
''')
    result,_,_=read(conn,view='structured')
    district=result['cities'][0]['districts'][0]
    assert district['adjacency']['YIELD_SCIENCE']==6 and district['plot_adjacency']['YIELD_SCIENCE']==3
    cond=next(c for c in result['cities'][0]['policy_conditions'] if c['modifier_id'].endswith('HIGH_ADJACENCY'))
    assert cond['meets_observed_threshold'] is False and cond['plot_observed']==3
    assert cond['native_conditions_met'] is False and cond['native_effect_confirmed'] is False
    text=read(conn)[0]['report']
    assert 'district_adj SCIENCE=6' in text and 'plot_adj SCIENCE=3' in text


@pytest.mark.parametrize('setup',[
    "GameInfo.Requirements.REQUIRES_CITY_HAS_HIGH_POPULATION.Inverse=1",
    "GameInfo.Requirements.REQUIRES_CITY_HAS_HIGH_POPULATION.Inverse=nil",
    "GameInfo.Requirements.REQUIRES_CITY_HAS_HIGH_POPULATION.Reverse=1",
    "GameInfo.Requirements.REQUIRES_CITY_HAS_HIGH_POPULATION.Triggered=1",
    "GameInfo.RequirementSets.CITY_HAS_HIGH_POPULATION.RequirementSetType='REQUIREMENTSET_TEST_ANY'",
    "GameInfo.Modifiers.RATIONALISM_BUILDING_YIELDS_HIGH_POP.OwnerRequirementSetId='CITY_HAS_HIGH_POPULATION'",
    "GameInfo.Requirements.REQUIRES_CITY_HAS_HIGH_POPULATION.RequirementType='REQUIREMENT_OTHER'",
    "GameInfo.Requirements.REQUIRES_CITY_HAS_HIGH_POPULATION.NewCondition=1",
    "GameInfo.Modifiers.RATIONALISM_BUILDING_YIELDS_HIGH_POP.NewCondition=1",
])
def test_unfamiliar_or_inverse_conditions_remain_visible_and_unknown(setup):
    conn=NativeConn();conn.lua.execute(setup)
    result,_,_=read(conn,view='structured')
    branch=next(m for m in result['modifiers'] if m['modifier_id'].endswith('HIGH_POP'))
    assert branch['comparison_supported'] is False and branch['requirements']
    cond=next(c for c in result['cities'][0]['policy_conditions'] if c['modifier_id'].endswith('HIGH_POP'))
    assert cond['meets_observed_threshold'] is None


@pytest.mark.parametrize('setup',[
    "district.GetAdjacencyYield=function()error('Unavailable')end",
    "district.IsComplete=function()error('Unavailable')end",
    "city.GetPopulation=function()return nil end",
])
def test_unknown_native_facts_are_not_zero_or_confirmed_effects(setup):
    conn=NativeConn();conn.lua.execute(setup)
    result,_,_=read(conn,view='structured')
    assert all(c['native_effect_confirmed'] is not True for c in result['cities'][0]['policy_conditions'])
    assert 'unknown' in read(conn)[0]['report']


def test_absent_campus_differs_from_zero_unfinished_and_pillaged():
    conn=NativeConn();conn.lua.execute('campus=false')
    result,_,_=read(conn,view='structured')
    assert result['cities'][0]['campus_status']=='absent' and result['cities'][0]['districts']==[]
    conn.lua.execute('campus=true;adjacency=0;complete=false;pillaged=true')
    result,_,_=read(conn,view='structured')
    city=result['cities'][0]
    assert city['campus_status']=='unavailable_or_unknown'
    assert city['districts'][0]['adjacency']['YIELD_SCIENCE']==0
    assert city['districts'][0]['complete'] is False and city['districts'][0]['pillaged'] is True


def test_brief_and_evidence_keep_same_critical_facts_and_details_expand():
    conn=NativeConn();short,_,_=read(conn)
    expanded,_,_=read(conn,view='evidence')
    assert '14 / required 15' in short['report'] and '3.5 / required 4' in short['report']
    assert 'Inverse' in short['report'] and 'Reverse' in short['report']
    assert expanded['cities'][0]['science_tooltip']=='科技|100%[NEWLINE]来自建筑'
    d=expanded['cities'][0]['districts'][0]
    assert d['plot_adjacency']==d['adjacency']
    assert len(json.dumps(short,ensure_ascii=False))<len(json.dumps(expanded,ensure_ascii=False))


@pytest.mark.parametrize('flag', ['_pending_save','_pending_load','_pending_end_turn'])
def test_pending_preserved_without_game_io(flag):
    conn=NativeConn();game=GameState(conn);setattr(game,flag,True)
    result=asyncio.run(game.get_city_yields())
    assert result['status']=='blocked' and getattr(game,flag) is True and conn.calls==[]


@pytest.mark.parametrize('city_id',[True,-1,'1;bad'])
def test_invalid_target_fails_before_io(city_id):
    conn=NativeConn()
    with pytest.raises(ValueError):read(conn,city_id=city_id)
    assert conn.calls==[]


def test_foreign_city_and_changed_turn_cannot_certify_facts():
    conn=NativeConn();conn.lua.execute('cityOwner=1')
    result,_,_=read(conn,view='structured')
    assert result['status']=='unknown' and result['cities']==[]
    conn=NativeConn();conn.lua.execute('city.GetPopulation=function()turn=turn+1;return 14 end')
    result,_,_=read(conn,view='structured')
    assert result['status']=='unknown' and not result['stable_world_turn']
    assert all(c['meets_observed_threshold'] is None for c in result['cities'][0]['policy_conditions'])


def test_extra_condition_is_collected_instead_of_discarded_or_assumed_true():
    conn=NativeConn();conn.lua.execute('''
GameInfo.RequirementSetRequirements=rows({
 {RequirementSetId='CITY_HAS_HIGH_POPULATION',RequirementId='REQUIRES_CITY_HAS_HIGH_POPULATION'},
 {RequirementSetId='CITY_HAS_HIGH_POPULATION',RequirementId='REQUIRES_CAMPUS_HAS_HIGH_ADJACENCY'},
 {RequirementSetId='CAMPUS_HAS_HIGH_ADJACENCY',RequirementId='REQUIRES_CAMPUS_HAS_HIGH_ADJACENCY'}})
''')
    result,_,_=read(conn,view='structured')
    branch=next(m for m in result['modifiers'] if m['modifier_id'].endswith('HIGH_POP'))
    assert len(branch['requirements'])==2 and branch['comparison_supported'] is False
    assert 'comparison unknown' in read(conn)[0]['report']


def test_missing_rule_table_does_not_become_an_unconditional_bonus():
    conn=NativeConn();conn.lua.execute('GameInfo.Requirements=nil')
    result,_,_=read(conn,view='structured')
    assert result['status']=='unknown' and not result['rules_complete']
    assert all(c['meets_observed_threshold'] is None for c in result['cities'][0].get('policy_conditions',[]))


@pytest.mark.parametrize('line,args',[('city yields',{}),('city yields 65536 --view evidence',{'city_id':65536,'view':'evidence'})])
def test_named_command_crosses_real_schema_and_game_state(line,args):
    from jsonschema import Draft202012Validator
    command=parse_command(line)
    assert command.tool=='get_city_yields' and command.arguments==args and not command.dry_run
    Draft202012Validator(server.mcp._tool_manager.get_tool(command.tool).parameters).validate(args)
    result=json.loads(asyncio.run(server.get_city_yields(make_ctx(game=GameState(NativeConn())),**args)))
    assert result['status']=='ready'


def test_real_stdio_pending_action_is_not_confirmed_by_yield_reader(tmp_path):
    from test_debug_cli import MOVE, run_fake
    _,_,events,_,_=run_fake(tmp_path,MOVE+'city yields\npending\nquit --abandon\n')
    event=next(r for r in events if r['event']=='tool_result' and r['tool']=='get_city_yields')
    value=json.loads(event['result']['content'][0]['text'])
    assert value['status']=='blocked' and value['pending']['action']


def test_native_active_branch_is_separate_from_city_total_science():
    conn=NativeConn();conn.lua.execute('population=17;nativeMet[11]=true')
    result,_,_=read(conn,view='structured')
    cond=next(c for c in result['cities'][0]['policy_conditions'] if c['modifier_id'].endswith('HIGH_POP'))
    assert result['status']=='ready' and cond['native_conditions_met'] is True
    assert cond['native_effect_confirmed'] is True and cond['native_object_id']=='20'
    assert result['cities'][0]['science']==33.125


@pytest.mark.parametrize('setup',[
    "GameEffects.GetObjectString=function()return 'City (99999), Owner: 0, Name: '..city:GetName()end",
    "GameEffects.GetObjectString=function()return 'City (65536), Owner: 1, Name: '..city:GetName()end",
    "GameEffects.GetObjectString=function()return 'Different format' end",
    "GameEffects.GetObjectType=function()return 'LOC_MODIFIER_OBJECT_UNIT'end",
    "GameEffects.GetObjectName=function()return 'Other city'end",
    "GameEffects.GetModifierTrackedObjectCount=function()return 2 end",
    "GameEffects.GetRequirementSetDefinition=function()return{Id='OTHER_SET'}end",
    "GameEffects.GetRequirementSetInnerRequirements=function(id)return{id,id}end",
    "GameEffects.GetRequirementState=function()return 'Unknown'end",
])
def test_unbound_incomplete_or_conflicting_engine_facts_never_confirm_effects(setup):
    conn=NativeConn();conn.lua.execute(setup)
    result,_,_=read(conn,view='structured')
    assert all(c['native_effect_confirmed'] is None for c in result['cities'][0]['policy_conditions'])


def test_native_and_ui_observation_disagreement_is_visible_not_silently_resolved():
    conn=NativeConn();conn.lua.execute('nativeMet[11]=true')
    result,_,_=read(conn,view='structured')
    assert result['status']=='unknown' and result['errors']
    cond=next(c for c in result['cities'][0]['policy_conditions'] if c['modifier_id'].endswith('HIGH_POP'))
    assert cond['native_conditions_met'] is True and cond['meets_observed_threshold'] is False
    assert cond['native_effect_confirmed'] is None


def test_core_world_change_cannot_confirm_an_earlier_city_condition():
    conn=NativeConn();conn.lua.execute('GameEffects.GetModifiers=function()turn=turn+1;return{10,11}end')
    result,_,_=read(conn,view='structured')
    assert result['status']=='unknown' and not result['stable_world_turn']
    assert all(c['native_effect_confirmed'] is None for c in result['cities'][0]['policy_conditions'])


def test_unselected_policy_needs_only_one_transport_request():
    conn=NativeConn();conn.lua.execute('slotted=false')
    result,_,_=read(conn,view='structured')
    assert result['status']=='ready' and len(conn.calls)==1 and 'engine' not in result


def test_native_building_yields_are_current_values_not_static_or_city_total():
    conn = NativeConn()
    conn.lua.execute("city.GetBuildingYield=function(_,building,yield)assert(building==1);return yield=='YIELD_SCIENCE' and 4.5 or 0 end")
    data, _, _ = read(conn, view='structured')
    building = data['cities'][0]['campus_buildings'][0]
    assert building['yields']['YIELD_SCIENCE'] == 4.5
    assert building['yields']['YIELD_FOOD'] == 0
    assert data['cities'][0]['science'] == 33.125
    assert 'SCIENCE=4.5' in read(conn)[0]['report']


def test_failed_building_yield_stays_unknown_and_marks_the_report_incomplete():
    conn = NativeConn()
    conn.lua.execute("city.GetBuildingYield=function(_,building,yield)if yield=='YIELD_SCIENCE' then error('unavailable')end;return 0 end")
    data, _, _ = read(conn, view='structured')
    assert data['cities'][0]['campus_buildings'][0]['yields']['YIELD_SCIENCE'] is None
    assert data['status'] == 'unknown'


def test_brief_aliases_repeated_identifiers_and_identical_yields_without_hiding_differences():
    conn=NativeConn()
    text=read(conn)[0]['report']
    assert text.count('RATIONALISM_BUILDING_YIELDS_HIGH_ADJACENCY')==1
    assert 'B1 = ' in text and 'B1: observed' in text
    assert 'plot_adj=district_adj' in text
    assert 'yield SCIENCE=12.25' in text
    assert 'Inverse' in text and 'Reverse' in text
    conn.lua.execute('district.GetAdjacencyYield=function(_,idx)return idx==3 and 7 or 0 end')
    text=read(conn)[0]['report']
    assert 'district_adj SCIENCE=7' in text and 'plot_adj SCIENCE=3.5' in text
