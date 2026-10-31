"""A partial map scan preserves sightings without claiming a complete count."""
import pytest

from test_public_reports import execute
from test_resource_plot_facts import MAP


def assert_incomplete(value, observed):
    assert value['status'] == 'partial'
    assert value['count_complete'] is False
    assert value['observed_total'] == observed
    assert value['total'] is None and value['truncated'] is None
    assert value['unknown_fields']


@pytest.mark.parametrize('resource_type,resource_class', [
    ('RESOURCE_WHEAT', 'RESOURCECLASS_BONUS'),
    ('RESOURCE_ANTIQUITY_SITE', 'RESOURCECLASS_ARTIFACT'),
    ('RESOURCE_MODDED_ARTIFACT', 'RESOURCECLASS_ARTIFACT'),
])
def test_unknown_current_visibility_does_not_confirm_consumable_absence(resource_type, resource_class):
    extra = MAP + f'''
GameInfo.Resources[1].ResourceType='{resource_type}'
GameInfo.Resources[1].ResourceClassType='{resource_class}'
PlayersVisibility[0].IsVisible=function()return nil end
Map.GetPlotByIndex=(function(old)return function(i)local plot=old(i)
 plot.GetImprovementType=function()error('unknown visibility must not read facilities')end
 return plot end end)(Map.GetPlotByIndex)
'''
    value, _ = execute('map-search', extra)
    assert_incomplete(value, 0)
    assert value['data']['locations'] == []
    assert any('visible_now' in field for field in value['unknown_fields'])


def test_unknown_village_visibility_does_not_read_hidden_improvements_or_claim_empty():
    value, _ = execute('map-search', MAP + '''
PlayersVisibility[0].IsVisible=function()return nil end
Map.GetPlotByIndex=(function(old)return function(i)local plot=old(i)
 plot.GetImprovementType=function()error('unknown visibility must not inspect huts')end
 return plot end end)(Map.GetPlotByIndex)
''', search_kind='village')
    assert_incomplete(value, 0)
    assert value['data']['locations'] == []


def test_later_resource_getter_exception_keeps_earlier_and_later_sightings():
    value, _ = execute('map-search', MAP + '''
Map.GetPlotCount=function()return 5 end
Map.GetPlotByIndex=(function(old)return function(i)local plot=old(i)
 if i==3 then plot.GetResourceType=function()error('resource unavailable')end end
 return plot end end)(Map.GetPlotByIndex)
''', limit=1, offset=1)
    assert_incomplete(value, 3)
    assert [row['x'] for row in value['data']['locations']] == [2]


def test_later_village_getter_exception_keeps_earlier_and_later_sightings():
    value, _ = execute('map-search', MAP + '''
Map.GetPlotCount=function()return 5 end
PlayersVisibility[0].IsVisible=function()return true end
GameInfo.Improvements[1]={ImprovementType='IMPROVEMENT_GOODY_HUT',Name='村庄',Goody=1}
Map.GetPlotByIndex=(function(old)return function(i)local plot=old(i)
 plot.GetImprovementType=function()if i==3 then error('hut unavailable')end;return 1 end
 return plot end end)(Map.GetPlotByIndex)
''', search_kind='village')
    assert_incomplete(value, 3)
    assert [row['x'] for row in value['data']['locations']] == [1, 2, 4]


def test_missing_resource_visibility_means_matching_total_is_unknown():
    value, _ = execute('map-search', MAP + '''
Players[0].GetResources=function()return{IsResourceVisible=function()return nil end}end
''')
    assert_incomplete(value, 0)
    assert value['data']['locations'] == []


def test_facility_field_failure_preserves_complete_location_count():
    value, _ = execute('map-search', MAP + '''
Map.GetPlotByIndex=(function(old)return function(i)local plot=old(i)
 plot.IsImprovementPillaged=function()return nil end
 return plot end end)(Map.GetPlotByIndex)
''', limit=1)
    assert value['status'] == 'partial'
    assert value['count_complete'] is True and value['observed_total'] == 3
    assert value['total'] == 3 and value['truncated'] is True
    assert len(value['data']['locations']) == 1


def test_known_nonmatching_resource_does_not_require_current_visibility():
    value, _ = execute('map-search', MAP + '''
GameInfo.Resources[1].ResourceType='RESOURCE_WHEAT'
GameInfo.Resources[1].ResourceClassType='RESOURCECLASS_BONUS'
PlayersVisibility[0].IsVisible=function()return nil end
''', search_kind='luxury')
    assert value['status'] == 'ready' and value['unknown_fields'] == []
    assert value['count_complete'] is True and value['total'] == value['observed_total'] == 0
    assert value['truncated'] is False


def test_unknown_revealed_state_cannot_establish_complete_empty_search():
    value, _ = execute('map-search', MAP + '''
PlayersVisibility[0].IsRevealed=function()return nil end
''')
    assert_incomplete(value, 0)
    assert value['data']['locations'] == []
