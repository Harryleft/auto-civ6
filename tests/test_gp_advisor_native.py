"""Activation advice uses the native Great Person component and honest scope."""
import asyncio

from civ_mcp.game_state import GameState
from civ_mcp.narrate import narrate_gp_advisor
from test_city_yields import NativeConn


def connection():
    conn=NativeConn()
    conn.lua.execute('''
GameInfo.Units={[20]={Name='Scientist'}}
GameInfo.GreatPersonClasses={[3]={GreatPersonClassType='GREAT_PERSON_CLASS_SCIENTIST'}}
GameInfo.Districts.DISTRICT_CAMPUS=GameInfo.Districts[0]
local person={GetClass=function()return 3 end,GetActionCharges=function()return 1 end,
 GetActivationHighlightPlots=function()return {8}end}
unit={GetID=function()return 65541 end,GetType=function()return 20 end,
 GetX=function()return 6 end,GetY=function()return 9 end,GetName=function()return 'Scientist'end,
 GetGreatPerson=function()return person end}
UnitManager={GetUnit=function(owner,index)assert(owner==0 and index==5);return unit end}
city.GetDistricts=function()return {HasDistrict=function()return true end,GetDistrict=function()return district end}end
Map.GetPlot=function()return {GetIndex=function()return 8 end}end
Map.GetPlotDistance=function()return 2 end
''')
    return conn


def read(conn):
    return asyncio.run(GameState(conn).get_gp_advisor(5))


def test_native_class_and_fractional_city_total_are_not_invented_activation_benefit():
    result=read(connection())
    assert result.gp_class=='GREAT_PERSON_CLASS_SCIENTIST'
    assert result.cities[0].city_yield==33.125
    text=narrate_gp_advisor(result)
    assert 'CAN ACTIVATE' not in text and 'needs move' not in text and 'Best' not in text
    assert 'native highlight yes' in text and 'current city yield 33.125' in text


def test_unreadable_highlight_is_unknown_instead_of_all_targets_false():
    conn=connection();conn.lua.execute("unit.GetGreatPerson=function()return {GetClass=function()return 3 end,GetActionCharges=function()return 1 end,GetActivationHighlightPlots=function()error('unavailable')end}end")
    result=read(conn)
    assert result.cities[0].can_activate is None
    assert 'native highlight unknown' in narrate_gp_advisor(result)


def test_bad_unit_preserves_specific_error():
    conn=connection();conn.lua.execute('UnitManager.GetUnit=function()return nil end')
    assert read(conn)=='ERR:UNIT_NOT_FOUND'


def cultural_connection():
    conn=connection()
    conn.lua.execute('''
GameInfo.GreatPersonClasses[3].GreatPersonClassType='GREAT_PERSON_CLASS_WRITER'
GameInfo.Districts.DISTRICT_THEATER=GameInfo.Districts[0]
city.GetYield=function()return 4.5 end
''')
    return conn


def test_unavailable_valid_cultural_slots_are_unknown_not_zero():
    conn=cultural_connection()
    result=read(conn)
    assert result.cities[0].slots_free==-1 and result.cities[0].slots_total==-1
    assert 'great work slots unknown' in narrate_gp_advisor(result)


def test_all_native_slots_are_scanned_without_a_fixed_six_slot_limit():
    conn=cultural_connection()
    conn.lua.execute('''
city.GetBuildings=function()return {HasBuilding=function()return true end,
 GetNumGreatWorkSlots=function(_,building)assert(building==1);return 8 end,
 GetGreatWorkSlotType=function(_,building,slot)assert(slot>=0 and slot<8);return 4 end,
 GetGreatWorkInSlot=function()return -1 end}end
''')
    result=read(conn)
    assert (result.cities[0].slots_free,result.cities[0].slots_total)==(8,8)
