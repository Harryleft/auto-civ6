"""Resource sightings do not imply ownership, resource supply or builder work."""
import asyncio

from civ_mcp import lua as lq
from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_cities, narrate_empire_resources
from test_public_reports import execute,engine
from test_resource_hint_visibility import ResourceConn


def test_unknown_empire_resource_visibility_is_not_an_empty_success():
    conn=ResourceConn();conn.lua.execute('''
resource_read.IsResourceVisible=function()return nil end
PlayersVisibility={[0]={IsVisible=function()return true end,IsRevealed=function()return true end}}
Map.GetGridSize=function()return 4,1 end
for i,p in pairs(plots)do local index=i;p.GetIndex=function()return index end;p.GetX=function()return index end;p.GetY=function()return 0 end end
''')
    stock,owned,nearby,lux,known=asyncio.run(GameState(conn).get_empire_resources())
    text=narrate_empire_resources(stock,owned,nearby,lux,visibility_known=known)
    assert known is False and 'incomplete/unknown'in text and 'No resources found'not in text


def test_owned_facility_getter_failure_keeps_resource_with_unknown_facility():
    lua,output=engine('''
local tea={Index=1,ResourceType='RESOURCE_TEA',Name='茶',ResourceClassType='RESOURCECLASS_LUXURY'}
GameInfo.Resources=setmetatable({[1]=tea},{__call=function()local i=0;return function()i=i+1;if i==1 then return tea end end end})
Players[0].GetResources=function()return{IsResourceVisible=function()return true end,GetResourceAmount=function()return 0 end}end
Players[0].GetCities=function()return {Members=function()return ipairs({})end}end
Map={GetGridSize=function()return 1,1 end,GetPlot=function()return{
 GetX=function()return 0 end,GetY=function()return 0 end,GetIndex=function()return 0 end,
 GetOwner=function()return 0 end,GetResourceType=function()return 1 end,
 GetImprovementType=function()error('unavailable')end}end}
''')
    lua.execute(lq.build_empire_resources_query())
    owned=lq.parse_empire_resources_response(output)[1]
    assert len(owned)==1 and owned[0].name=='TEA'
    assert owned[0].improvement_type is None and owned[0].improved is None and owned[0].pillaged is None


def test_archaeology_is_retained_in_city_facts_and_excluded_from_builder_tasks():
    conn=ResourceConn();conn.lua.execute('''
visible=true
GameInfo.Resources[7].ResourceType='RESOURCE_ANTIQUITY_SITE'
GameInfo.Resources[7].ResourceClassType='RESOURCECLASS_ARTIFACT'
''')
    city,tasks=conn.advice()
    assert city.unimproved_resources==[] and city.archaeology_sites==['ANTIQUITY_SITE@1,0']
    assert not any(t.x==1 and t.y==0 for t in tasks)
    assert 'Archaeology sites (not builder tasks)'in narrate_cities([city])
    assert conn.lua.globals().native_requests==0


MAP='''
Players[0].GetResources=function()return{IsResourceVisible=function()return true end}end
GameInfo.Resources={[1]={ResourceType='RESOURCE_TEA',Name='茶',ResourceClassType='RESOURCECLASS_LUXURY'}}
GameInfo.Improvements={[1]={ImprovementType='IMPROVEMENT_PLANTATION'}}
Map={GetPlotCount=function()return 4 end,GetPlotByIndex=function(i)return{
 GetX=function()return i end,GetY=function()return 0 end,GetIndex=function()return i end,
 GetOwner=function()return 1 end,GetResourceType=function()return 1 end,
 GetImprovementType=function()if i~=2 then error('fogged facility read')end;return 1 end,
 IsImprovementPillaged=function()return true end}end}
'''


def test_resource_search_discloses_foreign_owner_and_pillage_only_when_visible():
    d,_=execute('map-search',MAP,search_kind='luxury')
    visible=next(r for r in d['data']['locations'] if r['x']==2)
    fogged=next(r for r in d['data']['locations'] if r['x']==3)
    assert visible['x']==2 and visible['owner_id']==1 and visible['pillaged'] is True
    assert visible['improvement_type']=='IMPROVEMENT_PLANTATION'
    assert visible['work_unit']=='UNIT_BUILDER'
    assert fogged['owner_id'] is None and fogged['improvement_type'] is None and fogged['pillaged'] is None
    assert fogged['visible_now'] is False


def test_missing_native_pillage_is_unknown_instead_of_unpillaged():
    d,_=execute('map-search',MAP+"Map.GetPlotByIndex=(function(old)return function(i)local p=old(i);p.IsImprovementPillaged=nil;return p end end)(Map.GetPlotByIndex)")
    assert d['data']['locations'][0]['pillaged'] is None


def test_empire_details_preserve_foreign_vs_unclaimed_and_nearest_geometry():
    lua,output=engine('''
local tea={Index=1,ResourceType='RESOURCE_TEA',Name='茶',ResourceClassType='RESOURCECLASS_LUXURY'}
GameInfo.Resources=setmetatable({[1]=tea},{__call=function()local i=0;return function()i=i+1;if i==1 then return tea end end end})
GameInfo.Improvements={[1]={ImprovementType='IMPROVEMENT_PLANTATION'}}
Players[0].GetResources=function()return{IsResourceVisible=function()return true end,GetResourceAmount=function()return 0 end}end
local function city(x,name)return{GetX=function()return x end,GetY=function()return 0 end,GetName=function()return name end}end
Players[0].GetCities=function()return {Members=function()return ipairs({city(0,'first'),city(3,'nearest')})end}end
PlayersVisibility[0].IsRevealed=function()return true end
PlayersVisibility[0].IsVisible=function(_,i)return i~=4 end
Map={GetGridSize=function()return 5,1 end,GetPlotDistance=function(a,b,c,d)return math.abs(a-c)+math.abs(b-d)end,
 GetPlot=function(x,y)if y~=0 or x<0 or x>4 then return nil end;return{
 GetX=function()return x end,GetY=function()return 0 end,GetIndex=function()return x end,
 GetOwner=function()return x==0 and 0 or (x==2 and -1 or 1)end,
 GetResourceType=function()return 1 end,GetImprovementType=function()if x==4 then error('fog read')end;return x==2 and -1 or 1 end,
 IsImprovementPillaged=function()return true end}end}
''')
    lua.execute(lq.build_empire_resources_query())
    stock,owned,nearby,lux=lq.parse_empire_resources_response(output)
    assert owned[0].pillaged is True and owned[0].owner_id==0
    foreign=next(r for r in nearby if r.x==1);unclaimed=next(r for r in nearby if r.x==2);fog=next(r for r in nearby if r.x==4)
    assert foreign.owner_id==1 and unclaimed.owner_id==-1 and fog.owner_id is None
    assert unclaimed.improvement_type=='NONE' and unclaimed.nearest_city=='nearest' and unclaimed.distance==1
    assert fog.improvement_type is None and fog.pillaged is None
    report=narrate_empire_resources(stock,owned,nearby,lux)
    assert 'Nearby Unclaimed'not in report and 'owner player 1'in report and 'owner unclaimed'in report
    assert 'pillaged yes'in report and 'does not confirm resource supply'in report
