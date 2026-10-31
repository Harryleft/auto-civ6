"""Current timeline membership precedes GP filters and detail getter work."""
from dataclasses import replace

import pytest

from civ_mcp.lua import great_people
from civ_mcp.narrate import narrate_great_people
from test_great_person_pool_confirmation import GreatPeopleConn


def connection():
    conn=GreatPeopleConn()
    conn.lua.execute('''
GameInfo.GreatPersonClasses={
 [3]={Name='Scientist',GreatPersonClassType='GREAT_PERSON_CLASS_SCIENTIST'},
 [4]={Name='Admiral',GreatPersonClassType='GREAT_PERSON_CLASS_ADMIRAL'}}
GameInfo.GreatPersonIndividuals[0]={Name='Zero',GreatPersonClassType='GREAT_PERSON_CLASS_SCIENTIST',ActionCharges=1}
GameInfo.GreatPersonIndividuals[42]={Name='Ein|stein',GreatPersonClassType='GREAT_PERSON_CLASS_SCIENTIST',ActionCharges=2}
GameInfo.GreatPersonIndividuals[43]={Name='Admiral',GreatPersonClassType='GREAT_PERSON_CLASS_ADMIRAL'}
GameInfo.GreatPersonIndividuals[500]={Name='Static-only',GreatPersonClassType='GREAT_PERSON_CLASS_SCIENTIST'}
GameInfo.Eras={[0]={Name='Modern'}}
current={{Individual=0,Class=3,Cost=100,Era=0,ActionUsageText='Completed district required'},
 {Individual=42,Class=3,Cost=100,Era=0,PassiveEffectText='In range only'},
 {Individual=43,Class=4,Cost=100,Era=0}}
quote_ids={};point_classes={};condition_ids={}
local old_cost=gp.GetPatronizeCost
gp.GetPatronizeCost=function(self,owner,id,y)table.insert(quote_ids,id);return old_cost(self,owner,id,y)end
gp.GetEarnConditionsText=function(_,owner,id)table.insert(condition_ids,id);return 'native conditions'end
Players[0].GetGreatPeoplePoints=function()return {GetPointsTotal=function(_,class)table.insert(point_classes,class);return points end}end
''')
    return conn


def read(conn,**filters):
    conn.rows=[]
    conn.lua.globals().print=lambda value:conn.rows.append(str(value))
    conn.lua.execute(great_people.build_great_people_query(**filters))
    return great_people.parse_great_people_report(conn.rows)


def test_individual_zero_filters_current_timeline_before_detail_getters():
    conn=connection();r=read(conn,individual_id=0)
    assert [p.individual_id for p in r.people]==[0]
    assert (r.native_total,r.matched,r.shown,r.scan_complete)==(3,1,1,True)
    assert list(conn.lua.eval('quote_ids').values())==[0,0]
    assert list(conn.lua.eval('point_classes').values())==[3]
    assert list(conn.lua.eval('condition_ids').values())==[0]
    assert conn.lua.eval('#submissions')==0


def test_native_class_and_id_filters_are_intersected_without_static_candidate_invention():
    conn=connection();r=read(conn,class_type='GREAT_PERSON_CLASS_SCIENTIST')
    assert [p.individual_id for p in r.people]==[0,42]
    assert all(p.class_type=='GREAT_PERSON_CLASS_SCIENTIST'for p in r.people)
    assert r.scan_complete and r.matched==2
    missing=read(connection(),individual_id=500)
    assert missing.people==[] and missing.scan_complete and missing.no_current_match is True
    intersection=read(connection(),individual_id=43,class_type='GREAT_PERSON_CLASS_SCIENTIST')
    assert intersection.people==[] and intersection.scan_complete and intersection.no_current_match is True


def test_malformed_or_sparse_timeline_cannot_establish_no_current_match():
    conn=connection();conn.lua.execute('current[2]={Class=3}')
    r=read(conn,individual_id=500)
    assert r.people==[] and not r.scan_complete and r.no_current_match is None
    assert 'unknown'in narrate_great_people(r.people,report=r).lower()
    sparse=connection();sparse.lua.execute('current[2]=nil')
    r=read(sparse,individual_id=500)
    assert not r.scan_complete and r.native_total==2


def test_native_exhausted_class_has_no_candidate_without_making_scan_incomplete():
    conn=connection();conn.lua.execute('current[3]={Class=4,Cost=2147483647}')
    r=read(conn,individual_id=500)
    assert r.people==[] and r.scan_complete and r.no_current_match is True
    assert (r.native_total,r.matched,r.shown,r.exhausted_count)==(3,0,0,1)
    assert r.exhausted_classes==['GREAT_PERSON_CLASS_ADMIRAL']
    assert list(conn.lua.eval('quote_ids').values())==[]
    assert list(conn.lua.eval('condition_ids').values())==[]
    text=narrate_great_people(r.people,report=r)
    assert 'Exhausted native classes (1): GREAT_PERSON_CLASS_ADMIRAL'in text
    assert 'No current timeline candidate matches this filter.'in text
    assert 'Incomplete observation'not in text
    assert conn.lua.eval('#submissions')==0


