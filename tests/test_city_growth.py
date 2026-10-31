"""Native growth getters through GameState; never recompute engine rules."""
import asyncio
import json

import pytest

from civ_mcp.game_state import GameState
from civ_mcp.devtools.commands import parse_command
from test_public_reports import engine


class GrowthConn:
    def __init__(self, extra=''):
        self.lua,self.output=engine('''
turn=200;calls=0
Game.GetCurrentGameTurn=function()return turn end
GameInfo.Yields={};for i,k in ipairs({'FOOD','PRODUCTION','GOLD','SCIENCE','CULTURE','FAITH'})do GameInfo.Yields['YIELD_'..k]={Index=i-1}end
g={GetFoodSurplus=function()return 8 end,GetFood=function()return 24 end,GetGrowthThreshold=function()return 93 end,
 GetTurnsUntilGrowth=function()return 41 end,GetTurnsUntilStarvation=function()return -1 end,
 GetHousing=function()return 12 end,GetAmenities=function()return 5 end,GetAmenitiesNeeded=function()return 6 end,
 GetHappinessGrowthModifier=function()return -15 end,GetHappinessNonFoodYieldModifier=function()return -10 end,
 GetHousingGrowthModifier=function()return .25 end,GetOccupationGrowthModifier=function()return 1 end,
 GetLoyaltyGrowthModifier=function()return 1 end,GetOtherGrowthModifier=function()return 0 end,
 GetOverallGrowthModifier=function()return .2125 end}
source_reads=0
for _,name in ipairs({'GetHousingFromWater','GetHousingFromBuildings','GetHousingFromImprovements','GetHousingFromDistricts','GetHousingFromCivics','GetHousingFromGreatPeople','GetHousingFromStartingEra','GetHousingFromGreatWorks',
 'GetAmenitiesFromLuxuries','GetAmenitiesFromEntertainment','GetAmenitiesFromCivics','GetAmenitiesFromGreatPeople','GetAmenitiesFromCityStates','GetAmenitiesFromReligion','GetAmenitiesFromNationalParks','GetAmenitiesFromStartingEra','GetAmenitiesFromImprovements','GetAmenitiesFromGovernors','GetAmenitiesFromDistricts','GetAmenitiesFromNaturalWonders','GetAmenitiesFromTraits','GetAmenitiesLostFromWarWeariness','GetAmenitiesLostFromBankruptcy'})do
 g[name]=function()source_reads=source_reads+1;return 0 end
end
g.GetHousingFromWater=function()source_reads=source_reads+1;return 2 end
g.GetHousingFromBuildings=function()source_reads=source_reads+1;return 10 end
g.GetHousingFromImprovements=function()source_reads=source_reads+1;return .5 end
g.GetAmenitiesFromLuxuries=function()source_reads=source_reads+1;return 6 end
g.GetAmenitiesLostFromWarWeariness=function()source_reads=source_reads+1;return -1 end
city={GetID=function()return 262147 end,GetOwner=function()return 0 end,GetName=function()return '乌尔|100%\\n本城'end,
 GetGrowth=function()return g end,GetPopulation=function()return 12 end,GetYield=function()return 32 end,IsOccupied=function()return false end,
 GetYieldToolTip=function(_,i)return '来源|'..i..'\\n100%'end,GetHousingAdvice=function()return '住房建议'end,GetAmenityAdvice=function()return '宜居度建议'end}
Players[0].GetCities=function()return {Members=function()return ipairs({city})end}end
'''+extra)
        self.calls=0

    async def execute_write(self,code,**options):
        self.calls+=1;self.output.clear();self.lua.execute(code);return list(self.output)


def read(conn,city_id=None,view='structured'):
    return asyncio.run(GameState(conn).get_city_growth(city_id,view))


def test_native_overall_modifier_explains_food_progress_without_replacing_native_turns():
    conn=GrowthConn();d=read(conn);c=d['cities'][0]
    assert d['status']=='ready' and conn.calls==1
    assert c['turns_until_growth']==41 and c['derived']['growth_food_per_turn']==pytest.approx(1.7)
    assert c['derived']==dict(food_consumption=24,food_remaining=69,housing_headroom=0,amenities_balance=-1,growth_food_per_turn=1.7)
    assert c['happiness_growth_percent']==-15 and c['housing_growth_multiplier']==.25
    assert c['other_growth_fraction']==0 and c['happiness_nonfood_yield_percent']==-10
    # Engine overall can include rules unknown to this adapter. Preserve it.
    conn.lua.execute('g.GetOverallGrowthModifier=function()return .12345678901234567 end')
    assert read(conn)['cities'][0]['overall_growth_multiplier']==.12345678901234567


