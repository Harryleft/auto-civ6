"""Current dynamic observations must not depend on hidden or missing facts."""
import pytest

from civ_mcp.narrate import narrate_settle_candidates
from test_settle_advice_scope import SettleConn, read
from test_public_reports import execute
from test_resource_plot_facts import MAP


def target(conn, x=10):
    return next(c for c in read(conn) if c.x==x and c.y==0)


def test_fogged_neighbor_yield_changes_cannot_change_current_observation():
    conn=SettleConn()
    conn.lua.execute('''
PlayersVisibility[0].IsVisible=function(_,i)return i~=11 and i<20 end
hidden_food=1
local original=Map.GetPlot
Map.GetPlot=function(x,y)local p=original(x,y);if p and x==11 then p.GetYield=function(_,k)return k==0 and hidden_food or 0 end end;return p end
''')
    before=target(conn);conn.lua.execute('hidden_food=9');after=target(conn)
    assert before==after
    assert before.total_food is None and before.total_prod is None
    assert before.observed_food_subtotal==6 and before.food_observed_tiles==6
    assert before.dynamic_tiles_total==7 and before.current_visible_tiles==6
    assert 'observed subtotal'in narrate_settle_candidates([before])


def test_fogged_harvest_is_not_read_or_used_in_resource_candidates():
    conn=SettleConn()
    conn.lua.execute('''
PlayersVisibility[0].IsVisible=function(_,i)return i~=11 and i<20 end
local original=Map.GetPlot
Map.GetPlot=function(x,y)local p=original(x,y);if p and x==11 then p.GetResourceType=function()error('hidden resource read')end end;return p end
''')
    candidate=target(conn)
    assert candidate.resources==[] and candidate.resource_visibility_known is False
    assert candidate.resource_observed_tiles==6


def test_fogged_center_owner_changes_cannot_filter_the_possible_site():
    conn=SettleConn()
    conn.lua.execute('''
PlayersVisibility[0].IsVisible=function(_,i)return i~=10 and i<20 end
hidden_owner=-1
local original=Map.GetPlot
Map.GetPlot=function(x,y)local p=original(x,y);if p and x==10 then p.GetOwner=function()error('hidden owner read')end end;return p end
''')
    before=target(conn);conn.lua.execute('hidden_owner=1');after=target(conn)
    assert before==after and before.owner_id is None and before.visible_now is False


def test_visible_center_owner_unknown_is_not_silently_an_empty_result():
    conn=SettleConn()
    conn.lua.execute('''
local original=Map.GetPlot
Map.GetPlot=function(x,y)local p=original(x,y);if p and x==10 then p.GetOwner=function()return nil end end;return p end
''')
    candidate=target(conn)
    assert candidate.owner_id is None and candidate.visible_now is True


@pytest.mark.parametrize('resource_class,prefix',[('RESOURCECLASS_ARTIFACT','A:'),('RESOURCECLASS_MODDED','?:')])
def test_settlement_resource_classes_do_not_default_to_bonus(resource_class,prefix):
    conn=SettleConn();conn.lua.globals().resource_class=resource_class
    conn.lua.execute('''
GameInfo.Resources[1]={ResourceType='RESOURCE_ANTIQUITY_SITE',ResourceClassType=resource_class}
Players[0].GetResources=function()return{IsResourceVisible=function()return true end}end
local original=Map.GetPlot
Map.GetPlot=function(x,y)local p=original(x,y);if p and x==10 then p.GetResourceType=function()return 1 end end;return p end
''')
    assert target(conn).resources==[prefix+'ANTIQUITY_SITE']


def test_visible_yield_failure_preserves_known_subtotal_and_separate_zero_yield():
    conn=SettleConn()
    conn.lua.execute('''
local original=Map.GetPlot
Map.GetPlot=function(x,y)local p=original(x,y);if p and x==11 then p.GetYield=function(_,k)if k==0 then error('food unavailable')end;return 0 end end;return p end
''')
    candidate=target(conn)
    assert candidate.total_food is None and candidate.total_prod==0
    assert candidate.observed_food_subtotal==6 and candidate.food_observed_tiles==6
    assert candidate.production_observed_tiles==7


def test_entirely_fogged_footprint_has_unknown_totals_and_subtotals_not_zero():
    conn=SettleConn();conn.lua.execute('PlayersVisibility[0].IsVisible=function()return false end')
    candidate=target(conn)
    assert candidate.total_food is None and candidate.total_prod is None
    assert candidate.observed_food_subtotal is None and candidate.observed_prod_subtotal is None
    assert candidate.food_observed_tiles==0 and candidate.current_visible_tiles==0


def test_fractional_and_negative_current_yields_are_preserved():
    conn=SettleConn();conn.lua.execute('''
local original=Map.GetPlot
Map.GetPlot=function(x,y)local p=original(x,y)
 if p and x==10 then p.GetYield=function(_,k)return k==0 and -.25 or 0 end end
 if p and x==11 then p.GetYield=function(_,k)return k==0 and .5 or -.5 end end
 return p end
''')
    candidate=target(conn)
    assert candidate.total_food==5.25 and candidate.total_prod==-.5
    assert candidate.observed_food_subtotal==5.25 and candidate.observed_prod_subtotal==-.5


@pytest.mark.parametrize('change',[
    'GameConfiguration.GetValue=function()return nil end',
    "PlayerConfigurations[0].GetCivilizationTypeName=function()return ''end",
    'Game.GetCurrentGameTurn=function()return 0/0 end',
    'Game.GetCurrentGameTurn=function()return math.huge end',
    'GameConfiguration.GetValue=function()return math.huge end',
    'GameConfiguration.GetValue=function()return 7.5 end',
])
def test_public_report_requires_known_identity_before_returning_data(change):
    value,_=execute('map-search',MAP+'\n'+change)
    assert value['status']=='unknown' and value['data'] is None
    assert 'world.identity'in value['unknown_fields']


def test_resource_getter_failure_is_partial_but_fog_redaction_is_expected():
    value,_=execute('map-search',MAP+"Map.GetPlotByIndex=(function(old)return function(i)local p=old(i);p.IsImprovementPillaged=nil;return p end end)(Map.GetPlotByIndex)")
    current=next(r for r in value['data']['locations'] if r['x']==2)
    fogged=next(r for r in value['data']['locations'] if r['x']==3)
    assert current['pillaged'] is None and current['details_status']=='unknown'
    assert fogged['details_status']=='withheld_fog'
    assert value['status']=='partial' and 'map.plot.2,0.pillaged'in value['unknown_fields']
    assert not any(x.startswith('map.plot.3,0.')for x in value['unknown_fields'])
