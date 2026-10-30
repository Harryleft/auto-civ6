"""Great Person prices and eligibility through the real query and action seams."""
import asyncio

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_great_people
from test_great_person_pool_confirmation import GreatPeopleConn


def connection():
    conn=GreatPeopleConn()
    conn.lua.execute('''
GameInfo.GreatPersonClasses={[3]={Name='Scientist'}}
GameInfo.Eras={[0]={Name='Modern'}}
current[1].Era=0
GameInfo.GreatPersonIndividuals[42].GreatPersonIndividualType='GREAT_PERSON_INDIVIDUAL_EINSTEIN'
GameInfo.GreatPersonIndividualActionModifiers=function()return function()end end
GameInfo.GreatPersonIndividualBirthModifiers=function()return function()end end
''')
    return conn


def read(conn):
    return asyncio.run(GameState(conn).get_great_people())[0]


def test_independent_currency_failures_keep_other_quote_and_eligibility():
    conn=connection();conn.lua.execute("gp.GetPatronizeCost=function(_,owner,id,y)if y==2 then error('gold unknown')end;return faithCost end")
    person=read(conn)
    assert person.gold_cost is None
    assert person.faith_cost==250
    assert person.can_recruit is True
    assert 'gold unknown' in narrate_great_people([person])
    assert conn.lua.eval('#submissions')==0


def test_free_patronage_is_visible_and_quote_matches_exact_action_charge():
    conn=connection();conn.lua.execute('goldCost=0')
    person=read(conn)
    assert person.gold_cost==0 and person.can_patronize_gold is True
    text=narrate_great_people([person])
    assert 'gold 0' in text and 'balance 1000' in text
    result=asyncio.run(GameState(conn).patronize_great_person(42,'YIELD_GOLD'))
    assert result.startswith('PATRONIZED|'),result
    assert conn.lua.eval('gold')==1000 and conn.lua.eval('#submissions')==1


def test_unaffordable_quote_shows_balance_shortfall_and_native_refusal():
    conn=connection();conn.lua.execute('gold=12.5;faith=5')
    person=read(conn)
    assert person.can_patronize_gold is False and person.can_patronize_faith is False
    text=narrate_great_people([person])
    assert 'short 387.5' in text and 'short 245' in text
    result=asyncio.run(GameState(conn).patronize_great_person(42,'YIELD_GOLD'))
    assert 'CANNOT_PATRONIZE' in result and conn.lua.eval('#submissions')==0


def test_native_timeline_usage_and_passive_conditions_are_preserved_verbatim():
    conn=connection();conn.lua.execute('''
current[1].ActionNameText='Activate'
current[1].ActionEffectText='1500|production'
current[1].ActionUsageText='Must be on a completed district; costs all charges'
current[1].PassiveEffectText='Only naval units in range'
GameInfo.GreatPersonIndividuals[42].ActionCharges=2
''')
    text=read(conn).ability
    assert '1500|production' in text and 'completed district' in text and 'Only naval units in range' in text
    assert 'charges 2' in text


def test_missing_points_do_not_become_zero():
    conn=connection();conn.lua.execute('Players[0].GetGreatPeoplePoints=function()return nil end')
    person=read(conn)
    assert person.player_points is None
    assert 'your points: unknown/100' in narrate_great_people([person])


def test_large_fractional_patronage_quote_and_receipt_are_exact():
    conn=connection();conn.lua.execute('goldCost=1000001.25;gold=2000002.5')
    person=read(conn)
    assert 'gold 1000001.25' in narrate_great_people([person])
    receipt=asyncio.run(GameState(conn).patronize_great_person(42,'YIELD_GOLD'))
    assert 'cost:1000001.25 gold' in receipt


def test_missing_native_gp_system_is_not_an_empty_timeline():
    import pytest
    conn=connection();conn.lua.execute('Game.GetGreatPeople=function()return nil end')
    with pytest.raises(ValueError,match='NO_GP_SYSTEM'):
        read(conn)