def test_unknown_growth_modifier_is_not_one_and_does_not_erase_known_turns():
    d=read(GrowthConn('g.GetOverallGrowthModifier=nil'))
    assert d['status']=='partial' and d['cities'][0]['overall_growth_multiplier'] is None
    assert d['cities'][0]['derived']['growth_food_per_turn'] is None
    assert d['cities'][0]['turns_until_growth']==41


@pytest.mark.parametrize('value',['0/0','math.huge','true'])
def test_non_numeric_or_nonfinite_getter_stays_unknown(value):
    d=read(GrowthConn(f'g.GetHousingGrowthModifier=function()return {value} end'))
    assert d['status']=='partial' and d['cities'][0]['housing_growth_multiplier'] is None


def test_deficit_ignores_positive_growth_multiplier_and_preserves_starvation():
    d=read(GrowthConn('g.GetFoodSurplus=function()return -2 end;g.GetTurnsUntilGrowth=function()return -1 end;g.GetTurnsUntilStarvation=function()return 3 end'))
    assert d['cities'][0]['derived']['growth_food_per_turn']==-2
    assert d['cities'][0]['turns_until_starvation']==3


def test_evidence_preserves_tooltips_and_brief_has_units_and_all_limiters():
    e=read(GrowthConn(),262147,'evidence');b=read(GrowthConn(),262147,'brief')
    assert len(e['cities'][0]['yield_tooltips'])==6
    assert e['cities'][0]['yield_tooltips']['SCIENCE']=='来源|3\n100%'
    for token in ('41t','housing x0.25','loyalty x1','occupation x1','happiness -15%','other fraction 0','balance -1'):
        assert token in b['report']
    assert len(json.dumps(b,ensure_ascii=False))<len(json.dumps(e,ensure_ascii=False))


def test_source_components_preserve_zero_negative_fraction_and_native_total_separately():
    conn=GrowthConn();e=read(conn,262147,'evidence');c=e['cities'][0]
    assert e['status']=='ready' and conn.lua.globals().source_reads==23
    assert c['housing_sources']==dict(water=2,buildings=10,improvements=.5,districts=0,civics=0,great_people=0,starting_era=0,great_works=0)
    assert c['housing']==12 and sum(c['housing_sources'].values())==12.5
    assert c['amenity_sources']['luxuries']==6 and c['amenity_sources']['traits']==0
    assert c['amenity_losses']==dict(war_weariness=-1,bankruptcy=0)


def test_source_getter_missing_is_unknown_only_when_sources_are_requested():
    conn=GrowthConn('g.GetHousingFromBuildings=nil')
    default=read(conn,262147,'structured')
    assert default['status']=='ready' and 'housing_sources'not in default['cities'][0]
    assert conn.lua.globals().source_reads==0
    evidence=read(conn,262147,'evidence')
    assert evidence['status']=='partial' and evidence['cities'][0]['housing_sources']['buildings'] is None
    assert evidence['cities'][0]['housing_sources']['water']==2


def test_scope_mismatch_or_unknown_city_discards_city_facts():
    assert read(GrowthConn(),65536)['cities'] is None
    d=read(GrowthConn('city.GetPopulation=function()turn=201;return 12 end'))
    assert d['status']=='unknown' and d['cities'] is None
    assert read(GrowthConn('city.GetOwner=function()return 1 end'))['cities'] is None


def test_pending_rejected_before_any_native_io():
    conn=GrowthConn();game=GameState(conn);game._pending_load=True
    d=asyncio.run(game.get_city_growth())
    assert d['status']=='blocked' and conn.calls==0


@pytest.mark.parametrize('change',["GameConfiguration.GetValue=function()return nil end", "PlayerConfigurations[0].GetCivilizationTypeName=function()return ''end"])
def test_unknown_but_unchanged_world_identity_cannot_be_ready(change):
    d=read(GrowthConn(change))
    assert d['status']=='unknown' and d['cities'] is None
    assert 'world.identity'in d['unknown_fields']


def test_cli_growth_is_named_and_views_pass_through():
    c=parse_command('city growth 262147 --view evidence')
    assert c.tool=='get_city_growth' and c.arguments=={'city_id':262147,'view':'evidence'}
