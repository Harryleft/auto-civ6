"""A domain brief shares scope without dropping decision fields or unknowns."""
from copy import deepcopy
import json

from civ_mcp.resource_search import brief
from civ_mcp.devtools.commands import parse_command


def report():
    return dict(schema_version=1,status='partial',identity=dict(turn=200,player_id=0,seed=7,civilization='CIVILIZATION_SUMERIA'),
        total=2,offset=0,limit=2,truncated=False,unknown_fields=['map.1.pillaged'],data=dict(scope='native visibility boundary',locations=[
            dict(x=-2,y=4,type='RESOURCE_TEA',name='茶',class_='unused',**{'class':'RESOURCECLASS_LUXURY'},owner_id=0,visible_now=True,
                 improvement_type='IMPROVEMENT_PLANTATION',pillaged=False,work_unit='UNIT_BUILDER',details_status='current',scope='long repeated scope'),
            dict(x=3,y=0,type='RESOURCE_SPICES',name='香料',**{'class':'RESOURCECLASS_LUXURY'},owner_id=None,visible_now=False,
                 improvement_type=None,pillaged=None,work_unit='UNIT_BUILDER',details_status='withheld_fog',scope='long repeated scope')]))


def test_brief_preserves_metadata_values_and_shared_scope_once_without_mutating_input():
    data=report();before=deepcopy(data);out=brief(data,search_kind='luxury',query='茶',limit=2)
    assert data==before
    for key in ('status','identity','total','offset','limit','truncated','unknown_fields'):assert out[key]==data[key]
    text=out['report']
    for token in ('(-2,4)','RESOURCE_TEA','茶','owner=0','visible=yes','IMPROVEMENT_PLANTATION','pillaged=no','actor=B','withheld_fog','owner=?','facility=?'):
        assert token in text
    assert text.count('long repeated scope')==1
    assert out['expand']==dict(command='map search',search_kind='luxury',query='茶',limit=2,offset=0,view='structured')
    assert '"class_":"unused"'in text


def test_differing_scope_and_future_fields_are_not_dropped():
    data=report();data['data']['locations'][1]['scope']='different native scope'
    data['data']['new_fact']={'known':False}
    out=brief(data);text=out['report']
    assert 'scope=S1'in text and 'scope=S2'in text and 'different native scope'in text
    assert out['data_extra']=={'new_fact':{'known':False}}


def test_unknown_locations_never_claim_absence():
    out=brief(dict(schema_version=1,status='unknown',data=None,error='native unavailable'))
    assert out['status']=='unknown' and out['shown'] is None
    assert 'no complete absence established'in out['report']


def test_cli_default_is_brief_and_structured_is_explicitly_recoverable():
    assert parse_command('map search luxury').arguments['view']=='brief'
    assert parse_command('map search luxury --view structured').arguments['view']=='structured'
    assert parse_command('great-person list --individual 0 --class GREAT_PERSON_CLASS_ADMIRAL').arguments==dict(individual_id=0,class_type='GREAT_PERSON_CLASS_ADMIRAL')
    assert parse_command('city workforce 131073 --view evidence').tool=='get_city_workforce'