def test_exhausted_native_class_filter_and_all_exhausted_timeline_are_explicit():
    conn=connection();conn.lua.execute('current={{Class=4,Cost=2147483647}}')
    r=read(conn,class_type='GREAT_PERSON_CLASS_ADMIRAL')
    assert r.scan_complete and r.no_current_match is True and r.exhausted_count==1
    assert not r.people
    r=read(conn)
    assert 'all observed classes are exhausted'in narrate_great_people(r.people,report=r)


@pytest.mark.parametrize('change',[
    'current[3]={Class=4}',
    'current[3]={Class=4,Cost=100}',
    'current[3]={Class=4,Cost=2147483647,Individual=-1}',
    'current[3]={Class=4,Cost=2147483647};GameInfo.GreatPersonClasses[4]=nil',
    'current[3]={Class=4,Cost=2147483647};current[4]=current[3]',
])
def test_unknown_or_duplicate_exhausted_shape_still_prevents_absence_proof(change):
    conn=connection();conn.lua.execute(change)
    r=read(conn,individual_id=500)
    assert not r.scan_complete and r.no_current_match is None


def test_missing_exhausted_class_row_keeps_report_incomplete():
    conn=connection();conn.lua.execute('current[3]={Class=4,Cost=2147483647}')
    r=read(conn)
    assert r.scan_complete
    truncated=[row for row in conn.rows if not row.startswith('GP_EXHAUSTED|')]
    r=great_people.parse_great_people_report(truncated)
    assert not r.scan_complete and 'EXHAUSTED_COUNT_MISMATCH'in r.errors


def test_unknown_class_binding_and_duplicate_identity_keep_partial_known_records():
    conn=connection();conn.lua.execute('GameInfo.GreatPersonClasses[4]=nil')
    r=read(conn)
    assert [p.individual_id for p in r.people]==[0,42] and not r.scan_complete
    duplicate=connection();duplicate.lua.execute('current[3]=current[2]')
    r=read(duplicate)
    assert len({p.individual_id for p in r.people})==len(r.people)
    assert not r.scan_complete


def test_truncated_or_missing_scan_metadata_never_claims_complete_timeline():
    conn=connection();r=read(conn)
    assert r.scan_complete
    without_row=[line for line in conn.rows if not line.startswith('GP|Scientist|Zero|')]
    assert not great_people.parse_great_people_report(without_row).scan_complete
    legacy=[line for line in conn.rows if line.startswith('GP|')]
    assert len(great_people.parse_great_people_response(legacy))==3
    assert not great_people.parse_great_people_report(legacy).scan_complete


def test_historical_rows_keep_literal_percent_names_and_default_list_api():
    rows=['GP|Scientist|Name%20literal|Modern|100|Unclaimed|150|condition%7Ckept|gold:4,faith:2,recruit:true|42']
    person,=great_people.parse_great_people_response(rows)
    assert person.individual_name=='Name%20literal' and person.ability=='condition|kept'
    assert person.class_type is None


def test_parsed_candidate_identity_must_match_the_echoed_native_filter():
    conn=connection();read(conn)
    rows=[line if not line.startswith('GP_TIMELINE|') else 'GP_TIMELINE|3|3|3|true|0|?'for line in conn.rows]
    report=great_people.parse_great_people_report(rows)
    assert not report.scan_complete and 'TIMELINE_FILTER_MISMATCH'in report.errors


def test_filters_keep_exact_quotes_native_conditions_and_escaped_names():
    conn=connection();conn.lua.execute('goldCost=1000001.25;gold=2000002.5')
    r=read(conn,individual_id=42);p=r.people[0]
    assert p.individual_name=='Ein|stein' and p.gold_cost==1000001.25 and p.gold_balance==2000002.5
    assert 'In range only'in p.ability and 'native conditions'in p.ability
    assert 'charges 2'in p.ability and p.can_recruit is True


def test_identical_known_balances_are_shared_but_unknown_or_changed_balances_are_not():
    r=read(connection());text=narrate_great_people(r.people,report=r)
    assert 'Shared balances in this response: gold 1000; faith 700'in text
    assert 'balance 1000'not in text and 'balance 700'not in text
    changed=[replace(p,gold_balance=900 if p.individual_id==42 else 1000)for p in r.people]
    text=narrate_great_people(changed,report=r)
    assert 'balance 900'in text and 'balance 1000'in text
    unknown=[replace(p,gold_balance=None if p.individual_id==42 else 1000)for p in r.people]
    text=narrate_great_people(unknown,report=r)
    assert 'balance unknown'in text and 'OBSERVATION_UNAVAILABLE'in text


@pytest.mark.parametrize('filters',[{'individual_id':True},{'individual_id':-1},{'individual_id':1.0},
    {'class_type':'Scientist'},{'class_type':"GREAT_PERSON_CLASS_SCIENTIST';error('write')"}])
def test_invalid_filters_are_rejected_before_native_source_construction(filters):
    with pytest.raises(ValueError):great_people.build_great_people_query(**filters)
